"""Notifications subsystem (ITEM 4, 2026-09-21 playbook).

Covers: provider-mocked sends through the console provider, the day-dedupe
ledger (including the ON CONFLICT claim's exactly-once behaviour under
concurrency and the single-batch daily cap — 2026-09-28 audit N1/N2), the
alert hook's provider-transport lifecycle (N3), quiet hours, the per-worker
digest content (role-scoped duties), the daily kidding-watch and feed-reorder
scans, the overdue-critical sweep, and the owner-only preferences API (plus
the screening-confirm alert hook end to end).
"""

import asyncio
import importlib
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from app.core.config import Settings
from app.db import get_sessionmaker
from app.models import Farm, NotificationLog, NotificationRecipient, Task
from app.services.notifications import (
    ConsoleNotificationProvider,
    build_notification_provider,
    farms_ready_for_digest,
    feed_reorder_daily,
    kidding_watch_daily,
    notify_alert_class,
    overdue_critical_sweep,
    redact_phone_numbers,
    run_digest_for_farm,
    send_notification,
)
from app.services.notifications.providers import DeliveryResult, NotificationDeliveryError
from app.services.notifications.service import payload_hash
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


async def test_farm_daily_cap_holds_inside_a_single_fanout_batch(
    client: httpx.AsyncClient,
) -> None:
    """2026-09-28 audit N1: the cap used to count only COMMITTED rows, so one
    alert batch with N recipients sent N SMS regardless of the cap. Status
    writes now flush per recipient, and the batch stops at the ceiling."""
    owner = await owner_with_farm(client, email="notif-cap-batch@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    settings = Settings(**DEFAULTS, notifications_farm_daily_cap=2)
    provider = RecordingProvider()

    async with get_sessionmaker()() as db:
        farm = await _farm(db, farm_id)
        for phone in ("+919111111111", "+919222222222", "+919333333333"):
            await _recipient(db, farm_id, await _membership_id(client, owner), phone=phone)
        # One session, no interim commits: the exact fan-out shape that used
        # to defeat the cap.
        sent = await notify_alert_class(
            db,
            settings,
            provider,
            farm=farm,
            alert_class="SCREENING_FLAG",
            message="m",
            payload="finding:cap-batch",
            now_local=midday(farm),
        )
        rows = list(
            (
                await db.execute(select(NotificationLog).where(NotificationLog.farm_id == farm_id))
            ).scalars()
        )

    assert sent == 2
    assert len(provider.sent) == 2
    assert [row.status for row in rows].count("SKIPPED_CAP") == 1


async def test_concurrent_same_fact_delivery_sends_exactly_once(
    client: httpx.AsyncClient,
) -> None:
    """2026-09-28 audit N2: the dedupe slot is claimed ON CONFLICT before any
    send, so two concurrent sessions for the same fact produce exactly one
    SMS and one log row — never two sends plus an aborted batch."""
    owner = await owner_with_farm(client, email="notif-race@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    membership_id = await _membership_id(client, owner)
    settings = Settings(**DEFAULTS)
    provider = RecordingProvider()

    async with get_sessionmaker()() as db:
        recipient = await _recipient(db, farm_id, membership_id)
        recipient_id = recipient.id

    async def send_once():
        async with get_sessionmaker()() as db:
            farm = await _farm(db, farm_id)
            recipient = await db.get(NotificationRecipient, recipient_id)
            assert recipient is not None
            outcome = await send_notification(
                db,
                settings,
                provider,
                farm=farm,
                recipient=recipient,
                alert_class="SCREENING_FLAG",
                message="m",
                payload="finding:race:1",
                now_local=midday(farm),
            )
            await db.commit()
            return outcome

    first, second = await asyncio.gather(send_once(), send_once())

    assert {first.status, second.status} == {"SENT"}
    assert [first.fresh, second.fresh].count(True) == 1  # one sender, one replay
    assert len(provider.sent) == 1
    async with get_sessionmaker()() as db:
        rows = list((await db.execute(select(NotificationLog))).scalars())
    assert len(rows) == 1


async def test_emit_alert_closes_the_provider_transport(client: httpx.AsyncClient) -> None:
    """2026-09-28 audit N3: emit_alert built a fresh Msg91Provider per alert
    and never closed it — one leaked httpx transport per alert. The hook now
    closes any provider-owned client, like the digest loop's shutdown."""
    owner = await owner_with_farm(client, email="notif-close@farm.in")
    farm_id = int(owner["X-Farm-Id"])

    from app.core.config import get_settings

    get_settings().notifications_enabled = True

    class ClosingProvider(ConsoleNotificationProvider):
        def __init__(self) -> None:
            self.closed = 0

        async def aclose(self) -> None:
            self.closed += 1

    provider = ClosingProvider()
    import app.services.notifications.hooks as hooks

    original = hooks.build_notification_provider
    hooks.build_notification_provider = lambda _s: provider
    try:
        # No recipients opted in: the provider is still built and must still
        # be closed.
        await hooks.emit_alert(farm_id, "SCREENING_FLAG", "m", "finding:close:1")
    finally:
        hooks.build_notification_provider = original
        get_settings().notifications_enabled = False

    assert provider.closed == 1


class FlakyProvider:
    """Fails N transport sends, then delivers; counts every attempt."""

    name = "flaky"

    def __init__(self, failures_before_success: int) -> None:
        self.failures_before_success = failures_before_success
        self.calls = 0

    async def send_sms(self, phone: str, message: str) -> DeliveryResult:
        self.calls += 1
        if self.calls <= self.failures_before_success:
            raise NotificationDeliveryError(f"gateway 502 (attempt {self.calls})")
        return DeliveryResult(ok=True, message_id=f"flaky-{self.calls}")


class RefusingProvider:
    """Terminal provider answer: the request was seen and rejected."""

    name = "refusing"

    def __init__(self, error: str = "DLT template rejected") -> None:
        self.calls = 0
        self._error = error

    async def send_sms(self, phone: str, message: str) -> DeliveryResult:
        self.calls += 1
        return DeliveryResult(ok=False, error=self._error)


def test_redact_phone_numbers_strips_mobiles_but_keeps_short_ids() -> None:
    assert (
        redact_phone_numbers("MSG91 rejected mobile +919876543210: template mismatch (code 4021)")
        == "MSG91 rejected mobile <redacted>: template mismatch (code 4021)"
    )
    assert (
        redact_phone_numbers("balance low for account 918765432099")
        == "balance low for account <redacted>"
    )
    # Short ids, codes and ports survive — enough for diagnosis.
    assert redact_phone_numbers("error 5007, batch 12345, port 5432") == (
        "error 5007, batch 12345, port 5432"
    )


async def test_provider_error_echoing_the_recipient_phone_is_stored_redacted(
    client: httpx.AsyncClient,
) -> None:
    """MSG91 error payloads may name the recipient's mobile; the durable log
    keeps the diagnostic text but never the number."""
    owner = await owner_with_farm(client, email="notif-redact@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    membership_id = await _membership_id(client, owner)
    settings = Settings(**DEFAULTS)
    provider = RefusingProvider(error="Invalid mobile 919876543210 requested (code 4021)")

    async with get_sessionmaker()() as db:
        farm = await _farm(db, farm_id)
        recipient = await _recipient(db, farm_id, membership_id)
        outcome = await send_notification(
            db,
            settings,
            provider,
            farm=farm,
            recipient=recipient,
            alert_class="SCREENING_FLAG",
            message="m",
            payload="finding:redact:1",
            now_local=midday(farm),
        )
        await db.commit()
        row = (
            await db.execute(select(NotificationLog).where(NotificationLog.status == "FAILED"))
        ).scalar_one()

    assert outcome.status == "FAILED"
    assert row.error == "Invalid mobile <redacted> requested (code 4021)"


async def test_transport_blip_is_retried_and_still_delivers(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="notif-retry@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    membership_id = await _membership_id(client, owner)
    settings = Settings(**DEFAULTS, notifications_send_retry_backoff_seconds=0.0)
    provider = FlakyProvider(failures_before_success=1)

    async with get_sessionmaker()() as db:
        farm = await _farm(db, farm_id)
        recipient = await _recipient(db, farm_id, membership_id)
        outcome = await send_notification(
            db,
            settings,
            provider,
            farm=farm,
            recipient=recipient,
            alert_class="SCREENING_FLAG",
            message="m",
            payload="finding:retry:1",
            now_local=midday(farm),
        )
        await db.commit()

    assert outcome.status == "SENT" and outcome.fresh
    assert provider.calls == 2  # one blip, one recovery — no day-dedupe hole


async def test_persistent_transport_failure_records_failed(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="notif-dead@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    membership_id = await _membership_id(client, owner)
    settings = Settings(**DEFAULTS, notifications_send_retry_backoff_seconds=0.0)
    provider = FlakyProvider(failures_before_success=99)

    async with get_sessionmaker()() as db:
        farm = await _farm(db, farm_id)
        recipient = await _recipient(db, farm_id, membership_id)
        outcome = await send_notification(
            db,
            settings,
            provider,
            farm=farm,
            recipient=recipient,
            alert_class="SCREENING_FLAG",
            message="m",
            payload="finding:dead:1",
            now_local=midday(farm),
        )
        await db.commit()
        row = (
            await db.execute(select(NotificationLog).where(NotificationLog.status == "FAILED"))
        ).scalar_one()

    assert outcome.status == "FAILED" and outcome.fresh
    assert provider.calls == 2  # the configured attempt budget, not a loop
    assert row.error is not None and "gateway 502" in row.error


async def test_terminal_provider_refusal_is_not_retried(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client, email="notif-refuse@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    membership_id = await _membership_id(client, owner)
    settings = Settings(**DEFAULTS)
    provider = RefusingProvider()

    async with get_sessionmaker()() as db:
        farm = await _farm(db, farm_id)
        recipient = await _recipient(db, farm_id, membership_id)
        outcome = await send_notification(
            db,
            settings,
            provider,
            farm=farm,
            recipient=recipient,
            alert_class="SCREENING_FLAG",
            message="m",
            payload="finding:refused:1",
            now_local=midday(farm),
        )
        await db.commit()

    assert outcome.status == "FAILED" and outcome.fresh
    assert provider.calls == 1  # the provider answered; retrying re-rejects


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


async def test_digest_skips_inactive_memberships_entirely(client: httpx.AsyncClient) -> None:
    """2026-09-29 audit: a deactivated worker's recipient gets NO digest SMS —
    not even a 'no duties (inactive)' one. Spending daily-cap budget on a
    deactivated membership is pure cost."""
    owner = await owner_with_farm(client, email="notif-inactive@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    membership_id = await _membership_id(client, owner)
    settings = Settings(**DEFAULTS)
    provider = RecordingProvider()

    deactivated = await client.put(
        f"/api/team/workers/{membership_id}/status",
        json={"is_active": False},
        headers=owner,
    )
    assert deactivated.status_code == 200, deactivated.text

    async with get_sessionmaker()() as db:
        farm = await _farm(db, farm_id)
        await _recipient(db, farm_id, membership_id)
        await db.commit()
        summary = await run_digest_for_farm(db, settings, provider, farm, now_local=midday(farm))
        log_rows = (
            (await db.execute(select(NotificationLog).where(NotificationLog.farm_id == farm_id)))
            .scalars()
            .all()
        )

    assert provider.sent == []
    assert (summary.sent, summary.skipped) == (0, 1)
    assert log_rows == [], "no dedupe slot may be claimed for an inactive membership"


async def test_mid_fanout_crash_cannot_rollback_earlier_recipients(
    client: httpx.AsyncClient,
) -> None:
    """2026-09-29 audit (N2 tail): the digest used to commit only after the
    LAST recipient, so a mid-fan-out failure rolled back earlier recipients'
    settled rows — and the next minute-tick re-sent (re-billed) them. Each
    recipient's outcome now commits before the next begins."""
    owner = await owner_with_farm(client, email="notif-crash@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    settings = Settings(**DEFAULTS)

    class ExplodingProvider(RecordingProvider):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        async def send_sms(self, phone: str, message: str):  # type: ignore[override]
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("simulated mid-fan-out crash (not a delivery error)")
            return await super().send_sms(phone, message)

    boom = ExplodingProvider()
    async with get_sessionmaker()() as db:
        farm = await _farm(db, farm_id)
        # Recipients are one-per-membership: two workers, two phones.
        for phone in ("+919444444441", "+919444444442"):
            await _recipient(db, farm_id, await _membership_id(client, owner), phone=phone)
        await db.commit()
        with pytest.raises(RuntimeError, match="simulated mid-fan-out crash"):
            await run_digest_for_farm(db, settings, boom, farm, now_local=midday(farm))
        await db.rollback()
        # Earlier recipients' SENT rows SURVIVED the crash: committed per
        # recipient, not per fan-out.
        statuses = (
            (
                await db.execute(
                    select(NotificationLog.status).where(NotificationLog.farm_id == farm_id)
                )
            )
            .scalars()
            .all()
        )

    assert statuses.count("SENT") == 1, "recipient 1's delivery must be durable"


async def test_repeat_quiet_hours_touches_do_not_rewrite_the_placeholder(
    client: httpx.AsyncClient,
) -> None:
    """2026-09-29 audit: inside the quiet window, a digest minute that fires
    every tick used to DELETE + re-CLAIM + re-settle the SKIPPED_QUIET
    placeholder each minute all night. A repeat touch now reads the existing
    placeholder back: one row, one id, never rewritten."""
    owner = await owner_with_farm(client, email="notif-quiet@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    membership_id = await _membership_id(client, owner)
    settings = Settings(**DEFAULTS)
    provider = RecordingProvider()

    async with get_sessionmaker()() as db:
        farm = await _farm(db, farm_id)
        recipient_row = await _recipient(db, farm_id, membership_id)
        await db.commit()
        night = datetime.now(ZoneInfo(farm.timezone)).replace(
            hour=23, minute=5, second=0, microsecond=0
        )  # inside the default 21→6 quiet window
        first = await send_notification(
            db,
            settings,
            provider,
            farm=farm,
            recipient=recipient_row,
            alert_class="DAILY_DIGEST",
            message="m",
            payload=f"digest:{night.date().isoformat()}",
            now_local=night,
        )
        row_id = (
            (await db.execute(select(NotificationLog.id).where(NotificationLog.farm_id == farm_id)))
            .scalars()
            .one()
        )
        # Second and third touches inside the same window: read-only.
        for _ in range(2):
            repeat = await send_notification(
                db,
                settings,
                provider,
                farm=farm,
                recipient=recipient_row,
                alert_class="DAILY_DIGEST",
                message="m",
                payload=f"digest:{night.date().isoformat()}",
                now_local=night.replace(minute=6),
            )
            assert (repeat.status, repeat.fresh) == ("SKIPPED_QUIET", False)
        ids = (
            (await db.execute(select(NotificationLog.id).where(NotificationLog.farm_id == farm_id)))
            .scalars()
            .all()
        )

    assert (first.status, first.fresh) == ("SKIPPED_QUIET", True)
    assert ids == [row_id], "the placeholder must never be rewritten inside the window"


async def test_farms_ready_for_digest_fires_at_or_past_the_local_digest_time() -> None:
    """Catch-up window: the loop ticks roughly every minute and can drift
    past a farm's digest minute, so readiness is "same-day local time at or
    past the configured digest time", not exact-minute equality."""
    settings = Settings(**DEFAULTS, notifications_digest_hour=6, notifications_digest_minute=30)
    from app.models import User

    async with get_sessionmaker()() as db:
        tz_owner = User(email="tz-owner@farm.in", password_hash="not-used", created_at=utcnow())
        db.add(tz_owner)
        await db.flush()
        farm = Farm(name="TZ Farm", owner_id=tz_owner.id, timezone="Asia/Kolkata")
        db.add(farm)
        await db.commit()
        # 01:00 UTC == 06:30 local: the configured minute itself.
        on_time = await farms_ready_for_digest(
            db, settings, datetime(2026, 9, 21, 1, 0, tzinfo=UTC)
        )
        # 01:17 UTC == 06:47 local: the loop ticked late — still fires.
        late = await farms_ready_for_digest(db, settings, datetime(2026, 9, 21, 1, 17, tzinfo=UTC))
        # 00:59 UTC == 06:29 local: one minute early — no digest yet.
        early = await farms_ready_for_digest(db, settings, datetime(2026, 9, 21, 0, 59, tzinfo=UTC))
        # 19:00 UTC == 00:30 local the NEXT day: same wall-clock minute as a
        # 00:30 digest would be, but before 06:30 — must not fire.
        past_midnight = await farms_ready_for_digest(
            db, settings, datetime(2026, 9, 21, 19, 0, tzinfo=UTC)
        )
    assert [f.id for f in on_time] == [farm.id]
    assert [f.id for f in late] == [farm.id]
    assert early == []
    assert past_midnight == []


async def test_digest_catch_up_window_sends_late_but_exactly_once(
    client: httpx.AsyncClient,
) -> None:
    """A quiet-hours placeholder does not settle the day (the digest fires
    once the window opens), and a settled DAILY_DIGEST row closes the
    catch-up window — exactly one digest per farm per local day."""
    owner = await owner_with_farm(client, email="digest-once@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    membership_id = await _membership_id(client, owner)
    settings = Settings(
        **DEFAULTS,
        notifications_digest_hour=6,
        notifications_digest_minute=30,
        notifications_quiet_start_hour=6,
        notifications_quiet_end_hour=7,
    )
    provider = RecordingProvider()

    async with get_sessionmaker()() as db:
        farm = await _farm(db, farm_id)
        await _recipient(db, farm_id, membership_id)
        tz = ZoneInfo(farm.timezone)
        quiet_local = datetime.now(tz).replace(hour=6, minute=45, second=0, microsecond=0)
        # 06:45: past the 06:30 digest time but inside quiet hours.
        quiet = await run_digest_for_farm(db, settings, provider, farm, now_local=quiet_local)
        assert (quiet.sent, quiet.skipped) == (0, 1)
        # SKIPPED_QUIET is not a delivery outcome: the farm stays ready.
        still_ready = await farms_ready_for_digest(db, settings, quiet_local.astimezone(UTC))
        assert farm_id in [f.id for f in still_ready]
        # 07:15: quiet window over; the late tick catches up and sends.
        later_local = quiet_local.replace(hour=7, minute=15)
        sent = await run_digest_for_farm(db, settings, provider, farm, now_local=later_local)
        assert sent.sent == 1
        done = await farms_ready_for_digest(db, settings, later_local.astimezone(UTC))
    assert farm_id not in [f.id for f in done]
    assert len(provider.sent) == 1


async def test_farms_ready_for_digest_respects_the_loop_batch_size() -> None:
    """The batch bounds ready digest fan-out per tick — not the SCAN. With
    the settled-check in SQL before the limit, an already-done farm never
    consumes a slot, so a small batch can no longer permanently starve every
    farm after the first page (2026-09-29 audit)."""
    settings = Settings(
        **DEFAULTS,
        notifications_digest_hour=6,
        notifications_digest_minute=30,
        notifications_loop_batch_size=1,
    )
    now = datetime(2026, 9, 21, 1, 0, tzinfo=UTC)
    from app.models import FarmMembership, Role, User

    async with get_sessionmaker()() as db:
        farm_ids = []
        for name in ("Batch First", "Batch Second"):
            owner_row = User(
                email=f"tz-{name.lower().replace(' ', '-')}@farm.in",
                password_hash="not-used",
                created_at=utcnow(),
            )
            db.add(owner_row)
            await db.flush()
            farm = Farm(name=name, owner_id=owner_row.id, timezone="Asia/Kolkata")
            db.add(farm)
            await db.flush()
            farm_ids.append(farm.id)
        await db.commit()
        # Both farms ready, batch of one: only the first farm by id this tick.
        ready = await farms_ready_for_digest(db, settings, now)
        assert [f.id for f in ready] == [min(farm_ids)]
        # The first farm's digest settles (any settled DAILY_DIGEST row for
        # its local today closes the catch-up window). The membership target
        # only needs to exist for the log row's FK.
        settled_farm = min(farm_ids)
        owner_row = (
            await db.execute(select(User).where(User.email == "tz-batch-first@farm.in"))
        ).scalar_one()
        role = Role(farm_id=settled_farm, name="Digest probe role", permissions=["dashboard.view"])
        db.add(role)
        await db.flush()
        membership = FarmMembership(
            farm_id=settled_farm,
            user_id=owner_row.id,
            role_id=role.id,
            is_active=True,
        )
        db.add(membership)
        await db.flush()
        recipient = NotificationRecipient(
            farm_id=settled_farm,
            membership_id=membership.id,
            phone="+919999999998",
            daily_digest=True,
        )
        db.add(recipient)
        await db.flush()
        db.add(
            NotificationLog(
                farm_id=settled_farm,
                recipient_id=recipient.id,
                alert_class="DAILY_DIGEST",
                payload_hash=payload_hash("DAILY_DIGEST:digest:settled"),
                local_date=now.astimezone(ZoneInfo("Asia/Kolkata")).date(),
                status="SENT",
            )
        )
        await db.commit()
        # Same tick-sized batch, same minute: the settled farm no longer
        # consumes the slot — the second farm is reached, not starved.
        ready_after = await farms_ready_for_digest(db, settings, now)
    assert [f.id for f in ready_after] == [max(farm_ids)], (
        "a settled farm must not consume the batch slot of the next ready farm"
    )


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
            "movement_restriction": True,
            "verified": True,
        },
        headers=owner,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["phone"] == "+919888877777"
    assert saved.json()["movement_restriction"] is True
    assert saved.json()["verified"] is True

    # The new opt-in and the owner's verification flag persist: a fresh GET
    # answers the stored row, not the echo of the request just made.
    fetched = await client.get(f"/api/team/workers/{membership_id}/notifications", headers=owner)
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["movement_restriction"] is True
    assert fetched.json()["verified"] is True

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


async def test_notification_prefs_audit_event_never_logs_the_phone(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    """The preferences audit line names the membership only: a worker's phone
    number is PII, and team.py's audit stream logs ids, never contact material
    (``redact_phone_numbers`` keeps the same rule for SMS bodies)."""
    owner = await owner_with_farm(client, email="notif-audit@farm.in")
    membership_id = await _membership_id(client, owner)

    with caplog.at_level(logging.INFO, logger="goatfarm.audit"):
        saved = await client.put(
            f"/api/team/workers/{membership_id}/notifications",
            json={
                "phone": "+919888877777",
                "daily_digest": True,
                "screening_flags": True,
                "kidding_watch": False,
                "overdue_critical": False,
                "feed_reorder": False,
                "movement_restriction": True,
                "verified": True,
            },
            headers=owner,
        )
    assert saved.status_code == 200, saved.text
    events = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("security_event")
    ]
    matching = [m for m in events if "event='team.worker.notification_prefs'" in m]
    assert len(matching) == 1, events
    assert f"membership_id={membership_id}" in matching[0]
    assert "phone" not in matching[0]
    assert "+919888877777" not in matching[0]


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


class _MovementHooks:
    """Enable the alert fan-out with a capturing provider (hook-test helper)."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def __enter__(self) -> "_MovementHooks":
        from app.core.config import get_settings

        self._settings = get_settings()
        self._settings.notifications_enabled = True
        # An empty quiet window (start == end) is never inside, whatever the
        # farm-local hour the hook runs at.
        self._settings.notifications_quiet_start_hour = 4
        self._settings.notifications_quiet_end_hour = 4

        hooks = importlib.import_module("app.services.notifications.hooks")
        self._hooks = hooks
        self._original = hooks.build_notification_provider
        capturing = self

        class CapturingProvider(ConsoleNotificationProvider):
            async def send_sms(self, phone: str, message: str):
                capturing.sent.append((phone, message))
                return await super().send_sms(phone, message)

        hooks.build_notification_provider = lambda _s: CapturingProvider()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._hooks.build_notification_provider = self._original
        self._settings.notifications_enabled = False
        self._settings.notifications_quiet_start_hour = 21
        self._settings.notifications_quiet_end_hour = 6


async def _movement_recipient(db, farm_id: int, membership_id: int, *, opted_in: bool) -> None:
    db.add(
        NotificationRecipient(
            farm_id=farm_id,
            membership_id=membership_id,
            phone="+919999999999",
            daily_digest=False,
            screening_flags=False,
            kidding_watch=False,
            overdue_critical=False,
            feed_reorder=False,
            movement_restriction=opted_in,
        )
    )
    await db.commit()


async def _movement_animal(farm_id: int, tag: str) -> None:
    from app.models import Animal

    async with get_sessionmaker()() as db:
        db.add(
            Animal(
                farm_id=farm_id,
                tag_number=tag,
                sex="F",
                status="ACTIVE",
                source="PURCHASED",
                current_bucket="FOUNDATION",
            )
        )
        await db.commit()


async def test_movement_restriction_hook_fires_on_scheduled_disease_event(
    client: httpx.AsyncClient,
) -> None:
    """ITEM 4 hook (a): recording a suspected scheduled disease in the health
    log fans a MOVEMENT_RESTRICTION alert to the opted-in recipients."""
    owner = await owner_with_farm(client, email="notif-move@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    membership_id = await _membership_id(client, owner)
    await _movement_animal(farm_id, "MOVE-1")
    async with get_sessionmaker()() as db:
        await _movement_recipient(db, farm_id, membership_id, opted_in=True)
    from app.models import Animal

    async with get_sessionmaker()() as db:
        animal_id = (
            await db.execute(select(Animal.id).where(Animal.tag_number == "MOVE-1"))
        ).scalar_one()

    with _MovementHooks() as hooks:
        event = await client.post(
            "/api/health/events",
            json={
                "animal_id": animal_id,
                "date": today().isoformat(),
                "type": "TREATMENT",
                "disease_target": "PPR",
                "suspected_scheduled_disease": True,
                "notes": "Vet suspects PPR; authority notified.",
            },
            headers=owner,
        )

    assert event.status_code == 201, event.text
    assert len(hooks.sent) == 1
    assert "PPR" in hooks.sent[0][1]
    assert "movement restriction" in hooks.sent[0][1]
    async with get_sessionmaker()() as db:
        rows = list(
            (
                await db.execute(
                    select(NotificationLog).where(
                        NotificationLog.alert_class == "MOVEMENT_RESTRICTION"
                    )
                )
            ).scalars()
        )
    assert len(rows) == 1 and rows[0].status == "SENT"


async def test_movement_restriction_hook_skips_recipients_not_opted_in(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="notif-move-off@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    membership_id = await _membership_id(client, owner)
    await _movement_animal(farm_id, "MOVE-OFF")
    async with get_sessionmaker()() as db:
        await _movement_recipient(db, farm_id, membership_id, opted_in=False)

    with _MovementHooks() as hooks:
        from app.models import Animal

        async with get_sessionmaker()() as db:
            animal_id = (
                await db.execute(select(Animal.id).where(Animal.tag_number == "MOVE-OFF"))
            ).scalar_one()
        event = await client.post(
            "/api/health/events",
            json={
                "animal_id": animal_id,
                "date": today().isoformat(),
                "type": "TREATMENT",
                "disease_target": "FMD",
                "suspected_scheduled_disease": True,
            },
            headers=owner,
        )

    assert event.status_code == 201, event.text
    assert hooks.sent == []
    async with get_sessionmaker()() as db:
        rows = list(
            (
                await db.execute(
                    select(NotificationLog).where(
                        NotificationLog.alert_class == "MOVEMENT_RESTRICTION"
                    )
                )
            ).scalars()
        )
    assert rows == []


async def test_mortality_hook_fires_on_scheduled_disease_death(
    client: httpx.AsyncClient,
) -> None:
    """ITEM 4 hook (b): a DEAD status change carrying a scheduled-disease
    suspicion alerts with the animal's own identity, not just the disease."""
    owner = await owner_with_farm(client, email="notif-dead-hook@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    membership_id = await _membership_id(client, owner)
    await _movement_animal(farm_id, "DEAD-MOVE-1")
    async with get_sessionmaker()() as db:
        await _movement_recipient(db, farm_id, membership_id, opted_in=True)
        from app.models import Animal

        animal_id = (
            await db.execute(select(Animal.id).where(Animal.tag_number == "DEAD-MOVE-1"))
        ).scalar_one()

    with _MovementHooks() as hooks:
        status_change = await client.post(
            f"/api/animals/{animal_id}/status",
            json={
                "new_status": "DEAD",
                "suspected_scheduled_disease": True,
                "suspected_disease": "anthrax",
            },
            headers=owner,
        )

    assert status_change.status_code == 200, status_change.text
    assert len(hooks.sent) == 1
    assert "DEAD-MOVE-1" in hooks.sent[0][1]
    assert "anthrax" in hooks.sent[0][1]
    async with get_sessionmaker()() as db:
        rows = list(
            (
                await db.execute(
                    select(NotificationLog).where(
                        NotificationLog.alert_class == "MOVEMENT_RESTRICTION"
                    )
                )
            ).scalars()
        )
    assert len(rows) == 1 and rows[0].status == "SENT"


async def test_recipient_membership_fk_blocks_cross_farm_rows(
    client: httpx.AsyncClient,
) -> None:
    """2026-09-29 audit, L2: notification_recipients.membership_id was the
    last single-column tenant reference in the notifications schema — a row
    could point at another farm's membership via direct SQL, the exact class
    d7e9f1a3b5c7's docstring claims was closed. The composite
    (farm_id, membership_id) FK now refuses it, like notification_log."""
    from sqlalchemy import text

    from app.models import FarmMembership, Role, User

    owner_a = await owner_with_farm(client, email="tenant-fk-a@farm.in")
    owner_b = await owner_with_farm(client, email="tenant-fk-b@farm.in")
    farm_a = int(owner_a["X-Farm-Id"])
    farm_b = int(owner_b["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        owner_b_row = (
            await db.execute(select(User).where(User.email == "tenant-fk-b@farm.in"))
        ).scalar_one()
        role = Role(farm_id=farm_b, name="Cross-farm probe", permissions=["dashboard.view"])
        db.add(role)
        await db.flush()
        membership_b = FarmMembership(
            farm_id=farm_b, user_id=owner_b_row.id, role_id=role.id, is_active=True
        )
        db.add(membership_b)
        await db.commit()
        # Snapshot before the failing insert: the rollback it forces expires
        # the ORM instance, and re-reading .id then would refresh a detached
        # object.
        membership_b_id = membership_b.id

        # Direct SQL with farm A's tenant and farm B's membership: the
        # composite FK must abort the insert before it ever lands.
        with pytest.raises(DBAPIError):
            await db.execute(
                text(
                    "INSERT INTO notification_recipients "
                    "(farm_id, membership_id, phone, daily_digest, screening_flags, "
                    "kidding_watch, overdue_critical, feed_reorder, movement_restriction, "
                    "verified, created_at, updated_at) "
                    "VALUES (:farm, :membership, '+919999999997', true, true, true, "
                    "true, true, true, true, now(), now())"
                ),
                {"farm": farm_a, "membership": membership_b_id},
            )
            await db.commit()
        await db.rollback()

        # Control: the same membership under its OWN farm still inserts —
        # the guard is tenant-crossing, not the table.
        await db.execute(
            text(
                "INSERT INTO notification_recipients "
                "(farm_id, membership_id, phone, daily_digest, screening_flags, "
                "kidding_watch, overdue_critical, feed_reorder, movement_restriction, "
                "verified, created_at, updated_at) "
                "VALUES (:farm, :membership, '+919999999996', true, true, true, "
                "true, true, true, true, now(), now())"
            ),
            {"farm": farm_b, "membership": membership_b_id},
        )
        await db.commit()
