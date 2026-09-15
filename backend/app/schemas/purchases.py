"""Pydantic schemas for the purchases module."""

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..models import MAX_AGE_MONTHS, MAX_BATCH_COUNT  # single source
from .animals import AnimalOut
from .common import (
    MAX_FREE_TEXT_LENGTH,
    NonNegativeFloat,
    NonNegativeMoneyFloat,
    NonNegativeWeightKgFloat,
    PastOrTodayDate,
    PostgresText,
    StrictBool,
    StrictInputModel,
    StrictInt,
)
from .summaries import PurchaseQuarantineAnimalOut, QuarantineScheduleTaskOut
from .tasks import TaskOut

# Ceiling on a recorded market-to-farm journey, mirroring the database CHECK
# ``ck_purchase_batches_transport_hours`` (0–240). Ten days in transit is
# already extreme; a larger number is a data-entry slip, not a shipment.
MAX_TRANSPORT_HOURS = 240


class PurchaseBatchIn(StrictInputModel):
    date: PastOrTodayDate
    supplier: PostgresText | None = Field(default=None, max_length=120)
    origin_market: PostgresText | None = Field(default=None, max_length=120)
    # Journey length from the purchase market to the farm (see MAX_TRANSPORT_HOURS).
    transport_hours: StrictInt | None = Field(default=None, ge=0, le=MAX_TRANSPORT_HOURS)
    # Prior vaccinations/deworming reported by the seller at source.
    seller_health_history: PostgresText | None = Field(
        default=None, max_length=MAX_FREE_TEXT_LENGTH
    )
    count: StrictInt = Field(ge=1, le=MAX_BATCH_COUNT)
    sex: Literal["M", "F"] = "F"  # stub-animal sex (a bought buck is not a doe)
    avg_age_months: NonNegativeFloat | None = Field(default=None, le=MAX_AGE_MONTHS)
    avg_weight_kg: NonNegativeWeightKgFloat | None = None
    # Optional per-animal arrival weights. When present, one weight per head
    # (exactly `count` values, each within the same bounds as a recorded
    # weight); when omitted the batch average (if any) is used for every
    # animal, exactly as before.
    individual_weights_kg: list[NonNegativeWeightKgFloat] | None = None
    total_price: NonNegativeMoneyFloat | None = None
    notes: PostgresText | None = Field(default=None, max_length=MAX_FREE_TEXT_LENGTH)
    create_animals: StrictBool = True

    @field_validator("date")
    @classmethod
    def _not_ancient(cls, value: dt.date) -> dt.date:
        if value.year < 2000:
            raise ValueError("date must be year 2000 or later")
        return value

    @model_validator(mode="after")
    def _individual_weights_are_plausible(self) -> "PurchaseBatchIn":
        """Per-head arrival weights only make sense as a complete set.

        A short or long list would silently shift every animal's weight by one
        position, and weights without stub animals have nowhere to be written,
        so both mismatch shapes are rejected at the request boundary instead
        of being dropped or half-applied.
        """
        weights = self.individual_weights_kg
        if weights is None:
            return self
        if len(weights) != self.count:
            raise ValueError(
                f"individual_weights_kg must contain exactly {self.count} values "
                f"(one per animal), got {len(weights)}"
            )
        if not self.create_animals:
            raise ValueError("individual_weights_kg requires create_animals to be true")
        return self


class PurchaseBatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    date: dt.date
    supplier: str | None
    origin_market: str | None
    transport_hours: int | None
    seller_health_history: str | None
    count: int
    sex: Literal["M", "F"] | None
    avg_age_months: float | None
    avg_weight_kg: float | None
    total_price: float | None
    notes: str | None
    created_at: dt.datetime
    created_by_id: int | None
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
