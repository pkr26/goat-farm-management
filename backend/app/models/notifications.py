"""Notification recipients and the append-only delivery log (ITEM 4).

One row per membership that opted into notifications: the phone number, a
per-alert-class opt-in bitmap (simple booleans — five classes today), and a
verification flag (delivery attempts still proceed; the flag records that the
number was confirmed live by the owner, for audit clarity).

``notification_log`` is the audit trail AND the dedupe ledger: one successful
or failed delivery attempt per (farm, recipient, class, payload_hash, local
day). Re-alerting the same fact inside the day is a no-op by design.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ..utils import utcnow


class NotificationRecipient(Base):
    __tablename__ = "notification_recipients"
    __table_args__ = (
        UniqueConstraint("farm_id", "membership_id", name="uq_notification_recipients_membership"),
        Index("ix_notification_recipients_farm_enabled", "farm_id", "daily_digest"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id", ondelete="CASCADE"))
    membership_id: Mapped[int] = mapped_column(
        ForeignKey("farm_memberships.id", ondelete="CASCADE")
    )
    phone: Mapped[str] = mapped_column(String(20))
    # Alert-class opt-ins. The daily digest is the headline; the rest are
    # same-day owner/operator alerts.
    daily_digest: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    screening_flags: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    kidding_watch: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    overdue_critical: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    feed_reorder: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


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
        Index("ix_notification_log_farm_created", "farm_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id", ondelete="CASCADE"))
    recipient_id: Mapped[int] = mapped_column(
        ForeignKey("notification_recipients.id", ondelete="CASCADE")
    )
    alert_class: Mapped[str] = mapped_column(String(30))
    payload_hash: Mapped[str] = mapped_column(String(64))
    local_date: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(16))
    provider_message_id: Mapped[str] = mapped_column(String(64), nullable=True)
    error: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


__all__ = ["NotificationLog", "NotificationRecipient"]
