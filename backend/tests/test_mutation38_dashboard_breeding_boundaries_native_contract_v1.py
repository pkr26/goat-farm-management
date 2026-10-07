"""Real maturity, completed-birth links and exact rest dates govern suggestions."""

from datetime import date, timedelta

import httpx
from sqlalchemy import Date, cast, func, select

from app.db import get_sessionmaker
from app.models import (
    Animal,
    BreedingRecord,
    BucketMove,
    Farm,
    KiddingRecord,
    KidEntry,
    WeightRecord,
)

from .conftest import owner_with_farm


async def test_native_foundation_maturity_suggests_the_doe_and_not_the_male(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="mutation-dashboard-foundation-sex@farm.in")
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
        animals = []
        for sex in ("F", "M"):
            animal = Animal(
                farm_id=farm_id,
                tag_number="FOUNDATION-MATURE-" + sex,
                sex=sex,
                source="BORN",
                date_of_birth=day - timedelta(days=800),
                birth_type="SINGLE",
                birth_weight=2.5,
                current_bucket="FOUNDATION",
            )
            db.add(animal)
            await db.flush()
            db.add(WeightRecord(farm_id=farm_id, animal_id=animal.id, date=day, weight_kg=22.0))
            animals.append(animal)
        await db.commit()
        expected_id = animals[0].id
    response = await client.get("/api/dashboard", headers=owner)
    assert response.status_code == 200, response.text
    document = response.json()
    assert document["suggestions_total"] == 1
    assert [row["animal"]["id"] for row in document["suggestions"]] == [expected_id]
    assert document["suggestions"][0]["to"] == "BREEDING"


async def test_native_current_pregnancy_is_not_closed_by_another_does_recorded_birth(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="mutation-dashboard-birth-link@farm.in")
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
        does = []
        for index, bucket in enumerate(("PREGNANCY_EARLY", "RECOVERY")):
            doe = Animal(
                farm_id=farm_id,
                tag_number=f"BIRTH-LINK-{index}",
                sex="F",
                source="BORN",
                date_of_birth=day - timedelta(days=1200),
                birth_type="SINGLE",
                birth_weight=2.5,
                current_bucket=bucket,
            )
            db.add(doe)
            await db.flush()
            service = day - timedelta(days=100 if index == 0 else 170)
            record = BreedingRecord(
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
            db.add(record)
            await db.flush()
            if index == 1:
                litter = KiddingRecord(
                    farm_id=farm_id,
                    doe_id=doe.id,
                    breeding_record_id=record.id,
                    date=day - timedelta(days=20),
                    ease="NORMAL",
                )
                db.add(litter)
                await db.flush()
                db.add(
                    KidEntry(
                        farm_id=farm_id,
                        kidding_record_id=litter.id,
                        sex="M",
                        status="STILLBORN",
                        birth_weight=2.5,
                    )
                )
            does.append(doe)
        await db.commit()
        expected_id = does[0].id
    response = await client.get("/api/dashboard", headers=owner)
    assert response.status_code == 200, response.text
    document = response.json()
    assert document["suggestions_total"] == 1
    assert document["suggestions"][0]["animal"]["id"] == expected_id
    assert document["suggestions"][0]["to"] == "PREGNANCY_LATE"
    assert document["suggestions"][0]["reason"] == "Gestation day 100 (≥100)"


async def test_native_rest_program_is_ready_on_its_actual_thirtieth_day(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="mutation-dashboard-rest-edge@farm.in")
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
            tag_number="REST-DAY-THIRTY",
            sex="F",
            source="BORN",
            date_of_birth=day - timedelta(days=1200),
            birth_type="SINGLE",
            birth_weight=2.5,
            current_bucket="RESTING",
        )
        db.add(doe)
        await db.flush()
        db.add(
            BucketMove(
                farm_id=farm_id,
                animal_id=doe.id,
                from_bucket="RECOVERY",
                to_bucket="RESTING",
                effective_date=day - timedelta(days=30),
                reason="Retained completed rest program",
            )
        )
        db.add(WeightRecord(farm_id=farm_id, animal_id=doe.id, date=day, weight_kg=22.0))
        await db.commit()
        expected_id = doe.id
    response = await client.get("/api/dashboard", headers=owner)
    assert response.status_code == 200, response.text
    document = response.json()
    assert document["suggestions_total"] == 1
    assert document["suggestions"][0]["animal"]["id"] == expected_id
    assert document["suggestions"][0]["to"] == "BREEDING"
    assert document["suggestions"][0]["reason"] == "30 days resting (flush done)"
