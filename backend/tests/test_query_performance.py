"""Query-performance regression tests.

- 5-H2: GET /api/dashboard/reports aggregates in SQL — a golden test compares
  every number against the old Python-side algorithm computed straight from
  ORM rows.
- 5-M5: GET /api/breeding history is capped; candidate identities are fetched
  with SQL-side filtering/count/pagination and constant query count.
- 5-M3: the tasks completed tab applies its predicate in SQL BEFORE the
  100-row cap (an older eligible row must survive a flood of newer
  unverified CLEANING rows).
- Feeding plan reads only each active animal's latest bucket move and returns
  a bounded latest window even when movement/dispensing history grows.
- 5-M4 / 5-L3: the new hot-path indexes exist after `alembic upgrade head`.
"""

from datetime import date, timedelta

import httpx
from sqlalchemy import event, select, text
from sqlalchemy.orm import selectinload

from app.db import get_engine, get_sessionmaker
from app.models import (
    Animal,
    AnimalStatus,
    BreedingOutcome,
    BreedingRecord,
    Bucket,
    BucketDefinition,
    BucketMove,
    FeedingRecord,
    FeedingShift,
    HealthEvent,
    KiddingRecord,
    KidEntry,
    KidStatus,
    Task,
    TaskCategory,
    TaskStatus,
    WeightRecord,
    conception_rate,
)
from app.utils import today, utcnow

from .conftest import owner_with_farm


def iso(d: date) -> str:
    return d.isoformat()


async def make_animal(
    client: httpx.AsyncClient,
    headers: dict,
    tag: str,
    sex: str = "F",
    bucket: str = "FOUNDATION",
    **overrides: object,
) -> dict:
    payload = {
        "tag_number": tag,
        "sex": sex,
        "source": "PURCHASED",
        "current_bucket": bucket,
    } | overrides
    # Every direct create in this query-only helper represents a deliberate
    # existing-herd import. That provenance is required for BORN rows too;
    # live births belong to the kidding workflow.
    payload.setdefault("historical_import_reason", "Query fixture: audited existing-herd import")
    if "weight_kg" in payload and "date_of_birth" in payload:
        dob = date.fromisoformat(str(payload["date_of_birth"]))
        # A mature weight dated on the birth date is impossible. Preserve the
        # historical as-of evidence needed by backdated breeding tests while
        # recording it at a plausible one-year age (or today for younger rows).
        payload.setdefault("weight_date", iso(min(dob + timedelta(days=365), today())))
    if sex == "M" and bucket == "BREEDING":
        dob = today() - timedelta(days=800)
        payload.setdefault("date_of_birth", iso(dob))
        payload.setdefault("weight_kg", 30.0)
        payload.setdefault("weight_date", iso(dob + timedelta(days=365)))
    resp = await client.post("/api/animals", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def change_status(
    client: httpx.AsyncClient, headers: dict, animal_id: int, new_status: str, **overrides: object
) -> None:
    resp = await client.post(
        f"/api/animals/{animal_id}/status",
        json={"new_status": new_status} | overrides,
        headers=headers,
    )
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# 5-H2 — reports: SQL aggregates must match the old Python-computed values
# ---------------------------------------------------------------------------
async def _golden_reports(farm_id: int) -> dict:
    """The pre-5-H2 algorithm, run by hand over ORM rows: every aggregate the
    reports endpoint used to compute in Python."""
    async with get_sessionmaker()() as db:
        animals = list(
            (
                await db.execute(
                    select(Animal)
                    .options(selectinload(Animal.weight_records))
                    .where(Animal.farm_id == farm_id)
                )
            ).scalars()
        )
        defs = list(
            (
                await db.execute(
                    select(BucketDefinition)
                    .order_by(BucketDefinition.sort_order)
                )
            ).scalars()
        )
        records = list(
            (
                await db.execute(select(BreedingRecord).where(BreedingRecord.farm_id == farm_id))
            ).scalars()
        )
        kiddings = list(
            (
                await db.execute(
                    select(KiddingRecord)
                    .options(selectinload(KiddingRecord.kids))
                    .where(KiddingRecord.farm_id == farm_id)
                )
            ).scalars()
        )
        kid_entries = list(
            (
                await db.execute(
                    select(KidEntry)
                    .join(KiddingRecord, KidEntry.kidding_record_id == KiddingRecord.id)
                    .where(KiddingRecord.farm_id == farm_id)
                )
            ).scalars()
        )

    active = [a for a in animals if a.status == AnimalStatus.ACTIVE.value]
    bucket_rows = []
    for d in defs:
        members = [a for a in active if a.current_bucket == d.code]
        weights = [w for a in members if (w := a.latest_weight_kg)]
        bucket_rows.append(
            {
                "code": d.code,
                "count": len(members),
                "avg_weight": round(sum(weights) / len(weights), 1) if weights else None,
            }
        )
    sex_counts: dict[str, int] = {"M": 0, "F": 0}
    status_counts: dict[str, int] = {}
    for a in animals:
        status_counts[a.status] = status_counts.get(a.status, 0) + 1
        if a.status == AnimalStatus.ACTIVE.value:
            sex_counts[a.sex] = sex_counts.get(a.sex, 0) + 1

    first_cycle = [r for r in records if r.heat_cycle_number == 1]
    alive_per_kidding = [
        sum(1 for e in k.kids if e.status == KidStatus.ALIVE.value) for k in kiddings
    ]
    multi_kid = sum(1 for n in alive_per_kidding if n >= 2)
    cull_candidates = sorted(
        (a.id for a in active if a.cull_candidate),
    )

    deaths_by_month: dict[str, int] = {}
    for a in animals:
        if a.status == AnimalStatus.DEAD.value and a.status_date:
            key = a.status_date.strftime("%Y-%m")
            deaths_by_month[key] = deaths_by_month.get(key, 0) + 1
    total_kids = len(kid_entries)
    stillborn = sum(1 for e in kid_entries if e.status == KidStatus.STILLBORN.value)

    return {
        "bucket_rows": bucket_rows,
        "total_active": len(active),
        "sex_counts": sex_counts,
        "status_counts": status_counts,
        "breeding": {
            "total_records": len(records),
            "conception_rate": conception_rate(records),
            "first_cycle_rate": conception_rate(first_cycle),
            "kiddings": len(kiddings),
            "kids_per_kidding": (
                round(sum(alive_per_kidding) / len(alive_per_kidding), 2)
                if alive_per_kidding
                else None
            ),
            "twin_rate": round(100.0 * multi_kid / len(kiddings), 1) if kiddings else None,
            "cull_candidate_ids": cull_candidates,
        },
        "mortality": {
            "total_deaths": status_counts.get(AnimalStatus.DEAD.value, 0),
            "deaths_by_month": sorted(deaths_by_month.items(), reverse=True),
            "total_kids_born": total_kids,
            "stillborn": stillborn,
            "stillborn_rate": round(100.0 * stillborn / total_kids, 1) if total_kids else None,
        },
    }


async def test_reports_match_old_python_aggregation(client: httpx.AsyncClient) -> None:
    """Golden comparison (5-H2): the SQL-aggregated response equals the values
    the old load-everything-into-Python version produced."""
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    now = today()

    # Herd: weighted/unweighted animals across buckets, plus sold and dead.
    await make_animal(client, owner, "G-1", weight_kg=20.0)
    await make_animal(client, owner, "G-2", weight_kg=25.0)
    await make_animal(client, owner, "G-3", sex="M", bucket="MALE_KIDS", weight_kg=12.0)
    await make_animal(client, owner, "G-4", bucket="RESTING")  # no weight at all
    born = await make_animal(
        client,
        owner,
        "G-5",
        bucket="FEMALE_KIDS",
        source="BORN",
        date_of_birth=iso(now - timedelta(days=30)),
        birth_weight=3.0,
    )  # falls back to birth_weight
    assert born["latest_weight_kg"] == 3.0
    sold = await make_animal(client, owner, "G-6", sex="M")
    await change_status(client, owner, sold["id"], "SOLD", sale_price=9000.0)
    dead1 = await make_animal(client, owner, "G-7", sex="M")
    await change_status(client, owner, dead1["id"], "DEAD", date=iso(now - timedelta(days=3)))
    dead2 = await make_animal(client, owner, "G-8", sex="F")
    await change_status(client, owner, dead2["id"], "DEAD", date=iso(now - timedelta(days=9)))
    dead3 = await make_animal(client, owner, "G-9", sex="M")
    await change_status(client, owner, dead3["id"], "DEAD", date=iso(now - timedelta(days=40)))

    # Breeding: doe1 confirms then kids (2 alive + 1 stillborn); doe2 fails
    # two consecutive cycles (→ cull candidate).
    doe1 = await make_animal(
        client,
        owner,
        "GD-1",
        bucket="BREEDING",
        date_of_birth=iso(now - timedelta(days=800)),
        weight_kg=26.0,
    )
    doe2 = await make_animal(
        client,
        owner,
        "GD-2",
        bucket="BREEDING",
        date_of_birth=iso(now - timedelta(days=800)),
        weight_kg=27.0,
    )
    buck = await make_animal(client, owner, "GB-1", sex="M", bucket="BREEDING")

    async def breed(doe_id: int, days_ago: int, cycle: int) -> int:
        resp = await client.post(
            "/api/breeding",
            json={
                "doe_id": doe_id,
                "buck_id": buck["id"],
                "breeding_date": iso(now - timedelta(days=days_ago)),
                "heat_cycle_number": cycle,
            },
            headers=owner,
        )
        assert resp.status_code == 201, resp.text
        return resp.json()["id"]

    async def ultrasound(br_id: int, pregnant: bool, result_date: date) -> None:
        payload: dict = {"pregnant": pregnant, "date": iso(result_date)}
        if pregnant:
            payload["kid_count"] = 2
        resp = await client.post(f"/api/breeding/{br_id}/ultrasound", json=payload, headers=owner)
        assert resp.status_code == 200, resp.text

    br1 = await breed(doe1["id"], 160, 1)
    await ultrasound(br1, True, now - timedelta(days=128))
    resp = await client.post(
        "/api/kidding",
        json={
            "breeding_record_id": br1,
            "date": iso(now - timedelta(days=10)),
            "ease": "NORMAL",
            "kids": [
                {"sex": "M", "birth_weight": 2.5},
                {"sex": "F", "birth_weight": 2.2},
                {"sex": "M", "status": "STILLBORN"},
            ],
        },
        headers=owner,
    )
    assert resp.status_code == 201, resp.text

    br2 = await breed(doe2["id"], 120, 1)
    await ultrasound(br2, False, now - timedelta(days=88))
    br3 = await breed(doe2["id"], 60, 2)
    await ultrasound(br3, False, now - timedelta(days=28))

    expected = await _golden_reports(farm_id)
    resp = await client.get("/api/dashboard/reports", headers=owner)
    assert resp.status_code == 200, resp.text
    rep = resp.json()

    assert [
        {"code": r["code"], "count": r["count"], "avg_weight": r["avg_weight"]}
        for r in rep["bucket_rows"]
    ] == expected["bucket_rows"]
    assert rep["total_active"] == expected["total_active"]
    assert rep["sex_counts"] == expected["sex_counts"]
    assert rep["status_counts"] == expected["status_counts"]
    breeding = rep["breeding"]
    exp_breeding = expected["breeding"]
    assert breeding["total_records"] == exp_breeding["total_records"]
    assert breeding["conception_rate"] == exp_breeding["conception_rate"]
    assert breeding["first_cycle_rate"] == exp_breeding["first_cycle_rate"]
    assert breeding["kiddings"] == exp_breeding["kiddings"]
    assert breeding["kids_per_kidding"] == exp_breeding["kids_per_kidding"]
    assert breeding["twin_rate"] == exp_breeding["twin_rate"]
    assert sorted(a["id"] for a in breeding["cull_candidates"]) == sorted(
        exp_breeding["cull_candidate_ids"]
    )
    mortality = rep["mortality"]
    exp_mortality = expected["mortality"]
    assert mortality["total_deaths"] == exp_mortality["total_deaths"]
    assert [tuple(row) for row in mortality["deaths_by_month"]] == [
        tuple(row) for row in exp_mortality["deaths_by_month"]
    ]
    assert mortality["total_kids_born"] == exp_mortality["total_kids_born"]
    assert mortality["stillborn"] == exp_mortality["stillborn"]
    assert mortality["stillborn_rate"] == exp_mortality["stillborn_rate"]
    # Sanity: the scenario is non-trivial (otherwise the golden test proves nothing).
    assert rep["breeding"]["total_records"] == 3
    assert rep["mortality"]["total_deaths"] == 3
    assert rep["mortality"]["deaths_by_month"] != []


async def test_reports_match_old_python_aggregation_empty(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    expected = await _golden_reports(farm_id)
    resp = await client.get("/api/dashboard/reports", headers=owner)
    assert resp.status_code == 200, resp.text
    rep = resp.json()
    assert rep["total_active"] == expected["total_active"] == 0
    assert rep["status_counts"] == expected["status_counts"] == {}
    assert rep["breeding"]["conception_rate"] is None
    assert rep["mortality"]["deaths_by_month"] == []


# ---------------------------------------------------------------------------
# 5-M5 — breeding history is capped at the newest 100 records
# ---------------------------------------------------------------------------
async def test_breeding_list_capped_at_100(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    now = today()
    doe = await make_animal(
        client,
        owner,
        "CAP-D",
        bucket="BREEDING",
        date_of_birth=iso(now - timedelta(days=400)),
        weight_kg=26.0,
    )
    buck = await make_animal(client, owner, "CAP-B", sex="M", bucket="BREEDING")

    # 105 closed (FAILED) records straight into the DB — one PENDING per doe is
    # DB-enforced, so these must be terminal outcomes; dates walk back 105 days.
    async with get_sessionmaker()() as db:
        db.add_all(
            [
                BreedingRecord(
                    farm_id=farm_id,
                    doe_id=doe["id"],
                    buck_id=buck["id"],
                    breeding_date=now - timedelta(days=i),
                    heat_cycle_number=(i % 99) + 1,
                    ultrasound_done=True,
                    pregnant=False,
                    outcome=BreedingOutcome.FAILED.value,
                )
                for i in range(105)
            ]
        )
        await db.commit()

    resp = await client.get("/api/breeding", headers=owner)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["records"]) == 100
    dates = [r["breeding_date"] for r in body["records"]]
    assert dates == sorted(dates, reverse=True)
    assert dates[0] == iso(now)  # newest kept, 5 oldest dropped
    assert dates[-1] == iso(now - timedelta(days=99))
    # History carries bounded availability metadata, never unbounded ids.
    assert body["candidate_availability"] == {
        "eligible_doe_count": 1,
        "eligible_buck_count": 1,
    }
    assert "candidate_doe_ids" not in body
    assert "active_buck_ids" not in body


async def test_breeding_candidate_queries_are_page_bounded_and_constant(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    dob = today() - timedelta(days=400)
    for index in range(3):
        await make_animal(
            client,
            owner,
            f"SQL-DOE-{index:02d}",
            date_of_birth=iso(dob),
            weight_kg=26.0,
        )

    async def captured(path: str) -> tuple[httpx.Response, list[tuple[str, object]]]:
        statements: list[tuple[str, object]] = []

        def capture_statement(
            _conn: object,
            _cursor: object,
            statement: str,
            parameters: object,
            _context: object,
            _executemany: object,
        ) -> None:
            statements.append((statement, parameters))

        engine = get_engine().sync_engine
        event.listen(engine, "before_cursor_execute", capture_statement)
        try:
            response = await client.get(path, headers=owner)
        finally:
            event.remove(engine, "before_cursor_execute", capture_statement)
        return response, statements

    baseline, baseline_statements = await captured(
        "/api/breeding/candidates?kind=doe&limit=2&offset=1"
    )
    assert baseline.status_code == 200, baseline.text
    assert baseline.json()["total"] == 3
    assert len(baseline.json()["candidates"]) == 2
    baseline_history, baseline_history_statements = await captured("/api/breeding?limit=1")
    assert baseline_history.status_code == 200, baseline_history.text
    assert baseline_history.json()["candidate_availability"] == {
        "eligible_doe_count": 3,
        "eligible_buck_count": 0,
    }

    for index in range(3, 18):
        await make_animal(
            client,
            owner,
            f"SQL-DOE-{index:02d}",
            date_of_birth=iso(dob),
            weight_kg=26.0,
        )

    expanded, expanded_statements = await captured(
        "/api/breeding/candidates?kind=doe&limit=2&offset=1"
    )
    assert expanded.status_code == 200, expanded.text
    assert expanded.json()["total"] == 18
    assert len(expanded.json()["candidates"]) == 2
    assert len(expanded_statements) == len(baseline_statements)

    animal_page_sql = [
        statement
        for statement, _parameters in expanded_statements
        if "ORDER BY animals.tag_number, animals.id" in statement
    ]
    assert len(animal_page_sql) == 1
    assert "LIMIT" in animal_page_sql[0]

    # Latest weight is now one bounded correlated scalar in the page query;
    # no page-sized relationship load (and no lifetime history) crosses the
    # DB boundary. One LIMIT caps that scalar and another caps the page.
    assert "weight_records" in animal_page_sql[0]
    assert animal_page_sql[0].count("LIMIT") >= 2
    assert not any(
        "WHERE weight_records.animal_id IN" in statement
        for statement, _parameters in expanded_statements
    )

    history, history_statements = await captured("/api/breeding?limit=1")
    assert history.status_code == 200, history.text
    assert history.json()["candidate_availability"] == {
        "eligible_doe_count": 18,
        "eligible_buck_count": 0,
    }
    assert len(history_statements) == len(baseline_history_statements)
    assert not any(
        "WHERE weight_records.animal_id IN" in statement
        for statement, _parameters in history_statements
    )


# ---------------------------------------------------------------------------
# 5-M3 — completed tab: predicate runs in SQL before the 100-row cap
# ---------------------------------------------------------------------------
async def test_completed_tab_survives_unverified_cleaning_flood(
    client: httpx.AsyncClient,
) -> None:
    """Pre-5-M3 the newest 100 finished rows were filtered in Python: a flood
    of unverified DONE CLEANING rows pushed older eligible rows out of the
    completed tab even though the cap should apply AFTER filtering."""
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    base = utcnow()
    async with get_sessionmaker()() as db:
        # Older eligible row: DONE, no verification needed.
        db.add(
            Task(
                farm_id=farm_id,
                title="Old eligible duty",
                due_date=today(),
                status=TaskStatus.DONE.value,
                category=TaskCategory.OTHER.value,
                completed_at=base - timedelta(days=2),
            )
        )
        # 105 newer DONE CLEANING rows (awaiting verification — excluded from
        # the completed tab by the predicate).
        db.add_all(
            [
                Task(
                    farm_id=farm_id,
                    title=f"Cleaning #{i}",
                    due_date=today(),
                    status=TaskStatus.DONE.value,
                    category=TaskCategory.CLEANING.value,
                    completed_at=base - timedelta(minutes=i),
                )
                for i in range(105)
            ]
        )
        await db.commit()

    resp = await client.get("/api/tasks", headers=owner)
    assert resp.status_code == 200, resp.text
    tabs = resp.json()
    completed_titles = [t["title"] for t in tabs["completed"]]
    assert completed_titles == ["Old eligible duty"]
    awaiting_titles = [t["title"] for t in tabs["awaiting"]]
    assert len(awaiting_titles) == tabs["active_limit"] == 100
    assert tabs["awaiting_total"] == 105  # CLEANING filter applied in SQL


# ---------------------------------------------------------------------------
# 5-M4 / 5-L3 — hot-path indexes exist after `alembic upgrade head`
# ---------------------------------------------------------------------------
async def test_query_performance_indexes_exist(client: httpx.AsyncClient) -> None:
    async with get_sessionmaker()() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT tablename, indexname FROM pg_indexes "
                    "WHERE indexname IN ("
                    "'ix_tasks_purchase_batch_id', 'ix_tasks_breeding_record_id', "
                    "'ix_farms_owner_id', 'ix_bucket_moves_animal_moved_id_desc', "
                    "'ix_weight_records_animal_date_id_desc', "
                    "'ix_weight_records_recent_date_id_animal', "
                    "'ix_animals_farm_active_bucket_tag_id', "
                    "'ix_animals_farm_active_cull_tag_id', 'ix_animals_farm_status', "
                    "'ix_breeding_records_farm_confirmed_doe_date_id', "
                    "'ix_breeding_records_farm_confirmed_due_id', "
                    "'ix_tasks_farm_pending_due_id', "
                    "'ix_tasks_farm_pending_category_due_id')"
                )
            )
        ).all()
    found = {(tablename, indexname) for tablename, indexname in rows}
    assert found == {
        ("tasks", "ix_tasks_purchase_batch_id"),
        ("tasks", "ix_tasks_breeding_record_id"),
        ("farms", "ix_farms_owner_id"),
        ("bucket_moves", "ix_bucket_moves_animal_moved_id_desc"),
        ("weight_records", "ix_weight_records_animal_date_id_desc"),
        ("weight_records", "ix_weight_records_recent_date_id_animal"),
        ("animals", "ix_animals_farm_active_bucket_tag_id"),
        ("animals", "ix_animals_farm_active_cull_tag_id"),
        ("animals", "ix_animals_farm_status"),
        ("breeding_records", "ix_breeding_records_farm_confirmed_doe_date_id"),
        ("breeding_records", "ix_breeding_records_farm_confirmed_due_id"),
        ("tasks", "ix_tasks_farm_pending_due_id"),
        ("tasks", "ix_tasks_farm_pending_category_due_id"),
    }


# ---------------------------------------------------------------------------
# 5-M2 — finance all-time totals come from SQL GROUP BY (contract unchanged)
# ---------------------------------------------------------------------------
async def test_finance_totals_ignore_filters(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    for payload in (
        {"type": "INCOME", "category": "OTHER", "amount": 9000.0},
        {"type": "INCOME", "category": "MANURE", "amount": 500.5},
        {"type": "EXPENSE", "category": "FEED", "amount": 1200.25},
    ):
        resp = await client.post(
            "/api/finance/new",
            json={"date": iso(today()), "type": "EXPENSE", "category": "FEED", "amount": 1.0}
            | payload,
            headers=owner,
        )
        assert resp.status_code == 201, resp.text
    resp = await client.get("/api/finance", params={"type": "INCOME"}, headers=owner)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Totals are all-time and ignore the list filters.
    assert body["total_income"] == 9500.5
    assert body["total_expense"] == 1200.25
    assert len(body["transactions"]) == 2


async def test_reports_endpoint_uses_bounded_queries(client: httpx.AsyncClient) -> None:
    """5-H2 smoke guard: no ORM Animal rows are hydrated for the aggregates —
    the endpoint must not lazy-load history collections (MissingGreenlet would
    500). A populated farm + reports call is the regression tripwire."""
    owner = await owner_with_farm(client)
    await make_animal(client, owner, "SMOKE-1", weight_kg=30.0)
    resp = await client.get("/api/dashboard/reports", headers=owner)
    assert resp.status_code == 200, resp.text


async def test_bucket_board_preview_and_history_queries_stay_bounded(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    dob = today() - timedelta(days=500)

    async with get_sessionmaker()() as db:
        first = Animal(
            farm_id=farm_id,
            tag_number="BOARD-000",
            name="Deep history",
            sex="F",
            date_of_birth=dob,
            source="BORN",
            birth_type="SINGLE",
            birth_weight=3.0,
            current_bucket=Bucket.FOUNDATION.value,
        )
        db.add(first)
        await db.flush()
        db.add(WeightRecord(animal_id=first.id, date=today(), weight_kg=55.0))
        db.add(
            BucketMove(
                animal_id=first.id,
                from_bucket=None,
                to_bucket=Bucket.FOUNDATION.value,
                moved_at=utcnow() - timedelta(days=1),
            )
        )
        await db.commit()

    async def captured_board() -> tuple[httpx.Response, list[str]]:
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
            response = await client.get("/api/buckets", headers=owner)
        finally:
            event.remove(engine, "before_cursor_execute", capture_statement)
        return response, statements

    baseline, baseline_statements = await captured_board()
    assert baseline.status_code == 200, baseline.text

    async with get_sessionmaker()() as db:
        first = await db.get(Animal, first.id)
        assert first is not None
        db.add_all(
            [
                Animal(
                    farm_id=farm_id,
                    tag_number=f"BOARD-{index:03d}",
                    sex="F",
                    date_of_birth=dob,
                    source="BORN",
                    birth_type="SINGLE",
                    birth_weight=3.0,
                    current_bucket=Bucket.FOUNDATION.value,
                )
                for index in range(1, 105)
            ]
        )
        db.add_all(
            [
                WeightRecord(
                    animal_id=first.id,
                    date=today() - timedelta(days=index + 1),
                    weight_kg=20.0 + (index % 10),
                )
                for index in range(300)
            ]
        )
        db.add_all(
            [
                BucketMove(
                    animal_id=first.id,
                    from_bucket=Bucket.FOUNDATION.value,
                    to_bucket=Bucket.FOUNDATION.value,
                    moved_at=utcnow() - timedelta(days=index + 30),
                    reason="old board history",
                )
                for index in range(300)
            ]
        )
        await db.commit()

    expanded, expanded_statements = await captured_board()
    assert expanded.status_code == 200, expanded.text
    foundation = next(row for row in expanded.json() if row["bucket"] == "FOUNDATION")
    assert foundation["animals_total"] == 105
    assert len(foundation["animals"]) == foundation["animals_limit"] == 100
    assert [animal["tag_number"] for animal in foundation["animals"]] == [
        f"BOARD-{index:03d}" for index in range(100)
    ]
    first_preview = foundation["animals"][0]
    assert first_preview["latest_weight_kg"] == 55.0
    assert first_preview["days_in_current_bucket"] < 10
    assert len(expanded_statements) == len(baseline_statements)

    bounded_history_queries = [
        statement
        for statement in expanded_statements
        if "LATERAL" in statement
        and "bucket_latest_weight" in statement
        and "bucket_latest_move" in statement
    ]
    assert len(bounded_history_queries) == 1
    assert "row_number() OVER" in bounded_history_queries[0]
    assert bounded_history_queries[0].count("LIMIT") >= 2
    assert not any(
        "WHERE weight_records.animal_id IN" in statement
        or "WHERE bucket_moves.animal_id IN" in statement
        for statement in expanded_statements
    )


async def test_dashboard_previews_totals_and_query_count_stay_bounded(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])

    async def captured_dashboard() -> tuple[httpx.Response, list[str]]:
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
            response = await client.get("/api/dashboard", headers=owner)
        finally:
            event.remove(engine, "before_cursor_execute", capture_statement)
        return response, statements

    baseline, baseline_statements = await captured_dashboard()
    assert baseline.status_code == 200, baseline.text

    dob = today() - timedelta(days=500)
    async with get_sessionmaker()() as db:
        suggestion_animals = [
            Animal(
                farm_id=farm_id,
                tag_number=f"PREVIEW-{index:03d}",
                name=f"Preview {index}",
                sex="F",
                date_of_birth=dob,
                source="BORN",
                birth_type="SINGLE",
                birth_weight=26.0,
                current_bucket=Bucket.FOUNDATION.value,
                cull_candidate=True,
            )
            for index in range(105)
        ]
        buck = Animal(
            farm_id=farm_id,
            tag_number="PREVIEW-BUCK",
            sex="M",
            date_of_birth=dob,
            source="BORN",
            birth_type="SINGLE",
            birth_weight=30.0,
            current_bucket=Bucket.FOUNDATION.value,
        )
        kidding_does = [
            Animal(
                farm_id=farm_id,
                tag_number=f"DUE-{index:03d}",
                sex="F",
                date_of_birth=dob,
                source="BORN",
                birth_type="SINGLE",
                birth_weight=26.0,
                current_bucket=Bucket.DELIVERY.value,
            )
            for index in range(105)
        ]
        db.add_all([*suggestion_animals, buck, *kidding_does])
        await db.flush()

        today_tasks = [
            Task(
                farm_id=farm_id,
                title=f"Today {index:03d}",
                due_date=today(),
                category=TaskCategory.OTHER.value,
            )
            for index in range(105)
        ]
        overdue_tasks = [
            Task(
                farm_id=farm_id,
                title=f"Overdue {index:03d}",
                due_date=today() - timedelta(days=1),
                category=TaskCategory.OTHER.value,
            )
            for index in range(105)
        ]
        ultrasound_tasks = [
            Task(
                farm_id=farm_id,
                title=f"Ultrasound {index:03d}",
                due_date=today() + timedelta(days=1),
                category=TaskCategory.ULTRASOUND.value,
            )
            for index in range(105)
        ]
        db.add_all([*today_tasks, *overdue_tasks, *ultrasound_tasks])
        db.add_all(
            [
                BreedingRecord(
                    farm_id=farm_id,
                    doe_id=doe.id,
                    buck_id=buck.id,
                    breeding_date=today() - timedelta(days=140),
                    heat_cycle_number=1,
                    ultrasound_done=True,
                    pregnant=True,
                    kid_count_detected=1,
                    expected_kidding_date=today() + timedelta(days=10),
                    outcome=BreedingOutcome.CONFIRMED_PREGNANT.value,
                )
                for doe in kidding_does
            ]
        )
        first = suggestion_animals[0]
        db.add_all(
            [
                WeightRecord(
                    animal_id=first.id,
                    date=today() - timedelta(days=index),
                    weight_kg=30.0 + (index % 10),
                )
                for index in range(300)
            ]
        )
        db.add_all(
            [
                BucketMove(
                    animal_id=first.id,
                    from_bucket=Bucket.FOUNDATION.value,
                    to_bucket=Bucket.FOUNDATION.value,
                    moved_at=utcnow() - timedelta(days=index + 1),
                    reason="old dashboard history",
                )
                for index in range(300)
            ]
        )
        db.add_all(
            [
                BreedingRecord(
                    farm_id=farm_id,
                    doe_id=first.id,
                    buck_id=buck.id,
                    breeding_date=today() - timedelta(days=index + 1),
                    heat_cycle_number=(index % 99) + 1,
                    ultrasound_done=True,
                    pregnant=False,
                    outcome=BreedingOutcome.FAILED.value,
                )
                for index in range(300)
            ]
        )
        await db.flush()
        expected_task_ids = {
            "todays_tasks": [task.id for task in today_tasks[:100]],
            "overdue_tasks": [task.id for task in overdue_tasks[:100]],
            "ultrasounds_due": [task.id for task in ultrasound_tasks[:100]],
        }
        await db.commit()

    expanded, expanded_statements = await captured_dashboard()
    assert expanded.status_code == 200, expanded.text
    body = expanded.json()
    assert body["preview_limit"] == 100
    for field in (
        "todays_tasks",
        "overdue_tasks",
        "ultrasounds_due",
        "kiddings_due",
        "cull_candidates",
        "suggestions",
    ):
        assert len(body[field]) == body["preview_limit"]
        assert body[f"{field}_total"] == 105
    for field, ids in expected_task_ids.items():
        assert [row["id"] for row in body[field]] == ids
    assert [row["tag_number"] for row in body["cull_candidates"]] == [
        f"PREVIEW-{index:03d}" for index in range(100)
    ]
    assert [row["animal"]["tag_number"] for row in body["suggestions"]] == [
        f"PREVIEW-{index:03d}" for index in range(100)
    ]
    assert body["recent_weights_total"] == 300
    assert len(body["recent_weights"]) == body["recent_weights_limit"] == 10
    assert len(expanded_statements) == len(baseline_statements)
    assert not any(
        "WHERE weight_records.animal_id IN" in statement
        or "WHERE bucket_moves.animal_id IN" in statement
        or "WHERE breeding_records.doe_id IN" in statement
        or "WHERE kidding_records.breeding_record_id IN" in statement
        for statement in expanded_statements
    )

    reports = await client.get("/api/dashboard/reports", headers=owner)
    assert reports.status_code == 200, reports.text
    breeding = reports.json()["breeding"]
    assert breeding["cull_candidates_total"] == 105
    assert len(breeding["cull_candidates"]) == breeding["cull_candidates_limit"] == 100


async def test_dashboard_sql_suggestions_preserve_each_transition_rule(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    dob = today() - timedelta(days=800)
    async with get_sessionmaker()() as db:
        buck = Animal(
            farm_id=farm_id,
            tag_number="RULE-BUCK",
            sex="M",
            date_of_birth=dob,
            source="BORN",
            birth_type="SINGLE",
            birth_weight=30.0,
            current_bucket=Bucket.FOUNDATION.value,
        )
        resting = Animal(
            farm_id=farm_id,
            tag_number="RULE-RESTING",
            sex="F",
            date_of_birth=dob,
            source="BORN",
            birth_type="SINGLE",
            birth_weight=26.0,
            current_bucket=Bucket.RESTING.value,
            created_at=utcnow() - timedelta(days=31),
        )
        early = Animal(
            farm_id=farm_id,
            tag_number="RULE-EARLY",
            sex="F",
            date_of_birth=dob,
            source="BORN",
            birth_type="SINGLE",
            birth_weight=26.0,
            current_bucket=Bucket.PREGNANCY_EARLY.value,
        )
        late = Animal(
            farm_id=farm_id,
            tag_number="RULE-LATE",
            sex="F",
            date_of_birth=dob,
            source="BORN",
            birth_type="SINGLE",
            birth_weight=26.0,
            current_bucket=Bucket.PREGNANCY_LATE.value,
        )
        market = Animal(
            farm_id=farm_id,
            tag_number="RULE-MARKET",
            sex="M",
            date_of_birth=today() - timedelta(days=300),
            source="BORN",
            birth_type="SINGLE",
            birth_weight=24.0,
            current_bucket=Bucket.MALE_KIDS.value,
        )
        held_animals: list[Animal] = []
        held_pregnancies: list[tuple[Animal, int]] = []
        for hold_name, suspected in (("RESTRICTED", False), ("SUSPECTED", True)):
            for bucket, gestation_days in (
                (Bucket.RESTING, 0),
                (Bucket.PREGNANCY_EARLY, 100),
                (Bucket.PREGNANCY_LATE, 135),
                (Bucket.MALE_KIDS, 0),
            ):
                animal = Animal(
                    farm_id=farm_id,
                    tag_number=f"BLOCKED-{hold_name}-{bucket.value}",
                    sex="M" if bucket == Bucket.MALE_KIDS else "F",
                    date_of_birth=(
                        today() - timedelta(days=300) if bucket == Bucket.MALE_KIDS else dob
                    ),
                    source="BORN",
                    birth_type="SINGLE",
                    birth_weight=24.0 if bucket == Bucket.MALE_KIDS else 26.0,
                    current_bucket=bucket.value,
                    created_at=utcnow() - timedelta(days=31),
                    movement_restricted=True,
                    restriction_reason="Regulatory movement hold",
                    suspected_scheduled_disease=suspected,
                    suspected_disease="PPR" if suspected else None,
                )
                held_animals.append(animal)
                if gestation_days:
                    held_pregnancies.append((animal, gestation_days))

        young_resting = Animal(
            farm_id=farm_id,
            tag_number="BLOCKED-YOUNG-RESTING",
            sex="F",
            date_of_birth=today() - timedelta(days=270),
            source="BORN",
            birth_type="SINGLE",
            birth_weight=26.0,
            current_bucket=Bucket.RESTING.value,
            created_at=utcnow() - timedelta(days=31),
        )
        low_weight_resting = Animal(
            farm_id=farm_id,
            tag_number="BLOCKED-LIGHT-RESTING",
            sex="F",
            date_of_birth=dob,
            source="BORN",
            birth_type="SINGLE",
            birth_weight=21.0,
            current_bucket=Bucket.RESTING.value,
            created_at=utcnow() - timedelta(days=31),
        )
        pregnant_resting = Animal(
            farm_id=farm_id,
            tag_number="BLOCKED-PREGNANT-RESTING",
            sex="F",
            date_of_birth=dob,
            source="BORN",
            birth_type="SINGLE",
            birth_weight=26.0,
            current_bucket=Bucket.RESTING.value,
            created_at=utcnow() - timedelta(days=31),
        )
        withdrawal_market = Animal(
            farm_id=farm_id,
            tag_number="BLOCKED-WITHDRAWAL-MARKET",
            sex="M",
            date_of_birth=today() - timedelta(days=300),
            source="BORN",
            birth_type="SINGLE",
            birth_weight=24.0,
            current_bucket=Bucket.MALE_KIDS.value,
        )

        db.add_all(
            [
                buck,
                resting,
                early,
                late,
                market,
                young_resting,
                low_weight_resting,
                pregnant_resting,
                withdrawal_market,
                *held_animals,
            ]
        )
        await db.flush()
        db.add_all(
            [
                BreedingRecord(
                    farm_id=farm_id,
                    doe_id=early.id,
                    buck_id=buck.id,
                    breeding_date=today() - timedelta(days=100),
                    ultrasound_done=True,
                    pregnant=True,
                    kid_count_detected=1,
                    expected_kidding_date=today() + timedelta(days=50),
                    outcome=BreedingOutcome.CONFIRMED_PREGNANT.value,
                ),
                BreedingRecord(
                    farm_id=farm_id,
                    doe_id=pregnant_resting.id,
                    buck_id=buck.id,
                    breeding_date=today() - timedelta(days=50),
                    ultrasound_done=True,
                    pregnant=True,
                    kid_count_detected=1,
                    expected_kidding_date=today() + timedelta(days=100),
                    outcome=BreedingOutcome.CONFIRMED_PREGNANT.value,
                ),
                BreedingRecord(
                    farm_id=farm_id,
                    doe_id=late.id,
                    buck_id=buck.id,
                    breeding_date=today() - timedelta(days=135),
                    ultrasound_done=True,
                    pregnant=True,
                    kid_count_detected=1,
                    expected_kidding_date=today() + timedelta(days=15),
                    outcome=BreedingOutcome.CONFIRMED_PREGNANT.value,
                ),
                *(
                    BreedingRecord(
                        farm_id=farm_id,
                        doe_id=animal.id,
                        buck_id=buck.id,
                        breeding_date=today() - timedelta(days=gestation_days),
                        ultrasound_done=True,
                        pregnant=True,
                        kid_count_detected=1,
                        expected_kidding_date=today() + timedelta(days=150 - gestation_days),
                        outcome=BreedingOutcome.CONFIRMED_PREGNANT.value,
                    )
                    for animal, gestation_days in held_pregnancies
                ),
            ]
        )
        db.add(
            HealthEvent(
                farm_id=farm_id,
                animal_id=withdrawal_market.id,
                date=today(),
                type="TREATMENT",
                withdrawal_until=today() + timedelta(days=7),
            )
        )
        await db.commit()

    response = await client.get("/api/dashboard", headers=owner)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["suggestions_total"] == 4
    by_tag = {row["animal"]["tag_number"]: row for row in body["suggestions"]}
    assert by_tag["RULE-RESTING"]["to"] == Bucket.BREEDING.value
    assert "days resting" in by_tag["RULE-RESTING"]["reason"]
    assert by_tag["RULE-EARLY"]["to"] == Bucket.PREGNANCY_LATE.value
    assert "Gestation day 100" in by_tag["RULE-EARLY"]["reason"]
    assert by_tag["RULE-LATE"]["to"] == Bucket.DELIVERY.value
    assert "Gestation day 135" in by_tag["RULE-LATE"]["reason"]
    assert by_tag["RULE-MARKET"]["to"] == "SELL"
    assert "market ready" in by_tag["RULE-MARKET"]["reason"]
    assert not any(tag.startswith("BLOCKED-") for tag in by_tag)

    for blocked_id in (young_resting.id, low_weight_resting.id, pregnant_resting.id):
        rejected = await client.post(
            f"/api/animals/{blocked_id}/move",
            json={"to_bucket": Bucket.BREEDING.value, "reason": "Dashboard suggestion"},
            headers=owner,
        )
        assert rejected.status_code == 409, rejected.text
    rejected_sale = await client.post(
        f"/api/animals/{withdrawal_market.id}/status",
        json={"new_status": "SOLD", "sale_price": 1000},
        headers=owner,
    )
    assert rejected_sale.status_code == 409, rejected_sale.text


async def test_feeding_plan_query_count_and_rows_stay_bounded_by_history(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    animal = await make_animal(client, owner, "PLAN-HISTORY", bucket="RESTING")

    async def captured_plan() -> tuple[httpx.Response, list[str]]:
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
            response = await client.get("/api/feeding/plan", headers=owner)
        finally:
            event.remove(engine, "before_cursor_execute", capture_statement)
        return response, statements

    # Backdate the current movement so RESTING is on its FLUSH phase. This is
    # the latest row and must remain authoritative after old history is added.
    async with get_sessionmaker()() as db:
        current_move = (
            await db.execute(
                select(BucketMove)
                .where(BucketMove.animal_id == animal["id"])
                .order_by(BucketMove.moved_at.desc(), BucketMove.id.desc())
                .limit(1)
            )
        ).scalar_one()
        current_move.moved_at = utcnow() - timedelta(days=11)
        current_move.effective_date = today() - timedelta(days=11)
        await db.commit()

    baseline, baseline_statements = await captured_plan()
    assert baseline.status_code == 200, baseline.text
    assert baseline.json()["lines"][0]["recipe_code"] == "FLUSH_70_30"
    assert baseline.json()["records_total"] == 0

    async with get_sessionmaker()() as db:
        db.add_all(
            [
                BucketMove(
                    animal_id=animal["id"],
                    from_bucket=Bucket.RESTING.value,
                    to_bucket=Bucket.RESTING.value,
                    moved_at=utcnow() - timedelta(days=20 + index),
                    effective_date=today() - timedelta(days=20 + index),
                    reason="historical query-bound fixture",
                )
                for index in range(300)
            ]
        )
        records = [
            FeedingRecord(
                farm_id=farm_id,
                date=today(),
                shift=FeedingShift.MORNING.value,
                bucket=Bucket.RESTING.value,
                recipe_code="DRY_ROUGHAGE_ONLY",
                qty_kg=0.001,
            )
            for _index in range(250)
        ]
        db.add_all(records)
        await db.flush()
        newest_200_ids = [record.id for record in records][-200:]
        await db.commit()

    expanded, expanded_statements = await captured_plan()
    assert expanded.status_code == 200, expanded.text
    body = expanded.json()
    assert body["lines"][0]["recipe_code"] == "FLUSH_70_30"
    assert body["records_total"] == 250
    assert len(body["records"]) == body["records_limit"] == 200
    assert [record["id"] for record in body["records"]] == newest_200_ids
    assert body["dispensed_totals"] == [
        {
            "bucket": Bucket.RESTING.value,
            "recipe_code": "DRY_ROUGHAGE_ONLY",
            "shift": FeedingShift.MORNING.value,
            "qty_kg": 0.25,
        }
    ]
    assert len(expanded_statements) == len(baseline_statements)

    move_queries = [
        statement
        for statement in expanded_statements
        if "LATERAL" in statement and "bucket_moves" in statement
    ]
    assert len(move_queries) == 1
    assert "LIMIT" in move_queries[0]
    assert not any(
        "WHERE bucket_moves.animal_id IN" in statement for statement in expanded_statements
    )
    record_count_queries = [
        statement
        for statement in expanded_statements
        if "count(feeding_records.id)" in statement and "FROM feeding_records" in statement
    ]
    assert len(record_count_queries) == 1
    assert "LIMIT" not in record_count_queries[0]
    record_window_queries = [
        statement
        for statement in expanded_statements
        if "FROM feeding_records" in statement and "ORDER BY feeding_records.id DESC" in statement
    ]
    assert len(record_window_queries) == 1
    assert "LIMIT" in record_window_queries[0]
