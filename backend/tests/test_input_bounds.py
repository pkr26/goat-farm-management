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

from app.db import get_sessionmaker
from app.models import Transaction
from app.utils import today

from .conftest import owner_with_farm

MONEY_CAP = 1_000_000_000
QTY_KG_CAP = 1_000_000
WEIGHT_KG_CAP = 1000


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
    aid = await make_animal(client, owner, tag="S-1")
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


# ---------------------------------------------------------------------------
# Feed quantity fields (cap 1e6 kg)
# ---------------------------------------------------------------------------
async def test_dispense_qty_bounds(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    base = {"bucket": "BREEDING", "shift": "MORNING"}
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
# Weight fields (cap 1000 kg)
# ---------------------------------------------------------------------------
async def test_weight_bounds(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    aid = await make_animal(client, owner)
    for bad in (1e308, 1e309, WEIGHT_KG_CAP + 0.5, float("nan")):
        resp = await post_raw_json(client, f"/api/animals/{aid}/weight", {"weight_kg": bad}, owner)
        assert resp.status_code == 422, bad
    resp = await client.get(f"/api/animals/{aid}", headers=owner)
    assert resp.json()["weights"] == []
    resp = await client.post(
        f"/api/animals/{aid}/weight", json={"weight_kg": WEIGHT_KG_CAP}, headers=owner
    )
    assert resp.status_code == 201, resp.text


async def test_animal_create_weight_bounds(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    base = {"tag_number": "G-1", "sex": "F", "source": "PURCHASED", "current_bucket": "FOUNDATION"}
    for field in ("birth_weight", "weight_kg"):
        # Birth weight is valid only for a farm-born animal; entry weight is
        # valid for either source. Keep the fixture provenance coherent so
        # this test reaches the numeric boundary it is intended to exercise.
        field_base = base | ({"source": "BORN"} if field == "birth_weight" else {})
        for bad in (1e308, 1e309, WEIGHT_KG_CAP + 0.5, float("nan")):
            resp = await post_raw_json(client, "/api/animals", field_base | {field: bad}, owner)
            assert resp.status_code == 422, (field, bad)
        resp = await client.post(
            "/api/animals",
            json=field_base | {"tag_number": f"OK-{field}", field: WEIGHT_KG_CAP},
            headers=owner,
        )
        assert resp.status_code == 201, (field, resp.text)


async def test_kidding_birth_weight_bounds(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    # breeding-ready doe (>=10 months old, 26 kg entry weight) + buck
    doe = await client.post(
        "/api/animals",
        json={
            "tag_number": "D-1",
            "sex": "F",
            "source": "PURCHASED",
            "current_bucket": "FOUNDATION",
            "date_of_birth": iso(today() - timedelta(days=400)),
            "weight_kg": 26,
        },
        headers=owner,
    )
    assert doe.status_code == 201, doe.text
    buck = await client.post(
        "/api/animals",
        json={"tag_number": "B-1", "sex": "M", "source": "PURCHASED", "current_bucket": "BREEDING"},
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
    resp = await client.post("/api/kidding", json=kidding_payload(WEIGHT_KG_CAP), headers=owner)
    assert resp.status_code == 201, resp.text


async def test_purchase_avg_weight_bounds(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    base = {"date": iso(today()), "count": 1, "create_animals": False}
    for bad in (1e308, 1e309, WEIGHT_KG_CAP + 0.5, float("nan")):
        resp = await post_raw_json(
            client, "/api/purchases/new", base | {"avg_weight_kg": bad}, owner
        )
        assert resp.status_code == 422, bad
    resp = await client.post(
        "/api/purchases/new", json=base | {"avg_weight_kg": WEIGHT_KG_CAP}, headers=owner
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
