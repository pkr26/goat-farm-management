"""A skip queued behind a factual public completion preserves its committed audit."""

import asyncio
from datetime import date, datetime

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import tasks as tasks_api
from app.db import get_sessionmaker
from app.main import create_app
from app.models import Animal, Farm, Task, User
from app.services.tasks import complete_task

from .conftest import owner_with_farm
from .test_tasks_extended import make_duty


async def test_skip_queued_behind_a_real_public_completion_preserves_done_attribution(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client, email="duty-completion-skip-race@farm.in")
    duty = await make_duty(client, owner, title="Actually completed inspection", category="OTHER")
    task_id = int(duty["id"])
    completion_prepared = asyncio.Event()
    permit_completion_commit = asyncio.Event()
    completion_pid = 0
    skip_pid = 0
    skip_entered_transition = asyncio.Event()
    original_completion = complete_task
    original_transition_lock = tasks_api._lock_farm_for_recurring_transition

    async def observe_factual_transition(db: AsyncSession, farm: Farm, key: int) -> None:
        nonlocal skip_pid
        if key == task_id and completion_prepared.is_set():
            # Observe the real competing connection before its first transition
            # lock. Either a Task-row wait or a genuine earlier queue wait may
            # order these requests; the final public/audit oracle is unchanged.
            if farm.id != int(owner["X-Farm-Id"]) or skip_pid != 0:
                raise RuntimeError("Competing transition did not identify the single owned skip")
            pid = await db.scalar(text("SELECT pg_backend_pid()"))
            if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
                raise RuntimeError("Competing transition did not expose its real backend PID")
            skip_pid = pid
            skip_entered_transition.set()
        await original_transition_lock(db, farm, key)

    monkeypatch.setattr(
        tasks_api, "_lock_farm_for_recurring_transition", observe_factual_transition
    )

    async def pause_factual_completion(
        db: AsyncSession,
        task: Task,
        user: User | None = None,
        *,
        locked_animals: list[Animal] | None = None,
        reference_date: date | None = None,
    ) -> Task:
        nonlocal completion_pid
        result = await original_completion(
            db, task, user, locked_animals=locked_animals, reference_date=reference_date
        )
        if task.id == task_id:
            # The real public transition holds the canonical Task row lock,
            # has performed its actual DONE mutation and flushed its audit.
            # Pause only its commit boundary; never replace domain behavior.
            pid = await db.scalar(text("SELECT pg_backend_pid()"))
            if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
                raise RuntimeError("Factual completion did not expose its real backend PID")
            completion_pid = pid
            completion_prepared.set()
            await permit_completion_commit.wait()
        return result

    monkeypatch.setattr(tasks_api, "complete_task", pause_factual_completion)
    # Independent ASGI clients let each request own its transport and lifecycle.
    # The public bearer credentials still drive normal application authorization.
    async with (
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(), raise_app_exceptions=False),
            base_url="http://test",
        ) as completing_client,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(), raise_app_exceptions=False),
            base_url="http://test",
        ) as skipping_client,
    ):
        completion = asyncio.create_task(
            completing_client.post(f"/api/tasks/{task_id}/complete", headers=owner)
        )
        skip: asyncio.Task[httpx.Response] | None = None
        try:
            await asyncio.wait_for(completion_prepared.wait(), timeout=15)
            skip = asyncio.create_task(
                skipping_client.post(
                    f"/api/tasks/{task_id}/skip",
                    headers=owner,
                    json={"reason": "Competing standdown must not replace actual completion"},
                )
            )
            await asyncio.wait_for(skip_entered_transition.wait(), timeout=15)
            if skip_pid == completion_pid:
                raise RuntimeError("Competing requests did not retain independent real connections")
            deadline = asyncio.get_running_loop().time() + 15
            async with get_sessionmaker()() as observer:
                while True:
                    if skip.done():
                        raise RuntimeError("The owned skip ended before exposing its actual wait")
                    # pg_blocking_pids reads the actual live lock manager state.
                    # Restrict the witness to this request's known connection;
                    # another waiter cannot satisfy it. Release the writer for
                    # either genuine lock path; do not assert mutex topology.
                    blockers = await observer.scalar(
                        text("SELECT pg_blocking_pids(:reader)"), {"reader": skip_pid}
                    )
                    if not isinstance(blockers, list) or any(
                        not isinstance(pid, int) or isinstance(pid, bool) for pid in blockers
                    ):
                        raise RuntimeError("PostgreSQL did not return the actual blocking PID list")
                    if completion_pid in blockers:
                        break
                    if asyncio.get_running_loop().time() >= deadline:
                        raise TimeoutError(
                            "The competing skip never exposed its actual owned-transaction wait"
                        )
                    await asyncio.sleep(0.01)
            permit_completion_commit.set()
            completed, skipped = await asyncio.gather(completion, skip)
        finally:
            # Release the genuine writer first and let both HTTP transactions
            # finish normally. Cancellation is reserved for a cleanup deadline.
            permit_completion_commit.set()
            pending = [request for request in (completion, skip) if request is not None]
            _, unfinished = await asyncio.wait(pending, timeout=15)
            for request in unfinished:
                request.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    assert completed.status_code == 200, completed.text
    factual = completed.json()
    assert factual["status"] == "DONE" and factual["completed_by_id"] is not None
    assert factual["completed_at"] is not None
    assert skipped.status_code == 409, skipped.text
    assert skipped.json()["detail"] == "Task is not pending"
    async with get_sessionmaker()() as db:
        stored = await db.get(Task, task_id)
        assert stored is not None and stored.status == "DONE"
        assert stored.completed_by_id == factual["completed_by_id"]
        assert stored.completed_at is not None
        assert stored.completed_at == datetime.fromisoformat(factual["completed_at"])
        assert stored.skipped_at is None and stored.skipped_by_id is None
        assert stored.skip_reason is None
