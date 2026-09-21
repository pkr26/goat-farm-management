"""Independent checks for the calibrated, market-aware financial model."""

import math
import random
import statistics
from dataclasses import replace
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import app.simulation.montecarlo as montecarlo
import app.simulation.optimization as optimization_module
from app.simulation import (
    FeedBreakdown,
    FinanceAssumptions,
    MetaAssumptions,
    OptimizationResult,
    SimulationAssumptions,
    class_feed,
    combine_feed,
    cultivated_green_supply_kg,
    mirr,
    run_simulation,
)
from app.simulation.assumptions import (
    MAX_MONEY,
    MIN_FODDER_YIELD_T,
    CostsAssumptions,
    FeedAssumptions,
    HerdAssumptions,
    HerdEventAssumptions,
    OptimizationAssumptions,
    ParityMultipliers,
    SalesAssumptions,
)
from app.simulation.engine import _run_core
from app.simulation.market import (
    cultivated_green_supply_kg_dm_for_month,
    feed_prices_for_month,
    meat_price_for_month,
    other_revenue_growth,
)
from app.simulation.montecarlo import (
    _DRAW_ORDER,
    _apply_draws,
    _correlated_draws,
    _event_shock_path,
    _histogram,
    _monthly_start_probability,
    _pct_label,
    _triangular_from_uniform,
)
from app.simulation.optimization import (
    _candidate_from_core,
    _linspace,
    _rank_key,
    _sample_evenly,
    _sample_grid,
    _unique_bounded,
    run_optimization,
)
from app.simulation.shocks import MonthlyShockPath


def test_calendar_price_growth_and_festival_uplift_are_composed_once() -> None:
    a = SimulationAssumptions(meta=MetaAssumptions(horizon_months=24, start_year_month="2026-01"))
    a.sales.meat_price_per_kg = 100.0
    # Flat calendar first: the calibrated default carries a seasonal curve,
    # and this composition test sets exactly one month's multiplier itself.
    a.sales.monthly_meat_price_multipliers = [1.0] * 12
    a.sales.monthly_meat_price_multipliers[1] = 1.2
    a.sales.annual_livestock_price_growth_rate = 0.12
    a.sales.eid_month = 2
    a.sales.festival_sale_months = [2]
    result = run_simulation(a, with_break_even=False)

    # The revalidating run normalizes the hand-set curve to mean 1.0 (the
    # base price is the documented annual mean — P3, 2026-09-20 audit), so
    # February's effective multiplier is 1.2 scaled by 12/12.2.
    norm = 12.0 / 12.2
    assert result.months[1].meat_price_per_kg == pytest.approx(
        100.0 * 1.2 * norm * math.pow(1.12, 1.0 / 12.0) * (1.0 + a.sales.eid_price_uplift)
    )
    assert result.months[12].meat_price_per_kg == pytest.approx(112.0 * norm)
    # Once exact lunar-calendar simulation months are present, the legacy
    # recurring February setting must not invent another festival in year 2.
    assert result.months[13].meat_price_per_kg == pytest.approx(
        100.0 * 1.2 * norm * math.pow(1.12, 13.0 / 12.0)
    )


def test_feed_prices_compose_source_season_growth_and_market_shock() -> None:
    green_seasonality = [1.0] * 12
    dry_seasonality = [1.0] * 12
    concentrate_seasonality = [1.0] * 12
    green_seasonality[11] = 1.1
    dry_seasonality[11] = 1.2
    concentrate_seasonality[11] = 1.3
    feed = FeedAssumptions(
        green_price_per_kg=2.0,
        purchased_green_price_per_kg=3.0,
        dry_price_per_kg=5.0,
        concentrate_price_per_kg=7.0,
        annual_feed_price_growth_rate=0.21,
        monthly_green_price_multipliers=green_seasonality,
        monthly_dry_price_multipliers=dry_seasonality,
        monthly_concentrate_price_multipliers=concentrate_seasonality,
    )

    prices = feed_prices_for_month(
        feed,
        simulation_month=13,
        calendar_month=12,
        shock_multiplier=1.5,
    )

    # Drought/market shocks affect feed bought on the market. Home-grown green
    # fodder retains its cultivation cost but shares green-fodder seasonality.
    assert prices == pytest.approx(
        (
            2.0 * 1.1 * 1.21,
            3.0 * 1.1 * 1.21 * 1.5,
            5.0 * 1.2 * 1.21 * 1.5,
            7.0 * 1.3 * 1.21 * 1.5,
        )
    )
    assert feed_prices_for_month(
        feed,
        simulation_month=13,
        calendar_month=12,
    ) == pytest.approx(
        (
            2.0 * 1.1 * 1.21,
            3.0 * 1.1 * 1.21,
            5.0 * 1.2 * 1.21,
            7.0 * 1.3 * 1.21,
        )
    )


def test_monthly_fodder_supply_normalizes_to_annual_yield_and_applies_shock() -> None:
    seasonality = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 10.0, 10.0]
    feed = FeedAssumptions(
        cultivated_fodder_acres=2.0,
        fodder_yield_t_dm_per_acre_year=6.0,
        monthly_fodder_yield_multipliers=seasonality,
    )
    monthly = [
        cultivated_green_supply_kg_dm_for_month(feed, calendar_month)
        for calendar_month in range(1, 13)
    ]

    assert sum(monthly) == pytest.approx(12_000.0)
    assert monthly[3] == pytest.approx(12_000.0 * 4.0 / sum(seasonality))
    assert cultivated_green_supply_kg_dm_for_month(feed, 4, yield_multiplier=0.5) == pytest.approx(
        monthly[3] * 0.5
    )


def test_meat_price_and_other_revenue_compound_at_the_year_boundary() -> None:
    seasonality = [1.0] * 12
    seasonality[11] = 1.1
    sales = SalesAssumptions(
        meat_price_per_kg=100.0,
        monthly_meat_price_multipliers=seasonality,
        annual_livestock_price_growth_rate=0.21,
        eid_month=12,
        eid_price_uplift=0.3,
    )

    # The hand-set curve is normalized to mean 1.0 at construction (the base
    # price is the documented annual mean — P3, 2026-09-20 audit).
    december = 1.1 * 12.0 / 12.1
    assert meat_price_for_month(
        sales,
        simulation_month=13,
        calendar_month=12,
        shock_multiplier=0.5,
    ) == pytest.approx(100.0 * december * 1.21 * 1.3 * 0.5)
    assert meat_price_for_month(
        sales,
        simulation_month=13,
        calendar_month=12,
    ) == pytest.approx(100.0 * december * 1.21 * 1.3)
    assert other_revenue_growth(sales, 1) == pytest.approx(1.0)
    assert other_revenue_growth(sales, 13) == pytest.approx(1.21)


def test_public_feed_aggregation_and_cultivated_supply_helpers() -> None:
    first = FeedBreakdown(1.0, 2.0, 3.0, 4.0, 5.0, 6.0)
    second = FeedBreakdown(10.0, 20.0, 30.0, 40.0, 50.0, 60.0)

    assert combine_feed([first, second]) == FeedBreakdown(
        dm_kg=11.0,
        green_dm_kg=22.0,
        green_kg=33.0,
        dry_kg=44.0,
        concentrate_kg=55.0,
        cost=66.0,
    )
    assert combine_feed([]) == FeedBreakdown(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    feed = FeedAssumptions(
        cultivated_fodder_acres=2.0,
        fodder_yield_t_dm_per_acre_year=6.0,
    )
    assert cultivated_green_supply_kg(feed) == pytest.approx(1_000.0)


def test_class_feed_concentrate_share_endpoints_conserve_dry_matter() -> None:
    feed = FeedAssumptions()
    expected_dm = 10.0 * 20.0 * 0.05 * 30.44
    roughage_only = class_feed(10.0, 20.0, 0.05, 0.0, feed)
    concentrate_only = class_feed(10.0, 20.0, 0.05, 1.0, feed)

    assert roughage_only.dm_kg == pytest.approx(expected_dm)
    assert roughage_only.concentrate_kg == 0.0
    assert roughage_only.green_dm_kg + roughage_only.dry_kg * feed.dry_dm_pct == pytest.approx(
        expected_dm
    )
    assert roughage_only.cost == pytest.approx(
        roughage_only.green_kg * feed.green_price_per_kg
        + roughage_only.dry_kg * feed.dry_price_per_kg
    )

    assert concentrate_only.dm_kg == pytest.approx(expected_dm)
    assert concentrate_only.green_dm_kg == 0.0
    assert concentrate_only.green_kg == 0.0
    assert concentrate_only.dry_kg == 0.0
    assert concentrate_only.concentrate_kg * feed.concentrate_dm_pct == pytest.approx(expected_dm)
    assert concentrate_only.cost == pytest.approx(
        concentrate_only.concentrate_kg * feed.concentrate_price_per_kg
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
    # Isolate the transport line: the default 3% mandi commission would add a
    # revenue-proportional term this hand-check does not carry.
    a.sales.selling_cost_fraction = 0.0
    result = run_simulation(a, with_break_even=False)

    month_1 = result.months[0]
    month_13 = result.months[12]
    assert month_1.selling_cost == pytest.approx((month_1.sales_head + month_1.culls_head) * 100.0)
    assert month_13.selling_cost == pytest.approx(
        (month_13.sales_head + month_13.culls_head) * 112.0
    )


def test_green_fodder_is_physically_sourced_and_costed_by_source() -> None:
    no_land = SimulationAssumptions(meta=MetaAssumptions(horizon_months=12))
    # The calibrated default grows 3 acres; the "no land" arm of this identity
    # pins the true zero-acre plan explicitly.
    no_land.feed.cultivated_fodder_acres = 0.0
    no_land_result = run_simulation(no_land, with_break_even=False)
    assert all(month.feed_homegrown_green_kg == 0.0 for month in no_land_result.months)
    assert all(
        month.feed_purchased_green_kg == pytest.approx(month.feed_green_kg)
        for month in no_land_result.months
    )

    # Right-sized cultivation: enough to cover the herd, not so much that the
    # crop itself dominates the bill. Home-grown fodder is charged on what is
    # GROWN (green_price_per_kg is a cultivation cost), so acreage is a real
    # trade-off — substituting ₹1.00/kg home production for ₹2.50/kg purchase
    # saves money only while the crop is actually eaten.
    with_land = no_land.model_copy(deep=True)
    with_land.feed.cultivated_fodder_acres = 5.0
    with_land_result = run_simulation(with_land, with_break_even=False)
    assert all(month.feed_purchased_green_kg == 0.0 for month in with_land_result.months)
    assert sum(month.feed_cost for month in with_land_result.months) < sum(
        month.feed_cost for month in no_land_result.months
    )

    # ...and over-planting costs real money, which is the whole point of
    # costing production rather than consumption: 100 acres for this herd
    # wastes most of the crop, and the projection must say so.
    over_planted = no_land.model_copy(deep=True)
    over_planted.feed.cultivated_fodder_acres = 100.0
    over_planted_result = run_simulation(over_planted, with_break_even=False)
    assert sum(month.feed_cost for month in over_planted_result.months) > sum(
        month.feed_cost for month in no_land_result.months
    )
    assert sum(month.fodder_waste_kg_dm for month in over_planted_result.months) > 0.0


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
    # Young stock is insured at the DESEASONALIZED, non-festival base price
    # (no August discount either): premiums must not move with the mandi
    # calendar (the old market-value valuation spiked them in Bakrid months).
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
            # Zero cultivation isolates the storage balance: the opening stock
            # is the month's only supply.
            cultivated_fodder_acres=0.0,
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

    expected_depreciation = (
        breakdown.shed_cost * 0.90
        + breakdown.equipment_cost * 0.80
        # Auto-purchased sires capitalize: each monthly vintage depreciates
        # straight-line over the 60-month breeding-stock life for the months
        # it owns inside this 12-month horizon (the auto-restock tops up
        # fractional mortality losses every month, so vintages keep arriving).
        + sum(
            month.breeding_stock_capex * (12.0 - month.month + 1.0) / 60.0
            for month in result.months
        )
    )
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
    # Flat parity keeps conception exactly at the configured rate (the
    # default parity table drags the mixed-age foundation slightly below 1).
    a.reproduction.parity_multipliers = ParityMultipliers(litter_size=[1.0], conception_rate=[1.0])
    a.mortality.adult = 0.0
    first = run_simulation(a, with_break_even=False).months[0]
    # One buck serves 20 does at the single-sourced 1:20 policy.
    assert first.pregnant_does == pytest.approx(20.0)
    assert first.open_does == pytest.approx(80.0)


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
    a.reproduction.parity_multipliers = ParityMultipliers(litter_size=[1.0], conception_rate=[1.0])
    a.mortality.adult = 0.0
    first = run_simulation(a, with_break_even=False).months[0]

    assert first.purchases_head == pytest.approx(3.0)
    assert first.bucks == pytest.approx(3.0)
    assert first.pregnant_does == pytest.approx(50.0)


def test_feed_price_draw_moves_purchased_green_fodder() -> None:
    a = SimulationAssumptions(meta=MetaAssumptions(horizon_months=12))
    draws = dict.fromkeys(_DRAW_ORDER, 1.0)
    draws["feed_price"] = 1.5
    variant = _apply_draws(a, draws)
    assert variant.feed.purchased_green_price_per_kg == pytest.approx(
        a.feed.purchased_green_price_per_kg * 1.5
    )
    # Home-grown green fodder is a cultivation cost (its risk is the
    # fodder_yield draw), so the purchased-feed price draw must leave it
    # untouched — matching the drought/shock channel in market.py, which
    # also leaves the home green price unshocked.
    assert variant.feed.green_price_per_kg == a.feed.green_price_per_kg
    assert variant.feed.dry_price_per_kg == pytest.approx(a.feed.dry_price_per_kg * 1.5)
    assert variant.feed.concentrate_price_per_kg == pytest.approx(
        a.feed.concentrate_price_per_kg * 1.5
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


def test_apply_draws_scales_every_risk_control_in_the_documented_direction() -> None:
    assumptions = SimulationAssumptions()
    assumptions.sales.meat_price_per_kg = 100.0
    assumptions.sales.transport_cost_per_head = 50.0
    assumptions.feed.green_price_per_kg = 2.0
    assumptions.feed.purchased_green_price_per_kg = 3.0
    assumptions.feed.dry_price_per_kg = 4.0
    assumptions.feed.concentrate_price_per_kg = 5.0
    assumptions.feed.fodder_yield_t_dm_per_acre_year = 6.0
    assumptions.mortality.kid_pre_weaning = 0.1
    assumptions.mortality.kid_post_weaning = 0.2
    assumptions.mortality.adult = 0.3
    assumptions.mortality.grower = 0.4
    assumptions.reproduction.conception_rate = 0.5
    assumptions.costs.vet_per_animal_per_year = 10.0
    assumptions.costs.labour_per_month = 20.0
    assumptions.costs.misc_overhead_per_month = 30.0
    draws = {
        "meat_price": 1.5,
        "feed_price": 2.0,
        "adult_mortality": 1.5,
        "kid_mortality": 1.5,
        "litter_size": 1.0,
        "conception_rate": 1.5,
        "fodder_yield": 0.5,
        "operating_cost": 2.0,
        "milk_price": 1.25,
    }

    variant = _apply_draws(assumptions, draws)

    assert variant.sales.meat_price_per_kg == pytest.approx(150.0)
    assert variant.sales.transport_cost_per_head == pytest.approx(100.0)
    # Home-grown green fodder is cultivation cost (risk carried by the
    # fodder_yield draw), so the purchased-feed price draw leaves it at base.
    assert variant.feed.green_price_per_kg == pytest.approx(2.0)
    assert variant.feed.purchased_green_price_per_kg == pytest.approx(6.0)
    assert variant.feed.dry_price_per_kg == pytest.approx(8.0)
    assert variant.feed.concentrate_price_per_kg == pytest.approx(10.0)
    assert variant.feed.fodder_yield_t_dm_per_acre_year == pytest.approx(3.0)
    assert variant.mortality.kid_pre_weaning == pytest.approx(0.15)
    assert variant.mortality.kid_post_weaning == pytest.approx(0.3)
    assert variant.mortality.adult == pytest.approx(0.45)
    assert variant.mortality.grower == pytest.approx(0.6)
    assert variant.reproduction.conception_rate == pytest.approx(0.75)
    assert variant.costs.vet_per_animal_per_year == pytest.approx(20.0)
    assert variant.costs.labour_per_month == pytest.approx(40.0)
    assert variant.costs.misc_overhead_per_month == pytest.approx(60.0)


def test_apply_draws_preserves_the_smallest_positive_fodder_yield() -> None:
    assumptions = SimulationAssumptions()
    assumptions.feed.fodder_yield_t_dm_per_acre_year = MIN_FODDER_YIELD_T
    draws = dict.fromkeys(_DRAW_ORDER, 1.0)
    draws["fodder_yield"] = 0.5

    variant = _apply_draws(assumptions, draws)

    # RT-L8-4: the clamp floor is the schema floor (0.01), not a denormal —
    # a downside draw clamps back to validity instead of under it.
    assert variant.feed.fodder_yield_t_dm_per_acre_year == MIN_FODDER_YIELD_T


@pytest.mark.parametrize("uniform", [0.1, 0.25, 0.5, 0.8])
def test_triangular_inverse_matches_its_piecewise_cdf(uniform: float) -> None:
    low, high, mode = 0.5, 2.0, 1.0
    split = (mode - low) / (high - low)
    expected = (
        low + math.sqrt(uniform * (high - low) * (mode - low))
        if uniform < split
        else high - math.sqrt((1.0 - uniform) * (high - low) * (high - mode))
    )

    assert _triangular_from_uniform(low, high, uniform) == pytest.approx(expected)


def test_correlated_draw_uses_unit_normal_factor_model() -> None:
    class NormalSequence(random.Random):
        def __init__(self, values: list[float]) -> None:
            super().__init__(0)
            self.values = iter(values)

        def normalvariate(self, mu: float = 0.0, sigma: float = 1.0) -> float:
            return mu + sigma * next(self.values)

    risk = SimulationAssumptions().risk.model_copy(deep=True)
    for name in _DRAW_ORDER:
        getattr(risk, name).enabled = name == "meat_price"
    risk_vars = {name: getattr(risk, name) for name in _DRAW_ORDER}
    factor_values = [0.25, -0.5, 0.75]
    idiosyncratic_values = [-0.4, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
    strength = 0.5

    draws = _correlated_draws(
        NormalSequence(factor_values + idiosyncratic_values), risk_vars, strength
    )

    systematic = strength * 0.8 * factor_values[0]
    systematic_variance = strength**2 * 0.8**2
    normal = systematic + math.sqrt(1.0 - systematic_variance) * idiosyncratic_values[0]
    uniform = 0.5 * (1.0 + math.erf(normal / math.sqrt(2.0)))
    expected = _triangular_from_uniform(risk.meat_price.low, risk.meat_price.high, uniform)
    assert draws["meat_price"] == pytest.approx(expected)
    assert all(draws[name] == 1.0 for name in _DRAW_ORDER if name != "meat_price")


def test_monthly_start_probability_compounds_to_the_annual_probability() -> None:
    annual_probability = 0.37
    monthly_probability = _monthly_start_probability(annual_probability)

    assert monthly_probability == pytest.approx(
        1.0 - math.pow(1.0 - annual_probability, 1.0 / 12.0)
    )
    assert 1.0 - (1.0 - monthly_probability) ** 12 == pytest.approx(annual_probability)


def test_zero_event_probability_is_neutral_even_at_rng_zero() -> None:
    class ZeroRandom(random.Random):
        def random(self) -> float:
            return 0.0

    assumptions = SimulationAssumptions(meta=MetaAssumptions(horizon_months=12))
    assumptions.risk.disease_outbreak_probability_annual = 0.0
    assumptions.risk.drought_probability_annual = 0.0
    assumptions.risk.market_crash_probability_annual = 0.0

    path = _event_shock_path(assumptions, ZeroRandom())

    assert path == MonthlyShockPath.neutral(12)


def test_drought_and_market_episodes_are_counted_and_end_before_restarting() -> None:
    class MidpointRandom(random.Random):
        def random(self) -> float:
            return 0.5

    assumptions = SimulationAssumptions(meta=MetaAssumptions(horizon_months=12))
    assumptions.risk.disease_outbreak_probability_annual = 1.0
    assumptions.risk.disease_outbreak_duration_months = 3
    assumptions.risk.drought_probability_annual = 1.0
    assumptions.risk.drought_duration_months = 4
    assumptions.risk.market_crash_probability_annual = 1.0
    assumptions.risk.market_crash_duration_months = 2

    path = _event_shock_path(assumptions, MidpointRandom())

    assert (path.disease_outbreaks, path.drought_events, path.market_crashes) == (4, 3, 6)
    assert path.adult_mortality == [2.0] * 12
    assert path.kid_mortality == [2.5] * 12
    assert path.conception == [0.7] * 12
    assert path.fodder_yield == [0.5] * 12
    assert path.feed_price == [1.3] * 12
    assert path.meat_price == [0.75] * 12


def test_histogram_has_equal_width_edges_and_places_the_maximum_last() -> None:
    counts, edges = _histogram([10.0, 10.25, 10.5, 10.75], bins=3)

    assert counts == [1, 1, 2]
    assert edges == pytest.approx([10.0, 10.25, 10.5, 10.75])


def test_histogram_pads_a_constant_series_around_its_value() -> None:
    counts, edges = _histogram([5.0, 5.0, 5.0], bins=4)

    assert counts == [0, 0, 3, 0]
    assert edges == pytest.approx([4.0, 4.5, 5.0, 5.5, 6.0])


def test_monte_carlo_aggregates_each_run_and_every_reported_percentile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assumptions = SimulationAssumptions(meta=MetaAssumptions(horizon_months=12))
    assumptions.risk.monte_carlo_runs = 4
    # Legacy single-multiplier path: this test pins the aggregation plumbing
    # against the exact emitted shock paths, and the within-run annual price
    # process would rewrite those paths in place (its own tests cover it).
    assumptions.risk.within_run_price_variation = False
    npvs = [-10.0, 0.0, 0.5, 10.0]
    minimum_cash = [-5.0, 0.0, 0.5, 2.0]
    min_dscr = [None, 0.5, 1.0, 1.5]
    cores = []
    for run_index in range(4):
        months = [
            SimpleNamespace(
                total_herd=(run_index + 1) * 10.0 + month,
                cumulative_cash_flow=(run_index + 1) * 100.0 + month,
                cash_balance=(run_index + 1) * 1_000.0 + month,
            )
            for month in range(12)
        ]
        cores.append(
            SimpleNamespace(
                months=months,
                npv=npvs[run_index],
                minimum_cash_balance=minimum_cash[run_index],
                min_dscr=min_dscr[run_index],
            )
        )

    emitted_paths = [
        replace(
            MonthlyShockPath.neutral(12),
            disease_outbreaks=run_index + 1,
            drought_events=2 * (run_index + 1),
            market_crashes=3 * (run_index + 1),
        )
        for run_index in range(4)
    ]
    core_iterator = iter(cores)
    path_iterator = iter(emitted_paths)
    received_paths: list[MonthlyShockPath | None] = []

    def fake_run_core(
        _assumptions: SimulationAssumptions,
        shock_path: MonthlyShockPath | None = None,
    ) -> SimpleNamespace:
        received_paths.append(shock_path)
        return next(core_iterator)

    monkeypatch.setattr(montecarlo, "_correlated_draws", lambda *_args: {})
    monkeypatch.setattr(montecarlo, "_apply_draws", lambda value, _draws: value)
    monkeypatch.setattr(montecarlo, "_event_shock_path", lambda *_args: next(path_iterator))
    monkeypatch.setattr(montecarlo, "_run_core", fake_run_core)

    result = montecarlo.run_monte_carlo(assumptions)

    assert received_paths == emitted_paths
    assert result.herd_percentiles.p5[0] == pytest.approx(11.5)
    assert result.herd_percentiles.p25[0] == pytest.approx(17.5)
    assert result.herd_percentiles.p75[0] == pytest.approx(32.5)
    assert result.cash_percentiles.p5[0] == pytest.approx(115.0)
    assert result.liquidity_percentiles.p75[0] == pytest.approx(3_250.0)
    assert result.npv_p95 == pytest.approx(8.575)
    assert result.prob_npv_negative == pytest.approx(0.25)
    assert result.prob_liquidity_shortfall == pytest.approx(0.25)
    assert result.prob_dscr_below_one == pytest.approx(0.25)
    assert result.minimum_cash_p5 == pytest.approx(-4.25)
    assert result.minimum_cash_p50 == pytest.approx(0.25)
    assert result.ending_cash_p5 == pytest.approx(1_161.0)
    assert result.ending_cash_p50 == pytest.approx(2_511.0)
    assert result.mean_disease_outbreaks == pytest.approx(2.5)
    assert result.mean_drought_events == pytest.approx(5.0)
    assert result.mean_market_crashes == pytest.approx(7.5)


def test_sensitivity_applies_and_reports_each_low_and_high_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assumptions = SimulationAssumptions(meta=MetaAssumptions(horizon_months=12))
    assumptions.sales.meat_price_per_kg = 100.0
    assumptions.feed.green_price_per_kg = float(MAX_MONEY)
    assumptions.feed.purchased_green_price_per_kg = 500_000_000.0
    assumptions.feed.dry_price_per_kg = 250_000_000.0
    assumptions.feed.concentrate_price_per_kg = 125_000_000.0
    assumptions.mortality.kid_pre_weaning = 0.3
    assumptions.reproduction.litter_size = 0.75
    assumptions.reproduction.conception_rate = 0.6
    assumptions.growth.sale_age_months = 6
    assumptions.costs.labour_per_month = 100.0
    assumptions.finance.interest_rate_annual = 0.2

    def objective(value: SimulationAssumptions) -> SimpleNamespace:
        feed_prices = (
            value.feed.green_price_per_kg
            + value.feed.purchased_green_price_per_kg
            + value.feed.dry_price_per_kg
            + value.feed.concentrate_price_per_kg
        )
        return SimpleNamespace(
            npv=(
                value.sales.meat_price_per_kg
                + feed_prices / 1_000_000.0
                + 100.0 * value.mortality.kid_pre_weaning
                + 100.0 * value.reproduction.litter_size
                + 100.0 * value.reproduction.conception_rate
                + value.growth.sale_age_months
                + value.costs.labour_per_month
                + 100.0 * value.finance.interest_rate_annual
            )
        )

    monkeypatch.setattr(montecarlo, "_run_core", objective)

    by_name = {item.parameter: item for item in montecarlo.run_sensitivity(assumptions)}

    assert by_name["meat_price"].delta_npv_low == pytest.approx(-20.0)
    assert by_name["meat_price"].delta_npv_high == pytest.approx(20.0)
    assert by_name["feed_prices"].delta_npv_low == pytest.approx(-375.0)
    assert by_name["feed_prices"].delta_npv_high == pytest.approx(175.0)
    assert by_name["feed_prices"].label_high == "+9.3%"
    assert by_name["kid_pre_weaning_mortality"].delta_npv_low == pytest.approx(-6.0)
    assert by_name["kid_pre_weaning_mortality"].delta_npv_high == pytest.approx(6.0)
    assert by_name["litter_size"].delta_npv_low == pytest.approx(-15.0)
    assert by_name["litter_size"].delta_npv_high == pytest.approx(15.0)
    assert by_name["conception_rate"].delta_npv_low == pytest.approx(-12.0)
    assert by_name["conception_rate"].delta_npv_high == pytest.approx(12.0)
    assert by_name["sale_age_months"].delta_npv_low == pytest.approx(0.0)
    assert by_name["sale_age_months"].delta_npv_high == pytest.approx(2.0)
    assert by_name["labour_cost"].delta_npv_low == pytest.approx(-20.0)
    assert by_name["labour_cost"].delta_npv_high == pytest.approx(20.0)
    assert by_name["interest_rate"].delta_npv_low == pytest.approx(-4.0)
    assert by_name["interest_rate"].delta_npv_high == pytest.approx(4.0)

    assumptions.mortality.kid_pre_weaning = 0.8
    assumptions.reproduction.litter_size = 3.5
    bounded = {item.parameter: item for item in montecarlo.run_sensitivity(assumptions)}
    assert bounded["kid_pre_weaning_mortality"].label_high == "+12.5%"
    assert bounded["kid_pre_weaning_mortality"].delta_npv_high == pytest.approx(10.0)
    assert bounded["litter_size"].label_high == "+14.3%"
    assert bounded["litter_size"].delta_npv_high == pytest.approx(50.0)


def test_percentage_label_handles_a_zero_base() -> None:
    assert _pct_label(0.0, 123.0) == "+0%"


def test_sensitivity_and_optimization_tolerate_the_sale_age_boundary() -> None:
    """P1-5 (2026-09-20 audit): with a male-grower purchase arriving at 8
    months under sale_age_months=9, the arrival age sits exactly on the class
    chain boundary (growers must be <= sale_age − 1), so the scenario is valid
    but has NO room to lower the sale age. Both analyses sweep it ±2 months;
    the unclamped low variant (9 − 2 = 7) used to fail whole-scenario
    re-validation inside the analysis — a pydantic ValidationError out of
    run_sensitivity and a crash out of run_optimization. The sweeps clamp to
    ``min_feasible_sale_age`` (9 here), so the low side becomes a "+0 month"
    no-op and both analyses complete."""
    from app.simulation.assumptions import min_feasible_sale_age

    assumptions = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=24),
        events=[
            # Boundary: 8-month male growers under a 9-month sale age —
            # valid (8 <= 9 − 1) but infeasible to lower any further.
            HerdEventAssumptions(
                month=20, kind="purchase", animal_class="male_grower", count=5, age_months=8
            )
        ],
    )
    assumptions.growth.sale_age_months = 9
    assert min_feasible_sale_age(assumptions) == 9

    # run_sensitivity completes with all 9 tornado rows; the sale-age low side
    # clamps to the base (a reported no-op), the high side still moves +2.
    by_name = {item.parameter: item for item in montecarlo.run_sensitivity(assumptions)}
    assert len(by_name) == 9
    sale_age = by_name["sale_age_months"]
    assert sale_age.label_low == "+0 month(s)"
    assert sale_age.delta_npv_low == pytest.approx(0.0)
    assert sale_age.label_high == "+2 month(s)"

    # run_optimization completes without a ValidationError, and every sale age
    # it reports (baseline + ranked alternatives) stayed inside the feasible
    # window the clamp carved out.
    optimized = run_optimization(assumptions)
    candidates = [optimized.baseline, *optimized.alternatives]
    assert optimized.evaluated_candidates >= 1
    assert all(candidate.sale_age_months >= 9 for candidate in candidates)


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


def test_optimizer_sampling_helpers_pin_boundaries_and_axis_coverage() -> None:
    assert _linspace(2.0, 4.0, 1) == [2.0]
    assert _linspace(2.0, 2.0, 4) == [2.0]
    assert _linspace(2.0, 4.0, 2) == [2.0, 4.0]
    assert _linspace(2.0, 4.0, 3) == [2.0, 3.0, 4.0]

    assert _sample_evenly(list(range(6)), 0) == []
    assert _sample_evenly(list(range(6)), 1) == [3]
    assert _sample_evenly(list(range(7)), 3) == [0, 3, 6]

    # A one-ULP arithmetic echo of 0.3 is the same decision, while a genuine
    # schema-scale delta remains distinct and all decisions stay ordered.
    assert _unique_bounded([0.3000000001, 1.0 - 0.7, 0.3, -0.1, 1.1], 0.0, 1.0) == [
        0.0,
        0.3,
        0.3000000001,
        1.0,
    ]
    ulp_steps = [0.5]
    for _ in range(9):
        ulp_steps.append(math.nextafter(ulp_steps[-1], math.inf))
    # Eight ULPs is the documented arithmetic-noise tolerance; the ninth is a
    # distinct schema-scale decision. Pin both sides of the strict boundary.
    assert _unique_bounded([ulp_steps[9], ulp_steps[8], ulp_steps[0]], 0.0, 1.0) == [
        ulp_steps[0],
        ulp_steps[9],
    ]

    assert _sample_grid(([0, 1], ["a", "b", "c"]), 6) == [
        (0, "a"),
        (0, "b"),
        (0, "c"),
        (1, "a"),
        (1, "b"),
        (1, "c"),
    ]
    assert _sample_grid(([0, 1, 2], ["a", "b", "c"]), 6) == [
        (0, "a"),
        (0, "b"),
        (0, "c"),
        (2, "a"),
        (2, "b"),
        (2, "c"),
    ]
    assert _sample_grid(([0, 1, 2, 3], ["a", "b"]), 4) == [
        (0, "a"),
        (0, "b"),
        (3, "a"),
        (3, "b"),
    ]
    # When every axis is already width two, the widest axis still has to shed
    # one value to honor a two-candidate budget. Stopping at width two aliases
    # the flattened prefix onto only the first value of that axis.
    assert _sample_grid(([0, 1], ["a", "b"]), 2) == [(1, "a"), (1, "b")]


def test_optimizer_candidate_constraints_are_exact_and_auditable() -> None:
    assumptions = SimulationAssumptions(meta=MetaAssumptions(horizon_months=12))
    assumptions.optimization.maximum_project_cost = 100.0
    assumptions.optimization.maximum_funding_gap = 10.0
    assumptions.optimization.minimum_dscr = 1.2
    core = replace(
        _run_core(assumptions),
        project_cost=100.0,
        additional_working_capital_required=10.0,
        min_dscr=1.2,
        avg_dscr=1.2,
        capacity_places=50.0,
        projected_peak_head=50.0,
        cash_flows=[-100.0, 110.0],
        discount_times=[0.0, 1.0],
    )

    boundary = _candidate_from_core(assumptions, core)
    assert boundary.rank == 0
    assert boundary.feasible is True
    assert boundary.constraint_violations == []
    assert boundary.irr == pytest.approx(0.1)
    assert boundary.min_dscr == 1.2

    no_debt = _candidate_from_core(assumptions, replace(core, min_dscr=None))
    assert no_debt.feasible is True
    assert no_debt.min_dscr is None

    tolerance_boundary = _candidate_from_core(
        assumptions,
        replace(core, projected_peak_head=core.capacity_places + 1e-9),
    )
    assert tolerance_boundary.feasible is True

    violating = _candidate_from_core(
        assumptions,
        replace(
            core,
            project_cost=100.01,
            additional_working_capital_required=10.01,
            avg_dscr=1.19,
            projected_peak_head=50.5,
        ),
    )
    assert violating.feasible is False
    assert violating.constraint_violations == [
        "project cost exceeds the configured maximum",
        "liquidity funding gap exceeds the configured maximum",
        "average DSCR is below the configured floor",
        "projected herd exceeds funded housing/equipment capacity",
    ]


def test_optimizer_rank_keys_pin_each_objective_and_feasibility() -> None:
    assumptions = SimulationAssumptions(meta=MetaAssumptions(horizon_months=12))
    candidate = _candidate_from_core(assumptions, _run_core(assumptions)).model_copy(
        update={
            "feasible": True,
            "npv": 100.0,
            "funding_gap": 20.0,
            "minimum_cash_balance": -5.0,
            "min_dscr": 1.5,
        }
    )

    assert _rank_key(candidate, "liquidity") == (1.0, -5.0, 100.0)
    assert _rank_key(candidate, "npv") == (1.0, 100.0, -5.0)
    assert _rank_key(candidate, "balanced") == (1.0, 80.0, 1.5, -5.0)
    assert _rank_key(candidate.model_copy(update={"min_dscr": None}), "balanced") == (
        1.0,
        80.0,
        1_000_000.0,
        -5.0,
    )
    assert _rank_key(candidate.model_copy(update={"feasible": False}), "npv")[0] == 0.0


def _recorded_optimization(
    monkeypatch: pytest.MonkeyPatch,
    assumptions: SimulationAssumptions,
    *,
    rank_by_does: bool = False,
) -> tuple[OptimizationResult, list[SimulationAssumptions]]:
    base_core = _run_core(assumptions)
    observed: list[SimulationAssumptions] = []

    def fake_run_core(candidate: SimulationAssumptions):
        observed.append(candidate.model_copy(deep=True))
        if not rank_by_does:
            return base_core
        does = float(candidate.herd.does)
        return replace(
            base_core,
            npv=does,
            additional_working_capital_required=2.0 * does,
            minimum_cash_balance=-does,
            min_dscr=None,
        )

    monkeypatch.setattr(optimization_module, "_run_core", fake_run_core)
    return optimization_module.run_optimization(assumptions), observed


def test_optimizer_ranks_descending_and_reports_exact_result_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assumptions = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(
            does=10,
            bucks=0,
            auto_purchase_bucks=False,
            max_breeding_does=50,
        ),
        # Ranking mechanics test: equity-funded (no debt → no DSCR gate),
        # so feasibility tracks the cost/gap/capacity constraints alone.
        finance=FinanceAssumptions(loan_fraction_of_project_cost=0.0),
        optimization=OptimizationAssumptions(
            objective="npv",
            max_candidates=10,
            # Ranking mechanics test: the floor sits below the thin year-1
            # coverage these 12-month toys produce (avg-DSCR gate).
            minimum_dscr=0.0,
            doe_scale_low=0.5,
            doe_scale_high=1.5,
            doe_scale_steps=7,
            sale_age_radius_months=0,
            retention_step=0.0,
            loan_fraction_step=0.0,
            festival_hold_radius_months=0,
            service_cull_radius_months=0,
        ),
    )

    optimized, observed = _recorded_optimization(monkeypatch, assumptions, rank_by_does=True)

    assert [item.herd.does for item in observed] == [10, 5, 7, 8, 12, 13, 15]
    assert optimized.evaluated_candidates == 7
    assert optimized.feasible_candidates == 7
    assert optimized.recommended is not None
    assert optimized.recommended.starting_does == 15
    assert optimized.recommended.rank == 1
    assert optimized.baseline.starting_does == 10
    assert optimized.baseline.rank == 4
    assert [item.starting_does for item in optimized.alternatives] == [13, 12, 10, 8]
    assert [item.rank for item in optimized.alternatives] == [2, 3, 4, 5]


def test_optimizer_hard_candidate_cap_and_rounded_decisions_are_deduplicated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capped = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(does=10, bucks=0, auto_purchase_bucks=False),
        optimization=OptimizationAssumptions(
            max_candidates=1,
            minimum_dscr=0.0,
            doe_scale_low=2.0,
            doe_scale_high=2.0,
            doe_scale_steps=1,
            sale_age_radius_months=0,
            retention_step=0.0,
            loan_fraction_step=0.0,
        ),
    )
    capped_result, capped_observed = _recorded_optimization(monkeypatch, capped)
    assert capped_result.evaluated_candidates == 1
    assert len(capped_observed) == 1

    rounded = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(
            does=10,
            bucks=0,
            auto_purchase_bucks=False,
            max_breeding_does=0,
        ),
        optimization=OptimizationAssumptions(
            max_candidates=4,
            minimum_dscr=0.0,
            doe_scale_low=0.01,
            doe_scale_high=0.03,
            doe_scale_steps=3,
            sale_age_radius_months=0,
            retention_step=0.0,
            loan_fraction_step=0.0,
            festival_hold_radius_months=0,
            service_cull_radius_months=0,
        ),
    )
    rounded_result, rounded_observed = _recorded_optimization(monkeypatch, rounded)
    assert rounded_result.evaluated_candidates == 2
    assert [(item.herd.does, item.herd.max_breeding_does) for item in rounded_observed] == [
        (10, 0),
        (0, 0),
    ]


def test_optimizer_includes_bounded_sale_retention_and_loan_axis_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def one_axis_policy(**overrides: object) -> OptimizationAssumptions:
        values: dict[str, object] = {
            "max_candidates": 10,
            "minimum_dscr": 0.0,
            "doe_scale_low": 1.0,
            "doe_scale_high": 1.0,
            "doe_scale_steps": 1,
            "sale_age_radius_months": 0,
            "retention_step": 0.0,
            "loan_fraction_step": 0.0,
            # The new integer axes have their own dedicated test; the legacy
            # one-axis goldens here pin exactly one active dimension each.
            "festival_hold_radius_months": 0,
            "service_cull_radius_months": 0,
        }
        values.update(overrides)
        return OptimizationAssumptions.model_validate(values)

    lower_sale = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        optimization=one_axis_policy(sale_age_radius_months=2),
    )
    lower_sale.growth.sale_age_months = 7
    _, observed = _recorded_optimization(monkeypatch, lower_sale)
    assert {item.growth.sale_age_months for item in observed} == {6, 7, 8, 9}

    upper_sale = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        optimization=one_axis_policy(sale_age_radius_months=2),
    )
    upper_sale.growth.sale_age_months = 23
    _, observed = _recorded_optimization(monkeypatch, upper_sale)
    assert {item.growth.sale_age_months for item in observed} == {21, 22, 23, 24}

    lower_retention = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        optimization=one_axis_policy(retention_step=0.2),
    )
    lower_retention.herd.female_retention_fraction = 0.1
    _, observed = _recorded_optimization(monkeypatch, lower_retention)
    assert sorted({item.herd.female_retention_fraction for item in observed}) == pytest.approx(
        [0.0, 0.1, 0.3]
    )

    upper_retention = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        optimization=one_axis_policy(retention_step=0.2),
    )
    upper_retention.herd.female_retention_fraction = 0.9
    _, observed = _recorded_optimization(monkeypatch, upper_retention)
    assert sorted({item.herd.female_retention_fraction for item in observed}) == pytest.approx(
        [0.7, 0.9, 1.0]
    )

    loan_steps = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        optimization=one_axis_policy(loan_fraction_step=0.2),
    )
    loan_steps.finance.loan_fraction_of_project_cost = 0.3
    loan_steps.finance.subsidy_fraction = 0.2
    _, observed = _recorded_optimization(monkeypatch, loan_steps)
    observed_loan_fractions = {item.finance.loan_fraction_of_project_cost for item in observed}
    assert sorted(observed_loan_fractions) == pytest.approx([0.1, 0.3, 0.5])

    subsidized = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        optimization=one_axis_policy(loan_fraction_step=0.8),
    )
    subsidized.finance.loan_fraction_of_project_cost = 0.3
    subsidized.finance.subsidy_fraction = 0.7
    _, observed = _recorded_optimization(monkeypatch, subsidized)
    assert {item.finance.loan_fraction_of_project_cost for item in observed} == {0.0, 0.3}


def test_optimizer_scales_heads_caps_and_required_bucks_without_sentinel_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def scale_policy(scale: float) -> OptimizationAssumptions:
        return OptimizationAssumptions(
            max_candidates=2,
            minimum_dscr=0.0,
            doe_scale_low=scale,
            doe_scale_high=scale,
            doe_scale_steps=1,
            sale_age_radius_months=0,
            retention_step=0.0,
            loan_fraction_step=0.0,
        )

    unlimited = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(
            does=10,
            bucks=0,
            auto_purchase_bucks=False,
            max_breeding_does=0,
        ),
        optimization=scale_policy(2.0),
    )
    _, observed = _recorded_optimization(monkeypatch, unlimited)
    assert [(item.herd.does, item.herd.max_breeding_does) for item in observed] == [
        (10, 0),
        (20, 0),
    ]

    zero_scaled = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(
            does=1,
            bucks=0,
            auto_purchase_bucks=False,
            max_breeding_does=1,
        ),
        optimization=scale_policy(0.1),
    )
    _, observed = _recorded_optimization(monkeypatch, zero_scaled)
    assert [(item.herd.does, item.herd.max_breeding_does) for item in observed] == [
        (1, 1),
        (0, 1),
    ]

    cap_below_opening = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(
            does=10,
            bucks=0,
            auto_purchase_bucks=False,
            max_breeding_does=5,
        ),
        optimization=scale_policy(0.5),
    )
    _, observed = _recorded_optimization(monkeypatch, cap_below_opening)
    assert [(item.herd.does, item.herd.max_breeding_does) for item in observed] == [
        (10, 5),
        (5, 5),
    ]

    scaled_cap = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(
            does=10,
            bucks=0,
            auto_purchase_bucks=False,
            max_breeding_does=20,
        ),
        optimization=scale_policy(0.1),
    )
    _, observed = _recorded_optimization(monkeypatch, scaled_cap)
    assert [(item.herd.does, item.herd.max_breeding_does) for item in observed] == [
        (10, 20),
        (1, 2),
    ]

    required_buck = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(
            does=1,
            bucks=0,
            auto_purchase_bucks=True,
            max_breeding_does=1,
        ),
        optimization=scale_policy(1.0),
    )
    _, observed = _recorded_optimization(monkeypatch, required_buck)
    assert [(item.herd.does, item.herd.bucks) for item in observed] == [(1, 0), (1, 1)]


def test_optimizer_adjusts_only_explicit_stock_cost_and_preserves_its_sentinel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def scale_policy(scale: float) -> OptimizationAssumptions:
        return OptimizationAssumptions(
            max_candidates=2,
            minimum_dscr=0.0,
            doe_scale_low=scale,
            doe_scale_high=scale,
            doe_scale_steps=1,
            sale_age_radius_months=0,
            retention_step=0.0,
            loan_fraction_step=0.0,
        )

    explicit = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(does=50, bucks=2, max_breeding_does=50),
        optimization=scale_policy(0.5),
    )
    explicit.finance.initial_stock_cost = 500_000.0
    _, observed = _recorded_optimization(monkeypatch, explicit)
    assert [(item.herd.does, item.herd.bucks) for item in observed] == [(50, 2), (25, 2)]
    # The delta uses the calibrated doe/buck prices: -25 does x 9,500 and
    # -2 bucks x 15,000 (ceil(25/20) = 2 sires) off an explicit 500,000.
    assert [item.finance.initial_stock_cost for item in observed] == [500_000.0, 262_500.0]

    automatic = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(
            does=10,
            bucks=0,
            auto_purchase_bucks=False,
            max_breeding_does=0,
        ),
        optimization=scale_policy(2.0),
    )
    _, observed = _recorded_optimization(monkeypatch, automatic)
    assert [item.finance.initial_stock_cost for item in observed] == [0.0, 0.0]

    tiny_explicit = automatic.model_copy(deep=True)
    tiny_explicit.finance.initial_stock_cost = 1.0
    _, observed = _recorded_optimization(monkeypatch, tiny_explicit)
    assert [item.finance.initial_stock_cost for item in observed] == [1.0, 95_001.0]

    floored = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(
            does=50,
            bucks=0,
            auto_purchase_bucks=False,
            max_breeding_does=0,
        ),
        optimization=scale_policy(0.1),
    )
    floored.finance.initial_stock_cost = 100.0
    _, observed = _recorded_optimization(monkeypatch, floored)
    assert [item.finance.initial_stock_cost for item in observed] == [100.0, 0.01]


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
    financed = next(candidate for candidate in candidates if candidate.starting_bucks == 3)

    assert optimized.baseline.starting_bucks == 0
    # 50 does at 1:20 are financed with a 3-sire battery.
    assert financed.project_cost - optimized.baseline.project_cost == pytest.approx(45_000.0)


def test_mirr_and_model_fingerprint_are_reproducible() -> None:
    assert mirr([-100.0, 0.0, 121.0], [0.0, 1.0, 2.0], 0.10, 0.10) == pytest.approx(0.10)
    a = SimulationAssumptions(meta=MetaAssumptions(horizon_months=12))
    first = run_simulation(a, with_break_even=False)
    second = run_simulation(a, with_break_even=False)
    assert first.model_version == "3.3.0"
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
