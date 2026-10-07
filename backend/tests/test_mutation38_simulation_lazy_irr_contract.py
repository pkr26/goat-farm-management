"""Actual forecast cash returns distinguish a unique IRR from no return root."""

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


@pytest.mark.parametrize(
    "earns_cash", [False, True], ids=["one-negative-investment", "exact-unlevered-breakeven"]
)
def test_native_forecast_lazy_return_preserves_its_actual_cashflow_disposition(
    earns_cash: bool,
) -> None:
    payload = _payload()
    payload["finance"].update({"include_terminal_value": False, "working_capital_months": 0})
    if earns_cash:
        payload["herd"]["bucks"] = 1
        payload["finance"]["initial_stock_cost"] = 1200.0
        payload["sales"]["manure_income_per_adult_per_year"] = 1200.0
    else:
        payload["costs"].update(
            {
                "capacity_basis": "planned",
                "planned_capacity_head": 1,
                "shed_cost_per_animal_place": 1200.0,
            }
        )
    core = _forecast(payload)
    assert core.equity == 1200.0
    try:
        assessment = core.irr_assessment
        actual_return = core.irr
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as error:
        pytest.fail(
            f"A supported cashflow disposition must yield its optional native return: {error}"
        )
    if earns_cash:
        assert all(row.net_cash_flow == 100.0 for row in core.months)
        assert assessment.status == "unique"
        assert len(assessment.roots) == 1
        assert actual_return == pytest.approx(0.0, abs=1e-10)
    else:
        assert all(row.net_cash_flow == 0.0 for row in core.months)
        assert assessment.status == "no_root"
        assert assessment.roots == ()
        assert actual_return is None
