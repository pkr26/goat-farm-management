"""Pure unit tests for the bio-economic simulation engine.

No app/db/conftest imports: the engine is a self-contained pure-Python package
and is tested through its public API plus golden values computed independently
of the engine implementation.

Toy-model derivation used in the golden tests (10 foundation does, all open
and ready to breed in month 1, defaults otherwise, no bucks/purchases)::

    s = 0.95 ** (1/12)          # monthly adult survival
    month 1: 10 x 0.85 = 8.5 conceive -> preg1 = 8.5s, open = 1.5s
    month 2: preg2 = 8.5s^2; open 1.5s bred -> preg1 = 1.275s^2, open = 0.225s^2
    month 6: first batch kids: preg5 = 8.5s^5 -> births = 8.5s^5 x 1.6 x 0.98

First meat sales fall in simulation month 15 with the defaults: conceived in
month 1 -> kidding in month 6 -> male kids reach sale age 9 in month 15.
"""

import pytest
from pydantic import ValidationError

from app.simulation import (
    BREED_PRESETS,
    HerdAssumptions,
    MetaAssumptions,
    SalesAssumptions,
    SimulationAssumptions,
    amortization_schedule,
    bcr,
    class_feed,
    get_preset,
    irr,
    land_requirement_acres,
    monthly_emi,
    monthly_mortality_rate,
    npv,
    payback_month,
    percentile,
    run_monte_carlo,
    run_sensitivity,
    run_simulation,
    weight_at_age,
)

S_ADULT = 0.95 ** (1.0 / 12.0)  # monthly adult survival, default 5% annual mortality
S_KID = 0.90 ** (1.0 / 12.0)  # monthly pre-weaning survival, default 10% annual


def toy_assumptions(**herd_overrides: object) -> SimulationAssumptions:
    """10 open does, no bucks, no purchases, 12-month horizon.

    ``foundation_flock_state="open"`` keeps the golden derivation: all 10 does
    are open and ready to breed in month 1.
    """
    herd = {
        "does": 10,
        "bucks": 0,
        "auto_purchase_bucks": False,
        "foundation_flock_state": "open",
        **herd_overrides,
    }
    return SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(**herd),  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# (a) Golden toy-herd cohort counts
# ---------------------------------------------------------------------------
def test_toy_month1_conception() -> None:
    res = run_simulation(toy_assumptions(), with_break_even=False)
    m1 = res.months[0]
    assert m1.pregnant_does == pytest.approx(8.5 * S_ADULT, abs=1e-6)
    assert m1.open_does == pytest.approx(1.5 * S_ADULT, abs=1e-6)
    assert m1.births == 0.0
    assert m1.deaths == pytest.approx(10.0 * (1.0 - S_ADULT), abs=1e-6)


def test_toy_month2_and_month3_cohorts() -> None:
    res = run_simulation(toy_assumptions(), with_break_even=False)
    m2 = res.months[1]
    assert m2.pregnant_does == pytest.approx((8.5 + 1.275) * S_ADULT**2, abs=1e-6)
    assert m2.open_does == pytest.approx(0.225 * S_ADULT**2, abs=1e-6)
    m3 = res.months[2]
    assert m3.pregnant_does == pytest.approx((8.5 + 1.275 + 0.19125) * S_ADULT**3, abs=1e-6)
    assert m3.open_does == pytest.approx(0.03375 * S_ADULT**3, abs=1e-6)


def test_toy_first_kidding_month6() -> None:
    res = run_simulation(toy_assumptions(), with_break_even=False)
    m6 = res.months[5]
    expected_births = 8.5 * S_ADULT**5 * 1.6 * (1.0 - 0.02)
    assert m6.births == pytest.approx(expected_births, abs=1e-6)
    # Kids born this month take one month of pre-weaning mortality immediately.
    assert m6.f_kids == pytest.approx(expected_births * 0.5 * S_KID, abs=1e-6)
    assert m6.m_kids == pytest.approx(expected_births * 0.5 * S_KID, abs=1e-6)
    # Kidded does move to lactation and take one month of adult mortality.
    assert m6.lactating_does == pytest.approx(8.5 * S_ADULT**6, abs=1e-6)
    # No earlier births.
    assert all(row.births == 0.0 for row in res.months[:5])


# ---------------------------------------------------------------------------
# (b) Mortality rate conversion
# ---------------------------------------------------------------------------
def test_monthly_mortality_rate_conversion() -> None:
    assert monthly_mortality_rate(0.10) == pytest.approx(0.0087416, abs=1e-6)
    assert monthly_mortality_rate(0.0) == 0.0
    # Compounding the monthly rate back over 12 months returns the annual rate.
    mr = monthly_mortality_rate(0.05)
    assert (1.0 - mr) ** 12 == pytest.approx(0.95, abs=1e-12)


# ---------------------------------------------------------------------------
# (c) EMI formula
# ---------------------------------------------------------------------------
def test_monthly_emi_known_value() -> None:
    # P=100000, r=0.12/12=0.01, n=60 -> 2224.44 (standard annuity table value).
    assert monthly_emi(100000.0, 0.12, 60) == pytest.approx(2224.44, abs=0.01)
    assert monthly_emi(1200.0, 0.0, 12) == pytest.approx(100.0)
    assert monthly_emi(0.0, 0.12, 60) == 0.0


def test_amortization_moratorium_is_interest_only() -> None:
    schedule = amortization_schedule(120000.0, 0.12, 24, 12)
    assert len(schedule) == 24
    # Moratorium: interest-only payments, balance unchanged.
    for row in schedule[:12]:
        assert row.payment == pytest.approx(120000.0 * 0.01, abs=1e-9)
        assert row.principal == 0.0
        assert row.closing_balance == pytest.approx(120000.0)
    # EMI phase amortises to zero.
    assert schedule[-1].closing_balance == pytest.approx(0.0, abs=1e-6)
    assert schedule[12].payment == pytest.approx(monthly_emi(120000.0, 0.12, 12), abs=1e-6)


# ---------------------------------------------------------------------------
# (d) NPV / IRR of a known cash-flow series
# ---------------------------------------------------------------------------
def test_npv_known_series() -> None:
    flows = [-1000.0, 400.0, 400.0, 400.0, 400.0]
    times = [0.0, 1.0, 2.0, 3.0, 4.0]
    # 400 x annuity factor(10%, 4) = 400 x 3.1698654 = 1267.946 - 1000.
    assert npv(0.10, flows, times) == pytest.approx(267.9462, abs=1e-3)
    assert bcr(0.10, flows, times) == pytest.approx(1.2679462, abs=1e-6)


def test_irr_known_series() -> None:
    flows = [-1000.0, 400.0, 400.0, 400.0, 400.0]
    times = [0.0, 1.0, 2.0, 3.0, 4.0]
    assert irr(flows, times) == pytest.approx(0.2186, abs=5e-4)
    # No sign change -> no root.
    assert irr([100.0, 100.0], [0.0, 1.0]) is None


def test_payback_month() -> None:
    assert payback_month([-100.0, -50.0, 10.0]) == 2
    assert payback_month([-100.0, -50.0, -1.0]) is None
    assert payback_month([0.0, 5.0]) == 0


# ---------------------------------------------------------------------------
# (e) NABARD-style sanity run: default Osmanabadi, 50 does + 2 bucks, 120 months
# ---------------------------------------------------------------------------
def test_default_run_herd_grows_and_sales_timing() -> None:
    res = run_simulation(SimulationAssumptions(), with_break_even=False)
    months = res.months
    assert len(months) == 120
    assert months[59].total_herd > 52.0  # grew past the foundation herd
    assert 100.0 <= months[59].total_herd <= 400.0  # sane band at 5 years
    # Mixed foundation flock: does in gestation month 5 kid in month 1, and
    # their male kids reach sale age 12 in month 13.
    first_sales = next(row.month for row in months if row.sales_head > 0.0)
    assert first_sales == 13
    assert all(row.sales_head == 0.0 for row in months[:12])


def test_default_run_ebitda_positive_by_year3() -> None:
    """NABARD sanity: the default 50+2 Osmanabadi stall-fed unit is viable at
    the operating level — EBITDA positive from year 3 and in steady state."""
    res = run_simulation(SimulationAssumptions(), with_break_even=False)
    assert res.annual_pl[2].ebitda > 0.0
    for row in res.annual_pl[6:]:  # steady state, years 7-10
        assert row.ebitda > 0.0


def test_default_run_npv_bcr_consistency() -> None:
    res = run_simulation(SimulationAssumptions(), with_break_even=False)
    metrics = res.metrics
    assert (metrics.npv > 0.0) == (metrics.bcr > 1.0)
    assert metrics.project_cost > 0.0
    assert metrics.loan_amount == pytest.approx(0.85 * metrics.project_cost)
    assert metrics.equity == pytest.approx(
        metrics.project_cost - metrics.loan_amount - metrics.subsidy_amount
    )
    assert len(res.annual_pl) == 10
    assert len(metrics.dscr_per_year) == 10
    # Debt service only within the 72-month loan term.
    assert all(row.debt_service == 0.0 for row in res.months[72:])
    assert all(row.debt_service > 0.0 for row in res.months[:72])


# ---------------------------------------------------------------------------
# (f) Monte Carlo reproducibility
# ---------------------------------------------------------------------------
def test_monte_carlo_same_seed_reproducible() -> None:
    a = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=24),
        risk=SimulationAssumptions().risk.model_copy(update={"monte_carlo_runs": 30}),
    )
    first = run_monte_carlo(a)
    second = run_monte_carlo(a)
    assert first.npv_mean == second.npv_mean
    assert first.cash_percentiles.p50 == second.cash_percentiles.p50
    assert first.npv_histogram_counts == second.npv_histogram_counts


def test_monte_carlo_structure_and_spread() -> None:
    a = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=24),
        risk=SimulationAssumptions().risk.model_copy(update={"monte_carlo_runs": 50, "seed": 7}),
    )
    mc = run_monte_carlo(a)
    assert mc.runs == 50
    assert sum(mc.npv_histogram_counts) == 50
    assert len(mc.npv_histogram_edges) == 21
    assert len(mc.herd_percentiles.p50) == 24
    assert mc.npv_std > 0.0  # risk variables actually move the outcome
    assert mc.npv_p5 <= mc.npv_p50 <= mc.npv_p95
    assert 0.0 <= mc.prob_npv_negative <= 1.0
    for month in range(24):
        assert mc.herd_percentiles.p5[month] <= mc.herd_percentiles.p50[month]
        assert mc.herd_percentiles.p50[month] <= mc.herd_percentiles.p95[month]


# ---------------------------------------------------------------------------
# (g) Feed math hand-check
# ---------------------------------------------------------------------------
def test_class_feed_hand_check() -> None:
    feed = SimulationAssumptions().feed
    # 100 maintenance does x 32 kg x 3% BW x 30.44 d = 2922.24 kg DM, no grazing;
    # 5% concentrate DM, roughage remainder split green:dry = 2:1.
    part = class_feed(100.0, 32.0, 0.03, 0.05, feed)
    assert part.dm_kg == pytest.approx(2922.24, abs=1e-9)
    roughage_dm = 0.95 * 2922.24
    assert part.green_kg == pytest.approx(roughage_dm * (2.0 / 3.0) / 0.25, abs=1e-9)
    assert part.dry_kg == pytest.approx(roughage_dm * (1.0 / 3.0) / 0.88, abs=1e-9)
    assert part.concentrate_kg == pytest.approx(0.05 * 2922.24 / 0.90, abs=1e-9)
    # Default prices: green ₹1/kg, dry ₹5/kg, concentrate ₹25/kg (as-fed).
    expected_cost = (
        (roughage_dm * (2.0 / 3.0) / 0.25) * 1.0
        + (roughage_dm * (1.0 / 3.0) / 0.88) * 5.0
        + (0.05 * 2922.24 / 0.90) * 25.0
    )
    assert part.cost == pytest.approx(expected_cost, abs=1e-6)


def test_class_feed_grazing_offsets_purchased_dm() -> None:
    a = SimulationAssumptions()
    a.feed.grazing_dm_fraction = 0.3
    part = class_feed(100.0, 32.0, 0.03, 0.05, a.feed)
    # Grazed DM is free: all purchased quantities shrink by the grazing fraction.
    assert part.green_kg == pytest.approx(0.7 * 0.95 * (2.0 / 3.0) * 2922.24 / 0.25, abs=1e-9)
    assert part.green_dm_kg == pytest.approx(0.7 * 0.95 * (2.0 / 3.0) * 2922.24, abs=1e-9)


def test_fodder_balance_and_land_requirement() -> None:
    assert land_requirement_acres(12000.0, 6.0) == pytest.approx(2.0)
    # 10 acres x 6 t DM/acre/yr / 12 = 5000 kg DM/month cultivated supply.
    a = toy_assumptions()
    a.feed.cultivated_fodder_acres = 10.0
    res = run_simulation(a, with_break_even=False)
    assert res.months[0].fodder_surplus_kg > 4000.0  # small herd, ample supply
    assert res.feed_summary.fodder_deficit_months == 0
    # Default run cultivates nothing: every month is a deficit month.
    default = run_simulation(SimulationAssumptions(), with_break_even=False)
    assert default.feed_summary.fodder_deficit_months == 120
    assert default.feed_summary.land_requirement_acres > 0.0


# ---------------------------------------------------------------------------
# (h) Break-even meat price vs baseline NPV
# ---------------------------------------------------------------------------
def test_break_even_price_consistent_with_baseline_npv() -> None:
    res = run_simulation(SimulationAssumptions())  # includes break-even bisection
    base_price = SimulationAssumptions().sales.meat_price_per_kg
    break_even = res.metrics.break_even_meat_price_per_kg
    assert break_even is not None
    if res.metrics.npv >= 0.0:
        assert break_even <= base_price + 1e-6
    else:
        assert break_even >= base_price - 1e-6


# ---------------------------------------------------------------------------
# Supporting behaviour tests
# ---------------------------------------------------------------------------
def test_assumptions_defaults_valid_and_extra_forbidden() -> None:
    a = SimulationAssumptions()  # must construct: valid Osmanabadi stall-fed run
    assert a.meta.horizon_months == 120
    with pytest.raises(ValidationError):
        SimulationAssumptions(meta={"horizon_months": 120, "bogus": 1})  # type: ignore[dict-item]


def test_weight_curve() -> None:
    g = SimulationAssumptions().growth
    assert weight_at_age(0, g, 32.0) == 2.5
    assert weight_at_age(12, g, 32.0) == 26.5
    assert weight_at_age(24, g, 32.0) == 32.0
    assert weight_at_age(30, g, 32.0) == 32.0
    # Midpoint of the linear approach: 26.5 + 0.5 x (32 - 26.5).
    assert weight_at_age(18, g, 32.0) == pytest.approx(29.25)


def test_breed_presets_and_systems() -> None:
    expected = {
        "osmanabadi",
        "sirohi",
        "barbari",
        "jamunapari",
        "beetal",
        "black_bengal",
        "boer_cross",
    }
    assert set(BREED_PRESETS) == expected
    sirohi = get_preset("Sirohi")
    assert sirohi.sales.lactation_milk_litres == 110.0
    semi = get_preset("osmanabadi", "semi_intensive")
    assert semi.feed.grazing_dm_fraction == 0.3
    assert semi.mortality.adult == 0.06
    with pytest.raises(ValueError, match="unknown breed"):
        get_preset("merino")


def test_eid_uplift_applies_in_eid_month_only() -> None:
    base = SimulationAssumptions(meta=MetaAssumptions(horizon_months=24))
    eid = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=24),
        sales=SalesAssumptions(eid_month=10, eid_price_uplift=0.30),
    )
    res_base = run_simulation(base, with_break_even=False)
    res_eid = run_simulation(eid, with_break_even=False)
    # Start 2026-08: simulation month 15 is calendar month 10 -> uplifted.
    assert res_base.months[14].sales_head > 0.0
    ratio = res_eid.months[14].sales_revenue / res_base.months[14].sales_revenue
    assert ratio == pytest.approx(1.30, abs=1e-9)
    # A non-Eid month is untouched (biology and prices identical).
    assert res_eid.months[16].sales_revenue == pytest.approx(
        res_base.months[16].sales_revenue, rel=1e-9
    )


def test_milk_revenue_hand_check() -> None:
    a = toy_assumptions()
    a.sales.lactation_milk_litres = 110.0
    res = run_simulation(a, with_break_even=False)
    m6 = res.months[5]
    # 110 L over a 3-month lactation at Rs 30/L -> Rs 1100 per lactating doe-month.
    assert m6.milk_revenue == pytest.approx(8.5 * S_ADULT**6 * (110.0 / 3.0) * 30.0, abs=1e-6)
    # Osmanabadi default (0 L/lactation) earns nothing from milk.
    assert run_simulation(toy_assumptions(), with_break_even=False).months[5].milk_revenue == 0.0


def test_max_breeding_does_cap() -> None:
    a = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=24),
        herd=HerdAssumptions(does=10, bucks=0, auto_purchase_bucks=False, max_breeding_does=10),
    )
    res = run_simulation(a, with_break_even=False)
    for row in res.months:
        does = row.open_does + row.pregnant_does + row.lactating_does
        assert does <= 10.0 + 1e-9
    # Surplus female growers are sold as meat instead of joining the pool.
    assert res.months[17].sales_head > 0.0


def test_labour_scales_with_herd_size() -> None:
    res = run_simulation(SimulationAssumptions(), with_break_even=False)
    m12 = res.months[11]
    assert 75.0 < m12.total_herd <= 150.0  # -> 2 labourers at the 75-head threshold
    assert m12.labour_cost == pytest.approx(20000.0)


def test_sensitivity_tornado_sorted() -> None:
    a = SimulationAssumptions(meta=MetaAssumptions(horizon_months=24))
    items = run_sensitivity(a)
    assert len(items) == 8
    assert {item.parameter for item in items} == {
        "meat_price",
        "feed_prices",
        "kid_pre_weaning_mortality",
        "litter_size",
        "conception_rate",
        "sale_age_months",
        "labour_cost",
        "interest_rate",
    }
    impacts = [max(abs(i.delta_npv_low), abs(i.delta_npv_high)) for i in items]
    assert impacts == sorted(impacts, reverse=True)
    # Meat price must dominate: higher price raises NPV, lower price cuts it.
    meat = next(i for i in items if i.parameter == "meat_price")
    assert meat.delta_npv_high > 0.0 > meat.delta_npv_low


def test_run_simulation_optional_blocks() -> None:
    a = SimulationAssumptions(meta=MetaAssumptions(horizon_months=12))
    a.risk.monte_carlo_runs = 10
    res = run_simulation(a, with_break_even=False, with_monte_carlo=True, with_sensitivity=True)
    assert res.monte_carlo is not None
    assert res.monte_carlo.runs == 10
    assert res.sensitivity is not None and len(res.sensitivity) == 8
    plain = run_simulation(a, with_break_even=False)
    assert plain.monte_carlo is None
    assert plain.sensitivity is None


def test_percentile_linear_interpolation() -> None:
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.5) == pytest.approx(2.5)
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.0) == 1.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 1.0) == 4.0
    assert percentile([5.0], 0.95) == 5.0
    assert percentile([], 0.5) == 0.0


def test_stock_cost_and_project_cost_components() -> None:
    # Foundation stock only: stock cost = 50 x 8000 + 2 x 12000 = 424000.
    res = run_simulation(SimulationAssumptions(), with_break_even=False)
    metrics = res.metrics
    shed_plus_equipment = 52 * (4500.0 + 500.0)
    working_capital = metrics.project_cost - shed_plus_equipment - 424000.0
    assert working_capital > 0.0  # 3 months of year-1 average opex
    assert metrics.project_cost == pytest.approx(shed_plus_equipment + 424000.0 + working_capital)
