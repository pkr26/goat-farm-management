"""A constraint-valid retained DONE duty repairs its personal role on rejection.

The documented pre-D9 fallback also accepts DONE rows with a missing role.
The pending-only check permits this restored assignment shape without DDL;
the real completion and retained membership are preserved throughout.
"""

import asyncio
from datetime import datetime

import httpx
import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

import app.api.tasks as tasks_api
from app.db import get_sessionmaker
from app.models import FarmMembership, Task
from app.services.tasks import resolve_personal_task_role_fallback

from .conftest import owner_with_farm
from .test_tasks_extended import complete_duty, make_duty, role_id, worker_headers


async def _retained_completed_personal_duty(
    client: httpx.AsyncClient,
) -> tuple[dict[str, str], int, int, int, int, datetime]:
    owner = await owner_with_farm(client)
    worker, worker_id = await worker_headers(client, owner, "CLEANER", "retained-role@farm.in")
    await worker_headers(client, owner, "FEEDER", "different-role@farm.in")
    expected_role = await role_id(client, owner, "CLEANER")
    duty = await make_duty(
        client,
        owner,
        "Retained personal cleaning",
        category="CLEANING",
        assigned_user_id=worker_id,
    )
    completed = await complete_duty(client, worker, duty["id"])
    assert completed.status_code == 200, completed.text
    task_id = int(duty["id"])
    async with get_sessionmaker()() as db:
        task = await db.get(Task, task_id)
        assert task is not None and task.status == "DONE"
        assert task.completed_by_id == worker_id and task.completed_at is not None
        completed_at = task.completed_at
        assert task.assigned_role_id == expected_role
        membership = (
            await db.execute(
                select(FarmMembership).where(
                    FarmMembership.farm_id == task.farm_id,
                    FarmMembership.user_id == worker_id,
                )
            )
        ).scalar_one()
        membership_id = membership.id
        assert membership.role_id == expected_role
        await db.execute(update(Task).where(Task.id == task_id).values(assigned_role_id=None))
        await db.commit()
    return owner, task_id, worker_id, expected_role, membership_id, completed_at


async def _assert_retained_rejection(
    response: httpx.Response,
    task_id: int,
    worker_id: int,
    expected_role: int,
    completed_at: datetime,
) -> None:
    assert response.status_code == 200, response.text
    wire = response.json()
    assert wire["id"] == task_id and wire["status"] == "PENDING"
    assert wire["assigned_user_id"] == worker_id
    assert wire["assigned_role_id"] == expected_role and wire["assigned_role_name"] == "Cleaner"
    async with get_sessionmaker()() as db:
        task = await db.get(Task, task_id)
        assert task is not None and task.status == "PENDING"
        assert task.assigned_role_id == expected_role and task.assigned_user_id == worker_id
        assert task.completed_by_id == worker_id and task.completed_at == completed_at
        assert task.rejected_by_id is not None and task.rejected_at is not None
        assert task.verification_note == "Please clean again"


async def test_public_rejection_repairs_the_real_retained_personal_role(
    client: httpx.AsyncClient,
) -> None:
    (
        owner,
        task_id,
        worker_id,
        expected_role,
        _membership_id,
        completed_at,
    ) = await _retained_completed_personal_duty(client)
    response = await client.post(
        f"/api/tasks/{task_id}/reject",
        headers=owner,
        json={"note": "Please clean again"},
    )
    await _assert_retained_rejection(response, task_id, worker_id, expected_role, completed_at)


async def test_retained_role_repair_remains_available_under_a_shared_membership_lease(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        owner,
        task_id,
        worker_id,
        expected_role,
        membership_id,
        completed_at,
    ) = await _retained_completed_personal_duty(client)
    reader_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()

    async def observed_fallback(db: AsyncSession, task: Task) -> int | None:
        pid = (await db.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        assert isinstance(pid, int)
        if not reader_pid.done():
            reader_pid.set_result(pid)
        return await resolve_personal_task_role_fallback(db, task)

    monkeypatch.setattr(tasks_api, "resolve_personal_task_role_fallback", observed_fallback)
    blocked = False
    async with get_sessionmaker()() as pin:
        membership = (
            await pin.execute(
                select(FarmMembership)
                .where(FarmMembership.id == membership_id)
                .with_for_update(read=True, key_share=True)
            )
        ).scalar_one()
        assert membership.role_id == expected_role and membership.user_id == worker_id
        holder_pid = (await pin.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        request = asyncio.create_task(
            client.post(
                f"/api/tasks/{task_id}/reject",
                headers=owner,
                json={"note": "Please clean again"},
            )
        )
        try:
            pid = await asyncio.wait_for(asyncio.shield(reader_pid), timeout=15)
            deadline = asyncio.get_running_loop().time() + 15
            async with get_sessionmaker()() as observer:
                while not request.done():
                    blocked = bool(
                        (
                            await observer.execute(
                                text(
                                    "SELECT EXISTS(SELECT 1 FROM pg_stat_activity "
                                    "WHERE datname=current_database() AND pid=:reader "
                                    "AND wait_event_type='Lock' "
                                    "AND :holder=ANY(pg_blocking_pids(pid)))"
                                ),
                                {"reader": pid, "holder": holder_pid},
                            )
                        ).scalar_one()
                    )
                    if blocked:
                        break
                    if asyncio.get_running_loop().time() >= deadline:
                        raise TimeoutError(
                            "No completed rejection or actual membership lock witness"
                        )
                    await asyncio.sleep(0.01)
        finally:
            await pin.rollback()
            response = await asyncio.wait_for(request, timeout=15)
    assert not blocked, "The retained role read joined a compatible shared membership lease"
    await _assert_retained_rejection(response, task_id, worker_id, expected_role, completed_at)
