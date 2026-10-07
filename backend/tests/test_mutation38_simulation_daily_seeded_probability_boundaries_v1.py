"""Supported finite probability boundaries retain the real seeded animal outcome.

All requests pass through ordinary JSON validation and the unmodified native
engine. Random is used only to calculate the finite input probability for the
documented deterministic phase schedule; the engine owns its own real stream.
The probability of an event is the open interval [0, p), so equality is excluded.
"""

import json
from random import Random

import pytest

from app.models.species import GOAT_PROFILE
from app.simulation.daily_ops import DailyOpsInput, DailyOpsResult, run_daily_ops


def _draws(seed: int, count: int) -> list[float]:
    stream = Random(seed)
    return [stream.random() for _ in range(count)]


def _run(animals: list[dict[str, object]], *, seed: int = 37, **params: object) -> DailyOpsResult:
    policy: dict[str, object] = {
        "conception_rate": 1.0,
        "litter_size_mean": 1.0,
        "female_fraction_at_birth": 0.0,
        "stillbirth_rate": 0.0,
        "abortion_rate": 0.0,
        "kid_pre_weaning_mortality": 0.0,
        "kid_post_weaning_mortality": 0.0,
        "grower_annual_mortality": 0.0,
        "adult_annual_mortality": 0.0,
    }
    policy.update(params)
    payload = DailyOpsInput.model_validate_json(
        json.dumps(
            {
                "start_date": "2026-01-01",
                "horizon_days": 7,
                "seed": seed,
                "animals": animals,
                "params": policy,
            }
        )
    )
    try:
        return run_daily_ops(payload)
    except (ArithmeticError, AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
        pytest.fail(f"A supported native seeded request must complete: {exc}")


def _due_doe() -> list[dict[str, object]]:
    return [
        {
            "tag": "D1",
            "sex": "F",
            "bucket": "DELIVERY",
            "age_months": 24,
            "bred_days_ago": GOAT_PROFILE.gestation_days - 1,
        }
    ]


def test_equal_scan_probability_excludes_conception_in_the_native_seeded_run() -> None:
    # Day one consumes each starter's mortality draw even at zero hazard.
    # The next day's scan is therefore the third native random draw.
    probability = _draws(37, 3)[-1]
    result = _run(
        [
            {"tag": "B1", "sex": "M", "bucket": "BREEDING", "age_months": 24},
            {
                "tag": "D1",
                "sex": "F",
                "bucket": "BREEDING",
                "age_months": 24,
                "bred_days_ago": GOAT_PROFILE.pregnancy_check_after_service_days - 1,
            },
        ],
        conception_rate=probability,
    )
    assert result.totals.conceptions == 0
    assert result.totals.failed_services == 1
    assert any("scan NEGATIVE" in task.headline for task in result.days[1].tasks)


def test_equal_female_probability_produces_a_male_kid_in_the_native_seeded_run() -> None:
    # The first day has abortion then mortality; day two has litter then sex.
    result = _run(_due_doe(), female_fraction_at_birth=_draws(37, 4)[-1])
    birth = result.days[1].births[0]
    assert len(birth.kids) == 1
    assert birth.kids[0].sex == "M"
    assert birth.kids[0].status == "ALIVE"


def test_equal_stillbirth_probability_retains_the_live_native_birth() -> None:
    # The kid's stillbirth draw immediately follows its litter and sex draws.
    result = _run(_due_doe(), stillbirth_rate=_draws(37, 5)[-1])
    birth = result.days[1].births[0]
    assert len(birth.kids) == birth.live_kids == 1
    assert birth.kids[0].status == "ALIVE"
    assert result.totals.kids_born_alive == 1
    assert result.totals.kids_born_dead == 0


def test_equal_litter_cdf_probability_selects_the_next_supported_litter_size() -> None:
    # Mean 2-p gives one-kid mass p and two-kid mass 1-p. Equality lies in
    # the second interval, exactly as all other stochastic event intervals do.
    probability = _draws(37, 3)[-1]
    mean = 2.0 - probability
    assert 2.0 - mean == probability
    result = _run(_due_doe(), litter_size_mean=mean)
    birth = result.days[1].births[0]
    assert len(birth.kids) == birth.live_kids == 2
    assert [kid.tag for kid in birth.kids] == ["D1-1", "D1-2"]


def test_equal_daily_abortion_probability_retains_the_first_day_pregnancy() -> None:
    daily_probability = _draws(31, 1)[0]
    phase_days = GOAT_PROFILE.gestation_days - GOAT_PROFILE.pregnancy_check_after_service_days
    phase_probability = 1.0 - (1.0 - daily_probability) ** phase_days
    assert 1.0 - (1.0 - phase_probability) ** (1.0 / phase_days) == daily_probability
    result = _run(
        [
            {
                "tag": "D1",
                "sex": "F",
                "bucket": "PREGNANCY_EARLY",
                "age_months": 24,
                "bred_days_ago": GOAT_PROFILE.pregnancy_check_after_service_days,
            }
        ],
        seed=31,
        abortion_rate=phase_probability,
    )
    assert not any(move.context == "abortion" for move in result.days[0].moves)
    assert sum(row.heads for row in result.days[0].occupancy) == 1


def test_equal_daily_mortality_probability_retains_the_first_day_animal() -> None:
    daily_probability = _draws(31, 1)[0]
    annual_probability = 1.0 - (1.0 - daily_probability) ** 365
    assert 1.0 - (1.0 - annual_probability) ** (1.0 / 365) == daily_probability
    result = _run(
        [{"tag": "M1", "sex": "M", "bucket": "FOUNDATION", "age_months": 0}],
        seed=31,
        adult_annual_mortality=annual_probability,
    )
    assert result.days[0].exits == []
    assert sum(row.heads for row in result.days[0].occupancy) == 1
