"""Cross-farm owner overview + benchmarks (ITEM 3, 2026-09-21 playbook).

Endpoints under test:
- GET /api/owner/overview — per-owned-farm attention headlines.
- GET /api/owner/benchmarks — per-owned-farm performance figures.

Ownership is the permission: an owner sees exactly their farms' aggregates
(grouped, never looped per farm), a worker with full grants gets 403, and a
second owner's farm never appears in the first owner's response.
"""

from datetime import timedelta
from decimal import Decimal

import httpx
import pytest

from app.db import get_sessionmaker
from app.models import Animal, Task, Transaction
from app.utils import today

from .conftest import create_farm, owner_with_farm
from .test_finance_extended import custom_role_id, make_animal, worker_headers


async def _seed_overview_rows(client: httpx.AsyncClient, headers: dict) -> None:
    """Two animals, one on the kidding watch pen, one restricted, one overdue
    duty, one due-today duty pair, and this month's ledger rows."""
    doe_a = await make_animal(client, headers, tag="OWN-A-1")
    doe_b = await make_animal(client, headers, tag="OWN-A-2")
    async with get_sessionmaker()() as db:
        farm_id = int(headers["X-Farm-Id"])
        a = await db.get(Animal, doe_a["id"])
        b = await db.get(Animal, doe_b["id"])
        assert a is not None and b is not None
        a.current_bucket = "DELIVERY"  # kidding watch
        b.movement_restricted = True
        b.restriction_reason = "Vet hold"
        db.add(
            Task(
                farm_id=farm_id,
                title="Overdue duty",
                due_date=today() - timedelta(days=1),
                category="OTHER",
                status="PENDING",
            )
        )
        db.add(
            Task(
                farm_id=farm_id,
                title="Today pending",
                due_date=today(),
                category="OTHER",
                status="PENDING",
            )
        )
        from app.utils import utcnow

        db.add(
            Task(
                farm_id=farm_id,
                title="Today done",
                due_date=today(),
                category="OTHER",
                status="DONE",
                completed_at=utcnow(),
            )
        )
        db.add(
            Transaction(
                farm_id=farm_id,
                date=today(),
                type="INCOME",
                category="ANIMAL_SALE",
                amount=Decimal("5000.00"),
            )
        )
        db.add(
            Transaction(
                farm_id=farm_id,
                date=today(),
                type="EXPENSE",
                category="FEED",
                amount=Decimal("1200.50"),
            )
        )
        await db.commit()


async def test_owner_overview_aggregates_every_owned_farm_and_only_those(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="cross-owner@farm.in")
    farm_a_id = int(owner["X-Farm-Id"])
    await _seed_overview_rows(client, owner)

    # A second farm the SAME owner owns, with nothing in it.
    farm_b_headers = await create_farm(client, owner, name="Second Ranch")
    farm_b_id = int(farm_b_headers["X-Farm-Id"])

    # A different owner's farm must never leak into the first owner's view.
    other = await owner_with_farm(client, email="other-owner@farm.in")
    await make_animal(client, other, tag="OTHER-1")

    resp = await client.get("/api/owner/overview", headers=owner)
    assert resp.status_code == 200, resp.text
    assert resp.headers.get("cache-control") == "no-store"
    farms = {row["farm_id"]: row for row in resp.json()["farms"]}
    assert set(farms) == {farm_a_id, farm_b_id}

    row_a = farms[farm_a_id]
    assert row_a["farm_name"] == "Alpha Farm"
    assert row_a["active_animals"] >= 2
    assert row_a["kidding_watch"] == 1
    assert row_a["movement_restricted"] == 1
    assert row_a["overdue_duties"] == 1
    assert row_a["todays_duties_pending"] == 1
    assert row_a["todays_duties_done"] == 1
    assert Decimal(str(row_a["month_income"])) == Decimal("5000.00")
    assert Decimal(str(row_a["month_expense"])) == Decimal("1200.50")
    assert Decimal(str(row_a["month_net"])) == Decimal("3799.50")

    row_b = farms[farm_b_id]
    assert row_b["active_animals"] == 0
    assert row_b["overdue_duties"] == 0
    assert Decimal(str(row_b["month_income"])) == Decimal("0")


async def test_owner_overview_is_owner_only(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client, email="own-only@farm.in")
    # A worker with every domain grant still has no cross-farm lens.
    role_id = await custom_role_id(
        client,
        owner,
        "Everything",
        [
            "dashboard.view",
            "animals.view",
            "animals.manage",
            "breeding.view",
            "breeding.manage",
            "health.view",
            "health.manage",
            "tasks.view",
            "tasks.complete",
            "tasks.verify",
            "finance.view",
            "finance.manage",
            "feeding.view",
            "feeding.manage",
            "reports.view",
            "screening.view",
            "screening.manage",
            "team.manage",
            "simulation.view",
            "simulation.manage",
        ],
    )
    worker = await worker_headers(client, owner, role_id, "own-worker@farm.in")
    resp = await client.get("/api/owner/overview", headers=worker)
    assert resp.status_code == 403, resp.text

    resp_b = await client.get("/api/owner/benchmarks", headers=worker)
    assert resp_b.status_code == 403, resp_b.text

    # The owner sees their (empty) farm set: 200, not 403 — ownership of one
    # farm is enough, and empty aggregates are a valid answer.
    ok = await client.get("/api/owner/overview", headers=owner)
    assert ok.status_code == 200, ok.text


async def test_owner_benchmarks_rank_farms_over_the_window(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client, email="bench-owner@farm.in")
    farm_id = int(owner["X-Farm-Id"])

    # Growth + a sold animal with a realized margin, directly through the
    # models the aggregates read.
    sold = await make_animal(client, owner, tag="BENCH-SOLD")
    async with get_sessionmaker()() as db:
        from app.models import WeightRecord

        animal = await db.get(Animal, sold["id"])
        assert animal is not None
        animal.status = "SOLD"
        animal.status_date = today()
        animal.sale_price = Decimal("8000.00")
        animal.purchase_price = Decimal("5000.00")
        db.add(
            WeightRecord(
                farm_id=farm_id,
                animal_id=animal.id,
                date=today() - timedelta(days=10),
                weight_kg=20.0,
            )
        )
        db.add(
            WeightRecord(
                farm_id=farm_id,
                animal_id=animal.id,
                date=today(),
                weight_kg=30.0,
            )
        )
        await db.commit()

    resp = await client.get("/api/owner/benchmarks", params={"days": 90}, headers=owner)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["days"] == 90
    assert len(body["farms"]) == 1
    row = body["farms"][0]
    assert row["farm_id"] == farm_id
    # No breedings/kiddings in the window: withheld (None), never a fake 0.
    assert row["conception_rate"] is None
    assert row["kid_mortality_rate"] is None
    assert row["animals_sold"] == 1
    assert row["profit_per_animal_sold"] == 3000.0
    # 10 kg over 10 measured days (11-day span) ≈ 0.909 kg/day.
    assert row["avg_daily_gain_kg"] == pytest.approx(10.0 / 11.0, abs=0.01)

    # days bounds are enforced.
    bad = await client.get("/api/owner/benchmarks", params={"days": 4000}, headers=owner)
    assert bad.status_code == 422


async def _weigh_twice(farm_id: int, animal_id: int, start_kg: float, end_kg: float) -> None:
    """Two weighings 10 days apart inside the benchmark window."""
    from app.models import WeightRecord

    async with get_sessionmaker()() as db:
        db.add(
            WeightRecord(
                farm_id=farm_id,
                animal_id=animal_id,
                date=today() - timedelta(days=10),
                weight_kg=start_kg,
            )
        )
        db.add(WeightRecord(farm_id=farm_id, animal_id=animal_id, date=today(), weight_kg=end_kg))
        await db.commit()


async def test_owner_benchmarks_weight_gain_is_scoped_per_owned_farm(
    client: httpx.AsyncClient,
) -> None:
    """The weight-gain subquery filters to the owned farms and requires two
    weighings per animal: per-farm averages never cross-contaminate, an animal
    weighed once contributes nothing, and a third unowned farm's extreme gains
    cannot leak into the owner's figures."""
    owner = await owner_with_farm(client, email="bench-isolate@farm.in")
    farm_a = int(owner["X-Farm-Id"])
    farm_b_headers = await create_farm(client, owner, name="Gain Ranch")
    farm_b = int(farm_b_headers["X-Farm-Id"])

    gain_a = await make_animal(client, owner, tag="GAIN-A")
    once_a = await make_animal(client, owner, tag="GAIN-A-ONCE")
    gain_b = await make_animal(client, farm_b_headers, tag="GAIN-B")
    await _weigh_twice(farm_a, gain_a["id"], 20.0, 22.0)  # +2 kg over 11 days
    await _weigh_twice(farm_b, gain_b["id"], 20.0, 30.0)  # +10 kg over 11 days

    # A single weighing has no interval to gain over: HAVING count >= 2 keeps
    # it out of the average entirely (its 100 kg must not move farm A's figure).
    from app.models import WeightRecord

    async with get_sessionmaker()() as db:
        db.add(
            WeightRecord(
                farm_id=farm_a,
                animal_id=once_a["id"],
                date=today() - timedelta(days=5),
                weight_kg=100.0,
            )
        )
        await db.commit()

    # A different owner's farm with gains an order of magnitude beyond either
    # owned farm's: if the farm scoping ever regressed, its 480 kg would swamp
    # both owned averages.
    other = await owner_with_farm(client, email="bench-other@farm.in")
    farm_c = int(other["X-Farm-Id"])
    gain_c = await make_animal(client, other, tag="GAIN-C")
    await _weigh_twice(farm_c, gain_c["id"], 0.5, 480.0)

    resp = await client.get("/api/owner/benchmarks", params={"days": 90}, headers=owner)
    assert resp.status_code == 200, resp.text
    farms = {row["farm_id"]: row for row in resp.json()["farms"]}
    # Exactly the owned farms: the unowned farm never appears as a benchmark row.
    assert set(farms) == {farm_a, farm_b}
    assert farms[farm_a]["avg_daily_gain_kg"] == pytest.approx(2.0 / 11.0, abs=0.001)
    assert farms[farm_b]["avg_daily_gain_kg"] == pytest.approx(10.0 / 11.0, abs=0.001)


async def test_owner_benchmarks_profit_keeps_home_bred_sold_animals(
    client: httpx.AsyncClient,
) -> None:
    """A SOLD animal with no purchase price books margin = sale price: the
    coalesce(0) keeps home-bred stock in the average instead of dropping it."""
    owner = await owner_with_farm(client, email="bench-profit@farm.in")
    home_bred = await make_animal(client, owner, tag="BENCH-HOME")
    async with get_sessionmaker()() as db:
        animal = await db.get(Animal, home_bred["id"])
        assert animal is not None
        assert animal.purchase_price is None  # the fixture buys nothing
        animal.status = "SOLD"
        animal.status_date = today()
        animal.sale_price = Decimal("15000.00")
        await db.commit()

    resp = await client.get("/api/owner/benchmarks", params={"days": 90}, headers=owner)
    assert resp.status_code == 200, resp.text
    row = resp.json()["farms"][0]
    assert row["animals_sold"] == 1
    assert row["profit_per_animal_sold"] == 15000.0


async def test_owner_benchmarks_sold_counts_stay_per_farm_with_many_farms(
    client: httpx.AsyncClient,
) -> None:
    """Regression: the sales aggregate once referenced Farm.timezone without
    joining Farm, compiling to ``FROM animals, farms`` — every sold animal was
    counted once per farm row in the database and the sale window was evaluated
    against every farm's timezone. With several farms present, each owned farm
    must count only its own sales."""
    owner = await owner_with_farm(client, email="bench-sales@farm.in")
    farm_a = int(owner["X-Farm-Id"])
    farm_b_headers = await create_farm(client, owner, name="Sales Ranch")
    farm_b = int(farm_b_headers["X-Farm-Id"])
    # A second owner's farm so the cross join would multiply by three.
    other = await owner_with_farm(client, email="bench-sales-other@farm.in")

    sold_a = await make_animal(client, owner, tag="SALES-A")
    sold_b = await make_animal(client, farm_b_headers, tag="SALES-B")
    async with get_sessionmaker()() as db:
        for animal_row in (sold_a, sold_b):
            animal = await db.get(Animal, animal_row["id"])
            assert animal is not None
            animal.status = "SOLD"
            animal.status_date = today()
            animal.sale_price = Decimal("9000.00")
            animal.purchase_price = Decimal("4000.00")
        await db.commit()

    resp = await client.get("/api/owner/benchmarks", params={"days": 90}, headers=owner)
    assert resp.status_code == 200, resp.text
    farms = {row["farm_id"]: row for row in resp.json()["farms"]}
    assert set(farms) == {farm_a, farm_b}
    assert farms[farm_a]["animals_sold"] == 1
    assert farms[farm_b]["animals_sold"] == 1
    assert farms[farm_a]["profit_per_animal_sold"] == 5000.0
    assert farms[farm_b]["profit_per_animal_sold"] == 5000.0

    # The other owner sees only their own (empty) farm — never the sold stock.
    other_resp = await client.get("/api/owner/benchmarks", params={"days": 90}, headers=other)
    assert other_resp.status_code == 200, other_resp.text
    assert other_resp.json()["farms"][0]["animals_sold"] == 0
