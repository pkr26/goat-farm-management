"""Append-only security-event source of truth.

Structured application logs remain the delivery projection for operators and
SIEM collectors. These rows are committed with the state changes they
describe, so a rollback cannot leave a false event and a crash immediately
after commit cannot erase the authoritative record.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ..utils import utcnow


class SecurityEvent(Base):
    __tablename__ = "security_events"
    __table_args__ = (
        CheckConstraint("btrim(event) <> ''", name="ck_security_events_event_nonblank"),
        CheckConstraint("btrim(summary) <> ''", name="ck_security_events_summary_nonblank"),
        Index("ix_security_events_occurred_id", "occurred_at", "id"),
        Index("ix_security_events_event_occurred", "event", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    event: Mapped[str] = mapped_column(String(120))
    summary: Mapped[str] = mapped_column(Text)
    targets: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    occurred_at: Mapped[datetime] = mapped_column(
        default=utcnow, server_default=text("timezone('UTC', now())")
    )


__all__ = ["SecurityEvent"]
