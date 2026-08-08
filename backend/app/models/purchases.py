"""Purchases."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from ..utils import today

if TYPE_CHECKING:
    from .animals import Animal


class PurchaseBatch(Base):
    __tablename__ = "purchase_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    date: Mapped[date] = mapped_column(default=today)
    supplier: Mapped[str | None] = mapped_column(String(120))
    count: Mapped[int]
    avg_age_months: Mapped[float | None]
    avg_weight_kg: Mapped[float | None]
    total_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    notes: Mapped[str | None] = mapped_column(Text)

    animals: Mapped[list[Animal]] = relationship(back_populates="purchase_batch")
