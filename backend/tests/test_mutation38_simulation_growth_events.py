"""JSON growth facts and purchased young-stock age-chain conservation."""

import json

import pytest
from pydantic import ValidationError

from app.simulation.assumptions import (
    GrowthAssumptions,
    HerdEventAssumptions,
    SimulationAssumptions,
)


@pytest.mark.parametrize(
    "field,lower,upper", [("adult_weight_age_months", 13, 120), ("sale_age_months", 6, 24)]
)
def test_json_growth_calendar_preserves_inclusive_policy_endpoints(
    field: str, lower: int, upper: int
) -> None:
    for value in (lower, upper):
        try:
            admitted = GrowthAssumptions.model_validate_json(json.dumps({field: value}))
        except ValidationError as exc:
            pytest.fail(f"Valid growth calendar was rejected: {exc}")
        assert admitted.model_dump()[field] == value
    for value in (lower - 1, upper + 1):
        with pytest.raises(ValidationError):
            GrowthAssumptions.model_validate_json(json.dumps({field: value}))


def test_json_growth_curve_has_exactly_birth_through_month_twelve_and_permits_plateaus() -> None:
    for curve in ([2.5] * 13, [2.5, 3.0] + [4.0] * 11):
        try:
            admitted = GrowthAssumptions.model_validate_json(
                json.dumps({"weight_by_age_months": curve})
            )
        except ValidationError as exc:
            pytest.fail(f"Valid thirteen-point growth fact was rejected: {exc}")
        assert admitted.weight_by_age_months == curve
        assert admitted.birth_weight_kg == curve[0]
    for curve in ([2.5] * 12, [2.5] * 14, [2.5, 2.4] + [4.0] * 11, [3.0] * 13):
        with pytest.raises(ValidationError):
            GrowthAssumptions.model_validate_json(json.dumps({"weight_by_age_months": curve}))


def test_native_json_event_preserves_one_based_months_and_optional_age_bounds() -> None:
    event = {"month": 1, "kind": "purchase", "animal_class": "female_grower", "count": 1.0}
    for age in (None, 0, 30):
        try:
            admitted = HerdEventAssumptions.model_validate_json(
                json.dumps({**event, "age_months": age})
            )
        except ValidationError as exc:
            pytest.fail(f"Valid event-document endpoint was rejected: {exc}")
        assert admitted.month == 1 and admitted.age_months == age
    for changes in ({"month": 0}, {"age_months": -1}, {"age_months": 31}):
        with pytest.raises(ValidationError):
            HerdEventAssumptions.model_validate_json(json.dumps({**event, **changes}))


def test_json_event_at_the_final_projection_month_is_retained() -> None:
    event = {"month": 12, "kind": "purchase", "animal_class": "doe", "count": 1.0}
    try:
        admitted = SimulationAssumptions.model_validate_json(
            json.dumps({"meta": {"horizon_months": 12}, "events": [event]})
        )
    except ValidationError as exc:
        pytest.fail(f"The final month belongs to the projection: {exc}")
    assert admitted.events[0].month == admitted.meta.horizon_months == 12
    with pytest.raises(ValidationError):
        SimulationAssumptions.model_validate_json(
            json.dumps({"meta": {"horizon_months": 12}, "events": [{**event, "month": 13}]})
        )


@pytest.mark.parametrize(
    "animal_class,lower,upper",
    [
        ("female_kid", 0, 2),
        ("male_kid", 0, 2),
        ("female_weaner", 3, 5),
        ("male_weaner", 3, 5),
        ("female_grower", 6, 11),
        ("male_grower", 6, 8),
    ],
)
def test_json_purchased_young_stock_stays_in_its_inclusive_biological_age_chain(
    animal_class: str, lower: int, upper: int
) -> None:
    event = {"month": 1, "kind": "purchase", "animal_class": animal_class, "count": 1.0}
    for age in (lower, upper):
        try:
            admitted = SimulationAssumptions.model_validate_json(
                json.dumps({"events": [{**event, "age_months": age}]})
            )
        except ValidationError as exc:
            pytest.fail(f"Valid {animal_class} arrival age {age} was rejected: {exc}")
        assert admitted.events[0].animal_class == animal_class
        assert admitted.events[0].age_months == age
    for age in (lower - 1, upper + 1):
        with pytest.raises(ValidationError):
            SimulationAssumptions.model_validate_json(
                json.dumps({"events": [{**event, "age_months": age}]})
            )
    # An absent age retains legacy mid-class placement but cannot conceal a
    # later event carrying a different, invalid biological fact.
    with pytest.raises(ValidationError):
        SimulationAssumptions.model_validate_json(
            json.dumps({"events": [event, {**event, "age_months": upper + 1}]})
        )


@pytest.mark.parametrize("animal_class", ["female_grower", "male_grower"])
def test_json_empty_grower_chain_retains_only_the_graduation_boundary(animal_class: str) -> None:
    event = {
        "month": 1,
        "kind": "purchase",
        "animal_class": animal_class,
        "count": 1.0,
        "age_months": 6,
    }
    policy = {"reproduction": {"age_at_first_breeding_months": 6}, "growth": {"sale_age_months": 6}}
    try:
        admitted = SimulationAssumptions.model_validate_json(
            json.dumps({**policy, "events": [event]})
        )
    except ValidationError as exc:
        pytest.fail(f"Exact six-month empty-chain boundary was rejected: {exc}")
    assert admitted.events[0].age_months == 6
    with pytest.raises(ValidationError):
        SimulationAssumptions.model_validate_json(
            json.dumps({**policy, "events": [{**event, "age_months": 5}]})
        )


@pytest.mark.parametrize(
    "kind,animal_class", [("sale", "female_kid"), ("purchase", "doe"), ("purchase", "buck")]
)
def test_json_arrival_age_is_never_applied_to_sale_or_adult_purchase(
    kind: str, animal_class: str
) -> None:
    event = {"month": 1, "kind": kind, "animal_class": animal_class, "count": 1.0, "age_months": 0}
    with pytest.raises(ValidationError):
        SimulationAssumptions.model_validate_json(json.dumps({"events": [event]}))
