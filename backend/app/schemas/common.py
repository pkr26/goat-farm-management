"""Shared schema validators — the Pydantic layer of v1's manual guards."""

from datetime import date
from typing import Annotated

from pydantic import AfterValidator, Field

# SQLite-era overflow guard kept: PG bigint is also 64-bit.
MAX_ID = 2**62

# Every primary key is a PG INTEGER (int4): an id above this cannot exist.
# BoundedId deliberately keeps the wider ceiling above so schema-valid but
# impossible ids reach the routers, which answer with their documented
# not-found/invalid-id 4xx instead of letting asyncpg raise an int32
# DataError (500). Routers guard every lookup with this bound.
MAX_INT32_ID = 2**31 - 1


def _finite(value: float) -> float:
    import math

    if not math.isfinite(value):
        raise ValueError("must be a finite number")
    return value


def _not_future(value: date) -> date:
    if value > date.today():
        raise ValueError("date cannot be in the future")
    return value


def _not_negative(value: float) -> float:
    if value < 0:
        raise ValueError("cannot be negative")
    return value


def _positive(value: float) -> float:
    if value <= 0:
        raise ValueError("must be positive")
    return value


FiniteFloat = Annotated[float, AfterValidator(_finite)]
NonNegativeFloat = Annotated[FiniteFloat, AfterValidator(_not_negative)]
PositiveFloat = Annotated[FiniteFloat, AfterValidator(_positive)]
PastOrTodayDate = Annotated[date, AfterValidator(_not_future)]
BoundedId = Annotated[int, Field(ge=1, le=MAX_ID)]
