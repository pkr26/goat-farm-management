"""Supported production-system copies retain each breed's birth and field growth facts."""

import json

import pytest

from app.simulation.assumptions import SimulationAssumptions
from app.simulation.defaults import apply_system


@pytest.mark.parametrize(
    "birth,stall_curve,field_curve,adult",
    [
        (
            2.5,
            [2.5, 6.0, 9.5, 12.1, 14.6, 16.3, 17.8, 19.3, 20.8, 22.2, 23.5, 24.6, 25.6],
            [2.5, 3.8, 5.1, 6.3, 8.0, 9.7, 11.3, 12.9, 14.5, 16.1, 17.3, 18.5, 19.6],
            40.0,
        ),
        (
            3.0,
            [3.0, 8.25, 13.5, 17.4, 21.15, 23.7, 25.95, 28.2, 30.45, 32.55, 34.5, 36.15, 37.65],
            [3.0, 4.95, 6.9, 8.7, 11.25, 13.8, 16.2, 18.6, 21.0, 23.4, 25.2, 27.0, 28.65],
            55.0,
        ),
    ],
    ids=["reference-growth", "larger-breed-growth"],
)
def test_native_system_copy_preserves_birth_and_breed_scaled_field_growth(
    birth: float, stall_curve: list[float], field_curve: list[float], adult: float
) -> None:
    base = SimulationAssumptions.model_validate_json(
        json.dumps(
            {
                "growth": {
                    "birth_weight_kg": birth,
                    "weight_by_age_months": stall_curve,
                    "adult_weight_doe_kg": adult,
                    "adult_weight_buck_kg": adult + 10.0,
                }
            }
        )
    )
    original = base.model_dump()
    try:
        variant = apply_system(base, "semi_intensive")
        admitted = SimulationAssumptions.model_validate(variant.model_dump())
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as exc:
        pytest.fail(
            f"A valid native system conversion must retain a usable breed growth fact: {exc}"
        )
    assert variant is not base and variant.growth is not base.growth
    assert variant.growth.weight_by_age_months is not base.growth.weight_by_age_months
    assert base.model_dump() == original
    assert admitted.growth.birth_weight_kg == birth
    assert admitted.growth.weight_by_age_months == pytest.approx(field_curve, abs=1e-12)
    assert admitted.growth.growth_regime == "semi_intensive"
    assert admitted.feed.grazing_dm_fraction == 0.3
    assert admitted.mortality.adult > base.mortality.adult
    assert admitted.mortality.kid_pre_weaning > base.mortality.kid_pre_weaning
