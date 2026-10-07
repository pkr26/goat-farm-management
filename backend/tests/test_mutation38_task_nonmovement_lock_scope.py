"""A factual parent inspection never joins an unrelated dependent kid's writer queue."""

import asyncio
from datetime import date, timedelta

import httpx
from sqlalchemy import select, text

from app.db import get_sessionmaker
from app.models import Animal, Task
from app.utils import today

from .conftest import owner_with_farm
from .test_tasks_extended import make_duty, make_pregnancy, record_kidding


async def test_nonmovement_duty_completion_does_not_lock_a_dams_recovery_kid(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="nonmovement-parent-duty@farm.in")
    doe, breeding = await make_pregnancy(client, owner, today() - timedelta(days=150))
    birth = await record_kidding(
        client,
        owner,
        breeding,
        date.fromisoformat(breeding["expected_kidding_date"]),
        [{"tag": "PARENT-INSPECTION-KID", "sex": "F", "birth_weight": 2.5, "status": "ALIVE"}],
    )
    child_id = birth["kids"][0]["animal_id"]
    assert child_id is not None
    duty = await make_duty(
        client,
        owner,
        title="Inspect the doe without moving her family",
        category="OTHER",
        animal_id=doe["id"],
    )
    async with get_sessionmaker()() as pin:
        child = (
            await pin.execute(select(Animal).where(Animal.id == child_id).with_for_update())
        ).scalar_one()
        assert (
            child.dam_id == doe["id"]
            and child.current_bucket == "RECOVERY"
            and child.status == "ACTIVE"
        )
        holder_pid = (await pin.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        completion = asyncio.create_task(
            client.post(f"/api/tasks/{duty['id']}/complete", headers=owner)
        )
        blocked = False
        deadline = asyncio.get_running_loop().time() + 15
        try:
            async with get_sessionmaker()() as observer:
                while not completion.done():
                    blocked = bool(
                        (
                            await observer.execute(
                                text(
                                    "SELECT EXISTS(SELECT 1 FROM pg_stat_activity "
                                    "WHERE datname=current_database() AND wait_event_type='Lock' "
                                    "AND :holder=ANY(pg_blocking_pids(pid)))"
                                ),
                                {"holder": holder_pid},
                            )
                        ).scalar_one()
                    )
                    if blocked:
                        break
                    if asyncio.get_running_loop().time() >= deadline:
                        raise TimeoutError(
                            "Neither completion nor a dependent-row lock witness appeared"
                        )
                    await asyncio.sleep(0.01)
        finally:
            await pin.rollback()
            response = await completion
        assert not blocked, (
            "An ordinary nonmovement parent inspection joined its kid's PostgreSQL writer queue"
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "DONE"
    async with get_sessionmaker()() as db:
        task = await db.get(Task, duty["id"])
        retained_dam = await db.get(Animal, doe["id"])
        retained_child = await db.get(Animal, child_id)
        assert task is not None and task.status == "DONE" and task.completed_by_id is not None
        assert retained_dam is not None and retained_dam.current_bucket == "RECOVERY"
        assert retained_child is not None and retained_child.current_bucket == "RECOVERY"
        assert retained_child.status == "ACTIVE" and retained_child.dam_id == doe["id"]
