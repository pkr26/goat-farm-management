"""Finance."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
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
        CheckConstraint(
            "type IN ('INCOME', 'EXPENSE')",
            name="ck_transactions_type",
        ),
        CheckConstraint(
            "category IN ('ANIMAL_SALE', 'ANIMAL_PURCHASE', 'FEED', 'MEDICINE', "
            "'VET', 'LABOUR', 'EQUIPMENT', 'MILK', 'MANURE', 'OTHER')",
            name="ck_transactions_category",
        ),
        CheckConstraint(
            "(source_type IS NULL AND source_id IS NULL) OR "
            "(source_type IS NOT NULL AND btrim(source_type) <> '' "
            "AND source_id IS NOT NULL AND source_id > 0)",
            name="ck_transactions_source_pair",
        ),
        CheckConstraint(
            "(feed_inventory_id IS NULL AND feed_quantity_kg IS NULL "
            "AND feed_unit_price_per_kg IS NULL) OR "
            "(source_type = 'FEED_PURCHASE' AND feed_inventory_id IS NOT NULL "
            "AND feed_quantity_kg IS NOT NULL "
            "AND feed_unit_price_per_kg IS NOT NULL "
            "AND feed_quantity_kg BETWEEN 0.001 AND 1000000 "
            "AND feed_unit_price_per_kg BETWEEN 0 AND 1000000000)",
            name="ck_transactions_feed_purchase_provenance",
        ),
        CheckConstraint(
            "correction_of_id IS NULL OR correction_of_id <> id",
            name="ck_transactions_not_self_correction",
        ),
        CheckConstraint(
            "(voided_at IS NULL AND voided_by_id IS NULL AND void_reason IS NULL) OR "
            "(voided_at IS NOT NULL AND void_reason IS NOT NULL AND btrim(void_reason) <> '')",
            name="ck_transactions_void_state",
        ),
        UniqueConstraint("farm_id", "id", name="uq_transactions_farm_id_id"),
        ForeignKeyConstraint(
            ["farm_id", "related_animal_id"],
            ["animals.farm_id", "animals.id"],
            name="fk_transactions_farm_related_animal",
        ),
        ForeignKeyConstraint(
            ["farm_id", "correction_of_id"],
            ["transactions.farm_id", "transactions.id"],
            name="fk_transactions_farm_correction",
        ),
        ForeignKeyConstraint(
            ["farm_id", "feed_inventory_id"],
            ["feed_inventory.farm_id", "feed_inventory.id"],
            name="fk_transactions_farm_feed_inventory",
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
        Index("ix_transactions_feed_inventory_id", "feed_inventory_id"),
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
    feed_inventory_id: Mapped[int | None]
    feed_quantity_kg: Mapped[Decimal | None] = mapped_column(Numeric(15, 3))
    feed_unit_price_per_kg: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    correction_of_id: Mapped[int | None] = mapped_column(
        ForeignKey("transactions.id", ondelete="RESTRICT")
    )
    voided_at: Mapped[datetime | None]
    voided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    void_reason: Mapped[str | None] = mapped_column(String(255))

    related_animal: Mapped[Animal | None] = relationship(foreign_keys=[related_animal_id])
