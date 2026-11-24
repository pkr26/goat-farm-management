"""Same-day weight recency follows assigned entry identity, not collection order."""

from datetime import date

import pytest

from app.models import Animal, WeightRecord

MEASURED_ON = date(2026, 10, 5)


def _animal(records: list[WeightRecord]) -> Animal:
    return Animal(
        farm_id=1,
        tag_number="WEIGHT-TIE",
        sex="F",
        source="BORN",
        status="ACTIVE",
        current_bucket="FOUNDATION",
        date_of_birth=date(2025, 1, 1),
        birth_weight=2.6,
        weight_records=records,
        breedings_as_doe=[],
        bucket_moves=[],
    )


def test_same_day_weight_tie_resolves_to_the_higher_assigned_id_not_the_first_row() -> None:
    """Both rows are persisted, so the tie-break must read the real identities.

    The ``w.id or 0`` fallback keeps 11 and 12 distinct. Degrading it to
    ``w.id and 0`` collapses every assigned identity to 0, which makes the
    same-day keys compare equal and leaves ``max`` returning whichever row
    happens to sit first in the collection — here the older 25.0 kg reading.
    """
    earlier = WeightRecord(id=11, date=MEASURED_ON, weight_kg=25.0)
    later = WeightRecord(id=12, date=MEASURED_ON, weight_kg=31.0)
    # Lower identity deliberately first: the collection must not decide recency.
    animal = _animal([earlier, later])
    assert [record.id for record in animal.weight_records] == [11, 12]
    assert earlier.date == later.date

    try:
        latest = animal.latest_weight
        measured = animal.latest_weight_kg
        as_of = animal.latest_weight_kg_on(MEASURED_ON)
    except TypeError as error:
        # A persisted same-day pair must stay orderable; keep the failure
        # attributable instead of letting the comparison error escape.
        pytest.fail(f"Same-day persisted weight records must remain sortable: {error}")

    assert latest is not None
    assert latest.id == 12
    assert latest is later
    assert measured == 31.0
    assert as_of == 31.0
