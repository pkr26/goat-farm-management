"""Restored pregnancy duties must retain their authoritative workflow provenance."""

from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import Animal, BucketMove, Task

from .conftest import owner_with_farm
from .test_breeding_extended import all_tasks, iso, pregnant_doe


@pytest.mark.parametrize(
    ("restoration", "expected_detail"),
    [("manual", "authoritative generated duty"), ("calendar", "recorded pregnancy")],
    ids=["manual", "calendar"],
)
async def test_restored_delivery_duty_cannot_move_without_its_authority_and_calendar(
    client: httpx.AsyncClient, restoration: str, expected_detail: str
) -> None:
    owner = await owner_with_farm(client)
    doe, _buck, breeding = await pregnant_doe(client, owner, gestation_days=136)
    expected_date = date.fromisoformat(breeding["expected_kidding_date"])
    duty = next(
        task
        for task in await all_tasks(client, owner)
        if task["breeding_record_id"] == breeding["id"]
        and task["category"] == "BUCKET_MOVE"
        and task["due_date"] == iso(expected_date - timedelta(days=15))
    )
    async with get_sessionmaker()() as db:
        task = await db.get(Task, duty["id"])
        assert task is not None
        assert task.auto_generated and task.status == "PENDING"
        assert task.breeding_record_id == breeding["id"]
        before = list(
            (
                await db.execute(select(BucketMove.id).where(BucketMove.animal_id == doe["id"]))
            ).scalars()
        )
        # Model legacy restored rows through a normal constraint-enforced
        # commit, keeping the real pregnancy, farm and animal links intact.
        if restoration == "manual":
            task.auto_generated = False
        else:
            task.due_date -= timedelta(days=1)
        await db.commit()

    response = await client.post(f"/api/tasks/{duty['id']}/complete", headers=owner)
    assert response.status_code == 409, response.text
    assert expected_detail in response.json()["detail"]
    async with get_sessionmaker()() as db:
        task = await db.get(Task, duty["id"])
        animal = await db.get(Animal, doe["id"])
        assert task is not None and animal is not None
        assert task.status == "PENDING" and task.completed_at is None
        assert animal.current_bucket == "PREGNANCY_EARLY"
        after = list(
            (
                await db.execute(select(BucketMove.id).where(BucketMove.animal_id == doe["id"]))
            ).scalars()
        )
        assert after == before
