"""The exported deterministic native run computes optional analyses only on request."""

import json

import pytest

from app.simulation.assumptions import SimulationAssumptions
from app.simulation.engine import run_simulation


def test_native_deterministic_run_omits_unrequested_optional_analyses() -> None:
    assumptions = SimulationAssumptions.model_validate_json(
        json.dumps(
            {
                "meta": {"horizon_months": 12},
                "herd": {"does": 0, "bucks": 0, "auto_purchase_bucks": False},
            }
        )
    )
    try:
        default = run_simulation(assumptions, with_break_even=False)
        disabled = run_simulation(
            assumptions,
            with_break_even=False,
            with_monte_carlo=False,
            with_sensitivity=False,
            with_optimization=False,
        )
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as error:
        pytest.fail(f"A valid deterministic run must complete without optional analyses: {error}")
    assert default.monte_carlo is None
    assert default.sensitivity is None
    assert default.optimization is None
    assert default.model_dump(mode="json") == disabled.model_dump(mode="json")
