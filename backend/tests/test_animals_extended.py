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

Note: the list endpoint supports OPTIONAL limit/offset pagination (added in
the hardening phase); the default (no params) still returns the full filtered
list, and filter coverage is exhaustive.
"""

import re
from datetime import timedelta

import httpx

from app.utils import today

from .conftest import owner_with_farm

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


# Breeding-flow helpers (for profile kids / cull-flag tests).
async def make_doe(client: httpx.AsyncClient, headers: dict, tag: str = "D-101") -> dict:
    return await make_animal(
        client,
        headers,
        tag=tag,
        sex="F",
        bucket="FOUNDATION",
        date_of_birth=iso(today() - timedelta(days=400)),
        weight_kg=26.0,
    )


async def make_buck(client: httpx.AsyncClient, headers: dict, tag: str = "B-01") -> dict:
    return await make_animal(client, headers, tag=tag, sex="M", bucket="BREEDING")


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
    client: httpx.AsyncClient, headers: dict, breeding_id: int, pregnant: bool, kid_count: int = 2
) -> dict:
    resp = await client.post(
        f"/api/breeding/{breeding_id}/ultrasound",
        json={"pregnant": pregnant, "kid_count": kid_count},
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
    # LOW 8-4: the header is required by the contract — missing fails request
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
    # B's token against A's farm → 404 like any unknown farm (LOW 0-7: no
    # farm-id existence oracle), never data
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


async def test_create_full_payload_all_fields_stored(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    payload = {
        "tag_number": "P-100",
        "name": "Lakshmi",
        "sex": "F",
        "source": "PURCHASED",
        "current_bucket": "QUARANTINE",
        "breed": "Osmanabadi",
        "estimated_dob": iso(today() - timedelta(days=305)),
        "birth_type": "TWIN",
        "birth_weight": 2.6,
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
    assert animal["birth_type"] == "TWIN"
    assert animal["birth_weight"] == 2.6
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
    assert moves[0]["reason"] == "Initial entry"
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
    animal = await make_animal(client, owner, source="BORN", birth_weight=2.4)
    assert animal["latest_weight_kg"] == 2.4


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
        bucket="RECOVERY",
        date_of_birth=iso(today()),
        birth_type="TRIPLET",
        birth_weight=2.1,
    )
    assert animal["source"] == "BORN"
    assert animal["birth_type"] == "TRIPLET"
    assert animal["age_months"] == 0


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


async def test_create_very_long_notes_accepted(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    notes = "n" * 10000
    animal = await make_animal(client, owner, notes=notes)
    assert animal["notes"] == notes


async def test_create_ignores_unknown_extra_fields(client: httpx.AsyncClient) -> None:
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
    assert resp.status_code == 201, resp.text
    animal = resp.json()
    assert animal["tag_number"] == "X-001"
    assert animal["status"] == "ACTIVE"  # smuggled status ignored
    assert animal["id"] != 42
    assert "photo_path" not in animal


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
    for i, bucket in enumerate(ALL_BUCKETS):
        animal = await make_animal(client, owner, tag=f"B-{i}", bucket=bucket)
        assert animal["current_bucket"] == bucket
    assert (await client.get("/api/animals", headers=owner)).json()["total"] == 10


async def test_create_invalid_birth_type_422(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    base = {"tag_number": "G-1", "sex": "F", "source": "PURCHASED", "current_bucket": "FOUNDATION"}
    for bad in ["QUINTUPLET", "single", "", 2]:
        resp = await post_animal(client, owner, base | {"birth_type": bad})
        assert resp.status_code == 422, bad


async def test_create_valid_birth_types(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    for i, bt in enumerate(["SINGLE", "TWIN", "TRIPLET"]):
        animal = await make_animal(client, owner, tag=f"BT-{i}", birth_type=bt)
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
    # +2 days: tomorrow itself is accepted (timezone headroom for clients
    # east of UTC — see PastOrTodayDate), anything further out still 422s.
    future = iso(today() + timedelta(days=2))
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
    base = {"tag_number": "G-1", "sex": "F", "source": "PURCHASED", "current_bucket": "FOUNDATION"}
    for bad in [-2, "-2", "nan", "inf", "1e999"]:
        resp = await post_animal(client, owner, base | {"birth_weight": bad})
        assert resp.status_code == 422, bad
    animal = await make_animal(client, owner, tag="G-2", birth_weight=0)
    assert animal["birth_weight"] == 0.0  # zero is non-negative → allowed
    animal = await make_animal(client, owner, tag="G-3", birth_weight=1000)
    assert animal["birth_weight"] == 1000.0  # exactly the 1000 kg weight cap
    resp = await post_animal(client, owner, base | {"tag_number": "G-4", "birth_weight": 1e6})
    assert resp.status_code == 422  # beyond the cap — B2 float-overflow bound


async def test_create_purchase_price_boundaries(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    base = {"tag_number": "G-1", "sex": "F", "source": "PURCHASED", "current_bucket": "FOUNDATION"}
    for bad in [-500, "-500", "nan", "inf", "1e999"]:
        resp = await post_animal(client, owner, base | {"purchase_price": bad})
        assert resp.status_code == 422, bad
    animal = await make_animal(client, owner, tag="G-2", purchase_price=0)
    assert animal["purchase_price"] == 0.0
    animal = await make_animal(client, owner, tag="G-3", purchase_price=999999999.99)
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
        {"tag_number": "A-001", "sex": "M", "source": "BORN", "current_bucket": "BREEDING"},
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


async def test_list_search_matches_tag_only_not_name(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await make_animal(client, owner, tag="T-1", name="Alpha")
    assert await list_animals(client, owner, q="Alpha") == []
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
    await make_animal(client, owner, tag="A-0", bucket="MALE_KIDS")
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
    """Optional limit/offset page the (bucket, tag)-ordered list; `total`
    stays the full filtered count. Omitting both keeps the all-rows default."""
    owner = await owner_with_farm(client)
    for i in range(5):
        await make_animal(client, owner, tag=f"P-{i}")
    resp = await client.get("/api/animals", headers=owner)
    full = resp.json()
    assert full["total"] == len(full["animals"]) == 5  # default behavior unchanged

    resp = await client.get("/api/animals", headers=owner, params={"limit": "2", "offset": "1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 5
    assert [a["tag_number"] for a in body["animals"]] == ["P-1", "P-2"]

    resp = await client.get("/api/animals", headers=owner, params={"limit": "1000"})
    assert resp.status_code == 200 and len(resp.json()["animals"]) == 5
    resp = await client.get("/api/animals", headers=owner, params={"offset": "4"})
    assert [a["tag_number"] for a in resp.json()["animals"]] == ["P-4"]
    resp = await client.get("/api/animals", headers=owner, params={"offset": "5"})
    assert resp.json()["animals"] == [] and resp.json()["total"] == 5


async def test_list_pagination_params_validated(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    for params in ({"limit": "0"}, {"limit": "1001"}, {"limit": "-1"}, {"offset": "-1"}):
        resp = await client.get("/api/animals", headers=owner, params=params)
        assert resp.status_code == 422, params


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
    assert set(profile) == {"animal", "kids", "weights", "moves", "health_events", "breedings"}
    assert profile["animal"]["id"] == animal["id"]
    assert profile["kids"] == []
    assert profile["health_events"] == []
    assert profile["breedings"] == []
    assert len(profile["weights"]) == 1
    assert len(profile["moves"]) == 1


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
    animal = await make_animal(client, owner, bucket="FOUNDATION")
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
    br = await submit_ultrasound(client, owner, br["id"], pregnant=True, kid_count=2)
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
    assert all(k["dam_id"] == doe["id"] and k["sire_id"] == buck["id"] for k in kids)
    assert all(k["source"] == "BORN" and k["current_bucket"] == "RECOVERY" for k in kids)
    # the kids themselves have no breedings and no kids
    kid_profile = await get_profile(client, owner, kids[0]["id"])
    assert kid_profile["breedings"] == []
    assert kid_profile["kids"] == []


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
    animal = await make_animal(client, owner)
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
        json={"to_bucket": "FOUNDATION", "reason": "45-day protocol complete"},
        headers=owner,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["current_bucket"] == "FOUNDATION"


async def test_move_records_history_with_reason(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, bucket="FOUNDATION")
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
    animal = await make_animal(client, owner)
    for reason in [None, "", "   "]:
        payload = {"to_bucket": "BREEDING", "reason": reason}
        resp = await client.post(f"/api/animals/{animal['id']}/move", json=payload, headers=owner)
        assert resp.status_code == 200, resp.text
        moves = (await get_profile(client, owner, animal["id"]))["moves"]
        assert moves[0]["reason"] is None
        # move back so the next iteration has somewhere to move from
        resp = await client.post(
            f"/api/animals/{animal['id']}/move", json={"to_bucket": "FOUNDATION"}, headers=owner
        )
        assert resp.status_code == 200, resp.text


async def test_move_reason_whitespace_trimmed(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner)
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
        assert resp.status_code == 400, status
        assert "cannot move buckets" in resp.json()["detail"]
        after = await get_animal(client, owner, animal["id"])
        assert after["current_bucket"] == "FOUNDATION"  # unmoved
        assert len((await get_profile(client, owner, animal["id"]))["moves"]) == 1


async def test_move_chain_full_history(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, bucket="QUARANTINE")
    for target in ["FOUNDATION", "BREEDING"]:
        resp = await client.post(
            f"/api/animals/{animal['id']}/move", json={"to_bucket": target}, headers=owner
        )
        assert resp.status_code == 200, resp.text
    moves = (await get_profile(client, owner, animal["id"]))["moves"]
    assert [(m["from_bucket"], m["to_bucket"]) for m in moves] == [
        ("FOUNDATION", "BREEDING"),
        ("QUARANTINE", "FOUNDATION"),
        (None, "QUARANTINE"),
    ]


async def test_move_resets_days_in_bucket(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner)
    resp = await client.post(
        f"/api/animals/{animal['id']}/move", json={"to_bucket": "BREEDING"}, headers=owner
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["days_in_current_bucket"] == 0


async def test_move_to_every_bucket_accepted(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, bucket="QUARANTINE")
    for target in ALL_BUCKETS:
        if target == "QUARANTINE":
            continue
        resp = await client.post(
            f"/api/animals/{animal['id']}/move", json={"to_bucket": target}, headers=owner
        )
        assert resp.status_code == 200, (target, resp.text)
        assert resp.json()["current_bucket"] == target


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
    future = iso(today() + timedelta(days=2))  # tomorrow is allowed (tz headroom)
    resp = await client.post(
        f"/api/animals/{animal['id']}/weight",
        json={"date": future, "weight_kg": 10},
        headers=owner,
    )
    assert resp.status_code == 422
    assert (await get_profile(client, owner, animal["id"]))["weights"] == []


async def test_weight_tomorrow_date_accepted(client: httpx.AsyncClient) -> None:
    """UTC-tomorrow is a real "today" for clients east of UTC (IST is UTC+5:30,
    so 00:00-05:30 IST is still tomorrow in UTC) — one day of headroom keeps
    their same-day entries from 422ing."""
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner)
    tomorrow = iso(today() + timedelta(days=1))
    resp = await client.post(
        f"/api/animals/{animal['id']}/weight",
        json={"date": tomorrow, "weight_kg": 10},
        headers=owner,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["date"] == tomorrow


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


async def test_status_sold_zero_price_books_no_transaction(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner)
    resp = await mark_status(client, owner, animal["id"], "SOLD", sale_price=0)
    assert resp.status_code == 200, resp.text
    assert resp.json()["sale_price"] == 0.0
    assert await transactions(client, owner) == []


async def test_status_dead_and_culled_book_no_transaction(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    for i, status in enumerate(["DEAD", "CULLED"]):
        animal = await make_animal(client, owner, tag=f"T-{i}")
        resp = await mark_status(client, owner, animal["id"], status)
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == status
        assert resp.json()["sale_price"] is None
    assert await transactions(client, owner) == []


async def test_status_dead_ignores_smuggled_sale_price(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner)
    resp = await mark_status(client, owner, animal["id"], "DEAD", sale_price=9999)
    assert resp.status_code == 200, resp.text
    assert resp.json()["sale_price"] is None  # price only applies to SOLD
    assert await transactions(client, owner) == []


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
        resp = await mark_status(client, owner, animal["id"], followup, sale_price=100)
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
        client, owner, animal["id"], "SOLD", date=iso(today() + timedelta(days=2))
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
    for _ in range(2):  # two consecutive FAILED cycles → cull candidate (SPEC)
        br = await make_breeding(client, owner, doe["id"], buck["id"], today())
        await submit_ultrasound(client, owner, br["id"], pregnant=False)
    assert (await get_animal(client, owner, doe["id"]))["cull_candidate"] is True
    resp = await mark_status(client, owner, doe["id"], "CULLED")
    assert resp.status_code == 200, resp.text
    assert resp.json()["cull_candidate"] is False


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
        assert set(row) == {"bucket", "name", "who", "exit_rule", "daily_kg_per_head", "animals"}


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
    by_bucket = {r["bucket"]: [a["tag_number"] for a in r["animals"]] for r in rows}
    assert by_bucket["QUARANTINE"] == ["Q-1", "Q-2"]
    assert by_bucket["MALE_KIDS"] == ["K-1"]
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
    animal = await make_animal(client, owner, tag="M-1", bucket="FOUNDATION")
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


async def test_buckets_board_animal_entries_have_full_shape(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await make_animal(client, owner, tag="S-1", bucket="BREEDING", weight_kg=22.0)
    rows = (await client.get("/api/buckets", headers=owner)).json()
    breeding = next(r for r in rows if r["bucket"] == "BREEDING")
    entry = breeding["animals"][0]
    for field in [
        "id",
        "tag_number",
        "sex",
        "status",
        "current_bucket",
        "age_months",
        "latest_weight_kg",
        "days_in_current_bucket",
    ]:
        assert field in entry, field
    assert entry["latest_weight_kg"] == 22.0


# ---------------------------------------------------------------------------
# 10. RBAC on animals & buckets endpoints
# ---------------------------------------------------------------------------
async def test_mover_can_view_and_move_but_not_create_weight_or_status(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    mover = await worker_headers(client, owner, "MOVER", "mover@farm.in")
    animal = await make_animal(client, owner)

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
