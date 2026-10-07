"""A mature, healthy doe remains ineligible while her cohort awaits release."""

from datetime import date

import pytest

from app.models import Animal, WeightRecord


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    """The native model predicate performs no database I/O."""


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    """These valid pending model facts are never flushed."""


@pytest.mark.parametrize(
    ("bucket", "eligible"),
    [("QUARANTINE", False), ("FOUNDATION", True), ("BREEDING", True)],
    ids=["awaiting-quarantine-release", "first-service", "re-service"],
)
def test_doe_eligibility_requires_a_released_cohort_even_at_the_clinical_floors(
    bucket: str, eligible: bool
) -> None:
    reference = date(2026, 10, 5)
    animal = Animal(
        tag_number="MATURE-COHORT-GATE",
        sex="F",
        source="PURCHASED",
        status="ACTIVE",
        current_bucket=bucket,
        date_of_birth=date(2025, 10, 5),
        movement_restricted=False,
        suspected_scheduled_disease=False,
        breedings_as_doe=[],
        weight_records=[WeightRecord(date=reference, weight_kg=22.0)],
    )
    assert animal.age_months_on(reference) == 12
    assert animal.latest_weight_kg_on(reference) == 22.0
    assert animal.is_currently_pregnant is False
    assert animal.is_breeding_eligible_on(reference) is eligible
