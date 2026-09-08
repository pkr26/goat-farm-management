"""Calendar-aware price and production helpers for the simulation engine."""

import math
from collections.abc import Sequence

from .assumptions import FeedAssumptions, SalesAssumptions


def annual_growth_multiplier(annual_rate: float, simulation_month: int) -> float:
    """Compound an annual nominal growth rate from month 1's base price."""
    return math.pow(1.0 + annual_rate, (simulation_month - 1) / 12.0)


def calendar_multiplier(values: Sequence[float], calendar_month: int) -> float:
    """Return a January-indexed twelve-month multiplier."""
    return values[calendar_month - 1]


def meat_price_for_month(
    sales: SalesAssumptions,
    *,
    simulation_month: int,
    calendar_month: int,
    shock_multiplier: float = 1.0,
) -> float:
    """Gross live-weight market price for one projection month.

    Explicit festival simulation months replace the legacy recurring Gregorian
    ``eid_month``. This prevents an old saved calendar month from creating extra
    festivals after a user supplies the accurate lunar-calendar dates.
    """
    festival = (
        simulation_month in sales.festival_sale_months
        if sales.festival_sale_months
        else sales.eid_month > 0 and calendar_month == sales.eid_month
    )
    uplift = 1.0 + sales.eid_price_uplift if festival else 1.0
    return (
        sales.meat_price_per_kg
        * calendar_multiplier(sales.monthly_meat_price_multipliers, calendar_month)
        * annual_growth_multiplier(sales.annual_livestock_price_growth_rate, simulation_month)
        * uplift
        * shock_multiplier
    )


def other_revenue_growth(sales: SalesAssumptions, simulation_month: int) -> float:
    """Nominal growth factor for cull, milk and manure prices."""
    return annual_growth_multiplier(sales.annual_livestock_price_growth_rate, simulation_month)


def feed_prices_for_month(
    feed: FeedAssumptions,
    *,
    simulation_month: int,
    calendar_month: int,
    shock_multiplier: float = 1.0,
) -> tuple[float, float, float, float]:
    """Home green, purchased green, dry and concentrate prices for a month."""
    growth = annual_growth_multiplier(feed.annual_feed_price_growth_rate, simulation_month)
    green_factor = calendar_multiplier(feed.monthly_green_price_multipliers, calendar_month)
    return (
        feed.green_price_per_kg * green_factor * growth,
        feed.purchased_green_price_per_kg * green_factor * growth * shock_multiplier,
        feed.dry_price_per_kg
        * calendar_multiplier(feed.monthly_dry_price_multipliers, calendar_month)
        * growth
        * shock_multiplier,
        feed.concentrate_price_per_kg
        * calendar_multiplier(feed.monthly_concentrate_price_multipliers, calendar_month)
        * growth
        * shock_multiplier,
    )


def cultivated_green_supply_kg_dm_for_month(
    feed: FeedAssumptions,
    calendar_month: int,
    *,
    yield_multiplier: float = 1.0,
) -> float:
    """Cultivated green-fodder DM for a month while preserving annual yield.

    The twelve user multipliers are normalized by their sum. This lets users
    describe monsoon seasonality without accidentally changing the stated
    tonnes-DM-per-acre annual yield.
    """
    annual_kg_dm = feed.cultivated_fodder_acres * feed.fodder_yield_t_dm_per_acre_year * 1000.0
    seasonal = feed.monthly_fodder_yield_multipliers
    return annual_kg_dm * seasonal[calendar_month - 1] / sum(seasonal) * yield_multiplier


# Bakrid (Eid al-Adha) Gregorian dates as observed in India. Verified against
# timeanddate.com and Drik Panchang through 2032; 2033-2050 are projections
# (the festival drifts ~10.7-11 days earlier per Gregorian year) and are
# marked tentative — moon sighting moves the day, rarely the month. The
# simulation only needs the month: month resolution makes a ±1-day estimate
# safe. 2041-2045 are from the Fiqh Council of North America calendar /
# datehijri.com (India moon-sighting shifts 2044 to Nov 1 and 2045 to Oct 22
# — same month, so the table keeps the FCNA day); 2046-2050 follow the
# documented ~10.7-11 day annual drift and are month-resolution estimates
# only.
BAKRID_DATES_BY_YEAR: dict[int, tuple[int, int]] = {
    2026: (5, 28),
    2027: (5, 17),
    2028: (5, 5),
    2029: (4, 24),
    2030: (4, 14),
    2031: (4, 3),
    2032: (3, 22),
    2033: (3, 11),  # tentative
    2034: (2, 28),  # tentative
    2035: (2, 17),  # tentative
    2036: (2, 6),  # tentative
    2037: (1, 26),  # tentative
    2038: (1, 15),  # tentative
    2039: (1, 4),  # tentative
    2040: (12, 26),  # tentative
    2041: (12, 4),  # tentative (FCNA calendar)
    2042: (11, 23),  # tentative (FCNA calendar)
    2043: (11, 12),  # tentative (FCNA calendar)
    2044: (10, 31),  # tentative; India moon sighting Nov 1 — same month
    2045: (10, 21),  # tentative; India moon sighting Oct 22 — same month
    2046: (10, 10),  # tentative (drift projection ~10.7 d/yr)
    2047: (9, 29),  # tentative (drift projection)
    2048: (9, 18),  # tentative (drift projection)
    2049: (9, 7),  # tentative (drift projection)
    2050: (8, 28),  # tentative (drift projection)
}


def festival_coverage_last_year() -> int:
    """Last calendar year the embedded Bakrid calendar covers.

    Runs whose horizon extends beyond this year carry no festival uplift in
    the uncovered months (see ``bakrid_festival_months``); the public result
    surfaces a warning so a 2055-ending plan does not silently read as a
    no-Bakrid world.
    """
    return max(BAKRID_DATES_BY_YEAR)


def bakrid_festival_months(start_year_month: str, horizon_months: int) -> list[int]:
    """1-based simulation months whose calendar month contains Bakrid.

    Returns only months inside the embedded calendar rather than a guessed
    extrapolation: a wrong festival month silently mis-times a planned sale,
    which is worse than no festival at all (the public run reports a warning
    when the horizon extends past ``festival_coverage_last_year``).
    """
    try:
        year, month = int(start_year_month[:4]), int(start_year_month[5:7])
    except (ValueError, IndexError):
        return []
    result: list[int] = []
    for festival_year, (festival_month, _day) in sorted(BAKRID_DATES_BY_YEAR.items()):
        simulation_month = (festival_year - year) * 12 + (festival_month - month) + 1
        if 1 <= simulation_month <= horizon_months:
            result.append(simulation_month)
    return sorted(set(result))
