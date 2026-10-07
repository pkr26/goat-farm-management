"""A ready doe with an available sire can fail unlimited services without losing stock."""

import json

import pytest

from app.simulation.assumptions import SimulationAssumptions
from app.simulation.engine import run_simulation


def test_zero_conception_unlimited_service_completes_and_conserves_ready_stock() -> None:
    assumptions = SimulationAssumptions.model_validate_json(
        json.dumps(
            {
                "meta": {"horizon_months": 12},
                "herd": {
                    "does": 2,
                    "bucks": 1,
                    "auto_purchase_bucks": False,
                    "foundation_flock_state": "open",
                },
                "reproduction": {"conception_rate": 0.0, "max_services_before_cull": 0},
                "mortality": {
                    "kid_pre_weaning": 0.0,
                    "kid_post_weaning": 0.0,
                    "grower": 0.0,
                    "adult": 0.0,
                },
                "culling": {"doe_cull_rate_annual": 0.0, "buck_rotation_years": 10},
                "sales": {"festival_sale_months": [], "milk_sale_litres_per_doe_day": 0.0},
            }
        )
    )
    try:
        result = run_simulation(assumptions, with_break_even=False)
        json.dumps(result.model_dump(mode="json"), allow_nan=False)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as error:
        pytest.fail(f"Unlimited failed services with an available sire must finish: {error}")
    assert len(result.months) == 12
    for row in result.months:
        assert row.total_herd == 3.0
        assert (
            row.births
            == row.deaths
            == row.culls_head
            == row.sales_head
            == row.purchases_head
            == 0.0
        )
        assert row.milk_revenue == 0.0
