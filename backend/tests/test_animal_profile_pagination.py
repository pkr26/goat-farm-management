"""Bounded animal-profile history and least-privilege regressions.

The profile is a hot read path over lifetime tables.  These tests deliberately
attach more rows than either response cap so a regression to relationship
hydration, inaccurate totals, coupled cursors, or permission-blind counts is
visible immediately.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import event, select

from app.db import get_engine, get_sessionmaker
from app.models import (
    Animal,
    BreedingRecord,
    BucketMove,
    Farm,
    HealthEvent,
    WeightRecord,
)
from app.utils import today, utcnow

from .conftest import owner_with_farm

HISTORY_SIZE = 105
WORKER_PASSWORD = "workerpass123"


async def _create_profile_subject(headers: dict[str, str]) -> tuple[int, int, int]:
    farm_id = int(headers["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        owner_id = (await db.execute(select(Farm.owner_id).where(Farm.id == farm_id))).scalar_one()
        dam = Animal(
            farm_id=farm_id,
            tag_number="PROFILE-DAM",
            name="Profile dam",
            breed="Osmanabadi",
            sex="F",
            date_of_birth=today() - timedelta(days=900),
            source="PURCHASED",
            current_bucket="FOUNDATION",
        )
        buck = Animal(
            farm_id=farm_id,
            tag_number="PROFILE-SIRE",
            name="Profile sire",
            breed="Osmanabadi",
            sex="M",
            date_of_birth=today() - timedelta(days=900),
            source="PURCHASED",
            current_bucket="BREEDING",
        )
        db.add_all([dam, buck])
        await db.flush()
        dam_id = dam.id
        buck_id = buck.id
        await db.commit()
    return dam_id, buck_id, owner_id


async def _seed_dense_histories(
    *, farm_id: int, dam_id: int, buck_id: int, owner_id: int
) -> dict[str, list[Any]]:
    reference_date = today()
    reference_time = utcnow()
    kids = [
        Animal(
            farm_id=farm_id,
            tag_number=f"PROFILE-KID-{index:03d}",
            name=f"Kid {index}",
            breed="Osmanabadi",
            sex="F",
            date_of_birth=reference_date - timedelta(days=HISTORY_SIZE - index),
            birth_type="SINGLE",
            source="BORN",
            dam_id=dam_id,
            sire_id=buck_id,
            birth_weight=2.5,
            current_bucket="FEMALE_KIDS",
        )
        for index in range(HISTORY_SIZE)
    ]
    weights = [
        WeightRecord(
            animal_id=dam_id,
            date=reference_date - timedelta(days=index),
            weight_kg=30.0 + index / 10,
            bcs=index % 5 + 1,
            notes=f"clinical weight note {index}",
            created_by_id=owner_id,
        )
        for index in range(HISTORY_SIZE)
    ]
    moves = [
        BucketMove(
            animal_id=dam_id,
            from_bucket="FOUNDATION",
            to_bucket="FOUNDATION",
            moved_at=reference_time - timedelta(days=index),
            reason=f"move-{index:03d}",
            created_by_id=owner_id,
        )
        for index in range(HISTORY_SIZE)
    ]
    health_events = [
        HealthEvent(
            farm_id=farm_id,
            animal_id=dam_id,
            date=reference_date - timedelta(days=index),
            type="TREATMENT",
            product_name=f"product-{index:03d}",
            notes=f"clinical health note {index}",
            created_by_id=owner_id,
        )
        for index in range(HISTORY_SIZE)
    ]
    breedings = [
        BreedingRecord(
            farm_id=farm_id,
            doe_id=dam_id,
            buck_id=buck_id,
            breeding_date=reference_date - timedelta(days=index),
            method="NATURAL",
            heat_cycle_number=index % 99 + 1,
            ultrasound_date=reference_date - timedelta(days=index),
            ultrasound_result_date=reference_date - timedelta(days=index),
            ultrasound_done=True,
            pregnant=False,
            outcome="FAILED",
            created_by_id=owner_id,
        )
        for index in range(HISTORY_SIZE)
    ]

    async with get_sessionmaker()() as db:
        db.add_all([*kids, *weights, *moves, *health_events, *breedings])
        await db.flush()
        expected: dict[str, list[Any]] = {
            "kids": [kid.tag_number for kid in kids],
            "weights": [weight.id for weight in weights],
            "moves": [move.id for move in moves],
            "health_events": [health_event.id for health_event in health_events],
            "breedings": [breeding.id for breeding in breedings],
        }
        await db.commit()
    return expected


async def _captured_profile(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    animal_id: int,
    *,
    params: dict[str, int] | None = None,
) -> tuple[httpx.Response, list[str]]:
    statements: list[str] = []

    def capture_statement(
        _conn: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        statements.append(statement)

    engine = get_engine().sync_engine
    event.listen(engine, "before_cursor_execute", capture_statement)
    try:
        response = await client.get(f"/api/animals/{animal_id}", headers=headers, params=params)
    finally:
        event.remove(engine, "before_cursor_execute", capture_statement)
    return response, statements


async def _animals_only_worker(client: httpx.AsyncClient, owner: dict[str, str]) -> dict[str, str]:
    role = await client.post(
        "/api/team/roles",
        json={"name": "Profile viewer", "permissions": ["animals.view"]},
        headers=owner,
    )
    assert role.status_code == 201, role.text
    worker = await client.post(
        "/api/team/workers",
        json={
            "name": "Profile viewer",
            "email": "profile-viewer@example.com",
            "password": WORKER_PASSWORD,
            "role_id": role.json()["id"],
        },
        headers=owner,
    )
    assert worker.status_code == 201, worker.text
    login = await client.post(
        "/api/auth/login",
        json={"email": "profile-viewer@example.com", "password": WORKER_PASSWORD},
    )
    assert login.status_code == 200, login.text
    return {
        "Authorization": f"Bearer {login.json()['access_token']}",
        "X-Farm-Id": owner["X-Farm-Id"],
    }


async def test_profile_histories_have_exact_totals_independent_pages_and_constant_queries(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    dam_id, buck_id, owner_id = await _create_profile_subject(owner)

    sparse, sparse_statements = await _captured_profile(client, owner, dam_id)
    assert sparse.status_code == 200, sparse.text
    for total_field in (
        "kids_total",
        "weights_total",
        "moves_total",
        "health_events_total",
        "breedings_total",
    ):
        assert sparse.json()[total_field] == 0

    expected = await _seed_dense_histories(
        farm_id=farm_id,
        dam_id=dam_id,
        buck_id=buck_id,
        owner_id=owner_id,
    )
    dense, dense_statements = await _captured_profile(client, owner, dam_id)
    assert dense.status_code == 200, dense.text
    body = dense.json()

    assert body["history_limit"] == 25
    for collection, total_field in (
        ("kids", "kids_total"),
        ("weights", "weights_total"),
        ("moves", "moves_total"),
        ("health_events", "health_events_total"),
        ("breedings", "breedings_total"),
    ):
        assert body[total_field] == HISTORY_SIZE
        assert len(body[collection]) == 25
    assert set(body["kids"][0]) == {
        "id",
        "tag_number",
        "name",
        "sex",
        "date_of_birth",
        "estimated_dob",
        "status",
    }

    # Row count must not add SQL statements, and every lifetime collection
    # query must stay an explicitly limited page rather than a relationship
    # selectin/lazy load of all 105 records.
    assert len(dense_statements) == len(sparse_statements)
    selectin_history_loads = [
        statement
        for statement in dense_statements
        if (
            "WHERE weight_records.animal_id IN" in statement
            or "WHERE bucket_moves.animal_id IN" in statement
            or "WHERE breeding_records.doe_id IN" in statement
        )
        and "SELECT DISTINCT" not in statement
    ]
    assert selectin_history_loads == []
    unbounded_history_selects = [
        statement
        for statement in dense_statements
        if (
            statement.lstrip().startswith("SELECT weight_records.id")
            or statement.lstrip().startswith("SELECT bucket_moves.id")
            or statement.lstrip().startswith("SELECT health_events.id")
            or statement.lstrip().startswith("SELECT breeding_records.id")
            or (
                statement.lstrip().startswith("SELECT animals.id")
                and "animals.dam_id =" in statement.split("WHERE", maxsplit=1)[-1]
            )
        )
        and "LIMIT" not in statement
    ]
    assert unbounded_history_selects == []

    maximum = await client.get(
        f"/api/animals/{dam_id}",
        headers=owner,
        params={"history_limit": 100},
    )
    assert maximum.status_code == 200, maximum.text
    maximum_body = maximum.json()
    for collection in ("kids", "weights", "moves", "health_events", "breedings"):
        assert len(maximum_body[collection]) == 100

    offsets = {
        "kids_offset": 7,
        "weights_offset": 13,
        "moves_offset": 19,
        "health_events_offset": 23,
        "breedings_offset": 29,
    }
    paged = await client.get(
        f"/api/animals/{dam_id}",
        headers=owner,
        params={"history_limit": 10, **offsets},
    )
    assert paged.status_code == 200, paged.text
    page = paged.json()
    assert page["history_limit"] == 10
    assert {field: page[field] for field in offsets} == offsets
    assert [kid["tag_number"] for kid in page["kids"]] == expected["kids"][7:17]
    assert [weight["id"] for weight in page["weights"]] == expected["weights"][13:23]
    assert [move["id"] for move in page["moves"]] == expected["moves"][19:29]
    assert [event["id"] for event in page["health_events"]] == expected["health_events"][23:33]
    assert page["breedings"] == expected["breedings"][29:39]
    assert all(
        page[total] == HISTORY_SIZE
        for total in (
            "kids_total",
            "weights_total",
            "moves_total",
            "health_events_total",
            "breedings_total",
        )
    )

    over_limit = await client.get(
        f"/api/animals/{dam_id}",
        headers=owner,
        params={"history_limit": 101},
    )
    assert over_limit.status_code == 422


async def test_move_uses_latest_sql_facts_without_hydrating_lifetime_histories(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    dam_id, buck_id, owner_id = await _create_profile_subject(owner)
    await _seed_dense_histories(
        farm_id=farm_id,
        dam_id=dam_id,
        buck_id=buck_id,
        owner_id=owner_id,
    )

    statements: list[str] = []

    def capture_statement(
        _conn: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        statements.append(statement)

    engine = get_engine().sync_engine
    event.listen(engine, "before_cursor_execute", capture_statement)
    try:
        response = await client.post(
            f"/api/animals/{dam_id}/move",
            json={"to_bucket": "BREEDING", "reason": "Ready for service"},
            headers=owner,
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture_statement)

    assert response.status_code == 200, response.text
    assert response.json()["current_bucket"] == "BREEDING"
    assert response.json()["latest_weight_kg"] == 30.0
    assert not any(
        (
            "WHERE weight_records.animal_id IN" in statement
            or "WHERE bucket_moves.animal_id IN" in statement
            or "WHERE breeding_records.doe_id IN" in statement
        )
        and "SELECT DISTINCT" not in statement
        for statement in statements
    )
    assert not any(
        statement.lstrip().startswith("SELECT weight_records.id")
        or statement.lstrip().startswith("SELECT bucket_moves.id")
        for statement in statements
    )


async def test_pregnancy_moves_use_bounded_open_pregnancy_fact(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    dam_id, buck_id, owner_id = await _create_profile_subject(owner)
    await _seed_dense_histories(
        farm_id=farm_id,
        dam_id=dam_id,
        buck_id=buck_id,
        owner_id=owner_id,
    )
    async with get_sessionmaker()() as db:
        dam = await db.get(Animal, dam_id)
        assert dam is not None
        dam.current_bucket = "PREGNANCY_EARLY"
        db.add(
            BreedingRecord(
                farm_id=farm_id,
                doe_id=dam_id,
                buck_id=buck_id,
                breeding_date=today() - timedelta(days=100),
                method="NATURAL",
                heat_cycle_number=1,
                ultrasound_date=today() - timedelta(days=70),
                ultrasound_result_date=today() - timedelta(days=70),
                ultrasound_done=True,
                pregnant=True,
                kid_count_detected=1,
                expected_kidding_date=today() + timedelta(days=50),
                outcome="CONFIRMED_PREGNANT",
                created_by_id=owner_id,
            )
        )
        await db.commit()

    early_to_late = await client.post(
        f"/api/animals/{dam_id}/move",
        json={"to_bucket": "PREGNANCY_LATE", "reason": "Gestation day 100"},
        headers=owner,
    )
    assert early_to_late.status_code == 200, early_to_late.text
    assert early_to_late.json()["current_bucket"] == "PREGNANCY_LATE"
    late_to_delivery = await client.post(
        f"/api/animals/{dam_id}/move",
        json={"to_bucket": "DELIVERY", "reason": "Prepare delivery"},
        headers=owner,
    )
    assert late_to_delivery.status_code == 200, late_to_delivery.text
    assert late_to_delivery.json()["current_bucket"] == "DELIVERY"


async def test_profile_hides_health_history_count_and_narratives_without_permission(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    dam_id, buck_id, owner_id = await _create_profile_subject(owner)
    await _seed_dense_histories(
        farm_id=farm_id,
        dam_id=dam_id,
        buck_id=buck_id,
        owner_id=owner_id,
    )
    viewer = await _animals_only_worker(client, owner)

    response = await client.get(
        f"/api/animals/{dam_id}",
        headers=viewer,
        params={"history_limit": 10, "health_events_offset": 50, "breedings_offset": 50},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["health_events"] == []
    assert body["health_events_total"] == 0
    assert body["health_events_offset"] == 50
    assert body["breedings"] == []
    assert body["breedings_total"] == 0
    assert body["breedings_offset"] == 50
    assert body["weights_total"] == HISTORY_SIZE
    assert len(body["weights"]) == 10
    assert all(weight["notes"] is None for weight in body["weights"])
