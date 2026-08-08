"""Pydantic schemas for the health module."""

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .common import BoundedId, NonNegativeMoneyFloat, PastOrTodayDate

# Mirrors models.HealthEventType (v1 coerced anything else to TREATMENT;
# the JSON API rejects unknown types with 422 instead).
HealthEventTypeStr = Literal["VACCINE", "DEWORMING", "TREATMENT", "FOOTBATH", "VITAMIN"]


class MovementRestrictionClearIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    clearance_reference: str = Field(min_length=1, max_length=255)


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
    schedule_template_name: str | None
    next_due_authority: str | None
    product_lot: str | None
    product_manufactured_on: dt.date | None
    product_expires_on: dt.date | None
    vaccine_valid_until: dt.date | None
    certificate_number: str | None
    official_tag_number: str | None
    administered_by: str | None
    withdrawal_until: dt.date | None
    suspected_scheduled_disease: bool
    authority_notified_at: dt.date | None
    isolation_started_at: dt.date | None
    notes: str | None
    animal_tag: str | None = None


class HealthEventListOut(BaseModel):
    """A visible page of the farm health ledger.

    `total` is deliberately returned with every page so the UI does not imply
    that the requested page is the complete statutory/audit history.
    """

    events: list[HealthEventOut]
    total: int
    limit: int
    offset: int


class HealthAnimalOptionOut(BaseModel):
    """Least-privilege animal identity exposed inside health workflows."""

    id: int
    tag_number: str
    name: str | None
    current_bucket: str


class HealthAnimalOptionListOut(BaseModel):
    animals: list[HealthAnimalOptionOut]
    total: int
    limit: int
    offset: int


class HealthPurchaseBatchOptionOut(BaseModel):
    """Targetable purchase batch summary; no purchase ledger detail is exposed."""

    id: int
    date: dt.date
    supplier: str | None
    count: int
    active_animal_count: int


class HealthPurchaseBatchOptionListOut(BaseModel):
    batches: list[HealthPurchaseBatchOptionOut]
    total: int
    limit: int
    offset: int


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
    cost: NonNegativeMoneyFloat | None = None
    next_due_date: dt.date | None = None
    schedule_template_name: str | None = Field(default=None, max_length=120)
    # A date manually supplied by a user is only allowed to override the
    # template cadence when they also state the record that authorises it.
    next_due_authority: str | None = Field(default=None, max_length=120)
    product_lot: str | None = Field(default=None, max_length=120)
    product_manufactured_on: PastOrTodayDate | None = None
    product_expires_on: dt.date | None = None
    vaccine_valid_until: dt.date | None = None
    certificate_number: str | None = Field(default=None, max_length=120)
    official_tag_number: str | None = Field(default=None, max_length=80)
    administered_by: str | None = Field(default=None, max_length=120)
    withdrawal_until: dt.date | None = None
    suspected_scheduled_disease: bool = False
    authority_notified_at: PastOrTodayDate | None = None
    isolation_started_at: PastOrTodayDate | None = None
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
        if self.next_due_date is not None:
            # When `date` is omitted the API resolves it in the farm's IANA
            # timezone; schema validation has no farm context, so that
            # comparison is deliberately deferred to the endpoint.
            if self.date is not None and self.next_due_date <= self.date:
                raise ValueError("next_due_date must be after the health event date")
            if not self.schedule_template_name or not self.next_due_authority:
                raise ValueError(
                    "next_due_date requires a schedule template and recorded authority"
                )
        if self.product_manufactured_on and self.product_expires_on:
            if self.product_expires_on < self.product_manufactured_on:
                raise ValueError("product expiry cannot be before manufacture date")
        if self.product_expires_on and self.date and self.product_expires_on < self.date:
            raise ValueError("product expiry cannot predate the health event")
        if self.vaccine_valid_until and self.product_expires_on:
            if self.vaccine_valid_until > self.product_expires_on:
                raise ValueError("vaccine validity cannot extend beyond product expiry")
        if self.withdrawal_until and self.date and self.withdrawal_until < self.date:
            raise ValueError("withdrawal_until cannot be before the health event date")
        if self.suspected_scheduled_disease and not self.disease_target:
            raise ValueError("A suspected scheduled disease requires a disease target")
        # Reject cross-scope target ids instead of silently
        # ignoring them (v1 dropped a stray animal_id sent with batch scope).
        if self.scope != "animal" and self.animal_id is not None:
            raise ValueError("animal_id only applies to animal scope")
        if self.scope != "batch" and self.purchase_batch_id is not None:
            raise ValueError("purchase_batch_id only applies to batch scope")
        return self
