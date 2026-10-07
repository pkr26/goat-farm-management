"""The native housing gate admits a healthy sire at twelve calendar months."""

from datetime import date

import pytest

from app.models import Animal
from app.services.animals import bucket_transition_error


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    """This pure factual guard consumes dates and explicit measured weight."""


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    """The ordinary unsaved model does not open a database connection."""


@pytest.mark.parametrize(
    ("dob", "eligible"),
    [(date(2025, 10, 6), True), (date(2025, 10, 7), False)],
    ids=["twelve-calendar-months", "one-day-short"],
)
def test_native_sire_housing_accepts_twelve_month_boundary(dob: date, eligible: bool) -> None:
    sire = Animal(
        farm_id=71,
        tag_number="SIRE-AGE-BOUNDARY",
        sex="M",
        status="ACTIVE",
        current_bucket="FOUNDATION",
        date_of_birth=dob,
        movement_restricted=False,
        suspected_scheduled_disease=False,
    )
    error = bucket_transition_error(
        sire, "BREEDING", reference_date=date(2026, 10, 6), facts=(25.0, False)
    )
    if eligible:
        assert error is None
    else:
        assert error == "SIRE-AGE-BOUNDARY is not eligible to enter BREEDING"
