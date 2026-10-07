"""Supported pre-D9 terminal personal duties repair their retained role.

The native initial DONE row represents retained history, with constraints and
real attribution enabled. Current public creation supplies a role, so this is
the documented native verify_task/spawn compatibility boundary, not an ordinary
current API producer of a missing role. No existing identity/cache is rewritten.
"""

from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import Farm, FarmMembership, Role, Task, User
from app.services.tasks import lock_manual_task_queue, verify_task
from app.utils import today, utcnow

from .conftest import owner_with_farm
from .test_tasks_extended import add_worker, make_custom_role


@pytest.mark.parametrize(
    "inactive", [False, True], ids=["active-member", "retained-inactive-member"]
)
async def test_retained_terminal_personal_recurrence_repairs_role_and_spawns_one_successor(
    client: httpx.AsyncClient, inactive: bool
) -> None:
    label = "inactive" if inactive else "active"
    owner = await owner_with_farm(client, email=f"retained-recurrence-{label}-owner@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    role_id = await make_custom_role(client, owner, "Retained cleaning role", ["tasks.view"])
    email = f"retained-recurrence-{label}-worker@farm.in"
    await add_worker(client, owner, role_id, email)
    async with get_sessionmaker()() as db:
        membership = (
            await db.execute(
                select(FarmMembership)
                .join(User)
                .where(FarmMembership.farm_id == farm_id, User.email == email)
            )
        ).scalar_one()
        membership_id, worker_id = membership.id, membership.user_id
    if inactive:
        response = await client.put(
            f"/api/team/workers/{membership_id}/status", json={"is_active": False}, headers=owner
        )
        assert response.status_code == 200, response.text
    series = str(uuid4())
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        # Initial supported retained terminal history, not a prohibited
        # PENDING row or a rewrite of a newly created duty's assignment.
        historical = Task(
            farm_id=farm_id,
            title="Retained completed personal cleaning",
            due_date=today(farm.timezone) - timedelta(days=1),
            category="CLEANING",
            status="DONE",
            auto_generated=False,
            assigned_role_id=None,
            assigned_user_id=worker_id,
            completed_by_id=worker_id,
            completed_at=utcnow(),
            recur_days=1,
            recurring_series_id=series,
            created_by_id=farm.owner_id,
        )
        db.add(historical)
        await db.commit()
        task_id = historical.id
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        await lock_manual_task_queue(db, farm)
        task = (
            await db.execute(select(Task).where(Task.id == task_id).with_for_update())
        ).scalar_one()
        retained_membership = await db.get(FarmMembership, membership_id)
        owner_user = await db.get(User, farm.owner_id)
        assert retained_membership is not None and retained_membership.role_id == role_id
        assert retained_membership.is_active is (not inactive)
        assert owner_user is not None
        assert task.status == "DONE" and task.assigned_role_id is None
        assert task.completed_by_id == worker_id and task.completed_at is not None
        try:
            await verify_task(db, task, owner_user)
        except ValueError as exc:
            pytest.fail(
                f"A valid retained personal recurrence must repair its retained role: {exc}"
            )
        assert task.status == "VERIFIED" and task.assigned_role_id == role_id
        assert task.assigned_role is not None
        assert task.assigned_role.name == "Retained cleaning role"
        assert task.verified_by_id == farm.owner_id and task.verified_at is not None
        await db.commit()
    async with get_sessionmaker()() as db:
        occurrences = list(
            (
                await db.execute(
                    select(Task).where(Task.farm_id == farm_id, Task.recurring_series_id == series)
                )
            ).scalars()
        )
        assert len(occurrences) == 2
        successor = next(task for task in occurrences if task.id != task_id)
        source = next(task for task in occurrences if task.id == task_id)
        assert source.status == "VERIFIED" and source.assigned_role_id == role_id
        assert successor.status == "PENDING" and successor.assigned_role_id == role_id
        assert successor.assigned_user_id == worker_id and successor.recur_days == 1
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        assert successor.due_date == today(farm.timezone) + timedelta(days=1)
        assert successor.created_by_id == farm.owner_id
        assert await db.get(Role, role_id) is not None
