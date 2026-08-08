"""Extended team / roles / RBAC-enforcement tests.

Covers the team module (`/api/team`, `app/permissions.py`, `app/deps.py`):
worker invitation & lifecycle (create, role change, toggle, password reset),
preset-role seeding with their exact permission bundles, custom role CRUD,
the full permission-enforcement matrix (every preset role × every module's
GET endpoint), owner bypass, deactivated-worker access revocation,
`/api/auth/permissions` accuracy, auth/tenancy guards, and input fuzzing
(unicode, SQL-injection-looking strings, boundary values).

Expected preset bundles below are HARDCODED (not imported from
`app.permissions`) so accidental edits to the catalog fail the suite.
"""

import asyncio

import httpx
import pytest
from sqlalchemy import event, select, text

from app.core.config import get_settings
from app.db import get_engine, get_sessionmaker
from app.models import FarmMembership, User
from app.utils import today

from .conftest import owner_with_farm, register

WORKER_PW = "workerpass123"
COOKIE = get_settings().refresh_cookie_name

# Exact permission bundles per preset role (mirror of ROLE_PRESETS in
# app/permissions.py, frozen here as the contract under test).
PRESET_PERMS: dict[str, set[str]] = {
    "MOVER": {
        "dashboard.view",
        "animals.view",
        "animals.move",
        "buckets.view",
        "tasks.view",
        "tasks.complete",
    },
    "VET": {
        "dashboard.view",
        "animals.view",
        "animals.weight",
        "buckets.view",
        "breeding.view",
        "breeding.manage",
        "health.view",
        "health.manage",
        "tasks.view",
        "tasks.complete",
    },
    "CLEANER": {"dashboard.view", "tasks.view", "tasks.complete"},
    "CLEANER_MANAGER": {"dashboard.view", "tasks.view", "tasks.complete", "tasks.verify"},
    "FEEDER": {
        "dashboard.view",
        "feeding.view",
        "feeding.manage",
        "buckets.view",
        "tasks.view",
        "tasks.complete",
    },
}

PRESET_NAMES = {
    "MOVER": "Animal Mover",
    "VET": "Veterinarian",
    "CLEANER": "Cleaner",
    "CLEANER_MANAGER": "Cleaner Manager",
    "FEEDER": "Feeder",
}

# The full permission catalog (27 codes; SPEC says "~24").
ALL_PERMS = {
    "dashboard.view",
    "animals.view",
    "animals.create",
    "animals.move",
    "animals.weight",
    "animals.status",
    "buckets.view",
    "breeding.view",
    "breeding.manage",
    "kidding.view",
    "kidding.manage",
    "health.view",
    "health.manage",
    "purchases.view",
    "purchases.manage",
    "feeding.view",
    "feeding.manage",
    "tasks.view",
    "tasks.create",
    "tasks.complete",
    "tasks.verify",
    "finance.view",
    "finance.manage",
    "simulation.view",
    "simulation.manage",
    "reports.view",
    "team.manage",
}

# Every module's read endpoint and the permission that guards it.
GET_ENDPOINTS: list[tuple[str, str]] = [
    ("/api/dashboard", "dashboard.view"),
    ("/api/animals", "animals.view"),
    ("/api/buckets", "buckets.view"),
    ("/api/breeding", "breeding.view"),
    ("/api/kidding", "kidding.view"),
    ("/api/health/events", "health.view"),
    ("/api/purchases", "purchases.view"),
    ("/api/feeding/plan", "feeding.view"),
    ("/api/feeding/recipes", "feeding.view"),
    ("/api/feeding/inventory", "feeding.view"),
    ("/api/tasks", "tasks.view"),
    ("/api/finance", "finance.view"),
    ("/api/dashboard/reports", "reports.view"),
    ("/api/team", "team.manage"),
]

# Every team endpoint (method, url-with-ids-template) for auth/tenancy sweeps.
TEAM_ENDPOINTS: list[tuple[str, str]] = [
    ("GET", "/api/team"),
    ("POST", "/api/team/workers"),
    ("POST", "/api/team/workers/1/role"),
    ("POST", "/api/team/workers/1/toggle"),
    ("POST", "/api/team/workers/1/reset-password"),
    ("POST", "/api/team/roles"),
    ("PUT", "/api/team/roles/1"),
    ("DELETE", "/api/team/roles/1"),
]


# ---------------------------------------------------------------------------
# Helpers (same patterns as tests/test_rbac.py)
# ---------------------------------------------------------------------------
async def login_user(client: httpx.AsyncClient, email: str, password: str) -> dict:
    resp = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def team_page(client: httpx.AsyncClient, headers: dict) -> dict:
    resp = await client.get("/api/team", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def role_id(client: httpx.AsyncClient, owner: dict, code: str) -> int:
    page = await team_page(client, owner)
    return next(r["id"] for r in page["roles"] if r["code"] == code)


async def membership_id(client: httpx.AsyncClient, owner: dict, email: str) -> int:
    page = await team_page(client, owner)
    return next(m["id"] for m in page["memberships"] if m["email"] == email)


async def add_worker(
    client: httpx.AsyncClient,
    owner: dict,
    rid: int,
    email: str,
    name: str | None = "Worker",
    password: str | None = WORKER_PW,
) -> httpx.Response:
    payload: dict = {"email": email, "role_id": rid}
    if name is not None:
        payload["name"] = name
    if password is not None:
        payload["password"] = password
    return await client.post("/api/team/workers", json=payload, headers=owner)


async def worker_headers(client: httpx.AsyncClient, owner: dict, code: str, email: str) -> dict:
    """Owner adds a worker with preset role `code`; returns farm headers."""
    rid = await role_id(client, owner, code)
    resp = await add_worker(client, owner, rid, email)
    assert resp.status_code == 201, resp.text
    headers = await login_user(client, email, WORKER_PW)
    return headers | {"X-Farm-Id": owner["X-Farm-Id"]}


async def make_animal(client: httpx.AsyncClient, owner: dict, tag: str = "A-001") -> int:
    resp = await client.post(
        "/api/animals",
        json={
            "tag_number": tag,
            "sex": "F",
            "source": "PURCHASED",
            "current_bucket": "FOUNDATION",
        },
        headers=owner,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def create_duty(
    client: httpx.AsyncClient, owner: dict, title: str, rid: int | None = None
) -> dict:
    payload: dict = {"title": title, "due_date": today().isoformat(), "category": "CLEANING"}
    if rid is not None:
        payload["assigned_role_id"] = rid
    resp = await client.post("/api/tasks", json=payload, headers=owner)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def create_custom_role(
    client: httpx.AsyncClient,
    owner: dict,
    name: str,
    permissions: list[str],
    description: str | None = None,
) -> dict:
    payload: dict = {"name": name, "permissions": permissions}
    if description is not None:
        payload["description"] = description
    resp = await client.post("/api/team/roles", json=payload, headers=owner)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def permissions_of(client: httpx.AsyncClient, headers: dict) -> dict:
    resp = await client.get("/api/auth/permissions", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def team_manager_headers(
    client: httpx.AsyncClient,
    owner: dict,
    email: str = "tm@farm.in",
    extra_perms: list[str] | None = None,
) -> tuple[dict, int]:
    """A worker whose ONLY power is team.manage (+ extras); returns (headers, membership id)."""
    role = await create_custom_role(
        client, owner, "Team Clerk", ["team.manage"] + (extra_perms or [])
    )
    resp = await add_worker(client, owner, role["id"], email)
    assert resp.status_code == 201, resp.text
    mid = resp.json()["id"]
    headers = await login_user(client, email, WORKER_PW)
    return headers | {"X-Farm-Id": owner["X-Farm-Id"]}, mid


# ---------------------------------------------------------------------------
# Permission enforcement matrix: preset roles × read endpoints
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("role_code", sorted(PRESET_PERMS))
@pytest.mark.parametrize(("url", "perm"), GET_ENDPOINTS)
async def test_role_read_matrix(
    client: httpx.AsyncClient, role_code: str, url: str, perm: str
) -> None:
    owner = await owner_with_farm(client)
    worker = await worker_headers(client, owner, role_code, f"{role_code.lower()}@farm.in")
    resp = await client.get(url, headers=worker)
    if perm in PRESET_PERMS[role_code]:
        assert resp.status_code == 200, f"{role_code} GET {url} → {resp.status_code}"
    else:
        assert resp.status_code == 403, f"{role_code} GET {url} → {resp.status_code}"
        assert resp.json()["detail"] == f"Missing permission: {perm}"


@pytest.mark.parametrize(("url", "perm"), GET_ENDPOINTS)
async def test_owner_read_matrix(client: httpx.AsyncClient, url: str, perm: str) -> None:
    """Owner bypass: every read endpoint opens without any role."""
    owner = await owner_with_farm(client)
    resp = await client.get(url, headers=owner)
    assert resp.status_code == 200, f"owner GET {url} → {resp.status_code}"


# ---------------------------------------------------------------------------
# Per-role write access: allowed jobs succeed, sampled forbidden writes 403
# ---------------------------------------------------------------------------
async def test_mover_allowed_and_forbidden_writes(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    mover = await worker_headers(client, owner, "MOVER", "mover@farm.in")
    animal_id = await make_animal(client, owner)

    # his job: move animals between buckets
    resp = await client.post(
        f"/api/animals/{animal_id}/move",
        json={"to_bucket": "BREEDING", "reason": "ready"},
        headers=mover,
    )
    assert resp.status_code == 200, resp.text

    # his job: complete a duty assigned to his role
    rid = await role_id(client, owner, "MOVER")
    duty = await create_duty(client, owner, "Shift doelings", rid=rid)
    resp = await client.post(f"/api/tasks/{duty['id']}/complete", headers=mover)
    assert resp.status_code == 200, resp.text

    forbidden = [
        (
            "POST",
            "/api/animals",
            {
                "tag_number": "X-1",
                "sex": "F",
                "source": "PURCHASED",
                "current_bucket": "FOUNDATION",
            },
            "animals.create",
        ),
        ("POST", f"/api/animals/{animal_id}/weight", {"weight_kg": 12.5}, "animals.weight"),
        ("POST", f"/api/animals/{animal_id}/status", {"status": "DEAD"}, "animals.status"),
        (
            "POST",
            "/api/tasks",
            {"title": "X", "due_date": today().isoformat(), "category": "OTHER"},
            "tasks.create",
        ),
        (
            "POST",
            "/api/finance/new",
            {"date": today().isoformat(), "type": "EXPENSE", "category": "OTHER", "amount": 100},
            "finance.manage",
        ),
        (
            "POST",
            "/api/feeding/dispense",
            {"bucket": "FOUNDATION", "shift": "MORNING", "qty_kg": 5},
            "feeding.manage",
        ),
        (
            "POST",
            "/api/team/workers",
            {"email": "w2@farm.in", "password": WORKER_PW, "role_id": rid},
            "team.manage",
        ),
    ]
    for method, url, body, perm in forbidden:
        resp = await client.request(method, url, json=body, headers=mover)
        assert resp.status_code == 403, f"MOVER {url} → {resp.status_code}"
        assert resp.json()["detail"] == f"Missing permission: {perm}"


async def test_vet_allowed_and_forbidden_writes(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    vet = await worker_headers(client, owner, "VET", "vet@farm.in")
    animal_id = await make_animal(client, owner)

    # his job: health events + weights
    resp = await client.post(
        "/api/health/events",
        json={
            "scope": "animal",
            "animal_id": animal_id,
            "date": today().isoformat(),
            "type": "TREATMENT",
            "product_name": "Oxytetracycline",
        },
        headers=vet,
    )
    assert resp.status_code == 201, resp.text
    resp = await client.post(
        f"/api/animals/{animal_id}/weight", json={"weight_kg": 26.0}, headers=vet
    )
    assert resp.status_code == 201, resp.text

    rid = await role_id(client, owner, "VET")
    forbidden = [
        (
            "POST",
            "/api/animals",
            {
                "tag_number": "X-1",
                "sex": "F",
                "source": "PURCHASED",
                "current_bucket": "FOUNDATION",
            },
            "animals.create",
        ),
        ("POST", f"/api/animals/{animal_id}/move", {"to_bucket": "BREEDING"}, "animals.move"),
        ("POST", f"/api/animals/{animal_id}/status", {"status": "DEAD"}, "animals.status"),
        (
            "POST",
            "/api/tasks",
            {"title": "X", "due_date": today().isoformat(), "category": "OTHER"},
            "tasks.create",
        ),
        (
            "POST",
            "/api/finance/new",
            {"date": today().isoformat(), "type": "EXPENSE", "category": "OTHER", "amount": 100},
            "finance.manage",
        ),
        (
            "POST",
            "/api/feeding/dispense",
            {"bucket": "FOUNDATION", "shift": "MORNING", "qty_kg": 5},
            "feeding.manage",
        ),
        (
            "POST",
            "/api/purchases/new",
            {"date": today().isoformat(), "count": 5},
            "purchases.manage",
        ),
        (
            "POST",
            "/api/team/workers",
            {"email": "w2@farm.in", "password": WORKER_PW, "role_id": rid},
            "team.manage",
        ),
    ]
    for method, url, body, perm in forbidden:
        resp = await client.request(method, url, json=body, headers=vet)
        assert resp.status_code == 403, f"VET {url} → {resp.status_code}"
        assert resp.json()["detail"] == f"Missing permission: {perm}"


async def test_cleaner_allowed_and_forbidden_writes(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    cleaner = await worker_headers(client, owner, "CLEANER", "cleaner@farm.in")

    # his job: complete a cleaning duty assigned to his role
    rid = await role_id(client, owner, "CLEANER")
    duty = await create_duty(client, owner, "Scrub troughs", rid=rid)
    resp = await client.post(f"/api/tasks/{duty['id']}/complete", headers=cleaner)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "DONE"

    # but he cannot verify it (no tasks.verify)
    resp = await client.post(f"/api/tasks/{duty['id']}/verify", headers=cleaner)
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Missing permission: tasks.verify"

    animal_id = await make_animal(client, owner)
    forbidden = [
        (
            "POST",
            "/api/animals",
            {
                "tag_number": "X-1",
                "sex": "F",
                "source": "PURCHASED",
                "current_bucket": "FOUNDATION",
            },
            "animals.create",
        ),
        ("POST", f"/api/animals/{animal_id}/move", {"to_bucket": "BREEDING"}, "animals.move"),
        (
            "POST",
            "/api/tasks",
            {"title": "X", "due_date": today().isoformat(), "category": "OTHER"},
            "tasks.create",
        ),
        (
            "POST",
            "/api/finance/new",
            {"date": today().isoformat(), "type": "EXPENSE", "category": "OTHER", "amount": 100},
            "finance.manage",
        ),
        ("POST", "/api/team/roles", {"name": "Nope", "permissions": []}, "team.manage"),
    ]
    for method, url, body, perm in forbidden:
        resp = await client.request(method, url, json=body, headers=cleaner)
        assert resp.status_code == 403, f"CLEANER {url} → {resp.status_code}"
        assert resp.json()["detail"] == f"Missing permission: {perm}"


async def test_cleaner_manager_allowed_and_forbidden_writes(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    cleaner = await worker_headers(client, owner, "CLEANER", "cleaner@farm.in")
    manager = await worker_headers(client, owner, "CLEANER_MANAGER", "cm@farm.in")

    # his job: verify cleaning work done by cleaners (two-person rule)
    rid = await role_id(client, owner, "CLEANER")
    duty = await create_duty(client, owner, "Disinfect pens", rid=rid)
    resp = await client.post(f"/api/tasks/{duty['id']}/complete", headers=cleaner)
    assert resp.status_code == 200, resp.text
    resp = await client.post(f"/api/tasks/{duty['id']}/verify", headers=manager)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "VERIFIED"

    # ... and complete duties assigned to his own role
    cm_rid = await role_id(client, owner, "CLEANER_MANAGER")
    own_duty = await create_duty(client, owner, "Deep clean store", rid=cm_rid)
    resp = await client.post(f"/api/tasks/{own_duty['id']}/complete", headers=manager)
    assert resp.status_code == 200, resp.text

    forbidden = [
        (
            "POST",
            "/api/tasks",
            {"title": "X", "due_date": today().isoformat(), "category": "CLEANING"},
            "tasks.create",
        ),
        (
            "POST",
            "/api/animals",
            {
                "tag_number": "X-1",
                "sex": "F",
                "source": "PURCHASED",
                "current_bucket": "FOUNDATION",
            },
            "animals.create",
        ),
        ("POST", "/api/team/roles", {"name": "Nope", "permissions": []}, "team.manage"),
        (
            "POST",
            "/api/finance/new",
            {"date": today().isoformat(), "type": "EXPENSE", "category": "OTHER", "amount": 100},
            "finance.manage",
        ),
    ]
    for method, url, body, perm in forbidden:
        resp = await client.request(method, url, json=body, headers=manager)
        assert resp.status_code == 403, f"CLEANER_MANAGER {url} → {resp.status_code}"
        assert resp.json()["detail"] == f"Missing permission: {perm}"


async def test_feeder_allowed_and_forbidden_writes(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    feeder = await worker_headers(client, owner, "FEEDER", "feeder@farm.in")
    animal_id = await make_animal(client, owner)

    # his job: record dispensing + ration settings
    resp = await client.post(
        "/api/feeding/dispense",
        json={"bucket": "FOUNDATION", "shift": "MORNING", "qty_kg": 5},
        headers=feeder,
    )
    assert resp.status_code == 201, resp.text
    resp = await client.post(
        "/api/feeding/settings",
        json={"bucket": "FOUNDATION", "daily_kg_per_head": 1.2},
        headers=feeder,
    )
    assert resp.status_code == 204, resp.text

    rid = await role_id(client, owner, "FEEDER")
    forbidden = [
        (
            "POST",
            "/api/animals",
            {
                "tag_number": "X-1",
                "sex": "F",
                "source": "PURCHASED",
                "current_bucket": "FOUNDATION",
            },
            "animals.create",
        ),
        ("POST", f"/api/animals/{animal_id}/move", {"to_bucket": "BREEDING"}, "animals.move"),
        ("POST", f"/api/animals/{animal_id}/weight", {"weight_kg": 12.5}, "animals.weight"),
        (
            "POST",
            "/api/health/events",
            {
                "scope": "animal",
                "animal_id": animal_id,
                "date": today().isoformat(),
                "type": "TREATMENT",
                "product_name": "X",
            },
            "health.manage",
        ),
        (
            "POST",
            "/api/tasks",
            {"title": "X", "due_date": today().isoformat(), "category": "OTHER"},
            "tasks.create",
        ),
        (
            "POST",
            "/api/finance/new",
            {"date": today().isoformat(), "type": "EXPENSE", "category": "OTHER", "amount": 100},
            "finance.manage",
        ),
        (
            "POST",
            "/api/team/workers",
            {"email": "w2@farm.in", "password": WORKER_PW, "role_id": rid},
            "team.manage",
        ),
    ]
    for method, url, body, perm in forbidden:
        resp = await client.request(method, url, json=body, headers=feeder)
        assert resp.status_code == 403, f"FEEDER {url} → {resp.status_code}"
        assert resp.json()["detail"] == f"Missing permission: {perm}"


# ---------------------------------------------------------------------------
# GET /api/auth/permissions accuracy
# ---------------------------------------------------------------------------
async def test_permissions_owner_has_everything(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    body = await permissions_of(client, owner)
    assert body["is_owner"] is True
    assert set(body["permissions"]) == ALL_PERMS
    assert body["permissions"] == sorted(body["permissions"])
    assert len(body["permissions"]) == 27  # full catalog size


@pytest.mark.parametrize("role_code", sorted(PRESET_PERMS))
async def test_permissions_match_preset_bundle(client: httpx.AsyncClient, role_code: str) -> None:
    owner = await owner_with_farm(client)
    worker = await worker_headers(client, owner, role_code, f"{role_code.lower()}@farm.in")
    body = await permissions_of(client, worker)
    assert body["is_owner"] is False
    assert set(body["permissions"]) == PRESET_PERMS[role_code]
    assert body["permissions"] == sorted(body["permissions"])


async def test_permissions_reflect_role_edit_immediately(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    cleaner = await worker_headers(client, owner, "CLEANER", "cleaner@farm.in")
    rid = await role_id(client, owner, "CLEANER")
    resp = await client.put(
        f"/api/team/roles/{rid}",
        json={
            "name": "Cleaner",
            "permissions": ["dashboard.view", "tasks.view", "tasks.complete", "tasks.verify"],
        },
        headers=owner,
    )
    assert resp.status_code == 200, resp.text
    body = await permissions_of(client, cleaner)
    assert set(body["permissions"]) == PRESET_PERMS["CLEANER"] | {"tasks.verify"}


async def test_permissions_reflect_role_change(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    worker = await worker_headers(client, owner, "CLEANER", "w@farm.in")
    mid = await membership_id(client, owner, "w@farm.in")
    mover_rid = await role_id(client, owner, "MOVER")
    resp = await client.post(
        f"/api/team/workers/{mid}/role", json={"role_id": mover_rid}, headers=owner
    )
    assert resp.status_code == 200, resp.text
    body = await permissions_of(client, worker)
    assert set(body["permissions"]) == PRESET_PERMS["MOVER"]


async def test_permissions_requires_auth_and_farm(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    assert (await client.get("/api/auth/permissions")).status_code == 401
    auth_only = {k: v for k, v in owner.items() if k == "Authorization"}
    resp = await client.get("/api/auth/permissions", headers=auth_only)
    # The header is required by the contract (422, not 400).
    assert resp.status_code == 422
    assert "x-farm-id" in str(resp.json()["detail"]).lower()


# ---------------------------------------------------------------------------
# Team page: seeded presets, catalogs, member counts, ordering
# ---------------------------------------------------------------------------
async def test_team_page_seeds_five_presets_with_exact_bundles(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    page = await team_page(client, owner)
    roles = {r["code"]: r for r in page["roles"]}
    assert set(roles) == set(PRESET_PERMS)
    for code, perms in PRESET_PERMS.items():
        role = roles[code]
        assert role["name"] == PRESET_NAMES[code]
        assert set(role["permissions"]) == perms
        assert role["description"]
        assert role["member_count"] == 0
    assert page["memberships"] == []  # the owner is NOT a membership


async def test_team_page_permission_catalog_complete(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    page = await team_page(client, owner)
    grouped = [code for g in page["permission_groups"] for code in g["codes"]]
    assert set(grouped) == ALL_PERMS
    assert len(grouped) == len(set(grouped))  # no code listed twice
    assert set(page["permission_labels"]) == ALL_PERMS
    assert all(label for label in page["permission_labels"].values())


async def test_team_page_member_counts_track_assignments(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    cleaner_rid = await role_id(client, owner, "CLEANER")
    mover_rid = await role_id(client, owner, "MOVER")
    assert (await add_worker(client, owner, cleaner_rid, "c1@farm.in")).status_code == 201
    assert (await add_worker(client, owner, cleaner_rid, "c2@farm.in")).status_code == 201
    assert (await add_worker(client, owner, mover_rid, "m1@farm.in")).status_code == 201

    page = await team_page(client, owner)
    counts = {r["code"]: r["member_count"] for r in page["roles"]}
    assert counts["CLEANER"] == 2
    assert counts["MOVER"] == 1
    assert counts["VET"] == 0

    # reassigning one cleaner moves the count
    mid = await membership_id(client, owner, "c2@farm.in")
    resp = await client.post(
        f"/api/team/workers/{mid}/role", json={"role_id": mover_rid}, headers=owner
    )
    assert resp.status_code == 200, resp.text
    page = await team_page(client, owner)
    counts = {r["code"]: r["member_count"] for r in page["roles"]}
    assert counts["CLEANER"] == 1
    assert counts["MOVER"] == 2


async def test_team_page_active_members_first_and_fields(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    cleaner_rid = await role_id(client, owner, "CLEANER")
    mover_rid = await role_id(client, owner, "MOVER")
    r1 = await add_worker(client, owner, cleaner_rid, "aa@farm.in", name="First")
    r2 = await add_worker(client, owner, mover_rid, "zz@farm.in", name="Last")
    assert r1.status_code == 201 and r2.status_code == 201

    mid_first = r1.json()["id"]
    resp = await client.post(f"/api/team/workers/{mid_first}/toggle", headers=owner)
    assert resp.status_code == 200, resp.text

    page = await team_page(client, owner)
    assert [m["email"] for m in page["memberships"]] == ["zz@farm.in", "aa@farm.in"]
    m = page["memberships"][0]
    assert m["name"] == "Last"
    assert m["role_name"] == "Animal Mover"
    assert m["role_id"] == mover_rid
    assert m["is_active"] is True
    assert page["memberships"][1]["is_active"] is False


# ---------------------------------------------------------------------------
# Worker invitation / creation
# ---------------------------------------------------------------------------
async def test_create_worker_happy_path(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    rid = await role_id(client, owner, "VET")
    resp = await add_worker(client, owner, rid, "vet@farm.in", name="Dr. Rao")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["email"] == "vet@farm.in"
    assert body["name"] == "Dr. Rao"
    assert body["role_id"] == rid
    assert body["role_name"] == "Veterinarian"
    assert body["is_active"] is True
    assert body["id"] > 0 and body["user_id"] > 0

    # he can log in with the password the owner set and sees the farm
    worker = await login_user(client, "vet@farm.in", WORKER_PW)
    resp = await client.get("/api/auth/farms", headers=worker)
    assert [f["name"] for f in resp.json()] == ["Alpha Farm"]
    assert resp.json()[0]["role"] == "Veterinarian"


async def test_create_worker_existing_account_requires_consent_flow(
    client: httpx.AsyncClient,
) -> None:
    """A farm cannot silently enroll even an otherwise unaffiliated account."""
    owner = await owner_with_farm(client)
    await register(client, email="free@farm.in", password="hisownpass1", name="Free Agent")
    rid = await role_id(client, owner, "FEEDER")
    resp = await add_worker(client, owner, rid, "free@farm.in", password="hijacked123")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "That email can't be added to this farm's team."
    await login_user(client, "free@farm.in", "hisownpass1")
    assert (
        await client.post(
            "/api/auth/login",
            json={"email": "free@farm.in", "password": "hijacked123"},
        )
    ).status_code == 401


async def test_create_worker_new_account_requires_password(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    rid = await role_id(client, owner, "CLEANER")
    resp = await add_worker(client, owner, rid, "new@farm.in", password=None)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Password must be at least 8 characters."


@pytest.mark.parametrize(
    ("password", "expected"),
    [
        ("short7c", 400),  # 7 chars — under the policy minimum
        ("exactly8", 201),  # 8 chars — exactly the minimum
        ("        ", 400),  # 8 whitespace chars — whitespace-only rejected
    ],
)
async def test_create_worker_password_boundaries(
    client: httpx.AsyncClient, password: str, expected: int
) -> None:
    owner = await owner_with_farm(client)
    rid = await role_id(client, owner, "CLEANER")
    resp = await add_worker(client, owner, rid, "w@farm.in", password=password)
    assert resp.status_code == expected, resp.text


@pytest.mark.parametrize(
    "email",
    ["no-at-sign", "", "   "],
)
async def test_create_worker_invalid_email_rejected(client: httpx.AsyncClient, email: str) -> None:
    owner = await owner_with_farm(client)
    rid = await role_id(client, owner, "CLEANER")
    resp = await add_worker(client, owner, rid, email)
    # EmailMixin schema validation: malformed emails 422 before
    # the router runs.
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({}, 422),  # everything missing
        ({"password": WORKER_PW, "role_id": 1}, 422),  # email missing
        ({"email": "w@farm.in", "password": WORKER_PW}, 422),  # role_id missing
        ({"email": 123, "password": WORKER_PW, "role_id": 1}, 422),  # wrong type
        ({"email": None, "password": WORKER_PW, "role_id": 1}, 422),
    ],
)
async def test_create_worker_schema_validation(
    client: httpx.AsyncClient, payload: dict, expected: int
) -> None:
    owner = await owner_with_farm(client)
    resp = await client.post("/api/team/workers", json=payload, headers=owner)
    assert resp.status_code == expected, resp.text


async def test_create_worker_email_normalized_lowercase(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    rid = await role_id(client, owner, "CLEANER")
    resp = await add_worker(client, owner, rid, "  MixedCase@Farm.IN  ")
    assert resp.status_code == 201, resp.text
    assert resp.json()["email"] == "mixedcase@farm.in"
    # duplicate check is case-insensitive too
    resp = await add_worker(client, owner, rid, "MIXEDCASE@farm.in")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "That email can't be added to this farm's team."


async def test_create_worker_duplicate_rejected(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    rid = await role_id(client, owner, "CLEANER")
    assert (await add_worker(client, owner, rid, "w@farm.in")).status_code == 201
    resp = await add_worker(client, owner, rid, "w@farm.in")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "That email can't be added to this farm's team."


async def test_create_worker_readd_after_deactivation_rejected(client: httpx.AsyncClient) -> None:
    """A deactivated membership still exists — re-adding must not duplicate it."""
    owner = await owner_with_farm(client)
    rid = await role_id(client, owner, "CLEANER")
    resp = await add_worker(client, owner, rid, "w@farm.in")
    assert resp.status_code == 201, resp.text
    mid = resp.json()["id"]
    resp = await client.post(f"/api/team/workers/{mid}/toggle", headers=owner)
    assert resp.status_code == 200, resp.text
    resp = await add_worker(client, owner, rid, "w@farm.in")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "That email can't be added to this farm's team."


async def test_create_worker_cannot_add_farm_owner(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    rid = await role_id(client, owner, "CLEANER")
    resp = await add_worker(client, owner, rid, "owner@farm.in")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "That email can't be added to this farm's team."


async def test_create_worker_cannot_absorb_other_farms_owner(client: httpx.AsyncClient) -> None:
    """Accounts (and passwords) are global: an account that owns any farm is off-limits."""
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Alpha Farm")
    await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    rid = await role_id(client, owner_a, "CLEANER")
    resp = await add_worker(client, owner_a, rid, "b@farm.in")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "That email can't be added to this farm's team."


async def test_create_worker_cannot_add_worker_who_later_owned_a_farm(
    client: httpx.AsyncClient,
) -> None:
    """A worker who registers his own farm afterwards can't be re-added elsewhere."""
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Alpha Farm")
    rid = await role_id(client, owner_a, "CLEANER")
    resp = await add_worker(client, owner_a, rid, "w@farm.in")
    assert resp.status_code == 201, resp.text
    # worker founds his own farm
    worker = await login_user(client, "w@farm.in", WORKER_PW)
    resp = await client.post("/api/auth/farms", json={"name": "His Farm"}, headers=worker)
    assert resp.status_code == 201, resp.text
    # another farm can't absorb him anymore
    owner_c = await owner_with_farm(client, email="c@farm.in", farm_name="Gamma Farm")
    rid_c = await role_id(client, owner_c, "CLEANER")
    resp = await add_worker(client, owner_c, rid_c, "w@farm.in")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "That email can't be added to this farm's team."


async def test_create_worker_role_must_exist_on_this_farm(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Alpha Farm")
    owner_b = await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    rid_b = await role_id(client, owner_b, "CLEANER")

    resp = await add_worker(client, owner_a, 999_999, "w@farm.in")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Pick a valid role."

    # another farm's role id is equally invalid here (no cross-tenant leak)
    resp = await add_worker(client, owner_a, rid_b, "w@farm.in")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Pick a valid role."


@pytest.mark.parametrize(
    ("role_id", "expected"),
    [
        (0, 422),
        (-1, 422),
        (2**62 + 1, 422),  # beyond the BoundedId ceiling
        ("abc", 422),
        (1.5, 422),
        (None, 422),
        (True, 201),  # pydantic lax mode coerces True → 1 (an existing role)
    ],
)
async def test_create_worker_role_id_boundaries(
    client: httpx.AsyncClient, role_id: object, expected: int
) -> None:
    owner = await owner_with_farm(client)
    resp = await client.post(
        "/api/team/workers",
        json={"email": "w@farm.in", "password": WORKER_PW, "role_id": role_id},
        headers=owner,
    )
    assert resp.status_code == expected, resp.text


@pytest.mark.parametrize(
    ("name", "stored"),
    [
        (None, None),
        ("", None),
        ("   ", None),  # whitespace-only collapses to null
        ("  Padded Name  ", "Padded Name"),
        ("రాము యాదవ్", "రాము యాదవ్"),  # Telugu unicode
        ("Worker 🐐", "Worker 🐐"),  # emoji
        ("O'Brien-Smith", "O'Brien-Smith"),
        ("Robert'); DROP TABLE users;--", "Robert'); DROP TABLE users;--"),
        ("x" * 120, "x" * 120),  # max_length boundary
    ],
)
async def test_create_worker_name_handling(
    client: httpx.AsyncClient, name: str | None, stored: str | None
) -> None:
    owner = await owner_with_farm(client)
    rid = await role_id(client, owner, "CLEANER")
    payload: dict = {"email": "w@farm.in", "password": WORKER_PW, "role_id": rid, "name": name}
    resp = await client.post("/api/team/workers", json=payload, headers=owner)
    assert resp.status_code == 201, resp.text
    assert resp.json()["name"] == stored
    # the team page still renders (injection strings are inert data)
    page = await team_page(client, owner)
    assert page["memberships"][0]["name"] == stored


@pytest.mark.parametrize("name", ["x" * 121, "y" * 10_000])
async def test_create_worker_name_too_long(client: httpx.AsyncClient, name: str) -> None:
    owner = await owner_with_farm(client)
    rid = await role_id(client, owner, "CLEANER")
    resp = await add_worker(client, owner, rid, "w@farm.in", name=name)
    assert resp.status_code == 422, resp.status_code


async def test_create_worker_unknown_extra_fields_ignored(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    rid = await role_id(client, owner, "CLEANER")
    resp = await client.post(
        "/api/team/workers",
        json={
            "email": "w@farm.in",
            "password": WORKER_PW,
            "role_id": rid,
            "is_admin": True,  # not a real field — must not grant anything
            "role": "owner",
        },
        headers=owner,
    )
    assert resp.status_code == 201, resp.text
    worker = await login_user(client, "w@farm.in", WORKER_PW)
    body = await permissions_of(client, worker | {"X-Farm-Id": owner["X-Farm-Id"]})
    assert set(body["permissions"]) == PRESET_PERMS["CLEANER"]


async def test_create_worker_email_sql_injection_is_inert(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    rid = await role_id(client, owner, "CLEANER")
    evil = "sqli@farm.in' OR '1'='1"
    resp = await add_worker(client, owner, rid, evil)
    assert resp.status_code == 422, resp.text
    # Rejection is validation, not SQL execution; the team table still works.
    assert (await client.get("/api/team", headers=owner)).status_code == 200


# ---------------------------------------------------------------------------
# Worker role change
# ---------------------------------------------------------------------------
async def test_change_role_happy_path(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    worker = await worker_headers(client, owner, "CLEANER", "w@farm.in")
    mid = await membership_id(client, owner, "w@farm.in")
    vet_rid = await role_id(client, owner, "VET")

    resp = await client.post(
        f"/api/team/workers/{mid}/role", json={"role_id": vet_rid}, headers=owner
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["role_id"] == vet_rid
    assert body["role_name"] == "Veterinarian"

    # effective immediately: health opens, team page reflects it
    assert (await client.get("/api/health/events", headers=worker)).status_code == 200
    page = await team_page(client, owner)
    m = next(m for m in page["memberships"] if m["email"] == "w@farm.in")
    assert m["role_name"] == "Veterinarian"


async def test_change_role_rejects_bad_role(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Alpha Farm")
    owner_b = await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    await worker_headers(client, owner_a, "CLEANER", "w@farm.in")
    mid = await membership_id(client, owner_a, "w@farm.in")
    rid_b = await role_id(client, owner_b, "VET")

    for bad_rid in (999_999, rid_b):
        resp = await client.post(
            f"/api/team/workers/{mid}/role", json={"role_id": bad_rid}, headers=owner_a
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Pick a valid role."


async def test_change_role_membership_not_found(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Alpha Farm")
    owner_b = await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    await worker_headers(client, owner_b, "CLEANER", "w@farm.in")
    mid_b = await membership_id(client, owner_b, "w@farm.in")
    rid_a = await role_id(client, owner_a, "VET")

    # nonexistent and cross-farm membership ids both 404 — never a leak
    for bad_mid in (999_999, mid_b):
        resp = await client.post(
            f"/api/team/workers/{bad_mid}/role", json={"role_id": rid_a}, headers=owner_a
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Membership not found"


@pytest.mark.parametrize(
    ("role_id", "expected"),
    [(0, 422), (-3, 422), (2**62 + 1, 422), ("vet", 422), (None, 422)],
)
async def test_change_role_schema_validation(
    client: httpx.AsyncClient, role_id: object, expected: int
) -> None:
    owner = await owner_with_farm(client)
    await worker_headers(client, owner, "CLEANER", "w@farm.in")
    mid = await membership_id(client, owner, "w@farm.in")
    resp = await client.post(
        f"/api/team/workers/{mid}/role", json={"role_id": role_id}, headers=owner
    )
    assert resp.status_code == expected, resp.text


@pytest.mark.parametrize("mid", ["abc", "1.5"])
async def test_membership_id_path_must_be_int(client: httpx.AsyncClient, mid: str) -> None:
    owner = await owner_with_farm(client)
    resp = await client.post(f"/api/team/workers/{mid}/toggle", headers=owner)
    assert resp.status_code == 422, resp.status_code


# ---------------------------------------------------------------------------
# Worker toggle (deactivate / reactivate) & access revocation
# ---------------------------------------------------------------------------
async def test_deactivation_revokes_all_access(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    mover = await worker_headers(client, owner, "MOVER", "mover@farm.in")
    mid = await membership_id(client, owner, "mover@farm.in")

    resp = await client.post(f"/api/team/workers/{mid}/toggle", headers=owner)
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_active"] is False

    # Refresh sessions and already-issued bearer tokens both die immediately.
    for url in [
        "/api/tasks",
        "/api/animals",
        "/api/dashboard",
        "/api/auth/permissions",
        "/api/auth/farms",
        "/api/auth/me",
    ]:
        resp = await client.get(url, headers=mover)
        assert resp.status_code == 401, url
        assert resp.json()["detail"] == "Session has been revoked"


async def test_reactivation_restores_access(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    mover = await worker_headers(client, owner, "MOVER", "mover@farm.in")
    mid = await membership_id(client, owner, "mover@farm.in")

    for expected_active in (False, True):
        resp = await client.post(f"/api/team/workers/{mid}/toggle", headers=owner)
        assert resp.status_code == 200, resp.text
        assert resp.json()["is_active"] is expected_active

    # Reactivation restores account eligibility, but never resurrects a token
    # explicitly revoked by the deactivation security event.
    assert (await client.get("/api/animals", headers=mover)).status_code == 401
    fresh = await login_user(client, "mover@farm.in", WORKER_PW)
    fresh["X-Farm-Id"] = owner["X-Farm-Id"]
    assert (await client.get("/api/animals", headers=fresh)).status_code == 200
    body = await permissions_of(client, fresh)
    assert set(body["permissions"]) == PRESET_PERMS["MOVER"]


async def test_worker_cannot_toggle_own_membership(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    tm, mid = await team_manager_headers(client, owner)
    resp = await client.post(f"/api/team/workers/{mid}/toggle", headers=tm)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "You cannot deactivate your own membership."


async def test_team_manager_can_toggle_other_workers(client: httpx.AsyncClient) -> None:
    """team.manage is delegable: a non-owner holder may deactivate other workers."""
    owner = await owner_with_farm(client)
    tm, _ = await team_manager_headers(client, owner)
    await worker_headers(client, owner, "CLEANER", "w@farm.in")
    mid = await membership_id(client, owner, "w@farm.in")
    resp = await client.post(f"/api/team/workers/{mid}/toggle", headers=tm)
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_active"] is False


async def test_deactivation_reassigns_pending_personal_duties_to_worker_role(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    cleaner_role = await role_id(client, owner, "CLEANER")
    feeder_role = await role_id(client, owner, "FEEDER")
    added = await add_worker(client, owner, cleaner_role, "w@farm.in")
    assert added.status_code == 201, added.text
    worker_id = added.json()["user_id"]
    membership = added.json()["id"]

    personal = await client.post(
        "/api/tasks",
        json={
            "title": "Personal pending duty",
            "due_date": today().isoformat(),
            "assigned_user_id": worker_id,
        },
        headers=owner,
    )
    explicit = await client.post(
        "/api/tasks",
        json={
            "title": "Explicit role duty",
            "due_date": today().isoformat(),
            "assigned_user_id": worker_id,
            "assigned_role_id": feeder_role,
        },
        headers=owner,
    )
    assert personal.status_code == explicit.status_code == 201

    resp = await client.post(f"/api/team/workers/{membership}/toggle", headers=owner)
    assert resp.status_code == 200, resp.text
    page = (await client.get("/api/tasks", headers=owner)).json()
    rows = [row for value in page.values() if isinstance(value, list) for row in value]
    personal_after = next(row for row in rows if row["id"] == personal.json()["id"])
    explicit_after = next(row for row in rows if row["id"] == explicit.json()["id"])
    assert personal_after["assigned_user_id"] is None
    assert personal_after["assigned_role_id"] == cleaner_role
    assert explicit_after["assigned_user_id"] is None
    assert explicit_after["assigned_role_id"] == feeder_role


async def test_toggle_membership_not_found(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Alpha Farm")
    owner_b = await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    await worker_headers(client, owner_b, "CLEANER", "w@farm.in")
    mid_b = await membership_id(client, owner_b, "w@farm.in")
    for bad_mid in (999_999, mid_b, -1):
        resp = await client.post(f"/api/team/workers/{bad_mid}/toggle", headers=owner_a)
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Membership not found"


# ---------------------------------------------------------------------------
# Worker password reset
# ---------------------------------------------------------------------------
async def test_reset_capability_is_accurate_on_every_membership_response(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    cleaner = await role_id(client, owner, "CLEANER")
    mover = await role_id(client, owner, "MOVER")

    created = await add_worker(client, owner, cleaner, "capability@farm.in")
    assert created.status_code == 201, created.text
    membership_id = created.json()["id"]
    assert created.json()["can_reset_password"] is True
    assert created.json()["reset_password_block_reason"] is None

    changed = await client.post(
        f"/api/team/workers/{membership_id}/role",
        json={"role_id": mover},
        headers=owner,
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["can_reset_password"] is True
    assert changed.json()["reset_password_block_reason"] is None

    deactivated = await client.post(f"/api/team/workers/{membership_id}/toggle", headers=owner)
    assert deactivated.status_code == 200, deactivated.text
    assert deactivated.json()["can_reset_password"] is False
    assert (
        deactivated.json()["reset_password_block_reason"]
        == "Reactivate this membership before resetting the password."
    )

    reactivated = await client.post(f"/api/team/workers/{membership_id}/toggle", headers=owner)
    assert reactivated.status_code == 200, reactivated.text
    assert reactivated.json()["can_reset_password"] is True
    assert reactivated.json()["reset_password_block_reason"] is None

    reset = await client.post(
        f"/api/team/workers/{membership_id}/reset-password",
        json={"password": "brandnewpass1"},
        headers=owner,
    )
    assert reset.status_code == 200, reset.text
    assert reset.json()["can_reset_password"] is True
    assert reset.json()["reset_password_block_reason"] is None


async def test_team_page_reset_capability_is_private_and_complete(
    client: httpx.AsyncClient,
) -> None:
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Alpha Farm")
    owner_b = await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    cleaner_a = await role_id(client, owner_a, "CLEANER")
    cleaner_b = await role_id(client, owner_b, "CLEANER")

    eligible = await add_worker(client, owner_a, cleaner_a, "eligible@farm.in")
    inactive = await add_worker(client, owner_a, cleaner_a, "inactive@farm.in")
    owns_farm = await add_worker(client, owner_a, cleaner_a, "owner-worker@farm.in")
    cross_farm = await add_worker(client, owner_a, cleaner_a, "cross-worker@farm.in")
    for response in (eligible, inactive, owns_farm, cross_farm):
        assert response.status_code == 201, response.text

    toggled = await client.post(
        f"/api/team/workers/{inactive.json()['id']}/toggle", headers=owner_a
    )
    assert toggled.status_code == 200, toggled.text

    owner_worker_auth = await login_user(client, "owner-worker@farm.in", WORKER_PW)
    created_farm = await client.post(
        "/api/auth/farms", json={"name": "Worker-owned Farm"}, headers=owner_worker_auth
    )
    assert created_farm.status_code == 201, created_farm.text

    await register(client, "legacy-worker@farm.in", WORKER_PW)
    async with get_sessionmaker()() as db:
        users = {
            row.email: row
            for row in (
                await db.execute(
                    select(User).where(
                        User.email.in_(["legacy-worker@farm.in", "cross-worker@farm.in"])
                    )
                )
            ).scalars()
        }
        legacy_membership = FarmMembership(
            farm_id=int(owner_a["X-Farm-Id"]),
            user_id=users["legacy-worker@farm.in"].id,
            role_id=cleaner_a,
            is_active=True,
            account_provisioned_by_farm=False,
        )
        db.add_all(
            [
                legacy_membership,
                FarmMembership(
                    farm_id=int(owner_b["X-Farm-Id"]),
                    user_id=users["cross-worker@farm.in"].id,
                    role_id=cleaner_b,
                    is_active=True,
                    account_provisioned_by_farm=False,
                ),
            ]
        )
        await db.commit()
        legacy_membership_id = legacy_membership.id

    memberships = {row["email"]: row for row in (await team_page(client, owner_a))["memberships"]}
    assert memberships["eligible@farm.in"]["can_reset_password"] is True
    assert memberships["eligible@farm.in"]["reset_password_block_reason"] is None
    assert memberships["inactive@farm.in"]["can_reset_password"] is False
    assert (
        memberships["inactive@farm.in"]["reset_password_block_reason"]
        == "Reactivate this membership before resetting the password."
    )

    generic_reason = "This account must use self-service password recovery."
    for email in (
        "legacy-worker@farm.in",
        "owner-worker@farm.in",
        "cross-worker@farm.in",
    ):
        assert memberships[email]["can_reset_password"] is False
        assert memberships[email]["reset_password_block_reason"] == generic_reason

    changed = await client.post(
        f"/api/team/workers/{cross_farm.json()['id']}/role",
        json={"role_id": cleaner_a},
        headers=owner_a,
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["can_reset_password"] is False
    assert changed.json()["reset_password_block_reason"] == generic_reason

    cross_inactive = await client.post(
        f"/api/team/workers/{cross_farm.json()['id']}/toggle", headers=owner_a
    )
    assert cross_inactive.status_code == 200, cross_inactive.text
    assert cross_inactive.json()["can_reset_password"] is False
    assert (
        cross_inactive.json()["reset_password_block_reason"]
        == "Reactivate this membership before resetting the password."
    )
    cross_reactivated = await client.post(
        f"/api/team/workers/{cross_farm.json()['id']}/toggle", headers=owner_a
    )
    assert cross_reactivated.status_code == 200, cross_reactivated.text
    assert cross_reactivated.json()["can_reset_password"] is False
    assert cross_reactivated.json()["reset_password_block_reason"] == generic_reason

    # The endpoint uses the same generic response for all global-affiliation
    # cases, so it cannot be used to distinguish another farm's roster/owner.
    blocked_ids = (
        legacy_membership_id,
        owns_farm.json()["id"],
        cross_farm.json()["id"],
    )
    for membership_id in blocked_ids:
        response = await client.post(
            f"/api/team/workers/{membership_id}/reset-password",
            json={"password": "brandnewpass1"},
            headers=owner_a,
        )
        assert response.status_code == 400
        assert response.json()["detail"] == generic_reason


async def test_team_page_reset_eligibility_has_constant_query_count(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    cleaner = await role_id(client, owner, "CLEANER")
    first = await add_worker(client, owner, cleaner, "bounded-0@farm.in")
    assert first.status_code == 201, first.text

    async def counted_team_request() -> int:
        statements: list[str] = []

        def capture_statement(
            _conn: object,
            _cursor: object,
            statement: str,
            _parameters: object,
            _context: object,
            _executemany: object,
        ) -> None:
            statements.append(statement)

        engine = get_engine().sync_engine
        event.listen(engine, "before_cursor_execute", capture_statement)
        try:
            response = await client.get("/api/team", headers=owner)
            assert response.status_code == 200, response.text
        finally:
            event.remove(engine, "before_cursor_execute", capture_statement)
        return len(statements)

    baseline = await counted_team_request()
    for index in range(1, 9):
        response = await add_worker(client, owner, cleaner, f"bounded-{index}@farm.in")
        assert response.status_code == 201, response.text
    expanded = await counted_team_request()
    assert expanded == baseline


async def test_reset_password_happy_path(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await worker_headers(client, owner, "CLEANER", "w@farm.in")
    mid = await membership_id(client, owner, "w@farm.in")

    resp = await client.post(
        f"/api/team/workers/{mid}/reset-password",
        json={"password": "brandnewpass1"},
        headers=owner,
    )
    assert resp.status_code == 200, resp.text

    # old password dies, new one works
    resp = await client.post("/api/auth/login", json={"email": "w@farm.in", "password": WORKER_PW})
    assert resp.status_code == 401
    worker = await login_user(client, "w@farm.in", "brandnewpass1")
    assert (
        await client.get("/api/tasks", headers=worker | {"X-Farm-Id": owner["X-Farm-Id"]})
    ).status_code == 200


async def test_reset_password_requires_active_membership(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await worker_headers(client, owner, "CLEANER", "w@farm.in")
    mid = await membership_id(client, owner, "w@farm.in")
    resp = await client.post(f"/api/team/workers/{mid}/toggle", headers=owner)
    assert resp.status_code == 200, resp.text

    resp = await client.post(
        f"/api/team/workers/{mid}/reset-password",
        json={"password": "brandnewpass1"},
        headers=owner,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Reactivate this membership before resetting the password."


async def test_reset_password_never_touches_a_farm_owner(client: httpx.AsyncClient) -> None:
    """Worker founds his own farm → his password is global; resets here are blocked."""
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Alpha Farm")
    await worker_headers(client, owner_a, "CLEANER", "w@farm.in")
    mid = await membership_id(client, owner_a, "w@farm.in")
    worker = await login_user(client, "w@farm.in", WORKER_PW)
    resp = await client.post("/api/auth/farms", json={"name": "His Farm"}, headers=worker)
    assert resp.status_code == 201, resp.text

    resp = await client.post(
        f"/api/team/workers/{mid}/reset-password",
        json={"password": "hijacked123"},
        headers=owner_a,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "This account must use self-service password recovery."
    # and his password is indeed unchanged
    await login_user(client, "w@farm.in", WORKER_PW)


@pytest.mark.parametrize(
    ("password", "expected"),
    [
        ("short7c", 400),  # policy minimum
        ("        ", 400),  # whitespace-only
        ("", 422),  # schema min_length=1
        (None, 422),
    ],
)
async def test_reset_password_validation(
    client: httpx.AsyncClient, password: object, expected: int
) -> None:
    owner = await owner_with_farm(client)
    await worker_headers(client, owner, "CLEANER", "w@farm.in")
    mid = await membership_id(client, owner, "w@farm.in")
    resp = await client.post(
        f"/api/team/workers/{mid}/reset-password",
        json={"password": password},
        headers=owner,
    )
    assert resp.status_code == expected, resp.text


async def test_reset_password_missing_body_field(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await worker_headers(client, owner, "CLEANER", "w@farm.in")
    mid = await membership_id(client, owner, "w@farm.in")
    resp = await client.post(f"/api/team/workers/{mid}/reset-password", json={}, headers=owner)
    assert resp.status_code == 422


async def test_reset_password_membership_not_found(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Alpha Farm")
    owner_b = await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    await worker_headers(client, owner_b, "CLEANER", "w@farm.in")
    mid_b = await membership_id(client, owner_b, "w@farm.in")
    for bad_mid in (999_999, mid_b):
        resp = await client.post(
            f"/api/team/workers/{bad_mid}/reset-password",
            json={"password": "brandnewpass1"},
            headers=owner_a,
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Membership not found"


async def test_reset_password_revokes_worker_sessions(client: httpx.AsyncClient) -> None:
    """The owner's reset is the incident-response tool for a
    compromised worker account — it must kill the worker's live refresh
    sessions, not just rewrite the hash."""
    owner = await owner_with_farm(client)
    await worker_headers(client, owner, "CLEANER", "w@farm.in")  # jar: worker cookie
    mid = await membership_id(client, owner, "w@farm.in")
    worker_cookie = client.cookies.get(COOKIE)
    assert worker_cookie

    resp = await client.post(
        f"/api/team/workers/{mid}/reset-password",
        json={"password": "brandnewpass1"},
        headers=owner,
    )
    assert resp.status_code == 200, resp.text

    client.cookies.clear()
    client.cookies.set(COOKIE, worker_cookie)
    assert (await client.post("/api/auth/refresh")).status_code == 401
    # the worker signs in with the new password and gets a working session
    client.cookies.clear()
    await login_user(client, "w@farm.in", "brandnewpass1")
    assert (await client.post("/api/auth/refresh")).status_code == 200


async def test_toggle_deactivation_revokes_worker_sessions(client: httpx.AsyncClient) -> None:
    """Deactivating a membership ends the worker's sessions too;
    reactivation lets him sign in fresh."""
    owner = await owner_with_farm(client)
    await worker_headers(client, owner, "CLEANER", "w@farm.in")
    mid = await membership_id(client, owner, "w@farm.in")
    worker_cookie = client.cookies.get(COOKIE)
    assert worker_cookie

    resp = await client.post(f"/api/team/workers/{mid}/toggle", headers=owner)
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_active"] is False

    client.cookies.clear()
    client.cookies.set(COOKIE, worker_cookie)
    assert (await client.post("/api/auth/refresh")).status_code == 401

    resp = await client.post(f"/api/team/workers/{mid}/toggle", headers=owner)  # reactivate
    assert resp.status_code == 200, resp.text
    client.cookies.clear()
    await login_user(client, "w@farm.in", WORKER_PW)
    assert (await client.post("/api/auth/refresh")).status_code == 200


# ---------------------------------------------------------------------------
# Custom role CRUD
# ---------------------------------------------------------------------------
async def test_create_custom_role_happy_path(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await client.post(
        "/api/team/roles",
        json={
            "name": "Night Watchman",
            "description": "  Guards the shed overnight.  ",
            "permissions": ["dashboard.view", "tasks.view"],
        },
        headers=owner,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["code"] is None  # custom roles carry no preset code
    assert body["name"] == "Night Watchman"
    assert body["description"] == "Guards the shed overnight."  # trimmed
    assert body["permissions"] == ["dashboard.view", "tasks.view"]
    assert body["member_count"] == 0

    page = await team_page(client, owner)
    assert any(r["name"] == "Night Watchman" and r["code"] is None for r in page["roles"])


async def test_create_role_permissions_filtered_and_ordered(client: httpx.AsyncClient) -> None:
    """Unknown codes are dropped; valid ones come back in catalog order."""
    owner = await owner_with_farm(client)
    resp = await client.post(
        "/api/team/roles",
        json={
            "name": "Odd Role",
            "permissions": [
                "tasks.view",
                "root.all",  # unknown code
                "dashboard.view",
                "tasks.view",  # duplicate
                "ANIMALS.VIEW",  # wrong case = unknown
                "",
            ],
        },
        headers=owner,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["permissions"] == ["dashboard.view", "tasks.view"]


async def test_create_role_with_full_catalog(client: httpx.AsyncClient) -> None:
    """The owner holds every permission, so he may grant any of them."""
    owner = await owner_with_farm(client)
    role = await create_custom_role(client, owner, "Deputy Owner", sorted(ALL_PERMS))
    assert set(role["permissions"]) == ALL_PERMS
    # a worker holding it can open everything, including the team page
    resp = await add_worker(client, owner, role["id"], "deputy@farm.in")
    assert resp.status_code == 201, resp.text
    deputy = await login_user(client, "deputy@farm.in", WORKER_PW)
    deputy = deputy | {"X-Farm-Id": owner["X-Farm-Id"]}
    for url, _perm in GET_ENDPOINTS:
        resp = await client.get(url, headers=deputy)
        assert resp.status_code == 200, f"deputy GET {url} → {resp.status_code}"
    body = await permissions_of(client, deputy)
    assert body["is_owner"] is False
    assert set(body["permissions"]) == ALL_PERMS


async def test_create_role_duplicate_name_rejected(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await create_custom_role(client, owner, "Helper", [])
    resp = await client.post(
        "/api/team/roles", json={"name": "Helper", "permissions": []}, headers=owner
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "A role with that name already exists."

    # preset names are taken too
    resp = await client.post(
        "/api/team/roles", json={"name": "Veterinarian", "permissions": []}, headers=owner
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "A role with that name already exists."


async def test_create_role_same_name_on_different_farms_ok(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Alpha Farm")
    owner_b = await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    role_a = await create_custom_role(client, owner_a, "Helper", ["tasks.view"])
    role_b = await create_custom_role(client, owner_b, "Helper", ["tasks.view"])
    assert role_a["id"] != role_b["id"]
    # farm isolation: each farm sees only its own copy
    page_a = await team_page(client, owner_a)
    assert [r["id"] for r in page_a["roles"] if r["name"] == "Helper"] == [role_a["id"]]


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({}, 422),  # name missing
        ({"name": ""}, 422),  # schema min_length=1
        ({"name": "   "}, 400),  # passes schema, rejected after strip
        ({"name": None}, 422),
        ({"name": 42}, 422),
        ({"name": "x" * 80}, 201),  # max_length boundary
        ({"name": "x" * 81}, 422),
        ({"name": "x" * 10_000}, 422),
        ({"name": "R", "description": "d" * 255}, 201),
        ({"name": "R", "description": "d" * 256}, 422),
        ({"name": "R", "permissions": "tasks.view"}, 422),  # must be a list
        ({"name": "R", "permissions": [42]}, 422),  # v2 does not coerce int → str
        ({"name": "R", "permissions": None}, 422),
    ],
)
async def test_create_role_validation(
    client: httpx.AsyncClient, payload: dict, expected: int
) -> None:
    owner = await owner_with_farm(client)
    resp = await client.post("/api/team/roles", json=payload, headers=owner)
    assert resp.status_code == expected, resp.text


async def test_create_role_unicode_and_injection_names(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    for name in ["రాత్రి కాపలాదారు 🐐", "Role'); DROP TABLE roles;--"]:
        resp = await client.post(
            "/api/team/roles", json={"name": name, "permissions": []}, headers=owner
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["name"] == name
    page = await team_page(client, owner)
    assert len(page["roles"]) == 7  # 5 presets + 2 custom


async def test_update_role_happy_path(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    role = await create_custom_role(client, owner, "Helper", ["tasks.view"], description="d")
    resp = await client.put(
        f"/api/team/roles/{role['id']}",
        json={
            "name": "Senior Helper",
            "description": "  upgraded  ",
            "permissions": ["tasks.view", "tasks.complete"],
        },
        headers=owner,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "Senior Helper"
    assert body["description"] == "upgraded"
    assert body["permissions"] == ["tasks.view", "tasks.complete"]
    assert body["code"] is None


async def test_update_preset_role_keeps_code(client: httpx.AsyncClient) -> None:
    """Preset name/description/permissions are editable; the stable code is not."""
    owner = await owner_with_farm(client)
    rid = await role_id(client, owner, "FEEDER")
    resp = await client.put(
        f"/api/team/roles/{rid}",
        json={
            "name": "Feed Supervisor",
            "description": "renamed",
            "permissions": [
                "dashboard.view",
                "feeding.view",
                "feeding.manage",
                "buckets.view",
                "tasks.view",
                "tasks.complete",
                "reports.view",
            ],
        },
        headers=owner,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "Feed Supervisor"
    assert body["code"] == "FEEDER"  # stable key survives edits
    assert "reports.view" in body["permissions"]


async def test_update_role_takes_effect_immediately(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    feeder = await worker_headers(client, owner, "FEEDER", "feeder@farm.in")
    rid = await role_id(client, owner, "FEEDER")

    # strip feeding.manage → dispensing stops working on the next request
    resp = await client.put(
        f"/api/team/roles/{rid}",
        json={
            "name": "Feeder",
            "permissions": [
                "dashboard.view",
                "feeding.view",
                "buckets.view",
                "tasks.view",
                "tasks.complete",
            ],
        },
        headers=owner,
    )
    assert resp.status_code == 200, resp.text
    resp = await client.post(
        "/api/feeding/dispense",
        json={"bucket": "FOUNDATION", "shift": "MORNING", "qty_kg": 5},
        headers=feeder,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Missing permission: feeding.manage"


async def test_update_role_name_conflicts(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    role = await create_custom_role(client, owner, "Helper", [])

    # renaming to a preset's name clashes
    resp = await client.put(
        f"/api/team/roles/{role['id']}",
        json={"name": "Cleaner", "permissions": []},
        headers=owner,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Name is required and must be unique on this farm."

    # blank name rejected
    resp = await client.put(
        f"/api/team/roles/{role['id']}", json={"name": "  ", "permissions": []}, headers=owner
    )
    assert resp.status_code == 400

    # keeping its own name is fine
    resp = await client.put(
        f"/api/team/roles/{role['id']}", json={"name": "Helper", "permissions": []}, headers=owner
    )
    assert resp.status_code == 200, resp.text


async def test_update_role_not_found(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Alpha Farm")
    owner_b = await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    role_b = await create_custom_role(client, owner_b, "Helper", [])
    rid_b = await role_id(client, owner_b, "VET")

    for bad_rid in (999_999, role_b["id"], rid_b, -1):
        resp = await client.put(
            f"/api/team/roles/{bad_rid}",
            json={"name": "Hijack", "permissions": []},
            headers=owner_a,
        )
        assert resp.status_code == 404, f"role {bad_rid} → {resp.status_code}"
        assert resp.json()["detail"] == "Role not found"


@pytest.mark.parametrize("rid", ["abc", "2.5"])
async def test_role_id_path_must_be_int(client: httpx.AsyncClient, rid: str) -> None:
    owner = await owner_with_farm(client)
    assert (
        await client.put(f"/api/team/roles/{rid}", json={"name": "X"}, headers=owner)
    ).status_code == 422
    assert (await client.delete(f"/api/team/roles/{rid}", headers=owner)).status_code == 422


async def test_delete_custom_role(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    role = await create_custom_role(client, owner, "Temp", [])
    resp = await client.delete(f"/api/team/roles/{role['id']}", headers=owner)
    assert resp.status_code == 204, resp.text
    assert resp.content == b""
    page = await team_page(client, owner)
    assert all(r["id"] != role["id"] for r in page["roles"])
    # second delete → 404
    resp = await client.delete(f"/api/team/roles/{role['id']}", headers=owner)
    assert resp.status_code == 404


@pytest.mark.parametrize("code", sorted(PRESET_PERMS))
async def test_delete_preset_role_rejected(client: httpx.AsyncClient, code: str) -> None:
    """Presets are re-seeded at startup, so deleting one is refused (SPEC)."""
    owner = await owner_with_farm(client)
    rid = await role_id(client, owner, code)
    resp = await client.delete(f"/api/team/roles/{rid}", headers=owner)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Preset roles can't be deleted."


async def test_delete_role_with_members_rejected_then_allowed(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    role = await create_custom_role(client, owner, "Helper", ["tasks.view"])
    mover_rid = await role_id(client, owner, "MOVER")
    resp = await add_worker(client, owner, role["id"], "w@farm.in")
    assert resp.status_code == 201, resp.text
    mid = resp.json()["id"]

    resp = await client.delete(f"/api/team/roles/{role['id']}", headers=owner)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Role still has workers assigned — reassign them first."

    # even an inactive member blocks deletion
    resp = await client.post(f"/api/team/workers/{mid}/toggle", headers=owner)
    assert resp.status_code == 200, resp.text
    resp = await client.delete(f"/api/team/roles/{role['id']}", headers=owner)
    assert resp.status_code == 400

    # reassign → deletion succeeds
    resp = await client.post(
        f"/api/team/workers/{mid}/role", json={"role_id": mover_rid}, headers=owner
    )
    assert resp.status_code == 200, resp.text
    resp = await client.delete(f"/api/team/roles/{role['id']}", headers=owner)
    assert resp.status_code == 204, resp.text


async def test_delete_role_unassigns_duties(client: httpx.AsyncClient) -> None:
    """Historical task attribution stays readable: duties are unassigned, not dangled."""
    owner = await owner_with_farm(client)
    role = await create_custom_role(client, owner, "Helper", ["tasks.view"])
    duty = await create_duty(client, owner, "Odd job", rid=role["id"])
    assert duty["assigned_role_id"] == role["id"]

    resp = await client.delete(f"/api/team/roles/{role['id']}", headers=owner)
    assert resp.status_code == 204, resp.text

    resp = await client.get("/api/tasks", headers=owner)
    assert resp.status_code == 200, resp.text
    tasks = [task for rows in resp.json().values() if isinstance(rows, list) for task in rows]
    task = next(t for t in tasks if t["id"] == duty["id"])
    assert task["assigned_role_id"] is None
    assert task["status"] == "PENDING"


async def test_delete_role_not_found(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Alpha Farm")
    owner_b = await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    role_b = await create_custom_role(client, owner_b, "Helper", [])
    for bad_rid in (999_999, role_b["id"]):
        resp = await client.delete(f"/api/team/roles/{bad_rid}", headers=owner_a)
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Role not found"


async def test_role_fk_is_restrict_and_delete_assignment_race_never_drops_membership(
    client: httpx.AsyncClient,
) -> None:
    async with get_sessionmaker()() as db:
        delete_rule = (
            await db.execute(
                text(
                    "SELECT confdeltype FROM pg_constraint "
                    "WHERE conname = 'farm_memberships_role_id_fkey'"
                )
            )
        ).scalar_one()
    assert delete_rule in {"r", b"r"}  # PostgreSQL code for ON DELETE RESTRICT

    owner = await owner_with_farm(client)
    custom = await create_custom_role(client, owner, "Race Target", [])
    cleaner = await role_id(client, owner, "CLEANER")
    added = await add_worker(client, owner, cleaner, "racer@farm.in")
    assert added.status_code == 201, added.text
    membership_id = added.json()["id"]

    deleted, assigned = await asyncio.gather(
        client.delete(f"/api/team/roles/{custom['id']}", headers=owner),
        client.post(
            f"/api/team/workers/{membership_id}/role",
            json={"role_id": custom["id"]},
            headers=owner,
        ),
    )
    # Whichever lock wins determines the business outcome, but neither legal
    # serialization may cascade-delete the worker's membership.
    assert (deleted.status_code, assigned.status_code) in {(204, 400), (400, 200)}
    page = await team_page(client, owner)
    membership = next(row for row in page["memberships"] if row["id"] == membership_id)
    assert membership["email"] == "racer@farm.in"


# ---------------------------------------------------------------------------
# Privilege-escalation guards for delegated team.manage
# ---------------------------------------------------------------------------
async def test_role_action_permission_requires_view_permission(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    resp = await client.post(
        "/api/team/roles",
        json={"name": "Broken Animal Role", "permissions": ["animals.move"]},
        headers=owner,
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "Permission 'animals.move' requires 'animals.view'."

    resp = await client.post(
        "/api/team/roles",
        json={
            "name": "Valid Animal Role",
            "permissions": ["animals.view", "animals.move"],
        },
        headers=owner,
    )
    assert resp.status_code == 201, resp.text


async def test_team_manager_cannot_grant_perms_he_lacks(client: httpx.AsyncClient) -> None:
    """Managers cannot delegate team.manage, and editing never strips
    pre-existing permissions that the editor cannot control."""
    owner = await owner_with_farm(client)
    tm, _ = await team_manager_headers(client, owner)

    resp = await client.post(
        "/api/team/roles",
        json={"name": "Sneaky", "permissions": ["team.manage", "finance.view", "finance.manage"]},
        headers=tm,
    )
    assert resp.status_code == 403, resp.text

    resp = await client.post(
        "/api/team/roles",
        json={"name": "Ordinary", "permissions": ["finance.view", "finance.manage"]},
        headers=tm,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["permissions"] == []

    # same filter applies when EDITING a role that legitimately has more
    rich = await create_custom_role(client, owner, "Finance Role", ["finance.view"])
    resp = await client.put(
        f"/api/team/roles/{rich['id']}",
        json={"name": "Finance Role", "permissions": []},
        headers=tm,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["permissions"] == ["finance.view"]

    resp = await client.put(
        f"/api/team/roles/{rich['id']}",
        json={"name": "Finance Role", "permissions": ["team.manage"]},
        headers=tm,
    )
    assert resp.status_code == 403


async def test_team_manager_can_manage_team_endpoints(client: httpx.AsyncClient) -> None:
    """Sanity: the delegated holder can open the team page and add workers."""
    owner = await owner_with_farm(client)
    tm, _ = await team_manager_headers(client, owner)
    assert (await client.get("/api/team", headers=tm)).status_code == 200
    rid = await role_id(client, owner, "CLEANER")
    resp = await add_worker(client, tm, rid, "w@farm.in")
    assert resp.status_code == 201, resp.text
    # but his other modules stay closed
    assert (await client.get("/api/finance", headers=tm)).status_code == 403
    assert (await client.get("/api/animals", headers=tm)).status_code == 403


async def _second_team_manager(client: httpx.AsyncClient, owner: dict) -> tuple[dict, int]:
    """A second team.manage holder (distinct role name — they are unique per farm)."""
    role = await create_custom_role(client, owner, "Team Clerk 2", ["team.manage"])
    resp = await add_worker(client, owner, role["id"], "tm2@farm.in")
    assert resp.status_code == 201, resp.text
    mid = resp.json()["id"]
    headers = await login_user(client, "tm2@farm.in", WORKER_PW)
    return headers | {"X-Farm-Id": owner["X-Farm-Id"]}, mid


async def test_team_manager_cannot_act_on_peer_manager(client: httpx.AsyncClient) -> None:
    """Horizontal control among team.manage holders is owner-only —
    a manager may not demote, deactivate, or password-reset a peer manager
    (insider lockout/hijack only the owner could undo)."""
    owner = await owner_with_farm(client)
    tm1, _ = await team_manager_headers(client, owner)
    _, mid2 = await _second_team_manager(client, owner)
    rid = await role_id(client, owner, "CLEANER")
    manager_role = next(
        role for role in (await team_page(client, owner))["roles"] if role["name"] == "Team Clerk 2"
    )

    resp = await client.post(f"/api/team/workers/{mid2}/toggle", headers=tm1)
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Only the farm owner can manage other team managers."

    # The manager role itself and assignment into it are owner-only too.
    resp = await client.put(
        f"/api/team/roles/{manager_role['id']}",
        json={"name": "Team Clerk 2", "permissions": ["team.manage"]},
        headers=tm1,
    )
    assert resp.status_code == 403
    assert (
        await client.delete(f"/api/team/roles/{manager_role['id']}", headers=tm1)
    ).status_code == 403
    assert (
        await add_worker(client, tm1, manager_role["id"], "third-manager@farm.in")
    ).status_code == 403
    ordinary = await add_worker(client, owner, rid, "ordinary@farm.in")
    assert ordinary.status_code == 201, ordinary.text
    resp = await client.post(
        f"/api/team/workers/{ordinary.json()['id']}/role",
        json={"role_id": manager_role["id"]},
        headers=tm1,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Only the farm owner can manage team-manager roles."
    resp = await client.post(f"/api/team/workers/{mid2}/role", json={"role_id": rid}, headers=tm1)
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Only the farm owner can manage other team managers."
    resp = await client.post(
        f"/api/team/workers/{mid2}/reset-password",
        json={"password": "hijacked123"},
        headers=tm1,
    )
    assert resp.status_code == 403

    # the peer is untouched: still active, still a manager, password intact
    team = await team_page(client, owner)
    peer = next(m for m in team["memberships"] if m["id"] == mid2)
    assert peer["is_active"] is True
    assert peer["role_name"] == "Team Clerk 2"
    await login_user(client, "tm2@farm.in", WORKER_PW)


async def test_owner_can_still_act_on_team_managers(client: httpx.AsyncClient) -> None:
    """The peer guard exempts the owner: he manages managers as before."""
    owner = await owner_with_farm(client)
    _, mid2 = await _second_team_manager(client, owner)
    rid = await role_id(client, owner, "CLEANER")
    resp = await client.post(f"/api/team/workers/{mid2}/role", json={"role_id": rid}, headers=owner)
    assert resp.status_code == 200, resp.text
    resp = await client.post(
        f"/api/team/workers/{mid2}/reset-password",
        json={"password": "ownerreset123"},
        headers=owner,
    )
    assert resp.status_code == 200, resp.text
    resp = await client.post(f"/api/team/workers/{mid2}/toggle", headers=owner)
    assert resp.status_code == 200, resp.text
    # and a manager acting on a NON-manager worker stays delegable (covered
    # by test_team_manager_can_toggle_other_workers)


# ---------------------------------------------------------------------------
# Auth & tenancy sweeps across every team endpoint
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(("method", "url"), TEAM_ENDPOINTS)
async def test_team_endpoints_require_auth(
    client: httpx.AsyncClient, method: str, url: str
) -> None:
    resp = await client.request(method, url, json={})
    assert resp.status_code == 401, f"{method} {url} → {resp.status_code}"
    assert resp.json()["detail"] == "Missing bearer token"


@pytest.mark.parametrize(("method", "url"), TEAM_ENDPOINTS)
async def test_team_endpoints_require_farm_header(
    client: httpx.AsyncClient, method: str, url: str
) -> None:
    owner = await owner_with_farm(client)
    auth_only = {"Authorization": owner["Authorization"]}
    resp = await client.request(method, url, json={}, headers=auth_only)
    # The header is required by the contract (422, not 400).
    assert resp.status_code == 422, f"{method} {url} → {resp.status_code}"
    assert "x-farm-id" in str(resp.json()["detail"]).lower()


@pytest.mark.parametrize(
    ("farm_header", "expected"),
    [
        ("abc", 400),
        ("1.5", 400),
        ("0", 400),
        ("-7", 400),
        (str(2**62), 400),  # out of range
        ("", 400),
        (" 3 ", 404),  # parses to a nonexistent farm id
        ("999999", 404),
    ],
)
async def test_farm_header_values(
    client: httpx.AsyncClient, farm_header: str, expected: int
) -> None:
    owner = await owner_with_farm(client)
    headers = {"Authorization": owner["Authorization"], "X-Farm-Id": farm_header}
    resp = await client.get("/api/team", headers=headers)
    assert resp.status_code == expected, f"X-Farm-Id={farm_header!r} → {resp.status_code}"


@pytest.mark.parametrize(("method", "url"), TEAM_ENDPOINTS)
async def test_team_endpoints_reject_non_member(
    client: httpx.AsyncClient, method: str, url: str
) -> None:
    """A valid user with no membership on the farm gets 404 everywhere."""
    owner = await owner_with_farm(client)
    stranger = await register(client, email="stranger@farm.in", password="strangerpass1")
    headers = stranger | {"X-Farm-Id": owner["X-Farm-Id"]}
    resp = await client.request(method, url, json={}, headers=headers)
    assert resp.status_code == 404, f"{method} {url} → {resp.status_code}"
    assert resp.json()["detail"] == "Farm not found"


@pytest.mark.parametrize(("method", "url"), TEAM_ENDPOINTS)
async def test_team_endpoints_reject_worker_without_team_manage(
    client: httpx.AsyncClient, method: str, url: str
) -> None:
    """Every team endpoint sits behind team.manage (cleaner lacks it)."""
    owner = await owner_with_farm(client)
    cleaner = await worker_headers(client, owner, "CLEANER", "cleaner@farm.in")
    resp = await client.request(method, url, json={}, headers=cleaner)
    assert resp.status_code == 403, f"{method} {url} → {resp.status_code}"
    assert resp.json()["detail"] == "Missing permission: team.manage"


async def test_invalid_bearer_token_rejected(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    for token in ["garbage", "Bearer", "", owner["Authorization"].removeprefix("Bearer ") + "x"]:
        headers = {"Authorization": f"Bearer {token}", "X-Farm-Id": owner["X-Farm-Id"]}
        resp = await client.get("/api/team", headers=headers)
        assert resp.status_code == 401, f"token {token!r} → {resp.status_code}"


async def test_cross_farm_worker_cannot_peek_team(client: httpx.AsyncClient) -> None:
    """Farm B's worker aiming his token at farm A is a non-member there → 404."""
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Alpha Farm")
    owner_b = await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    worker_b = await worker_headers(client, owner_b, "MOVER", "w@farm.in")
    resp = await client.get("/api/team", headers=worker_b | {"X-Farm-Id": owner_a["X-Farm-Id"]})
    # Forbidden farms answer exactly like unknown ones.
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Farm not found"
