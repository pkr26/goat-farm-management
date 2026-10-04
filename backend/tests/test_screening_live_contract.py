"""Hermetic tests for the opt-in screening live-contract orchestration."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import pytest
import yaml

from app.services.screening.live_contract import (
    MAX_FIXTURE_BYTES,
    LiveContractFailure,
    ProviderTarget,
    _safe_endpoint_metadata,
    execute_from_environment,
    generated_fixture,
    run_live_contract,
)
from app.services.screening.providers import ProviderAnswer, VisionProvider
from app.services.screening.s3 import ScreeningObjectInfo


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def upload(self, key: str, data: bytes, content_type: str) -> None:
        assert content_type == "image/jpeg"
        self.objects[key] = data

    def object_info(self, key: str) -> ScreeningObjectInfo | None:
        value = self.objects.get(key)
        if value is None:
            return None
        return ScreeningObjectInfo(
            size=len(value),
            etag='"probe-etag"',
            version_id="probe-version",
            content_type="image/jpeg",
            metadata={},
        )

    def download(
        self,
        key: str,
        *,
        max_bytes: int,
        etag: str | None = None,
        version_id: str | None = None,
    ) -> bytes:
        assert etag == '"probe-etag"'
        assert version_id == "probe-version"
        value = self.objects[key]
        assert len(value) <= max_bytes
        return value

    def delete_permanently(self, keys: Sequence[str]) -> None:
        for key in keys:
            self.deleted.append(key)
            self.objects.pop(key, None)


class FakeProvider:
    name = "sandbox-provider"
    model = "sandbox-model-v1"

    def __init__(self, *, answer: str | None = None) -> None:
        self.answer = answer or json.dumps(
            {
                "flagged": False,
                "confidence": 0.87,
                "quality_problem": True,
                "observations": [],
            }
        )
        self.seen: bytes | None = None
        self.closed = False

    async def complete(self, image_jpeg: bytes, system_prompt: str) -> ProviderAnswer:
        assert "JSON object" in system_prompt
        self.seen = image_jpeg
        return ProviderAnswer(
            text=self.answer,
            provider=self.name,
            model=self.model,
            latency_ms=4,
        )

    async def aclose(self) -> None:
        self.closed = True


def test_retained_endpoint_metadata_cannot_include_url_credentials_or_query() -> None:
    endpoint = "https://account:secret@provider.invalid:8443/private/key?api_key=secret"
    assert _safe_endpoint_metadata(endpoint) == "https://provider.invalid:8443"


async def test_probe_crosses_storage_provider_and_permanent_cleanup() -> None:
    storage = FakeStorage()
    provider = FakeProvider()
    fixture = generated_fixture()

    report = await run_live_contract(
        storage=storage,
        providers=[
            ProviderTarget(cast(VisionProvider, provider), endpoint="https://sandbox.invalid/v1")
        ],
        object_prefix="sandbox",
        storage_endpoint="https://objects.invalid",
        storage_region="test-1",
        fixture=fixture,
    )

    assert 0 < len(fixture) <= MAX_FIXTURE_BYTES
    assert provider.seen == fixture
    assert provider.closed is True
    assert storage.objects == {}
    assert len(storage.deleted) == 1
    assert "contract-probe" in storage.deleted[0]
    assert report["status"] == "passed"
    assert report["storage"] == {
        "adapter": "ScreeningStorage",
        "endpoint": "https://objects.invalid",
        "region": "test-1",
        "upload_read_match": True,
        "permanent_delete_verified": True,
    }
    providers = cast(list[dict[str, object]], report["providers"])
    assert providers[0]["name"] == "sandbox-provider"
    assert providers[0]["model"] == "sandbox-model-v1"
    serialized = json.dumps(report)
    assert "probe-etag" not in serialized
    assert storage.deleted[0] not in serialized
    assert provider.answer not in serialized


async def test_schema_drift_fails_closed_and_still_cleans_up() -> None:
    storage = FakeStorage()
    provider = FakeProvider(answer='{"flagged": "not-a-boolean"}')

    with pytest.raises(LiveContractFailure) as caught:
        await run_live_contract(
            storage=storage,
            providers=[
                ProviderTarget(
                    cast(VisionProvider, provider), endpoint="https://sandbox.invalid/v1"
                )
            ],
            object_prefix="sandbox",
            storage_endpoint="https://objects.invalid",
            storage_region="test-1",
        )

    assert caught.value.stage == "provider_contract"
    assert caught.value.component == "sandbox-provider"
    assert provider.closed is True
    assert storage.objects == {}
    assert len(storage.deleted) == 1


async def test_absent_opt_in_skips_without_resolving_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SCREENING_CONTRACT_ENABLED", raising=False)
    monkeypatch.setenv("GOATFARM_S3_ACCESS_KEY_ID", "must-not-be-read")

    report, exit_code = await execute_from_environment()

    assert exit_code == 0
    assert report["status"] == "skipped"
    assert "must-not-be-read" not in json.dumps(report)


async def test_enabled_but_incomplete_configuration_fails_without_secret_echo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SCREENING_CONTRACT_ENABLED", "true")
    monkeypatch.setenv("GOATFARM_S3_ACCESS_KEY_ID", "live-secret-marker")
    monkeypatch.setenv("GITHUB_SHA", "revision-secret-marker")
    monkeypatch.delenv("GOATFARM_S3_BUCKET", raising=False)
    monkeypatch.delenv("GOATFARM_S3_SECRET_ACCESS_KEY", raising=False)
    monkeypatch.delenv("GOATFARM_SCREENING_ANTHROPIC_API_KEY", raising=False)

    report, exit_code = await execute_from_environment()

    assert exit_code == 2
    assert report["status"] == "failed"
    assert report["failed_stage"] == "configuration"
    assert "live-secret-marker" not in json.dumps(report)
    assert "revision-secret-marker" not in json.dumps(report)


def test_live_workflow_is_isolated_optional_and_retains_sanitized_evidence() -> None:
    root = Path(__file__).resolve().parents[2]
    workflow_path = root / ".github/workflows/screening-live-contract.yml"
    workflow = cast(
        dict[str, object],
        yaml.load(workflow_path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader),
    )
    triggers = cast(dict[str, object], workflow["on"])
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    jobs = cast(dict[str, dict[str, object]], workflow["jobs"])
    probe = jobs["probe"]
    assert probe["environment"] == "screening-contract"
    assert "env" not in probe
    steps = cast(list[dict[str, object]], probe["steps"])
    run_steps = [cast(str, step.get("run", "")) for step in steps]
    assert any("app.services.screening.live_contract" in command for command in run_steps)
    probe_step = next(
        step
        for step in steps
        if "app.services.screening.live_contract" in cast(str, step.get("run", ""))
    )
    probe_env = cast(dict[str, str], probe_step["env"])
    assert "SCREENING_CONTRACT_ENABLED" in probe_env
    assert "GOATFARM_S3_ACCESS_KEY_ID" in probe_env
    artifact_step = next(
        step for step in steps if "actions/upload-artifact@" in cast(str, step.get("uses", ""))
    )
    assert artifact_step["if"] == "always()"
    artifact_settings = cast(dict[str, str], artifact_step["with"])
    assert artifact_settings["path"] == "backend/screening-live-contract.json"
    assert "env" not in artifact_step

    runbook = (root / "ops/SCREENING_LIVE_CONTRACT.md").read_text(encoding="utf-8")
    assert "never runs on a pull request or push" in runbook
    assert "fail the run" in runbook
    assert "never contains the bucket, object key, credentials" in runbook
