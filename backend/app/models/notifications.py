"""Notification recipients and the append-only delivery log.

One row per membership that opted into notifications: the phone number, a
per-alert-class opt-in bitmap (simple booleans — six classes today), and a
verification flag (delivery attempts still proceed; the flag records that the
number was confirmed live by the owner, for audit clarity).

``notification_log`` is the audit trail AND the dedupe ledger: one delivery
attempt per (farm, recipient, class, payload_hash, local day), claimed via
``INSERT ... ON CONFLICT DO NOTHING`` before any send so concurrent sessions
can never double-send a paid SMS. ``SENDING`` is the
claim state: a crashed sender leaves the slot settled for the day — the safe
side for paid sends. Re-alerting the same fact inside the day is a no-op by
design.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ..utils import utcnow
from .enums import sql_in_values

# The server-owned vocabularies, single-sourced here so the service layer and the database CHECK
# constraints can never drift.
ALERT_CLASSES = (
    "DAILY_DIGEST",
    "SCREENING_FLAG",
    "KIDDING_WATCH",
    "OVERDUE_CRITICAL",
    "FEED_REORDER",
    "MOVEMENT_RESTRICTION",
)
NOTIFICATION_LOG_STATUSES = ("SENDING", "SENT", "FAILED", "SKIPPED_QUIET", "SKIPPED_CAP")


class NotificationRecipient(Base):
    __tablename__ = "notification_recipients"
    __table_args__ = (
        UniqueConstraint("farm_id", "membership_id", name="uq_notification_recipients_membership"),
        # Tenant candidate key: the log's composite (farm_id, recipient_id) FK
        # targets it, so a log row can never point across farms — the same
        # DB-level tenant guard every sibling table carries.
        UniqueConstraint("farm_id", "id", name="uq_notification_recipients_farm_id_id"),
        CheckConstraint("btrim(phone) <> ''", name="ck_notification_recipients_phone_nonblank"),
        Index("ix_notification_recipients_farm_enabled", "farm_id", "daily_digest"),
        ForeignKeyConstraint(
            ["farm_id", "membership_id"],
            ["farm_memberships.farm_id", "farm_memberships.id"],
            name="fk_notification_recipients_farm_id_membership_id",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id", ondelete="CASCADE"))
    membership_id: Mapped[int] = mapped_column()
    phone: Mapped[str] = mapped_column(String(20))
    # Alert-class opt-ins. The daily digest is the headline; the rest are same-day owner/operator
    # alerts. server_default "false" on every column keeps direct-SQL INSERTs symmetric with the
    # ORM's Python defaults — movement_restriction led the way; the migration backfilled the rest.
    daily_digest: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    screening_flags: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    kidding_watch: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    overdue_critical: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    feed_reorder: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    movement_restriction: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    verified: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    # UTC server defaults standardized by the housekeeping wave.
    created_at: Mapped[datetime] = mapped_column(
        default=utcnow, server_default=text("timezone('UTC', now())")
    )
    # ORM/SQLAlchemy-layer refresh only (onupdate); no DB trigger and no raw-SQL writers in
    # production.
    updated_at: Mapped[datetime] = mapped_column(
        default=utcnow,
        onupdate=utcnow,
        # text() so alembic autogenerate compares a SQL expression, not a
        # quoted literal (the string form breaks `alembic check`).
        server_default=text("timezone('UTC', now())"),
        server_onupdate=text("timezone('UTC', now())"),
    )


class NotificationLog(Base):
    __tablename__ = "notification_log"
    __table_args__ = (
        # The dedupe boundary: one delivery attempt per class+payload+day.
        UniqueConstraint(
            "farm_id",
            "recipient_id",
            "alert_class",
            "payload_hash",
            "local_date",
            name="uq_notification_log_day_dedupe",
        ),
        UniqueConstraint("outbox_id", "recipient_id", name="uq_notification_log_outbox_recipient"),
        ForeignKeyConstraint(
            ["farm_id", "outbox_id"],
            ["notification_outbox.farm_id", "notification_outbox.id"],
            ondelete="CASCADE",
            name="fk_notification_log_farm_id_outbox_id",
        ),
        # Tenant guard: the recipient must belong to THIS farm at the database level, not just by
        # application care.
        ForeignKeyConstraint(
            ["farm_id", "recipient_id"],
            ["notification_recipients.farm_id", "notification_recipients.id"],
            ondelete="CASCADE",
            name="fk_notification_log_farm_id_recipient_id",
        ),
        CheckConstraint(
            f"alert_class IN ({sql_in_values(ALERT_CLASSES)})",
            name="ck_notification_log_alert_class",
        ),
        CheckConstraint(
            f"status IN ({sql_in_values(NOTIFICATION_LOG_STATUSES)})",
            name="ck_notification_log_status",
        ),
        Index("ix_notification_log_farm_created", "farm_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id", ondelete="CASCADE"))
    recipient_id: Mapped[int]
    outbox_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    alert_class: Mapped[str] = mapped_column(String(30))
    payload_hash: Mapped[str] = mapped_column(String(64))
    local_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16))
    provider_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # UTC server default standardized by the housekeeping wave.
    created_at: Mapped[datetime] = mapped_column(
        default=utcnow, server_default=text("timezone('UTC', now())")
    )


class NotificationOutbox(Base):
    """One-shot domain alerts committed alongside the clinical mutation.

    Delivery outcomes stay in NotificationLog; a completed event means its
    eligible recipients have settled outcomes, including ambiguous failures.
    """

    __tablename__ = "notification_outbox"
    __table_args__ = (
        UniqueConstraint(
            "farm_id", "alert_class", "event_key", name="uq_notification_outbox_event"
        ),
        UniqueConstraint("farm_id", "id", name="uq_notification_outbox_farm_id_id"),
        CheckConstraint(
            "alert_class IN ('SCREENING_FLAG', 'MOVEMENT_RESTRICTION')",
            name="ck_notification_outbox_alert_class",
        ),
        CheckConstraint(
            "btrim(event_key) <> '' AND btrim(message) <> ''", name="ck_notification_outbox_content"
        ),
        Index(
            "ix_notification_outbox_due",
            "due_at",
            "id",
            postgresql_where=text("completed_at IS NULL"),
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id", ondelete="CASCADE"))
    alert_class: Mapped[str] = mapped_column(String(30))
    event_key: Mapped[str] = mapped_column(String(255))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        default=utcnow, server_default=text("timezone('UTC', now())")
    )
    due_at: Mapped[datetime] = mapped_column(
        default=utcnow, server_default=text("timezone('UTC', now())")
    )
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)


__all__ = [
    "ALERT_CLASSES",
    "NOTIFICATION_LOG_STATUSES",
    "NotificationLog",
    "NotificationOutbox",
    "NotificationRecipient",
]
