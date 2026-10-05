"""Independent accounting conservation controls for capitalized herd dispositions."""

from __future__ import annotations

import pytest

from app.simulation.assumptions import HerdEventAssumptions, SimulationAssumptions
from app.simulation.engine import _run_core


def _assumptions(horizon: int = 12) -> SimulationAssumptions:
    a = SimulationAssumptions()
    a.meta.horizon_months = horizon
    a.herd.does = a.herd.bucks = 0
    a.herd.auto_purchase_bucks = False
    a.costs.shed_cost_per_animal_place = a.costs.equipment_cost_per_animal = 0
    a.costs.misc_overhead_per_month = a.costs.vet_per_animal_per_year = 0
    a.costs.insurance_pct_stock_value_annual = 0
    a.costs.family_labour = True
    a.feed.green_price_per_kg = a.feed.purchased_green_price_per_kg = 0
    a.feed.dry_price_per_kg = a.feed.concentrate_price_per_kg = 0
    a.finance.loan_fraction_of_project_cost = 0
    a.finance.working_capital_months = 0
    a.finance.income_tax_rate = 0.3
    a.finance.terminal_livestock_realization_fraction = 1
    a.sales.transport_cost_per_head = a.sales.selling_cost_fraction = 0
    a.sales.manure_income_per_adult_per_year = a.sales.milk_sale_litres_per_doe_day = 0
    a.sales.annual_livestock_price_growth_rate = 0
    a.mortality.adult = 0
    a.culling.doe_cull_rate_annual = 0
    a.culling.max_doe_age_months = 180
    a.reproduction.max_services_before_cull = 0
    return a


def _event(
    month: int, kind: str, animal_class: str, count: float, price: float
) -> HerdEventAssumptions:
    return HerdEventAssumptions.model_validate(
        {
            "month": month,
            "kind": kind,
            "animal_class": animal_class,
            "count": count,
            "price_per_head": price,
        }
    )


@pytest.mark.parametrize("animal_class", ["doe", "buck"])
@pytest.mark.parametrize("sale_price", [80000, 100000, 120000])
@pytest.mark.parametrize("tax_rate", [0.0, 0.3])
@pytest.mark.parametrize("sale_month", [1, 7])
def test_disposal_at_loss_cost_or_gain_removes_book_and_never_double_charges_cash(
    animal_class: str,
    sale_price: int,
    tax_rate: float,
    sale_month: int,
) -> None:
    a = _assumptions()
    a.finance.income_tax_rate = tax_rate
    a.events = [
        _event(1, "purchase", animal_class, 1, 100000),
        _event(sale_month, "sale", animal_class, 1, sale_price),
    ]
    result = _run_core(SimulationAssumptions.model_validate(a.model_dump()))
    expected_depreciation = 100000 * (sale_month - 1) / 60
    expected_disposal = 100000 - expected_depreciation
    assert sum(m.depreciation for m in result.months) == pytest.approx(expected_depreciation)
    assert sum(m.breeding_stock_disposal_cost for m in result.months) == pytest.approx(
        expected_disposal
    )
    assert all(m.depreciation == 0 for m in result.months[sale_month - 1 :])
    assert result.months[-1].total_herd == pytest.approx(0)
    assert result.terminal_value_breakdown.breeding_stock == pytest.approx(0)
    assert result.terminal_value_breakdown.livestock == pytest.approx(0)
    annual = result.annual_pl[0]
    assert annual.ebitda == pytest.approx(sale_price)
    assert annual.ebit == pytest.approx(sale_price - 100000)
    assert annual.profit_before_tax == pytest.approx(sale_price - 100000)
    assert annual.tax == pytest.approx(max(0, sale_price - 100000) * tax_rate)
    assert annual.net_cash_flow == pytest.approx(sale_price - 100000 - annual.tax)
    assert annual.ebit == pytest.approx(
        annual.ebitda - annual.depreciation - annual.breeding_stock_disposal_cost
    )


def test_partial_disposal_allocates_each_purchase_vintage_and_excludes_other_sex() -> None:
    a = _assumptions()
    a.events = [
        _event(1, "purchase", "doe", 2, 60000),
        _event(7, "purchase", "doe", 2, 120000),
        _event(7, "purchase", "buck", 1, 30000),
        _event(7, "sale", "doe", 2, 100000),
    ]
    result = _run_core(SimulationAssumptions.model_validate(a.model_dump()))
    # Proportional cohort draw removes half of each doe vintage, no buck basis.
    assert result.months[6].breeding_stock_disposal_cost == pytest.approx(60000 * 0.9 + 120000)
    expected_dep = 120000 * 6 / 60 + 60000 * 6 / 60 + 120000 * 6 / 60 + 30000 * 6 / 60
    expected_book = 60000 * 0.8 + 120000 * 0.9 + 30000 * 0.9
    assert sum(m.depreciation for m in result.months) == pytest.approx(expected_dep)
    assert result.terminal_value_breakdown.breeding_stock == pytest.approx(expected_book)
    assert expected_dep + expected_book + sum(
        m.breeding_stock_disposal_cost for m in result.months
    ) == pytest.approx(390000)


@pytest.mark.parametrize("animal_class", ["doe", "buck"])
def test_mortality_writeoffs_conserve_acquisition_cost(animal_class: str) -> None:
    a = _assumptions(24)
    a.mortality.adult = 0.6
    a.events = [_event(1, "purchase", animal_class, 10, 10000)]
    result = _run_core(SimulationAssumptions.model_validate(a.model_dump()))
    survival = (1 - 0.6) ** (1 / 12)
    # First-month deaths have no prior accumulated depreciation.
    assert result.months[0].breeding_stock_disposal_cost == pytest.approx(100000 * (1 - survival))
    assert result.months[0].depreciation == pytest.approx(100000 * survival / 60)
    assert result.terminal_value_breakdown.breeding_stock == pytest.approx(
        100000 * (0.4**2) * (1 - 24 / 60)
    )
    assert (
        sum(m.depreciation + m.breeding_stock_disposal_cost for m in result.months)
        + result.terminal_value_breakdown.breeding_stock
    ) == pytest.approx(100000)
    assert sum(m.tax for m in result.months) == 0


def test_max_age_cull_removes_expired_vintage_before_new_purchase() -> None:
    a = _assumptions(24)
    a.culling.max_doe_age_months = 36
    a.herd.foundation_doe_age_min_months = a.herd.foundation_doe_age_max_months = 24
    a.events = [_event(1, "purchase", "doe", 1, 60000), _event(13, "purchase", "doe", 1, 120000)]
    result = _run_core(SimulationAssumptions.model_validate(a.model_dump()))
    # First purchase exits at age 37 in month 13; new purchase is age 25.
    assert result.months[12].breeding_stock_disposal_cost == pytest.approx(48000)
    assert result.months[12].total_herd == pytest.approx(1)
    assert result.months[12].depreciation == pytest.approx(120000 / 60)
    assert result.terminal_value_breakdown.breeding_stock == pytest.approx(120000 * 0.8)


def test_rate_cull_full_disposal_stops_depreciation() -> None:
    a = _assumptions(24)
    a.culling.doe_cull_rate_annual = 1
    a.events = [_event(1, "purchase", "doe", 2, 60000)]
    result = _run_core(SimulationAssumptions.model_validate(a.model_dump()))
    assert result.months[12].breeding_stock_disposal_cost == pytest.approx(96000)
    assert all(m.depreciation == 0 for m in result.months[12:])
    assert result.terminal_value_breakdown.breeding_stock == 0


def test_buck_rotation_preserves_same_month_purchase_and_removes_old_book() -> None:
    a = _assumptions(12)
    a.herd.auto_purchase_bucks = True
    a.culling.buck_rotation_years = 1
    a.events = [_event(1, "purchase", "buck", 1, 60000), _event(12, "purchase", "buck", 1, 120000)]
    result = _run_core(SimulationAssumptions.model_validate(a.model_dump()))
    assert result.months[-1].bucks == 1
    assert result.months[-1].breeding_stock_disposal_cost == pytest.approx(49000)
    assert result.months[-1].depreciation == pytest.approx(2000)
    assert result.terminal_value_breakdown.breeding_stock == pytest.approx(118000)


def test_fully_depreciated_disposal_has_no_second_book_charge() -> None:
    a = _assumptions()
    a.costs.breeding_stock_useful_life_months = 1
    a.events = [_event(1, "purchase", "buck", 1, 60000), _event(2, "sale", "buck", 1, 60000)]
    result = _run_core(SimulationAssumptions.model_validate(a.model_dump()))
    assert sum(m.depreciation for m in result.months) == 60000
    assert sum(m.breeding_stock_disposal_cost for m in result.months) == 0
    assert sum(m.tax for m in result.months) == 0


def test_repeat_breeder_cull_derecognizes_only_removed_doe_cost() -> None:
    a = _assumptions()
    a.herd.bucks = 1
    a.herd.purchased_doe_settling_months = 0
    a.reproduction.max_services_before_cull = 1
    a.reproduction.conception_rate = 0
    a.events = [_event(1, "purchase", "doe", 1, 60000)]
    result = _run_core(SimulationAssumptions.model_validate(a.model_dump()))
    assert result.months[0].culls_head == pytest.approx(1)
    assert result.months[0].breeding_stock_disposal_cost == pytest.approx(60000)
    assert sum(m.depreciation for m in result.months) == 0
    assert result.terminal_value_breakdown.breeding_stock == 0


def test_foundation_stock_and_homebred_replacements_have_no_in_run_purchase_basis() -> None:
    a = _assumptions()
    a.herd.does = 1
    a.events = [_event(1, "sale", "doe", 1, 10000), _event(1, "purchase", "buck", 1, 12000)]
    result = _run_core(SimulationAssumptions.model_validate(a.model_dump()))
    assert sum(m.breeding_stock_disposal_cost for m in result.months) == 0
    assert sum(m.depreciation for m in result.months) == pytest.approx(12000 * 12 / 60)
    assert result.terminal_value_breakdown.breeding_stock == pytest.approx(12000 * 0.8)


def test_non_cash_disposal_does_not_reduce_cash_dscr_twice() -> None:
    a = _assumptions()
    a.herd.does = 1
    a.herd.foundation_flock_state = "open"
    a.finance.loan_fraction_of_project_cost = 0.5
    a.finance.loan_term_months = 12
    a.finance.moratorium_months = 0
    a.finance.interest_rate_annual = 0
    a.events = [_event(1, "purchase", "buck", 1, 100000), _event(1, "sale", "buck", 1, 120000)]
    result = _run_core(SimulationAssumptions.model_validate(a.model_dump()))
    annual = result.annual_pl[0]
    assert annual.profit_before_tax == pytest.approx(20000)
    assert annual.tax == pytest.approx(6000)
    assert annual.breeding_stock_disposal_cost == pytest.approx(100000)
    assert result.dscr_per_year[0] == pytest.approx((120000 - 6000) / annual.debt_service)


def test_rotation_after_partial_ordered_sale_keeps_only_surviving_new_bucks() -> None:
    a = _assumptions()
    a.herd.auto_purchase_bucks = True
    a.culling.buck_rotation_years = 1
    a.events = [
        _event(1, "purchase", "buck", 1, 60000),
        _event(12, "purchase", "buck", 1, 120000),
        _event(12, "sale", "buck", 1, 100000),
    ]
    result = _run_core(SimulationAssumptions.model_validate(a.model_dump()))
    final = result.months[-1]
    # The proportional ordered sale leaves half an old and half a new buck;
    # rotation then culls only the old half, rather than sheltering it behind
    # the unreduced count of this month's original purchase.
    assert final.bucks == pytest.approx(0.5)
    assert final.culls_head == pytest.approx(1.5)
    assert final.breeding_stock_disposal_cost == pytest.approx(109000)
    assert final.depreciation == pytest.approx(1000)
    assert result.terminal_value_breakdown.breeding_stock == pytest.approx(59000)
