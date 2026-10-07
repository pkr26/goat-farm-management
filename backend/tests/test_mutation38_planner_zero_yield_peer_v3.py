"""Native sale-plan admission, guidance and independent target processing."""

import json
from collections.abc import Callable

import pytest

from app.simulation.assumptions import SimulationAssumptions
from app.simulation.planner import (
    SaleTarget,
    close_gaps,
)


def _completed[T](operation: Callable[[], T]) -> T:
    try:
        return operation()
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as error:
        pytest.fail(f"A supported native sale plan must complete coherently: {error}")


def _assumptions(*, stock: float = 0.0) -> SimulationAssumptions:
    events = (
        [{"month": 1, "kind": "purchase", "animal_class": "male_kid", "count": stock}]
        if stock
        else []
    )
    return SimulationAssumptions.model_validate_json(
        json.dumps(
            {
                "meta": {"start_year_month": "2051-01", "horizon_months": 24},
                "herd": {
                    "does": 0,
                    "bucks": 0,
                    "auto_purchase_bucks": True,
                    "purchased_doe_settling_months": 0,
                },
                "reproduction": {
                    "conception_rate": 1.0,
                    "litter_size": 2.0,
                    "stillbirth_rate": 0.0,
                    "sex_ratio_female": 0.5,
                    "parity_multipliers": {"litter_size": [1.0], "conception_rate": [1.0]},
                },
                "mortality": {
                    "kid_pre_weaning": 0.0,
                    "kid_post_weaning": 0.0,
                    "grower": 0.0,
                    "adult": 0.0,
                },
                "culling": {"doe_cull_rate_annual": 0.0, "buck_rotation_years": 10},
                "sales": {"festival_sale_months": []},
                "events": events,
            }
        )
    )


def test_all_male_births_leave_female_target_unmet_without_blocking_male_sale() -> None:
    document = _assumptions().model_dump(mode="json")
    document["reproduction"]["sex_ratio_female"] = 0.0
    assumptions = SimulationAssumptions.model_validate_json(json.dumps(document))
    purchases, evaluation, closed, _ = _completed(
        lambda: close_gaps(
            assumptions,
            [
                SaleTarget(month=24, animal_class="female_kid", count=1.0),
                SaleTarget(month=24, animal_class="male_kid", count=1.0),
            ],
        )
    )
    assert not evaluation.targets[0].met and not closed
    assert purchases and evaluation.targets[1].met and evaluation.targets[1].filled == 1.0
