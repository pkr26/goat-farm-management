"""Milk production records (dairy farms).

One row per animal, business date and milking shift. Dairy operations measure
per-buffalo yield at every milking; the aggregate of a day's rows is the
herd's production, while sales are booked as MILK-category finance
transactions (optionally carrying litres + ₹/litre, or fat-based procurement
provenance).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from ..utils import today, utcnow

if TYPE_CHECKING:
    from .animals import Animal


class MilkRecord(Base):
    __tablename__ = "milk_records"
    __table_args__ = (
        # One row per (animal, date, shift): re-submitting the same milking
        # replaces the reading instead of double-counting it.
        UniqueConstraint("animal_id", "date", "shift", name="uq_milk_records_animal_day_shift"),
        UniqueConstraint("farm_id", "id", name="uq_milk_records_farm_id_id"),
        CheckConstraint(
            "shift IN ('MORNING', 'AFTERNOON', 'NIGHT')",
            name="ck_milk_records_shift",
        ),
        CheckConstraint(
            "litres > 0 AND litres <= 100 AND litres::text NOT IN ('NaN', 'Infinity', '-Infinity')",
            name="ck_milk_records_litres",
        ),
        CheckConstraint(
            "fat_pct IS NULL OR (fat_pct BETWEEN 3 AND 12 AND "
            "fat_pct::text NOT IN ('NaN', 'Infinity', '-Infinity'))",
            name="ck_milk_records_fat_pct",
        ),
        ForeignKeyConstraint(
            ["farm_id", "animal_id"],
            ["animals.farm_id", "animals.id"],
            name="fk_milk_records_farm_animal",
        ),
        Index("ix_milk_records_farm_date_id", "farm_id", "date", "id"),
        Index(
            "ix_milk_records_farm_animal_date_id",
            "farm_id",
            "animal_id",
            "date",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    animal_id: Mapped[int] = mapped_column(ForeignKey("animals.id"), index=True)
    date: Mapped[date] = mapped_column(Date, default=today)
    shift: Mapped[str] = mapped_column(String(10))  # FeedingShift enum
    litres: Mapped[float] = mapped_column(Numeric(10, 3, asdecimal=False))
    # Murrah milk runs 6.0-7.5% fat; the bound just rejects typos/units errors.
    fat_pct: Mapped[float | None] = mapped_column(Numeric(4, 2, asdecimal=False))
    notes: Mapped[str | None] = mapped_column(String(255))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    # Correction audit. Finance corrects by voiding and replacing a row; milk
    # cannot — the (animal, date, shift) unique constraint would let a
    # replacement double-count a milking — so the one row is edited in place
    # and the FIRST submitted reading is frozen here. Later corrections keep
    # the stored originals (only the current columns move) so a chain of
    # corrections still shows what the parlour originally booked.
    original_litres: Mapped[float | None] = mapped_column(Numeric(10, 3, asdecimal=False))
    original_fat_pct: Mapped[float | None] = mapped_column(Numeric(4, 2, asdecimal=False))
    original_notes: Mapped[str | None] = mapped_column(Text)
    original_recorded_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", name="fk_milk_records_original_recorded_by")
    )
    corrected_at: Mapped[datetime | None]
    correction_reason: Mapped[str | None] = mapped_column(Text)

    animal: Mapped[Animal] = relationship(foreign_keys=[animal_id])
