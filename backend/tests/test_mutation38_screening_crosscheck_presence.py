"""A successful real cross-check is a visible provider cohort in the public scoreboard."""

import json

import httpx
import pytest
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import Farm, ScreeningImage, ScreeningRun
from app.services.screening.pipeline import run_screening_cycle
from app.services.screening.providers import ProviderAnswer
from app.services.screening.rotation import ProviderRotation
from app.utils import today

from .conftest import owner_with_farm
from .test_screening import CountingProvider, FakeStorage, _cycle_settings, _jpeg_bytes


@pytest.mark.parametrize(
    "cross_check_error", [False, True], ids=["healthy-disagreement", "provider-error"]
)
async def test_actual_successful_cross_check_retains_a_visible_scoreboard_row(
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
    assert primary.name in by_name
    if cross_check_error:
        assert checker.name not in by_name
    else:
        assert checker.name in by_name
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
