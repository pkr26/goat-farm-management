"""Real native queue admission conserves farm-local durable and retained spend."""

import asyncio
import datetime as dt
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_sessionmaker
from app.models import Farm, ScreeningDailyBudget, ScreeningImage, ScreeningRun
from app.services.screening import pipeline
from app.services.screening.budget import reserve_provider_attempt

from .conftest import owner_with_farm
from .test_mutation38_screening_queue_native_contracts import HOUR, NOW, STALE, _image


async def _retained_call(db: AsyncSession, image: ScreeningImage, when: dt.datetime) -> None:
    """Initially retained logical call with intact real image ancestry."""
    db.add(
        ScreeningRun(
            farm_id=image.farm_id,
            image_id=image.id,
            stage="GATE",
            run_status="OK",
            verdict="healthy",
            confidence=0.9,
            latency_ms=1,
            provider="retained-camera",
            model="retained-model",
            prompt_version="retained",
            created_at=when,
        )
    )
    await db.flush()


@pytest.mark.parametrize(
    "mode, expected",
    [
        ("omitted-cap", True),
        ("disabled-cap", True),
        ("one-charge", False),
        ("midnight-legacy", False),
        ("foreign-counter", False),
        ("current-counter", True),
    ],
    ids=[
        "default-disabled",
        "explicit-disabled",
        "cap-one",
        "local-midnight",
        "farm-isolation",
        "no-double-charge",
    ],
)
async def test_native_claims_conserve_local_legacy_and_durable_admission(
    client: httpx.AsyncClient, mode: str, expected: bool
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    peer_farm: int | None = None
    if mode == "foreign-counter":
        peer = await owner_with_farm(client, email="unrelated-charge@farm.in")
        peer_farm = int(peer["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        timezone = farm.timezone
        local_date = NOW.replace(tzinfo=dt.UTC).astimezone(ZoneInfo(timezone)).date()
        midnight = dt.datetime.combine(local_date, dt.time.min, tzinfo=ZoneInfo(timezone))
        midnight = midnight.astimezone(dt.UTC).replace(tzinfo=None)
        image = await _image(db, farm_id, mode)
        image_id = image.id
        await db.commit()
        if mode in ("one-charge", "current-counter", "foreign-counter"):
            charged_farm = peer_farm if peer_farm is not None else farm_id
            for _ in range(2 if mode == "current-counter" else 1):
                await reserve_provider_attempt(
                    db,
                    farm_id=charged_farm,
                    timezone_name=timezone,
                    provider="retained-camera",
                    cap=0,
                    attempt_id=str(uuid4()),
                    now=NOW,
                )
        if mode != "one-charge":
            # Legacy calls predate durable admission; current-counter calls
            # are the logical records of the two genuine charges just made.
            count = 2 if mode in ("current-counter", "foreign-counter") else 1
            for _ in range(count):
                await _retained_call(db, image, midnight if mode == "midnight-legacy" else NOW)
            await db.commit()
        cap = 1 if mode == "one-charge" else 2 if mode == "midnight-legacy" else 3
        if mode == "omitted-cap":
            rows, errors, flags = await pipeline._claim_retry_rows(db, 10, NOW, HOUR, STALE)
        else:
            rows, errors, flags = await pipeline._claim_retry_rows(
                db, 10, NOW, HOUR, STALE, 0 if mode == "disabled-cap" else cap
            )
        assert [row.id for row in rows] == ([image_id] if expected else [])
        assert errors == flags == 0
        await db.refresh(image)
        assert image.status == ("PROCESSING" if expected else "PENDING")
        assert image.screening_attempts == (1 if expected else 0)
        if mode == "current-counter":
            counter = await db.get(ScreeningDailyBudget, (farm_id, local_date))
            assert counter is not None and counter.reserved_calls == 2


async def test_native_claim_skips_a_real_owned_row_and_claims_the_free_photo(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        owned = await _image(db, farm_id, "owned-claim", created=NOW - HOUR)
        free = await _image(db, farm_id, "free-claim", created=NOW)
        owned_id, free_id = owned.id, free.id
        await db.commit()
    async with get_sessionmaker()() as holder, get_sessionmaker()() as observer:
        assert (
            await holder.scalar(
                select(ScreeningImage).where(ScreeningImage.id == owned_id).with_for_update()
            )
            is not None
        )
        pid = await holder.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(pid, int)

        async def claim() -> list[int]:
            async with get_sessionmaker()() as db:
                rows, errors, flags = await pipeline._claim_retry_rows(db, 10, NOW, HOUR, STALE, 0)
                assert errors == flags == 0
                return [row.id for row in rows]

        request = asyncio.create_task(claim())
        blocked: list[int] = []
        try:
            deadline = asyncio.get_running_loop().time() + 30
            while not request.done():
                blocked = list(
                    (
                        await observer.execute(
                            text(
                                "SELECT pid FROM pg_stat_activity WHERE datname=current_database() "
                                "AND wait_event_type='Lock' AND :holder=ANY(pg_blocking_pids(pid))"
                            ),
                            {"holder": pid},
                        )
                    ).scalars()
                )
                if blocked:
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError(
                        "Claim gave neither completion nor a PostgreSQL row-lock witness"
                    )
                await asyncio.sleep(0.02)
            await holder.rollback()
            claimed = await asyncio.wait_for(request, timeout=30)
        finally:
            await holder.rollback()
            if not request.done():
                request.cancel()
            await asyncio.gather(request, return_exceptions=True)
    assert not blocked and claimed == [free_id]
    async with get_sessionmaker()() as db:
        persisted_owned = await db.get(ScreeningImage, owned_id)
        persisted_free = await db.get(ScreeningImage, free_id)
        assert (
            persisted_owned is not None
            and persisted_owned.status == "PENDING"
            and persisted_owned.screening_attempts == 0
        )
        assert (
            persisted_free is not None
            and persisted_free.status == "PROCESSING"
            and persisted_free.screening_attempts == 1
        )
