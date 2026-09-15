"""Purchases."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from ..utils import today, utcnow

if TYPE_CHECKING:
    from .animals import Animal


class PurchaseBatch(Base):
    __tablename__ = "purchase_batches"
    __table_args__ = (
        UniqueConstraint("farm_id", "id", name="uq_purchase_batches_farm_id_id"),
        CheckConstraint(
            "count BETWEEN 1 AND 1000",
            name="ck_purchase_batches_count",
        ),
        CheckConstraint(
            "avg_age_months IS NULL OR "
            "(avg_age_months BETWEEN 0 AND 240 AND "
            "avg_age_months::text NOT IN ('NaN', 'Infinity', '-Infinity'))",
            name="ck_purchase_batches_avg_age",
        ),
        CheckConstraint(
            "avg_weight_kg IS NULL OR "
            "(avg_weight_kg BETWEEN 0 AND 1000 AND "
            "avg_weight_kg::text NOT IN ('NaN', 'Infinity', '-Infinity'))",
            name="ck_purchase_batches_avg_weight",
        ),
        CheckConstraint(
            "total_price IS NULL OR total_price BETWEEN 0 AND 1000000000",
            name="ck_purchase_batches_total_price",
        ),
        CheckConstraint(
            "sex IS NULL OR sex IN ('M', 'F')",
            name="ck_purchase_batches_sex",
        ),
        CheckConstraint(
            "transport_hours IS NULL OR transport_hours BETWEEN 0 AND 240",
            name="ck_purchase_batches_transport_hours",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    date: Mapped[date] = mapped_column(default=today)
    supplier: Mapped[str | None] = mapped_column(String(120))
    # Market/shed the animals were bought at and how long they were in transit
    # before arriving — the two provenance facts that frame a batch's arrival
    # stress and disease-exposure risk during quarantine.
    origin_market: Mapped[str | None] = mapped_column(String(120))
    transport_hours: Mapped[int | None]
    # Free-text prior vaccinations/deworming reported by the seller at source;
    # deliberately narrative (no structured claims to verify against).
    seller_health_history: Mapped[str | None] = mapped_column(Text)
    count: Mapped[int]
    # Nullable only for legacy batches that created no animals (there is no
    # historical evidence from which to infer the submitted sex).
    sex: Mapped[str | None] = mapped_column(String(1))
    avg_age_months: Mapped[float | None]
    avg_weight_kg: Mapped[float | None]
    total_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        default=utcnow, server_default=text("timezone('UTC', now())")
    )
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    animals: Mapped[list[Animal]] = relationship(
        back_populates="purchase_batch", foreign_keys="Animal.purchase_batch_id"
    )
