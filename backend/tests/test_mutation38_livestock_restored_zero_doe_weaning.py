"""A constraint-valid restored zero-ID dam retains ordinary weaning effects."""

from datetime import timedelta

import httpx
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import Animal, BreedingRecord, BucketMove, Farm, KidEntry, Task, WeightRecord
from app.services.breeding import create_breeding_record, doe_has_open_breeding
from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_extended import confirm, make_buck, make_kidding


async def test_generated_weaning_moves_a_restored_zero_id_dam_and_her_actual_litter(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="restored-zero-weaning@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    buck_data = await make_buck(client, owner, "RESTORED-ZERO-SIRE")
    birth_date = today() - timedelta(days=800)
    service_date = today() - timedelta(days=225)
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        # Restore the primary key at initial insertion. Ordinary ORM flush
        # and commit retain every current FK, uniqueness and CHECK guard;
        # no existing animal or clinical graph is renumbered.
        doe = Animal(
            id=0,
            farm_id=farm_id,
            tag_number="RESTORED-ZERO-DAM",
            breed="Osmanabadi",
            sex="F",
            source="PURCHASED",
            date_of_birth=birth_date,
            purchase_date=birth_date,
            current_bucket="BREEDING",
            status="ACTIVE",
        )
        db.add(doe)
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

    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        animals = list(
            (
                await db.execute(
                    select(Animal)
                    .where(Animal.farm_id == farm_id, Animal.id.in_([0, buck_data["id"]]))
                    .order_by(Animal.id)
                    .with_for_update()
                )
            ).scalars()
        )
        assert farm is not None and len(animals) == 2
        by_id = {animal.id: animal for animal in animals}
        weight = await db.scalar(
            select(WeightRecord.weight_kg).where(
                WeightRecord.animal_id == 0, WeightRecord.farm_id == farm_id
            )
        )
        assert weight == 26.0
        has_open = await doe_has_open_breeding(db, farm_id, 0)
        assert has_open is False
        # The bounded public creation DTO excludes zero, while the native
        # typed service accepts the real restored row under canonical locks.
        breeding = await create_breeding_record(
            db,
            farm,
            by_id[0],
            by_id[buck_data["id"]],
            service_date,
            created_by_id=farm.owner_id,
            doe_latest_weight_kg=weight,
            has_open_breeding=has_open,
            actor_is_owner=True,
        )
        breeding_id = breeding.id
        owner_id = farm.owner_id
        await db.commit()

    confirmed = await confirm(client, owner, breeding_id)
    kidding_date = service_date + timedelta(days=150)
    assert confirmed["expected_kidding_date"] == kidding_date.isoformat()
    record = await make_kidding(
        client,
        owner,
        breeding_id,
        date=kidding_date.isoformat(),
        kids=[
            {"sex": "M", "status": "ALIVE", "birth_weight": 2.5},
            {"sex": "F", "status": "ALIVE", "birth_weight": 2.5},
        ],
    )
    async with get_sessionmaker()() as db:
        entries = list(
            (
                await db.execute(select(KidEntry).where(KidEntry.kidding_record_id == record["id"]))
            ).scalars()
        )
        assert len(entries) == 2 and all(entry.animal_id is not None for entry in entries)
        kid_ids = [entry.animal_id for entry in entries if entry.animal_id is not None]
        duties = list(
            (
                await db.execute(
                    select(Task).where(
                        Task.farm_id == farm_id,
                        Task.breeding_record_id == breeding_id,
                        Task.category == "WEANING",
                    )
                )
            ).scalars()
        )
        assert len(duties) == 1
        duty = duties[0]
        assert duty.animal_id == 0 and duty.auto_generated and duty.status == "PENDING"
        assert duty.due_date == kidding_date + timedelta(days=60)
        duty_id = duty.id
        previous_move_ids = set(
            (
                await db.execute(
                    select(BucketMove.id).where(BucketMove.animal_id.in_([0, *kid_ids]))
                )
            ).scalars()
        )

    completed = await client.post(f"/api/tasks/{duty_id}/complete", headers=owner)
    assert completed.status_code == 200, completed.text
    async with get_sessionmaker()() as db:
        completed_duty = await db.get(Task, duty_id)
        assert completed_duty is not None and completed_duty.status == "DONE"
        assert (
            completed_duty.completed_by_id == owner_id and completed_duty.completed_at is not None
        )
        animals = list(
            (await db.execute(select(Animal).where(Animal.id.in_([0, *kid_ids])))).scalars()
        )
        assert len(animals) == 3
        by_id = {animal.id: animal for animal in animals}
        assert by_id[0].current_bucket == "RESTING"
        for kid_id in kid_ids:
            kid = by_id[kid_id]
            assert kid.dam_id == 0 and kid.sire_id == buck_data["id"]
            assert kid.status == "ACTIVE"
            assert kid.current_bucket == ("MALE_KIDS" if kid.sex == "M" else "FEMALE_KIDS")
        moves = list(
            (
                await db.execute(
                    select(BucketMove).where(
                        BucketMove.animal_id.in_([0, *kid_ids]),
                        BucketMove.id.not_in(previous_move_ids),
                    )
                )
            ).scalars()
        )
        assert len(moves) == 3
        assert {move.animal_id for move in moves} == {0, *kid_ids}
        assert all(move.farm_id == farm_id and move.created_by_id == owner_id for move in moves)
        assert all(move.effective_date == today() for move in moves)
        rebreed = list(
            (
                await db.execute(
                    select(Task).where(
                        Task.farm_id == farm_id,
                        Task.animal_id == 0,
                        Task.category == "REBREED",
                        Task.status == "PENDING",
                    )
                )
            ).scalars()
        )
        assert len(rebreed) == 1 and rebreed[0].due_date == today() + timedelta(days=30)
        retained_breeding = await db.get(BreedingRecord, breeding_id)
        assert retained_breeding is not None and retained_breeding.outcome == "CONFIRMED_PREGNANT"
        assert len(entries) == 2
