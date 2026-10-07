"""Normal-JSON assumption admission, policy defaults and bounded work contracts."""

import json

import pytest
from pydantic import BaseModel, ValidationError

from app.schemas.simulation import RunIn
from app.simulation.assumptions import (
    CostsAssumptions,
    CullingAssumptions,
    FeedAssumptions,
    GrowthAssumptions,
    HerdAssumptions,
    MetaAssumptions,
    ParityMultipliers,
    ReproductionAssumptions,
    SimulationAssumptions,
)

# These are the user-facing admissible policy ranges, exercised through JSON
# validation and returned values, rather than inspecting schema metadata.
INTEGER_POLICIES: list[tuple[type[BaseModel], str, int, int, dict[str, object]]] = [
    (MetaAssumptions, "horizon_months", 12, 240, {}),
    (HerdAssumptions, "does", 0, 100_000, {}),
    (HerdAssumptions, "bucks", 0, 100_000, {}),
    (HerdAssumptions, "female_growers", 0, 100_000, {}),
    (HerdAssumptions, "male_growers", 0, 100_000, {}),
    (HerdAssumptions, "female_weaners", 0, 100_000, {}),
    (HerdAssumptions, "male_weaners", 0, 100_000, {}),
    (HerdAssumptions, "female_kids", 0, 100_000, {}),
    (HerdAssumptions, "male_kids", 0, 100_000, {}),
    (HerdAssumptions, "max_breeding_does", 0, 100_000, {}),
    (
        HerdAssumptions,
        "foundation_doe_age_min_months",
        0,
        180,
        {"foundation_doe_age_max_months": 180},
    ),
    (
        HerdAssumptions,
        "foundation_doe_age_max_months",
        0,
        180,
        {"foundation_doe_age_min_months": 0},
    ),
    (HerdAssumptions, "purchased_doe_settling_months", 0, 6, {}),
    (ReproductionAssumptions, "gestation_months", 1, 12, {}),
    (ReproductionAssumptions, "lactation_months", 1, 12, {}),
    (ReproductionAssumptions, "months_open_before_breeding", 0, 12, {}),
    (ReproductionAssumptions, "age_at_first_breeding_months", 6, 30, {}),
    (ReproductionAssumptions, "max_services_before_cull", 0, 12, {}),
    (CullingAssumptions, "max_doe_age_months", 36, 180, {}),
    (CullingAssumptions, "buck_rotation_years", 1, 10, {}),
    (CullingAssumptions, "buck_doe_ratio", 1, 100, {}),
    (CostsAssumptions, "labour_per_head_threshold", 1, 1_000_000_000_000_000, {}),
]


@pytest.mark.parametrize(
    "model,field,lower,upper,context",
    INTEGER_POLICIES,
    ids=[f"{model.__name__}-{field}" for model, field, *_ in INTEGER_POLICIES],
)
def test_json_integer_policy_retains_both_endpoints_and_rejects_outside(
    model: type[BaseModel],
    field: str,
    lower: int,
    upper: int,
    context: dict[str, object],
) -> None:
    for value in (lower, upper):
        try:
            admitted = model.model_validate_json(json.dumps({**context, field: value}))
        except ValidationError as exc:
            pytest.fail(f"Valid {field}={value} policy was rejected: {exc}")
        assert admitted.model_dump()[field] == value
    for value in (lower - 1, upper + 1):
        with pytest.raises(ValidationError):
            model.model_validate_json(json.dumps({**context, field: value}))


@pytest.mark.parametrize(
    "model,expected",
    [
        (
            HerdAssumptions,
            {
                "does": 50,
                "bucks": 2,
                "max_breeding_does": 50,
                "foundation_doe_age_min_months": 18,
                "foundation_doe_age_max_months": 42,
                "purchased_doe_settling_months": 1,
            },
        ),
        (
            ReproductionAssumptions,
            {
                "gestation_months": 5,
                "lactation_months": 2,
                "months_open_before_breeding": 1,
                "age_at_first_breeding_months": 12,
                "max_services_before_cull": 2,
            },
        ),
        (
            CullingAssumptions,
            {"max_doe_age_months": 72, "buck_rotation_years": 3, "buck_doe_ratio": 20},
        ),
    ],
    ids=[
        "sourced-foundation-policy",
        "operational-reproduction-policy",
        "disposal-and-sire-policy",
    ],
)
def test_omitted_json_policy_preserves_the_documented_reference_unit(
    model: type[BaseModel], expected: dict[str, int]
) -> None:
    admitted = model.model_validate_json("{}")
    actual = admitted.model_dump()
    assert {field: actual[field] for field in expected} == expected


@pytest.mark.parametrize("field", ["litter_size", "conception_rate"])
def test_json_parity_tables_support_one_through_twelve_and_the_full_multiplier_range(
    field: str,
) -> None:
    for values in ([1.0], [2.0] * 12):
        try:
            admitted = ParityMultipliers.model_validate_json(json.dumps({field: values}))
        except ValidationError as exc:
            pytest.fail(f"Valid parity table was rejected: {exc}")
        assert admitted.model_dump()[field] == values
    for invalid_values in ([], [1.0] * 13, [0.0], [2.01], ["1.0"], [float("nan")]):
        with pytest.raises(ValidationError):
            ParityMultipliers.model_validate_json(json.dumps({field: invalid_values}))


@pytest.mark.parametrize(
    "model,field,ceiling,context",
    [
        (HerdAssumptions, "doe_purchase_price", 1_000_000_000.0, {}),
        (FeedAssumptions, "cultivated_fodder_acres", 1_000_000.0, {}),
        (FeedAssumptions, "fodder_yield_t_dm_per_acre_year", 1000.0, {}),
        (
            GrowthAssumptions,
            "adult_weight_doe_kg",
            1000.0,
            {},
        ),
    ],
    ids=["money", "cultivated-area", "annual-fodder-yield", "live-weight"],
)
def test_json_magnitude_ceiling_admits_the_endpoint_and_rejects_the_next_unit(
    model: type[BaseModel], field: str, ceiling: float, context: dict[str, object]
) -> None:
    try:
        admitted = model.model_validate_json(json.dumps({**context, field: ceiling}))
    except ValidationError as exc:
        pytest.fail(f"Valid magnitude endpoint was rejected: {exc}")
    assert admitted.model_dump()[field] == ceiling
    with pytest.raises(ValidationError):
        model.model_validate_json(json.dumps({**context, field: ceiling + 1.0}))


def test_normal_json_plan_event_work_budget_is_exactly_five_hundred_documents() -> None:
    event = {"month": 1, "kind": "purchase", "animal_class": "doe", "count": 1.0}
    try:
        admitted = SimulationAssumptions.model_validate_json(json.dumps({"events": [event] * 500}))
    except ValidationError as exc:
        pytest.fail(f"Valid event-document work budget was rejected: {exc}")
    assert len(admitted.events) == 500
    assert sum(event.count for event in admitted.events) == 500.0
    with pytest.raises(ValidationError):
        SimulationAssumptions.model_validate_json(json.dumps({"events": [event] * 501}))


def test_normal_json_run_requires_explicit_opt_in_for_every_optional_analysis() -> None:
    admitted = RunIn.model_validate_json('{"assumptions": {}}')
    assert (admitted.monte_carlo, admitted.sensitivity, admitted.optimization) == (
        False,
        False,
        False,
    )
