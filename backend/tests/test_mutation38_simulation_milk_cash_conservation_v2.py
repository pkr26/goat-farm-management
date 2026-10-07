"""Real surplus-milk receipts remain positive cash in monthly and annual accounts."""

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
    return core


def test_real_surplus_milk_receipts_conserve_monthly_and_annual_operating_cash() -> None:
    payload = _payload()
    payload["herd"].update(
        {"does": 9, "foundation_flock_state": "mixed", "doe_purchase_price": 0.0}
    )
    payload["sales"].update({"milk_sale_litres_per_doe_day": 1.0, "milk_price_per_litre": 30.0})
    payload["finance"].update(
        {"include_terminal_value": False, "working_capital_months": 0, "income_tax_rate": 0.0}
    )
    core = _forecast(payload)
    assert core.months[0].milk_revenue > 0.0
    for row in core.months:
        receipts = row.sales_revenue + row.cull_revenue + row.milk_revenue + row.manure_revenue
        expenses = (
            row.feed_cost
            + row.vet_cost
            + row.labour_cost
            + row.insurance_cost
            + row.misc_cost
            + row.selling_cost
            + row.purchase_cost
            + row.breeding_stock_capex
        )
        assert expenses == row.tax == row.debt_service == row.terminal_value == 0.0
        assert row.net_cash_flow == pytest.approx(receipts)
        assert row.net_cash_flow >= row.milk_revenue >= 0.0
    annual = core.annual_pl[0]
    assert annual.milk_revenue == pytest.approx(sum(row.milk_revenue for row in core.months))
    assert annual.total_revenue == pytest.approx(sum(row.net_cash_flow for row in core.months))
