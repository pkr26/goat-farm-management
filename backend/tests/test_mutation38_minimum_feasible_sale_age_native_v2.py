"""Sale-age optimization floors must preserve a complete admitted goat scenario."""

import json

import pytest

from app.simulation.assumptions import SimulationAssumptions, min_feasible_sale_age


@pytest.mark.parametrize(
    ("events", "expected_age"),
    (
        ([], 6),
        ([{"kind": "purchase", "month": 1, "animal_class": "male_grower", "count": 1}], 6),
        (
            [
                {
                    "kind": "purchase",
                    "month": 1,
                    "animal_class": "female_grower",
                    "count": 1,
                    "age_months": 8,
                }
            ],
            6,
        ),
        (
            [
                {
                    "kind": "purchase",
                    "month": 1,
                    "animal_class": "male_grower",
                    "count": 1,
                    "age_months": 8,
                }
            ],
            9,
        ),
    ),
    ids=("empty", "default-arrival-age", "female-independent-chain", "explicit-male-arrival"),
)
def test_native_sale_age_floor_preserves_whole_valid_purchase_document(
    events: list[dict[str, object]], expected_age: int
) -> None:
    assumptions = SimulationAssumptions.model_validate_json(json.dumps({"events": events}))
    try:
        floor = min_feasible_sale_age(assumptions)
        candidate = json.loads(assumptions.model_dump_json())
        candidate["growth"]["sale_age_months"] = floor
        admitted = SimulationAssumptions.model_validate_json(json.dumps(candidate))
    except Exception as error:
        pytest.fail(f"A sale-age floor must preserve its admitted purchase document: {error}")
    assert admitted.growth.sale_age_months == expected_age
    assert admitted.events == assumptions.events
