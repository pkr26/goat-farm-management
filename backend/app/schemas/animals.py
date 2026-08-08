"""Pydantic schemas for the animals module."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .common import (
    NonNegativeMoneyFloat,
    NonNegativeWeightKgFloat,
    PastOrTodayDate,
    WeightKgFloat,
)

Sex = Literal["M", "F"]
AnimalSourceStr = Literal["BORN", "PURCHASED"]
AnimalStatusStr = Literal["ACTIVE", "SOLD", "DEAD", "CULLED"]
BirthTypeStr = Literal["SINGLE", "TWIN", "TRIPLET", "QUADRUPLET", "MULTIPLET"]
BucketStr = Literal[
    "QUARANTINE",
    "FOUNDATION",
    "BREEDING",
    "PREGNANCY_EARLY",
    "PREGNANCY_LATE",
    "DELIVERY",
    "RECOVERY",
    "RESTING",
    "MALE_KIDS",
    "FEMALE_KIDS",
]


class AnimalCreateIn(BaseModel):
    tag_number: str | None = Field(default=None, min_length=1, max_length=50)
    name: str | None = Field(default=None, max_length=80)
    sex: Sex
    source: AnimalSourceStr
    current_bucket: BucketStr
    breed: str = Field(default="Osmanabadi", max_length=60)
    date_of_birth: PastOrTodayDate | None = None
    estimated_dob: PastOrTodayDate | None = None
    birth_type: BirthTypeStr | None = None
    birth_weight: NonNegativeWeightKgFloat | None = None
    purchase_date: PastOrTodayDate | None = None
    purchase_price: NonNegativeMoneyFloat | None = None
    seller_name: str | None = Field(default=None, max_length=120)
    weight_kg: WeightKgFloat | None = None  # optional entry weight record
    notes: str | None = None

    @model_validator(mode="after")
    def _source_fields_are_coherent(self) -> "AnimalCreateIn":
        """Reject source-field combinations that would create false lineage.

        Direct historical entry remains possible (for example a BORN animal
        with a birth weight), but a record cannot simultaneously claim a farm
        birth and purchase provenance.
        """
        if self.source == "BORN" and any(
            value is not None
            for value in (self.purchase_date, self.purchase_price, self.seller_name)
        ):
            raise ValueError("Born animals cannot include purchase provenance")
        if self.source == "PURCHASED" and any(
            value is not None for value in (self.birth_type, self.birth_weight)
        ):
            raise ValueError("Purchased animals cannot include birth-only fields")
        if self.current_bucket == "MALE_KIDS" and self.sex != "M":
            raise ValueError("Only male animals may enter MALE_KIDS")
        if self.current_bucket == "FEMALE_KIDS" and self.sex != "F":
            raise ValueError("Only female animals may enter FEMALE_KIDS")
        if (
            self.current_bucket in {"PREGNANCY_EARLY", "PREGNANCY_LATE", "DELIVERY"}
            and self.sex != "F"
        ):
            raise ValueError(f"Only female animals may enter {self.current_bucket}")
        return self


class AnimalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tag_number: str
    name: str | None
    breed: str
    sex: str
    date_of_birth: date | None
    estimated_dob: date | None
    birth_type: str | None
    source: str
    dam_id: int | None
    sire_id: int | None
    birth_weight: float | None
    current_bucket: str
    status: str
    status_date: date | None
    sale_price: float | None
    purchase_date: date | None
    purchase_price: float | None
    seller_name: str | None
    cull_candidate: bool
    movement_restricted: bool
    restriction_reason: str | None
    suspected_scheduled_disease: bool
    suspected_disease: str | None
    authority_notified_at: date | None
    restriction_cleared_at: datetime | None
    restriction_cleared_by_id: int | None
    restriction_clearance_reference: str | None
    mortality_cause: str | None
    mortality_reported_at: date | None
    notes: str | None
    created_at: datetime
    # computed
    age_months: int | None = None
    latest_weight_kg: float | None = None
    is_breeding_ready: bool = False
    is_currently_pregnant: bool = False
    days_in_current_bucket: int = 0


class AnimalListOut(BaseModel):
    animals: list[AnimalOut]
    total: int


class WeightIn(BaseModel):
    date: PastOrTodayDate | None = None  # defaults to today
    weight_kg: WeightKgFloat
    bcs: int | None = Field(default=None, ge=1, le=5)
    notes: str | None = Field(default=None, max_length=255)  # weight_records.notes String(255)


class WeightRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    date: date
    weight_kg: float
    bcs: int | None
    notes: str | None


class MoveIn(BaseModel):
    to_bucket: BucketStr
    reason: str | None = Field(default=None, max_length=255)  # bucket_moves.reason String(255)


class BucketMoveOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    from_bucket: str | None
    to_bucket: str
    reason: str | None
    moved_at: datetime


class StatusChangeIn(BaseModel):
    new_status: Literal["SOLD", "DEAD", "CULLED"]
    date: PastOrTodayDate | None = None  # defaults to today
    sale_price: NonNegativeMoneyFloat | None = None
    buyer_name: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=255)  # animals.status_notes String(255)
    mortality_cause: str | None = Field(default=None, max_length=120)
    mortality_reported_at: PastOrTodayDate | None = None
    suspected_scheduled_disease: bool = False
    suspected_disease: str | None = Field(default=None, max_length=120)
    authority_notified_at: PastOrTodayDate | None = None

    @model_validator(mode="after")
    def _death_escalation_fields_are_coherent(self) -> "StatusChangeIn":
        if self.new_status != "DEAD" and any(
            value is not None
            for value in (
                self.mortality_cause,
                self.mortality_reported_at,
                self.suspected_disease,
                self.authority_notified_at,
            )
        ):
            raise ValueError("Mortality and disease-escalation fields require DEAD status")
        if self.suspected_scheduled_disease and not self.suspected_disease:
            raise ValueError("A suspected scheduled disease requires an identified disease")
        return self


class AnimalProfileOut(BaseModel):
    animal: AnimalOut
    kids: list[AnimalOut]  # animals with dam_id = this animal (v1 profile showed them)
    weights: list[WeightRecordOut]
    moves: list[BucketMoveOut]
    health_events: list["HealthEventOut"]
    breedings: list[int]  # breeding record ids (details fetched via /api/breeding)


class BucketBoardRow(BaseModel):
    bucket: str
    name: str
    who: str
    exit_rule: str
    daily_kg_per_head: float
    animals: list[AnimalOut]


from .health import HealthEventOut  # noqa: E402

AnimalProfileOut.model_rebuild()
