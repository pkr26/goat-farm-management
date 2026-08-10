"""REGRESSION SUITE — the auth & security app bugs documented here are FIXED.

Every test asserted the behavior the API contract (schema
declarations / consistency with sibling endpoints) requires and FAILED
against the old app; the fixes are in backend/app code and these tests now
pass. Do not weaken these assertions — they guard the fixed behavior.
"""

import httpx
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import User
from app.security import verify_password

from .conftest import OWNER_PW, register


async def _insert_user(email: str, password_hash: str) -> int:
    async with get_sessionmaker()() as db:
        user = User(email=email, password_hash=password_hash)
        db.add(user)
        await db.flush()
        user_id = user.id
        await db.commit()
        return user_id


# FIXED — regression test
async def test_register_oversized_name_returns_422(client: httpx.AsyncClient) -> None:
    """RegisterIn.name used to have no max_length while User.name is
    varchar(120): a 121+ char name passed validation and crashed the INSERT
    → 500 DataError (asyncpg 'value too long'). The schema is now capped at
    the column width → clean 422.
    Repro: POST /api/auth/register {"email": "x@y.in", "password": "ownerpass123",
    "name": "N"*200} → was 500."""
    resp = await client.post(
        "/api/auth/register",
        json={"email": "longname@farm.in", "password": OWNER_PW, "name": "N" * 200},
    )
    assert resp.status_code == 422, resp.status_code


# FIXED — regression test
async def test_create_farm_oversized_location_returns_422(client: httpx.AsyncClient) -> None:
    """FarmCreateIn.location used to allow max_length=200 while Farm.location
    is varchar(120): a 121–200 char location was VALID per the schema yet
    crashed the INSERT → 500 DataError. The schema is now capped at the
    column width (120) → clean 422. (This test previously asserted 201 —
    accepting what the column cannot hold; corrected to the schema-side
    rejection.)
    Repro: POST /api/auth/farms {"name": "Farm", "location": "L"*150} → was 500."""
    headers = await register(client, "loclim@farm.in")
    resp = await client.post(
        "/api/auth/farms",
        json={"name": "Farm", "location": "L" * 150},
        headers=headers,
    )
    assert resp.status_code == 422, resp.status_code


# FIXED — regression test
async def test_create_farm_whitespace_only_name_rejected(client: httpx.AsyncClient) -> None:
    """FarmCreateIn.name is min_length=1, so "   " passed validation and the
    router used to strip it to "" and create a farm with an EMPTY name (201).
    The router now mirrors the animal-tag rule ("Tag number is required.")
    and rejects with 400."""
    headers = await register(client, "wsf@farm.in")
    resp = await client.post("/api/auth/farms", json={"name": "   "}, headers=headers)
    assert resp.status_code in (400, 422), resp.status_code


# FIXED — regression test
async def test_farm_header_beyond_int32_returns_404(
    client: httpx.AsyncClient,
) -> None:
    """deps.current_farm accepted 0 < X-Farm-Id < 2**62, but Farm.id is a
    PostgreSQL INTEGER (int32) — db.get(Farm, 2**62 - 1) raised asyncpg
    DataError 'value out of int32 range' → 500. Ids above the int4 ceiling
    now short-circuit to 404 'Farm not found' before any DB bind.
    Repro: GET /api/auth/permissions with X-Farm-Id: 4611686018427387903 →
    was 500."""
    headers = await register(client, "range500@farm.in")
    resp = await client.get(
        "/api/auth/permissions", headers=headers | {"X-Farm-Id": str(2**62 - 1)}
    )
    assert resp.status_code == 404, resp.status_code


async def test_duplicate_farm_headers_are_rejected_as_ambiguous(
    client: httpx.AsyncClient,
) -> None:
    """Tenant selection is an authorization input. A proxy and ASGI server
    may select different values from duplicate X-Farm-Id fields, so the API
    must not silently choose one farm and execute under that tenant context.
    """
    bearer = await register(client, "duplicate-farm-header@farm.in")
    first = await client.post("/api/auth/farms", json={"name": "First Farm"}, headers=bearer)
    second = await client.post("/api/auth/farms", json={"name": "Second Farm"}, headers=bearer)
    assert first.status_code == second.status_code == 201

    response = await client.get(
        "/api/auth/permissions",
        headers=[
            *bearer.items(),
            ("X-Farm-Id", str(first.json()["id"])),
            ("X-Farm-Id", str(second.json()["id"])),
        ],
    )
    assert response.status_code == 400
    assert "exactly once" in response.json()["detail"]


# FIXED — regression test
async def test_login_with_non_argon2_stored_hash_returns_401(client: httpx.AsyncClient) -> None:
    """verify_password caught VerifyMismatchError/VerificationError/
    Argon2Error but NOT argon2.exceptions.InvalidHashError (which argon2-cffi
    raises for stored hashes that aren't recognizable Argon2 at all — it is
    not an Argon2Error subclass). A user row whose password_hash is garbage
    ("plain-text", "", "$2b$bcrypt$...", "pbkdf2_sha256" without the trailing
    "$" that routes to the legacy verifier) made POST /api/auth/login crash
    → 500. Unverifiable hashes are now treated as invalid credentials → 401.
    Realistic source of such rows: a botched v1 migration or manual DB edits.
    Repro: insert User(password_hash="plain-text"), login → was 500."""
    await _insert_user("garbagehash@farm.in", "plain-text")
    resp = await client.post(
        "/api/auth/login", json={"email": "garbagehash@farm.in", "password": OWNER_PW}
    )
    assert resp.status_code == 401, resp.status_code


# FIXED — regression test
def test_verify_password_raises_on_non_argon2_stored_hashes() -> None:
    """Unit-level companion to the login bug above: these must all return
    (False, False) instead of raising argon2.exceptions.InvalidHashError."""
    for stored in ["", "plain-text", "$2b$not-argon", "pbkdf2_sha256"]:
        assert verify_password("anything", stored) == (False, False), stored


# FIXED — regression test
async def test_login_with_bcrypt_stored_hash_returns_401(client: httpx.AsyncClient) -> None:
    """Second API-level repro of the InvalidHashError hole: a bcrypt hash from
    a hypothetical passlib-era migration takes the Argon2 path (doesn't start
    with 'pbkdf2_sha256$') and used to crash → 500. Now a clean 401."""
    await _insert_user("bcrypt@farm.in", "$2b$12$abcdefghijklmnopqrstuuVXZ.")
    async with get_sessionmaker()() as db:  # sanity: row exists
        assert (await db.execute(select(User).where(User.email == "bcrypt@farm.in"))).scalar()
    resp = await client.post(
        "/api/auth/login", json={"email": "bcrypt@farm.in", "password": OWNER_PW}
    )
    assert resp.status_code == 401, resp.status_code


async def test_logout_rejects_ambiguous_duplicate_authorization_headers(
    client: httpx.AsyncClient,
) -> None:
    """Logout is state-changing even though it intentionally returns 204 for
    unauthenticated callers. It must share CurrentUser's duplicate-header rule:
    a proxy and the app may select different values from two Authorization
    fields, so neither principal is safe to revoke when the request is
    ambiguous.
    """
    first = await register(client, "duplicate-auth-a@farm.in")
    second = await register(client, "duplicate-auth-b@farm.in")
    client.cookies.clear()  # exercise bearer-only logout, not the refresh cookie

    response = await client.post(
        "/api/auth/logout",
        headers=[
            ("Authorization", first["Authorization"]),
            ("Authorization", second["Authorization"]),
        ],
    )
    assert response.status_code == 204
    assert (await client.get("/api/auth/me", headers=first)).status_code == 200
    assert (await client.get("/api/auth/me", headers=second)).status_code == 200


async def test_cookie_origin_guard_uses_origin_not_root_path() -> None:
    """When the API is mounted below a proxy root_path, Request.base_url
    contains that path but a browser Origin never does. Same-origin refreshes
    must compare scheme+authority rather than an impossible path-bearing value.
    """
    from app.main import create_app

    transport = httpx.ASGITransport(app=create_app(), root_path="/mounted")
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as mounted:
        await register(mounted, "root-path-origin@farm.in")
        response = await mounted.post("/api/auth/refresh", headers={"Origin": "http://test"})
    assert response.status_code == 200, response.text
