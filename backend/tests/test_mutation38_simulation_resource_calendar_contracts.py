"""Normal JSON feed calendars preserve a complete year and valid stored reserves."""

import json

import pytest
from pydantic import ValidationError

from app.simulation.assumptions import FeedAssumptions


@pytest.mark.parametrize(
    "field",
    [
        "monthly_green_price_multipliers",
        "monthly_dry_price_multipliers",
        "monthly_concentrate_price_multipliers",
        "monthly_fodder_yield_multipliers",
    ],
)
def test_feed_json_requires_one_observation_for_every_month_of_the_year(field: str) -> None:
    annual = [0.5 + month / 12 for month in range(12)]
    try:
        admitted = FeedAssumptions.model_validate_json(json.dumps({field: annual}))
    except ValidationError as exc:
        pytest.fail(f"A complete valid feed calendar must be admitted: {exc}")
    assert admitted.model_dump()[field] == annual
    for incomplete in (annual[:-1], [*annual, 1.0]):
        with pytest.raises(ValidationError):
            FeedAssumptions.model_validate_json(json.dumps({field: incomplete}))


def test_default_feed_json_retains_a_full_unseasoned_year_for_each_calendar() -> None:
    try:
        admitted = FeedAssumptions.model_validate_json("{}")
    except (TypeError, ValueError) as exc:
        pytest.fail(f"Omitted feed assumptions must provide a coherent default year: {exc}")
    wire = admitted.model_dump(mode="json")
    for field in (
        "monthly_green_price_multipliers",
        "monthly_dry_price_multipliers",
        "monthly_concentrate_price_multipliers",
        "monthly_fodder_yield_multipliers",
    ):
        assert wire[field] == [1.0] * 12


@pytest.mark.parametrize("reserve", [0.0, 100.0], ids=["empty", "full"])
def test_feed_json_admits_a_reserve_equal_to_capacity_and_rejects_actual_overflow(
    reserve: float,
) -> None:
    payload = {
        "initial_fodder_stock_kg_dm": reserve,
        "fodder_storage_capacity_kg_dm": reserve,
    }
    try:
        admitted = FeedAssumptions.model_validate_json(json.dumps(payload))
    except ValidationError as exc:
        pytest.fail(f"A reserve exactly fitting storage must be admitted: {exc}")
    assert admitted.initial_fodder_stock_kg_dm == reserve
    assert admitted.fodder_storage_capacity_kg_dm == reserve
    with pytest.raises(ValidationError):
        FeedAssumptions.model_validate_json(
            json.dumps({**payload, "initial_fodder_stock_kg_dm": reserve + 1.0})
        )
