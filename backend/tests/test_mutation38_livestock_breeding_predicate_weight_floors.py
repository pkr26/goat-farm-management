"""The canonical write predicates accept literal doe22/sire25 kilogram floors."""

from datetime import date

import pytest

from app.models import Animal
from app.services.breeding import is_breeding_candidate, is_buck_breeding_candidate


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    """Native scalar-fact predicates do not read or write PostgreSQL."""


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    """Every case owns a valid fresh ORM animal and literal readiness facts."""


@pytest.mark.parametrize(
    ("sex", "weight", "expected"),
    [
        ("F", 22.0, True),
        ("F", 21.99, False),
        ("F", None, False),
        ("M", 25.0, True),
        ("M", 24.99, False),
        ("M", None, False),
    ],
    ids=["doe-floor", "doe-below", "doe-unweighed", "sire-floor", "sire-below", "sire-unweighed"],
)
def test_canonical_service_predicates_preserve_inclusive_literal_weight_floors(
    sex: str, weight: float | None, expected: bool
) -> None:
    animal = Animal(
        tag_number=f"NATIVE-READINESS-{sex}",
        sex=sex,
        source="PURCHASED",
        status="ACTIVE",
        current_bucket="FOUNDATION",
        date_of_birth=date(2024, 1, 1),
        movement_restricted=False,
        suspected_scheduled_disease=False,
    )
    reference = date(2026, 10, 6)
    if sex == "F":
        actual = is_breeding_candidate(
            animal,
            latest_weight_kg=weight,
            has_open_breeding=False,
            reference_date=reference,
        )
    else:
        actual = is_buck_breeding_candidate(
            animal, latest_weight_kg=weight, reference_date=reference
        )
    assert actual is expected
