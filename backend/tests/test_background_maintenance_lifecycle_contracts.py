"""Serving leases and background retention remain correct across worker ticks."""

import asyncio
from datetime import timedelta
from hashlib import sha256
from types import SimpleNamespace

import httpx
import pytest
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import main as main_module
from app.core.config import Settings
from app.db import get_sessionmaker
from app.models import IdempotencyRecord, User
from app.services.idempotency import purge_expired_idempotency_records
from app.utils import utcnow

from .conftest import owner_with_farm


async def test_production_lease_reacquisition_restores_readiness_and_releases_the_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(environment="development").model_copy(update={"environment": "production"})
    monkeypatch.setattr(main_module, "_production_singleton_lease_healthy", False)
    lease = await main_module._acquire_production_singleton_lease(settings)
    assert lease is not None
    try:
        assert not isinstance(await main_module.readyz(), JSONResponse)
        with pytest.raises(RuntimeError, match="another production API replica"):
            await main_module._acquire_production_singleton_lease(settings)
    finally:
        await main_module._release_production_singleton_lease(lease)

    successor = await main_module._acquire_production_singleton_lease(settings)
    assert successor is not None, "Shutdown must release the physical session's advisory lease"
    await main_module._release_production_singleton_lease(successor)


async def test_singleton_watchdog_keeps_monitoring_after_a_healthy_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(environment="development").model_copy(update={"environment": "production"})
    lease = await main_module._acquire_production_singleton_lease(settings)
    assert lease is not None
    ticks = 0
    termination_health: list[bool] = []

    async def wake(_delay: float) -> None:
        nonlocal ticks
        ticks += 1
        if ticks == 2:
            # The first heartbeat has proved ownership. Lose this same real
            # PostgreSQL session lock before the next scheduled heartbeat.
            assert (
                await lease.connection.scalar(
                    text("SELECT pg_advisory_unlock(:lock_id)"),
                    {"lock_id": main_module._PRODUCTION_API_SINGLETON_LOCK_ID},
                )
                is True
            )
            await lease.connection.commit()
        assert ticks <= 2, "A lost singleton lease must terminate the serving lifetime"

    monkeypatch.setattr(
        main_module, "asyncio", SimpleNamespace(sleep=wake, CancelledError=asyncio.CancelledError)
    )
    monkeypatch.setattr(main_module, "_production_singleton_lease_healthy", True)
    monkeypatch.setattr(
        main_module,
        "_terminate_after_singleton_lease_loss",
        lambda: termination_health.append(main_module._production_singleton_lease_healthy),
    )
    try:
        await main_module._production_singleton_lease_watchdog(lease)
        assert ticks == 2
        assert termination_health == [False], "Readiness must fail before termination is requested"
        response = await main_module.readyz()
        assert isinstance(response, JSONResponse) and response.status_code == 503
    finally:
        await main_module._release_production_singleton_lease(lease)


async def _expired_records(client: httpx.AsyncClient, count: int) -> set[int]:
    owner = await owner_with_farm(client, email="periodic-retention@farm.in")
    async with get_sessionmaker()() as db:
        actor_id = (await db.execute(select(User.id))).scalar_one()
        records = [
            IdempotencyRecord(
                farm_id=int(owner["X-Farm-Id"]),
                actor_id=actor_id,
                operation="retention.contract",
                key_digest=sha256(str(index).encode()).hexdigest(),
                request_hash="0" * 64,
                response_status=201,
                response_body={"saved": index},
                completed_at=utcnow() - timedelta(days=2),
                expires_at=utcnow() - timedelta(days=1),
            )
            for index in range(count)
        ]
        db.add_all(records)
        await db.commit()
        return {record.id for record in records}


@pytest.mark.parametrize(
    ("count", "max_batches", "expected_remaining", "expected_batches"),
    [(5, 5, 0, [2, 2, 1]), (9, 3, 3, [2, 2, 2])],
    ids=["drain-short-final-page", "yield-after-configured-batch-budget"],
)
async def test_background_expiry_purge_commits_pages_and_yields_within_its_work_budget(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    count: int,
    max_batches: int,
    expected_remaining: int,
    expected_batches: list[int],
) -> None:
    ids = await _expired_records(client, count)
    observed_batches: list[int] = []
    purge = purge_expired_idempotency_records
    ticks = 0

    async def observed_purge(db: AsyncSession, *, batch_size: int) -> int:
        removed = await purge(db, batch_size=batch_size)
        observed_batches.append(removed)
        return removed

    async def wake(_delay: float) -> None:
        nonlocal ticks
        ticks += 1
        if ticks == 2:
            # A separate transaction must observe the completed work before
            # the worker starts another interval or accepts cancellation.
            async with get_sessionmaker()() as db:
                remaining = await db.scalar(
                    select(func.count())
                    .select_from(IdempotencyRecord)
                    .where(IdempotencyRecord.id.in_(ids))
                )
            assert remaining == expected_remaining
            raise asyncio.CancelledError

    monkeypatch.setattr(main_module, "purge_expired_idempotency_records", observed_purge)
    monkeypatch.setattr(
        main_module, "asyncio", SimpleNamespace(sleep=wake, CancelledError=asyncio.CancelledError)
    )
    with pytest.raises(asyncio.CancelledError):
        await main_module._idempotency_cleanup_loop(300, batch_size=2, max_batches=max_batches)
    assert ticks == 2
    assert observed_batches == expected_batches


async def test_background_expiry_purge_retries_a_transient_failure_at_the_next_tick(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = await _expired_records(client, 1)
    purge = purge_expired_idempotency_records
    attempts = 0
    ticks = 0

    async def transient_purge(db: AsyncSession, *, batch_size: int) -> int:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary retention storage outage")
        return await purge(db, batch_size=batch_size)

    async def wake(_delay: float) -> None:
        nonlocal ticks
        ticks += 1
        if ticks == 3:
            async with get_sessionmaker()() as db:
                assert (
                    await db.scalar(
                        select(func.count())
                        .select_from(IdempotencyRecord)
                        .where(IdempotencyRecord.id.in_(ids))
                    )
                    == 0
                )
            raise asyncio.CancelledError

    monkeypatch.setattr(main_module, "purge_expired_idempotency_records", transient_purge)
    monkeypatch.setattr(
        main_module, "asyncio", SimpleNamespace(sleep=wake, CancelledError=asyncio.CancelledError)
    )
    with pytest.raises(asyncio.CancelledError):
        await main_module._idempotency_cleanup_loop(300, batch_size=2, max_batches=3)
    assert attempts == 2
    assert ticks == 3


async def test_idle_raw_cleanup_waits_before_starting_another_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batches = 0
    pauses: list[float] = []

    async def idle_batch(*_args: object, **_kwargs: object) -> SimpleNamespace:
        nonlocal batches
        batches += 1
        if batches > 1:
            # Bound a worker that wrongly starts another idle pass without
            # yielding. Cancellation propagates through the real worker loop.
            raise asyncio.CancelledError
        return SimpleNamespace(
            claimed=0,
            completed=0,
            in_progress=0,
            failed=0,
            acknowledgement_failed=0,
        )

    async def pause(delay: float) -> None:
        pauses.append(delay)
        raise asyncio.CancelledError

    monkeypatch.setattr(main_module, "storage_for_settings", lambda _settings: object())
    monkeypatch.setattr(main_module, "run_raw_cleanup_batch", idle_batch)
    monkeypatch.setattr(
        main_module, "asyncio", SimpleNamespace(sleep=pause, CancelledError=asyncio.CancelledError)
    )
    with pytest.raises(asyncio.CancelledError):
        await main_module._screening_raw_cleanup_loop(300, batch_size=5, max_batches=3)

    assert batches == 1, "An empty retention queue must not trigger a busy loop"
    assert pauses == [300]
