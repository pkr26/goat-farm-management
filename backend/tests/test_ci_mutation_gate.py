"""Fail-closed contracts for the bounded mutation workflow's final gate."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER = REPO_ROOT / ".github" / "scripts" / "check_mutation_report.py"


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    """These subprocess-only contracts do not need the integration database."""


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    """Do not invoke the application suite's database cleanup fixture."""


def _run_gate(
    tmp_path: Path,
    *,
    format_name: str,
    plan: dict[str, Any],
    report: dict[str, Any],
    records: list[dict[str, Any]],
) -> subprocess.CompletedProcess[str]:
    plan_path = tmp_path / f"{format_name}-plan.json"
    report_path = tmp_path / f"{format_name}-report.json"
    results_path = tmp_path / f"{format_name}-results.jsonl"
    plan_path.write_text(json.dumps(plan))
    report_path.write_text(json.dumps(report))
    results_path.write_text("".join(f"{json.dumps(record)}\n" for record in records))
    return subprocess.run(
        [
            sys.executable,
            str(CHECKER),
            "--plan",
            str(plan_path),
            "--report",
            str(report_path),
            "--results",
            str(results_path),
            "--format",
            format_name,
            "--minimum",
            "80",
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def _complete_attempt(format_name: str, ident: str, verdict: str = "KILLED") -> dict[str, Any]:
    selection = ["tests/test_example.py::test_behavior"]
    digest = hashlib.sha256(json.dumps(selection, separators=(",", ":")).encode()).hexdigest()
    if format_name == "backend":
        return {
            "id": ident,
            "campaign_id": "campaign-a",
            "status": verdict,
            "selection_mode": "complete",
            "selection": selection,
            "selection_sha256": digest,
            "n_tests": 1,
            "baseline": {
                "status": "pass",
                "selection_sha256": digest,
                "pytest_receipt": {
                    "exit_code": 0,
                    "collected": 1,
                    "collection_errors": [],
                    "reports": [{"nodeid": selection[0], "when": "call", "outcome": "passed"}],
                },
            },
            "pytest_receipt": {
                "exit_code": 1 if verdict == "KILLED" else 0,
                "collected": 1,
                "collection_errors": [],
                "reports": [
                    {
                        "nodeid": selection[0],
                        "when": "call",
                        "outcome": "failed" if verdict == "KILLED" else "passed",
                        "assertion": verdict == "KILLED",
                    }
                ],
            },
        }
    baseline: dict[str, Any] = {
        "verdict": "SURVIVED",
        "exitCode": 0,
        "receipt": {
            "reason": "passed",
            "suiteErrors": [],
            "unhandledErrors": [],
            "tests": [{"name": "selected behavior", "state": "pass", "hooks": {}, "errors": []}],
        },
    }
    receipt: dict[str, Any] = copy.deepcopy(baseline["receipt"])
    if verdict == "KILLED":
        receipt["reason"] = "failed"
        receipt["tests"] = [
            {
                "name": "selected behavior",
                "state": "fail",
                "hooks": {},
                "errors": [{"name": "AssertionError"}],
            }
        ]
    return {
        "id": ident,
        "campaignId": "campaign-a",
        "verdict": verdict,
        "selectionMode": "complete",
        "selectionSha": digest,
        "tests": 1,
        "totalTests": 1,
        "baseline": baseline,
        "exitCode": 1 if verdict == "KILLED" else 0,
        "receipt": receipt,
    }


def _planned_report(
    format_name: str, killed: int, survived: int = 0
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    targets = [f"m{i}" for i in range(killed + survived)]
    records = [
        _complete_attempt(format_name, ident, "KILLED" if index < killed else "SURVIVED")
        for index, ident in enumerate(targets)
    ]
    statuses = {
        name: count for name, count in (("KILLED", killed), ("SURVIVED", survived)) if count
    }
    score = killed / len(targets) if targets else None
    if format_name == "backend":
        return (
            {"mutants": targets},
            {
                "campaign_id": "campaign-a",
                "executed": len(targets),
                "complete_measured": len(targets),
                "statuses": statuses,
                "score": score * 100 if score is not None else None,
            },
            records,
        )
    return (
        {"targets": targets, "campaignId": "campaign-a"},
        {
            "campaignId": "campaign-a",
            "scored": len(targets),
            "counts": statuses,
            "killed": killed,
            "survived": survived,
            "score": score,
        },
        records,
    )


@pytest.mark.parametrize(
    ("format_name", "plan", "report", "record"),
    [
        (
            "backend",
            {"mutants": ["planned"]},
            {
                "campaign_id": "campaign-a",
                "executed": 1,
                "complete_measured": 1,
                "score": 100,
            },
            {"id": "different", "campaign_id": "campaign-a"},
        ),
        (
            "frontend",
            {"targets": ["planned"], "campaignId": "campaign-a"},
            {
                "campaignId": "campaign-a",
                "scored": 1,
                "score": 1,
                "counts": {"KILLED": 1},
            },
            {"id": "different", "campaignId": "campaign-a"},
        ),
    ],
)
def test_mutation_gate_rejects_same_count_from_a_different_target(
    tmp_path: Path,
    format_name: str,
    plan: dict[str, Any],
    report: dict[str, Any],
    record: dict[str, Any],
) -> None:
    completed = _run_gate(
        tmp_path,
        format_name=format_name,
        plan=plan,
        report=report,
        records=[record],
    )
    assert completed.returncode == 1
    assert "fresh attempt IDs do not exactly match the plan" in completed.stderr


@pytest.mark.parametrize(
    ("format_name", "plan", "report", "record"),
    [
        (
            "backend",
            {"mutants": ["planned"]},
            {
                "campaign_id": "report-campaign",
                "executed": 1,
                "complete_measured": 1,
                "score": 100,
            },
            {"id": "planned", "campaign_id": "evidence-campaign"},
        ),
        (
            "frontend",
            {"targets": ["planned"], "campaignId": "plan-campaign"},
            {
                "campaignId": "plan-campaign",
                "scored": 1,
                "score": 1,
                "counts": {"KILLED": 1},
            },
            {"id": "planned", "campaignId": "evidence-campaign"},
        ),
    ],
)
def test_mutation_gate_rejects_campaign_mismatch(
    tmp_path: Path,
    format_name: str,
    plan: dict[str, Any],
    report: dict[str, Any],
    record: dict[str, Any],
) -> None:
    completed = _run_gate(
        tmp_path,
        format_name=format_name,
        plan=plan,
        report=report,
        records=[record],
    )
    assert completed.returncode == 1
    assert "campaign" in completed.stderr


@pytest.mark.parametrize("format_name", ["backend", "frontend"])
@pytest.mark.parametrize(("killed", "survived"), [(1, 0), (4, 1)])
def test_mutation_gate_accepts_exact_complete_evidence(
    tmp_path: Path, format_name: str, killed: int, survived: int
) -> None:
    plan, report, records = _planned_report(format_name, killed, survived)
    completed = _run_gate(
        tmp_path, format_name=format_name, plan=plan, report=report, records=records
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["raw_score_percent"] == 100 * killed / (killed + survived)


@pytest.mark.parametrize(
    ("format_name", "plan", "report"),
    [
        (
            "backend",
            {"mutants": []},
            {
                "campaign_id": "unmeasured",
                "executed": 0,
                "complete_measured": 0,
                "score": None,
            },
        ),
        (
            "frontend",
            {"targets": [], "campaignId": "campaign-a"},
            {
                "campaignId": "unmeasured",
                "scored": 0,
                "score": None,
                "counts": {},
            },
        ),
    ],
)
def test_mutation_gate_accepts_an_empty_plan_only_when_score_is_unmeasured(
    tmp_path: Path,
    format_name: str,
    plan: dict[str, Any],
    report: dict[str, Any],
) -> None:
    accepted = _run_gate(
        tmp_path,
        format_name=format_name,
        plan=plan,
        report=report,
        records=[],
    )
    assert accepted.returncode == 0, accepted.stderr

    report["score"] = 100 if format_name == "backend" else 1
    rejected = _run_gate(
        tmp_path,
        format_name=format_name,
        plan=plan,
        report=report,
        records=[],
    )
    assert rejected.returncode == 1
    assert "empty plan must have an unmeasured mutation score" in rejected.stderr


@pytest.mark.parametrize(
    ("format_name", "plan", "report", "record"),
    [
        (
            "backend",
            {"mutants": ["m1"]},
            {
                "campaign_id": "campaign-a",
                "executed": 1,
                "complete_measured": 0,
                "score": 100,
            },
            {"id": "m1", "campaign_id": "campaign-a"},
        ),
        (
            "frontend",
            {"targets": ["m1"], "campaignId": "campaign-a"},
            {
                "campaignId": "campaign-a",
                "scored": 1,
                "score": 0.79,
                "counts": {"SURVIVED": 1},
            },
            {"id": "m1", "campaignId": "campaign-a"},
        ),
    ],
)
def test_mutation_gate_rejects_incomplete_or_low_score_reports(
    tmp_path: Path,
    format_name: str,
    plan: dict[str, Any],
    report: dict[str, Any],
    record: dict[str, Any],
) -> None:
    record = _complete_attempt(
        format_name, "m1", "SURVIVED" if format_name == "frontend" else "KILLED"
    )
    completed = _run_gate(
        tmp_path,
        format_name=format_name,
        plan=plan,
        report=report,
        records=[record],
    )
    assert completed.returncode == 1
    expected = "complete-selection verdicts" if format_name == "backend" else "below 80.00%"
    assert expected in completed.stderr


def test_mutation_workflow_tracks_every_campaign_identity_input() -> None:
    workflow = yaml.load(
        (REPO_ROOT / ".github" / "workflows" / "mutation.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    paths = set(workflow["on"]["pull_request"]["paths"])
    assert {
        "backend/app/**",
        "backend/tests/**",
        "backend/alembic/**",
        "backend/scripts/**",
        "backend/pins/**",
        "backend/mutation/**",
        "backend/alembic.ini",
        "backend/.env.example",
        "backend/uv.lock",
        "backend/pyproject.toml",
        "frontend/src/**",
        "frontend/mutation/**",
        "frontend/patches/**",
        "frontend/public/**",
        "frontend/scripts/**",
        "frontend/package.json",
        "frontend/pnpm-lock.yaml",
        "frontend/vite*.config.*",
        "frontend/vitest*.config.*",
        "frontend/vitest.setup.ts",
        "frontend/tsconfig.json",
        "frontend/next.config.ts",
        "frontend/.nvmrc",
        "frontend/Dockerfile",
        "frontend/.dockerignore",
        ".github/**",
        "docker/**",
        "shared/**",
        "docker-compose*.yml",
        "Dockerfile",
        ".dockerignore",
        ".gitignore",
        ".env.example",
        ".trivyignore.compose-images",
        "README.md",
    } <= paths

    jobs = workflow["jobs"]
    for job_name in ("backend-changed-files", "frontend-changed-files"):
        run = next(
            step["run"]
            for step in jobs[job_name]["steps"]
            if step.get("name", "").startswith("Run bounded complete-selection")
        )
        assert "--results mutation/results.jsonl" in run


@pytest.mark.parametrize("format_name", ["backend", "frontend"])
@pytest.mark.parametrize(
    "score", [float("nan"), float("inf"), -float("inf"), -1, 101, True, "100", None]
)
def test_mutation_gate_rejects_nonfinite_unbounded_and_non_numeric_scores(
    tmp_path: Path, format_name: str, score: object
) -> None:
    plan, report, records = _planned_report(format_name, 1)
    report["score"] = score
    result = _run_gate(tmp_path, format_name=format_name, plan=plan, report=report, records=records)
    assert result.returncode == 1
    assert "score must be finite" in result.stderr


@pytest.mark.parametrize("format_name", ["backend", "frontend"])
@pytest.mark.parametrize(
    "defect",
    [
        "survivor",
        "timeout",
        "sampled",
        "missing-mode",
        "baseline",
        "partial-count",
        "receipt",
        "infrastructure",
    ],
)
def test_mutation_gate_recomputes_measurement_from_each_raw_attempt(
    tmp_path: Path, format_name: str, defect: str
) -> None:
    plan, report, records = _planned_report(format_name, 1)
    row = records[0]
    status = "status" if format_name == "backend" else "verdict"
    mode = "selection_mode" if format_name == "backend" else "selectionMode"
    if defect == "survivor":
        records[0] = _complete_attempt(format_name, "m0", "SURVIVED")
    elif defect == "timeout":
        row[status] = "INCONCLUSIVE_TIMEOUT"
    elif defect == "sampled":
        row[mode] = "sampled"
    elif defect == "missing-mode":
        del row[mode]
    elif defect == "baseline":
        row["baseline"]["status" if format_name == "backend" else "verdict"] = (
            "INCONCLUSIVE_TIMEOUT"
        )
    elif defect == "partial-count":
        row["n_tests" if format_name == "backend" else "totalTests"] = 2
    elif defect == "receipt":
        del row["pytest_receipt" if format_name == "backend" else "receipt"]
    elif format_name == "backend":
        row["pytest_receipt"]["reports"][0]["when"] = "setup"
    else:
        row["receipt"]["tests"][0]["errors"] = [{"name": "TypeError"}]
    result = _run_gate(tmp_path, format_name=format_name, plan=plan, report=report, records=records)
    assert result.returncode == 1, result.stdout
    assert any(word in result.stderr for word in ("contradict", "complete-selection")), (
        result.stderr
    )


@pytest.mark.parametrize("format_name", ["backend", "frontend"])
def test_mutation_gate_rejects_consistent_but_ineffective_mutations(
    tmp_path: Path, format_name: str
) -> None:
    plan, report, records = _planned_report(format_name, 3, 2)
    result = _run_gate(tmp_path, format_name=format_name, plan=plan, report=report, records=records)
    assert result.returncode == 1
    assert "mutation score 60.00% is below 80.00%" in result.stderr


def test_mutation_gate_checks_backend_exact_selection_digest(tmp_path: Path) -> None:
    plan, report, records = _planned_report("backend", 1)
    records[0]["selection"] = ["tests/test_different.py::test_other"]
    result = _run_gate(tmp_path, format_name="backend", plan=plan, report=report, records=records)
    assert result.returncode == 1
    assert "complete-selection" in result.stderr


@pytest.mark.parametrize("format_name", ["backend", "frontend"])
def test_mutation_gate_rejects_forged_status_totals_even_with_correct_score(
    tmp_path: Path, format_name: str
) -> None:
    plan, report, records = _planned_report(format_name, 1)
    report["statuses" if format_name == "backend" else "counts"] = {"SURVIVED": 1}
    result = _run_gate(tmp_path, format_name=format_name, plan=plan, report=report, records=records)
    assert result.returncode == 1
    assert "status counts contradict raw evidence" in result.stderr


@pytest.mark.parametrize(
    "defect", ["foreign-node", "missing-node", "short-collection", "extra-collection"]
)
def test_backend_gate_binds_receipt_to_the_exact_selected_tests(
    tmp_path: Path, defect: str
) -> None:
    plan, report, records = _planned_report("backend", 1)
    receipt = records[0]["pytest_receipt"]
    if defect == "foreign-node":
        receipt["reports"][0]["nodeid"] = "tests/test_unrelated.py::test_unrelated"
    elif defect == "missing-node":
        del receipt["reports"][0]["nodeid"]
    else:
        receipt["collected"] = 0 if defect == "short-collection" else 2
    result = _run_gate(tmp_path, format_name="backend", plan=plan, report=report, records=records)
    assert result.returncode == 1
    assert json.loads(result.stdout)["raw_complete_measured"] == 0
    assert "receipt" in result.stderr


def test_backend_gate_accepts_a_real_fail_fast_shape_without_unrun_call_reports(
    tmp_path: Path,
) -> None:
    plan, report, records = _planned_report("backend", 1)
    row = records[0]
    row["selection"].append("tests/test_example.py::test_later[parameter]")
    digest = hashlib.sha256(
        json.dumps(row["selection"], separators=(",", ":")).encode()
    ).hexdigest()
    row["selection_sha256"] = row["baseline"]["selection_sha256"] = digest
    row["n_tests"] = row["pytest_receipt"]["collected"] = 2
    row["baseline"]["pytest_receipt"]["collected"] = 2
    row["baseline"]["pytest_receipt"]["reports"].append(
        {"nodeid": row["selection"][-1], "when": "call", "outcome": "passed"}
    )
    # The runner passes the full selection, but pytest -x legitimately stops
    # after the first selected assertion failure.
    result = _run_gate(tmp_path, format_name="backend", plan=plan, report=report, records=records)
    assert result.returncode == 0, result.stderr


def test_backend_survivor_needs_outcomes_for_every_selected_test(tmp_path: Path) -> None:
    plan, report, records = _planned_report("backend", 4, 1)
    row = records[-1]
    row["selection"].append("tests/test_example.py::test_missing")
    digest = hashlib.sha256(
        json.dumps(row["selection"], separators=(",", ":")).encode()
    ).hexdigest()
    row["selection_sha256"] = row["baseline"]["selection_sha256"] = digest
    row["n_tests"] = row["pytest_receipt"]["collected"] = 2
    result = _run_gate(tmp_path, format_name="backend", plan=plan, report=report, records=records)
    assert result.returncode == 1
    assert json.loads(result.stdout)["raw_complete_measured"] == 4
