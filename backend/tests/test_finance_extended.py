"""Finance & dashboard/reports — extended QA suite.

Endpoints under test:
- POST /api/finance/new — validation, boundaries, related-animal linking.
- GET /api/finance — month/type/category filters, all-time totals, 12-month P&L.
- GET /api/dashboard — bucket counts, task buckets, kiddings/ultrasounds due,
  cull candidates, move suggestions, recent weights.
- GET /api/dashboard/reports — herd summary, breeding performance, mortality.

Aggregation numbers are hand-computed from fixtures built through the API.
SPEC.md is the domain contract (Financial entity, dashboard/reports pages).
Auth/tenancy follows the JSON API rules: 401 without a bearer token, 400 for a
missing/malformed X-Farm-Id, 404 for an unknown farm, 403 for a non-member.
"""

from datetime import date, timedelta

import httpx
import pytest

from app.utils import add_months, today

from .conftest import owner_with_farm, register

WORKER_PW = "workerpass123"

ALL_CATEGORIES = [
    "ANIMAL_SALE",
    "ANIMAL_PURCHASE",
    "FEED",
    "MEDICINE",
    "VET",
    "LABOUR",
    "EQUIPMENT",
    "MILK",
    "MANURE",
    "OTHER",
]

BUCKET_ORDER = [
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


def iso(d: date) -> str:
    return d.isoformat()


# ---------------------------------------------------------------------------
# Helpers (everything goes through the API)
# ---------------------------------------------------------------------------
def txn_payload(**overrides: object) -> dict:
    payload: dict = {"date": iso(today()), "type": "EXPENSE", "category": "FEED", "amount": 100.0}
    return payload | overrides


async def add_txn(client: httpx.AsyncClient, headers: dict, **overrides: object) -> dict:
    resp = await client.post("/api/finance/new", json=txn_payload(**overrides), headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def get_finance(client: httpx.AsyncClient, headers: dict, **params: str) -> dict:
    resp = await client.get("/api/finance", params=params, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def get_dashboard(client: httpx.AsyncClient, headers: dict) -> dict:
    resp = await client.get("/api/dashboard", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def get_reports(client: httpx.AsyncClient, headers: dict) -> dict:
    resp = await client.get("/api/dashboard/reports", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def make_animal(
    client: httpx.AsyncClient,
    headers: dict,
    tag: str = "A-001",
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


async def make_doe(client: httpx.AsyncClient, headers: dict, tag: str = "D-1") -> dict:
    """A breeding-ready doe (400 days old, 26 kg entry weight, BREEDING bucket)."""
    return await make_animal(
        client,
        headers,
        tag=tag,
        sex="F",
        bucket="BREEDING",
        date_of_birth=iso(today() - timedelta(days=400)),
        weight_kg=26.0,
    )


async def make_buck(client: httpx.AsyncClient, headers: dict, tag: str = "B-1") -> dict:
    return await make_animal(client, headers, tag=tag, sex="M", bucket="BREEDING")


async def make_breeding(
    client: httpx.AsyncClient,
    headers: dict,
    doe_id: int,
    buck_id: int,
    breeding_date: date | None = None,
    heat_cycle_number: int = 1,
) -> dict:
    resp = await client.post(
        "/api/breeding",
        json={
            "doe_id": doe_id,
            "buck_id": buck_id,
            "breeding_date": iso(breeding_date or today()),
            "heat_cycle_number": heat_cycle_number,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def ultrasound(
    client: httpx.AsyncClient, headers: dict, br_id: int, pregnant: bool, kid_count: int = 2
) -> dict:
    payload: dict = {"pregnant": pregnant}
    if pregnant:
        payload["kid_count"] = kid_count
    resp = await client.post(f"/api/breeding/{br_id}/ultrasound", json=payload, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def record_kidding(
    client: httpx.AsyncClient, headers: dict, br_id: int, kidding_date: date, kids: list[dict]
) -> dict:
    resp = await client.post(
        "/api/kidding",
        json={
            "breeding_record_id": br_id,
            "date": iso(kidding_date),
            "ease": "NORMAL",
            "kids": kids,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def change_status(
    client: httpx.AsyncClient, headers: dict, animal_id: int, new_status: str, **overrides: object
) -> dict:
    resp = await client.post(
        f"/api/animals/{animal_id}/status",
        json={"new_status": new_status} | overrides,
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def create_duty(
    client: httpx.AsyncClient,
    headers: dict,
    title: str,
    due_date: date,
    category: str = "OTHER",
) -> dict:
    resp = await client.post(
        "/api/tasks",
        json={"title": title, "due_date": iso(due_date), "category": category},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def login_user(client: httpx.AsyncClient, email: str, password: str) -> dict:
    resp = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def preset_role_id(client: httpx.AsyncClient, owner: dict, code: str) -> int:
    resp = await client.get("/api/team", headers=owner)
    assert resp.status_code == 200, resp.text
    return next(r["id"] for r in resp.json()["roles"] if r["code"] == code)


async def custom_role_id(
    client: httpx.AsyncClient, owner: dict, name: str, perms: list[str]
) -> int:
    resp = await client.post(
        "/api/team/roles", json={"name": name, "permissions": perms}, headers=owner
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def worker_headers(client: httpx.AsyncClient, owner: dict, role_id: int, email: str) -> dict:
    resp = await client.post(
        "/api/team/workers",
        json={"name": "Worker", "email": email, "password": WORKER_PW, "role_id": role_id},
        headers=owner,
    )
    assert resp.status_code == 201, resp.text
    headers = await login_user(client, email, WORKER_PW)
    return headers | {"X-Farm-Id": owner["X-Farm-Id"]}


# ---------------------------------------------------------------------------
# POST /api/finance/new — happy paths
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("category", ALL_CATEGORIES)
async def test_create_income_every_category(client: httpx.AsyncClient, category: str) -> None:
    owner = await owner_with_farm(client)
    txn = await add_txn(client, owner, type="INCOME", category=category, amount=500.0)
    assert txn["type"] == "INCOME"
    assert txn["category"] == category
    assert txn["amount"] == 500.0
    assert txn["id"] >= 1


@pytest.mark.parametrize("category", ALL_CATEGORIES)
async def test_create_expense_every_category(client: httpx.AsyncClient, category: str) -> None:
    owner = await owner_with_farm(client)
    txn = await add_txn(client, owner, type="EXPENSE", category=category, amount=750.5)
    assert txn["type"] == "EXPENSE"
    assert txn["category"] == category
    assert txn["amount"] == 750.5


async def test_create_returns_full_fields(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    txn = await add_txn(client, owner, notes="Feed for breeding does")
    assert txn["date"] == iso(today())
    assert txn["type"] == "EXPENSE"
    assert txn["category"] == "FEED"
    assert txn["amount"] == 100.0
    assert txn["notes"] == "Feed for breeding does"
    assert txn["related_animal_id"] is None
    assert txn["animal_tag"] is None


async def test_create_amount_decimal_precision(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    txn = await add_txn(client, owner, amount=12345.67)
    assert txn["amount"] == 12345.67


async def test_create_amount_one_paisa(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    txn = await add_txn(client, owner, amount=0.01)
    assert txn["amount"] == 0.01


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        (1e9, 201),  # exactly the ₹1e9 money cap (B2 float-overflow bound)
        (1e12, 422),
        (9_999_999_999.99, 422),
        (1e308, 422),
    ],
)
async def test_create_amount_huge(client: httpx.AsyncClient, amount: float, expected: int) -> None:
    owner = await owner_with_farm(client)
    resp = await client.post("/api/finance/new", json=txn_payload(amount=amount), headers=owner)
    assert resp.status_code == expected, resp.text
    if expected == 201:
        assert resp.json()["amount"] == amount


async def test_create_date_far_past(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    txn = await add_txn(client, owner, date="1990-01-01")
    assert txn["date"] == "1990-01-01"


async def test_create_notes_unicode_emoji(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    notes = "Sold 2 बकरे 🐐 to Śrī Rāmu — ₹5,000 (óakém)"
    txn = await add_txn(client, owner, notes=notes)
    assert txn["notes"] == notes


async def test_create_notes_sql_injection_string_stored_literally(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    notes = "'; DROP TABLE transactions;--"
    txn = await add_txn(client, owner, notes=notes)
    assert txn["notes"] == notes
    # The table must survive: a follow-up read works and returns the row.
    data = await get_finance(client, owner)
    assert [t["id"] for t in data["transactions"]] == [txn["id"]]


async def test_create_notes_max_length_255(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    notes = "x" * 255  # column is VARCHAR(255)
    txn = await add_txn(client, owner, notes=notes)
    assert txn["notes"] == notes


async def test_create_notes_whitespace_only_becomes_null(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    txn = await add_txn(client, owner, notes="   \t  ")
    assert txn["notes"] is None


async def test_create_notes_empty_string_becomes_null(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    txn = await add_txn(client, owner, notes="")
    assert txn["notes"] is None


async def test_create_notes_absent_becomes_null(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    txn = await add_txn(client, owner)
    assert txn["notes"] is None


async def test_create_notes_leading_trailing_whitespace_stripped(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    txn = await add_txn(client, owner, notes="  paid in cash  ")
    assert txn["notes"] == "paid in cash"


async def test_create_related_animal_own_farm_linked(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="G-101")
    txn = await add_txn(
        client, owner, category="MEDICINE", amount=250.0, related_animal_id=animal["id"]
    )
    assert txn["related_animal_id"] == animal["id"]
    assert txn["animal_tag"] == "G-101"


async def test_create_related_animal_unknown_id_stripped(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    txn = await add_txn(client, owner, related_animal_id=999999)
    assert txn["related_animal_id"] is None
    assert txn["animal_tag"] is None


async def test_create_related_animal_cross_farm_stripped(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client, email="a@farm.in", farm_name="Farm A")
    other = await owner_with_farm(client, email="b@farm.in", farm_name="Farm B")
    foreign = await make_animal(client, other, tag="FOREIGN-1")
    # Documented behavior: a foreign-farm animal link is stripped, not stored.
    txn = await add_txn(client, owner, related_animal_id=foreign["id"])
    assert txn["related_animal_id"] is None
    assert txn["animal_tag"] is None


async def test_create_related_animal_int32_max_stripped(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    # Largest id the int32 PK column could ever hold; no such animal → stripped.
    txn = await add_txn(client, owner, related_animal_id=2_147_483_647)
    assert txn["related_animal_id"] is None


async def test_create_unknown_extra_field_ignored(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await client.post(
        "/api/finance/new",
        json=txn_payload() | {"farm_id": 999, "hacker": "yes"},
        headers=owner,
    )
    assert resp.status_code == 201, resp.text
    assert "hacker" not in resp.json()


# ---------------------------------------------------------------------------
# POST /api/finance/new — validation (422)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("field", ["date", "type", "category", "amount"])
async def test_create_missing_required_field(client: httpx.AsyncClient, field: str) -> None:
    owner = await owner_with_farm(client)
    payload = txn_payload()
    del payload[field]
    resp = await client.post("/api/finance/new", json=payload, headers=owner)
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("bad_type", ["income", "PROFIT", "", "INCOME2", 123, None])
async def test_create_invalid_type(client: httpx.AsyncClient, bad_type: object) -> None:
    owner = await owner_with_farm(client)
    resp = await client.post("/api/finance/new", json=txn_payload(type=bad_type), headers=owner)
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("bad_category", ["feed", "GROCERY", "", 42, None])
async def test_create_invalid_category(client: httpx.AsyncClient, bad_category: object) -> None:
    owner = await owner_with_farm(client)
    resp = await client.post(
        "/api/finance/new", json=txn_payload(category=bad_category), headers=owner
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("amount", [0, -1, -0.01, -1_000_000.0])
async def test_create_amount_non_positive(client: httpx.AsyncClient, amount: float) -> None:
    owner = await owner_with_farm(client)
    resp = await client.post("/api/finance/new", json=txn_payload(amount=amount), headers=owner)
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("amount", ["abc", [100], {"rupees": 100}, None])
async def test_create_amount_wrong_type(client: httpx.AsyncClient, amount: object) -> None:
    owner = await owner_with_farm(client)
    resp = await client.post("/api/finance/new", json=txn_payload(amount=amount), headers=owner)
    assert resp.status_code == 422, resp.text


# PastOrTodayDate allows one day of timezone headroom (clients east of UTC
# entering their local "today"); genuinely future dates are still 422.
@pytest.mark.parametrize("days_ahead", [2, 365, 36500])
async def test_create_future_date_rejected(client: httpx.AsyncClient, days_ahead: int) -> None:
    owner = await owner_with_farm(client)
    future = today() + timedelta(days=days_ahead)
    resp = await client.post("/api/finance/new", json=txn_payload(date=iso(future)), headers=owner)
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize(
    "bad_date", ["31-12-2024", "2024-13-01", "2024-02-30", "not-a-date", "2024/01/15", 20240101]
)
async def test_create_invalid_date_format(client: httpx.AsyncClient, bad_date: object) -> None:
    owner = await owner_with_farm(client)
    resp = await client.post("/api/finance/new", json=txn_payload(date=bad_date), headers=owner)
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("bad_id", [0, -5, 2**62 + 1, 2**63])
async def test_create_related_animal_id_out_of_range(
    client: httpx.AsyncClient, bad_id: int
) -> None:
    owner = await owner_with_farm(client)
    resp = await client.post(
        "/api/finance/new", json=txn_payload(related_animal_id=bad_id), headers=owner
    )
    assert resp.status_code == 422, resp.text


async def test_create_related_animal_id_wrong_type(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await client.post(
        "/api/finance/new", json=txn_payload(related_animal_id="abc"), headers=owner
    )
    assert resp.status_code == 422, resp.text


async def test_create_empty_body(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await client.post("/api/finance/new", headers=owner)
    assert resp.status_code == 422, resp.text


async def test_create_null_body(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await client.post(
        "/api/finance/new",
        content=b"null",
        headers=owner | {"Content-Type": "application/json"},
    )
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# Auth & tenancy — finance endpoints
# ---------------------------------------------------------------------------
async def test_finance_get_requires_auth(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/finance")
    assert resp.status_code == 401


async def test_finance_post_requires_auth(client: httpx.AsyncClient) -> None:
    resp = await client.post("/api/finance/new", json=txn_payload())
    assert resp.status_code == 401


async def test_finance_invalid_token_rejected(client: httpx.AsyncClient) -> None:
    headers = {"Authorization": "Bearer not-a-real-token"}
    assert (await client.get("/api/finance", headers=headers)).status_code == 401
    assert (
        await client.post("/api/finance/new", json=txn_payload(), headers=headers)
    ).status_code == 401


async def test_finance_missing_farm_header(client: httpx.AsyncClient) -> None:
    auth = await register(client)
    assert (await client.get("/api/finance", headers=auth)).status_code == 400
    assert (
        await client.post("/api/finance/new", json=txn_payload(), headers=auth)
    ).status_code == 400


@pytest.mark.parametrize("bad_farm", ["abc", "0", "-7", str(2**62), "1.5", ""])
async def test_finance_bad_farm_header(client: httpx.AsyncClient, bad_farm: str) -> None:
    auth = await register(client)
    headers = auth | {"X-Farm-Id": bad_farm}
    resp = await client.get("/api/finance", headers=headers)
    assert resp.status_code == 400, resp.text


async def test_finance_nonexistent_farm_404(client: httpx.AsyncClient) -> None:
    auth = await register(client)
    resp = await client.get("/api/finance", headers=auth | {"X-Farm-Id": "999999"})
    assert resp.status_code == 404


async def test_finance_other_users_farm_forbidden(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client, email="a@farm.in", farm_name="Farm A")
    stranger = await register(client, email="stranger@farm.in")
    headers = stranger | {"X-Farm-Id": owner["X-Farm-Id"]}
    assert (await client.get("/api/finance", headers=headers)).status_code == 403
    assert (
        await client.post("/api/finance/new", json=txn_payload(), headers=headers)
    ).status_code == 403


async def test_finance_mover_worker_forbidden(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    rid = await preset_role_id(client, owner, "MOVER")  # no finance perms
    mover = await worker_headers(client, owner, rid, "mover@farm.in")
    resp = await client.get("/api/finance", headers=mover)
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Missing permission: finance.view"
    resp = await client.post("/api/finance/new", json=txn_payload(), headers=mover)
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Missing permission: finance.manage"


async def test_finance_worker_view_only_cannot_write(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    rid = await custom_role_id(client, owner, "Accountant", ["finance.view"])
    worker = await worker_headers(client, owner, rid, "acct@farm.in")
    assert (await client.get("/api/finance", headers=worker)).status_code == 200
    resp = await client.post("/api/finance/new", json=txn_payload(), headers=worker)
    assert resp.status_code == 403


async def test_finance_worker_with_manage_can_write(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    rid = await custom_role_id(client, owner, "Cashier", ["finance.view", "finance.manage"])
    worker = await worker_headers(client, owner, rid, "cashier@farm.in")
    resp = await client.post("/api/finance/new", json=txn_payload(), headers=worker)
    assert resp.status_code == 201, resp.text
    assert (await client.get("/api/finance", headers=worker)).status_code == 200


# ---------------------------------------------------------------------------
# GET /api/finance — list, totals, filters
# ---------------------------------------------------------------------------
async def test_list_empty_farm(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    data = await get_finance(client, owner)
    assert data["transactions"] == []
    assert data["total_income"] == 0.0
    assert data["total_expense"] == 0.0
    assert data["pnl"] == []


async def test_list_contains_created_transaction(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="G-77")
    txn = await add_txn(
        client,
        owner,
        type="INCOME",
        category="MANURE",
        amount=320.0,
        notes="compost sale",
        related_animal_id=animal["id"],
    )
    data = await get_finance(client, owner)
    assert len(data["transactions"]) == 1
    row = data["transactions"][0]
    assert row["id"] == txn["id"]
    assert row["type"] == "INCOME"
    assert row["category"] == "MANURE"
    assert row["amount"] == 320.0
    assert row["notes"] == "compost sale"
    assert row["related_animal_id"] == animal["id"]
    assert row["animal_tag"] == "G-77"


async def test_list_newest_date_first(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    d1 = today() - timedelta(days=2)
    d2 = today() - timedelta(days=10)
    d3 = today() - timedelta(days=1)
    for d in (d1, d2, d3):  # inserted out of order on purpose
        await add_txn(client, owner, date=iso(d))
    dates = [t["date"] for t in (await get_finance(client, owner))["transactions"]]
    assert dates == [iso(d3), iso(d1), iso(d2)]


async def test_list_same_date_newest_id_first(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    first = await add_txn(client, owner, notes="first")
    second = await add_txn(client, owner, notes="second")
    rows = (await get_finance(client, owner))["transactions"]
    assert [r["id"] for r in rows] == [second["id"], first["id"]]


async def test_list_capped_at_200_transactions(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    for _ in range(205):
        await add_txn(client, owner, amount=1.0)
    data = await get_finance(client, owner)
    assert len(data["transactions"]) == 200
    # Totals are all-time aggregates, not capped by the 200-row list window.
    assert data["total_expense"] == 205.0


async def test_totals_hand_computed(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await add_txn(client, owner, type="INCOME", category="ANIMAL_SALE", amount=1000.0)
    await add_txn(client, owner, type="INCOME", category="MILK", amount=2500.75)
    await add_txn(client, owner, type="EXPENSE", category="FEED", amount=499.25)
    await add_txn(client, owner, type="EXPENSE", category="VET", amount=1.0)
    data = await get_finance(client, owner)
    assert data["total_income"] == 3500.75
    assert data["total_expense"] == 500.25


async def test_totals_ignore_month_filter(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    this_month = today().replace(day=1)
    last_month = add_months(this_month, -1)
    await add_txn(client, owner, type="INCOME", amount=100.0, date=iso(this_month))
    await add_txn(client, owner, type="INCOME", amount=50.0, date=iso(last_month))
    await add_txn(client, owner, type="EXPENSE", amount=25.0, date=iso(last_month))
    data = await get_finance(client, owner, month=this_month.strftime("%Y-%m"))
    assert len(data["transactions"]) == 1
    # Totals are all-time, regardless of the list filter.
    assert data["total_income"] == 150.0
    assert data["total_expense"] == 25.0


async def test_totals_ignore_type_and_category_filters(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await add_txn(client, owner, type="INCOME", category="MILK", amount=100.0)
    await add_txn(client, owner, type="EXPENSE", category="FEED", amount=40.0)
    data = await get_finance(client, owner, type="INCOME", category="MILK")
    assert len(data["transactions"]) == 1
    assert data["total_income"] == 100.0
    assert data["total_expense"] == 40.0


async def test_month_filter_includes_whole_month(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    in_month = [await add_txn(client, owner, date=d) for d in ("2025-03-01", "2025-03-31")]
    await add_txn(client, owner, date="2025-02-28")
    await add_txn(client, owner, date="2025-04-01")
    data = await get_finance(client, owner, month="2025-03")
    assert {t["id"] for t in data["transactions"]} == {t["id"] for t in in_month}


async def test_month_filter_february_non_leap(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    feb1 = await add_txn(client, owner, date="2025-02-01")
    feb28 = await add_txn(client, owner, date="2025-02-28")
    await add_txn(client, owner, date="2025-01-31")
    await add_txn(client, owner, date="2025-03-01")
    data = await get_finance(client, owner, month="2025-02")
    assert {t["id"] for t in data["transactions"]} == {feb1["id"], feb28["id"]}


async def test_month_filter_year_boundary(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    dec31 = await add_txn(client, owner, date="2025-12-31")
    await add_txn(client, owner, date="2026-01-01")
    await add_txn(client, owner, date="2024-12-31")
    data = await get_finance(client, owner, month="2025-12")
    assert [t["id"] for t in data["transactions"]] == [dec31["id"]]


@pytest.mark.parametrize("garbage", ["abc", "2025-13", "2025-00", "2025/03", "99999-01", " "])
async def test_month_filter_garbage_matches_nothing(
    client: httpx.AsyncClient, garbage: str
) -> None:
    owner = await owner_with_farm(client)
    await add_txn(client, owner)
    data = await get_finance(client, owner, month=garbage)
    assert data["transactions"] == []


async def test_month_filter_unpadded_month_matches(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    txn = await add_txn(client, owner, date="2025-03-15")
    data = await get_finance(client, owner, month="2025-3")  # strptime is lenient
    assert [t["id"] for t in data["transactions"]] == [txn["id"]]


async def test_type_filter(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    inc = await add_txn(client, owner, type="INCOME", category="MILK", amount=10.0)
    await add_txn(client, owner, type="EXPENSE", category="FEED", amount=20.0)
    data = await get_finance(client, owner, type="INCOME")
    assert [t["id"] for t in data["transactions"]] == [inc["id"]]
    data = await get_finance(client, owner, type="EXPENSE")
    assert all(t["type"] == "EXPENSE" for t in data["transactions"])


async def test_type_filter_unknown_value_matches_nothing(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await add_txn(client, owner, type="INCOME")
    data = await get_finance(client, owner, type="income")  # no enum guard on filters
    assert data["transactions"] == []


async def test_category_filter(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    vet = await add_txn(client, owner, category="VET", amount=300.0)
    await add_txn(client, owner, category="FEED", amount=100.0)
    data = await get_finance(client, owner, category="VET")
    assert [t["id"] for t in data["transactions"]] == [vet["id"]]


async def test_category_filter_unknown_value_matches_nothing(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await add_txn(client, owner, category="FEED")
    data = await get_finance(client, owner, category="GROCERY")
    assert data["transactions"] == []


async def test_combined_filters(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    m1, m2 = "2025-05", "2025-06"
    hit = await add_txn(client, owner, type="EXPENSE", category="FEED", date=f"{m1}-10")
    await add_txn(client, owner, type="EXPENSE", category="VET", date=f"{m1}-10")
    await add_txn(client, owner, type="INCOME", category="FEED", date=f"{m1}-10")
    await add_txn(client, owner, type="EXPENSE", category="FEED", date=f"{m2}-10")
    data = await get_finance(client, owner, month=m1, type="EXPENSE", category="FEED")
    assert [t["id"] for t in data["transactions"]] == [hit["id"]]


# ---------------------------------------------------------------------------
# GET /api/finance — monthly P&L
# ---------------------------------------------------------------------------
async def test_pnl_single_month_hand_computed(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    d = today().replace(day=1)
    await add_txn(client, owner, type="INCOME", category="ANIMAL_SALE", amount=1000.0, date=iso(d))
    await add_txn(client, owner, type="INCOME", category="MILK", amount=500.5, date=iso(d))
    await add_txn(client, owner, type="EXPENSE", category="FEED", amount=200.25, date=iso(d))
    data = await get_finance(client, owner)
    assert len(data["pnl"]) == 1
    row = data["pnl"][0]
    assert row["month"] == d.strftime("%Y-%m")
    assert row["income"] == 1500.5
    assert row["expense"] == 200.25
    assert row["net"] == 1300.25
    assert row["categories"] == {
        "ANIMAL_SALE": {"income": 1000.0, "expense": 0.0},
        "MILK": {"income": 500.5, "expense": 0.0},
        "FEED": {"income": 0.0, "expense": 200.25},
    }


async def test_pnl_rounding_to_two_decimals(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await add_txn(client, owner, type="INCOME", amount=0.1)
    await add_txn(client, owner, type="INCOME", amount=0.2)
    data = await get_finance(client, owner)
    row = data["pnl"][0]
    assert row["income"] == 0.3  # not 0.30000000000000004
    assert row["net"] == 0.3


async def test_pnl_ordered_most_recent_month_first(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    first = today().replace(day=1)
    months = [add_months(first, -n) for n in (2, 0, 1)]  # insert out of order
    for m in months:
        await add_txn(client, owner, date=iso(m))
    pnl = (await get_finance(client, owner))["pnl"]
    assert [r["month"] for r in pnl] == [m.strftime("%Y-%m") for m in sorted(months, reverse=True)]


async def test_pnl_capped_at_12_months(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    first = today().replace(day=1)
    months = [add_months(first, -n) for n in range(14)]
    for m in months:
        await add_txn(client, owner, date=iso(m))
    pnl = (await get_finance(client, owner))["pnl"]
    assert len(pnl) == 12
    kept = {r["month"] for r in pnl}
    assert first.strftime("%Y-%m") in kept  # newest kept
    assert months[12].strftime("%Y-%m") not in kept  # oldest two dropped
    assert months[13].strftime("%Y-%m") not in kept


async def test_pnl_present_despite_filters(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await add_txn(client, owner, type="INCOME", amount=10.0, date=iso(today().replace(day=1)))
    data = await get_finance(client, owner, month="2020-01", type="INCOME", category="MILK")
    assert data["transactions"] == []  # list is filtered...
    assert len(data["pnl"]) == 1  # ...but the P&L is not


# ---------------------------------------------------------------------------
# Finance — automatic transactions from animal sales (SPEC: sale auto-creates
# an INCOME transaction)
# ---------------------------------------------------------------------------
async def test_sale_creates_income_transaction(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="MEAT-1", sex="M", bucket="MALE_KIDS")
    await change_status(client, owner, animal["id"], "SOLD", sale_price=15000.0, buyer_name="Raju")
    data = await get_finance(client, owner)
    assert data["total_income"] == 15000.0
    assert len(data["transactions"]) == 1
    txn = data["transactions"][0]
    assert txn["type"] == "INCOME"
    assert txn["category"] == "ANIMAL_SALE"
    assert txn["amount"] == 15000.0
    assert txn["related_animal_id"] == animal["id"]
    assert txn["animal_tag"] == "MEAT-1"
    assert "Sale of MEAT-1" in txn["notes"]


async def test_sale_without_price_creates_no_transaction(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="MEAT-2", sex="M", bucket="MALE_KIDS")
    await change_status(client, owner, animal["id"], "SOLD")
    assert (await get_finance(client, owner))["transactions"] == []


async def test_sale_with_zero_price_creates_no_transaction(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="MEAT-3", sex="M", bucket="MALE_KIDS")
    await change_status(client, owner, animal["id"], "SOLD", sale_price=0)
    assert (await get_finance(client, owner))["transactions"] == []


async def test_death_creates_no_transaction(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="DEAD-1")
    await change_status(client, owner, animal["id"], "DEAD")
    assert (await get_finance(client, owner))["transactions"] == []


async def test_finance_cross_farm_isolation(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Farm A")
    owner_b = await owner_with_farm(client, email="b@farm.in", farm_name="Farm B")
    await add_txn(client, owner_a, type="INCOME", category="MILK", amount=111.0)
    await add_txn(client, owner_b, type="EXPENSE", category="FEED", amount=222.0)
    data_a = await get_finance(client, owner_a)
    assert data_a["total_income"] == 111.0
    assert data_a["total_expense"] == 0.0
    assert len(data_a["transactions"]) == 1
    data_b = await get_finance(client, owner_b)
    assert data_b["total_income"] == 0.0
    assert data_b["total_expense"] == 222.0
    assert len(data_b["transactions"]) == 1
    # P&L rows are per-farm too.
    assert data_a["pnl"][0]["income"] == 111.0
    assert data_b["pnl"][0]["expense"] == 222.0


# ---------------------------------------------------------------------------
# GET /api/dashboard — herd counts, tasks, due lists
# ---------------------------------------------------------------------------
async def test_dashboard_empty_farm(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    dash = await get_dashboard(client, owner)
    assert [b["code"] for b in dash["buckets"]] == BUCKET_ORDER
    assert all(b["count"] == 0 for b in dash["buckets"])
    assert all(b["name"] for b in dash["buckets"])
    assert dash["total_active"] == 0
    assert dash["sex_counts"] == {"M": 0, "F": 0}
    assert dash["status_totals"] == {}
    assert dash["todays_tasks"] == []
    assert dash["overdue_tasks"] == []
    assert dash["ultrasounds_due"] == []
    assert dash["kiddings_due"] == []
    assert dash["cull_candidates"] == []
    assert dash["suggestions"] == []
    assert dash["recent_weights"] == []


async def test_dashboard_bucket_counts_hand_computed(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await make_animal(client, owner, tag="F-1", sex="F", bucket="FOUNDATION")
    await make_animal(client, owner, tag="F-2", sex="F", bucket="FOUNDATION")
    await make_animal(client, owner, tag="B-1", sex="M", bucket="BREEDING")
    await make_animal(client, owner, tag="Q-1", sex="M", bucket="QUARANTINE")
    dash = await get_dashboard(client, owner)
    counts = {b["code"]: b["count"] for b in dash["buckets"]}
    assert counts["FOUNDATION"] == 2
    assert counts["BREEDING"] == 1
    assert counts["QUARANTINE"] == 1
    assert sum(counts.values()) == 4
    assert dash["total_active"] == 4
    assert dash["sex_counts"] == {"M": 2, "F": 2}
    assert dash["status_totals"] == {"ACTIVE": 4}


async def test_dashboard_excludes_non_active_from_buckets(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    keep = await make_animal(client, owner, tag="K-1", sex="F", bucket="FOUNDATION")
    sold = await make_animal(client, owner, tag="S-1", sex="F", bucket="FOUNDATION")
    dead = await make_animal(client, owner, tag="D-1", sex="M", bucket="FOUNDATION")
    await change_status(client, owner, sold["id"], "SOLD")
    await change_status(client, owner, dead["id"], "DEAD")
    dash = await get_dashboard(client, owner)
    counts = {b["code"]: b["count"] for b in dash["buckets"]}
    assert counts["FOUNDATION"] == 1
    assert dash["total_active"] == 1
    assert dash["sex_counts"] == {"M": 0, "F": 1}
    assert dash["status_totals"] == {"ACTIVE": 1, "SOLD": 1, "DEAD": 1}
    assert keep["id"] != sold["id"]  # sanity: distinct animals


async def test_dashboard_status_totals_all_statuses(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    a1 = await make_animal(client, owner, tag="T-1")
    a2 = await make_animal(client, owner, tag="T-2")
    a3 = await make_animal(client, owner, tag="T-3")
    await make_animal(client, owner, tag="T-4")  # stays ACTIVE
    await change_status(client, owner, a1["id"], "SOLD")
    await change_status(client, owner, a2["id"], "DEAD")
    await change_status(client, owner, a3["id"], "CULLED")
    dash = await get_dashboard(client, owner)
    assert dash["status_totals"] == {"ACTIVE": 1, "SOLD": 1, "DEAD": 1, "CULLED": 1}


async def test_dashboard_todays_tasks(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await create_duty(client, owner, "Sweep the shed", today(), category="CLEANING")
    await create_duty(client, owner, "Tomorrow duty", today() + timedelta(days=1))
    await create_duty(client, owner, "Old duty", today() - timedelta(days=1))
    dash = await get_dashboard(client, owner)
    assert [t["title"] for t in dash["todays_tasks"]] == ["Sweep the shed"]
    assert [t["title"] for t in dash["overdue_tasks"]] == ["Old duty"]


async def test_dashboard_overdue_tasks_oldest_first(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await create_duty(client, owner, "one day late", today() - timedelta(days=1))
    await create_duty(client, owner, "five days late", today() - timedelta(days=5))
    await create_duty(client, owner, "three days late", today() - timedelta(days=3))
    dash = await get_dashboard(client, owner)
    assert [t["title"] for t in dash["overdue_tasks"]] == [
        "five days late",
        "three days late",
        "one day late",
    ]


async def test_dashboard_completed_task_not_listed(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    duty = await create_duty(client, owner, "Done duty", today())
    resp = await client.post(f"/api/tasks/{duty['id']}/complete", headers=owner)
    assert resp.status_code == 200, resp.text
    dash = await get_dashboard(client, owner)
    assert dash["todays_tasks"] == []
    assert dash["overdue_tasks"] == []


async def test_dashboard_ultrasound_due_within_7_days(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner, tag="US-1")
    buck = await make_buck(client, owner, tag="US-1-BUCK")
    # Ultrasound auto-task = breeding + 32d → due in 7 days.
    await make_breeding(client, owner, doe["id"], buck["id"], today() - timedelta(days=25))
    dash = await get_dashboard(client, owner)
    assert len(dash["ultrasounds_due"]) == 1
    assert dash["ultrasounds_due"][0]["category"] == "ULTRASOUND"
    assert dash["ultrasounds_due"][0]["due_date"] == iso(today() + timedelta(days=7))
    assert dash["overdue_tasks"] == []


async def test_dashboard_ultrasound_far_future_not_listed(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner, tag="US-2")
    buck = await make_buck(client, owner, tag="US-2-BUCK")
    await make_breeding(client, owner, doe["id"], buck["id"], today())
    dash = await get_dashboard(client, owner)
    assert dash["ultrasounds_due"] == []  # due in 32 days — outside the 7-day window


async def test_dashboard_overdue_ultrasound_listed(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner, tag="US-3")
    buck = await make_buck(client, owner, tag="US-3-BUCK")
    # Due 3 days ago → both in ultrasounds_due and in overdue_tasks.
    await make_breeding(client, owner, doe["id"], buck["id"], today() - timedelta(days=35))
    dash = await get_dashboard(client, owner)
    assert len(dash["ultrasounds_due"]) == 1
    overdue_cats = [t["category"] for t in dash["overdue_tasks"]]
    assert "ULTRASOUND" in overdue_cats


async def test_dashboard_kidding_due_within_14_days(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    # Due in 10 days (breeding 140 days ago + 150-day gestation).
    doe1 = await make_doe(client, owner, tag="KD-1")
    buck1 = await make_buck(client, owner, tag="KD-1-BUCK")
    br1 = await make_breeding(client, owner, doe1["id"], buck1["id"], today() - timedelta(days=140))
    await ultrasound(client, owner, br1["id"], pregnant=True)
    # Already overdue by 20 days — no lower bound on the dashboard query.
    doe2 = await make_doe(client, owner, tag="KD-2")
    buck2 = await make_buck(client, owner, tag="KD-2-BUCK")
    br2 = await make_breeding(client, owner, doe2["id"], buck2["id"], today() - timedelta(days=170))
    await ultrasound(client, owner, br2["id"], pregnant=True)
    dash = await get_dashboard(client, owner)
    due_tags = {r["doe_tag"] for r in dash["kiddings_due"]}
    assert due_tags == {"KD-1", "KD-2"}
    by_tag = {r["doe_tag"]: r for r in dash["kiddings_due"]}
    assert by_tag["KD-1"]["expected_kidding_date"] == iso(today() + timedelta(days=10))
    assert by_tag["KD-2"]["expected_kidding_date"] == iso(today() - timedelta(days=20))
    assert all(r["has_kidding"] is False for r in dash["kiddings_due"])


async def test_dashboard_pending_breeding_not_in_kiddings_due(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner, tag="KD-3")
    buck = await make_buck(client, owner, tag="KD-3-BUCK")
    # No ultrasound yet → outcome PENDING → never a kidding-due row.
    await make_breeding(client, owner, doe["id"], buck["id"], today() - timedelta(days=160))
    dash = await get_dashboard(client, owner)
    assert dash["kiddings_due"] == []


async def test_dashboard_failed_breeding_not_in_kiddings_due(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner, tag="KD-4")
    buck = await make_buck(client, owner, tag="KD-4-BUCK")
    br = await make_breeding(client, owner, doe["id"], buck["id"], today() - timedelta(days=160))
    await ultrasound(client, owner, br["id"], pregnant=False)
    dash = await get_dashboard(client, owner)
    assert dash["kiddings_due"] == []


async def test_dashboard_kidding_recorded_removes_row(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner, tag="KD-5")
    buck = await make_buck(client, owner, tag="KD-5-BUCK")
    br = await make_breeding(client, owner, doe["id"], buck["id"], today() - timedelta(days=160))
    await ultrasound(client, owner, br["id"], pregnant=True)
    assert len((await get_dashboard(client, owner))["kiddings_due"]) == 1
    await record_kidding(
        client, owner, br["id"], today(), [{"sex": "F", "birth_weight": 2.4, "status": "ALIVE"}]
    )
    dash = await get_dashboard(client, owner)
    assert dash["kiddings_due"] == []


async def test_dashboard_cull_candidate_after_two_failed_cycles(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    # SPEC: a doe with 2 consecutive FAILED records → cull_candidate flag.
    doe = await make_doe(client, owner, tag="CULL-1")
    buck = await make_buck(client, owner, tag="CULL-1-BUCK")
    br1 = await make_breeding(
        client, owner, doe["id"], buck["id"], today() - timedelta(days=60), heat_cycle_number=1
    )
    await ultrasound(client, owner, br1["id"], pregnant=False)
    dash = await get_dashboard(client, owner)
    assert dash["cull_candidates"] == []  # one failure is not enough
    br2 = await make_breeding(
        client, owner, doe["id"], buck["id"], today() - timedelta(days=35), heat_cycle_number=2
    )
    await ultrasound(client, owner, br2["id"], pregnant=False)
    dash = await get_dashboard(client, owner)
    assert [a["id"] for a in dash["cull_candidates"]] == [doe["id"]]
    assert dash["cull_candidates"][0]["cull_candidate"] is True


async def test_dashboard_sold_cull_candidate_removed(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner, tag="CULL-2")
    buck = await make_buck(client, owner, tag="CULL-2-BUCK")
    for cycle, days in ((1, 60), (2, 35)):
        br = await make_breeding(
            client,
            owner,
            doe["id"],
            buck["id"],
            today() - timedelta(days=days),
            heat_cycle_number=cycle,
        )
        await ultrasound(client, owner, br["id"], pregnant=False)
    assert len((await get_dashboard(client, owner))["cull_candidates"]) == 1
    # Only ACTIVE animals are cull candidates; leaving the herd clears the flag.
    await change_status(client, owner, doe["id"], "SOLD")
    assert (await get_dashboard(client, owner))["cull_candidates"] == []


async def test_dashboard_recent_weights(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await make_animal(client, owner, tag="W-1", weight_kg=18.5)
    dash = await get_dashboard(client, owner)
    assert len(dash["recent_weights"]) == 1
    assert dash["recent_weights"][0]["weight_kg"] == 18.5
    assert dash["recent_weights"][0]["date"] == iso(today())


async def test_dashboard_recent_weights_max_10(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    for i in range(12):
        await make_animal(client, owner, tag=f"W-{i:02d}", weight_kg=10.0 + i)
    dash = await get_dashboard(client, owner)
    assert len(dash["recent_weights"]) == 10


async def test_dashboard_recent_weights_newest_first(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="W-X")
    old = await client.post(
        f"/api/animals/{animal['id']}/weight",
        json={"date": iso(today() - timedelta(days=5)), "weight_kg": 15.0},
        headers=owner,
    )
    assert old.status_code == 201, old.text
    new = await client.post(
        f"/api/animals/{animal['id']}/weight",
        json={"date": iso(today()), "weight_kg": 17.0},
        headers=owner,
    )
    assert new.status_code == 201, new.text
    dash = await get_dashboard(client, owner)
    assert [w["weight_kg"] for w in dash["recent_weights"]] == [17.0, 15.0]


async def test_dashboard_suggestion_breeding_ready_doe(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    # SPEC: breeding-ready = female, ≥10 months, ≥22 kg, in FOUNDATION.
    doe = await make_animal(
        client,
        owner,
        tag="READY-1",
        sex="F",
        bucket="FOUNDATION",
        date_of_birth=iso(today() - timedelta(days=400)),
        weight_kg=26.0,
    )
    dash = await get_dashboard(client, owner)
    suggestions = [s for s in dash["suggestions"] if s["animal"]["id"] == doe["id"]]
    assert len(suggestions) == 1
    assert suggestions[0]["to"] == "BREEDING"
    assert "Breeding-ready" in suggestions[0]["reason"]


async def test_dashboard_no_suggestion_for_underweight_doe(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    doe = await make_animal(
        client,
        owner,
        tag="LIGHT-1",
        sex="F",
        bucket="FOUNDATION",
        date_of_birth=iso(today() - timedelta(days=400)),
        weight_kg=20.0,  # below the 22 kg SPEC threshold
    )
    dash = await get_dashboard(client, owner)
    assert all(s["animal"]["id"] != doe["id"] for s in dash["suggestions"])


async def test_dashboard_cross_farm_isolation(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Farm A")
    owner_b = await owner_with_farm(client, email="b@farm.in", farm_name="Farm B")
    await make_animal(client, owner_a, tag="A-ONLY", sex="F", bucket="FOUNDATION")
    await create_duty(client, owner_a, "Farm A duty", today())
    dash_b = await get_dashboard(client, owner_b)
    assert dash_b["total_active"] == 0
    assert dash_b["todays_tasks"] == []
    assert all(b["count"] == 0 for b in dash_b["buckets"])


async def test_dashboard_requires_auth(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/dashboard")).status_code == 401


async def test_dashboard_missing_farm_header(client: httpx.AsyncClient) -> None:
    auth = await register(client)
    assert (await client.get("/api/dashboard", headers=auth)).status_code == 400


async def test_dashboard_other_users_farm_forbidden(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client, email="a@farm.in", farm_name="Farm A")
    stranger = await register(client, email="stranger@farm.in")
    headers = stranger | {"X-Farm-Id": owner["X-Farm-Id"]}
    assert (await client.get("/api/dashboard", headers=headers)).status_code == 403


async def test_dashboard_mover_worker_allowed(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    rid = await preset_role_id(client, owner, "MOVER")  # MOVER holds dashboard.view
    mover = await worker_headers(client, owner, rid, "mover@farm.in")
    assert (await client.get("/api/dashboard", headers=mover)).status_code == 200


# ---------------------------------------------------------------------------
# GET /api/dashboard/reports — herd summary, breeding performance, mortality
# ---------------------------------------------------------------------------
async def test_reports_empty_farm(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    rep = await get_reports(client, owner)
    assert [r["code"] for r in rep["bucket_rows"]] == BUCKET_ORDER
    assert all(r["count"] == 0 for r in rep["bucket_rows"])
    assert all(r["avg_weight"] is None for r in rep["bucket_rows"])
    assert rep["total_active"] == 0
    assert rep["sex_counts"] == {"M": 0, "F": 0}
    assert rep["status_counts"] == {}
    breeding = rep["breeding"]
    assert breeding["total_records"] == 0
    assert breeding["conception_rate"] is None
    assert breeding["first_cycle_rate"] is None
    assert breeding["kiddings"] == 0
    assert breeding["kids_per_kidding"] is None
    assert breeding["twin_rate"] is None
    assert breeding["cull_candidates"] == []
    mortality = rep["mortality"]
    assert mortality["total_deaths"] == 0
    assert mortality["deaths_by_month"] == []
    assert mortality["total_kids_born"] == 0
    assert mortality["stillborn"] == 0
    assert mortality["stillborn_rate"] is None


async def test_reports_bucket_counts_and_avg_weight(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await make_animal(client, owner, tag="R-1", bucket="FOUNDATION", weight_kg=20.0)
    await make_animal(client, owner, tag="R-2", bucket="FOUNDATION", weight_kg=25.0)
    await make_animal(client, owner, tag="R-3", sex="M", bucket="MALE_KIDS", weight_kg=12.0)
    rep = await get_reports(client, owner)
    rows = {r["code"]: r for r in rep["bucket_rows"]}
    assert rows["FOUNDATION"]["count"] == 2
    assert rows["FOUNDATION"]["avg_weight"] == 22.5
    assert rows["MALE_KIDS"]["count"] == 1
    assert rows["MALE_KIDS"]["avg_weight"] == 12.0
    assert rows["BREEDING"]["count"] == 0
    assert rows["BREEDING"]["avg_weight"] is None
    assert rep["total_active"] == 3


async def test_reports_avg_weight_rounding(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await make_animal(client, owner, tag="R-4", weight_kg=10.0)
    await make_animal(client, owner, tag="R-5", weight_kg=15.0)
    await make_animal(client, owner, tag="R-6", weight_kg=15.1)
    rep = await get_reports(client, owner)
    row = next(r for r in rep["bucket_rows"] if r["code"] == "FOUNDATION")
    assert row["count"] == 3
    assert row["avg_weight"] == 13.4  # 40.1 / 3 = 13.366…, rounded to 1 decimal


async def test_reports_avg_weight_uses_latest_record(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="R-7", weight_kg=20.0)
    resp = await client.post(
        f"/api/animals/{animal['id']}/weight",
        json={"date": iso(today()), "weight_kg": 23.0},
        headers=owner,
    )
    assert resp.status_code == 201, resp.text
    rep = await get_reports(client, owner)
    row = next(r for r in rep["bucket_rows"] if r["code"] == "FOUNDATION")
    assert row["avg_weight"] == 23.0  # latest weight record, not the entry one


async def test_reports_bucket_without_weights_avg_none(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await make_animal(client, owner, tag="R-8")  # no entry weight
    rep = await get_reports(client, owner)
    row = next(r for r in rep["bucket_rows"] if r["code"] == "FOUNDATION")
    assert row["count"] == 1
    assert row["avg_weight"] is None


async def test_reports_sex_counts_active_only(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    doe = await make_animal(client, owner, tag="R-9", sex="F")
    await make_animal(client, owner, tag="R-10", sex="M")
    await change_status(client, owner, doe["id"], "SOLD")
    rep = await get_reports(client, owner)
    assert rep["sex_counts"] == {"M": 1, "F": 0}
    assert rep["total_active"] == 1
    assert rep["status_counts"] == {"ACTIVE": 1, "SOLD": 1}


async def test_reports_conception_rate_hand_computed(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    doe1 = await make_doe(client, owner, tag="CR-1")
    buck1 = await make_buck(client, owner, tag="CR-1-BUCK")
    br1 = await make_breeding(client, owner, doe1["id"], buck1["id"], today() - timedelta(days=40))
    await ultrasound(client, owner, br1["id"], pregnant=True)
    doe2 = await make_doe(client, owner, tag="CR-2")
    buck2 = await make_buck(client, owner, tag="CR-2-BUCK")
    br2 = await make_breeding(client, owner, doe2["id"], buck2["id"], today() - timedelta(days=40))
    await ultrasound(client, owner, br2["id"], pregnant=False)
    rep = await get_reports(client, owner)
    breeding = rep["breeding"]
    assert breeding["total_records"] == 2
    # SPEC: conception rate = confirmed / total completed breedings.
    assert breeding["conception_rate"] == 50.0
    assert breeding["first_cycle_rate"] == 50.0  # both were first cycles


async def test_reports_first_cycle_rate_excludes_later_cycles(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner, tag="CR-3")
    buck = await make_buck(client, owner, tag="CR-3-BUCK")
    # Cycle 1 fails, cycle 2 confirms (same doe).
    br1 = await make_breeding(
        client, owner, doe["id"], buck["id"], today() - timedelta(days=60), heat_cycle_number=1
    )
    await ultrasound(client, owner, br1["id"], pregnant=False)
    br2 = await make_breeding(
        client, owner, doe["id"], buck["id"], today() - timedelta(days=35), heat_cycle_number=2
    )
    await ultrasound(client, owner, br2["id"], pregnant=True)
    rep = await get_reports(client, owner)
    breeding = rep["breeding"]
    assert breeding["total_records"] == 2
    assert breeding["conception_rate"] == 50.0  # 1 confirmed of 2 completed
    assert breeding["first_cycle_rate"] == 0.0  # the only cycle-1 record failed


async def test_reports_conception_rate_none_when_all_pending(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner, tag="CR-4")
    buck = await make_buck(client, owner, tag="CR-4-BUCK")
    await make_breeding(client, owner, doe["id"], buck["id"])  # stays PENDING
    rep = await get_reports(client, owner)
    breeding = rep["breeding"]
    assert breeding["total_records"] == 1
    assert breeding["conception_rate"] is None  # PENDING excluded → no completed breedings
    assert breeding["first_cycle_rate"] is None


async def test_reports_kidding_and_stillborn_stats(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner, tag="KK-1")
    buck = await make_buck(client, owner, tag="KK-1-BUCK")
    br = await make_breeding(client, owner, doe["id"], buck["id"], today() - timedelta(days=160))
    await ultrasound(client, owner, br["id"], pregnant=True, kid_count=3)
    await record_kidding(
        client,
        owner,
        br["id"],
        today(),
        [
            {"tag": "KK-1-A", "sex": "F", "birth_weight": 2.5, "status": "ALIVE"},
            {"tag": "KK-1-B", "sex": "M", "birth_weight": 2.3, "status": "ALIVE"},
            {"sex": "M", "status": "STILLBORN"},
        ],
    )
    rep = await get_reports(client, owner)
    breeding = rep["breeding"]
    assert breeding["kiddings"] == 1
    assert breeding["kids_per_kidding"] == 2.0  # alive kids per kidding
    assert breeding["twin_rate"] == 100.0  # ≥2 alive kids → multi-kid kidding
    mortality = rep["mortality"]
    assert mortality["total_kids_born"] == 3
    assert mortality["stillborn"] == 1
    assert mortality["stillborn_rate"] == 33.3


async def test_reports_single_kid_twin_rate_zero(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner, tag="KK-2")
    buck = await make_buck(client, owner, tag="KK-2-BUCK")
    br = await make_breeding(client, owner, doe["id"], buck["id"], today() - timedelta(days=160))
    await ultrasound(client, owner, br["id"], pregnant=True, kid_count=1)
    await record_kidding(
        client, owner, br["id"], today(), [{"sex": "F", "birth_weight": 2.8, "status": "ALIVE"}]
    )
    rep = await get_reports(client, owner)
    assert rep["breeding"]["kids_per_kidding"] == 1.0
    assert rep["breeding"]["twin_rate"] == 0.0
    assert rep["mortality"]["stillborn_rate"] == 0.0  # kids born, none stillborn


async def test_reports_mortality_by_month(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    d_recent = add_months(today(), -1)
    d_older = add_months(today(), -2)
    a1 = await make_animal(client, owner, tag="M-1")
    a2 = await make_animal(client, owner, tag="M-2")
    await change_status(client, owner, a1["id"], "DEAD", date=iso(d_recent))
    await change_status(client, owner, a2["id"], "DEAD", date=iso(d_older))
    rep = await get_reports(client, owner)
    mortality = rep["mortality"]
    assert mortality["total_deaths"] == 2
    # Most recent month first.
    assert mortality["deaths_by_month"] == [
        [d_recent.strftime("%Y-%m"), 1],
        [d_older.strftime("%Y-%m"), 1],
    ]


async def test_reports_mortality_same_month_aggregated(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    d = add_months(today(), -1)
    a1 = await make_animal(client, owner, tag="M-3")
    a2 = await make_animal(client, owner, tag="M-4")
    await change_status(client, owner, a1["id"], "DEAD", date=iso(d))
    await change_status(client, owner, a2["id"], "DEAD", date=iso(d))
    rep = await get_reports(client, owner)
    assert rep["mortality"]["total_deaths"] == 2
    assert rep["mortality"]["deaths_by_month"] == [[d.strftime("%Y-%m"), 2]]


async def test_reports_sold_not_counted_as_death(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="M-5")
    await change_status(client, owner, animal["id"], "SOLD")
    rep = await get_reports(client, owner)
    assert rep["mortality"]["total_deaths"] == 0
    assert rep["mortality"]["deaths_by_month"] == []


async def test_reports_cull_candidates_listed(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner, tag="RC-1")
    buck = await make_buck(client, owner, tag="RC-1-BUCK")
    for cycle, days in ((1, 60), (2, 35)):
        br = await make_breeding(
            client,
            owner,
            doe["id"],
            buck["id"],
            today() - timedelta(days=days),
            heat_cycle_number=cycle,
        )
        await ultrasound(client, owner, br["id"], pregnant=False)
    rep = await get_reports(client, owner)
    ids = [a["id"] for a in rep["breeding"]["cull_candidates"]]
    assert ids == [doe["id"]]


async def test_reports_cross_farm_isolation(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Farm A")
    owner_b = await owner_with_farm(client, email="b@farm.in", farm_name="Farm B")
    await make_animal(client, owner_a, tag="A-1", weight_kg=30.0)
    dead = await make_animal(client, owner_a, tag="A-2")
    await change_status(client, owner_a, dead["id"], "DEAD")
    rep_b = await get_reports(client, owner_b)
    assert rep_b["total_active"] == 0
    assert all(r["count"] == 0 for r in rep_b["bucket_rows"])
    assert rep_b["mortality"]["total_deaths"] == 0
    assert rep_b["breeding"]["total_records"] == 0


async def test_reports_requires_auth(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/dashboard/reports")).status_code == 401


async def test_reports_missing_farm_header(client: httpx.AsyncClient) -> None:
    auth = await register(client)
    assert (await client.get("/api/dashboard/reports", headers=auth)).status_code == 400


async def test_reports_other_users_farm_forbidden(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client, email="a@farm.in", farm_name="Farm A")
    stranger = await register(client, email="stranger@farm.in")
    headers = stranger | {"X-Farm-Id": owner["X-Farm-Id"]}
    assert (await client.get("/api/dashboard/reports", headers=headers)).status_code == 403


async def test_reports_mover_worker_forbidden(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    rid = await preset_role_id(client, owner, "MOVER")  # no reports.view
    mover = await worker_headers(client, owner, rid, "mover@farm.in")
    resp = await client.get("/api/dashboard/reports", headers=mover)
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Missing permission: reports.view"


async def test_reports_worker_with_permission_allowed(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    rid = await custom_role_id(client, owner, "Analyst", ["reports.view"])
    worker = await worker_headers(client, owner, rid, "analyst@farm.in")
    assert (await client.get("/api/dashboard/reports", headers=worker)).status_code == 200
