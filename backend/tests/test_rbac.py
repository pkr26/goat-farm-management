"""RBAC + duty-workflow tests — port of v1 tests/test_rbac.py to the JSON API.

v1 ran against the Jinja app (form POSTs, 303 redirects, session cookies, flash
messages); the JSON API equivalents are: Bearer JWT + X-Farm-Id header, 401 for
anonymous, 403 `Missing permission: <code>`, and JSON detail messages instead
of flashes. Setup goes through the API; side effects the JSON schemas don't
expose (BucketMove / HealthEvent attribution, role rows) are checked in the
database directly, mirroring the old suite's session assertions.
"""

import logging
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select

from app.db import get_sessionmaker
from app.models import Animal, BreedingRecord, BucketMove, HealthEvent, Role, Task, User
from app.permissions import preset_codes
from app.security import password_policy_error
from app.seed import seed_default_roles
from app.services import complete_task
from app.utils import today

from .conftest import owner_with_farm

WORKER_PW = "workerpass123"

# v1 page → JSON endpoint (dashboard = "/", reports live under dashboard).
PAGE_URLS = {
    "/": "/api/dashboard",
    "/animals": "/api/animals",
    "/buckets": "/api/buckets",
    "/breeding": "/api/breeding",
    "/kidding": "/api/kidding",
    "/health": "/api/health/events",
    "/purchases": "/api/purchases",
    "/feeding": "/api/feeding/plan",
    "/tasks": "/api/tasks",
    "/finance": "/api/finance",
    "/reports": "/api/dashboard/reports",
    "/team": "/api/team",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def login_user(client: httpx.AsyncClient, email: str, password: str) -> tuple[dict, int]:
    """Login → (bearer headers, user id)."""
    resp = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["user"]["id"]


async def role_id(client: httpx.AsyncClient, owner: dict, code: str) -> int:
    resp = await client.get("/api/team", headers=owner)
    assert resp.status_code == 200, resp.text
    return next(r["id"] for r in resp.json()["roles"] if r["code"] == code)


async def membership_id(client: httpx.AsyncClient, owner: dict, email: str) -> int:
    resp = await client.get("/api/team", headers=owner)
    assert resp.status_code == 200, resp.text
    return next(m["id"] for m in resp.json()["memberships"] if m["email"] == email)


async def add_worker(
    client: httpx.AsyncClient,
    owner: dict,
    rid: int,
    email: str,
    name: str = "Worker",
    password: str = WORKER_PW,
) -> httpx.Response:
    return await client.post(
        "/api/team/workers",
        json={"name": name, "email": email, "password": password, "role_id": rid},
        headers=owner,
    )


async def worker_headers(
    client: httpx.AsyncClient, owner: dict, code: str, email: str
) -> tuple[dict, int]:
    """Owner adds a worker with preset role `code`; returns (farm headers, user id)."""
    rid = await role_id(client, owner, code)
    resp = await add_worker(client, owner, rid, email)
    assert resp.status_code == 201, resp.text
    headers, user_id = await login_user(client, email, WORKER_PW)
    return headers | {"X-Farm-Id": owner["X-Farm-Id"]}, user_id


async def make_animal(
    client: httpx.AsyncClient, owner: dict, tag: str = "A-001", sex: str = "F"
) -> int:
    """An ACTIVE purchased doe in FOUNDATION (v1 used a direct DB insert)."""
    resp = await client.post(
        "/api/animals",
        json={
            "tag_number": tag,
            "sex": sex,
            "source": "PURCHASED",
            "current_bucket": "FOUNDATION",
            "historical_import_reason": "Existing-herd RBAC fixture",
            "date_of_birth": (today() - timedelta(days=400)).isoformat(),
            "weight_kg": 25.0,
            "weight_date": (today() - timedelta(days=400)).isoformat(),
        },
        headers=owner,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def create_duty(
    client: httpx.AsyncClient,
    owner: dict,
    title: str,
    rid: int | None = None,
    recur_days: int | None = None,
) -> dict:
    payload: dict = {"title": title, "due_date": today().isoformat(), "category": "CLEANING"}
    if rid is not None:
        payload["assigned_role_id"] = rid
    if recur_days is not None:
        payload["recur_days"] = recur_days
    resp = await client.post("/api/tasks", json=payload, headers=owner)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def get_tabs(client: httpx.AsyncClient, headers: dict) -> dict:
    resp = await client.get("/api/tasks", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def task_titles(tabs: dict) -> set[str]:
    # Pagination metadata is scalar; only these fields contain task rows.
    return {
        task["title"]
        for tab in ("today", "overdue", "upcoming", "awaiting", "completed")
        for task in tabs[tab]
    }


# ---------------------------------------------------------------------------
# Access smoke tests
# ---------------------------------------------------------------------------
async def test_owner_can_open_every_page(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    for old_path, url in PAGE_URLS.items():
        resp = await client.get(url, headers=owner)
        assert resp.status_code == 200, f"{old_path} ({url}) → {resp.status_code}"


async def test_anonymous_is_unauthorized(client: httpx.AsyncClient) -> None:
    # v1 redirected to /login (303); the JSON API answers 401 instead.
    for url in ["/api/dashboard", "/api/animals", "/api/tasks"]:
        resp = await client.get(url)
        assert resp.status_code == 401, url
        assert resp.json()["detail"] == "Missing bearer token"


async def test_mover_permissions(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    mover, mover_id = await worker_headers(client, owner, "MOVER", "mover@farm.in")

    for url in ["/api/dashboard", "/api/animals", "/api/buckets", "/api/tasks"]:
        resp = await client.get(url, headers=mover)
        assert resp.status_code == 200, url
    for url, code in {
        "/api/finance": "finance.view",
        "/api/health/events": "health.view",
        "/api/team": "team.manage",
        "/api/dashboard/reports": "reports.view",
    }.items():
        resp = await client.get(url, headers=mover)
        assert resp.status_code == 403, url
        assert resp.json()["detail"] == f"Missing permission: {code}"

    # v1 403'd the /animals/new + /tasks/new pages; the JSON equivalents are
    # the create endpoints.
    resp = await client.post(
        "/api/animals",
        json={
            "tag_number": "X-1",
            "sex": "F",
            "source": "PURCHASED",
            "current_bucket": "FOUNDATION",
        },
        headers=mover,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Missing permission: animals.create"
    resp = await client.post(
        "/api/tasks",
        json={"title": "X", "due_date": today().isoformat(), "category": "OTHER"},
        headers=mover,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Missing permission: tasks.create"

    # can do his job: move an animal between buckets (attributed to him)
    animal_id = await make_animal(client, owner)
    resp = await client.post(
        f"/api/animals/{animal_id}/move",
        json={"to_bucket": "BREEDING", "reason": "ready"},
        headers=mover,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["current_bucket"] == "BREEDING"
    async with get_sessionmaker()() as db:
        result = await db.execute(
            select(BucketMove).where(
                BucketMove.animal_id == animal_id, BucketMove.to_bucket == "BREEDING"
            )
        )
        move = result.scalar_one()
        assert move.created_by_id == mover_id  # digitized


async def test_vet_can_record_health_but_not_finance(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    vet, vet_id = await worker_headers(client, owner, "VET", "vet@farm.in")

    animal_id = await make_animal(client, owner)
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
    assert [e["animal_id"] for e in resp.json()] == [animal_id]
    async with get_sessionmaker()() as db:
        result = await db.execute(select(HealthEvent).where(HealthEvent.animal_id == animal_id))
        event = result.scalar_one()
        assert event.created_by_id == vet_id

    resp = await client.get("/api/finance", headers=vet)
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Missing permission: finance.view"


async def test_cleaner_sees_only_tasks(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    cleaner, _ = await worker_headers(client, owner, "CLEANER", "cleaner@farm.in")

    assert (await client.get("/api/tasks", headers=cleaner)).status_code == 200
    assert (await client.get("/api/dashboard", headers=cleaner)).status_code == 200
    for url in [
        "/api/animals",
        "/api/health/events",
        "/api/feeding/plan",
        "/api/finance",
        "/api/team",
    ]:
        resp = await client.get(url, headers=cleaner)
        assert resp.status_code == 403, url


async def test_cleaner_dashboard_hides_the_breeding_programme(client: httpx.AsyncClient) -> None:
    """dashboard.view opens the page, not the herd's breeding programme.

    `api._shared.animal_out` blanks cull_candidate and is_currently_pregnant
    for anyone without breeding.view; the aggregate page must apply the same
    rule instead of handing a cleaner every pregnant doe's tag and due date.
    """
    owner = await owner_with_farm(client)
    cleaner, _ = await worker_headers(client, owner, "CLEANER", "cleaner@farm.in")
    doe_id = await make_animal(client, owner, "RBAC-DOE")
    buck_id = await make_animal(client, owner, "RBAC-BUCK", sex="M")
    async with get_sessionmaker()() as db:
        db.add(
            BreedingRecord(
                farm_id=int(owner["X-Farm-Id"]),
                doe_id=doe_id,
                buck_id=buck_id,
                breeding_date=today() - timedelta(days=140),
                ultrasound_date=today() - timedelta(days=108),
                ultrasound_done=True,
                pregnant=True,
                expected_kidding_date=today() + timedelta(days=10),
                outcome="CONFIRMED_PREGNANT",
            )
        )
        doe = (await db.execute(select(Animal).where(Animal.id == doe_id))).scalar_one()
        doe.cull_candidate = True
        await db.commit()

    owner_dash = (await client.get("/api/dashboard", headers=owner)).json()
    assert [row["doe_tag"] for row in owner_dash["kiddings_due"]] == ["RBAC-DOE"]
    assert [row["tag_number"] for row in owner_dash["cull_candidates"]] == ["RBAC-DOE"]

    resp = await client.get("/api/dashboard", headers=cleaner)
    assert resp.status_code == 200, resp.text
    cleaner_dash = resp.json()
    assert cleaner_dash["kiddings_due"] == []
    # null, not 0: a withheld section must be distinguishable from a
    # genuinely empty list, or the UI presents the gate as fact (RT-KL-4).
    assert cleaner_dash["kiddings_due_total"] is None
    assert cleaner_dash["cull_candidates"] == []
    assert cleaner_dash["cull_candidates_total"] is None
    # Withheld sections, not a 403 — the cleaner's own page still works. The
    # herd summary itself is animals.view-gated (RT-KL-1), so a cleaner sees
    # the None sentinel there too.
    assert cleaner_dash["total_active"] is None
    assert cleaner_dash["buckets"] is None
    assert owner_dash["total_active"] is not None


async def test_rbac_denial_emits_an_identifiable_audit_log_record(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    """Denials must stay observable: exactly one goatfarm.deps INFO record per
    denial, carrying the "RBAC denial" marker and the exact missing code.

    Pins the audit line against regressions that keep the 403 identical while
    dropping the permission code, the greppable prefix, or the record itself.
    """
    owner = await owner_with_farm(client)
    mover, _ = await worker_headers(client, owner, "MOVER", "auditlog@farm.in")

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="goatfarm.deps"):
        resp = await client.get("/api/finance", headers=mover)
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Missing permission: finance.view"

    records = [record for record in caplog.records if record.name == "goatfarm.deps"]
    assert len(records) == 1, [record.getMessage() for record in records]
    assert records[0].levelno == logging.INFO
    assert records[0].getMessage() == "RBAC denial: missing permission finance.view"


# ---------------------------------------------------------------------------
# Team management
# ---------------------------------------------------------------------------
async def test_worker_landing_and_deactivation(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    cleaner, _ = await worker_headers(client, owner, "CLEANER", "cleaner@farm.in")

    # single accessible farm → logged straight into it (v1: redirect to "/")
    resp = await client.get("/api/auth/farms", headers=cleaner)
    assert resp.status_code == 200
    assert [f["name"] for f in resp.json()] == ["Alpha Farm"]

    # owner deactivates → access gone (v1: bounce to the farm picker)
    mid = await membership_id(client, owner, "cleaner@farm.in")
    resp = await client.put(
        f"/api/team/workers/{mid}/status", json={"is_active": False}, headers=owner
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_active"] is False

    # Deactivation is tenant-local: the same bearer remains an authenticated
    # account but no longer authorizes this farm.
    resp = await client.get("/api/tasks", headers=cleaner)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Farm not found"
    resp = await client.get("/api/auth/farms", headers=cleaner)
    assert resp.status_code == 200
    assert resp.json() == []


async def test_cannot_add_same_person_twice_or_owner(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    rid = await role_id(client, owner, "CLEANER")
    resp = await add_worker(client, owner, rid, "cleaner@farm.in")
    assert resp.status_code == 201, resp.text

    resp = await add_worker(client, owner, rid, "cleaner@farm.in")
    assert resp.status_code == 400  # duplicate
    assert resp.json()["detail"] == "That email can't be added to this farm's team."

    resp = await add_worker(client, owner, rid, "owner@farm.in")
    assert resp.status_code == 400  # the owner
    # A generic response avoids disclosing account or membership existence.
    assert resp.json()["detail"] == "That email can't be added to this farm's team."


async def test_role_edit_takes_effect_immediately(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    mover, _ = await worker_headers(client, owner, "MOVER", "mover@farm.in")
    animal_id = await make_animal(client, owner)

    rid = await role_id(client, owner, "MOVER")
    resp = await client.put(
        f"/api/team/roles/{rid}",
        json={
            "name": "Animal Mover",
            "description": "",
            # animals.move removed
            "permissions": [
                "dashboard.view",
                "animals.view",
                "buckets.view",
                "tasks.view",
                "tasks.complete",
            ],
        },
        headers=owner,
    )
    assert resp.status_code == 200, resp.text
    assert "animals.move" not in resp.json()["permissions"]

    resp = await client.post(
        f"/api/animals/{animal_id}/move", json={"to_bucket": "BREEDING"}, headers=mover
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Missing permission: animals.move"


async def test_cross_tenant_isolation(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Alpha Farm")
    owner_b = await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    fid_a, fid_b = int(owner_a["X-Farm-Id"]), int(owner_b["X-Farm-Id"])
    assert fid_a != fid_b

    worker, _ = await worker_headers(client, owner_a, "MOVER", "mover@farm.in")

    # cannot work inside farm B — 404 like any unknown farm
    resp = await client.get("/api/animals", headers=worker | {"X-Farm-Id": str(fid_b)})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Farm not found"

    # he stays scoped to farm A
    resp = await client.get("/api/auth/farms", headers=worker)
    assert [f["name"] for f in resp.json()] == ["Alpha Farm"]

    # farm-B owner never sees farm A in his list
    resp = await client.get("/api/auth/farms", headers=owner_b)
    assert [f["name"] for f in resp.json()] == ["Beta Farm"]


# ---------------------------------------------------------------------------
# Duty workflow: assignment, completion, verification, recurrence
# ---------------------------------------------------------------------------
async def test_cleaning_duty_full_verification_loop(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    cleaner, cleaner_id = await worker_headers(client, owner, "CLEANER", "cleaner@farm.in")
    manager, manager_id = await worker_headers(client, owner, "CLEANER_MANAGER", "cm@farm.in")

    rid = await role_id(client, owner, "CLEANER")
    duty = await create_duty(client, owner, "Scrub BREEDING water troughs", rid=rid)

    # visible to cleaner, hidden from unrelated roles, owner sees all
    mover, _ = await worker_headers(client, owner, "MOVER", "mover@farm.in")
    assert "Scrub BREEDING water troughs" in task_titles(await get_tabs(client, cleaner))
    assert "Scrub BREEDING water troughs" in task_titles(await get_tabs(client, owner))
    assert "Scrub BREEDING water troughs" not in task_titles(await get_tabs(client, mover))

    # cleaner marks done → DONE + attributed, lands in manager's awaiting tab
    resp = await client.post(f"/api/tasks/{duty['id']}/complete", headers=cleaner)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "DONE"
    assert body["completed_by_id"] == cleaner_id
    assert body["needs_verification"] is True
    awaiting = (await get_tabs(client, manager))["awaiting"]
    assert "Scrub BREEDING water troughs" in {t["title"] for t in awaiting}

    # manager verifies → VERIFIED + attributed
    resp = await client.post(f"/api/tasks/{duty['id']}/verify", headers=manager)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "VERIFIED"
    assert body["verified_by_id"] == manager_id
    completed = (await get_tabs(client, owner))["completed"]
    assert "Scrub BREEDING water troughs" in {t["title"] for t in completed}


async def test_rejected_duty_goes_back_with_note(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    cleaner, _ = await worker_headers(client, owner, "CLEANER", "cleaner@farm.in")
    manager, _ = await worker_headers(client, owner, "CLEANER_MANAGER", "cm@farm.in")

    rid = await role_id(client, owner, "CLEANER")
    duty = await create_duty(client, owner, "Sweep feed alley", rid=rid)
    resp = await client.post(f"/api/tasks/{duty['id']}/complete", headers=cleaner)
    assert resp.status_code == 200, resp.text
    resp = await client.post(
        f"/api/tasks/{duty['id']}/reject",
        json={"note": "still dirty at the edges"},
        headers=manager,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "PENDING"
    assert body["verification_note"] == "still dirty at the edges"

    # cleaner sees the send-back on his list
    tabs = await get_tabs(client, cleaner)
    mine = next(t for t in tabs["today"] if t["title"] == "Sweep feed alley")
    assert mine["verification_note"] is not None
    assert "still dirty" in mine["verification_note"]


async def test_worker_cannot_complete_other_roles_duty(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    mover, _ = await worker_headers(client, owner, "MOVER", "mover@farm.in")

    rid = await role_id(client, owner, "CLEANER")
    duty = await create_duty(client, owner, "Disinfect kidding pens", rid=rid)
    resp = await client.post(f"/api/tasks/{duty['id']}/complete", headers=mover)
    assert resp.status_code == 403  # v1 bounced with a flash; the API 403s
    assert resp.json()["detail"] == "This duty is not assigned to you"

    tabs = await get_tabs(client, owner)
    task = next(t for t in tabs["today"] if t["id"] == duty["id"])
    assert task["status"] == "PENDING"


async def test_recurring_duty_spawns_next_occurrence(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    cleaner, _ = await worker_headers(client, owner, "CLEANER", "cleaner@farm.in")

    rid = await role_id(client, owner, "CLEANER")
    duty = await create_duty(client, owner, "Daily bunk sweep", rid=rid, recur_days=1)
    resp = await client.post(f"/api/tasks/{duty['id']}/complete", headers=cleaner)
    assert resp.status_code == 200, resp.text

    # CLEANING needs verification, so DONE is not terminal — the successor
    # spawns on verification, not completion (a reject could still reopen it).
    tabs = await get_tabs(client, owner)
    assert not [t for t in tabs["upcoming"] if t["title"] == "Daily bunk sweep"]
    resp = await client.post(f"/api/tasks/{duty['id']}/verify", headers=owner)
    assert resp.status_code == 200, resp.text

    tabs = await get_tabs(client, owner)
    spawned = [t for t in tabs["upcoming"] if t["title"] == "Daily bunk sweep"]
    assert len(spawned) == 1
    nxt = spawned[0]
    assert nxt["id"] != duty["id"]
    assert nxt["due_date"] == (today() + timedelta(days=1)).isoformat()
    assert nxt["status"] == "PENDING"
    assert nxt["assigned_role_id"] == duty["assigned_role_id"]
    assert nxt["recur_days"] == 1


async def test_unassigned_duty_visible_to_owner_only(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    cleaner, _ = await worker_headers(client, owner, "CLEANER", "cleaner@farm.in")

    await create_duty(client, owner, "Fix broken latch")  # no assignment
    assert "Fix broken latch" in task_titles(await get_tabs(client, owner))
    assert "Fix broken latch" not in task_titles(await get_tabs(client, cleaner))


# ---------------------------------------------------------------------------
# Password policy & seeding
# ---------------------------------------------------------------------------
def test_password_policy_enforced() -> None:
    assert password_policy_error("short") is not None
    assert password_policy_error("longenough1") is None


async def test_short_password_rejected_on_register_and_worker_create(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.post(
        "/api/auth/register", json={"email": "weak@farm.in", "password": "abc", "name": None}
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Password must be at least 8 characters."

    owner = await owner_with_farm(client)
    rid = await role_id(client, owner, "CLEANER")
    resp = await add_worker(client, owner, rid, "w@farm.in", password="abc")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Password must be at least 8 characters."


def test_role_permission_set_is_robust() -> None:
    role = Role(farm_id=1, name="X", permissions='["tasks.view", 42, "animals.view"]')
    assert role.permission_set() == {"tasks.view", "animals.view"}
    role.permissions = "not json"
    assert role.permission_set() == set()


async def test_seed_default_roles_idempotent(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client, email="s@farm.in", farm_name="Seed Farm")
    farm_id = int(headers["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        await seed_default_roles(db, farm_id)
        await seed_default_roles(db, farm_id)  # second call must not duplicate
        await db.commit()
        result = await db.execute(
            select(func.count()).select_from(Role).where(Role.farm_id == farm_id)
        )
        # A default goat farm seeds its whole (goat-scoped) preset vocabulary.
        assert result.scalar_one() == len(preset_codes())


async def test_complete_task_stamps_attribution(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client, email="t@farm.in", farm_name="Attr Farm")
    farm_id = int(headers["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        owner = (await db.execute(select(User).where(User.email == "t@farm.in"))).scalar_one()
        task = Task(farm_id=farm_id, title="t", due_date=today(), category="CLEANING")
        db.add(task)
        await db.commit()
        await complete_task(db, task, owner)
        await db.commit()
        assert task.completed_by_id == owner.id
        assert task.completed_at is not None
        assert task.needs_verification is True
