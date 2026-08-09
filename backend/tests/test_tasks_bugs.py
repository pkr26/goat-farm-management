"""REGRESSION SUITE — the tasks/duties app bugs documented here are FIXED.

Each test encodes the SPEC/contract expectation and FAILED against the old
app (an int32-overflow path id crashed with an asyncpg DataError → 500;
``reject_task`` flushed a PENDING personal duty with no role and violated
ck_tasks_user_assignment_has_role). Do not weaken them.
"""

import httpx
from sqlalchemy import select, text, update

from app.db import get_sessionmaker
from app.models import Task, TaskStatus, User
from app.services.tasks import reject_task

from .conftest import owner_with_farm
from .test_tasks_extended import complete_duty, make_duty, role_id, worker_headers


# FIXED — regression test
# Endpoint: POST /api/tasks/{task_id}/complete (and skip/verify/reject — same
# `_get_task` lookup path).
# Repro: a task id larger than int32 (e.g. 10**20) used to reach SQLAlchemy
# unbounded, and asyncpg raised DataError "value out of int32 range"
# (tasks.id is INTEGER/int4) → unhandled 500. `_get_task` now treats ids
# above the int4 PK ceiling as not found → 404 "Task not found".
async def test_huge_task_id_rejected_not_500(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await client.post(f"/api/tasks/{10**20}/complete", headers=owner)
    assert resp.status_code in (404, 422)


# FIXED — regression test
# Service: app.services.tasks.reject_task
# Repro: reject_task set `status = PENDING` — the exact state
# ck_tasks_user_assignment_has_role constrains — without resolving a role, so a
# pre-D9 personal duty (assigned_user_id set, assigned_role_id NULL) violated
# the constraint on flush. Only /api/tasks/{id}/reject repaired the row first,
# leaving the invariant dependent on that one caller. reject_task now repairs
# the row itself, like spawn_next_occurrence does for its own PENDING insert.
async def test_reject_task_service_repairs_a_legacy_personal_duty(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    worker, worker_id = await worker_headers(client, owner, "CLEANER", "legacy-svc@farm.in")
    cleaner_role = await role_id(client, owner, "CLEANER")
    duty = await make_duty(
        client, owner, "Legacy scrub", category="CLEANING", assigned_user_id=worker_id
    )
    assert (await complete_duty(client, worker, duty["id"])).status_code == 200
    # Only the constraint's own DDL can produce the pre-D9 shape; it is put back
    # verbatim so the row rejected below is validated against production
    # semantics.
    async with get_sessionmaker()() as db:
        await db.execute(
            text("ALTER TABLE tasks DROP CONSTRAINT ck_tasks_user_assignment_has_role")
        )
        await db.execute(update(Task).where(Task.id == duty["id"]).values(assigned_role_id=None))
        await db.execute(
            text(
                "ALTER TABLE tasks ADD CONSTRAINT ck_tasks_user_assignment_has_role "
                "CHECK (status <> 'PENDING' OR assigned_user_id IS NULL "
                "OR assigned_role_id IS NOT NULL) NOT VALID"
            )
        )
        await db.commit()

    # The service is called directly — no route, so nothing else resolves the
    # role for it.
    async with get_sessionmaker()() as db:
        verifier = (
            await db.execute(select(User).where(User.email == "owner@farm.in"))
        ).scalar_one()
        verifier_id = verifier.id
        task = await db.get(Task, duty["id"])
        assert task is not None
        await reject_task(db, task, verifier, "redo")
        await db.commit()

    async with get_sessionmaker()() as db:
        stored = await db.get(Task, duty["id"])
        assert stored is not None
        assert stored.status == TaskStatus.PENDING.value
        assert stored.assigned_role_id == cleaner_role
        assert stored.rejected_by_id == verifier_id
        assert stored.rejected_at is not None
