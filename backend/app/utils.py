"""Shared date helpers: farm-local business dates, naive UTC instants, exact
money rounding, and whole-month schedule arithmetic."""

import calendar
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MONEY_QUANTUM = Decimal("0.01")
DEFAULT_BUSINESS_TIMEZONE = "Asia/Kolkata"


def money(value: Decimal | float | int | str) -> Decimal:
    """Convert an API/domain money value to exact paise precision.

    Converting through ``str`` avoids importing the binary float's noise into
    the ledger; ``ROUND_HALF_UP`` matches ordinary financial rounding.
    """
    return Decimal(str(value)).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def allocate_money(
    value: Decimal | float | int | str,
    parts: int,
) -> list[Decimal]:
    """Split a non-negative amount into exact-paise, non-negative shares.

    Rounding ``value / parts`` independently can make the final remainder
    negative (₹0.02 / 4 rounds each ordinary share to ₹0.01). Allocate whole
    paise with ``divmod`` instead: the first ``remainder`` shares receive one
    extra paise and the returned list always sums exactly to ``money(value)``.
    """
    if parts < 1:
        raise ValueError("parts must be positive")
    total = money(value)
    if total < 0:
        raise ValueError("value cannot be negative")
    paise = int(total / MONEY_QUANTUM)
    base, remainder = divmod(paise, parts)
    return [MONEY_QUANTUM * (base + (1 if index < remainder else 0)) for index in range(parts)]


def today(timezone_name: str = DEFAULT_BUSINESS_TIMEZONE) -> date:
    """Return the current business date in an IANA timezone.

    Stored datetimes remain naive UTC; farm work dates are calendar facts and
    must follow the farm's day rather than the server/UTC rollover. Corrupt
    legacy timezone values fail safely to the documented India default.
    """
    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        # ValueError covers malformed keys (empty string, traversal-like
        # paths): a corrupt farm.timezone must fall back like an unknown
        # one instead of 500ing every request the farm makes (P3,
        # 2026-09-20 audit).
        zone = ZoneInfo(DEFAULT_BUSINESS_TIMEZONE)
    return datetime.now(zone).date()


def business_date(value: datetime, timezone_name: str = DEFAULT_BUSINESS_TIMEZONE) -> date:
    """Convert a stored UTC instant to its farm-local calendar date.

    Database timestamps are historically naive UTC. Aware values are also
    accepted so imports and tests cannot accidentally be shifted twice.
    """
    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo(DEFAULT_BUSINESS_TIMEZONE)
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return aware.astimezone(zone).date()


def utcnow() -> datetime:
    """Naive UTC timestamp (all stored datetimes are naive UTC). Replaces the
    deprecated datetime.utcnow()."""
    return datetime.now(UTC).replace(tzinfo=None)


def add_months(d: date, months: float) -> date:
    """Add whole months to a date (clamped to month end). Fractional months
    are rounded down — used only for seeded whole/half month ages."""
    whole_months = int(months)
    m = d.month - 1 + whole_months
    y = d.year + m // 12
    if not 1 <= y <= 9999:  # absurd input ages would explode date() below
        raise ValueError(f"add_months result year {y} out of range")
    m = m % 12 + 1
    day = min(d.day, calendar.monthrange(y, m)[1])
    return date(y, m, day)
