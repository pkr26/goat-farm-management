"""Pydantic schemas for the kidding module."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
# SPEC §KiddingRecord + models.KiddingEase: exactly these three (no CAESAREAN).
KiddingEaseStr = Literal["NORMAL", "ASSISTED", "DIFFICULT"]


class KidIn(StrictInputModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tag: PostgresText | None = Field(default=None, max_length=50)  # blank → auto tag
    sex: Literal["M", "F"]
    birth_weight: NonNegativeWeightKgFloat | None = None
    status: KidStatusStr = "ALIVE"
    mortality_reported_at: PastOrTodayDate | None = None

    @model_validator(mode="after")
    def _mortality_report_matches_status(self) -> "KidIn":
        if self.status == "DIED" and self.mortality_reported_at is None:
            raise ValueError("mortality_reported_at is required when kid status is DIED")
        if self.status != "DIED" and self.mortality_reported_at is not None:
            raise ValueError("mortality_reported_at is only valid when kid status is DIED")
        return self


class KiddingCreateIn(StrictInputModel):
    breeding_record_id: BoundedId
    # PastOrTodayDate covers "not in the future" incl. the one-day timezone
    # headroom; >= the breeding date is checked in the router.
    date: PastOrTodayDate
    ease: KiddingEaseStr = "NORMAL"
    notes: PostgresText | None = Field(default=None, max_length=MAX_FREE_TEXT_LENGTH)
    kids: list[KidIn] = Field(min_length=1, max_length=10)


class KidEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tag: str | None
    sex: str
    birth_weight: float | None
    status: str
    mortality_reported_at: date | None
    animal_id: int | None


class KiddingRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    doe_id: int
    date: date
    breeding_record_id: int | None
    ease: str
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
