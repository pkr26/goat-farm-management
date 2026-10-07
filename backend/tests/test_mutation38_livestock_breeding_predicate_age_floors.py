"""Canonical service eligibility uses the literal twelve-calendar-month floor."""

from datetime import date

import pytest

from app.models import Animal
from app.services.breeding import is_breeding_candidate, is_buck_breeding_candidate


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    """Native calendar/scalar readiness predicates have no database effects."""


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    """Each case owns a fresh ordinary ORM argument with literal readiness facts."""


@pytest.mark.parametrize("sex", ["F", "M"])
@pytest.mark.parametrize(
    ("dob", "expected"),
    [(date(2025, 10, 5), True), (date(2025, 10, 6), True), (date(2025, 10, 7), False)],
    ids=["after-anniversary", "on-anniversary", "before-anniversary"],
)
def test_canonical_service_predicates_accept_exact_twelve_month_calendar_anniversary(
    sex: str, dob: date, expected: bool
) -> None:
    animal = Animal(
        tag_number=f"NATIVE-AGE-{sex}",
        farm_id=1,
        sex=sex,
        source="PURCHASED",
        status="ACTIVE",
        current_bucket="FOUNDATION",
        date_of_birth=dob,
        movement_restricted=False,
        suspected_scheduled_disease=False,
    )
    reference = date(2026, 10, 6)
    if sex == "F":
        actual = is_breeding_candidate(
            animal,
            latest_weight_kg=26.0,
            has_open_breeding=False,
            reference_date=reference,
        )
    else:
        actual = is_buck_breeding_candidate(animal, latest_weight_kg=30.0, reference_date=reference)
    assert actual is expected
