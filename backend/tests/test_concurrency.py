"""Concurrency regression tests.

Check-then-act flows used to run with no row locks and no backing constraint,
so two in-flight requests could both pass the pre-check and double-apply the
side effects: two ANIMAL_SALE incomes for one goat, two open pregnancies for
one doe, lost feed-stock updates, duplicated recurring-task spawns, and a 500
IntegrityError on raced tag inserts.

Each test fires the request pair from two clients with ``asyncio.gather`` —
every request gets its own session/transaction, so the pair genuinely races
in PostgreSQL. Assertions target the FINAL state (one sale income, one open
breeding, one batch's worth of stock drawn, one spawned occurrence), which
must hold regardless of which request wins, plus the loser's status code.
"""

import asyncio
from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy import select, text

from app.db import get_sessionmaker
from app.main import create_app
from app.models import (
    Animal,
    AnimalStatus,
    BreedingRecord,
    BucketFeedSetting,
    Farm,
    FeedInventory,
    FeedRecipe,
    FeedRecipeLine,
    Role,
    Task,
    TaskCategory,
    TaskStatus,
    User,
)
from app.seed import (
    CONC,
    DRY_STOVER,
    FARM_INGREDIENTS,
    GREEN,
    ROLE_PRESETS,
    WET,
    seed_default_roles,
    seed_farm_inventory,
)
from app.utils import today, utcnow

from .conftest import owner_with_farm


def iso(d: date) -> str:
    return d.isoformat()


def second_client() -> httpx.AsyncClient:
    """A second client on the same app: its requests get their own DB
    sessions/connections, so a gather'd pair races for real."""
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    )


async def warm(client: httpx.AsyncClient, headers: dict) -> None:
    """Prime a fresh client (app/router/connection init) so its first timed
    request starts level with the fixture client's — without this the cold
    client reads only after the winner commits and the race never happens."""
    resp = await client.get("/api/animals", headers=headers)
    assert resp.status_code == 200, resp.text


async def wait_until_blocked(timeout_seconds: float = 10.0) -> None:
    """Poll until some session in the test DB is stuck waiting for a lock —
    i.e. the racing request's write is genuinely blocked on the holder's
    uncommitted row. Deterministic replacement for a wall-clock sleep (a
    sleep can fire before the request even reaches its INSERT under load)."""
    for _ in range(int(timeout_seconds / 0.01)):
        async with get_sessionmaker()() as db:
            blocked = (
                await db.execute(
                    text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname = current_database() AND wait_event_type = 'Lock'"
                    )
                )
            ).scalar_one()
        if blocked:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the racing request never blocked on the holder's lock")


async def wait_for_blocked_sessions(expected: int, timeout_seconds: float = 10.0) -> None:
    """Wait until an exact lock graph has queued at least ``expected`` sessions."""
    for _ in range(int(timeout_seconds / 0.01)):
        async with get_sessionmaker()() as db:
            blocked = (
                await db.execute(
                    text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname = current_database() AND wait_event_type = 'Lock'"
                    )
                )
            ).scalar_one()
        if blocked >= expected:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"expected {expected} lock-waiting sessions, saw fewer")


async def make_animal(
    client: httpx.AsyncClient,
    headers: dict,
    tag: str = "A-001",
    sex: str = "F",
    bucket: str = "FOUNDATION",
    **overrides: object,
) -> int:
    payload = {
        "tag_number": tag,
        "sex": sex,
        "source": "PURCHASED",
        "current_bucket": bucket,
    } | overrides
    if payload["source"] == "PURCHASED":
        payload.setdefault("historical_import_reason", "Existing-herd test fixture")
    if sex == "M" and bucket == "BREEDING":
        dob = today() - timedelta(days=800)
        payload.setdefault("date_of_birth", iso(dob))
        payload.setdefault("weight_kg", 30.0)
        payload.setdefault("weight_date", iso(dob))
    resp = await client.post("/api/animals", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def make_doe(client: httpx.AsyncClient, headers: dict, tag: str = "D-1") -> int:
    """A breeding-ready doe (>=10 months old, 26 kg entry weight, FOUNDATION)."""
    dob = today() - timedelta(days=800)
    return await make_animal(
        client,
        headers,
        tag=tag,
        sex="F",
        date_of_birth=iso(dob),
        weight_kg=26.0,
        weight_date=iso(dob),
    )


async def transactions(client: httpx.AsyncClient, headers: dict) -> list[dict]:
    resp = await client.get("/api/finance", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["transactions"]


async def inventory(client: httpx.AsyncClient, headers: dict) -> list[dict]:
    resp = await client.get("/api/feeding/inventory", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def task_tabs(client: httpx.AsyncClient, headers: dict) -> dict:
    resp = await client.get("/api/tasks", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def all_tasks(tabs: dict) -> list[dict]:
    return tabs["today"] + tabs["overdue"] + tabs["upcoming"] + tabs["awaiting"] + tabs["completed"]


# ---------------------------------------------------------------------------
# Startup seeding — two workers cannot duplicate editable preset roles
# ---------------------------------------------------------------------------
async def test_concurrent_role_seed_serializes_on_farm_row(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(
        client,
        email="concurrent-role-seed@farm.in",
        farm_name="Concurrent Role Seed Farm",
    )
    farm_id = int(headers["X-Farm-Id"])

    # Simulate a legacy/partial farm.  The API-created presets are removed in
    # one committed transaction before two app workers try to repair them.
    async with get_sessionmaker()() as db:
        roles = list((await db.execute(select(Role).where(Role.farm_id == farm_id))).scalars())
        for role in roles:
            await db.delete(role)
        await db.commit()

    async with get_sessionmaker()() as holder:
        await holder.execute(select(Farm.id).where(Farm.id == farm_id).with_for_update())

        async def competing_seed() -> None:
            async with get_sessionmaker()() as contender:
                await seed_default_roles(contender, farm_id)
                await contender.commit()

        contender_task = asyncio.create_task(competing_seed())
        await wait_until_blocked()
        await seed_default_roles(holder, farm_id)
        await holder.commit()
        await contender_task

    async with get_sessionmaker()() as db:
        seeded = list((await db.execute(select(Role).where(Role.farm_id == farm_id))).scalars())
    assert len(seeded) == len(ROLE_PRESETS)
    assert {role.code for role in seeded} == {preset["code"] for preset in ROLE_PRESETS}


# ---------------------------------------------------------------------------
# B4.1 — two concurrent sales of the same animal book exactly one income
# ---------------------------------------------------------------------------
async def test_concurrent_double_sale_books_one_income(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    aid = await make_animal(client, owner)
    payload = {"new_status": "SOLD", "date": iso(today()), "sale_price": 5000}
    async with second_client() as other:
        await warm(other, owner)
        r1, r2 = await asyncio.gather(
            client.post(f"/api/animals/{aid}/status", json=payload, headers=owner),
            other.post(f"/api/animals/{aid}/status", json=payload, headers=owner),
        )
    # One winner; the loser takes the row lock, re-reads SOLD and gets the
    # same 400 a sequential replay gets.
    assert sorted([r1.status_code, r2.status_code]) == [200, 400]
    sales = [t for t in await transactions(client, owner) if t["category"] == "ANIMAL_SALE"]
    assert len(sales) == 1
    assert sales[0]["amount"] == 5000
    resp = await client.get(f"/api/animals/{aid}", headers=owner)
    assert resp.json()["animal"]["status"] == "SOLD"


# ---------------------------------------------------------------------------
# B4.2 — two concurrent breedings of the same doe leave one open record
# ---------------------------------------------------------------------------
async def test_concurrent_double_breeding_leaves_one_open_record(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner)
    buck = await make_animal(client, owner, tag="B-1", sex="M", bucket="BREEDING")
    payload = {"doe_id": doe, "buck_id": buck, "breeding_date": iso(today())}
    async with second_client() as other:
        await warm(other, owner)
        r1, r2 = await asyncio.gather(
            client.post("/api/breeding", json=payload, headers=owner),
            other.post("/api/breeding", json=payload, headers=owner),
        )
    # Exactly one winner. The loser is rejected cleanly one of two ways,
    # depending on when its pre-checks ran: 400 "not eligible" (the doe is
    # already off the candidate list — the same code a sequential retry gets)
    # or 409 from the uq_breeding_open_pregnancy partial UNIQUE.
    codes = sorted([r1.status_code, r2.status_code])
    assert codes[0] == 201 and codes[1] in (400, 409), codes
    resp = await client.get("/api/breeding", headers=owner)
    records = [r for r in resp.json()["records"] if r["doe_id"] == doe]
    assert len(records) == 1
    assert records[0]["outcome"] == "PENDING"


async def test_double_breeding_blocked_insert_rejected_cleanly(
    client: httpx.AsyncClient,
) -> None:
    """Deterministic race path: an uncommitted PENDING row already holds the
    doe's slot — and, via the FK, a key-share lock on the doe row itself.
    create_breeding locks the doe FOR UPDATE before inserting,
    so the request blocks on THAT lock (not on uq_breeding_open_pregnancy as
    before the lock existed), re-reads the committed PENDING row after the
    holder commits, and fails eligibility with the same 400 a sequential
    retry gets — never a 500, and exactly one record survives."""
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner)
    buck = await make_animal(client, owner, tag="B-1", sex="M", bucket="BREEDING")
    farm_id = int(owner["X-Farm-Id"])
    holder = get_sessionmaker()()
    holder.add(BreedingRecord(farm_id=farm_id, doe_id=doe, buck_id=buck, breeding_date=today()))
    await holder.flush()

    # Commit the holder only once the request is genuinely blocked on the
    # doe's row lock — a wall-clock sleep could fire before the request
    # reached its FOR UPDATE under load.
    request = asyncio.create_task(
        client.post(
            "/api/breeding",
            json={"doe_id": doe, "buck_id": buck, "breeding_date": iso(today())},
            headers=owner,
        )
    )
    try:
        await wait_until_blocked()
        await holder.commit()
        resp = await request
    finally:
        # Never leak a lock-holding session: a failure above would leave the
        # holder's uncommitted row lock behind, and the autouse teardown's
        # TRUNCATE would block on it indefinitely (no timeout).
        await holder.rollback()
        await holder.close()
        if not request.done():
            request.cancel()
    assert resp.status_code == 400, resp.text
    assert "not eligible" in resp.json()["detail"]
    resp = await client.get("/api/breeding", headers=owner)
    records = [r for r in resp.json()["records"] if r["doe_id"] == doe]
    assert len(records) == 1  # only the holder's row


# ---------------------------------------------------------------------------
# B4.3 — concurrent mixes can never draw the same stock twice
# ---------------------------------------------------------------------------
async def test_concurrent_mixes_never_oversell_stock(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    for item in await inventory(client, owner):
        resp = await client.post(
            f"/api/feeding/inventory/{item['id']}/add",
            json={"qty_kg": 100},
            headers=owner,
        )
        assert resp.status_code == 200, resp.text
    # 200 kg batches: the biggest line (green fodder, 30 kg/100 kg) draws
    # 60 kg per mix — two naive successes would claim 120 kg from 100 kg.
    resp = await client.get("/api/feeding/recipes", headers=owner)
    recipe = next(r for r in resp.json()["recipes"] if r["code"] == "FATTENING_50_50")
    payload = {"recipe_code": "FATTENING_50_50", "batch_kg": 200}
    async with second_client() as other:
        await warm(other, owner)
        r1, r2 = await asyncio.gather(
            client.post("/api/feeding/mix", json=payload, headers=owner),
            other.post("/api/feeding/mix", json=payload, headers=owner),
        )
    # One winner; the loser re-reads the locked rows and gets the documented
    # shortage error.
    assert sorted([r1.status_code, r2.status_code]) == [200, 400]
    loser = r1 if r1.status_code == 400 else r2
    assert "need" in loser.json()["detail"]
    # Exactly one batch was drawn, and no balance went negative.
    stock = {i["ingredient"]: i["qty_on_hand"] for i in await inventory(client, owner)}
    for line in recipe["lines"]:
        needed = round(line["kg_per_100kg"] / 100.0 * 200, 3)
        assert stock[line["ingredient"]] == round(100 - needed, 3), line["ingredient"]
        assert stock[line["ingredient"]] >= 0


async def test_concurrent_dry_roughage_dispenses_never_oversell_inventory(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    items = {row["ingredient"]: row for row in await inventory(client, owner)}
    stocked = await client.post(
        f"/api/feeding/inventory/{items[DRY_STOVER]['id']}/add",
        json={"qty_kg": 5.0},
        headers=owner,
    )
    assert stocked.status_code == 200, stocked.text
    payload = {
        "bucket": "QUARANTINE",
        "shift": "MORNING",
        "recipe_code": "DRY_ROUGHAGE_ONLY",
        "qty_kg": 5.0,
    }
    async with second_client() as other:
        await warm(other, owner)
        one, two = await asyncio.gather(
            client.post("/api/feeding/dispense", json=payload, headers=owner),
            other.post("/api/feeding/dispense", json=payload, headers=owner),
        )

    assert sorted([one.status_code, two.status_code]) == [201, 400]
    loser = one if one.status_code == 400 else two
    assert loser.json()["detail"] == ("Dry jowar stover: need 5.000 kg, have 0.000 kg")
    after = {row["ingredient"]: row for row in await inventory(client, owner)}
    assert after[DRY_STOVER]["qty_on_hand"] == 0.0
    plan = await client.get("/api/feeding/plan", headers=owner)
    assert plan.status_code == 200, plan.text
    assert plan.json()["records_total"] == 1


# ---------------------------------------------------------------------------
# B4.4 — a double completion applies the side effects exactly once
# ---------------------------------------------------------------------------
async def test_concurrent_double_complete_spawns_one_occurrence(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    resp = await client.post(
        "/api/tasks",
        json={"title": "Daily sweep", "due_date": iso(today()), "recur_days": 1},
        headers=owner,
    )
    assert resp.status_code == 201, resp.text
    task_id = resp.json()["id"]
    async with second_client() as other:
        await warm(other, owner)
        r1, r2 = await asyncio.gather(
            client.post(f"/api/tasks/{task_id}/complete", headers=owner),
            other.post(f"/api/tasks/{task_id}/complete", headers=owner),
        )
    # The loser re-reads DONE after the lock and gets "Task is not pending".
    assert sorted([r1.status_code, r2.status_code]) == [200, 400]
    tabs = await task_tabs(client, owner)
    mine = [t for t in all_tasks(tabs) if t["title"] == "Daily sweep"]
    original = next(t for t in mine if t["id"] == task_id)
    assert original["status"] == "DONE"
    spawned = [t for t in mine if t["id"] != task_id and t["status"] == "PENDING"]
    assert len(spawned) == 1  # the recurring series spawned exactly once
    assert spawned[0]["due_date"] == iso(today() + timedelta(days=1))


# ---------------------------------------------------------------------------
# B5.1 — a raced tag insert on create_animal answers like the pre-check
# ---------------------------------------------------------------------------
async def test_create_animal_tag_race_returns_pre_check_400(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    # An uncommitted insert already holds the tag: the request's pre-check
    # can't see it (READ COMMITTED), so the request races into
    # uq_animal_tag_per_farm and must get the pre-check's 400, never a 500.
    holder = get_sessionmaker()()
    holder.add(
        Animal(
            farm_id=farm_id,
            tag_number="RACE-1",
            sex="F",
            source="PURCHASED",
            current_bucket="FOUNDATION",
        )
    )
    await holder.flush()

    # Commit the holder only once the request's INSERT is genuinely blocked
    # on the unique index — a wall-clock sleep can fire before the request
    # reaches its INSERT under load; then the holder commits first, the
    # request fails at the pre-check with the same 400, and the raced-INSERT
    # → IntegrityError → 400 translation this test guards is never exercised.
    request = asyncio.create_task(
        client.post(
            "/api/animals",
            json={
                "tag_number": "RACE-1",
                "sex": "F",
                "source": "PURCHASED",
                "current_bucket": "FOUNDATION",
            },
            headers=owner,
        )
    )
    try:
        await wait_until_blocked()
        await holder.commit()
        resp = await request
    finally:
        # Never leak a lock-holding session (see the B4.2 test above).
        await holder.rollback()
        await holder.close()
        if not request.done():
            request.cancel()
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "Tag 'RACE-1' already exists on this farm."
    resp = await client.get("/api/animals", headers=owner, params={"status": "ACTIVE"})
    assert [a["tag_number"] for a in resp.json()["animals"]] == ["RACE-1"]  # only the holder's row


# ---------------------------------------------------------------------------
# B5.2 — the cryptographically unlikely generated-tag collision still gets a
# clean 409 (rolled back wholesale), and the retry succeeds on a fresh id
# ---------------------------------------------------------------------------
async def test_purchase_batch_tag_collision_409_then_retry(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client)
    # Force the normally unguessable nonce so the IntegrityError backstop can
    # be exercised deterministically rather than relying on a 2^-48 event.
    monkeypatch.setattr("app.services.purchases.secrets.token_hex", lambda _n: "deadbeefcafe")
    await make_animal(client, owner, tag="B1-deadbeefcafe-0001")
    payload = {"date": iso(today()), "count": 2, "create_animals": True}
    resp = await client.post("/api/purchases/new", json=payload, headers=owner)
    assert resp.status_code == 409, resp.text  # never a 500 IntegrityError
    assert "tag" in resp.json()["detail"].lower()
    resp = await client.get("/api/purchases", headers=owner)
    assert resp.json()["batches"] == []  # nothing half-created

    resp = await client.post("/api/purchases/new", json=payload, headers=owner)
    assert resp.status_code == 201, resp.text  # retry gets a fresh batch id
    batch_id = resp.json()["id"]
    resp = await client.get(f"/api/purchases/{batch_id}", headers=owner)
    tags = [a["tag_number"] for a in resp.json()["animals"]]
    assert tags == [
        f"B{batch_id}-deadbeefcafe-0001",
        f"B{batch_id}-deadbeefcafe-0002",
    ]


# ---------------------------------------------------------------------------
# Concurrent mixes of recipes with reversed ingredient order never deadlock
# ---------------------------------------------------------------------------
async def test_concurrent_mixes_of_reversed_recipes_never_deadlock(
    client: httpx.AsyncClient,
) -> None:
    """Two recipes sharing ingredients in opposite line orders: locking stock
    rows in recipe-line order inverts the FOR UPDATE order between the two
    transactions (AB vs BA) → PostgreSQL DeadlockDetected → 500. The mix now
    locks stock rows in a canonical ingredient-name order, so the pair
    serializes and both mixes succeed."""
    owner = await owner_with_farm(client)
    async with get_sessionmaker()() as db:
        recipe_ab = FeedRecipe(code="RACE_AB", name="Race A-B", description=None)
        recipe_ab.lines = [
            FeedRecipeLine(ingredient=GREEN, kg_per_100kg=50, category=WET),
            FeedRecipeLine(ingredient="Crushed maize", kg_per_100kg=50, category=CONC),
        ]
        recipe_ba = FeedRecipe(code="RACE_BA", name="Race B-A", description=None)
        recipe_ba.lines = [  # same ingredients, opposite order
            FeedRecipeLine(ingredient="Crushed maize", kg_per_100kg=50, category=CONC),
            FeedRecipeLine(ingredient=GREEN, kg_per_100kg=50, category=WET),
        ]
        db.add_all([recipe_ab, recipe_ba])
        await db.commit()
    items = {i["ingredient"]: i for i in await inventory(client, owner)}
    for ingredient in (GREEN, "Crushed maize"):
        resp = await client.post(
            f"/api/feeding/inventory/{items[ingredient]['id']}/add",
            json={"qty_kg": 1000},
            headers=owner,
        )
        assert resp.status_code == 200, resp.text

    async with second_client() as other:
        await warm(other, owner)
        for _ in range(20):
            r1, r2 = await asyncio.gather(
                client.post(
                    "/api/feeding/mix",
                    json={"recipe_code": "RACE_AB", "batch_kg": 10},
                    headers=owner,
                ),
                other.post(
                    "/api/feeding/mix",
                    json={"recipe_code": "RACE_BA", "batch_kg": 10},
                    headers=owner,
                ),
            )
            assert r1.status_code == 200, r1.text  # never a deadlock 500
            assert r2.status_code == 200, r2.text
    # 40 mixes × 5 kg per ingredient drawn from 1000 kg — no lost updates.
    items = {i["ingredient"]: i for i in await inventory(client, owner)}
    assert items[GREEN]["qty_on_hand"] == 800
    assert items["Crushed maize"]["qty_on_hand"] == 800


# ---------------------------------------------------------------------------
# A committed completion must survive a concurrent animal-status skip
# ---------------------------------------------------------------------------
async def test_skip_never_overwrites_a_committed_completion(
    client: httpx.AsyncClient,
) -> None:
    """Animal deactivation no longer rewrites task rows synchronously.

    A scripted completion may hold a task lock while the status transition
    commits independently; the inactive-animal visibility/cleanup path must
    not overwrite the later DONE commit or introduce the old lock inversion.
    """
    owner = await owner_with_farm(client)
    aid = await make_animal(client, owner)
    farm_id = int(owner["X-Farm-Id"])
    # An animal-linked duty, straight into the DB: the API only creates
    # farm-level manual tasks, and the auto animal-linked ones are all
    # form-linked (completing those means posting their form, not this race).
    async with get_sessionmaker()() as db:
        task = Task(
            farm_id=farm_id,
            title="Footbath",
            due_date=today(),
            category=TaskCategory.OTHER.value,
            animal_id=aid,
            auto_generated=False,
        )
        db.add(task)
        await db.commit()
        task_id = task.id

    # The completer: lock the task, apply DONE + its side effects, but hold
    # the commit so the skipper races the locked row.
    completer = get_sessionmaker()()
    row = (
        await completer.execute(select(Task).where(Task.id == task_id).with_for_update())
    ).scalar_one()
    row.status = TaskStatus.DONE.value
    row.completed_at = utcnow()
    await completer.flush()

    skip_request = asyncio.create_task(
        client.post(f"/api/animals/{aid}/status", json={"new_status": "SOLD"}, headers=owner)
    )
    try:
        # Status writes deliberately do not wait on task rows.  Pending duties
        # become invisible immediately and bounded cleanup handles leftovers.
        resp = await asyncio.wait_for(skip_request, timeout=5)
        await completer.commit()
    finally:
        # Never leak a lock-holding session (see the B4.2 test above).
        await completer.rollback()
        await completer.close()
        if not skip_request.done():
            skip_request.cancel()
    assert resp.status_code == 200, resp.text

    async with get_sessionmaker()() as db:
        final = await db.get(Task, task_id)
        assert final is not None
        # THE invariant: never DONE-with-side-effects-then-SKIPPED.
        assert final.status == TaskStatus.DONE.value
        assert final.completed_at is not None
    resp = await client.get(f"/api/animals/{aid}", headers=owner)
    assert resp.json()["animal"]["status"] == "SOLD"  # the sale still went through


# ---------------------------------------------------------------------------
# Shared breeding-flow helpers for the pregnancy state-machine races below
# ---------------------------------------------------------------------------
async def bred_doe_with_record(
    client: httpx.AsyncClient,
    headers: dict,
    tag: str = "D-US",
    breeding_date: date | None = None,
) -> tuple[int, dict]:
    """A breeding-ready doe + buck with one PENDING breeding (via the API)."""
    doe = await make_doe(client, headers, tag=tag)
    buck = await make_animal(client, headers, tag=f"{tag}-BUCK", sex="M", bucket="BREEDING")
    resp = await client.post(
        "/api/breeding",
        json={
            "doe_id": doe,
            "buck_id": buck,
            # The default ultrasound submission records the farm-local date,
            # so make an implicit-result fixture eligible for the +32-day
            # planned check rather than fabricating a future result date.
            "breeding_date": iso(breeding_date or today() - timedelta(days=35)),
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return doe, resp.json()


async def confirm_pregnancy(client: httpx.AsyncClient, headers: dict, br_id: int) -> None:
    resp = await client.post(
        f"/api/breeding/{br_id}/ultrasound",
        json={"pregnant": True, "kid_count": 2},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text


async def breeding_follow_ups(client: httpx.AsyncClient, headers: dict, br_id: int) -> list[dict]:
    """The pregnancy follow-up duties spawned for a breeding record."""
    tabs = await task_tabs(client, headers)
    return [
        t
        for t in all_tasks(tabs)
        if t["breeding_record_id"] == br_id and t["category"] != "ULTRASOUND"
    ]


# ---------------------------------------------------------------------------
# 2-1 — concurrent ultrasound submissions apply the follow-ups exactly once
# ---------------------------------------------------------------------------
async def test_concurrent_ultrasound_submissions_apply_once(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    doe, br = await bred_doe_with_record(client, owner)
    payload = {"pregnant": True, "kid_count": 2}
    async with second_client() as other:
        await warm(other, owner)
        r1, r2 = await asyncio.gather(
            client.post(f"/api/breeding/{br['id']}/ultrasound", json=payload, headers=owner),
            other.post(f"/api/breeding/{br['id']}/ultrasound", json=payload, headers=owner),
        )
    # The breeding row is locked FOR UPDATE: the loser re-reads the committed
    # outcome and gets the same 409 a sequential replay gets.
    assert sorted([r1.status_code, r2.status_code]) == [200, 409]
    # ET+TT vaccine + Move to DELIVERY + Kidding due — exactly once, not twice.
    assert len(await breeding_follow_ups(client, owner, br["id"])) == 3
    resp = await client.get(f"/api/animals/{doe}", headers=owner)
    moves = [m for m in resp.json()["moves"] if m["to_bucket"] == "PREGNANCY_EARLY"]
    assert len(moves) == 1


async def test_concurrent_mixed_ultrasound_results_stay_consistent(
    client: httpx.AsyncClient,
) -> None:
    """A pregnant + a not-pregnant submission race: one wins outright, the
    loser 409s — never FAILED-with-pregnancy-tasks divergent state."""
    owner = await owner_with_farm(client)
    _doe, br = await bred_doe_with_record(client, owner)
    async with second_client() as other:
        await warm(other, owner)
        r1, r2 = await asyncio.gather(
            client.post(
                f"/api/breeding/{br['id']}/ultrasound",
                json={"pregnant": True, "kid_count": 2},
                headers=owner,
            ),
            other.post(
                f"/api/breeding/{br['id']}/ultrasound",
                json={"pregnant": False},
                headers=owner,
            ),
        )
    assert sorted([r1.status_code, r2.status_code]) == [200, 409]
    resp = await client.get(f"/api/breeding/{br['id']}", headers=owner)
    outcome = resp.json()["outcome"]
    follow_ups = await breeding_follow_ups(client, owner, br["id"])
    if outcome == "CONFIRMED_PREGNANT":
        assert len(follow_ups) == 3
    else:
        assert outcome == "FAILED"
        assert follow_ups == []  # no pregnancy tasks for a failed cycle


# ---------------------------------------------------------------------------
# 2-2 — a double abort runs mark_aborted exactly once
# ---------------------------------------------------------------------------
async def test_concurrent_double_abort_aborts_once(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    doe, br = await bred_doe_with_record(client, owner, breeding_date=today() - timedelta(days=40))
    await confirm_pregnancy(client, owner, br["id"])
    loss = {"loss_date": iso(today()), "cause": "UNKNOWN", "notes": "Concurrent loss"}
    async with second_client() as other:
        await warm(other, owner)
        r1, r2 = await asyncio.gather(
            client.post(f"/api/breeding/{br['id']}/abort", json=loss, headers=owner),
            other.post(f"/api/breeding/{br['id']}/abort", json=loss, headers=owner),
        )
    # The loser takes the breeding-row lock, re-reads ABORTED and 409s.
    assert sorted([r1.status_code, r2.status_code]) == [200, 409]
    resp = await client.get(f"/api/breeding/{br['id']}", headers=owner)
    assert resp.json()["outcome"] == "ABORTED"
    # Exactly one "Pregnancy aborted" move and one round of task skips.
    resp = await client.get(f"/api/animals/{doe}", headers=owner)
    aborts = [m for m in resp.json()["moves"] if m["reason"] == "Pregnancy aborted"]
    assert len(aborts) == 1
    follow_ups = await breeding_follow_ups(client, owner, br["id"])
    assert len(follow_ups) == 3
    assert {t["status"] for t in follow_ups} == {"SKIPPED"}


# ---------------------------------------------------------------------------
# 2-3 — kidding vs abort: the pregnancy never ends ABORTED with live kids
# ---------------------------------------------------------------------------
async def test_kidding_vs_abort_never_aborted_with_live_kids(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    _doe, br = await bred_doe_with_record(
        client, owner, breeding_date=today() - timedelta(days=150)
    )
    await confirm_pregnancy(client, owner, br["id"])
    kidding_payload = {
        "breeding_record_id": br["id"],
        "date": iso(today()),
        "ease": "NORMAL",
        "kids": [{"sex": "M"}],
    }
    loss = {"loss_date": iso(today()), "cause": "UNKNOWN", "notes": "Concurrent loss"}
    async with second_client() as other:
        await warm(other, owner)
        r_kid, r_abort = await asyncio.gather(
            client.post("/api/kidding", json=kidding_payload, headers=owner),
            other.post(f"/api/breeding/{br['id']}/abort", json=loss, headers=owner),
        )
    # Both flows lock the doe then the breeding row: they serialize, and the
    # loser re-reads the committed state and fails its own state guard.
    codes = sorted([r_kid.status_code, r_abort.status_code])
    assert codes in ([200, 400], [201, 409]), codes
    resp = await client.get(f"/api/breeding/{br['id']}", headers=owner)
    final = resp.json()
    if r_kid.status_code == 201:
        assert final["has_kidding"] is True
        assert final["outcome"] == "CONFIRMED_PREGNANT"
    else:
        assert final["outcome"] == "ABORTED"
        assert final["has_kidding"] is False
        # No born kids from an aborted pregnancy.
        resp = await client.get("/api/animals", headers=owner, params={"q": "D-US-K"})
        assert resp.json()["animals"] == []


async def test_kidding_locks_buck_and_doe_before_breeding_record(
    client: httpx.AsyncClient,
) -> None:
    """Kidding and a forged concurrent re-breeding share one parent lock order.

    The breeding row holder is a deterministic barrier.  Historically the
    kidding request held DOE then waited at the barrier, while re-breeding
    held the lower-id BUCK and waited on DOE.  Releasing the barrier made the
    kid insert request BUCK KEY SHARE and PostgreSQL aborted the cycle as a
    deadlock/500.  Kidding now locks both parents by ascending id before the
    breeding row, so re-breeding waits without holding either parent.
    """
    owner = await owner_with_farm(client, email="kidding-parent-locks@farm.in")
    buck = await make_animal(client, owner, tag="K-LOCK-B", sex="M", bucket="BREEDING")
    doe = await make_doe(client, owner, tag="K-LOCK-D")
    assert buck < doe  # the ordering needed by the historical deadlock
    breeding_date = today() - timedelta(days=150)
    created = await client.post(
        "/api/breeding",
        json={"doe_id": doe, "buck_id": buck, "breeding_date": iso(breeding_date)},
        headers=owner,
    )
    assert created.status_code == 201, created.text
    breeding_id = created.json()["id"]
    await confirm_pregnancy(client, owner, breeding_id)

    holder = get_sessionmaker()()
    await holder.execute(
        select(BreedingRecord.id).where(BreedingRecord.id == breeding_id).with_for_update()
    )
    kidding_request: asyncio.Task[httpx.Response] | None = None
    rebreed_request: asyncio.Task[httpx.Response] | None = None
    try:
        async with second_client() as other:
            await warm(other, owner)
            kidding_request = asyncio.create_task(
                client.post(
                    "/api/kidding",
                    json={
                        "breeding_record_id": breeding_id,
                        "date": iso(today()),
                        "ease": "NORMAL",
                        "kids": [{"sex": "F"}],
                    },
                    headers=owner,
                )
            )
            await wait_until_blocked()  # parent locks acquired; waiting on breeding row
            rebreed_request = asyncio.create_task(
                other.post(
                    "/api/breeding",
                    json={"doe_id": doe, "buck_id": buck, "breeding_date": iso(today())},
                    headers=owner,
                )
            )
            await wait_for_blocked_sessions(2)
            await holder.commit()
            kidding_response, rebreed_response = await asyncio.wait_for(
                asyncio.gather(kidding_request, rebreed_request),
                timeout=10,
            )
    finally:
        await holder.rollback()
        await holder.close()
        for request in (kidding_request, rebreed_request):
            if request is not None and not request.done():
                request.cancel()

    assert kidding_response.status_code == 201, kidding_response.text
    assert rebreed_response.status_code == 400, rebreed_response.text


# ---------------------------------------------------------------------------
# 2-4 — completing the delivery-move duty vs selling the doe never deadlocks
# ---------------------------------------------------------------------------
async def test_complete_delivery_move_vs_sell_never_deadlocks(
    client: httpx.AsyncClient,
) -> None:
    """complete_task used to lock task → animal while change_status locks
    animal → tasks: this pair deadlocked into a 500. Both now take the
    animal lock first, so they serialize cleanly — several rounds, because
    the deadlock needed an unlucky interleave, not a wrong final state."""
    owner = await owner_with_farm(client)
    async with second_client() as other:
        await warm(other, owner)
        for round_ in range(5):
            tag = f"D-DL{round_}"
            doe, br = await bred_doe_with_record(
                client, owner, tag=tag, breeding_date=today() - timedelta(days=136)
            )
            await confirm_pregnancy(client, owner, br["id"])
            # EKD = today + 14, so the "Move to DELIVERY" duty (EKD − 15)
            # fell due yesterday and is completable.
            move_task = next(
                t
                for t in await breeding_follow_ups(client, owner, br["id"])
                if t["category"] == "BUCKET_MOVE" and t["status"] == "PENDING"
            )
            r_complete, r_sell = await asyncio.gather(
                client.post(f"/api/tasks/{move_task['id']}/complete", headers=owner),
                other.post(
                    f"/api/animals/{doe}/status", json={"new_status": "SOLD"}, headers=owner
                ),
            )
            assert r_complete.status_code != 500, r_complete.text
            assert r_sell.status_code != 500, r_sell.text
            assert sorted([r_complete.status_code, r_sell.status_code]) in (
                [200, 200],  # the duty completed, then the sale went through
                [200, 400],  # the sale won; the duty was skipped under it
            )
            tabs = await task_tabs(client, owner)
            final_task = next(t for t in all_tasks(tabs) if t["id"] == move_task["id"])
            assert final_task["status"] in ("DONE", "SKIPPED")


# ---------------------------------------------------------------------------
# 2-5 — concurrent first-time saves of a bucket ration never 500
# ---------------------------------------------------------------------------
async def test_concurrent_first_feed_setting_save_never_500s(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    async with second_client() as other:
        await warm(other, owner)
        r1, r2 = await asyncio.gather(
            client.post(
                "/api/feeding/settings",
                json={"bucket": "FOUNDATION", "daily_kg_per_head": 1.5},
                headers=owner,
            ),
            other.post(
                "/api/feeding/settings",
                json={"bucket": "FOUNDATION", "daily_kg_per_head": 2.5},
                headers=owner,
            ),
        )
    # The loser's INSERT ... ON CONFLICT DO UPDATE waits for the winner and
    # then updates its row — no IntegrityError, both succeed.
    assert r1.status_code == 204, r1.text
    assert r2.status_code == 204, r2.text
    async with get_sessionmaker()() as db:
        rows = (await db.execute(select(BucketFeedSetting))).scalars().all()
    assert len(rows) == 1
    assert rows[0].daily_kg_per_head in (1.5, 2.5)  # last writer wins


async def test_feed_setting_blocked_insert_upserts_cleanly(
    client: httpx.AsyncClient,
) -> None:
    """Deterministic form of the race above: an uncommitted INSERT already
    holds the bucket's row. The request's INSERT ... ON CONFLICT DO UPDATE
    blocks on it, then applies the update once the holder commits — 204,
    never an IntegrityError 500, exactly one row."""
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    holder = get_sessionmaker()()
    holder.add(BucketFeedSetting(farm_id=farm_id, bucket="FOUNDATION", daily_kg_per_head=9.9))
    await holder.flush()

    request = asyncio.create_task(
        client.post(
            "/api/feeding/settings",
            json={"bucket": "FOUNDATION", "daily_kg_per_head": 1.5},
            headers=owner,
        )
    )
    try:
        await wait_until_blocked()  # the upsert is genuinely stuck on the holder's row
        await holder.commit()
        resp = await request
    finally:
        await holder.rollback()
        await holder.close()
        if not request.done():
            request.cancel()
    assert resp.status_code == 204, resp.text
    async with get_sessionmaker()() as db:
        rows = (await db.execute(select(BucketFeedSetting))).scalars().all()
    assert len(rows) == 1
    assert rows[0].daily_kg_per_head == 1.5  # the request's value won the upsert


async def test_farm_inventory_seed_waits_on_conflict_and_repairs_remaining_rows() -> None:
    """A concurrent seed must preserve the winning row and still add all others."""
    async with get_sessionmaker()() as db:
        owner = User(email="seed-race-owner@farm.in", password_hash="argon2-placeholder")
        db.add(owner)
        await db.flush()
        farm = Farm(name="Seed Race Farm", owner_id=owner.id)
        db.add(farm)
        await db.commit()
        farm_id = farm.id

    ingredient, category = FARM_INGREDIENTS[0]
    holder = get_sessionmaker()()
    holder.add(
        FeedInventory(
            farm_id=farm_id,
            ingredient=ingredient,
            category=category,
            unit="kg",
            qty_on_hand=42.125,
            reorder_level=9.0,
        )
    )
    await holder.flush()

    async def seed_concurrently() -> None:
        async with get_sessionmaker()() as db:
            await seed_farm_inventory(db, farm_id)
            await db.commit()

    seed_task = asyncio.create_task(seed_concurrently())
    try:
        await wait_until_blocked()
        await holder.commit()
        await seed_task
    finally:
        await holder.rollback()
        await holder.close()
        if not seed_task.done():
            seed_task.cancel()

    async with get_sessionmaker()() as db:
        rows = list(
            (
                await db.execute(
                    select(FeedInventory)
                    .where(FeedInventory.farm_id == farm_id)
                    .order_by(FeedInventory.ingredient)
                )
            ).scalars()
        )
    assert len(rows) == len(FARM_INGREDIENTS)
    assert {row.ingredient for row in rows} == {name for name, _category in FARM_INGREDIENTS}
    preserved = next(row for row in rows if row.ingredient == ingredient)
    assert preserved.qty_on_hand == 42.125
    assert preserved.reorder_level == 9.0


# ---------------------------------------------------------------------------
# 2-6 — the health form's linked-duty completion is row-locked
# ---------------------------------------------------------------------------
async def test_health_form_double_complete_spawns_one_occurrence(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    aid = await make_animal(client, owner)
    # Public manual-task creation intentionally permits only safe categories.
    # This direct legacy fixture keeps coverage of the health-form recurrence
    # race without reopening forged manual VACCINE duties.
    async with get_sessionmaker()() as db:
        task = Task(
            farm_id=int(owner["X-Farm-Id"]),
            title="Herd FMD round",
            due_date=today(),
            category=TaskCategory.VACCINE.value,
            animal_id=aid,
            recur_days=1,
            recurring_series_id="health-form-race-series",
            auto_generated=False,
        )
        db.add(task)
        await db.commit()
        task_id = task.id
    payload = {
        "animal_id": aid,
        "type": "VACCINE",
        "product_name": "FMD",
        "disease_target": "FMD",
        "task_id": task_id,
    }
    async with second_client() as other:
        await warm(other, owner)
        r1, r2 = await asyncio.gather(
            client.post("/api/health/events", json=payload, headers=owner),
            other.post("/api/health/events", json=payload, headers=owner),
        )
    # Strict task correlation permits exactly one event per duty.  The row
    # lock makes the losing request re-read DONE and return a conflict.
    assert sorted([r1.status_code, r2.status_code]) == [201, 409]
    tabs = await task_tabs(client, owner)
    mine = [t for t in all_tasks(tabs) if t["title"] == "Herd FMD round"]
    original = next(t for t in mine if t["id"] == task_id)
    assert original["status"] == "DONE"
    spawned = [t for t in mine if t["id"] != task_id and t["status"] == "PENDING"]
    assert len(spawned) == 1  # the recurring series spawned exactly once


# ---------------------------------------------------------------------------
# 2-7 — a raced kid-tag insert answers with the tag 400, not the kidding 409
# ---------------------------------------------------------------------------
async def test_kidding_kid_tag_race_returns_tag_400(client: httpx.AsyncClient) -> None:
    """An uncommitted insert already holds the kid's explicit tag: the
    request's tag pre-check can't see it, races into uq_animal_tag_per_farm
    and must get the tag pre-check's 400 — never the mislabeled 409
    'already has a kidding record'."""
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    _doe, br = await bred_doe_with_record(
        client, owner, breeding_date=today() - timedelta(days=150)
    )
    await confirm_pregnancy(client, owner, br["id"])
    holder = get_sessionmaker()()
    holder.add(
        Animal(
            farm_id=farm_id,
            tag_number="KID-RACE",
            sex="F",
            source="PURCHASED",
            current_bucket="FOUNDATION",
        )
    )
    await holder.flush()

    request = asyncio.create_task(
        client.post(
            "/api/kidding",
            json={
                "breeding_record_id": br["id"],
                "date": iso(today()),
                "ease": "NORMAL",
                "kids": [{"sex": "F", "tag": "KID-RACE"}],
            },
            headers=owner,
        )
    )
    try:
        # The kid-Animal INSERT is genuinely stuck on the holder's
        # uncommitted tag row before the holder commits.
        await wait_until_blocked()
        await holder.commit()
        resp = await request
    finally:
        await holder.rollback()
        await holder.close()
        if not request.done():
            request.cancel()
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "A kid tag already exists in this farm"


# ---------------------------------------------------------------------------
# 2-9 — a breeding that races the doe's sale is rejected, never persisted
# ---------------------------------------------------------------------------
async def test_breeding_after_concurrent_sale_is_rejected(
    client: httpx.AsyncClient,
) -> None:
    """The doe row is locked FOR UPDATE before the breeding is inserted: a
    sale that commits while the breeding request waits must be re-read —
    no open PENDING breeding may persist on a SOLD doe."""
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner)
    buck = await make_animal(client, owner, tag="B-1", sex="M", bucket="BREEDING")
    holder = get_sessionmaker()()
    row = (
        await holder.execute(select(Animal).where(Animal.id == doe).with_for_update())
    ).scalar_one()
    row.status = AnimalStatus.SOLD.value
    row.status_date = today()
    await holder.flush()

    request = asyncio.create_task(
        client.post(
            "/api/breeding",
            json={"doe_id": doe, "buck_id": buck, "breeding_date": iso(today())},
            headers=owner,
        )
    )
    try:
        # The breeding request is genuinely stuck on the doe's FOR UPDATE
        # before the sale commits.
        await wait_until_blocked()
        await holder.commit()
        resp = await request
    finally:
        await holder.rollback()
        await holder.close()
        if not request.done():
            request.cancel()
    assert resp.status_code == 400, resp.text  # the doe is no longer eligible
    resp = await client.get("/api/breeding", headers=owner)
    assert [r for r in resp.json()["records"] if r["doe_id"] == doe] == []


# ---------------------------------------------------------------------------
# 2-12 — a kidding never flips a committed user-skip back to DONE
# ---------------------------------------------------------------------------
async def test_kidding_stamp_survives_concurrent_user_skip(
    client: httpx.AsyncClient,
) -> None:
    """record_kidding stamps the KIDDING_DUE duty DONE under a row lock with
    a PENDING re-check: a user-skip that committed while the kidding was in
    flight must survive (the skip endpoint allows form-linked duties)."""
    owner = await owner_with_farm(client)
    _doe, br = await bred_doe_with_record(
        client, owner, breeding_date=today() - timedelta(days=150)
    )
    await confirm_pregnancy(client, owner, br["id"])
    due_task = next(
        t
        for t in await breeding_follow_ups(client, owner, br["id"])
        if t["category"] == "KIDDING_DUE"
    )
    holder = get_sessionmaker()()
    row = (
        await holder.execute(select(Task).where(Task.id == due_task["id"]).with_for_update())
    ).scalar_one()
    row.status = TaskStatus.SKIPPED.value
    row.skipped_at = utcnow()
    row.skip_reason = "Concurrent user skip fixture"
    await holder.flush()

    request = asyncio.create_task(
        client.post(
            "/api/kidding",
            json={
                "breeding_record_id": br["id"],
                "date": iso(today()),
                "ease": "NORMAL",
                "kids": [{"sex": "M"}],
            },
            headers=owner,
        )
    )
    try:
        # The kidding's DONE-stamp SELECT ... FOR UPDATE is genuinely stuck
        # on the skip's uncommitted row lock before the skip commits.
        await wait_until_blocked()
        await holder.commit()
        resp = await request
    finally:
        await holder.rollback()
        await holder.close()
        if not request.done():
            request.cancel()
    assert resp.status_code == 201, resp.text  # the kidding itself records fine
    async with get_sessionmaker()() as db:
        final = await db.get(Task, due_task["id"])
        assert final is not None
        assert final.status == TaskStatus.SKIPPED.value  # never flipped back to DONE
