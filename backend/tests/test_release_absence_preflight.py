"""Immutable-release preflight contract; GitHub and GHCR use local transport doubles."""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

SCRIPT = Path(__file__).resolve().parents[2] / ".github/scripts/check_release_absence.py"


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    pass


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    pass


@pytest.fixture
def preflight(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    spec = importlib.util.spec_from_file_location("release_absence_preflight", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name, value in {
        "RELEASE_OWNER": "Example",
        "GITHUB_REPOSITORY": "Example/project",
        "TAG": "v1.0.0",
        "GITHUB_ACTOR": "synthetic-actor",
        "GH_TOKEN": "synthetic-token-never-log",
        "BACKEND_IMAGE": "ghcr.io/example/goatfarm-backend",
        "FRONTEND_IMAGE": "ghcr.io/example/goatfarm-frontend",
        "EDGE_IMAGE": "ghcr.io/example/goatfarm-edge",
    }.items():
        monkeypatch.setenv(name, value)
    return module


class Response:
    def __init__(self, status: int, payload: object) -> None:
        self.status = status
        self.payload = json.dumps(payload).encode()

    def read(self, limit: int) -> bytes:
        return self.payload[:limit]

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *args: object) -> None:
        pass


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("first_user_release", 0),
        ("first_org_release", 0),
        ("authenticated_manifest_404", 0),
        ("existing_release_later_page", 1),
        ("existing_draft_release_later_page", 1),
        ("existing_manifest", 1),
        ("github_auth_error", 1),
        ("github_timeout", 1),
        ("github_invalid_json", 1),
        ("github_missing_pages", 1),
        ("github_malformed_packages", 1),
        ("github_packages_error", 1),
        ("owner_lookup_error", 1),
        ("ghcr_pull_token_error", 1),
        ("ghcr_empty_pull_token", 1),
        ("ghcr_malformed_pull_token", 1),
        ("ghcr_invalid_token_json", 1),
        ("ghcr_manifest_401", 1),
        ("ghcr_manifest_403", 1),
        ("ghcr_manifest_500", 1),
        ("ghcr_manifest_timeout", 1),
    ],
)
def test_release_preflight_requires_completed_inspections(
    preflight: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    scenario: str,
    expected: int,
) -> None:
    api_calls: list[list[str]] = []
    http_calls: list[tuple[str, str]] = []

    def gh(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        api_calls.append(command)
        assert command[:2] == ["gh", "api"]
        assert "-X" not in command
        endpoint = command[-1]
        value: object
        if "/releases?" in endpoint:
            assert "--paginate" in command and "--slurp" in command
            if scenario == "github_auth_error":
                return subprocess.CompletedProcess(command, 4, "", "synthetic-token-never-log")
            if scenario == "github_timeout":
                raise subprocess.TimeoutExpired(command, 60)
            if scenario == "github_invalid_json":
                return subprocess.CompletedProcess(command, 0, "not-json", "")
            if scenario == "github_missing_pages":
                value = []
            else:
                value = (
                    [[], [{"tag_name": "v1.0.0", "draft": scenario.startswith("existing_draft")}]]
                    if scenario
                    in {"existing_release_later_page", "existing_draft_release_later_page"}
                    else [[]]
                )
        elif endpoint == "users/example":
            if scenario == "owner_lookup_error":
                return subprocess.CompletedProcess(command, 1, "", "HTTP 503")
            value = {"type": "Organization" if scenario == "first_org_release" else "User"}
        else:
            assert endpoint == (
                "orgs/example/packages?package_type=container&per_page=100"
                if scenario == "first_org_release"
                else "users/example/packages?package_type=container&per_page=100"
            )
            if scenario == "github_packages_error":
                return subprocess.CompletedProcess(command, 1, "", "HTTP 403")
            if scenario == "github_malformed_packages":
                value = [[{"name": None}]]
            elif scenario in {"first_user_release", "first_org_release"}:
                value = [[]]
            else:
                value = [[], [{"name": "goatfarm-backend"}]]
        return subprocess.CompletedProcess(command, 0, json.dumps(value), "")

    def http(request: urllib.request.Request, **kwargs: Any) -> Response:
        http_calls.append((request.get_method(), request.full_url))
        assert request.full_url.startswith("https://ghcr.io/")
        assert request.get_method() in {"GET", "HEAD"}
        if request.full_url.startswith("https://ghcr.io/token?"):
            if scenario == "ghcr_pull_token_error":
                raise urllib.error.HTTPError(
                    request.full_url, 403, "denied", Message(), io.BytesIO()
                )
            if scenario == "ghcr_invalid_token_json":
                response = Response(200, {})
                response.payload = b"not-json"
                return response
            if scenario == "ghcr_malformed_pull_token":
                return Response(200, {"token": ["not-a-token"]})
            return Response(
                200, {"token": "" if scenario == "ghcr_empty_pull_token" else "fixture-pull-token"}
            )
        assert request.get_method() == "HEAD"
        assert request.get_header("Authorization") == "Bearer fixture-pull-token"
        if scenario == "existing_manifest":
            return Response(200, {})
        if scenario == "ghcr_manifest_timeout":
            raise urllib.error.URLError("synthetic-token-never-log")
        code = int(scenario.rsplit("_", 1)[1]) if scenario.startswith("ghcr_manifest_") else 404
        raise urllib.error.HTTPError(request.full_url, code, "synthetic", Message(), io.BytesIO())

    monkeypatch.setattr(preflight.subprocess, "run", gh)
    monkeypatch.setattr(preflight.urllib.request, "urlopen", http)
    assert preflight.main() == expected
    assert "synthetic-token-never-log" not in capsys.readouterr().out
    if scenario in {
        "first_user_release",
        "first_org_release",
        "existing_release_later_page",
        "existing_draft_release_later_page",
    }:
        assert http_calls == []
    if scenario in {"existing_release_later_page", "existing_draft_release_later_page"}:
        assert len(api_calls) == 1


def test_immutable_preflight_can_see_drafts_before_any_image_publication() -> None:
    workflow: dict[str, Any] = yaml.safe_load(
        (SCRIPT.parents[1] / "workflows/release.yml").read_text()
    )
    images = workflow["jobs"]["images"]
    # Successful reader-only release listings omit drafts. The preflight's
    # GET requests need a writer-authorized token to establish actual absence.
    assert images["permissions"]["contents"] == "write"
    steps = images["steps"]
    preflight_index = next(
        index
        for index, step in enumerate(steps)
        if "check_release_absence.py" in step.get("run", "")
    )
    assert steps[preflight_index]["env"]["GH_TOKEN"] == "${{ github.token }}"
    prerequisite_index = next(
        index
        for index, step in enumerate(steps)
        if step.get("name") == "Require successful CI and Security for the tagged main commit"
    )
    assert prerequisite_index < preflight_index
    publish_indexes = [
        index for index, step in enumerate(steps) if step.get("with", {}).get("push") is True
    ]
    assert publish_indexes and preflight_index < min(publish_indexes)
