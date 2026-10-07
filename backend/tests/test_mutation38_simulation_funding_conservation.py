"""An ordinary fully funded budget cannot pay a negative promoter contribution."""

import json

import pytest

from app.simulation.assumptions import SimulationAssumptions
from app.simulation.engine import _run_core


def test_upfront_loan_and_subsidy_preserve_nonnegative_actual_promoter_equity() -> None:
    assumptions = SimulationAssumptions.model_validate_json(
        json.dumps(
            {
                "meta": {"horizon_months": 12},
                "herd": {"does": 0, "bucks": 0, "auto_purchase_bucks": False},
                "costs": {
                    "capacity_basis": "planned",
                    "planned_capacity_head": 1,
                    "shed_cost_per_animal_place": 1234.0,
                    "equipment_cost_per_animal": 0.0,
                    "misc_overhead_per_month": 0.0,
                },
                "finance": {
                    "loan_fraction_of_project_cost": 0.8,
                    "subsidy_fraction": 0.2,
                    "working_capital_months": 0,
                    "interest_rate_annual": 0.0,
                    "loan_term_months": 12,
                    "moratorium_months": 0,
                    "include_terminal_value": False,
                },
            }
        )
    )
    try:
        core = _run_core(assumptions)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as error:
        pytest.fail(f"An ordinary fully funded facility budget must conserve real funds: {error}")
    assert core.project_cost == 1234.0
    assert core.loan_amount > 0.0 and core.subsidy_amount > 0.0
    assert core.equity >= 0.0, (
        "Full loan/subsidy funding must not pay a negative promoter contribution"
    )
    assert core.subsidy_amount <= core.project_cost - core.loan_amount
    assert core.loan_amount + core.subsidy_amount + core.equity == pytest.approx(core.project_cost)
    assert sum(row.debt_service for row in core.months) == pytest.approx(core.loan_amount)
