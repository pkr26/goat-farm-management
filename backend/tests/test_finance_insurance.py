"""Insurance register, per-animal lifetime P&L, feed stock valuation and the
dashboard insurance-expiry window.

Endpoints under test:
- GET/POST /api/finance/insurance, POST /api/finance/insurance/{id}/renew —
  the append-style policy register (renew moves the horizon forward; there is
  deliberately no edit/delete) plus the INSURANCE renewal duty it spawns for
  the ACCOUNTANT preset role.
- GET /api/finance/animals/{animal_id}/lifetime-pnl — provenance-scoped
  lifetime money in/out for one animal, zero-safe.
- GET /api/finance — the feed_stock_value memo line (never an expense).
- GET /api/dashboard — the insurance-expiring block behind finance.view.

Aggregation numbers are hand-computed from fixtures built through the API
(with direct-DB rows only where no writer exists, e.g. a voided ledger row).
"""

from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy import update as sa_update

from app.api.dashboard import INSURANCE_EXPIRING_WINDOW_DAYS
from app.db import get_sessionmaker
from app.models import InsurancePolicy, Task, Transaction
from app.utils import today, utcnow

from .conftest import create_farm, owner_with_farm, register
from .test_finance_extended import (
    change_status,
    custom_role_id,
    get_dashboard,
    get_finance,
    iso,
    make_animal,
    preset_role_id,
    worker_headers,
)


def policy_payload(**overrides: object) -> dict:
    payload: dict = {
        "policy_number": "POL-100",
        "insurer": "Oriental Insurance",
        "sum_insured": 15000.0,
        "premium": 450.0,
        "start_date": iso(today() - timedelta(days=10)),
        "renewal_date": iso(today() + timedelta(days=365)),
        "notes": "Herd mortality cover",
    }
    return payload | overrides


async def add_policy(
    client: httpx.AsyncClient, headers: dict, **overrides: object
) -> httpx.Response:
    return await client.post(
        "/api/finance/insurance", json=policy_payload(**overrides), headers=headers
    )


async def list_policies(
    client: httpx.AsyncClient, headers: dict, **params: object
) -> httpx.Response:
    return await client.get("/api/finance/insurance", params=params, headers=headers)


async def insurance_tasks(farm_id: int) -> list[Task]:
    async with get_sessionmaker()() as db:
        return list(
            (
                await db.execute(
                    select(Task)
                    .where(Task.farm_id == farm_id, Task.category == "INSURANCE")
                    .order_by(Task.id)
                )
            )
            .scalars()
            .all()
        )


# ---------------------------------------------------------------------------
# Register CRUD + scoping
# ---------------------------------------------------------------------------
async def test_insurance_register_create_list_and_tenant_scoping(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="ins-owner@farm.in")
    animal = await make_animal(client, owner, tag="INS-1")

    resp = await add_policy(client, owner, policy_number="POL-100", animal_id=animal["id"])
    assert resp.status_code == 201, resp.text
    policy = resp.json()
    assert policy["status"] == "active"
    assert policy["animal_id"] == animal["id"]
    assert policy["animal_tag"] == "INS-1"
    assert policy["sum_insured"] == 15000.0
    assert policy["premium"] == 450.0
    assert policy["start_date"] == iso(today() - timedelta(days=10))

    # A herd-level policy (no animal link) is equally first-class.
    resp = await add_policy(client, owner, policy_number="POL-200")
    assert resp.status_code == 201, resp.text
    assert resp.json()["animal_id"] is None
    assert resp.json()["animal_tag"] is None

    listing = await list_policies(client, owner)
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert body["total"] == 2
    # Most urgent renewal first.
    assert [p["policy_number"] for p in body["policies"]] == ["POL-100", "POL-200"]

    # Filters: status, per-animal, and a cross-farm/unknown animal id that
    # simply matches nothing (a filter, not an enumeration oracle).
    active = await list_policies(client, owner, status="active")
    assert active.json()["total"] == 2
    for_animal = await list_policies(client, owner, animal_id=animal["id"])
    assert [p["policy_number"] for p in for_animal.json()["policies"]] == ["POL-100"]
    unknown = await list_policies(client, owner, animal_id=999_999_999)
    assert unknown.json()["total"] == 0
    bad_status = await list_policies(client, owner, status="expired")
    assert bad_status.status_code == 422

    # Tenancy: another farm sees none of this farm's policies...
    other_headers = await register(client, email="ins-other@farm.in")
    other = await create_farm(client, other_headers, "Beta Farm")
    assert (await list_policies(client, other)).json()["total"] == 0
    # ...but may register the same policy number (uniqueness is per farm).
    assert (await add_policy(client, other)).status_code == 201
    # A duplicate number on the SAME farm is a conflict, not a second row.
    duplicate = await add_policy(client, owner, policy_number="POL-100")
    assert duplicate.status_code == 409
    assert "already registered" in duplicate.json()["detail"]
    # Cross-farm policy access is a 404, and renewals cannot reach it.
    policy_id = policy["id"]
    foreign_renew = await client.post(
        f"/api/finance/insurance/{policy_id}/renew",
        json={"renewal_date": iso(today() + timedelta(days=400))},
        headers=other,
    )
    assert foreign_renew.status_code == 404


async def test_insurance_create_validations(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client, email="ins-valid@farm.in")

    # start_date may not lie in the future (renewal_date may: that is the
    # point of the register).
    future_start = await add_policy(client, owner, start_date=iso(today() + timedelta(days=5)))
    assert future_start.status_code == 422

    renewal_before_start = await add_policy(
        client,
        owner,
        start_date=iso(today()),
        renewal_date=iso(today() - timedelta(days=1)),
    )
    assert renewal_before_start.status_code == 422

    # sum_insured must be strictly positive; premium may be an explicit ₹0.
    for bad_sum in (0, -100.0):
        resp = await add_policy(client, owner, sum_insured=bad_sum)
        assert resp.status_code == 422
    zero_premium = await add_policy(client, owner, policy_number="POL-FREE", premium=0.0)
    assert zero_premium.status_code == 201, zero_premium.text
    assert zero_premium.json()["premium"] == 0.0

    # Blank identifiers are noise, not policies.
    for field in ("policy_number", "insurer"):
        resp = await add_policy(client, owner, **{field: "   "})
        assert resp.status_code == 422

    # An animal id from another farm is rejected before anything is written.
    other_headers = await register(client, email="ins-valid-other@farm.in")
    other = await create_farm(client, other_headers, "Gamma Farm")
    foreign_animal = await make_animal(client, other, tag="GAMMA-1")
    resp = await add_policy(client, owner, animal_id=foreign_animal["id"])
    assert resp.status_code == 400
    assert "not on this farm" in resp.json()["detail"]


async def test_insurance_permissions_split_view_and_manage(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="ins-perm@farm.in")
    assert (await add_policy(client, owner, policy_number="POL-P1")).status_code == 201

    viewer_role = await preset_role_id(client, owner, "VIEWER")  # finance.view only
    viewer = await worker_headers(client, owner, viewer_role, "ins-viewer@farm.in")
    listing = await list_policies(client, viewer)
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    denied = await add_policy(client, viewer, policy_number="POL-P2")
    assert denied.status_code == 403

    no_finance_role = await custom_role_id(client, owner, "Barn only", ["animals.view"])
    worker = await worker_headers(client, owner, no_finance_role, "ins-worker@farm.in")
    assert (await list_policies(client, worker)).status_code == 403


# ---------------------------------------------------------------------------
# Renewal duties (INSURANCE -> ACCOUNTANT)
# ---------------------------------------------------------------------------
async def test_create_spawns_accountant_renewal_duty(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client, email="ins-task@farm.in")
    animal = await make_animal(client, owner, tag="INS-T1")
    farm_id = int(owner["X-Farm-Id"])
    renewal = today() + timedelta(days=365)

    resp = await add_policy(
        client,
        owner,
        policy_number="POL-R",
        animal_id=animal["id"],
        renewal_date=iso(renewal),
    )
    assert resp.status_code == 201, resp.text

    duties = await insurance_tasks(farm_id)
    assert len(duties) == 1
    duty = duties[0]
    assert duty.title == "Insurance renewal due: policy POL-R"
    assert duty.due_date == renewal - timedelta(days=30)
    assert duty.animal_id == animal["id"]
    assert duty.auto_generated is True
    assert duty.status == "PENDING"
    accountant_role = await preset_role_id(client, owner, "ACCOUNTANT")
    assert duty.assigned_role_id == accountant_role
    assert duty.assigned_user_id is None

    # A renewal date that is already today spawns nothing: the 30-day lead is
    # gone and a backdated duty would only bury real work.
    resp = await add_policy(
        client,
        owner,
        policy_number="POL-DUE",
        start_date=iso(today() - timedelta(days=364)),
        renewal_date=iso(today()),
    )
    assert resp.status_code == 201, resp.text
    assert len(await insurance_tasks(farm_id)) == 1


async def test_renew_moves_horizon_forward_and_respawns_duty(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="ins-renew@farm.in")
    animal = await make_animal(client, owner, tag="INS-R1")
    farm_id = int(owner["X-Farm-Id"])
    old_renewal = today() + timedelta(days=90)
    created = await add_policy(
        client,
        owner,
        policy_number="POL-X",
        animal_id=animal["id"],
        renewal_date=iso(old_renewal),
    )
    assert created.status_code == 201, created.text
    policy_id = created.json()["id"]

    # The horizon never moves backwards (and a far-future body is shaped fine;
    # it is the domain rule that rejects it).
    backwards = await client.post(
        f"/api/finance/insurance/{policy_id}/renew",
        json={"renewal_date": iso(old_renewal - timedelta(days=1))},
        headers=owner,
    )
    assert backwards.status_code == 422

    new_renewal = today() + timedelta(days=300)
    renewed = await client.post(
        f"/api/finance/insurance/{policy_id}/renew",
        json={"renewal_date": iso(new_renewal), "premium": 500.0},
        headers=owner,
    )
    assert renewed.status_code == 200, renewed.text
    body = renewed.json()
    assert body["renewal_date"] == iso(new_renewal)
    assert body["premium"] == 500.0
    assert body["status"] == "active"
    assert body["animal_tag"] == "INS-R1"

    # Append-style: the original duty stays (audit trail), the next one is
    # queued for the new horizon, both linked to the covered animal.
    duties = await insurance_tasks(farm_id)
    assert [d.due_date for d in duties] == [
        old_renewal - timedelta(days=30),
        new_renewal - timedelta(days=30),
    ]
    assert all(d.animal_id == animal["id"] for d in duties)
    assert all(d.title == "Insurance renewal due: policy POL-X" for d in duties)

    # Renewing to the same date is allowed (>= the current horizon); the
    # horizon is still in the future, so the operator's re-confirmation
    # queues its own duty.
    same_day = await client.post(
        f"/api/finance/insurance/{policy_id}/renew",
        json={"renewal_date": iso(new_renewal)},
        headers=owner,
    )
    assert same_day.status_code == 200
    assert len(await insurance_tasks(farm_id)) == 3

    unknown = await client.post(
        "/api/finance/insurance/999999999/renew",
        json={"renewal_date": iso(new_renewal + timedelta(days=30))},
        headers=owner,
    )
    assert unknown.status_code == 404
    forbidden = await client.post(
        f"/api/finance/insurance/{policy_id}/renew",
        json={"renewal_date": iso(new_renewal + timedelta(days=30))},
        headers=await worker_headers(
            client, owner, await preset_role_id(client, owner, "VIEWER"), "ins-r-v@farm.in"
        ),
    )
    assert forbidden.status_code == 403


# ---------------------------------------------------------------------------
# Per-animal lifetime P&L
# ---------------------------------------------------------------------------
async def test_lifetime_pnl_math_includes_only_provenance_scoped_money(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="pnl-owner@farm.in")
    farm_id = int(owner["X-Farm-Id"])

    # A recorded-price purchase books both the animal's denormalized
    # purchase_price AND its ANIMAL_PURCHASE ledger row (the create path
    # books the pair for a historical import exactly as for a managed
    # purchase) — the ledger copy is authoritative, so the profile must not
    # double-count the pair. The fixture imports the animal historically so
    # it enters FOUNDATION directly: a managed purchase would sit in the
    # 45-day quarantine protocol, whose biosecurity gate blocks the sale
    # below — a lifecycle fact this money-math test does not need.
    purchased = await client.post(
        "/api/animals",
        json={
            "tag_number": "P-L-1",
            "sex": "F",
            "source": "PURCHASED",
            "current_bucket": "FOUNDATION",
            "purchase_date": iso(today() - timedelta(days=30)),
            "purchase_price": 8000.0,
            "historical_import_reason": "Pre-app ledger-backed purchase",
        },
        headers=owner,
    )
    assert purchased.status_code == 201, purchased.text
    animal_id = purchased.json()["id"]

    treated = await client.post(
        "/api/health/events",
        json={
            "scope": "animal",
            "animal_id": animal_id,
            "date": iso(today() - timedelta(days=10)),
            "type": "TREATMENT",
            "product_name": "Oxytetracycline",
            "cost": 300.0,
        },
        headers=owner,
    )
    assert treated.status_code == 201, treated.text

    # A manual VET row linked to the same animal is real money, but it is not
    # a health-event booking — it must stay out of health_cost.
    manual = await client.post(
        "/api/finance/new",
        json={
            "date": iso(today()),
            "type": "EXPENSE",
            "category": "VET",
            "amount": 999.0,
            "related_animal_id": animal_id,
        },
        headers=owner,
    )
    assert manual.status_code == 201, manual.text

    assert (
        await add_policy(client, owner, policy_number="POL-PNL", animal_id=animal_id, premium=450.0)
    ).status_code == 201

    # change_status asserts the 200 itself and returns the parsed body.
    await change_status(client, owner, animal_id, "SOLD", sale_price=12000.0)

    # A voided HEALTH_EVENT-sourced row has no writer through the API; seed
    # one directly — audit trail, never cost.
    async with get_sessionmaker()() as db:
        db.add(
            Transaction(
                farm_id=farm_id,
                date=today(),
                type="EXPENSE",
                category="MEDICINE",
                amount=Decimal("777.00"),
                related_animal_id=animal_id,
                source_type="HEALTH_EVENT",
                source_id=animal_id,
                voided_at=utcnow(),
                void_reason="Booked against the wrong goat",
            )
        )
        await db.commit()

    resp = await client.get(f"/api/finance/animals/{animal_id}/lifetime-pnl", headers=owner)
    assert resp.status_code == 200, resp.text
    pnl = resp.json()
    assert pnl["animal_id"] == animal_id
    assert pnl["tag_number"] == "P-L-1"
    assert pnl["purchase_cost"] == 8000.0  # not 16000.0: no double count
    assert pnl["health_cost"] == 300.0  # manual VET and voided rows excluded
    assert pnl["insurance_premiums"] == 450.0
    assert pnl["sale_income"] == 12000.0
    assert pnl["net"] == 12000.0 - (8000.0 + 300.0 + 450.0)
    assert "farm level" in pnl["note"]


async def test_lifetime_pnl_zero_safe_and_scoped(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client, email="pnl-zero@farm.in")
    # make_animal books no money facts (historical import, no price).
    animal = await make_animal(client, owner, tag="P-Z-1")

    resp = await client.get(f"/api/finance/animals/{animal['id']}/lifetime-pnl", headers=owner)
    assert resp.status_code == 200, resp.text
    pnl = resp.json()
    assert pnl["purchase_cost"] == 0.0
    assert pnl["health_cost"] == 0.0
    assert pnl["insurance_premiums"] == 0.0
    assert pnl["sale_income"] == 0.0
    assert pnl["net"] == 0.0

    # An imported animal that DID record a price books the matching
    # ANIMAL_PURCHASE ledger row (same amount as the denormalized column),
    # so the ledger-first lookup reports the purchase cost either way.
    imported = await client.post(
        "/api/animals",
        json={
            "tag_number": "P-Z-2",
            "sex": "M",
            "source": "PURCHASED",
            "current_bucket": "FOUNDATION",
            "purchase_date": iso(today() - timedelta(days=200)),
            "purchase_price": 3000.0,
            "historical_import_reason": "Pre-app herd record",
        },
        headers=owner,
    )
    assert imported.status_code == 201, imported.text
    imported_pnl = await client.get(
        f"/api/finance/animals/{imported.json()['id']}/lifetime-pnl",
        headers=owner,
    )
    assert imported_pnl.status_code == 200
    assert imported_pnl.json()["purchase_cost"] == 3000.0
    assert imported_pnl.json()["net"] == -3000.0

    # Cross-farm and impossible ids are a 404, and the endpoint is gated.
    other_headers = await register(client, email="pnl-zero-other@farm.in")
    other = await create_farm(client, other_headers, "Delta Farm")
    assert (
        await client.get(f"/api/finance/animals/{animal['id']}/lifetime-pnl", headers=other)
    ).status_code == 404
    assert (
        await client.get("/api/finance/animals/9999999999/lifetime-pnl", headers=owner)
    ).status_code == 404
    no_finance = await custom_role_id(client, owner, "Barn only 2", ["animals.view"])
    worker = await worker_headers(client, owner, no_finance, "pnl-worker@farm.in")
    assert (
        await client.get(f"/api/finance/animals/{animal['id']}/lifetime-pnl", headers=worker)
    ).status_code == 403


# ---------------------------------------------------------------------------
# Feed stock valuation (memo line on the finance summary)
# ---------------------------------------------------------------------------
async def test_finance_summary_carries_feed_stock_value_as_memo(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="feed-val@farm.in")

    # Seeded inventory starts at zero stock: an empty valuation is a factual
    # zero, not a missing field.
    summary = await get_finance(client, owner)
    assert summary["feed_stock_value"] == 0.0

    inventory = await client.get("/api/feeding/inventory", headers=owner)
    assert inventory.status_code == 200, inventory.text
    items = inventory.json()
    assert len(items) >= 2
    first, second = items[0], items[1]

    restock = await client.post(
        f"/api/feeding/inventory/{first['id']}/add",
        json={"qty_kg": 100.0, "price_per_kg": 12.5},
        headers=owner,
    )
    assert restock.status_code == 200, restock.text
    # Unpriced stock contributes nothing: coalescing a missing last price to
    # zero, never inventing one.
    unpriced = await client.post(
        f"/api/feeding/inventory/{second['id']}/add",
        json={"qty_kg": 50.0},
        headers=owner,
    )
    assert unpriced.status_code == 200, unpriced.text

    summary = await get_finance(client, owner)
    assert summary["feed_stock_value"] == 1250.0  # 100 kg x Rs 12.50
    # Memo line, not an expense: the priced restock booked a Rs 1250 FEED
    # row (the unpriced one booked Rs 0), and the valuation rides beside the
    # totals rather than inside them.
    assert summary["total_expense"] == 1250.0
    assert summary["total_income"] == 0.0


# ---------------------------------------------------------------------------
# Dashboard insurance-expiring block
# ---------------------------------------------------------------------------
async def test_dashboard_insurance_expiring_window_and_ordering(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="dash-ins@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    animal = await make_animal(client, owner, tag="DASH-INS-1")

    window = timedelta(days=INSURANCE_EXPIRING_WINDOW_DAYS)
    assert (
        await add_policy(
            client,
            owner,
            policy_number="POL-PAST",
            animal_id=animal["id"],
            start_date=iso(today() - timedelta(days=40)),
            renewal_date=iso(today() - timedelta(days=5)),
        )
    ).status_code == 201  # overdue but still active: stays on the panel
    assert (
        await add_policy(
            client, owner, policy_number="POL-NEAR", renewal_date=iso(today() + timedelta(days=30))
        )
    ).status_code == 201
    assert (
        await add_policy(
            client, owner, policy_number="POL-EDGE", renewal_date=iso(today() + window)
        )
    ).status_code == 201  # boundary day is inside
    assert (
        await add_policy(
            client,
            owner,
            policy_number="POL-FAR",
            renewal_date=iso(today() + window + timedelta(days=1)),
        )
    ).status_code == 201  # one day past the window: outside

    # lapsed has no API writer (renewal is the only transition); seed it
    # directly — an expired status must never reappear as "expiring".
    assert (
        await add_policy(
            client,
            owner,
            policy_number="POL-LAPSED",
            start_date=iso(today() - timedelta(days=100)),
            renewal_date=iso(today() + timedelta(days=10)),
        )
    ).status_code == 201
    async with get_sessionmaker()() as db:
        await db.execute(
            sa_update(InsurancePolicy)
            .where(
                InsurancePolicy.farm_id == farm_id,
                InsurancePolicy.policy_number == "POL-LAPSED",
            )
            .values(status="lapsed")
        )
        await db.commit()

    dash = await get_dashboard(client, owner)
    expiring = dash["insurance_expiring"]
    assert [p["policy_number"] for p in expiring] == ["POL-PAST", "POL-NEAR", "POL-EDGE"]
    assert dash["insurance_expiring_total"] == 3
    # Most urgent first; per-animal policy carries the tag, herd-level not.
    assert expiring[0]["animal_tag"] == "DASH-INS-1"
    assert expiring[0]["renewal_date"] == iso(today() - timedelta(days=5))
    assert expiring[1]["animal_id"] is None
    assert expiring[1]["animal_tag"] is None
    assert expiring[1]["insurer"] == "Oriental Insurance"


async def test_dashboard_insurance_expiring_gated_on_finance_view(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="dash-gate@farm.in")
    assert (
        await add_policy(
            client, owner, policy_number="POL-GATE", renewal_date=iso(today() + timedelta(days=15))
        )
    ).status_code == 201

    # A cleaner holds dashboard.view but no finance.view: withheld means an
    # empty list and a null total — the gate must never render as a factual
    # "no policies renewing".
    cleaner = await worker_headers(
        client, owner, await preset_role_id(client, owner, "CLEANER"), "dash-cleaner@farm.in"
    )
    cleaner_dash = await get_dashboard(client, cleaner)
    assert cleaner_dash["insurance_expiring"] == []
    assert cleaner_dash["insurance_expiring_total"] is None

    # The VIEWER preset carries finance.view and sees the block.
    viewer = await worker_headers(
        client, owner, await preset_role_id(client, owner, "VIEWER"), "dash-viewer@farm.in"
    )
    viewer_dash = await get_dashboard(client, viewer)
    assert viewer_dash["insurance_expiring_total"] == 1
    assert viewer_dash["insurance_expiring"][0]["policy_number"] == "POL-GATE"


# The register is append-only by design: no edit or delete route exists (an
# unmatched path is a 404, never a silent write path).
@pytest.mark.parametrize(
    "method,path",
    [
        ("PUT", "/api/finance/insurance/1"),
        ("PATCH", "/api/finance/insurance/1"),
        ("DELETE", "/api/finance/insurance/1"),
    ],
)
async def test_insurance_has_no_edit_or_delete_route(
    client: httpx.AsyncClient, method: str, path: str
) -> None:
    owner = await owner_with_farm(client, email="ins-noroute@farm.in")
    resp = await client.request(method, path, headers=owner)
    assert resp.status_code == 404, f"{method} {path} must not exist"


# ---------------------------------------------------------------------------
# Premium payment history (the audit fix: renewal used to overwrite the
# premium column, hiding every earlier payment from the lifetime P&L)
# ---------------------------------------------------------------------------
async def test_renewal_books_premium_history_and_pnl_sums_payments(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="ins-hist@farm.in")
    animal = await make_animal(client, owner, tag="INS-H-1")

    created = await add_policy(
        client, owner, policy_number="POL-H-1", animal_id=animal["id"], premium=450.0
    )
    assert created.status_code == 201, created.text
    policy_id = created.json()["id"]

    renewed = await client.post(
        f"/api/finance/insurance/{policy_id}/renew",
        json={
            "renewal_date": iso(today() + timedelta(days=730)),
            "premium": 500.0,
        },
        headers=owner,
    )
    assert renewed.status_code == 200, renewed.text

    from app.models import InsurancePremium

    async with get_sessionmaker()() as db:
        payments = list(
            (
                await db.execute(
                    select(InsurancePremium)
                    .where(InsurancePremium.policy_id == policy_id)
                    .order_by(InsurancePremium.id)
                )
            )
            .scalars()
            .all()
        )
    assert [(p.premium, p.covered_from, p.covered_until) for p in payments] == [
        (
            Decimal("450.00"),
            today() - timedelta(days=10),
            today() + timedelta(days=365),
        ),
        (
            Decimal("500.00"),
            today() + timedelta(days=365),
            today() + timedelta(days=730),
        ),
    ]

    # The lifetime P&L sums what was actually payable across both periods —
    # not the renewal-overwritten column value (which reads 500).
    pnl = await client.get(f"/api/finance/animals/{animal['id']}/lifetime-pnl", headers=owner)
    assert pnl.status_code == 200, pnl.text
    assert pnl.json()["insurance_premiums"] == 950.0


async def test_claim_endpoint_is_terminal_and_single_shot(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="ins-claim@farm.in")
    animal = await make_animal(client, owner, tag="INS-C-1")
    created = await add_policy(client, owner, policy_number="POL-C-1", animal_id=animal["id"])
    assert created.status_code == 201, created.text
    policy_id = created.json()["id"]

    claimed = await client.post(
        f"/api/finance/insurance/{policy_id}/claim",
        json={"claim_date": iso(today())},
        headers=owner,
    )
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["status"] == "claimed"

    again = await client.post(
        f"/api/finance/insurance/{policy_id}/claim",
        json={"claim_date": iso(today())},
        headers=owner,
    )
    assert again.status_code == 409, again.text

    # A claim is a register fact, not a money movement: nothing booked.
    async with get_sessionmaker()() as db:
        count = await db.execute(
            select(Transaction).where(
                Transaction.farm_id == int(owner["X-Farm-Id"]),
                Transaction.category == "OTHER",
            )
        )
        assert len(list(count.scalars())) == 0

    # A claim before the policy started is a chronology error.
    early = await add_policy(client, owner, policy_number="POL-C-2")
    assert early.status_code == 201, early.text
    too_early = await client.post(
        f"/api/finance/insurance/{early.json()['id']}/claim",
        json={"claim_date": iso(today() - timedelta(days=30))},
        headers=owner,
    )
    assert too_early.status_code == 422, too_early.text


async def test_animal_exit_lapses_active_cover_and_blocks_renewal(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="ins-lapse@farm.in")
    animal = await make_animal(
        client, owner, tag="INS-L-1", sex="M", date_of_birth=iso(today() - timedelta(days=400))
    )
    # Renewal inside the 60-day dashboard window so both start on the card.
    soon = {"renewal_date": iso(today() + timedelta(days=30))}
    created = await add_policy(
        client, owner, policy_number="POL-L-1", animal_id=animal["id"], **soon
    )
    assert created.status_code == 201, created.text
    policy_id = created.json()["id"]
    # A herd-level policy must survive any single animal's exit untouched.
    herd = await add_policy(client, owner, policy_number="POL-L-2", **soon)
    assert herd.status_code == 201, herd.text

    dashboard = await get_dashboard(client, owner)
    assert dashboard["insurance_expiring_total"] == 2

    sold = await change_status(
        client,
        owner,
        animal["id"],
        "SOLD",
        sale_price=6000.0,
        sale_weight_kg=25.0,
        sale_price_per_kg=240.0,
    )
    assert sold["status"] == "SOLD"

    policies = (await list_policies(client, owner)).json()["policies"]
    by_number = {p["policy_number"]: p for p in policies}
    assert by_number["POL-L-1"]["status"] == "lapsed"
    assert by_number["POL-L-2"]["status"] == "active"

    # Lapsed cover leaves the expiry card (only live cover nags)...
    dashboard = await get_dashboard(client, owner)
    assert dashboard["insurance_expiring_total"] == 1
    # ...cannot be renewed for stock that has left the herd...
    blocked = await client.post(
        f"/api/finance/insurance/{policy_id}/renew",
        json={"renewal_date": iso(today() + timedelta(days=400))},
        headers=owner,
    )
    assert blocked.status_code == 422, blocked.text
    assert "left the herd" in blocked.json()["detail"]
    # ...but its claim window is still open.
    claimed = await client.post(f"/api/finance/insurance/{policy_id}/claim", json={}, headers=owner)
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["status"] == "claimed"

    # New cover cannot be registered against the exited animal either.
    refused = await add_policy(client, owner, policy_number="POL-L-3", animal_id=animal["id"])
    assert refused.status_code == 422, refused.text
    assert "left the herd" in refused.json()["detail"]


# ---------------------------------------------------------------------------
# Mortality memo (audit backlog #31: deaths visible on the finance summary,
# valued at the farm's own realized rate — a memo, never a transaction)
# ---------------------------------------------------------------------------
async def test_mortality_memo_values_deaths_at_the_realized_rate(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="memo@farm.in")
    dead = await make_animal(
        client,
        owner,
        tag="M-DEAD",
        sex="F",
        date_of_birth=iso(today() - timedelta(days=400)),
        weight_kg=30.0,
        weight_date=iso(today() - timedelta(days=5)),
    )
    buyer_lot = await make_animal(
        client,
        owner,
        tag="M-SOLD",
        sex="M",
        date_of_birth=iso(today() - timedelta(days=300)),
    )
    await change_status(
        client,
        owner,
        dead["id"],
        "DEAD",
        mortality_cause="pneumonia",
        mortality_cause_code="PNEUMONIA",
    )
    sold = await change_status(
        client,
        owner,
        buyer_lot["id"],
        "SOLD",
        sale_price=12500.0,
        sale_weight_kg=25.0,
        sale_price_per_kg=500.0,
    )
    assert sold["status"] == "SOLD"

    summary = await get_finance(client, owner)
    memo = summary["mortality_loss"]
    assert memo["window_months"] == 12
    assert memo["head_count"] == 1
    # 30 kg (last recorded weight) × ₹500/kg (realized rate) = ₹15,000.
    assert memo["estimated_loss"] == 15000.0
    assert "last recorded weight" in memo["basis"]
    # Ledger-neutral: the memo never touches the P&L totals.
    assert summary["total_expense"] == 0.0
    assert summary["total_income"] == 12500.0


async def test_mortality_memo_stays_unvalued_without_a_weighed_sale(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="memo2@farm.in")
    dead = await make_animal(
        client,
        owner,
        tag="M-DEAD-2",
        sex="F",
        weight_kg=28.0,
        weight_date=iso(today() - timedelta(days=2)),
    )
    await change_status(client, owner, dead["id"], "DEAD", mortality_cause_code="DIARRHOEA")
    # A sale recorded with no weight cannot price the mortality either.
    unweighed = await make_animal(
        client,
        owner,
        tag="M-SOLD-2",
        sex="M",
        date_of_birth=iso(today() - timedelta(days=300)),
    )
    await change_status(client, owner, unweighed["id"], "SOLD", sale_price=9000.0)

    memo = (await get_finance(client, owner))["mortality_loss"]
    assert memo["head_count"] == 1
    assert memo["estimated_loss"] is None
    assert "unvalued" in memo["basis"]
