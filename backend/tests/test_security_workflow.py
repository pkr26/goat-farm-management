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


def test_security_workflow_scans_the_production_compose_edge_digest() -> None:
    """The digest lockstep must cover docker-compose.production.yml too.

    The production manifest pins its nginx edge with a literal digest that no
    interpolation touches, so the dev-compose assertion above could not see a
    production-only drift: an operator refreshing only the production digest
    would deploy an image the weekly Trivy scans (and the scoped
    .trivyignore.compose-images rationale) never examined (2026-10-01 audit,
    09-3). The production file has no db service, so its only third-party
    image is the edge.
    """
    workflow = SECURITY_WORKFLOW.read_text()
    production = yaml.safe_load((REPO_ROOT / "docker-compose.production.yml").read_text())
    dev_compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())

    production_edge_image = production["services"]["edge"]["image"]
    dev_edge_image = dev_compose["services"]["edge"]["image"]
    # Every production service image that is not an interpolated
    # own-registry reference ("${...}@${...}") is a literal third-party
    # reference; today that must be exactly the edge nginx digest.
    literal_images = {
        name: service["image"]
        for name, service in production["services"].items()
        if not service["image"].startswith("${")
    }
    assert set(literal_images) == {"edge"}
    assert literal_images["edge"] == production_edge_image
    # The scanned image and both deployed manifests must be the same digest.
    assert production_edge_image == dev_edge_image
    assert f"COMPOSE_NGINX_IMAGE: {production_edge_image}" in workflow
    # The ignore file's per-image rationale headers name the same digests the
    # scans use, keeping the freshness-marker refresh honest about which
    # pinned images it evaluated.
    trivyignore = (REPO_ROOT / ".trivyignore.compose-images").read_text()
    for image in (production_edge_image, dev_compose["services"]["db"]["image"]):
        assert image in trivyignore
