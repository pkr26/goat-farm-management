"""Notification service (ITEM 4, 2026-09-21 playbook).

Every delivery passes through, in order:

1. quiet hours (farm-local) — inside the window, a first touch CLAIMS the
   slot and settles it as SKIPPED_QUIET, never sent (an alert is same-day
   information); a repeat touch inside the same window reads the existing
   placeholder back instead of delete/claim/settling it again, so a digest
   minute inside the quiet window cannot churn writes all night
   (2026-09-29 audit);
2. placeholder cleanup — once the window has OPENED, the SKIPPED_QUIET row
   holds the day's dedupe slot no longer and is deleted on re-entry;
3. the dedupe CLAIM — ``INSERT ... ON CONFLICT DO NOTHING`` on
   (farm, recipient, class, payload, local day) BEFORE any send, COMMITTED
   before the send (2026-09-28 audit, N2; 2026-09-29 durability pass): the
   claim is durable and visible to every other session, so two concurrent
   sessions can never both send the same paid SMS, and a crash mid-fan-out
   can no longer roll back earlier recipients' settled rows for a next-tick
   re-send — each recipient's outcome commits before the next begins. A
   loser reads the winner's row, waiting out an in-flight SENDING so both
   callers learn the settled outcome;
4. the per-farm daily cap — counts every claimed-or-settled row (SENDING
   claims are committed before any send, so the count is exact for this
   session AND for any concurrent one — 2026-09-28 audit, N1);
5. the provider send — the outcome lands in the log either way.

A claim left in SENDING by a crash settles the slot for the day without a
delivery: the safe side for paid SMS (no double-send), at the price of one
missed alert for that fact that day.

The daily digest aggregates each worker's duties due today (``task_scope``)
into one SMS per opted-in recipient; alert callers pass a stable ``payload``
whose hash IS the dedupe identity (e.g. "finding:42:CONFIRMED"). A recipient
whose membership is inactive gets NO digest at all — not even a "no duties"
SMS (2026-09-29 audit).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.config import Settings
from ...models import Farm, NotificationLog, NotificationRecipient, Task, TaskStatus
from ...models.notifications import ALERT_CLASSES
from ...utils import today
from .providers import (
    DeliveryResult,
    NotificationDeliveryError,
    NotificationProvider,
    redact_phone_numbers,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SendOutcome:
    """One guarded attempt: the recorded status plus whether THIS call
    actually delivered (day-dedupe replays answer the committed status with
    fresh=False, so fan-out counters report new deliveries only)."""

    status: str
    fresh: bool


def payload_hash(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _in_quiet_hours(settings: Settings, now_local: datetime) -> bool:
    start = settings.notifications_quiet_start_hour
    end = settings.notifications_quiet_end_hour
    hour = now_local.hour
    if start > end:
        # Overnight window (e.g. 21 → 6): quiet from start, or before end.
        return hour >= start or hour < end
    return start <= hour < end


async def _farm_send_count_today(
    db: AsyncSession, farm_id: int, local_date: date, excluding_id: int
) -> int:
    # Exact at read time: claims are COMMITTED before any send (see
    # send_notification), so SENDING rows are visible to every session —
    # this one and any concurrent loop — and settled rows are durable. The
    # caller's own fresh claim is excluded (it is about to settle).
    return int(
        (
            await db.execute(
                select(func.count())
                .select_from(NotificationLog)
                .where(
                    NotificationLog.farm_id == farm_id,
                    NotificationLog.local_date == local_date,
                    NotificationLog.status.in_(["SENDING", "SENT", "FAILED"]),
                    NotificationLog.id != excluding_id,
                )
            )
        ).scalar_one()
    )


async def _wait_for_claim_to_settle(db: AsyncSession, log_id: int, settings: Settings) -> str:
    """The dedupe loser's view of the winner's row. A concurrent winner may
    have committed its CLAIM but not yet its settle; poll the settled status
    for at most the winner's worst-case send budget (retries + backoff plus
    slack) so both callers learn the delivery outcome. A claim still in
    SENDING after the bound means the winner died mid-send — the slot is
    settled for the day and the caller reports the skip."""
    attempts = max(1, settings.notifications_send_retry_attempts)
    backoff = settings.notifications_send_retry_backoff_seconds
    deadline = asyncio.get_running_loop().time() + 5.0 + attempts * (attempts + 1) * backoff
    while True:
        row = (
            await db.execute(
                select(NotificationLog)
                .where(NotificationLog.id == log_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        if row.status != "SENDING":
            return row.status
        if asyncio.get_running_loop().time() >= deadline:
            return row.status
        await asyncio.sleep(0.1)


async def _send_with_retry(
    provider: NotificationProvider, phone: str, message: str, settings: Settings
) -> DeliveryResult:
    """Transport failures get one bounded retry with backoff (ITEM 4).

    A blip at the SMS gateway must not strand a same-day alert behind the
    day-dedupe as FAILED. Terminal provider answers (``ok=False``) are NOT
    retried — the provider saw the request and rejected it.
    """
    attempts = max(1, settings.notifications_send_retry_attempts)
    backoff = settings.notifications_send_retry_backoff_seconds
    last_exc: NotificationDeliveryError | None = None
    for attempt in range(attempts):
        try:
            return await provider.send_sms(phone, message)
        except NotificationDeliveryError as exc:
            last_exc = exc
            if attempt + 1 < attempts:
                logger.warning(
                    "notification transport failed (attempt %d/%d), retrying: %s",
                    attempt + 1,
                    attempts,
                    exc,
                )
                await asyncio.sleep(backoff * (attempt + 1))
    if last_exc is None:
        raise NotificationDeliveryError("send retry loop exited without an attempt")
    raise last_exc


async def send_notification(
    db: AsyncSession,
    settings: Settings,
    provider: NotificationProvider,
    *,
    farm: Farm,
    recipient: NotificationRecipient,
    alert_class: str,
    message: str,
    payload: str,
    now_local: datetime | None = None,
) -> SendOutcome:
    """One guarded delivery attempt. See SendOutcome for the return.

    ``now_local`` overrides the farm wall clock (tests); production callers
    leave it unset."""
    if alert_class not in ALERT_CLASSES:
        raise ValueError(f"Unknown alert class {alert_class!r}")
    now = now_local or datetime.now(ZoneInfo(farm.timezone))
    local_date = now.date()
    digest = payload_hash(f"{alert_class}:{payload}")
    fact_filter = (
        NotificationLog.farm_id == farm.id,
        NotificationLog.recipient_id == recipient.id,
        NotificationLog.alert_class == alert_class,
        NotificationLog.payload_hash == digest,
        NotificationLog.local_date == local_date,
    )

    async def _claim() -> int | None:
        """INSERT ... ON CONFLICT DO NOTHING claim of the day-dedupe slot.
        The claim runs BEFORE any send (2026-09-28 audit, N2) and its caller
        commits it before the send: the ON CONFLICT arbitration makes exactly
        one session the sender, durably."""
        return (
            await db.execute(
                pg_insert(NotificationLog)
                .values(
                    farm_id=farm.id,
                    recipient_id=recipient.id,
                    alert_class=alert_class,
                    payload_hash=digest,
                    local_date=local_date,
                    status="SENDING",
                )
                .on_conflict_do_nothing(constraint="uq_notification_log_day_dedupe")
                .returning(NotificationLog.id)
            )
        ).scalar_one_or_none()

    async def _current_row() -> NotificationLog:
        return (
            await db.execute(
                select(NotificationLog)
                .where(*fact_filter)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()

    if _in_quiet_hours(settings, now):
        # First touch inside the window claims the slot and settles the
        # SKIPPED_QUIET placeholder; a repeat touch reads it back WITHOUT
        # delete/claim/settle — a digest minute inside the quiet window used
        # to rewrite the placeholder every minute-tick all night
        # (2026-09-29 audit).
        existing = (
            await db.execute(
                select(NotificationLog).where(
                    *fact_filter, NotificationLog.status == "SKIPPED_QUIET"
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return SendOutcome(status="SKIPPED_QUIET", fresh=False)
        claimed_id = await _claim()
        if claimed_id is None:
            return SendOutcome(
                status=await _wait_for_claim_to_settle(db, (await _current_row()).id, settings),
                fresh=False,
            )
        log = await db.get(NotificationLog, claimed_id)
        if log is None:  # pragma: no cover — the row this loop just inserted
            raise RuntimeError("claimed notification row vanished before settle")
        log.status = "SKIPPED_QUIET"
        log.error = "quiet hours"
        await db.commit()
        return SendOutcome(status="SKIPPED_QUIET", fresh=True)

    # Dedupe: a settled attempt (SENT/FAILED/SKIPPED_CAP) for the same fact
    # today means somebody already handled it. A quiet-hours skip is NOT a
    # delivery outcome — it holds the day-dedupe slot only until the window
    # opens, so it is deleted here and the attempt re-runs.
    quiet_placeholder = cast(
        CursorResult[Any],
        await db.execute(
            delete(NotificationLog).where(
                *fact_filter,
                NotificationLog.status == "SKIPPED_QUIET",
            )
        ),
    )
    if quiet_placeholder.rowcount:
        await db.flush()

    claimed_id = await _claim()
    if claimed_id is None:
        # Lost the race: the winner's row exists (or is being settled).
        loser_row = await _current_row()
        return SendOutcome(
            status=await _wait_for_claim_to_settle(db, loser_row.id, settings),
            fresh=False,
        )
    # Commit the claim BEFORE any send: the SENDING row becomes durable and
    # farm-wide visible, so (a) a crash mid-send settles the slot without a
    # delivery — the documented safe side for paid SMS — and (b) a crash
    # mid-fan-out can never roll back earlier recipients' settled rows for a
    # next-tick re-send (2026-09-29 audit).
    await db.commit()
    log = await db.get(NotificationLog, claimed_id)
    if log is None:  # pragma: no cover — the row this loop just inserted
        raise RuntimeError("claimed notification row vanished before settle")

    async def settle(
        status: str, message_id: str | None = None, error: str | None = None
    ) -> SendOutcome:
        log.status = status
        log.provider_message_id = message_id
        # Provider payloads can echo the recipient's number (MSG91); it never
        # belongs in the durable log.
        log.error = None if error is None else redact_phone_numbers(error)[:500]
        # Commit EVERY status write so the outcome is durable before the next
        # recipient begins and the per-farm daily cap is exact across
        # sessions (2026-09-28 audit, N1; 2026-09-29 durability pass).
        await db.commit()
        return SendOutcome(status=status, fresh=True)

    sent_today = await _farm_send_count_today(db, farm.id, local_date, log.id)
    if sent_today >= settings.notifications_farm_daily_cap:
        return await settle("SKIPPED_CAP", error="farm daily cap reached")

    try:
        result: DeliveryResult = await _send_with_retry(
            provider, recipient.phone, message, settings
        )
    except NotificationDeliveryError as exc:
        logger.warning("notification transport failed (farm=%s): %s", farm.id, exc)
        return await settle("FAILED", error=str(exc))
    if result.ok:
        return await settle("SENT", message_id=result.message_id)
    return await settle("FAILED", error=result.error)


# --- the daily digest -------------------------------------------------------


@dataclass(frozen=True)
class DigestSummary:
    farm_id: int
    sent: int
    skipped: int


async def _digest_text_for_recipient(
    db: AsyncSession, farm: Farm, recipient: NotificationRecipient, reference: date
) -> str | None:
    """Duties due today for THIS recipient's worker, scoped like the board.

    None means "do not send at all" — an inactive membership (deactivated
    worker, tombstoned account) gets NO digest SMS, not even a "no duties"
    one: spending daily-cap budget on a deactivated worker is pure cost
    (2026-09-29 audit)."""
    from ...models import FarmMembership, User
    from ...services.tasks import task_scope  # local import: avoids cycle at module load

    row = (
        await db.execute(
            select(FarmMembership, User)
            .join(User, FarmMembership.user_id == User.id)
            .where(
                FarmMembership.id == recipient.membership_id,
                FarmMembership.farm_id == farm.id,
                FarmMembership.is_active.is_(True),
                User.deleted_at.is_(None),
            )
        )
    ).first()
    if row is None:
        return None
    _membership, user = row
    scoped = (await task_scope(db, farm, user)).where(
        Task.status == TaskStatus.PENDING.value,
        Task.due_date <= reference,
    )
    titles = list(
        (
            await db.execute(
                scoped.with_only_columns(Task.title).order_by(Task.due_date, Task.id).limit(10)
            )
        ).scalars()
    )
    overdue = list(
        (
            await db.execute(
                scoped.with_only_columns(Task.title)
                .where(Task.due_date < reference)
                .order_by(Task.due_date, Task.id)
                .limit(10)
            )
        ).scalars()
    )
    if not titles:
        return f"Herdly {reference.isoformat()}: no duties today. Good work!"
    parts = [f"Herdly {reference.isoformat()}: {len(titles)} duties today"]
    if overdue:
        parts.append(f"{len(overdue)} overdue (incl. today's list)")
    for title in titles[:5]:
        parts.append(f"- {title[:60]}")
    if len(titles) > 5:
        parts.append(f"... and {len(titles) - 5} more")
    return "\n".join(parts)


async def run_digest_for_farm(
    db: AsyncSession,
    settings: Settings,
    provider: NotificationProvider,
    farm: Farm,
    *,
    now_local: datetime | None = None,
) -> DigestSummary:
    """Send the morning digest to every opted-in recipient of one farm."""
    now = now_local or datetime.now(ZoneInfo(farm.timezone))
    reference = now.date()
    payload_day = reference.isoformat()
    recipients = list(
        (
            await db.execute(
                select(NotificationRecipient).where(
                    NotificationRecipient.farm_id == farm.id,
                    NotificationRecipient.daily_digest.is_(True),
                )
            )
        ).scalars()
    )
    sent = skipped = 0
    for recipient in recipients:
        message = await _digest_text_for_recipient(db, farm, recipient, reference)
        if message is None:
            # Inactive membership: no SMS at all (2026-09-29 audit).
            skipped += 1
            continue
        outcome = await send_notification(
            db,
            settings,
            provider,
            farm=farm,
            recipient=recipient,
            alert_class="DAILY_DIGEST",
            message=message,
            payload=f"digest:{payload_day}",
            now_local=now_local,
        )
        if outcome.status == "SENT" and outcome.fresh:
            sent += 1
        else:
            skipped += 1
    await db.commit()
    return DigestSummary(farm_id=farm.id, sent=sent, skipped=skipped)


async def farms_ready_for_digest(
    db: AsyncSession, settings: Settings, now_utc: datetime
) -> list[Farm]:
    """Farms whose local wall clock is at or past the digest time today.

    The loop ticks roughly every minute and can drift past a farm's digest
    minute, so readiness is a catch-up window — same-day local time >= the
    configured digest time — not exact-minute equality (a farm whose minute
    was jumped over would otherwise get no digest that day). The once-per-day
    guard is the log's day dedupe: a farm with a settled DAILY_DIGEST row for
    its local today is done. Quiet-hours placeholders (SKIPPED_QUIET) do not
    settle the day — the digest fires once the window opens.

    Readiness and the settled-check run in SQL BEFORE the batch limit, so a
    farm that is already done never consumes a batch slot: with more farms
    than ``notifications_loop_batch_size``, the first page can no longer
    permanently starve every farm after it (2026-09-29 audit). The limit
    bounds ready digest FAN-OUT per tick, not the scan.
    """
    from sqlalchemy import and_, literal
    from sqlalchemy.types import Date as SqlDate
    from sqlalchemy.types import DateTime

    # now_utc AT TIME ZONE farm.timezone → the farm's local wall clock (a
    # naive timestamp), evaluated per row.
    local_now = (
        literal(now_utc)
        .cast(DateTime(timezone=True))
        .op("AT TIME ZONE", return_type=DateTime())(Farm.timezone)
    )
    local_date = func.cast(local_now, SqlDate)
    minutes_of_day = func.extract("hour", local_now) * 60 + func.extract("minute", local_now)
    digest_minute_of_day = (
        settings.notifications_digest_hour * 60 + settings.notifications_digest_minute
    )
    settled_today = (
        select(NotificationLog.id)
        .where(
            NotificationLog.farm_id == Farm.id,
            NotificationLog.alert_class == "DAILY_DIGEST",
            NotificationLog.local_date == local_date,
            NotificationLog.status != "SKIPPED_QUIET",
        )
        .exists()
    )
    return list(
        (
            await db.execute(
                select(Farm)
                .where(
                    and_(
                        minutes_of_day >= digest_minute_of_day,
                        ~settled_today,
                    )
                )
                .order_by(Farm.id)
                .limit(settings.notifications_loop_batch_size)
            )
        ).scalars()
    )


# --- same-day alerts ---------------------------------------------------------


async def notify_alert_class(
    db: AsyncSession,
    settings: Settings,
    provider: NotificationProvider,
    *,
    farm: Farm,
    alert_class: str,
    message: str,
    payload: str,
    now_local: datetime | None = None,
) -> int:
    """Fan an alert out to every recipient opted into its class."""
    column = {
        "SCREENING_FLAG": NotificationRecipient.screening_flags,
        "KIDDING_WATCH": NotificationRecipient.kidding_watch,
        "OVERDUE_CRITICAL": NotificationRecipient.overdue_critical,
        "FEED_REORDER": NotificationRecipient.feed_reorder,
        "MOVEMENT_RESTRICTION": NotificationRecipient.movement_restriction,
    }.get(alert_class)
    if column is None:
        raise ValueError(f"Not an alert class: {alert_class!r}")
    recipients = list(
        (
            await db.execute(
                select(NotificationRecipient).where(
                    NotificationRecipient.farm_id == farm.id, column.is_(True)
                )
            )
        ).scalars()
    )
    sent = 0
    for recipient in recipients:
        outcome = await send_notification(
            db,
            settings,
            provider,
            farm=farm,
            recipient=recipient,
            alert_class=alert_class,
            message=message,
            payload=payload,
            now_local=now_local,
        )
        if outcome.status == "SENT" and outcome.fresh:
            sent += 1
    await db.commit()
    return sent


async def kidding_watch_daily(
    db: AsyncSession,
    settings: Settings,
    provider: NotificationProvider,
    farm: Farm,
    *,
    now_local: datetime | None = None,
) -> int:
    """Once per farm-local day: how many does sit in the late-pregnancy /
    pre-kidding pens right now (the payload is the local date, so the
    day-dedupe collapses the whole day into one alert)."""
    from ...models import Animal, AnimalStatus, Bucket

    reference = today(farm.timezone)
    count = int(
        (
            await db.execute(
                select(func.count())
                .select_from(Animal)
                .where(
                    Animal.farm_id == farm.id,
                    Animal.status == AnimalStatus.ACTIVE.value,
                    Animal.current_bucket.in_([Bucket.PREGNANCY_LATE.value, Bucket.DELIVERY.value]),
                )
            )
        ).scalar_one()
    )
    if count == 0:
        return 0
    return await notify_alert_class(
        db,
        settings,
        provider,
        farm=farm,
        alert_class="KIDDING_WATCH",
        message=(
            f"Herdly: {count} does on kidding watch today. "
            "Check the pre-kidding pens and birthing kits."
        ),
        payload=f"kidding-watch:{reference.isoformat()}",
        now_local=now_local,
    )


async def feed_reorder_daily(
    db: AsyncSession,
    settings: Settings,
    provider: NotificationProvider,
    farm: Farm,
    *,
    now_local: datetime | None = None,
) -> int:
    """Once per farm-local day: ingredients under their reorder level."""
    from ...models import FeedInventory

    reference = today(farm.timezone)
    rows = list(
        (
            await db.execute(
                select(
                    FeedInventory.ingredient,
                    FeedInventory.qty_on_hand,
                    FeedInventory.reorder_level,
                )
                .where(
                    FeedInventory.farm_id == farm.id,
                    FeedInventory.reorder_level.is_not(None),
                    FeedInventory.qty_on_hand < FeedInventory.reorder_level,
                )
                .order_by(FeedInventory.ingredient)
                .limit(10)
            )
        ).all()
    )
    if not rows:
        return 0
    names = ", ".join(str(row[0]) for row in rows[:5])
    message = (
        f"Herdly: {len(rows)} feed items below reorder level ({names}"
        f"{'…' if len(rows) > 5 else ''}). Order feed."
    )
    payload = f"feed-reorder:{reference.isoformat()}:{len(rows)}"
    return await notify_alert_class(
        db,
        settings,
        provider,
        farm=farm,
        alert_class="FEED_REORDER",
        message=message,
        payload=payload,
        now_local=now_local,
    )


async def overdue_critical_sweep(
    db: AsyncSession,
    settings: Settings,
    provider: NotificationProvider,
    farm: Farm,
    *,
    now_local: datetime | None = None,
) -> int:
    """Alert-worthy overdue pile: duties overdue by 3+ days, one SMS per day.

    The payload (oldest overdue due-date + count) changes as the pile grows,
    so a worsening board re-alerts the next day while the same shape does not.
    """
    reference = today(farm.timezone)
    rows = list(
        (
            await db.execute(
                select(Task.due_date)
                .where(
                    Task.farm_id == farm.id,
                    Task.status == TaskStatus.PENDING.value,
                    Task.due_date < reference,
                )
                .order_by(Task.due_date)
                .limit(50)
            )
        ).scalars()
    )
    critical = [due for due in rows if (reference - due).days >= 3]
    if not critical:
        return 0
    message = (
        f"Herdly: {len(critical)} duties are 3+ days overdue "
        f"(oldest {critical[0].isoformat()}). Please clear them."
    )
    return await notify_alert_class(
        db,
        settings,
        provider,
        farm=farm,
        alert_class="OVERDUE_CRITICAL",
        message=message,
        payload=f"overdue:{len(critical)}:{critical[0].isoformat()}",
        now_local=now_local,
    )
