"""Pydantic schemas for the breeding module."""

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .common import (
    MAX_FREE_TEXT_LENGTH,
    BoundedId,
    PastOrTodayDate,
    StrictBool,
    StrictInputModel,
    StrictInt,
)


class BreedingCreateIn(StrictInputModel):
    doe_id: BoundedId
    buck_id: BoundedId
    breeding_date: PastOrTodayDate
    heat_cycle_number: Annotated[StrictInt, Field(ge=1, le=99)] = 1


class UltrasoundIn(StrictInputModel):
    pregnant: StrictBool
    # Optional for backwards compatibility with existing records. When
    # supplied, chronology is enforced against the planned check date.
    date: PastOrTodayDate | None = None
    # SPEC §BreedingRecord: kid_count_detected is 1/2/3 nullable (SINGLE/TWIN/TRIPLET).
    kid_count: Annotated[StrictInt, Field(ge=1, le=3)] | None = None

    @model_validator(mode="after")
    def _kid_count_requires_pregnancy(self) -> "UltrasoundIn":
        if not self.pregnant and self.kid_count is not None:
            raise ValueError("kid_count is only valid when pregnant is true")
        return self


PregnancyLossCause = Literal[
    "UNKNOWN",
    "DISEASE",
    "INJURY",
    "NUTRITIONAL",
    "TRAUMA",
    "ANIMAL_STATUS_CHANGE",
    "OTHER",
]


class PregnancyLossIn(StrictInputModel):
    """Auditable facts required to close a confirmed pregnancy as lost."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    loss_date: PastOrTodayDate
    cause: PregnancyLossCause
    notes: str | None = Field(default=None, max_length=MAX_FREE_TEXT_LENGTH)


class BreedingRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    doe_id: int
    buck_id: int
    breeding_date: date
    method: str
    heat_cycle_number: int
    ultrasound_date: date | None
    ultrasound_result_date: date | None
    ultrasound_done: bool
    pregnant: bool | None
    kid_count_detected: int | None
    expected_kidding_date: date | None
    outcome: str  # PENDING | CONFIRMED_PREGNANT | FAILED | ABORTED
    loss_date: date | None
    loss_cause: str | None
    loss_notes: str | None
    loss_recorded_by_id: int | None
    loss_recorded_at: datetime | None
    has_kidding: bool = False
    doe_tag: str | None = None
    buck_tag: str | None = None


class BreedingCandidateAvailabilityOut(BaseModel):
    """Bounded aggregate readiness context for the breeding history page."""

    eligible_doe_count: int
    eligible_buck_count: int


class BreedingListOut(BaseModel):
    records: list[BreedingRecordOut]
    # Eligibility is mutation-sensitive. A view-only caller receives null;
    # managers receive counts and fetch identities through the paged picker.
    candidate_availability: BreedingCandidateAvailabilityOut | None
    total: int
    limit: int
    offset: int


class BreedingCandidateOut(BaseModel):
    """Minimum animal identity and readiness context needed by breeding pickers."""

    id: int
    tag_number: str
    name: str | None
    age_months: int | None
    latest_weight_kg: float | None


class BreedingCandidateListOut(BaseModel):
    candidates: list[BreedingCandidateOut]
    total: int
    limit: int
    offset: int
