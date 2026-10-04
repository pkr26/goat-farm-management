"""Fail-closed contracts for the bounded mutation workflow's final gate."""

from __future__ import annotations

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


@pytest.mark.parametrize(
    ("format_name", "plan", "report", "records"),
    [
        (
            "backend",
            {"mutants": ["m1", "m2"]},
            {
                "campaign_id": "campaign-a",
                "executed": 2,
                "complete_measured": 2,
                "score": 80,
            },
            [
                {"id": "m1", "campaign_id": "campaign-a"},
                {"id": "m2", "campaign_id": "campaign-a"},
            ],
        ),
        (
            "frontend",
            {"targets": ["m1", "m2"], "campaignId": "campaign-a"},
            {
                "campaignId": "campaign-a",
                "scored": 2,
                "score": 0.8,
                "counts": {"KILLED": 1, "SURVIVED": 1},
            },
            [
                {"id": "m1", "campaignId": "campaign-a"},
                {"id": "m2", "campaignId": "campaign-a"},
            ],
        ),
    ],
)
def test_mutation_gate_accepts_exact_complete_evidence(
    tmp_path: Path,
    format_name: str,
    plan: dict[str, Any],
    report: dict[str, Any],
    records: list[dict[str, Any]],
) -> None:
    completed = _run_gate(
        tmp_path,
        format_name=format_name,
        plan=plan,
        report=report,
        records=records,
    )
    assert completed.returncode == 0, completed.stderr


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
