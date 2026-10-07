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
    skip_entered_task_read = asyncio.Event()
    original_completion = complete_task
    original_task_read = tasks_api._get_task

    async def observe_factual_task_read(
        db: AsyncSession, farm: Farm, key: int, *, for_update: bool = False
    ) -> Task:
        nonlocal skip_pid
        if key == task_id and completion_prepared.is_set():
            # Record the actual competing request connection immediately before
            # its genuine read; preserve the callee and its caller-selected lock.
            pid = await db.scalar(text("SELECT pg_backend_pid()"))
            assert isinstance(pid, int)
            skip_pid = pid
            skip_entered_task_read.set()
        return await original_task_read(db, farm, key, for_update=for_update)

    monkeypatch.setattr(tasks_api, "_get_task", observe_factual_task_read)

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
            assert isinstance(pid, int)
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
            await asyncio.wait_for(skip_entered_task_read.wait(), timeout=15)
            deadline = asyncio.get_running_loop().time() + 15
            async with get_sessionmaker()() as observer:
                while True:
                    # pg_blocking_pids reads the actual live lock manager state.
                    # Restrict the witness to this request's known connection;
                    # another waiter in the database cannot satisfy the oracle.
                    blockers = await observer.scalar(
                        text("SELECT pg_blocking_pids(:reader)"), {"reader": skip_pid}
                    )
                    if isinstance(blockers, list) and completion_pid in blockers:
                        break
                    if asyncio.get_running_loop().time() >= deadline:
                        raise TimeoutError(
                            "The competing skip never exposed its actual completion-row wait"
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
