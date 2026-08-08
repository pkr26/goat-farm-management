"""Shared date helpers: "today"/"now" are always UTC (every stored datetime
is naive UTC), plus whole-month date arithmetic for schedules."""

import calendar
from datetime import UTC, date, datetime


def today() -> date:
    """ "Today" is the UTC date: every stored datetime is naive UTC (see
    utcnow), so comparing server-local `date.today()` against them drifts
    by one day whenever the local and UTC dates differ (any non-UTC host).
    """
    return datetime.now(UTC).date()


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
