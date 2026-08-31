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
    avg_fat_pct: float | None


class MilkAnimalSummaryOut(BaseModel):
    animal_id: int
    animal_tag: str
    total_litres: float
    avg_daily_litres: float
    days_recorded: int
    avg_fat_pct: float | None


class MilkSummaryOut(BaseModel):
    """Herd-level window summary: daily totals plus per-buffalo averages."""

    days: int
    total_litres: float
    avg_daily_litres: float
    avg_fat_pct: float | None
    daily: list[MilkDayTotalOut]
    animals: list[MilkAnimalSummaryOut]
