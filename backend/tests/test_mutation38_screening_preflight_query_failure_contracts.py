"""A real failed ownership read must not clobber independently completed work."""

import asyncio
import datetime as dt
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.db import get_engine, get_sessionmaker
from app.models import ScreeningImage, ScreeningRun
from app.models.screening import ScreeningCallReservation
from app.services.screening import pipeline
from app.services.screening.rotation import ProviderRotation
from app.utils import today, utcnow

from .conftest import owner_with_farm
from .test_screening import (
    CountingProvider,
    FakeStorage,
    _cycle_settings,
    _jpeg_bytes,
    _register_fake_objects,
)


async def test_native_failed_preflight_preserves_peer_completed_photo(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    key = f"raw/{farm_id}/{today().isoformat()}/BREEDING/preflight-query.jpg"
    storage = FakeStorage(objects={key: _jpeg_bytes(900, 1400)})
    async with get_sessionmaker()() as setup:
        await _register_fake_objects(setup, farm_id, storage)
        image_id = await setup.scalar(select(ScreeningImage.id).where(ScreeningImage.s3_key == key))
        assert isinstance(image_id, int)
    instant = utcnow()
    clock = instant
    monkeypatch.setattr(pipeline, "utcnow", lambda: clock)
    settings = _cycle_settings(max_images_per_cycle=1)
    settings.screening_daily_call_budget_per_farm = 10
    original_provider = CountingProvider(name="original-before-admission")
    peer_provider = CountingProvider(name="real-reclaim-peer")
    reached = asyncio.Event()
    release = asyncio.Event()
    query_failed = asyncio.Event()
    errors: list[str] = []
    async with (
        get_engine().connect() as original_connection,
        get_sessionmaker()(bind=original_connection) as original,
        get_sessionmaker()() as blocker,
    ):
        # Preserve the pooled connection's configured timeout for later tests.
        original_pid = await original.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(original_pid, int)
        previous_timeout = await original.scalar(text("SHOW statement_timeout"))
        assert isinstance(previous_timeout, str)
        await original.execute(text("SET statement_timeout = '300ms'"))
        native_scalar = original.scalar
        paused = False

        async def delayed_native_scalar(statement: Any, *args: Any, **kwargs: Any) -> Any:
            nonlocal paused
            if not paused:
                paused = True
                reached.set()
                await release.wait()
                try:
                    return await native_scalar(statement, *args, **kwargs)
                except Exception as exc:
                    sqlstate = (
                        getattr(exc.orig, "sqlstate", None) if isinstance(exc, DBAPIError) else None
                    )
                    errors.append(f"{type(exc).__name__}:{sqlstate}")
                    query_failed.set()
                    raise
            return await native_scalar(statement, *args, **kwargs)

        # Scheduling seam only: the original production SELECT is executed
        # unchanged, on its real session, and fails natively in PostgreSQL.
        monkeypatch.setattr(original, "scalar", delayed_native_scalar)
        task = asyncio.create_task(
            pipeline.run_screening_cycle(
                original, settings, storage, ProviderRotation([original_provider])
            )
        )
        try:
            await asyncio.wait_for(reached.wait(), timeout=30)
            clock = instant + dt.timedelta(
                seconds=settings.screening_stale_processing_after_seconds + 60
            )
            async with get_sessionmaker()() as peer:
                summary = await pipeline.run_screening_cycle(
                    peer, settings, storage, ProviderRotation([peer_provider])
                )
                assert summary.claimed == 1 and summary.healthy == 1 and summary.errors == 0
            async with get_sessionmaker()() as before_failure:
                completed = await before_failure.get(ScreeningImage, image_id)
                assert completed is not None and completed.status == "HEALTHY"
                assert completed.screening_attempts == 2 and completed.normalized_key is not None
            # No other writes intervene. ACCESS EXCLUSIVE blocks the genuine
            # ownership SELECT's ACCESS SHARE until its statement timeout.
            await blocker.execute(text("LOCK TABLE screening_images IN ACCESS EXCLUSIVE MODE"))
            release.set()
            await asyncio.wait_for(query_failed.wait(), timeout=30)
            await blocker.rollback()
            await asyncio.wait_for(task, timeout=30)
        finally:
            release.set()
            await blocker.rollback()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await original.rollback()
            await original.execute(
                text("SELECT set_config('statement_timeout', :previous_timeout, false)"),
                {"previous_timeout": previous_timeout},
            )
            assert await native_scalar(text("SELECT pg_backend_pid()")) == original_pid
            assert await native_scalar(text("SHOW statement_timeout")) == previous_timeout
            await original.commit()
    assert errors == ["DBAPIError:57014"], errors
    assert original_provider.calls == 0 and peer_provider.calls == 1
    async with get_sessionmaker()() as check:
        completed = await check.get(ScreeningImage, image_id)
        assert completed is not None and completed.screening_attempts == 2
        gate_count = await check.scalar(
            select(func.count()).select_from(ScreeningRun).where(ScreeningRun.image_id == image_id)
        )
        charges = await check.scalar(
            select(func.count())
            .select_from(ScreeningCallReservation)
            .where(ScreeningCallReservation.farm_id == farm_id)
        )
        assert gate_count == 1 and charges == 1
        assert completed.status == "HEALTHY" and completed.error is None, (
            "A failed ownership preflight changed an actual peer-completed photo "
            f"to {completed.status}: {completed.error}; "
            f"original provider calls={original_provider.calls}"
        )
