"""Enforce a provenance-bound mutation score for the exact CI target plan."""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, TypeGuard


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


def _number(value: object) -> TypeGuard[int | float]:
    return type(value) is int or (type(value) is float and math.isfinite(value))


def _receipt_verdict(record: dict[str, Any], format_name: str) -> str | None:
    """Reclassify structured test outcomes; a label alone cannot prove a kill."""
    receipt = record.get("pytest_receipt" if format_name == "backend" else "receipt")
    if not isinstance(receipt, dict):
        return None
    if format_name == "backend":
        if (
            type(receipt.get("exit_code")) is not int
            or receipt.get("collection_errors") != []
            or not (type(receipt.get("collected")) is int and receipt["collected"] > 0)
        ):
            return None
        reports = receipt.get("reports")
        selection = record.get("selection")
        if (
            not isinstance(selection, list)
            or not all(isinstance(node, str) for node in selection)
            or receipt["collected"] != len(selection)
        ):
            return None
        selected = set(selection)
        if (
            not isinstance(reports, list)
            or not reports
            or any(
                not isinstance(row, dict)
                or row.get("when") not in ("setup", "call", "teardown")
                or row.get("outcome") not in ("passed", "failed", "skipped")
                or not isinstance(row.get("nodeid"), str)
                or row["nodeid"] not in selected
                for row in reports
            )
        ):
            return None
        if len({(row["nodeid"], row["when"]) for row in reports}) != len(reports):
            return None
        failures = [row for row in reports if row["outcome"] == "failed"]
        if (
            receipt.get("exit_code") == 0
            and not failures
            and any(row["when"] == "call" and row["outcome"] == "passed" for row in reports)
            and {
                row["nodeid"]
                for row in reports
                if row["when"] == "call" or (row["when"] == "setup" and row["outcome"] == "skipped")
            }
            == selected
        ):
            return "SURVIVED"
        if (
            receipt.get("exit_code") == 1
            and failures
            and all(row["when"] == "call" and row.get("assertion") is True for row in failures)
        ):
            return "KILLED"
    else:
        tests = receipt.get("tests")
        if (
            type(record.get("exitCode")) is not int
            or receipt.get("suiteErrors") != []
            or receipt.get("unhandledErrors") != []
            or receipt.get("reason") not in ("passed", "failed")
            or not isinstance(tests, list)
            or not tests
        ):
            return None
        for test in tests:
            if not isinstance(test, dict) or test.get("state") not in (
                "pass",
                "fail",
                "skip",
                "todo",
            ):
                return None
            hooks = test.get("hooks")
            if not isinstance(hooks, dict) or any(state != "pass" for state in hooks.values()):
                return None
            errors = test.get("errors")
            if not isinstance(errors, list) or (test["state"] != "fail" and errors):
                return None
        failures = [test for test in tests if test["state"] == "fail"]
        if (
            record.get("exitCode") == 0
            and receipt["reason"] == "passed"
            and not failures
            and any(test["state"] == "pass" for test in tests)
        ):
            return "SURVIVED"
        if (
            record.get("exitCode") == 1
            and receipt["reason"] == "failed"
            and failures
            and all(
                isinstance(test.get("errors"), list)
                and test["errors"]
                and all(
                    isinstance(error, dict) and error.get("name") == "AssertionError"
                    for error in test["errors"]
                )
                for test in failures
            )
        ):
            return "KILLED"
    return None


def _complete_record(record: dict[str, Any], format_name: str) -> bool:
    baseline = record.get("baseline")
    if not isinstance(baseline, dict):
        return False
    if format_name == "backend":
        selection = record.get("selection")
        if (
            record.get("selection_mode") != "complete"
            or baseline.get("status") != "pass"
            or type(record.get("n_tests")) is not int
            or not isinstance(selection, list)
            or not selection
            or not all(isinstance(test, str) and test for test in selection)
            or len(set(selection)) != len(selection)
            or record["n_tests"] != len(selection)
        ):
            return False
        digest = hashlib.sha256(json.dumps(selection, separators=(",", ":")).encode()).hexdigest()
        return (
            record.get("selection_sha256") == digest == baseline.get("selection_sha256")
            and _receipt_verdict(
                {"selection": selection, "pytest_receipt": baseline.get("pytest_receipt")},
                "backend",
            )
            == "SURVIVED"
        )
    count = record.get("tests")
    selection_sha = record.get("selectionSha")
    baseline_receipt = baseline.get("receipt")
    baseline_tests = baseline_receipt.get("tests") if isinstance(baseline_receipt, dict) else None
    mutant_receipt = record.get("receipt")
    mutant_tests = mutant_receipt.get("tests") if isinstance(mutant_receipt, dict) else None
    if not isinstance(baseline_tests, list) or not isinstance(mutant_tests, list):
        return False
    for test in [*baseline_tests, *mutant_tests]:
        if not isinstance(test, dict) or not isinstance(test.get("name"), str) or not test["name"]:
            return False
    # Frontend selections count files, not test cases. Compare the complete
    # structured test inventory as well; unlike pytest -x, Vitest runs it all.
    if collections.Counter(test["name"] for test in baseline_tests) != collections.Counter(
        test["name"] for test in mutant_tests
    ):
        return False
    return (
        record.get("selectionMode") == "complete"
        and baseline.get("verdict") == "SURVIVED"
        and _receipt_verdict(baseline, "frontend") == "SURVIVED"
        and type(count) is int
        and count > 0
        and type(record.get("totalTests")) is int
        and count == record["totalTests"]
        and isinstance(selection_sha, str)
        and re.fullmatch(r"[0-9a-f]{64}", selection_sha) is not None
    )


def _measured_evidence(
    records: list[dict[str, Any]], format_name: str
) -> tuple[collections.Counter[str], int, int, list[str]]:
    statuses: collections.Counter[str] = collections.Counter()
    measured = killed = 0
    errors: list[str] = []
    for record in records:
        status = record.get("status" if format_name == "backend" else "verdict")
        if isinstance(status, str):
            statuses[status] += 1
        if status not in ("KILLED", "SURVIVED") or not _complete_record(record, format_name):
            errors.append(
                f"attempt {record.get('id')!r} has no complete-selection verdict "
                "with a passing baseline"
            )
            continue
        if _receipt_verdict(record, format_name) != status:
            errors.append(
                f"attempt {record.get('id')!r} verdict contradicts its structured test receipt"
            )
            continue
        measured += 1
        killed += status == "KILLED"
    return statuses, measured, killed, errors


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
    if not math.isfinite(args.minimum) or not 0 <= args.minimum <= 100:
        parser.error("--minimum must be finite and between 0 and 100")

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
        score = raw_score * 100 if _number(raw_score) else raw_score
        counts = report.get("counts")
        attempted = (
            sum(value for key, value in counts.items() if key != "PENDING" and type(value) is int)
            if isinstance(counts, dict)
            else None
        )
        report_campaign = report.get("campaignId")
        plan_campaign = plan.get("campaignId")
        campaign_key = "campaignId"

    errors: list[str] = []
    evidence_ids = [record.get("id") for record in records if isinstance(record.get("id"), str)]
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

    evidence_campaigns = {
        record[campaign_key] for record in records if isinstance(record.get(campaign_key), str)
    }
    if target_count:
        if len(evidence_campaigns) != 1 or not all(
            isinstance(record.get(campaign_key), str) and record[campaign_key] for record in records
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

    if type(attempted) is not int or attempted != target_count:
        errors.append(f"expected {target_count} fresh attempts, report contains {attempted!r}")
    if type(measured) is not int or measured != target_count:
        errors.append(
            f"expected {target_count} complete-selection verdicts, report contains {measured!r}; "
            "timeouts, invalid mutants, uncovered sites, and infrastructure errors cannot pass"
        )
    statuses, actual_measured, actual_killed, evidence_errors = _measured_evidence(
        records, args.format
    )
    errors.extend(evidence_errors)
    actual_score = 100 * actual_killed / actual_measured if actual_measured else None
    if measured != actual_measured:
        errors.append(
            f"report measured count {measured!r} contradicts raw evidence {actual_measured}"
        )
    status_key = "statuses" if args.format == "backend" else "counts"
    reported_statuses = report.get(status_key)
    if isinstance(reported_statuses, dict):
        if any(type(value) is not int or value < 0 for value in reported_statuses.values()):
            errors.append("reported status counts must be nonnegative integers")
        nonpending = {
            key: value
            for key, value in reported_statuses.items()
            if key != "PENDING" and value != 0
        }
        if nonpending != dict(statuses):
            errors.append("reported status counts contradict raw evidence")
    for field, actual in (
        ("killed", actual_killed),
        ("survived", actual_measured - actual_killed),
    ):
        if field in report and (type(report[field]) is not int or report[field] != actual):
            errors.append(f"reported {field} count contradicts raw evidence")
    mode_key = "selection_mode" if args.format == "backend" else "selectionMode"
    sampled = sum(record.get(mode_key) != "complete" for record in records)
    if "sampled" in report and (type(report["sampled"]) is not int or report["sampled"] != sampled):
        errors.append("reported sampled count contradicts raw evidence")
    if target_count == 0:
        if score is not None:
            errors.append("an empty plan must have an unmeasured mutation score")
    elif not _number(score) or not 0 <= score <= 100:
        errors.append("mutation score must be finite and between 0 and 100")
    elif actual_score is None or not math.isclose(score, actual_score, rel_tol=0, abs_tol=1e-9):
        errors.append(f"reported score {score!r} contradicts raw evidence {actual_score!r}")
    if actual_score is not None and actual_score + 1e-9 < args.minimum:
        errors.append(f"mutation score {actual_score:.2f}% is below {args.minimum:.2f}%")

    print(
        json.dumps(
            {
                "targets": target_count,
                "evidence_attempts": len(records),
                "campaign": report_campaign,
                "attempted": attempted,
                "complete_measured": measured,
                "score_percent": score if _number(score) and 0 <= score <= 100 else None,
                "raw_complete_measured": actual_measured,
                "raw_killed": actual_killed,
                "raw_score_percent": actual_score,
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
