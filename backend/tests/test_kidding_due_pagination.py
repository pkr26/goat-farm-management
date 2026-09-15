"""High-volume regressions for bounded kidding due/overdue pages.

The due board must never hydrate a farm's complete pregnancy history.  These
tests deliberately combine large eligible populations with large excluded
populations so caps, exact counts, filters, and independent cursors are all
exercised together.
"""

from __future__ import annotations

from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy import event, select

from app.db import get_engine, get_sessionmaker
from app.models import Animal, BreedingRecord, Farm, KiddingRecord
from app.schemas.common import MAX_PAGE_OFFSET
from app.utils import today

from .conftest import login_and_rotate, owner_with_farm

UPCOMING_TOTAL = 135
OVERDUE_TOTAL = 137
EXCLUDED_PER_KIND = 40


def _confirmed_pregnancy(
    *,
    farm_id: int,
    doe_id: int,
    buck_id: int,
    owner_id: int,
    expected_date: date,
    cycle: int,
) -> BreedingRecord:
    breeding_date = expected_date - timedelta(days=150)
    ultrasound_date = breeding_date + timedelta(days=32)
    return BreedingRecord(
        farm_id=farm_id,
        doe_id=doe_id,
        buck_id=buck_id,
        breeding_date=breeding_date,
        method="NATURAL",
        heat_cycle_number=cycle % 99 + 1,
        ultrasound_date=ultrasound_date,
        ultrasound_result_date=ultrasound_date,
        ultrasound_done=True,
        pregnant=True,
        kid_count_detected=2,
        expected_kidding_date=expected_date,
        outcome="CONFIRMED_PREGNANT",
        created_by_id=owner_id,
    )


async def _create_actors(headers: dict[str, str]) -> tuple[int, int, int, int]:
    farm_id = int(headers["X-Farm-Id"])
    reference_date = today()
    async with get_sessionmaker()() as db:
        owner_id = (await db.execute(select(Farm.owner_id).where(Farm.id == farm_id))).scalar_one()
        active_doe = Animal(
            farm_id=farm_id,
            tag_number="DUE-ACTIVE-DOE",
            sex="F",
            date_of_birth=reference_date - timedelta(days=900),
            source="PURCHASED",
            current_bucket="PREGNANCY_LATE",
        )
        inactive_doe = Animal(
            farm_id=farm_id,
            tag_number="DUE-INACTIVE-DOE",
            sex="F",
            date_of_birth=reference_date - timedelta(days=900),
            source="PURCHASED",
            current_bucket="PREGNANCY_LATE",
            status="SOLD",
            status_date=reference_date,
        )
        buck = Animal(
            farm_id=farm_id,
            tag_number="DUE-BUCK",
            sex="M",
            date_of_birth=reference_date - timedelta(days=900),
            source="PURCHASED",
            current_bucket="BREEDING",
        )
        db.add_all([active_doe, inactive_doe, buck])
        await db.flush()
        actor_ids = (active_doe.id, inactive_doe.id, buck.id, owner_id)
        await db.commit()
    return actor_ids


async def _seed_baseline(
    *,
    farm_id: int,
    active_doe_id: int,
    buck_id: int,
    owner_id: int,
) -> tuple[list[tuple[date, int]], list[tuple[date, int]]]:
    """One row in every loader branch makes query-count comparison fair."""
    reference_date = today()
    upcoming = _confirmed_pregnancy(
        farm_id=farm_id,
        doe_id=active_doe_id,
        buck_id=buck_id,
        owner_id=owner_id,
        expected_date=reference_date + timedelta(days=10),
        cycle=0,
    )
    overdue = _confirmed_pregnancy(
        farm_id=farm_id,
        doe_id=active_doe_id,
        buck_id=buck_id,
        owner_id=owner_id,
        expected_date=reference_date - timedelta(days=10),
        cycle=1,
    )
    already_kidded = _confirmed_pregnancy(
        farm_id=farm_id,
        doe_id=active_doe_id,
        buck_id=buck_id,
        owner_id=owner_id,
        expected_date=reference_date + timedelta(days=5),
        cycle=2,
    )
    async with get_sessionmaker()() as db:
        db.add_all([upcoming, overdue, already_kidded])
        await db.flush()
        db.add(
            KiddingRecord(
                farm_id=farm_id,
                doe_id=active_doe_id,
                date=reference_date,
                breeding_record_id=already_kidded.id,
                ease="NORMAL",
                created_by_id=owner_id,
            )
        )
        await db.flush()
        expected_upcoming = [(upcoming.expected_kidding_date, upcoming.id)]
        expected_overdue = [(overdue.expected_kidding_date, overdue.id)]
        await db.commit()
    return expected_upcoming, expected_overdue


async def _seed_dense_population(
    *,
    farm_id: int,
    active_doe_id: int,
    inactive_doe_id: int,
    buck_id: int,
    owner_id: int,
) -> tuple[list[tuple[date, int]], list[tuple[date, int]]]:
    reference_date = today()
    upcoming = [
        _confirmed_pregnancy(
            farm_id=farm_id,
            doe_id=active_doe_id,
            buck_id=buck_id,
            owner_id=owner_id,
            expected_date=reference_date + timedelta(days=index % 31),
            cycle=index,
        )
        for index in range(UPCOMING_TOTAL - 1)
    ]
    overdue = [
        _confirmed_pregnancy(
            farm_id=farm_id,
            doe_id=active_doe_id,
            buck_id=buck_id,
            owner_id=owner_id,
            expected_date=reference_date - timedelta(days=index % 45 + 1),
            cycle=index,
        )
        for index in range(OVERDUE_TOTAL - 1)
    ]
    out_of_window = [
        _confirmed_pregnancy(
            farm_id=farm_id,
            doe_id=active_doe_id,
            buck_id=buck_id,
            owner_id=owner_id,
            expected_date=reference_date + timedelta(days=31 + index),
            cycle=index,
        )
        for index in range(EXCLUDED_PER_KIND)
    ]
    kidded = [
        _confirmed_pregnancy(
            farm_id=farm_id,
            doe_id=active_doe_id,
            buck_id=buck_id,
            owner_id=owner_id,
            expected_date=(
                reference_date + timedelta(days=index % 31)
                if index % 2 == 0
                else reference_date - timedelta(days=index % 45 + 1)
            ),
            cycle=index,
        )
        for index in range(EXCLUDED_PER_KIND)
    ]
    inactive = [
        _confirmed_pregnancy(
            farm_id=farm_id,
            doe_id=inactive_doe_id,
            buck_id=buck_id,
            owner_id=owner_id,
            expected_date=(
                reference_date + timedelta(days=index % 31)
                if index % 2 == 0
                else reference_date - timedelta(days=index % 45 + 1)
            ),
            cycle=index,
        )
        for index in range(EXCLUDED_PER_KIND)
    ]

    async with get_sessionmaker()() as db:
        db.add_all([*upcoming, *overdue, *out_of_window, *kidded, *inactive])
        await db.flush()
        db.add_all(
            [
                KiddingRecord(
                    farm_id=farm_id,
                    doe_id=active_doe_id,
                    date=reference_date,
                    breeding_record_id=breeding.id,
                    ease="NORMAL",
                    created_by_id=owner_id,
                )
                for breeding in kidded
            ]
        )
        await db.flush()
        expected_upcoming = [(row.expected_kidding_date, row.id) for row in upcoming]
        expected_overdue = [(row.expected_kidding_date, row.id) for row in overdue]
        await db.commit()
    return expected_upcoming, expected_overdue


async def _captured_list(
    client: httpx.AsyncClient,
    headers: dict[str, str],
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
        response = await client.get("/api/kidding", headers=headers, params=params)
    finally:
        event.remove(engine, "before_cursor_execute", capture_statement)
    return response, statements


async def test_due_pages_are_bounded_exact_independent_stable_and_constant_query_count(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    active_doe_id, inactive_doe_id, buck_id, owner_id = await _create_actors(owner)
    expected_upcoming, expected_overdue = await _seed_baseline(
        farm_id=farm_id,
        active_doe_id=active_doe_id,
        buck_id=buck_id,
        owner_id=owner_id,
    )

    baseline, baseline_statements = await _captured_list(client, owner)
    assert baseline.status_code == 200, baseline.text
    assert baseline.json()["upcoming_total"] == 1
    assert baseline.json()["overdue_total"] == 1

    dense_upcoming, dense_overdue = await _seed_dense_population(
        farm_id=farm_id,
        active_doe_id=active_doe_id,
        inactive_doe_id=inactive_doe_id,
        buck_id=buck_id,
        owner_id=owner_id,
    )
    expected_upcoming = sorted([*expected_upcoming, *dense_upcoming])
    expected_overdue = sorted([*expected_overdue, *dense_overdue])

    default_page, dense_statements = await _captured_list(client, owner)
    assert default_page.status_code == 200, default_page.text
    body = default_page.json()
    assert (body["upcoming_total"], body["upcoming_limit"], body["upcoming_offset"]) == (
        UPCOMING_TOTAL,
        30,
        0,
    )
    assert (body["overdue_total"], body["overdue_limit"], body["overdue_offset"]) == (
        OVERDUE_TOTAL,
        30,
        0,
    )
    assert [row["id"] for row in body["upcoming"]] == [
        row_id for _due_date, row_id in expected_upcoming[:30]
    ]
    assert [row["id"] for row in body["overdue"]] == [
        row_id for _due_date, row_id in expected_overdue[:30]
    ]

    # Adding hundreds of eligible and excluded rows must not add statements.
    # Both due-parent SELECTs must carry SQL LIMIT clauses; count subqueries
    # are intentionally unbounded because they return only one aggregate row.
    assert len(dense_statements) == len(baseline_statements)
    due_parent_selects = [
        statement
        for statement in dense_statements
        if statement.lstrip().startswith("SELECT breeding_records.id")
        and "JOIN animals ON breeding_records.doe_id = animals.id" in statement
        and "ORDER BY breeding_records.expected_kidding_date, breeding_records.id" in statement
    ]
    assert len(due_parent_selects) == 2
    assert all("LIMIT" in statement for statement in due_parent_selects)

    independent = await client.get(
        "/api/kidding",
        headers=owner,
        params={
            "upcoming_limit": 100,
            "upcoming_offset": 7,
            "overdue_limit": 100,
            "overdue_offset": 11,
        },
    )
    assert independent.status_code == 200, independent.text
    page = independent.json()
    assert (page["upcoming_total"], page["upcoming_limit"], page["upcoming_offset"]) == (
        UPCOMING_TOTAL,
        100,
        7,
    )
    assert (page["overdue_total"], page["overdue_limit"], page["overdue_offset"]) == (
        OVERDUE_TOTAL,
        100,
        11,
    )
    assert [row["id"] for row in page["upcoming"]] == [
        row_id for _due_date, row_id in expected_upcoming[7:107]
    ]
    assert [row["id"] for row in page["overdue"]] == [
        row_id for _due_date, row_id in expected_overdue[11:111]
    ]

    # One cursor can move beyond its result set without disturbing the other.
    one_exhausted = await client.get(
        "/api/kidding",
        headers=owner,
        params={"upcoming_offset": UPCOMING_TOTAL, "overdue_limit": 1, "overdue_offset": 3},
    )
    assert one_exhausted.status_code == 200, one_exhausted.text
    exhausted_body = one_exhausted.json()
    assert exhausted_body["upcoming"] == []
    assert exhausted_body["upcoming_total"] == UPCOMING_TOTAL
    assert [row["id"] for row in exhausted_body["overdue"]] == [expected_overdue[3][1]]


@pytest.mark.parametrize(
    ("parameter", "value"),
    [
        ("upcoming_limit", 0),
        ("upcoming_limit", 101),
        ("overdue_limit", 0),
        ("overdue_limit", 101),
        ("upcoming_offset", -1),
        ("upcoming_offset", MAX_PAGE_OFFSET + 1),
        ("overdue_offset", -1),
        ("overdue_offset", MAX_PAGE_OFFSET + 1),
    ],
)
async def test_due_page_bounds_are_validated(
    client: httpx.AsyncClient,
    parameter: str,
    value: int,
) -> None:
    owner = await owner_with_farm(client)
    response = await client.get("/api/kidding", headers=owner, params={parameter: value})
    assert response.status_code == 422, response.text


async def test_kidding_scoped_pregnancy_lookup_resolves_deep_link_outside_pages(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    active_doe_id, _inactive_doe_id, buck_id, owner_id = await _create_actors(owner)
    pregnancy = _confirmed_pregnancy(
        farm_id=farm_id,
        doe_id=active_doe_id,
        buck_id=buck_id,
        owner_id=owner_id,
        expected_date=today() + timedelta(days=60),
        cycle=1,
    )
    async with get_sessionmaker()() as db:
        db.add(pregnancy)
        await db.flush()
        pregnancy_id = pregnancy.id
        await db.commit()

    role = await client.post(
        "/api/team/roles",
        json={
            "name": "Kidding recorder",
            "permissions": ["kidding.view", "kidding.manage"],
        },
        headers=owner,
    )
    assert role.status_code == 201, role.text
    worker = await client.post(
        "/api/team/workers",
        json={
            "name": "Kidding recorder",
            "email": "kidding-deep-link@example.com",
            "password": "workerpass123",
            "role_id": role.json()["id"],
        },
        headers=owner,
    )
    assert worker.status_code == 201, worker.text
    manage_only = await login_and_rotate(client, "kidding-deep-link@example.com", "workerpass123")
    manage_only = manage_only | {"X-Farm-Id": str(farm_id)}

    # Kidding access does not imply breeding-register access, yet the exact
    # live pregnancy referenced by a KIDDING_DUE task remains resolvable.
    assert (await client.get("/api/breeding", headers=manage_only)).status_code == 403
    deep_link = await client.get(
        f"/api/kidding/pregnancies/{pregnancy_id}",
        headers=manage_only,
    )
    assert deep_link.status_code == 200, deep_link.text
    assert deep_link.json()["id"] == pregnancy_id
    assert deep_link.json()["doe_id"] == active_doe_id
