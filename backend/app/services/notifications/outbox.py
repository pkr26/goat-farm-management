"""Transactional one-shot alerts with bounded, crash-resumable fan-out.

Quiet hours and the daily cap defer pending work. Provider-accepted sends,
FAILED and abandoned SENDING attempts stay settled in the delivery ledger;
they are never blindly retried, since a timeout cannot prove non-delivery.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.config import Settings, get_settings
from ...db import get_sessionmaker
from ...models import Farm, FarmMembership, NotificationRecipient, User
from ...models.notifications import NotificationOutbox
from ...utils import utcnow
from .providers import NotificationProvider
from .service import _in_quiet_hours, send_notification

logger = logging.getLogger(__name__)


async def enqueue_alert(
    db: AsyncSession, farm_id: int, alert_class: str, message: str, event_key: str
) -> int | None:
    """Enqueue in the domain transaction, before its commit; never send here."""
    if not get_settings().notifications_enabled:
        return None
    if alert_class not in ("SCREENING_FLAG", "MOVEMENT_RESTRICTION"):
        raise ValueError("Only one-shot clinical alerts belong in the outbox")
    result = await db.execute(
        insert(NotificationOutbox)
        .values(farm_id=farm_id, alert_class=alert_class, message=message, event_key=event_key)
        .on_conflict_do_nothing(constraint="uq_notification_outbox_event")
        .returning(NotificationOutbox.id)
    )
    event_id = result.scalar_one_or_none()
    if event_id is None:
        event_id = (
            await db.execute(
                select(NotificationOutbox.id).where(
                    NotificationOutbox.farm_id == farm_id,
                    NotificationOutbox.alert_class == alert_class,
                    NotificationOutbox.event_key == event_key,
                )
            )
        ).scalar_one()
    return event_id


def _next_allowed(settings: Settings, now: datetime, *, capped: bool) -> datetime:
    if capped:
        candidate = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        candidate = now
    # Advance by wall-clock hours through the configured quiet interval;
    # zones are attached throughout, so the UTC due instant follows DST.
    for _ in range(25):
        if not _in_quiet_hours(settings, candidate):
            return candidate.astimezone(UTC).replace(tzinfo=None)
        candidate = (candidate + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    return (now + timedelta(days=1)).astimezone(UTC).replace(tzinfo=None)


async def dispatch_outbox_event(
    event_id: int,
    settings: Settings,
    provider: NotificationProvider,
    *,
    now_utc: datetime | None = None,
) -> int:
    """Claim a short durable lease; resume missing recipients after a crash."""
    instant = (now_utc or utcnow()).replace(tzinfo=None)
    async with get_sessionmaker()() as db:
        event = (
            await db.execute(
                update(NotificationOutbox)
                .where(
                    NotificationOutbox.id == event_id,
                    NotificationOutbox.completed_at.is_(None),
                    NotificationOutbox.due_at <= instant,
                )
                .values(due_at=instant + timedelta(minutes=5))
                .returning(NotificationOutbox)
            )
        ).scalar_one_or_none()
        if event is None:
            return 0
        await db.commit()
        farm = await db.get(Farm, event.farm_id)
        if farm is None:
            return 0
        now = instant.replace(tzinfo=UTC).astimezone(ZoneInfo(farm.timezone))
        if _in_quiet_hours(settings, now):
            event.due_at = _next_allowed(settings, now, capped=False)
            await db.commit()
            return 0
        opted_in = (
            NotificationRecipient.screening_flags
            if event.alert_class == "SCREENING_FLAG"
            else NotificationRecipient.movement_restriction
        )
        # Recipient fan-out is also finite. A full page re-enters after the
        # lease, and settled recipient logs are filtered before pagination.
        from ...models import NotificationLog

        settled = (
            select(NotificationLog.id)
            .where(
                NotificationLog.outbox_id == event.id,
                NotificationLog.recipient_id == NotificationRecipient.id,
                NotificationLog.status.in_(["SENT", "FAILED", "SENDING"]),
            )
            .exists()
        )
        recipients = list(
            (
                await db.execute(
                    select(NotificationRecipient)
                    .join(FarmMembership, FarmMembership.id == NotificationRecipient.membership_id)
                    .join(User, User.id == FarmMembership.user_id)
                    .where(
                        NotificationRecipient.farm_id == farm.id,
                        FarmMembership.farm_id == farm.id,
                        FarmMembership.is_active.is_(True),
                        User.deleted_at.is_(None),
                        opted_in.is_(True),
                        ~settled,
                    )
                    .order_by(NotificationRecipient.id)
                    .limit(settings.notifications_loop_batch_size + 1)
                )
            ).scalars()
        )
        sent = 0
        capped = False
        for recipient in recipients[: settings.notifications_loop_batch_size]:
            outcome = await send_notification(
                db,
                settings,
                provider,
                farm=farm,
                recipient=recipient,
                alert_class=event.alert_class,
                message=event.message,
                payload=event.event_key,
                now_local=now,
                outbox_id=event.id,
            )
            sent += int(outcome.status == "SENT" and outcome.fresh)
            capped |= outcome.status == "SKIPPED_CAP"
        if capped:
            event.due_at = _next_allowed(settings, now, capped=True)
        elif len(recipients) <= settings.notifications_loop_batch_size:
            event.completed_at = instant
        else:
            event.due_at = instant  # yield to the next bounded dispatcher tick
        await db.commit()
        return sent


async def dispatch_pending_alerts(settings: Settings, provider: NotificationProvider) -> int:
    now = utcnow()
    async with get_sessionmaker()() as db:
        ids = list(
            (
                await db.execute(
                    select(NotificationOutbox.id)
                    .where(
                        NotificationOutbox.completed_at.is_(None),
                        NotificationOutbox.due_at <= now,
                    )
                    .order_by(NotificationOutbox.due_at, NotificationOutbox.id)
                    .limit(settings.notifications_loop_batch_size)
                )
            ).scalars()
        )
    sent = 0
    for event_id in ids:
        try:
            sent += await dispatch_outbox_event(event_id, settings, provider, now_utc=now)
        except Exception:
            # The durable lease expires; later events still get their turn.
            logger.exception("notification outbox event %s failed", event_id)
    return sent
