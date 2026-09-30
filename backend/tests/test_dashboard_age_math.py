"""Exact-value pins for the dashboard's age-in-months arithmetic.

The 2026-09-30 mutation campaign showed every operator in
``dashboard._age_months`` survived: the year/month/day components of
``(y - dob.y) * 12 + (m - dob.m)`` (including ``*`` → ``/`` and ``+`` → ``-``)
plus the day-of-month adjustment. Every age-derived dashboard figure could
compute garbage and still pass the suite, because API-level tests only assert
coarse buckets. These unit tests pin the exact integer for the boundary
cases: across a year boundary, the day before/on/after the month anniversary,
unknown DOB, and a future DOB clamped to zero.
"""

from datetime import date

from app.services.dashboard import _age_months


def test_age_months_same_year_basic() -> None:
    assert _age_months(date(2026, 1, 15), date(2026, 3, 15)) == 2
    assert _age_months(date(2026, 3, 15), date(2026, 3, 15)) == 0


def test_age_months_day_of_month_boundary() -> None:
    dob = date(2026, 1, 15)
    # The day BEFORE the monthly anniversary the age is still 1 month...
    assert _age_months(dob, date(2026, 3, 14)) == 1
    # ...on the anniversary it becomes 2...
    assert _age_months(dob, date(2026, 3, 15)) == 2
    # ...and stays 2 the day after.
    assert _age_months(dob, date(2026, 3, 16)) == 2


def test_age_months_across_year_boundary() -> None:
    dob = date(2025, 11, 20)
    assert _age_months(dob, date(2026, 2, 10)) == 2  # 3 calendar months minus the day adj
    assert _age_months(dob, date(2026, 2, 20)) == 3
    assert _age_months(dob, date(2026, 11, 19)) == 11
    assert _age_months(dob, date(2026, 11, 20)) == 12
    # Leap-year DOB: Feb 29 birthdays age on Mar 1 in non-leap years
    # (Mar 1 .day=1 < 29, so Feb's anniversary has not been reached).
    assert _age_months(date(2024, 2, 29), date(2026, 2, 28)) == 23
    assert _age_months(date(2024, 2, 29), date(2026, 3, 1)) == 24


def test_age_months_none_and_future_dob() -> None:
    assert _age_months(None, date(2026, 3, 15)) is None
    # A DOB in the future (data-entry error) must clamp to zero, not negative.
    assert _age_months(date(2026, 6, 1), date(2026, 1, 1)) == 0
    assert _age_months(date(2027, 1, 1), date(2026, 12, 31)) == 0


def test_age_months_first_of_month_dob() -> None:
    # dob on the 1st: the day adjustment (ref.day < dob.day) never fires.
    assert _age_months(date(2026, 1, 1), date(2026, 12, 31)) == 11
    assert _age_months(date(2026, 1, 1), date(2027, 1, 1)) == 12
    # dob at end of month: adjustment fires for every non-month-end ref.
    assert _age_months(date(2026, 1, 31), date(2026, 3, 30)) == 1
    assert _age_months(date(2026, 1, 31), date(2026, 3, 31)) == 2
