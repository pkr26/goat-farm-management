"""Pydantic schemas for the breeding module."""

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .common import (
    MAX_FREE_TEXT_LENGTH,
    BoundedId,
    PastOrTodayDate,
    PostgresText,
    StrictBool,
    StrictInputModel,
    StrictInt,
)

# AI and AI_SEXED stay in the wire vocabulary, reserved for a future AI
# workflow; today the goat-meat protocol is natural cover and
# services.breeding.create_breeding_record rejects both by design (pinned by
# test_ai_methods_refused_for_goats and test_red_m2_goat_farm_rejects_ai_service).
BreedingMethodValue = Literal["NATURAL", "AI", "AI_SEXED"]


class BreedingCreateIn(StrictInputModel):
    doe_id: BoundedId
    # Herd sire for NATURAL service. Required for NATURAL; NULL for AI /
    # AI_SEXED, where the sire is the named semen bull instead.
    buck_id: BoundedId | None = None
    breeding_date: PastOrTodayDate
    method: BreedingMethodValue = "NATURAL"
    # Semen-bull identity (bull name / code from the straw) for AI services.
    semen_sire_name: PostgresText | None = Field(default=None, max_length=120)
    # Accepted for wire compatibility but NOT stored: the cycle index is
    # derived from the doe's own consecutive failed cycles
    # (services.breeding.derived_heat_cycle_number), so the reports'
    # first-cycle metric cannot be inflated by a client that omits or forges
    # it. The bound mirrors ck_breeding_records_heat_cycle.
    heat_cycle_number: Annotated[StrictInt, Field(ge=1, le=99)] = 1

    @model_validator(mode="after")
    def _sire_matches_method(self) -> "BreedingCreateIn":
        if self.method == "NATURAL":
            if self.buck_id is None:
                raise ValueError("buck_id is required for a NATURAL service")
            if self.semen_sire_name is not None:
                raise ValueError("semen_sire_name is valid only for AI services")
        return self


class UltrasoundIn(StrictInputModel):
    pregnant: StrictBool
    # Optional for backwards compatibility with existing records. When
    # supplied, chronology is enforced against the breeding date; a positive
    # result additionally cannot predate the planned check date.
    date: PastOrTodayDate | None = None
    # SPEC §BreedingRecord: kid_count_detected is 1/2/3 nullable (SINGLE/TWIN/
    # TRIPLET). The bound is the goat maximum (litters reach 4); the cap is
    # enforced against GOAT_PROFILE by record_ultrasound_result.
    kid_count: Annotated[StrictInt, Field(ge=1, le=4)] | None = None

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
    "OTHER",
]
# The Out-side vocabulary adds the server-owned administrative close written
# when an animal leaves the herd with a stale pregnancy (input schemas
# deliberately never accept it).
PregnancyLossCauseWithSystem = Literal[
    "UNKNOWN",
    "DISEASE",
    "INJURY",
    "NUTRITIONAL",
    "TRAUMA",
    "ANIMAL_STATUS_CHANGE",
    "OTHER",
]
BreedingOutcomeStr = Literal[
    "PENDING",
    "CONFIRMED_PREGNANT",
    "FAILED",
    "ABORTED",
    "UNASSESSED",
]
BreedingMethodStr = BreedingMethodValue


class PregnancyLossIn(StrictInputModel):
    """Auditable facts required to close a confirmed pregnancy as lost."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    # ANIMAL_STATUS_CHANGE is intentionally absent: it is a server-owned
    # audit cause emitted only when the animal-status workflow closes a live
    # pregnancy. A user-entered loss must describe an actual observed cause.
    loss_date: PastOrTodayDate
    cause: PregnancyLossCause
    notes: PostgresText | None = Field(default=None, max_length=MAX_FREE_TEXT_LENGTH)


class BreedingRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    doe_id: int
    buck_id: int | None  # None for AI/AI_SEXED services
    semen_sire_name: str | None
    breeding_date: date
    method: BreedingMethodStr
    heat_cycle_number: int
    ultrasound_date: date | None
    ultrasound_result_date: date | None
    ultrasound_done: bool
    pregnant: bool | None
    kid_count_detected: int | None
    expected_kidding_date: date | None
    # UNASSESSED: the doe left the herd before her pregnancy check, so the
    # service can never be scanned (models.enums.BreedingOutcome).
    outcome: BreedingOutcomeStr
    loss_date: date | None
    loss_cause: PregnancyLossCauseWithSystem | None
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
    # A cull-flagged doe is servable by the owner only (create_breeding_record
    # 409s her for every other manager); the picker must show the flag so the
    # operator can skip her instead of filling the form into a rejection
    # (wave-5 note, 2026-09-20 audit).
    cull_candidate: bool = False


class BreedingCandidateListOut(BaseModel):
    candidates: list[BreedingCandidateOut]
    total: int
    limit: int
    offset: int
