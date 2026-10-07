"""A completion queued behind a factual public skip preserves its committed audit."""

import asyncio
from datetime import datetime

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import tasks as tasks_api
from app.db import get_sessionmaker
from app.models import Task, User
from app.services.tasks import skip_task

from .conftest import owner_with_farm
from .test_tasks_extended import make_duty


async def test_completion_queued_behind_a_real_public_skip_preserves_skipped_attribution(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client, email="duty-skip-completion-race@farm.in")
    duty = await make_duty(client, owner, title="Actual inspection standdown", category="OTHER")
    task_id = int(duty["id"])
    skip_prepared = asyncio.Event()
    permit_skip_commit = asyncio.Event()
    skip_pid = 0
    original_skip = skip_task
    reason = "Inspection cancelled by the actual owner"

    async def pause_factual_skip(
        db: AsyncSession, task: Task, user: User, reason: str | None = None
    ) -> Task:
        nonlocal skip_pid
        result = await original_skip(db, task, user, reason)
        if task.id == task_id:
            # Keep the genuine public SKIPPED mutation and Task mutex;
            # pause only before committing the actual audit facts.
            pid = await db.scalar(text("SELECT pg_backend_pid()"))
            assert isinstance(pid, int)
            skip_pid = pid
            skip_prepared.set()
            await permit_skip_commit.wait()
        return result

    monkeypatch.setattr(tasks_api, "skip_task", pause_factual_skip)
    skip = asyncio.create_task(
        client.post(f"/api/tasks/{task_id}/skip", headers=owner, json={"reason": reason})
    )
    completion: asyncio.Task[httpx.Response] | None = None
    try:
        await asyncio.wait_for(skip_prepared.wait(), timeout=15)
        completion = asyncio.create_task(
            client.post(f"/api/tasks/{task_id}/complete", headers=owner)
        )
        deadline = asyncio.get_running_loop().time() + 15
        async with get_sessionmaker()() as observer:
            while True:
                queued = await observer.scalar(
                    text(
                        "SELECT EXISTS(SELECT 1 FROM pg_stat_activity "
                        "WHERE datname=current_database() AND wait_event_type='Lock' "
                        "AND :holder=ANY(pg_blocking_pids(pid)))"
                    ),
                    {"holder": skip_pid},
                )
                if queued:
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError("The competing completion never exposed a real PG wait")
                await asyncio.sleep(0.01)
        permit_skip_commit.set()
        skipped, completed = await asyncio.gather(skip, completion)
    finally:
        permit_skip_commit.set()
        pending = [request for request in (skip, completion) if request is not None]
        for request in pending:
            if not request.done():
                request.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    assert skipped.status_code == 200, skipped.text
    factual = skipped.json()
    assert factual["status"] == "SKIPPED" and factual["skipped_by_id"] is not None
    assert factual["skipped_at"] is not None and factual["skip_reason"] == reason
    assert completed.status_code == 409, completed.text
    assert completed.json()["detail"] == "Task is not pending"
    async with get_sessionmaker()() as db:
        stored = await db.get(Task, task_id)
        assert stored is not None and stored.status == "SKIPPED"
        assert stored.skipped_by_id == factual["skipped_by_id"]
        assert stored.skipped_at == datetime.fromisoformat(factual["skipped_at"])
        assert stored.skip_reason == reason
        assert stored.completed_at is None and stored.completed_by_id is None
