"""Adversarial API tests for durable Idempotency-Key semantics.

Every concurrency case uses two ASGI clients (therefore two database
sessions). A test-only delay holds the winning claim transaction open long
enough for the duplicate PostgreSQL INSERT to contend on the unique index.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import timedelta
from decimal import Decimal
from types import ModuleType
from typing import Any, cast

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select, update

import app.api.animals as animals_api
import app.api.auth as auth_api
import app.api.feeding as feeding_api
import app.api.finance as finance_api
import app.api.health as health_api
import app.api.purchases as purchases_api
import app.api.simulation as simulation_api
import app.api.tasks as tasks_api
import app.api.team as team_api
from app.core.config import get_settings
from app.db import get_sessionmaker
from app.main import create_app
from app.models import (
    Animal,
    Farm,
    FeedFinishedStock,
    FeedingRecord,
    FeedInventory,
    HealthEvent,
    IdempotencyRecord,
    MovementRestrictionAction,
    PurchaseBatch,
    Role,
    SimulationScenario,
    Task,
    TaskStatus,
    Transaction,
    User,
    WeightRecord,
)
from app.schemas.finance import TransactionIn
from app.schemas.team import WorkerCreateIn
from app.services.idempotency import _request_hashes, purge_expired_idempotency_records
from app.utils import money as actual_money
from app.utils import today, utcnow

from .conftest import create_farm, login, owner_with_farm, register

WORKER_PASSWORD = "workerpass123"


def finance_payload(**overrides: object) -> dict[str, object]:
    return {
        "date": today().isoformat(),
        "type": "EXPENSE",
        "category": "FEED",
        "amount": 100.0,
    } | overrides


def test_ordinary_request_fingerprint_remains_legacy_sha256_compatible() -> None:
    operation = "POST /api/finance/new"
    payload = TransactionIn.model_validate(finance_payload(notes="legacy compatible"))
    canonical = json.dumps(
        {
            "version": 1,
            "operation": operation,
            "path": {},
            "body": payload.model_dump(mode="json", exclude_none=False),
        },
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    legacy_hash = hashlib.sha256(canonical).hexdigest()

    persisted_hash, accepted_hashes = _request_hashes(operation, payload, {})

    assert persisted_hash == legacy_hash
    assert accepted_hashes == (legacy_hash,)


def hold_winning_claim(monkeypatch: pytest.MonkeyPatch, route_module: ModuleType) -> None:
    """Keep the winner uncommitted while its duplicate reaches ON CONFLICT."""
    real_execute = route_module.execute_idempotent

    async def delayed_execute(*args: object, **kwargs: Any) -> Any:
        real_mutate = kwargs["mutate"]

        async def delayed_mutate() -> Any:
            await asyncio.sleep(0.1)
            return await real_mutate()

        kwargs["mutate"] = delayed_mutate
        return await real_execute(*args, **kwargs)

    monkeypatch.setattr(route_module, "execute_idempotent", delayed_execute)


async def concurrent_posts(
    path: str,
    payload: dict[str, object],
    headers: dict[str, str],
) -> tuple[httpx.Response, httpx.Response]:
    """Issue simultaneous requests through distinct clients/sessions."""
    app = create_app()
    async with (
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as first,
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as second,
    ):
        one, two = await asyncio.gather(
            first.post(path, json=payload, headers=headers),
            second.post(path, json=payload, headers=headers),
        )
    return one, two


async def inventory(client: httpx.AsyncClient, headers: dict[str, str]) -> list[dict[str, Any]]:
    response = await client.get("/api/feeding/inventory", headers=headers)
    assert response.status_code == 200, response.text
    return cast(list[dict[str, Any]], response.json())


async def stock_all_ingredients(
    client: httpx.AsyncClient, headers: dict[str, str], qty_kg: float = 1000.0
) -> None:
    for item in await inventory(client, headers):
        response = await client.post(
            f"/api/feeding/inventory/{item['id']}/add",
            json={"qty_kg": qty_kg},
            headers=headers,
        )
        assert response.status_code == 200, response.text


async def idempotency_count() -> int:
    async with get_sessionmaker()() as db:
        return (await db.execute(select(func.count(IdempotencyRecord.id)))).scalar_one()


async def test_farm_create_replays_at_quota_and_persists_one_complete_seed_graph(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = await register(client, "farm-idempotency-owner@farm.in")
    monkeypatch.setattr(get_settings(), "max_farms_per_user", 1)
    payload: dict[str, object] = {
        "name": " Retry-safe Farm ",
        "location": " Phoenix ",
        "timezone": "America/Phoenix",
    }
    raw_key = "farm-create-lost-response"
    keyed = headers | {"Idempotency-Key": raw_key}

    first = await client.post("/api/auth/farms", json=payload, headers=keyed)
    assert first.status_code == 201, first.text
    assert first.json()["name"] == "Retry-safe Farm"
    assert first.json()["location"] == "Phoenix"

    # The first commit consumes the user's only slot. Replay must be resolved
    # before capacity validation and return the exact committed response.
    replay_at_cap = await client.post("/api/auth/farms", json=payload, headers=keyed)
    assert replay_at_cap.status_code == 201, replay_at_cap.text
    assert replay_at_cap.json() == first.json()
    assert replay_at_cap.headers["Idempotency-Replayed"] == "true"

    changed_body = await client.post(
        "/api/auth/farms",
        json=payload | {"name": "A different logical farm"},
        headers=keyed,
    )
    assert changed_body.status_code == 409, changed_body.text
    assert "different request" in changed_body.json()["detail"]

    new_key_at_cap = await client.post(
        "/api/auth/farms",
        json=payload,
        headers=headers | {"Idempotency-Key": "farm-create-second-action"},
    )
    assert new_key_at_cap.status_code == 400, new_key_at_cap.text
    assert "maximum of 1 farms" in new_key_at_cap.json()["detail"]

    farm_id = first.json()["id"]
    async with get_sessionmaker()() as db:
        assert (
            await db.execute(select(func.count(Farm.id)).where(Farm.id == farm_id))
        ).scalar_one() == 1
        assert (
            await db.execute(select(func.count(Role.id)).where(Role.farm_id == farm_id))
        ).scalar_one() > 0
        assert (
            await db.execute(
                select(func.count(FeedInventory.id)).where(FeedInventory.farm_id == farm_id)
            )
        ).scalar_one() > 0
        records = list(
            (
                await db.execute(
                    select(IdempotencyRecord).where(
                        IdempotencyRecord.operation == "auth.farms.create"
                    )
                )
            ).scalars()
        )
    assert len(records) == 1
    assert records[0].farm_id is None
    assert records[0].key_digest != raw_key
    assert raw_key not in str(records[0].response_body)


async def test_farm_create_deduplicates_concurrently_and_isolates_actors(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_actor = await register(client, "farm-idempotency-first@farm.in")
    second_actor = await register(client, "farm-idempotency-second@farm.in")
    monkeypatch.setattr(get_settings(), "max_farms_per_user", 1)
    hold_winning_claim(monkeypatch, auth_api)
    payload: dict[str, object] = {"name": "Concurrent Farm"}
    shared_key = "same-key-is-safe-across-actors"

    one, two = await concurrent_posts(
        "/api/auth/farms",
        payload,
        first_actor | {"Idempotency-Key": shared_key},
    )
    assert one.status_code == two.status_code == 201
    assert one.json() == two.json()
    assert sorted(response.headers.get("Idempotency-Replayed", "") for response in (one, two)) == [
        "",
        "true",
    ]

    isolated = await client.post(
        "/api/auth/farms",
        json=payload,
        headers=second_actor | {"Idempotency-Key": shared_key},
    )
    assert isolated.status_code == 201, isolated.text
    assert isolated.json()["id"] != one.json()["id"]
    isolated_replay = await client.post(
        "/api/auth/farms",
        json=payload,
        headers=second_actor | {"Idempotency-Key": shared_key},
    )
    assert isolated_replay.status_code == 201
    assert isolated_replay.json() == isolated.json()

    async with get_sessionmaker()() as db:
        farm_counts = list(
            (
                await db.execute(
                    select(Farm.owner_id, func.count(Farm.id))
                    .group_by(Farm.owner_id)
                    .order_by(Farm.owner_id)
                )
            ).all()
        )
        records = list(
            (
                await db.execute(
                    select(IdempotencyRecord).where(
                        IdempotencyRecord.operation == "auth.farms.create"
                    )
                )
            ).scalars()
        )
    assert [count for _owner_id, count in farm_counts] == [1, 1]
    assert len(records) == 2
    assert {record.actor_id for record in records} == {owner_id for owner_id, _ in farm_counts}
    assert all(record.farm_id is None for record in records)


async def test_finance_create_replays_conflicts_and_deduplicates_concurrently(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client)
    payload = finance_payload(amount=101.25, notes="one durable ledger row")
    keyed = owner | {"Idempotency-Key": "finance-create-sequential"}

    first = await client.post("/api/finance/new", json=payload, headers=keyed)
    replay = await client.post("/api/finance/new", json=payload, headers=keyed)
    assert first.status_code == replay.status_code == 201
    assert replay.json() == first.json()
    assert "Idempotency-Replayed" not in first.headers
    assert replay.headers["Idempotency-Replayed"] == "true"

    conflict = await client.post(
        "/api/finance/new",
        json=payload | {"amount": 999.0},
        headers=keyed,
    )
    assert conflict.status_code == 409
    assert "different request" in conflict.json()["detail"]

    # Without the optional header, existing duplicate-creation behavior stays
    # intact: two calls create two independent ledger entries.
    omitted_one = await client.post(
        "/api/finance/new", json=finance_payload(amount=7), headers=owner
    )
    omitted_two = await client.post(
        "/api/finance/new", json=finance_payload(amount=7), headers=owner
    )
    assert omitted_one.status_code == omitted_two.status_code == 201
    assert omitted_one.json()["id"] != omitted_two.json()["id"]

    hold_winning_claim(monkeypatch, finance_api)
    concurrent_headers = owner | {"Idempotency-Key": "finance-create-concurrent"}
    one, two = await concurrent_posts(
        "/api/finance/new", finance_payload(amount=55.5), concurrent_headers
    )
    assert one.status_code == two.status_code == 201
    assert one.json() == two.json()
    assert sorted(response.headers.get("Idempotency-Replayed", "") for response in (one, two)) == [
        "",
        "true",
    ]

    async with get_sessionmaker()() as db:
        ledger_count = (
            await db.execute(
                select(func.count(Transaction.id)).where(
                    Transaction.farm_id == int(owner["X-Farm-Id"])
                )
            )
        ).scalar_one()
    assert ledger_count == 4  # one sequential + one concurrent + two headerless
    assert await idempotency_count() == 2


async def test_finance_correction_is_exactly_once_and_hashes_path_identity(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client)
    original = await client.post(
        "/api/finance/new", json=finance_payload(amount=400), headers=owner
    )
    assert original.status_code == 201, original.text
    original_id = original.json()["id"]
    correction = finance_payload(
        amount=375,
        category="EQUIPMENT",
        reason="Correct supplier invoice amount",
    )

    hold_winning_claim(monkeypatch, finance_api)
    keyed = owner | {"Idempotency-Key": "finance-correction-concurrent"}
    path = f"/api/finance/transactions/{original_id}/correct"
    one, two = await concurrent_posts(path, correction, keyed)
    assert one.status_code == two.status_code == 201
    assert one.json() == two.json()
    replacement_id = one.json()["id"]

    replay = await client.post(path, json=correction, headers=keyed)
    assert replay.status_code == 201
    assert replay.json() == one.json()

    # The body is identical, but a concrete transaction ID is part of the
    # request hash, so the same route-template key cannot drift to another row.
    different_path = await client.post(
        f"/api/finance/transactions/{original_id + 999}/correct",
        json=correction,
        headers=keyed,
    )
    assert different_path.status_code == 409

    async with get_sessionmaker()() as db:
        rows = list(
            (
                await db.execute(
                    select(Transaction)
                    .where(Transaction.farm_id == int(owner["X-Farm-Id"]))
                    .order_by(Transaction.id)
                )
            ).scalars()
        )
    assert len(rows) == 2
    assert rows[0].id == original_id
    assert rows[0].voided_at is not None
    assert rows[1].id == replacement_id
    assert rows[1].correction_of_id == original_id


async def test_purchase_batch_concurrent_retries_create_one_complete_domain_graph(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client)
    payload: dict[str, object] = {
        "date": today().isoformat(),
        "supplier": "Retry-safe supplier",
        "count": 2,
        "sex": "F",
        "avg_age_months": 7,
        "avg_weight_kg": 19.5,
        "total_price": 2500,
        "notes": "one batch only",
        "create_animals": True,
    }
    hold_winning_claim(monkeypatch, purchases_api)
    keyed = owner | {"Idempotency-Key": "purchase-batch-concurrent"}
    one, two = await concurrent_posts("/api/purchases/new", payload, keyed)
    assert one.status_code == two.status_code == 201
    assert one.json() == two.json()
    assert one.json()["animals_created"] == 2
    assert one.json()["open_tasks"] > 0
    batch_id = one.json()["id"]

    replay = await client.post("/api/purchases/new", json=payload, headers=keyed)
    assert replay.status_code == 201
    assert replay.json() == one.json()
    conflict = await client.post("/api/purchases/new", json=payload | {"count": 3}, headers=keyed)
    assert conflict.status_code == 409

    async with get_sessionmaker()() as db:
        batch_count = (await db.execute(select(func.count(PurchaseBatch.id)))).scalar_one()
        animal_count = (
            await db.execute(
                select(func.count(Animal.id)).where(Animal.purchase_batch_id == batch_id)
            )
        ).scalar_one()
        task_count = (
            await db.execute(select(func.count(Task.id)).where(Task.purchase_batch_id == batch_id))
        ).scalar_one()
        expense_count = (
            await db.execute(
                select(func.count(Transaction.id)).where(
                    Transaction.source_type == "PURCHASE_BATCH",
                    Transaction.source_id == batch_id,
                )
            )
        ).scalar_one()
    assert batch_count == 1
    assert animal_count == 2
    assert task_count == one.json()["open_tasks"]
    assert expense_count == 1
    assert await idempotency_count() == 1


async def test_individual_purchase_retry_creates_one_complete_domain_graph(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lost response cannot duplicate an auto-tagged one-head purchase."""
    owner = await owner_with_farm(client)
    payload: dict[str, object] = {
        "sex": "F",
        "source": "PURCHASED",
        "current_bucket": "FOUNDATION",
        "purchase_date": today().isoformat(),
        "purchase_price": 3210.5,
        "seller_name": "Retry-safe individual supplier",
        "weight_kg": 24.25,
    }
    hold_winning_claim(monkeypatch, animals_api)
    keyed = owner | {"Idempotency-Key": "individual-purchase-concurrent"}
    one, two = await concurrent_posts("/api/animals", payload, keyed)
    assert one.status_code == two.status_code == 201
    assert one.json() == two.json()
    assert sorted(response.headers.get("Idempotency-Replayed", "") for response in (one, two)) == [
        "",
        "true",
    ]
    animal_id = one.json()["id"]

    replay = await client.post("/api/animals", json=payload, headers=keyed)
    assert replay.status_code == 201
    assert replay.json() == one.json()
    # The initial WeightRecord is flushed before SQL-derived summary facts are
    # serialized and persisted in the idempotency record.  The first response,
    # replay, and subsequent profile therefore agree instead of permanently
    # replaying a stale null/birth-weight fallback.
    assert one.json()["latest_weight_kg"] == 24.25
    assert replay.json()["latest_weight_kg"] == 24.25
    profile = await client.get(f"/api/animals/{animal_id}", headers=owner)
    assert profile.status_code == 200, profile.text
    assert profile.json()["animal"]["latest_weight_kg"] == 24.25
    conflict = await client.post("/api/animals", json=payload | {"weight_kg": 25}, headers=keyed)
    assert conflict.status_code == 409

    async with get_sessionmaker()() as db:
        animal = await db.get(Animal, animal_id)
        assert animal is not None
        assert animal.purchase_batch_id is not None
        batch_id = animal.purchase_batch_id
        assert (
            await db.execute(
                select(func.count(Animal.id)).where(Animal.purchase_batch_id == batch_id)
            )
        ).scalar_one() == 1
        assert (
            await db.execute(
                select(func.count(PurchaseBatch.id)).where(PurchaseBatch.id == batch_id)
            )
        ).scalar_one() == 1
        assert (
            await db.execute(select(func.count(Task.id)).where(Task.purchase_batch_id == batch_id))
        ).scalar_one() > 0
        assert (
            await db.execute(
                select(func.count(Transaction.id)).where(
                    Transaction.source_type == "PURCHASE_BATCH",
                    Transaction.source_id == batch_id,
                )
            )
        ).scalar_one() == 1


async def test_weight_retry_appends_one_record_and_hashes_the_animal_path(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client)
    animal = await client.post(
        "/api/animals",
        json={
            "tag_number": "WEIGHT-IDEMP-1",
            "sex": "F",
            "source": "PURCHASED",
            "current_bucket": "QUARANTINE",
            "purchase_date": today().isoformat(),
        },
        headers=owner,
    )
    assert animal.status_code == 201, animal.text
    animal_id = animal.json()["id"]
    payload: dict[str, object] = {
        "date": today().isoformat(),
        "weight_kg": 23.125,
        "bcs": 3,
        "notes": "one physical weighing",
    }

    hold_winning_claim(monkeypatch, animals_api)
    keyed = owner | {"Idempotency-Key": "animal-weight-concurrent"}
    path = f"/api/animals/{animal_id}/weight"
    one, two = await concurrent_posts(path, payload, keyed)
    assert one.status_code == two.status_code == 201
    assert one.json() == two.json()
    assert sorted(response.headers.get("Idempotency-Replayed", "") for response in (one, two)) == [
        "",
        "true",
    ]

    replay = await client.post(path, json=payload, headers=keyed)
    assert replay.status_code == 201
    assert replay.json() == one.json()
    assert replay.headers["Idempotency-Replayed"] == "true"
    conflict = await client.post(
        f"/api/animals/{animal_id + 999}/weight",
        json=payload,
        headers=keyed,
    )
    assert conflict.status_code == 409

    async with get_sessionmaker()() as db:
        count = (
            await db.execute(
                select(func.count(WeightRecord.id)).where(WeightRecord.animal_id == animal_id)
            )
        ).scalar_one()
    assert count == 1


async def test_saved_scenario_retry_creates_one_scenario(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client)
    defaults = await client.get("/api/simulation/defaults", headers=owner)
    assert defaults.status_code == 200, defaults.text
    payload: dict[str, object] = {
        "name": "Retry-safe expansion plan",
        "notes": "one saved decision",
        "assumptions": defaults.json(),
    }

    hold_winning_claim(monkeypatch, simulation_api)
    keyed = owner | {"Idempotency-Key": "simulation-scenario-concurrent"}
    one, two = await concurrent_posts("/api/simulation/scenarios", payload, keyed)
    assert one.status_code == two.status_code == 201
    assert one.json() == two.json()
    assert sorted(response.headers.get("Idempotency-Replayed", "") for response in (one, two)) == [
        "",
        "true",
    ]

    replay = await client.post("/api/simulation/scenarios", json=payload, headers=keyed)
    assert replay.status_code == 201
    assert replay.json() == one.json()
    assert replay.headers["Idempotency-Replayed"] == "true"
    conflict = await client.post(
        "/api/simulation/scenarios",
        json=payload | {"name": "A genuinely separate plan"},
        headers=keyed,
    )
    assert conflict.status_code == 409

    async with get_sessionmaker()() as db:
        count = (
            await db.execute(
                select(func.count(SimulationScenario.id)).where(
                    SimulationScenario.farm_id == int(owner["X-Farm-Id"])
                )
            )
        ).scalar_one()
    assert count == 1


async def test_feed_stock_add_deduplicates_inventory_and_ledger_concurrently(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client)
    items = await inventory(client, owner)
    item_id = items[0]["id"]
    other_item_id = items[1]["id"]
    payload: dict[str, object] = {"qty_kg": 12.5, "price_per_kg": 2.4}

    hold_winning_claim(monkeypatch, feeding_api)
    keyed = owner | {"Idempotency-Key": "feed-stock-concurrent"}
    path = f"/api/feeding/inventory/{item_id}/add"
    one, two = await concurrent_posts(path, payload, keyed)
    assert one.status_code == two.status_code == 200
    assert one.json() == two.json()
    assert one.json()["qty_on_hand"] == pytest.approx(12.5)

    replay = await client.post(path, json=payload, headers=keyed)
    assert replay.status_code == 200
    assert replay.json() == one.json()
    changed_body = await client.post(path, json=payload | {"qty_kg": 13}, headers=keyed)
    assert changed_body.status_code == 409
    changed_path = await client.post(
        f"/api/feeding/inventory/{other_item_id}/add", json=payload, headers=keyed
    )
    assert changed_path.status_code == 409

    async with get_sessionmaker()() as db:
        item = await db.get(FeedInventory, item_id)
        expenses = list(
            (await db.execute(select(Transaction).where(Transaction.category == "FEED"))).scalars()
        )
    assert item is not None and item.qty_on_hand == pytest.approx(12.5)
    assert len(expenses) == 1
    assert expenses[0].amount == Decimal("30.00")


async def test_feed_mix_deduplicates_ingredient_and_finished_stock_concurrently(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client)
    await stock_all_ingredients(client, owner)
    before = {row["ingredient"]: row["qty_on_hand"] for row in await inventory(client, owner)}

    hold_winning_claim(monkeypatch, feeding_api)
    payload: dict[str, object] = {"recipe_code": "FATTENING_50_50", "batch_kg": 100}
    keyed = owner | {"Idempotency-Key": "feed-mix-concurrent"}
    one, two = await concurrent_posts("/api/feeding/mix", payload, keyed)
    assert one.status_code == two.status_code == 200
    assert one.json() == two.json()

    after = {row["ingredient"]: row["qty_on_hand"] for row in await inventory(client, owner)}
    for line in one.json()["lines"]:
        ingredient = line["ingredient"]
        assert after[ingredient] == pytest.approx(before[ingredient] - line["kg_per_100kg"])

    replay = await client.post("/api/feeding/mix", json=payload, headers=keyed)
    assert replay.status_code == 200
    assert replay.json() == one.json()
    assert {
        row["ingredient"]: row["qty_on_hand"] for row in await inventory(client, owner)
    } == after
    conflict = await client.post("/api/feeding/mix", json=payload | {"batch_kg": 50}, headers=keyed)
    assert conflict.status_code == 409

    async with get_sessionmaker()() as db:
        finished = (
            await db.execute(
                select(FeedFinishedStock).where(
                    FeedFinishedStock.farm_id == int(owner["X-Farm-Id"]),
                    FeedFinishedStock.recipe_code == "FATTENING_50_50",
                )
            )
        ).scalar_one()
    assert finished.qty_on_hand == pytest.approx(100.0)


async def test_feed_dispense_does_not_cache_failure_then_deduplicates_stock_and_log(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client)
    payload: dict[str, object] = {
        "bucket": "BREEDING",
        "shift": "MORNING",
        "recipe_code": "CREEP",
        "qty_kg": 10,
    }
    keyed = owner | {"Idempotency-Key": "feed-dispense-retry"}

    # This 400 rolls back the claim. The exact same key/body must be allowed
    # after inventory state is repaired.
    failed = await client.post("/api/feeding/dispense", json=payload, headers=keyed)
    assert failed.status_code == 400
    assert await idempotency_count() == 0

    await stock_all_ingredients(client, owner)
    mixed = await client.post(
        "/api/feeding/mix",
        json={"recipe_code": "CREEP", "batch_kg": 40},
        headers=owner,
    )
    assert mixed.status_code == 200, mixed.text

    hold_winning_claim(monkeypatch, feeding_api)
    one, two = await concurrent_posts("/api/feeding/dispense", payload, keyed)
    assert one.status_code == two.status_code == 201
    assert one.json() == two.json()

    replay = await client.post("/api/feeding/dispense", json=payload, headers=keyed)
    assert replay.status_code == 201
    assert replay.json() == one.json()
    conflict = await client.post(
        "/api/feeding/dispense", json=payload | {"qty_kg": 11}, headers=keyed
    )
    assert conflict.status_code == 409

    async with get_sessionmaker()() as db:
        log_count = (
            await db.execute(
                select(func.count(FeedingRecord.id)).where(
                    FeedingRecord.farm_id == int(owner["X-Farm-Id"])
                )
            )
        ).scalar_one()
        finished = (
            await db.execute(
                select(FeedFinishedStock).where(
                    FeedFinishedStock.farm_id == int(owner["X-Farm-Id"]),
                    FeedFinishedStock.recipe_code == "CREEP",
                )
            )
        ).scalar_one()
    assert log_count == 1
    assert finished.qty_on_hand == pytest.approx(30.0)


async def test_bulk_health_event_retries_preserve_the_reviewed_target_set_exactly_once(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client)
    for index in range(2):
        animal = await client.post(
            "/api/animals",
            json={
                "tag_number": f"HEALTH-IDEMP-{index}",
                "sex": "F",
                "source": "PURCHASED",
                "current_bucket": "FOUNDATION",
                "historical_import_reason": "Idempotency fixture",
            },
            headers=owner,
        )
        assert animal.status_code == 201, animal.text
    preview = await client.post(
        "/api/health/events/preview",
        json={"scope": "bucket", "bucket": "FOUNDATION"},
        headers=owner,
    )
    assert preview.status_code == 200, preview.text
    target_ids = preview.json()["target_animal_ids"]
    payload: dict[str, object] = {
        "scope": "bucket",
        "bucket": "FOUNDATION",
        "expected_animal_ids": target_ids,
        "type": "TREATMENT",
        "cost": 41.25,
        "disease_target": "Retry-safe concern",
        "suspected_scheduled_disease": True,
    }

    hold_winning_claim(monkeypatch, health_api)
    keyed = owner | {"Idempotency-Key": "bulk-health-concurrent"}
    one, two = await concurrent_posts("/api/health/events", payload, keyed)
    assert one.status_code == two.status_code == 201
    assert one.json() == two.json()
    assert sorted(response.headers.get("Idempotency-Replayed", "") for response in (one, two)) == [
        "",
        "true",
    ]

    replay = await client.post("/api/health/events", json=payload, headers=keyed)
    assert replay.status_code == 201
    assert replay.json() == one.json()
    assert replay.headers["Idempotency-Replayed"] == "true"
    changed_targets = await client.post(
        "/api/health/events",
        json=payload | {"expected_animal_ids": target_ids[:1]},
        headers=keyed,
    )
    assert changed_targets.status_code == 409

    async with get_sessionmaker()() as db:
        assert (await db.execute(select(func.count(HealthEvent.id)))).scalar_one() == 2
        assert (
            await db.execute(select(func.count(MovementRestrictionAction.id)))
        ).scalar_one() == 2
        assert (
            await db.execute(
                select(func.count(Transaction.id)).where(Transaction.source_type == "HEALTH_EVENT")
            )
        ).scalar_one() == 1
    assert await idempotency_count() == 1


async def test_keys_are_isolated_by_actor_and_farm(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    role = await client.post(
        "/api/team/roles",
        json={
            "name": "Finance clerk",
            "permissions": ["finance.view", "finance.manage"],
        },
        headers=owner,
    )
    assert role.status_code == 201, role.text
    worker = await client.post(
        "/api/team/workers",
        json={
            "name": "Retry Clerk",
            "email": "retry-clerk@farm.in",
            "password": WORKER_PASSWORD,
            "role_id": role.json()["id"],
        },
        headers=owner,
    )
    assert worker.status_code == 201, worker.text
    worker_headers = await login(client, "retry-clerk@farm.in", WORKER_PASSWORD)
    worker_headers |= {"X-Farm-Id": owner["X-Farm-Id"]}

    payload = finance_payload(amount=88)
    shared_key = {"Idempotency-Key": "same-opaque-key"}
    owner_result = await client.post("/api/finance/new", json=payload, headers=owner | shared_key)
    worker_result = await client.post(
        "/api/finance/new", json=payload, headers=worker_headers | shared_key
    )
    assert owner_result.status_code == worker_result.status_code == 201
    assert owner_result.json()["id"] != worker_result.json()["id"]

    beta = await create_farm(
        client,
        {"Authorization": owner["Authorization"]},
        name="Beta Farm",
    )
    beta_result = await client.post("/api/finance/new", json=payload, headers=beta | shared_key)
    assert beta_result.status_code == 201
    assert beta_result.json()["id"] not in {
        owner_result.json()["id"],
        worker_result.json()["id"],
    }

    async with get_sessionmaker()() as db:
        alpha_count = (
            await db.execute(
                select(func.count(Transaction.id)).where(
                    Transaction.farm_id == int(owner["X-Farm-Id"])
                )
            )
        ).scalar_one()
        beta_count = (
            await db.execute(
                select(func.count(Transaction.id)).where(
                    Transaction.farm_id == int(beta["X-Farm-Id"])
                )
            )
        ).scalar_one()
    assert alpha_count == 2
    assert beta_count == 1
    assert await idempotency_count() == 3


async def test_key_bounds_server_errors_and_expiry_cleanup(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client)
    payload = finance_payload(amount=19)
    for invalid in ("", "contains space", "x" * 129):
        response = await client.post(
            "/api/finance/new",
            json=payload,
            headers=owner | {"Idempotency-Key": invalid},
        )
        assert response.status_code == 422

    # An unhandled server exception also rolls the claim back. ASGITransport
    # propagates it in tests; production's exception boundary emits HTTP 500.
    def crash_money(value: object) -> Decimal:
        raise RuntimeError("injected mutation failure")

    monkeypatch.setattr("app.api.finance.money", crash_money)
    keyed = owner | {"Idempotency-Key": "retry-after-server-error"}
    with pytest.raises(RuntimeError, match="injected mutation failure"):
        await client.post("/api/finance/new", json=payload, headers=keyed)
    assert await idempotency_count() == 0
    monkeypatch.setattr("app.api.finance.money", actual_money)

    success = await client.post("/api/finance/new", json=payload, headers=keyed)
    assert success.status_code == 201
    first_id = success.json()["id"]

    # Expiry is an explicit replay boundary: this request removes only its own
    # indexed old row, and the same opaque key becomes a fresh operation.
    async with get_sessionmaker()() as db:
        await db.execute(
            update(IdempotencyRecord).values(expires_at=utcnow() - timedelta(seconds=1))
        )
        await db.commit()
    after_expiry = await client.post("/api/finance/new", json=payload, headers=keyed)
    assert after_expiry.status_code == 201
    assert after_expiry.json()["id"] != first_id
    assert await idempotency_count() == 1


async def test_expiry_cleanup_is_scheduled_and_strictly_batch_bounded(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    for index in range(7):
        response = await client.post(
            "/api/finance/new",
            json=finance_payload(amount=index + 1),
            headers=owner | {"Idempotency-Key": f"cleanup-key-{index}"},
        )
        assert response.status_code == 201

    async with get_sessionmaker()() as db:
        ids = list(
            (
                await db.execute(select(IdempotencyRecord.id).order_by(IdempotencyRecord.id))
            ).scalars()
        )
        await db.execute(
            update(IdempotencyRecord)
            .where(IdempotencyRecord.id.in_(ids[:5]))
            .values(expires_at=utcnow() - timedelta(seconds=1))
        )
        await db.commit()

    for expected_removed, expected_remaining in ((2, 5), (2, 3), (1, 2), (0, 2)):
        async with get_sessionmaker()() as db:
            removed = await purge_expired_idempotency_records(db, batch_size=2)
            await db.commit()
        assert removed == expected_removed
        assert await idempotency_count() == expected_remaining

    with pytest.raises(ValueError, match="batch_size"):
        async with get_sessionmaker()() as db:
            await purge_expired_idempotency_records(db, batch_size=0)


async def test_concurrent_waiter_takes_over_after_claimant_rollback(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client)
    payload = finance_payload(amount=73)
    calls = 0

    def fail_first_money(value: object) -> Decimal:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("first claimant rolls back")
        return actual_money(value)  # type: ignore[arg-type]

    monkeypatch.setattr("app.api.finance.money", fail_first_money)
    hold_winning_claim(monkeypatch, finance_api)
    keyed = owner | {"Idempotency-Key": "rollback-handoff-concurrent"}

    app = create_app()
    async with (
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as first,
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as second,
    ):
        outcomes = await asyncio.gather(
            first.post("/api/finance/new", json=payload, headers=keyed),
            second.post("/api/finance/new", json=payload, headers=keyed),
            return_exceptions=True,
        )

    failures = [outcome for outcome in outcomes if isinstance(outcome, RuntimeError)]
    successes = [outcome for outcome in outcomes if isinstance(outcome, httpx.Response)]
    assert len(failures) == 1
    assert str(failures[0]) == "first claimant rolls back"
    assert len(successes) == 1
    assert successes[0].status_code == 201
    assert "Idempotency-Replayed" not in successes[0].headers
    assert calls == 2

    async with get_sessionmaker()() as db:
        ledger_count = (await db.execute(select(func.count(Transaction.id)))).scalar_one()
    assert ledger_count == 1
    assert await idempotency_count() == 1


async def test_recurring_task_create_retries_persist_one_task_per_submission(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await owner_with_farm(client)
    payload: dict[str, object] = {
        "title": "Retry-safe daily water check",
        "due_date": today().isoformat(),
        "category": "OTHER",
        "recur_days": 1,
    }
    keyed = owner | {"Idempotency-Key": "task-recurring-sequential"}
    first = await client.post("/api/tasks", json=payload, headers=keyed)
    replay = await client.post("/api/tasks", json=payload, headers=keyed)
    assert first.status_code == replay.status_code == 201
    assert replay.json() == first.json()
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert first.json()["recurring_series_id"]

    hold_winning_claim(monkeypatch, tasks_api)
    concurrent_headers = owner | {"Idempotency-Key": "task-recurring-concurrent"}
    one, two = await concurrent_posts("/api/tasks", payload, concurrent_headers)
    assert one.status_code == two.status_code == 201
    assert one.json() == two.json()
    assert one.json()["recurring_series_id"]

    async with get_sessionmaker()() as db:
        rows = list(
            (
                await db.execute(
                    select(Task).where(
                        Task.farm_id == int(owner["X-Farm-Id"]),
                        Task.title == payload["title"],
                    )
                )
            ).scalars()
        )
    assert len(rows) == 2
    assert len({row.recurring_series_id for row in rows}) == 2


async def test_worker_create_replays_and_deduplicates_without_persisting_password(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await owner_with_farm(client)

    async def immediate_hash(password: str, *, actor_id: int) -> str:
        return f"prepared:{password}"

    # Keep this focused on the idempotency claim race. Actor-level password
    # admission is covered independently in test_password_capacity.py.
    monkeypatch.setattr(team_api, "_hash_team_password", immediate_hash)
    team = await client.get("/api/team", headers=owner)
    assert team.status_code == 200, team.text
    cleaner = next(role["id"] for role in team.json()["roles"] if role["code"] == "CLEANER")
    payload: dict[str, object] = {
        "email": "idempotent-worker@farm.in",
        "name": "Idempotent Worker",
        "password": WORKER_PASSWORD,
        "role_id": cleaner,
    }
    keyed = owner | {"Idempotency-Key": "worker-create-sequential"}
    first = await client.post("/api/team/workers", json=payload, headers=keyed)
    replay = await client.post("/api/team/workers", json=payload, headers=keyed)
    assert first.status_code == replay.status_code == 201
    assert replay.json() == first.json()
    assert replay.headers["Idempotency-Replayed"] == "true"

    conflict = await client.post(
        "/api/team/workers",
        json=payload | {"password": "differentpass123"},
        headers=keyed,
    )
    assert conflict.status_code == 409, conflict.text

    hold_winning_claim(monkeypatch, team_api)
    concurrent_payload = payload | {"email": "idempotent-concurrent-worker@farm.in"}
    concurrent_headers = owner | {"Idempotency-Key": "worker-create-concurrent"}
    one, two = await concurrent_posts(
        "/api/team/workers",
        concurrent_payload,
        concurrent_headers,
    )
    assert one.status_code == two.status_code == 201
    assert one.json() == two.json()

    async with get_sessionmaker()() as db:
        users = (
            await db.execute(
                select(func.count(User.id)).where(
                    User.email.in_(
                        [
                            payload["email"],
                            concurrent_payload["email"],
                        ]
                    )
                )
            )
        ).scalar_one()
        records = list(
            (
                await db.execute(
                    select(IdempotencyRecord).where(
                        IdempotencyRecord.operation == "team.workers.create"
                    )
                )
            ).scalars()
        )
    assert users == 2
    assert len(records) == 2
    assert all(WORKER_PASSWORD not in str(record.response_body) for record in records)
    sequential_record = next(
        record
        for record in records
        if record.response_body and record.response_body["email"] == payload["email"]
    )
    canonical = json.dumps(
        {
            "version": 1,
            "operation": "team.workers.create",
            "path": {},
            "body": WorkerCreateIn.model_validate(payload).model_dump(
                mode="json", exclude_none=False
            ),
        },
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    candidate_sha = hashlib.sha256(canonical).hexdigest()
    assert sequential_record.request_hash != candidate_sha


async def test_worker_create_replay_accepts_previous_hmac_key_during_rotation(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await owner_with_farm(client)

    async def immediate_hash(password: str, *, actor_id: int) -> str:
        return f"prepared:{password}"

    monkeypatch.setattr(team_api, "_hash_team_password", immediate_hash)
    team = await client.get("/api/team", headers=owner)
    cleaner = next(role["id"] for role in team.json()["roles"] if role["code"] == "CLEANER")
    payload = {
        "email": "rotated-idempotency-worker@farm.in",
        "name": "Rotated Idempotency Worker",
        "password": WORKER_PASSWORD,
        "role_id": cleaner,
    }
    settings = get_settings()
    old_secret = "old-sensitive-idempotency-secret-0000000001"
    new_secret = "new-sensitive-idempotency-secret-0000000002"
    monkeypatch.setattr(settings, "idempotency_request_hmac_secret", SecretStr(old_secret))
    monkeypatch.setattr(settings, "idempotency_request_hmac_previous_secrets", [])

    keyed = owner | {"Idempotency-Key": "worker-create-key-rotation"}
    first = await client.post("/api/team/workers", json=payload, headers=keyed)
    assert first.status_code == 201, first.text

    monkeypatch.setattr(settings, "idempotency_request_hmac_secret", SecretStr(new_secret))
    monkeypatch.setattr(
        settings,
        "idempotency_request_hmac_previous_secrets",
        [SecretStr(old_secret)],
    )
    replay = await client.post("/api/team/workers", json=payload, headers=keyed)
    assert replay.status_code == 201, replay.text
    assert replay.json() == first.json()
    assert replay.headers["Idempotency-Replayed"] == "true"

    conflict = await client.post(
        "/api/team/workers",
        json=payload | {"password": "changed-after-rotation123"},
        headers=keyed,
    )
    assert conflict.status_code == 409, conflict.text


async def test_pending_manual_task_limit_is_concurrency_safe_and_replay_safe(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await owner_with_farm(client)
    monkeypatch.setattr(get_settings(), "max_pending_manual_tasks_per_farm", 1)
    payloads = [
        {
            "title": f"Capacity contender {index}",
            "due_date": today().isoformat(),
            "category": "OTHER",
        }
        for index in (1, 2)
    ]
    headers = [owner | {"Idempotency-Key": f"manual-capacity-{index}"} for index in (1, 2)]
    app = create_app()
    async with (
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as first_client,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as second_client,
    ):
        responses = await asyncio.gather(
            first_client.post("/api/tasks", json=payloads[0], headers=headers[0]),
            second_client.post("/api/tasks", json=payloads[1], headers=headers[1]),
        )
    assert sorted(response.status_code for response in responses) == [201, 409]
    rejected = next(response for response in responses if response.status_code == 409)
    assert "pending manual-duty limit" in rejected.json()["detail"]
    winner_index = next(
        index for index, response in enumerate(responses) if response.status_code == 201
    )
    winner = responses[winner_index]

    # An ambiguous-response replay bypasses mutation/capacity and returns the
    # already-committed success even while the queue is exactly at its cap.
    replay = await client.post(
        "/api/tasks",
        json=payloads[winner_index],
        headers=headers[winner_index],
    )
    assert replay.status_code == 201, replay.text
    assert replay.json() == winner.json()
    assert replay.headers["Idempotency-Replayed"] == "true"

    skipped = await client.post(f"/api/tasks/{winner.json()['id']}/skip", headers=owner)
    assert skipped.status_code == 200, skipped.text
    replacement = await client.post(
        "/api/tasks",
        json={
            "title": "Capacity freed after skip",
            "due_date": today().isoformat(),
            "category": "OTHER",
        },
        headers=owner | {"Idempotency-Key": "manual-capacity-after-skip"},
    )
    assert replacement.status_code == 201, replacement.text
    async with get_sessionmaker()() as db:
        pending = (
            await db.execute(
                select(func.count(Task.id)).where(
                    Task.farm_id == int(owner["X-Farm-Id"]),
                    Task.auto_generated.is_(False),
                    Task.status == "PENDING",
                )
            )
        ).scalar_one()
    assert pending == 1


async def test_reject_cannot_exceed_pending_manual_task_limit(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await owner_with_farm(client)
    monkeypatch.setattr(get_settings(), "max_pending_manual_tasks_per_farm", 1)
    created = await client.post(
        "/api/tasks",
        json={
            "title": "Capacity-safe recurring clean",
            "due_date": today().isoformat(),
            "category": "CLEANING",
            "recur_days": 1,
        },
        headers=owner,
    )
    assert created.status_code == 201, created.text
    completed = await client.post(f"/api/tasks/{created.json()['id']}/complete", headers=owner)
    assert completed.status_code == 200, completed.text

    # Completion replaced the original pending row with its successor, so the
    # queue is still exactly at capacity. Reopening the reviewed occurrence
    # would be the second PENDING manual row and must fail without mutation.
    rejected = await client.post(
        f"/api/tasks/{created.json()['id']}/reject",
        json={"note": "Redo it"},
        headers=owner,
    )
    assert rejected.status_code == 409, rejected.text
    assert "pending manual-duty limit" in rejected.json()["detail"]

    async with get_sessionmaker()() as db:
        rows = list(
            (
                await db.execute(
                    select(Task)
                    .where(Task.recurring_series_id == created.json()["recurring_series_id"])
                    .order_by(Task.due_date, Task.id)
                )
            ).scalars()
        )
    assert [row.status for row in rows] == [TaskStatus.DONE.value, TaskStatus.PENDING.value]


async def test_concurrent_rejects_share_one_pending_manual_slot(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await owner_with_farm(client)
    task_ids: list[int] = []
    for index in (1, 2):
        created = await client.post(
            "/api/tasks",
            json={
                "title": f"Review contender {index}",
                "due_date": today().isoformat(),
                "category": "CLEANING",
            },
            headers=owner,
        )
        assert created.status_code == 201, created.text
        task_ids.append(created.json()["id"])
        assert (
            await client.post(f"/api/tasks/{created.json()['id']}/complete", headers=owner)
        ).status_code == 200

    monkeypatch.setattr(get_settings(), "max_pending_manual_tasks_per_farm", 1)
    app = create_app()
    async with (
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as one,
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as two,
    ):
        results = await asyncio.gather(
            one.post(f"/api/tasks/{task_ids[0]}/reject", json={"note": "redo"}, headers=owner),
            two.post(f"/api/tasks/{task_ids[1]}/reject", json={"note": "redo"}, headers=owner),
        )
    assert sorted(response.status_code for response in results) == [200, 409]

    async with get_sessionmaker()() as db:
        pending = (
            await db.execute(
                select(func.count(Task.id)).where(
                    Task.farm_id == int(owner["X-Farm-Id"]),
                    Task.auto_generated.is_(False),
                    Task.status == TaskStatus.PENDING.value,
                )
            )
        ).scalar_one()
    assert pending == 1


async def test_concurrent_create_and_reject_share_manual_capacity_lock(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await owner_with_farm(client)
    reviewed = await client.post(
        "/api/tasks",
        json={
            "title": "Reviewed capacity contender",
            "due_date": today().isoformat(),
            "category": "CLEANING",
        },
        headers=owner,
    )
    assert reviewed.status_code == 201, reviewed.text
    assert (
        await client.post(f"/api/tasks/{reviewed.json()['id']}/complete", headers=owner)
    ).status_code == 200
    monkeypatch.setattr(get_settings(), "max_pending_manual_tasks_per_farm", 1)

    app = create_app()
    async with (
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as creator,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as verifier,
    ):
        create_result, reject_result = await asyncio.gather(
            creator.post(
                "/api/tasks",
                json={
                    "title": "New capacity contender",
                    "due_date": today().isoformat(),
                    "category": "OTHER",
                },
                headers=owner,
            ),
            verifier.post(
                f"/api/tasks/{reviewed.json()['id']}/reject",
                json={"note": "redo"},
                headers=owner,
            ),
        )
    statuses = (create_result.status_code, reject_result.status_code)
    assert statuses.count(409) == 1
    assert next(status for status in statuses if status != 409) in {200, 201}

    async with get_sessionmaker()() as db:
        pending = (
            await db.execute(
                select(func.count(Task.id)).where(
                    Task.farm_id == int(owner["X-Farm-Id"]),
                    Task.auto_generated.is_(False),
                    Task.status == TaskStatus.PENDING.value,
                )
            )
        ).scalar_one()
    assert pending == 1


def test_openapi_declares_optional_bounded_idempotency_header_on_all_routes() -> None:
    schema = create_app().openapi()
    routes = [
        ("/api/auth/farms", "post"),
        ("/api/finance/new", "post"),
        ("/api/finance/transactions/{transaction_id}/correct", "post"),
        ("/api/purchases/new", "post"),
        ("/api/feeding/dispense", "post"),
        ("/api/feeding/mix", "post"),
        ("/api/feeding/inventory/{item_id}/add", "post"),
        ("/api/health/events", "post"),
        ("/api/animals", "post"),
        ("/api/animals/{animal_id}/weight", "post"),
        ("/api/tasks", "post"),
        ("/api/team/workers", "post"),
        ("/api/simulation/scenarios", "post"),
    ]
    for path, method in routes:
        parameters = schema["paths"][path][method]["parameters"]
        header = next(
            parameter for parameter in parameters if parameter["name"] == "Idempotency-Key"
        )
        assert header["in"] == "header"
        assert header["required"] is False
        string_variant = next(
            variant for variant in header["schema"]["anyOf"] if variant.get("type") == "string"
        )
        assert string_variant["minLength"] == 1
        assert string_variant["maxLength"] == 128
