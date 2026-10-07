"""Authored festival calendars govern both public finishing dates and prices.

The forecast lies beyond the embedded calendar so unresolved None exercises
the documented saved-scenario Gregorian fallback. No observed festival date
or actual future religious calendar is asserted by these authored dates.
"""

import json
from copy import deepcopy
from typing import Any

import pytest

from app.simulation.assumptions import SimulationAssumptions
from app.simulation.engine import run_simulation


@pytest.mark.parametrize(
    "authored_sales,resolved_months,sale_month",
    [
        pytest.param({"festival_sale_months": []}, [], 1, id="explicit-empty-months"),
        pytest.param(
            {"festival_sale_months": [3], "festival_date_overrides": []},
            [],
            1,
            id="empty-dates-replace-months",
        ),
        pytest.param({"festival_sale_months": [13]}, [], 1, id="pruned-months-stay-disabled"),
        pytest.param({"festival_sale_months": [3]}, [3], 3, id="explicit-months-replace-legacy"),
        pytest.param({"festival_sale_months": [1]}, [1], 1, id="festival-at-finishing-month"),
        pytest.param(
            {"festival_sale_months": [2], "festival_date_overrides": ["2052-02-01"]},
            [3],
            3,
            id="authored-dates-replace-months",
        ),
        pytest.param({"festival_sale_months": None}, None, 2, id="unresolved-legacy-calendar"),
        pytest.param(
            {"festival_sale_months": None, "eid_month": 0},
            None,
            1,
            id="unresolved-calendar-disabled",
        ),
        pytest.param(
            {"festival_sale_months": [2], "festival_date_overrides": ["2052-12-01"]},
            [],
            1,
            id="pruned-dates-stay-disabled",
        ),
        pytest.param(
            {"festival_sale_months": [], "festival_date_overrides": ["2052-01-01"]},
            [2],
            2,
            id="authoritative-dates-replace-empty-months",
        ),
    ],
)
def test_finishing_hold_uses_the_same_resolved_calendar_as_festival_pricing(
    authored_sales: dict[str, Any], resolved_months: list[int] | None, sale_month: int
) -> None:
    payload: dict[str, Any] = {
        "meta": {"horizon_months": 12, "start_year_month": "2051-12"},
        "herd": {"does": 0, "bucks": 0, "male_growers": 1, "auto_purchase_bucks": False},
        "growth": {"sale_age_months": 8},
        "reproduction": {"conception_rate": 0.0},
        "mortality": {
            "kid_pre_weaning": 0.0,
            "kid_post_weaning": 0.0,
            "grower": 0.0,
            "adult": 0.0,
        },
        "culling": {"doe_cull_rate_annual": 0.0, "buck_rotation_years": 10},
        "sales": {
            "eid_month": 1,
            "eid_price_uplift": 1.0,
            "festival_hold_months": 12,
            "annual_livestock_price_growth_rate": 0.0,
            "monthly_meat_price_multipliers": [1.0] * 12,
            **authored_sales,
        },
    }
    assumptions = SimulationAssumptions.model_validate_json(json.dumps(payload))
    assert assumptions.sales.festival_sale_months == resolved_months
    result = run_simulation(assumptions, with_break_even=False)
    assert [(row.month, row.sales_head) for row in result.months if row.sales_head] == [
        (sale_month, 1.0)
    ]
    assert len(result.months) == assumptions.meta.horizon_months
    previous = 1.0
    for row in result.months:
        assert row.births == row.deaths == row.culls_head == row.purchases_head == 0.0
        assert row.total_herd == pytest.approx(previous - row.sales_head)
        previous = row.total_herd
    assert previous == 0.0
    sale = result.months[sale_month - 1]
    festival_active = bool(resolved_months) or (
        resolved_months is None and assumptions.sales.eid_month > 0
    )
    uplift = 1.0 + assumptions.sales.eid_price_uplift if festival_active else 1.0
    assert sale.meat_price_per_kg == pytest.approx(assumptions.sales.meat_price_per_kg * uplift)
    json.dumps(result.model_dump(mode="json"), allow_nan=False)
    if resolved_months == []:
        # With no active festival, increasing the hold window must not move
        # sales, keep extra physical stock or add a month of feed/market cash.
        immediate_payload = deepcopy(payload)
        immediate_payload["sales"]["festival_hold_months"] = 0
        immediate = run_simulation(
            SimulationAssumptions.model_validate_json(json.dumps(immediate_payload)),
            with_break_even=False,
        )
        assert result.months == immediate.months
