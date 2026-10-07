"""Real finishing cohorts follow authored hold windows and recurring calendar months.

The legacy examples intentionally use a forecast outside the embedded lunar
calendar. Their explicitly configured Gregorian sale month remains the user's
legacy instruction; these tests make no claim about future observed religious dates.
"""

import json
from typing import Any

import pytest

from app.simulation.assumptions import SimulationAssumptions
from app.simulation.engine import run_simulation
from app.simulation.results import SimulationResult


def _payload() -> dict[str, Any]:
    return {
        "meta": {"horizon_months": 24, "start_year_month": "2051-02"},
        "herd": {"does": 0, "bucks": 0, "male_growers": 1, "auto_purchase_bucks": False},
        "growth": {"sale_age_months": 8},
        "reproduction": {"conception_rate": 0.0},
        "mortality": {"kid_pre_weaning": 0.0, "kid_post_weaning": 0.0, "grower": 0.0, "adult": 0.0},
        "culling": {"doe_cull_rate_annual": 0.0, "buck_rotation_years": 10},
        "sales": {
            "eid_price_uplift": 1.0,
            "monthly_meat_price_multipliers": [1.0] * 12,
            "annual_livestock_price_growth_rate": 0.0,
        },
    }


def _forecast(payload: dict[str, Any]) -> SimulationResult:
    assumptions = SimulationAssumptions.model_validate_json(json.dumps(payload))
    try:
        result = run_simulation(assumptions, with_break_even=False)
        json.dumps(result.model_dump(mode="json"), allow_nan=False)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as error:
        pytest.fail(f"A valid finishing-cohort calendar must finish and conserve head: {error}")
    assert len(result.months) == 24
    previous = 1.0
    for row in result.months:
        assert row.births == row.deaths == row.culls_head == 0.0
        assert row.total_herd == pytest.approx(previous + row.purchases_head - row.sales_head)
        previous = row.total_herd
    assert previous == 0.0
    return result


def test_recurring_july_sale_month_repeats_after_the_gregorian_year_wrap() -> None:
    payload = _payload()
    payload["sales"].update({"eid_month": 7, "festival_hold_months": 12})
    payload["events"] = [
        {
            "month": 12,
            "kind": "purchase",
            "animal_class": "male_grower",
            "count": 1.0,
            "age_months": 7,
        }
    ]
    result = _forecast(payload)
    assert [(row.month, row.sales_head) for row in result.months if row.sales_head] == [
        (6, 1.0),
        (18, 1.0),
    ]
    assert result.months[11].purchases_head == 1.0


def test_recurring_january_sale_month_is_enabled_by_its_first_calendar_position() -> None:
    payload = _payload()
    payload["meta"]["start_year_month"] = "2051-12"
    payload["sales"].update({"eid_month": 1, "festival_hold_months": 12})
    result = _forecast(payload)
    assert [(row.month, row.sales_head) for row in result.months if row.sales_head] == [(2, 1.0)]


def test_explicit_sale_calendar_admits_a_full_twelve_month_holding_window() -> None:
    payload = _payload()
    payload["sales"].update({"festival_sale_months": [13], "festival_hold_months": 12})
    result = _forecast(payload)
    assert [(row.month, row.sales_head) for row in result.months if row.sales_head] == [(13, 1.0)]


@pytest.mark.parametrize(
    "festival,expected_sale",
    [(3, 3), (4, 1)],
    ids=["inside-default-window", "beyond-default-window"],
)
def test_omitted_hold_setting_keeps_the_reference_two_month_finishing_policy(
    festival: int, expected_sale: int
) -> None:
    payload = _payload()
    payload["sales"]["festival_sale_months"] = [festival]
    result = _forecast(payload)
    assert [(row.month, row.sales_head) for row in result.months if row.sales_head] == [
        (expected_sale, 1.0)
    ]
