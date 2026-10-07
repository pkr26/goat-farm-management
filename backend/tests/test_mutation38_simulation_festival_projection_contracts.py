"""Dated festival advice preserves covered lunar years and cited projections.

The calendar advertises coverage through 2050 and documents a 354/355-day
lunar cycle with ±1-day uncertainty per event. These tests allow that
uncertainty and exercise the dashboard's actual dated advice helper.
The three exact global projections are from the two sources already cited
by the application (Alhabib 2039 pp5/6; 2040 p6), not local observations:
https://www.al-habib.info/islamic-calendar/global_pdf/global-islamic-calendar-year-2039-ce.pdf
https://www.al-habib.info/islamic-calendar/global_pdf/global-islamic-calendar-year-2040-ce.pdf
"""

from datetime import date, timedelta
from itertools import pairwise

import pytest

from app.services.dashboard import next_bakrid_date
from app.simulation.market import bakrid_occurrences


def _next_advice(reference: date) -> date | None:
    try:
        return next_bakrid_date(reference)
    except ValueError as error:
        pytest.fail(f"Covered festival advice must contain valid Gregorian dates: {error}")


def _dated_advice() -> list[date]:
    values: list[date] = []
    reference = date(2025, 12, 31)
    for _ in range(27):
        upcoming = _next_advice(reference)
        if upcoming is None:
            break
        assert upcoming > reference, "Festival advice must advance beyond the reference date"
        values.append(upcoming)
        reference = upcoming
    return values


def test_advertised_festival_years_have_actual_dated_advice() -> None:
    dates = _dated_advice()
    assert {observed.year for observed in dates} == set(range(2026, 2051))
    assert len(dates) == 26, "2039 contributes two distinct lunar occurrences"
    assert len(set(dates)) == len(dates)
    assert sum(observed.year == 2039 for observed in dates) == 2
    assert _next_advice(date(2050, 12, 31)) is None


def test_festival_advice_follows_lunar_spacing_with_declared_uncertainty() -> None:
    dates = _dated_advice()
    assert len(dates) > 1
    for previous, following in pairwise(dates):
        # 354/355 days, with one day either side of each endpoint.
        assert 352 <= (following - previous).days <= 357, (
            f"A projected lunar festival cannot move a Gregorian month: {previous}→{following}"
        )


@pytest.mark.parametrize(
    "projected",
    [date(2039, 1, 5), date(2039, 12, 26), date(2040, 12, 15)],
    ids=["global-2039-first", "global-2039-second", "global-2040"],
)
def test_cited_global_projection_is_retained_in_dated_advice(projected: date) -> None:
    assert _next_advice(projected - timedelta(days=1)) == projected
    rows = [row for row in bakrid_occurrences() if row.observed_on == projected]
    assert len(rows) == 1
    assert rows[0].projected is True
    assert rows[0].region == "Global crescent projection"
    assert rows[0].uncertainty_days == 1
    assert "al-habib.info" in rows[0].source
    upcoming = _next_advice(projected)
    assert upcoming is not None and upcoming > projected
