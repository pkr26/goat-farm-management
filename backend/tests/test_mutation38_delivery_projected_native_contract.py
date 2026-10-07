"""Projected, row-locked DELIVERY animals support the native duty's no-op completion."""

from datetime import date, timedelta

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
from .test_breeding_extended import all_tasks, iso, move_to, pregnant_doe


async def test_native_delivery_completion_accepts_a_genuine_projected_locked_animal(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    doe, _buck, breeding = await pregnant_doe(
        client, owner, "PROJECTED-DELIVERY", gestation_days=140
    )
    expected = date.fromisoformat(breeding["expected_kidding_date"])
    duty = next(
        task
        for task in await all_tasks(client, owner)
        if task["breeding_record_id"] == breeding["id"]
        and task["category"] == "BUCKET_MOVE"
        and task["due_date"] == iso(expected - timedelta(days=15))
    )
    corrected = await move_to(client, owner, doe["id"], "DELIVERY", history_override=True)
    assert corrected["current_bucket"] == "DELIVERY"
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        user = await db.get(User, farm.owner_id)
        assert user is not None
        before = list(
            (
                await db.execute(
                    select(
                        BucketMove.id,
                        BucketMove.from_bucket,
                        BucketMove.to_bucket,
                        BucketMove.effective_date,
                        BucketMove.reason,
                        BucketMove.created_by_id,
                    )
                    .where(BucketMove.animal_id == doe["id"])
                    .order_by(BucketMove.id)
                )
            ).all()
        )
        # This is an ordinary ORM projection of an actual committed row,
        # genuinely locked before the Task in the required native order.
        # complete_task declares prelocked Animals and has no full-row
        # precondition; a DELIVERY no-op should need no extra implicit I/O.
        animal = (
            await db.execute(
                select(Animal)
                .options(load_only(Animal.id, Animal.farm_id, Animal.current_bucket))
                .where(Animal.id == doe["id"], Animal.farm_id == farm_id)
                .with_for_update()
            )
        ).scalar_one()
        assert {"status", "movement_restricted", "suspected_scheduled_disease"} <= inspect(
            animal
        ).unloaded
        task = (
            await db.execute(
                select(Task).where(Task.id == duty["id"], Task.farm_id == farm_id).with_for_update()
            )
        ).scalar_one()
        assert task.status == "PENDING" and task.auto_generated is True
        try:
            completed = await complete_task(
                db, task, user, locked_animals=[animal], reference_date=today(farm.timezone)
            )
            await db.commit()
        except MissingGreenlet as exc:
            pytest.fail(
                f"A real projected locked DELIVERY row must complete without implicit I/O: {exc}"
            )
        assert completed.status == "DONE"
        assert completed.completed_by_id == user.id and completed.completed_at is not None

    async with get_sessionmaker()() as db:
        after = list(
            (
                await db.execute(
                    select(
                        BucketMove.id,
                        BucketMove.from_bucket,
                        BucketMove.to_bucket,
                        BucketMove.effective_date,
                        BucketMove.reason,
                        BucketMove.created_by_id,
                    )
                    .where(BucketMove.animal_id == doe["id"])
                    .order_by(BucketMove.id)
                )
            ).all()
        )
        committed = await db.get(Animal, doe["id"])
        committed_task = await db.get(Task, duty["id"])
        assert committed is not None and committed.status == "ACTIVE"
        assert committed.current_bucket == "DELIVERY"
        assert committed_task is not None and committed_task.status == "DONE"
        assert (
            committed_task.completed_by_id == farm.owner_id
            and committed_task.completed_at is not None
        )
        assert after == before
