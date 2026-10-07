"""Literal scheduled-stock facts, complete fills and age-dependent money conservation."""

import json
from typing import Any

import pytest

from app.simulation.assumptions import SimulationAssumptions
from app.simulation.engine import run_simulation
from app.simulation.results import SimulationResult

CURVE = [2.5, 5.0, 7.5, 10.0, 12.5, 15.0, 17.5, 20.0, 22.5, 25.0, 27.5, 30.0, 32.5]


def _base() -> dict[str, Any]:
    return {
        "meta": {"horizon_months": 12, "start_year_month": "2026-01"},
        "herd": {
            "does": 0,
            "bucks": 0,
            "female_growers": 0,
            "male_growers": 0,
            "female_weaners": 0,
            "male_weaners": 0,
            "female_kids": 0,
            "male_kids": 0,
            "auto_purchase_bucks": False,
            "foundation_flock_state": "open",
            "female_retention_fraction": 1.0,
            "max_breeding_does": 0,
            "doe_purchase_price": 1111.0,
            "buck_purchase_price": 2222.0,
        },
        "reproduction": {"conception_rate": 0.0, "max_services_before_cull": 0},
        "growth": {
            "weight_by_age_months": CURVE,
            "adult_weight_doe_kg": 40.0,
            "adult_weight_buck_kg": 50.0,
            "young_male_weight_premium": 0.2,
        },
        "mortality": {"kid_pre_weaning": 0.0, "kid_post_weaning": 0.0, "grower": 0.0, "adult": 0.0},
        "culling": {"doe_cull_rate_annual": 0.0, "buck_rotation_years": 10},
        "sales": {
            "meat_price_per_kg": 100.0,
            "monthly_meat_price_multipliers": [1.0] * 12,
            "annual_livestock_price_growth_rate": 0.0,
            "festival_sale_months": [],
            "cull_doe_price_per_kg": 10.0,
            "cull_buck_price_per_kg": 20.0,
        },
    }


def _forecast(payload: dict[str, object]) -> SimulationResult:
    try:
        assumptions = SimulationAssumptions.model_validate_json(json.dumps(payload))
        result = run_simulation(assumptions, with_break_even=False)
        json.dumps(result.model_dump(mode="json"), allow_nan=False)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as exc:
        pytest.fail(f"A valid ordered-stock schedule must complete with serializable fills: {exc}")
    assert len(result.months) == 12
    previous = 0.0
    for row in result.months:
        assert row.deaths == pytest.approx(0.0, abs=1e-8)
        assert row.births == 0.0
        assert row.total_herd == pytest.approx(
            previous + row.purchases_head - row.sales_head - row.culls_head, abs=1e-8
        )
        previous = row.total_herd
    return result


@pytest.mark.parametrize(
    "animal_class,age,placed_age",
    [
        ("female_kid", 0, 0),
        ("female_kid", 2, 2),
        ("female_kid", None, 1),
        ("male_kid", 0, 0),
        ("male_kid", 2, 2),
        ("male_kid", None, 1),
        ("female_weaner", 3, 3),
        ("female_weaner", 5, 5),
        ("female_weaner", None, 4),
        ("male_weaner", 3, 3),
        ("male_weaner", 5, 5),
        ("male_weaner", None, 4),
        ("female_grower", 6, 6),
        ("female_grower", 11, 11),
        ("female_grower", None, 9),
        ("male_grower", 6, 6),
        ("male_grower", 8, 8),
        ("male_grower", None, 7),
    ],
)
def test_ordered_young_purchase_and_partial_sale_conserve_age_money_and_head(
    animal_class: str, age: int | None, placed_age: int
) -> None:
    payload = _base()
    payload["events"] = [
        {
            "month": 1,
            "kind": "purchase",
            "animal_class": animal_class,
            "count": 2.0,
            "age_months": age,
        },
        {"month": 1, "kind": "sale", "animal_class": animal_class, "count": 3.0},
    ]
    result = _forecast(payload)
    row = result.months[0]
    purchase, sale = row.event_fills
    kg = CURVE[placed_age] * (1.2 if animal_class.startswith("male_") else 1.0)
    expected_price = kg * 100.0
    assert purchase.requested == purchase.filled == 2.0
    assert purchase.shortfall == 0.0
    assert sale.requested == 3.0 and sale.filled == 2.0 and sale.shortfall == 1.0
    assert purchase.animal_class == sale.animal_class == animal_class
    assert purchase.month == sale.month == 1
    assert purchase.kind == "purchase" and sale.kind == "sale"
    assert purchase.price_per_head == pytest.approx(expected_price)
    assert sale.price_per_head == pytest.approx(expected_price)
    assert purchase.revenue == sale.revenue == pytest.approx(2.0 * expected_price)
    assert row.purchase_cost == row.sales_revenue == pytest.approx(2.0 * expected_price)
    assert row.breeding_stock_capex == row.culls_head == row.cull_revenue == 0.0
    assert row.purchases_head == row.sales_head == 2.0
    assert all(month.total_herd == 0.0 for month in result.months)


@pytest.mark.parametrize("animal_class", ["doe", "buck"])
@pytest.mark.parametrize("override", [None, 0.0, 123.4])
def test_ordered_adult_events_respect_capitalization_and_explicit_zero_price(
    animal_class: str, override: float | None
) -> None:
    payload = _base()
    payload["events"] = [
        {
            "month": 1,
            "kind": "purchase",
            "animal_class": animal_class,
            "count": 2.0,
            "price_per_head": override,
        },
        {
            "month": 1,
            "kind": "sale",
            "animal_class": animal_class,
            "count": 3.0,
            "price_per_head": override,
        },
    ]
    result = _forecast(payload)
    row = result.months[0]
    purchase, sale = row.event_fills
    buy_price = override if override is not None else 1111.0 if animal_class == "doe" else 2222.0
    sell_price = override if override is not None else 400.0 if animal_class == "doe" else 1000.0
    assert purchase.filled == sale.filled == 2.0
    assert sale.requested == 3.0 and sale.shortfall == 1.0
    assert purchase.price_per_head == buy_price
    assert sale.price_per_head == sell_price
    assert purchase.revenue == row.breeding_stock_capex == pytest.approx(2.0 * buy_price)
    assert sale.revenue == row.cull_revenue == pytest.approx(2.0 * sell_price)
    assert row.purchase_cost == row.sales_head == row.sales_revenue == 0.0
    assert row.purchases_head == row.culls_head == 2.0
    assert all(month.total_herd == 0.0 for month in result.months)


@pytest.mark.parametrize("animal_class", ["female_grower", "male_grower"])
def test_empty_grower_chain_preserves_ordered_graduation_boundary_stock(animal_class: str) -> None:
    payload = _base()
    payload["reproduction"] = {
        "conception_rate": 0.0,
        "max_services_before_cull": 0,
        "age_at_first_breeding_months": 6,
    }
    payload["growth"] = {**dict(payload["growth"]), "sale_age_months": 6}
    payload["events"] = [
        {
            "month": 1,
            "kind": "purchase",
            "animal_class": animal_class,
            "count": 2.0,
            "age_months": 6,
        },
        {"month": 1, "kind": "sale", "animal_class": animal_class, "count": 1.0},
    ]
    result = _forecast(payload)
    purchase, sale = result.months[0].event_fills
    price = 2100.0 if animal_class == "male_grower" else 1750.0
    assert purchase.price_per_head == sale.price_per_head == pytest.approx(price)
    assert sale.filled == 1.0 and sale.shortfall == 0.0
    assert purchase.revenue == pytest.approx(2.0 * price)
    if animal_class == "female_grower":
        assert result.months[0].open_does == result.months[-1].total_herd == 1.0
    else:
        assert result.months[0].sales_head == 2.0
        assert result.months[0].sales_revenue == pytest.approx(2.0 * price)
        assert result.months[-1].total_herd == 0.0


@pytest.mark.parametrize("include_chain", [False, True])
def test_ordered_sale_prices_festival_held_males_at_their_actual_older_age(
    include_chain: bool,
) -> None:
    payload = _base()
    payload["sales"] = {
        **dict(payload["sales"]),
        "festival_sale_months": [3],
        "festival_hold_months": 2,
        "eid_price_uplift": 0.2,
    }
    events: list[dict[str, object]] = [
        {
            "month": 1,
            "kind": "purchase",
            "animal_class": "male_grower",
            "count": 2.0,
            "age_months": 8,
        }
    ]
    if include_chain:
        events.append(
            {
                "month": 2,
                "kind": "purchase",
                "animal_class": "male_grower",
                "count": 2.0,
                "age_months": 6,
            }
        )
    events.append({"month": 2, "kind": "sale", "animal_class": "male_grower", "count": 3.0})
    payload["events"] = events
    result = _forecast(payload)
    sale = result.months[1].event_fills[-1]
    assert result.months[0].sales_head == 0.0
    assert result.months[0].total_herd == 2.0
    assert sale.price_per_head == pytest.approx(2400.0 if include_chain else 3000.0)
    assert sale.filled == (3.0 if include_chain else 2.0)
    assert sale.shortfall == (0.0 if include_chain else 1.0)
    assert sale.revenue == pytest.approx(7200.0 if include_chain else 6000.0)
    assert result.months[1].total_herd == (1.0 if include_chain else 0.0)
    assert result.months[2].sales_revenue == pytest.approx(3960.0 if include_chain else 0.0)
    assert result.months[-1].total_herd == 0.0
