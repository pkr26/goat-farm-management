"""Pydantic schemas for the breeding module."""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from .common import BoundedId, PastOrTodayDate


class BreedingCreateIn(BaseModel):
    doe_id: BoundedId
    buck_id: BoundedId
    breeding_date: PastOrTodayDate
    heat_cycle_number: int = Field(default=1, ge=1, le=99)


class UltrasoundIn(BaseModel):
    pregnant: bool
    # SPEC §BreedingRecord: kid_count_detected is 1/2/3 nullable (SINGLE/TWIN/TRIPLET).
    kid_count: int | None = Field(default=None, ge=1, le=3)


class BreedingRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    doe_id: int
    buck_id: int
    breeding_date: date
    method: str
    heat_cycle_number: int
    ultrasound_date: date | None
    ultrasound_done: bool
    pregnant: bool | None
    kid_count_detected: int | None
    expected_kidding_date: date | None
    outcome: str  # PENDING | CONFIRMED_PREGNANT | FAILED | ABORTED
    has_kidding: bool = False
    doe_tag: str | None = None
    buck_tag: str | None = None


class BreedingListOut(BaseModel):
    records: list[BreedingRecordOut]
    candidate_doe_ids: list[int]  # does eligible for a new breeding
    active_buck_ids: list[int]
