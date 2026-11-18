"""Late daily facts cannot settle the next local day's duties digest."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, tzinfo
from typing import Self
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select, text

from app.core.config import Settings
from app.db import get_sessionmaker
from app.models import Farm, FarmMembership, NotificationLog, Task
from app.services.notifications import service

from .conftest import owner_with_farm
from .test_notifications import RecordingProvider, _membership_id, _recipient


@pytest.mark.parametrize("quiet_at_admission", [False, True], ids=["late-fact", "quiet-admission"])
async def test_delayed_digest_preserves_each_days_fact_and_current_admission_guards(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    quiet_at_admission: bool,
) -> None:
    owner = await owner_with_farm(client, email="midnight-digest@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    membership_id = await _membership_id(client, owner)
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        member = await db.get(FarmMembership, membership_id)
        assert farm is not None and member is not None
        local = datetime.now(ZoneInfo(farm.timezone)).replace(
            hour=23, minute=59, second=0, microsecond=0
        )
        recipient = await _recipient(db, farm_id, membership_id)
        recipient_id = recipient.id
        for due_date, title in (
            (local.date(), "Inspect the evening trough"),
            (local.date() + timedelta(days=1), "Inspect the new day's feed"),
        ):
            db.add(
                Task(
                    farm_id=farm_id,
                    title=title,
                    due_date=due_date,
                    category="OTHER",
                    status="PENDING",
                    assigned_user_id=member.user_id,
                    assigned_role_id=member.role_id,
                )
            )
        await db.commit()

    clock = [local]

    class AdvancingDateTime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> Self:
            return cls.fromtimestamp(clock[0].timestamp(), tz)

    monkeypatch.setattr(service, "datetime", AdvancingDateTime)
    settings = Settings(
        environment="development",
        notifications_enabled=True,
        notifications_digest_hour=23,
        notifications_digest_minute=59,
        notifications_quiet_start_hour=0 if quiet_at_admission else 4,
        notifications_quiet_end_hour=6 if quiet_at_admission else 4,
        notifications_delivery_concurrency=1,
        notifications_per_farm_delivery_concurrency=1,
    )
    provider = RecordingProvider()
    actual_slot = service.delivery_slot
    waiting_for_admission = asyncio.Event()

    @asynccontextmanager
    async def observed_slot(config: Settings, target_farm_id: int) -> AsyncIterator[None]:
        waiting_for_admission.set()
        async with actual_slot(config, target_farm_id):
            yield

    monkeypatch.setattr(service, "delivery_slot", observed_slot)
    pending = None
    try:
        async with get_sessionmaker()() as db:
            # This is a real occupied delivery permit, like another alert
            # class's provider I/O. No fabricated query result or log row.
            async with actual_slot(settings, farm_id):
                pending = asyncio.create_task(
                    service.run_digest_for_farm(db, settings, provider, farm)
                )
                await asyncio.wait_for(waiting_for_admission.wait(), timeout=10)
                assert provider.sent == []
                assert not pending.done()
                clock[0] = (local + timedelta(days=1)).replace(hour=0, minute=0, second=1)
            summary = await asyncio.wait_for(pending, timeout=10)
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)

    next_day = clock[0].date()
    async with get_sessionmaker()() as db:
        late = (
            await db.execute(
                select(NotificationLog).where(NotificationLog.recipient_id == recipient_id)
            )
        ).scalar_one()
        assert late.local_date == next_day, "Admission uses the actual new local day"
        assert late.payload_hash == service.payload_hash(f"DAILY_DIGEST:digest:{local.date()}")
        assert late.status == ("SKIPPED_QUIET" if quiet_at_admission else "SENT")
    assert summary.sent == (0 if quiet_at_admission else 1)
    if not quiet_at_admission:
        assert local.date().isoformat() in provider.sent[0][1]
        assert "Inspect the new day's feed" not in provider.sent[0][1]

    # A late receipt for yesterday must not consume today's distinct fact.
    clock[0] = clock[0].replace(hour=23, minute=59, second=0)
    async with get_sessionmaker()() as db:
        await db.execute(text("SET LOCAL DateStyle = 'German, DMY'"))
        ready = await service.farms_ready_for_digest(db, settings, clock[0])
        assert [row.id for row in ready] == [farm_id], (
            "A prior-day digest payload admitted today must not settle today's duties; "
            f"fact_day={local.date()}, admitted_day={late.local_date}, "
            f"payload_hash={late.payload_hash}, status={late.status}, messages={provider.sent!r}"
        )
        current = await service.run_digest_for_farm(db, settings, provider, farm)
        assert current.sent == 1
        assert await service.farms_ready_for_digest(db, settings, clock[0]) == []
        replay = await service.run_digest_for_farm(db, settings, provider, farm)
        assert replay.sent == 0, "A repeated current-day fact must not send again"
    assert len(provider.sent) == (1 if quiet_at_admission else 2)
    assert next_day.isoformat() in provider.sent[-1][1]
    assert "Inspect the new day's feed" in provider.sent[-1][1]
