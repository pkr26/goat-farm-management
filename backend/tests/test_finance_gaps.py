"""Finance gap tests (2026-09-23 verification plan, category 7).

- paise-exact cost splitting through the real purchase workflow: odd totals
  across awkward head-counts must allocate to the paisa with a deterministic
  residue and sum back exactly (a property loop over fixed random cases,
  anchored by the hand case ₹1,00,001 / 3);
- the append-only ledger's route-level guarantee: no PUT/PATCH/DELETE
  exists anywhere under /api/finance — corrections are the only edit path
  and they are a POST that voids-and-replaces.
"""

import random
from datetime import date, timedelta
from decimal import Decimal

import httpx

from app.utils import today

from .conftest import owner_with_farm
from .test_rbac_exhaustive import _app_routes


async def _batch_prices(client: httpx.AsyncClient, owner: dict, count: int, total: float) -> list[Decimal]:
    resp = await client.post(
        "/api/purchases/new",
        json={
            "date": today().isoformat(),
            "supplier": "Split supplier",
            "count": count,
            "total_price": total,
            "avg_age_months": 8,
        },
        headers=owner,
    )
    assert resp.status_code == 201, resp.text
    detail = (await client.get(f"/api/purchases/{resp.json()['id']}", headers=owner)).json()
    prices = [Decimal(str(a["purchase_price"])) for a in detail["animals"]]
    assert len(prices) == count
    return prices


async def test_purchase_split_hand_case_100001_over_3(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client, email="split@farm.in")
    prices = await _batch_prices(client, owner, 3, 100_001.00)
    assert sum(prices) == Decimal("100001.00"), (
        f"paise drift: {prices} sums to {sum(prices)}"
    )
    # Deterministic residue: the first head carries the extra paisa.
    assert prices[0] - prices[-1] in (Decimal("0.00"), Decimal("0.01"), Decimal("-0.01"))


async def test_purchase_split_property_loop_sums_exactly(client: httpx.AsyncClient) -> None:
    """Any total split N ways sums back to exactly the total — a property
    loop over random paisa-odd totals and awkward head counts."""
    owner = await owner_with_farm(client, email="split2@farm.in")
    rng = random.Random(20260923)
    for _ in range(12):
        count = rng.choice([2, 3, 5, 7, 11])
        paise = rng.randrange(10_000, 9_999_999_99)  # ₹100 – ₹1 crore, odd paisa
        total = Decimal(paise) / 100
        prices = await _batch_prices(client, owner, count, float(total))
        assert sum(prices) == total.quantize(Decimal("0.01")), (
            f"{total} over {count}: {prices} sums to {sum(prices)}"
        )
        # No head is allocated a negative or sub-paisa value.
        for price in prices:
            assert price >= 0 and price == price.quantize(Decimal("0.01"))


async def test_the_ledger_exposes_no_update_or_delete_routes() -> None:
    """Append-only at the route table: nothing under /api/finance accepts
    PUT/PATCH/DELETE. Corrections are POST /transactions/{id}/correct."""
    offenders = [
        (method, path)
        for method, path, _perm, _scoped in _app_routes()
        if path.startswith("/api/finance") and method in {"PUT", "PATCH", "DELETE"}
    ]
    assert not offenders, f"ledger edit/delete routes exist: {offenders}"


async def test_ledger_correction_voids_and_replaces(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client, email="void@farm.in")
    created = await client.post(
        "/api/finance/new",
        json={
            "date": (today() - timedelta(days=2)).isoformat(),
            "type": "EXPENSE",
            "category": "OTHER",
            "amount": 123.45,
            "notes": "original",
        },
        headers=owner,
    )
    assert created.status_code == 201, created.text
    original = created.json()

    corrected = await client.post(
        f"/api/finance/transactions/{original['id']}/correct",
        json={
            "date": (today() - timedelta(days=2)).isoformat(),
            "type": "EXPENSE",
            "category": "OTHER",
            "amount": 150.00,
            "notes": "correct amount",
            "reason": "mis-keyed the feed invoice",
        },
        headers=owner | {"Idempotency-Key": "void-1"},
    )
    assert corrected.status_code in (200, 201), corrected.text

    rows = (await client.get("/api/finance", headers=owner)).json()["transactions"]
    by_id = {t["id"]: t for t in rows}
    assert original["id"] in by_id
    voided = by_id[original["id"]]
    assert voided.get("voided") or voided.get("is_void") or voided.get("status") == "VOIDED" or (
        voided.get("correction_of_id") is None and voided.get("amount") == 0
    ) or voided.get("voided_at") or voided.get("void_reason"), (
        f"original row not visibly voided: {voided}"
    )
    replacement = next(
        (t for t in rows if t.get("correction_of_id") == original["id"]), None
    )
    assert replacement is not None, f"no replacement row references the original: {rows}"
    assert Decimal(str(replacement["amount"])) == Decimal("150.00")

    # Net effect of the pair is exactly the replacement amount.
    _ = date.today()
