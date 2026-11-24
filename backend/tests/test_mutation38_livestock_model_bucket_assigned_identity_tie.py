"""Same-instant move recency follows assigned move identity, not collection order."""

from datetime import UTC, date, datetime

import pytest

from app.models import Animal, BucketMove

MOVED_AT = datetime(2026, 10, 5, 4, 30, tzinfo=UTC)
REFERENCE_DATE = date(2026, 10, 15)


def _animal(moves: list[BucketMove]) -> Animal:
    return Animal(
        farm_id=1,
        tag_number="MOVE-TIE",
        sex="F",
        source="BORN",
        status="ACTIVE",
        current_bucket="BREEDING",
        date_of_birth=date(2024, 1, 1),
        birth_weight=2.6,
        weight_records=[],
        breedings_as_doe=[],
        bucket_moves=moves,
    )


def test_same_instant_move_tie_resolves_to_the_higher_assigned_id_not_the_first_row() -> None:
    """Both moves are persisted, so the tie-break must read the real identities.

    The ``m.id or 0`` fallback keeps 7 and 8 distinct. Degrading it to
    ``m.id and 0`` collapses every assigned identity to 0, which makes the
    same-instant keys compare equal and leaves ``max`` returning whichever row
    happens to sit first in the collection — here the superseded FOUNDATION
    placement, which also backdates the elapsed bucket days.
    """
    earlier = BucketMove(
        id=7,
        animal_id=1,
        from_bucket=None,
        to_bucket="FOUNDATION",
        moved_at=MOVED_AT,
        effective_date=date(2026, 9, 1),
    )
    later = BucketMove(
        id=8,
        animal_id=1,
        from_bucket="FOUNDATION",
        to_bucket="BREEDING",
        moved_at=MOVED_AT,
        effective_date=date(2026, 10, 5),
    )
    # Lower identity deliberately first: the collection must not decide recency.
    animal = _animal([earlier, later])
    assert [move.id for move in animal.bucket_moves] == [7, 8]
    assert earlier.moved_at == later.moved_at

    try:
        latest = animal.last_bucket_move
        elapsed = animal.days_in_current_bucket_on(REFERENCE_DATE, "Asia/Kolkata")
    except TypeError as error:
        # A same-instant persisted pair must stay orderable; keep the failure
        # attributable instead of letting the comparison error escape.
        pytest.fail(f"Same-instant persisted bucket moves must remain sortable: {error}")

    assert latest is not None
    assert latest.id == 8
    assert latest is later
    assert latest.to_bucket == "BREEDING"
    assert elapsed == 10
