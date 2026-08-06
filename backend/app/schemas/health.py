"""Pydantic schemas for the health module."""

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .common import BoundedId, NonNegativeFloat, PastOrTodayDate

# Mirrors models.HealthEventType (v1 coerced anything else to TREATMENT;
# the JSON API rejects unknown types with 422 instead).
HealthEventTypeStr = Literal["VACCINE", "DEWORMING", "TREATMENT", "FOOTBATH", "VITAMIN"]


class HealthEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    animal_id: int
    purchase_batch_id: int | None
    date: dt.date
    type: str
    product_name: str | None
    disease_target: str | None
    dose: str | None
    route: str | None
    vet_name: str | None
    cost: float | None
    next_due_date: dt.date | None
    notes: str | None
    animal_tag: str | None = None


class ScheduleRowOut(BaseModel):
    template_id: int
    template_name: str
    timing_note: str | None
    first_due: dt.date | None
    booster_due: dt.date | None
    last_done: dt.date | None
    next_due: dt.date | None
    status: str  # DONE | OVERDUE | UPCOMING | UNKNOWN


class ScheduleOut(BaseModel):
    animal_id: int
    rows: list[ScheduleRowOut]


# Imported late to break the import cycle: schemas/animals.py imports
# HealthEventOut from this module for AnimalProfileOut (see its bottom).
from .animals import BucketStr  # noqa: E402


class HealthEventIn(BaseModel):
    scope: Literal["animal", "bucket", "batch"] = "animal"
    animal_id: BoundedId | None = None
    bucket: BucketStr | None = None
    purchase_batch_id: BoundedId | None = None
    date: PastOrTodayDate | None = None  # v1: a blank date meant "today"
    type: HealthEventTypeStr
    product_name: str | None = Field(default=None, max_length=120)
    disease_target: str | None = Field(default=None, max_length=120)
    dose: str | None = Field(default=None, max_length=60)
    route: str | None = Field(default=None, max_length=20)  # health_events.route is String(20)
    vet_name: str | None = Field(default=None, max_length=120)
    cost: NonNegativeFloat | None = None
    next_due_date: dt.date | None = None
    notes: str | None = None
    task_id: BoundedId | None = None  # complete a linked VACCINE/DEWORMING task

    @model_validator(mode="after")
    def _scope_target_present(self) -> "HealthEventIn":
        if self.scope == "animal" and self.animal_id is None:
            raise ValueError("animal_id is required for animal scope")
        if self.scope == "bucket" and self.bucket is None:
            raise ValueError("bucket is required for bucket scope")
        if self.scope == "batch" and self.purchase_batch_id is None:
            raise ValueError("purchase_batch_id is required for batch scope")
        return self
