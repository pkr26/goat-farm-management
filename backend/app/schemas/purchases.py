"""Pydantic schemas for the purchases module."""

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..models import MAX_AGE_MONTHS, MAX_BATCH_COUNT  # single source (AUDIT 4-M4)
from .animals import AnimalOut
from .common import NonNegativeMoneyFloat, NonNegativeWeightKgFloat, PastOrTodayDate
from .tasks import TaskOut


class PurchaseBatchIn(BaseModel):
    date: PastOrTodayDate
    supplier: str | None = Field(default=None, max_length=120)
    count: int = Field(ge=1, le=MAX_BATCH_COUNT)
    sex: Literal["M", "F"] = "F"  # stub-animal sex (a bought buck is not a doe)
    avg_age_months: float | None = Field(default=None, ge=0, le=MAX_AGE_MONTHS)
    avg_weight_kg: NonNegativeWeightKgFloat | None = None
    total_price: NonNegativeMoneyFloat | None = None
    notes: str | None = None
    create_animals: bool = True

    @field_validator("date")
    @classmethod
    def _not_ancient(cls, value: dt.date) -> dt.date:
        if value.year < 2000:
            raise ValueError("date must be year 2000 or later")
        return value


class PurchaseBatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    date: dt.date
    supplier: str | None
    count: int
    avg_age_months: float | None
    avg_weight_kg: float | None
    total_price: float | None
    notes: str | None
    animals_created: int = 0
    open_tasks: int = 0  # pending quarantine tasks


class PurchaseBatchDetailOut(BaseModel):
    batch: PurchaseBatchOut
    animals: list[AnimalOut]
    tasks: list[TaskOut]
