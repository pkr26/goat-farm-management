"""Native task visibility uses the actual viewer's active farm membership.

The inactive-viewer case exercises the explicitly supported task_scope helper
boundary documented and covered by test_task_visibility_parity. It does not
grant a deactivated worker access through the public farm authorization gate.
"""

import httpx
import pytest
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import Farm, FarmMembership, Task, User
from app.services.tasks import task_scope

from .conftest import owner_with_farm, provisioned_worker_login
from .test_tasks_extended import WORKER_PW, add_worker, make_custom_role, make_duty


async def test_native_active_worker_sees_own_role_and_personal_duties_only(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="native-task-scope-owner@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    role = await make_custom_role(client, owner, "Actual viewer role", ["tasks.view"])
    other_role = await make_custom_role(client, owner, "Another worker role", ["tasks.view"])
    email = "native-task-scope-viewer@farm.in"
    await add_worker(client, owner, role, email)
    await add_worker(client, owner, other_role, "native-task-scope-peer@farm.in")
    headers, user_id = await provisioned_worker_login(client, email, WORKER_PW)
    worker_headers = headers | {"X-Farm-Id": str(farm_id)}
    role_duty = await make_duty(client, owner, title="Actual role duty", assigned_role_id=role)
    personal = await make_duty(
        client,
        owner,
        title="Actual personal duty",
        assigned_role_id=role,
        assigned_user_id=user_id,
    )
    owner_only = await make_duty(client, owner, title="Unassigned owner duty")
    peer = await make_duty(client, owner, title="Other role duty", assigned_role_id=other_role)
    expected = {role_duty["id"], personal["id"]}
    response = await client.get("/api/tasks", headers=worker_headers)
    assert response.status_code == 200, response.text
    listed = {row["id"] for row in response.json()["today"]}
    assert expected <= listed, "active worker must retain actual role and personal work"
    assert owner_only["id"] not in listed and peer["id"] not in listed
    async with get_sessionmaker()() as observer:
        farm = await observer.get(Farm, farm_id)
        user = await observer.get(User, user_id)
        assert farm is not None and user is not None
        try:
            rows = list((await observer.execute(await task_scope(observer, farm, user))).scalars())
        except (AttributeError, TypeError) as exc:
            pytest.fail(f"A native active viewer's task scope must execute: {exc}")
        assert {row.id for row in rows} == expected


async def test_public_deactivation_leaves_supported_helper_with_personal_scope_only(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="native-inactive-scope-owner@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    role = await make_custom_role(client, owner, "Deactivated viewer role", ["tasks.view"])
    email = "native-inactive-scope-viewer@farm.in"
    await add_worker(client, owner, role, email)
    headers, user_id = await provisioned_worker_login(client, email, WORKER_PW)
    role_duty = await make_duty(client, owner, title="Old role duty", assigned_role_id=role)
    personal = await make_duty(
        client,
        owner,
        title="Retained personal duty",
        assigned_role_id=role,
        assigned_user_id=user_id,
    )
    async with get_sessionmaker()() as observer:
        membership = (
            await observer.execute(
                select(FarmMembership).where(
                    FarmMembership.farm_id == farm_id, FarmMembership.user_id == user_id
                )
            )
        ).scalar_one()
        membership_id = membership.id
    deactivated = await client.put(
        f"/api/team/workers/{membership_id}/status", json={"is_active": False}, headers=owner
    )
    assert deactivated.status_code == 200, deactivated.text
    denied = await client.get("/api/tasks", headers=headers | {"X-Farm-Id": str(farm_id)})
    assert denied.status_code == 404, denied.text
    async with get_sessionmaker()() as observer:
        retained_membership = await observer.get(FarmMembership, membership_id)
        farm = await observer.get(Farm, farm_id)
        user = await observer.get(User, user_id)
        assert retained_membership is not None and not retained_membership.is_active
        assert farm is not None and user is not None
        try:
            rows = list((await observer.execute(await task_scope(observer, farm, user))).scalars())
        except (AttributeError, TypeError) as exc:
            pytest.fail(f"The supported inactive-viewer helper must retain personal scope: {exc}")
        assert {row.id for row in rows} == {personal["id"]}
        assert role_duty["id"] not in {row.id for row in rows}
        assert all(isinstance(row, Task) for row in rows)
