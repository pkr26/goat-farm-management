"""Behavioral regressions discovered independently after the October audit commit."""

import asyncio
from datetime import UTC, timedelta

import httpx
import pytest
from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import auth as auth_api
from app.api import team as team_api
from app.core.config import Settings
from app.db import get_sessionmaker
from app.main import create_app
from app.models import (
    Farm,
    FarmMembership,
    NotificationLog,
    NotificationRecipient,
    RefreshSession,
    Role,
    User,
)
from app.models.notifications import NotificationOutbox
from app.services.notifications import service
from app.services.notifications.outbox import dispatch_outbox_event

from .conftest import OWNER_PW, owner_with_farm
from .test_notification_delivery_integrity import _queued_alert
from .test_notifications import RecordingProvider


@pytest.mark.parametrize(
    ("legacy_status", "deliveries"),
    [("SENT", 0), ("FAILED", 0), ("SENDING", 0), ("SKIPPED_QUIET", 1), ("SKIPPED_CAP", 1)],
)
async def test_outbox_preserves_pre_migration_delivery_claims_across_midnight(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    legacy_status: str,
    deliveries: int,
) -> None:
    event_id, quiet = await _queued_alert(client, monkeypatch)
    midday = quiet.replace(hour=10)
    async with get_sessionmaker()() as db:
        event = await db.get(NotificationOutbox, event_id)
        assert event is not None
        recipient_id = (await db.execute(select(NotificationRecipient.id))).scalar_one()
        legacy = NotificationLog(
            farm_id=event.farm_id,
            recipient_id=recipient_id,
            alert_class=event.alert_class,
            payload_hash=service.payload_hash(f"{event.alert_class}:{event.event_key}"),
            local_date=midday.date(),
            status=legacy_status,
            provider_message_id="legacy-provider-receipt" if legacy_status == "SENT" else None,
        )
        db.add(legacy)
        await db.commit()
        legacy_id = legacy.id

    settings = Settings(environment="development", notifications_enabled=True)
    provider = RecordingProvider()
    assert (
        await dispatch_outbox_event(event_id, settings, provider, now_utc=midday.astimezone(UTC))
        == deliveries
    )
    assert (
        await dispatch_outbox_event(
            event_id,
            settings,
            provider,
            now_utc=(midday + timedelta(days=1)).astimezone(UTC),
        )
        == 0
    )
    assert len(provider.sent) == deliveries
    async with get_sessionmaker()() as db:
        stored_legacy = await db.get(NotificationLog, legacy_id)
        event = await db.get(NotificationOutbox, event_id)
        assert event is not None
        if deliveries:
            assert stored_legacy is None  # Deferred placeholders remain retryable.
            receipt = (
                await db.execute(
                    select(NotificationLog).where(NotificationLog.outbox_id == event_id)
                )
            ).scalar_one()
            assert receipt.status == "SENT"
        else:
            assert stored_legacy is not None
            assert stored_legacy.status == legacy_status
            assert stored_legacy.outbox_id == event_id
            if legacy_status == "SENT":
                assert stored_legacy.provider_message_id == "legacy-provider-receipt"
        assert event.completed_at is not None


async def test_ownership_transfer_does_not_deadlock_with_reciprocal_roster_edit(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_a = await owner_with_farm(client, email="transfer-lock-a@farm.in")
    owner_b = await owner_with_farm(client, email="transfer-lock-b@farm.in")
    async with get_sessionmaker()() as db:
        users = list((await db.execute(select(User).order_by(User.id))).scalars())
        roles = list(
            (
                await db.execute(select(Role).where(Role.code == "CLEANER").order_by(Role.farm_id))
            ).scalars()
        )
        member_b = FarmMembership(
            farm_id=int(owner_a["X-Farm-Id"]), user_id=users[1].id, role_id=roles[0].id
        )
        member_a = FarmMembership(
            farm_id=int(owner_b["X-Farm-Id"]), user_id=users[0].id, role_id=roles[1].id
        )
        db.add_all([member_a, member_b])
        await db.commit()
        member_a_id, member_b_id = member_a.id, member_b.id
        session_state = list(
            (
                await db.execute(
                    select(
                        RefreshSession.id,
                        RefreshSession.family_id,
                        RefreshSession.consumed_at,
                        RefreshSession.revoked_at,
                    ).order_by(RefreshSession.id)
                )
            ).all()
        )

    roster_locked, resume_roster, transfer_locked = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    original_pin = team_api._pin_membership_user
    original_generation = auth_api._require_authenticated_generation

    async def paused_pin(db: AsyncSession, membership: FarmMembership) -> User:
        roster_locked.set()
        await resume_roster.wait()
        return await original_pin(db, membership)

    async def notify_transfer_lock(
        db: AsyncSession, request: Request, user: User, authenticated_token_version: int
    ) -> None:
        await original_generation(db, request, user, authenticated_token_version)
        transfer_locked.set()

    monkeypatch.setattr(team_api, "_pin_membership_user", paused_pin)
    monkeypatch.setattr(auth_api, "_require_authenticated_generation", notify_transfer_lock)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as contender:
        roster = asyncio.create_task(
            contender.put(
                f"/api/team/workers/{member_a_id}/status",
                headers=owner_b,
                json={"is_active": False},
            )
        )
        transfer: asyncio.Task[httpx.Response] | None = None
        try:
            await asyncio.wait_for(roster_locked.wait(), timeout=5)
            transfer = asyncio.create_task(
                client.post(
                    f"/api/auth/farms/{owner_a['X-Farm-Id']}/transfer-ownership",
                    headers=owner_a,
                    json={"membership_id": member_b_id, "current_password": OWNER_PW},
                )
            )
            await asyncio.wait_for(transfer_locked.wait(), timeout=5)
            resume_roster.set()
            responses = await asyncio.wait_for(asyncio.gather(roster, transfer), timeout=10)
        finally:
            resume_roster.set()
            if not roster.done():
                roster.cancel()
            if transfer is not None and not transfer.done():
                transfer.cancel()
            pending = [roster] if transfer is None else [roster, transfer]
            await asyncio.gather(*pending, return_exceptions=True)
    assert [response.status_code for response in responses] == [200, 409]
    assert "retry" in responses[1].json()["detail"]
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, int(owner_a["X-Farm-Id"]))
        actor = await db.get(User, users[0].id)
        successor = await db.get(User, users[1].id)
        former_owner_member = await db.get(FarmMembership, member_a_id)
        successor_member = await db.get(FarmMembership, member_b_id)
        assert farm is not None and farm.owner_id == users[0].id
        assert actor is not None and actor.token_version == users[0].token_version
        assert successor is not None and successor.token_version == users[1].token_version
        assert former_owner_member is not None and not former_owner_member.is_active
        assert successor_member is not None and successor_member.is_active
        assert (former_owner_member.role_id, successor_member.role_id) == (
            member_a.role_id,
            member_b.role_id,
        )
        assert (
            list(
                (
                    await db.execute(
                        select(
                            RefreshSession.id,
                            RefreshSession.family_id,
                            RefreshSession.consumed_at,
                            RefreshSession.revoked_at,
                        ).order_by(RefreshSession.id)
                    )
                ).all()
            )
            == session_state
        )
    retried = await client.post(
        f"/api/auth/farms/{owner_a['X-Farm-Id']}/transfer-ownership",
        headers=owner_a,
        json={"membership_id": member_b_id, "current_password": OWNER_PW},
    )
    assert retried.status_code == 200, retried.text
