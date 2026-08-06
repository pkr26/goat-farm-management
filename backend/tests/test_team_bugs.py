"""REGRESSION SUITE — the team/RBAC app bugs documented here are FIXED.

Each test asserts the CORRECT behavior and FAILED against the old app
(int32-overflow ids → asyncpg DataError 500; a team.manage holder could
promote his own membership). The app is fixed and these now pass — kept as
regression guards. Helpers are reused from the extended suite.
"""

import httpx

from .conftest import owner_with_farm
from .test_team_extended import (
    WORKER_PW,
    create_custom_role,
    membership_id,
    permissions_of,
    team_manager_headers,
    worker_headers,
)

# An id that passes BoundedId (ge=1, le=2**62) and every path-int parse, but
# exceeds PostgreSQL's int4 primary-key columns.
HUGE_ID = 2**40


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
# Endpoints: POST /api/team/workers/{membership_id}/toggle and /reset-password
# (path id, no bounds check). Expected: 404 "Membership not found".
# Actual: 500 — same int32 overflow querying farm_memberships.
async def test_toggle_huge_path_id_returns_404_not_500(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await client.post(f"/api/team/workers/{HUGE_ID}/toggle", headers=owner)
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
