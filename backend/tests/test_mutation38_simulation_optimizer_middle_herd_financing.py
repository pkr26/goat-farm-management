"""The default middle herd size remains a decision across actual alternative financing policies."""

import json

import pytest

from app.simulation import optimization
from app.simulation.assumptions import SimulationAssumptions
from app.simulation.engine import _CoreResult, _run_core


def test_omitted_herd_size_axis_keeps_middle_size_for_each_financing_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assumptions = SimulationAssumptions.model_validate_json(
        json.dumps(
            {
                "meta": {"horizon_months": 12},
                "herd": {"does": 12, "bucks": 0, "auto_purchase_bucks": False},
                "finance": {"loan_fraction_of_project_cost": 0.85},
                "optimization": {
                    "max_candidates": 30,
                    "doe_scale_low": 0.75,
                    "doe_scale_high": 1.25,
                    "sale_age_radius_months": 0,
                    "retention_step": 0.0,
                    "loan_fraction_step": 0.15,
                    "festival_hold_radius_months": 0,
                    "service_cull_radius_months": 0,
                },
            }
        )
    )
    observed: set[tuple[int, float]] = set()

    def observe(a: SimulationAssumptions) -> _CoreResult:
        core = _run_core(a)
        observed.add((a.herd.does, a.finance.loan_fraction_of_project_cost))
        return core

    monkeypatch.setattr(optimization, "_run_core", observe)
    try:
        result = optimization.run_optimization(assumptions)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as error:
        pytest.fail(f"A valid middle-size and financing decision search must complete: {error}")
    assert {head for head, _ in observed} == {9, 12, 15}
    assert sorted(loan for head, loan in observed if head == 12) == pytest.approx([0.7, 0.85, 1.0])
    assert result.evaluated_candidates == len(observed) == 9
