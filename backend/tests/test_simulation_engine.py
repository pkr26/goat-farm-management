"""Pure unit tests for the bio-economic simulation engine.

No app/db/conftest imports: the engine is a self-contained pure-Python package
and is tested through its public API plus golden values computed independently
of the engine implementation.

Toy-model derivation used in the golden tests (10 foundation does + 1 buck —
conception is gated on buck presence — all does open and ready to breed in
month 1, defaults otherwise, no purchases)::

    s = 0.95 ** (1/12)          # monthly adult survival
    month 1: 10 x 0.85 = 8.5 conceive -> preg1 = 8.5s, open = 1.5s
    month 2: preg2 = 8.5s^2; open 1.5s bred -> preg1 = 1.275s^2, open = 0.225s^2
    month 6: first batch kids: preg5 = 8.5s^5 -> births = 8.5s^5 x 1.6 x 0.98

First meat sales fall in simulation month 15 with the defaults: conceived in
month 1 -> kidding in month 6 -> male kids reach sale age 9 in month 15.
"""

import math

import pytest
from pydantic import ValidationError

from app.simulation import (
    BREED_PRESETS,
    CullingAssumptions,
    HerdAssumptions,
    MetaAssumptions,
    MonthlyRow,
    ReproductionAssumptions,
    SalesAssumptions,
    SimulationAssumptions,
    SimulationResult,
    amortization_schedule,
    bcr,
    class_feed,
    get_preset,
    herd_cohorts,
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
from app.simulation.assumptions import HerdEventAssumptions

S_ADULT = 0.95 ** (1.0 / 12.0)  # monthly adult survival, default 5% annual mortality
S_KID = 0.90 ** (1.0 / 12.0)  # monthly pre-weaning survival, default 10% annual


def toy_assumptions(**herd_overrides: object) -> SimulationAssumptions:
    """10 open does + 1 buck, no purchases, 12-month horizon.

    ``foundation_flock_state="open"`` keeps the golden derivation: all 10 does
    are open and ready to breed in month 1. The single buck matters: conception
    is gated on buck presence (a zero-buck herd never conceives), and one buck
    serves any doe count at the full rate in v1, so the golden math is
    unchanged.
    """
    herd = {
        "does": 10,
        "bucks": 1,
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
    assert m1.deaths == pytest.approx(11.0 * (1.0 - S_ADULT), abs=1e-6)  # 10 does + 1 buck


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
    # Month 0 (the equity outflow) is never a payback: a zero-equity project
    # pays back when operating cash first accumulates, here month 1.
    assert payback_month([0.0, 5.0]) == 1
    assert payback_month([0.0, -5.0, -1.0]) is None


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
        herd=HerdAssumptions(does=10, bucks=1, auto_purchase_bucks=False, max_breeding_does=10),
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


# ---------------------------------------------------------------------------
# (i) Scheduled herd events (SimulationAssumptions.events)
# ---------------------------------------------------------------------------
def event_toy(
    events: list[HerdEventAssumptions], horizon: int = 24, **herd_overrides: object
) -> SimulationAssumptions:
    """Toy herd (10 open does) with scheduled events; doe culling disabled so
    the only adult flows at the event month come from the events themselves."""
    a = toy_assumptions(**herd_overrides)
    a.meta.horizon_months = horizon
    a.culling.doe_cull_rate_annual = 0.0
    a.culling.max_doe_age_months = 180  # no max-age cull inside the horizon
    a.events = events
    return a


def _doe_pool(row: MonthlyRow) -> float:
    return row.open_does + row.pregnant_does + row.lactating_does


def test_purchase_event_does_jump_at_event_month() -> None:
    event = HerdEventAssumptions(month=14, kind="purchase", animal_class="doe", count=10)
    res = run_simulation(event_toy([event]), with_break_even=False)
    base = run_simulation(event_toy([]), with_break_even=False)
    m14, m14_base = res.months[13], base.months[13]
    # Purchased does face this month's mortality like the rest of the pool.
    assert _doe_pool(m14) - _doe_pool(m14_base) == pytest.approx(10.0 * S_ADULT, abs=1e-6)
    assert m14.total_herd - m14_base.total_herd == pytest.approx(10.0 * S_ADULT, abs=1e-6)
    # Charged as opex at the default doe purchase price; revenue untouched.
    assert m14.purchases_head == 10.0
    assert m14.purchase_cost == pytest.approx(10.0 * 8000.0)
    assert m14.sales_revenue == pytest.approx(m14_base.sales_revenue, abs=1e-9)
    assert m14.cull_revenue == pytest.approx(m14_base.cull_revenue, abs=1e-9)
    assert any("Purchased 10 doe(s)" in note for note in m14.events)
    # Earlier months are identical to the baseline run.
    assert res.months[12].total_herd == pytest.approx(base.months[12].total_herd, abs=1e-9)


def test_purchase_events_per_class_jump_and_price() -> None:
    g = SimulationAssumptions().growth
    doe_w, buck_w = g.adult_weight_doe_kg, g.adult_weight_buck_kg
    s_weaner = 1.0 - monthly_mortality_rate(0.05)
    s_grower = 1.0 - monthly_mortality_rate(0.04)
    meat = SimulationAssumptions().sales.meat_price_per_kg
    # (animal_class, row accessor, survival, default price per head)
    cases = [
        ("doe", _doe_pool, S_ADULT, 8000.0),
        ("buck", lambda r: r.bucks, S_ADULT, 12000.0),
        ("female_kid", lambda r: r.f_kids, S_KID, weight_at_age(1, g, doe_w) * meat),
        ("male_kid", lambda r: r.m_kids, S_KID, weight_at_age(1, g, doe_w) * meat),
        ("female_weaner", lambda r: r.f_weaners, s_weaner, weight_at_age(4, g, doe_w) * meat),
        ("male_weaner", lambda r: r.m_weaners, s_weaner, weight_at_age(4, g, doe_w) * meat),
        ("female_grower", lambda r: r.f_growers, s_grower, weight_at_age(8, g, doe_w) * meat),
        ("male_grower", lambda r: r.m_growers, s_grower, weight_at_age(8, g, buck_w) * meat),
    ]
    for animal_class, accessor, survival, price in cases:
        event = HerdEventAssumptions.model_validate(
            {"month": 6, "kind": "purchase", "animal_class": animal_class, "count": 5}
        )
        res = run_simulation(event_toy([event], horizon=12), with_break_even=False)
        base = run_simulation(event_toy([], horizon=12), with_break_even=False)
        m6, m6_base = res.months[5], base.months[5]
        assert accessor(m6) - accessor(m6_base) == pytest.approx(5.0 * survival, abs=1e-6), (
            animal_class
        )
        assert m6.purchases_head == 5.0, animal_class
        assert m6.purchase_cost == pytest.approx(5.0 * price), animal_class


def test_purchase_price_per_head_override_used_verbatim() -> None:
    event = HerdEventAssumptions(
        month=3, kind="purchase", animal_class="doe", count=2, price_per_head=5000.0
    )
    res = run_simulation(event_toy([event], horizon=12), with_break_even=False)
    m3 = res.months[2]
    assert m3.purchase_cost == pytest.approx(2.0 * 5000.0)
    assert any("₹5,000/head" in note for note in m3.events)


def test_young_purchase_default_price_is_live_weight_meat_value() -> None:
    # A weaner is placed mid-class (age 4): 10.5 kg x ₹350/kg = ₹3,675/head.
    g = SimulationAssumptions().growth
    expected = 4.0 * weight_at_age(4, g, 32.0) * 350.0
    event = HerdEventAssumptions(month=3, kind="purchase", animal_class="female_weaner", count=4)
    res = run_simulation(event_toy([event], horizon=12), with_break_even=False)
    assert res.months[2].purchase_cost == pytest.approx(expected)
    assert any("₹3,675/head" in note for note in res.months[2].events)


def test_female_grower_purchase_without_grower_chain_joins_doe_pool() -> None:
    # afb == 6: no grower slots exist, so a grower purchase is already
    # breeding-age and goes straight into the doe pool.
    a = event_toy(
        [HerdEventAssumptions(month=3, kind="purchase", animal_class="female_grower", count=3)],
        horizon=12,
    )
    a.reproduction.age_at_first_breeding_months = 6
    base_a = a.model_copy(deep=True)
    base_a.events = []
    res = run_simulation(a, with_break_even=False)
    base = run_simulation(base_a, with_break_even=False)
    m3, m3_base = res.months[2], base.months[2]
    assert m3.f_growers == pytest.approx(m3_base.f_growers, abs=1e-9)
    assert _doe_pool(m3) - _doe_pool(m3_base) == pytest.approx(3.0 * S_ADULT, abs=1e-6)


def test_male_grower_purchase_at_sale_age_resold_immediately() -> None:
    # sale_age == 6: no grower slots, so the purchase is resold at once at the
    # sale-age weight while the purchase is charged at the mid-class weight.
    a = event_toy(
        [HerdEventAssumptions(month=3, kind="purchase", animal_class="male_grower", count=3)],
        horizon=12,
    )
    a.growth.sale_age_months = 6
    res = run_simulation(a, with_break_even=False)
    m3 = res.months[2]
    assert m3.m_growers == 0.0
    assert m3.sales_head == pytest.approx(3.0)
    assert m3.sales_revenue == pytest.approx(3.0 * weight_at_age(6, a.growth, 34.0) * 350.0)
    assert m3.purchase_cost == pytest.approx(3.0 * weight_at_age(5, a.growth, 34.0) * 350.0)


def test_sale_event_does_booked_as_culls() -> None:
    event = HerdEventAssumptions(month=6, kind="sale", animal_class="doe", count=5)
    res = run_simulation(event_toy([event], horizon=12), with_break_even=False)
    base = run_simulation(event_toy([], horizon=12), with_break_even=False)
    m6, m6_base = res.months[5], base.months[5]
    assert _doe_pool(m6) - _doe_pool(m6_base) == pytest.approx(-5.0 * S_ADULT, abs=1e-6)
    # Adult disposals are culls: 5 x 32 kg x ₹180/kg = ₹28,800, no meat sale.
    assert m6.culls_head - m6_base.culls_head == pytest.approx(5.0)
    assert m6.cull_revenue - m6_base.cull_revenue == pytest.approx(5.0 * 180.0 * 32.0)
    assert m6.sales_head == pytest.approx(m6_base.sales_head, abs=1e-9)
    assert m6.sales_revenue == pytest.approx(m6_base.sales_revenue, abs=1e-9)
    assert any("Sold 5 doe(s)" in note for note in m6.events)


def test_sale_event_young_stock_booked_as_meat() -> None:
    event = HerdEventAssumptions(month=2, kind="sale", animal_class="male_weaner", count=5)
    res = run_simulation(event_toy([event], horizon=12, male_weaners=10), with_break_even=False)
    base = run_simulation(event_toy([], horizon=12, male_weaners=10), with_break_even=False)
    m2, m2_base = res.months[1], base.months[1]
    # The remaining weaners graduate to the grower chain this same month and
    # take one month of grower mortality, so the shortfall shows up there.
    s_grower = 1.0 - monthly_mortality_rate(0.04)
    assert m2.m_growers - m2_base.m_growers == pytest.approx(-5.0 * s_grower, abs=1e-6)
    assert m2.total_herd - m2_base.total_herd == pytest.approx(-5.0 * s_grower, abs=1e-6)
    # Young-stock disposals are meat sales: 5 x 10.5 kg x ₹350/kg = ₹18,375.
    assert m2_base.sales_head == 0.0
    assert m2.sales_head == pytest.approx(5.0)
    assert m2.sales_revenue == pytest.approx(5.0 * 10.5 * 350.0)
    assert m2.culls_head == pytest.approx(m2_base.culls_head, abs=1e-9)
    assert m2.cull_revenue == pytest.approx(m2_base.cull_revenue, abs=1e-9)


def test_sale_event_capped_at_available_with_note() -> None:
    # Requesting 1,000 does from a 10-doe herd sells only what is on the
    # ground (~10 x one month of survival) and logs the shortfall.
    event = HerdEventAssumptions(month=2, kind="sale", animal_class="doe", count=1000)
    res = run_simulation(toy_assumptions_with_events(event), with_break_even=False)
    m2 = res.months[1]
    take = 10.0 * S_ADULT
    assert m2.culls_head == pytest.approx(take, abs=1e-6)
    assert m2.cull_revenue == pytest.approx(take * 180.0 * 32.0, abs=1e-6)
    (note,) = m2.events
    assert "only" in note and "of 1000 available" in note
    assert all(row.total_herd >= 0.0 for row in res.months)


def toy_assumptions_with_events(*events: HerdEventAssumptions) -> SimulationAssumptions:
    a = toy_assumptions()
    a.events = list(events)
    return a


def test_event_on_month_one() -> None:
    event = HerdEventAssumptions(month=1, kind="purchase", animal_class="doe", count=4)
    res = run_simulation(event_toy([event], horizon=12), with_break_even=False)
    base = run_simulation(event_toy([], horizon=12), with_break_even=False)
    m1 = res.months[0]
    assert m1.purchases_head == 4.0
    assert m1.purchase_cost == pytest.approx(4.0 * 8000.0)
    assert _doe_pool(m1) - _doe_pool(base.months[0]) == pytest.approx(4.0 * S_ADULT, abs=1e-6)


def test_events_on_empty_herd_no_crash_no_negative() -> None:
    a = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(does=0, bucks=0, auto_purchase_bucks=False),
        events=[HerdEventAssumptions(month=3, kind="sale", animal_class="doe", count=5)],
    )
    res = run_simulation(a, with_break_even=False)
    assert all(row.total_herd == 0.0 for row in res.months)
    assert all(row.sales_head == 0.0 and row.culls_head == 0.0 for row in res.months)
    assert all(row.cull_revenue == 0.0 and row.sales_revenue == 0.0 for row in res.months)
    (note,) = res.months[2].events
    assert "only 0 of 5 available" in note


def test_event_purchase_cost_reaches_annual_pl_and_lowers_npv() -> None:
    event = HerdEventAssumptions(month=14, kind="purchase", animal_class="doe", count=10)
    res = run_simulation(event_toy([event]), with_break_even=False)
    base = run_simulation(event_toy([]), with_break_even=False)
    # Month 14 falls in year 2; the purchase is opex, not project cost.
    assert res.annual_pl[1].stock_purchases - base.annual_pl[1].stock_purchases == pytest.approx(
        80000.0
    )
    assert res.annual_pl[0].stock_purchases == pytest.approx(base.annual_pl[0].stock_purchases)
    assert res.metrics.npv < base.metrics.npv


def test_event_sale_meat_revenue_reaches_annual_pl() -> None:
    # Month 12 is before the first organic meat sale (month 13), so the event
    # sale is the only year-1 meat-revenue delta: 3 x 18.5 kg x ₹350/kg.
    event = HerdEventAssumptions(month=12, kind="sale", animal_class="male_grower", count=3)
    a = SimulationAssumptions(meta=MetaAssumptions(horizon_months=24), events=[event])
    res = run_simulation(a, with_break_even=False)
    base = run_simulation(
        SimulationAssumptions(meta=MetaAssumptions(horizon_months=24)), with_break_even=False
    )
    assert res.months[11].sales_head - base.months[11].sales_head == pytest.approx(3.0)
    assert res.annual_pl[0].meat_revenue - base.annual_pl[0].meat_revenue == pytest.approx(
        3.0 * 18.5 * 350.0
    )


def test_profitable_event_sale_raises_npv() -> None:
    # Selling 5 does at ₹50,000/head (far above the ₹5,760 cull value) brings
    # cash forward and outweighs the lost future production.
    event = HerdEventAssumptions(
        month=14, kind="sale", animal_class="doe", count=5, price_per_head=50000.0
    )
    res = run_simulation(event_toy([event]), with_break_even=False)
    base = run_simulation(event_toy([]), with_break_even=False)
    assert res.months[13].cull_revenue - base.months[13].cull_revenue == pytest.approx(250000.0)
    assert res.annual_pl[1].cull_revenue - base.annual_pl[1].cull_revenue == pytest.approx(250000.0)
    assert res.metrics.npv > base.metrics.npv


def test_event_sale_of_young_stock_gets_eid_uplift() -> None:
    # Start 2026-08: simulation month 15 is calendar month 10.
    def run(eid_month: int) -> float:
        a = SimulationAssumptions(
            meta=MetaAssumptions(horizon_months=24),
            sales=SalesAssumptions(eid_month=eid_month, eid_price_uplift=0.30),
            events=[
                HerdEventAssumptions(month=15, kind="sale", animal_class="male_grower", count=2)
            ],
        )
        res = run_simulation(a, with_break_even=False)
        assert res.months[14].sales_head > 0.0
        return res.months[14].sales_revenue

    assert run(10) / run(0) == pytest.approx(1.30, abs=1e-9)


def test_events_survive_monte_carlo() -> None:
    event = HerdEventAssumptions(month=14, kind="purchase", animal_class="doe", count=10)

    def mc_run(with_event: bool) -> SimulationResult:
        a = event_toy([event] if with_event else [])
        a.risk.monte_carlo_runs = 30
        return run_simulation(a, with_break_even=False, with_monte_carlo=True)

    res, base = mc_run(True), mc_run(False)
    assert res.monte_carlo is not None and base.monte_carlo is not None
    # The p50 herd at the event month reflects the purchase across MC runs.
    diff = res.monte_carlo.herd_percentiles.p50[13] - base.monte_carlo.herd_percentiles.p50[13]
    assert diff > 5.0
    # The deterministic block still carries the event unchanged.
    assert res.months[13].purchases_head == 10.0
    assert any("Purchased 10 doe(s)" in note for note in res.months[13].events)


def test_events_deterministic() -> None:
    events = [
        HerdEventAssumptions(month=6, kind="purchase", animal_class="female_weaner", count=4),
        HerdEventAssumptions(month=14, kind="sale", animal_class="doe", count=2),
    ]
    first = run_simulation(event_toy(events), with_break_even=False)
    second = run_simulation(event_toy(events), with_break_even=False)
    assert first.model_dump() == second.model_dump()


def test_event_validation() -> None:
    def event_payload(**overrides: object) -> dict[str, object]:
        return {
            "month": 3,
            "kind": "purchase",
            "animal_class": "doe",
            "count": 5,
            **overrides,
        }

    # month > horizon is rejected by the model validator; month == horizon ok.
    with pytest.raises(ValidationError, match="exceeds the simulation horizon"):
        SimulationAssumptions(
            meta=MetaAssumptions(horizon_months=24),
            events=[event_payload(month=25)],  # type: ignore[list-item]
        )
    ok = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=24),
        events=[event_payload(month=24)],  # type: ignore[list-item]
    )
    assert ok.events[0].month == 24
    with pytest.raises(ValidationError):
        SimulationAssumptions(events=[event_payload(count=0)])  # type: ignore[list-item]
    with pytest.raises(ValidationError):
        SimulationAssumptions(events=[event_payload(count=-1)])  # type: ignore[list-item]
    with pytest.raises(ValidationError):
        SimulationAssumptions(events=[event_payload(animal_class="camel")])  # type: ignore[list-item]
    with pytest.raises(ValidationError):
        SimulationAssumptions(events=[event_payload(bogus=1)])  # type: ignore[list-item]


def test_project_cost_breakdown_sums_and_ignores_events() -> None:
    event = HerdEventAssumptions(month=14, kind="purchase", animal_class="doe", count=10)
    res = run_simulation(event_toy([event]), with_break_even=False)
    base = run_simulation(event_toy([]), with_break_even=False)
    b = res.project_cost_breakdown
    assert b.shed_cost + b.equipment_cost + b.stock_cost + b.working_capital == pytest.approx(
        res.metrics.project_cost
    )
    # Mid-run purchases are opex: the month-0 project cost is untouched.
    assert res.metrics.project_cost == base.metrics.project_cost
    assert res.project_cost_breakdown == base.project_cost_breakdown


# ---------------------------------------------------------------------------
# (j) Schema floors and cross-field guards
# ---------------------------------------------------------------------------
def test_max_doe_age_floor_36_and_boundary_run() -> None:
    """9-1: max_doe_age_months in [24, 35] used to make the foundation age
    spread (24..min(60, max_doe_age-12)) an empty range → ZeroDivisionError.
    The schema floor is now 36; the boundary value runs clean."""
    for bad in (23, 24, 35):
        with pytest.raises(ValidationError):
            SimulationAssumptions(culling={"max_doe_age_months": bad})  # type: ignore[dict-item]
    a = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=24),
        culling=CullingAssumptions(max_doe_age_months=36),
    )
    res = run_simulation(a, with_break_even=False)
    assert len(res.months) == 24
    assert all(math.isfinite(row.total_herd) for row in res.months)


def test_afb_must_not_exceed_max_doe_age() -> None:
    """9-2: the engine writes doe_ages[afb] for every doe entering the pool;
    afb beyond max_doe_age_months indexed out of range. The per-field floors
    already imply the invariant (afb <= 30 < 36 <= max_doe_age); the
    cross-field validator pins it against future bound changes."""
    # The boundary pair (afb at its ceiling, max age at its floor) validates.
    ok = SimulationAssumptions(
        reproduction=ReproductionAssumptions(age_at_first_breeding_months=30),
        culling=CullingAssumptions(max_doe_age_months=36),
    )
    assert ok.reproduction.age_at_first_breeding_months == 30
    # Mutation bypasses validation; revalidating the mutated state trips the
    # cross-field guard (invoked directly here to pin the invariant itself).
    ok.culling.max_doe_age_months = 24
    with pytest.raises(ValueError, match="age_at_first_breeding_months"):
        ok._breeding_age_within_doe_lifespan()
    # And the boundary pair (with a doe purchase writing doe_ages[30]) runs
    # without the old IndexError.
    a = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=24),
        reproduction=ReproductionAssumptions(age_at_first_breeding_months=30),
        culling=CullingAssumptions(max_doe_age_months=36),
        events=[HerdEventAssumptions(month=3, kind="purchase", animal_class="doe", count=2)],
    )
    res = run_simulation(a, with_break_even=False)
    assert all(math.isfinite(row.total_herd) for row in res.months)


# ---------------------------------------------------------------------------
# (k) Herd-snapshot cohort bucketing (app.simulation.snapshot)
# ---------------------------------------------------------------------------
def test_herd_cohorts_bucketing() -> None:
    animals = [
        ("F", 1),
        ("M", 2),  # kids
        ("F", 4),
        ("M", 3),  # weaners
        ("F", 8),
        ("M", 10),  # growers (6 m up to breeding age)
        ("F", 12),
        ("F", 30),  # does (at/past afb=12)
        ("M", 12),
        ("M", None),  # bucks (at 12 / unknown age -> adult)
        ("F", None),  # unknown-age female -> doe
    ]
    counts = herd_cohorts(animals, doe_adult_age=12)
    assert counts == {
        "does": 3,
        "bucks": 2,
        "f_kids": 1,
        "m_kids": 1,
        "f_weaners": 1,
        "m_weaners": 1,
        "f_growers": 1,
        "m_growers": 1,
    }
    # A later first-breeding age keeps the 12-month female a grower.
    assert herd_cohorts([("F", 12)], doe_adult_age=15)["f_growers"] == 1
