"""Public festival advice describes the prices actually enabled by authored inputs."""

import json
from typing import Any

import pytest

from app.simulation.assumptions import SimulationAssumptions
from app.simulation.engine import run_simulation


@pytest.mark.parametrize(
    "authored_sales,active_months",
    [
        pytest.param({"festival_sale_months": []}, [], id="explicit-empty-months"),
        pytest.param(
            {"festival_sale_months": [3], "festival_date_overrides": []},
            [],
            id="empty-dates-replace-months",
        ),
        pytest.param({"festival_sale_months": [13]}, [], id="pruned-months-stay-disabled"),
        pytest.param({"festival_sale_months": [3]}, [3], id="explicit-months-replace-legacy"),
        pytest.param({"festival_sale_months": [1]}, [1], id="first-simulation-month"),
        pytest.param(
            {"festival_sale_months": [2], "festival_date_overrides": ["2052-02-01"]},
            [3],
            id="authored-dates-replace-months",
        ),
        pytest.param({"festival_sale_months": None}, [2], id="unresolved-legacy-calendar"),
        pytest.param(
            {"festival_sale_months": None, "eid_month": 0},
            [],
            id="unresolved-calendar-disabled",
        ),
        pytest.param(
            {"festival_sale_months": [2], "festival_date_overrides": ["2052-12-01"]},
            [],
            id="pruned-dates-stay-disabled",
        ),
        pytest.param(
            {"festival_sale_months": [], "festival_date_overrides": ["2052-01-01"]},
            [2],
            id="authoritative-dates-replace-empty-months",
        ),
    ],
)
def test_public_festival_figures_match_the_enabled_monthly_price_uplift(
    authored_sales: dict[str, Any], active_months: list[int]
) -> None:
    # Outside embedded coverage, None preserves the user's legacy Gregorian
    # instruction. The dates below are authored inputs, not observed festivals.
    assumptions = SimulationAssumptions.model_validate_json(
        json.dumps(
            {
                "meta": {"horizon_months": 12, "start_year_month": "2051-12"},
                "herd": {
                    "does": 0,
                    "bucks": 0,
                    "male_growers": 1,
                    "auto_purchase_bucks": False,
                },
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
                    "festival_hold_months": 0,
                    "annual_livestock_price_growth_rate": 0.0,
                    "monthly_meat_price_multipliers": [1.0] * 12,
                    **authored_sales,
                },
            }
        )
    )
    result = run_simulation(assumptions, with_break_even=False)
    json.dumps(result.model_dump(mode="json"), allow_nan=False)
    base = assumptions.sales.meat_price_per_kg
    actual_uplift_months = [row.month for row in result.months if row.meat_price_per_kg > base]
    assert actual_uplift_months == active_months
    revenue = next(section for section in result.narrative_report if section.key == "revenue_mix")
    if not actual_uplift_months:
        assert "festival_months" not in revenue.figures
        assert "festival_uplift" not in revenue.figures
    else:
        assert revenue.figures["festival_months"] == ", ".join(map(str, actual_uplift_months))
        assert revenue.figures["festival_uplift"] == pytest.approx(
            result.months[actual_uplift_months[0] - 1].meat_price_per_kg / base - 1.0
        )
