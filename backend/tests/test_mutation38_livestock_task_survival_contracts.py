"""Survivorship uses live herd rows; postpartum moves retain the real actor."""

import httpx
import pytest
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import Animal, BucketMove, Farm, KidEntry
from app.services.tasks import _litter_has_surviving_kid

from .conftest import owner_with_farm
from .test_breeding_extended import all_tasks, kid_on_ekd, pregnant_doe


async def test_real_two_kid_litter_is_surviving_in_its_actual_farm(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    _, _, breeding = await pregnant_doe(client, owner, "REAL-SURVIVORS", gestation_days=160)
    record = await kid_on_ekd(
        client,
        owner,
        breeding,
        kids=[{"sex": "M", "status": "ALIVE"}, {"sex": "F", "status": "ALIVE"}],
    )
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        entries = list(
            (
                await db.execute(select(KidEntry).where(KidEntry.kidding_record_id == record["id"]))
            ).scalars()
        )
        assert len(entries) == 2 and all(entry.animal_id is not None for entry in entries)
        actual_ids = [entry.animal_id for entry in entries if entry.animal_id is not None]
        animals = list(
            (await db.execute(select(Animal).where(Animal.id.in_(actual_ids)))).scalars()
        )
        assert len(animals) == 2
        assert all(animal.status == "ACTIVE" and animal.farm_id == farm_id for animal in animals)
        try:
            survives = await _litter_has_surviving_kid(db, farm_id, entries)
        except Exception as error:
            pytest.fail(f"Real active litter survivorship must return a boolean: {error!r}")
        assert survives is True


async def test_actual_no_survivor_postpartum_move_retains_the_completing_owner(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    doe, _, breeding = await pregnant_doe(client, owner, "POSTPARTUM-ACTOR", gestation_days=180)
    await kid_on_ekd(client, owner, breeding, kids=[{"sex": "F", "status": "STILLBORN"}])
    duties = [
        task
        for task in await all_tasks(client, owner)
        if task["breeding_record_id"] == breeding["id"] and task["title_key"] == "move_to_resting"
    ]
    assert len(duties) == 1 and duties[0]["status"] == "PENDING"
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, int(owner["X-Farm-Id"]))
        assert farm is not None
        actor_id = farm.owner_id
        previous = set(
            (
                await db.execute(select(BucketMove.id).where(BucketMove.animal_id == doe["id"]))
            ).scalars()
        )
    completed = await client.post(f"/api/tasks/{duties[0]['id']}/complete", headers=owner)
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "DONE"
    assert completed.json()["completed_by_id"] == actor_id
    async with get_sessionmaker()() as db:
        moves = list(
            (
                await db.execute(
                    select(BucketMove).where(
                        BucketMove.animal_id == doe["id"], BucketMove.id.not_in(previous)
                    )
                )
            ).scalars()
        )
        assert len(moves) == 1
        assert moves[0].from_bucket == "RECOVERY" and moves[0].to_bucket == "RESTING"
        assert moves[0].created_by_id == actor_id
