"""Public scoreboards and exported clinical examples conserve actual model evidence."""

import hashlib
import json
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

import app.api.screening as screening_api
from app.db import get_sessionmaker
from app.models import Farm, ScreeningCrop, ScreeningFinding, ScreeningImage, ScreeningRun
from app.services.screening.pipeline import _enqueue_healthy_control, run_screening_cycle
from app.services.screening.providers import ProviderAnswer
from app.services.screening.rotation import ProviderRotation
from app.utils import today, utcnow

from .conftest import owner_with_farm
from .test_screening import CountingProvider, FakeStorage, _cycle_settings, _jpeg_bytes
from .type_helpers import Headers


async def _screen_photo(
    owner: Headers,
    *,
    name: str,
    verdict: str = "HEALTHY",
    crop_detection: bool = False,
    height: int = 160,
) -> int:
    farm_id = int(owner["X-Farm-Id"])
    day = today()
    key = f"raw/{farm_id}/{day.isoformat()}/{name}.jpg"
    storage = FakeStorage()
    storage.objects[key] = _jpeg_bytes(160, 80) if verdict == "FLAGGED" else _jpeg_bytes(80, height)

    class ClinicalProvider(CountingProvider):
        async def complete(self, image_jpeg: bytes, system_prompt: str) -> ProviderAnswer:
            if verdict == "UNASSESSABLE" and "Find every goat" not in system_prompt:
                self.calls += 1
                return ProviderAnswer(
                    text=json.dumps(
                        {
                            "flagged": False,
                            "quality_problem": True,
                            "confidence": 0.95,
                            "observations": [],
                        }
                    ),
                    provider=self.name,
                    model=self.model,
                    latency_ms=1,
                )
            return await super().complete(image_jpeg, system_prompt)

    provider = ClinicalProvider(name=name, fail=verdict == "ERROR")
    async with get_sessionmaker()() as db:
        image = ScreeningImage(
            farm_id=farm_id,
            s3_bucket=storage.bucket,
            s3_key=key,
            captured_date=day,
            status="PENDING",
        )
        db.add(image)
        await db.commit()
        summary = await run_screening_cycle(
            db,
            _cycle_settings(crop_detection=crop_detection),
            storage,
            ProviderRotation([provider]),
        )
        assert summary.claimed == 1 and image.status == verdict
        return image.id


@pytest.mark.parametrize("verdict", ["HEALTHY", "FLAGGED", "UNASSESSABLE", "ERROR"])
async def test_scoreboard_counts_each_actual_gate_verdict_without_fictional_averages(
    client: httpx.AsyncClient, verdict: str
) -> None:
    owner = await owner_with_farm(client)
    await _screen_photo(owner, name="one-verdict", verdict=verdict)
    response = await client.get("/api/screening/stats", headers=owner)
    assert response.status_code == 200, response.text
    assert response.json()["window_days"] == 30
    rows = response.json()["providers"]
    assert len(rows) == 1
    row = rows[0]
    assert row["provider"] == "one-verdict" and row["model"] == "fake-gate-1"
    assert (
        row["gate_runs"],
        row["gate_flagged"],
        row["gate_unassessable"],
        row["gate_errors"],
    ) == (1, int(verdict == "FLAGGED"), int(verdict == "UNASSESSABLE"), int(verdict == "ERROR"))
    assert row["avg_gate_latency_ms"] == (None if verdict == "ERROR" else 1)
    assert (
        row["avg_gate_confidence"]
        == {"HEALTHY": "0.930", "FLAGGED": "0.710", "UNASSESSABLE": "0.950", "ERROR": None}[verdict]
    )
    assert row["cross_checks"] == row["cross_check_agreements"] == 0
    assert row["findings_pending"] == int(verdict == "FLAGGED")
    assert (
        row["findings_confirmed"]
        == row["findings_rejected"]
        == row["healthy_controls_reviewed"]
        == 0
    )


@pytest.mark.parametrize(
    "cross_check_error", [False, True], ids=["healthy-disagreement", "provider-error"]
)
async def test_scoreboard_counts_only_successful_actual_cross_checks(
    client: httpx.AsyncClient, cross_check_error: bool
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    day = today()
    key = f"raw/{farm_id}/{day.isoformat()}/crosscheck.jpg"
    storage = FakeStorage(objects={key: _jpeg_bytes(160, 80)})

    class HealthyChecker(CountingProvider):
        async def complete(self, image_jpeg: bytes, system_prompt: str) -> ProviderAnswer:
            if self.fail:
                return await super().complete(image_jpeg, system_prompt)
            self.calls += 1
            return ProviderAnswer(
                text=json.dumps({"flagged": False, "confidence": 0.93, "observations": []}),
                provider=self.name,
                model=self.model,
                latency_ms=1,
            )

    primary = CountingProvider(name="actual-primary")
    checker = HealthyChecker(name="actual-checker", fail=cross_check_error)
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        local_day = today(farm.timezone)
        providers = [primary, checker] if local_day.toordinal() % 2 == 0 else [checker, primary]
        rotation = ProviderRotation(providers)
        assert rotation.primary_for(local_day) is primary
        db.add(
            ScreeningImage(
                farm_id=farm_id,
                s3_bucket=storage.bucket,
                s3_key=key,
                captured_date=day,
                status="PENDING",
            )
        )
        await db.commit()
        summary = await run_screening_cycle(db, _cycle_settings(), storage, rotation)
        assert summary.flagged == 1 and checker.calls == 1
        runs = list(
            (
                await db.execute(select(ScreeningRun).where(ScreeningRun.provider == checker.name))
            ).scalars()
        )
        assert len(runs) == 1 and runs[0].stage == "CROSS_CHECK"
        assert runs[0].run_status == ("ERROR" if cross_check_error else "OK")
    response = await client.get("/api/screening/stats", headers=owner)
    assert response.status_code == 200, response.text
    by_name = {row["provider"]: row for row in response.json()["providers"]}
    assert (
        by_name[primary.name]["cross_checks"]
        == by_name[primary.name]["cross_check_agreements"]
        == 0
    )
    if cross_check_error:
        assert checker.name not in by_name
    else:
        row = by_name[checker.name]
        assert row["cross_checks"] == 1 and row["cross_check_agreements"] == 0
        assert (
            row["gate_runs"]
            == row["gate_flagged"]
            == row["gate_unassessable"]
            == row["gate_errors"]
            == 0
        )
        assert row["avg_gate_latency_ms"] is row["avg_gate_confidence"] is None


async def test_scoreboard_includes_exact_window_edge_and_excludes_other_farms_and_old_history(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client)
    edge_id = await _screen_photo(owner, name="exact-window-edge", height=162)
    old_id = await _screen_photo(owner, name="outside-window", height=163)
    other_owner = await owner_with_farm(client, email="another-scoreboard-owner@farm.in")
    await _screen_photo(other_owner, name="another-farm", height=165)
    now = utcnow()
    async with get_sessionmaker()() as db:
        runs = list(
            (
                await db.execute(
                    select(ScreeningRun).where(ScreeningRun.image_id.in_([edge_id, old_id]))
                )
            ).scalars()
        )
        for run in runs:
            # Retained/restored actual model history keeps its original call facts.
            run.created_at = (
                now - timedelta(days=30) if run.image_id == edge_id else now - timedelta(days=31)
            )
        await db.commit()
    monkeypatch.setattr(screening_api, "utcnow", lambda: now)
    response = await client.get("/api/screening/stats", headers=owner)
    assert response.status_code == 200, response.text
    assert [row["provider"] for row in response.json()["providers"]] == ["exact-window-edge"]


async def test_published_stats_contract_has_a_thirty_day_default_and_zero_count_defaults(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/openapi.json")
    assert response.status_code == 200, response.text
    schema = response.json()
    days = next(
        item
        for item in schema["paths"]["/api/screening/stats"]["get"]["parameters"]
        if item["name"] == "days"
    )
    assert days["schema"]["default"] == 30 and days["schema"]["maximum"] == 365
    fields = schema["components"]["schemas"]["ScreeningProviderStatsOut"]["properties"]
    for name in [
        "gate_unassessable",
        "healthy_controls_confirmed",
        "healthy_controls_rejected",
        "healthy_controls_pending",
        "healthy_controls_reviewed",
    ]:
        assert fields[name]["default"] == 0


@pytest.mark.parametrize("crop_detection", [False, True], ids=["whole-frame", "cropped-goat"])
async def test_export_preserves_reviewed_photo_crop_and_actual_positive_model_verdict(
    client: httpx.AsyncClient, crop_detection: bool
) -> None:
    owner = await owner_with_farm(client)
    image_id = await _screen_photo(
        owner, name="reviewed-positive", verdict="FLAGGED", crop_detection=crop_detection
    )
    await _screen_photo(owner, name="other-real-photo", crop_detection=True, height=161)
    async with get_sessionmaker()() as db:
        image = await db.get(ScreeningImage, image_id)
        assert image is not None
        finding = (
            await db.execute(
                select(ScreeningFinding)
                .join(ScreeningRun, ScreeningRun.id == ScreeningFinding.run_id)
                .where(
                    ScreeningRun.image_id == image_id,
                    ScreeningFinding.crop_id.is_not(None)
                    if crop_detection
                    else ScreeningFinding.crop_id.is_(None),
                )
            )
        ).scalar_one()
        crop = await db.get(ScreeningCrop, finding.crop_id) if finding.crop_id is not None else None
        finding_id = finding.id
        expected = {
            "image_s3_key": image.normalized_key,
            "image_sha256": image.sha256,
            "crop_index": None if crop is None else crop.crop_index,
            "crop_box_1000": None
            if crop is None
            else [crop.box_x, crop.box_y, crop.box_w, crop.box_h],
            "crop_s3_key": None if crop is None else crop.normalized_key,
        }
        assert (crop is not None) == crop_detection
    reviewed = await client.post(
        f"/api/screening/findings/{finding_id}/review",
        headers=owner,
        json={"status": "CONFIRMED", "expected_status": "PENDING_REVIEW", "expected_revision": 0},
    )
    assert reviewed.status_code == 200, reviewed.text
    response = await client.get(
        "/api/screening/export", headers=owner, params={"vet_status": "CONFIRMED"}
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["record_count"] == len(payload["records"]) == 1
    record = payload["records"][0]
    assert record["finding_id"] == finding_id and record["example_kind"] == "POSITIVE_FINDING"
    assert record["model_verdict"] == "flagged" and record["vet_status"] == "CONFIRMED"
    assert record["label"] == "ORF" and record["detected_by"] == "reviewed-positive/fake-gate-1"
    assert all(record[name] == value for name, value in expected.items())


async def test_actual_hash_sample_preserves_confidence_and_one_native_neutral_review(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    assert int(owner["X-Farm-Id"]) == 1
    image_id = await _screen_photo(owner, name="actual-hash-control", height=164)
    async with get_sessionmaker()() as db:
        image = await db.get(ScreeningImage, image_id)
        assert image is not None and image.status == "HEALTHY"
        assert image.sha256 == "bb313505385607d77aadf25bdf99a2a9ca0b9064cb8f7f5749a09c76c5426f63"
        sample_digest = hashlib.sha256(
            f"healthy-control-v1:1:{image.sha256}".encode("ascii")
        ).digest()
        assert int.from_bytes(sample_digest[:8], "big") == 12228348645082028590
        assert int.from_bytes(sample_digest[:8], "big") % 10 == 0
        gate = (
            await db.execute(
                select(ScreeningRun).where(
                    ScreeningRun.image_id == image_id, ScreeningRun.stage == "GATE"
                )
            )
        ).scalar_one()
        controls = list(
            (
                await db.execute(select(ScreeningFinding).where(ScreeningFinding.run_id == gate.id))
            ).scalars()
        )
        assert len(controls) == 1 and controls[0].evaluation_kind == "HEALTHY_CONTROL"
        assert controls[0].confidence == Decimal("0.930")
        assert gate.detail is not None and gate.detail["healthy_control_sample"] is True
        assert gate.detail.get("served_by") == "actual-hash-control"
        assert gate.detail.get("observation_count") == 0
        assert gate.detail.get("quality_problem") is False
        assert isinstance(gate.detail.get("response_sha256"), str)
        # The exported native helper is safe to retry on its actual committed gate.
        try:
            await _enqueue_healthy_control(db, image, gate)
        except TypeError as exc:
            pytest.fail(f"The actual healthy gate must remain usable for its neutral review: {exc}")
        await db.commit()
        controls = list(
            (
                await db.execute(select(ScreeningFinding).where(ScreeningFinding.run_id == gate.id))
            ).scalars()
        )
        assert len(controls) == 1 and controls[0].confidence == Decimal("0.930")
