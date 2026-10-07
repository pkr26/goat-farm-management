"""Real provider failures keep the operator reason and refund only denied work."""

from typing import cast

import httpx
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import ScreeningDailyBudget, ScreeningImage
from app.services.screening.pipeline import run_screening_cycle
from app.services.screening.providers import OpenAICompatibleProvider
from app.services.screening.rotation import ProviderRotation
from app.services.screening.s3 import ScreeningStorage
from app.utils import today

from .conftest import owner_with_farm
from .test_screening import (
    CountingProvider,
    FakeStorage,
    _cycle_settings,
    _jpeg_bytes,
    _provider_settings,
    _register_fake_objects,
)


async def test_real_transport_denial_refunds_only_the_claim_and_keeps_it_retryable(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    storage = FakeStorage()
    storage.objects[f"raw/{farm_id}/{today().isoformat()}/retry-cap.jpg"] = _jpeg_bytes(200, 100)
    calls: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(503, json={"error": "camera transport temporarily unavailable"})

    settings = _cycle_settings()
    settings.screening_daily_call_budget_per_farm = 1
    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http,
        get_sessionmaker()() as db,
    ):
        await _register_fake_objects(db, farm_id, storage)
        image = (await db.execute(select(ScreeningImage))).scalar_one()
        image.screening_attempts = 4
        await db.commit()
        result = await run_screening_cycle(
            db,
            settings,
            cast(ScreeningStorage, storage),
            ProviderRotation([OpenAICompatibleProvider(_provider_settings(), client=http)]),
        )
        await db.refresh(image)
        budget = (await db.execute(select(ScreeningDailyBudget))).scalar_one()
        assert len(calls) == budget.reserved_calls == 1
        assert result.budget_deferred == 1 and image.screening_attempts == 4
        assert image.status == "ERROR" and image.error == (
            "Daily screening call budget reached; unfinished screening waits for the next local day"
        )


async def test_terminal_provider_error_keeps_its_real_safe_failure_reason(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    storage = FakeStorage()
    storage.objects[f"raw/{farm_id}/{today().isoformat()}/terminal-camera.jpg"] = _jpeg_bytes(
        200, 100
    )
    async with get_sessionmaker()() as db:
        await _register_fake_objects(db, farm_id, storage)
        image = (await db.execute(select(ScreeningImage))).scalar_one()
        image.screening_attempts = 4
        await db.commit()
        result = await run_screening_cycle(
            db,
            _cycle_settings(),
            cast(ScreeningStorage, storage),
            ProviderRotation([CountingProvider(name="camera", fail=True)]),
        )
        await db.refresh(image)
        assert result.errors == 1 and image.status == "ERROR" and image.screening_attempts == 5
        assert (
            image.error
            == "screening provider call failed (PROVIDER_ERROR); terminal after 5 attempts"
        )
