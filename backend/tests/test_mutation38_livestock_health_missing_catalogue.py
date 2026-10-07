"""Warm reference-catalogue gaps preserve health rejection and retry contracts."""

from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import HealthEvent, Task, VaccineTemplate
from app.utils import today

from .conftest import owner_with_farm
from .test_health_extended import get_batch, make_batch


async def test_inferred_missing_reference_template_is_422_and_retries_after_catalogue_repair(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    batch = await make_batch(
        client, owner, count=2, date=(today() - timedelta(days=50)).isoformat()
    )
    detail = await get_batch(client, owner, batch["id"])
    duty = next(
        task for task in detail["tasks"] if task["category"] == "VACCINE" and "PPR" in task["title"]
    )
    payload = {
        "scope": "batch",
        "purchase_batch_id": batch["id"],
        "expected_animal_ids": [animal["id"] for animal in detail["animals"]],
        "task_id": duty["id"],
        "type": "VACCINE",
        "disease_target": "PPR",
    }
    headers = owner | {"Idempotency-Key": "missing-inferred-reference-template"}
    async with get_sessionmaker()() as db:
        template = (
            await db.execute(select(VaccineTemplate).where(VaccineTemplate.name == "PPR"))
        ).scalar_one()
        original: dict[str, Any] = {
            column.key: getattr(template, column.key)
            for column in VaccineTemplate.__table__.columns
        }
        assert not list((await db.execute(select(HealthEvent))).scalars())
        # This is an ordinary constraint-enabled native reference deletion:
        # no clinical row or reference FK is removed. Startup reseeds missing
        # reference items, but the warmed process has no per-request repair.
        await db.delete(template)
        await db.commit()
    try:
        rejected = await client.post("/api/health/events", headers=headers, json=payload)
        assert rejected.status_code == 422, rejected.text
        assert rejected.json()["detail"] == "Unknown schedule template"
        async with get_sessionmaker()() as db:
            assert not list((await db.execute(select(HealthEvent))).scalars())
            task = await db.get(Task, duty["id"])
            assert task is not None and task.status == "PENDING"
            assert (
                await db.execute(select(VaccineTemplate).where(VaccineTemplate.name == "PPR"))
            ).scalar_one_or_none() is None
    finally:
        # Preserve the exact seeded row identity and definition for every
        # subsequent original control, even if the response assertion fails.
        async with get_sessionmaker()() as db:
            db.add(VaccineTemplate(**original))
            await db.commit()
    retried = await client.post("/api/health/events", headers=headers, json=payload)
    assert retried.status_code == 201, retried.text
    assert len(retried.json()) == 2
    assert {event["schedule_template_name"] for event in retried.json()} == {"PPR"}
    async with get_sessionmaker()() as db:
        events = list((await db.execute(select(HealthEvent))).scalars())
        task = await db.get(Task, duty["id"])
        assert len(events) == 2 and task is not None and task.status == "DONE"
        assert {event.schedule_template_id for event in events} == {original["id"]}
        restored = await db.get(VaccineTemplate, original["id"])
        assert restored is not None
        assert {
            column.key: getattr(restored, column.key)
            for column in VaccineTemplate.__table__.columns
        } == original
