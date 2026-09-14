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
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from ..utils import today, utcnow
from .enums import TransactionCategory, TransactionType, sql_in_values

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
            f"type IN ({sql_in_values(TransactionType)})",
            name="ck_transactions_type",
        ),
        CheckConstraint(
            f"category IN ({sql_in_values(TransactionCategory)})",
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
    created_at: Mapped[datetime] = mapped_column(
        default=utcnow, server_default=text("timezone('UTC', now())")
    )

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


# The ledger feed pages with ORDER BY date DESC, id DESC after a farm_id
# equality filter (optionally a month range). transactions only carried
# single-column indexes, so every page sorted the farm's whole ledger
# history (all years) instead of walking a tenant-bounded ordered index.
Index(
    "ix_transactions_farm_date_id",
    Transaction.farm_id,
    Transaction.date.desc(),
    Transaction.id.desc(),
)


# ---------------------------------------------------------------------------
# Insurance register
# ---------------------------------------------------------------------------

# Server-owned policy lifecycle vocabulary (lowercase on the wire). The
# register is append-style like the ledger: a row moves forward through
# renewal and ends as lapsed/claimed, but is never edited into a different
# fact or deleted — corrections happen by renewing, not rewriting.
INSURANCE_POLICY_STATUSES: tuple[str, ...] = ("active", "renewed", "lapsed", "claimed")
INSURANCE_STATUS_ACTIVE = "active"
INSURANCE_STATUS_CLAIMED = "claimed"
INSURANCE_STATUS_LAPSED = "lapsed"


class InsurancePolicy(Base):
    """One livestock insurance policy held by the farm.

    ``animal_id`` is nullable: herd-level policies cover the flock, per-animal
    policies link the covered goat (the tenant composite FK keeps the link
    inside the farm, exactly like transactions.related_animal_id). Premiums
    feed the per-animal lifetime P&L; renewals spawn INSURANCE duties.
    """

    __tablename__ = "insurance_policies"
    __table_args__ = (
        # Candidate key for the tenant composite animal FK below.
        UniqueConstraint("farm_id", "id", name="uq_insurance_policies_farm_id_id"),
        UniqueConstraint(
            "farm_id", "policy_number", name="uq_insurance_policies_farm_policy_number"
        ),
        CheckConstraint(
            "sum_insured > 0 AND sum_insured <= 1000000000",
            name="ck_insurance_policies_sum_insured",
        ),
        CheckConstraint(
            "premium >= 0 AND premium <= 1000000000",
            name="ck_insurance_policies_premium",
        ),
        CheckConstraint(
            "renewal_date >= start_date",
            name="ck_insurance_policies_renewal_after_start",
        ),
        CheckConstraint(
            f"status IN ({sql_in_values(INSURANCE_POLICY_STATUSES)})",
            name="ck_insurance_policies_status",
        ),
        CheckConstraint(
            "btrim(policy_number) <> ''",
            name="ck_insurance_policies_policy_number_nonblank",
        ),
        CheckConstraint(
            "btrim(insurer) <> ''",
            name="ck_insurance_policies_insurer_nonblank",
        ),
        ForeignKeyConstraint(
            ["farm_id", "animal_id"],
            ["animals.farm_id", "animals.id"],
            name="fk_insurance_policies_farm_animal",
        ),
        Index("ix_insurance_policies_animal_id", "animal_id"),
        # The register pages and the dashboard expiry window both filter by
        # farm plus status and then walk renewal_date.
        Index(
            "ix_insurance_policies_farm_status_renewal",
            "farm_id",
            "status",
            "renewal_date",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    animal_id: Mapped[int | None] = mapped_column(ForeignKey("animals.id"))
    policy_number: Mapped[str] = mapped_column(String(60))
    insurer: Mapped[str] = mapped_column(String(120))
    sum_insured: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    premium: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    start_date: Mapped[date] = mapped_column(default=today)
    renewal_date: Mapped[date]
    status: Mapped[str] = mapped_column(String(12), default=INSURANCE_STATUS_ACTIVE)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        default=utcnow, server_default=text("timezone('UTC', now())")
    )

    animal: Mapped[Animal | None] = relationship(foreign_keys=[animal_id])


class InsurancePremium(Base):
    """One premium payment against a policy — the register's audit ledger.

    ``InsurancePolicy.premium`` is only the CURRENT period's price: renewal
    overwrites it (a policy is one row, not a history), so the sum actually
    paid lives here. A row is booked at registration (covering start → first
    renewal) and at every renewal (covering the previous horizon → the new
    one); the per-animal lifetime P&L sums this table, never the column.
    """

    __tablename__ = "insurance_premiums"
    __table_args__ = (
        # Candidate key for the tenant composite policy FK below.
        UniqueConstraint("farm_id", "id", name="uq_insurance_premiums_farm_id_id"),
        CheckConstraint(
            "premium >= 0 AND premium <= 1000000000",
            name="ck_insurance_premiums_premium",
        ),
        CheckConstraint(
            "covered_until > covered_from",
            name="ck_insurance_premiums_covered_period",
        ),
        ForeignKeyConstraint(
            ["farm_id", "policy_id"],
            ["insurance_policies.farm_id", "insurance_policies.id"],
            name="fk_insurance_premiums_farm_policy",
        ),
        Index("ix_insurance_premiums_farm_policy", "farm_id", "policy_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    policy_id: Mapped[int] = mapped_column(ForeignKey("insurance_policies.id"))
    premium: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    covered_from: Mapped[date]
    covered_until: Mapped[date]
    recorded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        default=utcnow, server_default=text("timezone('UTC', now())")
    )
