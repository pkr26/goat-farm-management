"""Independent checks for the calibrated, market-aware financial model."""

import math
import random
import statistics

import pytest
from pydantic import ValidationError

from app.simulation import MetaAssumptions, SimulationAssumptions, mirr, run_simulation
from app.simulation.assumptions import (
    CostsAssumptions,
    FeedAssumptions,
    HerdAssumptions,
    HerdEventAssumptions,
    OptimizationAssumptions,
)
from app.simulation.montecarlo import (
    _DRAW_ORDER,
    _apply_draws,
    _correlated_draws,
    _event_shock_path,
)
from app.simulation.optimization import run_optimization


def test_calendar_price_growth_and_festival_uplift_are_composed_once() -> None:
    a = SimulationAssumptions(meta=MetaAssumptions(horizon_months=24, start_year_month="2026-01"))
    a.sales.meat_price_per_kg = 100.0
    a.sales.monthly_meat_price_multipliers[1] = 1.2
    a.sales.annual_livestock_price_growth_rate = 0.12
    a.sales.eid_month = 2
    a.sales.festival_sale_months = [2]
    result = run_simulation(a, with_break_even=False)

    assert result.months[1].meat_price_per_kg == pytest.approx(
        100.0 * 1.2 * math.pow(1.12, 1.0 / 12.0) * 1.3
    )
    assert result.months[12].meat_price_per_kg == pytest.approx(112.0)
    # Once exact lunar-calendar simulation months are present, the legacy
    # recurring February setting must not invent another festival in year 2.
    assert result.months[13].meat_price_per_kg == pytest.approx(
        100.0 * 1.2 * math.pow(1.12, 13.0 / 12.0)
    )


def test_direct_selling_costs_are_not_hidden_in_net_revenue() -> None:
    a = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        events=[
            HerdEventAssumptions(
                month=1,
                kind="sale",
                animal_class="doe",
                count=1.0,
                price_per_head=10_000.0,
            )
        ],
    )
    a.sales.selling_cost_fraction = 0.10
    a.sales.transport_cost_per_head = 100.0
    month = run_simulation(a, with_break_even=False).months[0]
    assert month.cull_revenue == pytest.approx(10_000.0)
    assert month.selling_cost == pytest.approx(1_100.0)


def test_per_head_transport_cost_follows_operating_cost_growth() -> None:
    a = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=24),
        events=[
            HerdEventAssumptions(
                month=month,
                kind="sale",
                animal_class="doe",
                count=1.0,
                price_per_head=10_000.0,
            )
            for month in (1, 13)
        ],
        costs=CostsAssumptions(operating_cost_growth_rate_annual=0.12),
    )
    a.sales.transport_cost_per_head = 100.0
    result = run_simulation(a, with_break_even=False)

    month_1 = result.months[0]
    month_13 = result.months[12]
    assert month_1.selling_cost == pytest.approx((month_1.sales_head + month_1.culls_head) * 100.0)
    assert month_13.selling_cost == pytest.approx(
        (month_13.sales_head + month_13.culls_head) * 112.0
    )


def test_green_fodder_is_physically_sourced_and_costed_by_source() -> None:
    no_land = SimulationAssumptions(meta=MetaAssumptions(horizon_months=12))
    no_land_result = run_simulation(no_land, with_break_even=False)
    assert all(month.feed_homegrown_green_kg == 0.0 for month in no_land_result.months)
    assert all(
        month.feed_purchased_green_kg == pytest.approx(month.feed_green_kg)
        for month in no_land_result.months
    )

    with_land = no_land.model_copy(deep=True)
    with_land.feed.cultivated_fodder_acres = 100.0
    with_land_result = run_simulation(with_land, with_break_even=False)
    assert all(month.feed_purchased_green_kg == 0.0 for month in with_land_result.months)
    assert sum(month.feed_cost for month in with_land_result.months) < sum(
        month.feed_cost for month in no_land_result.months
    )


def test_feed_and_insurance_use_the_tracked_age_cohort_weight() -> None:
    a = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(
            does=0,
            bucks=0,
            female_kids=1,
            max_breeding_does=0,
            auto_purchase_bucks=False,
        ),
    )
    a.mortality.kid_pre_weaning = 0.0
    first = run_simulation(a, with_break_even=False).months[0]
    purchased_dm = (
        first.feed_green_kg * a.feed.green_dm_pct
        + first.feed_dry_kg * a.feed.dry_dm_pct
        + first.feed_concentrate_kg * a.feed.concentrate_dm_pct
    )

    # The foundation kid starts at age 1 and closes month 1 at age 2. The old
    # class-midpoint shortcut fed it forever at age-1 weight (4.5 kg).
    assert first.f_kids == pytest.approx(1.0)
    assert purchased_dm == pytest.approx(
        a.growth.weight_by_age_months[2] * a.feed.dmi_kid_creep * 30.44
    )
    assert first.insurance_cost == pytest.approx(
        a.growth.weight_by_age_months[2]
        * a.sales.meat_price_per_kg
        * a.costs.insurance_pct_stock_value_annual
        / 12.0
    )


def test_fodder_storage_balance_includes_loss_and_capacity_waste() -> None:
    a = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        feed=FeedAssumptions(
            initial_fodder_stock_kg_dm=10_000.0,
            fodder_storage_capacity_kg_dm=10_000.0,
            fodder_storage_loss_fraction_monthly=0.10,
        ),
    )
    first = run_simulation(a, with_break_even=False).months[0]
    green_demand_dm = first.feed_green_kg * a.feed.green_dm_pct
    assert first.feed_purchased_green_kg == 0.0
    assert first.fodder_stock_kg_dm == pytest.approx(max(0.0, 9_000.0 - green_demand_dm))
    assert first.fodder_waste_kg_dm == pytest.approx(1_000.0)


def test_capacity_basis_projected_planned_and_opening() -> None:
    base = SimulationAssumptions(meta=MetaAssumptions(horizon_months=24))
    projected = run_simulation(base, with_break_even=False).project_cost_breakdown
    assert projected.capacity_places == pytest.approx(projected.projected_peak_head * 1.10)

    opening = base.model_copy(deep=True)
    opening.costs.capacity_basis = "opening_herd"
    opening_result = run_simulation(opening, with_break_even=False).project_cost_breakdown
    assert opening_result.capacity_places == pytest.approx(52.0 * 1.10)

    planned = base.model_copy(deep=True)
    planned.costs.capacity_basis = "planned"
    planned.costs.planned_capacity_head = 80
    planned_result = run_simulation(planned, with_break_even=False).project_cost_breakdown
    assert planned_result.capacity_places == 80.0


def test_depreciation_terminal_value_and_cash_tax_reconcile() -> None:
    a = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        costs=CostsAssumptions(
            shed_useful_life_years=1,
            equipment_useful_life_years=1,
            shed_residual_fraction=0.10,
            equipment_residual_fraction=0.20,
        ),
    )
    a.herd.male_growers = 250
    a.sales.meat_price_per_kg = 1_000.0
    a.finance.loan_fraction_of_project_cost = 0.0
    a.finance.income_tax_rate = 0.25
    result = run_simulation(a, with_break_even=False)
    breakdown = result.project_cost_breakdown
    annual = result.annual_pl[0]

    expected_depreciation = breakdown.shed_cost * 0.90 + breakdown.equipment_cost * 0.80
    assert annual.depreciation == pytest.approx(expected_depreciation)
    assert result.terminal_value_breakdown.shed == pytest.approx(breakdown.shed_cost * 0.10)
    assert result.terminal_value_breakdown.equipment == pytest.approx(
        breakdown.equipment_cost * 0.20
    )
    assert annual.tax == pytest.approx(max(0.0, annual.profit_before_tax) * 0.25)
    assert annual.profit_after_tax == pytest.approx(annual.profit_before_tax - annual.tax)
    assert result.metrics.tax_total == pytest.approx(annual.tax)


def test_disabling_terminal_value_zeros_every_terminal_component() -> None:
    a = SimulationAssumptions(meta=MetaAssumptions(horizon_months=12))
    a.finance.include_terminal_value = False
    result = run_simulation(a, with_break_even=False)
    assert result.terminal_value_breakdown.total == 0.0
    assert result.months[-1].terminal_value == 0.0
    assert result.metrics.terminal_value == 0.0


def test_operating_cash_balance_reconciles_from_funded_working_capital() -> None:
    result = run_simulation(SimulationAssumptions(), with_break_even=False)
    balance = result.project_cost_breakdown.working_capital
    observed_min = (0, balance)
    for month in result.months:
        working_capital_recovery = (
            result.terminal_value_breakdown.working_capital
            if month.month == len(result.months)
            else 0.0
        )
        balance += month.net_cash_flow - working_capital_recovery
        assert month.cash_balance == pytest.approx(balance)
        if balance < observed_min[1]:
            observed_min = (month.month, balance)
    assert result.metrics.minimum_cash_month == observed_min[0]
    assert result.metrics.minimum_cash_balance == pytest.approx(observed_min[1])
    assert result.metrics.additional_working_capital_required == pytest.approx(
        max(0.0, -observed_min[1])
    )


def test_terminal_working_capital_is_not_counted_twice_in_liquidity() -> None:
    result = run_simulation(SimulationAssumptions(), with_break_even=False)
    final = result.months[-1]
    investor_cash = -result.metrics.equity + sum(month.net_cash_flow for month in result.months)
    assert final.cumulative_cash_flow == pytest.approx(investor_cash)
    assert final.cash_balance == pytest.approx(
        result.project_cost_breakdown.working_capital
        + sum(month.net_cash_flow for month in result.months)
        - result.terminal_value_breakdown.working_capital
    )


def test_buck_service_capacity_limits_conception() -> None:
    a = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(
            does=100,
            bucks=1,
            max_breeding_does=100,
            auto_purchase_bucks=False,
            foundation_flock_state="open",
        ),
    )
    a.reproduction.conception_rate = 1.0
    a.mortality.adult = 0.0
    first = run_simulation(a, with_break_even=False).months[0]
    assert first.pregnant_does == pytest.approx(25.0)
    assert first.open_does == pytest.approx(75.0)


def test_automatic_buck_purchase_happens_before_the_months_service() -> None:
    a = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(
            does=50,
            bucks=0,
            max_breeding_does=50,
            auto_purchase_bucks=True,
            foundation_flock_state="open",
        ),
    )
    a.reproduction.conception_rate = 1.0
    a.mortality.adult = 0.0
    first = run_simulation(a, with_break_even=False).months[0]

    assert first.purchases_head == pytest.approx(2.0)
    assert first.bucks == pytest.approx(2.0)
    assert first.pregnant_does == pytest.approx(50.0)


def test_feed_price_draw_moves_purchased_green_fodder() -> None:
    a = SimulationAssumptions(meta=MetaAssumptions(horizon_months=12))
    draws = dict.fromkeys(_DRAW_ORDER, 1.0)
    draws["feed_price"] = 1.5
    variant = _apply_draws(a, draws)
    assert variant.feed.purchased_green_price_per_kg == pytest.approx(
        a.feed.purchased_green_price_per_kg * 1.5
    )


def test_operating_cost_draw_includes_per_head_transport() -> None:
    a = SimulationAssumptions(meta=MetaAssumptions(horizon_months=12))
    a.sales.transport_cost_per_head = 100.0
    draws = dict.fromkeys(_DRAW_ORDER, 1.0)
    draws["operating_cost"] = 1.2
    variant = _apply_draws(a, draws)
    assert variant.sales.transport_cost_per_head == pytest.approx(120.0)


def test_copula_has_expected_market_and_climate_correlation_signs() -> None:
    a = SimulationAssumptions()
    risk_vars = {name: getattr(a.risk, name) for name in _DRAW_ORDER}
    rng = random.Random(7)
    draws = [_correlated_draws(rng, risk_vars, 0.9) for _ in range(2_000)]

    meat = [draw["meat_price"] for draw in draws]
    feed = [draw["feed_price"] for draw in draws]
    fodder = [draw["fodder_yield"] for draw in draws]
    assert statistics.correlation(meat, feed) > 0.10
    assert statistics.correlation(feed, fodder) < -0.10


def test_disabled_risk_preserves_common_random_numbers_for_later_draws() -> None:
    enabled = SimulationAssumptions().risk
    disabled = enabled.model_copy(deep=True)
    disabled.meat_price.enabled = False
    enabled_vars = {name: getattr(enabled, name) for name in _DRAW_ORDER}
    disabled_vars = {name: getattr(disabled, name) for name in _DRAW_ORDER}
    enabled_draw = _correlated_draws(random.Random(99), enabled_vars, 0.6)
    disabled_draw = _correlated_draws(random.Random(99), disabled_vars, 0.6)
    assert disabled_draw["meat_price"] == 1.0
    assert disabled_draw["feed_price"] == enabled_draw["feed_price"]
    assert disabled_draw["operating_cost"] == enabled_draw["operating_cost"]


def test_seeded_monte_carlo_reports_liquidity_and_event_risk() -> None:
    a = SimulationAssumptions(meta=MetaAssumptions(horizon_months=12))
    a.risk.monte_carlo_runs = 8
    first = run_simulation(a, with_break_even=False, with_monte_carlo=True)
    second = run_simulation(a, with_break_even=False, with_monte_carlo=True)
    assert first.monte_carlo == second.monte_carlo
    assert first.monte_carlo is not None
    mc = first.monte_carlo
    assert len(mc.liquidity_percentiles.p50) == 12
    assert 0.0 <= mc.prob_liquidity_shortfall <= 1.0
    assert 0.0 <= mc.prob_dscr_below_one <= 1.0


def test_annual_event_probability_one_produces_a_bounded_shock_path() -> None:
    a = SimulationAssumptions(meta=MetaAssumptions(horizon_months=12))
    a.risk.disease_outbreak_probability_annual = 1.0
    a.risk.disease_outbreak_duration_months = 3
    path = _event_shock_path(a, random.Random(1))
    assert path.disease_outbreaks == 4
    assert all(multiplier == 2.0 for multiplier in path.adult_mortality)


def test_optimizer_respects_candidate_cap_and_never_recommends_infeasible_plan() -> None:
    a = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        optimization=OptimizationAssumptions(max_candidates=4, maximum_project_cost=0.0),
    )
    optimized = run_optimization(a)
    assert optimized.evaluated_candidates <= 4
    assert optimized.feasible_candidates == 0
    assert optimized.recommended is None
    assert optimized.baseline in optimized.alternatives


def test_optimizer_flags_underfunded_capacity_and_clamps_head_boundaries() -> None:
    a = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(does=100_000, bucks=0, max_breeding_does=100_000),
        costs=CostsAssumptions(capacity_basis="planned", planned_capacity_head=1),
        optimization=OptimizationAssumptions(
            max_candidates=2,
            doe_scale_low=5.0,
            doe_scale_high=5.0,
            doe_scale_steps=1,
            minimum_dscr=0.0,
        ),
    )
    optimized = run_optimization(a)
    assert optimized.evaluated_candidates <= 2
    assert all(candidate.starting_does <= 100_000 for candidate in optimized.alternatives)
    assert optimized.recommended is None
    assert any("capacity" in violation for violation in optimized.baseline.constraint_violations)


def test_optimizer_finances_required_opening_bucks_and_their_stock_value() -> None:
    a = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(does=50, bucks=0, max_breeding_does=50),
        optimization=OptimizationAssumptions(
            max_candidates=2,
            minimum_dscr=0.0,
            doe_scale_low=1.0,
            doe_scale_high=1.0,
            doe_scale_steps=1,
            sale_age_radius_months=0,
            retention_step=0.0,
            loan_fraction_step=0.0,
        ),
    )
    a.finance.initial_stock_cost = 500_000.0
    optimized = run_optimization(a)
    candidates = [
        optimized.baseline,
        *([optimized.recommended] if optimized.recommended is not None else []),
        *optimized.alternatives,
    ]
    financed = next(candidate for candidate in candidates if candidate.starting_bucks == 2)

    assert optimized.baseline.starting_bucks == 0
    assert financed.project_cost - optimized.baseline.project_cost == pytest.approx(24_000.0)


def test_mirr_and_model_fingerprint_are_reproducible() -> None:
    assert mirr([-100.0, 0.0, 121.0], [0.0, 1.0, 2.0], 0.10, 0.10) == pytest.approx(0.10)
    a = SimulationAssumptions(meta=MetaAssumptions(horizon_months=12))
    first = run_simulation(a, with_break_even=False)
    second = run_simulation(a, with_break_even=False)
    assert first.model_version == "3.0.0"
    assert first.assumptions_fingerprint == second.assumptions_fingerprint
    changed = a.model_copy(deep=True)
    changed.sales.meat_price_per_kg += 1.0
    assert (
        run_simulation(changed, with_break_even=False).assumptions_fingerprint
        != first.assumptions_fingerprint
    )


def test_cross_field_validation_rejects_incoherent_advanced_inputs() -> None:
    with pytest.raises(ValidationError, match="initial_fodder_stock"):
        FeedAssumptions(
            initial_fodder_stock_kg_dm=2.0,
            fodder_storage_capacity_kg_dm=1.0,
        )
    with pytest.raises(ValidationError, match="planned_capacity_head"):
        CostsAssumptions(capacity_basis="planned", planned_capacity_head=0)
    with pytest.raises(ValidationError, match="doe_scale_low"):
        OptimizationAssumptions(doe_scale_low=2.0, doe_scale_high=1.0)
    with pytest.raises(ValidationError, match="duplicates"):
        SimulationAssumptions(
            meta=MetaAssumptions(horizon_months=12),
            sales={"festival_sale_months": [2, 2]},  # type: ignore[arg-type]
        )
