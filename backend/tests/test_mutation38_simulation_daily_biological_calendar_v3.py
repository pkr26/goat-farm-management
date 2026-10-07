"""Real multiple-animal daily runs retain each animal's independent phase calendar."""

import json
from datetime import timedelta
from math import ceil
from random import Random

import pytest

from app.models.species import GOAT_PROFILE
from app.simulation.daily_ops import DailyOpsInput, DailyOpsResult, run_daily_ops
from app.simulation.feed import DAYS_PER_MONTH


def _animal(tag: str, **changes: object) -> dict[str, object]:
    row: dict[str, object] = {"tag": tag, "sex": "F", "bucket": "FOUNDATION", "age_months": 24}
    row.update(changes)
    return row


def _input(animals: list[dict[str, object]], days: int = 7, **params: object) -> DailyOpsInput:
    policy: dict[str, object] = {
        "conception_rate": 1.0,
        "litter_size_mean": 3.0,
        "stillbirth_rate": 0.0,
        "abortion_rate": 0.0,
        "kid_pre_weaning_mortality": 0.0,
        "kid_post_weaning_mortality": 0.0,
        "grower_annual_mortality": 0.0,
        "adult_annual_mortality": 0.0,
    }
    policy.update(params)
    return DailyOpsInput.model_validate_json(
        json.dumps(
            {
                "start_date": "2026-01-01",
                "horizon_days": days,
                "seed": 37,
                "animals": animals,
                "params": policy,
            }
        )
    )


def _completed(payload: DailyOpsInput) -> DailyOpsResult:
    try:
        return run_daily_ops(payload)
    except (ArithmeticError, AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
        pytest.fail(f"Every supported actual multi-animal daily run must complete: {exc}")


@pytest.mark.parametrize("started", [109, 110, 111, 124, 125, 126])
def test_native_primary_and_booster_calendars_do_not_repeat_prearrival_doses(started: int) -> None:
    payload = _input([_animal("D1", bucket="PREGNANCY_LATE", bred_days_ago=started)])
    result = _completed(payload)
    expected_kidding = payload.start_date + timedelta(days=GOAT_PROFILE.gestation_days - started)
    primary = [task for day in result.days for task in day.tasks if "Primary dose," in task.detail]
    boosters = [
        task for day in result.days for task in day.tasks if "vaccine booster:" in task.headline
    ]
    primary_gate = GOAT_PROFILE.gestation_days - 40
    booster_gate = GOAT_PROFILE.gestation_days - 25
    assert [task.day for task in primary] == (
        [primary_gate - started + 1] if started < primary_gate <= started + 6 else []
    )
    assert [task.day for task in boosters] == (
        [booster_gate - started + 1] if started < booster_gate <= started + 6 else []
    )
    for task in primary:
        assert expected_kidding.isoformat() in task.detail
    for task in boosters:
        assert task.detail == "Booster only — her primary dose pre-dates this run."
    assert not any(task.category == "KIDDING_DUE" for day in result.days for task in day.tasks)


def test_native_stillbirths_complete_each_entire_litter_on_each_dams_own_due_day() -> None:
    result = _completed(
        _input(
            [
                _animal("A1", bucket="DELIVERY", bred_days_ago=148),
                _animal("Z1", bucket="DELIVERY", bred_days_ago=149),
            ],
            stillbirth_rate=1.0,
        )
    )
    births = [birth for day in result.days for birth in day.births]
    assert [(birth.dam_tag, birth.day) for birth in births] == [("Z1", 2), ("A1", 3)]
    assert all(len(birth.kids) == 3 and birth.live_kids == 0 for birth in births)
    assert all(kid.status == "STILLBORN" for birth in births for kid in birth.kids)
    assert result.totals.kids_born_dead == 6
    assert result.totals.kids_born_alive == 0


def test_native_live_kids_wean_individually_and_face_the_actual_postweaning_class() -> None:
    result = _completed(
        _input(
            [
                _animal("A1", bucket="DELIVERY", bred_days_ago=148),
                _animal("Z1", bucket="DELIVERY", bred_days_ago=149),
            ],
            70,
            female_fraction_at_birth=1.0,
            kid_post_weaning_mortality=1.0,
        )
    )
    births = {birth.dam_tag: birth for day in result.days for birth in day.births}
    assert result.totals.kids_born_alive == 6
    for tag, birth in births.items():
        expected_day = birth.day + GOAT_PROFILE.weaning_days
        moves = result.days[expected_day - 1].moves
        assert {
            move.tag
            for move in moves
            if move.context == "weaning" and move.to_bucket == "FEMALE_KIDS"
        } == {kid.tag for kid in birth.kids}
        assert {exit.tag for exit in result.days[expected_day - 1].exits} == {
            kid.tag for kid in birth.kids
        }
        assert any(move.tag == tag and move.to_bucket == "RESTING" for move in moves)
        # A native dam-linked birth is weaned by that dam's schedule. The
        # operator must not be told that these known births are orphan starters.
        for kid in birth.kids:
            move = next(move for move in moves if move.tag == kid.tag)
            assert move.reason == f"Day-{GOAT_PROFILE.weaning_days} weaning"
        assert not any(
            task.headline.startswith("Age wean ") for task in result.days[expected_day - 1].tasks
        )
    assert result.totals.deaths == 6
    assert sum(row.heads for row in result.days[-1].occupancy) == 2


def test_native_waiting_scan_does_do_not_delay_another_does_due_scan_or_expected_date() -> None:
    payload = _input(
        [
            _animal("A1", bucket="BREEDING", bred_days_ago=30),
            _animal("Z1", bucket="BREEDING", bred_days_ago=31),
        ]
    )
    result = _completed(payload)
    scans = [
        (task.animals[0], task.day, task.detail)
        for day in result.days
        for task in day.tasks
        if "scan POSITIVE" in task.headline
    ]
    assert [(tag, day) for tag, day, _ in scans] == [("Z1", 2), ("A1", 3)]
    for tag, _, detail in scans:
        started = 31 if tag == "Z1" else 30
        due = payload.start_date + timedelta(days=GOAT_PROFILE.gestation_days - started)
        assert f"expected kidding {due.isoformat()}" in detail


def test_native_phase_probability_stream_does_not_consume_an_abortion_draw_on_the_due_day() -> None:
    stream = Random(37)
    observed = [stream.random() for _ in range(4)]
    result = _completed(
        _input(
            [_animal("D1", bucket="DELIVERY", bred_days_ago=149)],
            litter_size_mean=1.0,
            female_fraction_at_birth=observed[-1],
        )
    )
    assert len(result.days[1].births[0].kids) == 1
    assert result.days[1].births[0].kids[0].sex == "M"


def test_native_policy_age_equality_culls_and_sells_the_actual_starting_head_on_day_one() -> None:
    result = _completed(
        _input(
            [
                _animal("D1", age_months=25),
                _animal("M1", sex="M", bucket="MALE_KIDS", age_months=25),
            ],
            max_doe_age_months=25,
            male_sale_age_months=25,
        )
    )
    assert {(exit.tag, exit.kind) for exit in result.days[0].exits} == {
        ("D1", "CULLED"),
        ("M1", "SOLD"),
    }
    assert all(not day.occupancy for day in result.days)


def test_native_resting_clock_and_full_sire_capacity_record_every_waiting_doe() -> None:
    result = _completed(
        _input(
            [
                _animal("B1", sex="M", bucket="BREEDING"),
                _animal("A1", bucket="BREEDING"),
                _animal("C1", bucket="BREEDING"),
                _animal("R1", bucket="RESTING", days_in_bucket=29),
            ],
            buck_doe_ratio=1,
        )
    )
    assert result.totals.services == 1
    assert any("Hold C1" in task.headline for task in result.days[0].tasks)
    assert not any(move.tag == "R1" for move in result.days[0].moves)
    assert any(move.tag == "R1" and move.context == "breeding" for move in result.days[1].moves)
    assert any("Hold R1" in task.headline for task in result.days[1].tasks)
    resting_move = next(move for move in result.days[1].moves if move.tag == "R1")
    assert resting_move.reason.startswith("Flush complete (")
    assert "days in RESTING" in resting_move.reason


def test_native_immature_starters_do_not_block_the_other_mature_sire_or_doe() -> None:
    result = _completed(
        _input(
            [
                _animal("A1", sex="M", age_months=11),
                _animal("Z1", sex="M", age_months=24),
                _animal("A2", bucket="BREEDING", age_months=11),
                _animal("Z2", bucket="BREEDING", age_months=24),
            ]
        )
    )
    services = [task for task in result.days[0].tasks if task.headline.startswith("Breed ")]
    assert len(services) == 1
    assert services[0].animals == ["Z2", "Z1"]
    assert result.totals.services == 1


def test_native_heat_wait_does_not_hold_back_a_different_doe_reaching_breeding_age() -> None:
    payload = _input(
        [
            _animal("A1", bucket="BREEDING", bred_days_ago=10),
            _animal("B1", sex="M", bucket="BREEDING"),
            _animal("Z1", age_months=11),
        ],
        40,
        conception_rate=0.0,
        failed_services_before_cull=6,
    )
    result = _completed(payload)
    ready_day = (
        ceil(GOAT_PROFILE.min_breeding_age_months * DAYS_PER_MONTH) - round(11 * DAYS_PER_MONTH) + 1
    )
    services = [
        task for task in result.days[ready_day - 1].tasks if task.headline.startswith("Breed Z1")
    ]
    assert len(services) == 1
    assert services[0].animals == ["Z1", "B1"]
    expected_scan = payload.start_date + timedelta(
        days=ready_day - 1 + GOAT_PROFILE.pregnancy_check_after_service_days
    )
    assert expected_scan.isoformat() in services[0].detail


def test_native_abortion_of_the_first_doe_does_not_skip_another_actual_pregnancy() -> None:
    result = _completed(
        _input(
            [
                _animal("A1", bucket="PREGNANCY_EARLY", bred_days_ago=32),
                _animal("Z1", bucket="PREGNANCY_EARLY", bred_days_ago=32),
            ],
            abortion_rate=1.0,
        )
    )
    assert {move.tag for move in result.days[0].moves if move.context == "abortion"} == {"A1", "Z1"}
