"""Manual task assignments preserve valid identities and compatible shared readers.

The lease cases use independent native reference readers, not competing task
creators: the latter serialize through the farm queue mutex. All eligibility
facts and tasks are real committed PostgreSQL rows with enabled constraints.
"""

import asyncio
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select, text

from app.db import get_sessionmaker
from app.models import Animal, FarmMembership, Role, Task, User
from app.security import hash_password
from app.utils import today

from .conftest import owner_with_farm
from .test_concurrency import make_animal
from .test_tasks_extended import role_id

MAX_KEY = 2_147_483_647


def _payload(title: str, **assignment: int | None) -> dict[str, Any]:
    return {"title": title, "due_date": today().isoformat(), "category": "OTHER"} | assignment


async def _viewer(client: httpx.AsyncClient, owner: dict[str, str]) -> tuple[int, int, int]:
    viewer_role = await role_id(client, owner, "VIEWER")
    response = await client.post(
        "/api/team/workers",
        headers=owner,
        json={
            "name": "Assignment reader",
            "email": "assignment-reader@farm.in",
            "password": "worker-valid-secret-123",
            "role_id": viewer_role,
        },
    )
    assert response.status_code == 201, response.text
    row = response.json()
    return int(row["id"]), int(row["user_id"]), viewer_role


async def _count_tasks(farm_id: int) -> int:
    async with get_sessionmaker()() as db:
        return int(
            await db.scalar(select(func.count()).select_from(Task).where(Task.farm_id == farm_id))
            or 0
        )


async def _persisted_assignment(
    response: httpx.Response,
    farm_id: int,
    *,
    animal_id: int | None = None,
    user_id: int | None = None,
    assigned_role_id: int | None = None,
) -> None:
    assert response.status_code == 201, response.text
    row = response.json()
    assert row["animal_id"] == animal_id
    assert row["assigned_user_id"] == user_id
    assert row["assigned_role_id"] == assigned_role_id
    assert row["status"] == "PENDING" and row["auto_generated"] is False
    async with get_sessionmaker()() as db:
        task = await db.get(Task, int(row["id"]))
        assert task is not None and task.farm_id == farm_id
        assert task.animal_id == animal_id and task.assigned_user_id == user_id
        assert task.assigned_role_id == assigned_role_id and task.status == "PENDING"
        assert task.created_by_id is not None and task.auto_generated is False


@pytest.mark.parametrize("target", ["animal", "membership", "user", "role"])
async def test_manual_task_admission_coexists_with_independent_shared_reference_reader(
    client: httpx.AsyncClient, target: str
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    animal_id = await make_animal(client, owner, tag="SHARED-ASSIGNMENT-ANIMAL")
    membership_id, worker_id, viewer_role = await _viewer(client, owner)
    request: asyncio.Task[httpx.Response] | None = None
    blocked_by_reader: list[int] = []
    async with get_sessionmaker()() as reader, get_sessionmaker()() as observer:
        if target == "animal":
            row = await reader.scalar(
                select(Animal).where(Animal.id == animal_id).with_for_update(read=True)
            )
        elif target == "membership":
            row = await reader.scalar(
                select(FarmMembership)
                .where(FarmMembership.id == membership_id)
                .with_for_update(read=True)
            )
        elif target == "user":
            row = await reader.scalar(
                select(User).where(User.id == worker_id).with_for_update(read=True)
            )
        else:
            row = await reader.scalar(
                select(Role).where(Role.id == viewer_role).with_for_update(read=True)
            )
        assert row is not None
        reader_pid = await reader.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(reader_pid, int)
        request = asyncio.create_task(
            client.post(
                "/api/tasks",
                headers=owner | {"Idempotency-Key": f"shared-reference-{target}"},
                json=_payload(
                    f"Shared {target} reference duty",
                    animal_id=animal_id,
                    assigned_user_id=worker_id,
                    assigned_role_id=viewer_role,
                ),
            )
        )
        try:
            deadline = asyncio.get_running_loop().time() + 30
            try:
                while not request.done():
                    blocked_by_reader = list(
                        (
                            await observer.execute(
                                text(
                                    "SELECT pid FROM pg_stat_activity "
                                    "WHERE datname = current_database() "
                                    "AND wait_event_type = 'Lock' "
                                    "AND :reader_pid = ANY(pg_blocking_pids(pid))"
                                ),
                                {"reader_pid": reader_pid},
                            )
                        ).scalars()
                    )
                    if blocked_by_reader:
                        break
                    if asyncio.get_running_loop().time() >= deadline:
                        # A scheduling/resource deadline is infrastructure,
                        # never an assertion-based mutation kill.
                        raise TimeoutError(
                            "Task admission produced neither a response nor a reader-lock witness"
                        )
                    await asyncio.sleep(0.02)
            finally:
                # Release only the lease this test owns, including failures.
                await reader.rollback()
            response = await asyncio.wait_for(request, timeout=30)
        finally:
            await reader.rollback()
            if not request.done():
                request.cancel()
            await asyncio.gather(request, return_exceptions=True)
    await _persisted_assignment(
        response,
        farm_id,
        animal_id=animal_id,
        user_id=worker_id,
        assigned_role_id=viewer_role,
    )
    assert not blocked_by_reader, (
        f"Eligibility admission needlessly waited for the compatible {target} reader: "
        f"holder_pid={reader_pid}, blocked_pids={blocked_by_reader}"
    )


@pytest.mark.parametrize("target", ["animal", "user", "role"])
async def test_manual_assignment_admits_real_constraint_valid_int4_ceiling_identity(
    client: httpx.AsyncClient, target: str
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    viewer_role = await role_id(client, owner, "VIEWER")
    assignment: dict[str, int | None] = {}
    async with get_sessionmaker()() as db:
        if target == "animal":
            db.add(
                Animal(
                    id=MAX_KEY,
                    farm_id=farm_id,
                    tag_number="RETAINED-MAX-TASK-ANIMAL",
                    sex="F",
                    source="PURCHASED",
                    current_bucket="FOUNDATION",
                    status="ACTIVE",
                )
            )
            assignment["animal_id"] = MAX_KEY
        elif target == "user":
            db.add(
                User(
                    id=MAX_KEY,
                    email="retained-max-task-worker@farm.in",
                    name="Retained worker",
                    password_hash=hash_password("retained-valid-worker-secret-123"),
                )
            )
            await db.flush()
            db.add(
                FarmMembership(
                    farm_id=farm_id, user_id=MAX_KEY, role_id=viewer_role, is_active=True
                )
            )
            assignment["assigned_user_id"] = MAX_KEY
        else:
            db.add(
                Role(id=MAX_KEY, farm_id=farm_id, name="Retained maximum role", permissions="[]")
            )
            assignment["assigned_role_id"] = MAX_KEY
        # Explicit retained identities do not change any PostgreSQL sequence.
        await db.commit()
    response = await client.post(
        "/api/tasks", headers=owner, json=_payload(f"Maximum {target} assignment", **assignment)
    )
    await _persisted_assignment(
        response,
        farm_id,
        animal_id=MAX_KEY if target == "animal" else None,
        user_id=MAX_KEY if target == "user" else None,
        assigned_role_id=viewer_role if target == "user" else MAX_KEY if target == "role" else None,
    )


@pytest.mark.parametrize("state", ["other-farm", "sold"])
async def test_invalid_animal_assignment_is_400_atomic_and_failed_key_can_retry(
    client: httpx.AsyncClient, state: str
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    if state == "other-farm":
        other = await owner_with_farm(client, email="other-assignment-owner@farm.in")
        animal_id = await make_animal(client, other, tag="OTHER-FARM-ASSIGNMENT")
    else:
        animal_id = await make_animal(client, owner, tag="SOLD-ASSIGNMENT")
        sale = await client.post(
            f"/api/animals/{animal_id}/status",
            headers=owner,
            json={"new_status": "SOLD", "date": today().isoformat(), "sale_price": 1},
        )
        assert sale.status_code == 200, sale.text
    before = await _count_tasks(farm_id)
    headers = owner | {"Idempotency-Key": f"invalid-animal-assignment-{state}"}
    response = await client.post(
        "/api/tasks",
        headers=headers,
        json=_payload(f"Rejected {state} assignment", animal_id=animal_id),
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "Assigned animal is not active on this farm"
    assert await _count_tasks(farm_id) == before
    retry = await client.post(
        "/api/tasks", headers=headers, json=_payload(f"Rejected {state} assignment")
    )
    await _persisted_assignment(retry, farm_id)
    assert await _count_tasks(farm_id) == before + 1


async def test_role_only_manual_assignment_succeeds_without_a_worker_membership(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    viewer_role = await role_id(client, owner, "VIEWER")
    response = await client.post(
        "/api/tasks",
        headers=owner,
        json=_payload("Read-only audit role duty", assigned_role_id=viewer_role),
    )
    await _persisted_assignment(response, int(owner["X-Farm-Id"]), assigned_role_id=viewer_role)


async def test_worker_and_explicit_different_valid_role_are_rejected_without_a_task(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    _membership, worker_id, viewer_role = await _viewer(client, owner)
    other_role = await role_id(client, owner, "FEEDER")
    assert other_role != viewer_role
    before = await _count_tasks(farm_id)
    response = await client.post(
        "/api/tasks",
        headers=owner,
        json=_payload(
            "Inconsistent operational assignment",
            assigned_user_id=worker_id,
            assigned_role_id=other_role,
        ),
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "Assigned worker does not hold the assigned role"
    assert await _count_tasks(farm_id) == before
