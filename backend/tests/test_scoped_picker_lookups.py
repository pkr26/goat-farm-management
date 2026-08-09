"""Least-privilege, tenant-safe lookup contracts used by remote pickers."""

from datetime import timedelta

import httpx

from app.utils import today

from .conftest import owner_with_farm
from .test_tasks_extended import add_worker, login_user, make_custom_role

WORKER_PW = "workerpass123"


async def create_animal(
    client: httpx.AsyncClient,
    headers: dict,
    tag: str,
    *,
    sex: str = "F",
    name: str | None = None,
    breeding_ready: bool = False,
    bucket: str | None = None,
) -> dict:
    payload: dict[str, object] = {
        "tag_number": tag,
        "name": name,
        "sex": sex,
        "source": "PURCHASED",
        "current_bucket": bucket or ("BREEDING" if sex == "M" else "FOUNDATION"),
        "historical_import_reason": "Existing-herd scoped-picker fixture",
    }
    if breeding_ready:
        payload |= {
            "date_of_birth": (today() - timedelta(days=400)).isoformat(),
            "weight_kg": 26.0,
        }
    response = await client.post("/api/animals", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def set_status(client: httpx.AsyncClient, headers: dict, animal_id: int, status: str) -> None:
    response = await client.post(
        f"/api/animals/{animal_id}/status",
        json={"new_status": status},
        headers=headers,
    )
    assert response.status_code == 200, response.text


async def create_batch(
    client: httpx.AsyncClient, headers: dict, supplier: str, *, count: int = 1
) -> dict:
    response = await client.post(
        "/api/purchases/new",
        json={"date": today().isoformat(), "supplier": supplier, "count": count},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def limited_worker(
    client: httpx.AsyncClient,
    owner: dict,
    *,
    name: str,
    email: str,
    permissions: list[str],
) -> dict:
    role_id = await make_custom_role(client, owner, name, permissions)
    await add_worker(client, owner, role_id, email)
    headers, _user_id = await login_user(client, email, WORKER_PW)
    return headers | {"X-Farm-Id": owner["X-Farm-Id"]}


async def test_breeding_candidates_are_canonical_paged_and_least_privilege(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    literal_doe = await create_animal(
        client,
        owner,
        "DOE-%_MATCH",
        name="Literal Doe",
        breeding_ready=True,
    )
    other_doe = await create_animal(
        client,
        owner,
        "DOE-PLAIN",
        name="Plain Doe",
        breeding_ready=True,
    )
    await create_animal(client, owner, "DOE-YOUNG", name="Too young")
    literal_buck = await create_animal(
        client,
        owner,
        "BUCK-%_MATCH",
        sex="M",
        name="Literal Buck",
        breeding_ready=True,
    )
    inactive_buck = await create_animal(client, owner, "BUCK-DEAD", sex="M", breeding_ready=True)
    await set_status(client, owner, inactive_buck["id"], "DEAD")
    quarantine_buck = await create_animal(
        client,
        owner,
        "BUCK-QUARANTINE",
        sex="M",
        breeding_ready=True,
        bucket="QUARANTINE",
    )
    held_buck = await create_animal(client, owner, "BUCK-HELD", sex="M", breeding_ready=True)
    held = await client.post(
        "/api/health/events",
        json={
            "scope": "animal",
            "animal_id": held_buck["id"],
            "type": "TREATMENT",
            "disease_target": "PPR suspicion",
            "suspected_scheduled_disease": True,
        },
        headers=owner,
    )
    assert held.status_code == 201, held.text

    foreign_owner = await owner_with_farm(
        client, email="foreign-breeding@farm.in", farm_name="Foreign Farm"
    )
    foreign_doe = await create_animal(client, foreign_owner, "DOE-FOREIGN", breeding_ready=True)

    worker = await limited_worker(
        client,
        owner,
        name="Breeding only",
        email="breeding-picker@farm.in",
        permissions=["breeding.view", "breeding.manage"],
    )

    # The scoped workflow works without granting the full animal register.
    assert (await client.get("/api/animals", headers=worker)).status_code == 403
    first = await client.get(
        "/api/breeding/candidates",
        params={"kind": "doe", "limit": 1, "offset": 0},
        headers=worker,
    )
    assert first.status_code == 200, first.text
    assert first.json()["total"] == 2
    assert first.json()["limit"] == 1
    assert len(first.json()["candidates"]) == 1
    second = await client.get(
        "/api/breeding/candidates",
        params={"kind": "doe", "limit": 1, "offset": 1},
        headers=worker,
    )
    assert second.status_code == 200, second.text
    assert second.json()["total"] == 2
    assert {
        first.json()["candidates"][0]["id"],
        second.json()["candidates"][0]["id"],
    } == {literal_doe["id"], other_doe["id"]}
    assert foreign_doe["id"] not in {
        candidate["id"] for candidate in first.json()["candidates"] + second.json()["candidates"]
    }

    # Percent and underscore are literal text, never LIKE wildcards.
    literal = await client.get(
        "/api/breeding/candidates",
        params={"kind": "doe", "q": "%_MATCH"},
        headers=worker,
    )
    assert literal.status_code == 200, literal.text
    assert literal.json()["total"] == 1
    assert [row["id"] for row in literal.json()["candidates"]] == [literal_doe["id"]]
    assert literal.json()["candidates"][0]["age_months"] >= 10
    assert literal.json()["candidates"][0]["latest_weight_kg"] == 26.0

    bucks = await client.get(
        "/api/breeding/candidates",
        params={"kind": "buck", "q": "%_MATCH"},
        headers=worker,
    )
    assert bucks.status_code == 200, bucks.text
    assert [row["id"] for row in bucks.json()["candidates"]] == [literal_buck["id"]]
    all_bucks = await client.get(
        "/api/breeding/candidates", params={"kind": "buck"}, headers=worker
    )
    assert inactive_buck["id"] not in {row["id"] for row in all_bucks.json()["candidates"]}
    assert quarantine_buck["id"] not in {row["id"] for row in all_bucks.json()["candidates"]}
    assert held_buck["id"] not in {row["id"] for row in all_bucks.json()["candidates"]}

    created = await client.post(
        "/api/breeding",
        json={
            "doe_id": literal_doe["id"],
            "buck_id": literal_buck["id"],
            "breeding_date": today().isoformat(),
        },
        headers=worker,
    )
    assert created.status_code == 201, created.text
    after = await client.get("/api/breeding/candidates", params={"kind": "doe"}, headers=worker)
    assert literal_doe["id"] not in {row["id"] for row in after.json()["candidates"]}

    assert (
        await client.get(
            "/api/breeding/candidates",
            params={"kind": "doe", "limit": 101},
            headers=worker,
        )
    ).status_code == 422
    assert (
        await client.get(
            "/api/breeding/candidates",
            params={"kind": "doe", "q": "x" * 61},
            headers=worker,
        )
    ).status_code == 422
    assert (await client.get("/api/health/animals", headers=worker)).status_code == 403


async def test_health_lookups_are_targetable_tenant_safe_and_least_privilege(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    literal_animal = await create_animal(client, owner, "HEALTH-%_MATCH", name="Literal Animal")
    await create_animal(client, owner, "HEALTH-PLAIN", name="Plain Animal")
    inactive_animal = await create_animal(client, owner, "HEALTH-DEAD")
    await set_status(client, owner, inactive_animal["id"], "DEAD")

    literal_batch = await create_batch(client, owner, "Supplier %_MATCH", count=2)
    other_batch = await create_batch(client, owner, "Plain Supplier")
    empty_batch = await create_batch(client, owner, "No active animals")
    empty_detail = await client.get(f"/api/purchases/{empty_batch['id']}", headers=owner)
    assert empty_detail.status_code == 200, empty_detail.text
    for animal in empty_detail.json()["animals"]:
        await set_status(client, owner, animal["id"], "DEAD")

    foreign_owner = await owner_with_farm(
        client, email="foreign-health@farm.in", farm_name="Foreign Health Farm"
    )
    foreign_animal = await create_animal(client, foreign_owner, "HEALTH-FOREIGN")
    foreign_batch = await create_batch(client, foreign_owner, "Foreign Supplier")

    viewer = await limited_worker(
        client,
        owner,
        name="Health viewer",
        email="health-view-picker@farm.in",
        permissions=["health.view"],
    )
    collision_animal = await create_animal(
        client,
        owner,
        "HEALTH-ID-COLLISION",
        name=f"Unrelated #{literal_animal['id']}",
    )
    assert (await client.get("/api/animals", headers=viewer)).status_code == 403
    by_id = await client.get(
        "/api/health/animals", params={"q": f"#{literal_animal['id']}"}, headers=viewer
    )
    assert by_id.status_code == 200, by_id.text
    assert [row["id"] for row in by_id.json()["animals"]] == [literal_animal["id"]]
    assert collision_animal["id"] not in {row["id"] for row in by_id.json()["animals"]}
    literal = await client.get("/api/health/animals", params={"q": "%_MATCH"}, headers=viewer)
    assert literal.status_code == 200, literal.text
    assert [row["id"] for row in literal.json()["animals"]] == [literal_animal["id"]]
    for hidden_id in (inactive_animal["id"], foreign_animal["id"]):
        hidden = await client.get(
            "/api/health/animals", params={"q": f"#{hidden_id}"}, headers=viewer
        )
        assert hidden.status_code == 200, hidden.text
        assert hidden.json()["total"] == 0
    schedule = await client.get(f"/api/health/schedule/{literal_animal['id']}", headers=viewer)
    assert schedule.status_code == 200, schedule.text
    assert (await client.get("/api/health/purchase-batches", headers=viewer)).status_code == 403

    manager = await limited_worker(
        client,
        owner,
        name="Health manager",
        email="health-manage-picker@farm.in",
        permissions=["health.view", "health.manage"],
    )
    assert (await client.get("/api/purchases", headers=manager)).status_code == 403
    batches = await client.get(
        "/api/health/purchase-batches",
        params={"limit": 1, "offset": 0},
        headers=manager,
    )
    assert batches.status_code == 200, batches.text
    assert batches.json()["total"] == 2
    assert len(batches.json()["batches"]) == 1
    # A health-only manager gets only the opaque write selector. Supplier,
    # purchase date, original count and financial fields remain procurement
    # data behind purchases.view.
    assert set(batches.json()["batches"][0]) == {
        "id",
        "active_quarantine_animal_count",
    }
    next_batch = await client.get(
        "/api/health/purchase-batches",
        params={"limit": 1, "offset": 1},
        headers=manager,
    )
    assert next_batch.status_code == 200, next_batch.text
    assert {batches.json()["batches"][0]["id"], next_batch.json()["batches"][0]["id"]} == {
        literal_batch["id"],
        other_batch["id"],
    }
    literal_batches = await client.get(
        "/api/health/purchase-batches", params={"q": "%_MATCH"}, headers=manager
    )
    assert literal_batches.status_code == 200, literal_batches.text
    assert literal_batches.json()["total"] == 0  # supplier text is not a health search channel
    collision_batch = await create_batch(
        client,
        owner,
        f"Unrelated #{literal_batch['id']}",
    )
    exact_batch = await client.get(
        "/api/health/purchase-batches",
        params={"q": f"#{literal_batch['id']}"},
        headers=manager,
    )
    assert exact_batch.status_code == 200, exact_batch.text
    assert [row["id"] for row in exact_batch.json()["batches"]] == [literal_batch["id"]]
    assert collision_batch["id"] not in {row["id"] for row in exact_batch.json()["batches"]}
    assert exact_batch.json()["batches"][0]["active_quarantine_animal_count"] == 2
    for hidden_id in (empty_batch["id"], foreign_batch["id"]):
        hidden = await client.get(
            "/api/health/purchase-batches", params={"q": f"#{hidden_id}"}, headers=manager
        )
        assert hidden.status_code == 200, hidden.text
        assert hidden.json()["total"] == 0

    preview = await client.post(
        "/api/health/events/preview",
        json={"scope": "batch", "purchase_batch_id": literal_batch["id"]},
        headers=manager,
    )
    assert preview.status_code == 200, preview.text
    event = await client.post(
        "/api/health/events",
        json={
            "scope": "batch",
            "purchase_batch_id": literal_batch["id"],
            "type": "TREATMENT",
            "expected_animal_ids": preview.json()["target_animal_ids"],
        },
        headers=manager,
    )
    assert event.status_code == 201, event.text
    assert len(event.json()) == 2

    assert (
        await client.get("/api/health/animals", params={"limit": 101}, headers=manager)
    ).status_code == 422
    assert (
        await client.get("/api/health/purchase-batches", params={"q": "x" * 21}, headers=manager)
    ).status_code == 422
    oversized_id = await client.get(
        "/api/health/purchase-batches",
        params={"q": f"#{2_147_483_648}"},
        headers=manager,
    )
    assert oversized_id.status_code == 200, oversized_id.text
    assert oversized_id.json()["total"] == 0
    assert (
        await client.get("/api/breeding/candidates", params={"kind": "doe"}, headers=manager)
    ).status_code == 403
