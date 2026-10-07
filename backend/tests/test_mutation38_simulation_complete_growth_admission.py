"""Whole JSON growth admission protects adult anchors and every scheduled arrival."""

import json

import pytest
from pydantic import ValidationError

from app.simulation.assumptions import SimulationAssumptions


@pytest.mark.parametrize("adult_field", ["adult_weight_doe_kg", "adult_weight_buck_kg"])
def test_whole_json_admits_an_adult_equal_to_yearling_and_rejects_a_shrinking_anchor(
    adult_field: str,
) -> None:
    curve = [2.5 + month * 2.5 for month in range(12)] + [40.0]
    growth = {
        "weight_by_age_months": curve,
        "adult_weight_doe_kg": 60.0,
        "adult_weight_buck_kg": 60.0,
        adult_field: 40.0,
    }
    try:
        admitted = SimulationAssumptions.model_validate_json(json.dumps({"growth": growth}))
    except ValidationError as exc:
        pytest.fail(f"An adult plateau at the yearling anchor must be admitted: {exc}")
    assert admitted.growth.model_dump()[adult_field] == 40.0
    with pytest.raises(ValidationError):
        SimulationAssumptions.model_validate_json(
            json.dumps({"growth": {**growth, adult_field: 39.0}})
        )


def test_default_age_of_an_earlier_event_does_not_hide_a_later_invalid_arrival() -> None:
    unaged = {"month": 1, "kind": "purchase", "animal_class": "doe", "count": 1.0}
    arrival = {
        "month": 2,
        "kind": "purchase",
        "animal_class": "female_kid",
        "count": 1.0,
        "age_months": 2,
    }
    admitted = SimulationAssumptions.model_validate_json(json.dumps({"events": [unaged, arrival]}))
    assert admitted.events[0].age_months is None and admitted.events[1].age_months == 2
    with pytest.raises(ValidationError):
        SimulationAssumptions.model_validate_json(
            json.dumps({"events": [unaged, {**arrival, "age_months": 3}]})
        )
