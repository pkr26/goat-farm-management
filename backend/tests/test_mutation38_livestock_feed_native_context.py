"""Native pure ration contexts retain factual age, dependency and supplied as-of dates."""

from datetime import date, timedelta

import pytest

from app.models import Animal
from app.models.feed_rules import (
    creep_band_label,
    creep_daily_kg,
    recipe_age_days,
    recipe_for_context,
)
from app.services.feeding import recipe_for_animal


@pytest.mark.parametrize(
    "days,kg,band",
    [
        (13, 0, None),
        (14, 0.1, "14–30 d"),
        (30, 0.1, "14–30 d"),
        (31, 0.2, "31–45 d"),
        (45, 0.2, "31–45 d"),
        (46, 0.3, "46–60 d"),
        (60, 0.3, "46–60 d"),
        (61, 0, None),
    ],
)
def test_native_creep_ration_retains_the_literal_band_allowance(
    days: int, kg: float, band: str | None
) -> None:
    try:
        amount = creep_daily_kg(days)
        label = creep_band_label(days)
    except Exception as exc:
        pytest.fail(f"A supported age must yield its creep allowance: {exc!r}")
    assert amount == kg and label == band


def test_native_known_age_is_a_calendar_difference_including_the_leap_day() -> None:
    try:
        age = recipe_age_days(date(2020, 1, 1), date(2020, 3, 1))
    except Exception as exc:
        pytest.fail(f"Valid recorded dates must yield a factual age: {exc!r}")
    assert age == 60
    assert recipe_for_context("MALE_KIDS", None, date(2020, 3, 1), 0) == "FATTENING_50_50"


def test_native_recovery_ration_requires_explicit_dependent_kid_context() -> None:
    ref = date(2020, 3, 1)
    assert recipe_for_context("RECOVERY", date(2018, 3, 1), ref, 0) == "LACTATING_60_40"
    assert (
        recipe_for_context("RECOVERY", ref - timedelta(days=30), ref, 0, is_dependent_kid=True)
        == "CREEP"
    )


def test_native_recipe_respects_the_supplied_as_of_date() -> None:
    ref = date(2020, 3, 1)
    animal = Animal(
        tag_number="AS-OF-MALE-KID",
        sex="M",
        source="PURCHASED",
        current_bucket="MALE_KIDS",
        date_of_birth=ref - timedelta(days=60),
    )
    try:
        recipe = recipe_for_animal(animal, ref=ref)
    except Exception as exc:
        pytest.fail(f"The declared valid as-of date must be usable: {exc!r}")
    assert recipe == "LACTATING_60_40"
    assert recipe_for_animal(animal, ref=ref + timedelta(days=31)) == "FATTENING_50_50"
