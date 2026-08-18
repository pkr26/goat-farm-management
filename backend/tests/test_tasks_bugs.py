"""REGRESSION SUITE — the tasks/duties app bugs documented here are FIXED.

Each test encodes the SPEC/contract expectation and FAILED against the old
app (an int32-overflow path id crashed with an asyncpg DataError → 500;
``reject_task`` flushed a PENDING personal duty with no role and violated
ck_tasks_user_assignment_has_role). Do not weaken them.
"""

from datetime import date, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select, text, update

import app.services.tasks as task_service
from app.core.config import get_settings
from app.db import get_sessionmaker
from app.models import Task, TaskStatus, User
from app.services.tasks import reject_task, spawn_next_occurrence
from app.utils import today, utcnow

from .conftest import owner_with_farm
from .test_tasks_extended import complete_duty, make_animal, make_duty, role_id, worker_headers


@pytest.mark.parametrize("recur_days", [0, -1, -3650])
async def test_legacy_nonpositive_recurrence_is_bounded_without_date_overflow(
    recur_days: int,
) -> None:
    """Malformed legacy intervals terminate the series instead of doing date math.

    A negative interval used to pass the upper-bound-only service guard. Near
    ``date.max`` its subsequent subtraction overflowed before the intended
    representability check, turning completion/skip into an unhandled error.
    """
    task = Task(
        farm_id=1,
        title="Malformed legacy recurrence",
        due_date=date.max,
        status=TaskStatus.PENDING.value,
        category="OTHER",
        auto_generated=False,
        recur_days=recur_days,
        recurring_series_id="malformed-legacy-series",
    )
    db = AsyncMock()

    assert await spawn_next_occurrence(db, task) is task
    db.execute.assert_not_awaited()


async def test_serialized_recurring_actions_across_midnight_reuse_live_successor(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A queue-lock wait crossing midnight must not fork one recurrence series."""
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    series_id = "cross-midnight-series"
    base_date = today()
    async with get_sessionmaker()() as db:
        occurrences = [
            Task(
                farm_id=farm_id,
                title="Cross-midnight clean",
                due_date=base_date - timedelta(days=days_ago),
                status=TaskStatus.DONE.value,
                category="CLEANING",
                auto_generated=False,
                recur_days=1,
                recurring_series_id=series_id,
                completed_at=utcnow(),
            )
            for days_ago in (2, 1)
        ]
        db.add_all(occurrences)
        await db.commit()
        occurrence_ids = [task.id for task in occurrences]

    monkeypatch.setattr(task_service, "today", lambda _timezone=None: base_date)
    first = await client.post(f"/api/tasks/{occurrence_ids[0]}/verify", headers=owner)
    assert first.status_code == 200, first.text

    # Model the second request acquiring the farm queue mutex just after the
    # farm's date rolled over. Its calculated due date would differ, so the DB
    # per-date unique constraint alone cannot collapse the pair.
    monkeypatch.setattr(
        task_service,
        "today",
        lambda _timezone=None: base_date + timedelta(days=1),
    )
    second = await client.post(f"/api/tasks/{occurrence_ids[1]}/verify", headers=owner)
    assert second.status_code == 200, second.text

    rows = await _series_rows(series_id)
    assert [row.status for row in rows].count(TaskStatus.VERIFIED.value) == 2
    live = [row for row in rows if row.status == TaskStatus.PENDING.value]
    assert len(live) == 1
    assert live[0].due_date == base_date + timedelta(days=1)


async def test_duplicate_recurring_verification_reuses_successor_at_capacity(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reusing a live successor is queue-neutral even when no slot is free."""
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    series_id = "capacity-reuse-series"
    monkeypatch.setattr(get_settings(), "max_pending_manual_tasks_per_farm", 1)
    async with get_sessionmaker()() as db:
        occurrences = [
            Task(
                farm_id=farm_id,
                title="Capacity reuse clean",
                due_date=today() - timedelta(days=days_ago),
                status=TaskStatus.DONE.value,
                category="CLEANING",
                auto_generated=False,
                recur_days=1,
                recurring_series_id=series_id,
                completed_at=utcnow(),
            )
            for days_ago in (2, 1)
        ]
        db.add_all(occurrences)
        await db.commit()
        occurrence_ids = [task.id for task in occurrences]

    first = await client.post(f"/api/tasks/{occurrence_ids[0]}/verify", headers=owner)
    assert first.status_code == 200, first.text
    # The first successor now occupies the only manual PENDING slot. The second
    # verification closes history and points at that same row; it adds nothing.
    second = await client.post(f"/api/tasks/{occurrence_ids[1]}/verify", headers=owner)
    assert second.status_code == 200, second.text

    rows = await _series_rows(series_id)
    assert [row.status for row in rows].count(TaskStatus.VERIFIED.value) == 2
    assert [row.status for row in rows].count(TaskStatus.PENDING.value) == 1


async def _series_rows(series_id: str) -> list[Task]:
    """All occurrences of one recurring series, oldest due date first."""
    async with get_sessionmaker()() as db:
        return list(
            (
                await db.execute(
                    select(Task)
                    .where(Task.recurring_series_id == series_id)
                    .order_by(Task.due_date, Task.id)
                )
            ).scalars()
        )


async def _rewind_series_one_day(series_id: str) -> None:
    """Simulate the business day advancing between two actions on a series
    without touching the farm clock: shift every row one day into the past, so
    "today" sits one day beyond the occurrence's due date exactly as in the
    real cross-day reject → re-action workflow."""
    async with get_sessionmaker()() as db:
        rows = list(
            (await db.execute(select(Task).where(Task.recurring_series_id == series_id))).scalars()
        )
        for row in rows:
            row.due_date = row.due_date - timedelta(days=1)
        await db.commit()


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
        await db.execute(
            text("ALTER TABLE tasks VALIDATE CONSTRAINT ck_tasks_user_assignment_has_role")
        )
        await db.commit()


# FIXED — regression test
# Service: app.services.tasks.complete_task / verify_task.
# Repro: complete_task spawned a recurring successor the moment a CLEANING
# occurrence went DONE — but DONE only means "awaiting verification" for
# verification-required categories, so a verifier reject plus a re-completion
# on a LATER business day re-anchored spawn_next_occurrence on the later day
# and minted a SECOND successor at a different due date. The
# (farm_id, recurring_series_id, due_date) dedup cannot collapse two different
# dates, so both successor threads survived and the series' weekly cadence
# doubled permanently. The successor now spawns on the terminal transitions
# (verify/skip), never on DONE.
async def test_reject_then_cross_day_recompletion_does_not_double_the_series(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    manager, _ = await worker_headers(client, owner, "CLEANER_MANAGER", "cm@farm.in")
    duty = await make_duty(client, owner, "Weekly scrub", category="CLEANING", recur_days=7)
    series_id = duty["recurring_series_id"]

    assert (await complete_duty(client, owner, duty["id"])).status_code == 200
    rejected = await client.post(
        f"/api/tasks/{duty['id']}/reject", json={"note": "redo"}, headers=manager
    )
    assert rejected.status_code == 200, rejected.text

    # The worker only gets to the redo the next day.
    await _rewind_series_one_day(series_id)

    assert (await complete_duty(client, owner, duty["id"])).status_code == 200
    verified = await client.post(f"/api/tasks/{duty['id']}/verify", headers=manager)
    assert verified.status_code == 200, verified.text

    rows = await _series_rows(series_id)
    assert [row.status for row in rows] == [
        TaskStatus.VERIFIED.value,
        TaskStatus.PENDING.value,
    ]  # one verified occurrence, exactly ONE successor thread
    assert rows[-1].due_date == today() + timedelta(days=7)


# FIXED — regression test
# Endpoint: POST /api/tasks/{task_id}/complete then /verify.
# Repro: the successor of a verification-required recurring duty appeared as
# soon as the occurrence was marked DONE, i.e. before anyone reviewed the
# work — which is exactly what made the reject workflow able to duplicate the
# series. The spawn is deferred to the VERIFIED transition.
async def test_verification_required_recurrence_spawns_on_verify_not_on_done(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    manager, _ = await worker_headers(client, owner, "CLEANER_MANAGER", "cm@farm.in")
    duty = await make_duty(client, owner, "Daily scrub", category="CLEANING", recur_days=1)
    series_id = duty["recurring_series_id"]

    assert (await complete_duty(client, owner, duty["id"])).status_code == 200
    # DONE only means "awaiting verification" for CLEANING: no successor yet.
    assert [row.status for row in await _series_rows(series_id)] == [TaskStatus.DONE.value]

    verified = await client.post(f"/api/tasks/{duty['id']}/verify", headers=manager)
    assert verified.status_code == 200, verified.text
    rows = await _series_rows(series_id)
    assert [row.status for row in rows] == [
        TaskStatus.VERIFIED.value,
        TaskStatus.PENDING.value,
    ]
    assert rows[-1].due_date == today() + timedelta(days=1)


# FIXED — regression test
# Endpoint: POST /api/tasks/{task_id}/skip after a reject.
# Repro: same duplication through the other re-action: complete (spawn #1 on
# the old code), reject back to PENDING, then SKIP on a later business day —
# skip_task always spawns to keep the series alive, so the old completion-time
# spawn left two successor threads one day apart. With the spawn moved to the
# terminal transitions, the rejected-then-skipped occurrence produces exactly
# one successor (from skip), and verify/skip can never both fire for one
# occurrence (verify needs DONE, skip needs PENDING, both end terminal).
async def test_rejected_then_skipped_recurrence_spawns_exactly_one_successor(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    manager, _ = await worker_headers(client, owner, "CLEANER_MANAGER", "cm@farm.in")
    duty = await make_duty(client, owner, "Daily scrub", category="CLEANING", recur_days=1)
    series_id = duty["recurring_series_id"]

    assert (await complete_duty(client, owner, duty["id"])).status_code == 200
    rejected = await client.post(
        f"/api/tasks/{duty['id']}/reject", json={"note": "redo"}, headers=manager
    )
    assert rejected.status_code == 200, rejected.text

    # The skip decision is only made the next day.
    await _rewind_series_one_day(series_id)

    skipped = await client.post(
        f"/api/tasks/{duty['id']}/skip", json={"reason": "area under repair"}, headers=owner
    )
    assert skipped.status_code == 200, skipped.text
    assert skipped.json()["status"] == "SKIPPED"

    rows = await _series_rows(series_id)
    assert [row.status for row in rows] == [
        TaskStatus.SKIPPED.value,
        TaskStatus.PENDING.value,
    ]
    assert rows[-1].due_date == today() + timedelta(days=1)


# FIXED — regression test
# Endpoint: POST /api/tasks/{task_id}/verify.
# Repro: with the successor moved to the VERIFIED transition, verification —
# unlike completion/skip — does not close a PENDING row to pay for the row it
# spawns (the reviewed occurrence is already DONE). Left unguarded that would
# quietly push the farm past max_pending_manual_tasks_per_farm, the invariant
# creation and rejection both 409 on. Verify now applies the same capacity
# guard before spawning and succeeds once the queue drains.
async def test_verify_spawn_respects_pending_manual_duty_cap(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await owner_with_farm(client)
    manager, _ = await worker_headers(client, owner, "CLEANER_MANAGER", "cm@farm.in")
    monkeypatch.setattr(get_settings(), "max_pending_manual_tasks_per_farm", 1)

    duty = await make_duty(client, owner, "Capacity clean", category="CLEANING", recur_days=1)
    series_id = duty["recurring_series_id"]
    assert (await complete_duty(client, owner, duty["id"])).status_code == 200
    # Completion freed the queue's only slot; an unrelated duty takes it.
    filler = await make_duty(client, owner, "Filler")

    blocked = await client.post(f"/api/tasks/{duty['id']}/verify", headers=manager)
    assert blocked.status_code == 409, blocked.text
    assert "pending manual-duty limit" in blocked.json()["detail"]
    # Still awaiting verification, and no successor sneaked past the cap.
    assert [row.status for row in await _series_rows(series_id)] == [TaskStatus.DONE.value]

    assert (await complete_duty(client, owner, filler["id"])).status_code == 200
    verified = await client.post(f"/api/tasks/{duty['id']}/verify", headers=manager)
    assert verified.status_code == 200, verified.text
    assert [row.status for row in await _series_rows(series_id)] == [
        TaskStatus.VERIFIED.value,
        TaskStatus.PENDING.value,
    ]


# FIXED — regression test
# Endpoint: POST /api/tasks/{task_id}/reject.
# Repro: reject() sent a DONE duty back to PENDING without re-checking the
# linked animal was still ACTIVE, unlike complete/skip. A CLEANING duty
# completed while its animal was ACTIVE could have that animal sold/die before
# a verifier acted, and reject() would silently plant a PENDING row for an
# inactive animal: hidden from every list tab (actionable_pending_task_predicate
# filters it out), unreachable by a later complete/skip (both 409 on
# _require_locked_linked_animal_active), and permanently holding one slot of
# the farm's manual-duty capacity. reject() now takes the same ANIMAL lock and
# active-check as complete/skip, in the same FARM -> ANIMAL -> TASK order.
async def test_reject_409s_when_the_linked_animal_is_no_longer_active(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    manager, _ = await worker_headers(client, owner, "CLEANER_MANAGER", "cm@farm.in")
    animal_id = await make_animal(client, owner, "CLN-1")
    duty = await make_duty(client, owner, "Pen scrub", category="CLEANING", animal_id=animal_id)
    assert (await complete_duty(client, owner, duty["id"])).status_code == 200

    dead = await client.post(
        f"/api/animals/{animal_id}/status", json={"new_status": "DEAD"}, headers=owner
    )
    assert dead.status_code == 200, dead.text

    rejected = await client.post(
        f"/api/tasks/{duty['id']}/reject", json={"note": "redo"}, headers=manager
    )
    assert rejected.status_code == 409, rejected.text
    assert "no longer active" in rejected.json()["detail"]
    async with get_sessionmaker()() as db:
        stored = await db.get(Task, duty["id"])
        assert stored is not None
        # Still DONE, not a stranded PENDING row — the rejection never applied.
        assert stored.status == TaskStatus.DONE.value
