"""Exact workloads, eventual farm service and durable one-shot delivery."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import main
from app.core.config import Settings, get_settings
from app.db import get_sessionmaker
from app.models import Farm, FeedInventory, NotificationLog, NotificationRecipient, Task
from app.models.notifications import NotificationOutbox
from app.services.notifications import service
from app.services.notifications.outbox import dispatch_outbox_event, enqueue_alert
from app.services.notifications.providers import (
    DeliveryResult,
    NotificationDeliveryError,
    NotificationProvider,
)
from app.utils import today

from .conftest import owner_with_farm
from .test_notifications import RecordingProvider, _farm, _membership_id, _recipient, midday


async def test_alert_headlines_and_dedupe_use_exact_large_counts(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    captured: list[dict[str, Any]] = []

    async def capture(*args: Any, **kwargs: Any) -> int:
        captured.append(kwargs)
        return 1

    monkeypatch.setattr(service, "notify_alert_class", capture)
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        await db.execute(
            update(FeedInventory).where(FeedInventory.farm_id == farm_id).values(reorder_level=None)
        )
        for index in range(14):
            db.add(
                FeedInventory(
                    farm_id=farm_id,
                    ingredient=f"Low item {index}",
                    category="CONCENTRATE",
                    qty_on_hand=Decimal("1"),
                    reorder_level=Decimal("10"),
                    unit="kg",
                )
            )
        for index in range(70):
            db.add(
                Task(
                    farm_id=farm_id,
                    title=f"Overdue {index}",
                    category="OTHER",
                    status="PENDING",
                    due_date=today(farm.timezone) - timedelta(days=4),
                )
            )
        await db.commit()
        settings = Settings(environment="development")
        await service.feed_reorder_daily(db, settings, RecordingProvider(), farm)
        await service.overdue_critical_sweep(db, settings, RecordingProvider(), farm)
    assert "14 feed items" in captured[0]["message"]
    assert captured[0]["payload"].endswith(":14")
    assert "70 duties" in captured[1]["message"]
    assert captured[1]["payload"].startswith("overdue:70:")


async def test_cadence_cursor_reaches_farms_beyond_a_single_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    async def ensure(db: AsyncSession, *, batch_size: int, after_farm_id: int) -> tuple[int, int]:
        calls.append(after_farm_id)
        return (1, after_farm_id + 1) if after_farm_id < 3 else (0, after_farm_id)

    async def tick(_seconds: float) -> None:
        if len(calls) >= 5:
            raise asyncio.CancelledError

    monkeypatch.setattr(main, "ensure_cadence_farm_batch", ensure)
    monkeypatch.setattr(asyncio, "sleep", tick)
    with pytest.raises(asyncio.CancelledError):
        await main._cadence_materialization_loop(1, 1, 1)
    assert calls == [0, 1, 2, 3, 0]


async def test_bounded_alert_sweep_isolates_a_poisoned_farm(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owners = [
        await owner_with_farm(client, email=f"notification-page-{i}@test.in") for i in range(3)
    ]
    ids = [int(owner["X-Farm-Id"]) for owner in owners]
    visited: list[int] = []

    async def alert(
        db: AsyncSession, settings: Settings, provider: NotificationProvider, farm: Farm
    ) -> None:
        visited.append(farm.id)
        if farm.id == ids[0]:
            raise RuntimeError("invented farm failure")

    async def no_op(*args: Any) -> int:
        return 0

    import app.services.notifications as notifications

    monkeypatch.setattr(notifications, "overdue_critical_sweep", alert)
    monkeypatch.setattr(notifications, "kidding_watch_daily", no_op)
    monkeypatch.setattr(notifications, "feed_reorder_daily", no_op)
    settings = Settings(environment="development", notifications_loop_batch_size=2)
    count, cursor, exhausted = await main._notification_alert_farm_batch(
        settings, RecordingProvider(), 0
    )
    assert count == 2 and cursor == ids[1] and not exhausted
    count, cursor, exhausted = await main._notification_alert_farm_batch(
        settings, RecordingProvider(), cursor
    )
    assert count == 1 and cursor == ids[2] and exhausted
    # Farms within each bounded page run concurrently, so completion order is
    # intentionally unspecified; the cursor still advances in id order.
    assert sorted(visited) == ids


async def test_delivery_concurrency_is_bounded_per_farm_and_releases_caller_sessions(
    client: httpx.AsyncClient,
) -> None:
    """Slow provider work overlaps across farms but owns no caller DB checkout."""

    deliveries: list[tuple[int, int, str, datetime]] = []
    session_by_phone: dict[str, AsyncSession] = {}
    farm_by_phone: dict[str, int] = {}
    for farm_index in range(2):
        owner = await owner_with_farm(
            client, email=f"notification-concurrency-{farm_index}@test.in"
        )
        farm_id = int(owner["X-Farm-Id"])
        for recipient_index in range(2):
            membership_id = await _membership_id(client, owner)
            phone = f"+919811{farm_index:02d}{recipient_index:06d}"
            async with get_sessionmaker()() as db:
                recipient = await _recipient(db, farm_id, membership_id, phone=phone)
                farm = await db.get(Farm, farm_id)
                assert farm is not None
                deliveries.append((farm_id, recipient.id, phone, midday(farm)))

    class SlowProvider(RecordingProvider):
        def __init__(self) -> None:
            super().__init__()
            self.active = 0
            self.max_active = 0
            self.active_by_farm: dict[int, int] = {}
            self.two_farms_active = asyncio.Event()

        async def send_sms(self, phone: str, message: str) -> DeliveryResult:
            caller_db = session_by_phone[phone]
            assert not caller_db.in_transaction(), "provider I/O retained the caller transaction"
            farm_id = farm_by_phone[phone]
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.active_by_farm[farm_id] = self.active_by_farm.get(farm_id, 0) + 1
            assert self.active_by_farm[farm_id] == 1
            if sum(count > 0 for count in self.active_by_farm.values()) == 2:
                self.two_farms_active.set()
            try:
                await asyncio.wait_for(self.two_farms_active.wait(), timeout=0.5)
                await asyncio.sleep(0.03)
                return await super().send_sms(phone, message)
            finally:
                self.active -= 1
                self.active_by_farm[farm_id] -= 1

    provider = SlowProvider()
    settings = Settings(
        environment="development",
        notifications_enabled=True,
        notifications_delivery_concurrency=2,
        notifications_per_farm_delivery_concurrency=1,
    )

    async def deliver(
        farm_id: int, recipient_id: int, phone: str, local_now: datetime
    ) -> service.SendOutcome:
        async with get_sessionmaker()() as db:
            farm = await _farm(db, farm_id)
            recipient = await db.get(NotificationRecipient, recipient_id)
            assert recipient is not None
            session_by_phone[phone] = db
            farm_by_phone[phone] = farm_id
            return await service.send_notification(
                db,
                settings,
                provider,
                farm=farm,
                recipient=recipient,
                alert_class="SCREENING_FLAG",
                message="bounded",
                payload=f"bounded:{recipient_id}",
                now_local=local_now,
            )

    started = asyncio.get_running_loop().time()
    outcomes = await asyncio.gather(*(deliver(*delivery) for delivery in deliveries))
    elapsed = asyncio.get_running_loop().time() - started
    assert all(outcome.status == "SENT" for outcome in outcomes)
    assert provider.max_active == 2
    assert elapsed < 0.3, "four 30ms sends should run in two cross-farm waves, not serially"


async def test_notification_classes_are_scheduled_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A blocked outbox class cannot hold the digest or periodic alert class."""

    outbox_started = asyncio.Event()
    never_release = asyncio.Event()
    digest_ran = asyncio.Event()
    alerts_ran = asyncio.Event()

    async def blocked_outbox(_settings: Settings, _provider: NotificationProvider) -> int:
        outbox_started.set()
        await never_release.wait()
        return 0

    async def ready_digests(_db: AsyncSession, _settings: Settings, _now: datetime) -> list[Farm]:
        digest_ran.set()
        return []

    async def alert_page(
        _settings: Settings, _provider: NotificationProvider, _hour: datetime
    ) -> None:
        alerts_ran.set()

    import app.services.notifications as notifications
    import app.services.notifications.outbox as outbox

    monkeypatch.setattr(outbox, "dispatch_pending_alerts", blocked_outbox)
    monkeypatch.setattr(notifications, "farms_ready_for_digest", ready_digests)
    monkeypatch.setattr(main, "_notification_alert_maintenance_page", alert_page)
    settings = Settings(environment="development", notifications_enabled=True)
    provider = RecordingProvider()
    tasks = [
        asyncio.create_task(main._notification_outbox_loop(settings, provider)),
        asyncio.create_task(main._notification_digest_loop(settings, provider)),
        asyncio.create_task(main._notification_periodic_alert_loop(settings, provider)),
    ]
    try:
        await asyncio.wait_for(outbox_started.wait(), timeout=0.5)
        await asyncio.wait_for(asyncio.gather(digest_ran.wait(), alerts_ran.wait()), timeout=0.5)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _queued_alert(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, recipients: int = 1
) -> tuple[int, datetime]:
    monkeypatch.setattr(get_settings(), "notifications_enabled", True)
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    for index in range(recipients):
        member = await _membership_id(client, owner)
        async with get_sessionmaker()() as db:
            await _recipient(db, farm_id, member, phone=f"+91999999999{index}")
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        local = datetime.now(ZoneInfo(farm.timezone)).replace(
            hour=22, minute=0, second=0, microsecond=0
        )
        # Use tomorrow so the server-owned due time has already passed.
        quiet = local + timedelta(days=1)
        event_id = await enqueue_alert(
            db, farm_id, "SCREENING_FLAG", "Vet confirmed finding", "review:1"
        )
        assert event_id is not None
        await db.commit()
    return event_id, quiet


async def test_quiet_one_shot_replays_without_repeating_the_domain_mutation(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    event_id, quiet = await _queued_alert(client, monkeypatch)
    settings = Settings(environment="development", notifications_enabled=True)
    provider = RecordingProvider()
    assert (
        await dispatch_outbox_event(event_id, settings, provider, now_utc=quiet.astimezone(UTC))
        == 0
    )
    morning = (quiet + timedelta(days=1)).replace(hour=6)
    assert (
        await dispatch_outbox_event(event_id, settings, provider, now_utc=morning.astimezone(UTC))
        == 1
    )
    assert (
        await dispatch_outbox_event(event_id, settings, provider, now_utc=morning.astimezone(UTC))
        == 0
    )
    assert len(provider.sent) == 1


async def test_partial_cap_fanout_resumes_across_midnight_without_duplicate_paid_sends(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    event_id, quiet = await _queued_alert(client, monkeypatch, recipients=2)
    settings = Settings(
        environment="development", notifications_enabled=True, notifications_farm_daily_cap=1
    )
    provider = RecordingProvider()
    midday = quiet.replace(hour=10)
    assert (
        await dispatch_outbox_event(event_id, settings, provider, now_utc=midday.astimezone(UTC))
        == 1
    )
    morning = (midday + timedelta(days=1)).replace(hour=6)
    assert (
        await dispatch_outbox_event(event_id, settings, provider, now_utc=morning.astimezone(UTC))
        == 1
    )
    assert len({phone for phone, _message in provider.sent}) == len(provider.sent) == 2
    async with get_sessionmaker()() as db:
        event = await db.get(NotificationOutbox, event_id)
        assert event is not None
        assert event.completed_at is not None
        assert (
            await db.execute(
                select(func.count())
                .select_from(NotificationLog)
                .where(NotificationLog.outbox_id == event_id, NotificationLog.status == "SENT")
            )
        ).scalar_one() == 2


async def test_ambiguous_paid_attempt_is_never_blindly_retried(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    event_id, quiet = await _queued_alert(client, monkeypatch)

    class Ambiguous(RecordingProvider):
        async def send_sms(self, phone: str, message: str) -> DeliveryResult:
            self.sent.append((phone, message))
            raise NotificationDeliveryError("Response timed out after request was sent")

    provider = Ambiguous()
    settings = Settings(
        environment="development",
        notifications_enabled=True,
        notifications_send_retry_attempts=3,
        notifications_send_retry_backoff_seconds=0,
    )
    now = quiet.replace(hour=10)
    await dispatch_outbox_event(event_id, settings, provider, now_utc=now.astimezone(UTC))
    await dispatch_outbox_event(
        event_id, settings, provider, now_utc=(now + timedelta(days=1)).astimezone(UTC)
    )
    assert len(provider.sent) == 1
    async with get_sessionmaker()() as db:
        log = (
            await db.execute(select(NotificationLog).where(NotificationLog.outbox_id == event_id))
        ).scalar_one()
        assert log.status == "FAILED" and log.error is not None and "timed out" in log.error


async def test_outbox_insert_rolls_back_with_its_domain_transaction(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "notifications_enabled", True)
    owner = await owner_with_farm(client)
    async with get_sessionmaker()() as db:
        await enqueue_alert(
            db, int(owner["X-Farm-Id"]), "SCREENING_FLAG", "Never committed", "rolled-back"
        )
        await db.rollback()
        assert (
            await db.execute(select(func.count()).select_from(NotificationOutbox))
        ).scalar_one() == 0
