"""A pregnancy assessment preserves an already completed return-to-heat watch."""

import asyncio

import httpx
from sqlalchemy import select, text

from app.db import get_sessionmaker
from app.models import BreedingRecord, Farm, Task, User
from app.services.tasks import complete_task
from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_extended import all_tasks, bred_doe, tasks_by_category
from .test_tasks_extended import worker_headers


async def test_positive_scan_preserves_a_real_concurrent_heat_watch_completion(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    _manager_headers, manager_id = await worker_headers(
        client, owner, "MANAGER", "scan-watch-completion@farm.in"
    )
    _doe, _buck, breeding = await bred_doe(client, owner, "SCAN-WATCH-RACE")
    watch = tasks_by_category(await all_tasks(client, owner), "HEAT_WATCH")[0]
    task_id = int(watch["id"])
    request: asyncio.Task[httpx.Response] | None = None
    async with get_sessionmaker()() as writer:
        duty = (
            await writer.execute(select(Task).where(Task.id == task_id).with_for_update())
        ).scalar_one()
        manager = await writer.get(User, manager_id)
        farm = await writer.get(Farm, farm_id)
        assert manager is not None and farm is not None and manager_id != farm.owner_id
        assert duty.status == "PENDING" and duty.category == "HEAT_WATCH"
        assert duty.recur_days is None and duty.due_date <= today(farm.timezone)
        # HEAT_WATCH is a physical husbandry observation rather than a linked
        # clinical form. The native nonmovement completion helper performs the
        # genuine attributed event without inventing an ultrasound result.
        await complete_task(writer, duty, manager, reference_date=today(farm.timezone))
        await writer.flush()
        first_completed_at = duty.completed_at
        assert first_completed_at is not None and duty.completed_by_id == manager_id
        writer_pid = await writer.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(writer_pid, int)
        request = asyncio.create_task(
            client.post(
                f"/api/breeding/{breeding['id']}/ultrasound",
                headers=owner,
                json={"pregnant": True, "kid_count": 2, "date": today().isoformat()},
            )
        )
        try:
            for _ in range(1000):
                async with get_sessionmaker()() as observer:
                    queued = await observer.scalar(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                            "WHERE datname = current_database() "
                            "AND :writer_pid = ANY(pg_blocking_pids(pid)))"
                        ),
                        {"writer_pid": writer_pid},
                    )
                if queued:
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("The actual scan never reached the prepared watch completion")
            await writer.commit()
            response = await asyncio.wait_for(request, timeout=15)
        finally:
            await writer.rollback()
            if request is not None and not request.done():
                request.cancel()
            if request is not None:
                await asyncio.gather(request, return_exceptions=True)

    assert response.status_code == 200, response.text
    assert response.json()["outcome"] == "CONFIRMED_PREGNANT"
    async with get_sessionmaker()() as db:
        recorded = await db.get(BreedingRecord, breeding["id"])
        final = await db.get(Task, task_id)
        assert recorded is not None and recorded.created_by_id == farm.owner_id
        assert recorded.pregnant is True and recorded.ultrasound_done is True
        assert final is not None and final.status == "DONE"
        assert final.completed_by_id == manager_id
        assert final.completed_at == first_completed_at
