"""Health."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, Date, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from ..utils import today

if TYPE_CHECKING:
    from .animals import Animal


class HealthEvent(Base):
    __tablename__ = "health_events"
    __table_args__ = (
        Index("ix_health_events_date", "date"),
        CheckConstraint(
            "cost IS NULL OR cost >= 0",
            name="ck_health_events_cost_nonneg",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    animal_id: Mapped[int | None] = mapped_column(
        ForeignKey("animals.id"), index=True
    )  # null = batch event
    purchase_batch_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_batches.id"))
    date: Mapped[dt.date] = mapped_column(default=today)
    type: Mapped[str] = mapped_column(String(12))  # HealthEventType enum
    product_name: Mapped[str | None] = mapped_column(String(120))
    disease_target: Mapped[str | None] = mapped_column(String(120))
    dose: Mapped[str | None] = mapped_column(String(60))
    route: Mapped[str | None] = mapped_column(String(20))  # SC / Oral / IM
    vet_name: Mapped[str | None] = mapped_column(String(120))
    cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    next_due_date: Mapped[dt.date | None]
    # Exact schedule/template linkage prevents a free-text product name from
    # silently satisfying an unrelated scheduled task.
    schedule_template_name: Mapped[str | None] = mapped_column(String(120))
    next_due_authority: Mapped[str | None] = mapped_column(String(120))
    product_lot: Mapped[str | None] = mapped_column(String(120))
    product_manufactured_on: Mapped[dt.date | None] = mapped_column(Date)
    product_expires_on: Mapped[dt.date | None] = mapped_column(Date)
    vaccine_valid_until: Mapped[dt.date | None] = mapped_column(Date)
    certificate_number: Mapped[str | None] = mapped_column(String(120))
    official_tag_number: Mapped[str | None] = mapped_column(String(80))
    administered_by: Mapped[str | None] = mapped_column(String(120))
    withdrawal_until: Mapped[dt.date | None] = mapped_column(Date)
    suspected_scheduled_disease: Mapped[bool] = mapped_column(Boolean, default=False)
    authority_notified_at: Mapped[dt.date | None] = mapped_column(Date)
    isolation_started_at: Mapped[dt.date | None] = mapped_column(Date)
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
