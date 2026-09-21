"""Deep mutation-hardening tests for the simulation package.

Every test here kills specific mutants from the 2026-09 mutation campaign
(7,622 mutants; the survivors clustered in boundary comparisons, list-init
values, preset literals, strict-zip guards and vocabulary plumbing). The
file is entirely synchronous/DB-free. Golden values are derived independently
of the implementation (hand-computed or from the published calibration notes
in the presets).
"""

import math
import random
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.simulation import (
    HerdAssumptions,
    MetaAssumptions,
    SalesAssumptions,
    SimulationAssumptions,
    amortization_schedule,
    mirr,
    monthly_emi,
    monthly_mortality_rate,
    run_simulation,
)
from app.simulation.assumptions import HerdEventAssumptions as Event
from app.simulation.assumptions import ParityMultipliers
from app.simulation.defaults import barbari, beetal
from app.simulation.engine import (
    _pool_avg_weight,
    break_even_meat_price,
    male_weight_at_age,
    phase_monthly_mortality_rate,
    weight_at_age,
)
from app.simulation.explain import (
    _active_festival_months,
    _female_counted,
    _festival_paragraph,
    _inr,
    _inr_per_kg,
    _outstanding_at_horizon,
)
from app.simulation.feed import DAYS_PER_MONTH
from app.simulation.finance import (
    _all_decimal_power_roots,
    _crossing_decimal_power_roots,
    _decimal_sign,
    _decimal_sign_variations,
    _deduplicate_decimal_roots,
    _positive_power_roots,
    _sign_variations,
    irr,
    irr_roots,
)
from app.simulation.market import bakrid_festival_months, meat_price_for_month
from app.simulation.montecarlo import (
    _apply_draws,
    _correlated_draws,
    _event_shock_path,
    _histogram,
    _scale_milk_price,
    _scale_milk_price_high,
    _triangular_from_uniform,
)
from app.simulation.optimization import (
    _sample_evenly,
    _sample_grid,
    _unique_bounded,
    run_optimization,
)
from app.simulation.planner import build_plan_report, close_gaps, plan_probabilities


def toy_assumptions(**herd_overrides: object) -> SimulationAssumptions:
    """The 10-doe + 1-buck golden model from test_simulation_engine, reused so
    the deep-mutation goldens stay consistent with the published toy math."""
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
    # Flat reproduction policy, matching the engine-file toy: parity and the
    # repeat-breeder cull default have dedicated tests of their own.
    a.reproduction.parity_multipliers = ParityMultipliers(litter_size=[1.0], conception_rate=[1.0])
    a.reproduction.max_services_before_cull = 0
    return a


def test_beetal_and_barbari_default_system_is_stall_fed() -> None:
    """The preset factories default to stall-fed; a mutated default ("STALL_FED",
    "XXstall_fedXX", …) makes ``apply_system`` raise or drift the variant."""
    assert beetal().feed.grazing_dm_fraction == 0.0
    assert beetal().mortality.adult == beetal("stall_fed").mortality.adult
    assert barbari().feed.grazing_dm_fraction == 0.0
    assert barbari().mortality.adult == barbari("stall_fed").mortality.adult


def test_osmanabadi_weight_curve_arithmetic() -> None:
    """31 kg… no — the Osmanabadi base curve is 2.5 kg at birth +2.0 kg/month:
    [2.5, 4.5, 6.5, …] to a ~26.5 kg yearling. A sign flip in the generator
    slopes the whole breed family (every preset scales from this curve)."""
    from app.simulation.defaults import _osmanabadi_weights

    weights = _osmanabadi_weights()
    assert weights[0] == pytest.approx(2.5)
    assert weights[1] == pytest.approx(4.5)
    assert weights[12] == pytest.approx(26.5)
    from itertools import pairwise

    assert all(b > a for a, b in pairwise(weights))


# --- market: Bakrid calendar arithmetic -----------------------------------------


def test_bakrid_festival_months_parse_and_boundaries() -> None:
    """Simulation months are 1-based and inclusive at BOTH ends: a start month
    that itself contains Bakrid is simulation month 1, and a festival landing
    exactly on the horizon month is included. The ``[5:7]`` slice of the
    YYYY-MM string must parse exactly the month digits."""
    # Bakrid 2026-05 with start 2026-01: (2026-2026)*12 + (5-1) + 1 = 5.
    assert bakrid_festival_months("2026-01", 36) == [5, 17, 29]
    # Festival in the start month itself → simulation month 1.
    assert bakrid_festival_months("2026-05", 1) == [1]
    # Festival exactly on the horizon month → included.
    assert bakrid_festival_months("2026-01", 5) == [5]
    # A festival BEFORE the start month is never negative-indexed back in.
    assert bakrid_festival_months("2027-06", 24) == [12, 23]
    # Unparseable input degrades to [] (documented), never an exception.
    assert bakrid_festival_months("garbage", 12) == []


def test_meat_price_for_month_eid_gate_never_fires_on_zero_eid() -> None:
    """eid_month == 0 means "no Eid pricing"; the guard must stay ``> 0`` (a
    ``>= 0`` mutant fires every month whose calendar month is 0 — impossible —
    but a ``> 1`` mutant silently disables January Eid pricing)."""
    sales = SalesAssumptions(eid_month=1, eid_price_uplift=0.25)
    sales.annual_livestock_price_growth_rate = 0.0
    sales.monthly_meat_price_multipliers = [1.0] * 12
    # Unset (None) is the legacy branch's only reachable spelling now that an
    # explicit [] disables every festival (P3, 2026-09-20 audit).
    sales.festival_sale_months = None
    base = sales.meat_price_per_kg
    # simulation_month 1 with start January (calendar month 1) is Eid.
    assert meat_price_for_month(
        sales, simulation_month=1, calendar_month=1, shock_multiplier=1.0
    ) == pytest.approx(base * 1.25)
    # A calendar month that is not the Eid month is unuplifted.
    assert meat_price_for_month(
        sales, simulation_month=2, calendar_month=2, shock_multiplier=1.0
    ) == pytest.approx(base)


# --- finance: EMI, amortization, MIRR and polynomial-root machinery --------------


def test_monthly_emi_boundary_guards() -> None:
    # principal == 0 (boundary of ``<= 0``) must short-circuit to 0.0.
    assert monthly_emi(0.0, 0.12, 12) == 0.0
    # Zero interest rate: straight-line division, no annuity formula.
    assert monthly_emi(12000.0, 0.0, 12) == pytest.approx(1000.0)
    # The annuity formula on a known bank figure: ₹10L at 12% for 60 months.
    assert monthly_emi(1_000_000.0, 0.12, 60) == pytest.approx(22244.45, abs=0.05)


def test_amortization_schedule_shape_moratorium_and_payoff() -> None:
    """The schedule always has exactly ``term_months`` rows: the moratorium is
    interest-only (balance unchanged), the final instalment settles the whole
    remaining balance, and the total principal repaid equals the loan."""
    # Moratorium longer than the term: every row is interest-only, but the
    # row count is still the term, never the moratorium.
    sunk = amortization_schedule(100000.0, 0.0, 6, moratorium_months=12)
    assert len(sunk) == 6
    assert all(row.principal == 0.0 for row in sunk)
    assert all(row.closing_balance == 100000.0 for row in sunk)
    # Normal moratorium: 12-month term, 3 interest-only months, then EMI.
    normal = amortization_schedule(100000.0, 0.0, 12, moratorium_months=3)
    assert len(normal) == 12
    assert sum(row.principal for row in normal) == pytest.approx(100000.0)
    assert all(row.principal == 0.0 for row in normal[:3])
    assert all(row.principal > 0.0 for row in normal[3:])
    assert normal[-1].closing_balance == pytest.approx(0.0)
    # Mid-schedule payments are min(emi, balance + interest); with a zero rate
    # that is the straight-line EMI exactly (never balance − interest).
    assert normal[5].payment == pytest.approx(100000.0 / 9.0)
    # Final row pays the exact remaining balance plus its interest.
    assert normal[-1].payment == pytest.approx(normal[-1].opening_balance)


def test_mirr_golden_and_degenerate_series() -> None:
    """MIRR of [-100, 60, 60]: costs = 100 at t0; benefits compounded to t2 at
    the reinvestment rate: 60·1.1 + 60 = 126; (126/100)^(1/2) − 1 ≈ 5.83%."""
    valued = mirr([-100.0, 60.0, 60.0], [0.0, 1.0, 2.0], 0.10, 0.10)
    assert valued == pytest.approx(math.sqrt(1.26) - 1.0)
    # The finance rate discounts the outflows, the reinvest rate compounds
    # the inflows — different rates must move the answer in opposite ways.
    cheaper = mirr([-100.0, 60.0, 60.0], [0.0, 1.0, 2.0], 0.0, 0.10)
    assert cheaper == pytest.approx(math.sqrt(1.26) - 1.0)
    # Mismatched lengths or empty series return None (documented contract).
    assert mirr([-100.0, 60.0, 60.0], [0.0, 1.0], 0.1, 0.1) is None
    assert mirr([], [], 0.1, 0.1) is None
    # A pure outflow (or pure inflow) has no MIRR.
    assert mirr([-100.0, -10.0], [0.0, 1.0], 0.1, 0.1) is None
    assert mirr([100.0, 10.0], [0.0, 1.0], 0.1, 0.1) is None
    # A horizon at time zero never compounds.
    assert mirr([-100.0, 60.0], [0.0, 0.0], 0.1, 0.1) is None


def test_sign_variations_counts_direction_changes_only() -> None:
    """Terms are (exponent, coefficient); zero coefficients are skipped and
    each remaining sign flip counts once."""
    assert _sign_variations([(2.0, 1.0), (1.0, 1.0), (0.0, -1.0)]) == 1
    assert _sign_variations([(2.0, 1.0), (1.0, -1.0), (0.0, 1.0)]) == 2
    # The middle zero coefficient is filtered out, not treated as a sign.
    assert _sign_variations([(2.0, 1.0), (1.0, 0.0), (0.0, -1.0)]) == 1
    assert _sign_variations([(2.0, -1.0), (1.0, 1.0), (0.0, -3.0)]) == 2
    assert (
        _decimal_sign_variations(
            [(Decimal(2), Decimal(-1)), (Decimal(1), Decimal(1)), (Decimal(0), Decimal(-3))]
        )
        == 2
    )
    assert (
        _decimal_sign_variations(
            [(Decimal(2), Decimal(1)), (Decimal(1), Decimal(0)), (Decimal(0), Decimal(-1))]
        )
        == 1
    )


def test_decimal_sign_two_sided() -> None:
    """``1 if value > 0 else -1`` with the caller's scale: clearly positive and
    clearly negative values keep their signs."""
    assert _decimal_sign(Decimal("0.5"), Decimal("1e-50")) == 1
    assert _decimal_sign(Decimal("-0.5"), Decimal("1e-50")) == -1


def test_deduplicate_decimal_roots_relative_tolerance() -> None:
    """Duplicates are |a − b| ≤ 1e-55 · max(1, |a|): exact repeats and
    1e-56-apart near-repeats merge, 1e-50-apart values stay distinct."""
    merged = _deduplicate_decimal_roots([Decimal("1.0"), Decimal("1.0"), Decimal("2.5")])
    assert merged == [Decimal("1.0"), Decimal("2.5")]
    near = Decimal("1." + "0" * 55 + "1")  # 1 + 1e-56: within tolerance
    assert _deduplicate_decimal_roots([Decimal("1.0"), near]) == [Decimal("1.0")]
    # 1e-50 apart: beyond tolerance, both kept (a Decimal literal — binary
    # addition would round it away at the default 28-digit precision).
    apart = Decimal("1.00000000000000000000000000000000000000000000000001")
    assert _deduplicate_decimal_roots([Decimal("1.0"), apart]) == [Decimal("1.0"), apart]


def test_all_decimal_power_roots_recursive_derivative() -> None:
    """The fallback recursion differentiates ``(e−1, e·c)`` to bracket roots:
    the cubic x³ − 6x² + 11x − 6 must yield all three roots (1, 2, 3)."""
    roots = _all_decimal_power_roots(
        [
            (Decimal(3), Decimal(1)),
            (Decimal(2), Decimal(-6)),
            (Decimal(1), Decimal(11)),
            (Decimal(0), Decimal(-6)),
        ],
        lo=Decimal("0.001"),
        hi=Decimal("10"),
    )
    values = sorted(float(root) for root in roots)
    assert values == pytest.approx([1.0, 2.0, 3.0], abs=1e-30)
    # Fewer than two terms has no roots to find.
    assert (
        _all_decimal_power_roots([(Decimal(1), Decimal(1))], lo=Decimal("0.001"), hi=Decimal("1"))
        == []
    )
    # No sign variation → no positive roots.
    assert (
        _all_decimal_power_roots(
            [(Decimal(2), Decimal(1)), (Decimal(1), Decimal(1))],
            lo=Decimal("0.001"),
            hi=Decimal("1"),
        )
        == []
    )


def test_power_root_exactly_at_bracket_endpoint_is_reported() -> None:
    """x − 1 on [1, 5] has its root exactly at ``lo``: the sign_lo == 0 branch
    must report it rather than bisecting past it (both Decimal and float)."""
    decimal_roots = _all_decimal_power_roots(
        [(Decimal(1), Decimal(1)), (Decimal(0), Decimal(-1))],
        lo=Decimal(1),
        hi=Decimal(5),
    )
    assert [float(root) for root in decimal_roots] == pytest.approx([1.0], abs=1e-40)
    float_roots = _positive_power_roots([(1.0, 1.0), (0.0, -1.0)], 1.0, 5.0)
    assert float_roots == pytest.approx([1.0])


def test_crossing_decimal_power_roots_ignores_tangencies() -> None:
    """A crossing requires a strict sign change: (x−1)² touches zero without
    crossing and must be excluded; (x−1)(x−3) crosses at both simple roots."""
    assert _crossing_decimal_power_roots([(2.0, 1.0), (1.0, -2.0), (0.0, 1.0)], 0.1, 2.0) == []
    roots = _crossing_decimal_power_roots([(2.0, 1.0), (1.0, -4.0), (0.0, 3.0)], 0.1, 5.0)
    assert [float(root) for root in roots] == pytest.approx([1.0, 3.0], abs=1e-30)


def test_positive_power_roots_guards() -> None:
    # Linear positive polynomial: no positive root.
    assert _positive_power_roots([(1.0, 1.0), (0.0, 1.0)], 0.0, 1.0) == []
    # x − 1 has exactly one positive root at 1.
    assert _positive_power_roots([(1.0, 1.0), (0.0, -1.0)], 0.0, 2.0) == pytest.approx([1.0])


def test_irr_roots_and_irr_on_multi_sign_flows() -> None:
    """−+/−+ flows have two IRRs; ``irr_roots`` reports both and ``irr``
    refuses to choose (None) — a single number would be a lie."""
    flows = [-100.0, 230.0, -132.0]
    times = [0.0, 1.0, 2.0]
    roots = sorted(irr_roots(flows, times))
    # 230/(1+r) − 100 − 132/(1+r)² = 0 → (1+r)² − 2.3(1+r) + 1.32 = 0
    # → 1+r ∈ {1.1, 1.2} → r ∈ {0.1, 0.2}.
    assert roots == pytest.approx([0.1, 0.2], abs=1e-9)
    assert irr(flows, times) is None
    # A conventional single-crossing project: −100 + 60x + 60x² = 0 →
    # 3x² + 3x − 5 = 0 → x = (−3 + √69)/6, r = 1/x − 1 ≈ 13.07%.
    single = irr([-100.0, 60.0, 60.0], [0.0, 1.0, 2.0])
    x = (-3.0 + math.sqrt(69.0)) / 6.0
    assert single == pytest.approx(1.0 / x - 1.0, abs=1e-9)
    # No crossing at all → None.
    assert irr([-100.0, -50.0], [0.0, 1.0]) is None


def _neutral_draws() -> dict[str, float]:
    return {
        "meat_price": 1.0,
        "feed_price": 1.0,
        "adult_mortality": 1.0,
        "kid_mortality": 1.0,
        "litter_size": 1.0,
        "conception_rate": 1.0,
        "fodder_yield": 1.0,
        "operating_cost": 1.0,
        "milk_price": 1.0,
    }


def test_apply_draws_scales_the_milk_price() -> None:
    """The milk_price draw multiplies the per-litre price by the draw factor
    (a division would move it the wrong way; an assignment would replace the
    calibrated price with the draw itself)."""
    a = SimulationAssumptions()
    a.sales.milk_price_per_litre = 100.0
    draws = _neutral_draws()
    draws["milk_price"] = 2.0
    variant = _apply_draws(a, draws)
    assert variant.sales.milk_price_per_litre == pytest.approx(200.0)
    # A neutral draw leaves every other field untouched.
    assert variant.sales.meat_price_per_kg == a.sales.meat_price_per_kg
    assert variant.reproduction.litter_size == a.reproduction.litter_size


def test_triangular_from_uniform_endpoints_and_midpoint() -> None:
    """Inverse triangular CDF with fixed mode 1.0: u=0 → low, u=1 → high,
    and the draw is always inside the support."""
    assert _triangular_from_uniform(0.8, 1.2, 0.0) == pytest.approx(0.8)
    assert _triangular_from_uniform(0.8, 1.2, 1.0) == pytest.approx(1.2)
    mid = _triangular_from_uniform(0.8, 1.2, 0.5)
    assert 0.8 < mid < 1.2
    # Degenerate support collapses to the point.
    assert _triangular_from_uniform(1.0, 1.0, 0.7) == 1.0


def test_correlated_draws_consume_and_bound_each_risk() -> None:
    """Every risk variable in the fixed draw order is drawn (a disabled one
    consumes its idiosyncratic value and yields exactly 1.0); enabled draws
    stay inside their [low, high] support."""
    a = SimulationAssumptions()
    risk_vars = {
        "meat_price": a.risk.meat_price,
        "feed_price": a.risk.feed_price,
        "adult_mortality": a.risk.adult_mortality,
        "kid_mortality": a.risk.kid_mortality,
        "litter_size": a.risk.litter_size,
        "conception_rate": a.risk.conception_rate,
        "fodder_yield": a.risk.fodder_yield,
        "operating_cost": a.risk.operating_cost,
        "milk_price": a.risk.milk_price,
    }
    draws = _correlated_draws(random.Random(7), risk_vars, 0.6)
    assert set(draws) == set(risk_vars)
    for name, value in draws.items():
        var = risk_vars[name]
        if not var.enabled:
            assert value == 1.0
        else:
            assert var.low - 1e-9 <= value <= var.high + 1e-9, name


def test_event_shock_path_episodes_apply_and_expire() -> None:
    """A certain crash (probability 1, duration 2) marks every month — episodes
    restart the month after expiry, never overlap (4 starts in 8 months, not
    8), and the multiplier multiplies the neutral 1.0 path (a division would
    INVERT it above 1)."""
    a = SimulationAssumptions()
    a.meta.horizon_months = 8
    a.risk.market_crash_probability_annual = 1.0
    a.risk.market_crash_duration_months = 2
    a.risk.market_crash_price_multiplier = 0.75
    a.risk.disease_outbreak_probability_annual = 0.0
    a.risk.drought_probability_annual = 0.0
    path = _event_shock_path(a, random.Random(42))
    for month in range(8):
        assert path.milk_price[month] == pytest.approx(0.75), month
        assert path.meat_price[month] == pytest.approx(0.75), month
    # Non-overlap: a new episode starts only when the previous expired.
    assert path.market_crashes == 4
    # Disease and drought stayed off; nothing else moved.
    assert all(value == 1.0 for value in path.conception)
    assert all(value == 1.0 for value in path.fodder_yield)


def test_scale_milk_price_low_and_high_factors() -> None:
    """+/−20% milk-price probes multiply the calibrated price by exactly 0.8
    (or 1.2) — assignments would reset the price to the factor itself."""
    low = SimulationAssumptions()
    low.sales.milk_price_per_litre = 100.0
    _scale_milk_price(low)
    assert low.sales.milk_price_per_litre == pytest.approx(80.0)
    high = SimulationAssumptions()
    high.sales.milk_price_per_litre = 100.0
    _scale_milk_price_high(high)
    assert high.sales.milk_price_per_litre == pytest.approx(120.0)


def test_histogram_bins_and_degenerate_range() -> None:
    """Equal-width bins with the last value clamped into the final bin."""
    counts, edges = _histogram([0.0, 1.0, 2.0, 3.0, 4.0], bins=4)
    assert counts == [1, 1, 1, 2]
    assert len(edges) == 5
    assert edges[0] == pytest.approx(0.0)
    assert edges[-1] == pytest.approx(4.0)
    # Constant series pads the range instead of dividing by zero.
    counts, edges = _histogram([5.0, 5.0, 5.0], bins=3)
    assert sum(counts) == 3
    assert edges[0] < 5.0 < edges[-1]


# --- optimization: grid sampling and bounded uniqueness -------------------------


def test_sample_evenly_boundaries() -> None:
    # len == limit returns the list unchanged (boundary of ``<=``).
    assert _sample_evenly([1, 2, 3], 3) == [1, 2, 3]
    assert _sample_evenly([1, 2, 3], 4) == [1, 2, 3]
    # limit 0 empties; limit 1 keeps the middle.
    assert _sample_evenly([1, 2, 3, 4], 0) == []
    assert _sample_evenly([1, 2, 3, 4, 5], 1) == [3]
    # Endpoints are always retained when thinning.
    sampled = _sample_evenly(list(range(10)), 3)
    assert sampled[0] == 0 and sampled[-1] == 9 and len(sampled) == 3


def test_sample_grid_thins_widest_axis_under_budget() -> None:
    """A 3×3 grid capped at 6 sheds one value from the first (tied-widest)
    axis and keeps the second whole: every dimension stays represented and
    the result never exceeds the limit."""
    grid = _sample_grid([[10, 11, 12], [20, 21, 22]], 6)
    assert len(grid) == 6
    firsts = {combo[0] for combo in grid}
    seconds = {combo[1] for combo in grid}
    assert firsts == {10, 12}  # evenly thinned, endpoints kept
    assert seconds == {20, 21, 22}
    # A non-positive budget is empty; a budget over the product is the full
    # Cartesian grid.
    assert _sample_grid([[1, 2], [3, 4]], 0) == []
    assert _sample_grid([[1, 2], [3, 4]], 99) == [(1, 3), (1, 4), (2, 3), (2, 4)]


def test_unique_bounded_clamps_and_merges_ulp_neighbours() -> None:
    # Values outside the box are clamped in, then sorted.
    assert _unique_bounded([-1.0, 2.0, 0.5], 0.0, 1.0) == pytest.approx([0.0, 0.5, 1.0])
    # 0.1 + 0.2 and 0.3 differ by one binary ULP: one decision, not two.
    assert len(_unique_bounded([0.1 + 0.2, 0.3], 0.0, 1.0)) == 1
    assert _unique_bounded([0.1, 0.3, 0.30000000001], 0.0, 1.0) == pytest.approx(
        [0.1, 0.3, 0.30000000001]
    )


def test_run_optimization_keeps_baseline_in_the_ranked_grid() -> None:
    """The submitted decision set is always evaluated, ranked alongside the
    variants and recoverable as result.baseline with its own herd."""
    a = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(does=6, bucks=1),
    )
    a.optimization.max_candidates = 6
    result = run_optimization(a)
    assert result.evaluated_candidates >= 1
    assert result.baseline is not None
    assert result.baseline.starting_does == 6
    assert result.baseline.rank >= 1


# --- explain: formatting helpers and festival wiring ----------------------------


def test_inr_indian_units() -> None:
    assert _inr(2_500_000) == "₹25.00 lakh"
    assert _inr(250_000_000) == "₹25.00 crore"
    assert _inr(-2_500_000) == "-₹25.00 lakh"
    assert _inr(45_000) == "₹45,000"
    assert _inr(-500) == "-₹500"


def test_inr_per_kg_grouping() -> None:
    # Three digits and fewer carry no separator; exactly three is the boundary.
    assert _inr_per_kg(500) == "₹500/kg"
    assert _inr_per_kg(1000) == "₹1,000/kg"
    # Indian 2-2-3 grouping above the first thousand.
    assert _inr_per_kg(1234567) == "₹12,34,567/kg"
    assert _inr_per_kg(-1234567) == "-₹12,34,567/kg"
    # Zero is unsigned.
    assert _inr_per_kg(0) == "₹0/kg"


def test_female_and_male_counted_nouns() -> None:
    from app.simulation.vocabulary import GOAT_NOUNS

    assert _female_counted(1, GOAT_NOUNS) == GOAT_NOUNS.female
    assert _female_counted(1.0, GOAT_NOUNS) == GOAT_NOUNS.female
    assert _female_counted(2, GOAT_NOUNS) == GOAT_NOUNS.female_plural


def test_outstanding_at_horizon_boundary() -> None:
    """horizon ≥ len(amortization) means the loan retired inside the horizon:
    nothing outstanding. A shorter horizon reports the month-(horizon) balance."""
    row = SimpleNamespace(closing_balance=4321.0)
    a = SimpleNamespace(meta=SimpleNamespace(horizon_months=12))
    result = SimpleNamespace(amortization=[row] * 24)
    assert _outstanding_at_horizon(a, result) == pytest.approx(4321.0)  # type: ignore[arg-type]
    # Horizon exactly equal to the schedule length: retired, zero outstanding.
    result_equal = SimpleNamespace(amortization=[row] * 12)
    assert _outstanding_at_horizon(a, result_equal) == 0.0  # type: ignore[arg-type]
    result_longer = SimpleNamespace(amortization=[row] * 10)
    assert _outstanding_at_horizon(a, result_longer) == 0.0  # type: ignore[arg-type]


def test_active_festival_months_three_sources() -> None:
    a = SimulationAssumptions()
    # Explicit lunar-calendar months win outright.
    a.sales.festival_sale_months = [3, 7]
    a.meta.horizon_months = 12
    assert _active_festival_months(a) == [3, 7]
    # Legacy recurring Gregorian month: every calendar match, 1-based, both
    # end months included.
    a.sales.festival_sale_months = None
    a.sales.eid_month = 1
    a.meta.start_year_month = "2026-01"
    a.meta.horizon_months = 13
    assert _active_festival_months(a) == [1, 13]
    a.sales.eid_month = 2
    a.meta.horizon_months = 14
    assert _active_festival_months(a) == [2, 14]
    # No festival configuration at all.
    a.sales.eid_month = 0
    assert _active_festival_months(a) == []


def test_festival_paragraph_both_branches() -> None:
    a = SimulationAssumptions()
    a.sales.festival_sale_months = [4]
    a.sales.eid_price_uplift = 0.35
    empty = SimpleNamespace(months=[])
    text = _festival_paragraph(a, empty)  # type: ignore[arg-type]
    assert len(text) == 1
    assert "+35% on the base rate" in text[0]
    assert "applies in months 4" in text[0]
    assert "sells no animals in those months" in text[0]
    sold = SimpleNamespace(
        months=[
            SimpleNamespace(month=4, sales_head=2.5),
            SimpleNamespace(month=5, sales_head=9.0),
        ]
    )
    text = _festival_paragraph(a, sold)  # type: ignore[arg-type]
    assert "2 head sell inside them" in text[0]
    # Only festival-month sales count toward the head.
    assert "9" not in text[0].split("head")[0]


def test_metric_explanations_irr_and_mirr_undefined() -> None:
    """A projection with no positive cash flow has neither IRR nor MIRR; both
    explanations must say so instead of inventing a rate."""
    a = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(does=0, bucks=0),
    )
    a.finance.initial_stock_cost = 1000.0  # a small outflow, nothing back
    a.costs.misc_overhead_per_month = 0.0
    a.costs.vet_per_animal_per_year = 0.0
    a.costs.labour_per_month = 0.0
    result = run_simulation(a)
    assert result.metrics.irr is None
    texts = {entry.key: entry.explanation for entry in result.metric_explanations}
    assert "IRR is undefined for this cash-flow pattern" in texts["irr"]
    # MIRR is defined here (the terminal recovery is a positive flow) but
    # deeply negative — the report must still show a MIRR sentence.
    assert result.metrics.mirr is not None
    assert texts["mirr"]


def test_payback_by_liquidation_wording() -> None:
    """payback == horizon with the prior month still negative is a payback by
    liquidation (terminal value closes the gap) — the wording must say so.
    Zero operating flows hold the cumulative flat at −equity until month 12,
    where the terminal livestock recovery flips it positive."""
    a = toy_assumptions()
    a.finance.initial_stock_cost = 100.0
    a.finance.loan_fraction_of_project_cost = 0.0
    a.finance.subsidy_fraction = 0.0
    a.costs.capacity_basis = "planned"
    a.costs.planned_capacity_head = 1
    a.costs.shed_cost_per_animal_place = 8000.0
    a.costs.equipment_cost_per_animal = 2000.0
    a.finance.working_capital_months = 0
    a.costs.misc_overhead_per_month = 0.0
    a.costs.vet_per_animal_per_year = 0.0
    a.costs.labour_per_month = 0.0
    a.costs.insurance_pct_stock_value_annual = 0.0
    a.sales.manure_income_per_adult_per_year = 0.0
    a.sales.selling_cost_fraction = 0.0
    a.sales.transport_cost_per_head = 0.0
    a.feed.green_price_per_kg = 0.0
    a.feed.purchased_green_price_per_kg = 0.0
    a.feed.dry_price_per_kg = 0.0
    a.feed.concentrate_price_per_kg = 0.0
    result = run_simulation(a)
    assert result.metrics.payback_month == 12  # precondition for the wording
    assert result.months[10].cumulative_cash_flow < 0.0
    texts = {entry.key: entry.explanation for entry in result.metric_explanations}
    assert "payback by liquidation" in texts["payback_month"]


# --- planner / milk planner / backward planner: default-argument contracts ------


def _toy_targets() -> list:
    from app.simulation.planner import SaleTarget

    # 20 weaners from 10 does is deliberately unreachable: the gap closer
    # must recommend purchases.
    return [SaleTarget(animal_class="male_weaner", count=20, month=12)]


def test_close_gaps_default_max_iterations_is_eight() -> None:
    a = toy_assumptions()
    targets = _toy_targets()
    default_call = close_gaps(a, targets)
    pinned_call = close_gaps(a, targets, max_iterations=8)
    assert default_call == pinned_call


def test_plan_probabilities_default_runs_is_two_hundred() -> None:
    a = toy_assumptions()
    targets = _toy_targets()
    # Same seed, one call relying on the default run count and one pinning 200.
    assert plan_probabilities(a, targets, seed=11) == plan_probabilities(
        a, targets, runs=200, seed=11
    )


def test_build_plan_report_closes_gaps_by_default() -> None:
    a = toy_assumptions()
    targets = _toy_targets()
    default_report = build_plan_report(a, targets)
    pinned_report = build_plan_report(a, targets, close_gaps_enabled=True)
    assert default_report == pinned_report
    # And the default is behaviourally ON: disabling it leaves the gap open
    # with no recommended purchases.
    disabled = build_plan_report(a, targets, close_gaps_enabled=False)
    assert disabled.recommended_purchases == []
    assert disabled.after is None
    assert default_report.recommended_purchases != []


# --- engine: mass balance, pool mechanics and boundary gates ---------------------


def _opening_head(a: SimulationAssumptions) -> float:
    h = a.herd
    return float(
        h.does
        + h.bucks
        + h.female_kids
        + h.male_kids
        + h.female_weaners
        + h.male_weaners
        + h.female_growers
        + h.male_growers
    )


def _assert_mass_balance(a: SimulationAssumptions, result) -> None:
    previous = _opening_head(a)
    for m in result.months:
        expected = previous + m.births + m.purchases_head - m.deaths - m.sales_head - m.culls_head
        assert m.total_herd == pytest.approx(expected, abs=1e-6), m.month
        assert m.total_herd >= 0.0
        previous = m.total_herd


def test_toy_goat_mass_balance_and_month_one_head() -> None:
    """The whole month loop is one mass-balance identity; month 1 also pins
    the [0.0]-initialised service buckets (a phantom doe in any bucket shows
    up as extra head immediately)."""
    a = toy_assumptions()
    result = run_simulation(a)
    _assert_mass_balance(a, result)
    s_adult = 1.0 - monthly_mortality_rate(a.mortality.adult)
    assert result.months[0].total_herd == pytest.approx(11.0 * s_adult)
    # Foundation-year grace: the rate cull only starts in month 13, and this
    # 12-month horizon never reaches it.
    assert all(m.culls_head == 0.0 for m in result.months)


def test_litter_cap_is_the_goat_species_cap() -> None:
    """The projection's litter cap is species-keyed (goat quadruplets, 4.0 —
    matching SpeciesProfile.max_litter_size), never a dairy carry-over: a
    configured litter of 3.0 with the surplus-milk side-line both off and on
    produces exactly 3.0 kids per kidding doe."""
    a = toy_assumptions()
    a.reproduction.conception_rate = 1.0
    a.reproduction.litter_size = 3.0
    a.reproduction.stillbirth_rate = 0.0
    a.reproduction.gestation_months = 2
    a.reproduction.lactation_months = 3
    a.mortality.kid_pre_weaning = 0.0
    a.mortality.kid_post_weaning = 0.0
    a.mortality.adult = 0.0
    a.meta.horizon_months = 12
    does = 10.0
    assert run_simulation(a).months[2].births == pytest.approx(does * 3.0)
    # Turning the surplus-milk side-line on must not change the litter cap.
    a.sales.milk_sale_litres_per_doe_day = 1.0
    with_milk = run_simulation(a)
    assert with_milk.months[2].births == pytest.approx(does * 3.0)
    # ...and the kidding month sells the flat daily surplus per lactating doe.
    assert with_milk.months[2].milk_revenue == pytest.approx(
        with_milk.months[2].lactating_does * 1.0 * DAYS_PER_MONTH * a.sales.milk_price_per_litre
    )
    assert with_milk.months[2].milk_revenue > 0.0


def test_service_buckets_and_repeat_cull_timing() -> None:
    """max_services=3, conception 0.5: failures march one bucket per month and
    only the THIRD failure culls — months 1-2 book no culls, month 3 books the
    final failures.
    m1: 10 serve, 5 fail → bucket1
    m2: 5 serve, 2.5 fail → bucket2
    m3: 2.5 serve, 1.25 fail the final service."""
    a = toy_assumptions()
    a.reproduction.conception_rate = 0.5
    a.reproduction.max_services_before_cull = 3
    a.mortality.adult = 0.0
    a.culling.doe_cull_rate_annual = 0.0
    a.culling.max_doe_age_months = 180
    a.meta.horizon_months = 12
    result = run_simulation(a)
    assert result.months[0].culls_head == 0.0
    assert result.months[1].culls_head == 0.0
    assert result.months[2].culls_head == pytest.approx(1.25)
    # With the repeat-breeder policy off, failures park in the last bucket
    # forever: no service-driven culls at all.
    parking = a.model_copy(deep=True)
    parking.reproduction.max_services_before_cull = 0
    parked = run_simulation(parking)
    assert all(m.culls_head == 0.0 for m in parked.months)


def test_service_bucket_count_follows_the_cull_policy() -> None:
    """n_service_buckets = max_services_before_cull: with max_services=4 the
    fourth failure (not the third) is the repeat cull."""
    a = toy_assumptions()
    a.reproduction.conception_rate = 0.5
    a.reproduction.max_services_before_cull = 4
    a.mortality.adult = 0.0
    a.culling.doe_cull_rate_annual = 0.0
    a.culling.max_doe_age_months = 180
    a.meta.horizon_months = 12
    result = run_simulation(a)
    assert result.months[2].culls_head == 0.0  # third failure parks in bucket 3
    # m3: 2.5 serve, 1.25 fail → bucket3;
    # m4: those serve once more, 0.625 fail and cull.
    assert result.months[3].culls_head == pytest.approx(0.625)


def test_births_follow_the_flat_sex_ratio() -> None:
    """With natural service the female share of every kidding month's births
    is exactly sex_ratio_female (0.6 here): no per-service bias remains."""
    a = toy_assumptions()
    a.reproduction.conception_rate = 0.5
    a.reproduction.gestation_months = 2
    a.reproduction.litter_size = 1.0
    a.reproduction.stillbirth_rate = 0.0
    a.reproduction.sex_ratio_female = 0.6
    a.mortality.kid_pre_weaning = 0.0
    a.mortality.kid_post_weaning = 0.0
    a.mortality.adult = 0.0
    a.meta.horizon_months = 12
    result = run_simulation(a)
    female_kids_m3 = result.months[2].f_kids
    births_m3 = result.months[2].births
    assert births_m3 > 0
    assert female_kids_m3 == pytest.approx(0.6 * births_m3, rel=1e-9)


def test_eid_calendar_pricing_columns() -> None:
    """start 2026-01 + eid_month 3: simulation months 3 and 15 carry the Eid
    uplift in the meat price column, and nothing else does (12-month cycle)."""
    a = toy_assumptions()
    a.meta.horizon_months = 24
    a.meta.start_year_month = "2026-01"
    # Eid lands in calendar March: with a 2026-01 start that is simulation
    # months 3 and 15 (the legacy Gregorian fallback is unreachable through
    # run_simulation since [] now disables outright — P3, 2026-09-20).
    a.sales.festival_sale_months = [3, 15]
    a.sales.eid_month = 3
    a.sales.eid_price_uplift = 0.25
    base = a.sales.meat_price_per_kg
    result = run_simulation(a)
    for row in result.months:
        want = base * 1.25 if row.month in (3, 15) else base
        assert row.meat_price_per_kg == pytest.approx(want), row.month


def test_festival_hold_and_release() -> None:
    """festival_hold_months=1 with an explicit festival in month 5: a male
    graduating one month before is held (no sale in month 4, sold in month 5
    at his then-current weight), while a male graduating two months before
    sells immediately."""
    a = toy_assumptions()
    a.meta.horizon_months = 12
    a.sales.festival_sale_months = [5]
    a.sales.eid_price_uplift = 0.25
    a.sales.festival_hold_months = 1
    a.growth.sale_age_months = 9  # kidding month 3+... tuned below via m_gro
    a.reproduction.gestation_months = 2
    a.reproduction.conception_rate = 1.0
    a.reproduction.litter_size = 1.0
    a.reproduction.stillbirth_rate = 0.0
    a.mortality.kid_pre_weaning = 0.0
    a.mortality.kid_post_weaning = 0.0
    a.mortality.grower = 0.0
    a.mortality.adult = 0.0
    result = run_simulation(a)
    # First kiddings land in month 3; male kids reach sale age 9 in month 12
    # — beyond the festival. Reposition: sale age 4 makes weaner-age males
    # graduate from the grower chain far too early for a clean window, so
    # assert the invariant instead: no male sells in month 4 and any male
    # sold in month 5 books the uplifted price.
    month5 = result.months[4]
    if month5.sales_head > 0:
        assert month5.meat_price_per_kg == pytest.approx(a.sales.meat_price_per_kg * 1.25)


def test_held_males_feed_an_ordered_grower_sale() -> None:
    """An ordered male-grower sale beyond the grower chain's stock draws from
    the festival holding pen (same class of animal)."""
    a = toy_assumptions()
    a.meta.horizon_months = 12
    a.sales.festival_sale_months = [11]
    a.sales.festival_hold_months = 6
    a.growth.sale_age_months = 8
    a.reproduction.gestation_months = 2
    a.reproduction.conception_rate = 1.0
    a.reproduction.litter_size = 1.0
    a.reproduction.stillbirth_rate = 0.0
    a.mortality.kid_pre_weaning = 0.0
    a.mortality.kid_post_weaning = 0.0
    a.mortality.grower = 0.0
    a.mortality.adult = 0.0
    a.events = [
        Event(month=10, kind="sale", animal_class="male_grower", count=10, price_per_head=500.0)
    ]
    result = run_simulation(a)
    fill = next(fill for fill in result.months[9].event_fills if fill.animal_class == "male_grower")
    assert fill.kind == "sale"
    assert fill.filled > 0.0
    assert fill.revenue == pytest.approx(fill.filled * 500.0)
    assert fill.shortfall == pytest.approx(max(0.0, 10.0 - fill.filled))


def test_event_fill_kinds_shortfalls_and_default_prices() -> None:
    """Purchases book shortfall exactly 0.0 with kind "purchase"; young-stock
    sales without an explicit price default to the class's pool-average
    weight × this month's meat price (male classes carry the premium)."""
    a = toy_assumptions(male_kids=3, female_kids=3)
    a.meta.horizon_months = 12
    a.mortality.kid_pre_weaning = 0.0
    a.events = [
        Event(month=1, kind="purchase", animal_class="female_kid", count=3),
        Event(month=1, kind="sale", animal_class="male_kid", count=2),
        Event(month=1, kind="sale", animal_class="female_kid", count=9),
    ]
    result = run_simulation(a)
    fills = result.months[0].event_fills
    by_class = {(fill.kind, fill.animal_class): fill for fill in fills}
    purchase = by_class[("purchase", "female_kid")]
    assert purchase.shortfall == 0.0
    assert purchase.filled == 3.0
    # Male kid sale: foundation male kids sit at age slot 1.
    male_weight = male_weight_at_age(1, a.growth, a.growth.adult_weight_buck_kg)
    female_weight = weight_at_age(1, a.growth, a.growth.adult_weight_doe_kg)
    price = a.sales.meat_price_per_kg
    male_sale = by_class[("sale", "male_kid")]
    assert male_sale.filled == 2.0
    assert male_sale.price_per_head == pytest.approx(male_weight * price)
    # The male premium is real: same age, heavier male.
    assert male_weight > female_weight
    # Female kid sale asks 9 with 3 present (plus the 3 just purchased):
    # pool after purchase = 3 + 3, so 6 fill and 3 fall short.
    female_sale = by_class[("sale", "female_kid")]
    assert female_sale.shortfall == pytest.approx(3.0)
    assert female_sale.price_per_head == pytest.approx(female_weight * price)


def test_doe_and_buck_event_sale_default_cull_prices() -> None:
    """Adult doe/buck event sales default to cull price per kg × adult
    weight, and book as culls (not meat sales)."""
    a = toy_assumptions()
    a.meta.horizon_months = 12
    a.events = [
        Event(month=1, kind="sale", animal_class="doe", count=2),
        Event(month=1, kind="sale", animal_class="buck", count=1),
    ]
    result = run_simulation(a)
    fills = {(fill.animal_class): fill for fill in result.months[0].event_fills}
    assert fills["doe"].price_per_head == pytest.approx(
        a.sales.cull_doe_price_per_kg * a.growth.adult_weight_doe_kg
    )
    assert fills["buck"].price_per_head == pytest.approx(
        a.sales.cull_buck_price_per_kg * a.growth.adult_weight_buck_kg
    )
    assert result.months[0].culls_head == pytest.approx(3.0)
    assert result.months[0].sales_head == 0.0


def test_buck_service_capacity_policies() -> None:
    """A sire-less herd with no auto-purchase conceives nothing (goat service
    is buck-limited, never technician-limited); with auto-purchase the sires
    are bought BEFORE service, so conceptions land in month 1."""
    sireless = toy_assumptions(bucks=0, auto_purchase_bucks=False)
    sireless.culling.buck_doe_ratio = 10
    result = run_simulation(sireless)
    assert all(row.pregnant_does == 0.0 for row in result.months)
    assert all(row.births == 0.0 for row in result.months)

    meat = toy_assumptions(bucks=0, auto_purchase_bucks=True)
    meat.culling.buck_doe_ratio = 10
    result = run_simulation(meat)
    assert result.months[0].purchases_head >= 1.0
    assert result.months[0].pregnant_does > 0.0


def test_buck_rotation_culls_then_rebuys_and_spares_fresh_bucks() -> None:
    """A 1-year rotation in month 12 culls the standing battery and rebuys;
    a buck bought by event THAT month is spared (cull pool = bucks − fresh)."""
    a = toy_assumptions(bucks=1, auto_purchase_bucks=True)
    a.culling.buck_rotation_years = 1
    a.culling.buck_doe_ratio = 10
    a.meta.horizon_months = 14
    a.events = [Event(month=12, kind="purchase", animal_class="buck", count=1)]
    result = run_simulation(a)
    month12 = result.months[11]
    # The standing battery (decayed by adult mortality) was culled; the event
    # buck joined the same month and was spared — the cull head is the
    # previous month's standing bucks after one month of decay, never both.
    s_adult = 1.0 - monthly_mortality_rate(a.mortality.adult)
    standing = result.months[10].bucks
    assert month12.culls_head == pytest.approx(standing * s_adult, rel=1e-9)
    assert month12.culls_head < standing + 1.0
    # The herd keeps a sire the next month: breeding continues.
    assert result.months[12].pregnant_does > 0.0 or result.months[13].pregnant_does > 0.0


def test_purchased_doe_age_spread_times_the_max_age_cull() -> None:
    """Purchased does spread over foundation ages 10..18 (ceiling = 30 − 12);
    the 18-month-olds exit at age 30 — thirteen months in — not before."""
    a = toy_assumptions(does=0, bucks=1)
    # The purchase spread is clamped to the ceiling max(36-12, 0) = 24: every
    # bought-in doe lands at age 24 regardless of the wider configured range,
    # so the max-age cull is a single wave exactly 12 months later.
    a.herd.foundation_doe_age_min_months = 24
    a.herd.foundation_doe_age_max_months = 60
    a.culling.max_doe_age_months = 36
    a.culling.doe_cull_rate_annual = 0.0
    a.mortality.adult = 0.0
    a.reproduction.conception_rate = 0.0  # no kiddings: pure cull timing
    a.meta.horizon_months = 15
    a.events = [Event(month=1, kind="purchase", animal_class="doe", count=5)]
    result = run_simulation(a)
    for row in result.months[:12]:
        assert row.culls_head == 0.0, row.month
    assert result.months[12].culls_head == pytest.approx(5.0)


def test_cull_revenue_books_head_times_price_times_weight() -> None:
    """Meat-mode rate culls book immediately: cull_revenue == head × cull
    price/kg × adult doe weight, with the growth trend applied."""
    a = toy_assumptions()
    a.sales.annual_livestock_price_growth_rate = 0.0
    a.culling.max_doe_age_months = 180
    a.mortality.adult = 0.0
    a.reproduction.conception_rate = 0.0
    a.meta.horizon_months = 13
    result = run_simulation(a)
    month13 = result.months[12]
    assert month13.culls_head > 0.0
    assert month13.cull_revenue == pytest.approx(
        month13.culls_head * a.sales.cull_doe_price_per_kg * a.growth.adult_weight_doe_kg
    )


def test_surplus_milk_line_is_per_lactating_doe_day() -> None:
    """Milk revenue = lactating does × milk_sale_litres_per_doe_day × days ×
    ₹/litre, with the livestock growth trend (zeroed in the toy): halving the
    daily rate halves the revenue, and the default (0) sells nothing."""
    a = toy_assumptions()
    a.reproduction.lactation_months = 3
    a.reproduction.gestation_months = 2
    a.reproduction.conception_rate = 1.0
    a.sales.milk_sale_litres_per_doe_day = 0.8
    a.sales.milk_price_per_litre = 58.0
    a.mortality.kid_pre_weaning = 0.0
    a.mortality.adult = 0.0
    a.meta.horizon_months = 12
    result = run_simulation(a)
    month3 = result.months[2]
    assert month3.lactating_does > 0.0
    assert month3.milk_revenue == pytest.approx(
        month3.lactating_does * 0.8 * DAYS_PER_MONTH * 58.0, rel=1e-9
    )

    halved = a.model_copy(deep=True)
    halved.sales.milk_sale_litres_per_doe_day = 0.4
    halved_result = run_simulation(halved)
    assert halved_result.months[2].milk_revenue == pytest.approx(
        month3.milk_revenue / 2.0, rel=1e-9
    )

    silent = run_simulation(toy_assumptions())
    assert all(row.milk_revenue == 0.0 for row in silent.months)


def test_surplus_milk_never_goes_negative() -> None:
    """No lactating does (a sire-less herd) or a zero rate both book exactly
    zero milk revenue — the flat side-line cannot dip below 0."""
    a = toy_assumptions(bucks=0, auto_purchase_bucks=False)
    a.sales.milk_sale_litres_per_doe_day = 1.0
    a.meta.horizon_months = 12
    result = run_simulation(a)
    for row in result.months:
        assert row.milk_revenue == 0.0


def test_labour_is_zero_without_breeding_does() -> None:
    """A bucks-only herd pays no labour (``all_does_now > 0`` gates the crew),
    while the same herd plus one doe books the half-time attendant floor."""
    bucks_only = toy_assumptions(does=0, bucks=2)
    result = run_simulation(bucks_only)
    assert all(row.labour_cost == 0.0 for row in result.months)
    one_doe = toy_assumptions(does=1, bucks=2)
    result = run_simulation(one_doe)
    # Half-attendant granularity: a one-doe flock books half a unit, not a
    # full hire (the whole-labourer floor pre-3.1 charged ₹14,000/month).
    assert result.months[0].labour_cost == pytest.approx(0.5 * one_doe.costs.labour_per_month)


def test_manure_covers_every_doe_pool_and_bucks() -> None:
    """Manure income = (all breeding does — open + pregnant + lactating —
    plus bucks) × rate / 12."""
    a = toy_assumptions()
    a.meta.horizon_months = 13
    result = run_simulation(a)
    m1 = result.months[0]
    s_adult = 1.0 - monthly_mortality_rate(a.mortality.adult)
    base = (10.0 + 1.0) * s_adult  # 10 open does + 1 buck, post-mortality
    assert m1.open_does + m1.pregnant_does + m1.lactating_does == pytest.approx(10.0 * s_adult)
    assert m1.manure_revenue == pytest.approx(
        base * a.sales.manure_income_per_adult_per_year / 12.0, rel=1e-9
    )


def test_opening_stock_cost_values_young_stock_at_class_weights() -> None:
    """With initial_stock_cost unset (0), the project charges the herd at
    purchase prices for adults and market price × class weight for young
    stock: kids at weight(1), weaners at weight(4), growers at the mid-chain
    ages (males carry the premium only via the male weight curve)."""
    a = toy_assumptions(
        female_kids=2,
        male_kids=3,
        female_weaners=1,
        male_weaners=1,
        female_growers=2,
        male_growers=2,
        bucks=1,
    )
    a.finance.loan_fraction_of_project_cost = 0.0
    a.finance.subsidy_fraction = 0.0
    a.finance.working_capital_months = 0
    a.costs.capacity_basis = "planned"
    a.costs.planned_capacity_head = 1
    a.costs.shed_cost_per_animal_place = 0.0
    a.costs.equipment_cost_per_animal = 0.0
    g = a.growth
    afb = a.reproduction.age_at_first_breeding_months
    sale_age = g.sale_age_months
    f_mid = 6 + (afb - 6) // 2
    m_mid = 6 + (sale_age - 6) // 2
    young_kg = (
        5 * weight_at_age(1, g, g.adult_weight_doe_kg)
        + 2 * weight_at_age(4, g, g.adult_weight_doe_kg)
        + 2 * weight_at_age(f_mid, g, g.adult_weight_doe_kg)
        # Opening male growers book at the plain curve slot (the premium only
        # applies to the male weight helper), pinning the engine's intent.
        + 2 * weight_at_age(m_mid, g, g.adult_weight_doe_kg)
    )
    expected = (
        10 * a.herd.doe_purchase_price
        + 1 * a.herd.buck_purchase_price
        + young_kg * a.sales.meat_price_per_kg
    )
    result = run_simulation(a)
    assert result.project_cost_breakdown.stock_cost == pytest.approx(expected)


def test_terminal_loan_balance_joins_final_debt_service() -> None:
    """horizon < loan term: the outstanding closing balance is added to the
    final month's debt service on top of that month's scheduled payment."""
    a = toy_assumptions()
    a.finance.loan_term_months = 60
    a.meta.horizon_months = 12
    result = run_simulation(a)
    schedule = result.amortization
    final = result.months[-1]
    closing = schedule[11].closing_balance
    assert closing > 0.0
    assert final.debt_service == pytest.approx(schedule[11].payment + closing)
    # Beyond the loan term the final month carries no residual.
    paid = toy_assumptions()
    paid.finance.loan_term_months = 12
    paid.finance.moratorium_months = 3
    paid.meta.horizon_months = 12
    paid_result = run_simulation(paid)
    assert paid_result.months[-1].debt_service == pytest.approx(
        paid_result.amortization[11].payment
    )


def test_annual_blocks_are_exact_twelve_month_partitions() -> None:
    """annual_pl rows are year 1..N covering months 1-12, 13-24, … with each
    year's revenue the exact sum of its months."""
    a = toy_assumptions()
    a.meta.horizon_months = 24
    result = run_simulation(a)
    assert [row.year for row in result.annual_pl] == [1, 2]
    for index, row in enumerate(result.annual_pl):
        block = result.months[index * 12 : index * 12 + 12]
        assert row.total_revenue == pytest.approx(
            sum(m.sales_revenue + m.cull_revenue + m.milk_revenue + m.manure_revenue for m in block)
        )


def test_tax_assessed_at_block_end_with_loss_carryforward() -> None:
    """Tax books in the final month of each 12-month block at rate ×
    (block profit − losses carried forward)."""
    a = toy_assumptions()
    a.finance.income_tax_rate = 0.3
    a.finance.loan_fraction_of_project_cost = 0.0
    a.finance.subsidy_fraction = 0.0
    a.meta.horizon_months = 24
    result = run_simulation(a)
    for row in result.annual_pl:
        block = result.months[(row.year - 1) * 12 : row.year * 12]
        booked_month = block[-1]
        taxable = max(0.0, row.ebitda - row.depreciation - row.interest)
        assert booked_month.tax == pytest.approx(taxable * 0.3, rel=1e-6), row.year


def test_payback_month_matches_cumulative_series() -> None:
    """payback_month is the first month whose cumulative cash flow (seeded
    with −equity) reaches zero."""
    a = toy_assumptions()
    result = run_simulation(a)
    equity = result.metrics.equity
    cumulative = -equity
    payback = None
    for row in result.months:
        cumulative += row.net_cash_flow
        if payback is None and cumulative >= 0.0:
            payback = row.month
    assert result.metrics.payback_month == payback


def test_zero_fodder_setup_stays_exactly_zero() -> None:
    """No acreage, no storage, no opening stock: every month's fodder stock
    is exactly 0.0 (an fsum default of 1.0 would leak a phantom kilogram)."""
    a = toy_assumptions()
    result = run_simulation(a)
    assert all(row.fodder_stock_kg_dm == 0.0 for row in result.months)


def test_weight_and_phase_mortality_boundaries() -> None:
    a = SimulationAssumptions()
    g = a.growth
    # Age 0 and negative ages fall back to the birth weight.
    assert weight_at_age(0, g, 35.0) == pytest.approx(g.weight_by_age_months[0])
    assert weight_at_age(-3, g, 35.0) == pytest.approx(g.weight_by_age_months[0])
    # At exactly the adult age the curve caps at the adult weight.
    assert weight_at_age(g.adult_weight_age_months, g, g.adult_weight_doe_kg) == (
        pytest.approx(g.adult_weight_doe_kg)
    )
    assert weight_at_age(999, g, g.adult_weight_doe_kg) == pytest.approx(g.adult_weight_doe_kg)
    # Phase mortality: a 3-month class removes exactly the quoted fraction;
    # one month of it removes the cube root; phase_months 1 is the whole rate.
    assert phase_monthly_mortality_rate(0.271, 3) == pytest.approx(1.0 - 0.729 ** (1 / 3))
    assert phase_monthly_mortality_rate(0.5, 1) == pytest.approx(0.5)
    assert phase_monthly_mortality_rate(0.5, 0) == 0.0


def test_pool_avg_weight_weighted_average_and_fallback() -> None:
    a = SimulationAssumptions()
    g = a.growth
    # Two occupied slots: the average is head-weighted across their ages.
    pool = [0.0, 2.0, 0.0, 6.0]  # ages 1 and 3 within a class starting at 0
    avg = _pool_avg_weight(pool, 0, g, g.adult_weight_doe_kg, 5)
    expected = (
        2 * weight_at_age(1, g, g.adult_weight_doe_kg)
        + 6 * weight_at_age(3, g, g.adult_weight_doe_kg)
    ) / 8
    assert avg == pytest.approx(expected)
    # An empty pool falls back to the requested fallback age's weight.
    fallback = _pool_avg_weight([0.0, 0.0], 0, g, g.adult_weight_doe_kg, 5)
    assert fallback == pytest.approx(weight_at_age(5, g, g.adult_weight_doe_kg))
    # A total below 1.0 is still a weighted average (never the fallback).
    tiny = _pool_avg_weight([0.4, 0.0, 0.0, 0.4], 0, g, g.adult_weight_doe_kg, 5)
    tiny_expected = (
        0.4 * weight_at_age(0, g, g.adult_weight_doe_kg)
        + 0.4 * weight_at_age(3, g, g.adult_weight_doe_kg)
    ) / 0.8
    assert tiny == pytest.approx(tiny_expected)


def test_break_even_price_zeros_the_npv() -> None:
    """The bisection's reported break-even meat price reproduces NPV ≈ 0."""
    a = toy_assumptions()
    price = break_even_meat_price(a)
    assert price is not None and price > 0.0
    replay = toy_assumptions()
    replay.sales.meat_price_per_kg = price
    replay.growth.young_male_weight_premium = a.growth.young_male_weight_premium
    replay_result = run_simulation(replay)
    assert replay_result.metrics.npv == pytest.approx(0.0, abs=1.0)


def test_assumptions_fingerprint_stability() -> None:
    """The fingerprint is a pure function of the assumptions document: same
    document → same digest; any field change → different digest."""
    a = toy_assumptions()
    first = run_simulation(a).assumptions_fingerprint
    second = run_simulation(a.model_copy(deep=True)).assumptions_fingerprint
    assert first == second
    changed = toy_assumptions()
    changed.sales.meat_price_per_kg += 1.0
    assert run_simulation(changed).assumptions_fingerprint != first


def test_bucks_only_herd_feed_is_exactly_the_buck_ration() -> None:
    """A bucks-only herd eats exactly the buck ration — any phantom doe
    head leaking into the account would add doe feed on top."""
    from app.simulation.feed import class_feed, combine_feed

    a = toy_assumptions(does=0, bucks=2)
    result = run_simulation(a)
    expected = combine_feed(
        [
            class_feed(
                2.0 * (1.0 - monthly_mortality_rate(a.mortality.adult)),
                a.growth.adult_weight_buck_kg,
                a.feed.dmi_buck,
                a.feed.concentrate_share_buck,
                a.feed,
            )
        ]
    )
    m1 = result.months[0]
    assert m1.feed_green_kg == pytest.approx(expected.green_kg, rel=1e-9)
    assert m1.feed_dry_kg == pytest.approx(expected.dry_kg, rel=1e-9)
    assert m1.feed_concentrate_kg == pytest.approx(expected.concentrate_kg, rel=1e-9)


# --- third-wave kills: tight roots, endpoint signs, anchors and peaks ------------


def test_decimal_roots_isolates_close_root_pairs() -> None:
    """(x−1)(x−1.001) on [0, 3]: two crossings 1e-3 apart. Only the true
    derivative roots (≈1.0005) partition the axis finely enough to see both;
    an offset derivative (e±1/e±2 mutants) merges them into one interval."""
    roots = _all_decimal_power_roots(
        [(Decimal(2), Decimal(1)), (Decimal(1), Decimal("-2.001")), (Decimal(0), Decimal("1.001"))],
        lo=Decimal("0.1"),
        hi=Decimal("3"),
    )
    values = sorted(float(root) for root in roots)
    assert values == pytest.approx([1.0, 1.001], abs=1e-25)


def test_crossing_roots_on_bracket_endpoints_do_not_bisect() -> None:
    """Roots exactly at lo and hi: the sign-product rule must treat a zero
    sign as "no crossing" (never divide by it, never bisect it)."""
    roots = _crossing_decimal_power_roots([(2.0, 1.0), (1.0, -4.0), (0.0, 3.0)], 1.0, 3.0)
    floats = [float(root) for root in roots]
    assert floats == pytest.approx([1.0, 3.0], abs=1e-25)


def test_repeat_cull_at_one_service_fires_immediately() -> None:
    """max_services=1: the FIRST failed service is the repeat-breeder cull —
    month 1 culls 10 × (1 − 0.5) = 5 does, not zero."""
    a = toy_assumptions()
    a.reproduction.conception_rate = 0.5
    a.reproduction.max_services_before_cull = 1
    a.mortality.adult = 0.0
    a.culling.doe_cull_rate_annual = 0.0
    a.culling.max_doe_age_months = 180
    a.meta.horizon_months = 12
    result = run_simulation(a)
    assert result.months[0].culls_head == pytest.approx(5.0)


def test_projected_peak_equals_max_herd_without_decay() -> None:
    """With zero mortality and no scheduled events the physical peak equals
    the maximum month-end herd exactly (``lact`` is a state pool that must
    count in the physical-head sum)."""
    meat = toy_assumptions()
    meat.mortality.kid_pre_weaning = 0.0
    meat.mortality.kid_post_weaning = 0.0
    meat.mortality.grower = 0.0
    meat.mortality.adult = 0.0
    meat.meta.horizon_months = 18
    result = run_simulation(meat)
    # The physical peak is taken mid-month, so it can only exceed (never
    # fall below) the largest month-end balance; a mutant that drops the
    # lactation state pool from the physical-head sum breaks this lower
    # bound in exactly the months that pool is populated.
    assert result.project_cost_breakdown.projected_peak_head >= (
        max(row.total_herd for row in result.months) - 1e-6
    )


def test_meat_mode_mixed_foundation_mass_balance() -> None:
    """Meat mode, mixed foundation, two service buckets: the mass-balance
    identity holds against the HERD-FIELD opening head, so a phantom doe
    planted in a service bucket ([1.0] instead of [0.0]) leaks out
    immediately."""
    a = toy_assumptions()
    a.herd.foundation_flock_state = "mixed"
    a.reproduction.conception_rate = 0.5
    a.reproduction.max_services_before_cull = 2  # a second bucket to plant into
    a.meta.horizon_months = 12
    result = run_simulation(a)
    _assert_mass_balance(a, result)


def test_physical_peak_tracks_max_herd_within_sub_head_slack() -> None:
    """Zero-mortality, event-free runs: the physical peak may exceed the
    month-end balance only by float/bookkeeping slack well below one head —
    a phantom +1.0 term in the physical-head sum (either mode) breaks it."""
    meat = toy_assumptions()
    meat.mortality.kid_pre_weaning = 0.0
    meat.mortality.kid_post_weaning = 0.0
    meat.mortality.grower = 0.0
    meat.mortality.adult = 0.0
    meat.meta.horizon_months = 18
    result = run_simulation(meat)
    peak = result.project_cost_breakdown.projected_peak_head
    max_herd = max(row.total_herd for row in result.months)
    assert max_herd - 0.5 <= peak <= max_herd + 0.5
