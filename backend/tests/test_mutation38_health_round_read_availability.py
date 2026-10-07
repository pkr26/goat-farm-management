"""Progress reads retain availability while a real writer owns the duty."""

import asyncio

import httpx
from sqlalchemy import select, text

from app.db import get_sessionmaker
from app.models import Task

from .conftest import owner_with_farm
from .test_health_safety import _seed_herd_round_task


async def test_public_round_progress_does_not_wait_for_the_owned_task_write_mutex(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    task_id = await _seed_herd_round_task(headers, "PPR vaccination round — all animals")
    async with get_sessionmaker()() as pin:
        task = (
            await pin.execute(select(Task).where(Task.id == task_id).with_for_update())
        ).scalar_one()
        assert task.status == "PENDING"
        pid = (await pin.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        request = asyncio.create_task(client.get(f"/api/health/rounds/{task_id}", headers=headers))
        blocked = False
        deadline = asyncio.get_running_loop().time() + 15
        try:
            async with get_sessionmaker()() as observer:
                while not request.done():
                    blocked = bool(
                        (
                            await observer.execute(
                                text(
                                    "SELECT EXISTS(SELECT 1 FROM pg_stat_activity "
                                    "WHERE datname=current_database() AND wait_event_type='Lock' "
                                    "AND :pid=ANY(pg_blocking_pids(pid)))"
                                ),
                                {"pid": pid},
                            )
                        ).scalar_one()
                    )
                    if blocked:
                        break
                    if asyncio.get_running_loop().time() >= deadline:
                        raise TimeoutError(
                            "Progress-read completion or a real lock witness was absent"
                        )
                    await asyncio.sleep(0.01)
        finally:
            await pin.rollback()
            response = await request
        assert not blocked, "A read-only progress request joined the Task writer's PostgreSQL queue"
        assert response.status_code == 200, response.text
        assert response.json()["task_id"] == task_id
        assert response.json()["task_status"] == "PENDING"
    async with get_sessionmaker()() as db:
        retained = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one()
        assert retained.status == "PENDING" and retained.completed_at is None
