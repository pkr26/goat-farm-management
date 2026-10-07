"""A vanished grower chain admits its graduation boundary, never a later juvenile age."""

import json

import pytest
from pydantic import ValidationError

from app.simulation.assumptions import SimulationAssumptions


@pytest.mark.parametrize("animal_class", ["female_grower", "male_grower"])
def test_empty_grower_arrival_is_exactly_the_six_month_graduation_boundary(
    animal_class: str,
) -> None:
    event = {
        "month": 1,
        "kind": "purchase",
        "animal_class": animal_class,
        "count": 1.0,
        "age_months": 6,
    }
    policy = {
        "reproduction": {"age_at_first_breeding_months": 6},
        "growth": {"sale_age_months": 6},
    }
    try:
        admitted = SimulationAssumptions.model_validate_json(
            json.dumps({**policy, "events": [event]})
        )
    except (AttributeError, LookupError, TypeError, ValueError) as exc:
        pytest.fail(f"The exact graduation-boundary arrival is a valid full schedule: {exc}")
    assert admitted.events[0].age_months == 6
    with pytest.raises(ValidationError):
        SimulationAssumptions.model_validate_json(
            json.dumps({**policy, "events": [{**event, "age_months": 7}]})
        )
