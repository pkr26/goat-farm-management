"""Notifications subsystem (ITEM 4, 2026-09-21 playbook).

Covers: provider-mocked sends through the console provider, the day-dedupe
ledger, quiet hours, the per-farm daily cap, the per-worker digest content
(role-scoped duties), the daily kidding-watch and feed-reorder scans, the
overdue-critical sweep, and the owner-only preferences API (plus the
screening-confirm alert hook end to end).
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.db import get_sessionmaker
from app.models import Farm, NotificationLog, NotificationRecipient, Task
from app.services.notifications import (
    ConsoleNotificationProvider,
    build_notification_provider,
    farms_ready_for_digest,
    feed_reorder_daily,
    kidding_watch_daily,
    overdue_critical_sweep,
    run_digest_for_farm,
    send_notification,
)
from app.utils import today, utcnow

from .conftest import owner_with_farm, register

DEFAULTS = {"environment": "development", "notifications_enabled": True}


def midday(farm: Farm) -> datetime:
    """Farm-local 10:00 today — outside quiet hours everywhere."""
    local = datetime.now(ZoneInfo(farm.timezone))
    return local.replace(hour=10, minute=0, second=0, microsecond=0)


class RecordingProvider:
    """Test double: records every message instead of sending."""

    name = "recording"

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_sms(self, phone: str, message: str):
        self.sent.append((phone, message))
        from app.services.notifications.providers import DeliveryResult

        return DeliveryResult(ok=True, message_id=f"rec-{len(self.sent)}")


async def _farm(db, farm_id: int) -> Farm:
    farm = await db.get(Farm, farm_id)
    assert farm is not None
    return farm


async def _recipient(
    db, farm_id: int, membership_id: int, phone: str = "+919999999999"
) -> NotificationRecipient:
    recipient = NotificationRecipient(
        farm_id=farm_id,
        membership_id=membership_id,
        phone=phone,
        daily_digest=True,
        screening_flags=True,
        kidding_watch=True,
        overdue_critical=True,
        feed_reorder=True,
    )
    db.add(recipient)
    await db.commit()
    return recipient


_worker_seq = 0


async def _membership_id(client: httpx.AsyncClient, owner: dict) -> int:
    """Create one worker (the notification target) and return its membership."""
    global _worker_seq
    _worker_seq += 1
    role = await client.post(
        "/api/team/roles",
        json={"name": f"Notif role {_worker_seq}", "permissions": ["dashboard.view"]},
        headers=owner,
    )
    assert role.status_code == 201, role.text
    worker = await client.post(
        "/api/team/workers",
        json={
            "name": "Notif Worker",
            "email": f"notif-worker-{_worker_seq}@farm.in",
            "password": "worker-pass-123",
            "role_id": role.json()["id"],
        },
        headers=owner,
    )
    assert worker.status_code == 201, worker.text
    return worker.json()["id"]


# --- send path guards --------------------------------------------------------


async def test_send_records_sent_and_dedupes_within_the_day(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client, email="notif-dedupe@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    membership_id = await _membership_id(client, owner)
    settings = Settings(**DEFAULTS)
    provider = RecordingProvider()

    async with get_sessionmaker()() as db:
        farm = await _farm(db, farm_id)
        recipient = await _recipient(db, farm_id, membership_id)
        first = await send_notification(
            db,
            settings,
            provider,
            farm=farm,
            recipient=recipient,
            alert_class="SCREENING_FLAG",
            message="m",
            payload="finding:1:CONFIRMED",
            now_local=midday(farm),
        )
        await db.commit()
        again = await send_notification(
            db,
            settings,
            provider,
            farm=farm,
            recipient=recipient,
            alert_class="SCREENING_FLAG",
            message="m",
            payload="finding:1:CONFIRMED",
            now_local=midday(farm),
        )
        await db.commit()

    assert first.status == "SENT" and first.fresh
    assert again.status == "SENT" and not again.fresh
    assert len(provider.sent) == 1  # exactly one SMS


async def test_quiet_hours_skip_without_burning_the_day(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client, email="notif-quiet@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    membership_id = await _membership_id(client, owner)
    settings = Settings(**DEFAULTS)
    provider = RecordingProvider()

    async with get_sessionmaker()() as db:
        farm = await _farm(db, farm_id)
        recipient = await _recipient(db, farm_id, membership_id)
        local = datetime.now(ZoneInfo(farm.timezone))
        night = local.replace(hour=23, minute=30, second=0, microsecond=0)
        status = await send_notification(
            db,
            settings,
            provider,
            farm=farm,
            recipient=recipient,
            alert_class="OVERDUE_CRITICAL",
            message="m",
            payload="overdue:3",
            now_local=night,
        )
        await db.commit()

    assert status.status == "SKIPPED_QUIET"
    assert provider.sent == []


async def test_farm_daily_cap_stops_sends_and_logs_the_reason(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client, email="notif-cap@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    membership_id = await _membership_id(client, owner)
    settings = Settings(**DEFAULTS, notifications_farm_daily_cap=2)
    provider = RecordingProvider()

    async with get_sessionmaker()() as db:
        farm = await _farm(db, farm_id)
        recipient = await _recipient(db, farm_id, membership_id)
        for i in range(3):
            status = await send_notification(
                db,
                settings,
                provider,
                farm=farm,
                recipient=recipient,
                alert_class="SCREENING_FLAG",
                message="m",
                payload=f"finding:{i}",
                now_local=midday(farm),
            )
            await db.commit()

    assert status.status == "SKIPPED_CAP"
    assert len(provider.sent) == 2


# --- digest ------------------------------------------------------------------


async def test_digest_sends_each_recipient_their_own_scope(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client, email="notif-digest@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    membership_id = await _membership_id(client, owner)
    settings = Settings(**DEFAULTS)
    provider = RecordingProvider()

    async with get_sessionmaker()() as db:
        farm = await _farm(db, farm_id)
        await _recipient(db, farm_id, membership_id)
        db.add(
            Task(
                farm_id=farm_id,
                title="Owner-only duty",
                due_date=today(),
                category="OTHER",
                status="PENDING",
            )
        )
        await db.commit()
        summary = await run_digest_for_farm(db, settings, provider, farm, now_local=midday(farm))

    assert summary.sent == 1
    # The recipient's OWN task scope is empty (the duty is unassigned →
    # role-scoped for the owner's membership only).
    assert "no duties today" in provider.sent[0][1]


async def test_farms_ready_for_digest_matches_the_local_minute() -> None:
    settings = Settings(**DEFAULTS, notifications_digest_hour=6, notifications_digest_minute=30)
    # 01:00 UTC == 06:30 Asia/Kolkata.
    now = datetime(2026, 9, 21, 1, 0, tzinfo=UTC)
    from app.models import User

    async with get_sessionmaker()() as db:
        tz_owner = User(email="tz-owner@farm.in", password_hash="not-used", created_at=utcnow())
        db.add(tz_owner)
        await db.flush()
        farm = Farm(name="TZ Farm", owner_id=tz_owner.id, timezone="Asia/Kolkata")
        db.add(farm)
        await db.commit()
        ready = await farms_ready_for_digest(db, settings, now)
    assert [f.id for f in ready] == [farm.id]


# --- daily scans + sweep -------------------------------------------------------


async def test_kidding_watch_daily_counts_watch_pen_does(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client, email="notif-watch@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    membership_id = await _membership_id(client, owner)
    settings = Settings(**DEFAULTS)
    provider = RecordingProvider()
    from app.models import Animal

    async with get_sessionmaker()() as db:
        farm = await _farm(db, farm_id)
        await _recipient(db, farm_id, membership_id)
        db.add(
            Animal(
                farm_id=farm_id,
                tag_number="WATCH-1",
                sex="F",
                status="ACTIVE",
                source="PURCHASED",
                current_bucket="DELIVERY",
            )
        )
        await db.commit()
        sent = await kidding_watch_daily(db, settings, provider, farm, now_local=midday(farm))
        # Idempotent within the day.
        again = await kidding_watch_daily(db, settings, provider, farm, now_local=midday(farm))

    assert sent == 1
    assert again == 0
    assert "1 does on kidding watch" in provider.sent[0][1]


async def test_feed_reorder_daily_alerts_under_reorder_items(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client, email="notif-feed@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    membership_id = await _membership_id(client, owner)
    settings = Settings(**DEFAULTS)
    provider = RecordingProvider()
    from app.models import FeedInventory

    async with get_sessionmaker()() as db:
        farm = await _farm(db, farm_id)
        await _recipient(db, farm_id, membership_id)
        db.add(
            FeedInventory(
                farm_id=farm_id,
                ingredient="Maize",
                category="CONCENTRATE",
                qty_on_hand=Decimal("5"),
                reorder_level=Decimal("50"),
                unit="kg",
            )
        )
        await db.commit()
        sent = await feed_reorder_daily(db, settings, provider, farm, now_local=midday(farm))

    assert sent == 1
    assert "Maize" in provider.sent[0][1]


async def test_overdue_critical_sweep_alerts_three_day_old_duties(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="notif-overdue@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    membership_id = await _membership_id(client, owner)
    settings = Settings(**DEFAULTS)
    provider = RecordingProvider()

    async with get_sessionmaker()() as db:
        farm = await _farm(db, farm_id)
        await _recipient(db, farm_id, membership_id)
        db.add(
            Task(
                farm_id=farm_id,
                title="Ancient duty",
                due_date=today() - timedelta(days=4),
                category="OTHER",
                status="PENDING",
            )
        )
        db.add(
            Task(
                farm_id=farm_id,
                title="Fresh overdue",
                due_date=today() - timedelta(days=1),
                category="OTHER",
                status="PENDING",
            )
        )
        await db.commit()
        sent = await overdue_critical_sweep(db, settings, provider, farm, now_local=midday(farm))

    assert sent == 1
    assert "1 duties are 3+ days overdue" in provider.sent[0][1]


# --- provider seam + config ----------------------------------------------------


async def test_console_provider_is_the_default_and_msg91_requires_a_key() -> None:
    settings = Settings(environment="development")
    assert isinstance(build_notification_provider(settings), ConsoleNotificationProvider)
    with pytest.raises(ValueError, match="MSG91_AUTH_KEY"):
        build_notification_provider(
            Settings(environment="development", notifications_provider="msg91")
        )


def test_production_notifications_config_fails_closed() -> None:
    with pytest.raises(ValueError, match="NOTIFICATIONS"):
        Settings(
            environment="production",
            cookie_secure=True,
            notifications_enabled=True,
            notifications_provider="console",
        )


# --- API + hook -----------------------------------------------------------------


async def test_preferences_api_is_owner_only_and_persists(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client, email="notif-api@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    membership_id = await _membership_id(client, owner)

    empty = await client.get(f"/api/team/workers/{membership_id}/notifications", headers=owner)
    assert empty.status_code == 200
    assert empty.json() is None

    saved = await client.put(
        f"/api/team/workers/{membership_id}/notifications",
        json={
            "phone": "+919888877777",
            "daily_digest": True,
            "screening_flags": True,
            "kidding_watch": False,
            "overdue_critical": False,
            "feed_reorder": False,
        },
        headers=owner,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["phone"] == "+919888877777"

    # A worker (even one viewing the roster) cannot edit preferences.
    worker = await register(client, "notif-worker@farm.in")
    forbidden = await client.put(
        f"/api/team/workers/{membership_id}/notifications",
        json={
            "phone": "+910000000000",
            "daily_digest": False,
            "screening_flags": False,
            "kidding_watch": False,
            "overdue_critical": False,
            "feed_reorder": False,
        },
        headers=worker | {"X-Farm-Id": str(farm_id)},
    )
    # A non-member cannot even resolve the worker: 403 (owner check) or 404
    # (membership lookup) — both refuse the write.
    assert forbidden.status_code in (403, 404)


async def test_screening_confirm_hook_fires_the_alert(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client, email="notif-hook@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    membership_id = await _membership_id(client, owner)
    from app.models import ScreeningFinding, ScreeningImage, ScreeningRun

    async with get_sessionmaker()() as db:
        await _recipient(db, farm_id, membership_id)
        image = ScreeningImage(
            farm_id=farm_id,
            bucket="BREEDING",
            s3_bucket="b",
            s3_key="raw/hook.jpg",
            captured_date=today(),
            status="FLAGGED",
        )
        db.add(image)
        await db.flush()
        run = ScreeningRun(
            farm_id=farm_id,
            image_id=image.id,
            stage="GATE",
            run_status="OK",
            provider="fake",
            model="fake",
            prompt_version="v1",
            latency_ms=1,
            created_at=utcnow(),
        )
        db.add(run)
        await db.flush()
        finding = ScreeningFinding(
            farm_id=farm_id,
            run_id=run.id,
            label="Orf lesions",
            note=None,
            status="PENDING_REVIEW",
            region="lips",
            confidence=None,
            severity=None,
        )
        db.add(finding)
        await db.commit()
        finding_id = finding.id

    # Enable notifications on the app settings singleton for the request.
    from app.core.config import get_settings

    get_settings().notifications_enabled = True
    # The hook runs on the real clock; an empty quiet window (start == end)
    # is never inside, whatever the farm-local hour.
    get_settings().notifications_quiet_start_hour = 4
    get_settings().notifications_quiet_end_hour = 4
    sent: list[tuple[str, str]] = []

    class HookProvider(ConsoleNotificationProvider):
        async def send_sms(self, phone: str, message: str):
            sent.append((phone, message))
            return await super().send_sms(phone, message)

    import logging

    logging.getLogger("app.services.notifications.hooks").setLevel(logging.DEBUG)

    import app.services.notifications.hooks as hooks

    original = hooks.build_notification_provider
    hooks.build_notification_provider = lambda _s: HookProvider()
    try:
        review = await client.post(
            f"/api/screening/findings/{finding_id}/review",
            json={"status": "CONFIRMED", "expected_status": "PENDING_REVIEW"},
            headers=owner,
        )
    finally:
        hooks.build_notification_provider = original
        get_settings().notifications_enabled = False
        get_settings().notifications_quiet_start_hour = 21
        get_settings().notifications_quiet_end_hour = 6

    assert review.status_code == 200, review.text
    assert len(sent) == 1
    assert "CONFIRMED" in sent[0][1]
    # The delivery is in the audit ledger.
    async with get_sessionmaker()() as db:
        rows = list(
            (
                await db.execute(
                    select(NotificationLog).where(NotificationLog.alert_class == "SCREENING_FLAG")
                )
            ).scalars()
        )
    assert len(rows) == 1 and rows[0].status == "SENT"
