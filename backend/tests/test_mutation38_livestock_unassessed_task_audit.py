"""Closing an unassessable service preserves task actors and real completions."""

import asyncio
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select, text

from app.db import get_sessionmaker
from app.models import BreedingRecord, Farm, Task, User
from app.services.tasks import complete_task
from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_extended import all_tasks, make_animal, make_breeding


async def _pending_service(client: httpx.AsyncClient) -> tuple[dict[str, str], int, int, int]:
    owner = await owner_with_farm(client)
    reference_date = today()
    service_date = reference_date - timedelta(days=25)
    doe = await make_animal(
        client,
        owner,
        "UNASSESSED-AUDIT-DOE",
        date_of_birth=(reference_date - timedelta(days=800)).isoformat(),
        weight_kg=26.0,
        weight_date=(service_date - timedelta(days=1)).isoformat(),
    )
    buck = await make_animal(
        client,
        owner,
        "UNASSESSED-AUDIT-BUCK",
        sex="M",
        bucket="BREEDING",
        date_of_birth=(reference_date - timedelta(days=800)).isoformat(),
        weight_kg=30.0,
        weight_date=(service_date - timedelta(days=1)).isoformat(),
    )
    record = await make_breeding(
        client,
        owner,
        int(doe["id"]),
        int(buck["id"]),
        breeding_date=service_date.isoformat(),
    )
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, int(owner["X-Farm-Id"]))
        assert farm is not None
        actor_id = farm.owner_id
    return owner, int(doe["id"]), int(record["id"]), actor_id


async def test_public_unassessed_closure_attributes_cancelled_pregnancy_work_to_its_actor(
    client: httpx.AsyncClient,
) -> None:
    owner, doe_id, record_id, actor_id = await _pending_service(client)
    response = await client.post(
        f"/api/animals/{doe_id}/status",
        headers=owner | {"Idempotency-Key": "unassessed-task-audit-sale"},
        json={"new_status": "SOLD", "date": today().isoformat(), "sale_price": 5000},
    )
    assert response.status_code == 200, response.text
    related = [
        task for task in await all_tasks(client, owner) if task["breeding_record_id"] == record_id
    ]
    assert {task["category"] for task in related} == {"ULTRASOUND", "HEAT_WATCH"}
    assert {task["status"] for task in related} == {"SKIPPED"}
    # The broader inactive-animal cleanup can also make status SKIPPED;
    # the clinical close must retain the actual responsible actor/reason.
    assert all(task["skipped_by_id"] == actor_id for task in related)
    assert all(
        task["skip_reason"] == "Doe left the herd before the pregnancy check" for task in related
    )
    assert all(task["skipped_at"] is not None for task in related)


async def test_unassessed_close_preserves_a_committing_native_heat_watch_completion(
    client: httpx.AsyncClient,
) -> None:
    owner, doe_id, record_id, actor_id = await _pending_service(client)
    sale: asyncio.Task[httpx.Response] | None = None
    async with get_sessionmaker()() as worker:
        user = await worker.get(User, actor_id)
        assert user is not None
        duty = (
            await worker.execute(
                select(Task)
                .where(Task.breeding_record_id == record_id, Task.category == "HEAT_WATCH")
                .with_for_update()
            )
        ).scalar_one()
        assert duty.status == "PENDING" and duty.due_date < today()
        duty_id = duty.id
        # The declared native completion helper permits non-movement care
        # duties with its default locked_animals=None. Complete real care
        # through that helper, retaining its transaction and Task row lock.
        await complete_task(worker, duty, user)
        await worker.flush()
        assert duty.status == "DONE" and duty.completed_by_id == actor_id
        worker_pid = (await worker.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        try:
            sale = asyncio.create_task(
                client.post(
                    f"/api/animals/{doe_id}/status",
                    headers=owner | {"Idempotency-Key": "unassessed-task-racing-sale"},
                    json={"new_status": "SOLD", "date": today().isoformat(), "sale_price": 5000},
                )
            )
            for _ in range(2000):
                assert not sale.done(), "Sale must reach the actual committing care task"
                async with get_sessionmaker()() as observer:
                    queued = await observer.scalar(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                            "WHERE datname = current_database() "
                            "AND wait_event_type = 'Lock' "
                            "AND :worker_pid = ANY(pg_blocking_pids(pid)))"
                        ),
                        {"worker_pid": worker_pid},
                    )
                if queued:
                    break
                await asyncio.sleep(0.01)
            else:
                pytest.fail("Real herd exit never reached the held care-task transaction")
            await worker.commit()
            try:
                response = await asyncio.wait_for(sale, timeout=30)
            except TimeoutError:
                pytest.fail("Herd exit did not finish after real care completion committed")
        finally:
            await worker.rollback()
            if sale is not None and not sale.done():
                sale.cancel()
                await asyncio.gather(sale, return_exceptions=True)
    assert response.status_code == 200, response.text
    async with get_sessionmaker()() as observer:
        saved_duty = await observer.get(Task, duty_id)
        record = await observer.get(BreedingRecord, record_id)
        assert saved_duty is not None and record is not None
        assert record.outcome == "UNASSESSED"
        assert saved_duty.status == "DONE"
        assert saved_duty.completed_by_id == actor_id and saved_duty.completed_at is not None
        assert (
            saved_duty.skipped_by_id is None
            and saved_duty.skipped_at is None
            and saved_duty.skip_reason is None
        )
