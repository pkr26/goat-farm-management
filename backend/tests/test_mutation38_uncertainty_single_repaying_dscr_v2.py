"""Real debt repayment makes Monte Carlo coverage risk measurable."""

import json

import pytest

from app.simulation.assumptions import SimulationAssumptions
from app.simulation.montecarlo import run_monte_carlo


@pytest.mark.parametrize("loan_fraction", [0.0, 0.5])
def test_actual_unproductive_investment_distinguishes_no_debt_from_repaying_debt(
    loan_fraction: float,
) -> None:
    assumptions = SimulationAssumptions.model_validate_json(
        json.dumps(
            {
                "meta": {"horizon_months": 12, "start_year_month": "2051-01"},
                "herd": {"does": 0, "bucks": 0, "auto_purchase_bucks": False},
                "finance": {
                    "loan_fraction_of_project_cost": loan_fraction,
                    "initial_stock_cost": 1000.0,
                    "loan_term_months": 6,
                    "moratorium_months": 0,
                },
                "risk": {"monte_carlo_runs": 1, "seed": 17},
            }
        )
    )
    result = run_monte_carlo(assumptions)
    assert result.prob_npv_negative == 1.0
    if loan_fraction == 0.0:
        assert result.prob_dscr_below_one is None
    else:
        # Real principal payments have no operating earnings to cover them.
        # The sole real run is measurable and breaches one, so the reported
        # probability must be exactly1, never a ratio larger than one.
        assert result.prob_dscr_below_one == 1.0
