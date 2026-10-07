"""Generated movement duties preserve real restored zero-key clinical links."""

from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import (
    Animal,
    BreedingRecord,
    BucketMove,
    Farm,
    HealthEvent,
    PurchaseBatch,
    Task,
    User,
    WeightRecord,
)
from app.services.breeding import create_breeding_record, record_ultrasound_result
from app.services.health import record_health_event, template_names_for_task, validated_template
from app.services.kidding import KidSpec, record_kidding
from app.services.purchases import schedule_quarantine_tasks
from app.services.tasks import complete_task
from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_extended import confirm, make_buck, make_doe, make_kidding


async def _restored_pregnancy(
    client: httpx.AsyncClient, zero_key: str
) -> tuple[dict[str, str], int, int, int, int, date]:
    owner = await owner_with_farm(client, email="restored-movement-zero@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    buck_data = await make_buck(client, owner, "RESTORED-MOVEMENT-SIRE")
    buck_id = int(buck_data["id"])
    birth_date = today() - timedelta(days=800)
    service_date = today() - timedelta(days=220)
    if zero_key == "animal":
        async with get_sessionmaker()() as db:
            farm = await db.get(Farm, farm_id)
            assert farm is not None
            db.add(
                Animal(
                    id=0,
                    farm_id=farm_id,
                    tag_number="RESTORED-MOVEMENT-DAM",
                    breed="Osmanabadi",
                    sex="F",
                    source="PURCHASED",
                    date_of_birth=birth_date,
                    purchase_date=birth_date,
                    current_bucket="BREEDING",
                    status="ACTIVE",
                )
            )
            await db.flush()
            db.add(
                WeightRecord(
                    farm_id=farm_id,
                    animal_id=0,
                    date=birth_date,
                    weight_kg=26.0,
                    created_by_id=farm.owner_id,
                )
            )
            await db.commit()
        doe_id = 0
    else:
        doe_data = await make_doe(client, owner, "RESTORED-MOVEMENT-DAM", bucket="BREEDING")
        doe_id = int(doe_data["id"])

    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        animals = list(
            (
                await db.execute(
                    select(Animal)
                    .where(Animal.farm_id == farm_id, Animal.id.in_([doe_id, buck_id]))
                    .order_by(Animal.id)
                    .with_for_update()
                )
            ).scalars()
        )
        assert farm is not None and len(animals) == 2
        by_id = {animal.id: animal for animal in animals}
        owner_id = farm.owner_id
        if zero_key == "animal":
            breeding = await create_breeding_record(
                db,
                farm,
                by_id[doe_id],
                by_id[buck_id],
                service_date,
                created_by_id=owner_id,
                doe_latest_weight_kg=26.0,
                has_open_breeding=False,
                actor_is_owner=True,
            )
        else:
            # Initial restored service provenance has no asserted clinical
            # result. PK0 is inserted from the start under all constraints;
            # the subsequent native scan records the actual observation and
            # generates its duties. No existing record is renumbered.
            breeding = BreedingRecord(
                id=0,
                farm_id=farm_id,
                doe_id=doe_id,
                buck_id=buck_id,
                breeding_date=service_date,
                method="NATURAL",
                heat_cycle_number=1,
                ultrasound_date=service_date + timedelta(days=32),
                ultrasound_done=False,
                outcome="PENDING",
                created_by_id=owner_id,
            )
            db.add(breeding)
            await db.flush()
        breeding_id = breeding.id
        await db.commit()

    if zero_key == "animal":
        await confirm(client, owner, breeding_id)
    else:
        # The bounded HTTP BR route excludes zero. The native clinical
        # helper accepts the restored record with canonical Animal -> BR
        # FOR UPDATE locks and a genuine planned-day positive observation.
        async with get_sessionmaker()() as db:
            await db.execute(
                select(Animal)
                .where(Animal.id.in_([doe_id, buck_id]))
                .order_by(Animal.id)
                .with_for_update()
            )
            restored = (
                await db.execute(
                    select(BreedingRecord).where(BreedingRecord.id == 0).with_for_update()
                )
            ).scalar_one()
            await record_ultrasound_result(
                db,
                restored,
                True,
                1,
                result_date=service_date + timedelta(days=32),
                created_by_id=owner_id,
            )
            await db.commit()
    return owner, doe_id, buck_id, breeding_id, owner_id, service_date


@pytest.mark.parametrize("zero_key", ["animal", "breeding"])
@pytest.mark.parametrize("movement", ["day100", "delivery", "postpartum"])
async def test_restored_zero_key_generated_movements_preserve_cohort_and_actor(
    client: httpx.AsyncClient, zero_key: str, movement: str
) -> None:
    owner, doe_id, buck_id, breeding_id, owner_id, service_date = await _restored_pregnancy(
        client, zero_key
    )
    title_key = "move_to_pregnancy_late" if movement == "day100" else "move_to_delivery"
    target = "PREGNANCY_LATE" if movement == "day100" else "DELIVERY"
    if movement == "postpartum":
        kidding_date = service_date + timedelta(days=150)
        if zero_key == "animal":
            await make_kidding(
                client,
                owner,
                breeding_id,
                date=kidding_date.isoformat(),
                kids=[{"sex": "F", "status": "STILLBORN"}],
            )
        else:
            async with get_sessionmaker()() as db:
                farm = await db.get(Farm, int(owner["X-Farm-Id"]))
                assert farm is not None
                await db.execute(
                    select(Animal)
                    .where(Animal.id.in_([doe_id, buck_id]))
                    .order_by(Animal.id)
                    .with_for_update()
                )
                breeding = (
                    await db.execute(
                        select(BreedingRecord)
                        .where(BreedingRecord.id == breeding_id)
                        .with_for_update()
                    )
                ).scalar_one()
                kids: list[KidSpec] = [
                    {
                        "tag": "RESTORED-ZERO-STILLBORN",
                        "tag_is_explicit": True,
                        "sex": "F",
                        "birth_weight": None,
                        "status": "STILLBORN",
                        "mortality_reported_at": None,
                        "colostrum_within_2h": None,
                        "navel_dipped": None,
                        "dam_rejected": False,
                    }
                ]
                await record_kidding(
                    db, farm, breeding, kidding_date, "NORMAL", "", kids, created_by_id=owner_id
                )
                await db.commit()
        title_key, target = "move_to_resting", "RESTING"

    async with get_sessionmaker()() as db:
        duties = list(
            (
                await db.execute(
                    select(Task).where(
                        Task.breeding_record_id == breeding_id, Task.title_key == title_key
                    )
                )
            ).scalars()
        )
        assert len(duties) == 1
        duty = duties[0]
        assert duty.status == "PENDING" and duty.auto_generated
        assert duty.animal_id == doe_id and duty.breeding_record_id == breeding_id
        duty_id = duty.id
        previous_move_ids = set(
            (
                await db.execute(select(BucketMove.id).where(BucketMove.animal_id == doe_id))
            ).scalars()
        )

    completed = await client.post(f"/api/tasks/{duty_id}/complete", headers=owner)
    assert completed.status_code == 200, completed.text
    async with get_sessionmaker()() as db:
        current = await db.get(Animal, doe_id)
        assert current is not None and current.current_bucket == target
        done = await db.get(Task, duty_id)
        assert done is not None and done.status == "DONE"
        assert done.completed_by_id == owner_id and done.completed_at is not None
        moves = list(
            (
                await db.execute(
                    select(BucketMove).where(
                        BucketMove.animal_id == doe_id, BucketMove.id.not_in(previous_move_ids)
                    )
                )
            ).scalars()
        )
        assert len(moves) == 1
        assert moves[0].to_bucket == target and moves[0].created_by_id == owner_id
        assert moves[0].effective_date == today()
        if movement == "postpartum":
            prompts = list(
                (
                    await db.execute(
                        select(Task).where(
                            Task.animal_id == doe_id,
                            Task.category == "REBREED",
                            Task.status == "PENDING",
                        )
                    )
                ).scalars()
            )
            assert len(prompts) == 1 and prompts[0].due_date == today() + timedelta(days=30)


async def test_restored_zero_batch_generated_release_preserves_protocol_and_movement(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="restored-zero-batch@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    arrival = today() - timedelta(days=50)
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        batch = PurchaseBatch(
            id=0,
            farm_id=farm_id,
            date=arrival,
            supplier="Restored historical batch",
            count=1,
            sex="F",
            created_by_id=farm.owner_id,
        )
        db.add(batch)
        await db.flush()
        animal = Animal(
            farm_id=farm_id,
            tag_number="RESTORED-ZERO-BATCH-DAM",
            breed="Osmanabadi",
            sex="F",
            source="PURCHASED",
            purchase_batch_id=0,
            purchase_date=arrival,
            date_of_birth=today() - timedelta(days=800),
            current_bucket="QUARANTINE",
            status="ACTIVE",
        )
        db.add(animal)
        await db.flush()
        await schedule_quarantine_tasks(db, farm, batch)
        await db.commit()
        animal_id, owner_id = animal.id, farm.owner_id

    async with get_sessionmaker()() as db:
        duties = list(
            (
                await db.execute(select(Task).where(Task.purchase_batch_id == 0).order_by(Task.id))
            ).scalars()
        )
        assert len(duties) == 11 and all(duty.auto_generated for duty in duties)
        ordinary = [duty.id for duty in duties if duty.category == "QUARANTINE"]
        clinical = [duty.id for duty in duties if duty.category in {"VACCINE", "DEWORMING"}]
        release_id = next(duty.id for duty in duties if duty.category == "BUCKET_MOVE")
        assert len(ordinary) == len(clinical) == 5
    for task_id in ordinary:
        performed = await client.post(f"/api/tasks/{task_id}/complete", headers=owner)
        assert performed.status_code == 200, performed.text
    for task_id in clinical:
        async with get_sessionmaker()() as db:
            farm = await db.get(Farm, farm_id)
            user = await db.get(User, owner_id)
            animal = (
                await db.execute(select(Animal).where(Animal.id == animal_id).with_for_update())
            ).scalar_one()
            duty = (
                await db.execute(select(Task).where(Task.id == task_id).with_for_update())
            ).scalar_one()
            assert farm is not None and user is not None
            templates = template_names_for_task(duty.title, duty.category)
            assert templates
            template = await validated_template(db, templates[0], duty.category)
            assert template is not None
            # The public batch input excludes zero. These actual native
            # clinical services record each corresponding treatment with
            # real template/actor/date before completing its matching duty
            # under the canonical Animal -> Task locks. DONE rows and
            # clinical metadata are never inserted or assigned by fixtures.
            await record_health_event(
                db,
                farm,
                [animal],
                duty.due_date,
                duty.category,
                "",
                templates[0],
                "",
                "",
                "",
                None,
                None,
                templates[0],
                template.id,
                None,
                "",
                None,
                None,
                None,
                "",
                "",
                "",
                None,
                False,
                None,
                None,
                "Recorded quarantine duty for restored batch",
                purchase_batch_id=0,
                created_by_id=owner_id,
            )
            await complete_task(db, duty, user)
            await db.commit()
    async with get_sessionmaker()() as db:
        prerequisites = list(
            (
                await db.execute(
                    select(Task).where(Task.purchase_batch_id == 0, Task.id != release_id)
                )
            ).scalars()
        )
        assert len(prerequisites) == 10
        assert all(
            duty.status == "DONE" and duty.completed_by_id == owner_id for duty in prerequisites
        )
        health = list(
            (
                await db.execute(select(HealthEvent).where(HealthEvent.purchase_batch_id == 0))
            ).scalars()
        )
        assert sorted(event.type for event in health) == [
            "DEWORMING",
            "VACCINE",
            "VACCINE",
            "VACCINE",
            "VACCINE",
        ]
        assert all(
            event.animal_id == animal_id and event.created_by_id == owner_id for event in health
        )
    released = await client.post(f"/api/tasks/{release_id}/complete", headers=owner)
    assert released.status_code == 200, released.text
    async with get_sessionmaker()() as db:
        released_animal = await db.get(Animal, animal_id)
        release = await db.get(Task, release_id)
        assert released_animal is not None and released_animal.current_bucket == "FOUNDATION"
        assert release is not None and release.status == "DONE"
        assert release.completed_by_id == owner_id
        moves = list(
            (
                await db.execute(select(BucketMove).where(BucketMove.animal_id == animal_id))
            ).scalars()
        )
        assert len(moves) == 1 and moves[0].to_bucket == "FOUNDATION"
        assert moves[0].created_by_id == owner_id and moves[0].effective_date == today()
