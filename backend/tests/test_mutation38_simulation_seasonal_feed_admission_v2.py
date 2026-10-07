"""Feed-seasonality inputs are positive, finite and preserve the documented cap."""

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
def test_json_seasonal_feed_values_admit_the_cap_and_reject_zero_negative_or_excess(
    field: str,
) -> None:
    values = [10.0] + [1.0] * 11
    try:
        admitted = FeedAssumptions.model_validate_json(json.dumps({field: values}))
    except ValidationError as exc:
        pytest.fail(
            f"A valid seasonal feed multiplier at the supported cap must be admitted: {exc}"
        )
    assert admitted.model_dump()[field] == values
    for invalid in (0.0, -0.1, 10.01):
        with pytest.raises(ValidationError):
            FeedAssumptions.model_validate_json(json.dumps({field: [invalid] + [1.0] * 11}))
