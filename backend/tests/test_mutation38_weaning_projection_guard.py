"""A real held dam blocks native weaning before extra reads on a projected kid."""

import httpx
import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import MissingGreenlet
from sqlalchemy.orm import load_only

from app.db import get_sessionmaker
from app.models import Animal, BucketMove, Farm, Task, User
from app.services.tasks import complete_task
from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_extended import all_tasks, kid_on_ekd, place_health_hold, pregnant_doe


async def test_native_weaning_reports_the_held_dam_with_a_real_projected_locked_kid(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    doe, _, breeding = await pregnant_doe(client, owner, "HELD-WEAN", gestation_days=220)
    delivery = await kid_on_ekd(client, owner, breeding, kids=[{"sex": "M"}])
    kid_id = int(delivery["kids"][0]["animal_id"])
    doe_id = int(doe["id"])
    assert doe_id < kid_id
    await place_health_hold(client, owner, doe_id)
    duties = [
        task
        for task in await all_tasks(client, owner)
        if task["breeding_record_id"] == breeding["id"] and task["category"] == "WEANING"
    ]
    assert len(duties) == 1
    async with get_sessionmaker()() as db:
        before = set(
            (
                await db.execute(
                    select(BucketMove.id).where(BucketMove.animal_id.in_([doe_id, kid_id]))
                )
            ).scalars()
        )
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, int(owner["X-Farm-Id"]))
        assert farm is not None
        user = await db.get(User, farm.owner_id)
        assert user is not None
        # Acquire actual Animal rows in canonical order, followed by Task.
        # The dam carries a real public health hold. The kid is a normal
        # projection; no synthetic getters, inputs or query responses exist.
        mother = (
            await db.execute(select(Animal).where(Animal.id == doe_id).with_for_update())
        ).scalar_one()
        child = (
            await db.execute(
                select(Animal)
                .options(
                    load_only(
                        Animal.id,
                        Animal.farm_id,
                        Animal.dam_id,
                        Animal.status,
                        Animal.current_bucket,
                        Animal.sex,
                    )
                )
                .where(Animal.id == kid_id)
                .with_for_update()
            )
        ).scalar_one()
        assert mother.movement_restricted or mother.suspected_scheduled_disease
        assert "movement_restricted" in inspect(child).unloaded
        task = (
            await db.execute(select(Task).where(Task.id == duties[0]["id"]).with_for_update())
        ).scalar_one()
        assert task.status == "PENDING"
        try:
            await complete_task(
                db, task, user, locked_animals=[mother, child], reference_date=today(farm.timezone)
            )
        except MissingGreenlet as exc:
            pytest.fail(f"The known dam hold must reject weaning without implicit kid I/O: {exc}")
        except ValueError as exc:
            assert str(exc) == "Weaning is blocked by an animal lifecycle or movement-hold state"
        else:
            pytest.fail("A genuine held dam must block weaning")
        await db.rollback()
    async with get_sessionmaker()() as db:
        committed_task = await db.get(Task, int(duties[0]["id"]))
        assert committed_task is not None and committed_task.status == "PENDING"
        assert committed_task.completed_at is None and committed_task.completed_by_id is None
        after = set(
            (
                await db.execute(
                    select(BucketMove.id).where(BucketMove.animal_id.in_([doe_id, kid_id]))
                )
            ).scalars()
        )
        assert after == before
