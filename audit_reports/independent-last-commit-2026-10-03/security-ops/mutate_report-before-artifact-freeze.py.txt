"""Report compatible latest verdicts; historical unreceipted scores are untrusted."""

import argparse
import collections
import json
from pathlib import Path
from typing import Any

from mutate_identity import digest_json, input_identity, latest_compatible, read_results, sha_file

BACKEND = Path(__file__).resolve().parent.parent
MUTDIR = BACKEND / "mutation"


def summarize(
    manifest: dict[str, dict[str, Any]], records: list[dict[str, Any]], *, campaign_id: str
) -> dict[str, Any]:
    latest = latest_compatible(records, campaign_id, manifest)
    statuses = collections.Counter(r["status"] for r in latest.values())
    complete = [
        r
        for r in latest.values()
        if r.get("selection_mode") == "complete" and r["status"] in {"KILLED", "SURVIVED"}
    ]
    killed = sum(r["status"] == "KILLED" for r in complete)
    return {
        "total": len(manifest),
        "executed": len(latest),
        "missing": len(manifest) - len(latest),
        "statuses": dict(statuses),
        "sampled": sum(
            r.get("selection_mode") in {"sampled", "module-fallback", "dedicated-only"}
            for r in latest.values()
        ),
        "complete_measured": len(complete),
        "score": 100 * killed / len(complete) if complete else None,
        "untrusted_or_other_campaign_attempts": len(records)
        - sum(r.get("campaign_id") == campaign_id for r in records),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-id")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    manifest = {m["id"]: m for m in json.loads((MUTDIR / "manifest.json").read_text())}
    records = read_results(MUTDIR / "results.jsonl")
    inputs = input_identity(BACKEND)
    eligible = [
        r
        for r in records
        if r.get("provenance")
        and r.get("campaign_id") == digest_json(r["provenance"])
        and all(r["provenance"].get(k) == v for k, v in inputs.items())
        and r["provenance"].get("manifest_sha256") == sha_file(MUTDIR / "manifest.json")
        and r["provenance"].get("coverage_sha256") == sha_file(BACKEND / ".coverage-mut")
        and r["provenance"].get("coverage_provenance_sha256")
        == sha_file(BACKEND / ".coverage-mut.provenance.json")
    ]
    campaign = args.campaign_id or (eligible[-1]["campaign_id"] if eligible else "unmeasured")
    summary = summarize(manifest, eligible, campaign_id=campaign)
    summary["untrusted_or_other_campaign_attempts"] += len(records) - len(eligible)
    print(json.dumps(summary, indent=2))
    if args.dry_run:
        return
    score = f"{summary['score']:.2f}%" if summary["score"] is not None else "unmeasured"
    output = [
        "# Backend mutation measurement",
        "",
        f"Campaign: `{campaign}`",
        "",
        "Historical attempts without compatible receipts are untrusted.",
        "",
        f"Complete-selection assertion-kill score: **{score}**",
        "",
        "Timeouts, infrastructure failures, invalid mutants and sampled selections "
        "do not improve this score.",
        "",
        "```json",
        json.dumps(summary, indent=2),
        "```",
        "",
    ]
    temporary = MUTDIR / "report.md.tmp"
    temporary.write_text("\n".join(output))
    temporary.replace(MUTDIR / "report.md")


if __name__ == "__main__":
    main()
