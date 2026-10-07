"""Clinical replanning preserves a real native skip of a retained manual duty."""

import asyncio
from datetime import date, timedelta

import httpx
from sqlalchemy import select, text

from app.db import get_sessionmaker
from app.models import Animal, Farm, KidEntry, Task, User
from app.services.tasks import skip_task

from .conftest import owner_with_farm
from .test_breeding_extended import kid_on_ekd, pregnant_doe


async def test_last_kid_death_preserves_a_concurrent_native_skip_of_a_retained_manual_duty(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    doe, _buck, breeding = await pregnant_doe(
        client, owner, "RETAINED-WEANING-RACE", gestation_days=170
    )
    kidding = await kid_on_ekd(client, owner, breeding, kids=[{"sex": "M"}])
    child_id = kidding["kids"][0]["animal_id"]
    due = date.fromisoformat(kidding["date"]) + timedelta(days=60)
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        # A constraints-enabled restored older manual checklist is not an
        # authoritative generated movement gate. The exported native skip
        # can retire it without moving any animal or inventing clinical work.
        # Current public clinical routes acquire Animals before Tasks and
        # serialize earlier; this oracle adopts the retained native interface.
        retained = Task(
            farm_id=farm_id,
            animal_id=doe["id"],
            category="WEANING",
            title="Retained manual weaning checklist",
            due_date=due,
            auto_generated=False,
            status="PENDING",
            created_by_id=farm.owner_id,
            title_args={},
        )
        db.add(retained)
        await db.commit()
        retained_id = retained.id

    request: asyncio.Task[httpx.Response] | None = None
    reason = "Retired the retained manual checklist after an owner review"
    async with get_sessionmaker()() as writer:
        duty = (
            await writer.execute(
                select(Task)
                .where(Task.id == retained_id, Task.farm_id == farm_id)
                .with_for_update()
            )
        ).scalar_one()
        user = await writer.get(User, farm.owner_id)
        assert user is not None
        assert duty.status == "PENDING" and duty.auto_generated is False and duty.recur_days is None
        await skip_task(writer, duty, user, reason)
        first_skipped_at = duty.skipped_at
        assert first_skipped_at is not None and duty.skipped_by_id == user.id
        writer_pid = await writer.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(writer_pid, int)
        request = asyncio.create_task(
            client.post(
                f"/api/animals/{child_id}/status", headers=owner, json={"new_status": "DEAD"}
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
                raise AssertionError("The actual kid death never reached the retained Task writer")
            await writer.commit()
            response = await asyncio.wait_for(request, timeout=15)
        finally:
            await writer.rollback()
            if request is not None and not request.done():
                request.cancel()
            if request is not None:
                await asyncio.gather(request, return_exceptions=True)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "DEAD"
    async with get_sessionmaker()() as db:
        child = await db.get(Animal, child_id)
        entry = (
            await db.execute(select(KidEntry).where(KidEntry.animal_id == child_id))
        ).scalar_one()
        final = await db.get(Task, retained_id)
        assert child is not None and child.status == "DEAD" and entry.status == "DIED"
        assert final is not None and final.status == "SKIPPED"
        assert final.skipped_by_id == user.id
        assert final.skipped_at == first_skipped_at and final.skip_reason == reason
