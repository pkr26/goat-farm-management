"""Actual starter cohorts report whether a sire is standing, developing or unavailable."""

import json

import pytest

from app.simulation.daily_ops import DailyOpsInput, run_daily_ops


@pytest.mark.parametrize(
    "sex,bucket,age,kind",
    [
        ("M", "BREEDING", 12, "standing"),
        ("M", "BREEDING", 11, "future"),
        ("M", "FOUNDATION", 11, "future"),
        ("M", "QUARANTINE", 11, "future"),
        ("M", "MALE_KIDS", 8, "unavailable-home"),
        ("F", "BREEDING", 24, "unavailable"),
    ],
)
def test_real_starter_sire_advice_agrees_with_sex_age_and_supported_bucket(
    sex: str,
    bucket: str,
    age: int,
    kind: str,
) -> None:
    payload = DailyOpsInput.model_validate_json(
        json.dumps(
            {
                "start_date": "2026-01-01",
                "horizon_days": 7,
                "seed": 37,
                "animals": [{"tag": "ONLY", "sex": sex, "bucket": bucket, "age_months": age}],
                "params": {
                    "conception_rate": 1.0,
                    "litter_size_mean": 1.0,
                    "stillbirth_rate": 0.0,
                    "abortion_rate": 0.0,
                    "kid_pre_weaning_mortality": 0.0,
                    "kid_post_weaning_mortality": 0.0,
                    "grower_annual_mortality": 0.0,
                    "adult_annual_mortality": 0.0,
                },
            }
        )
    )
    try:
        result = run_daily_ops(payload)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as exc:
        pytest.fail(f"A supported actual starter cohort must complete: {exc}")
    assert len(result.days) == 7
    assert result.totals.services == 0
    assert any("No buck stood in BREEDING at the start" in n for n in result.notes) is (
        kind == "future"
    )
    assert any("No buck in this herd" in n for n in result.notes) is kind.startswith("unavailable")
    assert any("Home-born males are sold at the meat age" in n for n in result.notes) is (
        kind == "unavailable-home"
    )
