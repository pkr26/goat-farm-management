"""Actual ordered grower trades retain consistent quotes and depleted-stock completion."""

import json

import pytest

from app.simulation.assumptions import SimulationAssumptions
from app.simulation.engine import run_simulation


@pytest.mark.parametrize("animal_class", ["female_grower", "male_grower"])
def test_actual_empty_grower_quote_matches_same_month_default_purchase_and_filled_sale(
    animal_class: str,
) -> None:
    # The first order has a genuine stock shortfall. A subsequent default-age
    # purchase and sale expose the actual same-class placement price, so its
    # quoted price can be compared without prescribing an internal midpoint.
    payload = {
        "meta": {"horizon_months": 12, "start_year_month": "2026-01"},
        "herd": {"does": 0, "bucks": 0, "auto_purchase_bucks": False},
        "mortality": {"kid_pre_weaning": 0.0, "kid_post_weaning": 0.0, "grower": 0.0, "adult": 0.0},
        "events": [
            {"month": 1, "kind": kind, "animal_class": animal_class, "count": 1.0}
            for kind in ("sale", "purchase", "sale")
        ],
    }
    assumptions = SimulationAssumptions.model_validate_json(json.dumps(payload))
    try:
        result = run_simulation(assumptions, with_break_even=False)
        json.dumps(result.model_dump(mode="json"), allow_nan=False)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as exc:
        pytest.fail(f"Supported actual ordered grower trades must complete: {exc}")
    fills = result.months[0].event_fills
    assert len(fills) == 3
    assert [fill.kind for fill in fills] == ["sale", "purchase", "sale"]
    assert [fill.filled for fill in fills] == [0.0, 1.0, 1.0]
    assert [fill.shortfall for fill in fills] == [1.0, 0.0, 0.0]
    assert fills[0].price_per_head > 0.0
    assert fills[0].price_per_head == pytest.approx(fills[1].price_per_head)
    assert fills[2].price_per_head == pytest.approx(fills[1].price_per_head)
    assert [fill.revenue for fill in fills] == pytest.approx(
        [0.0, fills[1].price_per_head, fills[1].price_per_head]
    )
    assert result.months[0].purchases_head == 1.0 and result.months[0].sales_head == 1.0
    assert all(row.total_herd == 0.0 and row.deaths == 0.0 for row in result.months)


def test_actual_second_order_after_emptying_the_held_male_pen_has_a_clean_stock_shortfall() -> None:
    payload = {
        "meta": {"horizon_months": 12, "start_year_month": "2026-01"},
        "herd": {"does": 0, "bucks": 0, "male_growers": 1, "auto_purchase_bucks": False},
        "growth": {"sale_age_months": 8},
        "mortality": {"kid_pre_weaning": 0.0, "kid_post_weaning": 0.0, "grower": 0.0, "adult": 0.0},
        "sales": {"festival_sale_months": [4], "festival_hold_months": 3},
        "events": [
            {"month": 2, "kind": "sale", "animal_class": "male_grower", "count": 1.0},
            {"month": 2, "kind": "sale", "animal_class": "male_grower", "count": 1.0},
        ],
    }
    assumptions = SimulationAssumptions.model_validate_json(json.dumps(payload))
    try:
        result = run_simulation(assumptions, with_break_even=False)
        json.dumps(result.model_dump(mode="json"), allow_nan=False)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as exc:
        pytest.fail(f"Supported actual depleted-pen orders must complete: {exc}")
    assert result.months[0].total_herd == 1.0 and result.months[0].sales_head == 0.0
    fills = result.months[1].event_fills
    assert len(fills) == 2
    assert [fill.filled for fill in fills] == [1.0, 0.0]
    assert [fill.shortfall for fill in fills] == [0.0, 1.0]
    assert fills[0].revenue > 0.0 and fills[1].revenue == 0.0
    assert result.months[1].sales_head == 1.0
    assert all(row.total_herd == 0.0 for row in result.months[1:])
    previous = 1.0
    for row in result.months:
        assert row.total_herd == pytest.approx(
            previous
            + row.births
            + row.purchases_head
            - row.sales_head
            - row.culls_head
            - row.deaths
        )
        previous = row.total_herd
