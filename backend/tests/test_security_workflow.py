"""Regression coverage for the security workflow's local-enforcement path.

These tests intentionally avoid the database: they validate the CI artifact
that protects it, including the fallback required for private repositories
without GitHub Code Security SARIF upload access.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SARIF_GATE = REPO_ROOT / ".github" / "scripts" / "gate_sarif.py"
SECURITY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "security.yml"


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    """Override the integration suite's database fixture for this static module."""


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    """This module executes no application database paths."""


def _write_sarif(path: Path, results: list[dict], rules: list[dict]) -> None:
    path.write_text(
        json.dumps(
            {
                "version": "2.1.0",
                "runs": [
                    {"tool": {"driver": {"name": "CodeQL", "rules": rules}}, "results": results}
                ],
            }
        )
    )


def _run_gate(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SARIF_GATE), str(path)],
        check=False,
        capture_output=True,
        text=True,
    )


def test_sarif_gate_enforces_error_and_high_security_results(tmp_path: Path) -> None:
    output = tmp_path / "results"
    output.mkdir()
    _write_sarif(
        output / "codeql.sarif",
        [
            {"ruleId": "low", "level": "warning"},
            {"ruleId": "error", "level": "error", "locations": []},
            {"ruleId": "high", "ruleIndex": 2},
        ],
        [
            {"id": "low", "properties": {"security-severity": "3.1"}},
            {"id": "error"},
            {"id": "high", "properties": {"security-severity": "7.0"}},
        ],
    )

    result = _run_gate(output)

    assert result.returncode == 1
    assert "codeql.sarif: error" in result.stderr
    assert "codeql.sarif: high" in result.stderr
    assert "codeql.sarif: low" not in result.stderr


def test_sarif_gate_allows_lower_severity_findings_and_rejects_missing_output(
    tmp_path: Path,
) -> None:
    output = tmp_path / "results"
    output.mkdir()
    _write_sarif(
        output / "codeql.sarif",
        [{"ruleId": "low", "level": "warning"}],
        [{"id": "low", "properties": {"security-severity": "6.9"}}],
    )

    passed = _run_gate(output)
    missing = _run_gate(tmp_path / "missing")

    assert passed.returncode == 0, passed.stderr
    assert missing.returncode == 2
    assert "No SARIF files found" in missing.stderr


def test_security_workflow_gates_private_codeql_and_scans_exact_compose_images() -> None:
    workflow = SECURITY_WORKFLOW.read_text()
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())
    db_image = compose["services"]["db"]["image"]
    edge_image = compose["services"]["edge"]["image"]

    assert "upload: never" in workflow
    assert 'python3 .github/scripts/gate_sarif.py "${SARIF_OUTPUT}"' in workflow
    assert "SARIF_OUTPUT: ${{ steps.codeql-analyze.outputs.sarif-output }}" in workflow
    assert workflow.index("name: Preserve CodeQL SARIF") < workflow.index(
        "name: Fail on CodeQL error or high-severity findings"
    )
    assert f"COMPOSE_POSTGRES_IMAGE: {db_image}" in workflow
    assert f"COMPOSE_NGINX_IMAGE: {edge_image}" in workflow
    for image_name in ("${{ env.COMPOSE_POSTGRES_IMAGE }}", "${{ env.COMPOSE_NGINX_IMAGE }}"):
        assert workflow.count(f"image: {image_name}") >= 1
        assert workflow.count(f"image-ref: {image_name}") >= 1
