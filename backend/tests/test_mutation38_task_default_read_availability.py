"""A default native duty read preserves availability under a real writer lock."""

import asyncio

import httpx
from sqlalchemy import select, text

from app.api.tasks import _get_task
from app.db import get_sessionmaker
from app.models import Farm, Task

from .conftest import owner_with_farm
from .test_tasks_extended import make_duty


async def test_default_native_task_read_does_not_join_an_existing_writer_mutex(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="native-duty-read@farm.in")
    duty = await make_duty(client, owner, title="Read this actual manual duty")
    task_id = int(duty["id"])
    async with get_sessionmaker()() as pin, get_sessionmaker()() as reader:
        locked = (
            await pin.execute(select(Task).where(Task.id == task_id).with_for_update())
        ).scalar_one()
        assert locked.status == "PENDING" and locked.title == "Read this actual manual duty"
        holder_pid = (await pin.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        farm = await reader.get(Farm, int(owner["X-Farm-Id"]))
        assert farm is not None
        reader_pid = (await reader.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        read = asyncio.create_task(_get_task(reader, farm, task_id))
        blocked = False
        deadline = asyncio.get_running_loop().time() + 15
        try:
            async with get_sessionmaker()() as observer:
                while not read.done():
                    blocked = bool(
                        (
                            await observer.execute(
                                text(
                                    "SELECT EXISTS(SELECT 1 FROM pg_stat_activity "
                                    "WHERE datname=current_database() AND pid=:reader "
                                    "AND wait_event_type='Lock' "
                                    "AND :holder=ANY(pg_blocking_pids(pid)))"
                                ),
                                {"reader": reader_pid, "holder": holder_pid},
                            )
                        ).scalar_one()
                    )
                    if blocked:
                        break
                    if asyncio.get_running_loop().time() >= deadline:
                        raise TimeoutError(
                            "Neither a completed duty read nor a real lock witness appeared"
                        )
                    await asyncio.sleep(0.01)
        finally:
            await pin.rollback()
            observed = await read
        assert not blocked, "The default native duty read joined another writer's PostgreSQL mutex"
        assert observed.id == task_id and observed.title == "Read this actual manual duty"
        assert observed.status == "PENDING" and observed.completed_at is None
        assert observed.farm_id == farm.id
    async with get_sessionmaker()() as check:
        retained = await check.get(Task, task_id)
        assert retained is not None
        assert retained.status == "PENDING" and retained.title == "Read this actual manual duty"
        assert retained.completed_at is None and retained.completed_by_id is None
