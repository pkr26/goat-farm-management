"""Tests proving each 2026-09 simulation/financial audit remediation.

Covers (model 3.1.0):
- the extended Bakrid calendar and its coverage warning (fix 2),
- Monte Carlo bootstrap confidence intervals (fix 3),
- within-run annual AR(1) price variation (fix 4),
- parity-structured reproduction (fix 6),
- the SPEC-aligned kidding cycle vs GOAT_PROFILE (fix 7),
- family labour and half-attendant granularity (fix 8),
- deseasonalized insurance valuation (fix 9),
- Telangana selling-cost defaults (fix 10),
- the optimizer's festival-hold and service-cull axes (fix 11).

Fix 1 (repeat-breeder cull default parity) and fix 5 (breeding-stock
capitalization identities) live in test_simulation_engine.py and
test_simulation_financials.py respectively, beside the mechanics they pin.
"""

import math
import random

import pytest
from pydantic import ValidationError

from app.simulation import (
    HerdAssumptions,
    MetaAssumptions,
    OptimizationAssumptions,
    SimulationAssumptions,
    run_monte_carlo,
    run_simulation,
    weight_at_age,
)
from app.simulation.assumptions import ParityMultipliers
from app.simulation.market import (
    annual_growth_multiplier,
    bakrid_festival_months,
    festival_coverage_last_year,
)
from app.simulation.montecarlo import (
    _annual_ar1_factors,
    _apply_annual_price_variation,
    _triangular_log_sd,
)
from app.simulation.planner import _kidding_interval_months
from app.simulation.shocks import MonthlyShockPath

DAYS_PER_MONTH = 30.44  # the SPEC-parity conversion the profile test uses


# ---------------------------------------------------------------------------
# Fix 2 — Bakrid calendar through 2050, coverage warning, graceful degradation
# ---------------------------------------------------------------------------
def test_festival_calendar_extends_through_2050() -> None:
    assert festival_coverage_last_year() == 2050
    # A 2045 start with a horizon past the table still resolves every KNOWN
    # festival: 2045 Oct .. 2050 Aug -> simulation months 10, 22, 33, 45, 57,
    # 68 for a January 2045 start.
    months = bakrid_festival_months("2045-01", 120)
    assert months == [10, 22, 33, 45, 57, 68]
    # Nothing beyond the last covered festival is invented.
    assert max(months) < 120
    # A start AFTER the table degrades to no festivals at all (no guesses).
    assert bakrid_festival_months("2051-01", 60) == []


def test_horizon_past_festival_coverage_surfaces_a_warning() -> None:
    a = SimulationAssumptions(
        meta=MetaAssumptions(start_year_month="2045-01", horizon_months=120),
    )
    res = run_simulation(a, with_break_even=False)
    assert res.warnings == [
        "Festival calendar covers through 2050; months beyond that carry no Bakrid uplift."
    ]
    # The caveat also reaches the human narrative, next to the risk section.
    risks = next(section for section in res.narrative_report if section.key == "risks")
    assert any("Festival calendar covers through 2050" in p for p in risks.paragraphs)


def test_horizon_inside_festival_coverage_warns_nothing() -> None:
    a = SimulationAssumptions(
        meta=MetaAssumptions(start_year_month="2040-01", horizon_months=120),
    )
    res = run_simulation(a, with_break_even=False)
    # The run ends in 2049, inside the 2050 table: no caveat.
    assert res.warnings == []
    # A run with the uplift explicitly disabled has nothing to warn about
    # either.
    no_festival = a.model_copy(deep=True)
    no_festival.sales.festival_sale_months = []
    no_festival_res = run_simulation(no_festival, with_break_even=False)
    assert no_festival_res.warnings == []


def test_festival_hold_degrades_gracefully_past_coverage() -> None:
    """Males finishing after the last known festival are sold immediately:
    the hold rule must never hold stock for a festival it cannot see."""
    a = SimulationAssumptions(
        meta=MetaAssumptions(start_year_month="2045-01", horizon_months=120),
    )
    a.sales.festival_hold_months = 2
    res = run_simulation(a, with_break_even=False)
    last_festival_month = max(a.sales.festival_sale_months or ())
    assert last_festival_month == 68
    # Meat sales continue at a normal cadence in the uncovered years: held
    # males would instead bunch into festival months, and there are none
    # after month 67.
    tail_sales = [row.sales_head for row in res.months[72:96]]
    assert any(head > 0.0 for head in tail_sales)
    # Every month the engine marks as festival-priced carries the uplift, and
    # no uncovered month does.
    for row in res.months:
        base = (
            row.meat_price_per_kg
            / annual_growth_multiplier(a.sales.annual_livestock_price_growth_rate, row.month)
            / a.sales.monthly_meat_price_multipliers[row.calendar_month - 1]
        )
        if row.month in (a.sales.festival_sale_months or ()):
            assert base == pytest.approx(a.sales.meat_price_per_kg * 1.35, rel=1e-9)
        elif row.month > last_festival_month:
            assert base == pytest.approx(a.sales.meat_price_per_kg, rel=1e-9)


# ---------------------------------------------------------------------------
# Fix 3 — Monte Carlo bootstrap confidence intervals
# ---------------------------------------------------------------------------
def _mc_assumptions(runs: int, seed: int = 7) -> SimulationAssumptions:
    a = SimulationAssumptions(meta=MetaAssumptions(horizon_months=24))
    a.risk.monte_carlo_runs = runs
    a.risk.seed = seed
    return a


def test_monte_carlo_confidence_intervals_are_reproducible() -> None:
    first = run_monte_carlo(_mc_assumptions(30))
    second = run_monte_carlo(_mc_assumptions(30))
    assert first.npv_p5_ci is not None and second.npv_p5_ci is not None
    assert first.npv_p5_ci == second.npv_p5_ci
    assert first.npv_p50_ci == second.npv_p50_ci
    assert first.npv_p95_ci == second.npv_p95_ci
    assert first.minimum_cash_p5_ci == second.minimum_cash_p5_ci
    assert first.prob_npv_negative_se == second.prob_npv_negative_se
    # A different seed shifts the bootstrap stream (the salt mixes the seed).
    other = run_monte_carlo(_mc_assumptions(30, seed=8))
    assert other.npv_p5_ci is not None
    assert other.npv_p5_ci != first.npv_p5_ci


def test_monte_carlo_ci_brackets_the_point_estimates() -> None:
    mc = run_monte_carlo(_mc_assumptions(40))
    assert mc.npv_p5_ci is not None
    assert mc.npv_p50_ci is not None
    assert mc.npv_p95_ci is not None
    assert mc.minimum_cash_p5_ci is not None
    assert mc.npv_p5_ci[0] <= mc.npv_p5 <= mc.npv_p5_ci[1]
    assert mc.npv_p50_ci[0] <= mc.npv_p50 <= mc.npv_p50_ci[1]
    assert mc.npv_p95_ci[0] <= mc.npv_p95 <= mc.npv_p95_ci[1]
    assert mc.minimum_cash_p5_ci[0] <= mc.minimum_cash_p5 <= mc.minimum_cash_p5_ci[1]
    # Analytic binomial SE of the loss probability is non-negative and
    # consistent with the observed proportion.
    assert mc.prob_npv_negative_se is not None
    assert mc.prob_npv_negative_se >= 0.0
    expected_se = math.sqrt(mc.prob_npv_negative * (1.0 - mc.prob_npv_negative) / mc.runs)
    assert mc.prob_npv_negative_se == pytest.approx(expected_se)


def test_monte_carlo_ci_width_shrinks_with_more_runs() -> None:
    thin = run_monte_carlo(_mc_assumptions(15))
    thick = run_monte_carlo(_mc_assumptions(160))
    assert thin.npv_p5_ci is not None and thick.npv_p5_ci is not None
    thin_width = thin.npv_p5_ci[1] - thin.npv_p5_ci[0]
    thick_width = thick.npv_p5_ci[1] - thick.npv_p5_ci[0]
    assert thick_width < thin_width


def test_monte_carlo_single_run_has_no_percentile_ci() -> None:
    mc = run_monte_carlo(_mc_assumptions(1))
    assert mc.npv_p5_ci is None
    assert mc.npv_p50_ci is None
    assert mc.npv_p95_ci is None
    assert mc.minimum_cash_p5_ci is None


# ---------------------------------------------------------------------------
# Fix 4 — within-run annual AR(1) price variation
# ---------------------------------------------------------------------------
def test_annual_ar1_factors_are_deterministic_with_unit_marginal_variance() -> None:
    rng = random.Random(1234)
    factors = _annual_ar1_factors(1200, rng, rho=0.3)
    assert len(factors) == 1200
    # One factor per projection year, shared by its twelve months.
    assert factors[0] == factors[11]
    assert factors[11] != factors[12]
    # Stationary variance is 1 by construction (x = rho*x + sqrt(1-rho^2)*e).
    years = factors[::12]
    mean = sum(years) / len(years)
    variance = sum((x - mean) ** 2 for x in years) / len(years)
    assert variance == pytest.approx(1.0, rel=0.25)
    # Same seed -> same path.
    assert _annual_ar1_factors(24, random.Random(5), 0.3) == _annual_ar1_factors(
        24, random.Random(5), 0.3
    )


def test_apply_annual_price_variation_shares_the_configured_variance() -> None:
    a = SimulationAssumptions(meta=MetaAssumptions(horizon_months=24))
    path = MonthlyShockPath.neutral(24)
    draws = dict.fromkeys(
        (
            "meat_price",
            "feed_price",
            "adult_mortality",
            "kid_mortality",
            "litter_size",
            "conception_rate",
            "fodder_yield",
            "operating_cost",
            "milk_price",
        ),
        1.0,
    )
    draws["meat_price"] = 1.2
    effective = _apply_annual_price_variation(path, draws, a, random.Random(99))
    # The persistent component carries 1 - share of the run draw's log.
    from app.simulation.montecarlo import _WITHIN_RUN_VARIANCE_SHARE as share

    assert effective["meat_price"] == pytest.approx(math.exp(math.log(1.2) * math.sqrt(1 - share)))
    # Total marginal variance is preserved: the between-run component carries
    # (1 - share) x the configured triangular log-spread and the within-run
    # annual channel share x of it. Sample the standardized AR(1) process to
    # verify the within-run conditional variance (stationary years only — the
    # chain starts at 0 and needs a few years to reach its unit variance).
    sigma = _triangular_log_sd(a.risk.meat_price.low, a.risk.meat_price.high)
    yearly_samples: list[float] = []
    for seed in range(120):
        factors = _annual_ar1_factors(120, random.Random(1000 + seed), 0.3)
        yearly_samples.extend(factors[60:120])
    import statistics

    within_variance = statistics.variance(yearly_samples)
    assert within_variance == pytest.approx(1.0, rel=0.15)
    assert within_variance * share * sigma**2 == pytest.approx(share * sigma**2, rel=0.15)
    # The annual layer multiplies the shock path (no longer neutral).
    assert any(factor != 1.0 for factor in path.meat_price)
    # Disabled variables are left completely alone.
    disabled = a.model_copy(deep=True)
    disabled.risk.meat_price.enabled = False
    disabled_path = MonthlyShockPath.neutral(24)
    disabled_draws = dict(draws)
    disabled_draws["meat_price"] = 1.0
    effective_disabled = _apply_annual_price_variation(
        disabled_path, disabled_draws, disabled, random.Random(99)
    )
    assert effective_disabled["meat_price"] == 1.0
    assert all(factor == 1.0 for factor in disabled_path.meat_price)


def test_within_run_variation_flag_controls_the_process() -> None:
    legacy = _mc_assumptions(12)
    legacy.risk.within_run_price_variation = False
    # When off, rho is irrelevant: the legacy single-multiplier behaviour is
    # recovered exactly (the flag fully disables the annual layer).
    other_rho = legacy.model_copy(deep=True)
    other_rho.risk.price_process_rho = 0.9
    assert run_monte_carlo(legacy).npv_mean == run_monte_carlo(other_rho).npv_mean
    # When on, runs live through price years: the NPV distribution moves.
    enabled = _mc_assumptions(12)
    enabled.risk.within_run_price_variation = True
    assert run_monte_carlo(enabled).npv_mean != run_monte_carlo(legacy).npv_mean


# ---------------------------------------------------------------------------
# Fix 6 — parity-structured reproduction
# ---------------------------------------------------------------------------
def test_parity_structure_reduces_kid_supply_below_flat() -> None:
    a = SimulationAssumptions(meta=MetaAssumptions(horizon_months=60))
    flat = a.model_copy(deep=True)
    flat.reproduction.parity_multipliers = ParityMultipliers(
        litter_size=[1.0], conception_rate=[1.0]
    )
    parity = run_simulation(a, with_break_even=False)
    flat_res = run_simulation(flat, with_break_even=False)
    parity_births = sum(row.births for row in parity.months)
    flat_births = sum(row.births for row in flat_res.months)
    assert parity_births < flat_births
    # Same-average check: the default table's litter entries average below the
    # mature 1.0, so the herd-wide expected litter is below the flat model's.
    assert sum(ParityMultipliers().litter_size) / len(ParityMultipliers().litter_size) < 1.0


def test_flat_parity_tables_of_any_length_reproduce_the_flat_model() -> None:
    a = SimulationAssumptions(meta=MetaAssumptions(horizon_months=36))
    single = a.model_copy(deep=True)
    single.reproduction.parity_multipliers = ParityMultipliers(
        litter_size=[1.0], conception_rate=[1.0]
    )
    multi = a.model_copy(deep=True)
    multi.reproduction.parity_multipliers = ParityMultipliers(
        litter_size=[1.0] * 7, conception_rate=[1.0] * 7
    )
    single_res = run_simulation(single, with_break_even=False)
    multi_res = run_simulation(multi, with_break_even=False)
    assert [row.births for row in single_res.months] == pytest.approx(
        [row.births for row in multi_res.months]
    )


def test_parity_multiplier_tables_are_validated() -> None:
    with pytest.raises(ValidationError):
        ParityMultipliers(litter_size=[0.0], conception_rate=[1.0])
    with pytest.raises(ValidationError):
        ParityMultipliers(litter_size=[1.0], conception_rate=[2.5])
    with pytest.raises(ValidationError):
        ParityMultipliers(litter_size=[], conception_rate=[1.0])


# ---------------------------------------------------------------------------
# Fix 7 — kidding cycle aligned with the operational SPEC
# ---------------------------------------------------------------------------
def test_kidding_cycle_matches_goat_profile_within_monthly_rounding() -> None:
    """The engine's meat-mode cycle is single-sourced with the operational
    biology: gestation 150 d -> 5 months, the weaning+rebreed pool
    (GOAT_PROFILE.weaning_days = 60) -> 2 months, and the open period covers
    the 14-day voluntary waiting window. Month resolution rounds each phase,
    so the composed interval must sit within one month of the profile's
    day-resolution interval."""
    from app.models.species import GOAT_PROFILE

    a = SimulationAssumptions()
    r = a.reproduction
    assert r.gestation_months == round(GOAT_PROFILE.gestation_days / DAYS_PER_MONTH)
    assert r.lactation_months == round(GOAT_PROFILE.weaning_days / DAYS_PER_MONTH)
    profile_interval_months = (
        GOAT_PROFILE.gestation_days
        + GOAT_PROFILE.weaning_days
        + GOAT_PROFILE.voluntary_waiting_days
    ) / DAYS_PER_MONTH
    engine_interval = _kidding_interval_months(a)
    assert abs(engine_interval - profile_interval_months) <= 1.0
    # And inside the published Osmanabadi kidding interval (232-297 days).
    assert (
        232 / DAYS_PER_MONTH <= engine_interval + 1 / r.conception_rate - 1 <= 297 / DAYS_PER_MONTH
    )


# ---------------------------------------------------------------------------
# Fix 8 — family labour and half-attendant granularity
# ---------------------------------------------------------------------------
def test_labour_charges_half_attendant_units() -> None:
    for does, expected_units in ((3, 0.5), (50, 1.0), (130, 2.5)):
        a = SimulationAssumptions(meta=MetaAssumptions(horizon_months=12))
        a.herd = HerdAssumptions(
            does=does,
            bucks=1,
            auto_purchase_bucks=False,
            foundation_flock_state="open",
        )
        a.mortality.adult = 0.0
        a.reproduction.conception_rate = 0.0
        a.reproduction.max_services_before_cull = 0
        first = run_simulation(a, with_break_even=False).months[0]
        assert first.labour_cost == pytest.approx(
            expected_units * a.costs.labour_per_month, rel=1e-9
        ), does


def test_family_labour_is_zero_cash_with_imputed_note() -> None:
    a = SimulationAssumptions(meta=MetaAssumptions(horizon_months=24))
    family = a.model_copy(deep=True)
    family.costs.family_labour = True
    res = run_simulation(family, with_break_even=False)
    assert all(row.labour_cost == 0.0 for row in res.months)
    # IRR-style metrics still work off the actual (now higher) cash flows.
    assert res.metrics.npv is not None
    hired = run_simulation(a, with_break_even=False)
    assert res.metrics.npv > hired.metrics.npv
    # The narrative discloses the imputed market wage the family forgoes.
    cost_mix = next(section for section in res.narrative_report if section.key == "cost_mix")
    assert any("family labour" in paragraph for paragraph in cost_mix.paragraphs)
    assert any("unpaid family work" in paragraph for paragraph in cost_mix.paragraphs)
    # ...while the hired run says nothing about family labour.
    hired_mix = next(section for section in hired.narrative_report if section.key == "cost_mix")
    assert not any("family labour" in paragraph for paragraph in hired_mix.paragraphs)


# ---------------------------------------------------------------------------
# Fix 9 — insurance on the deseasonalized base price
# ---------------------------------------------------------------------------
def test_insurance_premium_ignores_the_festival_uplift() -> None:
    """Identical herds in festival and non-festival months carry the same
    premium: two runs with the same biology, one with the Bakrid uplift
    active and one without, must agree on every month's insurance cost while
    their realized meat prices differ."""

    def build(festivals: bool) -> SimulationAssumptions:
        a = SimulationAssumptions(meta=MetaAssumptions(horizon_months=24))
        a.herd = HerdAssumptions(
            does=0,
            bucks=0,
            female_weaners=30,
            auto_purchase_bucks=False,
        )
        a.mortality.kid_post_weaning = 0.0
        a.mortality.grower = 0.0
        a.reproduction.age_at_first_breeding_months = 24  # no graduation sales
        a.herd.female_retention_fraction = 0.0
        a.sales.annual_livestock_price_growth_rate = 0.0
        a.sales.monthly_meat_price_multipliers = [1.0] * 12
        if not festivals:
            a.sales.festival_sale_months = []
        return a

    festival_run = run_simulation(build(True), with_break_even=False)
    plain_run = run_simulation(build(False), with_break_even=False)
    festival_months = set(build(True).sales.festival_sale_months or ())
    assert festival_months  # the uplift is genuinely active in this window
    for festival_row, plain_row in zip(festival_run.months, plain_run.months, strict=True):
        # Same herd, same biology.
        assert festival_row.total_herd == pytest.approx(plain_row.total_herd)
        assert festival_row.insurance_cost == pytest.approx(plain_row.insurance_cost)
        if festival_row.month in festival_months:
            assert festival_row.meat_price_per_kg > plain_row.meat_price_per_kg


def test_insurance_values_young_stock_at_the_deseasonalized_base() -> None:
    a = SimulationAssumptions(meta=MetaAssumptions(horizon_months=12))
    a.herd = HerdAssumptions(does=0, bucks=0, female_weaners=10, auto_purchase_bucks=False)
    a.mortality.kid_post_weaning = 0.0  # keep the closing weight exact
    a.sales.annual_livestock_price_growth_rate = 0.0
    first = run_simulation(a, with_break_even=False).months[0]
    # stock_value = young kg x base price (no seasonal multiplier, no uplift):
    # recoverable exactly from the premium line.
    rate = a.costs.insurance_pct_stock_value_annual
    young_value = first.insurance_cost * 12.0 / rate
    expected = (
        10.0 * weight_at_age(5, a.growth, a.growth.adult_weight_doe_kg) * a.sales.meat_price_per_kg
    )
    assert young_value == pytest.approx(expected, rel=1e-9)


# ---------------------------------------------------------------------------
# Fix 10 — Telangana selling-cost defaults
# ---------------------------------------------------------------------------
def test_selling_cost_defaults_are_the_telangana_market_rates() -> None:
    sales = SimulationAssumptions().sales
    assert sales.selling_cost_fraction == 0.03  # mandi commission 2-4%, mid
    assert sales.transport_cost_per_head == 100.0  # shared truck to the shandy


def test_default_run_selling_cost_brackets_commission_and_transport() -> None:
    a = SimulationAssumptions(meta=MetaAssumptions(horizon_months=24))
    res = run_simulation(a, with_break_even=False)
    for row in res.months:
        revenue = row.sales_revenue + row.cull_revenue
        growth = annual_growth_multiplier(a.costs.operating_cost_growth_rate_annual, row.month)
        assert row.selling_cost == pytest.approx(
            revenue * 0.03 + (row.sales_head + row.culls_head) * 100.0 * growth
        )
    # Explain.py itemizes selling as its own cost-mix line.
    cost_mix = next(section for section in res.narrative_report if section.key == "cost_mix")
    assert "selling_cost" in cost_mix.figures
    assert cost_mix.figures["selling_cost"] == pytest.approx(
        sum(row.selling_cost for row in res.months)
    )


# ---------------------------------------------------------------------------
# Fix 11 — optimizer explores the festival-hold and service-cull axes
# ---------------------------------------------------------------------------
def test_optimizer_explores_festival_hold_and_service_cull_axes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.simulation import optimization as optimization_module

    policy = OptimizationAssumptions(
        max_candidates=12,
        minimum_dscr=0.0,
        doe_scale_low=1.0,
        doe_scale_high=1.0,
        doe_scale_steps=1,
        sale_age_radius_months=0,
        retention_step=0.0,
        loan_fraction_step=0.0,
        festival_hold_radius_months=1,
        service_cull_radius_months=1,
    )
    a = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        optimization=policy,
    )
    observed: list[SimulationAssumptions] = []
    original_run_core = optimization_module._run_core

    def record(assumptions: SimulationAssumptions, *_args: object) -> object:
        observed.append(assumptions)
        return original_run_core(assumptions)

    monkeypatch.setattr(optimization_module, "_run_core", record)
    result = optimization_module.run_optimization(a)

    assert {item.sales.festival_hold_months for item in observed} == {1, 2, 3}
    assert {item.reproduction.max_services_before_cull for item in observed} == {1, 2, 3}
    # The candidate rows carry the decisions for auditability.
    assert {c.festival_hold_months for c in [result.baseline, *result.alternatives]} <= {1, 2, 3}
    assert {c.max_services_before_cull for c in [result.baseline, *result.alternatives]} <= {
        1,
        2,
        3,
    }
    # ...and the budget holds.
    assert result.evaluated_candidates <= policy.max_candidates
