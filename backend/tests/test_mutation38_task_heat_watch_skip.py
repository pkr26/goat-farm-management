"""Skipping the optional heat watch preserves the actual open pregnancy check."""

from datetime import timedelta

import httpx
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import Animal, BreedingRecord, Task
from app.utils import today

from .conftest import owner_with_farm
from .test_tasks_extended import make_breeding, make_buck, make_doe


async def test_optional_generated_heat_watch_can_be_skipped_while_the_scan_stays_open(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="optional-heat-watch-skip@farm.in")
    doe = await make_doe(client, owner, tag="HEAT-WATCH-DOE")
    buck = await make_buck(client, owner, tag="HEAT-WATCH-BUCK")
    breeding = await make_breeding(client, owner, doe, buck, today() - timedelta(days=20))
    async with get_sessionmaker()() as db:
        tasks = list(
            (
                await db.execute(select(Task).where(Task.breeding_record_id == breeding["id"]))
            ).scalars()
        )
        watch = next(task for task in tasks if task.category == "HEAT_WATCH")
        scan = next(task for task in tasks if task.category == "ULTRASOUND")
        assert watch.auto_generated and watch.status == scan.status == "PENDING"
        watch_id, scan_id = watch.id, scan.id
    reason = "Observer unavailable; the planned pregnancy check remains required"
    skipped = await client.post(
        f"/api/tasks/{watch_id}/skip", headers=owner, json={"reason": reason}
    )
    assert skipped.status_code == 200, skipped.text
    factual = skipped.json()
    assert factual["status"] == "SKIPPED" and factual["skip_reason"] == reason
    assert factual["skipped_by_id"] is not None and factual["skipped_at"] is not None
    async with get_sessionmaker()() as db:
        stored_watch = await db.get(Task, watch_id)
        stored_scan = await db.get(Task, scan_id)
        stored_breeding = await db.get(BreedingRecord, breeding["id"])
        stored_doe = await db.get(Animal, doe["id"])
        assert stored_watch is not None and stored_watch.status == "SKIPPED"
        assert stored_watch.completed_at is None and stored_watch.completed_by_id is None
        assert stored_scan is not None and stored_scan.status == "PENDING"
        assert stored_breeding is not None and stored_breeding.outcome == "PENDING"
        assert stored_doe is not None and stored_doe.current_bucket == "BREEDING"
