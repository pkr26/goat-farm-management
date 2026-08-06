"""Pydantic schemas for the animals module."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .common import BoundedId, NonNegativeFloat, PastOrTodayDate, PositiveFloat

Sex = Literal["M", "F"]
AnimalSourceStr = Literal["BORN", "PURCHASED"]
AnimalStatusStr = Literal["ACTIVE", "SOLD", "DEAD", "CULLED"]
BirthTypeStr = Literal["SINGLE", "TWIN", "TRIPLET"]
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
    tag_number: str = Field(min_length=1, max_length=50)
    name: str | None = Field(default=None, max_length=80)
    sex: Sex
    source: AnimalSourceStr
    current_bucket: BucketStr
    breed: str = Field(default="Osmanabadi", max_length=60)
    date_of_birth: PastOrTodayDate | None = None
    estimated_dob: PastOrTodayDate | None = None
    birth_type: BirthTypeStr | None = None
    birth_weight: NonNegativeFloat | None = None
    purchase_date: PastOrTodayDate | None = None
    purchase_price: NonNegativeFloat | None = None
    seller_name: str | None = Field(default=None, max_length=120)
    weight_kg: PositiveFloat | None = None  # optional entry weight record
    notes: str | None = None


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
    weight_kg: PositiveFloat
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
    sale_price: NonNegativeFloat | None = None
    buyer_name: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=255)  # animals.status_notes String(255)


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


class AnimalIdsIn(BaseModel):
    ids: list[BoundedId]


from .health import HealthEventOut  # noqa: E402

AnimalProfileOut.model_rebuild()
