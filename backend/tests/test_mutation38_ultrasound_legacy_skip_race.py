"""A later scan preserves a real skipped appointment retained without an Animal FK."""

import asyncio
from datetime import datetime

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import tasks as tasks_api
from app.db import get_sessionmaker
from app.models import BreedingRecord, Task, User
from app.services.tasks import skip_task
from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_extended import all_tasks, bred_doe, tasks_by_category


async def test_scan_preserves_a_real_concurrent_skip_of_a_retained_unlinked_appointment(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    _doe, _buck, breeding = await bred_doe(client, owner, "RETAINED-SCAN-APPOINTMENT")
    generated = tasks_by_category(await all_tasks(client, owner), "ULTRASOUND")[0]
    # Restore a PENDING appointment whose breeding link survived but whose
    # optional Animal FK was absent. Current Task constraints retain this
    # shape; the public skip route explicitly handles unlinked generated
    # duties. The authoritative newly generated check remains present.
    async with get_sessionmaker()() as db:
        source = await db.get(Task, generated["id"])
        assert source is not None
        restored = Task(
            farm_id=farm_id,
            title="Retained ultrasound appointment",
            due_date=source.due_date,
            category="ULTRASOUND",
            status="PENDING",
            auto_generated=True,
            breeding_record_id=breeding["id"],
            animal_id=None,
            assigned_role_id=source.assigned_role_id,
            title_args={},
        )
        db.add(restored)
        await db.commit()
        appointment_id = restored.id
    visible = await client.get(f"/api/tasks/{appointment_id}", headers=owner)
    assert visible.status_code == 200, visible.text
    assert visible.json()["animal_id"] is None
    assert visible.json()["breeding_record_id"] == breeding["id"]
    assert visible.json()["action_url"] == f"/breeding/{breeding['id']}/ultrasound"

    skip_prepared = asyncio.Event()
    permit_skip_commit = asyncio.Event()
    skip_pid = 0
    first_skipped_at: datetime | None = None
    first_actor: int | None = None
    original_skip = skip_task

    async def pause_prepared_skip(
        db: AsyncSession, task: Task, user: User, reason: str | None = None
    ) -> Task:
        nonlocal skip_pid, first_skipped_at, first_actor
        result = await original_skip(db, task, user, reason)
        if task.id == appointment_id:
            assert task.status == "SKIPPED" and task.completed_at is None
            pid = await db.scalar(text("SELECT pg_backend_pid()"))
            assert isinstance(pid, int)
            skip_pid = pid
            first_skipped_at = task.skipped_at
            first_actor = task.skipped_by_id
            skip_prepared.set()
            await permit_skip_commit.wait()
        return result

    monkeypatch.setattr(tasks_api, "skip_task", pause_prepared_skip)
    skipped = asyncio.create_task(
        client.post(
            f"/api/tasks/{appointment_id}/skip",
            headers=owner,
            json={"reason": "Retained appointment missed; later observation recorded separately"},
        )
    )
    assessment: asyncio.Task[httpx.Response] | None = None
    try:
        await asyncio.wait_for(skip_prepared.wait(), timeout=10)
        assessment = asyncio.create_task(
            client.post(
                f"/api/breeding/{breeding['id']}/ultrasound",
                headers=owner,
                json={"pregnant": True, "kid_count": 2, "date": today().isoformat()},
            )
        )
        for _ in range(1000):
            async with get_sessionmaker()() as observer:
                queued = await observer.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                        "WHERE datname = current_database() "
                        "AND :skip_pid = ANY(pg_blocking_pids(pid)))"
                    ),
                    {"skip_pid": skip_pid},
                )
            if queued:
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError(
                "The actual assessment never reached the retained appointment skip"
            )
        permit_skip_commit.set()
        skip_response, response = await asyncio.wait_for(
            asyncio.gather(skipped, assessment), timeout=15
        )
    finally:
        permit_skip_commit.set()
        pending = [task for task in (skipped, assessment) if task is not None]
        for task in pending:
            if not task.done():
                task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    assert skip_response.status_code == 200, skip_response.text
    assert skip_response.json()["status"] == "SKIPPED"
    assert response.status_code == 200, response.text
    assert response.json()["outcome"] == "CONFIRMED_PREGNANT"
    async with get_sessionmaker()() as db:
        recorded = await db.get(BreedingRecord, breeding["id"])
        appointment = await db.get(Task, appointment_id)
        actual_check = await db.get(Task, generated["id"])
        assert (
            recorded is not None and recorded.pregnant is True and recorded.ultrasound_done is True
        )
        assert appointment is not None and appointment.status == "SKIPPED"
        assert first_skipped_at is not None and appointment.skipped_at == first_skipped_at
        assert first_actor is not None and appointment.skipped_by_id == first_actor
        assert appointment.skip_reason == (
            "Retained appointment missed; later observation recorded separately"
        )
        assert appointment.completed_by_id is None and appointment.completed_at is None
        assert actual_check is not None and actual_check.status == "DONE"
        assert actual_check.completed_by_id == first_actor and actual_check.completed_at is not None
