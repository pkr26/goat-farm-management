"""Factual lifecycle guards and synchronous ORM movement state changes."""

from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Animal, BucketMove
from app.services.animals import bucket_transition_error, move_animal, require_bucket_transition


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    """The pure guard and pending ORM state contracts do not flush to SQL."""


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    """Every case owns a fresh unbound session and ordinary valid model rows."""


def _doe(*, movement_hold: bool = False, disease_hold: bool = False) -> Animal:
    return Animal(
        id=123,
        farm_id=71,
        tag_number="NATIVE-TRANSITION",
        sex="F",
        source="PURCHASED",
        status="ACTIVE",
        current_bucket="BREEDING",
        date_of_birth=date(2024, 1, 1),
        movement_restricted=movement_hold,
        suspected_scheduled_disease=disease_hold,
    )


@pytest.mark.parametrize("hold", ["movement", "disease"])
def test_native_guard_defaults_require_explicit_reclassification_during_each_hold(
    hold: str,
) -> None:
    doe = _doe(movement_hold=hold == "movement", disease_hold=hold == "disease")
    # An authoritative ultrasound can explicitly reclassify without clearing
    # a physical hold; the ordinary default must keep the factual fence.
    error = bucket_transition_error(
        doe,
        "PREGNANCY_EARLY",
        context="ultrasound",
        reference_date=date(2026, 10, 6),
        facts=(26.0, True),
    )
    assert error == "NATIVE-TRANSITION has a recorded movement restriction or disease hold"
    assert (
        bucket_transition_error(
            doe,
            "PREGNANCY_EARLY",
            context="ultrasound",
            reference_date=date(2026, 10, 6),
            facts=(26.0, True),
            allow_restricted_reclassification=True,
        )
        is None
    )


@pytest.mark.parametrize("hold", ["movement", "disease"])
def test_native_required_transition_keeps_the_default_hold_fence(hold: str) -> None:
    doe = _doe(movement_hold=hold == "movement", disease_hold=hold == "disease")
    with pytest.raises(ValueError, match="recorded movement restriction or disease hold"):
        require_bucket_transition(
            doe,
            "PREGNANCY_EARLY",
            context="ultrasound",
            reference_date=date(2026, 10, 6),
            facts=(26.0, True),
        )
    require_bucket_transition(
        doe,
        "PREGNANCY_EARLY",
        context="ultrasound",
        reference_date=date(2026, 10, 6),
        facts=(26.0, True),
        allow_restricted_reclassification=True,
    )


@pytest.mark.parametrize("hold", ["movement", "disease"])
async def test_native_move_keeps_held_animals_unchanged_without_explicit_opt_in(hold: str) -> None:
    doe = _doe(movement_hold=hold == "movement", disease_hold=hold == "disease")
    async with AsyncSession() as db:
        move_animal(
            db,
            doe,
            "PREGNANCY_EARLY",
            context="ultrasound",
            reference_date=date(2026, 10, 6),
            facts=(26.0, True),
        )
        assert doe.current_bucket == "BREEDING"
        assert not list(db.new)
        move_animal(
            db,
            doe,
            "PREGNANCY_EARLY",
            context="ultrasound",
            reference_date=date(2026, 10, 6),
            facts=(26.0, True),
            allow_restricted_reclassification=True,
        )
        assert doe.current_bucket == "PREGNANCY_EARLY"
        moves = [row for row in db.new if isinstance(row, BucketMove)]
        assert len(moves) == 1
        assert moves[0].animal_id == 123
        assert moves[0].from_bucket == "BREEDING"
        assert moves[0].to_bucket == "PREGNANCY_EARLY"


@pytest.mark.parametrize(("sex", "weight"), [("F", 22.0), ("M", 25.0)])
def test_native_breeding_guard_accepts_the_literal_doe_and_sire_weight_floors(
    sex: str, weight: float
) -> None:
    animal = _doe()
    animal.sex = sex
    animal.current_bucket = "FOUNDATION"
    assert (
        bucket_transition_error(
            animal, "BREEDING", reference_date=date(2026, 10, 6), facts=(weight, False)
        )
        is None
    )


def test_same_day_rest_guard_reports_zero_completed_days_and_exact_earliest_date() -> None:
    doe = _doe()
    doe.current_bucket = "RESTING"
    error = bucket_transition_error(
        doe,
        "BREEDING",
        reference_date=date(2026, 10, 6),
        facts=(26.0, False),
        resting_since=date(2026, 10, 6),
    )
    assert error == (
        "NATIVE-TRANSITION has been in RESTING for 0 of the 10-day rest-and-flush window "
        "before re-entering BREEDING — earliest re-entry is 2026-10-16"
    )


async def test_native_move_retains_the_supplied_audit_reason_in_pending_history() -> None:
    doe = _doe()
    async with AsyncSession() as db:
        move_animal(
            db,
            doe,
            "RESTING",
            reason="Reviewed stock correction",
            context="history_override",
            reference_date=date(2026, 10, 6),
        )
        assert doe.current_bucket == "RESTING"
        moves = [row for row in db.new if isinstance(row, BucketMove)]
        assert len(moves) == 1
        assert moves[0].reason == "Reviewed stock correction"
        assert moves[0].effective_date == date(2026, 10, 6)
