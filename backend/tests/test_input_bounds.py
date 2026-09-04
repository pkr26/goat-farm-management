"""Input-bounds regression tests.

Money/quantity fields were unbounded positives: `1e308 * 1e308` overflows to
`inf` inside derived values (feed-purchase qty × price) and the poisoned row
then breaks every later `GET /api/finance` on JSON serialization. Every such
field now has a domain cap (money ≤ ₹1e9, feed quantities ≤ 1e6 kg, weights
≤ 1000 kg), and the finance readers skip non-finite legacy rows.
"""

import json
from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy.exc import DBAPIError

from app.core.config import get_settings
from app.db import get_sessionmaker
from app.models import Transaction
from app.schemas.common import MAX_INT32_ID, MAX_PAGE_OFFSET
from app.utils import today

from .conftest import owner_with_farm, register

MONEY_CAP = 1_000_000_000
QTY_KG_CAP = 1_000_000
WEIGHT_KG_CAP = 1000


@pytest.mark.parametrize(
    ("url", "parameter"),
    [
        ("/api/animals", "offset"),
        ("/api/breeding", "offset"),
        ("/api/breeding/candidates?kind=doe", "offset"),
        ("/api/feeding/records", "offset"),
        ("/api/finance", "offset"),
        ("/api/health/events", "offset"),
        ("/api/kidding", "offset"),
        ("/api/purchases", "offset"),
        ("/api/tasks", "today_offset"),
    ],
)
async def test_page_offsets_have_a_driver_safe_upper_bound(
    client: httpx.AsyncClient, url: str, parameter: str
) -> None:
    owner = await owner_with_farm(client)
    response = await client.get(
        url,
        params={parameter: MAX_PAGE_OFFSET + 1},
        headers=owner,
    )
    assert response.status_code == 422, response.text


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("GET", "/api/animals/-999999999999999999999"),
        ("POST", "/api/tasks/-999999999999999999999/complete"),
        ("PUT", "/api/team/workers/-999999999999999999999/status"),
        ("GET", "/api/simulation/scenarios/-999999999999999999999"),
    ],
)
async def test_negative_out_of_range_path_ids_never_reach_asyncpg(
    client: httpx.AsyncClient, method: str, url: str
) -> None:
    owner = await owner_with_farm(client)
    # Worker status is now desired-state PUT, so provide its required body;
    # otherwise request validation would stop at the missing JSON payload and
    # this test would never exercise the path-id bound.
    kwargs = {"json": {"is_active": True}} if method == "PUT" else {}
    response = await client.request(method, url, headers=owner, **kwargs)
    assert response.status_code == 404, response.text


async def test_simulation_compare_rejects_out_of_range_ids(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    response = await client.get(
        "/api/simulation/scenarios/compare",
        params={"ids": "-999999999999999999999"},
        headers=owner,
    )
    assert response.status_code == 400, response.text
    assert "positive PostgreSQL integer" in response.json()["detail"]


async def test_out_of_int32_purchase_batch_id_is_a_domain_conflict(
    client: httpx.AsyncClient,
) -> None:
    """A batch-scope health write carrying a schema-valid but impossible batch
    id stays the documented 409. `BoundedId` deliberately admits ids up to
    2**62, so the writer's own int32 guard is what keeps the batch lookup away
    from asyncpg — without it the id reaches `purchase_batches.id = $1::INTEGER`
    and the driver's out-of-int32-range DataError becomes a 500."""
    owner = await owner_with_farm(client)
    batch = await client.post(
        "/api/purchases/new",
        json={"date": iso(today()), "supplier": "Kurnool Traders", "count": 2},
        headers=owner,
    )
    assert batch.status_code == 201, batch.text
    detail = await client.get(f"/api/purchases/{batch.json()['id']}", headers=owner)
    assert detail.status_code == 200, detail.text
    # The reviewed ids must be genuinely lockable, otherwise the request stops
    # at the earlier snapshot check and never reaches the guarded batch lookup.
    animal_ids = sorted(animal["id"] for animal in detail.json()["animals"])
    assert len(animal_ids) == 2, detail.text
    response = await client.post(
        "/api/health/events",
        json={
            "scope": "batch",
            "purchase_batch_id": MAX_INT32_ID + 1,
            "expected_animal_ids": animal_ids,
            "type": "VACCINE",
        },
        headers=owner,
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "Reviewed target snapshot is stale"
    events = await client.get("/api/health/events", headers=owner)
    assert events.status_code == 200, events.text
    assert events.json()["events"] == []  # nothing was dosed


async def test_simulation_compare_and_request_target_are_bounded_before_parsing(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    compare = await client.get(
        "/api/simulation/scenarios/compare",
        params={"ids": ",".join("1" for _index in range(100))},
        headers=owner,
    )
    assert compare.status_code == 422, compare.text

    target = await client.get(
        "/api/animals",
        params={"q": "x" * 9_000},
        headers=owner,
    )
    assert target.status_code == 414, target.text
    assert target.json() == {"detail": "Request target is too long"}


async def test_over_long_request_target_keeps_the_standard_error_envelope(
    client: httpx.AsyncClient,
) -> None:
    """The 414 emitted before routing carries the same `{"detail": ...}` body
    every other handler emits — the SPA reads that key, so a null body or a
    renamed/reworded key would silently swallow the error."""
    owner = await owner_with_farm(client)
    response = await client.get(
        "/api/animals",
        params={"q": "x" * (get_settings().max_request_target_bytes + 1)},
        headers=owner,
    )
    assert response.status_code == 414, response.text
    assert response.json() == {"detail": "Request target is too long"}


async def test_max_calendar_month_filter_is_empty_not_500(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    response = await client.get("/api/finance", params={"month": "9999-12"}, headers=owner)
    assert response.status_code == 200, response.text
    assert response.json()["transactions"] == []


async def phoenix_owner_with_farm(client: httpx.AsyncClient) -> dict:
    headers = await register(client)
    response = await client.post(
        "/api/auth/farms",
        json={"name": "Arizona Farm", "timezone": "America/Phoenix"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return headers | {"X-Farm-Id": str(response.json()["id"])}


async def test_finance_rejects_next_farm_day_for_create_and_correction(
    client: httpx.AsyncClient,
) -> None:
    owner = await phoenix_owner_with_farm(client)
    local_today = today("America/Phoenix")
    future = iso(local_today + timedelta(days=1))
    base = {"type": "EXPENSE", "category": "FEED", "amount": 10}

    create = await client.post("/api/finance/new", json=base | {"date": future}, headers=owner)
    assert create.status_code == 422, create.text

    original = await client.post(
        "/api/finance/new",
        json=base | {"date": iso(local_today)},
        headers=owner,
    )
    assert original.status_code == 201, original.text
    correction = await client.post(
        f"/api/finance/transactions/{original.json()['id']}/correct",
        json=base | {"date": future, "reason": "Wrong receipt"},
        headers=owner,
    )
    assert correction.status_code == 422, correction.text
    rows = (await client.get("/api/finance", headers=owner)).json()["transactions"]
    assert len(rows) == 1 and rows[0]["voided_at"] is None


async def test_feeding_rejects_next_farm_day_without_recording(
    client: httpx.AsyncClient,
) -> None:
    owner = await phoenix_owner_with_farm(client)
    future = iso(today("America/Phoenix") + timedelta(days=1))
    response = await client.post(
        "/api/feeding/dispense",
        json={
            "bucket": "BREEDING",
            "shift": "MORNING",
            "recipe_code": "DRY_ROUGHAGE_ONLY",
            "qty_kg": 1,
            "date": future,
        },
        headers=owner,
    )
    assert response.status_code == 422, response.text
    history = await client.get("/api/feeding/records", headers=owner)
    assert history.status_code == 200
    assert history.json()["records"] == []


def iso(d: date) -> str:
    return d.isoformat()


async def post_raw_json(
    client: httpx.AsyncClient, url: str, payload: dict, headers: dict
) -> httpx.Response:
    """POST with a manually serialized body: httpx's `json=` refuses non-finite
    floats, but Python's json.dumps emits the non-standard `NaN`/`Infinity`
    tokens verbatim — exactly the garbage an adversary would send."""
    return await client.post(
        url,
        content=json.dumps(payload).encode(),
        headers=headers | {"content-type": "application/json"},
    )


async def make_animal(client: httpx.AsyncClient, headers: dict, tag: str = "A-001") -> int:
    resp = await client.post(
        "/api/animals",
        json={"tag_number": tag, "sex": "F", "source": "PURCHASED", "current_bucket": "FOUNDATION"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def inventory_item_id(client: httpx.AsyncClient, headers: dict) -> int:
    resp = await client.get("/api/feeding/inventory", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()[0]["id"]


# ---------------------------------------------------------------------------
# Money fields (cap ₹1e9)
# ---------------------------------------------------------------------------
async def test_transaction_amount_bounds(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    base = {"date": iso(today()), "type": "INCOME", "category": "OTHER"}
    for bad in (1e308, 1e309, MONEY_CAP + 0.01, float("nan")):
        resp = await post_raw_json(client, "/api/finance/new", base | {"amount": bad}, owner)
        assert resp.status_code == 422, bad
    # boundary: exactly the cap is accepted
    resp = await client.post("/api/finance/new", json=base | {"amount": MONEY_CAP}, headers=owner)
    assert resp.status_code == 201, resp.text
    assert resp.json()["amount"] == MONEY_CAP


async def test_animal_price_bounds(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    base = {"tag_number": "G-1", "sex": "F", "source": "PURCHASED", "current_bucket": "FOUNDATION"}
    for bad in (1e308, 1e309, MONEY_CAP + 0.01, float("nan")):
        resp = await post_raw_json(client, "/api/animals", base | {"purchase_price": bad}, owner)
        assert resp.status_code == 422, bad
    assert (await client.get("/api/animals", headers=owner)).json()["total"] == 0
    resp = await client.post(
        "/api/animals", json=base | {"purchase_price": MONEY_CAP}, headers=owner
    )
    assert resp.status_code == 201, resp.text
    # BORN + historical import keeps the animal out of the 45-day quarantine
    # pen, whose sale fence would (correctly) 409 the cap-bound probes below.
    resp = await client.post(
        "/api/animals",
        json={
            "tag_number": "S-1",
            "sex": "F",
            "source": "BORN",
            "current_bucket": "FOUNDATION",
            "historical_import_reason": "Price-bounds fixture",
        },
        headers=owner,
    )
    assert resp.status_code == 201, resp.text
    aid = resp.json()["id"]
    for bad in (1e308, 1e309, MONEY_CAP + 0.01, float("nan")):
        resp = await post_raw_json(
            client, f"/api/animals/{aid}/status", {"new_status": "SOLD", "sale_price": bad}, owner
        )
        assert resp.status_code == 422, bad
    resp = await client.get(f"/api/animals/{aid}", headers=owner)
    assert resp.json()["animal"]["status"] == "ACTIVE"  # never sold by garbage
    resp = await client.post(
        f"/api/animals/{aid}/status",
        json={"new_status": "SOLD", "sale_price": MONEY_CAP},
        headers=owner,
    )
    assert resp.status_code == 200, resp.text


async def test_health_cost_bounds(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    aid = await make_animal(client, owner)
    base = {"scope": "animal", "animal_id": aid, "date": iso(today()), "type": "TREATMENT"}
    for bad in (1e308, 1e309, MONEY_CAP + 0.01, float("nan")):
        resp = await post_raw_json(client, "/api/health/events", base | {"cost": bad}, owner)
        assert resp.status_code == 422, bad
    assert (await client.get("/api/health/events", headers=owner)).json()["events"] == []
    resp = await client.post("/api/health/events", json=base | {"cost": MONEY_CAP}, headers=owner)
    assert resp.status_code == 201, resp.text


async def test_feed_price_per_kg_bounds(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    item_id = await inventory_item_id(client, owner)
    for bad in (1e308, 1e309, MONEY_CAP + 0.01, float("nan")):
        resp = await post_raw_json(
            client,
            f"/api/feeding/inventory/{item_id}/add",
            {"qty_kg": 1, "price_per_kg": bad},
            owner,
        )
        assert resp.status_code == 422, bad
    resp = await client.post(
        f"/api/feeding/inventory/{item_id}/add",
        json={"qty_kg": 1, "price_per_kg": MONEY_CAP},
        headers=owner,
    )
    assert resp.status_code == 200, resp.text


async def test_purchase_total_price_bounds(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    base = {"date": iso(today()), "count": 1, "create_animals": False}
    for bad in (1e308, 1e309, MONEY_CAP + 0.01, float("nan")):
        resp = await post_raw_json(client, "/api/purchases/new", base | {"total_price": bad}, owner)
        assert resp.status_code == 422, bad
    resp = await client.post(
        "/api/purchases/new", json=base | {"total_price": MONEY_CAP}, headers=owner
    )
    assert resp.status_code == 201, resp.text


async def test_max_length_supplier_cannot_overflow_quarantine_task_titles(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    response = await client.post(
        "/api/purchases/new",
        json={
            "date": iso(today()),
            "supplier": "S" * 120,
            "count": 1,
            "create_animals": True,
        },
        headers=owner,
    )
    assert response.status_code == 201, response.text
    detail = await client.get(f"/api/purchases/{response.json()['id']}", headers=owner)
    assert detail.status_code == 200, detail.text
    titles = [task["title"] for task in detail.json()["tasks"]]
    assert len(titles) == 8
    assert all(len(title) <= 200 for title in titles)


# ---------------------------------------------------------------------------
# Feed quantity fields (cap 1e6 kg)
# ---------------------------------------------------------------------------
async def test_dispense_qty_bounds(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    inventory = await client.get("/api/feeding/inventory", headers=owner)
    assert inventory.status_code == 200, inventory.text
    dry_stover = next(row for row in inventory.json() if row["ingredient"] == "Dry jowar stover")
    stocked = await client.post(
        f"/api/feeding/inventory/{dry_stover['id']}/add",
        json={"qty_kg": QTY_KG_CAP},
        headers=owner,
    )
    assert stocked.status_code == 200, stocked.text
    base = {
        "bucket": "BREEDING",
        "shift": "MORNING",
        "recipe_code": "DRY_ROUGHAGE_ONLY",
    }
    for bad in (1e308, 1e309, QTY_KG_CAP + 1, float("nan")):
        resp = await post_raw_json(client, "/api/feeding/dispense", base | {"qty_kg": bad}, owner)
        assert resp.status_code == 422, bad
    resp = await client.post(
        "/api/feeding/dispense", json=base | {"qty_kg": QTY_KG_CAP}, headers=owner
    )
    assert resp.status_code == 201, resp.text


async def test_stock_add_qty_bounds(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    item_id = await inventory_item_id(client, owner)
    for bad in (1e308, 1e309, QTY_KG_CAP + 1, float("nan")):
        resp = await post_raw_json(
            client, f"/api/feeding/inventory/{item_id}/add", {"qty_kg": bad}, owner
        )
        assert resp.status_code == 422, bad
    item = next(
        i
        for i in (await client.get("/api/feeding/inventory", headers=owner)).json()
        if i["id"] == item_id
    )
    assert item["qty_on_hand"] == 0  # nothing landed
    resp = await client.post(
        f"/api/feeding/inventory/{item_id}/add", json={"qty_kg": QTY_KG_CAP}, headers=owner
    )
    assert resp.status_code == 200, resp.text


async def test_mix_batch_bounds(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    base = {"recipe_code": "FATTENING_50_50"}
    for bad in (1e308, 1e309, QTY_KG_CAP + 1, float("nan")):
        resp = await post_raw_json(client, "/api/feeding/mix", base | {"batch_kg": bad}, owner)
        assert resp.status_code == 422, bad
    # exactly the cap passes the schema; the service then refuses on stock
    resp = await client.post(
        "/api/feeding/mix", json=base | {"batch_kg": QTY_KG_CAP}, headers=owner
    )
    assert resp.status_code == 400, resp.text


async def test_feed_setting_daily_kg_bounds(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    base = {"bucket": "BREEDING"}
    for bad in (1e308, 1e309, QTY_KG_CAP + 1, float("nan")):
        resp = await post_raw_json(
            client, "/api/feeding/settings", base | {"daily_kg_per_head": bad}, owner
        )
        assert resp.status_code == 422, bad
    resp = await client.post(
        "/api/feeding/settings", json=base | {"daily_kg_per_head": QTY_KG_CAP}, headers=owner
    )
    assert resp.status_code == 204, resp.text


# ---------------------------------------------------------------------------
# Weight fields (schema cap 1000 kg; species-scaled adult cap on top)
# ---------------------------------------------------------------------------
async def test_weight_bounds(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    aid = await make_animal(client, owner)
    for bad in (1e308, 1e309, WEIGHT_KG_CAP + 0.5, float("nan")):
        resp = await post_raw_json(client, f"/api/animals/{aid}/weight", {"weight_kg": bad}, owner)
        assert resp.status_code == 422, bad
    resp = await client.get(f"/api/animals/{aid}", headers=owner)
    assert resp.json()["weights"] == []
    # The species-scaled adult cap (goat: 150 kg) is enforced below the schema
    # cap — a goat-scale farm refuses even schema-valid giant readings.
    resp = await client.post(
        f"/api/animals/{aid}/weight", json={"weight_kg": WEIGHT_KG_CAP}, headers=owner
    )
    assert resp.status_code == 422, resp.text
    assert "credible adult scale" in resp.json()["detail"]
    resp = await client.post(f"/api/animals/{aid}/weight", json={"weight_kg": 60.0}, headers=owner)
    assert resp.status_code == 201, resp.text


async def test_animal_create_weight_bounds(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    base = {"tag_number": "G-1", "sex": "F", "source": "PURCHASED", "current_bucket": "FOUNDATION"}
    for field in ("birth_weight", "weight_kg"):
        # Birth weight is valid only for a farm-born animal; entry weight is
        # valid for either source. Keep the fixture provenance coherent so
        # this test reaches the numeric boundary it is intended to exercise.
        field_base = base | (
            {
                "source": "BORN",
                "historical_import_reason": "Legacy numeric-boundary fixture",
            }
            if field == "birth_weight"
            else {}
        )
        for bad in (1e308, 1e309, WEIGHT_KG_CAP + 0.5, float("nan")):
            resp = await post_raw_json(client, "/api/animals", field_base | {field: bad}, owner)
            assert resp.status_code == 422, (field, bad)
        # The schema cap is 1000 kg, but the species band is tighter (goat:
        # 0.5-8 kg birth, 150 kg adult) — schema-valid giants are refused on
        # this path too, closing the create-form bypass of the band checks.
        resp = await client.post(
            "/api/animals",
            json=field_base | {"tag_number": f"OK-{field}", field: WEIGHT_KG_CAP},
            headers=owner,
        )
        assert resp.status_code == 422, (field, resp.text)
        in_band = 4.5 if field == "birth_weight" else 60.0
        resp = await client.post(
            "/api/animals",
            json=field_base | {"tag_number": f"OK-{field}", field: in_band},
            headers=owner,
        )
        assert resp.status_code == 201, (field, resp.text)


async def test_kidding_birth_weight_bounds(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    # breeding-ready doe (>=10 months old, 26 kg entry weight) + buck
    dob = today() - timedelta(days=800)
    doe = await client.post(
        "/api/animals",
        json={
            "tag_number": "D-1",
            "sex": "F",
            "source": "PURCHASED",
            "current_bucket": "FOUNDATION",
            "date_of_birth": iso(dob),
            "weight_kg": 26,
            "weight_date": iso(dob),
            "historical_import_reason": "Existing-herd test fixture",
        },
        headers=owner,
    )
    assert doe.status_code == 201, doe.text
    buck = await client.post(
        "/api/animals",
        json={
            "tag_number": "B-1",
            "sex": "M",
            "source": "PURCHASED",
            "current_bucket": "BREEDING",
            "date_of_birth": iso(dob),
            "weight_kg": 30,
            "weight_date": iso(dob),
            "historical_import_reason": "Existing-herd test fixture",
        },
        headers=owner,
    )
    assert buck.status_code == 201, buck.text
    resp = await client.post(
        "/api/breeding",
        json={
            "doe_id": doe.json()["id"],
            "buck_id": buck.json()["id"],
            # backdated so the valid-arm kidding lands at a realistic ~150d gestation
            "breeding_date": iso(today() - timedelta(days=150)),
        },
        headers=owner,
    )
    assert resp.status_code == 201, resp.text
    br_id = resp.json()["id"]
    resp = await client.post(
        f"/api/breeding/{br_id}/ultrasound",
        json={"pregnant": True, "kid_count": 1},
        headers=owner,
    )
    assert resp.status_code == 200, resp.text

    def kidding_payload(weight: object) -> dict:
        return {
            "breeding_record_id": br_id,
            "date": iso(today()),
            "kids": [{"sex": "M", "birth_weight": weight}],
        }

    for bad in (1e308, 1e309, WEIGHT_KG_CAP + 0.5, float("nan")):
        resp = await post_raw_json(client, "/api/kidding", kidding_payload(bad), owner)
        assert resp.status_code == 422, bad
    # Species-banded birth weight (goat: 0.5-8 kg) sits below the schema cap —
    # a schema-valid 1000 kg kid is refused as not a credible newborn.
    resp = await client.post("/api/kidding", json=kidding_payload(WEIGHT_KG_CAP), headers=owner)
    assert resp.status_code == 422, resp.text
    assert "not a credible newborn weight" in resp.json()["detail"]
    resp = await client.post("/api/kidding", json=kidding_payload(3.5), headers=owner)
    assert resp.status_code == 201, resp.text


async def test_purchase_avg_weight_bounds(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    base = {"date": iso(today()), "count": 1, "create_animals": False}
    for bad in (1e308, 1e309, WEIGHT_KG_CAP + 0.5, float("nan")):
        resp = await post_raw_json(
            client, "/api/purchases/new", base | {"avg_weight_kg": bad}, owner
        )
        assert resp.status_code == 422, bad
    # Species-scaled adult cap (goat: 150 kg) applies below the schema cap.
    resp = await client.post(
        "/api/purchases/new", json=base | {"avg_weight_kg": WEIGHT_KG_CAP}, headers=owner
    )
    assert resp.status_code == 422, resp.text
    assert "credible adult scale" in resp.json()["detail"]
    resp = await client.post(
        "/api/purchases/new", json=base | {"avg_weight_kg": 45.0}, headers=owner
    )
    assert resp.status_code == 201, resp.text


# ---------------------------------------------------------------------------
# Defense in depth: the exact-money database type rejects poisoned rows
# ---------------------------------------------------------------------------
async def test_finance_survives_previously_poisoned_rows(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    for type_, amount in (("INCOME", 5000), ("EXPENSE", 1000)):
        resp = await client.post(
            "/api/finance/new",
            json={"date": iso(today()), "type": type_, "category": "OTHER", "amount": amount},
            headers=owner,
        )
        assert resp.status_code == 201, resp.text
    # The legacy Float schema could store non-finite values. The hardening
    # migration sanitizes those rows before converting to NUMERIC(14, 2);
    # after migration even a direct ORM write is rejected by PostgreSQL.
    farm_id = int(owner["X-Farm-Id"])
    for amount in (float("inf"), float("nan")):
        async with get_sessionmaker()() as db:
            db.add(
                Transaction(
                    farm_id=farm_id,
                    date=today(),
                    type="INCOME",
                    category="OTHER",
                    amount=amount,
                )
            )
            with pytest.raises(DBAPIError):
                await db.commit()
            await db.rollback()

    resp = await client.get("/api/finance", headers=owner)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_income"] == 5000
    assert body["total_expense"] == 1000
    assert len(body["transactions"]) == 2
