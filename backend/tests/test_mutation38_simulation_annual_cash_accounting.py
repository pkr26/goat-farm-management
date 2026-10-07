"""Actual long-horizon annual and trading/subsidy ledgers conserve their monthly cash."""

import json
from typing import Any

import pytest

from app.simulation.assumptions import SimulationAssumptions
from app.simulation.engine import _CoreResult, _run_core


def _payload() -> dict[str, Any]:
    return {
        "meta": {"horizon_months": 12},
        "herd": {
            "does": 0,
            "bucks": 0,
            "auto_purchase_bucks": False,
            "foundation_flock_state": "open",
        },
        "reproduction": {"conception_rate": 0.0},
        "mortality": {"kid_pre_weaning": 0.0, "kid_post_weaning": 0.0, "grower": 0.0, "adult": 0.0},
        "culling": {"doe_cull_rate_annual": 0.0, "buck_rotation_years": 10},
        "sales": {
            "festival_sale_months": [],
            "annual_livestock_price_growth_rate": 0.0,
            "manure_income_per_adult_per_year": 0.0,
            "selling_cost_fraction": 0.0,
            "transport_cost_per_head": 0.0,
        },
        "feed": {
            "green_price_per_kg": 0.0,
            "purchased_green_price_per_kg": 0.0,
            "dry_price_per_kg": 0.0,
            "concentrate_price_per_kg": 0.0,
            "cultivated_fodder_acres": 0.0,
        },
        "costs": {
            "vet_per_animal_per_year": 0.0,
            "labour_per_month": 0.0,
            "insurance_pct_stock_value_annual": 0.0,
            "misc_overhead_per_month": 0.0,
            "operating_cost_growth_rate_annual": 0.0,
            "shed_cost_per_animal_place": 0.0,
            "equipment_cost_per_animal": 0.0,
        },
        "finance": {
            "loan_fraction_of_project_cost": 0.0,
            "terminal_livestock_realization_fraction": 1.0,
        },
    }


def _forecast(payload: dict[str, Any]) -> _CoreResult:
    assumptions = SimulationAssumptions.model_validate_json(json.dumps(payload))
    try:
        core = _run_core(assumptions)
        json.dumps([row.model_dump(mode="json") for row in core.months], allow_nan=False)
        json.dumps(core.terminal_value_breakdown.model_dump(mode="json"), allow_nan=False)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as error:
        pytest.fail(
            f"A valid financial forecast must complete with conserved cash and assets: {error}"
        )
    assert len(core.months) == payload["meta"]["horizon_months"]
    assert all(row.births == row.deaths == 0.0 for row in core.months)
    return core


def test_a_twelve_year_forecast_labels_every_annual_period_in_sequence() -> None:
    payload = _payload()
    payload["meta"]["horizon_months"] = 144
    payload["herd"]["bucks"] = 1
    payload["sales"]["manure_income_per_adult_per_year"] = 1200.0
    payload["finance"].update({"include_terminal_value": False, "working_capital_months": 0})
    core = _forecast(payload)
    assert len(core.annual_pl) == 12
    assert [row.year for row in core.annual_pl] == list(range(1, 13))
    for index, annual in enumerate(core.annual_pl):
        monthly = core.months[index * 12 : (index + 1) * 12]
        assert annual.manure_revenue == pytest.approx(sum(row.manure_revenue for row in monthly))
        assert annual.net_cash_flow == pytest.approx(sum(row.net_cash_flow for row in monthly))


def test_a_real_trading_purchase_is_expensed_once_in_annual_operating_profit_and_cash() -> None:
    payload = _payload()
    payload["events"] = [
        {
            "month": 1,
            "kind": "purchase",
            "animal_class": "male_kid",
            "count": 1.0,
            "price_per_head": 333.0,
        }
    ]
    payload["finance"].update({"include_terminal_value": False, "working_capital_months": 0})
    core = _forecast(payload)
    annual = core.annual_pl[0]
    assert sum(row.purchase_cost for row in core.months) == 333.0
    assert annual.stock_purchases == annual.total_opex == 333.0
    assert annual.breeding_stock_capex == annual.depreciation == 0.0
    assert annual.ebitda == pytest.approx(annual.total_revenue - 333.0)
    assert annual.net_cash_flow == pytest.approx(annual.total_revenue - 333.0)
    assert sum(row.net_cash_flow for row in core.months) == pytest.approx(annual.net_cash_flow)


def test_approved_dated_grant_is_a_cash_benefit_while_remaining_outside_operating_income() -> None:
    payload = _payload()
    payload["costs"].update(
        {
            "capacity_basis": "planned",
            "planned_capacity_head": 1,
            "shed_cost_per_animal_place": 1200.0,
        }
    )
    payload["finance"].update(
        {
            "include_terminal_value": False,
            "working_capital_months": 0,
            "discount_rate_annual": 0.0,
            "nlm_subsidy": True,
            "nlm_unit_females": 100,
            "nlm_unit_males": 5,
            "nlm_eligible_capital_cost": 2.0,
            "nlm_approved_subsidy_amount": 1.0,
            "nlm_subsidy_receipts": [{"month": 1, "amount": 0.5}, {"month": 12, "amount": 0.5}],
        }
    )
    core = _forecast(payload)
    assert core.equity == 1200.0
    assert core.annual_pl[0].total_revenue == 0.0
    assert sum(row.subsidy_receipt for row in core.months) == 1.0
    assert core.bcr == pytest.approx(1.0 / 1200.0)
    assert core.npv == -1199.0
