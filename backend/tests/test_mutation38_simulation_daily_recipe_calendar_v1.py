"""Real daily male-grower recipes cross the operational day-90 boundary on the actual date."""

import json
from datetime import timedelta

import pytest

from app.models.feed_rules import recipe_for_context
from app.simulation.daily_ops import DailyOpsInput, run_daily_ops
from app.simulation.feed import DAYS_PER_MONTH


def test_real_male_grower_recipe_changes_on_its_operational_calendar_boundary() -> None:
    input_ = DailyOpsInput.model_validate_json(
        json.dumps(
            {
                "start_date": "2026-01-01",
                "horizon_days": 31,
                "seed": 37,
                "animals": [{"tag": "M1", "sex": "M", "bucket": "MALE_KIDS", "age_months": 2}],
                "params": {
                    "male_sale_age_months": 36,
                    "adult_annual_mortality": 0.0,
                    "kid_pre_weaning_mortality": 0.0,
                    "kid_post_weaning_mortality": 0.0,
                    "grower_annual_mortality": 0.0,
                },
            }
        )
    )
    try:
        result = run_daily_ops(input_)
    except (ArithmeticError, IndexError, KeyError, TypeError, ValueError, RuntimeError) as exc:
        pytest.fail(f"A supported native grower feeding calendar must complete: {exc}")
    dob = input_.start_date - timedelta(days=round(input_.animals[0].age_months * DAYS_PER_MONTH))
    assert len(result.days) == input_.horizon_days
    for record in result.days:
        actual_day = input_.start_date + timedelta(days=record.day - 1)
        expected = recipe_for_context("MALE_KIDS", dob, actual_day, record.day - 1)
        assert len(record.feeding) == 1
        assert record.feeding[0].recipe == expected
    assert result.days[-2].feeding[0].recipe == "LACTATING_60_40"
    assert result.days[-1].feeding[0].recipe == "FATTENING_50_50"
