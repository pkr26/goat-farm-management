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

First meat sales fall in simulation month 16 with the defaults: conceived in
month 1 -> kidding in month 6 -> male kids reach sale age 10 in month 16
(when no festival hold intervenes; the 12-month toy horizon ends first).
Kid and weaner mortality are whole-phase (3-month class) rates, so the monthly
kid survival below compounds 0.90 over the three kid-class slots.
"""

import hashlib
import json
import math
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.simulation import (
    BREED_PRESETS,
    PRESET_FACTORIES,
    CostsAssumptions,
    CullingAssumptions,
    FeedAssumptions,
    HerdAssumptions,
    MetaAssumptions,
    MonthlyRow,
    MortalityAssumptions,
    ReproductionAssumptions,
    SalesAssumptions,
    SimulationAssumptions,
    SimulationResult,
    amortization_schedule,
    apply_system,
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
from app.simulation import engine as engine_module
from app.simulation.assumptions import MAX_MONEY, HerdEventAssumptions, ParityMultipliers
from app.simulation.engine import (
    _ceil_head_ratio,
    _draw,
    _run_core,
    male_weight_at_age,
    phase_monthly_mortality_rate,
)
from app.simulation.finance import irr_roots
from app.simulation.shocks import MonthlyShockPath

S_ADULT = 0.95 ** (1.0 / 12.0)  # monthly adult survival, default 5% annual mortality
S_KID = 0.85 ** (1.0 / 3.0)  # monthly kid survival: default 15% is a whole-phase (3 m) rate
S_WEANER = 0.95 ** (1.0 / 3.0)  # monthly weaner survival: 5% whole-phase rate


def flatten_market(a: SimulationAssumptions) -> SimulationAssumptions:
    """Remove the market calendar (4% escalation, seasonal curve) in place.

    Mechanics goldens price events at the base price; the calibrated default
    would otherwise multiply every month by a season and growth factor that
    has nothing to do with the mechanism under test.
    """
    a.sales.annual_livestock_price_growth_rate = 0.0
    a.feed.annual_feed_price_growth_rate = 0.0
    a.sales.monthly_meat_price_multipliers = [1.0] * 12
    return a


def toy_assumptions(**herd_overrides: object) -> SimulationAssumptions:
    """10 open does + 1 buck, no purchases, 12-month horizon.

    ``foundation_flock_state="open"`` keeps the golden derivation: all 10 does
    are open and ready to breed in month 1. The single buck matters: conception
    is gated on buck presence (a zero-buck herd never conceives), and one buck
    serves any doe count at the full rate in v1, so the golden math is
    unchanged.

    Nominal price escalation (the calibrated 4%/yr default) is zeroed: these
    are cohort-mechanics goldens, and a growth factor on every ₹ figure would
    make each hand-derivation carry a month-indexed multiplier that has
    nothing to do with the mechanic under test.
    """
    herd = {
        "does": 10,
        "bucks": 1,
        "auto_purchase_bucks": False,
        "foundation_flock_state": "open",
        **herd_overrides,
    }
    a = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(**herd),  # type: ignore[arg-type]
    )
    a.sales.annual_livestock_price_growth_rate = 0.0
    a.feed.annual_feed_price_growth_rate = 0.0
    a.sales.monthly_meat_price_multipliers = [1.0] * 12
    # Flat reproduction policy for the golden cohort math: the parity table
    # and the repeat-breeder cull default (max_services_before_cull = 2, the
    # new model default) each have dedicated tests; the toy goldens pin
    # engine mechanics in isolation, so both policies are pinned off here.
    a.reproduction.parity_multipliers = ParityMultipliers(litter_size=[1.0], conception_rate=[1.0])
    a.reproduction.max_services_before_cull = 0
    return a


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
    # Kids born this month take one month of pre-weaning mortality immediately
    # (kid mortality is a whole-phase 3-month rate, so one month is 1/3 of it).
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


def test_phase_mortality_rate_conversion() -> None:
    """Kid/weaner mortality are whole-phase (3-month class) rates.

    Converting the documented 10% as an ANNUAL rate realized only
    1 - 0.9**(3/12) = 2.6% of the crop; the phase converter must remove
    exactly 10% across the three monthly slots of the kid class."""
    mr = phase_monthly_mortality_rate(0.10, 3)
    assert (1.0 - mr) ** 3 == pytest.approx(0.90, abs=1e-12)
    assert phase_monthly_mortality_rate(0.0, 3) == 0.0
    assert phase_monthly_mortality_rate(0.10, 0) == 0.0
    # The old annual conversion really did lose 10x less than documented.
    old_realized = 1.0 - (1.0 - monthly_mortality_rate(0.10)) ** 3
    assert old_realized == pytest.approx(0.026, abs=5e-4)
    assert old_realized * 3 < 0.10


def test_documented_pre_weaning_rate_removes_ten_percent_of_the_crop() -> None:
    """Engine-level pin of the whole-phase semantics: one 10%-documented
    pre-weaning rate must cost 10% of a crop over its three kid-class months
    (the annual-rate conversion realized only 2.6%)."""
    a = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(
            does=10,
            bucks=1,
            auto_purchase_bucks=False,
            foundation_flock_state="open",
        ),
        reproduction=ReproductionAssumptions(
            conception_rate=1.0,
            gestation_months=1,
            months_open_before_breeding=12,  # no rebreeding inside the horizon
            stillbirth_rate=0.0,
            # Flat parity table: this test pins mortality semantics, and the
            # default parity litter structure would scale the 16-kid crop.
            parity_multipliers=ParityMultipliers(litter_size=[1.0], conception_rate=[1.0]),
        ),
        mortality=MortalityAssumptions(
            kid_pre_weaning=0.10,
            kid_post_weaning=0.0,
            grower=0.0,
            adult=0.0,
        ),
        culling=CullingAssumptions(doe_cull_rate_annual=0.0, max_doe_age_months=180),
    )
    res = run_simulation(a, with_break_even=False)
    born = res.months[1].births
    assert born == pytest.approx(16.0)
    # One kid-class month down: 1/3 of the phase rate.
    kid_survival_monthly = 0.90 ** (1.0 / 3.0)  # the run's explicit 10% phase rate
    assert res.months[1].f_kids + res.months[1].m_kids == pytest.approx(
        16.0 * kid_survival_monthly, abs=1e-9
    )
    # After the third kid-class month the crop is exactly 10% smaller; the
    # following month they are weaners and take no further kid mortality.
    assert res.months[3].f_kids + res.months[3].m_kids == pytest.approx(born * 0.90, abs=1e-9)
    assert res.months[4].f_weaners + res.months[4].m_weaners == pytest.approx(born * 0.90, abs=1e-9)


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
    # BCR takes gross benefits and gross costs, not one net series: the same
    # project is 1267.946 of discounted benefit against 1000 of cost.
    benefits = [0.0, 400.0, 400.0, 400.0, 400.0]
    costs = [1000.0, 0.0, 0.0, 0.0, 0.0]
    assert bcr(0.10, benefits, costs, times) == pytest.approx(1.2679462, abs=1e-6)
    assert bcr(0.10, benefits, [0.0] * 5, times) is None  # no costs -> undefined


def test_irr_known_series() -> None:
    flows = [-1000.0, 400.0, 400.0, 400.0, 400.0]
    times = [0.0, 1.0, 2.0, 3.0, 4.0]
    assert irr(flows, times) == pytest.approx(0.2186, abs=5e-4)
    # No sign change -> no root.
    assert irr([100.0, 100.0], [0.0, 1.0]) is None


def test_irr_is_none_when_the_series_has_several_roots() -> None:
    """A series with more than one sign reversal can cross zero repeatedly.
    Bisecting the whole (-0.99, 10) bracket deterministically kept the
    LEFTMOST root — here -89.16% for a project whose NPV at the 10% discount
    rate is +₹201,590. Several valid rates means no single IRR."""
    flows = [
        -954244.0,
        -390293.0,
        -528292.0,
        1930642.0,
        1572511.0,
        -238266.0,
        -51854.0,
        -865625.0,
        94536.0,
    ]
    times = [float(year) for year in range(len(flows))]
    assert npv(0.10, flows, times) == pytest.approx(201589.88, abs=0.01)  # viable project
    roots = irr_roots(flows, times)
    assert roots == pytest.approx([-0.89156, -0.26447, 0.16333], abs=1e-5)
    for root in roots:
        # Rupees, against flows of order 1e6 — every root really zeroes NPV.
        assert npv(root, flows, times) == pytest.approx(0.0, abs=0.01)
    assert irr(flows, times) is None


def test_irr_finds_close_root_pair_between_old_scan_samples() -> None:
    """Derivative isolation must not hide two crossings in one scan interval."""
    flows = [-1_000_000.0, 3_750_000.0, -4_640_600.0, 1_898_400.0]
    times = [0.0, 1.0, 2.0, 3.0]
    roots = irr_roots(flows, times)
    assert roots == pytest.approx([0.12, 0.13, 0.50], abs=1e-9)
    assert all(npv(root, flows, times) == pytest.approx(0.0, abs=1e-6) for root in roots)
    assert irr(flows, times) is None


def test_irr_does_not_promote_a_near_zero_stationary_point_to_a_root() -> None:
    # ((x - 1/1.12)^2 + 1e-12) * (x - 1/1.5) has only the 50% real
    # crossing.  The shallow minimum around 12% is positive, not an IRR.
    shallow_rate_x = 1.0 / 1.12
    crossing_x = 1.0 / 1.5
    epsilon = 1e-12
    scale = 1_000_000.0
    flows = [
        -(shallow_rate_x**2 * crossing_x + epsilon * crossing_x) * scale,
        (shallow_rate_x**2 + 2.0 * shallow_rate_x * crossing_x + epsilon) * scale,
        -(2.0 * shallow_rate_x + crossing_x) * scale,
        scale,
    ]
    times = [0.0, 1.0, 2.0, 3.0]

    roots = irr_roots(flows, times)

    assert roots == pytest.approx([0.5], abs=1e-8)
    assert npv(roots[0], flows, times) == pytest.approx(0.0, abs=1e-6)
    assert irr(flows, times) == pytest.approx(0.5, abs=1e-8)


def test_irr_retains_a_flat_crossing_when_deciding_ambiguity() -> None:
    # In x = 1 / (1 + rate), these binary-float coefficients are the expanded
    # form of (x - 0.2)^3 * (x - 0.4).  The triple crossing at x ~= 0.2 is a
    # tangent root of the derivative.  Dropping derivative tangencies merges
    # both parent crossings into one same-sign interval and used to report the
    # 150% IRR as unique instead of also finding the crossing near 400%.
    flows = [
        0.003200000000000001,
        -0.056000000000000015,
        0.3600000000000001,
        -1.0,
        1.0,
    ]
    times = [0.0, 1.0, 2.0, 3.0, 4.0]

    roots = irr_roots(flows, times)

    # The decimal solver preserves the supplied binary floats exactly; their
    # second crossing is near, but not identically at, 400%.
    assert roots == pytest.approx([1.5, 4.0], abs=1e-4)
    for root in roots:
        assert npv(root - 1e-3, flows, times) * npv(root + 1e-3, flows, times) < 0.0
    assert irr(flows, times) is None


def test_irr_survives_sign_reversals_with_a_single_root() -> None:
    """Sign reversals alone must not disqualify an IRR — only genuinely
    multiple roots do, otherwise ordinary projects with a lumpy year lose the
    metric entirely."""
    flows = [-1000.0, 600.0, -100.0, 900.0]
    times = [0.0, 1.0, 2.0, 3.0]
    assert len(irr_roots(flows, times)) == 1
    value = irr(flows, times)
    assert value is not None
    assert npv(value, flows, times) == pytest.approx(0.0, abs=1e-6)


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
    # their male kids reach sale age 9 in month 10.
    first_sales = next(row.month for row in months if row.sales_head > 0.0)
    assert first_sales == 10
    assert all(row.sales_head == 0.0 for row in months[:9])


def test_default_run_reports_purchased_fodder_and_honest_operating_result() -> None:
    """A zero-acre stall-fed plan must buy its entire green-fodder requirement.

    The old engine priced zero acres exactly like sufficient land and could
    therefore promise positive steady-state EBITDA without paying for the
    shortfall. The calibrated default now grows 3 acres, so this test pins the
    zero-acre plan explicitly; the identity and physical purchase disclosure
    are what must remain true either way.
    """
    a = SimulationAssumptions()
    a.feed.cultivated_fodder_acres = 0.0
    res = run_simulation(a, with_break_even=False)
    assert res.feed_summary.fodder_deficit_months == 120
    assert sum(res.feed_summary.annual_homegrown_green_kg) == 0.0
    assert sum(res.feed_summary.annual_purchased_green_kg) == pytest.approx(
        sum(res.feed_summary.annual_green_kg)
    )
    for row in res.annual_pl:
        assert row.ebitda == pytest.approx(row.total_revenue - row.total_opex)


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
    # The calibrated default cultivates 3 acres: home-grown green covers part
    # of the need (so fewer than all 120 months are deficit months), and the
    # herd still reports a positive land requirement.
    default = run_simulation(SimulationAssumptions(), with_break_even=False)
    assert default.feed_summary.fodder_deficit_months < 120
    assert sum(default.feed_summary.annual_homegrown_green_kg) > 0.0
    assert default.feed_summary.land_requirement_acres > 0.0


def test_drought_yield_multiplier_and_dm_conversion_reach_cultivation_cost() -> None:
    assumptions = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(does=0, bucks=0, auto_purchase_bucks=False),
        feed=FeedAssumptions(
            cultivated_fodder_acres=1.0,
            fodder_yield_t_dm_per_acre_year=6.0,
            green_dm_pct=0.25,
            green_price_per_kg=2.0,
        ),
    )
    # This is the multiplier emitted by a public drought episode. Calling the
    # core directly keeps the assertion deterministic while exercising the
    # same path used by Monte Carlo.
    shocks = MonthlyShockPath.neutral(12)
    shocks.fodder_yield[0] = 0.5

    first = _run_core(assumptions, shocks).months[0]

    cultivated_dm_kg = 6_000.0 / 12.0 * 0.5
    cultivated_as_fed_kg = cultivated_dm_kg / assumptions.feed.green_dm_pct
    assert first.feed_cost == pytest.approx(
        cultivated_as_fed_kg * assumptions.feed.green_price_per_kg
    )
    assert first.fodder_surplus_kg == pytest.approx(cultivated_dm_kg)


def test_land_requirement_is_a_true_annual_rate_on_ragged_horizons() -> None:
    """The acreage is a per-year rate, so a horizon that is not a whole number
    of years must not divide a partial tail block by a full year. It used to:
    121 months reported 8% LESS land than 120, and 13 months 45% less than 12
    — adding a month made the farm need less fodder ground."""

    def acres(horizon: int) -> float:
        res = run_simulation(
            SimulationAssumptions(meta=MetaAssumptions(horizon_months=horizon)),
            with_break_even=False,
        )
        return res.feed_summary.land_requirement_acres

    # The herd (and its green-DM need) only grows, so the annualised average
    # must rise monotonically with the horizon — never dip at a ragged year.
    horizons = [12, 13, 18, 24, 120, 121]
    values = [acres(h) for h in horizons]
    assert values == sorted(values), dict(zip(horizons, values, strict=True))
    # Whole-year horizons are unchanged: the average is still sum / n_years.
    res = run_simulation(
        SimulationAssumptions(meta=MetaAssumptions(horizon_months=24)), with_break_even=False
    )
    feed = SimulationAssumptions().feed
    expected = (
        sum(res.feed_summary.annual_green_kg)
        * feed.green_dm_pct
        / 2.0
        / (feed.fodder_yield_t_dm_per_acre_year * 1000.0)
    )
    assert res.feed_summary.land_requirement_acres == pytest.approx(expected)


def test_doe_cull_rate_removes_the_documented_annual_fraction() -> None:
    """``doe_cull_rate_annual`` is documented as an annual fraction, so twelve
    monthly applications must remove exactly that fraction. A plain rate/12
    hazard left (1 - r/12)^12 standing: 18.3% for a 20% policy, and only 64.8%
    for the schema maximum of 1.0 ("cull the whole herd this year")."""
    for annual in (0.2, 1.0):
        a = SimulationAssumptions(
            meta=MetaAssumptions(horizon_months=25),
            herd=HerdAssumptions(
                does=100, bucks=0, auto_purchase_bucks=False, foundation_flock_state="open"
            ),
            culling=CullingAssumptions(doe_cull_rate_annual=annual, max_doe_age_months=180),
        )
        a.mortality.adult = 0.0  # isolate culling from every other removal
        a.reproduction.conception_rate = 0.0
        res = run_simulation(a, with_break_even=False)

        def does(index: int, rows: list[MonthlyRow] = res.months) -> float:
            row = rows[index]
            return row.open_does + row.pregnant_does + row.lactating_does

        # The rate cull starts in month 13, so month 12 -> month 24 is one
        # full year of it.
        assert does(23) == pytest.approx(does(11) * (1.0 - annual), abs=1e-9), annual


def test_buck_rotation_skipped_when_auto_purchase_is_off() -> None:
    """Rotation cull-then-restaff is one atomic policy: with
    ``auto_purchase_bucks=False`` there is no replacement path, so the
    rotation must be skipped, not half-applied. Firing the cull alone zeroed
    the sire battery at month 36 and — conception being gated on buck
    presence — permanently sterilized the herd for the rest of the horizon."""
    culling = CullingAssumptions(doe_cull_rate_annual=0.0, max_doe_age_months=180)
    # Repeat-breeder culls off too (max_services_before_cull defaults to 2
    # now): this test isolates the rotation policy, and service-driven culls
    # would pollute the "culls stay 0" expectation below.
    reproduction = ReproductionAssumptions(max_services_before_cull=0)
    manual = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=48),
        herd=HerdAssumptions(does=50, bucks=2, auto_purchase_bucks=False),
        culling=culling,
        reproduction=reproduction,
    )
    res = run_simulation(manual, with_break_even=False)
    m36 = res.months[35]  # default buck_rotation_years=3 -> rotation month 36
    # The battery survives (adult mortality only) and no rotation cull is
    # booked (rate/max-age doe culls are disabled above, so culls stay 0).
    assert m36.bucks == pytest.approx(2.0 * S_ADULT**36, abs=1e-6)
    assert m36.culls_head == pytest.approx(0.0, abs=1e-9)
    assert m36.cull_revenue == pytest.approx(0.0, abs=1e-9)
    # The herd keeps breeding past the rotation month: month-48 kiddings come
    # from month-43 conceptions, which need a live buck after month 36.
    assert res.months[-1].births > 0.0

    # With auto-purchase enabled the rotation is unchanged: the same herd is
    # culled at month 36 (battery topped up monthly, one month of mortality
    # since the last top-up) and re-staffed to the 1:20 ratio (3 sires for
    # 50 does) the same month.
    auto = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=48),
        herd=HerdAssumptions(does=50, bucks=2, auto_purchase_bucks=True),
        culling=culling,
        reproduction=reproduction,
    )
    m36_auto = run_simulation(auto, with_break_even=False).months[35]
    assert m36_auto.culls_head == pytest.approx(3.0 * S_ADULT, abs=1e-6)
    assert m36_auto.bucks == pytest.approx(3.0, abs=1e-9)


def test_scheduled_buck_purchase_at_rotation_month_survives_the_cull() -> None:
    """A buck purchase scheduled for the same month as buck rotation must not
    be swept into that same month's cull-then-restaff: the user's explicit
    purchase would otherwise be destroyed the instant it lands, ending at the
    same buck count auto-purchase alone would have produced. ``does=0`` keeps
    the pre-existing battery's trajectory trivial (mortality only, no
    auto-restock) so the rotation cull's size is exactly known."""
    # Two separately ordered purchases also pin the month's accumulator: an
    # assignment would protect only the second lot from the rotation cull.
    events = [
        HerdEventAssumptions(month=12, kind="purchase", animal_class="buck", count=2),
        HerdEventAssumptions(month=12, kind="purchase", animal_class="buck", count=3),
    ]
    a = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(does=0, bucks=2, auto_purchase_bucks=True),
        culling=CullingAssumptions(buck_rotation_years=1),
        events=events,
    )
    a.sales.annual_livestock_price_growth_rate = 0.0
    res = run_simulation(a, with_break_even=False)
    m12 = res.months[11]
    # Only the pre-existing 2-head battery (12 months of mortality) is
    # rotated out; the 5 freshly bought bucks face just this month's
    # mortality, like any other scheduled purchase, and survive uncalled.
    assert m12.culls_head == pytest.approx(2.0 * S_ADULT**12, abs=1e-6)
    assert m12.bucks == pytest.approx(5.0 * S_ADULT, abs=1e-6)
    assert m12.purchases_head == pytest.approx(5.0)
    # Breeding bucks capitalize: young-stock purchase_cost stays 0 and the
    # breeding-livestock asset account carries the cash.
    assert m12.purchase_cost == pytest.approx(0.0)
    assert m12.breeding_stock_capex == pytest.approx(5.0 * a.herd.buck_purchase_price)


def test_auto_purchase_fills_only_the_missing_part_of_a_sire_battery() -> None:
    assumptions = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(
            does=50,
            bucks=1,
            auto_purchase_bucks=True,
            foundation_flock_state="open",
        ),
        reproduction=ReproductionAssumptions(conception_rate=0.0),
        mortality=MortalityAssumptions(adult=0.0),
        culling=CullingAssumptions(doe_cull_rate_annual=0.0, max_doe_age_months=180),
    )

    first = run_simulation(assumptions, with_break_even=False).months[0]

    assert first.purchases_head == pytest.approx(2.0)
    # Auto-purchased sires capitalize on the breeding-stock account.
    assert first.purchase_cost == pytest.approx(0.0)
    assert first.breeding_stock_capex == pytest.approx(2.0 * assumptions.herd.buck_purchase_price)
    assert first.bucks == pytest.approx(3.0)


def test_post_mortality_sire_top_up_handles_one_or_zero_does() -> None:
    one_doe = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(
            does=1,
            bucks=0,
            auto_purchase_bucks=True,
            foundation_flock_state="open",
        ),
        reproduction=ReproductionAssumptions(conception_rate=0.0),
        mortality=MortalityAssumptions(adult=0.5),
        culling=CullingAssumptions(doe_cull_rate_annual=0.0, max_doe_age_months=180),
    )
    first = run_simulation(one_doe, with_break_even=False).months[0]
    survival = 1.0 - monthly_mortality_rate(one_doe.mortality.adult)

    # One sire is bought before service, then the small mortality loss is
    # topped up after mortality because a positive fractional doe pool still
    # needs one whole sire.
    assert first.purchases_head == pytest.approx(2.0 - survival)
    assert first.bucks == pytest.approx(1.0)

    empty = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(does=0, bucks=0, auto_purchase_bucks=True),
    )
    empty_first = run_simulation(empty, with_break_even=False).months[0]
    assert empty_first.purchases_head == 0.0
    assert empty_first.bucks == 0.0


def test_one_month_open_waiting_period_is_a_valid_reproductive_cycle() -> None:
    assumptions = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        reproduction=ReproductionAssumptions(months_open_before_breeding=1),
    )

    result = run_simulation(assumptions, with_break_even=False)

    assert len(result.months) == 12
    assert all(month.total_herd >= 0.0 for month in result.months)


def test_zero_open_wait_combines_ready_and_returning_lactating_does() -> None:
    assumptions = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(
            does=9,
            bucks=0,
            auto_purchase_bucks=False,
            foundation_flock_state="mixed",
        ),
        reproduction=ReproductionAssumptions(
            months_open_before_breeding=0,
            conception_rate=0.0,
        ),
        mortality=MortalityAssumptions(adult=0.0),
        culling=CullingAssumptions(doe_cull_rate_annual=0.0, max_doe_age_months=180),
    )

    first = run_simulation(assumptions, with_break_even=False).months[0]

    # Nine foundation does are spread over one ready, five pregnant and two
    # lactating slots (the SPEC-aligned lactation pool is 2 months). Month 1
    # combines the ready doe with the one leaving the last lactation slot:
    # 2 x (9 / 8).
    assert first.open_does == pytest.approx(2.25)


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
    # Field-recorded weights to 6 m (12.1 kg @ 3 m weaning, ~17.8 @ 6 m), then
    # the commercial stall-fed finish: males reach the SPEC sale window
    # (8-9 mo / 24-28 kg) on schedule, yearling 25.6 kg.
    assert weight_at_age(3, g, 32.0) == pytest.approx(12.1)
    assert weight_at_age(6, g, 32.0) == pytest.approx(17.8)
    assert weight_at_age(12, g, 32.0) == pytest.approx(25.6)
    # Age 13 is the first interpolated value after the fixed 0..12 table; the
    # linear approach now spans ages 13..adult_weight_age_months (default 24).
    assert weight_at_age(13, g, 32.0) == pytest.approx(25.6 + (32.0 - 25.6) / 12.0)
    assert weight_at_age(24, g, 32.0) == 32.0
    assert weight_at_age(30, g, 32.0) == 32.0
    # Midpoint of the linear approach: 25.6 + 0.5 x (32 - 25.6).
    assert weight_at_age(18, g, 32.0) == pytest.approx(28.8)


def test_male_weight_curve_carries_the_young_male_premium() -> None:
    """Young males run heavier than female contemporaries (~10% goats): the
    premium applies to every young-male age and stops at the adult weight,
    where the buck's own explicit weight takes over."""
    g = SimulationAssumptions().growth
    assert g.young_male_weight_premium == pytest.approx(0.10)
    assert male_weight_at_age(1, g, 42.0) == pytest.approx(6.0 * 1.10)
    assert male_weight_at_age(10, g, 42.0) == pytest.approx(23.5 * 1.10)
    assert male_weight_at_age(23, g, 42.0) == pytest.approx(weight_at_age(23, g, 42.0) * 1.10)
    # At the adult weight age the premium no longer applies.
    assert male_weight_at_age(24, g, 42.0) == pytest.approx(42.0)


def test_draw_is_proportional_bounded_and_total_on_an_empty_pool() -> None:
    pool = [1.0, 3.0]
    assert _draw(pool, 2.0) == pytest.approx(2.0)
    assert pool == pytest.approx([0.5, 1.5])

    # An over-sized request clears the remaining pool without going negative.
    assert _draw(pool, 10.0) == pytest.approx(2.0)
    assert pool == pytest.approx([0.0, 0.0])

    empty: list[float] = []
    assert _draw(empty, 1.0) == 0.0
    assert empty == []


@pytest.mark.parametrize(
    "curve",
    [
        [-1.0] * 13,
        [2.5] * 14,
        [2.5, 4.0, 3.0, *([5.0] * 10)],
    ],
)
def test_weight_curve_rejects_nonphysical_or_wrong_length(curve: list[float]) -> None:
    with pytest.raises(ValidationError):
        SimulationAssumptions.model_validate({"growth": {"weight_by_age_months": curve}})


def test_birth_weight_must_match_age_zero_growth_curve() -> None:
    with pytest.raises(ValidationError, match="birth_weight_kg must equal"):
        SimulationAssumptions.model_validate({"growth": {"birth_weight_kg": 3.0}})


def test_breed_presets_and_systems() -> None:
    # The Osmanabadi default carries the calibrated decelerating field-weight
    # curve (12.1 kg at weaning, 20.5 kg yearling), not a straight line.
    osmanabadi_curve = [
        2.5,
        6.0,
        9.5,
        12.1,
        14.6,
        16.3,
        17.8,
        19.3,
        20.8,
        22.2,
        23.5,
        24.6,
        25.6,
    ]
    expected = {
        "osmanabadi": (
            9_500,
            15_000,
            1.6,
            10,
            2,  # SPEC weaning+rebreed interval (GOAT_PROFILE.weaning_days=60)
            2.5,
            33,
            42,
            osmanabadi_curve,
            9,
            0,
            370,
            0.15,
        ),
        "sirohi": (9_000, 14_000, 1.4, 12, 2, 3.0, 40, 50, 1.18, 9, 110, 370, 0.15),
        "barbari": (7_000, 10_000, 1.8, 10, 2, 2.0, 27, 30, 0.85, 8, 90, 370, 0.15),
        "jamunapari": (
            11_000,
            16_000,
            1.3,
            15,
            6,
            3.5,
            45,
            55,
            1.3,
            9,
            200,
            370,
            0.15,
        ),
        "beetal": (10_000, 15_000, 1.6, 14, 5, 3.2, 40, 46, 1.2, 9, 175, 370, 0.15),
        "black_bengal": (
            4_500,
            6_000,
            2.0,
            9,
            2,  # default lactation pool (3 before the SPEC weaning alignment)
            1.5,
            18,
            20,
            0.55,
            8,
            0,
            370,
            0.12,
        ),
        "boer_cross": (10_000, 18_000, 1.7, 12, 2, 3.0, 40, 50, 1.3, 8, 0, 400, 0.15),
    }

    # The preset registry itself is pinned: a breed added or removed from
    # PRESET_FACTORIES must be a deliberate act, not silent drift.
    assert set(BREED_PRESETS) == set(expected)

    for name, values in expected.items():
        (
            doe_price,
            buck_price,
            litter_size,
            breeding_age,
            lactation_months,
            birth_weight,
            adult_doe_weight,
            adult_buck_weight,
            curve,
            sale_age,
            milk_litres,
            meat_price,
            kid_mortality,
        ) = values
        # Calling the public factory without an argument also pins its promised
        # stall-fed default, independently of get_preset's explicit dispatch.
        preset = PRESET_FACTORIES[name]()
        assert preset.herd.doe_purchase_price == doe_price
        assert preset.herd.buck_purchase_price == buck_price
        assert preset.reproduction.litter_size == litter_size
        assert preset.reproduction.age_at_first_breeding_months == breeding_age
        assert preset.reproduction.lactation_months == lactation_months
        assert preset.growth.birth_weight_kg == birth_weight
        assert preset.growth.adult_weight_doe_kg == adult_doe_weight
        assert preset.growth.adult_weight_buck_kg == adult_buck_weight
        # ``curve`` is either the Osmanabadi table itself (the calibrated
        # default) or a factor scaling the linear base curve to the breed's
        # yearling weight.
        expected_curve = (
            curve
            if isinstance(curve, list)
            else [birth_weight + 2.0 * curve * month for month in range(13)]
        )
        assert preset.growth.weight_by_age_months == pytest.approx(expected_curve)
        assert preset.growth.sale_age_months == sale_age
        assert preset.sales.lactation_milk_litres == milk_litres
        assert preset.sales.meat_price_per_kg == meat_price
        assert preset.mortality.kid_pre_weaning == kid_mortality


def test_preset_name_normalization_accepts_space_and_hyphen() -> None:
    expected = get_preset("black_bengal").model_dump()
    assert get_preset("  Black Bengal  ").model_dump() == expected
    assert get_preset("BLACK-BENGAL").model_dump() == expected


def test_apply_system_is_deep_and_applies_rounded_bounded_mortality_uplifts() -> None:
    base = SimulationAssumptions(
        mortality=MortalityAssumptions(adult=0.1234564, kid_pre_weaning=0.2345674)
    )
    stall_fed = apply_system(base, "stall_fed")
    stall_fed.feed.grazing_dm_fraction = 0.5
    assert base.feed.grazing_dm_fraction == 0.0

    semi_intensive = apply_system(base, "semi_intensive")
    assert base.feed.grazing_dm_fraction == 0.0
    assert base.mortality.adult == 0.1234564
    assert base.mortality.kid_pre_weaning == 0.2345674
    assert semi_intensive.feed.grazing_dm_fraction == 0.3
    assert semi_intensive.mortality.adult == 0.133456
    assert semi_intensive.mortality.kid_pre_weaning == 0.254567

    ceiling = apply_system(
        SimulationAssumptions(mortality=MortalityAssumptions(adult=0.9, kid_pre_weaning=0.9)),
        "semi_intensive",
    )
    assert ceiling.mortality.adult == 0.9
    assert ceiling.mortality.kid_pre_weaning == 0.9


def test_eid_uplift_applies_in_festival_month_only() -> None:
    """Explicit festival months price exactly those months at the uplift and
    replace the legacy recurring ``eid_month``; an explicit empty list
    disables the auto Bakrid calendar, leaving the legacy fallback active."""
    flat = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=24),
        sales=SalesAssumptions(festival_sale_months=[], eid_price_uplift=0.30),
    )
    festival = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=24),
        # A legacy eid_month is still present: explicit festival months must
        # win, so month 3 (calendar October) stays at the base price.
        sales=SalesAssumptions(festival_sale_months=[15], eid_month=10, eid_price_uplift=0.30),
    )
    legacy = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=24),
        sales=SalesAssumptions(festival_sale_months=[], eid_month=10, eid_price_uplift=0.30),
    )
    res_flat = run_simulation(flat, with_break_even=False)
    res_festival = run_simulation(festival, with_break_even=False)
    res_legacy = run_simulation(legacy, with_break_even=False)

    def price_ratio(result: SimulationResult, month: int) -> float:
        base_price = res_flat.months[month - 1].meat_price_per_kg
        return result.months[month - 1].meat_price_per_kg / base_price

    # The festival month itself carries the full uplift on real sales...
    assert res_festival.months[14].sales_head > 0.0
    assert price_ratio(res_festival, 15) == pytest.approx(1.30, rel=1e-9)
    # ...its neighbours do not, and the legacy October months do not either
    # (explicit festival months replace the Gregorian fallback).
    assert price_ratio(res_festival, 14) == pytest.approx(1.0, rel=1e-9)
    assert price_ratio(res_festival, 16) == pytest.approx(1.0, rel=1e-9)
    assert price_ratio(res_festival, 3) == pytest.approx(1.0, rel=1e-9)
    # With the festival list cleared the legacy fallback applies in every
    # calendar October: simulation months 3 and 15 (start 2026-08).
    assert price_ratio(res_legacy, 3) == pytest.approx(1.30, rel=1e-9)
    assert price_ratio(res_legacy, 15) == pytest.approx(1.30, rel=1e-9)
    assert price_ratio(res_legacy, 14) == pytest.approx(1.0, rel=1e-9)


def test_males_finishing_near_a_festival_are_held_and_sold_in_it() -> None:
    """``festival_hold_months`` = 2: males whose sale age lands within two
    months before a festival month are held (still growing, eating and mortal)
    and sold IN the festival month at the festival price — Telangana herds
    are managed to finish bucks into Bakrid."""
    event = HerdEventAssumptions(month=9, kind="purchase", animal_class="male_grower", count=3)

    def run(festival_months: list[int]) -> SimulationResult:
        a = event_toy([event], horizon=24)
        a.sales.festival_sale_months = festival_months
        a.sales.festival_hold_months = 2
        return run_simulation(a, with_break_even=False)

    held = run([12])
    immediate = run([])
    # Grower mortality compounds while the cohort waits (documented: held
    # males face grower mortality like any grower).
    s_grower = 1.0 - monthly_mortality_rate(0.04)
    # The purchased growers finish the chain in month 10. Without a festival
    # they sell that month (one month of grower mortality after landing).
    assert immediate.months[9].sales_head == pytest.approx(3.0 * s_grower)
    assert immediate.months[9].m_growers == pytest.approx(0.0)
    # With a month-12 festival two months ahead they are HELD instead: months
    # 10 and 11 book no sale, and the surviving head (three months of grower
    # mortality: landing, held, held) sells in the festival month itself at
    # the festival price.
    assert held.months[9].sales_head == pytest.approx(0.0)
    assert held.months[10].sales_head == pytest.approx(0.0)
    assert held.months[11].sales_head == pytest.approx(3.0 * s_grower**3)
    assert held.months[11].meat_price_per_kg > immediate.months[9].meat_price_per_kg
    # Held males keep growing while they wait: they sell one month heavier
    # than the sale-age weight of 10, at exactly the festival-inclusive
    # monthly price the run reports.
    g = event_toy([event], horizon=24).growth
    assert held.months[11].sales_revenue == pytest.approx(
        3.0
        * s_grower**3
        * male_weight_at_age(11, g, g.adult_weight_buck_kg)
        * held.months[11].meat_price_per_kg,
        rel=1e-9,
    )


def test_milk_revenue_hand_check() -> None:
    a = toy_assumptions()
    a.sales.lactation_milk_litres = 110.0
    # Flat persistency + zero milk-price growth keeps the curve at the monthly
    # average, so the hand-check below stays litres/month arithmetic.
    a.sales.milk_persistency_monthly = 1.0
    a.sales.annual_milk_price_growth_rate = 0.0
    res = run_simulation(a, with_break_even=False)
    m6 = res.months[5]
    # 110 L over the 2-month lactation pool (the SPEC weaning interval) at
    # Rs 30/L -> Rs 1650 per milking doe-month.
    assert m6.milk_revenue == pytest.approx(m6.lactating_does * (110.0 / 2.0) * 30.0, abs=1e-6)
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
    # The staffing rule is the TNAU/NABARD norm: one labourer per ~50 does
    # WITH progeny — charged on adult breeding females, not standing head, in
    # HALF-attendant units (ceil(2 x does / 60) / 2). Rs 14,000/month
    # escalating at the 5%/yr operating-cost growth rate (month 12 of year 1:
    # 1.05**(11/12)).
    adult_does = m12.open_does + m12.pregnant_does + m12.lactating_does
    labour_units = max(0.5, math.ceil(2.0 * adult_does / 60) / 2.0) if adult_does > 0 else 0.0
    growth = 1.05 ** (11.0 / 12.0)
    assert m12.labour_cost == pytest.approx(labour_units * 14000.0 * growth, rel=1e-9)
    assert 25.0 < adult_does <= 180.0  # -> 0.5-3 attendants at the per-60-doe rule


def test_sensitivity_tornado_sorted() -> None:
    a = SimulationAssumptions(meta=MetaAssumptions(horizon_months=24))
    items = run_sensitivity(a)
    assert len(items) == 9
    assert {item.parameter for item in items} == {
        "meat_price",
        "milk_price",
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


def test_sensitivity_items_report_the_perturbation_they_applied() -> None:
    """The narrative used to label every tornado entry a flat "20%". Two of
    the eight cases are not: sale age moves by whole months, and the high side
    of conception_rate clamps at the schema ceiling of 1.0."""
    a = SimulationAssumptions(meta=MetaAssumptions(horizon_months=24))
    a.reproduction.conception_rate = 0.85  # 0.85 x 1.2 clamps to 1.0, i.e. +17.6%
    by_name = {item.parameter: item for item in run_sensitivity(a)}
    assert by_name["meat_price"].label_low == "-20.0%"
    assert by_name["meat_price"].label_high == "+20.0%"
    # sale_age_months is +/-2 months, never a percentage.
    assert by_name["sale_age_months"].label_low == "-2 month(s)"
    assert by_name["sale_age_months"].label_high == "+2 month(s)"
    # A clamped high side reports the move it really made.
    assert by_name["conception_rate"].label_high == "+17.6%"


def test_sensitivity_high_conception_never_reduces_a_valid_one_hundred_percent_base() -> None:
    assumptions = SimulationAssumptions(meta=MetaAssumptions(horizon_months=12))
    assumptions.reproduction.conception_rate = 1.0
    item = next(
        result for result in run_sensitivity(assumptions) if result.parameter == "conception_rate"
    )
    # The +20% case clamps at the schema/domain ceiling (1.0). The old 0.98
    # clamp mislabeled a two-point reduction as the optimistic scenario.
    assert item.delta_npv_high == pytest.approx(0.0)


def test_sensitivity_clamps_low_litter_and_high_interest_to_schema_bounds() -> None:
    assumptions = SimulationAssumptions(meta=MetaAssumptions(horizon_months=12))
    assumptions.reproduction.litter_size = 0.5
    assumptions.finance.interest_rate_annual = 0.5
    by_name = {item.parameter: item for item in run_sensitivity(assumptions)}
    assert by_name["litter_size"].label_low == "+0.0%"
    assert by_name["litter_size"].delta_npv_low == pytest.approx(0.0)
    assert by_name["interest_rate"].label_high == "+0.0%"
    assert by_name["interest_rate"].delta_npv_high == pytest.approx(0.0)


def test_run_simulation_optional_blocks() -> None:
    a = SimulationAssumptions(meta=MetaAssumptions(horizon_months=12))
    a.risk.monte_carlo_runs = 10
    res = run_simulation(a, with_break_even=False, with_monte_carlo=True, with_sensitivity=True)
    assert res.monte_carlo is not None
    assert res.monte_carlo.runs == 10
    assert res.sensitivity is not None and len(res.sensitivity) == 9
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
    # Foundation stock only: 50 x 9,500 + 2 x 15,000 = 505,000 (the
    # calibrated Telangana purchase prices).
    res = run_simulation(SimulationAssumptions(), with_break_even=False)
    metrics = res.metrics
    stock_cost = 50 * 9_500.0 + 2 * 15_000.0
    assert res.project_cost_breakdown.stock_cost == pytest.approx(stock_cost)
    # Working capital = 12 months x year-1 average monthly opex (feed, vet,
    # labour, insurance, misc, selling) — recomputed independently here, not
    # derived from the reported project cost.
    year1 = res.months[:12]
    avg_monthly_opex = (
        sum(
            row.feed_cost
            + row.vet_cost
            + row.labour_cost
            + row.insurance_cost
            + row.misc_cost
            + row.selling_cost
            for row in year1
        )
        / 12.0
    )
    assert res.project_cost_breakdown.working_capital == pytest.approx(12.0 * avg_monthly_opex)
    assert avg_monthly_opex > 0.0
    shed_plus_equipment = res.project_cost_breakdown.capacity_places * (6000.0 + 500.0)
    assert metrics.project_cost == pytest.approx(
        shed_plus_equipment + stock_cost + 12.0 * avg_monthly_opex
    )


def test_shed_stops_depreciating_at_residual_value_before_terminal_realization() -> None:
    assumptions = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=24),
        costs=CostsAssumptions(
            shed_useful_life_years=1,
            shed_residual_fraction=0.10,
        ),
    )
    assumptions.finance.terminal_asset_realization_fraction = 0.5

    result = run_simulation(assumptions, with_break_even=False)
    shed_cost = result.project_cost_breakdown.shed_cost

    # The 24-month horizon outlives the one-year useful life. Depreciation is
    # capped at 90% of cost, then only half of the 10% closing book value is
    # assumed recoverable.
    assert result.terminal_value_breakdown.shed == pytest.approx(shed_cost * 0.10 * 0.5)


def test_auto_stock_cost_values_every_young_cohort_at_its_tracked_age() -> None:
    assumptions = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(
            does=0,
            bucks=0,
            female_kids=1,
            male_kids=2,
            female_weaners=3,
            male_weaners=4,
            female_growers=5,
            male_growers=6,
            max_breeding_does=0,
            auto_purchase_bucks=False,
        ),
    )
    # Put the grower midpoint beyond month 12 so doe and buck adult weights
    # produce different valuations and every coefficient is observable.
    assumptions.reproduction.age_at_first_breeding_months = 24
    assumptions.growth.sale_age_months = 24
    assumptions.sales.meat_price_per_kg = 100.0

    breakdown = run_simulation(assumptions, with_break_even=False).project_cost_breakdown
    growth = assumptions.growth
    midpoint_age = 15
    expected = 100.0 * (
        3.0 * weight_at_age(1, growth, growth.adult_weight_doe_kg)
        + 7.0 * weight_at_age(4, growth, growth.adult_weight_doe_kg)
        + 5.0 * weight_at_age(midpoint_age, growth, growth.adult_weight_doe_kg)
        + 6.0 * weight_at_age(midpoint_age, growth, growth.adult_weight_buck_kg)
    )
    assert breakdown.stock_cost == pytest.approx(expected)


def test_scheduled_purchase_cost_is_excluded_from_working_capital() -> None:
    base = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(does=0, bucks=0, auto_purchase_bucks=False),
    )
    # This test isolates how the PURCHASE is treated in working capital; the
    # Telangana selling-cost defaults (3% + Rs 100/head) would legitimately
    # enter year-1 average opex through the month-1 sale below.
    base.sales.selling_cost_fraction = 0.0
    base.sales.transport_cost_per_head = 0.0
    round_trip = base.model_copy(deep=True)
    round_trip.events = [
        HerdEventAssumptions(month=1, kind="purchase", animal_class="female_kid", count=100),
        HerdEventAssumptions(month=1, kind="sale", animal_class="female_kid", count=100),
    ]

    baseline = run_simulation(base, with_break_even=False)
    result = run_simulation(round_trip, with_break_even=False)

    assert result.months[0].purchase_cost > 0.0
    assert result.months[0].total_herd == 0.0
    assert result.project_cost_breakdown.working_capital == pytest.approx(
        baseline.project_cost_breakdown.working_capital
    )


def test_nonzero_selling_cost_reaches_annual_opex_and_operating_margin() -> None:
    assumptions = toy_assumptions()
    assumptions.events = [
        HerdEventAssumptions(
            month=1,
            kind="sale",
            animal_class="doe",
            count=1,
            price_per_head=10_000.0,
        )
    ]
    assumptions.sales.selling_cost_fraction = 0.10
    assumptions.sales.transport_cost_per_head = 100.0

    result = run_simulation(assumptions, with_break_even=False)
    annual = result.annual_pl[0]

    assert annual.selling_cost == pytest.approx(sum(month.selling_cost for month in result.months))
    assert annual.selling_cost == pytest.approx(1_100.0)
    assert annual.total_opex == pytest.approx(
        annual.feed_cost
        + annual.vet_cost
        + annual.labour_cost
        + annual.insurance_cost
        + annual.misc_cost
        + annual.selling_cost
        + annual.stock_purchases
    )
    assert result.metrics.operating_margin == pytest.approx(annual.ebitda / annual.total_revenue)

    no_revenue = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(does=0, bucks=0, auto_purchase_bucks=False),
    )
    assert run_simulation(no_revenue, with_break_even=False).metrics.operating_margin is None


def test_dscr_subtracts_cash_tax_from_annual_debt_capacity() -> None:
    assumptions = SimulationAssumptions(meta=MetaAssumptions(horizon_months=12))
    assumptions.herd.male_growers = 250
    assumptions.sales.meat_price_per_kg = 1_000.0
    assumptions.finance.income_tax_rate = 0.25

    result = run_simulation(assumptions, with_break_even=False)
    annual = result.annual_pl[0]

    assert annual.tax > 0.0
    assert annual.debt_service > 0.0
    # The 72-month loan outlives the 12-month horizon, so the final month's
    # debt service carries the balloon (closing balance) — DSCR excludes it
    # (a refinancing event, not an operating-coverage failure) while the
    # annual debt_service row still includes it.
    balloon = result.amortization[11].closing_balance
    assert balloon > 0.0
    operating_debt = annual.debt_service - balloon
    assert operating_debt > 0.0
    assert result.metrics.dscr_per_year == [
        pytest.approx((annual.ebitda - annual.tax) / operating_debt)
    ]
    assert result.metrics.dscr_per_year[0] != pytest.approx(annual.ebitda / annual.debt_service)
    # Cash tax is inside the numerator: a naive EBITDA-only ratio differs.
    assert result.metrics.dscr_per_year[0] != pytest.approx(annual.ebitda / operating_debt)


def test_dscr_excludes_the_end_of_horizon_loan_balloon_but_debt_service_keeps_it() -> None:
    """Term-180 loan on a short horizon: the closing balance charged in the
    final month collapses the year's ratio when wrongly treated as an
    operating payment. DSCR excludes the balloon; debt service, cash flow and
    NPV still carry it."""
    assumptions = SimulationAssumptions(meta=MetaAssumptions(horizon_months=24))
    assumptions.herd.male_growers = 250
    assumptions.sales.meat_price_per_kg = 1_000.0
    assumptions.finance.loan_term_months = 180

    result = run_simulation(assumptions, with_break_even=False)
    final = result.annual_pl[-1]
    balloon = result.amortization[23].closing_balance
    assert balloon > 0.0

    # The balloon is charged as principal in the final month/year...
    assert final.principal > balloon
    assert result.months[23].debt_service > result.months[22].debt_service * 10
    # ...but DSCR covers only the scheduled operating debt service.
    operating_debt = max(final.debt_service - balloon, 0.0)
    assert operating_debt > 0.0
    assert result.metrics.dscr_per_year[-1] == pytest.approx(
        (final.ebitda - final.tax) / operating_debt
    )
    # The exclusion is material: charging the balloon as an operating payment
    # would collapse the final year's coverage (0.9 -> 0.2 in a term-180 run).
    with_balloon = (final.ebitda - final.tax) / final.debt_service
    assert with_balloon < 1.0
    assert result.metrics.dscr_per_year[-1] > 3.0 * with_balloon
    # And NPV/cash feel the balloon: without it the final month's cash flow
    # would be materially higher.
    assert result.months[23].net_cash_flow < result.months[22].net_cash_flow


# ---------------------------------------------------------------------------
# (i) Scheduled herd events (SimulationAssumptions.events)
# ---------------------------------------------------------------------------
def event_toy(
    events: list[HerdEventAssumptions], horizon: int = 24, **herd_overrides: object
) -> SimulationAssumptions:
    """Toy herd (10 open does) with scheduled events; doe culling disabled so
    the only adult flows at the event month come from the events themselves."""
    a = toy_assumptions(**herd_overrides)
    a = pin_legacy_growth(a)
    a.meta.horizon_months = horizon
    a.culling.doe_cull_rate_annual = 0.0
    a.culling.max_doe_age_months = 180  # no max-age cull inside the horizon
    a.events = events
    return a


def pin_legacy_growth(a: SimulationAssumptions) -> SimulationAssumptions:
    """Force the pre-sale-window-alignment growth defaults (sale age 10 on the
    field-average curve). The event/hold/shock mechanics tests below pin
    exact weights and timings against these numbers; the SPEC-window
    alignment (9 months, stall-fed finish curve) is covered by its own tests."""
    a.growth.sale_age_months = 10
    a.growth.weight_by_age_months = [
        2.5,
        6.0,
        9.5,
        12.1,
        14.1,
        15.8,
        17.1,
        18.2,
        19.0,
        19.6,
        20.0,
        20.3,
        20.5,
    ]
    a.reproduction.age_at_first_breeding_months = 12
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
    # Charged in the purchase month at the default doe price — as
    # capitalized breeding-stock cash, not young-stock opex; revenue untouched.
    assert m14.purchases_head == 10.0
    assert m14.purchase_cost == pytest.approx(0.0)
    assert m14.breeding_stock_capex == pytest.approx(
        10.0 * SimulationAssumptions().herd.doe_purchase_price
    )
    assert m14.sales_revenue == pytest.approx(m14_base.sales_revenue, abs=1e-9)
    assert m14.cull_revenue == pytest.approx(m14_base.cull_revenue, abs=1e-9)
    assert any("Purchased 10 doe(s)" in note for note in m14.events)
    # Earlier months are identical to the baseline run.
    assert res.months[12].total_herd == pytest.approx(base.months[12].total_herd, abs=1e-9)


def test_purchase_events_per_class_jump_and_price() -> None:
    g = pin_legacy_growth(SimulationAssumptions()).growth
    doe_w, buck_w = g.adult_weight_doe_kg, g.adult_weight_buck_kg
    s_weaner = S_WEANER  # whole-phase 5% weaner rate, one monthly slot
    s_grower = 1.0 - monthly_mortality_rate(0.04)
    meat = SimulationAssumptions().sales.meat_price_per_kg
    # (animal_class, row accessor, survival, default price per head)
    base = SimulationAssumptions()
    cases = [
        ("doe", _doe_pool, S_ADULT, base.herd.doe_purchase_price),
        ("buck", lambda r: r.bucks, S_ADULT, base.herd.buck_purchase_price),
        ("female_kid", lambda r: r.f_kids, S_KID, weight_at_age(1, g, doe_w) * meat),
        # Young males are valued on the male curve (female table x the ~10%
        # young-male weight premium).
        ("male_kid", lambda r: r.m_kids, S_KID, male_weight_at_age(1, g, buck_w) * meat),
        ("female_weaner", lambda r: r.f_weaners, s_weaner, weight_at_age(4, g, doe_w) * meat),
        ("male_weaner", lambda r: r.m_weaners, s_weaner, male_weight_at_age(4, g, buck_w) * meat),
        # Mid-class is the slot the engine actually fills: a chain covering
        # ages 6..11 is filled at index 3, i.e. age 9 — not the age-8
        # midpoint of the class bounds the valuation constant used to use.
        # The female chain spans 6..afb-1 (6 slots, midpoint age 9); the male
        # chain spans 6..sale_age-1 (4 slots at the calibrated age-10 sale,
        # midpoint age 8).
        ("female_grower", lambda r: r.f_growers, s_grower, weight_at_age(9, g, doe_w) * meat),
        ("male_grower", lambda r: r.m_growers, s_grower, male_weight_at_age(8, g, buck_w) * meat),
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
        # Breeding classes (doe/buck) capitalize; young stock stays opex.
        if animal_class in ("doe", "buck"):
            assert m6.purchase_cost == pytest.approx(0.0), animal_class
            assert m6.breeding_stock_capex == pytest.approx(5.0 * price), animal_class
        else:
            assert m6.purchase_cost == pytest.approx(5.0 * price), animal_class
            assert m6.breeding_stock_capex == pytest.approx(0.0), animal_class


@pytest.mark.parametrize(
    ("animal_class", "row_field"),
    [
        ("female_kid", "f_kids"),
        ("male_kid", "m_kids"),
        ("female_weaner", "f_weaners"),
        ("male_weaner", "m_weaners"),
        ("female_grower", "f_growers"),
        ("male_grower", "m_growers"),
    ],
)
def test_multiple_young_purchases_accumulate_inventory_cost_and_logs(
    animal_class: str, row_field: str
) -> None:
    assumptions = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(
            does=0,
            bucks=0,
            max_breeding_does=0,
            auto_purchase_bucks=False,
        ),
        mortality=MortalityAssumptions(
            kid_pre_weaning=0.0,
            kid_post_weaning=0.0,
            grower=0.0,
            adult=0.0,
        ),
        events=[
            HerdEventAssumptions(
                month=1,
                kind="purchase",
                animal_class=animal_class,  # type: ignore[arg-type]
                count=2,
                price_per_head=100.0,
            ),
            HerdEventAssumptions(
                month=1,
                kind="purchase",
                animal_class=animal_class,  # type: ignore[arg-type]
                count=3,
                price_per_head=200.0,
            ),
        ],
    )

    first = run_simulation(assumptions, with_break_even=False).months[0]
    label = animal_class.replace("_", " ")

    assert getattr(first, row_field) == pytest.approx(5.0)
    assert first.purchases_head == pytest.approx(5.0)
    assert first.purchase_cost == pytest.approx(800.0)
    assert first.events == [
        f"Purchased 2 {label}(s) at ₹100/head (₹200)",
        f"Purchased 3 {label}(s) at ₹200/head (₹600)",
    ]


def test_purchase_price_per_head_override_used_verbatim() -> None:
    event = HerdEventAssumptions(
        month=3, kind="purchase", animal_class="doe", count=2, price_per_head=5000.0
    )
    res = run_simulation(event_toy([event], horizon=12), with_break_even=False)
    m3 = res.months[2]
    # Doe purchases carry the override price on the capitalized account.
    assert m3.purchase_cost == pytest.approx(0.0)
    assert m3.breeding_stock_capex == pytest.approx(2.0 * 5000.0)
    assert any("₹5,000/head" in note for note in m3.events)


def test_explicit_zero_event_prices_do_not_fall_back_to_defaults() -> None:
    assumptions = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(does=0, bucks=0, auto_purchase_bucks=False),
        events=[
            HerdEventAssumptions(
                month=1,
                kind="purchase",
                animal_class="doe",
                count=1,
                price_per_head=0.0,
            ),
            HerdEventAssumptions(
                month=1,
                kind="sale",
                animal_class="doe",
                count=1,
                price_per_head=0.0,
            ),
        ],
    )

    month = run_simulation(assumptions, with_break_even=False).months[0]

    assert month.purchases_head == pytest.approx(1.0)
    assert month.culls_head == pytest.approx(1.0)
    assert month.purchase_cost == 0.0
    assert month.cull_revenue == 0.0
    assert all("₹0/head" in note for note in month.events)


def test_young_purchase_default_price_is_live_weight_meat_value() -> None:
    # A weaner is placed mid-class (age 4): 14.1 kg x Rs 370/kg = Rs 5,217/head
    # (legacy-curve weights — this test pins the placement/valuation rule).
    g = pin_legacy_growth(SimulationAssumptions()).growth
    expected = (
        4.0
        * weight_at_age(4, g, g.adult_weight_doe_kg)
        * SimulationAssumptions().sales.meat_price_per_kg
    )
    event = HerdEventAssumptions(month=3, kind="purchase", animal_class="female_weaner", count=4)
    res = run_simulation(event_toy([event], horizon=12), with_break_even=False)
    assert res.months[2].purchase_cost == pytest.approx(expected)
    assert any("₹5,217/head" in note for note in res.months[2].events)


@pytest.mark.parametrize(
    ("animal_class", "adult_weight_field"),
    [
        ("female_grower", "adult_weight_doe_kg"),
        ("male_grower", "adult_weight_buck_kg"),
    ],
)
def test_long_chain_grower_purchase_uses_sex_weight_and_midpoint_slot(
    animal_class: str, adult_weight_field: str
) -> None:
    assumptions = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(
            does=0,
            bucks=0,
            max_breeding_does=0,
            auto_purchase_bucks=False,
        ),
        mortality=MortalityAssumptions(
            kid_pre_weaning=0.0,
            kid_post_weaning=0.0,
            grower=0.0,
            adult=0.0,
        ),
        events=[
            HerdEventAssumptions(
                month=1,
                kind="purchase",
                animal_class=animal_class,  # type: ignore[arg-type]
                count=2,
            )
        ],
    )
    assumptions.reproduction.age_at_first_breeding_months = 24
    assumptions.growth.sale_age_months = 24
    flatten_market(assumptions)
    # The mechanic under test is purchase pricing at the mid-chain slot; the
    # default festival hold would move the male's graduation sale into a
    # festival month, so festival pricing is switched off here.
    assumptions.sales.festival_sale_months = []

    result = run_simulation(assumptions, with_break_even=False)
    midpoint_age = 15
    weight_fn = male_weight_at_age if animal_class == "male_grower" else weight_at_age
    expected_cost = (
        2.0
        * weight_fn(
            midpoint_age,
            assumptions.growth,
            getattr(assumptions.growth, adult_weight_field),
        )
        * assumptions.sales.meat_price_per_kg
    )

    assert result.months[0].purchase_cost == pytest.approx(expected_cost)
    if animal_class == "male_grower":
        assert all(month.sales_head == 0.0 for month in result.months[:8])
        assert result.months[8].sales_head == pytest.approx(2.0)


def test_female_grower_purchase_without_chain_still_obeys_retention() -> None:
    def deltas(first_breeding_age: int) -> tuple[float, float, float, float]:
        assumptions = event_toy(
            [
                HerdEventAssumptions(
                    month=3,
                    kind="purchase",
                    animal_class="female_grower",
                    count=3,
                )
            ],
            horizon=12,
        )
        assumptions.reproduction.age_at_first_breeding_months = first_breeding_age
        assumptions.herd.female_retention_fraction = 0.0
        baseline_assumptions = assumptions.model_copy(deep=True)
        baseline_assumptions.events = []

        month = run_simulation(assumptions, with_break_even=False).months[2]
        baseline = run_simulation(baseline_assumptions, with_break_even=False).months[2]
        return (
            _doe_pool(month) - _doe_pool(baseline),
            month.sales_head - baseline.sales_head,
            month.purchase_cost - baseline.purchase_cost,
            month.sales_revenue - baseline.sales_revenue,
        )

    base = pin_legacy_growth(SimulationAssumptions())
    expected_value = (
        3.0
        * weight_at_age(6, base.growth, base.growth.adult_weight_doe_kg)
        * base.sales.meat_price_per_kg
    )
    without_chain = deltas(6)

    assert without_chain == pytest.approx((0.0, 3.0, expected_value, expected_value))
    # The empty chain preserves the one-slot afb=7 retention/head-flow policy.
    assert without_chain[:2] == pytest.approx(deltas(7)[:2])


def test_one_doe_cap_limits_empty_chain_female_grower_graduation() -> None:
    assumptions = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(
            does=0,
            bucks=0,
            female_growers=2,
            female_retention_fraction=1.0,
            max_breeding_does=1,
            auto_purchase_bucks=False,
        ),
        reproduction=ReproductionAssumptions(
            age_at_first_breeding_months=6,
            conception_rate=0.0,
        ),
        mortality=MortalityAssumptions(
            kid_pre_weaning=0.0,
            kid_post_weaning=0.0,
            grower=0.0,
            adult=0.0,
        ),
        culling=CullingAssumptions(doe_cull_rate_annual=0.0, max_doe_age_months=180),
    )

    first = run_simulation(assumptions, with_break_even=False).months[0]

    assert _doe_pool(first) == pytest.approx(1.0)
    assert first.sales_head == pytest.approx(1.0)


def test_female_grower_purchase_at_filled_doe_cap_is_sold_as_surplus() -> None:
    assumptions = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(
            does=5,
            bucks=0,
            female_retention_fraction=1.0,
            max_breeding_does=5,
            auto_purchase_bucks=False,
            foundation_flock_state="open",
        ),
        reproduction=ReproductionAssumptions(
            age_at_first_breeding_months=6,
            conception_rate=0.0,
        ),
        mortality=MortalityAssumptions(
            kid_pre_weaning=0.0,
            kid_post_weaning=0.0,
            grower=0.0,
            adult=0.0,
        ),
        culling=CullingAssumptions(doe_cull_rate_annual=0.0, max_doe_age_months=180),
        events=[
            HerdEventAssumptions(
                month=1,
                kind="purchase",
                animal_class="female_grower",
                count=3,
            )
        ],
    )

    first = run_simulation(assumptions, with_break_even=False).months[0]

    assert _doe_pool(first) == pytest.approx(5.0)
    assert first.purchases_head == pytest.approx(3.0)
    assert first.sales_head == pytest.approx(3.0)
    assert first.f_growers == 0.0


def test_ordered_doe_sale_opens_cap_space_for_boundary_grower_retention() -> None:
    assumptions = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(
            does=5,
            bucks=0,
            female_retention_fraction=1.0,
            max_breeding_does=5,
            auto_purchase_bucks=False,
            foundation_flock_state="open",
        ),
        reproduction=ReproductionAssumptions(
            age_at_first_breeding_months=6,
            conception_rate=0.0,
        ),
        mortality=MortalityAssumptions(
            kid_pre_weaning=0.0,
            kid_post_weaning=0.0,
            grower=0.0,
            adult=0.0,
        ),
        culling=CullingAssumptions(doe_cull_rate_annual=0.0, max_doe_age_months=180),
        events=[
            HerdEventAssumptions(
                month=1,
                kind="purchase",
                animal_class="female_grower",
                count=3,
            ),
            HerdEventAssumptions(
                month=1,
                kind="sale",
                animal_class="doe",
                count=2,
            ),
        ],
    )

    first = run_simulation(assumptions, with_break_even=False).months[0]

    # The adult sale removes two of the original does. Graduation then fills
    # those two cap places and sells only the third grower as young stock.
    assert _doe_pool(first) == pytest.approx(5.0)
    assert first.culls_head == pytest.approx(2.0)
    assert first.sales_head == pytest.approx(1.0)


def test_adult_purchase_and_grower_graduation_both_enter_doe_age_tracking() -> None:
    assumptions = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=24),
        herd=HerdAssumptions(
            does=0,
            bucks=0,
            female_growers=1,
            female_retention_fraction=1.0,
            max_breeding_does=0,
            auto_purchase_bucks=False,
        ),
        reproduction=ReproductionAssumptions(
            age_at_first_breeding_months=24,
            conception_rate=0.0,
        ),
        mortality=MortalityAssumptions(
            kid_pre_weaning=0.0,
            kid_post_weaning=0.0,
            grower=0.0,
            adult=0.0,
        ),
        culling=CullingAssumptions(doe_cull_rate_annual=0.0, max_doe_age_months=36),
        events=[
            # The starting grower reaches age 24 in month 9. Bought-in adults
            # are also entered at age 24 under this minimum max-age setting;
            # both lots must accumulate in the same parallel age slot.
            HerdEventAssumptions(
                month=9,
                kind="purchase",
                animal_class="doe",
                count=2,
            )
        ],
    )
    # Pin the purchase-age window to the same synchronized 24 months (the
    # calibrated default spreads bought-in does over 18-42 months).
    assumptions.herd.foundation_doe_age_min_months = 24
    assumptions.herd.foundation_doe_age_max_months = 24

    result = run_simulation(assumptions, with_break_even=False)

    assert _doe_pool(result.months[8]) == pytest.approx(3.0)
    assert result.months[20].culls_head == pytest.approx(3.0)
    assert _doe_pool(result.months[20]) == pytest.approx(0.0)


def test_male_grower_purchase_at_sale_age_preserves_mass_and_value() -> None:
    # sale_age == 6: no grower slots exist because the purchased animals are
    # already market-ready. They graduate and sell in the event month at the
    # same age-based value used for the purchase, rather than disappearing.
    a = event_toy(
        [HerdEventAssumptions(month=3, kind="purchase", animal_class="male_grower", count=3)],
        horizon=12,
    )
    a.growth.sale_age_months = 6
    base_a = a.model_copy(deep=True)
    base_a.events = []
    res = run_simulation(a, with_break_even=False)
    base = run_simulation(base_a, with_break_even=False)
    m3, m3_base = res.months[2], base.months[2]
    assert m3.m_growers == 0.0
    assert m3.sales_head - m3_base.sales_head == pytest.approx(3.0)
    assert m3.purchases_head == pytest.approx(3.0)
    expected_value = (
        3.0
        * male_weight_at_age(6, a.growth, a.growth.adult_weight_buck_kg)
        * a.sales.meat_price_per_kg
    )
    assert m3.purchase_cost == pytest.approx(expected_value)
    assert m3.sales_revenue - m3_base.sales_revenue == pytest.approx(expected_value)


@pytest.mark.parametrize("animal_class", ["female_grower", "male_grower"])
def test_empty_grower_chain_event_sale_addresses_starting_inventory(animal_class: str) -> None:
    herd_values = {
        "female_growers": 3 if animal_class == "female_grower" else 0,
        "male_growers": 3 if animal_class == "male_grower" else 0,
    }
    assumptions = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(
            does=0,
            bucks=0,
            max_breeding_does=0,
            female_retention_fraction=1.0,
            auto_purchase_bucks=False,
            **herd_values,
        ),
        events=[
            HerdEventAssumptions(
                month=1,
                kind="sale",
                animal_class=animal_class,  # type: ignore[arg-type]
                count=3,
                price_per_head=123.0,
            )
        ],
    )
    assumptions.reproduction.age_at_first_breeding_months = 6
    assumptions.growth.sale_age_months = 6

    month = run_simulation(assumptions, with_break_even=False).months[0]

    assert month.sales_head == pytest.approx(3.0)
    assert month.sales_revenue == pytest.approx(369.0)
    assert month.total_herd == pytest.approx(0.0)
    assert month.events == [f"Sold 3 {animal_class.replace('_', ' ')}(s) at ₹123/head (₹369)"]


@pytest.mark.parametrize("animal_class", ["female_grower", "male_grower"])
def test_empty_grower_chain_ordered_purchase_then_sale_round_trip(animal_class: str) -> None:
    herd_values = {
        "female_growers": 2 if animal_class == "female_grower" else 0,
        "male_growers": 2 if animal_class == "male_grower" else 0,
    }
    assumptions = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(
            does=0,
            bucks=0,
            max_breeding_does=0,
            auto_purchase_bucks=False,
            **herd_values,
        ),
        events=[
            HerdEventAssumptions(
                month=1,
                kind="purchase",
                animal_class=animal_class,  # type: ignore[arg-type]
                count=3,
            ),
            HerdEventAssumptions(
                month=1,
                kind="sale",
                animal_class=animal_class,  # type: ignore[arg-type]
                count=5,
                price_per_head=123.0,
            ),
        ],
    )
    assumptions.reproduction.age_at_first_breeding_months = 6
    assumptions.growth.sale_age_months = 6

    result = run_simulation(assumptions, with_break_even=False)
    month = result.months[0]

    assert month.purchases_head == pytest.approx(3.0)
    assert month.sales_head == pytest.approx(5.0)
    assert month.sales_revenue == pytest.approx(615.0)
    assert month.total_herd == pytest.approx(0.0)
    assert month.events[-1] == f"Sold 5 {animal_class.replace('_', ' ')}(s) at ₹123/head (₹615)"
    assert result.project_cost_breakdown.projected_peak_head == pytest.approx(5.0)


@pytest.mark.parametrize(
    ("animal_class", "herd_field"),
    [
        ("doe", "does"),
        ("buck", "bucks"),
        ("female_kid", "female_kids"),
        ("male_kid", "male_kids"),
        ("female_weaner", "female_weaners"),
        ("male_weaner", "male_weaners"),
        ("female_grower", "female_growers"),
        ("male_grower", "male_growers"),
    ],
)
def test_default_event_sale_price_and_branch_for_every_animal_class(
    animal_class: str, herd_field: str
) -> None:
    herd_values: dict[str, object] = {
        "does": 0,
        "bucks": 0,
        "max_breeding_does": 0,
        "auto_purchase_bucks": False,
        "foundation_flock_state": "open",
        herd_field: 2,
    }
    assumptions = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions.model_validate(herd_values),
        reproduction=ReproductionAssumptions(
            age_at_first_breeding_months=24,
            conception_rate=0.0,
        ),
        mortality=MortalityAssumptions(
            kid_pre_weaning=0.0,
            kid_post_weaning=0.0,
            grower=0.0,
            adult=0.0,
        ),
        culling=CullingAssumptions(doe_cull_rate_annual=0.0, max_doe_age_months=180),
        events=[
            HerdEventAssumptions(
                month=1,
                kind="sale",
                animal_class=animal_class,  # type: ignore[arg-type]
                count=1,
            )
        ],
    )
    assumptions.growth.sale_age_months = 24
    flatten_market(assumptions)
    growth = assumptions.growth
    if animal_class == "doe":
        expected = assumptions.sales.cull_doe_price_per_kg * growth.adult_weight_doe_kg
    elif animal_class == "buck":
        expected = assumptions.sales.cull_buck_price_per_kg * growth.adult_weight_buck_kg
    else:
        ages = {
            "female_kid": 1,
            "male_kid": 1,
            "female_weaner": 4,
            "male_weaner": 4,
            "female_grower": 15,
            "male_grower": 15,
        }
        male = animal_class.startswith("male_")
        adult_weight = (
            growth.adult_weight_buck_kg
            if animal_class == "male_grower"
            else growth.adult_weight_doe_kg
        )
        weight_fn = male_weight_at_age if male else weight_at_age
        expected = (
            weight_fn(ages[animal_class], growth, adult_weight)
            * assumptions.sales.meat_price_per_kg
        )

    first = run_simulation(assumptions, with_break_even=False).months[0]

    if animal_class in {"doe", "buck"}:
        assert first.culls_head == pytest.approx(1.0)
        assert first.cull_revenue == pytest.approx(expected)
        assert first.sales_head == 0.0
    else:
        assert first.sales_head == pytest.approx(1.0)
        assert first.sales_revenue == pytest.approx(expected)
        assert first.culls_head == 0.0
    assert "only" not in first.events[0]
    assert first.events[0].startswith(f"Sold 1 {animal_class.replace('_', ' ')}(s)")


def test_multiple_event_sales_accumulate_adult_and_young_revenue() -> None:
    assumptions = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(
            does=1,
            bucks=1,
            female_kids=1,
            male_kids=1,
            max_breeding_does=0,
            auto_purchase_bucks=False,
            foundation_flock_state="open",
        ),
        reproduction=ReproductionAssumptions(conception_rate=0.0),
        mortality=MortalityAssumptions(
            kid_pre_weaning=0.0,
            kid_post_weaning=0.0,
            grower=0.0,
            adult=0.0,
        ),
        culling=CullingAssumptions(doe_cull_rate_annual=0.0, max_doe_age_months=180),
        events=[
            HerdEventAssumptions(
                month=1,
                kind="sale",
                animal_class="doe",
                count=1,
                price_per_head=100.0,
            ),
            HerdEventAssumptions(
                month=1,
                kind="sale",
                animal_class="buck",
                count=1,
                price_per_head=200.0,
            ),
            HerdEventAssumptions(
                month=1,
                kind="sale",
                animal_class="female_kid",
                count=1,
                price_per_head=300.0,
            ),
            HerdEventAssumptions(
                month=1,
                kind="sale",
                animal_class="male_kid",
                count=1,
                price_per_head=400.0,
            ),
        ],
    )

    first = run_simulation(assumptions, with_break_even=False).months[0]

    assert first.culls_head == pytest.approx(2.0)
    assert first.cull_revenue == pytest.approx(300.0)
    assert first.sales_head == pytest.approx(2.0)
    assert first.sales_revenue == pytest.approx(700.0)


def test_sale_event_does_booked_as_culls() -> None:
    event = HerdEventAssumptions(month=6, kind="sale", animal_class="doe", count=5)
    res = run_simulation(event_toy([event], horizon=12), with_break_even=False)
    base = run_simulation(event_toy([], horizon=12), with_break_even=False)
    m6, m6_base = res.months[5], base.months[5]
    assert _doe_pool(m6) - _doe_pool(m6_base) == pytest.approx(-5.0 * S_ADULT, abs=1e-6)
    # Adult disposals are culls: 5 x 33 kg x Rs 220/kg = Rs 36,300, no meat sale.
    assert m6.culls_head - m6_base.culls_head == pytest.approx(5.0)
    assert m6.cull_revenue - m6_base.cull_revenue == pytest.approx(5.0 * 220.0 * 33.0)
    assert m6.sales_head == pytest.approx(m6_base.sales_head, abs=1e-9)
    assert m6.sales_revenue == pytest.approx(m6_base.sales_revenue, abs=1e-9)
    assert any("Sold 5 doe(s)" in note for note in m6.events)


def test_sale_event_young_stock_booked_as_meat() -> None:
    event = HerdEventAssumptions(month=2, kind="sale", animal_class="male_weaner", count=5)
    a = event_toy([event], horizon=12, male_weaners=10)
    res = run_simulation(a, with_break_even=False)
    base = run_simulation(event_toy([], horizon=12, male_weaners=10), with_break_even=False)
    m2, m2_base = res.months[1], base.months[1]
    # The remaining weaners graduate to the grower chain this same month and
    # take one month of grower mortality, so the shortfall shows up there.
    s_grower = 1.0 - monthly_mortality_rate(0.04)
    assert m2.m_growers - m2_base.m_growers == pytest.approx(-5.0 * s_grower, abs=1e-6)
    assert m2.total_herd - m2_base.total_herd == pytest.approx(-5.0 * s_grower, abs=1e-6)
    # Young-stock disposals are meat sales, priced at the weight of the animals
    # actually drawn. These weaners were PLACED mid-class (age 4) but the event
    # fires in month 2, by which time the pool has aged into the age-5 slot
    # (male curve: 15.8 kg x the 10% young-male premium = 17.38 kg) at the
    # calibrated Rs 370/kg base price. Pricing every draw at the fixed
    # placement age understated this by ~16%.
    assert m2_base.sales_head == 0.0
    assert m2.sales_head == pytest.approx(5.0)
    assert m2.sales_revenue == pytest.approx(
        5.0
        * male_weight_at_age(5, a.growth, a.growth.adult_weight_buck_kg)
        * a.sales.meat_price_per_kg
    )
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
    assert m2.cull_revenue == pytest.approx(take * 220.0 * 33.0, abs=1e-6)
    (note,) = m2.events
    assert note.startswith(f"Sold {take:g} doe(s) at ")
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
    assert m1.purchase_cost == pytest.approx(0.0)
    assert m1.breeding_stock_capex == pytest.approx(
        4.0 * SimulationAssumptions().herd.doe_purchase_price
    )
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
    # Month 14 falls in year 2; the purchase cash is neither project cost nor
    # young-stock opex — it lands on the capitalized breeding-stock line.
    assert res.annual_pl[1].breeding_stock_capex - base.annual_pl[1].breeding_stock_capex == (
        pytest.approx(10.0 * SimulationAssumptions().herd.doe_purchase_price)
    )
    assert res.annual_pl[0].breeding_stock_capex == pytest.approx(
        base.annual_pl[0].breeding_stock_capex
    )
    assert res.metrics.npv < base.metrics.npv


def test_event_sale_meat_revenue_reaches_annual_pl() -> None:
    # Month 10 is before the first organic meat sale (month 11), so the event
    # sale is the only month-10 meat-revenue delta. The draw is priced at the
    # grower pool's WEIGHT-WEIGHTED AVERAGE, not a fixed mid-class age: _draw
    # takes head proportionally from every age slot, and by month 10 the chain
    # holds promoted animals rather than the mid-class placement the foundation
    # stock started at. At the calibrated sale age 10 the chain spans ages 6-9
    # on the MALE curve (10% young-male premium); the event fires in month 10
    # (the last month before organic graduation sales start competing for the
    # same pool) and averages 19.9049 kg (the default parity table shifts the
    # pool's age mix slightly; 19.9106 before parity structure). The market
    # calendar is flattened and festivals disabled so the average is the only
    # thing priced.
    event = HerdEventAssumptions(month=10, kind="sale", animal_class="male_grower", count=3)
    a = SimulationAssumptions(meta=MetaAssumptions(horizon_months=24), events=[event])
    a = pin_legacy_growth(a)
    flatten_market(a)
    a.sales.festival_sale_months = []
    base_a = SimulationAssumptions(meta=MetaAssumptions(horizon_months=24))
    base_a = pin_legacy_growth(base_a)
    flatten_market(base_a)
    base_a.sales.festival_sale_months = []
    res = run_simulation(a, with_break_even=False)
    base = run_simulation(base_a, with_break_even=False)
    assert res.months[9].sales_head - base.months[9].sales_head == pytest.approx(3.0)
    assert res.months[9].sales_revenue - base.months[9].sales_revenue == pytest.approx(
        3.0 * 19.9049 * 370.0, rel=1e-4
    )
    # The event's revenue reaches the annual P&L two ways: the P&L row
    # aggregates the months exactly, and the year-1 delta is economically
    # bounded — positive (the event pulls revenue forward into month 10) but
    # below the event-month delta, because at sale age 10 the baseline sells
    # those same animals inside year 1 too (graduation from month 11).
    month10_delta = res.months[9].sales_revenue - base.months[9].sales_revenue
    annual_delta = res.annual_pl[0].meat_revenue - base.annual_pl[0].meat_revenue
    assert res.annual_pl[0].meat_revenue == pytest.approx(
        sum(m.sales_revenue for m in res.months[:12]), rel=1e-4
    )
    assert 0.0 < annual_delta < month10_delta


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


def test_event_sale_of_young_stock_gets_festival_uplift() -> None:
    """A scheduled young-stock sale in a festival month is priced at the
    festival-inclusive price: with festival holding off, the same animals
    cross the scale, so revenue moves by exactly the uplift."""

    def run(festival_months: list[int]) -> float:
        a = SimulationAssumptions(
            meta=MetaAssumptions(horizon_months=24),
            sales=SalesAssumptions(festival_sale_months=festival_months, eid_price_uplift=0.30),
            events=[
                HerdEventAssumptions(month=15, kind="sale", animal_class="male_grower", count=2)
            ],
        )
        a.sales.festival_hold_months = 0
        res = run_simulation(a, with_break_even=False)
        assert res.months[14].sales_head > 0.0
        return res.months[14].sales_revenue

    assert run([15]) / run([]) == pytest.approx(1.30, abs=1e-9)


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


def test_project_cost_breakdown_sums_and_funds_scheduled_peak_capacity() -> None:
    event = HerdEventAssumptions(month=14, kind="purchase", animal_class="doe", count=10)
    res = run_simulation(event_toy([event]), with_break_even=False)
    base = run_simulation(event_toy([]), with_break_even=False)
    b = res.project_cost_breakdown
    assert b.shed_cost + b.equipment_cost + b.stock_cost + b.working_capital == pytest.approx(
        res.metrics.project_cost
    )
    # The animals themselves remain a month-14 purchase (capitalized breeding
    # cash now, not young-stock opex), but their forecast housing/equipment
    # requirement must be funded at project start.
    assert res.months[13].purchase_cost == pytest.approx(0.0)
    assert res.months[13].breeding_stock_capex > 0.0
    assert b.stock_cost == pytest.approx(base.project_cost_breakdown.stock_cost)
    assert b.working_capital == pytest.approx(base.project_cost_breakdown.working_capital)
    assert b.projected_peak_head > base.project_cost_breakdown.projected_peak_head
    assert b.shed_cost > base.project_cost_breakdown.shed_cost
    assert res.metrics.project_cost > base.metrics.project_cost


def test_project_capacity_captures_ordered_purchase_before_same_month_sale() -> None:
    assumptions = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(does=0, bucks=0, auto_purchase_bucks=False),
        events=[
            HerdEventAssumptions(
                month=1,
                kind="purchase",
                animal_class="female_kid",
                count=1_000,
            ),
            HerdEventAssumptions(
                month=1,
                kind="sale",
                animal_class="female_kid",
                count=1_000,
            ),
        ],
    )

    result = run_simulation(assumptions, with_break_even=False)

    assert result.months[0].total_herd == pytest.approx(0.0)
    assert result.project_cost_breakdown.projected_peak_head == pytest.approx(1_000.0)
    assert result.project_cost_breakdown.capacity_places == pytest.approx(1_100.0)


@pytest.mark.parametrize(
    "animal_class",
    [
        "doe",
        "buck",
        "female_kid",
        "male_kid",
        "female_weaner",
        "male_weaner",
        "female_grower",
        "male_grower",
    ],
)
def test_project_capacity_counts_every_event_purchase_cohort(animal_class: str) -> None:
    assumptions = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(does=0, bucks=0, auto_purchase_bucks=False),
        mortality=MortalityAssumptions(
            kid_pre_weaning=0.0,
            kid_post_weaning=0.0,
            grower=0.0,
            adult=0.0,
        ),
        culling=CullingAssumptions(doe_cull_rate_annual=0.0, max_doe_age_months=180),
        costs=CostsAssumptions(capacity_buffer_fraction=0.0),
        events=[
            HerdEventAssumptions(
                month=1,
                kind="purchase",
                animal_class=animal_class,  # type: ignore[arg-type]
                count=123,
            )
        ],
    )

    breakdown = run_simulation(assumptions, with_break_even=False).project_cost_breakdown

    assert breakdown.projected_peak_head == pytest.approx(123.0)
    assert breakdown.capacity_places == pytest.approx(123.0)


def test_project_capacity_captures_births_before_newborn_mortality() -> None:
    assumptions = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(
            does=10,
            bucks=1,
            auto_purchase_bucks=False,
            foundation_flock_state="open",
        ),
        reproduction=ReproductionAssumptions(
            conception_rate=1.0,
            gestation_months=1,
            lactation_months=8,
            months_open_before_breeding=12,
            litter_size=4.0,
            stillbirth_rate=0.0,
            # Capacity pin, not biology: the flat parity table keeps the crop
            # at exactly 10 does x 4 kids.
            parity_multipliers=ParityMultipliers(litter_size=[1.0], conception_rate=[1.0]),
        ),
        mortality=MortalityAssumptions(
            kid_pre_weaning=0.9,
            kid_post_weaning=0.9,
            grower=0.9,
            adult=0.0,
        ),
        culling=CullingAssumptions(
            doe_cull_rate_annual=0.0,
            max_doe_age_months=180,
        ),
    )

    result = run_simulation(assumptions, with_break_even=False)

    assert result.months[1].births == pytest.approx(40.0)
    assert result.months[1].total_herd < 51.0
    assert result.project_cost_breakdown.projected_peak_head == pytest.approx(51.0)


def test_project_capacity_captures_pre_service_auto_buck_purchase() -> None:
    assumptions = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(
            does=50,
            bucks=0,
            auto_purchase_bucks=True,
            foundation_flock_state="open",
        ),
        reproduction=ReproductionAssumptions(conception_rate=0.0),
        mortality=MortalityAssumptions(adult=0.9),
        culling=CullingAssumptions(
            doe_cull_rate_annual=0.0,
            max_doe_age_months=180,
        ),
    )

    result = run_simulation(assumptions, with_break_even=False)

    assert result.months[0].purchases_head >= 2.0
    assert result.months[0].total_herd < 53.0
    # 50 does at the 1:20 policy need a 3-sire battery.
    assert result.project_cost_breakdown.projected_peak_head == pytest.approx(53.0)


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


def test_herd_cohorts_preserves_boundary_sex_and_multiplicity() -> None:
    counts = herd_cohorts(
        [
            ("F", 0),
            ("F", 2),
            ("M", 1),
            ("F", 3),
            ("F", 5),
            ("M", 4),
            ("F", 6),
            ("F", 7),
            ("M", 6),
        ],
        doe_adult_age=12,
    )
    assert counts == {
        "does": 0,
        "bucks": 0,
        "f_kids": 2,
        "m_kids": 1,
        "f_weaners": 2,
        "m_weaners": 1,
        "f_growers": 2,
        "m_growers": 1,
    }


# ---------------------------------------------------------------------------
# (l) Mutation-sensitive numerical and assembly boundaries
# ---------------------------------------------------------------------------
def test_whole_unit_policy_has_an_inclusive_eight_ulp_noise_window() -> None:
    eight_ulps = 1.0
    for _ in range(8):
        eight_ulps = math.nextafter(eight_ulps, math.inf)
    nine_ulps = math.nextafter(eight_ulps, math.inf)

    assert abs(eight_ulps - 1.0) == 8 * math.ulp(eight_ulps)
    assert abs(nine_ulps - 1.0) == 9 * math.ulp(nine_ulps)
    assert _ceil_head_ratio(eight_ulps, 1) == 1
    assert _ceil_head_ratio(nine_ulps, 1) == 2


def test_one_doe_mixed_foundation_still_spans_the_reproductive_cycle() -> None:
    assumptions = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(
            does=1,
            bucks=0,
            auto_purchase_bucks=False,
            foundation_flock_state="mixed",
        ),
        reproduction=ReproductionAssumptions(conception_rate=0.0, stillbirth_rate=0.0),
        mortality=MortalityAssumptions(adult=0.0),
        culling=CullingAssumptions(doe_cull_rate_annual=0.0, max_doe_age_months=180),
    )

    first = _run_core(assumptions).months[0]

    assert first.births > 0.0
    assert first.lactating_does > 0.0
    assert first.open_does < 1.0


def test_single_purchased_foundation_doe_is_present_in_the_age_ledger() -> None:
    assumptions = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=13),
        herd=HerdAssumptions(
            does=1,
            bucks=0,
            auto_purchase_bucks=False,
            foundation_flock_state="open",
        ),
        reproduction=ReproductionAssumptions(conception_rate=0.0),
        mortality=MortalityAssumptions(adult=0.0),
        culling=CullingAssumptions(doe_cull_rate_annual=0.0, max_doe_age_months=36),
    )
    # Synchronized cohort: pin the foundation age window to the exact age the
    # max-doe-age clock starts from (the default is a 18-42 month spread).
    assumptions.herd.foundation_doe_age_min_months = 24
    assumptions.herd.foundation_doe_age_max_months = 24

    result = _run_core(assumptions)

    assert sum(month.culls_head for month in result.months[:12]) == 0.0
    assert result.months[12].culls_head == pytest.approx(1.0)
    assert result.months[12].total_herd == pytest.approx(0.0)


def test_purchased_doe_age_spread_stops_at_sixty_months() -> None:
    assumptions = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=13),
        herd=HerdAssumptions(
            does=38,
            bucks=0,
            auto_purchase_bucks=False,
            foundation_flock_state="open",
        ),
        reproduction=ReproductionAssumptions(conception_rate=0.0),
        mortality=MortalityAssumptions(adult=0.0),
        culling=CullingAssumptions(doe_cull_rate_annual=0.0, max_doe_age_months=73),
    )

    result = _run_core(assumptions)

    assert sum(month.culls_head for month in result.months) == 0.0
    assert result.months[-1].total_herd == pytest.approx(38.0)


def test_break_even_search_preserves_a_valid_sub_rupee_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        engine_module,
        "_run_core",
        lambda assumptions: SimpleNamespace(npv=assumptions.sales.meat_price_per_kg - 0.5),
    )

    value = engine_module.break_even_meat_price(SimulationAssumptions())

    assert value == pytest.approx(0.5, abs=1e-6)


def test_break_even_search_returns_exact_zero_when_zero_price_exactly_breaks_even(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        engine_module,
        "_run_core",
        lambda assumptions: SimpleNamespace(npv=assumptions.sales.meat_price_per_kg),
    )

    assert engine_module.break_even_meat_price(SimulationAssumptions()) == 0.0


def test_break_even_search_accepts_an_exact_root_at_the_public_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        engine_module,
        "_run_core",
        lambda assumptions: SimpleNamespace(
            npv=assumptions.sales.meat_price_per_kg - float(MAX_MONEY)
        ),
    )

    value = engine_module.break_even_meat_price(SimulationAssumptions())

    assert value is not None
    assert value == pytest.approx(float(MAX_MONEY), abs=1e-3)


def test_assumptions_fingerprint_is_sha256_of_canonical_compact_json() -> None:
    assumptions = SimulationAssumptions(meta=MetaAssumptions(horizon_months=12))
    canonical = json.dumps(
        assumptions.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    result = run_simulation(assumptions, with_break_even=False)

    assert result.assumptions_fingerprint == hashlib.sha256(canonical).hexdigest()


def test_computed_unreachable_break_even_is_reported_as_computed() -> None:
    assumptions = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(does=0, bucks=0, auto_purchase_bucks=False),
        costs=CostsAssumptions(labour_per_month=0.0, misc_overhead_per_month=1.0),
    )

    result = run_simulation(assumptions)
    explanation = next(
        item for item in result.metric_explanations if item.key == "break_even_meat_price_per_kg"
    )

    assert result.metrics.break_even_meat_price_per_kg is None
    assert explanation.explanation.startswith("Even at the break-even search ceiling")


# ---------------------------------------------------------------------------
# (m) Mutation-complete engine identities and valid-domain boundaries
# ---------------------------------------------------------------------------
def _mutation_empty_assumptions(horizon: int = 12) -> SimulationAssumptions:
    """A zero-noise project whose economics come only from explicit test inputs."""
    assumptions = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=horizon),
        herd=HerdAssumptions(does=0, bucks=0, auto_purchase_bucks=False),
        mortality=MortalityAssumptions(
            kid_pre_weaning=0.0,
            kid_post_weaning=0.0,
            grower=0.0,
            adult=0.0,
        ),
        culling=CullingAssumptions(
            doe_cull_rate_annual=0.0,
            max_doe_age_months=180,
        ),
        feed=FeedAssumptions(
            green_price_per_kg=0.0,
            purchased_green_price_per_kg=0.0,
            dry_price_per_kg=0.0,
            concentrate_price_per_kg=0.0,
        ),
        costs=CostsAssumptions(
            vet_per_animal_per_year=0.0,
            labour_per_month=0.0,
            insurance_pct_stock_value_annual=0.0,
            misc_overhead_per_month=0.0,
            shed_cost_per_animal_place=0.0,
            equipment_cost_per_animal=0.0,
            capacity_buffer_fraction=0.0,
        ),
        sales=SalesAssumptions(
            meat_price_per_kg=0.0,
            cull_doe_price_per_kg=0.0,
            cull_buck_price_per_kg=0.0,
            milk_price_per_litre=0.0,
            lactation_milk_litres=0.0,
            manure_income_per_adult_per_year=0.0,
        ),
    )
    assumptions.finance.loan_fraction_of_project_cost = 0.0
    assumptions.finance.working_capital_months = 0
    assumptions.finance.income_tax_rate = 0.0
    assumptions.finance.include_terminal_value = False
    # The calibrated default escalates prices 4%/yr and operating costs 5%/yr;
    # mutation-economics goldens must price at the base they set explicitly.
    assumptions.sales.annual_livestock_price_growth_rate = 0.0
    assumptions.feed.annual_feed_price_growth_rate = 0.0
    assumptions.costs.operating_cost_growth_rate_annual = 0.0
    assumptions.sales.monthly_meat_price_multipliers = [1.0] * 12
    # Zero-noise reproduction policy: flat parity table (the default parity
    # structure scales conception below 1.0 and sends the failures into the
    # repeat-breeder cull) and the service cull parked. Tests that pin those
    # policies set them explicitly.
    assumptions.reproduction.parity_multipliers = ParityMultipliers(
        litter_size=[1.0], conception_rate=[1.0]
    )
    assumptions.reproduction.max_services_before_cull = 0
    return assumptions


def test_adult_doe_age_ledger_accumulates_foundation_and_event_purchases() -> None:
    assumptions = _mutation_empty_assumptions(13)
    assumptions.herd.does = 1
    assumptions.herd.bucks = 1
    assumptions.herd.foundation_flock_state = "open"
    assumptions.reproduction.conception_rate = 0.0
    assumptions.culling.max_doe_age_months = 36
    assumptions.sales.cull_doe_price_per_kg = 2.0
    # Both the foundation and the event-purchased doe must sit at the same
    # synchronized age (24 m) so one max-doe-age cull empties them together;
    # the calibrated default spreads purchases over 18-42 months.
    assumptions.herd.foundation_doe_age_min_months = 24
    assumptions.herd.foundation_doe_age_max_months = 24
    assumptions.events = [
        HerdEventAssumptions(
            month=1,
            kind="purchase",
            animal_class="doe",
            count=1,
            price_per_head=0.0,
        ),
        HerdEventAssumptions(
            month=13,
            kind="sale",
            animal_class="buck",
            count=1,
            price_per_head=5.0,
        ),
    ]

    month13 = _run_core(assumptions).months[12]

    assert month13.culls_head == pytest.approx(3.0)
    assert month13.cull_revenue == pytest.approx(
        5.0 + 2.0 * assumptions.sales.cull_doe_price_per_kg * assumptions.growth.adult_weight_doe_kg
    )
    assert month13.total_herd == pytest.approx(0.0)


def test_year_two_adult_event_prices_apply_livestock_growth_by_multiplication() -> None:
    assumptions = _mutation_empty_assumptions(13)
    assumptions.herd.does = 1
    assumptions.herd.bucks = 1
    assumptions.herd.foundation_flock_state = "open"
    assumptions.reproduction.conception_rate = 0.0
    assumptions.herd.doe_purchase_price = 10.0
    assumptions.herd.buck_purchase_price = 20.0
    assumptions.sales.cull_doe_price_per_kg = 2.0
    assumptions.sales.cull_buck_price_per_kg = 3.0
    assumptions.sales.annual_livestock_price_growth_rate = 0.21
    assumptions.events = [
        HerdEventAssumptions(month=13, kind="sale", animal_class="doe", count=1),
        HerdEventAssumptions(month=13, kind="sale", animal_class="buck", count=1),
        HerdEventAssumptions(month=13, kind="purchase", animal_class="doe", count=1),
        HerdEventAssumptions(month=13, kind="purchase", animal_class="buck", count=1),
    ]

    month13 = _run_core(assumptions).months[12]
    growth = 1.21

    assert month13.cull_revenue == pytest.approx(
        growth
        * (
            assumptions.sales.cull_doe_price_per_kg * assumptions.growth.adult_weight_doe_kg
            + assumptions.sales.cull_buck_price_per_kg * assumptions.growth.adult_weight_buck_kg
        )
    )
    # Doe/buck purchases capitalize at the same growth-adjusted prices.
    assert month13.purchase_cost == pytest.approx(0.0)
    assert month13.breeding_stock_capex == pytest.approx(
        growth * (assumptions.herd.doe_purchase_price + assumptions.herd.buck_purchase_price)
    )


def test_female_grower_midpoint_purchase_graduates_in_the_third_month() -> None:
    assumptions = _mutation_empty_assumptions(12)
    assumptions.reproduction.age_at_first_breeding_months = 12
    assumptions.herd.female_retention_fraction = 0.0
    assumptions.sales.meat_price_per_kg = 1.0
    assumptions.events = [
        HerdEventAssumptions(
            month=1,
            kind="purchase",
            animal_class="female_grower",
            count=1,
            price_per_head=0.0,
        )
    ]

    months = _run_core(assumptions).months

    assert [month.sales_head for month in months[:4]] == pytest.approx([0.0, 0.0, 1.0, 0.0])
    assert months[2].sales_revenue == pytest.approx(
        weight_at_age(12, assumptions.growth, assumptions.growth.adult_weight_doe_kg)
    )


def test_surplus_grower_revenue_adds_to_an_earlier_ordered_sale() -> None:
    assumptions = _mutation_empty_assumptions()
    assumptions.herd.female_kids = 1
    assumptions.herd.female_growers = 1
    assumptions.herd.female_retention_fraction = 0.0
    assumptions.reproduction.age_at_first_breeding_months = 6
    assumptions.sales.meat_price_per_kg = 2.0
    assumptions.events = [
        HerdEventAssumptions(
            month=1,
            kind="sale",
            animal_class="female_kid",
            count=1,
            price_per_head=5.0,
        )
    ]

    first = _run_core(assumptions).months[0]

    assert first.sales_head == pytest.approx(2.0)
    assert first.sales_revenue == pytest.approx(
        5.0 + 2.0 * weight_at_age(6, assumptions.growth, assumptions.growth.adult_weight_doe_kg)
    )


def test_three_month_open_waiting_pipeline_conserves_every_foundation_doe() -> None:
    assumptions = _mutation_empty_assumptions()
    assumptions.herd.does = 17
    assumptions.herd.foundation_flock_state = "mixed"
    assumptions.reproduction.conception_rate = 0.0
    assumptions.reproduction.months_open_before_breeding = 3
    assumptions.reproduction.stillbirth_rate = 0.0

    first = _run_core(assumptions).months[0]

    assert first.total_herd == pytest.approx(17.0 + first.births)


def test_biological_shocks_are_multiplied_then_clamped_to_public_caps() -> None:
    assumptions = _mutation_empty_assumptions()
    assumptions.herd.does = 1
    assumptions.herd.bucks = 1
    assumptions.herd.foundation_flock_state = "open"
    assumptions.reproduction.gestation_months = 1
    assumptions.reproduction.conception_rate = 1.0
    assumptions.reproduction.litter_size = 3.0
    assumptions.reproduction.stillbirth_rate = 0.0
    shocks = MonthlyShockPath.neutral(12)
    shocks.conception[0] = 2.0
    shocks.litter_size[1] = 2.0

    months = _run_core(assumptions, shocks).months

    assert months[0].pregnant_does == pytest.approx(1.0)
    assert months[0].open_does == pytest.approx(0.0)
    assert months[1].births == pytest.approx(4.0)


def test_scheduled_stock_and_pre_service_sires_accumulate_purchase_totals() -> None:
    assumptions = _mutation_empty_assumptions()
    assumptions.herd.does = 26
    assumptions.herd.foundation_flock_state = "open"
    assumptions.herd.auto_purchase_bucks = True
    assumptions.herd.buck_purchase_price = 7.0
    assumptions.reproduction.conception_rate = 0.0
    assumptions.events = [
        HerdEventAssumptions(
            month=1,
            kind="purchase",
            animal_class="female_kid",
            count=2,
            price_per_head=3.0,
        )
    ]

    first = _run_core(assumptions).months[0]

    assert first.purchases_head == pytest.approx(4.0)
    # Young-stock cash (2 kids x 3) stays opex; the 2 auto-purchased sires
    # (x 7) capitalize on the breeding-stock account.
    assert first.purchase_cost == pytest.approx(2.0 * 3.0)
    assert first.breeding_stock_capex == pytest.approx(2.0 * 7.0)
    assert first.bucks == pytest.approx(2.0)


def test_same_month_sire_purchases_are_all_protected_from_rotation() -> None:
    assumptions = _mutation_empty_assumptions()
    assumptions.herd.does = 50
    assumptions.herd.bucks = 2
    assumptions.herd.foundation_flock_state = "open"
    assumptions.herd.auto_purchase_bucks = True
    assumptions.herd.buck_purchase_price = 7.0
    assumptions.reproduction.conception_rate = 0.0
    assumptions.culling.buck_rotation_years = 1
    assumptions.events = [
        HerdEventAssumptions(
            month=12,
            kind="sale",
            animal_class="buck",
            count=2,
            price_per_head=0.0,
        ),
        HerdEventAssumptions(
            month=12,
            kind="purchase",
            animal_class="buck",
            count=1,
            price_per_head=7.0,
        ),
    ]

    month12 = _run_core(assumptions).months[11]

    # Rotation culls the whole standing battery (auto-topped to 3 under the
    # 1:20 policy before the rotation fires); the event purchases are
    # protected from that sweep.
    assert month12.culls_head == pytest.approx(3.0)
    # 50 does at 1:20 need 3 sires: the event's 1 plus 2 auto-purchases.
    assert month12.purchases_head == pytest.approx(3.0)
    assert month12.purchase_cost == pytest.approx(0.0)
    assert month12.breeding_stock_capex == pytest.approx(21.0)
    assert month12.bucks == pytest.approx(3.0)


def test_rotation_replaces_an_exactly_one_buck_battery() -> None:
    assumptions = _mutation_empty_assumptions()
    assumptions.herd.does = 1
    assumptions.herd.bucks = 1
    assumptions.herd.foundation_flock_state = "open"
    assumptions.herd.auto_purchase_bucks = True
    assumptions.reproduction.conception_rate = 0.0
    assumptions.culling.buck_rotation_years = 1

    month12 = _run_core(assumptions).months[11]

    assert month12.culls_head == pytest.approx(1.0)
    assert month12.purchases_head == pytest.approx(1.0)
    assert month12.bucks == pytest.approx(1.0)


def test_exact_homegrown_consumption_leaves_no_manufactured_fodder_stock() -> None:
    assumptions = _mutation_empty_assumptions()
    assumptions.herd.does = 1
    assumptions.herd.foundation_flock_state = "open"
    assumptions.reproduction.conception_rate = 0.0
    # Zero cultivation: the opening stock must exactly cover this month's
    # demand with nothing manufactured and nothing left over.
    assumptions.feed.cultivated_fodder_acres = 0.0
    demand = class_feed(
        1.0,
        assumptions.growth.adult_weight_doe_kg,
        assumptions.feed.dmi_doe_maintenance,
        assumptions.feed.concentrate_share_doe_maintenance,
        assumptions.feed,
    ).green_dm_kg
    assumptions.feed.initial_fodder_stock_kg_dm = demand
    assumptions.feed.fodder_storage_capacity_kg_dm = demand
    assumptions.feed.fodder_storage_loss_fraction_monthly = 0.0

    first = _run_core(assumptions).months[0]

    assert first.feed_purchased_green_kg == pytest.approx(0.0)
    assert first.feed_homegrown_green_kg == pytest.approx(demand / assumptions.feed.green_dm_pct)
    assert first.fodder_stock_kg_dm == pytest.approx(0.0)
    assert first.fodder_waste_kg_dm == pytest.approx(0.0)


def test_fodder_surplus_and_as_fed_conversion_preserve_dm_mass() -> None:
    assumptions = _mutation_empty_assumptions()
    assumptions.herd.does = 1
    assumptions.herd.foundation_flock_state = "open"
    assumptions.reproduction.conception_rate = 0.0
    assumptions.feed.cultivated_fodder_acres = 1.0
    assumptions.feed.fodder_yield_t_dm_per_acre_year = 6.0

    first = _run_core(assumptions).months[0]
    monthly_supply_dm = 500.0
    demand_dm = first.feed_green_kg * assumptions.feed.green_dm_pct

    assert first.fodder_surplus_kg == pytest.approx(monthly_supply_dm - demand_dm)
    assert first.feed_homegrown_green_kg == pytest.approx(first.feed_green_kg)
    assert first.feed_purchased_green_kg == pytest.approx(0.0)


def test_price_and_operating_shocks_multiply_each_revenue_and_cost_base() -> None:
    assumptions = pin_legacy_growth(_mutation_empty_assumptions(13))
    assumptions.herd.does = 1
    assumptions.herd.bucks = 1
    assumptions.herd.foundation_flock_state = "open"
    assumptions.reproduction.gestation_months = 1
    assumptions.reproduction.lactation_months = 12
    assumptions.reproduction.conception_rate = 1.0
    assumptions.reproduction.stillbirth_rate = 0.0
    assumptions.culling.buck_rotation_years = 10
    assumptions.sales.annual_livestock_price_growth_rate = 0.21
    assumptions.sales.lactation_milk_litres = 120.0
    assumptions.sales.milk_price_per_litre = 2.0
    # Flat curve + milk-price growth matching the livestock rate keeps this
    # hand-check in litres/month arithmetic.
    assumptions.sales.milk_persistency_monthly = 1.0
    assumptions.sales.annual_milk_price_growth_rate = 0.21
    assumptions.sales.manure_income_per_adult_per_year = 12.0
    assumptions.sales.selling_cost_fraction = 0.10
    assumptions.sales.transport_cost_per_head = 2.0
    assumptions.costs.operating_cost_growth_rate_annual = 0.21
    assumptions.costs.vet_per_animal_per_year = 12.0
    assumptions.costs.labour_per_month = 10.0
    assumptions.costs.misc_overhead_per_month = 5.0
    assumptions.events = [
        HerdEventAssumptions(
            month=13,
            kind="sale",
            animal_class="buck",
            count=1,
            price_per_head=100.0,
        )
    ]
    shocks = MonthlyShockPath.neutral(13)
    shocks.operating_cost[12] = 2.0

    month13 = _run_core(assumptions, shocks).months[12]
    livestock_growth = 1.21
    operating_growth = 1.21 * 2.0
    # Dairy regime: lactating_does is a milking overlay of the open/pregnant
    # pools, so the distinct adults are open + pregnant + bucks.
    adult_head = month13.open_does + month13.pregnant_does + month13.bucks

    assert month13.lactating_does > 0.0
    assert month13.milk_revenue == pytest.approx(
        month13.lactating_does
        * (assumptions.sales.lactation_milk_litres / assumptions.reproduction.lactation_months)
        * assumptions.sales.milk_price_per_litre
        * livestock_growth
    )
    assert month13.manure_revenue == pytest.approx(
        adult_head * assumptions.sales.manure_income_per_adult_per_year * livestock_growth / 12.0
    )
    assert month13.vet_cost == pytest.approx(
        month13.total_herd * assumptions.costs.vet_per_animal_per_year / 12.0 * operating_growth
    )
    # Labour is charged on adult breeding females (the TNAU/NABARD per-doe
    # norm) in HALF-attendant units (ceil(2 x does / threshold) / 2, floor
    # half a unit), not standing head; this toy run has no finishing pen.
    assert month13.labour_cost == pytest.approx(
        max(
            0.5,
            _ceil_head_ratio(
                2.0 * (month13.open_does + month13.pregnant_does),
                assumptions.costs.labour_per_head_threshold,
            )
            / 2.0,
        )
        * assumptions.costs.labour_per_month
        * operating_growth
    )
    assert month13.misc_cost == pytest.approx(
        assumptions.costs.misc_overhead_per_month * operating_growth
    )
    assert month13.selling_cost == pytest.approx(100.0 * 0.10 + 2.0 * operating_growth)
    core = _run_core(assumptions, shocks)
    for annual in core.annual_pl:
        assert annual.total_revenue == pytest.approx(
            annual.meat_revenue + annual.cull_revenue + annual.milk_revenue + annual.manure_revenue
        )
    times = [0.0, *[month.month / 12.0 for month in core.months]]
    benefits = [
        0.0,
        *[
            month.sales_revenue
            + month.cull_revenue
            + month.milk_revenue
            + month.manure_revenue
            + month.terminal_value
            for month in core.months
        ],
    ]
    costs = [
        core.equity,
        *[
            month.feed_cost
            + month.vet_cost
            + month.labour_cost
            + month.insurance_cost
            + month.misc_cost
            + month.selling_cost
            + month.purchase_cost
            + month.debt_service
            + month.tax
            for month in core.months
        ],
    ]
    assert core.bcr == pytest.approx(
        bcr(assumptions.finance.discount_rate_annual, benefits, costs, times)
    )


def test_labour_cost_distinguishes_zero_from_a_fractional_positive_herd() -> None:
    empty = _mutation_empty_assumptions()
    empty.costs.labour_per_month = 10.0
    empty_first = _run_core(empty).months[0]

    fractional = _mutation_empty_assumptions()
    fractional.herd.does = 1
    fractional.herd.foundation_flock_state = "open"
    fractional.reproduction.conception_rate = 0.0
    fractional.mortality.adult = 0.9
    fractional.costs.labour_per_month = 10.0
    fractional_first = _run_core(fractional).months[0]

    assert empty_first.total_herd == pytest.approx(0.0)
    assert empty_first.labour_cost == pytest.approx(0.0)
    assert 0.0 < fractional_first.total_herd < 1.0
    # Half-attendant granularity: a fractional (< threshold/2) herd books
    # half a unit, not a whole labourer.
    assert fractional_first.labour_cost == pytest.approx(5.0)


def test_working_capital_average_includes_nonzero_selling_cost() -> None:
    assumptions = _mutation_empty_assumptions()
    assumptions.herd.male_kids = 1
    assumptions.finance.working_capital_months = 3
    assumptions.sales.selling_cost_fraction = 0.10
    assumptions.sales.transport_cost_per_head = 1.0
    assumptions.events = [
        HerdEventAssumptions(
            month=1,
            kind="sale",
            animal_class="male_kid",
            count=1,
            price_per_head=10.0,
        )
    ]

    core = _run_core(assumptions)
    expected_average = (
        sum(
            month.feed_cost
            + month.vet_cost
            + month.labour_cost
            + month.insurance_cost
            + month.misc_cost
            + month.selling_cost
            for month in core.months[:12]
        )
        / 12.0
    )

    assert core.months[0].selling_cost == pytest.approx(2.0)
    assert core.working_capital == pytest.approx(
        assumptions.finance.working_capital_months * expected_average
    )


def test_opening_capacity_basis_sums_every_physical_cohort() -> None:
    assumptions = _mutation_empty_assumptions()
    assumptions.herd = HerdAssumptions(
        does=1,
        bucks=2,
        female_kids=3,
        male_kids=4,
        female_weaners=5,
        male_weaners=6,
        female_growers=7,
        male_growers=8,
        auto_purchase_bucks=False,
    )
    assumptions.costs.capacity_basis = "opening_herd"
    assumptions.costs.capacity_buffer_fraction = 0.0

    core = _run_core(assumptions)

    assert core.capacity_places == pytest.approx(36.0)
    assert core.projected_peak_head >= core.capacity_places


def test_subunit_stock_cost_and_debt_remain_visible_in_every_metric() -> None:
    assumptions = _mutation_empty_assumptions()
    assumptions.finance.initial_stock_cost = 0.5
    assumptions.finance.loan_fraction_of_project_cost = 1.0
    assumptions.finance.loan_term_months = 24
    assumptions.finance.moratorium_months = 0
    assumptions.finance.interest_rate_annual = 0.0
    # Zero the Telangana selling-cost defaults: this subunit pins stock cost
    # and debt visibility, and the Rs 100/head transport default would swamp
    # the 0.75 revenue with a Rs 100 selling cost.
    assumptions.sales.selling_cost_fraction = 0.0
    assumptions.sales.transport_cost_per_head = 0.0
    assumptions.events = [
        HerdEventAssumptions(
            month=1,
            kind="purchase",
            animal_class="female_kid",
            count=1,
            price_per_head=0.0,
        ),
        HerdEventAssumptions(
            month=1,
            kind="sale",
            animal_class="female_kid",
            count=1,
            price_per_head=0.75,
        ),
    ]

    core = _run_core(assumptions)
    annual = core.annual_pl[0]

    assert core.stock_cost == pytest.approx(0.5)
    assert core.loan_amount == pytest.approx(0.5)
    assert annual.principal == pytest.approx(0.5)
    assert annual.debt_service == pytest.approx(0.5)
    assert annual.total_revenue == pytest.approx(0.75)
    # The 24-month loan outlives the 12-month horizon: debt_service carries
    # the 0.25 balloon (0.25 scheduled principal + 0.25 balloon = 0.5), but
    # DSCR covers only the scheduled 0.25 of operating debt service.
    assert core.amortization[11].closing_balance == pytest.approx(0.25)
    assert core.dscr_per_year == pytest.approx([3.0])
    assert core.avg_dscr == pytest.approx(3.0)
    assert core.min_dscr == pytest.approx(3.0)
    assert core.minimum_cash_month == 0
    assert core.operating_margin == pytest.approx(1.0)


def test_monthly_depreciation_stops_exactly_at_life_and_keeps_month_alignment() -> None:
    assumptions = _mutation_empty_assumptions(13)
    assumptions.costs.capacity_basis = "planned"
    assumptions.costs.planned_capacity_head = 1
    assumptions.costs.shed_cost_per_animal_place = 1_200.0
    assumptions.costs.shed_useful_life_years = 1
    assumptions.costs.shed_residual_fraction = 0.0

    core = _run_core(assumptions)

    assert [month.depreciation for month in core.months[:12]] == pytest.approx([100.0] * 12)
    assert core.months[12].depreciation == pytest.approx(0.0)


def test_terminal_equipment_and_working_capital_apply_realization_fractions() -> None:
    assumptions = _mutation_empty_assumptions()
    assumptions.costs.capacity_basis = "planned"
    assumptions.costs.planned_capacity_head = 1
    assumptions.costs.equipment_cost_per_animal = 1_200.0
    assumptions.costs.equipment_useful_life_years = 10
    assumptions.costs.equipment_residual_fraction = 0.0
    assumptions.costs.misc_overhead_per_month = 10.0
    assumptions.finance.working_capital_months = 3
    assumptions.finance.include_terminal_value = True
    assumptions.finance.terminal_asset_realization_fraction = 0.5
    assumptions.finance.terminal_working_capital_recovery_fraction = 0.4

    core = _run_core(assumptions)

    assert core.equipment_cost == pytest.approx(1_200.0)
    assert core.working_capital == pytest.approx(30.0)
    assert core.terminal_value_breakdown.equipment == pytest.approx((1_200.0 - 120.0) * 0.5)
    assert core.terminal_value_breakdown.working_capital == pytest.approx(30.0 * 0.4)


def test_terminal_principal_is_added_only_to_the_final_annual_block() -> None:
    assumptions = _mutation_empty_assumptions(24)
    assumptions.costs.capacity_basis = "planned"
    assumptions.costs.planned_capacity_head = 1
    assumptions.costs.shed_cost_per_animal_place = 100.0
    assumptions.finance.loan_fraction_of_project_cost = 1.0
    assumptions.finance.loan_term_months = 120
    assumptions.finance.moratorium_months = 0
    assumptions.finance.interest_rate_annual = 0.0

    core = _run_core(assumptions)
    terminal_balance = core.amortization[23].closing_balance

    assert terminal_balance > 1.0
    assert core.annual_pl[0].principal == pytest.approx(
        sum(row.principal for row in core.amortization[:12])
    )
    assert core.annual_pl[1].principal == pytest.approx(
        sum(row.principal for row in core.amortization[12:24]) + terminal_balance
    )


def test_tax_loss_pool_accumulates_and_is_consumed_across_four_blocks() -> None:
    assumptions = _mutation_empty_assumptions(48)
    assumptions.finance.income_tax_rate = 0.25
    assumptions.finance.tax_loss_carryforward = True
    # Zero the selling-cost defaults so the block P&Ls contain exactly the
    # transaction prices below (transport Rs 100/head would swamp them).
    assumptions.sales.selling_cost_fraction = 0.0
    assumptions.sales.transport_cost_per_head = 0.0
    transactions = [
        (1, 100.0, 0.0),
        (13, 200.0, 0.0),
        (25, 0.0, 250.0),
        (37, 0.0, 100.0),
    ]
    assumptions.events = [
        event
        for month, purchase_price, sale_price in transactions
        for event in (
            HerdEventAssumptions(
                month=month,
                kind="purchase",
                animal_class="female_kid",
                count=1,
                price_per_head=purchase_price,
            ),
            HerdEventAssumptions(
                month=month,
                kind="sale",
                animal_class="female_kid",
                count=1,
                price_per_head=sale_price,
            ),
        )
    ]

    core = _run_core(assumptions)

    assert [row.tax for row in core.annual_pl] == pytest.approx([0.0, 0.0, 0.0, 12.5])
    assert [month.month for month in core.months if month.tax != 0.0] == [48]
    final = core.months[-1]
    assert final.tax == pytest.approx(12.5)
    assert final.net_cash_flow == pytest.approx(
        final.sales_revenue
        + final.cull_revenue
        + final.milk_revenue
        + final.manure_revenue
        + final.terminal_value
        - final.feed_cost
        - final.vet_cost
        - final.labour_cost
        - final.insurance_cost
        - final.misc_cost
        - final.selling_cost
        - final.purchase_cost
        - final.debt_service
        - final.tax
    )
    times = [0.0, *[month.month / 12.0 for month in core.months]]
    benefits = [
        0.0,
        *[
            month.sales_revenue
            + month.cull_revenue
            + month.milk_revenue
            + month.manure_revenue
            + month.terminal_value
            for month in core.months
        ],
    ]
    costs = [
        core.equity,
        *[
            month.feed_cost
            + month.vet_cost
            + month.labour_cost
            + month.insurance_cost
            + month.misc_cost
            + month.selling_cost
            + month.purchase_cost
            + month.debt_service
            + month.tax
            for month in core.months
        ],
    ]
    assert core.bcr == pytest.approx(
        bcr(assumptions.finance.discount_rate_annual, benefits, costs, times)
    )


@pytest.mark.parametrize("carryforward", [True, False])
def test_subunit_taxable_profit_is_taxed_with_or_without_carryforward(carryforward: bool) -> None:
    assumptions = _mutation_empty_assumptions()
    assumptions.finance.income_tax_rate = 0.5
    assumptions.finance.tax_loss_carryforward = carryforward
    assumptions.sales.selling_cost_fraction = 0.0
    assumptions.sales.transport_cost_per_head = 0.0
    assumptions.events = [
        HerdEventAssumptions(
            month=1,
            kind="purchase",
            animal_class="female_kid",
            count=1,
            price_per_head=0.0,
        ),
        HerdEventAssumptions(
            month=1,
            kind="sale",
            animal_class="female_kid",
            count=1,
            price_per_head=0.5,
        ),
    ]

    core = _run_core(assumptions)

    assert core.annual_pl[0].profit_before_tax == pytest.approx(0.5)
    assert core.annual_pl[0].tax == pytest.approx(0.25)
    assert core.tax_total == pytest.approx(0.25)


def test_taxable_profit_deducts_each_months_interest_and_depreciation() -> None:
    assumptions = _mutation_empty_assumptions(24)
    assumptions.costs.capacity_basis = "planned"
    assumptions.costs.planned_capacity_head = 1
    assumptions.costs.shed_cost_per_animal_place = 1_200.0
    assumptions.finance.loan_fraction_of_project_cost = 1.0
    assumptions.finance.loan_term_months = 24
    assumptions.finance.moratorium_months = 0
    assumptions.finance.interest_rate_annual = 0.12
    assumptions.finance.income_tax_rate = 0.25
    assumptions.events = [
        event
        for month in (1, 13)
        for event in (
            HerdEventAssumptions(
                month=month,
                kind="purchase",
                animal_class="female_kid",
                count=1,
                price_per_head=0.0,
            ),
            HerdEventAssumptions(
                month=month,
                kind="sale",
                animal_class="female_kid",
                count=1,
                price_per_head=1_000.0,
            ),
        )
    ]

    core = _run_core(assumptions)

    for start, annual in zip((0, 12), core.annual_pl, strict=True):
        months = core.months[start : start + 12]
        interest = sum(core.amortization[month.month - 1].interest for month in months)
        taxable = (
            sum(
                month.sales_revenue
                + month.cull_revenue
                + month.milk_revenue
                + month.manure_revenue
                - month.feed_cost
                - month.vet_cost
                - month.labour_cost
                - month.insurance_cost
                - month.misc_cost
                - month.selling_cost
                - month.purchase_cost
                for month in months
            )
            - interest
            - sum(month.depreciation for month in months)
        )
        assert taxable > 0.0
        assert annual.tax == pytest.approx(taxable * assumptions.finance.income_tax_rate)


@pytest.mark.parametrize(
    ("summary_field", "month_field"),
    [
        ("annual_homegrown_green_kg", "feed_homegrown_green_kg"),
        ("annual_dry_kg", "feed_dry_kg"),
        ("annual_concentrate_kg", "feed_concentrate_kg"),
        ("annual_feed_cost", "feed_cost"),
        ("annual_fodder_waste_kg_dm", "fodder_waste_kg_dm"),
    ],
)
def test_ragged_annual_feed_summaries_are_disjoint_twelve_month_blocks(
    summary_field: str,
    month_field: str,
) -> None:
    assumptions = _mutation_empty_assumptions(25)
    assumptions.herd.does = 1
    assumptions.herd.foundation_flock_state = "open"
    assumptions.reproduction.conception_rate = 0.0
    assumptions.feed.cultivated_fodder_acres = 1.0
    assumptions.feed.green_price_per_kg = 1.0
    assumptions.feed.dry_price_per_kg = 1.0
    assumptions.feed.concentrate_price_per_kg = 1.0

    core = _run_core(assumptions)
    observed = getattr(core.feed_summary, summary_field)
    expected = [
        sum(getattr(month, month_field) for month in core.months[start : start + 12])
        for start in (0, 12, 24)
    ]

    assert all(getattr(core.months[index], month_field) > 0.0 for index in (0, 12, 24))
    assert observed == pytest.approx(expected)


def test_fractional_green_purchase_counts_as_a_fodder_deficit_month() -> None:
    assumptions = _mutation_empty_assumptions()
    assumptions.herd.does = 1
    assumptions.herd.foundation_flock_state = "open"
    assumptions.reproduction.conception_rate = 0.0
    assumptions.feed.grazing_dm_fraction = 0.999
    # No cultivation: the point under test is a tiny PURCHASE still being a
    # deficit month (the calibrated 3-acre default would cover it at home).
    assumptions.feed.cultivated_fodder_acres = 0.0

    core = _run_core(assumptions)

    assert all(0.0 < month.feed_purchased_green_kg < 1.0 for month in core.months)
    assert core.feed_summary.fodder_deficit_months == 12


# ---------------------------------------------------------------------------
# (n) Audit-remediation pins: repeat-breeder cull default parity
# ---------------------------------------------------------------------------
def test_repeat_cull_default_matches_species_profile() -> None:
    """The simulation's default service-cull cap cannot drift from the
    operational flag (models.species.GOAT_PROFILE.failed_services_before_cull
    = 2: the daily-ops write path flags a doe as a cull candidate after two
    failed services). app.simulation must stay DB-free (mutmut), so the two
    are single-sourced by this test, not by an import."""
    from app.models.species import GOAT_PROFILE

    assert (
        SimulationAssumptions().reproduction.max_services_before_cull
        == GOAT_PROFILE.failed_services_before_cull
    )


def test_default_run_actually_culls_repeat_breeders() -> None:
    """The new default is not just a number: with every other removal channel
    closed, the engine itself must book repeat-breeder culls — and the
    retention pipeline must replace them (the doe pool recovers)."""
    a = SimulationAssumptions()
    a.mortality.adult = 0.0
    a.mortality.grower = 0.0
    a.culling.doe_cull_rate_annual = 0.0
    a.culling.max_doe_age_months = 180
    a.culling.buck_rotation_years = 10  # no rotation inside the 36 months
    a.meta.horizon_months = 36
    # Conception 0.7 fails enough services to cull, but leaves the retention
    # pipeline able to hold the breeding pool near its cap.
    a.reproduction.parity_multipliers = ParityMultipliers(litter_size=[1.0], conception_rate=[0.7])
    res = run_simulation(a, with_break_even=False)
    total_culls = sum(row.culls_head for row in res.months)
    assert total_culls > 0.0  # the repeat-breeder channel fired
    # Disabling the policy on the same biology removes exactly those culls.
    parked = a.model_copy(deep=True)
    parked.reproduction.max_services_before_cull = 0
    parked_res = run_simulation(parked, with_break_even=False)
    assert sum(row.culls_head for row in parked_res.months) == pytest.approx(0.0)
    assert sum(row.culls_head for row in parked_res.months) < total_culls
    # Replacement: the doe pool is held near the cap by retained daughters
    # despite the culls (the retention logic refills the breeding pool).
    final_does = (
        res.months[-1].open_does + res.months[-1].pregnant_does + res.months[-1].lactating_does
    )
    assert final_does >= a.herd.does * 0.75
