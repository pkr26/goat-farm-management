"""Actual six-month stock has the grower risk, with independent class-rate controls."""

import json

import pytest

from app.simulation.daily_ops import DailyOpsInput, run_daily_ops


@pytest.mark.parametrize("weaner_rate,grower_rate,dead", [(1.0, 0.0, False), (0.0, 1.0, True)])
def test_actual_six_month_female_stock_uses_grower_and_not_weaner_mortality(
    weaner_rate: float,
    grower_rate: float,
    dead: bool,
) -> None:
    # Six calendar-model months is the supported transition from the weaner
    # class into grower stock. Opposing certain/zero risks make the class choice
    # observable in the animal's real journey without replacing its random draw.
    payload = DailyOpsInput.model_validate_json(
        json.dumps(
            {
                "start_date": "2026-01-01",
                "horizon_days": 7,
                "seed": 37,
                "animals": [
                    {"tag": "SIX-MONTH-DOE", "sex": "F", "bucket": "FEMALE_KIDS", "age_months": 6}
                ],
                "params": {
                    "conception_rate": 1.0,
                    "stillbirth_rate": 0.0,
                    "abortion_rate": 0.0,
                    "kid_pre_weaning_mortality": 0.0,
                    "kid_post_weaning_mortality": weaner_rate,
                    "grower_annual_mortality": grower_rate,
                    "adult_annual_mortality": 0.0,
                },
            }
        )
    )
    try:
        result = run_daily_ops(payload)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as exc:
        pytest.fail(f"Supported actual six-month stock must complete: {exc}")
    assert result.head_start == 1 and len(result.days) == 7 and len(result.journeys) == 1
    journey = result.journeys[0]
    assert result.totals.deaths == int(dead)
    assert result.totals.sales == 0 and result.totals.culls == 0
    assert journey.final_status == ("DEAD" if dead else "ACTIVE")
    assert journey.exit_kind == ("DEAD" if dead else None)
    assert journey.exit_day == (1 if dead else None)
    assert journey.final_bucket == (None if dead else "FEMALE_KIDS")
    if dead:
        assert "Grower mortality" in journey.exit_reason
        assert len(result.days[0].exits) == 1
    else:
        assert result.days[0].exits == []
