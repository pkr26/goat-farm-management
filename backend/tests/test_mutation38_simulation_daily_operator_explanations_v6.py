"""Operator explanations and feeding instructions agree with actual native records."""

import json

import pytest

from app.models.constants import SHIFT_SPLIT
from app.models.feed_rules import DRY_ROUGHAGE, GOAT_BUILDING_NAMES
from app.models.species import GOAT_PROFILE
from app.simulation.daily_ops import (
    FEED_STORE,
    DailyOpsInput,
    DailyOpsResult,
    run_daily_ops,
)
from app.simulation.feed import DAYS_PER_MONTH


def _request(animals: list[dict[str, object]], days: int) -> DailyOpsInput:
    return DailyOpsInput.model_validate_json(
        json.dumps(
            {
                "start_date": "2026-01-01",
                "horizon_days": days,
                "seed": 37,
                "animals": animals,
                "params": {
                    "conception_rate": 1.0,
                    "litter_size_mean": 1.0,
                    "stillbirth_rate": 0.0,
                    "abortion_rate": 0.0,
                    "kid_pre_weaning_mortality": 0.0,
                    "kid_post_weaning_mortality": 0.0,
                    "grower_annual_mortality": 0.0,
                    "adult_annual_mortality": 0.0,
                    "male_sale_age_months": 9,
                },
            }
        )
    )


def _complete(payload: DailyOpsInput) -> DailyOpsResult:
    try:
        return run_daily_ops(payload)
    except (ArithmeticError, AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
        pytest.fail(f"An ordinary valid operator daily request must complete: {exc}")


def test_last_day_sales_are_reflected_in_actual_final_head_and_building_explanations() -> None:
    days = round(9 * DAYS_PER_MONTH) - round(8 * DAYS_PER_MONTH) + 1
    # The only animal first enters its ordinary meat-sale window on the final
    # day, making yesterday's occupancy materially different from today's.
    age = 8
    payload = _request([{"tag": "M1", "sex": "M", "bucket": "MALE_KIDS", "age_months": age}], days)
    result = _complete(payload)
    assert result.days[-1].exits[0].kind == "SOLD"
    assert result.days[-1].exits[0].tag == "M1"
    assert sum(row.heads for row in result.days[-2].occupancy) == 1
    assert result.days[-1].occupancy == []
    explanations = {row.key: row for row in result.explanations}
    assert explanations["exits"].figures["head_final"] == 0
    assert explanations["routine"].figures["buildings_occupied_last_day"] == 0
    window = f"{payload.params.male_sale_age_months}–{payload.params.male_sale_age_months + 1}"
    assert f"{window} month meat window" in explanations["exits"].explanation
    assert any(f"{window} month window" in note for note in result.notes)
    building_days = ", ".join(
        f"{GOAT_BUILDING_NAMES[building]} {count}"
        for building, count in result.totals.building_days.items()
    )
    assert f"Building-days of occupancy: {building_days}." in explanations["buildings"].explanation


def test_native_feed_tasks_identify_direct_roughage_and_the_actual_declared_shift_share() -> None:
    payload = _request(
        [
            {
                "tag": "Q1",
                "sex": "F",
                "bucket": "QUARANTINE",
                "age_months": 24,
                "days_in_bucket": 0,
            },
            {"tag": "F1", "sex": "F", "bucket": "FOUNDATION", "age_months": 0},
        ],
        7,
    )
    result = _complete(payload)
    for record in result.days:
        dry_lines = [line for line in record.feeding if line.recipe == DRY_ROUGHAGE]
        assert len(dry_lines) == (1 if record.day <= 3 else 0)
        mixes = [task for task in record.tasks if task.building == FEED_STORE]
        assert any("Direct-fed from dry roughage stock" in task.detail for task in mixes) == bool(
            dry_lines
        )
        assert any("Morning occupancy forecast" in task.detail for task in mixes)
        expected_shares = {round(share * 100) for share in SHIFT_SPLIT.values()}
        deliveries = [
            task for task in record.tasks if task.category == "FEED" and task.building != FEED_STORE
        ]
        assert deliveries
        for task in deliveries:
            assert any(f"× {share}% shift share" in task.detail for share in expected_shares)


def test_actual_due_day_has_the_kidding_watch_even_when_the_doe_started_mid_pregnancy() -> None:
    result = _complete(
        _request(
            [
                {
                    "tag": "D1",
                    "sex": "F",
                    "bucket": "DELIVERY",
                    "age_months": 24,
                    "bred_days_ago": GOAT_PROFILE.gestation_days - 1,
                }
            ],
            7,
        )
    )
    due_day = result.days[1]
    assert len(due_day.births) == 1
    assert any("Kidding due" in task.headline and task.animals == ["D1"] for task in due_day.tasks)
