"""Shared schema validators — the Pydantic layer of v1's manual guards."""

from datetime import date, timedelta
from typing import Annotated

from pydantic import AfterValidator, Field

from ..utils import today

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
    # One day of headroom past the UTC date: users are in timezones east of
    # UTC (IST is UTC+5:30), where 00:00–05:30 local is still "tomorrow" in
    # UTC — a strict `> today()` rejected their same-day entries with a 422.
    if value > today() + timedelta(days=1):
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
# "Not in the future" tolerates one day past the UTC date so client timezones
# east of UTC (e.g. IST, UTC+5:30) can submit their local "today" during the
# hours when it is still tomorrow in UTC.
PastOrTodayDate = Annotated[date, AfterValidator(_not_future)]
BoundedId = Annotated[int, Field(ge=1, le=MAX_ID)]

# Bounded money/quantity variants. Unbounded positives let `1e308 * 1e308`
# overflow to inf in derived values (feed-purchase qty × price) and poison
# stored rows — a single inf transaction used to break every later
# GET /api/finance on JSON serialization. Caps are far past anything the
# domain can legitimately reach: ₹1e9 (100 crore) for money, 1e6 kg for feed
# quantities, 1000 kg for a single animal's weight.
MoneyFloat = Annotated[PositiveFloat, Field(le=1_000_000_000)]
NonNegativeMoneyFloat = Annotated[NonNegativeFloat, Field(le=1_000_000_000)]
QuantityKgFloat = Annotated[PositiveFloat, Field(le=1_000_000)]
WeightKgFloat = Annotated[PositiveFloat, Field(le=1000)]
NonNegativeWeightKgFloat = Annotated[NonNegativeFloat, Field(le=1000)]
