"""Paid, capped and mixed-day delivery facts retain their public accounting."""

from datetime import timedelta

import httpx
from sqlalchemy import select

from app.core.config import Settings
from app.db import get_sessionmaker
from app.models import FarmMembership, NotificationLog, Task
from app.services.notifications import service
from app.utils import today

from .conftest import owner_with_farm
from .test_mutation38_notification_claim_contracts import _supported_delivery
from .test_notifications import RecordingProvider, _farm, _membership_id, _recipient, midday


async def test_already_paid_fact_keeps_sent_receipt_when_replayed_during_quiet_hours(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    membership_id = await _membership_id(client, owner)
    settings = Settings(environment="development", notifications_enabled=True)
    provider = RecordingProvider()
    async with get_sessionmaker()() as db:
        farm = await _farm(db, farm_id)
        recipient = await _recipient(db, farm_id, membership_id)
        morning = midday(farm)
        paid = await _supported_delivery(
            db, settings, provider, farm, recipient, "paid:41", morning
        )
        quiet = await _supported_delivery(
            db, settings, provider, farm, recipient, "paid:41", morning.replace(hour=22)
        )
        assert paid.status == quiet.status == "SENT"
        assert paid.fresh and not quiet.fresh
        assert len(provider.sent) == 1
        row = (await db.execute(select(NotificationLog))).scalar_one()
        assert row.status == "SENT" and row.provider_message_id == "rec-1"
        assert row.error is None


async def test_first_daily_cap_refusal_is_fresh_and_its_same_fact_replay_is_not(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    first_member = await _membership_id(client, owner)
    second_member = await _membership_id(client, owner)
    settings = Settings(
        environment="development", notifications_enabled=True, notifications_farm_daily_cap=1
    )
    provider = RecordingProvider()
    async with get_sessionmaker()() as db:
        farm = await _farm(db, farm_id)
        first_recipient = await _recipient(db, farm_id, first_member, "+919999999991")
        second_recipient = await _recipient(db, farm_id, second_member, "+919999999992")
        at = midday(farm)
        sent = await _supported_delivery(
            db, settings, provider, farm, first_recipient, "cap-fact:41", at
        )
        capped = await _supported_delivery(
            db, settings, provider, farm, second_recipient, "cap-fact:41", at
        )
        replay = await _supported_delivery(
            db, settings, provider, farm, second_recipient, "cap-fact:41", at
        )
        assert sent.status == "SENT" and sent.fresh
        assert capped.status == replay.status == "SKIPPED_CAP"
        assert capped.fresh and not replay.fresh
        assert len(provider.sent) == 1
        rows = list((await db.execute(select(NotificationLog))).scalars())
        assert len(rows) == 2
        assert sorted(row.status for row in rows) == ["SENT", "SKIPPED_CAP"]


async def test_mixed_today_and_overdue_digest_counts_only_the_one_overdue_duty(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    membership_id = await _membership_id(client, owner)
    async with get_sessionmaker()() as db:
        farm = await _farm(db, farm_id)
        recipient = await _recipient(db, farm_id, membership_id)
        role_id = await db.scalar(
            select(FarmMembership.role_id).where(FarmMembership.id == membership_id)
        )
        assert isinstance(role_id, int)
        reference = today(farm.timezone)
        db.add(
            Task(
                farm_id=farm_id,
                title="Overdue inspection",
                due_date=reference - timedelta(days=1),
                category="OTHER",
                status="PENDING",
                assigned_role_id=role_id,
            )
        )
        db.add_all(
            [
                Task(
                    farm_id=farm_id,
                    title=f"Inspection {i:02d}",
                    due_date=reference,
                    category="OTHER",
                    status="PENDING",
                    assigned_role_id=role_id,
                )
                for i in range(1, 12)
            ]
        )
        await db.commit()
        body = await service._digest_text_for_recipient(db, farm, recipient, reference)
    assert body == "\n".join(
        [
            f"Herdly {reference.isoformat()}: 12 duties today",
            "1 overdue (incl. today's list)",
            "- Overdue inspection",
            *[f"- Inspection {i:02d}" for i in range(1, 5)],
            "... and 7 more",
        ]
    )
