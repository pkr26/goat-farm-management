"""A new service preserves the actor and time of an already completed REBREED duty."""

import asyncio
from datetime import timedelta

import httpx
from sqlalchemy import select, text, update

from app.db import get_sessionmaker
from app.models import BreedingRecord, BucketMove, Farm, Task, User
from app.services.tasks import complete_task
from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_extended import _kidded_doe_with_due_weaning, all_tasks, tasks_by_category
from .test_tasks_extended import worker_headers


async def test_new_service_preserves_a_real_concurrently_committed_rebreed_completion(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    _manager_headers, manager_id = await worker_headers(
        client, owner, "MANAGER", "rebreed-completion-manager@farm.in"
    )
    doe, buck, _kidding = await _kidded_doe_with_due_weaning(client, owner, "REBREED-RACE-DOE")
    weaning = tasks_by_category(await all_tasks(client, owner), "WEANING")[0]
    completed_weaning = await client.post(f"/api/tasks/{weaning['id']}/complete", headers=owner)
    assert completed_weaning.status_code == 200, completed_weaning.text
    rebreed = tasks_by_category(await all_tasks(client, owner), "REBREED")[0]
    task_id = int(rebreed["id"])
    # Restore a valid retained calendar: the doe entered rest forty days ago,
    # and her thirty-day prompt fell due ten days ago. Native breeding tests
    # use this historical-rest fixture boundary to exercise later re-service.
    async with get_sessionmaker()() as db:
        await db.execute(
            update(BucketMove)
            .where(BucketMove.animal_id == doe["id"], BucketMove.to_bucket == "RESTING")
            .values(effective_date=today() - timedelta(days=40))
        )
        await db.execute(
            update(Task).where(Task.id == task_id).values(due_date=today() - timedelta(days=10))
        )
        await db.commit()
    ready = await client.post(
        f"/api/animals/{doe['id']}/move", headers=owner, json={"to_bucket": "BREEDING"}
    )
    assert ready.status_code == 200, ready.text

    request: asyncio.Task[httpx.Response] | None = None
    async with get_sessionmaker()() as writer:
        duty = (
            await writer.execute(select(Task).where(Task.id == task_id).with_for_update())
        ).scalar_one()
        manager = await writer.get(User, manager_id)
        farm = await writer.get(Farm, farm_id)
        assert manager is not None and farm is not None and manager_id != farm.owner_id
        assert duty.status == "PENDING" and duty.category == "REBREED" and duty.recur_days is None
        assert duty.due_date <= today(farm.timezone)
        # The exported helper supports non-movement categories without a
        # prelocked Animal set. It performs the real attributed completion;
        # this transaction holds the Task row until its eventual commit.
        await complete_task(writer, duty, manager, reference_date=today(farm.timezone))
        await writer.flush()
        first_completed_at = duty.completed_at
        assert first_completed_at is not None and duty.completed_by_id == manager_id
        writer_pid = await writer.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(writer_pid, int)
        request = asyncio.create_task(
            client.post(
                "/api/breeding",
                headers=owner,
                json={
                    "doe_id": doe["id"],
                    "buck_id": buck["id"],
                    "breeding_date": today().isoformat(),
                },
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
                raise AssertionError(
                    "The actual service request never reached the prepared Task completion"
                )
            await writer.commit()
            response = await asyncio.wait_for(request, timeout=15)
        finally:
            await writer.rollback()
            if request is not None and not request.done():
                request.cancel()
            if request is not None:
                await asyncio.gather(request, return_exceptions=True)

    assert response.status_code == 201, response.text
    assert response.json()["outcome"] == "PENDING"
    async with get_sessionmaker()() as db:
        recorded = await db.get(BreedingRecord, response.json()["id"])
        final = await db.get(Task, task_id)
        assert recorded is not None and recorded.created_by_id == farm.owner_id
        assert final is not None and final.status == "DONE"
        assert final.completed_by_id == manager_id
        assert final.completed_at == first_completed_at
