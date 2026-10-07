"""An independent team-provisioning lease does not serialize the manual-duty queue."""

import asyncio

import httpx
from sqlalchemy import text

from app.api.team import _lock_farm_provisioning
from app.db import get_sessionmaker
from app.models import Farm, Task
from app.utils import today

from .conftest import owner_with_farm


async def test_public_manual_duty_creation_does_not_join_the_team_provisioning_mutex(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="independent-duty-provisioning@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    title = "Create an inspection while the team queue is held"
    async with get_sessionmaker()() as pin:
        farm = await pin.get(Farm, farm_id)
        assert farm is not None
        # Use the genuine team lease primitive and its compatible Farm KEY
        # SHARE pin, preserving the documented native provisioning ordering.
        await _lock_farm_provisioning(pin, farm)
        holder_pid = await pin.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(holder_pid, int)
        creation = asyncio.create_task(
            client.post(
                "/api/tasks",
                headers=owner,
                json={"title": title, "due_date": today().isoformat()},
            )
        )
        blocked = False
        deadline = asyncio.get_running_loop().time() + 15
        try:
            async with get_sessionmaker()() as observer:
                while not creation.done():
                    blocked = bool(
                        await observer.scalar(
                            text(
                                "SELECT EXISTS(SELECT 1 FROM pg_stat_activity "
                                "WHERE datname=current_database() AND wait_event_type='Lock' "
                                "AND :holder=ANY(pg_blocking_pids(pid)))"
                            ),
                            {"holder": holder_pid},
                        )
                    )
                    if blocked:
                        break
                    if asyncio.get_running_loop().time() >= deadline:
                        raise TimeoutError("Neither duty creation nor a real queue wait appeared")
                    await asyncio.sleep(0.01)
        finally:
            await pin.rollback()
            response = await creation
        assert not blocked, (
            "The manual-duty request joined an unrelated team-provisioning PostgreSQL mutex"
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["title"] == title and body["status"] == "PENDING"
    async with get_sessionmaker()() as db:
        task = await db.get(Task, body["id"])
        assert task is not None and task.farm_id == farm_id and task.title == title
        assert task.status == "PENDING" and task.completed_at is None
        assert not task.auto_generated and task.created_by_id is not None
