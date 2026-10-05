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

from .type_helpers import JsonObject

REPO_ROOT = Path(__file__).resolve().parents[2]
SARIF_GATE = REPO_ROOT / ".github" / "scripts" / "gate_sarif.py"
SECURITY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "security.yml"


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    """Override the integration suite's database fixture for this static module."""


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    """This module executes no application database paths."""


def _write_sarif(path: Path, results: list[JsonObject], rules: list[JsonObject]) -> None:
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


@pytest.mark.parametrize(
    "run_patch",
    [
        {"invocations": [{"executionSuccessful": False}]},
        {"invocations": [{"executionSuccessful": "true"}]},
        {"invocations": [{}]},
        {"invocations": []},
        {"invocations": None},
        {"invocations": [{"executionSuccessful": True, "processStartFailureMessage": "failed"}]},
        {
            "invocations": [
                {"executionSuccessful": True, "toolExecutionNotifications": [{"level": "error"}]}
            ]
        },
        {
            "invocations": [
                {
                    "executionSuccessful": True,
                    "toolConfigurationNotifications": [{"level": "error"}],
                }
            ]
        },
        {"invocations": [{"executionSuccessful": True, "toolExecutionNotifications": "invalid"}]},
        {"invocations": [{"executionSuccessful": True, "toolExecutionNotifications": [None]}]},
        {"tool": {"driver": {"name": "UnknownScanner"}}},
        {"tool": {"driver": {"name": []}}},
        {"results": None},
        {"results": [None]},
        {"results": [{}]},
        {"results": [{"ruleId": "bad-score", "properties": {"security-severity": "NaN"}}]},
    ],
)
def test_sarif_gate_rejects_failed_or_incomplete_runs(
    tmp_path: Path, run_patch: JsonObject
) -> None:
    path = tmp_path / "incomplete.sarif"
    run: JsonObject = {"tool": {"driver": {"name": "CodeQL"}}, "results": []}
    run.update(run_patch)
    path.write_text(json.dumps({"version": "2.1.0", "runs": [run]}))
    result = _run_gate(path)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "passed" not in result.stdout


@pytest.mark.parametrize("runs", [[], None, [None]])
def test_sarif_gate_never_equates_absent_runs_with_a_clean_scan(
    tmp_path: Path, runs: object
) -> None:
    path = tmp_path / "missing-runs.sarif"
    path.write_text(json.dumps({"version": "2.1.0", "runs": runs}))
    assert _run_gate(path).returncode == 2


@pytest.mark.parametrize("tool_name", ["CodeQL", "CodeQL command-line toolchain"])
@pytest.mark.parametrize("with_invocation", [False, True])
def test_sarif_gate_accepts_valid_zero_findings_with_optional_invocation_metadata(
    tmp_path: Path, tool_name: str, with_invocation: bool
) -> None:
    path = tmp_path / "clean.sarif"
    run: JsonObject = {"tool": {"driver": {"name": tool_name}}, "results": []}
    if with_invocation:
        run["invocations"] = [
            {"executionSuccessful": True, "toolExecutionNotifications": [{"level": "warning"}]}
        ]
    path.write_text(json.dumps({"version": "2.1.0", "runs": [run]}))
    result = _run_gate(path)
    assert result.returncode == 0, result.stderr


def test_one_failed_sarif_run_rejects_an_otherwise_clean_directory(tmp_path: Path) -> None:
    _write_sarif(tmp_path / "clean.sarif", [], [])
    (tmp_path / "broken.sarif").write_text(json.dumps({"version": "2.1.0", "runs": []}))
    result = _run_gate(tmp_path)
    assert result.returncode == 2
    assert "passed" not in result.stdout


def test_security_workflow_gates_private_codeql_and_scans_deployed_images() -> None:
    workflow = SECURITY_WORKFLOW.read_text()
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())
    db_image = compose["services"]["db"]["image"]
    edge = compose["services"]["edge"]

    assert "upload: never" in workflow
    assert 'python3 .github/scripts/gate_sarif.py "${SARIF_OUTPUT}"' in workflow
    assert "SARIF_OUTPUT: ${{ steps.codeql-analyze.outputs.sarif-output }}" in workflow
    assert workflow.index("name: Preserve CodeQL SARIF") < workflow.index(
        "name: Fail on CodeQL error or high-severity findings"
    )
    assert f"COMPOSE_POSTGRES_IMAGE: {db_image}" in workflow
    assert workflow.count("image: ${{ env.COMPOSE_POSTGRES_IMAGE }}") >= 1
    assert workflow.count("image-ref: ${{ env.COMPOSE_POSTGRES_IMAGE }}") >= 1

    # The edge is now a first-party hardened image.  Security must build the
    # same Dockerfile as local Compose, generate its SBOM, and scan it without
    # the third-party PostgreSQL ignore list masking findings.
    assert edge["image"] == "goatfarm-edge:local"
    assert edge["build"] == {"context": ".", "dockerfile": "docker/edge/Dockerfile"}
    assert f"file: {edge['build']['dockerfile']}" in workflow
    assert "tags: goatfarm-edge:security" in workflow
    assert "image: goatfarm-edge:security" in workflow
    assert "image-ref: goatfarm-edge:security" in workflow
    assert workflow.count("trivyignores: .trivyignore.compose-images") == 1
    assert "COMPOSE_NGINX_IMAGE" not in workflow


def test_security_and_release_workflows_cover_digest_pinned_production_edge() -> None:
    """The built-and-scanned edge must be the signed production artifact."""
    workflow = SECURITY_WORKFLOW.read_text()
    release = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text()
    production = yaml.safe_load((REPO_ROOT / "docker-compose.production.yml").read_text())
    dev_compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())

    production_edge = production["services"]["edge"]
    production_edge_image = production_edge["image"]
    assert production_edge_image == (
        "${GOATFARM_EDGE_IMAGE_REPOSITORY:?set the edge registry repository}"
        "@${GOATFARM_EDGE_IMAGE_DIGEST:?set its sha256 digest}"
    )
    assert "build" not in production_edge
    assert dev_compose["services"]["edge"]["build"]["dockerfile"] == "docker/edge/Dockerfile"

    assert "file: docker/edge/Dockerfile" in workflow
    assert "image-ref: goatfarm-edge:security" in workflow
    assert release.count("file: docker/edge/Dockerfile") == 4
    assert 'cosign sign --yes "${EDGE_IMAGE}@${EDGE_DIGEST}"' in release
    assert "edge_digest=${edge_digest}" in release

    # The scoped ignore list remains only for inherited upstream gosu in the local database;
    # first-party edge findings cannot be waived by this file.
    trivyignore = (REPO_ROOT / ".trivyignore.compose-images").read_text()
    assert dev_compose["services"]["db"]["image"] in trivyignore
    assert dev_compose["services"]["db"]["build"]["dockerfile"] == "docker/postgres/Dockerfile"
    assert 'docker build -f docker/postgres/Dockerfile -t "${COMPOSE_POSTGRES_IMAGE}" .' in workflow
    assert "nginx:" not in trivyignore


def test_privileged_qemu_and_buildkit_toolchain_are_immutable() -> None:
    """Action SHAs must not conceal mutable privileged helper images."""
    workflows = [
        yaml.load(path.read_text(), Loader=yaml.BaseLoader)
        for path in sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml"))
    ]
    buildx_steps = [
        step
        for workflow in workflows
        for job in workflow.get("jobs", {}).values()
        for step in job.get("steps", [])
        if str(step.get("uses", "")).startswith("docker/setup-buildx-action@")
    ]
    assert len(buildx_steps) == 3
    for step in buildx_steps:
        inputs = step["with"]
        assert inputs["version"] == "v0.37.2"
        assert inputs["driver-opts"] == (
            "image=moby/buildkit:v0.33.1@sha256:"
            "cec9f139f45e93c5c69c60f8b07cfad9f43f4ef6b6a6cd917527fea5ff2e3dea"
        )

    qemu_steps = [
        step
        for workflow in workflows
        for job in workflow.get("jobs", {}).values()
        for step in job.get("steps", [])
        if str(step.get("uses", "")).startswith("docker/setup-qemu-action@")
    ]
    assert len(qemu_steps) == 1
    qemu_inputs = qemu_steps[0]["with"]
    assert qemu_inputs["image"] == (
        "docker.io/tonistiigi/binfmt:qemu-v10.2.3-68@sha256:"
        "400a4873b838d1b89194d982c45e5fb3cda4593fbfd7e08a02e76b03b21166f0"
    )
    assert qemu_inputs["platforms"] == "arm64"


@pytest.mark.parametrize(
    "result_patch",
    [
        {"ruleId": "high", "ruleIndex": 0},
        {"ruleId": "high", "ruleIndex": -1},
        {"ruleId": "high", "ruleIndex": 99},
        {"ruleId": "high", "ruleIndex": True},
        {"ruleId": [], "ruleIndex": 0},
        {"ruleId": "low", "level": "ERROR"},
        {"ruleId": "low", "level": []},
    ],
)
def test_sarif_gate_rejects_contradictory_rule_identity_and_invalid_levels(
    tmp_path: Path, result_patch: JsonObject
) -> None:
    path = tmp_path / "contradictory.sarif"
    _write_sarif(
        path,
        [result_patch],
        [
            {"id": "low", "properties": {"security-severity": "1.0"}},
            {"id": "high", "properties": {"security-severity": "9.8"}},
        ],
    )
    result = _run_gate(path)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "passed" not in result.stdout


def test_sarif_gate_accepts_matching_or_index_only_rule_identity(tmp_path: Path) -> None:
    path = tmp_path / "consistent.sarif"
    _write_sarif(
        path,
        [{"ruleId": "low", "ruleIndex": 0}, {"ruleIndex": 0}],
        [
            {
                "id": "low",
                "defaultConfiguration": {"level": "warning"},
                "properties": {"security-severity": "1.0"},
            },
        ],
    )
    assert _run_gate(path).returncode == 0
