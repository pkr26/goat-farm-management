"""Public release gates reject constraint-valid legacy provenance and holds."""

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import Animal, BucketMove, HealthEvent, Task

from .conftest import owner_with_farm
from .test_health_extended import (
    _backdated_batch_with_tasks,
    complete_quarantine_prerequisites,
)


@pytest.mark.parametrize(
    ("restoration", "expected_detail"),
    [
        ("manual-release", "authoritative"),
        ("wrong-calendar", "protocol date"),
        ("movement-only", "restriction"),
    ],
    ids=["manual-release", "wrong-calendar", "movement-only"],
)
async def test_release_rejects_each_legacy_provenance_fault_or_single_movement_hold(
    client: httpx.AsyncClient, restoration: str, expected_detail: str
) -> None:
    owner = await owner_with_farm(client, email=f"release-{restoration}@farm.in")
    detail = await _backdated_batch_with_tasks(client, owner, days=50, count=1)
    batch_id = detail["batch"]["id"]
    animal_id = detail["animals"][0]["id"]
    release_id = next(task["id"] for task in detail["tasks"] if task["category"] == "BUCKET_MOVE")
    # Every prerequisite is actually performed through its audited public
    # health or task workflow. No completed clinical row is manufactured.
    await complete_quarantine_prerequisites(client, owner, detail)
    async with get_sessionmaker()() as db:
        prerequisites = list(
            (
                await db.execute(
                    select(Task).where(Task.purchase_batch_id == batch_id, Task.id != release_id)
                )
            ).scalars()
        )
        assert len(prerequisites) == 10
        assert all(
            row.status in {"DONE", "VERIFIED"} and row.completed_at is not None
            for row in prerequisites
        )
        health = list(
            (
                await db.execute(
                    select(HealthEvent).where(HealthEvent.purchase_batch_id == batch_id)
                )
            ).scalars()
        )
        assert sorted(row.type for row in health) == [
            "DEWORMING",
            "VACCINE",
            "VACCINE",
            "VACCINE",
            "VACCINE",
        ]
        release = await db.get(Task, release_id)
        animal = await db.get(Animal, animal_id)
        assert release is not None and animal is not None
        assert release.auto_generated and release.status == "PENDING"
        assert animal.current_bucket == "QUARANTINE"
        # These legacy states are accepted by the actual PostgreSQL schema.
        # Commit each normally; no constraint or trigger is disabled.
        if restoration == "manual-release":
            release.auto_generated = False
        elif restoration == "wrong-calendar":
            release.due_date -= timedelta(days=1)
        else:
            animal.movement_restricted = True
            animal.restriction_reason = "Recorded authority movement restriction"
            assert animal.suspected_scheduled_disease is False
        await db.commit()
    response = await client.post(f"/api/tasks/{release_id}/complete", headers=owner)
    assert response.status_code == 409, response.text
    assert expected_detail in response.json()["detail"].lower()
    async with get_sessionmaker()() as db:
        release = await db.get(Task, release_id)
        animal = await db.get(Animal, animal_id)
        assert release is not None and animal is not None
        assert release.status == "PENDING" and release.completed_at is None
        assert animal.current_bucket == "QUARANTINE"
        moves = list(
            (
                await db.execute(select(BucketMove).where(BucketMove.animal_id == animal_id))
            ).scalars()
        )
        assert len(moves) == 1 and moves[0].to_bucket == "QUARANTINE"
