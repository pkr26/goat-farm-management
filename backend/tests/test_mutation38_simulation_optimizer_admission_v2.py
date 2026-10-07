"""Normal saved optimizer policies retain editor ranges and actionable defaults.

The existing editor publishes the candidate/size/sale-age bounds. Zero pins an
integer axis; the documented bounded neighborhood admits the full half-year
radius needed to explore all 0..12 policies around their midpoint.
"""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from app.api.simulation import _run_cost
from app.simulation import optimization
from app.simulation.assumptions import OptimizationAssumptions, SimulationAssumptions
from app.simulation.engine import _CoreResult, _run_core


def _policy(payload: dict[str, Any]) -> OptimizationAssumptions:
    try:
        return OptimizationAssumptions.model_validate_json(json.dumps(payload))
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as error:
        pytest.fail(f"A supported optimizer document must be admitted: {error}")


@pytest.mark.parametrize(
    "field,low,high",
    [
        ("max_candidates", 1, 300),
        ("doe_scale_steps", 1, 9),
        ("sale_age_radius_months", 0, 9),
        ("festival_hold_radius_months", 0, 6),
        ("service_cull_radius_months", 0, 6),
    ],
    ids=[
        "candidate-budget",
        "herd-size-count",
        "sale-age-neighborhood",
        "festival-neighborhood",
        "service-neighborhood",
    ],
)
@pytest.mark.parametrize("upper", [False, True], ids=["lower", "upper"])
def test_saved_optimizer_endpoint_is_admitted(field: str, low: int, high: int, upper: bool) -> None:
    value = high if upper else low
    assert _policy({field: value}).model_dump()[field] == value


@pytest.mark.parametrize(
    "field,low,high",
    [
        ("max_candidates", 1, 300),
        ("doe_scale_steps", 1, 9),
        ("sale_age_radius_months", 0, 9),
        ("festival_hold_radius_months", 0, 6),
        ("service_cull_radius_months", 0, 6),
    ],
    ids=[
        "candidate-budget",
        "herd-size-count",
        "sale-age-neighborhood",
        "festival-neighborhood",
        "service-neighborhood",
    ],
)
@pytest.mark.parametrize("above", [False, True], ids=["below", "above"])
def test_optimizer_out_of_range_input_identifies_its_field(
    field: str, low: int, high: int, above: bool
) -> None:
    value = high + 1 if above else low - 1
    try:
        OptimizationAssumptions.model_validate_json(json.dumps({field: value}))
    except ValidationError as error:
        assert any(tuple(item["loc"]) == (field,) for item in error.errors()), error.errors()
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as error:
        pytest.fail(f"An invalid decision policy must produce a field validation error: {error}")
    else:
        pytest.fail("An out-of-range decision must not enter a saved optimizer policy")


def test_equal_herd_scale_bounds_pin_the_existing_herd_size() -> None:
    policy = _policy({"doe_scale_low": 1.0, "doe_scale_high": 1.0})
    assert policy.doe_scale_low == policy.doe_scale_high == 1.0


def test_default_optimizer_work_reservation_matches_the_published_starting_budget() -> None:
    # Existing frontend integration fixtures advertise 120 candidate plans when
    # no policy is supplied. Charge that work in addition to the normal run.
    assumptions = SimulationAssumptions.model_validate_json('{"meta":{"horizon_months":12}}')
    normal = _run_cost(assumptions, False, False, False)
    optimized = _run_cost(assumptions, False, False, True)
    assert optimized - normal == 120 * 12


@pytest.mark.parametrize(
    "axis,expected",
    [
        ("doe_scale_steps", {9, 12, 15}),
        ("sale_age_radius_months", {10, 11, 12, 13, 14}),
        ("festival_hold_radius_months", {5, 6, 7}),
        ("service_cull_radius_months", {5, 6, 7}),
    ],
    ids=[
        "small-middle-large-herd",
        "two-sale-months-either-side",
        "adjacent-festival-decisions",
        "adjacent-service-decisions",
    ],
)
def test_omitted_optimizer_axis_explores_its_documented_neighbors(
    monkeypatch: pytest.MonkeyPatch, axis: str, expected: set[int]
) -> None:
    policy: dict[str, Any] = {
        "max_candidates": 20,
        "doe_scale_low": 1.0,
        "doe_scale_high": 1.0,
        "doe_scale_steps": 1,
        "sale_age_radius_months": 0,
        "retention_step": 0.0,
        "loan_fraction_step": 0.0,
        "festival_hold_radius_months": 0,
        "service_cull_radius_months": 0,
    }
    del policy[axis]
    if axis == "doe_scale_steps":
        policy.update(doe_scale_low=0.75, doe_scale_high=1.25)
    assumptions = SimulationAssumptions.model_validate_json(
        json.dumps(
            {
                "meta": {"horizon_months": 12},
                "herd": {"does": 12, "bucks": 0, "auto_purchase_bucks": False},
                "growth": {"sale_age_months": 12},
                "sales": {"festival_hold_months": 6},
                "reproduction": {"max_services_before_cull": 6},
                "optimization": policy,
            }
        )
    )
    seen: set[int] = set()
    original = _run_core

    def observe(a: SimulationAssumptions) -> _CoreResult:
        result = original(a)
        if axis == "doe_scale_steps":
            seen.add(a.herd.does)
        elif axis == "sale_age_radius_months":
            seen.add(a.growth.sale_age_months)
        elif axis == "festival_hold_radius_months":
            seen.add(a.sales.festival_hold_months)
        else:
            seen.add(a.reproduction.max_services_before_cull)
        return result

    monkeypatch.setattr(optimization, "_run_core", observe)
    try:
        result = optimization.run_optimization(assumptions)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as error:
        pytest.fail(
            f"An admitted optimizer policy must complete its actual decision search: {error}"
        )
    assert seen == expected
    assert result.evaluated_candidates == len(expected)
