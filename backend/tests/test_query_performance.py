"""Query-performance audit (Lens 5) regression tests.

- 5-H2: GET /api/dashboard/reports aggregates in SQL — a golden test compares
  every number against the old Python-side algorithm computed straight from
  ORM rows.
- 5-M5: GET /api/breeding history is capped at the newest 100 records.
- 5-M3: the tasks completed tab applies its predicate in SQL BEFORE the
  100-row cap (an older eligible row must survive a flood of newer
  unverified CLEANING rows).
- 5-M4 / 5-L3: the new hot-path indexes exist after `alembic upgrade head`.
"""

from datetime import date, timedelta

import httpx
from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

from app.db import get_sessionmaker
from app.models import (
    Animal,
    AnimalStatus,
    BreedingOutcome,
    BreedingRecord,
    BucketDefinition,
    KiddingRecord,
    KidEntry,
    KidStatus,
    Task,
    TaskCategory,
    TaskStatus,
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
                await db.execute(select(BucketDefinition).order_by(BucketDefinition.sort_order))
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
        client, owner, "G-5", bucket="FEMALE_KIDS", birth_weight=3.0
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
        date_of_birth=iso(now - timedelta(days=400)),
        weight_kg=26.0,
    )
    doe2 = await make_animal(
        client,
        owner,
        "GD-2",
        bucket="BREEDING",
        date_of_birth=iso(now - timedelta(days=400)),
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

    async def ultrasound(br_id: int, pregnant: bool) -> None:
        payload: dict = {"pregnant": pregnant}
        if pregnant:
            payload["kid_count"] = 2
        resp = await client.post(f"/api/breeding/{br_id}/ultrasound", json=payload, headers=owner)
        assert resp.status_code == 200, resp.text

    br1 = await breed(doe1["id"], 160, 1)
    await ultrasound(br1, True)
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
    await ultrasound(br2, False)
    br3 = await breed(doe2["id"], 60, 2)
    await ultrasound(br3, False)

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
                    heat_cycle_number=i + 1,
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
    # Picker payloads ride along unchanged.
    assert body["candidate_doe_ids"] == [doe["id"]]
    assert body["active_buck_ids"] == [buck["id"]]


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
    assert len(awaiting_titles) == 105  # CLEANING filter applied in SQL


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
                    "'ix_farms_owner_id')"
                )
            )
        ).all()
    found = {(tablename, indexname) for tablename, indexname in rows}
    assert found == {
        ("tasks", "ix_tasks_purchase_batch_id"),
        ("tasks", "ix_tasks_breeding_record_id"),
        ("farms", "ix_farms_owner_id"),
    }


# ---------------------------------------------------------------------------
# 5-M2 — finance all-time totals come from SQL GROUP BY (contract unchanged)
# ---------------------------------------------------------------------------
async def test_finance_totals_ignore_filters(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    for payload in (
        {"type": "INCOME", "category": "ANIMAL_SALE", "amount": 9000.0},
        {"type": "INCOME", "category": "MILK", "amount": 500.5},
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
