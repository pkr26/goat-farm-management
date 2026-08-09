"""Pydantic schemas for the purchases module."""

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..models import MAX_AGE_MONTHS, MAX_BATCH_COUNT  # single source
from .animals import AnimalOut
from .common import (
    MAX_FREE_TEXT_LENGTH,
    NonNegativeFloat,
    NonNegativeMoneyFloat,
    NonNegativeWeightKgFloat,
    PastOrTodayDate,
    StrictBool,
    StrictInputModel,
    StrictInt,
)
from .summaries import PurchaseQuarantineAnimalOut, QuarantineScheduleTaskOut
from .tasks import TaskOut


class PurchaseBatchIn(StrictInputModel):
    date: PastOrTodayDate
    supplier: str | None = Field(default=None, max_length=120)
    count: StrictInt = Field(ge=1, le=MAX_BATCH_COUNT)
    sex: Literal["M", "F"] = "F"  # stub-animal sex (a bought buck is not a doe)
    avg_age_months: NonNegativeFloat | None = Field(default=None, le=MAX_AGE_MONTHS)
    avg_weight_kg: NonNegativeWeightKgFloat | None = None
    total_price: NonNegativeMoneyFloat | None = None
    notes: str | None = Field(default=None, max_length=MAX_FREE_TEXT_LENGTH)
    create_animals: StrictBool = True

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
    sex: Literal["M", "F"] | None
    avg_age_months: float | None
    avg_weight_kg: float | None
    total_price: float | None
    notes: str | None
    animals_created: int = 0
    open_tasks: int = 0  # pending quarantine tasks


class PurchaseBatchListOut(BaseModel):
    batches: list[PurchaseBatchOut]
    total: int
    limit: int
    offset: int


class PurchaseBatchDetailOut(BaseModel):
    batch: PurchaseBatchOut
    animals: list[AnimalOut | PurchaseQuarantineAnimalOut]
    tasks: list[TaskOut | QuarantineScheduleTaskOut]
    # `animals` is a bounded page (a batch may hold MAX_BATCH_COUNT head), so the
    # envelope carries its window the same way PurchaseBatchListOut does —
    # without it a client cannot tell a 100-animal batch from a truncated 1,000.
    animals_total: int
    animals_limit: int
    animals_offset: int
