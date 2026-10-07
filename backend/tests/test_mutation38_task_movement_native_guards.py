"""Movement completion resolves its actual farm and fails closed on retained missing links."""

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import Animal, BucketMove, Farm, Task, User
from app.services.tasks import complete_task
from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_extended import confirm, make_breeding, make_buck, make_doe


async def test_native_day_hundred_completion_without_explicit_date_uses_its_actual_farm(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client, email="native-movement-farm-calendar@example.test")
    farm_id = int(headers["X-Farm-Id"])
    doe = await make_doe(client, headers, "NATIVE-DAY100-DOE", bucket="BREEDING")
    buck = await make_buck(client, headers, "NATIVE-DAY100-SIRE")
    service_date = today() - timedelta(days=120)
    breeding = await make_breeding(
        client, headers, doe["id"], buck["id"], breeding_date=service_date.isoformat()
    )
    await confirm(client, headers, breeding["id"])
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        farm.timezone = "Pacific/Honolulu"
        await db.commit()
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        owner = await db.get(User, farm.owner_id)
        assert owner is not None
        animal = (
            await db.execute(select(Animal).where(Animal.id == doe["id"]).with_for_update())
        ).scalar_one()
        task = (
            await db.execute(
                select(Task)
                .where(
                    Task.breeding_record_id == breeding["id"],
                    Task.title_key == "move_to_pregnancy_late",
                )
                .with_for_update()
            )
        ).scalar_one()
        assert animal.current_bucket == "PREGNANCY_EARLY"
        assert task.status == "PENDING" and task.due_date == service_date + timedelta(days=100)
        task_id, actor_id = task.id, owner.id
        reference = today(farm.timezone)
        try:
            # The native signature permits an omitted reference_date. Actual
            # canonical Animal→Task locks and the real generated duty are used.
            done = await complete_task(db, task, owner, locked_animals=[animal])
        except ValueError as error:
            pytest.fail(f"Valid native day100 completion must resolve its existing farm: {error}")
        assert done is task and done.status == "DONE"
        await db.commit()
    async with get_sessionmaker()() as db:
        persisted_animal = await db.get(Animal, doe["id"])
        persisted_task = await db.get(Task, task_id)
        assert persisted_animal is not None and persisted_animal.current_bucket == "PREGNANCY_LATE"
        assert (
            persisted_task is not None
            and persisted_task.completed_by_id == actor_id
            and persisted_task.completed_at is not None
        )
        moves = list(
            (
                await db.execute(
                    select(BucketMove).where(
                        BucketMove.animal_id == doe["id"],
                        BucketMove.from_bucket == "PREGNANCY_EARLY",
                        BucketMove.to_bucket == "PREGNANCY_LATE",
                    )
                )
            ).scalars()
        )
        assert len(moves) == 1
        assert moves[0].effective_date == reference and moves[0].created_by_id == actor_id


async def test_retained_generated_movement_without_an_animal_link_is_a_conflict(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client, email="retained-movement-missing-link@example.test")
    farm_id = int(headers["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        # Initially retained pre-hardening generated row. Nullable link
        # columns and all actual head enum/FK/check constraints are retained;
        # no contemporary clinical record or association is removed.
        retained = Task(
            farm_id=farm_id,
            title="Retained unlinked movement",
            due_date=today(farm.timezone),
            category="BUCKET_MOVE",
            status="PENDING",
            auto_generated=True,
            created_by_id=farm.owner_id,
        )
        db.add(retained)
        await db.commit()
        task_id = retained.id
        assert retained.animal_id is None and retained.purchase_batch_id is None
    completed = await client.post(f"/api/tasks/{task_id}/complete", headers=headers)
    assert completed.status_code == 409, completed.text
    assert "animal linked" in completed.json()["detail"]
    async with get_sessionmaker()() as db:
        actual = await db.get(Task, task_id)
        assert actual is not None and actual.status == "PENDING"
        assert actual.animal_id is None and actual.breeding_record_id is None
        assert actual.completed_by_id is None and actual.completed_at is None
        assert not list(
            (await db.execute(select(BucketMove.id).where(BucketMove.farm_id == farm_id))).scalars()
        )
