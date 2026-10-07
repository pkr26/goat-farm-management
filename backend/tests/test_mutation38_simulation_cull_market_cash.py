"""A real adult event sale quotes and books the same supported native market shock."""

import json
from dataclasses import replace
from typing import Literal

import pytest

from app.simulation.assumptions import SimulationAssumptions
from app.simulation.engine import _run_core
from app.simulation.shocks import MonthlyShockPath


@pytest.mark.parametrize(
    "animal_class,adult_weight", [("buck", 42.0), ("doe", 41.0)], ids=["spent-sire", "spent-dam"]
)
@pytest.mark.parametrize("market_shock", [0.5, 2.0], ids=["market-crash", "strong-market"])
def test_actual_adult_sale_quote_and_cash_share_the_market_exposure(
    animal_class: Literal["buck", "doe"],
    adult_weight: float,
    market_shock: float,
) -> None:
    assumptions = SimulationAssumptions.model_validate_json(
        json.dumps(
            {
                "meta": {"horizon_months": 12},
                "herd": {
                    "does": int(animal_class == "doe"),
                    "bucks": int(animal_class == "buck"),
                    "auto_purchase_bucks": False,
                    "foundation_flock_state": "open",
                },
                "growth": {"adult_weight_doe_kg": 41.0, "adult_weight_buck_kg": 42.0},
                "reproduction": {"conception_rate": 0.0},
                "mortality": {
                    "kid_pre_weaning": 0.0,
                    "kid_post_weaning": 0.0,
                    "grower": 0.0,
                    "adult": 0.0,
                },
                "culling": {"doe_cull_rate_annual": 0.0, "buck_rotation_years": 10},
                "sales": {
                    "festival_sale_months": [],
                    "annual_livestock_price_growth_rate": 0.0,
                    "cull_doe_price_per_kg": 100.0,
                    "cull_buck_price_per_kg": 100.0,
                },
                "events": [
                    {"month": 1, "kind": "sale", "animal_class": animal_class, "count": 1.0}
                ],
            }
        )
    )
    path = replace(MonthlyShockPath.neutral(12), meat_price=[market_shock] * 12)
    try:
        core = _run_core(assumptions, shock_path=path)
        json.dumps(core.terminal_value_breakdown.model_dump(mode="json"), allow_nan=False)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as error:
        pytest.fail(f"A supported market exposure must produce a conserved adult sale: {error}")
    assert len(core.months) == 12
    first = core.months[0]
    expected_receipt = 100.0 * adult_weight * market_shock
    assert first.culls_head == 1.0
    assert first.cull_revenue == expected_receipt
    assert first.event_fills[0].filled == 1.0
    assert first.event_fills[0].price_per_head == first.event_fills[0].revenue == expected_receipt
    assert first.event_fills[0].shortfall == 0.0
    assert all(
        row.total_herd == row.births == row.deaths == row.purchases_head == 0.0
        for row in core.months
    )
    assert all(row.culls_head == row.cull_revenue == 0.0 for row in core.months[1:])
    assert (
        core.terminal_value_breakdown.breeding_stock
        == core.terminal_value_breakdown.livestock
        == 0.0
    )
