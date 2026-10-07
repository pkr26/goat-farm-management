"""Real public health events preserve domain boundary and rejection contracts."""

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select, text

from app.db import get_sessionmaker
from app.models import Animal, HealthEvent, Task
from app.utils import today

from .conftest import owner_with_farm
from .test_health_extended import get_batch, make_animal, make_batch


@pytest.mark.parametrize("days,expected", [(3649, 201), (3650, 201), (3651, 422)])
async def test_omitted_health_date_keeps_the_literal_ten_year_next_due_boundary(
    client: httpx.AsyncClient,
    days: int,
    expected: int,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, "DEFAULTED-DATE-NEXT-DUE")
    response = await client.post(
        "/api/health/events",
        headers=owner,
        json={
            "animal_id": animal["id"],
            "type": "TREATMENT",
            "next_due_date": (today() + timedelta(days=days)).isoformat(),
            "schedule_template_name": "Vet follow-up review",
            "next_due_authority": "Consulting veterinarian",
        },
    )
    assert response.status_code == expected, response.text
    async with get_sessionmaker()() as db:
        rows = list((await db.execute(select(HealthEvent))).scalars())
        if expected == 201:
            assert len(rows) == 1 and rows[0].date == today()
            assert rows[0].next_due_date == today() + timedelta(days=days)
            assert rows[0].schedule_template_name == "Vet follow-up review"
            assert rows[0].next_due_authority == "Consulting veterinarian"
        else:
            assert response.json()["detail"] == (
                "next_due_date cannot be more than 10 years after the health event date"
            )
            assert not rows


@pytest.mark.parametrize("disease,expected", [("PPR", 201), ("FMD", 422)])
async def test_explicit_health_schedule_template_accepts_its_own_target_and_rejects_another(
    client: httpx.AsyncClient,
    disease: str,
    expected: int,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, "EXPLICIT-PPR-PROGRAMME")
    response = await client.post(
        "/api/health/events",
        headers=owner,
        json={
            "animal_id": animal["id"],
            "type": "VACCINE",
            "schedule_template_name": "PPR",
            "disease_target": disease,
        },
    )
    assert response.status_code == expected, response.text
    async with get_sessionmaker()() as db:
        events = list((await db.execute(select(HealthEvent))).scalars())
        if expected == 201:
            assert len(events) == 1 and events[0].disease_target == "PPR"
            assert events[0].schedule_template_name == "PPR"
        else:
            assert response.json()["detail"] == (
                "Disease target does not match the selected schedule template"
            )
            assert not events


async def test_public_health_event_and_schedule_accept_the_real_int4_ceiling_animal(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    async with get_sessionmaker()() as db:
        await db.execute(text("SELECT setval('animals_id_seq', 2147483647, false)"))
        await db.commit()
    animal = await make_animal(client, owner, "CEILING-CLINICAL-IDENTITY")
    assert animal["id"] == 2_147_483_647
    recorded = await client.post(
        "/api/health/events",
        headers=owner,
        json={"animal_id": animal["id"], "type": "TREATMENT", "notes": "Vet examination"},
    )
    assert recorded.status_code == 201, recorded.text
    assert len(recorded.json()) == 1 and recorded.json()[0]["animal_id"] == animal["id"]
    schedule = await client.get(f"/api/health/schedule/{animal['id']}", headers=owner)
    assert schedule.status_code == 200, schedule.text
    assert schedule.json()["animal_id"] == animal["id"] and schedule.json()["rows"]
    bulk = await client.post(
        "/api/health/events",
        headers=owner,
        json={
            "scope": "bucket",
            "bucket": "FOUNDATION",
            "expected_animal_ids": [animal["id"]],
            "type": "TREATMENT",
            "notes": "Reviewed follow-up for the supported identity",
        },
    )
    assert bulk.status_code == 201, bulk.text
    assert len(bulk.json()) == 1 and bulk.json()[0]["animal_id"] == animal["id"]


async def test_schedule_keeps_reserved_zero_not_found_for_a_constraint_valid_legacy_animal(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    async with get_sessionmaker()() as db:
        # The same explicit restored INTEGER-zero identity admitted by the
        # existing profile contract is storage-valid. API health schedules
        # still reserve nonpositive path identities as not found.
        db.add(
            Animal(
                id=0,
                farm_id=int(owner["X-Farm-Id"]),
                tag_number="ZERO-CLINICAL-LEGACY",
                sex="F",
                source="PURCHASED",
                current_bucket="FOUNDATION",
            )
        )
        await db.commit()
    register = await client.get("/api/animals", headers=owner)
    assert register.status_code == 200, register.text
    assert register.json()["total"] == 1 and register.json()["animals"][0]["id"] == 0
    schedule = await client.get("/api/health/schedule/0", headers=owner)
    assert schedule.status_code == 404, schedule.text
    assert schedule.json()["detail"] == "Animal not found"


async def test_exact_250_animal_reviewed_bucket_cohort_can_be_recorded(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    await make_batch(client, owner, count=250)
    preview = await client.post(
        "/api/health/events/preview",
        headers=owner,
        json={"scope": "bucket", "bucket": "QUARANTINE"},
    )
    assert preview.status_code == 200, preview.text
    ids = preview.json()["target_animal_ids"]
    assert len(ids) == 250 and len(set(ids)) == 250
    recorded = await client.post(
        "/api/health/events",
        headers=owner,
        json={
            "scope": "bucket",
            "bucket": "QUARANTINE",
            "expected_animal_ids": ids,
            "type": "TREATMENT",
            "notes": "Reviewed quarantine cohort examination",
        },
    )
    assert recorded.status_code == 201, recorded.text
    assert len(recorded.json()) == 250
    assert {event["animal_id"] for event in recorded.json()} == set(ids)
    async with get_sessionmaker()() as db:
        assert len(list((await db.execute(select(HealthEvent))).scalars())) == 250


async def test_linked_batch_scope_rejection_is_422_and_preserves_its_real_pending_duty(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    first = await make_batch(
        client, owner, count=2, date=(today() - timedelta(days=50)).isoformat()
    )
    second = await make_batch(
        client, owner, count=1, date=(today() - timedelta(days=50)).isoformat()
    )
    first_detail = await get_batch(client, owner, first["id"])
    second_detail = await get_batch(client, owner, second["id"])
    duty = next(
        task
        for task in first_detail["tasks"]
        if task["category"] == "VACCINE" and "PPR" in task["title"]
    )
    wrong = await client.post(
        "/api/health/events",
        headers=owner,
        json={
            "scope": "batch",
            "purchase_batch_id": second["id"],
            "expected_animal_ids": [animal["id"] for animal in second_detail["animals"]],
            "task_id": duty["id"],
            "type": "VACCINE",
            "disease_target": "PPR",
        },
    )
    assert wrong.status_code == 422, wrong.text
    assert wrong.json()["detail"] == "Health event scope must match the linked batch"
    async with get_sessionmaker()() as db:
        task = await db.get(Task, duty["id"])
        assert task is not None and task.status == "PENDING"
        assert not list((await db.execute(select(HealthEvent))).scalars())
    permitted = await client.post(
        "/api/health/events",
        headers=owner,
        json={
            "scope": "batch",
            "purchase_batch_id": first["id"],
            "expected_animal_ids": [animal["id"] for animal in first_detail["animals"]],
            "task_id": duty["id"],
            "type": "VACCINE",
            "disease_target": "PPR",
        },
    )
    assert permitted.status_code == 201, permitted.text
    assert len(permitted.json()) == 2
    async with get_sessionmaker()() as db:
        task = await db.get(Task, duty["id"])
        assert task is not None and task.status == "DONE"
