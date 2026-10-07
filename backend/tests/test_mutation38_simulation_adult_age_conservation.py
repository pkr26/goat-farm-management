"""A full supported age-policy horizon conserves every foundation/retained doe."""

import json

import pytest

from app.simulation.assumptions import SimulationAssumptions
from app.simulation.engine import run_simulation


def test_valid_doe_age_policy_completes_and_conserves_retained_stock() -> None:
    assumptions = SimulationAssumptions.model_validate_json(
        json.dumps(
            {
                "meta": {"horizon_months": 36},
                "herd": {
                    "does": 2,
                    "bucks": 0,
                    "female_kids": 1,
                    "male_kids": 0,
                    "female_weaners": 0,
                    "male_weaners": 0,
                    "female_growers": 0,
                    "male_growers": 0,
                    "auto_purchase_bucks": False,
                    "female_retention_fraction": 1.0,
                    "foundation_flock_state": "open",
                },
                "reproduction": {"conception_rate": 0.0},
                "mortality": {
                    "kid_pre_weaning": 0.0,
                    "kid_post_weaning": 0.0,
                    "grower": 0.0,
                    "adult": 0.0,
                },
                "culling": {"max_doe_age_months": 36, "doe_cull_rate_annual": 0.0},
                "sales": {"festival_sale_months": []},
            }
        )
    )
    try:
        result = run_simulation(assumptions, with_break_even=False)
        json.dumps(result.model_dump(mode="json"), allow_nan=False)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as error:
        pytest.fail(
            f"A supported complete doe-age forecast must finish and retain its stock: {error}"
        )
    assert len(result.months) == 36
    previous = 3.0
    for row in result.months:
        assert row.births == row.deaths == row.purchases_head == row.sales_head == 0.0
        assert row.total_herd == pytest.approx(previous - row.culls_head, abs=1e-8)
        previous = row.total_herd
    assert sum(row.culls_head for row in result.months) == pytest.approx(3.0, abs=1e-8)
    assert result.months[-1].total_herd == pytest.approx(0.0, abs=1e-8)
