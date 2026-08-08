"""Finance."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Index, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from ..utils import today, utcnow

if TYPE_CHECKING:
    from .animals import Animal


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint(
            "amount >= 0 AND amount <= 1000000000",
            name="ck_transactions_amount_bounded",
        ),
        Index(
            "uq_transactions_active_source",
            "farm_id",
            "source_type",
            "source_id",
            unique=True,
            postgresql_where=text(
                "source_type IS NOT NULL AND source_id IS NOT NULL AND voided_at IS NULL"
            ),
        ),
        Index("ix_transactions_correction_of_id", "correction_of_id"),
        Index("ix_transactions_related_animal_id", "related_animal_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    date: Mapped[date] = mapped_column(default=today, index=True)
    type: Mapped[str] = mapped_column(String(10))  # TransactionType enum
    category: Mapped[str] = mapped_column(String(20))  # TransactionCategory enum
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    related_animal_id: Mapped[int | None] = mapped_column(ForeignKey("animals.id"))
    notes: Mapped[str | None] = mapped_column(String(255))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    # Polymorphic provenance for system-generated ledger rows.  The partial
    # unique index guarantees one active ledger entry per source event while
    # still allowing an audited correction to replace a voided row.
    source_type: Mapped[str | None] = mapped_column(String(40))
    source_id: Mapped[int | None]
    correction_of_id: Mapped[int | None] = mapped_column(
        ForeignKey("transactions.id", ondelete="RESTRICT")
    )
    voided_at: Mapped[datetime | None]
    voided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    void_reason: Mapped[str | None] = mapped_column(String(255))

    related_animal: Mapped[Animal | None] = relationship()
