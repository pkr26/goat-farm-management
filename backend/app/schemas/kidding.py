"""Pydantic schemas for the kidding module."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .breeding import BreedingRecordOut
from .common import BoundedId, NonNegativeWeightKgFloat, PastOrTodayDate

KidStatusStr = Literal["ALIVE", "STILLBORN", "DIED"]
# SPEC §KiddingRecord + models.KiddingEase: exactly these three (no CAESAREAN).
KiddingEaseStr = Literal["NORMAL", "ASSISTED", "DIFFICULT"]


class KidIn(BaseModel):
    tag: str | None = Field(default=None, max_length=50)  # blank → auto tag
    sex: Literal["M", "F"]
    birth_weight: NonNegativeWeightKgFloat | None = None
    status: KidStatusStr = "ALIVE"


class KiddingCreateIn(BaseModel):
    breeding_record_id: BoundedId
    # PastOrTodayDate covers "not in the future" incl. the one-day timezone
    # headroom; >= the breeding date is checked in the router.
    date: PastOrTodayDate
    ease: KiddingEaseStr = "NORMAL"
    notes: str | None = None
    kids: list[KidIn] = Field(min_length=1, max_length=10)


class KidEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tag: str | None
    sex: str
    birth_weight: float | None
    status: str
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
    overdue: list[BreedingRecordOut]  # confirmed, past expected kidding date
