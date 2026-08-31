"""Pydantic schemas for the milk module."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .common import BoundedId, PastOrTodayDate, PostgresText, StrictInputModel

MilkShift = Literal["MORNING", "AFTERNOON", "NIGHT"]


class MilkRecordIn(StrictInputModel):
    animal_id: BoundedId
    date: PastOrTodayDate
    shift: MilkShift
    # A single buffalo's per-milking yield; >100 L is a units error.
    litres: float = Field(gt=0, le=100)
    # Murrah milk runs 6.0–7.5% fat; 3–12 rejects typos without binding the
    # biology. The DB CHECK mirrors these bounds.
    fat_pct: float | None = Field(default=None, ge=3, le=12)
    notes: PostgresText | None = Field(default=None, max_length=255)
    # Required when this submission replaces an already-recorded milking: the
    # correction is written beside the frozen original reading, so a silent
    # re-keying can never overwrite parlour history without a stated reason.
    correction_reason: PostgresText | None = Field(default=None, max_length=255)


class MilkRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    animal_id: int
    date: date
    shift: str
    litres: float
    fat_pct: float | None
    notes: str | None
    created_at: datetime
    animal_tag: str | None = None
    # Correction audit: the first submitted reading (kept across repeated
    # corrections) and the latest correction's reason/time. All None while
    # the row has never been corrected.
    original_litres: float | None = None
    original_fat_pct: float | None = None
    original_notes: str | None = None
    original_recorded_by_id: int | None = None
    corrected_at: datetime | None = None
    correction_reason: str | None = None


class MilkListOut(BaseModel):
    records: list[MilkRecordOut]
    total: int
    limit: int
    offset: int
    # Aggregate over the full filtered set (not just this page).
    total_litres: float


class MilkDayTotalOut(BaseModel):
    date: date
    litres: float
    recorded_animals: int
    # Litre-weighted mean over the fat-tested milk of that day.
    avg_fat_pct: float | None


class MilkAnimalSummaryOut(BaseModel):
    animal_id: int
    animal_tag: str
    total_litres: float
    avg_daily_litres: float
    days_recorded: int
    # Litre-weighted mean over the animal's fat-tested milk in the window.
    avg_fat_pct: float | None


class MilkSummaryOut(BaseModel):
    """Herd-level window summary: daily totals plus per-buffalo averages."""

    days: int
    total_litres: float
    avg_daily_litres: float
    avg_fat_pct: float | None
    daily: list[MilkDayTotalOut]
    # Per-animal rows are capped (the parlour board renders a bounded list);
    # this is every animal with records in the window so callers can tell a
    # complete herd from a truncated one.
    animals_total: int
    animals: list[MilkAnimalSummaryOut]
