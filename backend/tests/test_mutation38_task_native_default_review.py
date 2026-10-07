"""The documented native verification default continues a real recurring duty."""

import httpx
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import Farm, Task, User
from app.services.tasks import lock_manual_task_queue, verify_task
from app.utils import today

from .conftest import owner_with_farm
from .test_tasks_extended import complete_duty, make_duty


async def test_native_verification_with_omitted_spawn_flag_continues_the_actual_series(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client, email="native-default-review@example.test")
    duty = await make_duty(
        client, headers, "Native default review", category="CLEANING", recur_days=1
    )
    completed = await complete_duty(client, headers, duty["id"])
    assert completed.status_code == 200, completed.text
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, int(headers["X-Farm-Id"]))
        assert farm is not None
        owner = await db.get(User, farm.owner_id)
        assert owner is not None
        # The documented native interface requires FARM before TASK whenever
        # verification may spawn. This unlinked duty has no ANIMAL lock.
        await lock_manual_task_queue(db, farm)
        task = (
            await db.execute(select(Task).where(Task.id == duty["id"]).with_for_update())
        ).scalar_one()
        assert task.status == "DONE" and task.recur_days == 1
        assert task.animal_id is None and task.needs_verification
        completed_by, completed_at = task.completed_by_id, task.completed_at
        series_id = task.recurring_series_id
        # Deliberately omit the optional flag: None's published meaning is
        # to spawn whenever the actual reviewed duty recurs.
        reviewed = await verify_task(db, task, owner)
        assert reviewed is task
        await db.commit()
    async with get_sessionmaker()() as db:
        rows = list(
            (
                await db.execute(
                    select(Task)
                    .where(Task.recurring_series_id == series_id)
                    .order_by(Task.due_date, Task.id)
                )
            ).scalars()
        )
        assert len(rows) == 2, [(row.id, row.status) for row in rows]
        original = next(row for row in rows if row.id == duty["id"])
        pending = [row for row in rows if row.status == "PENDING"]
        assert original.status == "VERIFIED" and len(pending) == 1
        assert original.verified_by_id == farm.owner_id and original.verified_at is not None
        assert (original.completed_by_id, original.completed_at) == (completed_by, completed_at)
        successor = pending[0]
        assert successor.due_date > today() and successor.recur_days == 1
        assert successor.animal_id is None
        assert successor.category == original.category == "CLEANING"
        assert successor.created_by_id == original.created_by_id
