"""Pydantic schemas for the kidding module."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..models.species import GOAT_PROFILE
from .animals import IdentifierText, Sex
from .breeding import BreedingRecordOut
from .common import (
    MAX_FREE_TEXT_LENGTH,
    BoundedId,
    NonNegativeWeightKgFloat,
    PastOrTodayDate,
    PostgresText,
    StrictBool,
    StrictInputModel,
)

KidStatusStr = Literal["ALIVE", "STILLBORN", "DIED"]
# SPEC §KiddingRecord + models.KiddingEase: exactly these four since the
# husbandry-standards release added CAESAREAN.
KiddingEaseStr = Literal["NORMAL", "ASSISTED", "DIFFICULT", "CAESAREAN"]


class KidIn(StrictInputModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tag: IdentifierText | None = Field(default=None, max_length=50)  # blank → auto tag
    sex: Literal["M", "F"]
    birth_weight: NonNegativeWeightKgFloat | None = None
    status: KidStatusStr = "ALIVE"
    mortality_reported_at: PastOrTodayDate | None = None
    # Neonatal care facts; None = not recorded. StrictBool, like every other mutating boolean: lax
    # coercion would let 1/"true"/"off" silently pose as a recorded care fact instead of the 422 the
    # strict-input contract promises.
    colostrum_within_2h: StrictBool | None = None
    navel_dipped: StrictBool | None = None
    dam_rejected: StrictBool = False

    @model_validator(mode="after")
    def _mortality_report_matches_status(self) -> "KidIn":
        if self.status == "DIED" and self.mortality_reported_at is None:
            raise ValueError("mortality_reported_at is required when kid status is DIED")
        if self.status != "DIED" and self.mortality_reported_at is not None:
            raise ValueError("mortality_reported_at is only valid when kid status is DIED")
        return self

    @model_validator(mode="after")
    def _stillborn_takes_no_neonatal_care(self) -> "KidIn":
        # A stillborn kid never nursed, was never navel-dipped and cannot be
        # rejected by the dam: recording any of it would corrupt care metrics.
        if self.status == "STILLBORN" and (
            self.colostrum_within_2h is not None
            or self.navel_dipped is not None
            or self.dam_rejected
        ):
            raise ValueError(
                "colostrum_within_2h, navel_dipped and dam_rejected must be "
                "unset when kid status is STILLBORN"
            )
        return self


class KiddingCreateIn(StrictInputModel):
    breeding_record_id: BoundedId
    # PastOrTodayDate covers "not in the future" incl. the one-day timezone
    # headroom; >= the breeding date is checked in the router.
    date: PastOrTodayDate
    ease: KiddingEaseStr = "NORMAL"
    # Postpartum care facts; None = not recorded. Parity is deliberately NOT accepted: it is
    # server-derived from the doe's kidding history (like heat_cycle_number in breeding), so
    # extra="forbid" rejects client input. StrictBool per the strict-input contract.
    placenta_passed: StrictBool | None = None
    mastitis_suspected: StrictBool = False
    notes: PostgresText | None = Field(default=None, max_length=MAX_FREE_TEXT_LENGTH)
    # Litter bound is the species ceiling (GOAT_PROFILE.max_litter_size = 4, enforced again by
    # services.kidding), not an arbitrary round number.
    kids: list[KidIn] = Field(min_length=1, max_length=GOAT_PROFILE.max_litter_size)


class KidEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tag: str | None
    sex: Sex
    birth_weight: float | None
    status: KidStatusStr
    mortality_reported_at: date | None
    colostrum_within_2h: bool | None
    navel_dipped: bool | None
    dam_rejected: bool
    animal_id: int | None
    created_at: datetime


class KiddingRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    doe_id: int
    date: date
    breeding_record_id: int | None
    ease: KiddingEaseStr
    parity: int | None
    placenta_passed: bool | None
    mastitis_suspected: bool
    notes: str | None
    created_at: datetime
    # The literal [] default is deliberate: it is aliasing-safe (Pydantic v2 deep-copies defaults)
    # AND it is what emits the committed spec's `"default": []` — switching to the sibling
    # default_factory idiom would silently drop that key from shared/openapi.json for zero
    # behavioral gain, so the style inconsistency is documented instead of churned.
    kids: list[KidEntryOut] = []
    doe_tag: str | None = None


class KiddingListOut(BaseModel):
    records: list[KiddingRecordOut]
    upcoming: list[BreedingRecordOut]  # confirmed, due within 30 days, not overdue
    upcoming_total: int
    upcoming_limit: int
    upcoming_offset: int
    overdue: list[BreedingRecordOut]  # confirmed, past expected kidding date
    overdue_total: int
    overdue_limit: int
    overdue_offset: int
    total: int
    limit: int
    offset: int
