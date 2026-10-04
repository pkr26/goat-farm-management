"""Enforce a provenance-bound mutation score for the exact CI target plan."""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path
from typing import Any


def _object(path: Path) -> dict[str, Any]:
    value: object = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return value


def _records(path: Path) -> list[dict[str, Any]]:
    """Load every fresh attempt, failing closed on malformed JSONL evidence."""
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            value: object = json.loads(line)
        except (TypeError, ValueError) as exc:
            raise TypeError(f"{path}:{line_number} is not valid JSON") from exc
        if not isinstance(value, dict):
            raise TypeError(f"{path}:{line_number} must contain a JSON object")
        records.append(value)
    return records


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--results",
        type=Path,
        required=True,
        help="fresh JSONL attempts produced after the CI results file was truncated",
    )
    parser.add_argument("--format", choices=("backend", "frontend"), required=True)
    parser.add_argument("--minimum", type=float, default=80.0)
    args = parser.parse_args()

    plan = _object(args.plan)
    report = _object(args.report)
    records = _records(args.results)
    target_key = "mutants" if args.format == "backend" else "targets"
    raw_targets = plan.get(target_key)
    if not isinstance(raw_targets, list) or not all(
        isinstance(target, str) and target for target in raw_targets
    ):
        raise TypeError(f"plan is missing {target_key!r}")
    if len(set(raw_targets)) != len(raw_targets):
        raise TypeError(f"plan {target_key!r} contains duplicate IDs")
    target_count = len(raw_targets)

    if args.format == "backend":
        measured = report.get("complete_measured")
        score = report.get("score")
        attempted = report.get("executed")
        report_campaign = report.get("campaign_id")
        plan_campaign = None
        campaign_key = "campaign_id"
    else:
        measured = report.get("scored")
        raw_score = report.get("score")
        score = raw_score * 100 if isinstance(raw_score, (int, float)) else raw_score
        counts = report.get("counts")
        attempted = (
            sum(
                value
                for key, value in counts.items()
                if key != "PENDING" and isinstance(value, int)
            )
            if isinstance(counts, dict)
            else None
        )
        report_campaign = report.get("campaignId")
        plan_campaign = plan.get("campaignId")
        campaign_key = "campaignId"

    errors: list[str] = []
    evidence_ids = [record.get("id") for record in records]
    if len(records) != target_count:
        errors.append(
            f"expected {target_count} fresh JSONL attempts, evidence contains {len(records)}"
        )
    if collections.Counter(evidence_ids) != collections.Counter(raw_targets):
        missing = sorted(
            set(raw_targets) - {item for item in evidence_ids if isinstance(item, str)}
        )
        unexpected = sorted(
            {item for item in evidence_ids if isinstance(item, str)} - set(raw_targets)
        )
        errors.append(
            "fresh attempt IDs do not exactly match the plan"
            f"; missing={missing!r}, unexpected={unexpected!r}"
        )

    evidence_campaigns = {record.get(campaign_key) for record in records}
    if target_count:
        if len(evidence_campaigns) != 1 or not all(
            isinstance(campaign, str) and campaign for campaign in evidence_campaigns
        ):
            errors.append("fresh attempts do not all belong to one identified campaign")
        else:
            evidence_campaign = next(iter(evidence_campaigns))
            if report_campaign != evidence_campaign:
                errors.append(
                    f"report campaign {report_campaign!r} does not match fresh evidence "
                    f"campaign {evidence_campaign!r}"
                )
            if plan_campaign is not None and plan_campaign != evidence_campaign:
                errors.append(
                    f"plan campaign {plan_campaign!r} does not match fresh evidence "
                    f"campaign {evidence_campaign!r}"
                )
    elif records:
        errors.append("an empty plan must not have mutation attempts")

    if attempted != target_count:
        errors.append(f"expected {target_count} fresh attempts, report contains {attempted!r}")
    if measured != target_count:
        errors.append(
            f"expected {target_count} complete-selection verdicts, report contains {measured!r}; "
            "timeouts, invalid mutants, uncovered sites, and infrastructure errors cannot pass"
        )
    if target_count == 0:
        if score is not None:
            errors.append("an empty plan must have an unmeasured mutation score")
    elif not isinstance(score, (int, float)):
        errors.append("mutation score is unmeasured")
    elif score + 1e-9 < args.minimum:
        errors.append(f"mutation score {score:.2f}% is below {args.minimum:.2f}%")

    print(
        json.dumps(
            {
                "targets": target_count,
                "evidence_attempts": len(records),
                "campaign": report_campaign,
                "attempted": attempted,
                "complete_measured": measured,
                "score_percent": score,
                "minimum_percent": args.minimum,
            },
            indent=2,
        )
    )
    for error in errors:
        print(f"::error::{error}", file=sys.stderr)
    if target_count == 0 and not errors:
        print("No covered mutation sites matched the changed production files")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
