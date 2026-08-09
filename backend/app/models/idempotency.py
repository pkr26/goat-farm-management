"""Durable request-idempotency records for retry-prone mutations."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ..utils import utcnow

CREATE_FARM_IDEMPOTENCY_OPERATION = "auth.farms.create"


class IdempotencyRecord(Base):
    """One committed response for one actor/farm/operation/key tuple.

    Claims are inserted before the mutation, but the row is not visible until
    the mutation and response are committed in the same database transaction.
    The raw client key is deliberately never persisted; ``key_digest`` is its
    SHA-256 digest.
    """

    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint(
            "farm_id",
            "actor_id",
            "operation",
            "key_digest",
            name="uq_idempotency_scope_key",
        ),
        CheckConstraint(
            "response_status IS NULL OR (response_status >= 200 AND response_status < 400)",
            name="ck_idempotency_success_status",
        ),
        CheckConstraint(
            "(farm_id IS NULL AND operation = 'auth.farms.create') OR "
            "(farm_id IS NOT NULL AND operation <> 'auth.farms.create')",
            name="ck_idempotency_scope_kind",
        ),
        # PostgreSQL 14 does not support UNIQUE NULLS NOT DISTINCT.  The
        # ordinary constraint above protects tenant-scoped rows, while this
        # partial unique index gives the one actor-scoped operation identical
        # collision semantics when farm_id is NULL.
        Index(
            "uq_idempotency_actor_scope_key",
            "actor_id",
            "operation",
            "key_digest",
            unique=True,
            postgresql_where=text("farm_id IS NULL"),
        ),
        Index("ix_idempotency_records_expires_at", "expires_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    farm_id: Mapped[int | None] = mapped_column(
        ForeignKey("farms.id", ondelete="CASCADE"), index=True
    )
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    operation: Mapped[str] = mapped_column(String(120))
    key_digest: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    response_status: Mapped[int | None]
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    completed_at: Mapped[datetime | None]
    expires_at: Mapped[datetime]
