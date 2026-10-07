"""A valid held Animal makes the synchronous movement helper a silent no-op."""

from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Animal, BucketMove
from app.services.animals import move_animal


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    """This native session-state contract performs no database I/O."""


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    """No history or Animal row is flushed by these pending-state probes."""


@pytest.mark.parametrize("disease", [False, True], ids=["movement-only", "disease-hold"])
async def test_a_valid_movement_hold_completes_without_relocating_or_recording_history(
    disease: bool,
) -> None:
    # Both cases satisfy the actual hold constraints: a reason accompanies a
    # movement restriction; a suspected scheduled disease also has that hold
    # and its disease label. Probe the documented synchronous pending stage.
    animal = Animal(
        tag_number="PENDING-MOVE-HOLD",
        sex="F",
        status="ACTIVE",
        current_bucket="FOUNDATION",
        date_of_birth=date(2024, 1, 1),
        movement_restricted=True,
        restriction_reason="Recorded movement restriction",
        suspected_scheduled_disease=disease,
        suspected_disease="PPR" if disease else None,
        restriction_version=1,
    )
    async with AsyncSession(autoflush=False) as db:
        db.add(animal)
        assert list(db.new) == [animal]
        try:
            move_animal(
                db,
                animal,
                "BREEDING",
                reference_date=date(2026, 10, 5),
                facts=(30.0, False),
            )
        except ValueError as exc:
            pytest.fail(f"The valid held-animal no-op must complete without an error: {exc!r}")
        assert animal.current_bucket == "FOUNDATION"
        assert list(db.new) == [animal]
        assert not any(isinstance(row, BucketMove) for row in db.new)
