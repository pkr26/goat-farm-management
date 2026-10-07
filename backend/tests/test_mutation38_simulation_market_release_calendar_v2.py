"""Observable October 2026 release calendar and physical market conservation."""

from datetime import date, timedelta

import pytest

from app.services.dashboard import next_bakrid_date
from app.simulation.assumptions import FeedAssumptions, SalesAssumptions
from app.simulation.market import (
    annual_growth_multiplier,
    bakrid_festival_months,
    bakrid_occurrences,
    cultivated_green_supply_kg_dm_for_month,
    feed_prices_for_month,
    meat_price_for_month,
)

# Compatibility of the released public advisory dates, including projections.
# These fixtures assert the published release, not that tentative future
# dates are observed facts. The product exposes full dates to its operators.
RELEASED_ADVISORY_DATES = (
    "2026-05-28",
    "2027-05-17",
    "2028-05-05",
    "2029-04-24",
    "2030-04-14",
    "2031-04-03",
    "2032-03-22",
    "2033-03-11",
    "2034-02-28",
    "2035-02-17",
    "2036-02-06",
    "2037-01-26",
    "2038-01-15",
    "2039-01-05",
    "2039-12-26",
    "2040-12-15",
    "2041-12-04",
    "2042-11-23",
    "2043-11-12",
    "2044-11-01",
    "2045-10-21",
    "2046-10-10",
    "2047-09-29",
    "2048-09-18",
    "2049-09-07",
    "2050-08-28",
)


def test_public_release_advisory_dates_and_strictly_after_boundaries_remain_complete() -> None:
    try:
        occurrences = bakrid_occurrences()
        actual_dates = [row.observed_on.isoformat() for row in occurrences]
        advisories = [
            next_bakrid_date(date.fromisoformat(text) - timedelta(days=1))
            for text in RELEASED_ADVISORY_DATES
        ]
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as exc:
        pytest.fail(f"The released public calendar must produce valid dated advisories: {exc}")
    assert actual_dates == list(RELEASED_ADVISORY_DATES)
    assert advisories == [date.fromisoformat(text) for text in RELEASED_ADVISORY_DATES]
    for index, occurrence in enumerate(occurrences):
        expected_next = occurrences[index + 1].observed_on if index + 1 < len(occurrences) else None
        assert next_bakrid_date(occurrence.observed_on) == expected_next
        assert occurrence.projected is (occurrence.observed_on.year > 2026)
        assert occurrence.uncertainty_days == 1
        if occurrence.observed_on.year in (2039, 2040):
            assert occurrence.region == "Global crescent projection"
            assert occurrence.source.endswith(f"year-{occurrence.observed_on.year}-ce.pdf")
        else:
            assert occurrence.region == "India"
            assert "future dates require local confirmation" in occurrence.source


@pytest.mark.parametrize(
    "start,horizon,expected",
    [
        ("2039-01", 12, [1, 12]),
        ("2039-12", 13, [1, 13]),
        ("2044-11", 12, [1, 12]),
        ("2050-08", 12, [1]),
        ("2050-09", 12, []),
    ],
)
def test_release_festival_months_follow_the_actual_one_based_calendar_window(
    start: str,
    horizon: int,
    expected: list[int],
) -> None:
    try:
        actual = bakrid_festival_months(start, horizon)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as exc:
        pytest.fail(f"A released calendar window must map to valid simulation months: {exc}")
    assert actual == expected


def test_native_cultivated_supply_conserves_the_declared_annual_dm_for_unscaled_seasons() -> None:
    feed = FeedAssumptions(
        cultivated_fodder_acres=2.5,
        fodder_yield_t_dm_per_acre_year=7.2,
        monthly_fodder_yield_multipliers=[
            1.0,
            2.0,
            3.0,
            4.0,
            2.0,
            1.0,
            3.0,
            4.0,
            3.0,
            2.0,
            1.0,
            2.0,
        ],
    )
    total = sum(cultivated_green_supply_kg_dm_for_month(feed, month) for month in range(1, 13))
    assert total == pytest.approx(18_000.0)
    feed.monthly_fodder_yield_multipliers = [
        value * 7 for value in feed.monthly_fodder_yield_multipliers
    ]
    assert sum(
        cultivated_green_supply_kg_dm_for_month(feed, month) for month in range(1, 13)
    ) == pytest.approx(total)
    assert sum(
        cultivated_green_supply_kg_dm_for_month(feed, month, yield_multiplier=0.4)
        for month in range(1, 13)
    ) == pytest.approx(total * 0.4)


@pytest.mark.parametrize("capacity", [0.0, 123.4])
def test_declared_fodder_storage_capacity_can_be_fully_occupied(capacity: float) -> None:
    try:
        feed = FeedAssumptions(
            initial_fodder_stock_kg_dm=capacity,
            fodder_storage_capacity_kg_dm=capacity,
        )
    except ValueError as exc:
        pytest.fail(f"The supported full storage boundary must admit: {exc}")
    assert feed.initial_fodder_stock_kg_dm == feed.fodder_storage_capacity_kg_dm == capacity


def test_month_one_is_the_nominal_base_and_a_full_year_compounds_once() -> None:
    assert annual_growth_multiplier(0.25, 1) == 1.0
    assert annual_growth_multiplier(0.25, 13) == pytest.approx(1.25)
    assert annual_growth_multiplier(0.25, 25) == pytest.approx(1.25**2)
    assert annual_growth_multiplier(0.0, 120) == 1.0


def test_actual_market_shock_leaves_home_fodder_unchanged_and_prices_all_purchased_feed() -> None:
    feed = FeedAssumptions(
        green_price_per_kg=2.0,
        purchased_green_price_per_kg=3.0,
        dry_price_per_kg=5.0,
        concentrate_price_per_kg=7.0,
        annual_feed_price_growth_rate=0.25,
        monthly_green_price_multipliers=[1.0] * 11 + [1.2],
        monthly_dry_price_multipliers=[1.0] * 11 + [1.4],
        monthly_concentrate_price_multipliers=[1.0] * 11 + [1.6],
    )
    prices = feed_prices_for_month(
        feed, simulation_month=13, calendar_month=12, shock_multiplier=0.5
    )
    assert prices == pytest.approx(
        (2.0 * 1.2 * 1.25, 3.0 * 1.2 * 1.25 * 0.5, 5.0 * 1.4 * 1.25 * 0.5, 7.0 * 1.6 * 1.25 * 0.5)
    )
    sales = SalesAssumptions(
        meat_price_per_kg=400.0,
        eid_month=12,
        eid_price_uplift=0.25,
        festival_sale_months=[],
        monthly_meat_price_multipliers=[1.0] * 12,
    )
    assert meat_price_for_month(sales, simulation_month=1, calendar_month=12) == 400.0
    sales.festival_sale_months = [1]
    assert meat_price_for_month(sales, simulation_month=1, calendar_month=12) == 500.0
