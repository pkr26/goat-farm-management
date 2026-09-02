"""Shared schema validators — the Pydantic layer of v1's manual guards."""

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from ..utils import today

# SQLite-era overflow guard kept: PG bigint is also 64-bit.
MAX_ID = 2**62

# Every primary key is a PG INTEGER (int4): an id above this cannot exist.
# BoundedId deliberately keeps the wider ceiling above so schema-valid but
# impossible ids reach the routers, which answer with their documented
# not-found/invalid-id 4xx instead of letting asyncpg raise an int32
# DataError (500). Routers guard every lookup with this bound.
MAX_INT32_ID = 2**31 - 1
# Offset pagination remains part of the current SPA contract. Bound it well
# below PostgreSQL's bigint ceiling so arbitrary-precision query integers
# cannot become driver errors or deliberately absurd scans.
MAX_PAGE_OFFSET = 1_000_000
# Large enough for useful clinical/purchase narrative, small enough to avoid
# accidentally persisting an attachment-sized blob in a Text column.
MAX_FREE_TEXT_LENGTH = 4_000


class StrictInputModel(BaseModel):
    """Base class for every client-controlled request body.

    Pydantic otherwise ignores unknown keys.  That makes a misspelled field
    look accepted even though the server silently discards it — particularly
    dangerous for dates, prices, task assignments and compliance records.
    """

    model_config = ConfigDict(extra="forbid")


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


def _quantity_kg_precision(value: float) -> float:
    """Normalize feed quantities to the inventory ledger's gram precision.

    Values that round below one gram are not real positive stock movements: the
    old service accepted them, rounded the balance change to zero, and still
    created a dispensing/restock record. ``ROUND_HALF_UP`` also avoids Python's
    binary-float/banker's-rounding surprises at the half-gram boundary.
    """
    rounded = Decimal(str(value)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    if rounded <= 0:
        raise ValueError("must be at least 0.001 kg after rounding")
    return float(rounded)


def _money_precision(value: float) -> float:
    """Normalize currency inputs to paise without turning a charge into free data."""
    rounded = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if value != 0 and rounded == 0:
        raise ValueError("a non-zero amount must round to at least 0.01")
    return float(rounded)


def _postgres_text(value: str) -> str:
    """Reject text bytes PostgreSQL cannot store before they reach asyncpg.

    Tabs, newlines and carriage returns remain valid narrative text. Other C0
    controls have no useful representation in these JSON forms and PostgreSQL
    rejects NUL outright, so accepting them only turns a validation mistake
    into an opaque database 500.
    """
    allowed = "\t\n\r"
    if any(char < " " and char not in allowed for char in value):
        raise ValueError("cannot contain control characters")
    # A lone UTF-16 surrogate survives json.loads ('"\\ud800"' decodes to a
    # real str) and clears the control-character rule, but str.encode("utf-8")
    # — exactly what asyncpg's text codec calls on a bind parameter — raises
    # UnicodeEncodeError. That is not a DBAPI error, so SQLAlchemy never wraps
    # it and no router's `except IntegrityError` sees it: it reached the
    # catch-all handler as the opaque 500 this validator exists to prevent.
    # Encodability is the property actually being promised, so test it.
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("cannot contain unpaired surrogate characters") from None
    return value


# JSON booleans are Python ints and Pydantic's default coercion also accepts
# numeric strings. Neither is a safe wire representation for money, weights,
# quantities, or primary keys: `true` must never silently become animal/role
# id 1 and `"12.5"` must not look like a successfully recorded measurement.
FiniteFloat = Annotated[float, Field(strict=True), AfterValidator(_finite)]
StrictInt = Annotated[int, Field(strict=True)]
StrictBool = Annotated[bool, Field(strict=True)]
NonNegativeFloat = Annotated[FiniteFloat, AfterValidator(_not_negative)]
PositiveFloat = Annotated[FiniteFloat, AfterValidator(_positive)]
# "Not in the future" tolerates one day past the UTC date so client timezones
# east of UTC (e.g. IST, UTC+5:30) can submit their local "today" during the
# hours when it is still tomorrow in UTC.
PastOrTodayDate = Annotated[date, AfterValidator(_not_future)]
BoundedId = Annotated[int, Field(strict=True, ge=1, le=MAX_ID)]
PostgresText = Annotated[str, AfterValidator(_postgres_text)]

# Bounded money/quantity variants. Unbounded positives let `1e308 * 1e308`
# overflow to inf in derived values (feed-purchase qty × price) and poison
# stored rows — a single inf transaction used to break every later
# GET /api/finance on JSON serialization. Caps are far past anything the
# domain can legitimately reach: ₹1e9 (100 crore) for money, 1e6 kg for feed
# quantities, 1000 kg for a single animal's weight.
MoneyFloat = Annotated[
    PositiveFloat,
    Field(le=1_000_000_000),
    AfterValidator(_money_precision),
]
NonNegativeMoneyFloat = Annotated[
    NonNegativeFloat,
    Field(le=1_000_000_000),
    AfterValidator(_money_precision),
]
QuantityKgFloat = Annotated[
    PositiveFloat,
    Field(le=1_000_000),
    AfterValidator(_quantity_kg_precision),
]
WeightKgFloat = Annotated[PositiveFloat, Field(le=1000)]
NonNegativeWeightKgFloat = Annotated[NonNegativeFloat, Field(le=1000)]


class ErrorOut(BaseModel):
    """Documented shape of every raised-error response ({"detail": ...})."""

    detail: str


# Router-level OpenAPI response declarations: handlers systematically raise
# HTTPException(400|401|403|404|409|422|429) that the generated contract used
# to leave undeclared (53/86 routes), so consumers could not know a route can
# 409/404. APIRouter(responses=...) merges these into every route's docs.
COMMON_ERROR_RESPONSES = {
    400: {"model": ErrorOut, "description": "Rejected (invalid state or values)"},
    401: {"model": ErrorOut, "description": "Not authenticated"},
    403: {"model": ErrorOut, "description": "Authenticated but not permitted"},
    404: {"model": ErrorOut, "description": "Not found (or belongs to another farm)"},
    409: {"model": ErrorOut, "description": "Conflict (state, replay, or race)"},
    429: {"model": ErrorOut, "description": "Rate limited"},
}
