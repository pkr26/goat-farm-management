"""Sparse actual stage/model cohorts do not acquire fictional scoreboard counts."""

import httpx

from app.db import get_sessionmaker
from app.models import ScreeningImage
from app.services.screening.pipeline import run_screening_cycle
from app.services.screening.providers import ProviderAnswer
from app.services.screening.rotation import ProviderRotation
from app.utils import today

from .conftest import owner_with_farm
from .test_screening import CountingProvider, FakeStorage, _cycle_settings, _jpeg_bytes


async def test_native_stage_adapter_preserves_actual_gate_and_specialist_model_cohorts(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    day = today()
    key = f"raw/{farm_id}/{day.isoformat()}/actual-stage-models.jpg"
    storage = FakeStorage()
    storage.objects[key] = _jpeg_bytes(160, 80)

    class StageModelProvider(CountingProvider):
        async def complete(self, image_jpeg: bytes, system_prompt: str) -> ProviderAnswer:
            answer = await super().complete(image_jpeg, system_prompt)
            # The typed native provider reports its actually served model.
            # Its specialist and gate can use different model deployments;
            # the journal/scoreboard must conserve that returned metadata.
            return ProviderAnswer(
                text=answer.text,
                provider=self.name,
                model="aaa-specialist" if "veterinary specialist" in system_prompt else "zzz-gate",
                latency_ms=1,
            )

    provider = StageModelProvider(name="native-stage-adapter", model="zzz-gate")
    async with get_sessionmaker()() as db:
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
        summary = await run_screening_cycle(
            db, _cycle_settings(), storage, ProviderRotation([provider])
        )
        assert summary.flagged == 1 and provider.calls == 2
    response = await client.get("/api/screening/stats", headers=owner)
    assert response.status_code == 200, response.text
    rows = response.json()["providers"]
    assert [(row["provider"], row["model"]) for row in rows] == [
        ("native-stage-adapter", "zzz-gate"),
        ("native-stage-adapter", "aaa-specialist"),
    ]
    gate, specialist = rows
    assert (
        gate["gate_runs"],
        gate["gate_flagged"],
        gate["gate_unassessable"],
        gate["gate_errors"],
    ) == (1, 1, 0, 0)
    assert (
        specialist["gate_runs"],
        specialist["gate_flagged"],
        specialist["gate_unassessable"],
        specialist["gate_errors"],
    ) == (0, 0, 0, 0)
    assert gate["avg_gate_latency_ms"] == 1 and gate["avg_gate_confidence"] == "0.710"
    assert specialist["avg_gate_latency_ms"] is specialist["avg_gate_confidence"] is None
    assert (gate["findings_confirmed"], gate["findings_rejected"], gate["findings_pending"]) == (
        0,
        0,
        0,
    )
    assert (
        specialist["findings_confirmed"],
        specialist["findings_rejected"],
        specialist["findings_pending"],
    ) == (0, 0, 1)
    for row in rows:
        assert row["cross_checks"] == row["cross_check_agreements"] == 0
        assert (
            row["healthy_controls_confirmed"],
            row["healthy_controls_rejected"],
            row["healthy_controls_pending"],
            row["healthy_controls_reviewed"],
        ) == (0, 0, 0, 0)
        assert all(
            row[name] is None
            for name in [
                "positive_precision",
                "positive_precision_ci_low",
                "positive_precision_ci_high",
                "healthy_false_negative_rate",
                "healthy_false_negative_ci_low",
                "healthy_false_negative_ci_high",
            ]
        )


async def test_real_model_version_history_has_stable_provider_then_model_tie_order(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    day = today()
    storage = FakeStorage()
    deployments = [
        ("zulu", "aaa-model"),
        ("alfa", "zzz-model"),
        *[("shared", f"m{i:02d}") for i in range(6, -1, -1)],
    ]
    for index, (name, model) in enumerate(deployments):
        key = f"raw/{farm_id}/{day.isoformat()}/model-history-{index}.jpg"
        storage.objects[key] = _jpeg_bytes(80, 160 + index)
        provider = CountingProvider(name=name, model=model)
        async with get_sessionmaker()() as db:
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
            summary = await run_screening_cycle(
                db, _cycle_settings(), storage, ProviderRotation([provider])
            )
            assert summary.healthy == 1 and summary.flagged == summary.errors == 0
            assert provider.calls == 1
    response = await client.get("/api/screening/stats", headers=owner)
    assert response.status_code == 200, response.text
    rows = response.json()["providers"]
    assert [(row["provider"], row["model"]) for row in rows] == [
        ("alfa", "zzz-model"),
        *[("shared", f"m{i:02d}") for i in range(7)],
        ("zulu", "aaa-model"),
    ]
    assert all(
        row["gate_runs"] == 1 and row["gate_flagged"] == row["gate_errors"] == 0 for row in rows
    )
