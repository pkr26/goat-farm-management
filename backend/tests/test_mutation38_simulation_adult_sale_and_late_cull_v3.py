"""Empty adult sales and recently bought settling does retain cohort accounting."""

import json
from typing import Any

import pytest

from app.simulation.assumptions import SimulationAssumptions
from app.simulation.engine import run_simulation
from app.simulation.results import SimulationResult


def _forecast(
    events: list[dict[str, Any]],
    *,
    months: int = 12,
    bucks: int = 0,
    rotation: int = 3,
    buck_cull_price: float = 240.0,
    auto: bool = False,
) -> SimulationResult:
    payload = {
        "meta": {"horizon_months": months},
        "herd": {
            "does": 0,
            "bucks": bucks,
            "auto_purchase_bucks": auto,
            "foundation_flock_state": "open",
            "purchased_doe_settling_months": 6,
        },
        "reproduction": {"conception_rate": 0.0, "max_services_before_cull": 0},
        "mortality": {"adult": 0.0},
        "culling": {"doe_cull_rate_annual": 0.2, "buck_rotation_years": rotation},
        "growth": {"adult_weight_doe_kg": 40.0},
        "sales": {
            "cull_doe_price_per_kg": 123.0,
            "cull_buck_price_per_kg": buck_cull_price,
            "annual_livestock_price_growth_rate": 0.0,
        },
        "events": events,
    }
    assumptions = SimulationAssumptions.model_validate_json(json.dumps(payload))
    try:
        result = run_simulation(assumptions, with_break_even=False)
        json.dumps(result.model_dump(mode="json"), allow_nan=False)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as exc:
        pytest.fail(f"A valid adult-sale/cull schedule must return conserved cohort facts: {exc}")
    assert len(result.months) == months
    return result


@pytest.mark.parametrize("animal_class", ["doe", "buck"])
def test_unavailable_adult_sale_returns_the_full_shortfall_without_value_or_head(
    animal_class: str,
) -> None:
    result = _forecast([{"month": 1, "kind": "sale", "animal_class": animal_class, "count": 3.0}])
    fill = result.months[0].event_fills[0]
    assert fill.requested == fill.shortfall == 3.0
    assert fill.filled == fill.revenue == 0.0
    for month in result.months:
        assert month.total_herd == month.culls_head == month.cull_revenue == 0.0
        assert month.breeding_stock_disposal_cost == 0.0


def test_foundation_year_rate_cull_counts_real_bought_does_still_settling() -> None:
    result = _forecast(
        [
            {
                "month": 13,
                "kind": "purchase",
                "animal_class": "doe",
                "count": 2.0,
                "price_per_head": 100.0,
            }
        ],
        months=24,
    )
    assert all(month.total_herd == 0.0 for month in result.months[:12])
    month = result.months[12]
    removed = 2.0 * (1.0 - 0.8 ** (1.0 / 12.0))
    assert month.purchases_head == 2.0
    assert month.breeding_stock_capex == 200.0
    assert month.culls_head == pytest.approx(removed)
    assert month.total_herd == pytest.approx(2.0 - removed)
    assert month.cull_revenue == pytest.approx(removed * 40.0 * 123.0)
    assert month.breeding_stock_disposal_cost == pytest.approx(removed * 100.0)


def test_actual_sire_rotation_admits_zero_proceeds_and_conserves_the_disposed_head() -> None:
    result = _forecast([], bucks=1, rotation=1, buck_cull_price=0.0, auto=True)
    assert all(month.bucks == month.total_herd == 1.0 for month in result.months[:11])
    final = result.months[-1]
    assert final.culls_head == 1.0
    assert final.bucks == final.total_herd == final.cull_revenue == 0.0
    assert final.purchases_head == final.breeding_stock_capex == 0.0
