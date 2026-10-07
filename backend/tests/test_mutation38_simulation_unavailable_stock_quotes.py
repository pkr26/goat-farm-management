"""An unavailable class retains its documented default placement quote in planner data."""

import json
from typing import Any

import pytest

from app.simulation.assumptions import SimulationAssumptions
from app.simulation.engine import run_simulation
from app.simulation.results import SimulationResult


def _forecast(animal_class: str, stock: str, available: int) -> SimulationResult:
    herd: dict[str, Any] = {
        "does": 0,
        "bucks": 0,
        "female_kids": 0,
        "male_kids": 0,
        "female_weaners": 0,
        "male_weaners": 0,
        "female_growers": 0,
        "male_growers": 0,
        "auto_purchase_bucks": False,
        "foundation_flock_state": "open",
    }
    herd[stock] = available
    payload = {
        "meta": {"horizon_months": 12},
        "herd": herd,
        "reproduction": {"conception_rate": 0.0},
        "growth": {
            "weight_by_age_months": [
                2.5,
                5.0,
                7.5,
                10.0,
                12.5,
                15.0,
                17.5,
                20.0,
                22.5,
                25.0,
                27.5,
                30.0,
                32.5,
            ],
            "adult_weight_doe_kg": 40.0,
            "adult_weight_buck_kg": 50.0,
            "young_male_weight_premium": 0.2,
        },
        "sales": {
            "meat_price_per_kg": 100.0,
            "monthly_meat_price_multipliers": [1.0] * 12,
            "annual_livestock_price_growth_rate": 0.0,
            "festival_sale_months": [],
        },
        "events": [{"month": 1, "kind": "sale", "animal_class": animal_class, "count": 1.0}],
    }
    assumptions = SimulationAssumptions.model_validate_json(json.dumps(payload))
    try:
        result = run_simulation(assumptions, with_break_even=False)
        json.dumps(result.model_dump(mode="json"), allow_nan=False)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as exc:
        pytest.fail(f"A valid sale target must return its fill and placement quote: {exc}")
    assert len(result.months) == 12
    assert all(row.total_herd == 0.0 for row in result.months)
    return result


@pytest.mark.parametrize(
    "animal_class,stock",
    [
        ("female_kid", "female_kids"),
        ("male_kid", "male_kids"),
        ("female_weaner", "female_weaners"),
        ("male_weaner", "male_weaners"),
    ],
    ids=["female-kid", "male-kid", "female-weaner", "male-weaner"],
)
def test_unavailable_class_keeps_the_placement_quote_of_its_actual_fresh_stock(
    animal_class: str,
    stock: str,
) -> None:
    empty = _forecast(animal_class, stock, 0)
    placed = _forecast(animal_class, stock, 1)
    empty_fill = empty.months[0].event_fills[0]
    placed_fill = placed.months[0].event_fills[0]
    assert empty_fill.requested == placed_fill.requested == 1.0
    assert empty_fill.filled == empty_fill.revenue == 0.0
    assert empty_fill.shortfall == 1.0
    assert placed_fill.filled == 1.0 and placed_fill.shortfall == 0.0
    assert placed_fill.price_per_head == placed_fill.revenue > 0.0
    # The helper documents that an empty pool keeps its placement age. The
    # same class, curve, male premium and market must therefore quote the
    # same unit rate as the actual newly placed stock, even with no fill.
    assert empty_fill.price_per_head == pytest.approx(placed_fill.price_per_head)
    empty_wire = empty.model_dump(mode="json")["months"][0]["event_fills"][0]
    placed_wire = placed.model_dump(mode="json")["months"][0]["event_fills"][0]
    assert empty_wire["price_per_head"] == pytest.approx(placed_wire["price_per_head"])
