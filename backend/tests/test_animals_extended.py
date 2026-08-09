"""Extended coverage for the animals & buckets module.

Endpoints under test (backend/app/api/animals.py, backend/app/api/buckets.py):
- GET  /api/animals                     list with bucket/sex/status/q filters
- POST /api/animals                     create (tag uniqueness per farm, enums,
                                        date/weight bounds, entry weight)
- GET  /api/animals/{id}                profile (kids, weights, moves, health,
                                        breedings)
- POST /api/animals/{id}/move           bucket move (state-machine guards)
- POST /api/animals/{id}/weight         weight record (bcs 1-5, date bounds)
- POST /api/animals/{id}/status         mark SOLD/DEAD/CULLED (sale books an
                                        INCOME transaction, replay guarded)
- GET  /api/buckets                     board: 10 seeded buckets with occupancy

Note: the list endpoint is always finite (100 rows by default, 200 maximum),
returns the full filtered total, and supports limit/offset pagination.
"""

import re
from datetime import date, timedelta

import httpx
import pytest

from app.utils import today

from .conftest import owner_with_farm, register

WORKER_PW = "workerpass123"

ALL_BUCKETS = [
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


def iso(d) -> str:
    return d.isoformat()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def post_animal(client: httpx.AsyncClient, headers: dict, payload: dict) -> httpx.Response:
    return await client.post("/api/animals", json=payload, headers=headers)


async def make_animal(
    client: httpx.AsyncClient,
    headers: dict,
    tag: str = "A-001",
    sex: str = "F",
    bucket: str = "FOUNDATION",
    **overrides: object,
) -> dict:
    """Create an animal via the API; returns the AnimalOut body."""
    payload = {
        "tag_number": tag,
        "sex": sex,
        "source": "PURCHASED",
        "current_bucket": bucket,
    } | overrides
    # BREEDING is a supported historical-import cohort, but importing directly
    # into it must still satisfy the same maturity/weight safety floor as a
    # live move.  Most tests using this helper exercise unrelated list/board
    # behavior, so give those fixtures a valid history by default.
    if payload["current_bucket"] == "BREEDING":
        payload.setdefault("date_of_birth", iso(today() - timedelta(days=400)))
        payload.setdefault("weight_kg", 25.0 if payload["sex"] == "M" else 22.0)
    if payload["source"] in {"PURCHASED", "BORN"}:
        payload.setdefault("historical_import_reason", "Existing-herd test fixture")
    resp = await post_animal(client, headers, payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def get_profile(client: httpx.AsyncClient, headers: dict, animal_id: int) -> dict:
    resp = await client.get(f"/api/animals/{animal_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def get_animal(client: httpx.AsyncClient, headers: dict, animal_id: int) -> dict:
    return (await get_profile(client, headers, animal_id))["animal"]


async def list_animals(client: httpx.AsyncClient, headers: dict, **params: str) -> list[dict]:
    resp = await client.get("/api/animals", headers=headers, params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()["animals"]


async def transactions(client: httpx.AsyncClient, headers: dict) -> list[dict]:
    resp = await client.get("/api/finance", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["transactions"]


async def mark_status(
    client: httpx.AsyncClient, headers: dict, animal_id: int, status: str, **overrides: object
) -> httpx.Response:
    return await client.post(
        f"/api/animals/{animal_id}/status",
        json={"new_status": status} | overrides,
        headers=headers,
    )


async def role_id(client: httpx.AsyncClient, owner: dict, code: str) -> int:
    resp = await client.get("/api/team", headers=owner)
    assert resp.status_code == 200, resp.text
    return next(r["id"] for r in resp.json()["roles"] if r["code"] == code)


async def worker_headers(client: httpx.AsyncClient, owner: dict, code: str, email: str) -> dict:
    """Owner adds a worker with preset role `code`; returns that worker's farm headers."""
    rid = await role_id(client, owner, code)
    resp = await client.post(
        "/api/team/workers",
        json={"name": "Worker", "email": email, "password": WORKER_PW, "role_id": rid},
        headers=owner,
    )
    assert resp.status_code == 201, resp.text
    resp = await client.post("/api/auth/login", json={"email": email, "password": WORKER_PW})
    assert resp.status_code == 200, resp.text
    return {
        "Authorization": f"Bearer {resp.json()['access_token']}",
        "X-Farm-Id": owner["X-Farm-Id"],
    }


async def custom_worker_headers(
    client: httpx.AsyncClient,
    owner: dict,
    *,
    name: str,
    email: str,
    permissions: list[str],
) -> dict:
    role = await client.post(
        "/api/team/roles",
        json={"name": name, "permissions": permissions},
        headers=owner,
    )
    assert role.status_code == 201, role.text
    worker = await client.post(
        "/api/team/workers",
        json={
            "name": name,
            "email": email,
            "password": WORKER_PW,
            "role_id": role.json()["id"],
        },
        headers=owner,
    )
    assert worker.status_code == 201, worker.text
    login = await client.post("/api/auth/login", json={"email": email, "password": WORKER_PW})
    assert login.status_code == 200, login.text
    return {
        "Authorization": f"Bearer {login.json()['access_token']}",
        "X-Farm-Id": owner["X-Farm-Id"],
    }


# Breeding-flow helpers (for profile kids / cull-flag tests).
async def make_doe(client: httpx.AsyncClient, headers: dict, tag: str = "D-101") -> dict:
    dob = today() - timedelta(days=800)
    return await make_animal(
        client,
        headers,
        tag=tag,
        sex="F",
        bucket="FOUNDATION",
        date_of_birth=iso(dob),
        weight_kg=26.0,
        weight_date=iso(dob),
    )


async def make_buck(client: httpx.AsyncClient, headers: dict, tag: str = "B-01") -> dict:
    dob = today() - timedelta(days=800)
    return await make_animal(
        client,
        headers,
        tag=tag,
        sex="M",
        bucket="BREEDING",
        date_of_birth=iso(dob),
        weight_kg=30.0,
        weight_date=iso(dob),
    )


async def make_breeding(
    client: httpx.AsyncClient, headers: dict, doe_id: int, buck_id: int, breeding_date
) -> dict:
    resp = await client.post(
        "/api/breeding",
        json={"doe_id": doe_id, "buck_id": buck_id, "breeding_date": iso(breeding_date)},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def submit_ultrasound(
    client: httpx.AsyncClient,
    headers: dict,
    breeding_id: int,
    pregnant: bool,
    kid_count: int = 2,
    result_date: date | None = None,
) -> dict:
    payload: dict[str, object] = {"pregnant": pregnant}
    if pregnant:
        payload["kid_count"] = kid_count
    if result_date is not None:
        payload["date"] = iso(result_date)
    resp = await client.post(
        f"/api/breeding/{breeding_id}/ultrasound",
        json=payload,
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# 1. Auth & tenancy
# ---------------------------------------------------------------------------
async def test_animals_endpoints_require_auth(client: httpx.AsyncClient) -> None:
    for method, url in [
        ("GET", "/api/animals"),
        ("POST", "/api/animals"),
        ("GET", "/api/animals/1"),
        ("POST", "/api/animals/1/move"),
        ("POST", "/api/animals/1/weight"),
        ("POST", "/api/animals/1/status"),
        ("GET", "/api/buckets"),
    ]:
        resp = await client.request(method, url)
        assert resp.status_code == 401, (method, url)
        assert resp.json()["detail"] == "Missing bearer token"


async def test_missing_farm_header_rejected(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    auth_only = {"Authorization": headers["Authorization"]}
    # The header is required by the contract — missing fails request
    # validation (422) before the farm dependency runs.
    resp = await client.get("/api/animals", headers=auth_only)
    assert resp.status_code == 422
    assert "x-farm-id" in str(resp.json()["detail"]).lower()
    resp = await client.get("/api/buckets", headers=auth_only)
    assert resp.status_code == 422
    resp = await client.post("/api/animals", json={}, headers=auth_only)
    assert resp.status_code == 422


async def test_non_integer_farm_header_rejected(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    for bad in ["abc", "1.5", "1e3", ""]:
        resp = await client.get("/api/animals", headers=headers | {"X-Farm-Id": bad})
        assert resp.status_code == 400, bad
        assert resp.json()["detail"] == "X-Farm-Id must be an integer"


async def test_out_of_range_farm_header_rejected(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    for bad in ["0", "-3", str(2**62)]:
        resp = await client.get("/api/animals", headers=headers | {"X-Farm-Id": bad})
        assert resp.status_code == 400, bad
        assert resp.json()["detail"] == "X-Farm-Id out of range"


async def test_nonexistent_farm_header_404(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await client.get("/api/animals", headers=headers | {"X-Farm-Id": "999999"})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Farm not found"


async def test_other_users_farm_not_found(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Alpha Farm")
    owner_b = await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    # B's token against A's farm → 404 like any unknown farm, never data
    headers = owner_b | {"X-Farm-Id": owner_a["X-Farm-Id"]}
    for method, url in [("GET", "/api/animals"), ("GET", "/api/buckets"), ("POST", "/api/animals")]:
        resp = await client.request(method, url, headers=headers)
        assert resp.status_code == 404, (method, url)
        assert resp.json()["detail"] == "Farm not found"


async def test_cross_farm_animal_ids_return_404(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Alpha Farm")
    owner_b = await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    foreign = await make_animal(client, owner_b, tag="B-999")

    resp = await client.get(f"/api/animals/{foreign['id']}", headers=owner_a)
    assert resp.status_code == 404
    resp = await client.post(
        f"/api/animals/{foreign['id']}/move", json={"to_bucket": "BREEDING"}, headers=owner_a
    )
    assert resp.status_code == 404
    resp = await client.post(
        f"/api/animals/{foreign['id']}/weight", json={"weight_kg": 10}, headers=owner_a
    )
    assert resp.status_code == 404
    resp = await mark_status(client, owner_a, foreign["id"], "SOLD", sale_price=1000)
    assert resp.status_code == 404
    # nothing leaked and nothing changed on farm B
    assert (await get_animal(client, owner_b, foreign["id"]))["status"] == "ACTIVE"
    assert await transactions(client, owner_b) == []


async def test_list_never_leaks_other_farms_animals(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Alpha Farm")
    owner_b = await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    await make_animal(client, owner_b, tag="B-001")
    await make_animal(client, owner_b, tag="B-002")
    assert await list_animals(client, owner_a) == []
    assert {a["tag_number"] for a in await list_animals(client, owner_b)} == {"B-001", "B-002"}


async def test_tag_uniqueness_is_per_farm(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Alpha Farm")
    owner_b = await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    a = await make_animal(client, owner_a, tag="A-001")
    b = await make_animal(client, owner_b, tag="A-001")
    assert a["tag_number"] == b["tag_number"] == "A-001"
    assert a["id"] != b["id"]


# ---------------------------------------------------------------------------
# 2. Create — happy paths
# ---------------------------------------------------------------------------
async def test_create_minimal_animal_defaults(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner)
    assert animal["tag_number"] == "A-001"
    assert animal["breed"] == "Osmanabadi"  # SPEC default breed
    assert animal["status"] == "ACTIVE"
    assert animal["cull_candidate"] is False
    assert animal["name"] is None
    assert animal["dam_id"] is None and animal["sire_id"] is None
    assert animal["date_of_birth"] is None and animal["estimated_dob"] is None
    assert animal["birth_type"] is None and animal["birth_weight"] is None
    assert animal["status_date"] is None and animal["sale_price"] is None
    assert animal["age_months"] is None
    assert animal["latest_weight_kg"] is None
    assert animal["is_breeding_ready"] is False
    assert animal["is_currently_pregnant"] is False
    assert animal["days_in_current_bucket"] == 0
    assert animal["created_at"] is not None


async def test_create_purchased_payload_stores_purchase_provenance(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    payload = {
        "tag_number": "P-100",
        "name": "Lakshmi",
        "sex": "F",
        "source": "PURCHASED",
        "current_bucket": "QUARANTINE",
        "breed": "Osmanabadi",
        "estimated_dob": iso(today() - timedelta(days=305)),
        "purchase_date": iso(today() - timedelta(days=10)),
        "purchase_price": 9500.5,
        "seller_name": "Kurnool Traders",
        "weight_kg": 21.5,
        "notes": "  calm doe  ",
    }
    resp = await post_animal(client, owner, payload)
    assert resp.status_code == 201, resp.text
    animal = resp.json()
    assert animal["name"] == "Lakshmi"
    assert animal["estimated_dob"] == payload["estimated_dob"]
    assert animal["birth_type"] is None
    assert animal["birth_weight"] is None
    assert animal["purchase_date"] == payload["purchase_date"]
    assert animal["purchase_price"] == 9500.5
    assert animal["seller_name"] == "Kurnool Traders"
    assert animal["notes"] == "calm doe"  # stripped
    assert animal["latest_weight_kg"] == 21.5


async def test_create_records_initial_bucket_move(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, bucket="QUARANTINE")
    moves = (await get_profile(client, owner, animal["id"]))["moves"]
    assert len(moves) == 1
    assert moves[0]["from_bucket"] is None
    assert moves[0]["to_bucket"] == "QUARANTINE"
    assert moves[0]["reason"] == "Historical import: Existing-herd test fixture"
    assert moves[0]["moved_at"] is not None


async def test_create_with_entry_weight_creates_record(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, weight_kg=24.5)
    assert animal["latest_weight_kg"] == 24.5
    weights = (await get_profile(client, owner, animal["id"]))["weights"]
    assert len(weights) == 1
    assert weights[0]["weight_kg"] == 24.5
    assert weights[0]["date"] == iso(today())
    assert weights[0]["notes"] == "Entry weight"
    assert weights[0]["bcs"] is None


async def test_create_without_entry_weight_has_no_records(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner)
    profile = await get_profile(client, owner, animal["id"])
    assert profile["weights"] == []
    assert profile["animal"]["latest_weight_kg"] is None


async def test_create_latest_weight_falls_back_to_birth_weight(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    payload = {
        "tag_number": "BIRTH-WEIGHT-FALLBACK",
        "sex": "F",
        "source": "BORN",
        "current_bucket": "FEMALE_KIDS",
        "birth_weight": 2.4,
        "historical_import_reason": "Existing-herd test fixture",
    }
    keyed = owner | {"Idempotency-Key": "birth-weight-fallback"}
    created = await post_animal(client, keyed, payload)
    assert created.status_code == 201, created.text
    replay = await post_animal(client, keyed, payload)
    assert replay.status_code == 201, replay.text
    assert replay.headers["Idempotency-Replayed"] == "true"
    profile = await get_profile(client, owner, created.json()["id"])

    assert created.json()["latest_weight_kg"] == 2.4
    assert replay.json() == created.json()
    assert profile["animal"] == created.json()


async def test_create_strips_tag_whitespace(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag_number="  A-007  ")
    assert animal["tag_number"] == "A-007"


async def test_create_whitespace_optional_strings_become_none(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, name="   ", seller_name="  ", notes="   ")
    assert animal["name"] is None
    assert animal["seller_name"] is None
    assert animal["notes"] is None


async def test_create_empty_breed_defaults_osmanabadi(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, breed="   ")
    assert animal["breed"] == "Osmanabadi"


async def test_create_born_source_with_birth_fields(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(
        client,
        owner,
        tag="K-001",
        source="BORN",
        bucket="FEMALE_KIDS",
        date_of_birth=iso(today() - timedelta(days=90)),
        birth_type="TRIPLET",
        birth_weight=2.1,
    )
    assert animal["source"] == "BORN"
    assert animal["birth_type"] == "TRIPLET"
    assert animal["age_months"] == 2


async def test_direct_born_and_workflow_state_imports_are_owner_only_and_bounded(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    creator = await custom_worker_headers(
        client,
        owner,
        name="Animal registrar",
        email="animal-import-forger@farm.in",
        permissions=["animals.view", "animals.create"],
    )
    base = {
        "sex": "F",
        "source": "BORN",
        "historical_import_reason": "Claimed legacy row",
        "date_of_birth": iso(today() - timedelta(days=500)),
        "weight_kg": 30,
    }
    for index, bucket in enumerate(
        ("BREEDING", "PREGNANCY_EARLY", "PREGNANCY_LATE", "DELIVERY", "RECOVERY")
    ):
        forged = await post_animal(
            client,
            creator,
            base | {"tag_number": f"FORGED-{index}", "current_bucket": bucket},
        )
        assert forged.status_code == 403, forged.text
        assert "Only the farm owner" in forged.json()["detail"]

    missing_audit = await post_animal(
        client,
        owner,
        {
            "tag_number": "BORN-WITHOUT-AUDIT",
            "sex": "F",
            "source": "BORN",
            "current_bucket": "FEMALE_KIDS",
        },
    )
    assert missing_audit.status_code == 422

    for index, bucket in enumerate(("PREGNANCY_EARLY", "PREGNANCY_LATE", "DELIVERY", "RECOVERY")):
        impossible = await post_animal(
            client,
            owner,
            base | {"tag_number": f"OWNER-WORKFLOW-{index}", "current_bucket": bucket},
        )
        assert impossible.status_code == 422, impossible.text

    immature_buck = await post_animal(
        client,
        owner,
        {
            "tag_number": "IMMATURE-IMPORT-BUCK",
            "sex": "M",
            "source": "BORN",
            "current_bucket": "BREEDING",
            "historical_import_reason": "Legacy buck row",
            "date_of_birth": iso(today() - timedelta(days=300)),
            "weight_kg": 24.99,
        },
    )
    assert immature_buck.status_code == 422


async def test_create_rejects_mixed_birth_and_purchase_provenance(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    purchased_with_birth = await post_animal(
        client,
        owner,
        {
            "tag_number": "P-MIXED",
            "sex": "F",
            "source": "PURCHASED",
            "current_bucket": "QUARANTINE",
            "birth_weight": 2.5,
        },
    )
    born_with_purchase = await post_animal(
        client,
        owner,
        {
            "tag_number": "B-MIXED",
            "sex": "F",
            "source": "BORN",
            "current_bucket": "RECOVERY",
            "seller_name": "Unverified seller",
        },
    )
    assert purchased_with_birth.status_code == 422
    assert born_with_purchase.status_code == 422


async def test_create_rejects_sex_incompatible_lifecycle_bucket(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    for payload in (
        {
            "tag_number": "F-IN-MALE-KIDS",
            "sex": "F",
            "source": "BORN",
            "current_bucket": "MALE_KIDS",
        },
        {
            "tag_number": "M-IN-DELIVERY",
            "sex": "M",
            "source": "PURCHASED",
            "current_bucket": "DELIVERY",
        },
        {
            "tag_number": "M-IN-RESTING",
            "sex": "M",
            "source": "PURCHASED",
            "current_bucket": "RESTING",
        },
    ):
        response = await post_animal(client, owner, payload)
        assert response.status_code == 422, response.text


async def test_create_purchased_in_quarantine(client: httpx.AsyncClient) -> None:
    """SPEC bucket table: QUARANTINE is where newly purchased animals live."""
    owner = await owner_with_farm(client)
    animal = await make_animal(
        client,
        owner,
        tag="Q-001",
        bucket="QUARANTINE",
        purchase_date=iso(today()),
        purchase_price=8000,
    )
    assert animal["current_bucket"] == "QUARANTINE"
    resp = await client.get("/api/buckets", headers=owner)
    assert resp.status_code == 200, resp.text
    quarantine = next(r for r in resp.json() if r["bucket"] == "QUARANTINE")
    assert [a["tag_number"] for a in quarantine["animals"]] == ["Q-001"]


async def test_individual_purchase_is_forced_into_managed_quarantine(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    response = await post_animal(
        client,
        owner,
        {
            "tag_number": "Q-FORCED",
            "sex": "F",
            "source": "PURCHASED",
            "current_bucket": "FOUNDATION",
            "purchase_date": iso(today()),
        },
    )
    assert response.status_code == 201, response.text
    animal = response.json()
    assert animal["current_bucket"] == "QUARANTINE"
    batches = (await client.get("/api/purchases", headers=owner)).json()
    assert batches["total"] == 1
    assert batches["batches"][0]["animals_created"] == 1
    assert batches["batches"][0]["open_tasks"] == 8


async def test_historical_purchase_import_requires_and_audits_a_reason(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(
        client,
        owner,
        tag="HIST-1",
        bucket="FOUNDATION",
        historical_import_reason="Migrated from the 2024 paper register",
    )
    assert animal["current_bucket"] == "FOUNDATION"
    moves = (await get_profile(client, owner, animal["id"]))["moves"]
    assert moves[0]["reason"] == "Historical import: Migrated from the 2024 paper register"
    assert (await client.get("/api/purchases", headers=owner)).json()["total"] == 0


async def test_historical_import_with_a_price_requires_its_purchase_date(
    client: httpx.AsyncClient,
) -> None:
    """A historical import stores purchase_date verbatim, so a price without a
    date used to book the acquisition of an old herd into today's ledger."""
    owner = await owner_with_farm(client)
    base = {
        "sex": "F",
        "source": "PURCHASED",
        "current_bucket": "FOUNDATION",
        "historical_import_reason": "Migrated from the 2022 paper register",
        "purchase_price": 8000.0,
    }
    undated = await post_animal(client, owner, base | {"tag_number": "HIST-NODATE"})
    assert undated.status_code == 422, undated.text
    assert "purchase_date is required" in undated.json()["detail"]
    assert await transactions(client, owner) == []

    bought_on = today() - timedelta(days=1200)
    dated = await post_animal(
        client,
        owner,
        base | {"tag_number": "HIST-DATED", "purchase_date": iso(bought_on)},
    )
    assert dated.status_code == 201, dated.text
    assert dated.json()["purchase_date"] == iso(bought_on)
    booked = await transactions(client, owner)
    assert len(booked) == 1
    # The expense belongs to the period the herd was bought in, not to today.
    assert booked[0]["date"] == iso(bought_on)
    assert booked[0]["amount"] == 8000.0


async def test_imported_quarantine_animal_can_still_leave_quarantine(
    client: httpx.AsyncClient,
) -> None:
    """A mid-quarantine import owns no batch, so no day-45 release duty was
    ever generated for it: the manual release is its only way out."""
    owner = await owner_with_farm(client)
    imported = await make_animal(
        client,
        owner,
        tag="Q-9",
        bucket="QUARANTINE",
        historical_import_reason="mid-quarantine herd migration",
    )
    # No batch, therefore no protocol series and no day-45 release duty.
    assert (await client.get("/api/purchases", headers=owner)).json()["total"] == 0
    resp = await client.post(
        f"/api/animals/{imported['id']}/move",
        json={"to_bucket": "FOUNDATION", "reason": "Quarantine completed before migration"},
        headers=owner,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["current_bucket"] == "FOUNDATION"

    # A batch animal still has to come out through its guarded protocol duty.
    batch = await client.post(
        "/api/purchases/new",
        json={"date": iso(today()), "count": 1},
        headers=owner,
    )
    assert batch.status_code == 201, batch.text
    detail = await client.get(f"/api/purchases/{batch.json()['id']}", headers=owner)
    batch_animal = detail.json()["animals"][0]
    blocked = await client.post(
        f"/api/animals/{batch_animal['id']}/move",
        json={"to_bucket": "FOUNDATION"},
        headers=owner,
    )
    assert blocked.status_code == 409, blocked.text
    assert "guarded batch task" in blocked.json()["detail"]


async def test_create_age_months_from_dob(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    # 731 days is always >= 24 full months and < 25, whatever the leap days
    animal = await make_animal(client, owner, date_of_birth=iso(today() - timedelta(days=731)))
    assert animal["age_months"] == 24


async def test_create_age_months_prefers_dob_over_estimated(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(
        client,
        owner,
        date_of_birth=iso(today() - timedelta(days=731)),  # 24 months
        estimated_dob=iso(today() - timedelta(days=305)),  # 10 months
    )
    assert animal["age_months"] == 24


async def test_create_age_months_from_estimated_dob(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, estimated_dob=iso(today() - timedelta(days=305)))
    assert animal["age_months"] == 10


async def test_create_unicode_name_and_notes(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(
        client,
        owner,
        tag="U-001",
        name="లక్ష్మి మేక 🐐",
        notes="Продается за ₹9500 — haleem à gogo",
    )
    assert animal["name"] == "లక్ష్మి మేక 🐐"
    profile = await get_profile(client, owner, animal["id"])
    assert profile["animal"]["notes"] == "Продается за ₹9500 — haleem à gogo"


async def test_create_emoji_tag_roundtrip(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="🐐-001")
    assert animal["tag_number"] == "🐐-001"
    found = await list_animals(client, owner, q="🐐-001")
    assert [a["id"] for a in found] == [animal["id"]]


async def test_create_sql_injection_tag_stored_literally(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    evil = "A-1'; DROP TABLE animals;--"
    animal = await make_animal(client, owner, tag=evil)
    assert animal["tag_number"] == evil
    # table still alive; search with the literal string finds exactly it
    found = await list_animals(client, owner, q=evil)
    assert [a["tag_number"] for a in found] == [evil]
    assert (await client.get("/api/animals", headers=owner)).json()["total"] == 1


async def test_create_notes_are_bounded_to_common_free_text_limit(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    rejected = await post_animal(
        client,
        owner,
        {
            "tag_number": "NOTE-LONG",
            "sex": "F",
            "source": "PURCHASED",
            "current_bucket": "FOUNDATION",
            "notes": "n" * 4001,
        },
    )
    assert rejected.status_code == 422, rejected.text
    animal = await make_animal(client, owner, tag="NOTE-MAX", notes="n" * 4000)
    assert animal["notes"] == "n" * 4000


async def test_create_rejects_unknown_extra_fields(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await post_animal(
        client,
        owner,
        {
            "tag_number": "X-001",
            "sex": "F",
            "source": "PURCHASED",
            "current_bucket": "FOUNDATION",
            "photo_path": "/etc/passwd",
            "farm_id": 1,
            "status": "DEAD",
            "id": 42,
        },
    )
    assert resp.status_code == 422, resp.text
    assert (await client.get("/api/animals", headers=owner)).json()["total"] == 0


async def test_animal_mutation_bodies_reject_unknown_fields(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner)
    requests = [
        (f"/api/animals/{animal['id']}/weight", {"weight_kg": 20, "weigt_kg": 20}),
        (f"/api/animals/{animal['id']}/move", {"to_bucket": "FOUNDATION", "force": True}),
        (f"/api/animals/{animal['id']}/status", {"new_status": "SOLD", "force": True}),
    ]
    for path, payload in requests:
        response = await client.post(path, json=payload, headers=owner)
        assert response.status_code == 422, (path, response.text)


# ---------------------------------------------------------------------------
# 3. Create — validation
# ---------------------------------------------------------------------------
async def test_create_missing_required_fields_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    base = {
        "tag_number": "G-1",
        "sex": "F",
        "source": "PURCHASED",
        "current_bucket": "FOUNDATION",
    }
    resp = await post_animal(client, owner, {})
    assert resp.status_code == 422
    for field in ["sex", "source", "current_bucket"]:  # tag_number is optional (auto-generated)
        payload = {k: v for k, v in base.items() if k != field}
        resp = await post_animal(client, owner, payload)
        assert resp.status_code == 422, field
    assert (await client.get("/api/animals", headers=owner)).json()["total"] == 0


async def test_create_invalid_sex_values_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    base = {"tag_number": "G-1", "sex": "F", "source": "PURCHASED", "current_bucket": "FOUNDATION"}
    for bad in ["X", "m", "f", "Male", "", 5, True, None]:
        resp = await post_animal(client, owner, base | {"sex": bad})
        assert resp.status_code == 422, bad


async def test_create_valid_sex_enum_values(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    male = await make_animal(client, owner, tag="M-1", sex="M")
    female = await make_animal(client, owner, tag="F-1", sex="F")
    assert male["sex"] == "M" and female["sex"] == "F"


async def test_create_invalid_source_values_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    base = {"tag_number": "G-1", "sex": "F", "source": "PURCHASED", "current_bucket": "FOUNDATION"}
    for bad in ["STOLEN", "born", "purchased", "", None, 3]:
        resp = await post_animal(client, owner, base | {"source": bad})
        assert resp.status_code == 422, bad


async def test_create_invalid_bucket_values_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    base = {"tag_number": "G-1", "sex": "F", "source": "PURCHASED", "current_bucket": "FOUNDATION"}
    for bad in ["MOON", "quarantine", "", None, 7]:
        resp = await post_animal(client, owner, base | {"current_bucket": bad})
        assert resp.status_code == 422, bad


async def test_create_every_valid_bucket_accepted(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    importable_buckets = [
        "QUARANTINE",
        "FOUNDATION",
        "BREEDING",
        "RESTING",
        "MALE_KIDS",
        "FEMALE_KIDS",
    ]
    for i, bucket in enumerate(importable_buckets):
        sex = "M" if bucket == "MALE_KIDS" else "F"
        animal = await make_animal(client, owner, tag=f"B-{i}", sex=sex, bucket=bucket)
        assert animal["current_bucket"] == bucket
    # Pregnancy, delivery, and recovery are workflow-derived states rather
    # than legal historical-import shortcuts.
    for i, bucket in enumerate(["PREGNANCY_EARLY", "PREGNANCY_LATE", "DELIVERY", "RECOVERY"]):
        resp = await post_animal(
            client,
            owner,
            {
                "tag_number": f"WF-{i}",
                "sex": "F",
                "source": "PURCHASED",
                "current_bucket": bucket,
                "historical_import_reason": "Existing-herd test fixture",
            },
        )
        assert resp.status_code == 422, (bucket, resp.text)
    assert (await client.get("/api/animals", headers=owner)).json()["total"] == len(
        importable_buckets
    )


async def test_create_invalid_birth_type_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    base = {"tag_number": "G-1", "sex": "F", "source": "PURCHASED", "current_bucket": "FOUNDATION"}
    for bad in ["QUINTUPLET", "single", "", 2]:
        resp = await post_animal(client, owner, base | {"birth_type": bad})
        assert resp.status_code == 422, bad


async def test_create_valid_birth_types(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    for i, bt in enumerate(["SINGLE", "TWIN", "TRIPLET"]):
        animal = await make_animal(
            client,
            owner,
            tag=f"BT-{i}",
            source="BORN",
            bucket="FEMALE_KIDS",
            birth_type=bt,
        )
        assert animal["birth_type"] == bt


async def test_create_empty_tag_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await post_animal(
        client,
        owner,
        {"tag_number": "", "sex": "F", "source": "PURCHASED", "current_bucket": "FOUNDATION"},
    )
    assert resp.status_code == 422  # min_length=1


async def test_create_without_tag_auto_generates(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await post_animal(
        client,
        owner,
        {"sex": "F", "source": "PURCHASED", "current_bucket": "FOUNDATION"},
    )
    assert resp.status_code == 201, resp.text
    tag = resp.json()["tag_number"]
    assert re.fullmatch(r"G-[2-9A-HJ-NP-Z]{5}", tag), tag


async def test_create_whitespace_tag_auto_generates(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await post_animal(
        client,
        owner,
        {"tag_number": "   ", "sex": "F", "source": "PURCHASED", "current_bucket": "FOUNDATION"},
    )
    assert resp.status_code == 201, resp.text
    assert re.fullmatch(r"G-[2-9A-HJ-NP-Z]{5}", resp.json()["tag_number"])


async def test_create_tagless_creates_get_distinct_tags(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    base = {"sex": "F", "source": "PURCHASED", "current_bucket": "FOUNDATION"}
    first = await post_animal(client, owner, base)
    second = await post_animal(client, owner, base)
    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["tag_number"] != second.json()["tag_number"]


async def test_create_explicit_tag_still_works(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="MANUAL-1")
    assert animal["tag_number"] == "MANUAL-1"


async def test_create_duplicate_explicit_tag_400(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await make_animal(client, owner, tag="DUP-1")
    resp = await post_animal(
        client,
        owner,
        {"tag_number": "DUP-1", "sex": "F", "source": "PURCHASED", "current_bucket": "FOUNDATION"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Tag 'DUP-1' already exists on this farm."


async def test_create_tag_length_boundaries(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    ok = await make_animal(client, owner, tag="T" * 50)
    assert ok["tag_number"] == "T" * 50
    resp = await post_animal(
        client,
        owner,
        {
            "tag_number": "T" * 51,
            "sex": "F",
            "source": "PURCHASED",
            "current_bucket": "FOUNDATION",
        },
    )
    assert resp.status_code == 422


async def test_create_name_length_boundaries(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, name="N" * 80)
    assert animal["name"] == "N" * 80
    resp = await post_animal(
        client,
        owner,
        {
            "tag_number": "G-2",
            "sex": "F",
            "source": "PURCHASED",
            "current_bucket": "FOUNDATION",
            "name": "N" * 81,
        },
    )
    assert resp.status_code == 422


async def test_create_breed_too_long_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    base = {"tag_number": "G-1", "sex": "F", "source": "PURCHASED", "current_bucket": "FOUNDATION"}
    resp = await post_animal(client, owner, base | {"breed": "B" * 61})
    assert resp.status_code == 422
    animal = await make_animal(client, owner, tag="G-2", breed="B" * 60)
    assert animal["breed"] == "B" * 60


async def test_create_seller_name_too_long_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    base = {"tag_number": "G-1", "sex": "F", "source": "PURCHASED", "current_bucket": "FOUNDATION"}
    resp = await post_animal(client, owner, base | {"seller_name": "S" * 121})
    assert resp.status_code == 422
    animal = await make_animal(client, owner, tag="G-2", seller_name="S" * 120)
    assert animal["seller_name"] == "S" * 120


async def test_create_future_dates_rejected(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    base = {"tag_number": "G-1", "sex": "F", "source": "PURCHASED", "current_bucket": "FOUNDATION"}
    # The schema retains UTC/local parsing headroom; the endpoint applies the
    # selected farm's exact business date.
    future = iso(today() + timedelta(days=1))
    for field in ["date_of_birth", "estimated_dob", "purchase_date"]:
        resp = await post_animal(client, owner, base | {field: future})
        assert resp.status_code == 422, field
    assert (await client.get("/api/animals", headers=owner)).json()["total"] == 0


async def test_create_today_dates_accepted(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(
        client,
        owner,
        date_of_birth=iso(today()),
        estimated_dob=iso(today()),
        purchase_date=iso(today()),
    )
    assert animal["date_of_birth"] == iso(today())


async def test_create_far_past_dates_accepted(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(
        client, owner, date_of_birth="1990-01-01", purchase_date="1990-06-01"
    )
    assert animal["date_of_birth"] == "1990-01-01"


async def test_create_malformed_dates_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    base = {"tag_number": "G-1", "sex": "F", "source": "PURCHASED", "current_bucket": "FOUNDATION"}
    for bad in ["32/13/2020", "2020-13-01", "garbage", "2020-02-30", 20200101]:
        resp = await post_animal(client, owner, base | {"date_of_birth": bad})
        assert resp.status_code == 422, bad


async def test_create_birth_weight_boundaries(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    base = {
        "tag_number": "G-1",
        "sex": "F",
        "source": "BORN",
        "current_bucket": "FEMALE_KIDS",
        "historical_import_reason": "Legacy birth-weight record",
    }
    for bad in [-2, "-2", "nan", "inf", "1e999"]:
        resp = await post_animal(client, owner, base | {"birth_weight": bad})
        assert resp.status_code == 422, bad
    animal = await make_animal(client, owner, tag="G-2", source="BORN", birth_weight=0)
    assert animal["birth_weight"] == 0.0  # zero is non-negative → allowed
    animal = await make_animal(client, owner, tag="G-3", source="BORN", birth_weight=1000)
    assert animal["birth_weight"] == 1000.0  # exactly the 1000 kg weight cap
    resp = await post_animal(client, owner, base | {"tag_number": "G-4", "birth_weight": 1e6})
    assert resp.status_code == 422  # beyond the cap — B2 float-overflow bound


async def test_create_purchase_price_boundaries(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    base = {"tag_number": "G-1", "sex": "F", "source": "PURCHASED", "current_bucket": "FOUNDATION"}
    for bad in [-500, "-500", "nan", "inf", "1e999"]:
        resp = await post_animal(client, owner, base | {"purchase_price": bad})
        assert resp.status_code == 422, bad
    # A priced historical import must date its own acquisition (otherwise the
    # expense would land in today's ledger against an undated animal).
    bought_on = iso(today() - timedelta(days=30))
    animal = await make_animal(client, owner, tag="G-2", purchase_price=0, purchase_date=bought_on)
    assert animal["purchase_price"] == 0.0
    animal = await make_animal(
        client, owner, tag="G-3", purchase_price=999999999.99, purchase_date=bought_on
    )
    assert animal["purchase_price"] == 999999999.99


async def test_create_entry_weight_must_be_positive(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    base = {"tag_number": "G-1", "sex": "F", "source": "PURCHASED", "current_bucket": "FOUNDATION"}
    for bad in [0, -1, "nan", "inf", "abc"]:
        resp = await post_animal(client, owner, base | {"weight_kg": bad})
        assert resp.status_code == 422, bad
    assert (await client.get("/api/animals", headers=owner)).json()["total"] == 0


async def test_create_wrong_types_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    base = {"tag_number": "G-1", "sex": "F", "source": "PURCHASED", "current_bucket": "FOUNDATION"}
    for override in [
        {"tag_number": 123},
        {"tag_number": ["A-1"]},
        {"name": 42},
        {"current_bucket": ["FOUNDATION"]},
        {"birth_type": {"x": 1}},
        {"purchase_price": "abc"},
        {"weight_kg": {"kg": 5}},
    ]:
        resp = await post_animal(client, owner, base | override)
        assert resp.status_code == 422, override


async def test_create_duplicate_tag_rejected_400(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await make_animal(client, owner, tag="A-001")
    resp = await post_animal(
        client,
        owner,
        {
            "tag_number": "A-001",
            "sex": "F",
            "source": "BORN",
            "current_bucket": "FEMALE_KIDS",
            "historical_import_reason": "Duplicate legacy row",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Tag 'A-001' already exists on this farm."
    assert (await client.get("/api/animals", headers=owner)).json()["total"] == 1


async def test_create_duplicate_tag_whitespace_variant_400(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await make_animal(client, owner, tag="A-001")
    resp = await post_animal(
        client,
        owner,
        {
            "tag_number": "  A-001  ",
            "sex": "F",
            "source": "PURCHASED",
            "current_bucket": "FOUNDATION",
        },
    )
    assert resp.status_code == 400  # stripped before the uniqueness check


async def test_create_tag_stays_unique_after_sold(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="A-001")
    resp = await mark_status(client, owner, animal["id"], "SOLD")
    assert resp.status_code == 200, resp.text
    resp = await post_animal(
        client,
        owner,
        {"tag_number": "A-001", "sex": "F", "source": "PURCHASED", "current_bucket": "FOUNDATION"},
    )
    assert resp.status_code == 400  # a sold animal's tag is still reserved


# ---------------------------------------------------------------------------
# 4. List — filters, search, ordering
# ---------------------------------------------------------------------------
async def _seed_herd(client: httpx.AsyncClient, owner: dict) -> dict[str, dict]:
    """A small herd spanning buckets/sexes/statuses for filter tests."""
    herd = {}
    herd["doe_foundation"] = await make_animal(
        client, owner, tag="D-1", sex="F", bucket="FOUNDATION"
    )
    herd["doe_breeding"] = await make_animal(client, owner, tag="D-2", sex="F", bucket="BREEDING")
    herd["buck_breeding"] = await make_animal(client, owner, tag="B-1", sex="M", bucket="BREEDING")
    herd["male_kid"] = await make_animal(client, owner, tag="K-1", sex="M", bucket="MALE_KIDS")
    sold = await make_animal(client, owner, tag="S-1", sex="F", bucket="QUARANTINE")
    resp = await mark_status(client, owner, sold["id"], "SOLD", sale_price=5000)
    assert resp.status_code == 200, resp.text
    herd["sold"] = sold
    return herd


async def test_list_empty_farm(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await client.get("/api/animals", headers=owner)
    assert resp.status_code == 200
    assert resp.json() == {"animals": [], "total": 0}


async def test_list_defaults_to_active_only(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    herd = await _seed_herd(client, owner)
    animals = await list_animals(client, owner)
    tags = {a["tag_number"] for a in animals}
    assert tags == {"D-1", "D-2", "B-1", "K-1"}  # S-1 is SOLD → hidden by default
    assert herd["sold"]["tag_number"] not in tags


async def test_list_include_all_statuses_is_explicit(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await _seed_herd(client, owner)
    animals = await list_animals(client, owner, include_all_statuses="true")
    assert {a["tag_number"] for a in animals} == {"D-1", "D-2", "B-1", "K-1", "S-1"}


async def test_list_total_matches_length(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await _seed_herd(client, owner)
    resp = await client.get("/api/animals", headers=owner)
    body = resp.json()
    assert body["total"] == len(body["animals"]) == 4


async def test_list_filter_by_bucket(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await _seed_herd(client, owner)
    animals = await list_animals(client, owner, bucket="BREEDING")
    assert {a["tag_number"] for a in animals} == {"D-2", "B-1"}
    animals = await list_animals(client, owner, bucket="QUARANTINE")
    assert animals == []  # the only quarantined animal is SOLD (default filter)


async def test_list_filter_by_sex(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await _seed_herd(client, owner)
    males = await list_animals(client, owner, sex="M")
    assert {a["tag_number"] for a in males} == {"B-1", "K-1"}
    females = await list_animals(client, owner, sex="F")
    assert {a["tag_number"] for a in females} == {"D-1", "D-2"}


async def test_list_filter_by_each_status(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    for i, status in enumerate(["SOLD", "DEAD", "CULLED"]):
        animal = await make_animal(client, owner, tag=f"X-{i}")
        resp = await mark_status(client, owner, animal["id"], status)
        assert resp.status_code == 200, resp.text
        animals = await list_animals(client, owner, status=status)
        assert {a["tag_number"] for a in animals} == {f"X-{i}"}
        assert all(a["status"] == status for a in animals)
        assert all(a["status_date"] == iso(today()) for a in animals)


async def test_list_filter_status_active_explicit(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await _seed_herd(client, owner)
    animals = await list_animals(client, owner, status="ACTIVE")
    assert {a["tag_number"] for a in animals} == {"D-1", "D-2", "B-1", "K-1"}


async def test_list_combined_bucket_and_sex(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await _seed_herd(client, owner)
    animals = await list_animals(client, owner, bucket="BREEDING", sex="M")
    assert [a["tag_number"] for a in animals] == ["B-1"]


async def test_list_combined_bucket_and_status(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await _seed_herd(client, owner)
    animals = await list_animals(client, owner, bucket="QUARANTINE", status="SOLD")
    assert [a["tag_number"] for a in animals] == ["S-1"]


async def test_list_combined_all_filters(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await _seed_herd(client, owner)
    animals = await list_animals(
        client, owner, bucket="QUARANTINE", sex="F", status="SOLD", q="S-1"
    )
    assert [a["tag_number"] for a in animals] == ["S-1"]
    animals = await list_animals(client, owner, bucket="QUARANTINE", sex="M", status="SOLD")
    assert animals == []


async def test_list_combined_filters_no_match(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await _seed_herd(client, owner)
    resp = await client.get(
        "/api/animals",
        headers=owner,
        params={"bucket": "DELIVERY", "sex": "M", "status": "DEAD"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"animals": [], "total": 0}


async def test_list_search_q_substring(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await _seed_herd(client, owner)
    animals = await list_animals(client, owner, q="D-")
    assert {a["tag_number"] for a in animals} == {"D-1", "D-2"}
    animals = await list_animals(client, owner, q="-1")
    assert {a["tag_number"] for a in animals} == {"D-1", "B-1", "K-1"}  # S-1 hidden


async def test_list_search_q_case_insensitive(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await make_animal(client, owner, tag="GoAt-7")
    for q in ["goat-7", "GOAT-7", "gOAT"]:
        animals = await list_animals(client, owner, q=q)
        assert [a["tag_number"] for a in animals] == ["GoAt-7"], q


async def test_list_search_q_whitespace_stripped(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await make_animal(client, owner, tag="A-123")
    animals = await list_animals(client, owner, q="  A-123  ")
    assert [a["tag_number"] for a in animals] == ["A-123"]


async def test_list_search_q_empty_returns_all(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await make_animal(client, owner, tag="A-1")
    await make_animal(client, owner, tag="B-2")
    for q in ["", "   "]:
        animals = await list_animals(client, owner, q=q)
        assert len(animals) == 2


async def test_list_search_q_no_match(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await make_animal(client, owner, tag="A-1")
    animals = await list_animals(client, owner, q="ZZZ-no-such-tag")
    assert animals == []


async def test_list_search_matches_tag_or_name(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await make_animal(client, owner, tag="T-1", name="Alpha")
    assert [a["tag_number"] for a in await list_animals(client, owner, q="Alpha")] == ["T-1"]
    assert [a["tag_number"] for a in await list_animals(client, owner, q="T-1")] == ["T-1"]


async def test_list_invalid_filter_values_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    for params in [
        {"bucket": "MOON"},
        {"bucket": "quarantine"},
        {"bucket": ""},
        {"sex": "X"},
        {"sex": ""},
        {"status": "RETIRED"},
        {"status": "active"},
        {"status": ""},
    ]:
        resp = await client.get("/api/animals", headers=owner, params=params)
        assert resp.status_code == 422, params


async def test_list_ordering_by_bucket_then_tag(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await make_animal(client, owner, tag="Z-9", bucket="QUARANTINE")
    await make_animal(client, owner, tag="B-2", bucket="BREEDING")
    await make_animal(client, owner, tag="A-0", sex="M", bucket="MALE_KIDS")
    await make_animal(client, owner, tag="A-1", bucket="BREEDING")
    animals = await list_animals(client, owner)
    assert [a["tag_number"] for a in animals] == ["A-1", "B-2", "A-0", "Z-9"]


async def test_list_includes_computed_fields(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await make_animal(
        client,
        owner,
        tag="C-1",
        date_of_birth=iso(today() - timedelta(days=731)),
        weight_kg=25.0,
    )
    animal = (await list_animals(client, owner))[0]
    for field in [
        "age_months",
        "latest_weight_kg",
        "is_breeding_ready",
        "is_currently_pregnant",
        "days_in_current_bucket",
        "cull_candidate",
        "created_at",
    ]:
        assert field in animal, field
    assert animal["age_months"] == 24
    assert animal["latest_weight_kg"] == 25.0


async def test_list_limit_offset_pagination(client: httpx.AsyncClient) -> None:
    """limit/offset page the (bucket, tag)-ordered list; `total` remains the
    full filtered count and omission uses the finite 100-row default."""
    owner = await owner_with_farm(client)
    for i in range(5):
        await make_animal(client, owner, tag=f"P-{i}")
    resp = await client.get("/api/animals", headers=owner)
    full = resp.json()
    assert full["total"] == len(full["animals"]) == 5

    resp = await client.get("/api/animals", headers=owner, params={"limit": "2", "offset": "1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 5
    assert [a["tag_number"] for a in body["animals"]] == ["P-1", "P-2"]

    resp = await client.get("/api/animals", headers=owner, params={"limit": "200"})
    assert resp.status_code == 200 and len(resp.json()["animals"]) == 5
    resp = await client.get("/api/animals", headers=owner, params={"offset": "4"})
    assert [a["tag_number"] for a in resp.json()["animals"]] == ["P-4"]
    resp = await client.get("/api/animals", headers=owner, params={"offset": "5"})
    assert resp.json()["animals"] == [] and resp.json()["total"] == 5


async def test_list_pagination_params_validated(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    for params in ({"limit": "0"}, {"limit": "201"}, {"limit": "-1"}, {"offset": "-1"}):
        resp = await client.get("/api/animals", headers=owner, params=params)
        assert resp.status_code == 422, params


async def test_list_omitted_limit_is_finite_and_reports_full_total(
    client: httpx.AsyncClient,
) -> None:
    from app.db import get_sessionmaker
    from app.models import Animal

    owner = await owner_with_farm(client)
    async with get_sessionmaker()() as db:
        db.add_all(
            Animal(
                farm_id=int(owner["X-Farm-Id"]),
                tag_number=f"FINITE-{index:03d}",
                sex="F",
                source="BORN",
                current_bucket="FOUNDATION",
                status="ACTIVE",
            )
            for index in range(105)
        )
        await db.commit()

    first = await client.get("/api/animals", headers=owner)
    assert first.status_code == 200, first.text
    assert first.json()["total"] == 105
    assert len(first.json()["animals"]) == 100
    tail = await client.get("/api/animals", headers=owner, params={"offset": 100})
    assert tail.status_code == 200, tail.text
    assert tail.json()["total"] == 105
    assert len(tail.json()["animals"]) == 5


async def test_breeding_ready_flag_in_list(client: httpx.AsyncClient) -> None:
    """SPEC: breeding-ready doe = female, >=10 mo, >=22 kg, not pregnant, in
    FOUNDATION/FEMALE_KIDS/RESTING."""
    owner = await owner_with_farm(client)
    ready = await make_animal(
        client,
        owner,
        tag="R-1",
        date_of_birth=iso(today() - timedelta(days=400)),
        weight_kg=25.0,
    )
    assert ready["is_breeding_ready"] is True
    # too light
    light = await make_animal(
        client,
        owner,
        tag="R-2",
        date_of_birth=iso(today() - timedelta(days=400)),
        weight_kg=20.0,
    )
    assert light["is_breeding_ready"] is False
    # right age/weight but wrong bucket (BREEDING is not a "ready" bucket)
    wrong_bucket = await make_animal(
        client,
        owner,
        tag="R-3",
        bucket="BREEDING",
        date_of_birth=iso(today() - timedelta(days=400)),
        weight_kg=25.0,
    )
    assert wrong_bucket["is_breeding_ready"] is False
    # male never is
    male = await make_animal(client, owner, tag="R-4", sex="M")
    assert male["is_breeding_ready"] is False


# ---------------------------------------------------------------------------
# 5. Profile
# ---------------------------------------------------------------------------
async def test_profile_happy_structure(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, weight_kg=20.0)
    profile = await get_profile(client, owner, animal["id"])
    assert set(profile) == {
        "animal",
        "kids",
        "kids_total",
        "kids_offset",
        "weights",
        "weights_total",
        "weights_offset",
        "moves",
        "moves_total",
        "moves_offset",
        "health_events",
        "health_events_total",
        "health_events_offset",
        "breedings",
        "breedings_total",
        "breedings_offset",
        "history_limit",
    }
    assert profile["animal"]["id"] == animal["id"]
    assert profile["kids"] == []
    assert profile["kids_total"] == 0
    assert profile["health_events"] == []
    assert profile["health_events_total"] == 0
    assert profile["breedings"] == []
    assert profile["breedings_total"] == 0
    assert len(profile["weights"]) == 1
    assert profile["weights_total"] == 1
    assert len(profile["moves"]) == 1
    assert profile["moves_total"] == 1
    assert profile["history_limit"] == 25


async def test_profile_not_found_404(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await client.get("/api/animals/999999", headers=owner)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Animal not found"


async def test_profile_malformed_id_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    for bad in ["abc", "1.5", "A-001"]:
        resp = await client.get(f"/api/animals/{bad}", headers=owner)
        assert resp.status_code == 422, bad


async def test_profile_zero_negative_and_huge_ids_404(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    # ids >= 2**31 overflow the int32 PK with an unhandled 500 — see
    # tests/test_animals_bugs.py. 2**31 - 1 is the largest valid int32 id.
    for bad in [0, -5, 2**31 - 1]:
        resp = await client.get(f"/api/animals/{bad}", headers=owner)
        assert resp.status_code == 404, bad


async def test_profile_weights_newest_first(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner)  # no entry weight
    for d, kg in [("2020-01-01", 10.0), ("2021-06-01", 15.0), (iso(today()), 20.0)]:
        resp = await client.post(
            f"/api/animals/{animal['id']}/weight",
            json={"date": d, "weight_kg": kg},
            headers=owner,
        )
        assert resp.status_code == 201, resp.text
    weights = (await get_profile(client, owner, animal["id"]))["weights"]
    assert [w["weight_kg"] for w in weights] == [20.0, 15.0, 10.0]


async def test_profile_moves_newest_first(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    dob = today() - timedelta(days=500)
    animal = await make_animal(
        client,
        owner,
        bucket="FOUNDATION",
        date_of_birth=iso(dob),
        weight_kg=26,
        weight_date=iso(dob),
    )
    for target in ["BREEDING", "RESTING"]:
        resp = await client.post(
            f"/api/animals/{animal['id']}/move", json={"to_bucket": target}, headers=owner
        )
        assert resp.status_code == 200, resp.text
    moves = (await get_profile(client, owner, animal["id"]))["moves"]
    assert [m["to_bucket"] for m in moves] == ["RESTING", "BREEDING", "FOUNDATION"]
    assert moves[-1]["from_bucket"] is None  # initial entry last


async def test_profile_kids_and_breedings(client: httpx.AsyncClient) -> None:
    """Dam profile lists her kids (SPEC: profile shows full history + kids) and
    her breeding record ids, newest first."""
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner)
    buck = await make_buck(client, owner)
    br = await make_breeding(client, owner, doe["id"], buck["id"], today() - timedelta(days=160))
    # Record the scan on its planned check date: a delivery may not predate the
    # confirmation that the doe was carrying.
    br = await submit_ultrasound(
        client,
        owner,
        br["id"],
        pregnant=True,
        kid_count=2,
        result_date=date.fromisoformat(br["ultrasound_date"]),
    )
    kidding_date = br["expected_kidding_date"]
    resp = await client.post(
        "/api/kidding",
        json={
            "breeding_record_id": br["id"],
            "date": kidding_date,
            "ease": "NORMAL",
            "kids": [
                {"tag": "K-202", "sex": "F", "birth_weight": 2.5, "status": "ALIVE"},
                {"tag": "K-201", "sex": "M", "birth_weight": 2.8, "status": "ALIVE"},
            ],
        },
        headers=owner,
    )
    assert resp.status_code == 201, resp.text

    profile = await get_profile(client, owner, doe["id"])
    assert profile["breedings"] == [br["id"]]
    kids = profile["kids"]
    assert [k["tag_number"] for k in kids] == ["K-201", "K-202"]  # sorted by tag
    assert all(
        set(k)
        == {
            "id",
            "tag_number",
            "name",
            "sex",
            "date_of_birth",
            "estimated_dob",
            "status",
        }
        for k in kids
    )
    assert profile["kids_total"] == 2
    # Lineage and operational fields remain available on each kid's own
    # profile; the parent page intentionally returns only bounded identity.
    kid_profiles = [await get_profile(client, owner, kid["id"]) for kid in kids]
    assert all(
        kid_profile["animal"]["dam_id"] == doe["id"]
        and kid_profile["animal"]["sire_id"] == buck["id"]
        and kid_profile["animal"]["source"] == "BORN"
        and kid_profile["animal"]["current_bucket"] == "RECOVERY"
        for kid_profile in kid_profiles
    )
    assert all(kid_profile["breedings"] == [] for kid_profile in kid_profiles)
    assert all(kid_profile["kids"] == [] for kid_profile in kid_profiles)


async def test_profile_health_events_listed(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner)
    resp = await client.post(
        "/api/health/events",
        json={
            "scope": "animal",
            "animal_id": animal["id"],
            "date": iso(today()),
            "type": "TREATMENT",
            "product_name": "Oxytetracycline",
        },
        headers=owner,
    )
    assert resp.status_code == 201, resp.text
    events = (await get_profile(client, owner, animal["id"]))["health_events"]
    assert len(events) == 1
    assert events[0]["type"] == "TREATMENT"


async def test_profile_of_sold_animal_still_accessible(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner)
    resp = await mark_status(client, owner, animal["id"], "DEAD")
    assert resp.status_code == 200, resp.text
    profile = await get_profile(client, owner, animal["id"])
    assert profile["animal"]["status"] == "DEAD"
    assert profile["animal"]["status_date"] == iso(today())


async def test_profile_reflects_move_and_weight(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    dob = today() - timedelta(days=500)
    animal = await make_animal(
        client, owner, date_of_birth=iso(dob), weight_kg=26, weight_date=iso(dob)
    )
    resp = await client.post(
        f"/api/animals/{animal['id']}/move", json={"to_bucket": "BREEDING"}, headers=owner
    )
    assert resp.status_code == 200, resp.text
    resp = await client.post(
        f"/api/animals/{animal['id']}/weight", json={"weight_kg": 30.0}, headers=owner
    )
    assert resp.status_code == 201, resp.text
    refreshed = await get_animal(client, owner, animal["id"])
    assert refreshed["current_bucket"] == "BREEDING"
    assert refreshed["latest_weight_kg"] == 30.0


# ---------------------------------------------------------------------------
# 6. Bucket moves
# ---------------------------------------------------------------------------
async def test_move_happy_updates_bucket(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, bucket="QUARANTINE")
    resp = await client.post(
        f"/api/animals/{animal['id']}/move",
        json={
            "to_bucket": "FOUNDATION",
            "reason": "Imported completed quarantine history",
            "history_override": True,
        },
        headers=owner,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["current_bucket"] == "FOUNDATION"


async def test_move_records_history_with_reason(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    dob = today() - timedelta(days=500)
    animal = await make_animal(
        client,
        owner,
        bucket="FOUNDATION",
        date_of_birth=iso(dob),
        weight_kg=26,
        weight_date=iso(dob),
    )
    resp = await client.post(
        f"/api/animals/{animal['id']}/move",
        json={"to_bucket": "BREEDING", "reason": "breeding-ready"},
        headers=owner,
    )
    assert resp.status_code == 200, resp.text
    moves = (await get_profile(client, owner, animal["id"]))["moves"]
    assert len(moves) == 2
    assert moves[0]["from_bucket"] == "FOUNDATION"
    assert moves[0]["to_bucket"] == "BREEDING"
    assert moves[0]["reason"] == "breeding-ready"


async def test_mature_heavy_male_kid_can_graduate_to_breeding_only_at_buck_minimums(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    old_dob = today() - timedelta(days=800)
    young_dob = today() - timedelta(days=200)
    young = await make_animal(
        client,
        owner,
        tag="BUCK-YOUNG",
        sex="M",
        bucket="MALE_KIDS",
        date_of_birth=iso(young_dob),
        weight_kg=30,
        weight_date=iso(young_dob),
    )
    light = await make_animal(
        client,
        owner,
        tag="BUCK-LIGHT",
        sex="M",
        bucket="MALE_KIDS",
        date_of_birth=iso(old_dob),
        weight_kg=24.99,
        weight_date=iso(old_dob),
    )
    ready = await make_animal(
        client,
        owner,
        tag="BUCK-READY",
        sex="M",
        bucket="MALE_KIDS",
        date_of_birth=iso(old_dob),
        weight_kg=25,
        weight_date=iso(old_dob),
    )

    for animal in (young, light):
        response = await client.post(
            f"/api/animals/{animal['id']}/move",
            json={"to_bucket": "BREEDING"},
            headers=owner,
        )
        assert response.status_code == 409, response.text
    response = await client.post(
        f"/api/animals/{ready['id']}/move",
        json={"to_bucket": "BREEDING"},
        headers=owner,
    )
    assert response.status_code == 200, response.text
    assert response.json()["current_bucket"] == "BREEDING"


async def test_move_same_bucket_is_noop(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, bucket="FOUNDATION")
    resp = await client.post(
        f"/api/animals/{animal['id']}/move", json={"to_bucket": "FOUNDATION"}, headers=owner
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["current_bucket"] == "FOUNDATION"
    moves = (await get_profile(client, owner, animal["id"]))["moves"]
    assert len(moves) == 1  # only the initial entry — no duplicate move row


async def test_move_blank_reason_stored_as_none(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    dob = today() - timedelta(days=500)
    animal = await make_animal(
        client, owner, date_of_birth=iso(dob), weight_kg=26, weight_date=iso(dob)
    )
    for reason in [None, "", "   "]:
        payload = {"to_bucket": "BREEDING", "reason": reason}
        resp = await client.post(f"/api/animals/{animal['id']}/move", json=payload, headers=owner)
        assert resp.status_code == 200, resp.text
        moves = (await get_profile(client, owner, animal["id"]))["moves"]
        assert moves[0]["reason"] is None
        # move back so the next iteration has somewhere to move from
        resp = await client.post(
            f"/api/animals/{animal['id']}/move",
            json={
                "to_bucket": "FOUNDATION",
                "history_override": True,
                "reason": "Reset test history",
            },
            headers=owner,
        )
        assert resp.status_code == 200, resp.text


async def test_move_reason_whitespace_trimmed(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    dob = today() - timedelta(days=500)
    animal = await make_animal(
        client, owner, date_of_birth=iso(dob), weight_kg=26, weight_date=iso(dob)
    )
    resp = await client.post(
        f"/api/animals/{animal['id']}/move",
        json={"to_bucket": "BREEDING", "reason": "  vet advice  "},
        headers=owner,
    )
    assert resp.status_code == 200, resp.text
    moves = (await get_profile(client, owner, animal["id"]))["moves"]
    assert moves[0]["reason"] == "vet advice"


async def test_move_invalid_bucket_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner)
    for bad in ["MOON", "foundation", "", None, 3]:
        resp = await client.post(
            f"/api/animals/{animal['id']}/move", json={"to_bucket": bad}, headers=owner
        )
        assert resp.status_code == 422, bad
    assert (await get_animal(client, owner, animal["id"]))["current_bucket"] == "FOUNDATION"


async def test_move_missing_bucket_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner)
    resp = await client.post(f"/api/animals/{animal['id']}/move", json={}, headers=owner)
    assert resp.status_code == 422


async def test_move_nonexistent_animal_404(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await client.post(
        "/api/animals/424242/move", json={"to_bucket": "BREEDING"}, headers=owner
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Animal not found"


async def test_move_malformed_id_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await client.post("/api/animals/abc/move", json={"to_bucket": "BREEDING"}, headers=owner)
    assert resp.status_code == 422


async def test_move_rejected_for_every_non_active_status(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    for i, status in enumerate(["SOLD", "DEAD", "CULLED"]):
        animal = await make_animal(client, owner, tag=f"N-{i}", bucket="FOUNDATION")
        resp = await mark_status(client, owner, animal["id"], status)
        assert resp.status_code == 200, resp.text
        resp = await client.post(
            f"/api/animals/{animal['id']}/move", json={"to_bucket": "BREEDING"}, headers=owner
        )
        assert resp.status_code == 409, status
        assert "cannot move buckets" in resp.json()["detail"]
        after = await get_animal(client, owner, animal["id"])
        assert after["current_bucket"] == "FOUNDATION"  # unmoved
        assert len((await get_profile(client, owner, animal["id"]))["moves"]) == 1


async def test_move_chain_full_history(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    dob = today() - timedelta(days=500)
    animal = await make_animal(
        client,
        owner,
        bucket="QUARANTINE",
        date_of_birth=iso(dob),
        weight_kg=26,
        weight_date=iso(dob),
    )
    for _target, body in [
        (
            "FOUNDATION",
            {
                "to_bucket": "FOUNDATION",
                "history_override": True,
                "reason": "Imported quarantine completion",
            },
        ),
        ("BREEDING", {"to_bucket": "BREEDING"}),
    ]:
        resp = await client.post(f"/api/animals/{animal['id']}/move", json=body, headers=owner)
        assert resp.status_code == 200, resp.text
    moves = (await get_profile(client, owner, animal["id"]))["moves"]
    assert [(m["from_bucket"], m["to_bucket"]) for m in moves] == [
        ("FOUNDATION", "BREEDING"),
        ("QUARANTINE", "FOUNDATION"),
        (None, "QUARANTINE"),
    ]


async def test_move_resets_days_in_bucket(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    dob = today() - timedelta(days=500)
    animal = await make_animal(
        client, owner, date_of_birth=iso(dob), weight_kg=26, weight_date=iso(dob)
    )
    resp = await client.post(
        f"/api/animals/{animal['id']}/move", json={"to_bucket": "BREEDING"}, headers=owner
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["days_in_current_bucket"] == 0


async def test_owner_history_override_can_reconstruct_every_compatible_bucket(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, bucket="QUARANTINE")
    for target in ALL_BUCKETS:
        if target == "QUARANTINE":
            continue
        resp = await client.post(
            f"/api/animals/{animal['id']}/move",
            json={
                "to_bucket": target,
                "history_override": True,
                "reason": "Imported lifecycle history",
            },
            headers=owner,
        )
        if target == "MALE_KIDS":
            assert resp.status_code == 409, (target, resp.text)
            continue
        assert resp.status_code == 200, (target, resp.text)
        assert resp.json()["current_bucket"] == target


async def test_illegal_lifecycle_move_requires_owner_history_override(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="STATE-1", bucket="FOUNDATION")
    rejected = await client.post(
        f"/api/animals/{animal['id']}/move",
        json={"to_bucket": "DELIVERY", "reason": "skip lifecycle"},
        headers=owner,
    )
    assert rejected.status_code == 409, rejected.text
    overridden = await client.post(
        f"/api/animals/{animal['id']}/move",
        json={
            "to_bucket": "DELIVERY",
            "reason": "Correcting a historical paper record",
            "history_override": True,
        },
        headers=owner,
    )
    assert overridden.status_code == 200, overridden.text
    moves = (await get_profile(client, owner, animal["id"]))["moves"]
    assert moves[0]["reason"].startswith("[HISTORY OVERRIDE]")


async def test_farm_timezone_and_animal_chronology_guard_weight_dates(
    client: httpx.AsyncClient,
) -> None:
    auth = await register(client, email="phoenix@farm.in")
    farm_response = await client.post(
        "/api/auth/farms",
        json={"name": "Arizona Farm", "timezone": "America/Phoenix"},
        headers=auth,
    )
    assert farm_response.status_code == 201, farm_response.text
    owner = auth | {"X-Farm-Id": str(farm_response.json()["id"])}
    phoenix_today = today("America/Phoenix")
    dob = phoenix_today - timedelta(days=100)
    animal = await make_animal(
        client,
        owner,
        tag="PHX-1",
        source="BORN",
        date_of_birth=iso(dob),
    )
    future = await client.post(
        f"/api/animals/{animal['id']}/weight",
        json={"date": iso(phoenix_today + timedelta(days=1)), "weight_kg": 12},
        headers=owner,
    )
    assert future.status_code == 422, future.text
    before_birth = await client.post(
        f"/api/animals/{animal['id']}/weight",
        json={"date": iso(dob - timedelta(days=1)), "weight_kg": 12},
        headers=owner,
    )
    assert before_birth.status_code == 422, before_birth.text


# ---------------------------------------------------------------------------
# 7. Weight records
# ---------------------------------------------------------------------------
async def test_weight_happy_creates_record(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner)
    resp = await client.post(
        f"/api/animals/{animal['id']}/weight",
        json={"weight_kg": 23.5, "bcs": 3, "notes": "  monthly check  "},
        headers=owner,
    )
    assert resp.status_code == 201, resp.text
    record = resp.json()
    assert record["weight_kg"] == 23.5
    assert record["bcs"] == 3
    assert record["notes"] == "monthly check"  # stripped
    assert record["date"] == iso(today())
    assert record["id"] is not None


async def test_weight_defaults_date_to_today(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner)
    resp = await client.post(
        f"/api/animals/{animal['id']}/weight", json={"weight_kg": 10}, headers=owner
    )
    assert resp.status_code == 201
    assert resp.json()["date"] == iso(today())


async def test_weight_explicit_past_date_accepted(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner)
    resp = await client.post(
        f"/api/animals/{animal['id']}/weight",
        json={"date": "2020-02-29", "weight_kg": 10},
        headers=owner,
    )
    assert resp.status_code == 201
    assert resp.json()["date"] == "2020-02-29"


async def test_weight_future_date_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner)
    future = iso(today() + timedelta(days=1))
    resp = await client.post(
        f"/api/animals/{animal['id']}/weight",
        json={"date": future, "weight_kg": 10},
        headers=owner,
    )
    assert resp.status_code == 422
    assert (await get_profile(client, owner, animal["id"]))["weights"] == []


async def test_weight_schema_headroom_cannot_bypass_farm_business_date(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner)
    tomorrow = iso(today() + timedelta(days=1))
    resp = await client.post(
        f"/api/animals/{animal['id']}/weight",
        json={"date": tomorrow, "weight_kg": 10},
        headers=owner,
    )
    assert resp.status_code == 422, resp.text
    assert (await get_profile(client, owner, animal["id"]))["weights"] == []


async def test_weight_zero_and_negative_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner)
    for bad in [0, -1, -0.001, "-5"]:
        resp = await client.post(
            f"/api/animals/{animal['id']}/weight", json={"weight_kg": bad}, headers=owner
        )
        assert resp.status_code == 422, bad


async def test_weight_nonfinite_and_garbage_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner)
    for bad in ["nan", "inf", "-inf", "1e999", "abc", None]:
        resp = await client.post(
            f"/api/animals/{animal['id']}/weight", json={"weight_kg": bad}, headers=owner
        )
        assert resp.status_code == 422, bad
    assert (await get_profile(client, owner, animal["id"]))["weights"] == []


async def test_weight_missing_weight_kg_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner)
    resp = await client.post(f"/api/animals/{animal['id']}/weight", json={}, headers=owner)
    assert resp.status_code == 422


async def test_weight_bcs_boundaries(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner)
    for bcs in [1, 5]:
        resp = await client.post(
            f"/api/animals/{animal['id']}/weight",
            json={"weight_kg": 20, "bcs": bcs},
            headers=owner,
        )
        assert resp.status_code == 201, bcs
        assert resp.json()["bcs"] == bcs


async def test_weight_bcs_out_of_range_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner)
    for bad in [0, 6, -1, 99, 3.5, "abc"]:
        resp = await client.post(
            f"/api/animals/{animal['id']}/weight",
            json={"weight_kg": 20, "bcs": bad},
            headers=owner,
        )
        assert resp.status_code == 422, bad
    assert (await get_profile(client, owner, animal["id"]))["weights"] == []


async def test_weight_blank_notes_become_none(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner)
    resp = await client.post(
        f"/api/animals/{animal['id']}/weight",
        json={"weight_kg": 20, "notes": "   "},
        headers=owner,
    )
    assert resp.status_code == 201
    assert resp.json()["notes"] is None


async def test_weight_rejected_for_non_active_animals(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    for i, status in enumerate(["SOLD", "DEAD", "CULLED"]):
        animal = await make_animal(client, owner, tag=f"W-{i}")
        resp = await mark_status(client, owner, animal["id"], status)
        assert resp.status_code == 200, resp.text
        resp = await client.post(
            f"/api/animals/{animal['id']}/weight", json={"weight_kg": 30}, headers=owner
        )
        assert resp.status_code == 400, status
        assert "cannot record a weight" in resp.json()["detail"]
        assert (await get_profile(client, owner, animal["id"]))["weights"] == []


async def test_weight_nonexistent_animal_404(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await client.post("/api/animals/424242/weight", json={"weight_kg": 10}, headers=owner)
    assert resp.status_code == 404


async def test_weight_malformed_id_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await client.post("/api/animals/abc/weight", json={"weight_kg": 10}, headers=owner)
    assert resp.status_code == 422


async def test_weight_latest_tracks_newest_date(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner)
    for d, kg in [(iso(today()), 25.0), ("2020-06-01", 40.0), ("2021-01-01", 30.0)]:
        resp = await client.post(
            f"/api/animals/{animal['id']}/weight",
            json={"date": d, "weight_kg": kg},
            headers=owner,
        )
        assert resp.status_code == 201, resp.text
    # the 40 kg record is older — today's 25 kg stays "latest"
    assert (await get_animal(client, owner, animal["id"]))["latest_weight_kg"] == 25.0


async def test_weight_multiple_records_accumulate(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, weight_kg=5.0)  # entry weight
    for kg in [10.0, 15.0]:
        resp = await client.post(
            f"/api/animals/{animal['id']}/weight", json={"weight_kg": kg}, headers=owner
        )
        assert resp.status_code == 201, resp.text
    weights = (await get_profile(client, owner, animal["id"]))["weights"]
    assert len(weights) == 3
    assert weights[0]["weight_kg"] == 15.0  # newest first; entry weight last
    assert weights[-1]["notes"] == "Entry weight"


# ---------------------------------------------------------------------------
# 8. Status changes (SOLD / DEAD / CULLED)
# ---------------------------------------------------------------------------
async def test_status_sold_books_income_transaction(client: httpx.AsyncClient) -> None:
    """SPEC Financial: animal sale → auto-creates an INCOME transaction."""
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="MEAT-1")
    resp = await mark_status(
        client,
        owner,
        animal["id"],
        "SOLD",
        date=iso(today()),
        sale_price=24500,
        buyer_name="Hyderabad Traders",
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "SOLD"
    assert body["sale_price"] == 24500.0
    assert body["status_date"] == iso(today())
    txns = await transactions(client, owner)
    assert len(txns) == 1
    txn = txns[0]
    assert txn["type"] == "INCOME"
    assert txn["category"] == "ANIMAL_SALE"
    assert txn["amount"] == 24500.0
    assert txn["related_animal_id"] == animal["id"]
    assert "MEAT-1" in txn["notes"]
    assert "Hyderabad Traders" in txn["notes"]


async def test_status_sold_without_price_books_no_transaction(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner)
    resp = await mark_status(client, owner, animal["id"], "SOLD")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "SOLD"
    assert resp.json()["sale_price"] is None
    assert await transactions(client, owner) == []


async def test_status_sold_zero_price_books_a_zero_income_transaction(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner)
    resp = await mark_status(client, owner, animal["id"], "SOLD", sale_price=0)
    assert resp.status_code == 200, resp.text
    assert resp.json()["sale_price"] == 0.0
    txns = await transactions(client, owner)
    assert len(txns) == 1
    assert txns[0]["category"] == "ANIMAL_SALE"
    assert txns[0]["amount"] == 0.0


async def test_status_dead_and_culled_book_no_transaction(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    for i, status in enumerate(["DEAD", "CULLED"]):
        animal = await make_animal(client, owner, tag=f"T-{i}")
        resp = await mark_status(client, owner, animal["id"], status)
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == status
        assert resp.json()["sale_price"] is None
    assert await transactions(client, owner) == []


async def test_status_dead_rejects_smuggled_sale_price_without_mutation(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner)
    resp = await mark_status(client, owner, animal["id"], "DEAD", sale_price=9999)
    assert resp.status_code == 422, resp.text
    unchanged = await get_animal(client, owner, animal["id"])
    assert unchanged["status"] == "ACTIVE"
    assert unchanged["sale_price"] is None
    assert await transactions(client, owner) == []


async def test_status_dead_rejects_whitespace_suspected_disease_without_mutation(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner)
    response = await client.post(
        f"/api/animals/{animal['id']}/status",
        json={
            "new_status": "DEAD",
            "suspected_scheduled_disease": True,
            "suspected_disease": "   ",
        },
        headers=owner,
    )
    assert response.status_code == 422, response.text
    unchanged = await get_animal(client, owner, animal["id"])
    assert unchanged["status"] == "ACTIVE"
    assert unchanged["suspected_scheduled_disease"] is False


@pytest.mark.parametrize(
    "escalation_fields",
    [
        {"suspected_disease": "Anthrax"},
        {"authority_notified_at": iso(today())},
        {
            "suspected_disease": "Anthrax",
            "authority_notified_at": iso(today()),
            "suspected_scheduled_disease": False,
        },
    ],
)
async def test_status_dead_rejects_disease_details_without_suspicion_flag(
    client: httpx.AsyncClient,
    escalation_fields: dict[str, object],
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner)
    response = await mark_status(
        client,
        owner,
        animal["id"],
        "DEAD",
        **escalation_fields,
    )
    assert response.status_code == 422, response.text
    unchanged = await get_animal(client, owner, animal["id"])
    assert unchanged["status"] == "ACTIVE"
    assert unchanged["suspected_disease"] is None
    assert unchanged["authority_notified_at"] is None


async def test_status_defaults_date_to_today(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner)
    resp = await mark_status(client, owner, animal["id"], "CULLED")
    assert resp.status_code == 200
    assert resp.json()["status_date"] == iso(today())


async def test_status_explicit_past_date_stored(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner)
    resp = await mark_status(client, owner, animal["id"], "SOLD", date="2026-01-15", sale_price=100)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status_date"] == "2026-01-15"
    txn = (await transactions(client, owner))[0]
    assert txn["date"] == "2026-01-15"  # income booked on the sale date


async def test_status_replay_double_sell_rejected(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner)
    payload = {"date": iso(today()), "sale_price": 5000}
    resp = await mark_status(client, owner, animal["id"], "SOLD", **payload)
    assert resp.status_code == 200, resp.text
    resp = await mark_status(client, owner, animal["id"], "SOLD", **payload)
    assert resp.status_code == 400
    assert "already sold" in resp.json()["detail"]
    assert len(await transactions(client, owner)) == 1  # exactly one income


async def test_status_transitions_between_terminal_states_rejected(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner)
    resp = await mark_status(client, owner, animal["id"], "DEAD")
    assert resp.status_code == 200, resp.text
    for followup in ["SOLD", "CULLED", "DEAD"]:
        extra = {"sale_price": 100} if followup == "SOLD" else {}
        resp = await mark_status(client, owner, animal["id"], followup, **extra)
        assert resp.status_code == 400, followup
    assert (await get_animal(client, owner, animal["id"]))["status"] == "DEAD"
    assert await transactions(client, owner) == []


async def test_status_active_target_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner)
    resp = await mark_status(client, owner, animal["id"], "ACTIVE")
    assert resp.status_code == 422  # schema only allows SOLD/DEAD/CULLED


async def test_status_invalid_values_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner)
    for bad in ["RETIRED", "sold", "", None, 5]:
        resp = await mark_status(client, owner, animal["id"], bad)
        assert resp.status_code == 422, bad
    assert (await get_animal(client, owner, animal["id"]))["status"] == "ACTIVE"


async def test_status_future_date_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner)
    resp = await mark_status(
        client, owner, animal["id"], "SOLD", date=iso(today() + timedelta(days=1))
    )
    assert resp.status_code == 422
    assert (await get_animal(client, owner, animal["id"]))["status"] == "ACTIVE"


async def test_status_sale_price_validation(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    for i, bad in enumerate([-1, -0.01, "nan", "inf", "1e999", "abc"]):
        animal = await make_animal(client, owner, tag=f"SP-{i}")
        resp = await mark_status(client, owner, animal["id"], "SOLD", sale_price=bad)
        assert resp.status_code == 422, bad
        assert (await get_animal(client, owner, animal["id"]))["status"] == "ACTIVE"
    assert await transactions(client, owner) == []


async def test_status_buyer_name_too_long_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner)
    resp = await mark_status(client, owner, animal["id"], "SOLD", buyer_name="B" * 121)
    assert resp.status_code == 422
    resp = await mark_status(client, owner, animal["id"], "SOLD", buyer_name="B" * 120)
    assert resp.status_code == 200, resp.text


async def test_status_nonexistent_animal_404(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await mark_status(client, owner, 424242, "SOLD", sale_price=100)
    assert resp.status_code == 404


async def test_status_malformed_id_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await client.post("/api/animals/abc/status", json={"new_status": "SOLD"}, headers=owner)
    assert resp.status_code == 422


async def test_status_sold_animal_visible_under_status_filter(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="S-1")
    resp = await mark_status(client, owner, animal["id"], "SOLD", sale_price=100)
    assert resp.status_code == 200, resp.text
    assert await list_animals(client, owner) == []
    sold = await list_animals(client, owner, status="SOLD")
    assert [a["tag_number"] for a in sold] == ["S-1"]


async def test_status_change_clears_cull_candidate(client: httpx.AsyncClient) -> None:
    """A cull-flagged doe that is actually culled loses the flag (the flag is a
    worklist marker for live animals)."""
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner)
    buck = await make_buck(client, owner)
    # Each recorded result must be on/after its scheduled (+32-day) check.
    for days_ago in (70, 35):  # two consecutive FAILED cycles → cull candidate
        br = await make_breeding(
            client, owner, doe["id"], buck["id"], today() - timedelta(days=days_ago)
        )
        await submit_ultrasound(
            client,
            owner,
            br["id"],
            pregnant=False,
            result_date=date.fromisoformat(br["ultrasound_date"]),
        )
    assert (await get_animal(client, owner, doe["id"]))["cull_candidate"] is True
    resp = await mark_status(client, owner, doe["id"], "CULLED")
    assert resp.status_code == 200, resp.text
    assert resp.json()["cull_candidate"] is False


async def test_batch_quarantine_duties_are_swept_when_its_herd_is_gone(
    client: httpx.AsyncClient,
) -> None:
    """The 45-day protocol is linked to the batch, not to an animal, so the
    per-animal skip cannot reach it. Once nobody is left the duties can never
    be completed (the health form needs an active quarantine animal) nor
    skipped (a generated batch duty refuses one), so they must be swept."""
    owner = await owner_with_farm(client)
    created = await client.post(
        "/api/purchases/new",
        json={"date": iso(today()), "count": 2, "create_animals": True},
        headers=owner,
    )
    assert created.status_code == 201, created.text
    batch_id = created.json()["id"]
    detail = await client.get(f"/api/purchases/{batch_id}", headers=owner)
    assert detail.status_code == 200, detail.text
    animals = detail.json()["animals"]
    assert len(animals) == 2
    assert {task["status"] for task in detail.json()["tasks"]} == {"PENDING"}

    first = await mark_status(client, owner, animals[0]["id"], "DEAD", mortality_cause="PPR")
    assert first.status_code == 200, first.text
    # One animal left: the batch protocol is still live work.
    listed = (await client.get("/api/purchases", headers=owner)).json()["batches"][0]
    assert listed["open_tasks"] == 8

    second = await mark_status(client, owner, animals[1]["id"], "DEAD", mortality_cause="PPR")
    assert second.status_code == 200, second.text
    listed = (await client.get("/api/purchases", headers=owner)).json()["batches"][0]
    assert listed["open_tasks"] == 0
    closed = await client.get(f"/api/purchases/{batch_id}", headers=owner)
    tasks = closed.json()["tasks"]
    assert len(tasks) == 8
    assert {task["status"] for task in tasks} == {"SKIPPED"}
    dash = await client.get("/api/dashboard", headers=owner)
    assert dash.status_code == 200, dash.text
    assert dash.json()["todays_tasks_total"] == dash.json()["overdue_tasks_total"] == 0


# ---------------------------------------------------------------------------
# 9. Buckets board
# ---------------------------------------------------------------------------
async def test_buckets_board_returns_all_ten_in_spec_order(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await client.get("/api/buckets", headers=owner)
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert [r["bucket"] for r in rows] == ALL_BUCKETS
    for row in rows:
        assert set(row) == {
            "bucket",
            "name",
            "who",
            "exit_rule",
            "daily_kg_per_head",
            "animals",
            "animals_total",
            "animals_limit",
            "animals_page_path",
        }
        assert row["animals_limit"] == 100
        assert row["animals_page_path"] == f"/animals?bucket={row['bucket']}&status=ACTIVE"


async def test_buckets_board_register_link_opens_the_population_it_counted(
    client: httpx.AsyncClient,
) -> None:
    """A sold animal keeps its last bucket forever, so a link without the
    status filter opens a register whose headcount contradicts the board."""
    owner = await owner_with_farm(client)
    await make_animal(client, owner, tag="LIVE-1", bucket="FOUNDATION")
    sold = await make_animal(client, owner, tag="GONE-1", bucket="FOUNDATION")
    assert (await mark_status(client, owner, sold["id"], "SOLD", sale_price=100)).status_code == 200

    row = next(
        r
        for r in (await client.get("/api/buckets", headers=owner)).json()
        if r["bucket"] == "FOUNDATION"
    )
    assert row["animals_total"] == 1
    _path, _, query = row["animals_page_path"].partition("?")
    filters = dict(pair.split("=", 1) for pair in query.split("&"))
    register = await client.get("/api/animals", params=filters, headers=owner)
    assert register.status_code == 200, register.text
    assert register.json()["total"] == row["animals_total"]
    assert [a["tag_number"] for a in register.json()["animals"]] == ["LIVE-1"]


async def test_buckets_board_quarantine_metadata_matches_spec(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await client.get("/api/buckets", headers=owner)
    row = next(r for r in resp.json() if r["bucket"] == "QUARANTINE")
    assert row["name"] == "Quarantine Ward"
    assert row["who"] == "Newly purchased animals"
    assert "45-day" in row["exit_rule"]
    assert "FOUNDATION" in row["exit_rule"]


async def test_buckets_board_default_feed_rates(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await client.get("/api/buckets", headers=owner)
    rates = {r["bucket"]: r["daily_kg_per_head"] for r in resp.json()}
    assert rates == {
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


async def test_buckets_board_occupancy(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await make_animal(client, owner, tag="Q-1", bucket="QUARANTINE")
    await make_animal(client, owner, tag="Q-2", bucket="QUARANTINE")
    await make_animal(client, owner, tag="K-1", sex="M", bucket="MALE_KIDS")
    rows = (await client.get("/api/buckets", headers=owner)).json()
    by_row = {r["bucket"]: r for r in rows}
    by_bucket = {
        bucket: [a["tag_number"] for a in row["animals"]] for bucket, row in by_row.items()
    }
    assert by_bucket["QUARANTINE"] == ["Q-1", "Q-2"]
    assert by_bucket["MALE_KIDS"] == ["K-1"]
    assert by_row["QUARANTINE"]["animals_total"] == 2
    assert by_row["MALE_KIDS"]["animals_total"] == 1
    for bucket in ALL_BUCKETS:
        if bucket not in ("QUARANTINE", "MALE_KIDS"):
            assert by_bucket[bucket] == [], bucket


async def test_buckets_board_animals_sorted_by_tag(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await make_animal(client, owner, tag="B-2", bucket="BREEDING")
    await make_animal(client, owner, tag="A-1", bucket="BREEDING")
    await make_animal(client, owner, tag="C-3", bucket="BREEDING")
    rows = (await client.get("/api/buckets", headers=owner)).json()
    breeding = next(r for r in rows if r["bucket"] == "BREEDING")
    assert [a["tag_number"] for a in breeding["animals"]] == ["A-1", "B-2", "C-3"]


async def test_buckets_board_excludes_non_active_animals(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    alive = await make_animal(client, owner, tag="L-1", bucket="BREEDING")
    for i, status in enumerate(["SOLD", "DEAD", "CULLED"]):
        animal = await make_animal(client, owner, tag=f"X-{i}", bucket="BREEDING")
        resp = await mark_status(client, owner, animal["id"], status)
        assert resp.status_code == 200, resp.text
    rows = (await client.get("/api/buckets", headers=owner)).json()
    breeding = next(r for r in rows if r["bucket"] == "BREEDING")
    assert [a["tag_number"] for a in breeding["animals"]] == [alive["tag_number"]]


async def test_buckets_board_reflects_moves(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    dob = today() - timedelta(days=500)
    animal = await make_animal(
        client,
        owner,
        tag="M-1",
        bucket="FOUNDATION",
        date_of_birth=iso(dob),
        weight_kg=26,
        weight_date=iso(dob),
    )
    resp = await client.post(
        f"/api/animals/{animal['id']}/move", json={"to_bucket": "BREEDING"}, headers=owner
    )
    assert resp.status_code == 200, resp.text
    rows = (await client.get("/api/buckets", headers=owner)).json()
    by_bucket = {r["bucket"]: [a["tag_number"] for a in r["animals"]] for r in rows}
    assert by_bucket["FOUNDATION"] == []
    assert by_bucket["BREEDING"] == ["M-1"]


async def test_buckets_board_cross_farm_isolation(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Alpha Farm")
    owner_b = await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    await make_animal(client, owner_b, tag="B-1", bucket="BREEDING")
    rows = (await client.get("/api/buckets", headers=owner_a)).json()
    assert all(r["animals"] == [] for r in rows)  # farm A sees none of B's goats
    rows = (await client.get("/api/buckets", headers=owner_b)).json()
    breeding = next(r for r in rows if r["bucket"] == "BREEDING")
    assert [a["tag_number"] for a in breeding["animals"]] == ["B-1"]


async def test_buckets_board_empty_farm_all_buckets_empty(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    rows = (await client.get("/api/buckets", headers=owner)).json()
    assert len(rows) == 10
    assert all(r["animals"] == [] for r in rows)
    assert all(r["animals_total"] == 0 for r in rows)


async def test_buckets_board_animal_entries_have_purpose_specific_shape(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    await make_animal(client, owner, tag="S-1", bucket="BREEDING", weight_kg=22.0)
    rows = (await client.get("/api/buckets", headers=owner)).json()
    breeding = next(r for r in rows if r["bucket"] == "BREEDING")
    entry = breeding["animals"][0]
    assert set(entry) == {
        "id",
        "tag_number",
        "name",
        "sex",
        "latest_weight_kg",
        "days_in_current_bucket",
    }
    assert entry["latest_weight_kg"] == 22.0


async def test_feeder_bucket_board_exposes_only_operational_animal_fields(
    client: httpx.AsyncClient,
) -> None:
    """buckets.view must not become a side door into the full animal profile."""
    owner = await owner_with_farm(client)
    created = await make_animal(
        client,
        owner,
        tag="PRIVATE-1",
        bucket="FOUNDATION",
        purchase_date=iso(today()),
        purchase_price=4321.0,
        seller_name="Private Seller",
        notes="Confidential owner note",
        weight_kg=24.5,
    )
    feeder = await worker_headers(client, owner, "FEEDER", "feeder-private@farm.in")
    mover = await worker_headers(client, owner, "MOVER", "mover-private@farm.in")

    expected_fields = {
        "id",
        "tag_number",
        "name",
        "sex",
        "latest_weight_kg",
        "days_in_current_bucket",
    }
    # The board is an operational preview for every caller, including owners;
    # profile data remains on the independently-authorized animals page.
    owner_rows = (await client.get("/api/buckets", headers=owner)).json()
    owner_entry = next(
        animal for row in owner_rows for animal in row["animals"] if animal["id"] == created["id"]
    )
    assert set(owner_entry) == expected_fields

    response = await client.get("/api/buckets", headers=feeder)
    assert response.status_code == 200, response.text
    feeder_entry = next(
        animal
        for row in response.json()
        for animal in row["animals"]
        if animal["id"] == created["id"]
    )
    assert set(feeder_entry) == expected_fields
    assert feeder_entry["tag_number"] == "PRIVATE-1"
    assert feeder_entry["latest_weight_kg"] == 24.5

    # animals.view does not widen a purpose-specific aggregate response.
    mover_rows = (await client.get("/api/buckets", headers=mover)).json()
    mover_entry = next(
        animal for row in mover_rows for animal in row["animals"] if animal["id"] == created["id"]
    )
    assert set(mover_entry) == expected_fields


# ---------------------------------------------------------------------------
# 10. RBAC on animals & buckets endpoints
# ---------------------------------------------------------------------------
async def test_animal_reads_redact_nested_fields_by_effective_permissions(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    dob = today() - timedelta(days=800)
    purchase_date = today() - timedelta(days=700)
    primary = await make_animal(
        client,
        owner,
        tag="PRIVATE-DOE",
        date_of_birth=iso(dob),
        purchase_date=iso(purchase_date),
        purchase_price=4321,
        seller_name="Private Seller",
        weight_kg=26,
        weight_date=iso(purchase_date),
        notes="Confidential owner note",
    )
    private_weight = await client.post(
        f"/api/animals/{primary['id']}/weight",
        json={"weight_kg": 27, "notes": "Private clinical weight narrative"},
        headers=owner,
    )
    assert private_weight.status_code == 201, private_weight.text
    buck = await make_buck(client, owner, "PRIVATE-BUCK")
    breeding = await make_breeding(
        client, owner, primary["id"], buck["id"], today() - timedelta(days=40)
    )
    first_event = await client.post(
        "/api/health/events",
        json={
            "animal_id": primary["id"],
            "date": iso(today()),
            "type": "TREATMENT",
            "product_name": "Private medicine",
            "disease_target": "PPR concern",
            "cost": 125,
            "suspected_scheduled_disease": True,
            "authority_notified_at": iso(today()),
            "isolation_started_at": iso(today()),
            "notes": "Private clinical note",
        },
        headers=owner,
    )
    assert first_event.status_code == 201, first_event.text
    cleared = await client.post(
        f"/api/health/restrictions/{primary['id']}/clear",
        json={"clearance_reference": "AHD-SECRET-1", "expected_restriction_version": 1},
        headers=owner,
    )
    assert cleared.status_code == 204, cleared.text
    second_event = await client.post(
        "/api/health/events",
        json={
            "animal_id": primary["id"],
            "date": iso(today()),
            "type": "TREATMENT",
            "disease_target": "Anthrax concern",
            "suspected_scheduled_disease": True,
            "authority_notified_at": iso(today()),
            "isolation_started_at": iso(today()),
            "notes": "Private follow-up",
        },
        headers=owner,
    )
    assert second_event.status_code == 201, second_event.text

    sold = await make_animal(client, owner, tag="PRIVATE-SOLD")
    sold_response = await mark_status(
        client, owner, sold["id"], "SOLD", sale_price=9876, buyer_name="Private Buyer"
    )
    assert sold_response.status_code == 200, sold_response.text
    dead = await make_animal(client, owner, tag="PRIVATE-DEAD")
    dead_response = await mark_status(
        client,
        owner,
        dead["id"],
        "DEAD",
        mortality_cause="Private diagnosis",
        mortality_reported_at=iso(today()),
    )
    assert dead_response.status_code == 200, dead_response.text
    # A grown male kid, so the suggestion comes from the market rule (age +
    # weight) rather than a breeding rule. This test is about field-level
    # redaction inside a nested dashboard animal, and a MOVER holds
    # animals.view but not breeding.view — the breeding-derived suggestions are
    # withheld from them entirely, which is asserted separately in
    # test_finance_extended.py.
    suggestion = await make_animal(
        client,
        owner,
        tag="PRIVATE-SUGGESTION",
        sex="M",
        bucket="MALE_KIDS",
        date_of_birth=iso(dob),
        purchase_date=iso(purchase_date),
        purchase_price=7777,
        seller_name="Dashboard Seller",
        weight_kg=26,
        weight_date=iso(purchase_date),
        notes="Dashboard secret",
    )

    mover = await worker_headers(client, owner, "MOVER", "field-mover@farm.in")
    vet = await worker_headers(client, owner, "VET", "field-vet@farm.in")
    animal_finance = await custom_worker_headers(
        client,
        owner,
        name="Animal Finance Reader",
        email="animal-finance@farm.in",
        permissions=["animals.view", "finance.view"],
    )
    animal_purchases = await custom_worker_headers(
        client,
        owner,
        name="Animal Purchase Reader",
        email="animal-purchases@farm.in",
        permissions=["animals.view", "purchases.view"],
    )
    finance_only = await custom_worker_headers(
        client,
        owner,
        name="Finance Only Reader",
        email="finance-only@farm.in",
        permissions=["finance.view"],
    )

    mover_profile = await get_profile(client, mover, primary["id"])
    mover_animal = mover_profile["animal"]
    assert {
        key: mover_animal[key]
        for key in (
            "purchase_date",
            "purchase_price",
            "seller_name",
            "sale_price",
            "restriction_reason",
            "suspected_scheduled_disease",
            "suspected_disease",
            "authority_notified_at",
            "restriction_cleared_at",
            "restriction_cleared_by_id",
            "restriction_clearance_reference",
            "mortality_cause",
            "mortality_reported_at",
            "notes",
        )
    } == {
        "purchase_date": None,
        "purchase_price": None,
        "seller_name": None,
        "sale_price": None,
        "restriction_reason": None,
        "suspected_scheduled_disease": False,
        "suspected_disease": None,
        "authority_notified_at": None,
        "restriction_cleared_at": None,
        "restriction_cleared_by_id": None,
        "restriction_clearance_reference": None,
        "mortality_cause": None,
        "mortality_reported_at": None,
        "notes": None,
    }
    assert mover_animal["movement_restricted"] is True
    assert mover_animal["is_breeding_ready"] is False
    assert mover_animal["is_currently_pregnant"] is False
    assert mover_profile["health_events"] == []
    assert mover_profile["breedings"] == []
    assert all(weight["notes"] is None for weight in mover_profile["weights"])
    # Coordinates remain operationally visible, but arbitrary movement free
    # text can contain clinical/commercial detail and requires health.view.
    assert mover_profile["moves"]
    assert all(move["reason"] is None for move in mover_profile["moves"])
    assert any(move["to_bucket"] == "FOUNDATION" for move in mover_profile["moves"])

    mover_list = await list_animals(client, mover, q="PRIVATE-DOE")
    assert len(mover_list) == 1
    assert mover_list[0]["purchase_price"] is None
    assert mover_list[0]["notes"] is None
    assert mover_list[0]["movement_restricted"] is True
    mover_buckets = (await client.get("/api/buckets", headers=mover)).json()
    mover_bucket_animal = next(
        animal
        for row in mover_buckets
        for animal in row["animals"]
        if animal["id"] == primary["id"]
    )
    assert "purchase_price" not in mover_bucket_animal
    assert "notes" not in mover_bucket_animal
    assert "movement_restricted" not in mover_bucket_animal
    mover_dashboard = await client.get("/api/dashboard", headers=mover)
    assert mover_dashboard.status_code == 200, mover_dashboard.text
    mover_suggestion = next(
        row["animal"]
        for row in mover_dashboard.json()["suggestions"]
        if row["animal"]["id"] == suggestion["id"]
    )
    assert "purchase_price" not in mover_suggestion
    assert "seller_name" not in mover_suggestion
    assert "notes" not in mover_suggestion

    vet_profile = await get_profile(client, vet, primary["id"])
    vet_animal = vet_profile["animal"]
    assert vet_animal["purchase_date"] is None
    assert vet_animal["purchase_price"] is None
    assert vet_animal["seller_name"] is None
    assert vet_animal["notes"] == "Confidential owner note"
    assert vet_animal["movement_restricted"] is True
    assert vet_animal["suspected_scheduled_disease"] is True
    assert vet_animal["suspected_disease"] == "Anthrax concern"
    assert vet_animal["restriction_clearance_reference"] == "AHD-SECRET-1"
    assert vet_profile["breedings"] == [breeding["id"]]
    assert [event["cost"] for event in vet_profile["health_events"]] == [None, 125.0]
    assert vet_profile["health_events"][0]["notes"] == "Private follow-up"
    assert vet_profile["weights"][0]["notes"] == "Private clinical weight narrative"
    assert any(
        move["reason"] == "Historical import: Existing-herd test fixture"
        for move in vet_profile["moves"]
    )

    finance_profile = await get_profile(client, animal_finance, primary["id"])
    assert finance_profile["animal"]["purchase_price"] == 4321.0
    assert finance_profile["animal"]["purchase_date"] is None
    assert finance_profile["animal"]["seller_name"] is None
    assert finance_profile["animal"]["notes"] is None
    assert finance_profile["health_events"] == []
    assert finance_profile["breedings"] == []
    assert all(weight["notes"] is None for weight in finance_profile["weights"])
    assert (await get_animal(client, animal_finance, sold["id"]))["sale_price"] == 9876.0

    purchases_profile = await get_profile(client, animal_purchases, primary["id"])
    assert purchases_profile["animal"]["purchase_price"] == 4321.0
    assert purchases_profile["animal"]["purchase_date"] == iso(purchase_date)
    assert purchases_profile["animal"]["seller_name"] == "Private Seller"
    assert purchases_profile["animal"]["sale_price"] is None
    assert purchases_profile["animal"]["notes"] is None
    assert all(weight["notes"] is None for weight in purchases_profile["weights"])

    owner_profile = await get_profile(client, owner, primary["id"])
    assert owner_profile["animal"]["purchase_price"] == 4321.0
    assert owner_profile["animal"]["seller_name"] == "Private Seller"
    assert owner_profile["animal"]["notes"] == "Confidential owner note"
    assert owner_profile["animal"]["suspected_scheduled_disease"] is True
    assert owner_profile["animal"]["restriction_clearance_reference"] == "AHD-SECRET-1"
    assert owner_profile["breedings"] == [breeding["id"]]
    assert [event["cost"] for event in owner_profile["health_events"]] == [None, 125.0]
    assert owner_profile["weights"][0]["notes"] == "Private clinical weight narrative"
    assert (await get_animal(client, owner, sold["id"]))["sale_price"] == 9876.0
    assert (await get_animal(client, owner, dead["id"]))["mortality_cause"] == ("Private diagnosis")
    assert (await get_animal(client, mover, dead["id"]))["mortality_cause"] is None

    forbidden = await client.get(f"/api/animals/{primary['id']}", headers=finance_only)
    assert forbidden.status_code == 403
    assert forbidden.json()["detail"] == "Missing permission: animals.view"


async def test_mover_can_view_and_move_but_not_create_weight_or_status(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    mover = await worker_headers(client, owner, "MOVER", "mover@farm.in")
    dob = today() - timedelta(days=500)
    animal = await make_animal(
        client, owner, date_of_birth=iso(dob), weight_kg=26, weight_date=iso(dob)
    )

    assert (await client.get("/api/animals", headers=mover)).status_code == 200
    assert (await client.get("/api/buckets", headers=mover)).status_code == 200
    assert (await client.get(f"/api/animals/{animal['id']}", headers=mover)).status_code == 200
    resp = await client.post(
        f"/api/animals/{animal['id']}/move", json={"to_bucket": "BREEDING"}, headers=mover
    )
    assert resp.status_code == 200, resp.text

    resp = await post_animal(
        client,
        mover,
        {"tag_number": "X-1", "sex": "F", "source": "PURCHASED", "current_bucket": "FOUNDATION"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Missing permission: animals.create"
    resp = await client.post(
        f"/api/animals/{animal['id']}/weight", json={"weight_kg": 10}, headers=mover
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Missing permission: animals.weight"
    resp = await mark_status(client, mover, animal["id"], "SOLD", sale_price=100)
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Missing permission: animals.status"


async def test_vet_can_record_weight_but_not_move_create_or_status(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    vet = await worker_headers(client, owner, "VET", "vet@farm.in")
    animal = await make_animal(client, owner)

    assert (await client.get("/api/animals", headers=vet)).status_code == 200
    assert (await client.get("/api/buckets", headers=vet)).status_code == 200
    resp = await client.post(
        f"/api/animals/{animal['id']}/weight", json={"weight_kg": 21}, headers=vet
    )
    assert resp.status_code == 201, resp.text

    resp = await client.post(
        f"/api/animals/{animal['id']}/move", json={"to_bucket": "BREEDING"}, headers=vet
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Missing permission: animals.move"
    resp = await post_animal(
        client,
        vet,
        {"tag_number": "X-1", "sex": "F", "source": "PURCHASED", "current_bucket": "FOUNDATION"},
    )
    assert resp.status_code == 403
    resp = await mark_status(client, vet, animal["id"], "DEAD")
    assert resp.status_code == 403


async def test_cleaner_forbidden_on_all_animal_endpoints(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    cleaner = await worker_headers(client, owner, "CLEANER", "cleaner@farm.in")
    animal = await make_animal(client, owner)
    resp = await client.get("/api/animals", headers=cleaner)
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Missing permission: animals.view"
    resp = await client.get("/api/buckets", headers=cleaner)
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Missing permission: buckets.view"
    resp = await client.get(f"/api/animals/{animal['id']}", headers=cleaner)
    assert resp.status_code == 403
    resp = await client.post(
        f"/api/animals/{animal['id']}/move", json={"to_bucket": "BREEDING"}, headers=cleaner
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# N2: selling/killing a lactating doe must not orphan kids
# in RECOVERY (they'd stay on the lactating recipe forever with no new
# WEANING task since hers gets skipped).
# ---------------------------------------------------------------------------
async def _make_kid(
    client: httpx.AsyncClient, headers: dict, tag: str, dam_id: int, sex: str
) -> dict:
    """Directly create an ACTIVE kid in RECOVERY with dam_id set — simulates
    the state after a kidding was recorded."""
    resp = await client.post(
        "/api/animals",
        json={
            "tag_number": tag,
            "sex": sex,
            "source": "BORN",
            "current_bucket": "MALE_KIDS" if sex == "M" else "FEMALE_KIDS",
            "date_of_birth": iso(today() - timedelta(days=15)),
            "historical_import_reason": "Post-kidding lifecycle test fixture",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    kid = resp.json()
    # dam_id is set by the kidding flow, not by create; patch it on the model
    # directly via a SQLAlchemy update using an internal test helper.
    from sqlalchemy import update as sa_update

    from app.db import get_sessionmaker
    from app.models import Animal

    async with get_sessionmaker()() as db:
        await db.execute(
            sa_update(Animal)
            .where(Animal.id == kid["id"])
            .values(dam_id=dam_id, current_bucket="RECOVERY")
        )
        await db.commit()
    return kid


async def test_selling_lactating_doe_moves_recovery_kids_out(client: httpx.AsyncClient) -> None:
    """Doe SOLD while she has ACTIVE kids in RECOVERY → each kid moves to
    MALE_KIDS/FEMALE_KIDS by sex."""
    headers = await owner_with_farm(client)
    doe_resp = await client.post(
        "/api/animals",
        json={
            "tag_number": "DAM-1",
            "sex": "F",
            "source": "PURCHASED",
            "current_bucket": "RECOVERY",
        },
        headers=headers,
    )
    doe = doe_resp.json()
    kid_f = await _make_kid(client, headers, "K-F", doe["id"], "F")
    kid_m = await _make_kid(client, headers, "K-M", doe["id"], "M")

    resp = await client.post(
        f"/api/animals/{doe['id']}/status",
        json={"new_status": "SOLD", "sale_price": 3000},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    kid_f_after = (await client.get(f"/api/animals/{kid_f['id']}", headers=headers)).json()
    kid_m_after = (await client.get(f"/api/animals/{kid_m['id']}", headers=headers)).json()
    assert kid_f_after["animal"]["current_bucket"] == "FEMALE_KIDS"
    assert kid_m_after["animal"]["current_bucket"] == "MALE_KIDS"


async def test_dead_doe_moves_recovery_kids_out(client: httpx.AsyncClient) -> None:
    """Same behavior for the DEAD status transition — mother lost, kids get
    an early wean rather than lingering in RECOVERY."""
    headers = await owner_with_farm(client)
    doe = (
        await client.post(
            "/api/animals",
            json={
                "tag_number": "DAM-2",
                "sex": "F",
                "source": "PURCHASED",
                "current_bucket": "RECOVERY",
            },
            headers=headers,
        )
    ).json()
    kid = await _make_kid(client, headers, "K-D", doe["id"], "F")

    resp = await client.post(
        f"/api/animals/{doe['id']}/status", json={"new_status": "DEAD"}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    after = (await client.get(f"/api/animals/{kid['id']}", headers=headers)).json()
    assert after["animal"]["current_bucket"] == "FEMALE_KIDS"


async def test_kids_not_in_recovery_are_untouched_when_dam_sold(client: httpx.AsyncClient) -> None:
    """A kid that already left RECOVERY (weaned earlier) stays where it is —
    the orphan sweep is narrowly scoped to the RECOVERY bucket."""
    headers = await owner_with_farm(client)
    doe = (
        await client.post(
            "/api/animals",
            json={
                "tag_number": "DAM-3",
                "sex": "F",
                "source": "PURCHASED",
                "current_bucket": "RECOVERY",
            },
            headers=headers,
        )
    ).json()
    kid = await _make_kid(client, headers, "K-W", doe["id"], "F")
    # Move the kid out of RECOVERY (as complete_task WEANING would).
    move = await client.post(
        f"/api/animals/{kid['id']}/move",
        json={
            "to_bucket": "FEMALE_KIDS",
            "reason": "Historical test fixture: already weaned",
            "history_override": True,
        },
        headers=headers,
    )
    assert move.status_code == 200, move.text

    resp = await client.post(
        f"/api/animals/{doe['id']}/status",
        json={"new_status": "SOLD", "sale_price": 3000},
        headers=headers,
    )
    assert resp.status_code == 200
    after = (await client.get(f"/api/animals/{kid['id']}", headers=headers)).json()
    # Still in FEMALE_KIDS; the orphan sweep didn't move her again.
    assert after["animal"]["current_bucket"] == "FEMALE_KIDS"
    # And no phantom "Dam marked sold — early wean" BucketMove landed on her.
    reasons = [m["reason"] for m in after["moves"]]
    assert "Dam marked sold — early wean" not in reasons


async def test_manual_orphan_wean_rejects_kid_without_kidding_provenance(
    client: httpx.AsyncClient,
) -> None:
    """A terminal dam_id alone is not authority to forge RECOVERY → kids."""
    headers = await owner_with_farm(client)
    doe = (
        await client.post(
            "/api/animals",
            json={
                "tag_number": "DAM-NO-PROVENANCE",
                "sex": "F",
                "source": "PURCHASED",
                "current_bucket": "RECOVERY",
            },
            headers=headers,
        )
    ).json()
    kid = await _make_kid(client, headers, "K-NO-PROVENANCE", doe["id"], "M")
    held = await client.post(
        "/api/health/events",
        json={
            "scope": "animal",
            "animal_id": kid["id"],
            "type": "TREATMENT",
            "disease_target": "Reportable-condition concern",
            "suspected_scheduled_disease": True,
        },
        headers=headers,
    )
    assert held.status_code == 201, held.text
    dead = await mark_status(client, headers, doe["id"], "DEAD")
    assert dead.status_code == 200, dead.text
    cleared = await client.post(
        f"/api/health/restrictions/{kid['id']}/clear",
        json={
            "clearance_reference": "District AHD fixture clearance",
            "expected_restriction_version": 1,
        },
        headers=headers,
    )
    assert cleared.status_code == 204, cleared.text

    forged = await client.post(
        f"/api/animals/{kid['id']}/move",
        json={"to_bucket": "MALE_KIDS"},
        headers=headers,
    )
    assert forged.status_code == 409
    assert "illegal lifecycle transition" in forged.json()["detail"].lower()
    assert (await get_animal(client, headers, kid["id"]))["current_bucket"] == "RECOVERY"


# ---------------------------------------------------------------------------
# N3: a terminal status also closes the doe's never-scanned service
# ---------------------------------------------------------------------------
# The sweep beside the pregnancy auto-abort: a PENDING (not yet ultrasounded)
# service has no other exit once the doe leaves the herd, because
# record_ultrasound_result refuses a non-ACTIVE doe. It used to sit in her
# history for good.
async def _bred_doe(
    client: httpx.AsyncClient, headers: dict, tag: str = "D-BRED"
) -> tuple[dict, dict, dict]:
    doe = await make_doe(client, headers, tag)
    buck = await make_buck(client, headers, f"{tag}-B")
    br = await make_breeding(client, headers, doe["id"], buck["id"], today() - timedelta(days=35))
    assert br["outcome"] == "PENDING"
    return doe, buck, br


async def _breeding_record(client: httpx.AsyncClient, headers: dict, br_id: int) -> dict:
    resp = await client.get(f"/api/breeding/{br_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _duties(client: httpx.AsyncClient, headers: dict) -> list[dict]:
    resp = await client.get("/api/tasks", headers=headers)
    assert resp.status_code == 200, resp.text
    tabs = resp.json()
    return tabs["today"] + tabs["overdue"] + tabs["upcoming"] + tabs["awaiting"] + tabs["completed"]


async def test_sale_closes_never_scanned_service_and_still_books_income(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    doe, _buck, br = await _bred_doe(client, headers)

    sold = await mark_status(client, headers, doe["id"], "SOLD", sale_price=4000)
    assert sold.status_code == 200, sold.text
    assert sold.json()["status"] == "SOLD"

    assert (await _breeding_record(client, headers, br["id"]))["outcome"] == "UNASSESSED"
    # Closing the service leaves no duty pointing at it, and the sale's own
    # side effects are untouched by the extra sweep.
    related = [t for t in await _duties(client, headers) if t["breeding_record_id"] == br["id"]]
    assert {t["status"] for t in related} == {"SKIPPED"}
    assert [t["amount"] for t in await transactions(client, headers)] == [4000.0]


async def test_selling_the_sire_leaves_the_does_service_open(
    client: httpx.AsyncClient,
) -> None:
    """The sweep belongs to the doe, not the sire: she is still in the herd and
    her pregnancy check can still answer whether this service took."""
    headers = await owner_with_farm(client)
    _doe, buck, br = await _bred_doe(client, headers)

    sold = await mark_status(client, headers, buck["id"], "SOLD", sale_price=5000)
    assert sold.status_code == 200, sold.text
    assert (await _breeding_record(client, headers, br["id"]))["outcome"] == "PENDING"
