"""Exact public preview snapshots for capacity, saved cohorts and linked duties."""

from datetime import timedelta

import httpx
from sqlalchemy import text

from app.db import get_sessionmaker
from app.models import Animal, Task
from app.utils import today

from .conftest import owner_with_farm
from .test_health_extended import get_batch, make_animal, make_batch
from .test_health_safety import _seed_herd_round_task


async def test_bucket_preview_accepts_exact_capacity_and_rejects_an_extra_animal(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="bulk-capacity@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        animals = [
            Animal(
                farm_id=farm_id,
                tag_number=f"PREVIEW-{number:03d}",
                name=f"Member {number}",
                breed="Osmanabadi",
                sex="F",
                source="PURCHASED",
                current_bucket="FOUNDATION",
                status="ACTIVE",
            )
            for number in range(250)
        ]
        db.add_all(animals)
        await db.commit()
        expected_ids = [animal.id for animal in animals]
    payload = {"scope": "bucket", "bucket": "FOUNDATION"}
    admitted = await client.post("/api/health/events/preview", headers=owner, json=payload)
    assert admitted.status_code == 200, admitted.text
    snapshot = admitted.json()
    assert snapshot["target_animal_ids"] == expected_ids
    assert snapshot["target_count"] == snapshot["max_targets"] == 250
    assert snapshot["target_animal_ages_months"] == [None] * 250
    assert snapshot["target_animals"] == [
        {"id": animal_id, "tag_number": f"PREVIEW-{number:03d}", "name": f"Member {number}"}
        for number, animal_id in enumerate(expected_ids)
    ]
    await make_animal(client, owner, tag="PREVIEW-OVER-CAPACITY")
    over_capacity = await client.post("/api/health/events/preview", headers=owner, json=payload)
    assert over_capacity.status_code == 409, over_capacity.text
    assert "exceeds 250 animals" in over_capacity.json()["detail"]


async def test_batch_preview_admits_the_largest_persistable_batch_key(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="bulk-batch-key@farm.in")
    async with get_sessionmaker()() as db:
        await db.execute(
            text("SELECT setval(pg_get_serial_sequence('purchase_batches', 'id'), :id, false)"),
            {"id": 2_147_483_647},
        )
        await db.commit()
    batch = await make_batch(client, owner, count=2)
    assert batch["id"] == 2_147_483_647
    detail = await get_batch(client, owner, batch["id"])
    response = await client.post(
        "/api/health/events/preview",
        headers=owner,
        json={"scope": "batch", "purchase_batch_id": batch["id"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["target_animal_ids"] == sorted(
        animal["id"] for animal in detail["animals"]
    )
    assert response.json()["purchase_batch_id"] == 2_147_483_647
    assert response.json()["target_count"] == 2 and response.json()["max_targets"] == 1000


async def test_linked_preview_admits_a_constraint_valid_restored_maximum_task_key(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="bulk-task-key@farm.in")
    batch = await make_batch(client, owner, count=2)
    detail = await get_batch(client, owner, batch["id"])
    source_id = next(task["id"] for task in detail["tasks"] if task["category"] == "VACCINE")
    async with get_sessionmaker()() as db:
        source = await db.get(Task, source_id)
        assert source is not None and source.status == "PENDING"
        # Restored pending programme provenance, not a fabricated completion.
        # No constraints are disabled and the actual generated donor survives.
        db.add(
            Task(
                id=2_147_483_647,
                farm_id=source.farm_id,
                title=source.title,
                title_key=source.title_key,
                title_args=source.title_args.copy(),
                due_date=source.due_date,
                status="PENDING",
                category=source.category,
                purchase_batch_id=source.purchase_batch_id,
                auto_generated=source.auto_generated,
                assigned_role_id=source.assigned_role_id,
            )
        )
        await db.commit()
    response = await client.post(
        "/api/health/events/preview",
        headers=owner,
        json={"scope": "batch", "purchase_batch_id": batch["id"], "task_id": 2_147_483_647},
    )
    assert response.status_code == 200, response.text
    assert response.json()["task_id"] == 2_147_483_647
    assert response.json()["target_animal_ids"] == sorted(
        animal["id"] for animal in detail["animals"]
    )


async def test_round_preview_preserves_declared_members_exclusions_and_component_evidence(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="bulk-round-cohort@farm.in")
    members = [
        await make_animal(
            client,
            owner,
            tag=f"ROUND-MEMBER-{number}",
            date_of_birth=(today() - timedelta(days=400)).isoformat(),
        )
        for number in range(3)
    ]
    covered_id, excluded_id, untreated_id = [member["id"] for member in members]
    task_id = await _seed_herd_round_task(
        owner, "ET + HS pre-monsoon round (2026) — all animals", initialize=False
    )
    started = await client.post(f"/api/health/rounds/{task_id}/start", headers=owner)
    assert started.status_code == 200 and started.json()["total_targets"] == 3, started.text
    await make_animal(client, owner, tag="ROUND-AFTER-SNAPSHOT")
    exclusion = await client.post(
        f"/api/health/rounds/{task_id}/exclusions",
        headers=owner,
        json={"animal_ids": [excluded_id], "reason": "Vet deferred treatment for this member"},
    )
    assert exclusion.status_code == 200, exclusion.text
    recorded = await client.post(
        "/api/health/events",
        headers=owner,
        json={
            "scope": "bucket",
            "bucket": "FOUNDATION",
            "task_id": task_id,
            "expected_animal_ids": [covered_id],
            "type": "VACCINE",
            "disease_target": "ET",
        },
    )
    assert recorded.status_code == 201, recorded.text
    preview = await client.post(
        "/api/health/events/preview",
        headers=owner,
        json={
            "scope": "bucket",
            "bucket": "FOUNDATION",
            "task_id": task_id,
            "round_component": "Enterotoxaemia (ET)",
        },
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["target_animal_ids"] == [untreated_id]
    assert preview.json()["target_count"] == 1
    assert preview.json()["round_component"] == "Enterotoxaemia (ET)"


async def test_preview_rejects_nonrequired_round_components_and_mismatched_batch_duties(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="bulk-preview-admission@farm.in")
    await make_animal(client, owner, tag="PREVIEW-FOUNDATION")
    round_id = await _seed_herd_round_task(owner, "ET + HS pre-monsoon round (2026) — all animals")
    wrong_component = await client.post(
        "/api/health/events/preview",
        headers=owner,
        json={
            "scope": "bucket",
            "bucket": "FOUNDATION",
            "task_id": round_id,
            "round_component": "Peste des Petits Ruminants (PPR)",
        },
    )
    assert wrong_component.status_code == 422, wrong_component.text
    assert wrong_component.json()["detail"] == "Select a required round component"
    batch = await make_batch(client, owner, count=1)
    detail = await get_batch(client, owner, batch["id"])
    task_id = next(task["id"] for task in detail["tasks"] if task["category"] == "VACCINE")
    wrong_scope = await client.post(
        "/api/health/events/preview",
        headers=owner,
        json={"scope": "bucket", "bucket": "FOUNDATION", "task_id": task_id},
    )
    assert wrong_scope.status_code == 422, wrong_scope.text
    assert wrong_scope.json()["detail"] == "Health preview scope must match the linked batch"
    other_batch = await make_batch(client, owner, count=1)
    wrong_batch = await client.post(
        "/api/health/events/preview",
        headers=owner,
        json={
            "scope": "batch",
            "purchase_batch_id": other_batch["id"],
            "task_id": task_id,
        },
    )
    assert wrong_batch.status_code == 422, wrong_batch.text
    assert wrong_batch.json()["detail"] == "Health preview scope must match the linked batch"
    nonherd_component = await client.post(
        "/api/health/events/preview",
        headers=owner,
        json={
            "scope": "batch",
            "purchase_batch_id": batch["id"],
            "task_id": task_id,
            "round_component": "Enterotoxaemia (ET)",
        },
    )
    assert nonherd_component.status_code == 422, nonherd_component.text
    assert nonherd_component.json()["detail"] == "Round component requires a herd duty"
