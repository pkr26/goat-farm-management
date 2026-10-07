"""Native retained lifecycle and positive-service histories remain readable."""

from datetime import date, timedelta

import httpx
from sqlalchemy import Date, cast, func, select

from app.db import get_sessionmaker
from app.models import Animal, BreedingRecord, BucketMove, Farm, WeightRecord

from .conftest import owner_with_farm


async def test_two_real_lifecycle_moves_keep_the_latest_rest_program_readable(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="mutation-dashboard-rest-history@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        day = (
            await db.execute(
                select(cast(func.timezone(Farm.timezone, func.now()), Date)).where(
                    Farm.id == farm_id
                )
            )
        ).scalar_one()
        assert isinstance(day, date)
        animal = Animal(
            farm_id=farm_id,
            tag_number="RETAINED-REST-HISTORY",
            sex="F",
            source="BORN",
            date_of_birth=day - timedelta(days=1200),
            birth_type="SINGLE",
            birth_weight=2.5,
            current_bucket="RESTING",
        )
        db.add(animal)
        await db.flush()
        for previous, following, elapsed in (
            ("DELIVERY", "RECOVERY", 60),
            ("RECOVERY", "RESTING", 45),
        ):
            db.add(
                BucketMove(
                    farm_id=farm_id,
                    animal_id=animal.id,
                    from_bucket=previous,
                    to_bucket=following,
                    effective_date=day - timedelta(days=elapsed),
                    reason="Retained postpartum lifecycle fact",
                )
            )
        db.add(WeightRecord(farm_id=farm_id, animal_id=animal.id, date=day, weight_kg=22.0))
        await db.commit()
        expected_id = animal.id
    response = await client.get("/api/dashboard", headers=owner)
    assert response.status_code == 200, response.text
    document = response.json()
    assert document["suggestions_total"] == 1
    suggestion = document["suggestions"][0]
    assert suggestion["animal"]["id"] == expected_id
    assert suggestion["to"] == "BREEDING"
    assert suggestion["reason"] == "45 days resting (flush done)"


async def test_latest_retained_positive_service_remains_readable_with_an_old_unlinked_birth_history(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="mutation-dashboard-positive-history@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        day = (
            await db.execute(
                select(cast(func.timezone(Farm.timezone, func.now()), Date)).where(
                    Farm.id == farm_id
                )
            )
        ).scalar_one()
        assert isinstance(day, date)
        doe = Animal(
            farm_id=farm_id,
            tag_number="RETAINED-POSITIVE-HISTORY",
            sex="F",
            source="BORN",
            date_of_birth=day - timedelta(days=1200),
            birth_type="SINGLE",
            birth_weight=2.5,
            current_bucket="PREGNANCY_EARLY",
        )
        db.add(doe)
        await db.flush()
        # These are retained positive assessment facts 400 days apart. An old
        # missing KiddingRecord does not claim a second biological pregnancy
        # today, or current-writer admission of rebreeding without closure.
        for elapsed in (500, 100):
            service = day - timedelta(days=elapsed)
            db.add(
                BreedingRecord(
                    farm_id=farm_id,
                    doe_id=doe.id,
                    method="AI",
                    breeding_date=service,
                    ultrasound_done=True,
                    ultrasound_result_date=service + timedelta(days=32),
                    pregnant=True,
                    outcome="CONFIRMED_PREGNANT",
                    expected_kidding_date=service + timedelta(days=150),
                )
            )
        await db.commit()
        expected_id = doe.id
    response = await client.get("/api/dashboard", headers=owner)
    assert response.status_code == 200, response.text
    document = response.json()
    assert document["suggestions_total"] == 1
    suggestion = document["suggestions"][0]
    assert suggestion["animal"]["id"] == expected_id
    assert suggestion["to"] == "PREGNANCY_LATE"
    assert suggestion["reason"] == "Gestation day 100 (≥100)"
