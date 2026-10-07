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


def test_actual_future_sire_and_meat_male_coexist_without_a_sterile_herd_warning() -> None:
    # A male being sold for meat does not prevent a different young foundation
    # sire from maturing and serving an actual eligible doe later in the run.
    payload = DailyOpsInput.model_validate_json(
        json.dumps(
            {
                "start_date": "2026-01-01",
                "horizon_days": 70,
                "seed": 37,
                "animals": [
                    {"tag": "YOUNG-SIRE", "sex": "M", "bucket": "FOUNDATION", "age_months": 11},
                    {"tag": "MEAT-MALE", "sex": "M", "bucket": "MALE_KIDS", "age_months": 8},
                    {"tag": "READY-DOE", "sex": "F", "bucket": "FOUNDATION", "age_months": 24},
                ],
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
        pytest.fail(f"A supported mixed starter herd must complete: {exc}")
    journeys = {journey.tag: journey for journey in result.journeys}
    meat = journeys["MEAT-MALE"]
    sire = journeys["YOUNG-SIRE"]
    doe = journeys["READY-DOE"]
    assert meat.final_status == "SOLD" and meat.exit_day == 1
    assert sire.final_status == "ACTIVE" and sire.final_bucket == "BREEDING"
    assert any(hop.context == "breeding" and hop.to_bucket == "BREEDING" for hop in sire.hops)
    assert doe.final_status == "ACTIVE" and doe.final_bucket == "PREGNANCY_EARLY"
    assert result.totals.services == 1 and result.totals.conceptions == 1
    assert any("No buck stood in BREEDING at the start" in note for note in result.notes)
    assert all("No buck in this herd" not in note for note in result.notes)
    assert all("Home-born males are sold at the meat age" not in note for note in result.notes)
