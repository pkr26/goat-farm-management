"""Focused livestock oracles for generic mutations and newly included model methods."""

from datetime import date, datetime, timedelta

import httpx
import pytest

from app.db import get_sessionmaker
from app.models import Animal, BucketMove, WeightRecord
from app.models.enums import FeedingShift
from app.services.chronology import (
    require_purchase_before_recorded_facts,
    require_status_after_recorded_facts,
)
from app.services.feeding import _shift_quantities
from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_extended import make_animal


@pytest.mark.parametrize(
    ("daily", "morning", "afternoon", "night"),
    [(0.053, 0.021, 0.011, 0.021), (0.106, 0.043, 0.021, 0.042)],
)
def test_shift_remainder_is_relative_to_the_100_percent_whole(
    daily: float, morning: float, afternoon: float, night: float
) -> None:
    # 53 g: 21.2 / 10.6 / 21.2 g; the remaining gram goes to afternoon.
    # 106 g: 42.4 / 21.2 / 42.4 g; the tie goes to morning before night.
    assert _shift_quantities(daily) == {
        FeedingShift.MORNING: morning,
        FeedingShift.AFTERNOON: afternoon,
        FeedingShift.NIGHT: night,
    }


def test_same_timestamp_bucket_history_uses_latest_id_and_its_effective_date() -> None:
    audit_time = datetime(2026, 10, 5, 23, 0)
    earlier = BucketMove(id=1, moved_at=audit_time, effective_date=date(2026, 10, 3))
    correction = BucketMove(id=2, moved_at=audit_time, effective_date=date(2026, 9, 25))
    animal = Animal(bucket_moves=[correction, earlier])
    assert animal.last_bucket_move is correction
    assert animal.days_in_current_bucket_on(date(2026, 10, 5), "Asia/Kolkata") == 10
    assert animal.days_in_current_bucket_on(date(2026, 10, 5), "America/Phoenix") == 10


@pytest.mark.parametrize(
    ("bucket", "eligible"), [("FOUNDATION", True), ("BREEDING", True), ("MALE_KIDS", False)]
)
def test_mature_sire_readiness_and_breeding_housing_are_distinct(
    bucket: str, eligible: bool
) -> None:
    reference = date(2026, 10, 5)
    animal = Animal(
        sex="M",
        status="ACTIVE",
        current_bucket=bucket,
        date_of_birth=date(2025, 10, 5),
        movement_restricted=False,
        suspected_scheduled_disease=False,
        weight_records=[WeightRecord(id=1, date=reference, weight_kg=25.0)],
    )
    assert animal.is_buck_ready_on(reference) is True
    assert animal.is_buck_eligible_on(reference) is eligible


@pytest.mark.parametrize("disqualification", ["DEAD", "MOVEMENT_HOLD", "SUSPECTED_DISEASE"])
def test_sire_model_floor_requires_active_clear_health(disqualification: str) -> None:
    reference = date(2026, 10, 5)
    animal = Animal(
        sex="M",
        status="DEAD" if disqualification == "DEAD" else "ACTIVE",
        current_bucket="BREEDING",
        date_of_birth=date(2025, 10, 5),
        movement_restricted=disqualification == "MOVEMENT_HOLD",
        suspected_scheduled_disease=disqualification == "SUSPECTED_DISEASE",
        weight_records=[WeightRecord(id=1, date=reference, weight_kg=25.0)],
    )
    assert animal.is_buck_ready_on(reference) is False
    assert animal.is_buck_eligible_on(reference) is False


async def test_acquisition_and_terminal_dates_may_equal_the_recorded_fact_date(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    created = await make_animal(client, headers, "CHRONO-EQUAL-DAY")
    fact_date = today() - timedelta(days=3)
    recorded = await client.post(
        "/api/health/events",
        json={
            "scope": "animal",
            "animal_id": created["id"],
            "type": "TREATMENT",
            "date": fact_date.isoformat(),
        },
        headers=headers,
    )
    assert recorded.status_code == 201, recorded.text
    async with get_sessionmaker()() as db:
        animal = await db.get(Animal, created["id"])
        assert animal is not None
        rejected_same_day: list[str] = []
        for check in (require_purchase_before_recorded_facts, require_status_after_recorded_facts):
            try:
                await check(db, animal, fact_date)
            except ValueError as exc:
                rejected_same_day.append(str(exc))
        assert rejected_same_day == [], "A recorded event may share its acquisition or exit day"
        with pytest.raises(ValueError, match="earliest recorded"):
            await require_purchase_before_recorded_facts(db, animal, fact_date + timedelta(days=1))
        with pytest.raises(ValueError, match="latest recorded"):
            await require_status_after_recorded_facts(db, animal, fact_date - timedelta(days=1))
