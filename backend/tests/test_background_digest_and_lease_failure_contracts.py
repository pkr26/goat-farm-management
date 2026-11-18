"""Real heartbeat failures fail closed and eligible digest farms make progress."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, tzinfo
from types import SimpleNamespace
from typing import Self
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app import main
from app.core.config import Settings
from app.db import get_sessionmaker
from app.models import Farm, FarmMembership, NotificationLog, Task
from app.schemas.ops import ReadinessStatusOut
from app.services.maintenance_progress import (
    _JOB_LOCK_KEYS,
    _LOCK_NAMESPACE,
    MaintenanceJob,
    MaintenancePage,
    run_maintenance_page,
)
from app.services.notifications import service as notification_service
from app.services.notifications.providers import DeliveryResult

from .conftest import owner_with_farm
from .test_maintenance_progress import _farms
from .test_notifications import RecordingProvider, _membership_id, _recipient


async def test_a_real_failed_lease_heartbeat_refuses_readiness_before_requesting_shutdown(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    settings = Settings(environment="development").model_copy(update={"environment": "production"})
    monkeypatch.setattr(main, "_production_singleton_lease_healthy", True)
    lease = await main._acquire_production_singleton_lease(settings)
    assert lease is not None
    termination_health: list[bool] = []
    readiness_at_termination: list[int] = []
    readiness_probes: list[asyncio.Future[ReadinessStatusOut | JSONResponse]] = []

    def terminate() -> None:
        # This is the callback boundary at which the server would receive its
        # signal. The refusal flag must already be visible to readiness.
        termination_health.append(main._production_singleton_lease_healthy)
        assert main._production_singleton_lease_healthy is False
        # The refusal path is immediate and performs no pool I/O. Eager
        # execution observes the actual async probe before this callback
        # could permit a process termination action.
        probe = asyncio.eager_task_factory(asyncio.get_running_loop(), main.readyz())
        readiness_probes.append(probe)
        assert probe.done(), "Failed lease readiness must refuse without waiting for database I/O"
        response = probe.result()
        assert isinstance(response, JSONResponse)
        readiness_at_termination.append(response.status_code)
        assert response.status_code == 503

    monkeypatch.setattr(main, "_terminate_after_singleton_lease_loss", terminate)
    caplog.set_level(logging.CRITICAL, logger="app.main")
    worker = None
    try:
        # PostgreSQL, rather than a mocked scalar result, puts this owned
        # transaction into failed state. The watchdog's own query then raises.
        with pytest.raises(DBAPIError):
            await lease.connection.execute(text("SELECT 1 / 0"))
        assert lease.connection.in_transaction()
        worker = asyncio.create_task(main._production_singleton_lease_watchdog(lease, 0))
        done, _ = await asyncio.wait({worker}, timeout=2)
        assert worker in done, "A failed ownership query must finish by requesting shutdown"
        await worker
        assert termination_health == [False]
        assert readiness_at_termination == [503]
        response = await main.readyz()
        assert isinstance(response, JSONResponse) and response.status_code == 503
        failures = [r for r in caplog.records if "lease heartbeat failed" in r.getMessage()]
        assert len(failures) == 1
        traceback = failures[0].exc_info
        assert isinstance(traceback, tuple), "The critical diagnostic must retain the DB failure"
        assert isinstance(traceback[1], DBAPIError)
    finally:
        if worker is not None and not worker.done():
            worker.cancel()
        if worker is not None:
            await asyncio.gather(worker, return_exceptions=True)
        for probe in readiness_probes:
            if not probe.done():
                probe.cancel()
        await asyncio.gather(*readiness_probes, return_exceptions=True)
        # A logical pool return can retain a session advisory lock. Retire the
        # actual failed physical session before returning its wrapper.
        await lease.connection.invalidate()
        await lease.connection.close()
    assert lease.connection.closed
    successor = await main._acquire_production_singleton_lease(settings)
    assert successor is not None, "The failed physical lease must not remain in the pool"
    await main._release_production_singleton_lease(successor)


async def test_cancelling_a_real_lease_watchdog_does_not_report_a_lease_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(environment="development").model_copy(update={"environment": "production"})
    monkeypatch.setattr(main, "_production_singleton_lease_healthy", True)
    lease = await main._acquire_production_singleton_lease(settings)
    assert lease is not None
    terminations: list[bool] = []
    monkeypatch.setattr(
        main, "_terminate_after_singleton_lease_loss", lambda: terminations.append(True)
    )
    worker = asyncio.create_task(main._production_singleton_lease_watchdog(lease))
    try:
        await asyncio.sleep(0)
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker
        assert terminations == []
        assert main._production_singleton_lease_healthy is True
    finally:
        if not worker.done():
            worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        await main._release_production_singleton_lease(lease)


async def test_background_digest_delivers_a_normal_eligible_farm_once_and_reports_real_progress(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    owner = await owner_with_farm(client, email="background-digest-owner@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    membership_id = await _membership_id(client, owner)
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        membership = await db.get(FarmMembership, membership_id)
        assert farm is not None and membership is not None
        local = datetime.now(ZoneInfo(farm.timezone)).replace(
            hour=10, minute=0, second=0, microsecond=0
        )
        recipient = await _recipient(db, farm_id, membership_id)
        recipient_id, phone = recipient.id, recipient.phone
        db.add_all(
            [
                Task(
                    farm_id=farm_id,
                    title="Inspect the north trough",
                    due_date=local.date(),
                    category="OTHER",
                    status="PENDING",
                    assigned_role_id=membership.role_id,
                    assigned_user_id=membership.user_id,
                ),
                Task(
                    farm_id=farm_id,
                    title="Owner-only bank followup",
                    due_date=local.date(),
                    category="OTHER",
                    status="PENDING",
                ),
            ]
        )
        await db.commit()

    fixed_utc = local.astimezone(UTC)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> Self:
            return cls.fromtimestamp(fixed_utc.timestamp(), tz)

    monkeypatch.setattr(main, "utcnow", lambda: fixed_utc.replace(tzinfo=None))
    monkeypatch.setattr(notification_service, "datetime", FixedDateTime)
    settings = Settings(
        environment="development",
        notifications_enabled=True,
        notifications_digest_hour=6,
        notifications_digest_minute=30,
    )
    provider_io_idle_transactions: list[int] = []

    class CheckedProvider(RecordingProvider):
        async def send_sms(self, phone: str, message: str) -> DeliveryResult:
            async with get_sessionmaker()() as probe:
                idle = await probe.scalar(
                    text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname = current_database() "
                        "AND pid <> pg_backend_pid() AND state = 'idle in transaction'"
                    )
                )
                provider_io_idle_transactions.append(int(idle or 0))
            return await super().send_sms(phone, message)

    provider = CheckedProvider()
    ticks = 0

    async def completed_tick(_delay: float) -> None:
        nonlocal ticks
        ticks += 1
        async with get_sessionmaker()() as db:
            rows = (
                (
                    await db.execute(
                        select(NotificationLog).where(NotificationLog.farm_id == farm_id)
                    )
                )
                .scalars()
                .all()
            )
            assert len(rows) == 1 and rows[0].status == "SENT"
            assert rows[0].recipient_id == recipient_id
            assert rows[0].alert_class == "DAILY_DIGEST" and rows[0].local_date == local.date()
        assert len(provider.sent) == 1, "A later tick must not send the settled digest again"
        if ticks == 2:
            raise asyncio.CancelledError
        assert ticks < 2

    monkeypatch.setattr(
        main,
        "asyncio",
        SimpleNamespace(
            BoundedSemaphore=asyncio.BoundedSemaphore,
            gather=asyncio.gather,
            sleep=completed_tick,
            CancelledError=asyncio.CancelledError,
        ),
    )
    caplog.set_level(logging.INFO, logger="app.main")
    with pytest.raises(asyncio.CancelledError):
        await main._notification_digest_loop(settings, provider)
    assert ticks == 2
    assert provider_io_idle_transactions == [0]
    assert provider.sent[0][0] == phone
    assert "Inspect the north trough" in provider.sent[0][1]
    assert "Owner-only bank followup" not in provider.sent[0][1]
    summaries = [
        r.getMessage() for r in caplog.records if r.getMessage().startswith("digest farm=")
    ]
    assert summaries == [f"digest farm={farm_id} sent=1 skipped=0"]


async def test_contended_cadence_yields_then_materializes_real_farm_duties(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    farm_ids = await _farms(1, with_animals=True)
    actual_page = run_maintenance_page
    probes = 0
    ticks = 0

    async def observed_page(
        db: AsyncSession,
        job: MaintenanceJob,
        work: Callable[[int], Awaitable[MaintenancePage]],
    ) -> MaintenancePage | None:
        nonlocal probes
        probes += 1
        return await actual_page(db, job, work)

    async with get_sessionmaker()() as owner:
        await owner.execute(
            text("SELECT pg_advisory_xact_lock(:namespace, :job_key)"),
            {"namespace": _LOCK_NAMESPACE, "job_key": _JOB_LOCK_KEYS["cadence"]},
        )

        async def next_tick(_delay: float) -> None:
            nonlocal ticks
            ticks += 1
            if ticks == 1:
                return
            if ticks == 2:
                assert probes == 1, "A busy cadence page must yield before another ownership probe"
                async with get_sessionmaker()() as observer:
                    assert (
                        not (
                            await observer.execute(select(Task).where(Task.farm_id == farm_ids[0]))
                        )
                        .scalars()
                        .all()
                    )
                await owner.commit()  # Release this owned transaction's actual advisory lock.
                return
            assert ticks == 3 and probes == 2
            async with get_sessionmaker()() as observer:
                tasks = (
                    (await observer.execute(select(Task).where(Task.farm_id == farm_ids[0])))
                    .scalars()
                    .all()
                )
                assert tasks and all(task.status == "PENDING" for task in tasks)
            raise asyncio.CancelledError

        monkeypatch.setattr(main, "run_maintenance_page", observed_page)
        monkeypatch.setattr(
            main,
            "asyncio",
            SimpleNamespace(sleep=next_tick, CancelledError=asyncio.CancelledError),
        )
        try:
            with pytest.raises(asyncio.CancelledError):
                await main._cadence_materialization_loop(300, farm_batch_size=10, max_batches=3)
        finally:
            await owner.rollback()
    assert ticks == 3 and probes == 2
