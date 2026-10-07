"""Literal breeding-calendar and cycle-boundary contracts through real workflows."""

from datetime import date, timedelta

import httpx
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import Animal, BreedingRecord, Farm
from app.services.breeding import (
    create_breeding_record,
    derived_heat_cycle_number,
    record_ultrasound_result,
)
from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_extended import (
    all_tasks,
    get_animal,
    make_buck,
    make_doe,
    pregnant_doe,
)


async def test_confirmed_pregnancy_steps_up_at_literal_gestation_day100(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    doe, _buck, breeding = await pregnant_doe(client, owner, gestation_days=140)
    duties = [
        task
        for task in await all_tasks(client, owner)
        if task["breeding_record_id"] == breeding["id"]
        and task["title_key"] == "move_to_pregnancy_late"
    ]
    assert len(duties) == 1
    duty = duties[0]
    breeding_date = date.fromisoformat(breeding["breeding_date"])
    assert duty["due_date"] == (breeding_date + timedelta(days=100)).isoformat()
    assert (
        duty["due_date"]
        == (date.fromisoformat(breeding["expected_kidding_date"]) - timedelta(days=50)).isoformat()
    )
    response = await client.post(f"/api/tasks/{duty['id']}/complete", headers=owner)
    assert response.status_code == 200, response.text
    assert (await get_animal(client, owner, doe["id"]))["current_bucket"] == "PREGNANCY_LATE"


async def test_native_recorded_failed_history_saturates_at_schema_cycle99(
    client: httpx.AsyncClient,
) -> None:
    """Ninety-nine actually recorded scan failures retain the last legal cycle."""
    owner = await owner_with_farm(client)
    doe_data = await make_doe(
        client, owner, "NATIVE-CYCLE-CAP-DOE", bucket="BREEDING", age_days=5_000
    )
    birth_date = today() - timedelta(days=5_000)
    buck_data = await make_buck(
        client,
        owner,
        "NATIVE-CYCLE-CAP-BUCK",
        date_of_birth=birth_date.isoformat(),
        weight_date=birth_date.isoformat(),
    )
    first_service_date = today() - timedelta(days=3_500)
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, int(owner["X-Farm-Id"]))
        animals = list(
            (
                await db.execute(
                    select(Animal)
                    .where(Animal.id.in_([doe_data["id"], buck_data["id"]]))
                    .order_by(Animal.id)
                    .with_for_update()
                )
            ).scalars()
        )
        assert farm is not None and len(animals) == 2
        by_id = {animal.id: animal for animal in animals}
        doe, buck = by_id[doe_data["id"]], by_id[buck_data["id"]]
        for index in range(99):
            service_date = first_service_date + timedelta(days=35 * index)
            breeding = await create_breeding_record(
                db,
                farm,
                doe,
                buck,
                service_date,
                created_by_id=farm.owner_id,
                doe_latest_weight_kg=26.0,
                has_open_breeding=False,
                actor_is_owner=True,
            )
            # Each failure is actually observed/closed through the native
            # audited scan workflow, with a legal +32-day observation. No
            # completed clinical facts or FAILED rows are fabricated.
            await record_ultrasound_result(
                db,
                breeding,
                False,
                result_date=service_date + timedelta(days=32),
                created_by_id=farm.owner_id,
            )
            assert breeding.outcome == "FAILED"
        await db.commit()

    async with get_sessionmaker()() as db:
        history = list(
            (
                await db.execute(
                    select(BreedingRecord)
                    .where(BreedingRecord.doe_id == doe_data["id"])
                    .order_by(BreedingRecord.breeding_date)
                )
            ).scalars()
        )
        assert len(history) == 99
        assert all(
            record.outcome == "FAILED"
            and record.ultrasound_done
            and record.pregnant is False
            and record.ultrasound_result_date == record.breeding_date + timedelta(days=32)
            for record in history
        )
        assert history[-1].heat_cycle_number == 99
        assert await derived_heat_cycle_number(db, int(owner["X-Farm-Id"]), doe_data["id"]) == 99
