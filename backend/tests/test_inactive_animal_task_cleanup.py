"""Fail-closed and bounded cleanup after animal lifecycle exit."""

import asyncio

import httpx
from sqlalchemy import func, insert, select

import app.main as main_module
from app.db import get_sessionmaker
from app.models import Task, TaskStatus
from app.services import skip_inactive_animal_tasks_batch
from app.utils import today

from .conftest import owner_with_farm


async def test_high_cardinality_retirement_is_hidden_then_converges_in_batches(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    created = await client.post(
        "/api/animals",
        json={
            "tag_number": "RETIRE-501",
            "sex": "F",
            "source": "BORN",
            "current_bucket": "FOUNDATION",
            "historical_import_reason": "Existing herd migration",
        },
        headers=owner,
    )
    assert created.status_code == 201, created.text
    animal_id = created.json()["id"]
    farm_id = int(owner["X-Farm-Id"])

    async with get_sessionmaker()() as db:
        await db.execute(
            insert(Task),
            [
                {
                    "farm_id": farm_id,
                    "title": f"Historical live duty {index}",
                    "due_date": today(),
                    "status": TaskStatus.PENDING.value,
                    "category": "OTHER",
                    "auto_generated": False,
                    "animal_id": animal_id,
                }
                for index in range(501)
            ],
        )
        await db.commit()

    retired = await client.post(
        f"/api/animals/{animal_id}/status",
        json={"new_status": "SOLD"},
        headers=owner,
    )
    assert retired.status_code == 200, retired.text

    # The request performs only one fixed 500-row unit. The residual proves
    # visibility does not depend on cleanup having already caught up.
    async with get_sessionmaker()() as db:
        pending_after_request = (
            await db.execute(
                select(func.count())
                .select_from(Task)
                .where(
                    Task.animal_id == animal_id,
                    Task.status == TaskStatus.PENDING.value,
                )
            )
        ).scalar_one()
    assert pending_after_request == 1

    tasks = await client.get("/api/tasks", headers=owner)
    assert tasks.status_code == 200, tasks.text
    assert tasks.json()["today_total"] == 0
    assert tasks.json()["overdue_total"] == 0
    assert tasks.json()["upcoming_total"] == 0

    dashboard = await client.get("/api/dashboard", headers=owner)
    assert dashboard.status_code == 200, dashboard.text
    assert dashboard.json()["todays_tasks_total"] == 0
    assert dashboard.json()["overdue_tasks_total"] == 0
    assert dashboard.json()["ultrasounds_due_total"] == 0

    async with get_sessionmaker()() as db:
        assert await skip_inactive_animal_tasks_batch(db, batch_size=50) == 1
        await db.commit()
    async with get_sessionmaker()() as db:
        assert await skip_inactive_animal_tasks_batch(db, batch_size=50) == 0


async def test_periodic_cleanup_commits_only_fixed_batches(
    monkeypatch,
) -> None:
    calls: list[int] = []
    results = iter((500, 3))

    async def fake_cleanup(_db, *, batch_size: int) -> int:
        calls.append(batch_size)
        return next(results)

    monkeypatch.setattr(main_module, "skip_inactive_animal_tasks_batch", fake_cleanup)
    worker = asyncio.create_task(
        main_module._inactive_animal_task_cleanup_loop(
            interval_seconds=3600,
            batch_size=500,
            max_batches=10,
        )
    )
    try:
        for _ in range(100):
            if len(calls) == 2:
                break
            await asyncio.sleep(0.01)
        assert calls == [500, 500]
    finally:
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
