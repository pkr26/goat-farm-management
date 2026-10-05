"""Independent adversarial controls for the ad2f616 evidence gates."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from .test_ci_mutation_gate import _planned_report, _run_gate
from .test_security_workflow import _run_gate as _run_sarif


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    """These CLI controls operate only on temporary evidence files."""


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    """No database records are used."""


def test_backend_gate_rejects_a_failed_baseline_receipt_despite_pass_label(tmp_path: Path) -> None:
    plan, report, records = _planned_report("backend", 1)
    records[0]["baseline"]["pytest_receipt"] = copy.deepcopy(records[0]["pytest_receipt"])
    result = _run_gate(tmp_path, format_name="backend", plan=plan, report=report, records=records)
    assert result.returncode == 1, result.stdout + result.stderr


def test_backend_gate_rejects_duplicate_conflicting_test_outcomes(tmp_path: Path) -> None:
    plan, report, records = _planned_report("backend", 1)
    reports = records[0]["pytest_receipt"]["reports"]
    reports.append({**reports[0], "outcome": "passed", "assertion": False})
    result = _run_gate(tmp_path, format_name="backend", plan=plan, report=report, records=records)
    assert result.returncode == 1, result.stdout + result.stderr


@pytest.mark.parametrize("defect", ["missing-test", "extra-test", "changed-name"])
def test_frontend_gate_binds_the_mutant_test_inventory_to_the_clean_baseline(
    tmp_path: Path, defect: str
) -> None:
    plan, report, records = _planned_report("frontend", 1)
    row = records[0]
    row["baseline"]["receipt"]["tests"][0]["name"] = "selected behavior"
    row["receipt"]["tests"][0]["name"] = "selected behavior"
    if defect == "missing-test":
        row["baseline"]["receipt"]["tests"].append(
            {"name": "also selected", "state": "pass", "hooks": {}, "errors": []}
        )
    elif defect == "extra-test":
        row["receipt"]["tests"].append(
            {"name": "not selected", "state": "pass", "hooks": {}, "errors": []}
        )
    else:
        row["receipt"]["tests"][0]["name"] = "unrelated behavior"
    result = _run_gate(tmp_path, format_name="frontend", plan=plan, report=report, records=records)
    assert result.returncode == 1, result.stdout + result.stderr


@pytest.mark.parametrize("component", ["baseline", "mutant"])
def test_frontend_gate_rejects_missing_hook_outcomes(tmp_path: Path, component: str) -> None:
    plan, report, records = _planned_report("frontend", 1)
    receipt = (
        records[0]["baseline"]["receipt"] if component == "baseline" else records[0]["receipt"]
    )
    del receipt["tests"][0]["hooks"]
    result = _run_gate(tmp_path, format_name="frontend", plan=plan, report=report, records=records)
    assert result.returncode == 1, result.stdout + result.stderr


@pytest.mark.parametrize("component", ["baseline", "mutant"])
def test_frontend_gate_rejects_a_run_reason_that_contradicts_its_test_outcomes(
    tmp_path: Path, component: str
) -> None:
    plan, report, records = _planned_report("frontend", 1)
    receipt = (
        records[0]["baseline"]["receipt"] if component == "baseline" else records[0]["receipt"]
    )
    receipt["reason"] = "failed" if component == "baseline" else "passed"
    result = _run_gate(tmp_path, format_name="frontend", plan=plan, report=report, records=records)
    assert result.returncode == 1, result.stdout + result.stderr


@pytest.mark.parametrize(
    "termination", [{"exitCode": 2}, {"exitSignalName": "SIGKILL"}, {"exitSignalNumber": 9}]
)
def test_sarif_gate_rejects_failed_codeql_process_despite_a_success_flag(
    tmp_path: Path, termination: dict[str, object]
) -> None:
    path = tmp_path / "contradictory.sarif"
    run = {
        "tool": {"driver": {"name": "CodeQL"}},
        "results": [],
        "invocations": [{"executionSuccessful": True, **termination}],
    }
    path.write_text(json.dumps({"version": "2.1.0", "runs": [run]}))
    result = _run_sarif(path)
    assert result.returncode == 2, result.stdout + result.stderr
