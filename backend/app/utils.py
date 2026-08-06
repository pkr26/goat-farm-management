"""Shared formatting helpers: dates DD-MM-YYYY, money in rupees."""

import calendar
import math
from datetime import UTC, date, datetime


def today() -> date:
    return date.today()


def utcnow() -> datetime:
    """Naive UTC timestamp (SQLite stores naive datetimes). Replaces the
    deprecated datetime.utcnow()."""
    return datetime.now(UTC).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Defensive form parsers — malformed input returns None instead of raising
# ValueError (which would surface as a 500). Callers decide the fallback.
# ---------------------------------------------------------------------------
def parse_date(value: str | None) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_float(value: str | None) -> float | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        result = float(value)
    except ValueError:
        return None
    # "nan"/"inf"/"1e999" parse fine but corrupt data downstream (SQLite
    # stores NaN as NULL → IntegrityError; inf survives and crashes money
    # formatting), so treat non-finite as malformed.
    return result if math.isfinite(result) else None


def finite(value: float | None) -> float | None:
    """Guard for pydantic-coerced `float = Form(...)` fields, which accept
    "nan"/"inf" natively. Returns None for non-finite input."""
    if value is None or not math.isfinite(value):
        return None
    return value


def parse_int(value: str | None) -> int | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


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


def format_date(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.strftime("%d-%m-%Y")
    return str(value)


def _indian_grouping(integer_part: str) -> str:
    """Group digits Indian-style: 1234567 -> 12,34,567."""
    if len(integer_part) <= 3:
        return integer_part
    head, tail = integer_part[:-3], integer_part[-3:]
    groups: list[str] = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join(groups) + "," + tail


def format_money(amount: float | None) -> str:
    if amount is None:
        return "—"
    amount = float(amount)
    if not math.isfinite(amount):  # never 500 a page over corrupt stored data
        return "—"
    negative = amount < 0
    amount = abs(amount)
    if amount == int(amount):
        body = _indian_grouping(str(int(amount)))
    else:
        integer, _, frac = f"{amount:.2f}".partition(".")
        body = _indian_grouping(integer) + "." + frac
    return ("-₹" if negative else "₹") + body
