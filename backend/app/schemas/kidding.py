"""Pydantic schemas for the kidding module."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .animals import IdentifierText, Sex
from .breeding import BreedingRecordOut
from .common import (
    MAX_FREE_TEXT_LENGTH,
    BoundedId,
    NonNegativeWeightKgFloat,
    PastOrTodayDate,
    PostgresText,
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
    # Neonatal care facts; None = not recorded.
    colostrum_within_2h: bool | None = None
    navel_dipped: bool | None = None
    dam_rejected: bool = False

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
    # Postpartum care facts; None = not recorded. Parity is deliberately NOT
    # accepted: it is server-derived from the doe's kidding history (like
    # heat_cycle_number in breeding), so extra="forbid" rejects client input.
    placenta_passed: bool | None = None
    mastitis_suspected: bool = False
    notes: PostgresText | None = Field(default=None, max_length=MAX_FREE_TEXT_LENGTH)
    kids: list[KidIn] = Field(min_length=1, max_length=10)


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
