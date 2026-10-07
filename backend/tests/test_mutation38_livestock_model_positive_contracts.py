"""Supported livestock calculations return domain values without raising."""

from datetime import date

import pytest

from app.models import Animal, BreedingRecord, WeightRecord, conception_rate


def _animal(records: list[WeightRecord], birth_weight: float | None = 2.6) -> Animal:
    return Animal(
        farm_id=1,
        tag_number="MODEL-DOE",
        sex="F",
        source="BORN",
        status="ACTIVE",
        current_bucket="FOUNDATION",
        date_of_birth=date(2025, 1, 1),
        birth_weight=birth_weight,
        weight_records=records,
        breedings_as_doe=[],
        bucket_moves=[],
    )


@pytest.mark.parametrize(
    ("measured_weight", "birth_weight", "expected"),
    [(None, 2.6, 2.6), (None, None, None), (26.0, 2.6, 26.0)],
)
def test_latest_weight_returns_measurement_or_birth_weight_without_error(
    measured_weight: float | None, birth_weight: float | None, expected: float | None
) -> None:
    records = (
        [WeightRecord(id=11, date=date(2026, 10, 5), weight_kg=measured_weight)]
        if measured_weight is not None
        else []
    )
    animal = _animal(records, birth_weight)
    try:
        actual = animal.latest_weight_kg
    except AttributeError as error:
        pytest.fail(f"A valid animal's latest weight must be readable: {error}")
    assert actual == expected


@pytest.mark.parametrize("pending_first", [True, False])
def test_as_of_weight_orders_pending_and_persisted_same_day_records_without_error(
    pending_first: bool,
) -> None:
    reference = date(2026, 10, 5)
    pending = WeightRecord(date=reference, weight_kg=30.0)
    persisted = WeightRecord(id=11, date=reference, weight_kg=25.0)
    future = WeightRecord(id=12, date=date(2026, 10, 6), weight_kg=32.0)
    records = [pending, persisted] if pending_first else [persisted, pending]
    animal = _animal([future, *records])
    assert pending.id is None
    try:
        actual = animal.latest_weight_kg_on(reference)
    except TypeError as error:
        pytest.fail(f"Valid pending and persisted weight records must be sortable: {error}")
    assert actual == 25.0


@pytest.mark.parametrize("reference", [date(2026, 10, 5), date(2025, 1, 1)])
def test_unknown_dob_keeps_recorded_birth_weight_readable(reference: date) -> None:
    animal = _animal([])
    animal.date_of_birth = None
    animal.estimated_dob = None
    assert animal.effective_dob is None
    try:
        actual = animal.latest_weight_kg_on(reference)
    except TypeError as error:
        pytest.fail(f"An unknown DOB must not prevent reading recorded birth weight: {error}")
    assert actual == 2.6


@pytest.mark.parametrize(
    ("tag", "name", "expected"),
    [
        ("A-001", None, "A-001"),
        ("A-001", "", "A-001"),
        ("A-001", "Kaveri", "A-001 · Kaveri"),
        ("🐐-1", "ఆకు", "🐐-1 · ఆకు"),
    ],
)
def test_valid_animal_display_name_is_readable(tag: str, name: str | None, expected: str) -> None:
    animal = _animal([])
    animal.tag_number = tag
    animal.name = name
    try:
        actual = animal.display_name
    except TypeError as error:
        pytest.fail(f"A valid animal's display name must be readable: {error}")
    assert actual == expected


@pytest.mark.parametrize(
    ("outcomes", "expected"),
    [
        ([], None),
        (["PENDING", "UNASSESSED"], None),
        (["CONFIRMED_PREGNANT", "ABORTED", "FAILED", "PENDING", "UNASSESSED"], 66.7),
        (["FAILED"], 0.0),
    ],
)
def test_valid_breeding_history_returns_conception_rate_without_error(
    outcomes: list[str], expected: float | None
) -> None:
    records = [
        BreedingRecord(
            farm_id=1,
            doe_id=1,
            buck_id=2,
            breeding_date=date(2026, 7, 1),
            outcome=outcome,
        )
        for outcome in outcomes
    ]
    try:
        actual = conception_rate(records)
    except ZeroDivisionError as error:
        pytest.fail(f"Valid unassessed breeding history must not divide by zero: {error}")
    assert actual == expected
