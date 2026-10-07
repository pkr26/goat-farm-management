"""Actual bounded optimizer helpers must complete valid numeric decisions."""

from collections.abc import Callable

import pytest

from app.simulation import optimization
from app.simulation.assumptions import SimulationAssumptions
from app.simulation.engine import _run_core


def _completed[T](operation: Callable[[], T]) -> T:
    try:
        return operation()
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as error:
        pytest.fail(f"A supported bounded decision must complete without a runtime error: {error}")


@pytest.mark.parametrize(
    "low,high,steps,expected",
    [
        (0.75, 1.25, 1, [0.75]),
        (0.75, 1.25, 2, [0.75, 1.25]),
        (0.75, 1.25, 3, [0.75, 1.0, 1.25]),
        (1.0, 1.0, 1, [1.0]),
    ],
    ids=["pinned-lower-size", "both-size-endpoints", "small-middle-large", "equal-size-endpoints"],
)
def test_valid_herd_size_axis_completes(
    low: float, high: float, steps: int, expected: list[float]
) -> None:
    assert _completed(lambda: optimization._linspace(low, high, steps)) == pytest.approx(expected)


@pytest.mark.parametrize(
    "values,expected",
    [
        ([0.3, 1.0 - 0.7], [0.3]),
        ([0.1, 0.2, 0.4], [0.1, 0.2, 0.4]),
        ([-1.0, 0.3, 2.0], [0.0, 0.3, 1.0]),
    ],
    ids=[
        "same-financing-policy-rounded-differently",
        "distinct-retention-decisions",
        "bounded-decisions",
    ],
)
def test_bounded_financing_decisions_complete_and_keep_unique_policies(
    values: list[float], expected: list[float]
) -> None:
    assert _completed(lambda: optimization._unique_bounded(values, 0.0, 1.0)) == pytest.approx(
        expected
    )


@pytest.mark.parametrize(
    "limit,expected",
    [(0, []), (1, [5]), (2, [0, 9]), (3, [0, 4, 9]), (10, list(range(10))), (20, list(range(10)))],
    ids=[
        "disabled-axis-budget",
        "one-middle-value",
        "both-endpoints",
        "evenly-spaced-decisions",
        "exact-axis-budget",
        "spare-axis-budget",
    ],
)
def test_sampling_a_valid_decision_axis_completes(limit: int, expected: list[int]) -> None:
    sampled = _completed(lambda: optimization._sample_evenly(list(range(10)), limit))
    assert sampled == expected
    assert len(sampled) <= max(0, limit)


def test_actual_equity_funded_baseline_has_no_debt_constraint_or_missing_ceiling_error() -> None:
    assumptions = SimulationAssumptions.model_validate_json(
        '{"meta":{"horizon_months":12},"herd":{"does":0,"bucks":1,"auto_purchase_bucks":false},"finance":{"loan_fraction_of_project_cost":0},"optimization":{"maximum_project_cost":null,"maximum_funding_gap":null,"minimum_dscr":10}}'
    )
    core = _completed(lambda: _run_core(assumptions))
    assert core.avg_dscr is None
    candidate = _completed(lambda: optimization._candidate_from_core(assumptions, core))
    assert candidate.project_cost == core.project_cost
    assert candidate.funding_gap == core.additional_working_capital_required
    assert not any(
        "DSCR" in violation or "configured maximum" in violation
        for violation in candidate.constraint_violations
    )
    assert isinstance(candidate.feasible, bool)
