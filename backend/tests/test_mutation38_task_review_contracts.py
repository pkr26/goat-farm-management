"""Actual review transitions preserve linked recurrence and serialize conflicting decisions."""

import asyncio
from typing import Literal

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import tasks as tasks_api
from app.db import get_sessionmaker
from app.models import Animal, Farm, Task, User
from app.services.tasks import reject_task as original_reject
from app.services.tasks import verify_task as original_verify
from app.utils import today

from .conftest import owner_with_farm
from .test_tasks_extended import complete_duty, make_animal, make_duty


@pytest.mark.parametrize("departure", [False, True], ids=["active", "departed"])
async def test_reviewed_linked_recurrence_continues_only_while_its_animal_is_active(
    client: httpx.AsyncClient, departure: bool
) -> None:
    owner = await owner_with_farm(client, email="review-linked-recurrence@example.test")
    farm_id = int(owner["X-Farm-Id"])
    animal_id = await make_animal(client, owner, tag="REVIEW-LINKED-ANIMAL")
    duty = await make_duty(
        client,
        owner,
        "Review linked cleaning",
        category="CLEANING",
        recur_days=1,
        animal_id=animal_id,
    )
    series_id = duty["recurring_series_id"]
    completed = await complete_duty(client, owner, duty["id"])
    assert completed.status_code == 200, completed.text
    async with get_sessionmaker()() as db:
        original = await db.get(Task, duty["id"])
        assert original is not None and original.status == "DONE"
        completed_by, completed_at = original.completed_by_id, original.completed_at
        rows = list(
            (await db.execute(select(Task).where(Task.recurring_series_id == series_id))).scalars()
        )
        assert [row.id for row in rows] == [duty["id"]]
    if departure:
        departed = await client.post(
            f"/api/animals/{animal_id}/status", json={"new_status": "DEAD"}, headers=owner
        )
        assert departed.status_code == 200, departed.text
    verified = await client.post(f"/api/tasks/{duty['id']}/verify", headers=owner)
    assert verified.status_code == 200, verified.text
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        animal = await db.get(Animal, animal_id)
        assert farm is not None and animal is not None
        assert animal.status == ("DEAD" if departure else "ACTIVE")
        rows = list(
            (
                await db.execute(
                    select(Task)
                    .where(Task.recurring_series_id == series_id)
                    .order_by(Task.due_date, Task.id)
                )
            ).scalars()
        )
        reviewed = next(row for row in rows if row.id == duty["id"])
        assert reviewed.status == "VERIFIED"
        assert reviewed.verified_by_id == farm.owner_id and reviewed.verified_at is not None
        assert (reviewed.completed_by_id, reviewed.completed_at) == (completed_by, completed_at)
        pending = [row for row in rows if row.status == "PENDING"]
        assert len(pending) == (0 if departure else 1), [(row.id, row.status) for row in rows]
        assert len(rows) == (1 if departure else 2)
        if pending:
            assert pending[0].animal_id == animal_id
            assert pending[0].recur_days == 1
            assert pending[0].due_date > today()
            assert pending[0].created_by_id == reviewed.created_by_id


@pytest.mark.parametrize("first", ["reject", "verify"])
async def test_conflicting_public_reviews_recheck_the_committed_task_state(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    first: Literal["reject", "verify"],
) -> None:
    owner = await owner_with_farm(client, email="serialized-review@example.test")
    duty = await make_duty(client, owner, "One-off review", category="CLEANING")
    completed = await complete_duty(client, owner, duty["id"])
    assert completed.status_code == 200, completed.text
    prepared, permit_commit = asyncio.Event(), asyncio.Event()
    holder_pid = 0

    async def pause_after_actual_write(db: AsyncSession, task: Task) -> None:
        nonlocal holder_pid
        if task.id == duty["id"]:
            pid = await db.scalar(text("SELECT pg_backend_pid()"))
            assert isinstance(pid, int)
            holder_pid = pid
            prepared.set()
            await permit_commit.wait()

    async def paused_rejection(db: AsyncSession, task: Task, user: User, note: str) -> Task:
        actual = await original_reject(db, task, user, note)
        await pause_after_actual_write(db, task)
        return actual

    async def paused_verification(
        db: AsyncSession, task: Task, user: User, *, spawn_successor: bool | None = None
    ) -> Task:
        actual = await original_verify(db, task, user, spawn_successor=spawn_successor)
        await pause_after_actual_write(db, task)
        return actual

    monkeypatch.setattr(
        tasks_api,
        "reject_task" if first == "reject" else "verify_task",
        paused_rejection if first == "reject" else paused_verification,
    )
    second = "verify" if first == "reject" else "reject"

    async def request(action: str) -> httpx.Response:
        return await client.post(
            f"/api/tasks/{duty['id']}/{action}",
            json={"note": "Actual rejected attempt"} if action == "reject" else {},
            headers=owner | {"Idempotency-Key": f"serialized-review-{action}"},
        )

    holder = asyncio.create_task(request(first))
    contender: asyncio.Task[httpx.Response] | None = None
    try:
        await asyncio.wait_for(prepared.wait(), timeout=20)
        contender = asyncio.create_task(request(second))
        for _ in range(1000):
            async with get_sessionmaker()() as observer:
                blocked = await observer.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                        "WHERE datname=current_database() "
                        "AND :holder_pid=ANY(pg_blocking_pids(pid)))"
                    ),
                    {"holder_pid": holder_pid},
                )
            if blocked:
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("Competing review never waited on the actual task writer")
        permit_commit.set()
        first_response, second_response = await asyncio.gather(holder, contender)
    finally:
        permit_commit.set()
        running = [task for task in (holder, contender) if task is not None]
        for task in running:
            if not task.done():
                task.cancel()
        await asyncio.gather(*running, return_exceptions=True)
    assert first_response.status_code == 200, first_response.text
    assert second_response.status_code == 409, second_response.text
    async with get_sessionmaker()() as db:
        actual = await db.get(Task, duty["id"])
        assert actual is not None
        assert actual.status == ("PENDING" if first == "reject" else "VERIFIED")
        if first == "reject":
            assert actual.verification_note == "Actual rejected attempt"
            assert actual.rejected_by_id is not None and actual.rejected_at is not None
            assert actual.verified_by_id is None and actual.verified_at is None
        else:
            assert actual.verified_by_id is not None and actual.verified_at is not None
            assert actual.rejected_by_id is None and actual.rejected_at is None
