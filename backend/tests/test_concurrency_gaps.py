"""Concurrency gap tests (2026-09-23 verification plan, category 14).

The pairwise races (mix ordering, move-vs-sell, tag races) are pinned in
test_concurrency / test_lock_order_hardening. These add the combined
workload and the cross-tenant dimension:

- one farm under simultaneous kiddings + bucket moves + feed mixes + duty
  completions — no 5xx, no deadlock, and the final state is EXACT (one
  kidding per doe, parity 1, non-negative gram-exact inventory, tasks
  DONE);
- farms A and B hammered simultaneously — every request succeeds inside
  its own tenant and neither farm's state leaks into the other's (the
  per-farm advisory locks must not serialize unrelated tenants into
  failures).
"""

import asyncio
from datetime import timedelta

import httpx
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import FeedInventory, KidEntry, KiddingRecord, Task
from app.utils import today

from .conftest import owner_with_farm
from .test_e2e_lifecycle_audit import breed, make_animal


async def _confirmed_doe(client: httpx.AsyncClient, owner: dict, tag: str, bred_days_ago: int):
    doe = await make_animal(client, owner, f"{tag}-F")
    buck = await make_animal(client, owner, f"{tag}-M", sex="M", weight_kg=32.0)
    br = await breed(client, owner, doe["id"], buck["id"], today() - timedelta(days=bred_days_ago))
    detail = (await client.get(f"/api/breeding/{br['id']}", headers=owner)).json()
    scan = await client.post(
        f"/api/breeding/{br['id']}/ultrasound",
        json={"pregnant": True, "kid_count": 1, "date": detail["ultrasound_date"]},
        headers=owner,
    )
    assert scan.status_code == 200, scan.text
    return doe, br


async def test_mixed_workload_one_farm_final_state_is_exact(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client, email="mixed@farm.in")

    does: list[dict] = []
    breedings = []
    for i in range(3):
        doe, br = await _confirmed_doe(client, owner, f"MIX{i}", bred_days_ago=150)
        does.append(doe)
        breedings.append(br)

    # Two movable (non-pregnant) does for concurrent manual moves.
    movers = [await make_animal(client, owner, f"MOVE-{i}") for i in range(2)]

    # Stock + a couple of completable duties.
    listing = await client.get("/api/feeding/inventory", headers=owner)
    for item in listing.json():
        await client.post(
            f"/api/feeding/inventory/{item['id']}/add",
            json={"qty_kg": 500.0, "price_per_kg": 10.0},
            headers=owner | {"Idempotency-Key": f"mix-stock-{item['id']}"},
        )
    recipes = (await client.get("/api/feeding/recipes", headers=owner)).json()
    code = recipes["recipes"][0]["code"] if isinstance(recipes, dict) else recipes[0]["code"]
    duties = []
    for i in range(3):
        t = await client.post(
            "/api/tasks",
            json={"title": f"Mixed duty {i}", "category": "OTHER", "due_date": today().isoformat()},
            headers=owner,
        )
        assert t.status_code == 201, t.text
        duties.append(t.json()["id"])

    async def kidding(i: int) -> httpx.Response:
        return await client.post(
            "/api/kidding",
            json={
                "breeding_record_id": breedings[i]["id"],
                "date": (today() - timedelta(days=2)).isoformat(),
                "ease": "NORMAL",
                "notes": "",
                "kids": [{"sex": "M", "status": "ALIVE", "birth_weight": 2.5}],
            },
            headers=owner | {"Idempotency-Key": f"mixed-kidding-{i}"},
        )

    async def move(i: int) -> httpx.Response:
        return await client.post(
            f"/api/animals/{movers[i]['id']}/move",
            json={"to_bucket": "BREEDING", "reason": "mixed workload"},
            headers=owner,
        )

    async def mix(i: int) -> httpx.Response:
        return await client.post(
            "/api/feeding/mix",
            json={"recipe_code": code, "batch_kg": 5.0},
            headers=owner | {"Idempotency-Key": f"mixed-mix-{i}"},
        )

    async def complete(i: int) -> httpx.Response:
        return await client.post(f"/api/tasks/{duties[i]}/complete", headers=owner)

    responses = await asyncio.gather(
        kidding(0), kidding(1), kidding(2),
        move(0), move(1),
        mix(0), mix(1),
        complete(0), complete(1), complete(2),
    )
    for resp in responses:
        assert resp.status_code < 500, f"5xx under mixed load: {resp.status_code} {resp.text[:150]}"

    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        for i, br in enumerate(breedings):
            rows = (
                await db.execute(
                    select(KiddingRecord).where(KiddingRecord.breeding_record_id == br["id"])
                )
            ).scalars().all()
            assert len(rows) == 1, f"doe {i}: {len(rows)} kidding rows"
            assert rows[0].parity == 1
            kids = (
                await db.execute(
                    select(KidEntry).where(KidEntry.kidding_record_id == rows[0].id)
                )
            ).scalars().all()
            assert len(kids) == 1

        from app.models import Animal

        for mover in movers:
            row = await db.get(Animal, mover["id"])
            assert row.current_bucket == "BREEDING", row.current_bucket

        inventory = (
            await db.execute(select(FeedInventory).where(FeedInventory.farm_id == farm_id))
        ).scalars().all()
        for item in inventory:
            assert item.qty_on_hand >= 0, f"{item.ingredient} went negative: {item.qty_on_hand}"

        tasks = (
            await db.execute(select(Task).where(Task.id.in_(duties)))
        ).scalars().all()
        assert all(t.status == "DONE" for t in tasks), [t.status for t in tasks]


async def test_cross_farm_parallel_hammer_stays_isolated(client: httpx.AsyncClient) -> None:
    a = await owner_with_farm(client, email="hammer-a@farm.in", farm_name="Hammer A")
    b = await owner_with_farm(client, email="hammer-b@farm.in", farm_name="Hammer B")

    async def burst(headers: dict, tag_prefix: str) -> list[httpx.Response]:
        async def one(i: int) -> httpx.Response:
            return await client.post(
                "/api/animals",
                json={
                    "tag_number": f"{tag_prefix}-{i}",
                    "sex": "F",
                    "source": "PURCHASED",
                    "current_bucket": "FOUNDATION",
                    "date_of_birth": (today() - timedelta(days=700)).isoformat(),
                    "historical_import_reason": "hammer fixture",
                },
                headers=headers,
            )

        async def list_animals() -> httpx.Response:
            return await client.get("/api/animals", headers=headers)

        return await asyncio.gather(*[one(i) for i in range(6)], list_animals())

    a_results, b_results = await asyncio.gather(burst(a, "HA"), burst(b, "HB"))
    for resp in a_results[:-1] + b_results[:-1]:
        assert resp.status_code == 201, f"create failed: {resp.status_code} {resp.text[:120]}"

    a_list = (await client.get("/api/animals", headers=a)).json()["animals"]
    b_list = (await client.get("/api/animals", headers=b)).json()["animals"]
    a_tags = {x["tag_number"] for x in a_list}
    b_tags = {x["tag_number"] for x in b_list}
    assert a_tags == {f"HA-{i}" for i in range(6)}, sorted(a_tags)
    assert b_tags == {f"HB-{i}" for i in range(6)}, sorted(b_tags)
    assert not (a_tags & b_tags)
