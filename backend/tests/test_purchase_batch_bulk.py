"""Large purchase batches stay correct, bounded, and set-efficient."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from datetime import date
from decimal import Decimal

import httpx
from sqlalchemy import event, func, select

from app.db import get_engine, get_sessionmaker
from app.main import create_app
from app.models import (
    MAX_ANIMAL_TAG_LENGTH,
    MAX_BATCH_COUNT,
    Animal,
    BucketMove,
    PurchaseBatch,
    Task,
    Transaction,
)
from app.schemas.common import MAX_INT32_ID
from app.services.purchases import _estimated_dob_from_age, _purchase_batch_tag
from app.utils import add_months, today

from .conftest import owner_with_farm
from .test_tasks_extended import add_worker, login_user, make_custom_role


def _assert_batch_tag_sequence(tags: list[str], batch_id: int) -> None:
    matches = [re.fullmatch(rf"B{batch_id}-([0-9a-f]{{12}})-([0-9]{{4}})", tag) for tag in tags]
    assert all(match is not None for match in matches)
    concrete_matches = [match for match in matches if match is not None]
    assert len({match.group(1) for match in concrete_matches}) == 1
    assert [int(match.group(2)) for match in concrete_matches] == list(range(1, len(tags) + 1))
    assert all(len(tag) <= 50 for tag in tags)


def test_generated_tag_fits_at_batch_and_id_boundaries() -> None:
    tag = _purchase_batch_tag(MAX_INT32_ID, "f" * 12, MAX_BATCH_COUNT)
    assert tag == f"B{MAX_INT32_ID}-{'f' * 12}-1000"
    assert len(tag) <= MAX_ANIMAL_TAG_LENGTH


def test_estimated_dob_is_monotone_as_stated_age_increases() -> None:
    """A batch declared a hundredth of a month YOUNGER must never receive an
    EARLIER birth date. The old fixed 30.4375-day fraction was measured
    against a calendar-month base, so it overshot short months: for a
    2026-08-10 batch, 5.99 months landed on 2026-02-08 — two days before the
    6.0-month anchor of 2026-02-10 — inverting the age→DOB relationship at
    every February-crossing whole-month boundary."""
    batch_date = date(2026, 8, 10)
    assert _estimated_dob_from_age(batch_date, 6.0) == date(2026, 2, 10)
    assert _estimated_dob_from_age(batch_date, 5.99) >= _estimated_dob_from_age(batch_date, 6.0)

    # Whole-month behavior is unchanged: 0 months is the batch date itself
    # (age-based vaccine scheduling breaks on a missing DOB) and whole months
    # keep calendar arithmetic with month-end clamping.
    assert _estimated_dob_from_age(batch_date, 0) == batch_date
    assert _estimated_dob_from_age(date(2026, 3, 31), 1) == date(2026, 2, 28)
    assert _estimated_dob_from_age(batch_date, 7) == add_months(batch_date, -7)

    # Sweep the full plausible age range in 0.01-month steps for two batch
    # dates (a mid-month one and a clamping month-end one): a greater stated
    # age must always map to an earlier-or-equal birth date.
    for anchor in (batch_date, date(2026, 1, 31)):
        previous = _estimated_dob_from_age(anchor, 0)
        for hundredths in range(1, 6001):
            current = _estimated_dob_from_age(anchor, hundredths / 100)
            assert current <= previous, (anchor, hundredths)
            previous = current


async def _capture_statements(
    mutation: Callable[[], Awaitable[httpx.Response]],
) -> tuple[httpx.Response, list[str]]:
    statements: list[str] = []

    def capture(
        _conn: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        statements.append(statement)

    engine = get_engine().sync_engine
    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = await mutation()
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    return response, statements


async def test_thousand_head_purchase_is_constant_statement_and_exact(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)

    async def create(count: int, key: str) -> tuple[httpx.Response, list[str]]:
        return await _capture_statements(
            lambda: client.post(
                "/api/purchases/new",
                json={
                    "date": today().isoformat(),
                    "supplier": "Bulk performance supplier",
                    "count": count,
                    "avg_age_months": 7,
                    "total_price": 1000.03,
                    "create_animals": True,
                },
                headers=owner | {"Idempotency-Key": key},
            )
        )

    small, small_statements = await create(1, "bulk-statement-small")
    assert small.status_code == 201, small.text
    maximum, maximum_statements = await create(1000, "bulk-statement-maximum")
    assert maximum.status_code == 201, maximum.text
    batch_id = maximum.json()["id"]

    # The request may add at most a tiny fixed number of insert pages; it must
    # never regress to one SQL round trip per animal or movement.
    assert len(maximum_statements) <= len(small_statements) + 3
    animal_inserts = [
        statement
        for statement in maximum_statements
        if statement.lstrip().startswith("INSERT INTO animals")
    ]
    move_inserts = [
        statement
        for statement in maximum_statements
        if statement.lstrip().startswith("INSERT INTO bucket_moves")
    ]
    assert 1 <= len(animal_inserts) <= 2
    assert 1 <= len(move_inserts) <= 2

    async with get_sessionmaker()() as db:
        animal_count, unique_tags, total_allocated = (
            await db.execute(
                select(
                    func.count(Animal.id),
                    func.count(func.distinct(Animal.tag_number)),
                    func.sum(Animal.purchase_price),
                ).where(Animal.purchase_batch_id == batch_id)
            )
        ).one()
        tags = list(
            (
                await db.execute(
                    select(Animal.tag_number)
                    .where(Animal.purchase_batch_id == batch_id)
                    .order_by(Animal.id)
                )
            ).scalars()
        )
        move_count = (
            await db.execute(
                select(func.count(BucketMove.id))
                .join(Animal, BucketMove.animal_id == Animal.id)
                .where(Animal.purchase_batch_id == batch_id)
            )
        ).scalar_one()
        task_count = (
            await db.execute(select(func.count(Task.id)).where(Task.purchase_batch_id == batch_id))
        ).scalar_one()
        booked_total = (
            await db.execute(
                select(Transaction.amount).where(
                    Transaction.source_type == "PURCHASE_BATCH",
                    Transaction.source_id == batch_id,
                )
            )
        ).scalar_one()

    assert animal_count == unique_tags == move_count == 1000
    _assert_batch_tag_sequence(tags, batch_id)
    assert total_allocated == booked_total == Decimal("1000.03")
    assert task_count == maximum.json()["open_tasks"] == 8

    over_limit = await client.post(
        "/api/purchases/new",
        json={"date": today().isoformat(), "count": 1001},
        headers=owner,
    )
    assert over_limit.status_code == 422, over_limit.text


async def test_thousand_head_batch_detail_is_a_bounded_page(client: httpx.AsyncClient) -> None:
    """A batch may legitimately hold 1,000 animals; its detail response must be
    a bounded page like every other list, not a ~900 KB document of full animal
    records that the field tablet re-fetches on every refresh."""
    owner = await owner_with_farm(client)
    created = await client.post(
        "/api/purchases/new",
        json={
            "date": today().isoformat(),
            "supplier": "Bulk detail supplier",
            "count": MAX_BATCH_COUNT,
            "create_animals": True,
        },
        headers=owner,
    )
    assert created.status_code == 201, created.text
    batch_id = created.json()["id"]

    first = await client.get(f"/api/purchases/{batch_id}", headers=owner)
    assert first.status_code == 200, first.text
    body = first.json()
    assert len(body["animals"]) == 100  # default page, not all 1,000
    assert body["batch"]["animals_created"] == MAX_BATCH_COUNT  # exact total stays available
    assert len(body["tasks"]) == 8  # the protocol itself is bounded

    page = await client.get(
        f"/api/purchases/{batch_id}",
        params={"animals_limit": 50, "animals_offset": 100},
        headers=owner,
    )
    assert page.status_code == 200, page.text
    tags = [animal["tag_number"] for animal in body["animals"]]
    next_tags = [animal["tag_number"] for animal in page.json()["animals"]]
    assert len(next_tags) == 50
    assert not set(tags) & set(next_tags)  # deterministic, non-overlapping pages
    assert tags + next_tags == sorted(tags + next_tags)

    for params in ({"animals_limit": 0}, {"animals_limit": 201}, {"animals_offset": -1}):
        rejected = await client.get(f"/api/purchases/{batch_id}", params=params, headers=owner)
        assert rejected.status_code == 422, (params, rejected.text)


async def test_concurrent_bulk_batches_keep_generated_tags_disjoint(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    app = create_app()
    payload = {
        "date": today().isoformat(),
        "supplier": "Concurrent bulk supplier",
        "count": 100,
        "total_price": 1234.56,
        "create_animals": True,
    }
    transport_a = httpx.ASGITransport(app=app)
    transport_b = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport_a, base_url="http://test") as client_a,
        httpx.AsyncClient(transport=transport_b, base_url="http://test") as client_b,
    ):
        first, second = await asyncio.gather(
            client_a.post(
                "/api/purchases/new",
                json=payload,
                headers=owner | {"Idempotency-Key": "bulk-concurrent-a"},
            ),
            client_b.post(
                "/api/purchases/new",
                json=payload,
                headers=owner | {"Idempotency-Key": "bulk-concurrent-b"},
            ),
        )
    assert first.status_code == second.status_code == 201, (first.text, second.text)
    batch_ids = {first.json()["id"], second.json()["id"]}
    assert len(batch_ids) == 2

    async with get_sessionmaker()() as db:
        animal_count, unique_tags = (
            await db.execute(
                select(
                    func.count(Animal.id),
                    func.count(func.distinct(Animal.tag_number)),
                ).where(Animal.purchase_batch_id.in_(batch_ids))
            )
        ).one()
        per_batch = dict(
            (
                await db.execute(
                    select(Animal.purchase_batch_id, func.count(Animal.id))
                    .where(Animal.purchase_batch_id.in_(batch_ids))
                    .group_by(Animal.purchase_batch_id)
                )
            ).all()
        )
    assert animal_count == unique_tags == 200
    assert per_batch == {batch_id: 100 for batch_id in batch_ids}


async def test_animal_creator_cannot_squat_predictable_next_batch_tags(
    client: httpx.AsyncClient,
) -> None:
    """An animal creator knows the next batch id but not its secure nonce."""
    owner = await owner_with_farm(client)
    role_id = await make_custom_role(
        client,
        owner,
        "Animal creator only",
        ["animals.view", "animals.create"],
    )
    await add_worker(client, owner, role_id, "batch-tag-squatter@example.com")
    worker, _worker_id = await login_user(client, "batch-tag-squatter@example.com")
    worker["X-Farm-Id"] = owner["X-Farm-Id"]

    # RT-C-1: the managed-purchase branch now requires purchases.manage, so
    # the animal creator cannot even reach the batch/tag machinery — the
    # squatting vector is closed at the permission boundary.
    response = await client.post(
        "/api/animals",
        json={
            "tag_number": "B2-001",
            "sex": "F",
            "source": "PURCHASED",
            "current_bucket": "QUARANTINE",
        },
        headers=worker,
    )
    assert response.status_code == 403, response.text
    denied = await client.post(
        "/api/purchases/new",
        json={"date": today().isoformat(), "count": 1},
        headers=worker,
    )
    assert denied.status_code == 403, denied.text

    # The nonce defense itself is unchanged: an owner-driven explicit-tag
    # squat of the formerly predictable "B2-001" still cannot collide with
    # batch 2's secure tags.
    squatted = await client.post(
        "/api/animals",
        json={
            "tag_number": "B2-001",
            "sex": "F",
            "source": "PURCHASED",
            "current_bucket": "QUARANTINE",
        },
        headers=owner | {"Idempotency-Key": "squat-b2-001"},
    )
    assert squatted.status_code == 201, squatted.text

    response = await client.post(
        "/api/purchases/new",
        json={"date": today().isoformat(), "count": 2},
        headers=owner,
    )
    assert response.status_code == 201, response.text
    assert response.json()["id"] == 2
    detail = await client.get("/api/purchases/2", headers=owner)
    assert detail.status_code == 200, detail.text
    tags = [animal["tag_number"] for animal in detail.json()["animals"]]
    _assert_batch_tag_sequence(tags, 2)
    assert "B2-001" not in tags


async def test_health_batch_picker_cost_is_independent_of_closed_batch_history(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    target = await client.post(
        "/api/purchases/new",
        json={"date": today().isoformat(), "count": 2},
        headers=owner,
    )
    assert target.status_code == 201, target.text

    async def lookup() -> httpx.Response:
        return await client.get("/api/health/purchase-batches", headers=owner)

    baseline, baseline_statements = await _capture_statements(lookup)
    assert baseline.status_code == 200, baseline.text
    assert baseline.json()["total"] == 1

    # Closed/history-only batches have no active quarantine animals and must
    # not become the driving relation for a routine clinical target lookup.
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        db.add_all(
            [
                PurchaseBatch(
                    farm_id=farm_id,
                    date=today(),
                    supplier=f"Closed supplier {index}",
                    count=1,
                    sex="F",
                    avg_age_months=None,
                    avg_weight_kg=None,
                    total_price=None,
                    notes=None,
                )
                for index in range(1_000)
            ]
        )
        await db.commit()

    expanded, expanded_statements = await _capture_statements(lookup)
    assert expanded.status_code == 200, expanded.text
    assert expanded.json() == baseline.json()
    assert len(expanded_statements) == len(baseline_statements)
    picker_statements = [
        sql for sql in expanded_statements if "GROUP BY animals.purchase_batch_id" in sql
    ]
    assert len(picker_statements) == 2  # one grouped total + one bounded page
    assert all("JOIN purchase_batches" in sql for sql in picker_statements)
    assert all(sql.count("FROM animals JOIN purchase_batches") == 1 for sql in picker_statements)
