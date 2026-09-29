"""Shared schema validators — the Pydantic layer of v1's manual guards."""

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any

from fastapi import HTTPException
from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from ..characters import FORBIDDEN_TEXT_CHARS
from ..utils import today

# SQLite-era overflow guard kept: PG bigint is also 64-bit.
MAX_ID = 2**62

# Almost every primary key is a PG INTEGER (int4): an id above this cannot
# exist on those tables. The exceptions are bigint — the screening fact
# tables (images/crops/runs/findings) and idempotency_records.id — whose
# routers guard with the int8 ceiling instead (2026-09-28 audit, D1).
# BoundedId deliberately keeps the wider ceiling above so schema-valid but
# impossible ids reach the routers, which answer with their documented
# not-found/invalid-id 4xx instead of letting asyncpg raise an int32
# DataError (500). Routers guard every int4 lookup with this bound.
MAX_INT32_ID = 2**31 - 1
# Offset pagination remains part of the current SPA contract. Bound it low:
# deep offsets are a deep-scan DoS (every page re-scans and re-sorts the rows
# it discards), and no farm view legitimately pages ten thousand rows in. The
# ceiling also stays far below PostgreSQL's bigint limit so
# arbitrary-precision query integers cannot become driver errors.
MAX_PAGE_OFFSET = 10_000
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

    Beyond C0, DEL/C1 controls, Unicode line separators (U+2028/U+2029) and
    bidirectional embedding/override/isolate marks are rejected: they are
    invisible or line-break-like in exports/log viewers and can visually
    reverse surrounding text (2026-09-16 audit, INJ-3). ZWJ/ZWNJ stay allowed
    — Telugu conjuncts require them.
    """
    allowed = "\t\n\r"
    if any(char < " " and char not in allowed for char in value):
        raise ValueError("cannot contain control characters")
    if any(char in FORBIDDEN_TEXT_CHARS for char in value):
        raise ValueError(
            "cannot contain control, line-separator, or directional formatting characters"
        )
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


# Machine-readable error codes for the highest-stakes failure classes
# (ITEM 5, 2026-09-21 playbook): clients map these through their locale
# catalogs instead of matching English server prose. The code is derived
# from the status only, so it is stable across wording changes; ``detail``
# remains the human-readable (English) text and is always present.
ERROR_CODES_BY_STATUS: dict[int, str] = {
    401: "UNAUTHENTICATED",
    403: "PERMISSION_DENIED",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
}

# Conflict codes (2026-09-29, RFC 9457-style ``code`` members): 409 covers
# several failure families the status alone cannot separate, and the A3
# convention moved every wrong-lifecycle and standing-quota answer onto it.
# Per-class codes let localized clients branch on the family instead of
# byte-pinning English ``detail`` prose (the raise-site helpers below attach
# them; see http_exception_handler).
LIFECYCLE_CONFLICT = "LIFECYCLE_CONFLICT"
STANDING_QUOTA_CONFLICT = "STANDING_QUOTA_CONFLICT"
STALE_STATE_CONFLICT = "STALE_STATE_CONFLICT"


class ErrorOut(BaseModel):
    """Documented shape of every raised-error response ({"detail": ...}).

    ``code`` is present on the four status-derived classes above and on the
    coded 409 families (LIFECYCLE_CONFLICT / STANDING_QUOTA_CONFLICT /
    STALE_STATE_CONFLICT — absent otherwise) so localized clients never have
    to parse ``detail`` prose.
    """

    detail: str
    code: str | None = None


class CodedHTTPException(HTTPException):
    """A 409 carrying its conflict-family code (2026-09-29).

    FastAPI's exception handlers receive ``Exception``, so the code rides on
    a dedicated attribute the default handler picks up; everything else
    (status, detail, headers) behaves exactly like a plain HTTPException.
    """

    conflict_code: str

    def __init__(self, conflict_code: str, *, detail: str) -> None:
        super().__init__(status_code=409, detail=detail)
        self.conflict_code = conflict_code


def lifecycle_conflict(*, detail: str) -> CodedHTTPException:
    """409: the resource's lifecycle state refuses this request (a duty that
    already transitioned, a terminal animal, a batch already submitted)."""
    return CodedHTTPException(LIFECYCLE_CONFLICT, detail=detail)


def standing_quota(*, detail: str) -> CodedHTTPException:
    """409: a standing per-farm capacity is full (open duties, plans,
    walkthroughs, scenarios, team seats, idempotency records)."""
    return CodedHTTPException(STANDING_QUOTA_CONFLICT, detail=detail)


def stale_state_conflict(*, detail: str) -> CodedHTTPException:
    """409: optimistic-concurrency mismatch — the row changed under the
    caller's expected revision/status; reload and retry."""
    return CodedHTTPException(STALE_STATE_CONFLICT, detail=detail)


class RequestValidationIssueOut(BaseModel):
    """One safe, client-actionable issue from the custom 422 handler."""

    type: str
    loc: list[str | int]
    msg: str


class RequestValidationErrorOut(BaseModel):
    """422 shape emitted for malformed request bodies, paths, and queries."""

    detail: list[RequestValidationIssueOut]
    code: str | None = None


# Router-level OpenAPI response declarations: handlers systematically raise
# HTTPException(...) and global middleware emits bounded-request failures.
# APIRouter(responses=...) merges these into every route's docs. Validation
# failures have two real wire shapes: FastAPI request validation returns a
# list in ``detail``, while domain rules deliberately return ErrorOut's string
# detail. Document their disjoint union so generated clients do not discard
# legitimate business-rule messages.
# Annotated to match APIRouter's expected shape (dict[int | str, dict[str, Any]])
# so the ~14 routers passing this to APIRouter(responses=...) stay strict-clean.
COMMON_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    # 400 vs 409 vs 429 convention (2026-09-28 audit, A3/A4): 400 is the
    # request itself being invalid against STABLE state (bad values); 409 is
    # the resource's state refusing the transition — wrong lifecycle state,
    # already-processed replay, race, or a standing quota; 429 is only ever a
    # request-rate throttle and always carries Retry-After.
    400: {"model": ErrorOut, "description": "Bad request (invalid values against stable state)"},
    401: {"model": ErrorOut, "description": "Not authenticated"},
    403: {"model": ErrorOut, "description": "Authenticated but not permitted"},
    404: {"model": ErrorOut, "description": "Not found (or belongs to another farm)"},
    409: {
        "model": ErrorOut,
        "description": "Conflict (wrong lifecycle state, replay, race, or standing quota)",
    },
    413: {"model": ErrorOut, "description": "Request body is too large"},
    414: {"model": ErrorOut, "description": "Request target is too long"},
    415: {"model": ErrorOut, "description": "Unsupported media type"},
    422: {
        "description": "Input validation failed or a business rule was rejected",
        "content": {
            "application/json": {
                "schema": {
                    "oneOf": [
                        {"$ref": "#/components/schemas/ErrorOut"},
                        {"$ref": "#/components/schemas/RequestValidationErrorOut"},
                    ]
                }
            }
        },
    },
    429: {
        "model": ErrorOut,
        "description": "Rate limited (request-rate throttle only; always carries Retry-After)",
    },
    500: {"model": ErrorOut, "description": "Internal server error"},
    503: {"model": ErrorOut, "description": "Temporarily unavailable"},
}
