"""Report compatible latest verdicts; historical unreceipted scores are untrusted."""

import argparse
import collections
import json
from collections import OrderedDict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from mutate_identity import (
    digest_json,
    input_identity,
    iter_results,
    read_artifact,
    sha_bytes,
    sha_file,
)

BACKEND = Path(__file__).resolve().parent.parent
MUTDIR = BACKEND / "mutation"


def summarize(
    manifest: dict[str, dict[str, Any]], records: Iterable[dict[str, Any]], *, campaign_id: str
) -> dict[str, Any]:
    fingerprints = {mid: digest_json(mutant) for mid, mutant in manifest.items()}
    latest: dict[str, dict[str, Any]] = {}
    total = same_campaign = 0
    for record in records:
        total += 1
        if record.get("campaign_id") != campaign_id:
            continue
        same_campaign += 1
        mid = record.get("id")
        if (
            isinstance(mid, str)
            and mid in fingerprints
            and record.get("mutant_digest") == fingerprints[mid]
        ):
            latest[mid] = {
                "status": record["status"],
                "selection_mode": record.get("selection_mode"),
            }
    statuses = collections.Counter(r["status"] for r in latest.values())
    complete = [
        r
        for r in latest.values()
        if r.get("selection_mode") == "complete" and r["status"] in {"KILLED", "SURVIVED"}
    ]
    killed = sum(r["status"] == "KILLED" for r in complete)
    return {
        "campaign_id": campaign_id,
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
        "untrusted_or_other_campaign_attempts": total - same_campaign,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-id")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    manifest_bytes = read_artifact(
        MUTDIR / "manifest.json", instruction="run python mutation/mutate_gen.py first"
    )
    manifest = {m["id"]: m for m in json.loads(manifest_bytes)}
    results_path = MUTDIR / "results.jsonl"
    before = results_path.stat() if results_path.exists() else None
    artifacts = {
        "manifest_sha256": sha_bytes(manifest_bytes),
        "coverage_sha256": sha_file(BACKEND / ".coverage-mut"),
        "coverage_provenance_sha256": sha_file(BACKEND / ".coverage-mut.provenance.json"),
    }
    inputs = input_identity(BACKEND)
    cache: OrderedDict[str, tuple[dict[str, Any], bool]] = OrderedDict()

    def eligible(record: dict[str, Any]) -> bool:
        provenance, cid = record.get("provenance"), record.get("campaign_id")
        if not isinstance(provenance, dict) or not isinstance(cid, str):
            return False
        previous = cache.get(cid)
        if previous is not None and previous[0] == provenance:
            cache.move_to_end(cid)
            return previous[1]
        valid = bool(provenance) and (
            cid == digest_json(provenance)
            and all(provenance.get(key) == value for key, value in inputs.items())
            and all(provenance.get(key) == value for key, value in artifacts.items())
        )
        cache[cid] = (provenance, valid)
        cache.move_to_end(cid)
        while len(cache) > 8:
            cache.popitem(last=False)
        return valid

    total_records = eligible_records = 0
    campaign = "unmeasured"
    for record in iter_results(results_path):
        total_records += 1
        if eligible(record):
            eligible_records += 1
            campaign = record["campaign_id"]
    campaign = args.campaign_id or campaign

    def report_records() -> Iterable[dict[str, Any]]:
        for record in iter_results(results_path):
            if eligible(record):
                yield record
            elif record.get("campaign_id") == campaign:
                # An unproven later attempt must supersede an earlier claimed
                # kill. It cannot restore a previously cached diagnostic score.
                yield {**record, "status": "UNVERIFIED", "selection_mode": None}

    summary = summarize(manifest, report_records(), campaign_id=campaign)
    after = results_path.stat() if results_path.exists() else None
    if (before is None) != (after is None) or (
        before is not None
        and after is not None
        and (before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_ino, after.st_size, after.st_mtime_ns)
    ):
        raise ValueError(
            "mutation receipts changed during reporting; retry after the writer finishes"
        )
    summary["untrusted_or_other_campaign_attempts"] += total_records - eligible_records
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
        f"Diagnostic complete-selection producer score: **{score}**",
        "Use the independent receipt verifier before certifying a complete measurement.",
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
