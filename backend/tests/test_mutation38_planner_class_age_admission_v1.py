"""Planner class-age bounds must admit real coherent purchase documents."""

import json

import pytest
from pydantic import ValidationError

from app.simulation.assumptions import SimulationAssumptions
from app.simulation.planner import _class_max_age


@pytest.mark.parametrize("animal_class", ["male_grower", "female_grower"])
def test_planner_maximum_class_age_is_admitted_by_the_current_public_chain(
    animal_class: str,
) -> None:
    document = json.loads(SimulationAssumptions().model_dump_json())
    document["meta"]["horizon_months"] = 12
    document["growth"]["sale_age_months"] = 7
    document["reproduction"]["age_at_first_breeding_months"] = 7
    assumptions = SimulationAssumptions.model_validate_json(json.dumps(document))
    age = _class_max_age(
        animal_class,
        assumptions.growth.sale_age_months,
        assumptions.reproduction.age_at_first_breeding_months,
    )
    document["events"] = [
        {
            "month": 1,
            "kind": "purchase",
            "animal_class": animal_class,
            "count": 1.0,
            "age_months": age,
        }
    ]
    try:
        admitted = SimulationAssumptions.model_validate_json(json.dumps(document))
    except ValidationError:
        admitted = None
    assert admitted is not None, "Planner class bounds must describe an admitted arrival age"
