"""Health."""

from __future__ import annotations

import datetime as dt
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from ..utils import today

if TYPE_CHECKING:
    from .animals import Animal


class HealthEvent(Base):
    __tablename__ = "health_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    animal_id: Mapped[int | None] = mapped_column(
        ForeignKey("animals.id"), index=True
    )  # null = batch event
    purchase_batch_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_batches.id"))
    date: Mapped[date] = mapped_column(default=today)
    type: Mapped[str] = mapped_column(String(12))  # HealthEventType enum
    product_name: Mapped[str | None] = mapped_column(String(120))
    disease_target: Mapped[str | None] = mapped_column(String(120))
    dose: Mapped[str | None] = mapped_column(String(60))
    route: Mapped[str | None] = mapped_column(String(20))  # SC / Oral / IM
    vet_name: Mapped[str | None] = mapped_column(String(120))
    cost: Mapped[float | None]
    next_due_date: Mapped[dt.date | None]
    notes: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    animal: Mapped[Animal | None] = relationship()


class VaccineTemplate(Base):
    """Seeded reference data: vaccination schedule templates."""

    __tablename__ = "vaccine_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    first_dose_age_months: Mapped[float | None]  # null = pregnancy-linked
    booster_weeks: Mapped[float | None]
    repeat_months: Mapped[float | None]
    timing_note: Mapped[str | None] = mapped_column(String(255))
