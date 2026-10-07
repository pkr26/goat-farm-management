"""Accepted native expected-head inputs cannot leave book assets after physical extinction."""

import json
from dataclasses import replace
from typing import Literal

import pytest

from app.simulation.assumptions import SimulationAssumptions
from app.simulation.engine import _CoreResult, _run_core
from app.simulation.shocks import MonthlyShockPath


def _forecast(animal_class: Literal["buck", "doe"], *, tiny: bool) -> _CoreResult:
    assumptions = SimulationAssumptions.model_validate_json(
        json.dumps(
            {
                "meta": {"horizon_months": 12},
                "herd": {
                    "does": 0,
                    "bucks": 0,
                    "female_kids": 0,
                    "male_kids": 0,
                    "female_weaners": 0,
                    "male_weaners": 0,
                    "female_growers": 0,
                    "male_growers": 0,
                    "auto_purchase_bucks": tiny,
                    "female_retention_fraction": 0.0,
                },
                "reproduction": {"conception_rate": 0.0},
                "mortality": {
                    "kid_pre_weaning": 0.0,
                    "kid_post_weaning": 0.0,
                    "grower": 0.0,
                    "adult": 0.9,
                },
                "culling": {"doe_cull_rate_annual": 0.0, "buck_rotation_years": 1},
                "sales": {"festival_sale_months": []},
                "costs": {"breeding_stock_useful_life_months": 24},
                "finance": {"terminal_livestock_realization_fraction": 1.0},
                "events": [
                    {
                        "month": 1,
                        "kind": "purchase",
                        "animal_class": animal_class,
                        "count": 5e-324 if tiny else 1.0,
                        "price_per_head": 1_000_000_000.0 if tiny else 200.0,
                    }
                ],
            }
        )
    )
    path = replace(MonthlyShockPath.neutral(12), adult_mortality=[20.0] * 12)
    try:
        core = _run_core(assumptions, shock_path=path)
        json.dumps(core.terminal_value_breakdown.model_dump(mode="json"), allow_nan=False)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as error:
        pytest.fail(f"Accepted finite expected-head stock must finish its native forecast: {error}")
    assert len(core.months) == 12
    return core


@pytest.mark.parametrize("animal_class", ["buck", "doe"], ids=["sire", "dam"])
def test_physical_extinction_derecognizes_all_book_value(
    animal_class: Literal["buck", "doe"],
) -> None:
    core = _forecast(animal_class, tiny=True)
    opening = core.months[0]
    assert opening.purchases_head == opening.deaths == 5e-324
    assert opening.breeding_stock_capex > 0.0
    assert opening.breeding_stock_disposal_cost == opening.breeding_stock_capex
    assert all(row.total_herd == row.culls_head == 0.0 for row in core.months)
    assert all(row.breeding_stock_disposal_cost == 0.0 for row in core.months[1:])
    assert core.terminal_value_breakdown.breeding_stock == 0.0
    assert core.terminal_value_breakdown.livestock == 0.0


@pytest.mark.parametrize("animal_class", ["buck", "doe"], ids=["sire", "dam"])
def test_surviving_stock_retains_proportional_book_and_market_conservation(
    animal_class: Literal["buck", "doe"],
) -> None:
    core = _forecast(animal_class, tiny=False)
    closing_head = core.months[-1].total_herd
    assert closing_head == pytest.approx(1e-6, rel=1e-9)
    assert all(row.culls_head == 0.0 for row in core.months)
    assert core.months[0].breeding_stock_capex == 200.0
    # Twelve months of the declared 24-month life leave half the surviving basis.
    assert core.terminal_value_breakdown.breeding_stock == pytest.approx(
        200.0 * closing_head * 0.5, rel=1e-9
    )
    assert core.terminal_value_breakdown.livestock >= 0.0
    assert (
        core.terminal_value_breakdown.breeding_stock + core.terminal_value_breakdown.livestock
    ) == pytest.approx(core.records[-1].stock_value, rel=1e-9)
