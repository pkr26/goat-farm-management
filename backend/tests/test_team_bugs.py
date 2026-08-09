"""REGRESSION SUITE — the team/RBAC app bugs documented here are FIXED.

Each test asserts the CORRECT behavior and FAILED against the old app
(int32-overflow ids → asyncpg DataError 500; a team.manage holder could
promote his own membership). The app is fixed and these now pass — kept as
regression guards. Helpers are reused from the extended suite.
"""

import httpx
import pytest
from sqlalchemy import func, literal, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

import app.api.team as team_api
from app.db import get_sessionmaker
from app.models import Farm, FarmMembership, Role, User
from app.utils import today, utcnow

from .conftest import owner_with_farm
from .test_team_extended import (
    WORKER_PW,
    add_worker,
    create_custom_role,
    login_user,
    membership_id,
    permissions_of,
    role_id,
    team_manager_headers,
    worker_headers,
)

# An id that passes BoundedId (ge=1, le=2**62) and every path-int parse, but
# exceeds PostgreSQL's int4 primary-key columns.
HUGE_ID = 2**40


async def test_tombstoned_role_fails_closed_for_auth_and_live_assignment(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    role = await create_custom_role(
        client,
        owner,
        "Retired operator",
        ["dashboard.view", "tasks.view", "tasks.complete"],
    )
    added = await add_worker(client, owner, role["id"], "retired@farm.in")
    assert added.status_code == 201, added.text
    worker = await login_user(client, "retired@farm.in", WORKER_PW)
    worker["X-Farm-Id"] = owner["X-Farm-Id"]

    # Simulate a damaged/out-of-process tombstone with a living membership.
    # Normal DELETE refuses this state, but authorization must still fail
    # closed rather than trusting the relationship's historical Role object.
    async with get_sessionmaker()() as db:
        stored = await db.get(Role, role["id"])
        assert stored is not None
        stored.deleted_at = utcnow()
        await db.commit()

    denied = await client.get("/api/tasks", headers=worker)
    assert denied.status_code == 404, denied.text
    assert denied.json()["detail"] == "Farm not found"
    farms = await client.get("/api/auth/farms", headers=worker)
    assert farms.status_code == 200, farms.text
    assert farms.json() == []

    page = await client.get("/api/team", headers=owner)
    assert page.status_code == 200, page.text
    assert role["id"] not in {item["id"] for item in page.json()["roles"]}
    provision = await add_worker(client, owner, role["id"], "must-not-exist@farm.in")
    assert provision.status_code == 400, provision.text
    assert provision.json()["detail"] == "Pick a valid role."
    task = await client.post(
        "/api/tasks",
        json={
            "title": "Must not target a deleted role",
            "due_date": today().isoformat(),
            "assigned_role_id": role["id"],
        },
        headers=owner,
    )
    assert task.status_code == 400, task.text
    rename = await client.put(
        f"/api/team/roles/{role['id']}",
        json={"name": "Revived", "permissions": []},
        headers=owner,
    )
    assert rename.status_code == 404, rename.text


# FIXED — regression test
# Endpoint: POST /api/team/workers (body role_id)
# Repro: create a worker with role_id=2**40 (valid per BoundedId, le=2**62).
# Expected: 400 "Pick a valid role." (role does not exist).
# Actual: 500 — asyncpg DataError "value out of int32 range": the roles PK is
# an int4 column, but schemas/common.py's BoundedId ceiling (2**62, "PG bigint
# is also 64-bit") lets int4-overflowing ids reach the ORM. A 500 on malformed
# input contradicts the API's validation contract (4xx, never a crash).
async def test_create_worker_huge_role_id_returns_400_not_500(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    resp = await client.post(
        "/api/team/workers",
        json={"email": "w@farm.in", "password": WORKER_PW, "role_id": HUGE_ID},
        headers=owner,
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "Pick a valid role."


# FIXED — regression test
# Endpoint: POST /api/team/workers/{membership_id}/role (body role_id)
# Same int32-overflow root cause as above, via RoleChangeIn.role_id.
# Expected: 400 "Pick a valid role." Actual: 500 (asyncpg int32 DataError).
async def test_change_role_huge_role_id_returns_400_not_500(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    await worker_headers(client, owner, "CLEANER", "w@farm.in")
    mid = await membership_id(client, owner, "w@farm.in")
    resp = await client.post(
        f"/api/team/workers/{mid}/role", json={"role_id": HUGE_ID}, headers=owner
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "Pick a valid role."


# FIXED — regression test
# Endpoints: PUT/DELETE /api/team/roles/{role_id} (path id, no bounds check)
# Repro: role_id=2**40 in the path. Expected: 404 "Role not found".
# Actual: 500 — same int32 overflow (db.get(Role, HUGE_ID)).
async def test_update_role_huge_path_id_returns_404_not_500(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    resp = await client.put(
        f"/api/team/roles/{HUGE_ID}", json={"name": "X", "permissions": []}, headers=owner
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Role not found"


async def test_delete_role_huge_path_id_returns_404_not_500(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    resp = await client.delete(f"/api/team/roles/{HUGE_ID}", headers=owner)
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Role not found"


# FIXED — regression test
# Endpoints: PUT /api/team/workers/{membership_id}/status and POST /reset-password
# (path id, no bounds check). Expected: 404 "Membership not found".
# Actual: 500 — same int32 overflow querying farm_memberships.
async def test_toggle_huge_path_id_returns_404_not_500(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await client.put(
        f"/api/team/workers/{HUGE_ID}/status",
        json={"is_active": False},
        headers=owner,
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Membership not found"


async def test_reset_password_huge_path_id_returns_404_not_500(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    resp = await client.post(
        f"/api/team/workers/{HUGE_ID}/reset-password",
        json={"password": "brandnewpass1"},
        headers=owner,
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Membership not found"


# FIXED — regression test
# Endpoint: POST /api/team/workers/{membership_id}/role
# Repro: owner creates role "Team Clerk" (permissions=["team.manage"]) and a
# richer role "Finance Manager" (finance.view/manage); the clerk calls
# /workers/{his own membership id}/role with the Finance Manager role id.
# Expected: 400 — self-service role swaps must be refused. The sibling toggle
# endpoint already guards self-operations (400 "You cannot deactivate your own
# membership."), and _clean_permissions filters role grants to the caller's own
# permissions — both show deliberate escalation prevention that this endpoint
# defeats: the clerk hands himself finance.view, a permission he could never
# put into a role. SPEC ("RBAC / Team → Rules"): the owner "can change roles"
# — delegation via team.manage should not include self-promotion.
# Actual: 200, role changed; GET /api/auth/permissions immediately shows
# finance.view/finance.manage and GET /api/finance opens.
async def test_team_manager_cannot_self_promote_to_richer_role(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    tm, mid = await team_manager_headers(client, owner)
    rich = await create_custom_role(
        client, owner, "Finance Manager", ["finance.view", "finance.manage"]
    )

    resp = await client.post(
        f"/api/team/workers/{mid}/role", json={"role_id": rich["id"]}, headers=tm
    )
    assert resp.status_code == 400, resp.text

    # his permissions must be unchanged
    body = await permissions_of(client, tm)
    assert set(body["permissions"]) == {"team.manage"}
    assert (await client.get("/api/finance", headers=tm)).status_code == 403


async def test_team_manager_cannot_create_any_password_controlled_alternate_account(
    client: httpx.AsyncClient,
) -> None:
    """Even an in-scope role cannot be used to manufacture a second identity."""
    owner = await owner_with_farm(client)
    manager, _ = await team_manager_headers(client, owner)
    cleaner = await role_id(client, owner, "CLEANER")

    response = await add_worker(
        client,
        manager,
        cleaner,
        "manager-alternate@farm.in",
        password="alternatepass123",
    )
    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "Only the farm owner can create worker accounts."

    # The rejected operation must not leave a usable richer account behind.
    login = await client.post(
        "/api/auth/login",
        json={"email": "manager-alternate@farm.in", "password": "alternatepass123"},
    )
    assert login.status_code == 401, login.text
    assert (await client.get("/api/finance", headers=manager)).status_code == 403


async def test_team_manager_cannot_assign_richer_role_to_alternate_account(
    client: httpx.AsyncClient,
) -> None:
    """A manager cannot upgrade a controlled worker and then log in as it."""
    owner = await owner_with_farm(client)
    cleaner_permissions = ["dashboard.view", "tasks.view", "tasks.complete"]
    manager, _ = await team_manager_headers(client, owner, extra_perms=cleaner_permissions)
    cleaner = await role_id(client, owner, "CLEANER")
    created = await add_worker(client, owner, cleaner, "bounded-alternate@farm.in")
    assert created.status_code == 201, created.text
    rich = await create_custom_role(
        client, owner, "Finance Manager", ["finance.view", "finance.manage"]
    )

    response = await client.post(
        f"/api/team/workers/{created.json()['id']}/role",
        json={"role_id": rich["id"]},
        headers=manager,
    )
    assert response.status_code == 403, response.text
    assert (
        response.json()["detail"]
        == "You can only manage workers and roles within your own permissions."
    )

    # Authenticate as the alternate account and verify the failed assignment
    # did not confer either finance access or the manager's team.manage power.
    alternate = await login_user(client, "bounded-alternate@farm.in", WORKER_PW)
    alternate["X-Farm-Id"] = owner["X-Farm-Id"]
    assert set((await permissions_of(client, alternate))["permissions"]) == set(cleaner_permissions)
    assert (await client.get("/api/finance", headers=alternate)).status_code == 403
    assert (await client.get("/api/team", headers=alternate)).status_code == 403


async def test_team_manager_cannot_reset_richer_alternate_credentials(
    client: httpx.AsyncClient,
) -> None:
    """Credential reset is owner-only and cannot become an account-takeover path."""
    owner = await owner_with_farm(client)
    manager, _ = await team_manager_headers(client, owner)
    rich = await create_custom_role(
        client, owner, "Finance Manager", ["finance.view", "finance.manage"]
    )
    created = await add_worker(
        client,
        owner,
        rich["id"],
        "finance-worker@farm.in",
        password="originalpass123",
    )
    assert created.status_code == 201, created.text

    response = await client.post(
        f"/api/team/workers/{created.json()['id']}/reset-password",
        json={"password": "hijackedpass123"},
        headers=manager,
    )
    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "Only the farm owner can reset worker passwords."

    # The attempted takeover password never works. The original account still
    # authenticates with its original role, while the manager remains bounded.
    hijacked = await client.post(
        "/api/auth/login",
        json={"email": "finance-worker@farm.in", "password": "hijackedpass123"},
    )
    assert hijacked.status_code == 401, hijacked.text
    alternate = await login_user(client, "finance-worker@farm.in", "originalpass123")
    alternate["X-Farm-Id"] = owner["X-Farm-Id"]
    assert (await client.get("/api/finance", headers=alternate)).status_code == 200
    assert (await client.get("/api/finance", headers=manager)).status_code == 403


async def test_team_manager_cannot_toggle_richer_worker(
    client: httpx.AsyncClient,
) -> None:
    """Roster activation cannot be used to disrupt an account above the caller."""
    owner = await owner_with_farm(client)
    manager, _ = await team_manager_headers(client, owner)
    rich = await create_custom_role(
        client, owner, "Finance Manager", ["finance.view", "finance.manage"]
    )
    created = await add_worker(client, owner, rich["id"], "finance-worker@farm.in")
    assert created.status_code == 201, created.text

    response = await client.put(
        f"/api/team/workers/{created.json()['id']}/status",
        json={"is_active": False},
        headers=manager,
    )
    assert response.status_code == 403, response.text
    assert (
        response.json()["detail"]
        == "You can only manage workers and roles within your own permissions."
    )

    # The richer worker remains active and can still use its original account.
    richer_worker = await login_user(client, "finance-worker@farm.in", WORKER_PW)
    richer_worker["X-Farm-Id"] = owner["X-Farm-Id"]
    assert (await client.get("/api/finance", headers=richer_worker)).status_code == 200


async def test_team_manager_cannot_update_or_delete_richer_role(
    client: httpx.AsyncClient,
) -> None:
    """Role metadata and deletion obey the same RBAC ceiling as assignment."""
    owner = await owner_with_farm(client)
    manager, _ = await team_manager_headers(client, owner)
    rich = await create_custom_role(
        client, owner, "Finance Manager", ["finance.view", "finance.manage"]
    )

    updated = await client.put(
        f"/api/team/roles/{rich['id']}",
        json={
            "name": "Disguised Finance Role",
            "permissions": ["finance.view", "finance.manage"],
        },
        headers=manager,
    )
    assert updated.status_code == 403, updated.text
    assert (
        updated.json()["detail"]
        == "You can only manage workers and roles within your own permissions."
    )

    deleted = await client.delete(f"/api/team/roles/{rich['id']}", headers=manager)
    assert deleted.status_code == 403, deleted.text
    assert (
        deleted.json()["detail"]
        == "You can only manage workers and roles within your own permissions."
    )

    # The owner retains full control over the same role.
    owner_update = await client.put(
        f"/api/team/roles/{rich['id']}",
        json={
            "name": "Owner-renamed Finance Role",
            "permissions": ["finance.view", "finance.manage"],
        },
        headers=owner,
    )
    assert owner_update.status_code == 200, owner_update.text
    assert owner_update.json()["name"] == "Owner-renamed Finance Role"
    assert (await client.delete(f"/api/team/roles/{rich['id']}", headers=owner)).status_code == 204


async def test_reset_password_increments_the_locked_token_version(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_get_membership` selectinloads the target User, so the later locked
    re-read used to hand back that cached pre-lock instance and
    `token_version += 1` incremented a stale value — a lost update that
    re-wrote the version a concurrent change-password had just committed,
    leaving the revoked worker's access token valid for its whole TTL."""
    owner = await owner_with_farm(client, "reset-stale-owner@farm.in")
    rid = await role_id(client, owner, "CLEANER")
    assert (await add_worker(client, owner, rid, "reset-stale@farm.in")).status_code == 201
    mid = await membership_id(client, owner, "reset-stale@farm.in")

    async with get_sessionmaker()() as db:
        worker_id, before = (
            await db.execute(
                select(User.id, User.token_version).where(User.email == "reset-stale@farm.in")
            )
        ).one()

    original = team_api._get_membership
    bumped = False

    async def bump_between_load_and_lock(
        db: AsyncSession,
        farm: Farm,
        target_id: int,
        *,
        for_update: bool = False,
        no_key_update: bool = False,
    ) -> FarmMembership:
        nonlocal bumped
        membership = await original(
            db, farm, target_id, for_update=for_update, no_key_update=no_key_update
        )
        if for_update and not bumped:
            bumped = True
            # Stand in for a concurrent self-service change-password that
            # commits a new token_version after the membership load but before
            # the route takes its FOR UPDATE lock on the same user row.
            async with get_sessionmaker()() as other:
                await other.execute(
                    update(User)
                    .where(User.id == worker_id)
                    .values(token_version=User.token_version + 1)
                )
                await other.commit()
        return membership

    monkeypatch.setattr(team_api, "_get_membership", bump_between_load_and_lock)
    resp = await client.post(
        f"/api/team/workers/{mid}/reset-password",
        json={"password": "brandnewpass1"},
        headers=owner,
    )
    assert resp.status_code == 200, resp.text
    assert bumped

    async with get_sessionmaker()() as db:
        after = (
            await db.execute(select(User.token_version).where(User.id == worker_id))
        ).scalar_one()
    # Both increments survive: the reset built on the value it locked, so the
    # token minted by the concurrent change-password is now invalid.
    assert after == before + 2


async def test_team_provisioning_lock_does_not_block_unrelated_tenant_writes(
    client: httpx.AsyncClient,
) -> None:
    """Team/role provisioning serializes on a per-farm ADVISORY lock. A
    `SELECT farms.id ... FOR UPDATE` conflicts with the FOR KEY SHARE that
    every farm-scoped child insert takes, so the guard used to stall every
    unrelated write in the tenant for the rest of the request transaction."""
    owner = await owner_with_farm(client, "advisory-owner@farm.in")
    farm_id = int(owner["X-Farm-Id"])

    async with get_sessionmaker()() as holder:
        farm = (await holder.execute(select(Farm).where(Farm.id == farm_id))).scalar_one()
        await team_api._lock_farm_provisioning(holder, farm)

        # An unrelated farm-scoped insert (FK to farms) must not queue behind it.
        async with get_sessionmaker()() as writer:
            await writer.execute(text("SET lock_timeout = '2s'"))
            writer.add(Role(farm_id=farm_id, name="Concurrent insert probe", permissions="[]"))
            await writer.commit()

        # The guard still self-conflicts, so the capacity ceilings stay real.
        async with get_sessionmaker()() as contender:
            taken = (
                await contender.execute(
                    select(
                        func.pg_try_advisory_xact_lock(
                            literal(team_api.TEAM_PROVISIONING_LOCK_NAMESPACE), literal(farm_id)
                        )
                    )
                )
            ).scalar_one()
            await contender.rollback()
        assert taken is False
        await holder.rollback()
