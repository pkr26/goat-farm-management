"""The lactation yield curve: Wood's research-backed shape and the legacy
geometric default, normalised and shared by the engine and the milk planner."""

import pytest

from app.simulation.assumptions import SalesAssumptions
from app.simulation.feed import DAYS_PER_MONTH
from app.simulation.lactation import (
    WOOD_CURVATURE_B,
    curve_from_assumptions,
    monthly_milk_curve,
    wood_daily_yield,
    wood_monthly_weights,
)


def test_geometric_default_is_bit_identical_to_the_legacy_formula() -> None:
    # The engine built this inline before the curve moved to lactation.py;
    # the default must keep every existing scenario's numbers unchanged.
    curve = monthly_milk_curve(2400.0, 10, shape="geometric", persistency_monthly=0.93)
    weights = [0.93**i for i in range(10)]
    total = sum(weights)
    expected = [2400.0 * w / total for w in weights]
    assert curve == expected


def test_wood_curve_rises_then_falls_with_peak_in_the_peak_days_month() -> None:
    curve = monthly_milk_curve(2400.0, 10, shape="wood", peak_day=65.0)
    # 65 days in milk falls in month 3 (61-91 days): the monthly bucket
    # containing the peak carries the most milk.
    assert curve[2] == max(curve)
    # Rising phase: calving month yield is real but below the pre-peak month.
    assert curve[0] < curve[1] < curve[2]
    # Decline after the peak is monotone.
    from itertools import pairwise

    assert all(later < earlier for earlier, later in pairwise(curve[2:]))


def test_curve_sums_exactly_to_the_lactation_total_for_both_shapes() -> None:
    for shape in ("geometric", "wood"):
        curve = monthly_milk_curve(1837.5, 7, shape=shape, peak_day=50.0)
        assert sum(curve) == pytest.approx(1837.5, abs=1e-9)


def test_zero_litres_or_months_give_a_zero_curve() -> None:
    assert monthly_milk_curve(0.0, 10) == [0.0] * 10
    assert monthly_milk_curve(2400.0, 0) == []


def test_wood_daily_yield_peaks_at_the_configured_day() -> None:
    peak_day = 65.0
    samples = [wood_daily_yield(t, peak_day) for t in range(1, 200)]
    best_day = samples.index(max(samples)) + 1
    assert abs(best_day - peak_day) <= 1.0
    # Zero at calving (colostrum-only days are not saleable tank milk).
    assert wood_daily_yield(0.0, peak_day) == 0.0


def test_later_peak_flattens_the_early_curve() -> None:
    early = monthly_milk_curve(2400.0, 10, shape="wood", peak_day=45.0)
    late = monthly_milk_curve(2400.0, 10, shape="wood", peak_day=95.0)
    # A later peak shifts milk out of the first month into the middle.
    assert late[0] < early[0]
    assert sum(late[3:6]) > sum(early[3:6])


def test_murrah_curve_shape_matches_recorded_herd_levels() -> None:
    # 2,400 L over a 305-day lactation with a day-65 Wood peak: peak around
    # 11-12 L/day, average 7.9 L/day (ICAR/NDRI recorded second-lactation
    # Murrah levels; the preset's own market note says 12+ L/day at peak).
    curve = monthly_milk_curve(2400.0, 10, shape="wood", peak_day=65.0)
    peak_daily = max(curve) / DAYS_PER_MONTH
    avg_daily = 2400.0 / 10 / DAYS_PER_MONTH
    assert 10.5 <= peak_daily <= 12.5
    assert avg_daily == pytest.approx(7.88, abs=0.05)
    assert 1.25 <= peak_daily / avg_daily <= 1.50


def test_curve_from_assumptions_follows_the_schema_fields() -> None:
    wood = curve_from_assumptions(
        SalesAssumptions(
            lactation_milk_litres=2000.0,
            milk_curve_shape="wood",
            milk_peak_day=70.0,
        ),
        10,
    )
    assert sum(wood) == pytest.approx(2000.0, abs=1e-9)
    assert wood[2] == max(wood)

    legacy = curve_from_assumptions(
        SalesAssumptions(lactation_milk_litres=2000.0, milk_persistency_monthly=0.9),
        10,
    )
    # The legacy default still peaks in the first month of milk.
    assert legacy[0] == max(legacy)


def test_wood_monthly_weights_use_daily_resolution() -> None:
    weights = wood_monthly_weights(3, 65.0)
    assert len(weights) == 3
    assert all(w > 0.0 for w in weights)
    # Each bucket integrates ~a month of daily yields, so scale is days.
    mid_month_yield = wood_daily_yield(45.0, 65.0)
    assert weights[1] == pytest.approx(mid_month_yield * DAYS_PER_MONTH, rel=0.35)


def test_wood_curvature_is_inside_the_published_buffalo_range() -> None:
    # Published river-buffalo Wood fits: b = 0.465-0.677, c = 0.006-0.010,
    # peak day 57-73. Our fixed b and a 65-day peak give c inside that range.
    c = WOOD_CURVATURE_B / 65.0
    assert 0.465 <= WOOD_CURVATURE_B <= 0.677
    assert 0.006 <= c <= 0.010


def test_engine_uses_the_same_curve_construction() -> None:
    # The engine must build its yield curve through the shared helper, so a
    # scenario and a plan built from the same assumptions agree on biology.
    from app.simulation.assumptions import SimulationAssumptions
    from app.simulation.engine import _run_core

    a = SimulationAssumptions(
        meta={"horizon_months": 24},
        herd={"does": 10, "foundation_flock_state": "open"},
        reproduction={"gestation_months": 5, "lactation_months": 3},
        sales={
            "lactation_milk_litres": 300.0,
            "milk_curve_shape": "wood",
            "milk_price_per_litre": 1.0,
            "annual_milk_price_growth_rate": 0.0,
            "manure_income_per_adult_per_year": 0.0,
            "monthly_milk_yield_multipliers": [1.0] * 12,
        },
    )
    curve = curve_from_assumptions(a.sales, a.reproduction.lactation_months)
    core = _run_core(a)
    milked = [row for row in core.months if row.lactating_does > 0]
    assert milked, "open foundation does must freshen within 24 months"
    for row in milked:
        # Total litres is a stage-weighted sum: bounded by head x min/max
        # stage yield whatever the stage mix is.
        assert row.milk_revenue <= row.lactating_does * max(curve) * 1.0001
        assert row.milk_revenue >= row.lactating_does * min(curve) * 0.9999
