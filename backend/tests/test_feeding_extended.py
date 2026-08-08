"""Extended feeding-module tests (SPEC "Feeding — TMR recipes", "Feed
allocation per bucket", "FeedInventory").

Covers, against the live JSON API (httpx + real PostgreSQL):
- GET /api/feeding/plan — per-bucket lines, headcounts, per-head kg defaults
  and overrides, the hard 40/20/40 shift split, today's dispensing log.
- Per-bucket recipe allocation: QUARANTINE dry-roughage days 1–3, RESTING
  MAINTENANCE_75_25 → FLUSH_70_30 at day 10, MALE_KIDS LACTATING_60_40 →
  FATTENING_50_50 at day 91 (API level via date_of_birth; model level for the
  bucket-move-driven boundaries the API cannot backdate).
- POST /api/feeding/settings — per-bucket daily_kg_per_head override (upsert,
  per-farm isolation, validation).
- POST /api/feeding/dispense — FeedingRecord log (recipe whitelist, dates,
  qty bounds, attribution, tenancy).
- GET /api/feeding/recipes — the 5 seeded SPEC recipes + allocation table.
- POST /api/feeding/mix — batch mixing decrements inventory per recipe lines,
  all-or-nothing negative-stock guard.
- GET /api/feeding/inventory + POST …/{item_id}/add — seeded rows, purchase
  entries bump stock / last price and book a FEED expense.
- Auth & tenancy on every endpoint: 401 / 400 / 403 / 404, FEEDER role.
"""

from datetime import date, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import (
    SHIFT_SPLIT,
    Animal,
    AnimalSource,
    AnimalStatus,
    Bucket,
    BucketMove,
    Farm,
    FeedingRecord,
    FeedingShift,
    FeedInventory,
    Sex,
    User,
)
from app.services import (
    BUCKET_ALLOCATION_REFERENCE,
    DRY_ROUGHAGE,
    add_feed_stock,
    recipe_for_animal,
)
from app.utils import today

from .conftest import login, owner_with_farm

WORKER_PW = "workerpass123"
GREEN = "Super Napier green fodder"
STOVER = "Dry jowar stover"
MINERAL = "Mineral mix"
ALL_RECIPE_CODES = [
    "FATTENING_50_50",
    "LACTATING_60_40",
    "MAINTENANCE_75_25",
    "FLUSH_70_30",
    "CREEP",
]

# SPEC "Feed allocation per bucket" — expected recipe for an animal created
# today (day 0 in bucket) or with a known DOB (MALE_KIDS).
BUCKET_RECIPE_API = {
    "QUARANTINE": DRY_ROUGHAGE,  # days 1–3: dry roughage only
    "FOUNDATION": "LACTATING_60_40",
    "BREEDING": "MAINTENANCE_75_25",
    "PREGNANCY_EARLY": "MAINTENANCE_75_25",
    "PREGNANCY_LATE": "LACTATING_60_40",
    "DELIVERY": "LACTATING_60_40",
    "RECOVERY": "LACTATING_60_40",
    "RESTING": "MAINTENANCE_75_25",  # days 1–10 dry-off
    "FEMALE_KIDS": "LACTATING_60_40",
}

# Seeded BucketDefinition daily_kg_per_head defaults (seed.BUCKET_DEFINITIONS).
BUCKET_DEFAULT_KG = {
    "QUARANTINE": 0.8,
    "FOUNDATION": 1.2,
    "BREEDING": 1.2,
    "PREGNANCY_EARLY": 1.2,
    "PREGNANCY_LATE": 1.4,
    "DELIVERY": 1.5,
    "RECOVERY": 1.5,
    "RESTING": 1.2,
    "MALE_KIDS": 1.0,
    "FEMALE_KIDS": 1.0,
}

# Seeded per-farm ingredients in (category, ingredient) sort order.
EXPECTED_INVENTORY_ORDER = [
    "Crushed maize",
    "DORB",
    "Maize DDGS",
    "Mineral mix",
    "Mustard DOC",
    "Soya DOC",
    "Dry jowar stover",
    "Groundnut haulms",
    "Super Napier green fodder",
]

# SPEC recipe 1: FATTENING_50_50 lines (kg per 100 kg batch).
FATTENING_LINES = {
    GREEN: 30.0,
    STOVER: 20.0,
    "Crushed maize": 17.5,
    "Maize DDGS": 10.0,
    "Soya DOC": 7.5,
    "Mustard DOC": 7.5,
    "DORB": 6.0,
    MINERAL: 1.5,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def make_animal(
    client: httpx.AsyncClient,
    headers: dict,
    tag: str,
    *,
    sex: str = "F",
    bucket: str = "BREEDING",
    dob_days: int | None = None,
    weight_kg: float | None = None,
) -> dict:
    payload: dict = {
        "tag_number": tag,
        "sex": sex,
        "source": "PURCHASED",
        "current_bucket": bucket,
    }
    if dob_days is not None:
        payload["date_of_birth"] = (today() - timedelta(days=dob_days)).isoformat()
    if weight_kg is not None:
        payload["weight_kg"] = weight_kg
    resp = await client.post("/api/animals", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def get_plan(client: httpx.AsyncClient, headers: dict) -> dict:
    resp = await client.get("/api/feeding/plan", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def get_inventory(client: httpx.AsyncClient, headers: dict) -> list[dict]:
    resp = await client.get("/api/feeding/inventory", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def inv_item(client: httpx.AsyncClient, headers: dict, ingredient: str) -> dict:
    return next(i for i in await get_inventory(client, headers) if i["ingredient"] == ingredient)


async def add_stock(
    client: httpx.AsyncClient,
    headers: dict,
    item_id: int | str,
    qty: object,
    price: object = None,
) -> httpx.Response:
    payload: dict = {"qty_kg": qty}
    if price is not None:
        payload["price_per_kg"] = price
    return await client.post(f"/api/feeding/inventory/{item_id}/add", json=payload, headers=headers)


async def stock_all(client: httpx.AsyncClient, headers: dict, qty: float) -> None:
    """Top up every seeded ingredient row to `qty` kg (no price → no expense)."""
    for item in await get_inventory(client, headers):
        resp = await add_stock(client, headers, item["id"], qty)
        assert resp.status_code == 200, resp.text


async def dispense(client: httpx.AsyncClient, headers: dict, **overrides: object) -> httpx.Response:
    payload: dict = {"bucket": "BREEDING", "shift": "MORNING", "qty_kg": 5.0} | overrides
    return await client.post("/api/feeding/dispense", json=payload, headers=headers)


async def mix_ready(
    client: httpx.AsyncClient, headers: dict, recipe_code: str, batch_kg: float = 100.0
) -> None:
    """Stock ingredients and create ready-to-dispense recipe inventory."""
    await stock_all(client, headers, 1000.0)
    resp = await client.post(
        "/api/feeding/mix",
        json={"recipe_code": recipe_code, "batch_kg": batch_kg},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text


async def finance_txns(client: httpx.AsyncClient, headers: dict) -> list[dict]:
    resp = await client.get("/api/finance", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["transactions"]


async def role_id(client: httpx.AsyncClient, owner: dict, code: str) -> int:
    resp = await client.get("/api/team", headers=owner)
    assert resp.status_code == 200, resp.text
    return next(r["id"] for r in resp.json()["roles"] if r["code"] == code)


async def worker_headers(client: httpx.AsyncClient, owner: dict, code: str, email: str) -> dict:
    """Owner adds a worker with preset role `code`; returns farm headers."""
    rid = await role_id(client, owner, code)
    resp = await client.post(
        "/api/team/workers",
        json={"name": "Worker", "email": email, "password": WORKER_PW, "role_id": rid},
        headers=owner,
    )
    assert resp.status_code == 201, resp.text
    auth = await login(client, email, WORKER_PW)
    return auth | {"X-Farm-Id": owner["X-Farm-Id"]}


def in_memory_animal(
    bucket: Bucket,
    *,
    dob: date | None = None,
    days_in_bucket: int | None = None,
) -> Animal:
    """Unpersisted Animal for pure `recipe_for_animal` tests (same pattern as
    test_logic.make_doe_object): the API cannot backdate BucketMove.moved_at,
    so day-in-bucket boundaries are pinned in memory."""
    animal = Animal(
        farm_id=1,
        tag_number="T-1",
        sex=Sex.F.value,
        source=AnimalSource.PURCHASED.value,
        status=AnimalStatus.ACTIVE.value,
        current_bucket=bucket.value,
        date_of_birth=dob,
    )
    if days_in_bucket is not None:
        move = BucketMove(animal_id=1, from_bucket=None, to_bucket=bucket.value, reason="test")
        move.moved_at = datetime.combine(
            today() - timedelta(days=days_in_bucket), datetime.min.time()
        )
        animal.bucket_moves = [move]
    return animal


# ---------------------------------------------------------------------------
# GET /api/feeding/plan — basics
# ---------------------------------------------------------------------------
async def test_plan_empty_farm_has_no_lines_or_records(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    plan = await get_plan(client, headers)
    assert plan["lines"] == []
    assert plan["records"] == []


async def test_plan_response_shape(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "D-1")
    plan = await get_plan(client, headers)
    assert set(plan) == {"lines", "records"}
    line = plan["lines"][0]
    assert set(line) == {
        "bucket",
        "recipe_code",
        "recipe_name",
        "heads",
        "kg_per_head",
        "daily_kg",
        "shifts",
    }
    shift = line["shifts"][0]
    assert set(shift) == {"shift", "pct", "kg", "time"}


async def test_plan_single_bucket_headcount_and_kg_math(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    for i in range(3):
        await make_animal(client, headers, f"D-{i}")
    lines = (await get_plan(client, headers))["lines"]
    assert len(lines) == 1
    line = lines[0]
    assert line["bucket"] == "BREEDING"
    assert line["recipe_code"] == "MAINTENANCE_75_25"
    assert line["recipe_name"] == "Maintenance 75:25"
    assert line["heads"] == 3
    assert line["kg_per_head"] == 1.2  # BucketDefinition default
    assert line["daily_kg"] == pytest.approx(3.6)


async def test_plan_shift_split_is_hard_40_20_40(client: httpx.AsyncClient) -> None:
    """SPEC: 3× daily, split 40% 6:30 AM / 20% 1:30 PM / 40% 7:30 PM."""
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "D-1")
    line = (await get_plan(client, headers))["lines"][0]
    pcts = {s["shift"]: s["pct"] for s in line["shifts"]}
    assert pcts == {"MORNING": 40, "AFTERNOON": 20, "NIGHT": 40}
    assert sum(pcts.values()) == 100  # split always sums to 100


async def test_plan_shift_times_match_spec(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "D-1")
    line = (await get_plan(client, headers))["lines"][0]
    times = {s["shift"]: s["time"] for s in line["shifts"]}
    assert times["MORNING"] == "6:30 AM (sweep bunks first)"  # morning includes bunk sweeping
    assert times["AFTERNOON"] == "1:30 PM"
    assert times["NIGHT"] == "7:30 PM"


async def test_plan_shift_kg_sums_back_to_daily_total(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    for i in range(4):
        await make_animal(client, headers, f"D-{i}")
    line = (await get_plan(client, headers))["lines"][0]
    assert line["daily_kg"] == pytest.approx(4.8)
    by_shift = {s["shift"]: s["kg"] for s in line["shifts"]}
    assert by_shift["MORNING"] == pytest.approx(1.92)
    assert by_shift["AFTERNOON"] == pytest.approx(0.96)
    assert by_shift["NIGHT"] == pytest.approx(1.92)
    assert sum(by_shift.values()) == pytest.approx(line["daily_kg"])


async def test_plan_lines_sorted_by_bucket_definition_order(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    # created in reverse order — the plan must still follow bucket sort_order
    await make_animal(client, headers, "FK-1", bucket="FEMALE_KIDS", dob_days=200)
    await make_animal(client, headers, "Q-1", bucket="QUARANTINE")
    await make_animal(client, headers, "B-1", bucket="BREEDING")
    lines = (await get_plan(client, headers))["lines"]
    assert [line["bucket"] for line in lines] == ["QUARANTINE", "BREEDING", "FEMALE_KIDS"]


async def test_plan_same_bucket_two_recipes_gets_two_lines(client: httpx.AsyncClient) -> None:
    """A frame-builder (day ≤90) and a fattening (day 91+) male kid share the
    MALE_KIDS bucket but eat different recipes → two plan lines."""
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "MK-YOUNG", sex="M", bucket="MALE_KIDS", dob_days=60)
    await make_animal(client, headers, "MK-OLD", sex="M", bucket="MALE_KIDS", dob_days=120)
    lines = (await get_plan(client, headers))["lines"]
    assert len(lines) == 2
    by_recipe = {line["recipe_code"]: line for line in lines}
    assert by_recipe["LACTATING_60_40"]["heads"] == 1
    assert by_recipe["FATTENING_50_50"]["heads"] == 1
    assert all(line["bucket"] == "MALE_KIDS" for line in lines)


async def test_plan_excludes_sold_animals(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    sold = await make_animal(client, headers, "D-SOLD")
    await make_animal(client, headers, "D-KEEP")
    resp = await client.post(
        f"/api/animals/{sold['id']}/status",
        json={"new_status": "SOLD", "sale_price": 5000},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    lines = (await get_plan(client, headers))["lines"]
    assert len(lines) == 1
    assert lines[0]["heads"] == 1


async def test_plan_excludes_dead_animals(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    dead = await make_animal(client, headers, "D-DEAD")
    await make_animal(client, headers, "D-KEEP")
    resp = await client.post(
        f"/api/animals/{dead['id']}/status", json={"new_status": "DEAD"}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    lines = (await get_plan(client, headers))["lines"]
    assert len(lines) == 1
    assert lines[0]["heads"] == 1


# ---------------------------------------------------------------------------
# GET /api/feeding/plan — per-bucket recipe allocation (SPEC table)
# ---------------------------------------------------------------------------
async def test_plan_quarantine_day0_gets_dry_roughage_only(client: httpx.AsyncClient) -> None:
    """SPEC: QUARANTINE days 1–3 dry roughage only, zero grain."""
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "Q-1", bucket="QUARANTINE")
    line = (await get_plan(client, headers))["lines"][0]
    assert line["recipe_code"] == DRY_ROUGHAGE
    assert line["recipe_name"].startswith("Dry roughage only")
    assert line["kg_per_head"] == 0.8  # quarantine ration default


async def test_plan_foundation_gets_lactating(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "F-1", bucket="FOUNDATION")
    assert (await get_plan(client, headers))["lines"][0]["recipe_code"] == "LACTATING_60_40"


async def test_plan_breeding_gets_maintenance(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "B-1", bucket="BREEDING")
    assert (await get_plan(client, headers))["lines"][0]["recipe_code"] == "MAINTENANCE_75_25"


async def test_plan_pregnancy_early_gets_maintenance(client: httpx.AsyncClient) -> None:
    """SPEC: early pregnancy — maintenance feed, do NOT overfeed."""
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "PE-1", bucket="PREGNANCY_EARLY")
    assert (await get_plan(client, headers))["lines"][0]["recipe_code"] == "MAINTENANCE_75_25"


async def test_plan_pregnancy_late_gets_lactating(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "PL-1", bucket="PREGNANCY_LATE")
    line = (await get_plan(client, headers))["lines"][0]
    assert line["recipe_code"] == "LACTATING_60_40"
    assert line["kg_per_head"] == 1.4


async def test_plan_delivery_gets_lactating(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "DL-1", bucket="DELIVERY")
    line = (await get_plan(client, headers))["lines"][0]
    assert line["recipe_code"] == "LACTATING_60_40"
    assert line["kg_per_head"] == 1.5


async def test_plan_recovery_gets_lactating(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "RC-1", bucket="RECOVERY")
    line = (await get_plan(client, headers))["lines"][0]
    assert line["recipe_code"] == "LACTATING_60_40"
    assert line["kg_per_head"] == 1.5


async def test_plan_resting_day0_gets_maintenance(client: httpx.AsyncClient) -> None:
    """SPEC: RESTING days 1–10 dry-off on MAINTENANCE_75_25 (flush starts day 10)."""
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "R-1", bucket="RESTING")
    line = (await get_plan(client, headers))["lines"][0]
    assert line["recipe_code"] == "MAINTENANCE_75_25"
    assert line["kg_per_head"] == 1.2


async def test_plan_female_kids_gets_lactating(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "FK-1", bucket="FEMALE_KIDS", dob_days=200)
    line = (await get_plan(client, headers))["lines"][0]
    assert line["recipe_code"] == "LACTATING_60_40"
    assert line["kg_per_head"] == 1.0


async def test_plan_male_kids_frame_builder_gets_lactating(client: httpx.AsyncClient) -> None:
    """SPEC: MALE_KIDS day 61–90 LACTATING_60_40 (frame-builder)."""
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "MK-1", sex="M", bucket="MALE_KIDS", dob_days=75)
    line = (await get_plan(client, headers))["lines"][0]
    assert line["recipe_code"] == "LACTATING_60_40"
    assert line["kg_per_head"] == 1.0


async def test_plan_male_kids_day90_still_lactating(client: httpx.AsyncClient) -> None:
    """Boundary: day 90 is the last frame-builder day (switch is at day 91)."""
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "MK-90", sex="M", bucket="MALE_KIDS", dob_days=90)
    assert (await get_plan(client, headers))["lines"][0]["recipe_code"] == "LACTATING_60_40"


async def test_plan_male_kids_day91_switches_to_fattening(client: httpx.AsyncClient) -> None:
    """SPEC: MALE_KIDS day 91+ FATTENING_50_50."""
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "MK-91", sex="M", bucket="MALE_KIDS", dob_days=91)
    assert (await get_plan(client, headers))["lines"][0]["recipe_code"] == "FATTENING_50_50"


async def test_plan_male_kids_unknown_age_defaults_to_fattening(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "MK-NODOB", sex="M", bucket="MALE_KIDS")
    assert (await get_plan(client, headers))["lines"][0]["recipe_code"] == "FATTENING_50_50"


async def test_plan_full_bucket_table_matches_spec(client: httpx.AsyncClient) -> None:
    """One animal per bucket: recipe allocation + default per-head ration for
    the whole SPEC table in a single plan."""
    headers = await owner_with_farm(client)
    for bucket in BUCKET_RECIPE_API:
        await make_animal(client, headers, f"{bucket}-1", bucket=bucket)
    await make_animal(client, headers, "MK-FAT", sex="M", bucket="MALE_KIDS", dob_days=200)
    lines = (await get_plan(client, headers))["lines"]
    assert [line["bucket"] for line in lines] == [
        "QUARANTINE",
        "FOUNDATION",
        "BREEDING",
        "PREGNANCY_EARLY",
        "PREGNANCY_LATE",
        "DELIVERY",
        "RECOVERY",
        "RESTING",
        "MALE_KIDS",
        "FEMALE_KIDS",
    ]
    for line in lines:
        expected = (
            "FATTENING_50_50"
            if line["bucket"] == "MALE_KIDS"
            else BUCKET_RECIPE_API[line["bucket"]]
        )
        assert line["recipe_code"] == expected, line["bucket"]
        assert line["kg_per_head"] == BUCKET_DEFAULT_KG[line["bucket"]], line["bucket"]
        assert line["heads"] == 1
        # every line's split sums to 100 and its kg sums back to the daily total
        assert sum(s["pct"] for s in line["shifts"]) == 100
        assert sum(s["kg"] for s in line["shifts"]) == pytest.approx(line["daily_kg"], abs=0.02)


# ---------------------------------------------------------------------------
# recipe_for_animal — pure model-level boundaries the API cannot reach
# (BucketMove.moved_at cannot be backdated through the JSON API)
# ---------------------------------------------------------------------------
def test_shift_split_constant_sums_to_100() -> None:
    """The 3-shift split is a fixed constant: it must always total 100%."""
    assert sum(SHIFT_SPLIT.values()) == pytest.approx(1.0)
    assert set(SHIFT_SPLIT) == set(FeedingShift)
    assert SHIFT_SPLIT[FeedingShift.MORNING] == 0.40
    assert SHIFT_SPLIT[FeedingShift.AFTERNOON] == 0.20
    assert SHIFT_SPLIT[FeedingShift.NIGHT] == 0.40


def test_recipe_resting_day9_still_maintenance() -> None:
    animal = in_memory_animal(Bucket.RESTING, days_in_bucket=9)
    assert recipe_for_animal(animal) == "MAINTENANCE_75_25"


def test_recipe_resting_day10_switches_to_flush() -> None:
    """SPEC: RESTING days 10–30 flush (70/30)."""
    animal = in_memory_animal(Bucket.RESTING, days_in_bucket=10)
    assert recipe_for_animal(animal) == "FLUSH_70_30"


def test_recipe_resting_day30_still_flush() -> None:
    animal = in_memory_animal(Bucket.RESTING, days_in_bucket=30)
    assert recipe_for_animal(animal) == "FLUSH_70_30"


def test_recipe_quarantine_day2_dry_roughage() -> None:
    animal = in_memory_animal(Bucket.QUARANTINE, days_in_bucket=2)
    assert recipe_for_animal(animal) == DRY_ROUGHAGE


def test_recipe_quarantine_day3_transitions_to_maintenance() -> None:
    """SPEC: dry roughage days 1–3 (arrival day = day 1), then MAINTENANCE."""
    animal = in_memory_animal(Bucket.QUARANTINE, days_in_bucket=3)
    assert recipe_for_animal(animal) == "MAINTENANCE_75_25"


def test_recipe_male_kids_day90_91_boundary() -> None:
    young = in_memory_animal(Bucket.MALE_KIDS, dob=today() - timedelta(days=90))
    assert recipe_for_animal(young) == "LACTATING_60_40"
    old = in_memory_animal(Bucket.MALE_KIDS, dob=today() - timedelta(days=91))
    assert recipe_for_animal(old) == "FATTENING_50_50"


def test_recipe_static_bucket_mapping() -> None:
    """Every non-date-driven bucket maps to its SPEC recipe."""
    assert recipe_for_animal(in_memory_animal(Bucket.FOUNDATION)) == "LACTATING_60_40"
    assert recipe_for_animal(in_memory_animal(Bucket.FEMALE_KIDS)) == "LACTATING_60_40"
    assert recipe_for_animal(in_memory_animal(Bucket.PREGNANCY_LATE)) == "LACTATING_60_40"
    assert recipe_for_animal(in_memory_animal(Bucket.RECOVERY)) == "LACTATING_60_40"
    assert recipe_for_animal(in_memory_animal(Bucket.DELIVERY)) == "LACTATING_60_40"
    assert recipe_for_animal(in_memory_animal(Bucket.BREEDING)) == "MAINTENANCE_75_25"
    assert recipe_for_animal(in_memory_animal(Bucket.PREGNANCY_EARLY)) == "MAINTENANCE_75_25"


# ---------------------------------------------------------------------------
# POST /api/feeding/settings — per-bucket daily_kg_per_head override
# ---------------------------------------------------------------------------
async def test_settings_happy_returns_204_with_empty_body(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await client.post(
        "/api/feeding/settings",
        json={"bucket": "BREEDING", "daily_kg_per_head": 2.5},
        headers=headers,
    )
    assert resp.status_code == 204, resp.text
    assert resp.content == b""


async def test_settings_override_reflected_in_plan(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    for i in range(2):
        await make_animal(client, headers, f"D-{i}")
    resp = await client.post(
        "/api/feeding/settings",
        json={"bucket": "BREEDING", "daily_kg_per_head": 2.5},
        headers=headers,
    )
    assert resp.status_code == 204, resp.text
    line = (await get_plan(client, headers))["lines"][0]
    assert line["kg_per_head"] == 2.5
    assert line["daily_kg"] == pytest.approx(5.0)  # 2 heads × 2.5 kg
    by_shift = {s["shift"]: s["kg"] for s in line["shifts"]}
    assert by_shift["MORNING"] == pytest.approx(2.0)
    assert by_shift["AFTERNOON"] == pytest.approx(1.0)
    assert by_shift["NIGHT"] == pytest.approx(2.0)


async def test_settings_upsert_replaces_previous_override(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "D-1")
    for kg in (2.5, 3.25):
        resp = await client.post(
            "/api/feeding/settings",
            json={"bucket": "BREEDING", "daily_kg_per_head": kg},
            headers=headers,
        )
        assert resp.status_code == 204, resp.text
    line = (await get_plan(client, headers))["lines"][0]
    assert line["kg_per_head"] == 3.25  # not duplicated, not summed


async def test_settings_override_is_per_bucket(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "B-1", bucket="BREEDING")
    await make_animal(client, headers, "F-1", bucket="FOUNDATION")
    resp = await client.post(
        "/api/feeding/settings",
        json={"bucket": "BREEDING", "daily_kg_per_head": 3.0},
        headers=headers,
    )
    assert resp.status_code == 204, resp.text
    lines = {line["bucket"]: line for line in (await get_plan(client, headers))["lines"]}
    assert lines["BREEDING"]["kg_per_head"] == 3.0
    assert lines["FOUNDATION"]["kg_per_head"] == 1.2  # untouched default


async def test_settings_override_is_per_farm(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Alpha Farm")
    owner_b = await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    await make_animal(client, owner_a, "A-1")
    await make_animal(client, owner_b, "B-1")
    resp = await client.post(
        "/api/feeding/settings",
        json={"bucket": "BREEDING", "daily_kg_per_head": 4.0},
        headers=owner_a,
    )
    assert resp.status_code == 204, resp.text
    line_a = (await get_plan(client, owner_a))["lines"][0]
    line_b = (await get_plan(client, owner_b))["lines"][0]
    assert line_a["kg_per_head"] == 4.0
    assert line_b["kg_per_head"] == 1.2  # no cross-farm leak


async def test_settings_zero_and_negative_rejected(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    for bad in (0, 0.0, -1, -0.5, "-1"):
        resp = await client.post(
            "/api/feeding/settings",
            json={"bucket": "BREEDING", "daily_kg_per_head": bad},
            headers=headers,
        )
        assert resp.status_code == 422, bad


async def test_settings_nonfinite_rejected(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    for bad in ("nan", "inf", "-inf", "1e999"):
        resp = await client.post(
            "/api/feeding/settings",
            json={"bucket": "BREEDING", "daily_kg_per_head": bad},
            headers=headers,
        )
        assert resp.status_code == 422, bad


async def test_settings_garbage_types_rejected(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    for payload in [
        {"bucket": "BREEDING", "daily_kg_per_head": "abc"},
        {"bucket": "BREEDING", "daily_kg_per_head": None},
        {"bucket": "BREEDING", "daily_kg_per_head": [1.5]},
        {"bucket": "MOON", "daily_kg_per_head": 1.5},
        {"bucket": "breeding", "daily_kg_per_head": 1.5},  # case-sensitive enum
        {"bucket": 42, "daily_kg_per_head": 1.5},
        {"daily_kg_per_head": 1.5},  # missing bucket
        {"bucket": "BREEDING"},  # missing kg
        {},
    ]:
        resp = await client.post("/api/feeding/settings", json=payload, headers=headers)
        assert resp.status_code == 422, payload


async def test_settings_tiny_positive_accepted(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "D-1")
    resp = await client.post(
        "/api/feeding/settings",
        json={"bucket": "BREEDING", "daily_kg_per_head": 0.001},
        headers=headers,
    )
    assert resp.status_code == 204, resp.text
    line = (await get_plan(client, headers))["lines"][0]
    assert line["kg_per_head"] == pytest.approx(0.001)


async def test_settings_huge_finite_accepted(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "D-1")
    resp = await client.post(
        "/api/feeding/settings",
        json={"bucket": "BREEDING", "daily_kg_per_head": 1e6},
        headers=headers,
    )
    assert resp.status_code == 204, resp.text
    line = (await get_plan(client, headers))["lines"][0]
    assert line["kg_per_head"] == pytest.approx(1e6)
    assert line["daily_kg"] == pytest.approx(1e6)


async def test_settings_shift_rounding_is_deterministic(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "D-1")
    resp = await client.post(
        "/api/feeding/settings",
        json={"bucket": "BREEDING", "daily_kg_per_head": 1.13},
        headers=headers,
    )
    assert resp.status_code == 204, resp.text
    line = (await get_plan(client, headers))["lines"][0]
    assert line["daily_kg"] == pytest.approx(1.13)
    by_shift = {s["shift"]: s["kg"] for s in line["shifts"]}
    assert by_shift == {"MORNING": 0.45, "AFTERNOON": 0.23, "NIGHT": 0.45}
    assert sum(by_shift.values()) == pytest.approx(1.13)


async def test_settings_unknown_extra_field_ignored(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await client.post(
        "/api/feeding/settings",
        json={"bucket": "BREEDING", "daily_kg_per_head": 2.0, "junk": "x" * 100},
        headers=headers,
    )
    assert resp.status_code == 204, resp.text


# ---------------------------------------------------------------------------
# POST /api/feeding/dispense — the dispensing log
# ---------------------------------------------------------------------------
async def test_dispense_happy_with_recipe(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    await mix_ready(client, headers, "MAINTENANCE_75_25")
    resp = await dispense(client, headers, recipe_code="MAINTENANCE_75_25", qty_kg=12.5)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["bucket"] == "BREEDING"
    assert body["shift"] == "MORNING"
    assert body["recipe_code"] == "MAINTENANCE_75_25"
    assert body["qty_kg"] == 12.5
    assert body["date"] == today().isoformat()  # defaults to today
    assert body["id"] > 0


async def test_dispense_response_shape(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    await mix_ready(client, headers, "CREEP")
    resp = await dispense(client, headers, recipe_code="CREEP")
    assert resp.status_code == 201, resp.text
    assert set(resp.json()) == {"id", "date", "shift", "bucket", "recipe_code", "qty_kg"}


async def test_dispense_without_recipe_stores_null(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await dispense(client, headers)
    assert resp.status_code == 201, resp.text
    assert resp.json()["recipe_code"] is None


async def test_dispense_explicit_today_and_past_dates(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    for d in (today(), today() - timedelta(days=1), date(2000, 1, 1)):
        resp = await dispense(client, headers, date=d.isoformat())
        assert resp.status_code == 201, d
        assert resp.json()["date"] == d.isoformat()


async def test_dispense_future_date_rejected(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    future = today() + timedelta(days=2)  # tomorrow is allowed (tz headroom)
    resp = await dispense(client, headers, date=future.isoformat())
    assert resp.status_code == 422


async def test_dispense_garbage_date_rejected(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    for bad in ("garbage", "32/13/2020", "2026-02-30"):
        resp = await dispense(client, headers, date=bad)
        assert resp.status_code == 422, bad


async def test_dispense_zero_and_negative_qty_rejected(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    for bad in (0, 0.0, -1, -0.25):
        resp = await dispense(client, headers, qty_kg=bad)
        assert resp.status_code == 422, bad


async def test_dispense_nonfinite_and_garbage_qty_rejected(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    for bad in ("nan", "inf", "-inf", "1e999", "abc", None, [5]):
        resp = await dispense(client, headers, qty_kg=bad)
        assert resp.status_code == 422, bad


async def test_dispense_invalid_shift_rejected(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    for bad in ("MIDDAY", "morning", "", "MORNING ", 7):
        resp = await dispense(client, headers, shift=bad)
        assert resp.status_code == 422, bad


async def test_dispense_invalid_bucket_rejected(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    for bad in ("MOON", "breeding", "", "BREEDING ", None):
        resp = await dispense(client, headers, bucket=bad)
        assert resp.status_code == 422, bad


async def test_dispense_missing_required_fields_rejected(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    for payload in [
        {"shift": "MORNING", "qty_kg": 5},
        {"bucket": "BREEDING", "qty_kg": 5},
        {"bucket": "BREEDING", "shift": "MORNING"},
        {},
    ]:
        resp = await client.post("/api/feeding/dispense", json=payload, headers=headers)
        assert resp.status_code == 422, payload


async def test_dispense_unknown_recipe_rejected_400(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await dispense(client, headers, recipe_code="NO_SUCH_RECIPE")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Unknown recipe NO_SUCH_RECIPE"


async def test_dispense_recipe_code_is_case_sensitive(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await dispense(client, headers, recipe_code="creep")
    assert resp.status_code == 400


async def test_dispense_whitespace_only_recipe_becomes_null(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await dispense(client, headers, recipe_code="   ")
    assert resp.status_code == 201, resp.text
    assert resp.json()["recipe_code"] is None


async def test_dispense_padded_recipe_is_trimmed(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    await mix_ready(client, headers, "CREEP")
    resp = await dispense(client, headers, recipe_code="  CREEP  ")
    assert resp.status_code == 201, resp.text
    assert resp.json()["recipe_code"] == "CREEP"


async def test_dispense_every_seeded_recipe_accepted(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    await stock_all(client, headers, 1000.0)
    for code in ALL_RECIPE_CODES:
        mixed = await client.post(
            "/api/feeding/mix",
            json={"recipe_code": code, "batch_kg": 10.0},
            headers=headers,
        )
        assert mixed.status_code == 200, mixed.text
        resp = await dispense(client, headers, recipe_code=code)
        assert resp.status_code == 201, code
        assert resp.json()["recipe_code"] == code


async def test_dispense_appears_in_todays_plan_records(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    await mix_ready(client, headers, "MAINTENANCE_75_25")
    resp = await dispense(client, headers, recipe_code="MAINTENANCE_75_25", qty_kg=7.5)
    assert resp.status_code == 201, resp.text
    record_id = resp.json()["id"]
    records = (await get_plan(client, headers))["records"]
    assert len(records) == 1
    rec = records[0]
    assert rec["id"] == record_id
    assert rec["date"] == today().isoformat()
    assert rec["qty_kg"] == 7.5


async def test_dispense_backdated_record_not_in_todays_log(client: httpx.AsyncClient) -> None:
    """The plan's dispensing log is 'today's log': yesterday's record is stored
    but not listed."""
    headers = await owner_with_farm(client)
    yesterday = (today() - timedelta(days=1)).isoformat()
    resp = await dispense(client, headers, date=yesterday)
    assert resp.status_code == 201, resp.text
    assert (await get_plan(client, headers))["records"] == []
    history = await client.get("/api/feeding/records", headers=headers)
    assert history.status_code == 200, history.text
    assert history.json()["total"] == 1
    assert history.json()["records"][0]["date"] == yesterday


async def test_dispense_multiple_records_logged_in_order(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    ids = []
    for shift, qty in [("MORNING", 4.0), ("AFTERNOON", 2.0), ("NIGHT", 4.0)]:
        resp = await dispense(client, headers, shift=shift, qty_kg=qty)
        assert resp.status_code == 201, resp.text
        ids.append(resp.json()["id"])
    records = (await get_plan(client, headers))["records"]
    assert [r["id"] for r in records] == ids  # insertion order
    assert [r["shift"] for r in records] == ["MORNING", "AFTERNOON", "NIGHT"]


async def test_dispense_duplicate_is_logged_twice(client: httpx.AsyncClient) -> None:
    """The dispensing log is a log, not a state machine: a double-recorded
    shift creates two rows (no uniqueness guard in SPEC)."""
    headers = await owner_with_farm(client)
    for _ in range(2):
        resp = await dispense(client, headers, qty_kg=5.0)
        assert resp.status_code == 201, resp.text
    records = (await get_plan(client, headers))["records"]
    assert len(records) == 2
    assert records[0]["id"] != records[1]["id"]


async def test_dispense_attributed_to_recording_user(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    await mix_ready(client, headers, "CREEP")
    resp = await dispense(client, headers, recipe_code="CREEP")
    assert resp.status_code == 201, resp.text
    async with get_sessionmaker()() as db:
        user = (await db.execute(select(User).where(User.email == "owner@farm.in"))).scalar_one()
        record = (await db.execute(select(FeedingRecord))).scalar_one()
        assert record.created_by_id == user.id
        assert record.farm_id == int(headers["X-Farm-Id"])


async def test_dispense_records_are_farm_isolated(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Alpha Farm")
    owner_b = await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    resp = await dispense(client, owner_a, qty_kg=9.0)
    assert resp.status_code == 201, resp.text
    assert len((await get_plan(client, owner_a))["records"]) == 1
    assert (await get_plan(client, owner_b))["records"] == []  # no leak


async def test_dispense_does_not_decrement_inventory(client: httpx.AsyncClient) -> None:
    """Ingredient stock decreases at MIX time, not at dispensing time."""
    headers = await owner_with_farm(client)
    await stock_all(client, headers, 100.0)
    mixed = await client.post(
        "/api/feeding/mix",
        json={"recipe_code": "FATTENING_50_50", "batch_kg": 50.0},
        headers=headers,
    )
    assert mixed.status_code == 200, mixed.text
    before = {i["id"]: i["qty_on_hand"] for i in await get_inventory(client, headers)}
    resp = await dispense(client, headers, recipe_code="FATTENING_50_50", qty_kg=50.0)
    assert resp.status_code == 201, resp.text
    after = {i["id"]: i["qty_on_hand"] for i in await get_inventory(client, headers)}
    assert after == before


async def test_dispense_requires_finished_stock_and_consumes_it(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    refused = await dispense(client, headers, recipe_code="CREEP", qty_kg=1.0)
    assert refused.status_code == 400
    assert "ready feed" in refused.json()["detail"]

    await mix_ready(client, headers, "CREEP", batch_kg=10.0)
    ready = await client.get("/api/feeding/finished-stock", headers=headers)
    assert ready.status_code == 200, ready.text
    assert ready.json()[0]["qty_on_hand"] == pytest.approx(10.0)
    accepted = await dispense(client, headers, recipe_code="CREEP", qty_kg=3.25)
    assert accepted.status_code == 201, accepted.text
    remaining = await client.get("/api/feeding/finished-stock", headers=headers)
    assert remaining.json()[0]["qty_on_hand"] == pytest.approx(6.75)


async def test_virtual_dry_roughage_can_be_recorded(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await dispense(client, headers, recipe_code=DRY_ROUGHAGE, qty_kg=2.0)
    assert resp.status_code == 201, resp.text
    assert resp.json()["recipe_code"] == DRY_ROUGHAGE


async def test_dispense_unicode_recipe_rejected_safely(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await dispense(client, headers, recipe_code="🐐🔥 घास")
    assert resp.status_code == 400
    assert (await get_plan(client, headers))["records"] == []


async def test_dispense_sql_injection_recipe_rejected_safely(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    payload = "'; DROP TABLE feeding_records;--"
    resp = await dispense(client, headers, recipe_code=payload)
    assert resp.status_code == 400
    assert resp.json()["detail"] == f"Unknown recipe {payload}"
    # nothing recorded, tables intact
    assert (await get_plan(client, headers))["records"] == []
    assert len(await get_inventory(client, headers)) == 9


async def test_dispense_very_long_recipe_code_rejected_safely(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await dispense(client, headers, recipe_code="X" * 10_000)
    assert resp.status_code == 400
    assert (await get_plan(client, headers))["records"] == []


async def test_dispense_tiny_and_huge_qty_boundaries(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await dispense(client, headers, qty_kg=0.001)
    assert resp.status_code == 201, resp.text
    assert resp.json()["qty_kg"] == pytest.approx(0.001)
    resp = await dispense(client, headers, qty_kg=1e6)  # exactly the 1e6 kg quantity cap
    assert resp.status_code == 201, resp.text
    assert resp.json()["qty_kg"] == pytest.approx(1e6)
    resp = await dispense(client, headers, qty_kg=1e12)  # beyond the cap — B2 bound
    assert resp.status_code == 422


async def test_dispense_unknown_extra_field_ignored(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await dispense(client, headers, junk="x" * 10_000)
    assert resp.status_code == 201, resp.text
    assert "junk" not in resp.json()


# ---------------------------------------------------------------------------
# GET /api/feeding/recipes — seeded TMR recipes + allocation reference
# ---------------------------------------------------------------------------
async def test_recipes_returns_five_seeded_recipes(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await client.get("/api/feeding/recipes", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"recipes", "allocation"}
    assert [r["code"] for r in body["recipes"]] == ALL_RECIPE_CODES  # id order


async def test_recipes_response_shape(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    body = (await client.get("/api/feeding/recipes", headers=headers)).json()
    recipe = body["recipes"][0]
    assert set(recipe) == {"id", "code", "name", "description", "lines"}
    line = recipe["lines"][0]
    assert set(line) == {"ingredient", "kg_per_100kg", "category"}


async def test_recipes_lines_sum_to_100_per_recipe(client: httpx.AsyncClient) -> None:
    """Every TMR recipe must describe a complete 100 kg batch."""
    headers = await owner_with_farm(client)
    body = (await client.get("/api/feeding/recipes", headers=headers)).json()
    for recipe in body["recipes"]:
        total = sum(line["kg_per_100kg"] for line in recipe["lines"])
        assert total == pytest.approx(100.0), recipe["code"]


async def test_recipes_line_categories_are_valid(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    body = (await client.get("/api/feeding/recipes", headers=headers)).json()
    valid = {"ROUGHAGE_WET", "ROUGHAGE_DRY", "CONCENTRATE"}
    for recipe in body["recipes"]:
        for line in recipe["lines"]:
            assert line["category"] in valid, line


async def test_recipes_fattening_lines_match_spec(client: httpx.AsyncClient) -> None:
    """SPEC recipe 1: FATTENING_50_50 — Green 30, Dry 20, Maize 17.5, DDGS 10,
    Soya DOC 7.5, Mustard DOC 7.5, DORB 6, Mineral 1.5."""
    headers = await owner_with_farm(client)
    body = (await client.get("/api/feeding/recipes", headers=headers)).json()
    recipe = next(r for r in body["recipes"] if r["code"] == "FATTENING_50_50")
    lines = {line["ingredient"]: line["kg_per_100kg"] for line in recipe["lines"]}
    assert lines == FATTENING_LINES
    assert recipe["name"] == "Fattening 50:50"


async def test_recipes_creep_is_dry_concentrate_only(client: httpx.AsyncClient) -> None:
    """SPEC recipe 5: CREEP ≈ 60/30/10 + mineral, dry concentrate only."""
    headers = await owner_with_farm(client)
    body = (await client.get("/api/feeding/recipes", headers=headers)).json()
    recipe = next(r for r in body["recipes"] if r["code"] == "CREEP")
    assert all(line["category"] == "CONCENTRATE" for line in recipe["lines"])
    lines = {line["ingredient"]: line["kg_per_100kg"] for line in recipe["lines"]}
    assert lines["Crushed maize"] == 60.0
    assert lines["Soya DOC"] == 30.0


async def test_recipes_allocation_table_covers_all_ten_buckets(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    body = (await client.get("/api/feeding/recipes", headers=headers)).json()
    allocation = body["allocation"]
    assert [a["bucket"] for a in allocation] == [b.value for b in Bucket]
    assert len(allocation) == 10
    assert len(BUCKET_ALLOCATION_REFERENCE) == 10


async def test_recipes_allocation_text_matches_spec(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    body = (await client.get("/api/feeding/recipes", headers=headers)).json()
    by_bucket = {a["bucket"]: a["allocation"] for a in body["allocation"]}
    assert by_bucket["RESTING"] == "Days 1–10 MAINTENANCE_75_25, days 10–30 FLUSH_70_30"
    assert "day 91+ FATTENING_50_50" in by_bucket["MALE_KIDS"]
    assert "LACTATING_60_40" in by_bucket["MALE_KIDS"]
    assert by_bucket["FOUNDATION"] == "LACTATING_60_40"
    assert "dry roughage" in by_bucket["QUARANTINE"].lower()


# ---------------------------------------------------------------------------
# POST /api/feeding/mix — batch mixing + negative-stock guard
# ---------------------------------------------------------------------------
async def test_mix_happy_decrements_every_recipe_line(client: httpx.AsyncClient) -> None:
    """SPEC: mixing a batch decreases stock per recipe lines."""
    headers = await owner_with_farm(client)
    await stock_all(client, headers, 1000.0)
    resp = await client.post(
        "/api/feeding/mix",
        json={"recipe_code": "FATTENING_50_50", "batch_kg": 100.0},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    inventory = await get_inventory(client, headers)
    by_ingredient = {i["ingredient"]: i["qty_on_hand"] for i in inventory}
    for ingredient, kg_per_100 in FATTENING_LINES.items():
        assert by_ingredient[ingredient] == pytest.approx(1000.0 - kg_per_100), ingredient
    assert by_ingredient["Groundnut haulms"] == 1000.0  # not in the recipe


async def test_mix_returns_the_recipe_payload(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    await stock_all(client, headers, 100.0)
    resp = await client.post(
        "/api/feeding/mix",
        json={"recipe_code": "CREEP", "batch_kg": 10.0},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["code"] == "CREEP"
    assert body["name"] == "Creep feed"
    assert len(body["lines"]) == 4


async def test_mix_unknown_recipe_400(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await client.post(
        "/api/feeding/mix",
        json={"recipe_code": "NO_SUCH", "batch_kg": 10.0},
        headers=headers,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Unknown recipe NO_SUCH"


async def test_mix_recipe_code_case_and_whitespace_sensitive(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    await stock_all(client, headers, 100.0)
    for code in ("creep", " CREEP", "CREEP "):
        resp = await client.post(
            "/api/feeding/mix",
            json={"recipe_code": code, "batch_kg": 10.0},
            headers=headers,
        )
        assert resp.status_code == 400, code
    # failed mixes must not have decremented anything
    assert all(i["qty_on_hand"] == 100.0 for i in await get_inventory(client, headers))


async def test_mix_empty_or_missing_recipe_code_422(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    for payload in [
        {"recipe_code": "", "batch_kg": 10.0},
        {"batch_kg": 10.0},
        {"recipe_code": "CREEP"},
        {},
    ]:
        resp = await client.post("/api/feeding/mix", json=payload, headers=headers)
        assert resp.status_code == 422, payload


async def test_mix_zero_and_negative_batch_422(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    for bad in (0, 0.0, -1, -100.5):
        resp = await client.post(
            "/api/feeding/mix",
            json={"recipe_code": "CREEP", "batch_kg": bad},
            headers=headers,
        )
        assert resp.status_code == 422, bad


async def test_mix_nonfinite_and_garbage_batch_422(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    for bad in ("nan", "inf", "-inf", "1e999", "abc", None):
        resp = await client.post(
            "/api/feeding/mix",
            json={"recipe_code": "CREEP", "batch_kg": bad},
            headers=headers,
        )
        assert resp.status_code == 422, bad


async def test_mix_zero_stock_lists_every_shortage(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await client.post(
        "/api/feeding/mix",
        json={"recipe_code": "FATTENING_50_50", "batch_kg": 100.0},
        headers=headers,
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    for ingredient in FATTENING_LINES:
        assert ingredient in detail, ingredient
    assert "need 30.0 kg, have 0.0 kg" in detail  # green fodder line


async def test_mix_refusal_is_all_or_nothing(client: httpx.AsyncClient) -> None:
    """Negative-stock guard: one short ingredient blocks the whole mix and no
    other ingredient is decremented."""
    headers = await owner_with_farm(client)
    for item in await get_inventory(client, headers):
        qty = 1.0 if item["ingredient"] == MINERAL else 100.0
        resp = await add_stock(client, headers, item["id"], qty)
        assert resp.status_code == 200, resp.text
    # 100 kg of FATTENING needs 1.5 kg mineral; only 1.0 on hand
    resp = await client.post(
        "/api/feeding/mix",
        json={"recipe_code": "FATTENING_50_50", "batch_kg": 100.0},
        headers=headers,
    )
    assert resp.status_code == 400
    assert MINERAL in resp.json()["detail"]
    inventory = await get_inventory(client, headers)
    by_ingredient = {i["ingredient"]: i["qty_on_hand"] for i in inventory}
    assert by_ingredient[MINERAL] == 1.0
    assert by_ingredient[GREEN] == 100.0  # untouched despite being sufficient


async def test_mix_exact_fit_leaves_zero_stock(client: httpx.AsyncClient) -> None:
    """Boundary: on_hand == needed is allowed (guard is `on_hand < needed`)."""
    headers = await owner_with_farm(client)
    for item in await get_inventory(client, headers):
        needed = FATTENING_LINES.get(item["ingredient"], 0.0)
        if needed:
            resp = await add_stock(client, headers, item["id"], needed)
            assert resp.status_code == 200, resp.text
    resp = await client.post(
        "/api/feeding/mix",
        json={"recipe_code": "FATTENING_50_50", "batch_kg": 100.0},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    for item in await get_inventory(client, headers):
        assert item["qty_on_hand"] == 0.0, item["ingredient"]


async def test_mix_one_kg_batch_rounding(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    await stock_all(client, headers, 100.0)
    resp = await client.post(
        "/api/feeding/mix",
        json={"recipe_code": "MAINTENANCE_75_25", "batch_kg": 1.0},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    green = await inv_item(client, headers, GREEN)
    assert green["qty_on_hand"] == pytest.approx(99.55)  # 100 − 0.45
    soya = await inv_item(client, headers, "Soya DOC")
    assert soya["qty_on_hand"] == pytest.approx(99.97)  # 100 − 0.03


async def test_mix_tiny_batch_rounds_need_to_zero(client: httpx.AsyncClient) -> None:
    """A batch that would consume zero-rounded ingredients is rejected."""
    headers = await owner_with_farm(client)
    resp = await client.post(
        "/api/feeding/mix",
        json={"recipe_code": "FATTENING_50_50", "batch_kg": 0.001},
        headers=headers,
    )
    assert resp.status_code == 400, resp.text
    assert "too small" in resp.json()["detail"].lower()
    assert all(i["qty_on_hand"] == 0.0 for i in await get_inventory(client, headers))


async def test_mix_creep_only_touches_its_four_ingredients(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    await stock_all(client, headers, 500.0)
    resp = await client.post(
        "/api/feeding/mix",
        json={"recipe_code": "CREEP", "batch_kg": 100.0},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    by_ingredient = {
        i["ingredient"]: i["qty_on_hand"] for i in await get_inventory(client, headers)
    }
    assert by_ingredient["Crushed maize"] == pytest.approx(440.0)
    assert by_ingredient["Soya DOC"] == pytest.approx(470.0)
    assert by_ingredient["Maize DDGS"] == pytest.approx(491.0)
    assert by_ingredient[MINERAL] == pytest.approx(499.0)
    assert by_ingredient[GREEN] == 500.0  # roughage untouched by a concentrate-only mix
    assert by_ingredient[STOVER] == 500.0


async def test_mix_sequential_batches_accumulate(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    await stock_all(client, headers, 100.0)
    for _ in range(2):
        resp = await client.post(
            "/api/feeding/mix",
            json={"recipe_code": "FATTENING_50_50", "batch_kg": 100.0},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
    green = await inv_item(client, headers, GREEN)
    assert green["qty_on_hand"] == pytest.approx(40.0)  # 100 − 30 − 30


async def test_mix_eventually_hits_the_stock_guard(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    await stock_all(client, headers, 100.0)
    # 100 kg FATTENING needs 30 kg green; three mixes need 90 kg, the fourth
    # would need 30 from the remaining 10 → refused, stock frozen at 10.
    for _ in range(3):
        resp = await client.post(
            "/api/feeding/mix",
            json={"recipe_code": "FATTENING_50_50", "batch_kg": 100.0},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
    resp = await client.post(
        "/api/feeding/mix",
        json={"recipe_code": "FATTENING_50_50", "batch_kg": 100.0},
        headers=headers,
    )
    assert resp.status_code == 400
    green = await inv_item(client, headers, GREEN)
    assert green["qty_on_hand"] == pytest.approx(10.0)


async def test_mix_is_farm_isolated(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Alpha Farm")
    owner_b = await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    await stock_all(client, owner_a, 100.0)
    resp = await client.post(
        "/api/feeding/mix",
        json={"recipe_code": "FATTENING_50_50", "batch_kg": 100.0},
        headers=owner_a,
    )
    assert resp.status_code == 200, resp.text
    # farm B silos untouched (and still empty)
    assert all(i["qty_on_hand"] == 0.0 for i in await get_inventory(client, owner_b))


async def test_mix_does_not_book_a_finance_transaction(client: httpx.AsyncClient) -> None:
    """Mixing consumes already-purchased stock — only the purchase books a
    FEED expense, not the mix itself."""
    headers = await owner_with_farm(client)
    await stock_all(client, headers, 100.0)  # no price → no expense rows
    resp = await client.post(
        "/api/feeding/mix",
        json={"recipe_code": "FATTENING_50_50", "batch_kg": 50.0},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert await finance_txns(client, headers) == []


async def test_mix_unknown_extra_field_ignored(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    await stock_all(client, headers, 100.0)
    resp = await client.post(
        "/api/feeding/mix",
        json={"recipe_code": "CREEP", "batch_kg": 10.0, "junk": 1},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# GET /api/feeding/inventory
# ---------------------------------------------------------------------------
async def test_inventory_seeded_with_nine_zero_stock_rows(client: httpx.AsyncClient) -> None:
    """SPEC seed data: the 9 ingredients, zero stock, reorder level 100."""
    headers = await owner_with_farm(client)
    inventory = await get_inventory(client, headers)
    assert len(inventory) == 9
    for item in inventory:
        assert item["qty_on_hand"] == 0.0
        assert item["unit"] == "kg"
        assert item["reorder_level"] == 100.0
        assert item["last_purchase_price_per_kg"] is None
        assert item["id"] > 0


async def test_inventory_ordered_by_category_then_ingredient(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    inventory = await get_inventory(client, headers)
    assert [i["ingredient"] for i in inventory] == EXPECTED_INVENTORY_ORDER


async def test_inventory_categories_match_seed(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    inventory = await get_inventory(client, headers)
    by_ingredient = {i["ingredient"]: i["category"] for i in inventory}
    assert by_ingredient[GREEN] == "ROUGHAGE_WET"
    assert by_ingredient[STOVER] == "ROUGHAGE_DRY"
    assert by_ingredient["Groundnut haulms"] == "ROUGHAGE_DRY"
    assert by_ingredient["Crushed maize"] == "CONCENTRATE"
    assert by_ingredient[MINERAL] == "CONCENTRATE"


async def test_inventory_response_shape(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    item = (await get_inventory(client, headers))[0]
    assert set(item) == {
        "id",
        "ingredient",
        "category",
        "unit",
        "qty_on_hand",
        "reorder_level",
        "last_purchase_price_per_kg",
    }


async def test_inventory_rows_are_per_farm(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Alpha Farm")
    owner_b = await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    inv_a = await get_inventory(client, owner_a)
    inv_b = await get_inventory(client, owner_b)
    ids_a = {i["id"] for i in inv_a}
    ids_b = {i["id"] for i in inv_b}
    assert ids_a.isdisjoint(ids_b)  # separate rows per farm
    item = next(i for i in inv_a if i["ingredient"] == "Crushed maize")
    resp = await add_stock(client, owner_a, item["id"], 50.0)
    assert resp.status_code == 200, resp.text
    maize_b = next(
        i for i in await get_inventory(client, owner_b) if i["ingredient"] == "Crushed maize"
    )
    assert maize_b["qty_on_hand"] == 0.0  # no cross-farm leak


# ---------------------------------------------------------------------------
# POST /api/feeding/inventory/{item_id}/add — stock purchase
# ---------------------------------------------------------------------------
async def test_add_stock_happy_increases_qty(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    item = await inv_item(client, headers, "Crushed maize")
    resp = await add_stock(client, headers, item["id"], 50.0)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == item["id"]
    assert body["qty_on_hand"] == 50.0
    assert (await inv_item(client, headers, "Crushed maize"))["qty_on_hand"] == 50.0


async def test_add_stock_accumulates_across_purchases(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    item = await inv_item(client, headers, "DORB")
    for qty in (10.0, 5.5, 0.25):
        resp = await add_stock(client, headers, item["id"], qty)
        assert resp.status_code == 200, resp.text
    assert (await inv_item(client, headers, "DORB"))["qty_on_hand"] == pytest.approx(15.75)


async def test_add_stock_updates_last_purchase_price(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    item = await inv_item(client, headers, "Soya DOC")
    resp = await add_stock(client, headers, item["id"], 20.0, price=45.5)
    assert resp.status_code == 200, resp.text
    assert resp.json()["last_purchase_price_per_kg"] == 45.5
    resp = await add_stock(client, headers, item["id"], 10.0, price=50.0)
    assert resp.status_code == 200, resp.text
    assert resp.json()["last_purchase_price_per_kg"] == 50.0  # latest wins


async def test_add_stock_without_price_leaves_price_none(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    item = await inv_item(client, headers, "DORB")
    resp = await add_stock(client, headers, item["id"], 10.0)
    assert resp.status_code == 200, resp.text
    assert resp.json()["last_purchase_price_per_kg"] is None


async def test_add_stock_without_price_keeps_previous_price(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    item = await inv_item(client, headers, "Soya DOC")
    resp = await add_stock(client, headers, item["id"], 20.0, price=45.5)
    assert resp.status_code == 200, resp.text
    resp = await add_stock(client, headers, item["id"], 5.0)  # no price this time
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["qty_on_hand"] == 25.0
    assert body["last_purchase_price_per_kg"] == 45.5  # not clobbered


async def test_add_stock_books_a_feed_expense(client: httpx.AsyncClient) -> None:
    """SPEC: a purchase entry books an EXPENSE/FEED transaction."""
    headers = await owner_with_farm(client)
    item = await inv_item(client, headers, "Crushed maize")
    resp = await add_stock(client, headers, item["id"], 100.0, price=25.5)
    assert resp.status_code == 200, resp.text
    txns = await finance_txns(client, headers)
    assert len(txns) == 1
    txn = txns[0]
    assert txn["type"] == "EXPENSE"
    assert txn["category"] == "FEED"
    assert txn["amount"] == pytest.approx(2550.0)  # qty × price
    assert txn["date"] == today().isoformat()
    assert "Crushed maize" in txn["notes"]


async def test_add_stock_without_price_books_no_expense(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    item = await inv_item(client, headers, "DORB")
    resp = await add_stock(client, headers, item["id"], 10.0)
    assert resp.status_code == 200, resp.text
    assert await finance_txns(client, headers) == []


async def test_add_stock_expense_amount_rounded_to_paise(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    item = await inv_item(client, headers, "Mineral mix")
    resp = await add_stock(client, headers, item["id"], 3.333, price=33.33)
    assert resp.status_code == 200, resp.text
    txns = await finance_txns(client, headers)
    assert len(txns) == 1
    assert txns[0]["amount"] == round(3.333 * 33.33, 2)  # 111.09


async def test_add_stock_zero_and_negative_qty_422(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    item = await inv_item(client, headers, "DORB")
    for bad in (0, 0.0, -1, -0.001):
        resp = await add_stock(client, headers, item["id"], bad)
        assert resp.status_code == 422, bad
    assert (await inv_item(client, headers, "DORB"))["qty_on_hand"] == 0.0


async def test_add_stock_nonfinite_and_garbage_qty_422(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    item = await inv_item(client, headers, "DORB")
    for bad in ("nan", "inf", "-inf", "1e999", "abc", None):
        resp = await add_stock(client, headers, item["id"], bad)
        assert resp.status_code == 422, bad
    assert (await inv_item(client, headers, "DORB"))["qty_on_hand"] == 0.0


async def test_add_stock_bad_price_422(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    item = await inv_item(client, headers, "DORB")
    for bad in (-5, "nan", "inf", "abc"):
        resp = await add_stock(client, headers, item["id"], 10.0, price=bad)
        assert resp.status_code == 422, bad
    item = await inv_item(client, headers, "DORB")
    assert item["qty_on_hand"] == 0.0
    assert item["last_purchase_price_per_kg"] is None
    assert await finance_txns(client, headers) == []


async def test_add_stock_zero_price_is_accepted(client: httpx.AsyncClient) -> None:
    """An explicit ₹0 restock is real data — it books a ₹0 expense
    and zeroes the last price instead of 422ing."""
    headers = await owner_with_farm(client)
    item = await inv_item(client, headers, "DORB")
    resp = await add_stock(client, headers, item["id"], 10.0, price=0)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["qty_on_hand"] == 10.0
    assert body["last_purchase_price_per_kg"] == 0.0
    txns = await finance_txns(client, headers)
    assert len(txns) == 1
    assert txns[0]["amount"] == 0.0


async def test_add_stock_missing_qty_422(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    item = await inv_item(client, headers, "DORB")
    for payload in [{}, {"price_per_kg": 10.0}]:
        resp = await client.post(
            f"/api/feeding/inventory/{item['id']}/add", json=payload, headers=headers
        )
        assert resp.status_code == 422, payload


async def test_add_stock_nonexistent_item_404(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await add_stock(client, headers, 999_999, 10.0)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Feed inventory item not found"


async def test_add_stock_zero_and_negative_id_404(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    for bad_id in (0, -5):
        resp = await add_stock(client, headers, bad_id, 10.0)
        assert resp.status_code == 404, bad_id


async def test_add_stock_malformed_id_422(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    for bad_id in ("abc", "1.5", "1e3"):
        resp = await add_stock(client, headers, bad_id, 10.0)
        assert resp.status_code == 422, bad_id


async def test_add_stock_cross_farm_item_404_and_untouched(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Alpha Farm")
    owner_b = await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    foreign = await inv_item(client, owner_b, "Crushed maize")
    resp = await add_stock(client, owner_a, foreign["id"], 500.0, price=10.0)
    assert resp.status_code == 404  # never "found but forbidden" — no existence leak
    # farm B's row untouched, no expense booked on either farm
    maize_b = await inv_item(client, owner_b, "Crushed maize")
    assert maize_b["qty_on_hand"] == 0.0
    assert await finance_txns(client, owner_a) == []
    assert await finance_txns(client, owner_b) == []


async def test_add_stock_tiny_and_huge_quantities(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    item = await inv_item(client, headers, "DORB")
    resp = await add_stock(client, headers, item["id"], 0.001)
    assert resp.status_code == 200, resp.text
    assert resp.json()["qty_on_hand"] == pytest.approx(0.001)
    resp = await add_stock(client, headers, item["id"], 1e6)  # exactly the 1e6 kg quantity cap
    assert resp.status_code == 200, resp.text
    assert resp.json()["qty_on_hand"] == pytest.approx(1e6 + 0.001)
    resp = await add_stock(client, headers, item["id"], 1e9)  # beyond the cap — B2 bound
    assert resp.status_code == 422


async def test_add_stock_qty_rounded_to_three_decimals(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    item = await inv_item(client, headers, "DORB")
    resp = await add_stock(client, headers, item["id"], 10.1234)
    assert resp.status_code == 200, resp.text
    assert resp.json()["qty_on_hand"] == pytest.approx(10.123)


async def test_add_stock_unknown_extra_field_ignored(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    item = await inv_item(client, headers, "DORB")
    resp = await client.post(
        f"/api/feeding/inventory/{item['id']}/add",
        json={"qty_kg": 5.0, "junk": "x" * 10_000},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert "junk" not in resp.json()


# ---------------------------------------------------------------------------
# Auth & tenancy on every feeding endpoint
# ---------------------------------------------------------------------------
ENDPOINTS: list[tuple[str, str, dict | None]] = [
    ("GET", "/api/feeding/plan", None),
    ("GET", "/api/feeding/recipes", None),
    ("GET", "/api/feeding/inventory", None),
    ("POST", "/api/feeding/settings", {"bucket": "BREEDING", "daily_kg_per_head": 1.5}),
    ("POST", "/api/feeding/dispense", {"bucket": "BREEDING", "shift": "MORNING", "qty_kg": 5}),
    ("POST", "/api/feeding/mix", {"recipe_code": "CREEP", "batch_kg": 10}),
    ("POST", "/api/feeding/inventory/1/add", {"qty_kg": 10}),
]


async def _hit(
    client: httpx.AsyncClient, method: str, url: str, body: dict | None, headers: dict | None = None
) -> httpx.Response:
    if method == "GET":
        return await client.get(url, headers=headers or {})
    return await client.post(url, json=body, headers=headers or {})


async def test_anonymous_gets_401_on_every_endpoint(client: httpx.AsyncClient) -> None:
    for method, url, body in ENDPOINTS:
        resp = await _hit(client, method, url, body)
        assert resp.status_code == 401, url
        assert resp.json()["detail"] == "Missing bearer token"


async def test_invalid_token_gets_401(client: httpx.AsyncClient) -> None:
    headers = {"Authorization": "Bearer not-a-real-token"}
    for method, url, body in ENDPOINTS:
        resp = await _hit(client, method, url, body, headers)
        assert resp.status_code == 401, url
        assert resp.json()["detail"] == "Invalid or expired token"


async def test_missing_farm_header_gets_422(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    auth_only = {"Authorization": headers["Authorization"]}
    for method, url, body in ENDPOINTS:
        resp = await _hit(client, method, url, body, auth_only)
        # The header is required by the contract (422, not 400).
        assert resp.status_code == 422, url
        assert "x-farm-id" in str(resp.json()["detail"]).lower()


async def test_non_integer_farm_header_gets_400(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    bad = {"Authorization": headers["Authorization"], "X-Farm-Id": "abc"}
    resp = await client.get("/api/feeding/plan", headers=bad)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "X-Farm-Id must be an integer"


async def test_out_of_range_farm_header_gets_400(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    bad = {"Authorization": headers["Authorization"], "X-Farm-Id": str(2**62)}
    resp = await client.get("/api/feeding/plan", headers=bad)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "X-Farm-Id out of range"


async def test_nonexistent_farm_gets_404(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    bad = {"Authorization": headers["Authorization"], "X-Farm-Id": "424242"}
    resp = await client.get("/api/feeding/plan", headers=bad)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Farm not found"


async def test_non_member_farm_gets_404(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Alpha Farm")
    owner_b = await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    intruder = {"Authorization": owner_b["Authorization"], "X-Farm-Id": owner_a["X-Farm-Id"]}
    for method, url, body in ENDPOINTS:
        resp = await _hit(client, method, url, body, intruder)
        # Forbidden farms answer exactly like unknown ones.
        assert resp.status_code == 404, url
        assert resp.json()["detail"] == "Farm not found"


async def test_feeder_role_can_view_everything(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    feeder = await worker_headers(client, owner, "FEEDER", "feeder@farm.in")
    for url in ("/api/feeding/plan", "/api/feeding/recipes", "/api/feeding/inventory"):
        resp = await client.get(url, headers=feeder)
        assert resp.status_code == 200, url


async def test_feeder_role_can_manage_feeding(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await mix_ready(client, owner, "CREEP")
    feeder = await worker_headers(client, owner, "FEEDER", "feeder@farm.in")
    resp = await client.post(
        "/api/feeding/settings",
        json={"bucket": "BREEDING", "daily_kg_per_head": 2.0},
        headers=feeder,
    )
    assert resp.status_code == 204, resp.text
    resp = await dispense(client, feeder, recipe_code="CREEP")
    assert resp.status_code == 201, resp.text
    item = await inv_item(client, owner, "Crushed maize")
    resp = await add_stock(client, feeder, item["id"], 200.0)
    assert resp.status_code == 200, resp.text
    await stock_all(client, owner, 100.0)  # owner fills the other silos
    resp = await client.post(
        "/api/feeding/mix",
        json={"recipe_code": "CREEP", "batch_kg": 10.0},
        headers=feeder,
    )
    assert resp.status_code == 200, resp.text


async def test_feeder_role_cannot_touch_animals(client: httpx.AsyncClient) -> None:
    """Feed → FEEDER duty split: the feeder has no animal permissions."""
    owner = await owner_with_farm(client)
    feeder = await worker_headers(client, owner, "FEEDER", "feeder@farm.in")
    resp = await client.post(
        "/api/animals",
        json={"tag_number": "X-1", "sex": "F", "source": "PURCHASED", "current_bucket": "BREEDING"},
        headers=feeder,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Missing permission: animals.create"


async def test_cleaner_forbidden_from_feeding(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    cleaner = await worker_headers(client, owner, "CLEANER", "cleaner@farm.in")
    for url in ("/api/feeding/plan", "/api/feeding/recipes", "/api/feeding/inventory"):
        resp = await client.get(url, headers=cleaner)
        assert resp.status_code == 403, url
        assert resp.json()["detail"] == "Missing permission: feeding.view"
    for method, url, body in ENDPOINTS:
        if method == "POST":
            resp = await _hit(client, method, url, body, cleaner)
            assert resp.status_code == 403, url
            assert resp.json()["detail"] == "Missing permission: feeding.manage"


async def test_mover_forbidden_from_feeding(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    mover = await worker_headers(client, owner, "MOVER", "mover@farm.in")
    resp = await client.get("/api/feeding/plan", headers=mover)
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Missing permission: feeding.view"
    resp = await dispense(client, mover)
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Missing permission: feeding.manage"


async def test_vet_forbidden_from_feeding(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    vet = await worker_headers(client, owner, "VET", "vet@farm.in")
    resp = await client.get("/api/feeding/plan", headers=vet)
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Missing permission: feeding.view"
    item_id = (await get_inventory(client, owner))[0]["id"]
    resp = await add_stock(client, vet, item_id, 10.0)
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Missing permission: feeding.manage"


# ---------------------------------------------------------------------------
# End-to-end flow: purchase → mix → plan → dispense → finance
# ---------------------------------------------------------------------------
async def test_full_feeding_day_flow(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    # 1. purchase green fodder with a price (books the only FEED expense)
    green = await inv_item(client, headers, GREEN)
    resp = await add_stock(client, headers, green["id"], 500.0, price=4.0)
    assert resp.status_code == 200, resp.text
    # 2. top up the rest price-free and mix 100 kg of maintenance TMR
    for item in await get_inventory(client, headers):
        if item["ingredient"] != GREEN:
            resp = await add_stock(client, headers, item["id"], 500.0)
            assert resp.status_code == 200, resp.text
    resp = await client.post(
        "/api/feeding/mix",
        json={"recipe_code": "MAINTENANCE_75_25", "batch_kg": 100.0},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert (await inv_item(client, headers, GREEN))["qty_on_hand"] == pytest.approx(455.0)
    # 3. two breeding does on a 2.0 kg/head override → 4.0 kg/day, 1.6/0.8/1.6
    for i in range(2):
        await make_animal(client, headers, f"D-{i}")
    resp = await client.post(
        "/api/feeding/settings",
        json={"bucket": "BREEDING", "daily_kg_per_head": 2.0},
        headers=headers,
    )
    assert resp.status_code == 204, resp.text
    line = (await get_plan(client, headers))["lines"][0]
    assert line["daily_kg"] == pytest.approx(4.0)
    assert [s["kg"] for s in line["shifts"]] == [1.6, 0.8, 1.6]
    # 4. dispense the morning shift against the plan
    resp = await dispense(
        client, headers, shift="MORNING", recipe_code="MAINTENANCE_75_25", qty_kg=1.6
    )
    assert resp.status_code == 201, resp.text
    records = (await get_plan(client, headers))["records"]
    assert len(records) == 1
    assert records[0]["qty_kg"] == 1.6
    # 5. exactly one FEED expense: the priced purchase (500 × 4.0)
    txns = await finance_txns(client, headers)
    assert len(txns) == 1
    assert txns[0]["category"] == "FEED"
    assert txns[0]["amount"] == pytest.approx(2000.0)


# ---------------------------------------------------------------------------
# — an explicit ₹0/kg restock is a real price, not "no price"
# ---------------------------------------------------------------------------
# add_feed_stock used `if price_per_kg:`, dropping an explicit 0: no ₹0
# expense was booked and last_purchase_price_per_kg kept its stale value —
# inconsistent with create_purchase_batch's careful explicit-₹0 handling.
# Service-level test: the endpoint schema (StockAddIn.price_per_kg is a
# PositiveFloat) currently rejects ₹0 with a 422, so the fixed branch is
# exercised by calling the service the way a loosened schema would.
async def test_add_stock_zero_price_books_zero_expense(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    item = await inv_item(client, headers, MINERAL)
    farm_id = int(headers["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        farm = (await db.execute(select(Farm).where(Farm.id == farm_id))).scalar_one()
        row = (
            await db.execute(select(FeedInventory).where(FeedInventory.id == item["id"]))
        ).scalar_one()
        row.last_purchase_price_per_kg = 42.0  # a stale price the ₹0 must zero out
        await add_feed_stock(db, farm, row, 10, 0)
        await db.commit()
    body = await inv_item(client, headers, MINERAL)
    assert body["qty_on_hand"] == 10
    assert body["last_purchase_price_per_kg"] == 0  # zeroed, not left stale
    feed_txns = [t for t in await finance_txns(client, headers) if t["category"] == "FEED"]
    assert len(feed_txns) == 1
    assert feed_txns[0]["amount"] == 0  # an explicit ₹0 books a ₹0 expense
