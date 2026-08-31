"""Financial correctness tests for the simulation engine.

Independent verification written from first principles (a lender's/modeler's
view), complementing the golden unit tests in test_simulation_engine.py:

1. Financing validation — equity can never go negative (loan + subsidy <= 1)
   and the moratorium must be shorter than the loan term, otherwise the
   principal silently vanishes from every cash flow.
2. Terminal debt — when the loan outlives the horizon, the outstanding
   balance is repaid in the final month as extra principal inside
   ``debt_service`` (no terminal asset value in v1, so terminal debt must not
   be dropped — and the annual P&L and DSCR must see the balloon).
3. Herd mass balance — animals are conserved across a grid of scenarios:
   herd_t == herd_{t-1} + births + purchases - deaths - sales - culls.
4. Accounting identities — monthly rows sum to the annual P&L, EBITDA and
   debt-service decompositions hold, and the amortization schedule chains.
5. Metric correctness — NPV at the returned IRR is ~0, the break-even meat
   price really zeroes NPV, DSCR/payback are consistent with the cash flows.
6. Biological sanity — conception/sex-ratio/stillbirth/max-age-cull/buck
   rotation edge behaviours and the steady-state kidding cadence.
"""

import math
import re
from itertools import pairwise

import httpx
import pytest
from pydantic import ValidationError

from app.simulation import (
    HerdAssumptions,
    MetaAssumptions,
    MonthlyRow,
    SimulationAssumptions,
    amortization_schedule,
    finance,
    npv,
    run_monte_carlo,
    run_sensitivity,
    run_simulation,
)
from app.simulation.assumptions import MAX_MONEY, FinanceAssumptions, HerdEventAssumptions
from app.simulation.defaults import get_preset
from app.simulation.engine import _ceil_head_ratio, _pool_avg_weight, weight_at_age
from app.simulation.montecarlo import _DRAW_ORDER, _apply_draws

from .conftest import owner_with_farm

S_ADULT = 0.95 ** (1.0 / 12.0)  # monthly adult survival, default 5% annual

MONTH_FLOAT_FIELDS = [
    "f_kids",
    "f_weaners",
    "f_growers",
    "open_does",
    "pregnant_does",
    "lactating_does",
    "m_kids",
    "m_weaners",
    "m_growers",
    "bucks",
    "total_herd",
    "births",
    "deaths",
    "sales_head",
    "sales_revenue",
    "culls_head",
    "cull_revenue",
    "milk_revenue",
    "manure_revenue",
    "purchases_head",
    "purchase_cost",
    "feed_green_kg",
    "feed_dry_kg",
    "feed_concentrate_kg",
    "feed_cost",
    "vet_cost",
    "labour_cost",
    "insurance_cost",
    "misc_cost",
    "debt_service",
    "net_cash_flow",
    "cumulative_cash_flow",
    "fodder_surplus_kg",
]


def toy(**herd_overrides: object) -> SimulationAssumptions:
    """10 open does + 1 buck, no purchases (golden-derivation herd).

    The single buck matters: conception is gated on buck presence (a zero-buck
    herd never conceives), and one buck serves any doe count at the full rate
    in v1, so the golden math is unchanged.
    """
    herd = {
        "does": 10,
        "bucks": 1,
        "auto_purchase_bucks": False,
        "foundation_flock_state": "open",
        **herd_overrides,
    }
    return SimulationAssumptions(herd=HerdAssumptions(**herd))  # type: ignore[arg-type]


def initial_herd(a: SimulationAssumptions) -> float:
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


def revenue_of(row: MonthlyRow) -> float:
    return row.sales_revenue + row.cull_revenue + row.milk_revenue + row.manure_revenue


def opex_of(row: MonthlyRow) -> float:
    return (
        row.feed_cost
        + row.vet_cost
        + row.labour_cost
        + row.insurance_cost
        + row.misc_cost
        + row.selling_cost
        + row.purchase_cost
    )


def assert_mass_balance(a: SimulationAssumptions) -> None:
    """Animals are conserved month over month and never go negative/NaN."""
    res = run_simulation(a, with_break_even=False)
    prev = initial_herd(a)
    for row in res.months:
        expected = (
            prev + row.births + row.purchases_head - row.deaths - row.sales_head - row.culls_head
        )
        assert row.total_herd == pytest.approx(expected, abs=1e-6), (
            f"mass balance broken in month {row.month}"
        )
        for field_name in MONTH_FLOAT_FIELDS:
            value = getattr(row, field_name)
            assert math.isfinite(value), f"{field_name} not finite in month {row.month}"
            if field_name != "fodder_surplus_kg" and field_name not in (
                "net_cash_flow",
                "cumulative_cash_flow",
            ):
                assert value >= 0.0, f"{field_name} negative in month {row.month}"
        prev = row.total_herd


# ---------------------------------------------------------------------------
# 1. Financing validation
# ---------------------------------------------------------------------------
def test_loan_plus_subsidy_above_one_is_rejected() -> None:
    with pytest.raises(ValidationError, match="equity cannot be negative"):
        FinanceAssumptions(loan_fraction_of_project_cost=0.6, subsidy_fraction=0.5)
    with pytest.raises(ValidationError, match="equity cannot be negative"):
        FinanceAssumptions(loan_fraction_of_project_cost=0.85, subsidy_fraction=0.9)


def test_loan_plus_subsidy_exactly_one_is_full_financing() -> None:
    a = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=24),
        finance=FinanceAssumptions(loan_fraction_of_project_cost=0.75, subsidy_fraction=0.25),
    )
    res = run_simulation(a, with_break_even=False)
    m = res.metrics
    assert m.equity == pytest.approx(0.0, abs=1e-6)
    assert m.loan_amount + m.subsidy_amount == pytest.approx(m.project_cost)


def test_moratorium_covering_the_term_is_rejected() -> None:
    with pytest.raises(ValidationError, match="never repaid"):
        FinanceAssumptions(loan_term_months=12, moratorium_months=12)
    with pytest.raises(ValidationError, match="never repaid"):
        FinanceAssumptions(loan_term_months=36, moratorium_months=60)


def test_moratorium_one_short_of_term_repays_in_full() -> None:
    schedule = amortization_schedule(100000.0, 0.12, 12, 11)
    # A single EMI month: the whole balance is cleared in the final payment.
    assert schedule[-1].closing_balance == pytest.approx(0.0, abs=1e-6)
    assert sum(row.principal for row in schedule) == pytest.approx(100000.0, abs=1e-6)


# ---------------------------------------------------------------------------
# 2. Terminal debt when the loan outlives the horizon
# ---------------------------------------------------------------------------
def test_terminal_balance_charged_in_final_month() -> None:
    a = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=24),
        finance=FinanceAssumptions(loan_term_months=120, moratorium_months=12),
    )
    a.sales.meat_price_per_kg = 700.0  # positive EBITDA keeps "worse" pointing down
    res = run_simulation(a, with_break_even=False)
    balance_at_24 = res.amortization[23].closing_balance
    assert balance_at_24 > 0.0  # 96 scheduled payments remain after the horizon
    final = res.months[-1]
    # The balloon is part of the final month's debt service (as principal).
    assert final.debt_service == pytest.approx(res.amortization[23].payment + balance_at_24)
    assert final.net_cash_flow == pytest.approx(
        revenue_of(final) + final.terminal_value - opex_of(final) - final.debt_service - final.tax
    )
    # Non-final months carry only scheduled debt service.
    mid = res.months[-2]
    assert mid.debt_service == pytest.approx(res.amortization[22].payment)
    assert mid.net_cash_flow == pytest.approx(
        revenue_of(mid) - opex_of(mid) - mid.debt_service - mid.tax
    )


def test_terminal_balance_reaches_annual_pl_and_dscr() -> None:
    """The balloon lands in the final year's debt_service/principal, so DSCR
    prices it in (9-5: it used to be invisible to the P&L and DSCR)."""
    a = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=24),
        finance=FinanceAssumptions(loan_term_months=120, moratorium_months=12),
    )
    a.sales.meat_price_per_kg = 700.0  # positive EBITDA keeps "worse" pointing down
    res = run_simulation(a, with_break_even=False)
    balance_at_24 = res.amortization[23].closing_balance
    final_year = res.annual_pl[-1]
    scheduled_payment = sum(row.payment for row in res.amortization[12:24])
    scheduled_principal = sum(row.principal for row in res.amortization[12:24])
    assert final_year.debt_service == pytest.approx(scheduled_payment + balance_at_24)
    assert final_year.principal == pytest.approx(scheduled_principal + balance_at_24)
    # The interest+principal decomposition identity survives the balloon.
    assert final_year.debt_service == pytest.approx(final_year.interest + final_year.principal)
    # DSCR of the terminal year reflects the balloon — materially below the
    # figure that would exclude it (0.19 vs a reported 1.20 without it).
    assert res.metrics.dscr_per_year[-1] == pytest.approx(
        final_year.ebitda / final_year.debt_service
    )
    assert res.metrics.dscr_per_year[-1] < final_year.ebitda / scheduled_payment


def test_terminal_balance_reaches_npv_and_cumulative_cash() -> None:
    a = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=24),
        finance=FinanceAssumptions(loan_term_months=120, moratorium_months=12),
    )
    res = run_simulation(a, with_break_even=False)
    m = res.metrics
    flows = [-m.equity, *[month.net_cash_flow for month in res.months]]
    times = [0.0, *[month.month / 12.0 for month in res.months]]
    assert m.npv == pytest.approx(npv(a.finance.discount_rate_annual, flows, times))
    assert res.months[-1].cumulative_cash_flow == pytest.approx(
        -m.equity + sum(row.net_cash_flow for row in res.months)
    )


def test_npv_uses_monthly_cash_timing_not_year_end_lumping() -> None:
    a = SimulationAssumptions(meta=MetaAssumptions(horizon_months=24))
    result = run_simulation(a, with_break_even=False)
    monthly_flows = [-result.metrics.equity, *[row.net_cash_flow for row in result.months]]
    monthly_times = [0.0, *[row.month / 12.0 for row in result.months]]
    annual_flows = [-result.metrics.equity, *[row.net_cash_flow for row in result.annual_pl]]
    annual_times = [0.0, 1.0, 2.0]

    assert result.metrics.npv == pytest.approx(
        npv(a.finance.discount_rate_annual, monthly_flows, monthly_times)
    )
    assert result.metrics.npv != pytest.approx(
        npv(a.finance.discount_rate_annual, annual_flows, annual_times)
    )


def test_no_terminal_debt_balloon_when_loan_ends_within_horizon() -> None:
    a = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=24),
        finance=FinanceAssumptions(loan_term_months=24, moratorium_months=12),
    )
    res = run_simulation(a, with_break_even=False)
    assert res.amortization[-1].closing_balance == pytest.approx(0.0, abs=1e-6)
    final = res.months[-1]
    assert final.net_cash_flow == pytest.approx(
        revenue_of(final) + final.terminal_value - opex_of(final) - final.debt_service - final.tax
    )


def test_default_final_cash_includes_asset_recovery_but_no_debt_balloon() -> None:
    res = run_simulation(SimulationAssumptions(), with_break_even=False)
    final = res.months[-1]
    assert final.debt_service == 0.0
    assert final.terminal_value == pytest.approx(res.terminal_value_breakdown.total)
    assert final.terminal_value > 0.0
    assert final.net_cash_flow == pytest.approx(
        revenue_of(final) + final.terminal_value - opex_of(final) - final.tax
    )


# ---------------------------------------------------------------------------
# 3. Herd mass balance across a scenario grid
# ---------------------------------------------------------------------------
def test_mass_balance_default_run() -> None:
    assert_mass_balance(SimulationAssumptions())


def test_mass_balance_toy_open_flock() -> None:
    assert_mass_balance(toy())


def test_mass_balance_no_grower_chains() -> None:
    # Non-zero starting growers are the point: with afb == 6 / sale_age == 6
    # the grower age arrays are empty, and the foundation growers used to be
    # dropped on the floor — 50 head that vanished from the herd while shed
    # and stock cost still billed for them.
    a = toy(female_growers=20, male_growers=30)
    a.reproduction.age_at_first_breeding_months = 6
    a.growth.sale_age_months = 6
    assert_mass_balance(a)


def test_starting_growers_without_a_chain_are_kept_not_deleted() -> None:
    """afb == 6 / sale_age == 6 leave no grower slots. The females are already
    breeding-age and the males already at sale age, so both must graduate in
    month 1 exactly as a one-slot chain (afb/sale_age == 7) makes them — not
    disappear while ``capacity_places`` and ``stock_cost`` still charge for
    them."""
    a = toy(female_growers=20, male_growers=30)
    a.meta.horizon_months = 12
    a.reproduction.age_at_first_breeding_months = 6
    a.growth.sale_age_months = 6
    a.herd.max_breeding_does = 0  # unlimited: every retained female is kept
    a.herd.female_retention_fraction = 1.0
    res = run_simulation(a, with_break_even=False)
    m1 = res.months[0]
    # The 30 males are sold at sale-age weight in month 1; the 20 females join
    # the doe pool. Nothing is silently lost.
    assert m1.sales_head == pytest.approx(30.0)
    assert m1.f_growers == 0.0 and m1.m_growers == 0.0
    does = m1.open_does + m1.pregnant_does + m1.lactating_does
    assert does == pytest.approx(30.0 * S_ADULT, abs=1e-6)  # 10 foundation + 20 growers
    # Opening head are preserved, and capacity also funds the larger projected
    # peak rather than pretending the month-0 population is the maximum.
    breakdown = res.project_cost_breakdown
    assert breakdown.projected_peak_head >= 61.0
    assert breakdown.capacity_places == pytest.approx(breakdown.projected_peak_head * 1.10)
    assert breakdown.shed_cost == pytest.approx(breakdown.capacity_places * 4500.0)


def test_starting_growers_match_the_one_slot_chain_at_the_boundary() -> None:
    """Continuity check: afb/sale_age == 6 must behave like the limit of 7,
    where the single grower slot graduates in month 1."""

    def run(age: int) -> tuple[float, float]:
        a = toy(female_growers=20, male_growers=30)
        a.meta.horizon_months = 12
        a.reproduction.age_at_first_breeding_months = age
        a.growth.sale_age_months = age
        res = run_simulation(a, with_break_even=False)
        m1 = res.months[0]
        return m1.total_herd, m1.sales_head

    assert run(6) == pytest.approx(run(7))


def test_mass_balance_empty_herd_with_events() -> None:
    a = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(does=0, bucks=0, auto_purchase_bucks=False),
        events=[HerdEventAssumptions(month=3, kind="sale", animal_class="doe", count=5)],
    )
    assert_mass_balance(a)


def test_mass_balance_extreme_mortality() -> None:
    a = SimulationAssumptions(meta=MetaAssumptions(horizon_months=36))
    a.mortality.kid_pre_weaning = 0.9
    a.mortality.kid_post_weaning = 0.9
    a.mortality.grower = 0.9
    a.mortality.adult = 0.9
    assert_mass_balance(a)


def test_mass_balance_unbounded_growth() -> None:
    a = SimulationAssumptions(meta=MetaAssumptions(horizon_months=36))
    a.mortality.kid_pre_weaning = 0.0
    a.mortality.kid_post_weaning = 0.0
    a.mortality.grower = 0.0
    a.mortality.adult = 0.0
    a.culling.doe_cull_rate_annual = 0.0
    a.herd.female_retention_fraction = 1.0
    a.herd.max_breeding_does = 0  # unlimited
    res = run_simulation(a, with_break_even=False)
    # Nothing dies or leaves involuntarily: the herd never shrinks.
    for prev_row, row in pairwise(res.months):
        assert row.total_herd >= prev_row.total_herd - 1e-9
    assert_mass_balance(a)


def test_mass_balance_events_every_class() -> None:
    a = toy()
    a.meta.horizon_months = 36
    a.events = [
        HerdEventAssumptions(month=6, kind="purchase", animal_class=cls, count=3)
        for cls in (
            "doe",
            "buck",
            "female_kid",
            "male_kid",
            "female_weaner",
            "male_weaner",
            "female_grower",
            "male_grower",
        )
    ] + [
        HerdEventAssumptions(month=18, kind="sale", animal_class=cls, count=1)
        for cls in (
            "doe",
            "buck",
            "female_kid",
            "male_kid",
            "female_weaner",
            "male_weaner",
            "female_grower",
            "male_grower",
        )
    ]
    assert_mass_balance(a)


def test_mass_balance_semi_intensive_milk_breed() -> None:
    a = SimulationAssumptions(meta=MetaAssumptions(horizon_months=36))
    a.sales.lactation_milk_litres = 110.0
    a.feed.grazing_dm_fraction = 0.3
    assert_mass_balance(a)


def test_mass_balance_max_horizon() -> None:
    assert_mass_balance(SimulationAssumptions(meta=MetaAssumptions(horizon_months=240)))


def test_mass_balance_huge_herd_stays_finite() -> None:
    a = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=24),
        herd=HerdAssumptions(does=100_000, bucks=4_000),
    )
    assert_mass_balance(a)


# ---------------------------------------------------------------------------
# 4. Accounting identities
# ---------------------------------------------------------------------------
def test_monthly_rows_sum_to_annual_pl() -> None:
    res = run_simulation(
        SimulationAssumptions(meta=MetaAssumptions(horizon_months=130)), with_break_even=False
    )
    assert len(res.annual_pl) == 11  # 10 full years + a 10-month stub
    for start in range(0, 130, 12):
        block = res.months[start : start + 12]
        row = res.annual_pl[start // 12]
        assert row.year == start // 12 + 1
        assert row.meat_revenue == pytest.approx(sum(m.sales_revenue for m in block))
        assert row.cull_revenue == pytest.approx(sum(m.cull_revenue for m in block))
        assert row.milk_revenue == pytest.approx(sum(m.milk_revenue for m in block))
        assert row.manure_revenue == pytest.approx(sum(m.manure_revenue for m in block))
        assert row.feed_cost == pytest.approx(sum(m.feed_cost for m in block))
        assert row.vet_cost == pytest.approx(sum(m.vet_cost for m in block))
        assert row.labour_cost == pytest.approx(sum(m.labour_cost for m in block))
        assert row.insurance_cost == pytest.approx(sum(m.insurance_cost for m in block))
        assert row.misc_cost == pytest.approx(sum(m.misc_cost for m in block))
        assert row.stock_purchases == pytest.approx(sum(m.purchase_cost for m in block))
        assert row.debt_service == pytest.approx(sum(m.debt_service for m in block))
        assert row.net_cash_flow == pytest.approx(sum(m.net_cash_flow for m in block))
        # Decomposition identities inside the annual row itself.
        assert row.total_revenue == pytest.approx(
            row.meat_revenue + row.cull_revenue + row.milk_revenue + row.manure_revenue
        )
        assert row.total_opex == pytest.approx(
            row.feed_cost
            + row.vet_cost
            + row.labour_cost
            + row.insurance_cost
            + row.misc_cost
            + row.stock_purchases
        )
        assert row.ebitda == pytest.approx(row.total_revenue - row.total_opex)
        assert row.debt_service == pytest.approx(row.interest + row.principal)


def test_amortization_schedule_chains() -> None:
    res = run_simulation(SimulationAssumptions(), with_break_even=False)
    schedule = res.amortization
    assert len(schedule) == 72
    for prev, row in pairwise(schedule):
        assert row.opening_balance == pytest.approx(prev.closing_balance, abs=1e-6)
    for row in schedule:
        assert row.payment == pytest.approx(row.interest + row.principal, abs=1e-6)
        assert row.closing_balance == pytest.approx(row.opening_balance - row.principal, abs=1e-6)
    assert sum(row.principal for row in schedule) == pytest.approx(res.metrics.loan_amount)
    assert schedule[-1].closing_balance == pytest.approx(0.0, abs=1e-6)
    # Moratorium year: interest-only, balance flat at the loan amount.
    for row in schedule[:12]:
        assert row.principal == 0.0
        assert row.opening_balance == pytest.approx(res.metrics.loan_amount)


def test_zero_loan_schedule_is_all_zero() -> None:
    schedule = amortization_schedule(0.0, 0.12, 24, 12)
    assert all(row.payment == 0.0 and row.closing_balance == 0.0 for row in schedule)


def test_project_cost_split_is_exact() -> None:
    res = run_simulation(SimulationAssumptions(), with_break_even=False)
    m = res.metrics
    b = res.project_cost_breakdown
    assert b.shed_cost + b.equipment_cost + b.stock_cost + b.working_capital == pytest.approx(
        m.project_cost
    )
    assert m.loan_amount + m.subsidy_amount + m.equity == pytest.approx(m.project_cost)


def test_cumulative_cash_chain() -> None:
    res = run_simulation(SimulationAssumptions(), with_break_even=False)
    running = -res.metrics.equity
    for row in res.months:
        running += row.net_cash_flow
        assert row.cumulative_cash_flow == pytest.approx(running)


# ---------------------------------------------------------------------------
# 5. Metric correctness
# ---------------------------------------------------------------------------
def npv_at_returned_irr(a: SimulationAssumptions) -> float | None:
    """Recompute NPV at the engine's own IRR from the result's monthly flows.

    IRR is solved from the same monthly series NPV, BCR and MIRR use, so this
    check rebuilds that series. It used to rebuild year-end lumped blocks —
    exactly the timing mismatch that let one response report an IRR above the
    discount rate and a negative NPV at the same time.
    """
    res = run_simulation(a, with_break_even=False)
    irr_value = res.metrics.irr
    if irr_value is None:
        return None
    flows = [-res.metrics.equity, *[row.net_cash_flow for row in res.months]]
    times = [0.0, *[row.month / 12.0 for row in res.months]]
    return npv(irr_value, flows, times)


def test_irr_zeroes_npv_default_run() -> None:
    value = npv_at_returned_irr(SimulationAssumptions())
    assert value is not None
    assert value == pytest.approx(0.0, abs=1e-3)


def test_irr_zeroes_npv_profitable_run() -> None:
    a = SimulationAssumptions()
    a.sales.meat_price_per_kg = 500.0
    value = npv_at_returned_irr(a)
    assert value is not None
    assert value == pytest.approx(0.0, abs=1e-3)


def test_irr_zeroes_npv_milk_breed() -> None:
    a = SimulationAssumptions()
    a.sales.lactation_milk_litres = 175.0
    a.sales.meat_price_per_kg = 450.0
    value = npv_at_returned_irr(a)
    assert value is not None
    assert value == pytest.approx(0.0, abs=1e-3)


def test_break_even_price_zeroes_npv() -> None:
    a = SimulationAssumptions()
    res = run_simulation(a)  # break-even bisection enabled
    break_even = res.metrics.break_even_meat_price_per_kg
    assert break_even is not None and break_even > 0.0
    variant = a.model_copy(deep=True)
    variant.sales.meat_price_per_kg = break_even
    assert run_simulation(variant, with_break_even=False).metrics.npv == pytest.approx(0.0, abs=1.0)


def test_break_even_price_can_recover_from_a_zero_base_price() -> None:
    a = SimulationAssumptions()
    a.sales.meat_price_per_kg = 0.0
    result = run_simulation(a)
    break_even = result.metrics.break_even_meat_price_per_kg
    assert break_even is not None and break_even > 0.0
    explanation = next(
        item for item in result.metric_explanations if item.key == "break_even_meat_price_per_kg"
    )
    assert explanation.figures["safety_margin"] is None
    variant = a.model_copy(deep=True)
    variant.sales.meat_price_per_kg = break_even
    assert run_simulation(variant, with_break_even=False).metrics.npv == pytest.approx(0.0, abs=1.0)


def test_break_even_price_can_recover_from_a_tiny_base_price() -> None:
    a = SimulationAssumptions()
    a.sales.meat_price_per_kg = 0.01

    break_even = run_simulation(a).metrics.break_even_meat_price_per_kg

    assert break_even is not None
    assert break_even > a.sales.meat_price_per_kg * 5.0
    variant = a.model_copy(deep=True)
    variant.sales.meat_price_per_kg = break_even
    assert run_simulation(variant, with_break_even=False).metrics.npv == pytest.approx(0.0, abs=1.0)


def test_break_even_price_never_exceeds_the_public_schema_ceiling() -> None:
    a = SimulationAssumptions()
    a.herd.does = 1
    a.herd.bucks = 1
    a.herd.max_breeding_does = 1
    a.costs.labour_per_month = MAX_MONEY
    a.costs.misc_overhead_per_month = MAX_MONEY
    a.feed.green_price_per_kg = 1_000_000.0
    a.feed.dry_price_per_kg = 1_000_000.0
    a.feed.concentrate_price_per_kg = 1_000_000.0
    a.sales.meat_price_per_kg = MAX_MONEY

    break_even = run_simulation(a).metrics.break_even_meat_price_per_kg
    assert break_even is not None
    assert 0.0 <= break_even <= MAX_MONEY
    variant = a.model_copy(deep=True)
    variant.sales.meat_price_per_kg = break_even
    assert run_simulation(variant, with_break_even=False).metrics.npv == pytest.approx(
        0.0, abs=10.0
    )


def test_break_even_searches_the_full_public_price_domain() -> None:
    a = SimulationAssumptions()
    a.costs.labour_per_month = 1_000_000.0

    break_even = run_simulation(a).metrics.break_even_meat_price_per_kg

    assert break_even is not None
    assert break_even > 5.0 * a.sales.meat_price_per_kg
    variant = a.model_copy(deep=True)
    variant.sales.meat_price_per_kg = break_even
    assert run_simulation(variant, with_break_even=False).metrics.npv == pytest.approx(0.0, abs=1.0)


def test_feed_price_sensitivity_labels_all_prices_not_only_green_fodder() -> None:
    a = SimulationAssumptions()
    a.feed.green_price_per_kg = 0.0
    a.feed.purchased_green_price_per_kg = 10.0
    a.feed.dry_price_per_kg = 0.0
    a.feed.concentrate_price_per_kg = 0.0

    feed = next(item for item in run_sensitivity(a) if item.parameter == "feed_prices")

    assert feed.label_low == "-20.0%"
    assert feed.label_high == "+20.0%"
    assert feed.delta_npv_low > 0.0
    assert feed.delta_npv_high < 0.0


def test_dscr_consistency() -> None:
    res = run_simulation(SimulationAssumptions(), with_break_even=False)
    active = [row for row in res.annual_pl if row.debt_service > 0.0]
    assert active  # the default 72-month loan has 6 debt years
    for row, dscr in zip(res.annual_pl, res.metrics.dscr_per_year, strict=True):
        expected = row.ebitda / row.debt_service if row.debt_service > 0.0 else 0.0
        assert dscr == pytest.approx(expected)
    expected_series = [row.ebitda / row.debt_service for row in active]
    assert res.metrics.avg_dscr == pytest.approx(sum(expected_series) / len(expected_series))
    assert res.metrics.min_dscr == pytest.approx(min(expected_series))
    # Debt years are exactly the first 6 (72-month term from month 1).
    assert [row.year for row in active] == [1, 2, 3, 4, 5, 6]


def test_avg_and_min_dscr_are_none_only_without_debt_years() -> None:
    """0.0 was the "no debt year" sentinel, which collides with the real
    thing: a year with zero or negative EBITDA is the worst DSCR there is, and
    consumers read it as "this project has no debt"."""
    a = SimulationAssumptions()
    a.finance.loan_fraction_of_project_cost = 0.0
    m = run_simulation(a, with_break_even=False).metrics
    assert all(row == 0.0 for row in m.dscr_per_year)
    assert m.avg_dscr is None and m.min_dscr is None
    # With a loan, a negative weakest year stays a number — never the sentinel.
    m = run_simulation(SimulationAssumptions(), with_break_even=False).metrics
    assert m.min_dscr is not None and m.min_dscr < 0.0
    assert m.avg_dscr is not None and m.avg_dscr < 0.0


def test_payback_matches_cumulative_series() -> None:
    a = SimulationAssumptions()
    a.sales.meat_price_per_kg = 700.0  # profitable: payback exists
    res = run_simulation(a, with_break_even=False)
    payback = res.metrics.payback_month
    assert payback is not None
    assert res.months[payback - 1].cumulative_cash_flow >= 0.0
    if payback > 1:
        assert res.months[payback - 2].cumulative_cash_flow < 0.0


def test_npv_bcr_sign_agreement_across_grid() -> None:
    """NPV > 0 must always coincide with BCR > 1 (same flows, same split)."""
    for meat_price in (200.0, 300.0, 350.0, 450.0, 600.0):
        a = SimulationAssumptions()
        a.sales.meat_price_per_kg = meat_price
        m = run_simulation(a, with_break_even=False).metrics
        assert m.bcr is not None
        assert (m.npv > 0.0) == (m.bcr > 1.0), meat_price


def test_bcr_is_gross_benefits_over_gross_costs() -> None:
    """BCR includes terminal recovery as a benefit and cash tax as a cost."""
    for meat_price in (350.0, 500.0, 700.0):
        a = SimulationAssumptions()
        a.sales.meat_price_per_kg = meat_price
        res = run_simulation(a, with_break_even=False)
        rate = a.finance.discount_rate_annual
        times = [0.0, *[row.month / 12.0 for row in res.months]]
        benefits = [
            0.0,
            *[
                row.sales_revenue
                + row.cull_revenue
                + row.milk_revenue
                + row.manure_revenue
                + row.terminal_value
                for row in res.months
            ],
        ]
        costs = [
            res.metrics.equity,
            *[opex_of(row) + row.debt_service + row.tax for row in res.months],
        ]
        expected = npv(rate, benefits, times) / npv(rate, costs, times)
        assert res.metrics.bcr == pytest.approx(expected), meat_price
        # The identity NPV = PV(benefits) - PV(costs) still holds, which is
        # why the sign agreement above survives the change.
        assert res.metrics.npv == pytest.approx(
            npv(rate, benefits, times) - npv(rate, costs, times)
        )
    # And it is no longer the net-flow ratio, which would read 4.16 at 700.
    assert res.metrics.bcr is not None and res.metrics.bcr < 2.0


# ---------------------------------------------------------------------------
# 6. Biological sanity
# ---------------------------------------------------------------------------
def test_zero_bucks_means_no_conception() -> None:
    """Conception is gated on buck presence (9-4): a zero-buck herd with
    auto-purchase off never conceives — no pregnancies, no births. The flock
    starts "open" so the only route into pregnancy is conception."""
    a = SimulationAssumptions(meta=MetaAssumptions(horizon_months=36))
    a.herd.bucks = 0
    a.herd.auto_purchase_bucks = False
    a.herd.foundation_flock_state = "open"
    res = run_simulation(a, with_break_even=False)
    assert all(row.pregnant_does == 0.0 for row in res.months)
    assert all(row.births == 0.0 for row in res.months)
    assert all(row.f_kids == 0.0 and row.m_kids == 0.0 for row in res.months)


def test_auto_purchased_buck_enables_conception_in_purchase_month() -> None:
    """Automatic sire procurement happens before service, so the accounting
    month that buys the buck must also receive its conception capacity."""
    a = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(does=10, bucks=0, foundation_flock_state="open"),
    )
    res = run_simulation(a, with_break_even=False)
    month1 = res.months[0]
    expected_conceptions = 10.0 * a.reproduction.conception_rate * S_ADULT
    assert month1.pregnant_does == pytest.approx(expected_conceptions)
    assert month1.purchases_head >= 1.0
    assert month1.bucks == pytest.approx(1.0)


def test_selling_all_bucks_stops_conception() -> None:
    a = toy()
    a.meta.horizon_months = 24
    a.events = [HerdEventAssumptions(month=1, kind="sale", animal_class="buck", count=1)]
    res = run_simulation(a, with_break_even=False)
    assert res.months[0].bucks == 0.0
    assert all(row.pregnant_does == 0.0 for row in res.months)
    assert all(row.births == 0.0 for row in res.months)


def test_zero_conception_means_no_births_and_declining_herd() -> None:
    # "open" foundation flock: no initial pregnancies, and with conception at
    # zero no doe ever conceives — the herd only declines.
    a = SimulationAssumptions(meta=MetaAssumptions(horizon_months=36))
    a.reproduction.conception_rate = 0.0
    a.herd.auto_purchase_bucks = False
    a.herd.foundation_flock_state = "open"
    res = run_simulation(a, with_break_even=False)
    assert all(row.births == 0.0 for row in res.months)
    assert all(row.f_kids == 0.0 and row.m_kids == 0.0 for row in res.months)
    prev = math.inf
    for row in res.months:
        assert row.total_herd <= prev + 1e-9
        prev = row.total_herd


def test_all_female_sex_ratio_produces_no_males() -> None:
    a = toy()
    a.reproduction.sex_ratio_female = 1.0
    res = run_simulation(a, with_break_even=False)
    for row in res.months:
        assert row.m_kids == 0.0 and row.m_weaners == 0.0 and row.m_growers == 0.0
    assert any(row.f_kids > 0.0 for row in res.months)


def test_all_male_sex_ratio_produces_no_females() -> None:
    a = toy()
    a.reproduction.sex_ratio_female = 0.0
    res = run_simulation(a, with_break_even=False)
    for row in res.months:
        assert row.f_kids == 0.0 and row.f_weaners == 0.0 and row.f_growers == 0.0
    assert any(row.m_kids > 0.0 for row in res.months)


def test_stillbirth_halves_live_births() -> None:
    a = toy()
    a.reproduction.stillbirth_rate = 0.5
    res = run_simulation(a, with_break_even=False)
    expected = 8.5 * S_ADULT**5 * 1.6 * 0.5
    assert res.months[5].births == pytest.approx(expected, abs=1e-6)


def test_max_age_cull_empties_synchronized_foundation_herd() -> None:
    """Foundation does placed at age 24 with a 36-month cap are all culled in
    month 13 (rate-based cull disabled, no replacements before month 18)."""
    a = toy()
    a.meta.horizon_months = 24
    a.culling.doe_cull_rate_annual = 0.0
    a.culling.max_doe_age_months = 36
    # A synchronized cohort needs every foundation doe at the same age; the
    # calibrated default spreads purchases over 18-42 months, so pin the
    # window to a point at 24 (also the age the old hard-coded range implied).
    a.herd.foundation_doe_age_min_months = 24
    a.herd.foundation_doe_age_max_months = 24
    res = run_simulation(a, with_break_even=False)
    m13 = res.months[12]
    assert m13.culls_head == pytest.approx(10.0 * S_ADULT**13, abs=1e-6)
    does = m13.open_does + m13.pregnant_does + m13.lactating_does
    assert does == pytest.approx(0.0, abs=1e-9)
    assert all(
        (row.open_does + row.pregnant_does + row.lactating_does) > 5.0 for row in res.months[:12]
    )


def test_buck_rotation_culls_and_restaffs() -> None:
    a = SimulationAssumptions(meta=MetaAssumptions(horizon_months=48))
    res = run_simulation(a, with_break_even=False)
    m36 = res.months[35]  # 3-year rotation fires at month 36
    surviving_bucks = 2.0 * S_ADULT**36
    does = m36.open_does + m36.pregnant_does + m36.lactating_does
    assert m36.culls_head >= surviving_bucks - 1e-6
    assert m36.cull_revenue >= surviving_bucks * 200.0 * 34.0 - 1e-6
    assert m36.bucks == pytest.approx(math.ceil(does / 25))


def test_auto_buck_purchase_scales_with_doe_count() -> None:
    a = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(does=51, bucks=0),
    )
    res = run_simulation(a, with_break_even=False)
    m1 = res.months[0]
    # Three sires arrive before service. Expected mortality then removes a
    # fractional head and the end-of-month policy top-up restores three.
    expected_purchases = 3.0 + 3.0 * (1.0 - S_ADULT)
    assert m1.purchases_head == pytest.approx(expected_purchases)
    assert m1.purchase_cost == pytest.approx(expected_purchases * 15000.0)
    assert m1.bucks == pytest.approx(3.0)


def test_auto_buck_purchase_ignores_ulp_noise_at_exact_ratio_boundary() -> None:
    """Capping a partitioned cohort at 50 can recombine to 50.00000000000001.

    That one-ULP representation error used to make ``ceil(does / 25)`` buy a
    third buck even though the herd is exactly at the two-buck policy boundary.
    """
    a = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(
            does=43,
            bucks=0,
            female_growers=14,
            max_breeding_does=50,
        ),
    )
    a.reproduction.age_at_first_breeding_months = 6
    a.mortality.adult = 0.0
    a.mortality.grower = 0.0
    a.culling.doe_cull_rate_annual = 0.0

    month1 = run_simulation(a, with_break_even=False).months[0]

    does = month1.open_does + month1.pregnant_does + month1.lactating_does
    assert does == pytest.approx(50.0, abs=1e-12)
    assert month1.purchases_head == 2.0
    assert month1.bucks == 2.0


def test_whole_unit_policy_ceil_preserves_real_fractional_demand() -> None:
    """The shared buck/labour boundary helper snaps noise, not real headcount."""
    assert _ceil_head_ratio(75.00000000000001, 75) == 1
    assert _ceil_head_ratio(75.001, 75) == 2


def test_steady_state_kidding_cadence() -> None:
    """50 capped does kid about every 10 months (5 gestation + 3 lactation +
    2 open) at ~85% conception: steady-state live births cluster near
    50 x 12/10 x 1.6 x 0.98 = 94/year."""
    res = run_simulation(SimulationAssumptions(), with_break_even=False)
    steady_births = [
        sum(row.births for row in res.months[y * 12 : (y + 1) * 12]) for y in range(6, 10)
    ]
    for yearly in steady_births:
        assert 60.0 <= yearly <= 130.0


def test_kid_pipeline_timing_matches_biology() -> None:
    """Conception month 1 -> kidding month 6 -> male sale at age 10 in month 16
    (born at age 0 in month 6; reaches the sale age 10 ten months later)."""
    a = toy()
    a.meta.horizon_months = 24
    res = run_simulation(a, with_break_even=False)
    assert all(row.births == 0.0 for row in res.months[:5])
    assert res.months[5].births > 0.0
    first_sale = next(row.month for row in res.months if row.sales_head > 0.0)
    assert first_sale == 16  # month 6 birth + 10 months to reach sale age 10


# ---------------------------------------------------------------------------
# 7. Feed identities
# ---------------------------------------------------------------------------
def test_feed_dm_conservation_identity() -> None:
    """As-fed quantities convert back to the purchased DM requirement."""
    a = SimulationAssumptions(meta=MetaAssumptions(horizon_months=12))
    a.feed.grazing_dm_fraction = 0.3
    # The identity is against base prices; the calibrated default now grows
    # feed prices 4%/yr, which this physical-to-cost check must not mix in.
    a.feed.annual_feed_price_growth_rate = 0.0
    res = run_simulation(a, with_break_even=False)
    feed = a.feed
    for row in res.months:
        dm_back = (
            row.feed_green_kg * feed.green_dm_pct
            + row.feed_dry_kg * feed.dry_dm_pct
            + row.feed_concentrate_kg * feed.concentrate_dm_pct
        )
        assert dm_back > 0.0
        assert row.feed_homegrown_green_kg + row.feed_purchased_green_kg == pytest.approx(
            row.feed_green_kg
        )
        # Each physical source is valued at its own as-fed price.
        assert row.feed_cost == pytest.approx(
            row.feed_homegrown_green_kg * feed.green_price_per_kg
            + row.feed_purchased_green_kg * feed.purchased_green_price_per_kg
            + row.feed_dry_kg * feed.dry_price_per_kg
            + row.feed_concentrate_kg * feed.concentrate_price_per_kg
        )


def test_full_grazing_means_zero_feed_cost() -> None:
    a = toy()
    a.feed.grazing_dm_fraction = 1.0
    res = run_simulation(a, with_break_even=False)
    for row in res.months:
        assert row.feed_cost == 0.0
        assert row.feed_green_kg == 0.0
        assert row.feed_dry_kg == 0.0
        assert row.feed_concentrate_kg == 0.0
    assert res.feed_summary.fodder_deficit_months == 0
    assert res.feed_summary.land_requirement_acres == 0.0


# ---------------------------------------------------------------------------
# 8. Degenerate and boundary configs
# ---------------------------------------------------------------------------
def test_zero_price_zero_cost_run_is_all_zero() -> None:
    a = SimulationAssumptions(
        herd=HerdAssumptions(does=0, bucks=0, max_breeding_does=0, auto_purchase_bucks=False)
    )
    a.costs.vet_per_animal_per_year = 0.0
    a.costs.labour_per_month = 0.0
    a.costs.insurance_pct_stock_value_annual = 0.0
    a.costs.misc_overhead_per_month = 0.0
    a.costs.shed_cost_per_animal_place = 0.0
    a.costs.equipment_cost_per_animal = 0.0
    a.finance.loan_fraction_of_project_cost = 0.0
    a.finance.working_capital_months = 0
    res = run_simulation(a, with_break_even=False)
    m = res.metrics
    assert m.project_cost == 0.0
    assert m.npv == 0.0
    assert m.bcr is None
    assert m.irr is None
    assert all(row.net_cash_flow == 0.0 for row in res.months)


def test_horizon_240_runs_finite() -> None:
    res = run_simulation(
        SimulationAssumptions(meta=MetaAssumptions(horizon_months=240)), with_break_even=False
    )
    assert len(res.months) == 240
    assert len(res.annual_pl) == 20
    assert math.isfinite(res.metrics.npv)
    assert math.isfinite(res.months[-1].total_herd)


def test_calendar_wraps_across_year_boundary() -> None:
    a = SimulationAssumptions(meta=MetaAssumptions(horizon_months=14, start_year_month="2026-12"))
    res = run_simulation(a, with_break_even=False)
    assert [row.calendar_month for row in res.months[:3]] == [12, 1, 2]
    assert res.months[12].calendar_month == 12  # month 13 wraps to December again


def test_eid_uplift_follows_calendar_not_simulation_month() -> None:
    """Start 2026-12, Eid in calendar month 1: the uplift hits simulation
    month 2, verified with a scheduled young-stock sale that month."""

    def revenue_with_eid(eid_month: int) -> float:
        a = toy(male_weaners=10)
        a.meta = MetaAssumptions(horizon_months=12, start_year_month="2026-12")
        a.sales.eid_month = eid_month
        a.sales.eid_price_uplift = 0.30
        a.events = [HerdEventAssumptions(month=2, kind="sale", animal_class="male_weaner", count=5)]
        return run_simulation(a, with_break_even=False).months[1].sales_revenue

    assert revenue_with_eid(1) / revenue_with_eid(0) == pytest.approx(1.30, abs=1e-9)


# ---------------------------------------------------------------------------
# 9. Monte Carlo / sensitivity robustness
# ---------------------------------------------------------------------------
def test_mc_litter_size_capped_at_schema_max() -> None:
    a = SimulationAssumptions()
    a.reproduction.litter_size = 3.9
    draws = dict.fromkeys(_DRAW_ORDER, 1.0)
    draws["litter_size"] = 2.0  # 3.9 x 2 = 7.8 without the cap
    variant = _apply_draws(a, draws)
    assert variant.reproduction.litter_size == 4.0


def test_mc_litter_size_clamped_at_schema_min() -> None:
    a = SimulationAssumptions()
    a.reproduction.litter_size = 0.5
    draws = dict.fromkeys(_DRAW_ORDER, 1.0)
    draws["litter_size"] = 0.85
    variant = _apply_draws(a, draws)
    assert variant.reproduction.litter_size == 0.5
    assert SimulationAssumptions.model_validate(variant.model_dump()) == variant


def test_disabled_conception_risk_preserves_schema_valid_base() -> None:
    a = SimulationAssumptions()
    a.reproduction.conception_rate = 1.0
    a.risk.conception_rate.enabled = False
    variant = _apply_draws(a, dict.fromkeys(_DRAW_ORDER, 1.0))
    assert variant.reproduction.conception_rate == 1.0


def test_mc_survives_extreme_spreads() -> None:
    a = SimulationAssumptions(meta=MetaAssumptions(horizon_months=24))
    a.risk.monte_carlo_runs = 10
    for var in (
        a.risk.meat_price,
        a.risk.feed_price,
        a.risk.adult_mortality,
        a.risk.kid_mortality,
        a.risk.litter_size,
        a.risk.conception_rate,
    ):
        var.low, var.high = 0.5, 100.0
    mc = run_monte_carlo(a)
    assert mc.runs == 10
    assert sum(mc.npv_histogram_counts) == 10
    for value in (mc.npv_mean, mc.npv_std, mc.npv_p5, mc.npv_p50, mc.npv_p95):
        assert math.isfinite(value)
    for month_idx in range(24):
        assert math.isfinite(mc.herd_percentiles.p50[month_idx])
        assert math.isfinite(mc.cash_percentiles.p95[month_idx])


def test_sensitivity_at_sale_age_bounds() -> None:
    for sale_age in (6, 24):
        a = SimulationAssumptions(meta=MetaAssumptions(horizon_months=24))
        a.growth.sale_age_months = sale_age
        items = run_sensitivity(a)
        assert len(items) == 9
        assert all(
            math.isfinite(i.delta_npv_low) and math.isfinite(i.delta_npv_high) for i in items
        )


# ---------------------------------------------------------------------------
# 10. API surface: the new financing guards are 422, not 500
# ---------------------------------------------------------------------------
async def test_api_rejects_negative_equity_financing(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await client.get("/api/simulation/defaults", headers=headers)
    assert resp.status_code == 200, resp.text
    assumptions = resp.json()
    assumptions["meta"]["horizon_months"] = 12
    assumptions["finance"]["loan_fraction_of_project_cost"] = 0.9
    assumptions["finance"]["subsidy_fraction"] = 0.2
    resp = await client.post(
        "/api/simulation/run", json={"assumptions": assumptions}, headers=headers
    )
    assert resp.status_code == 422, resp.text


async def test_api_rejects_moratorium_covering_term(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await client.get("/api/simulation/defaults", headers=headers)
    assert resp.status_code == 200, resp.text
    assumptions = resp.json()
    assumptions["meta"]["horizon_months"] = 12
    assumptions["finance"]["loan_term_months"] = 12
    assumptions["finance"]["moratorium_months"] = 12
    resp = await client.post(
        "/api/simulation/run", json={"assumptions": assumptions}, headers=headers
    )
    assert resp.status_code == 422, resp.text


async def test_api_accepts_boundary_financing(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await client.get("/api/simulation/defaults", headers=headers)
    assert resp.status_code == 200, resp.text
    assumptions = resp.json()
    assumptions["meta"]["horizon_months"] = 24
    # loan + subsidy == 1.0 (zero equity) and moratorium one short of the term.
    assumptions["finance"]["loan_fraction_of_project_cost"] = 0.75
    assumptions["finance"]["subsidy_fraction"] = 0.25
    assumptions["finance"]["loan_term_months"] = 12
    assumptions["finance"]["moratorium_months"] = 11
    resp = await client.post(
        "/api/simulation/run", json={"assumptions": assumptions}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    metrics = resp.json()["metrics"]
    assert metrics["equity"] == pytest.approx(0.0, abs=1e-6)


# ---------------------------------------------------------------------------
# 11. Cross-field guards (9-1/9-2): schema-valid crashes are now 422 / fine
# ---------------------------------------------------------------------------
async def test_api_rejects_max_doe_age_below_36(client: httpx.AsyncClient) -> None:
    """max_doe_age_months in [24, 35] used to ZeroDivisionError mid-run (500);
    the floor is now 36 (the engine spreads foundation does over
    24..min(60, max_doe_age-12), an empty range below 36)."""
    headers = await owner_with_farm(client)
    resp = await client.get("/api/simulation/defaults", headers=headers)
    assumptions = resp.json()
    assumptions["meta"]["horizon_months"] = 12
    for bad in (24, 30, 35):
        assumptions["culling"]["max_doe_age_months"] = bad
        resp = await client.post(
            "/api/simulation/run", json={"assumptions": assumptions}, headers=headers
        )
        assert resp.status_code == 422, bad
    # The floor itself validates and runs.
    assumptions["culling"]["max_doe_age_months"] = 36
    resp = await client.post(
        "/api/simulation/run", json={"assumptions": assumptions}, headers=headers
    )
    assert resp.status_code == 200, resp.text


async def test_api_accepts_afb_at_boundary_of_min_doe_age(client: httpx.AsyncClient) -> None:
    """The 9-2 IndexError pair (age_at_first_breeding > max_doe_age) is no
    longer constructible (afb <= 30 < 36 <= max_doe_age, plus a cross-field
    validator); the boundary pair validates and runs clean."""
    headers = await owner_with_farm(client)
    resp = await client.get("/api/simulation/defaults", headers=headers)
    assumptions = resp.json()
    assumptions["meta"]["horizon_months"] = 12
    assumptions["reproduction"]["age_at_first_breeding_months"] = 30
    assumptions["culling"]["max_doe_age_months"] = 36
    resp = await client.post(
        "/api/simulation/run", json={"assumptions": assumptions}, headers=headers
    )
    assert resp.status_code == 200, resp.text


class TestIrrRootIsolationRouting:
    """The exact-Decimal isolation must be reserved for whole-period series.

    ``Decimal.__pow__`` is exact integer exponentiation for an integral
    exponent but correctly-rounded exp/ln for a fractional one — ~70x dearer
    per term at 96 digits. Routing on term count alone sent the *shortest*
    legal horizons (13-24 monthly terms) into the expensive branch and every
    longer one into the cheap scan, so horizon 23 cost 2.5s against horizon
    24's 0.06s while the API priced requests as proportional to the horizon.
    """

    @staticmethod
    def _monthly_terms(count: int) -> list[tuple[float, float]]:
        flows = [-500_000.0] + [(-1.0) ** index * 40_000.0 for index in range(count - 1)]
        return [(index / 12.0, flow) for index, flow in enumerate(flows)]

    def test_monthly_series_never_enters_the_decimal_isolation(self) -> None:
        terms = self._monthly_terms(24)
        normalised = finance._normalise_power_terms(terms)
        assert finance._sign_variations(normalised) > 1, "must reach the multi-root branch"
        assert not finance._has_integral_exponents(normalised)

        calls = 0
        original = finance._decimal_power_sum

        def counting(*args: object, **kwargs: object) -> object:
            nonlocal calls
            calls += 1
            return original(*args, **kwargs)

        finance._decimal_power_sum = counting  # type: ignore[assignment]
        try:
            roots = finance._positive_power_roots(normalised, 1.0 / 11.0, 1.0 / 0.01)
        finally:
            finance._decimal_power_sum = original  # type: ignore[assignment]

        assert calls == 0, "a sub-annual series must not pay for 96-digit Decimal work"
        # ...and the cheap path still finds what the exact one did.
        assert roots == finance._scanned_power_roots(normalised, 1.0 / 11.0, 1.0 / 0.01)

    def test_annual_series_keeps_exact_isolation_and_its_multiple_roots(self) -> None:
        # The series irr()'s docstring documents: three genuine crossings, so
        # irr() must report None rather than pick one.
        flows = [
            -954_244.0,
            -390_293.0,
            -528_292.0,
            1_930_642.0,
            1_572_511.0,
            -238_266.0,
            -51_854.0,
            -865_625.0,
            94_536.0,
        ]
        times = [float(index) for index in range(len(flows))]
        normalised = finance._normalise_power_terms(
            [(time, flow) for flow, time in zip(flows, times, strict=True)]
        )
        assert finance._has_integral_exponents(normalised)

        roots = finance.irr_roots(flows, times)
        assert len(roots) == 3
        assert roots == pytest.approx([-0.8916, -0.2645, 0.1633], abs=1e-3)
        assert finance.irr(flows, times) is None

    def test_short_and_long_horizons_agree_across_the_old_routing_boundary(self) -> None:
        # The 24-term cap used to split these two; horizons on either side of
        # the old boundary must stay single-rooted and nearly identical (the
        # economics barely move month-to-month there). Full monotonicity in
        # horizon is no longer asserted: the calibrated cash-flow shape has a
        # genuine NPV cliff when the terminal value's Bakrid timing moves off
        # the final month (horizon 22 -> 23), which is economics, not routing.
        results = {}
        for horizon in (22, 23, 24, 25):
            assumptions = get_preset("osmanabadi", "stall_fed").model_copy(deep=True)
            assumptions.meta.horizon_months = horizon
            results[horizon] = run_simulation(assumptions, with_break_even=False).metrics.irr
        assert all(value is not None for value in results.values())
        assert abs(results[23] - results[24]) < 0.01, (
            "the old 24-term routing boundary must not move the IRR"
        )
        # Pin the CAUSE of the horizon-22 cliff so nobody re-asserts blind
        # monotonicity: the default preset's Bakrid months include 22, so a
        # 22-month run liquidates the herd at festival-inflated stock value
        # while a 23-month run cannot. The jump is economics, not the solver:
        # strip the festival months and the 22/23 pair sits close together.
        preset = get_preset("osmanabadi", "stall_fed")
        assert 22 in preset.sales.festival_sale_months

        def irr_without_festivals(horizon: int) -> float | None:
            variant = preset.model_copy(deep=True)
            variant.meta.horizon_months = horizon
            variant.sales.festival_sale_months = []
            return run_simulation(variant, with_break_even=False).metrics.irr

        smooth_pair = [irr_without_festivals(h) for h in (22, 23)]
        assert all(value is not None for value in smooth_pair)
        assert abs(smooth_pair[22 - 22] - smooth_pair[23 - 22]) < 0.05, (
            "without the Bakrid terminal-timing effect the IRR curve is smooth"
        )


class TestScheduledSaleUsesRealPoolWeight:
    """Young-stock event sales are priced at live weight, per the module docstring.

    ``_draw`` removes head proportionally across every age slot, so the draw's
    mean weight IS the pool's mean weight. Pricing it at one hard-coded
    mid-class age was only correct for freshly placed stock; a pool filled by
    promotions can sit anywhere in its class, and a grower chain spans up to 24
    monthly slots.
    """

    def test_pool_average_tracks_the_actual_age_distribution(self) -> None:
        assumptions = get_preset("osmanabadi", "stall_fed")
        growth = assumptions.growth
        doe_weight = growth.adult_weight_doe_kg
        first_breeding = assumptions.reproduction.age_at_first_breeding_months
        slots = first_breeding - 6
        mid_age = (6 + first_breeding - 1) // 2

        # Every head parked in the OLDEST slot must price above the mid age...
        oldest = [0.0] * slots
        oldest[-1] = 10.0
        assert _pool_avg_weight(oldest, 6, growth, doe_weight, mid_age) == pytest.approx(
            weight_at_age(6 + slots - 1, growth, doe_weight)
        )

        # ...and in the youngest slot, below it.
        youngest = [0.0] * slots
        youngest[0] = 10.0
        assert _pool_avg_weight(youngest, 6, growth, doe_weight, mid_age) == pytest.approx(
            weight_at_age(6, growth, doe_weight)
        )

        # A uniform pool is exactly the mean of its slot weights.
        uniform = [1.0] * slots
        expected = sum(weight_at_age(6 + i, growth, doe_weight) for i in range(slots)) / slots
        assert _pool_avg_weight(uniform, 6, growth, doe_weight, mid_age) == pytest.approx(expected)

        # An empty pool has no composition; keep the placement age.
        assert _pool_avg_weight([0.0] * slots, 6, growth, doe_weight, mid_age) == pytest.approx(
            weight_at_age(mid_age, growth, doe_weight)
        )

    def test_event_sale_price_follows_the_pool_it_draws_from(self) -> None:
        """End to end: the event's own quoted price moves with the pool's age mix.

        The monthly row mixes routine sale-age sales into sales_revenue, so
        the event's contribution is read from the note it logs.
        """

        def event_price(sale_month: int) -> float:
            assumptions = get_preset("osmanabadi", "stall_fed").model_copy(deep=True)
            assumptions.meta.horizon_months = 36
            # Hold the meat price flat so composition is the only variable.
            assumptions.sales.annual_livestock_price_growth_rate = 0.0
            assumptions.events = [
                HerdEventAssumptions(
                    month=sale_month, kind="sale", animal_class="male_grower", count=5
                )
            ]
            result = run_simulation(assumptions, with_break_even=False)
            note = result.months[sale_month - 1].events[0]
            match = re.search(r"₹([\d,]+)/head", note)
            assert match, note
            return float(match.group(1).replace(",", ""))

        # Month 9's grower chain still holds the young foundation cohort;
        # by month 21 it has filled out with older promoted animals. Pricing
        # every draw at one fixed mid-class age made these identical.
        early = event_price(9)
        mature = event_price(21)
        assert mature > early * 1.05, f"per-head {mature} should clearly exceed {early}"
