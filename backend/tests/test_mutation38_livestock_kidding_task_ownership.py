"""A real kidding sweep preserves a genuinely committing physical care duty."""

import asyncio

import httpx
import pytest
from sqlalchemy import select, text

from app.db import get_sessionmaker
from app.models import BreedingRecord, Farm, Task, User
from app.services.tasks import complete_task
from app.utils import today

from .conftest import owner_with_farm
from .test_kidding_husbandry import confirmed_pregnancy, post_kidding


async def test_kidding_preserves_a_committing_native_birthing_kit_completion(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    _doe, _buck, breeding = await confirmed_pregnancy(
        client, owner, tag="KIDDING-KIT-RACE", bred_days_ago=150, kid_count=1
    )
    record_id = int(breeding["id"])
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, int(owner["X-Farm-Id"]))
        assert farm is not None
        actor_id = farm.owner_id
    delivery: asyncio.Task[httpx.Response] | None = None
    async with get_sessionmaker()() as worker:
        user = await worker.get(User, actor_id)
        assert user is not None
        duty = (
            await worker.execute(
                select(Task)
                .where(Task.breeding_record_id == record_id, Task.category == "BIRTHING_KIT")
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
            delivery = asyncio.create_task(
                post_kidding(client, owner, record_id, today().isoformat(), kids=[{"sex": "F"}])
            )
            for _ in range(2000):
                assert not delivery.done(), "Kidding must reach the actual committing care task"
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
                pytest.fail("Real kidding never reached the held care-task transaction")
            await worker.commit()
            try:
                response = await asyncio.wait_for(delivery, timeout=30)
            except TimeoutError:
                pytest.fail("Kidding did not finish after real care completion committed")
        finally:
            await worker.rollback()
            if delivery is not None and not delivery.done():
                delivery.cancel()
                await asyncio.gather(delivery, return_exceptions=True)
    assert response.status_code == 201, response.text
    async with get_sessionmaker()() as observer:
        saved_duty = await observer.get(Task, duty_id)
        record = await observer.get(BreedingRecord, record_id)
        assert saved_duty is not None and record is not None
        assert record.outcome == "CONFIRMED_PREGNANT"
        assert saved_duty.status == "DONE"
        assert saved_duty.completed_by_id == actor_id and saved_duty.completed_at is not None
        assert (
            saved_duty.skipped_by_id is None
            and saved_duty.skipped_at is None
            and saved_duty.skip_reason is None
        )
