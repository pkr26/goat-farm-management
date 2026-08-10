"""REGRESSION SUITE — the auth & security app bugs documented here are FIXED.

Every test asserted the behavior the API contract (schema
declarations / consistency with sibling endpoints) requires and FAILED
against the old app; the fixes are in backend/app code and these tests now
pass. Do not weaken these assertions — they guard the fixed behavior.
"""

import asyncio
import hashlib
from collections.abc import Iterator

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select

import app.api.auth as auth_api
from app.core.config import get_settings
from app.db import get_sessionmaker
from app.deps import INVALID_LOGOUT_REFRESH_TOKEN_SCOPE
from app.main import create_app
from app.models import User
from app.ratelimit import auth_limiter
from app.security import hash_password, verify_password
from app.security import verify_password_async as real_verify_password_async
from app.security import verify_password_with_work_async as real_verify_password_with_work_async

from .conftest import OWNER_PW, register
from .test_auth_extended import (
    forge_token,
    make_pbkdf2_hash,
    set_refresh_cookie,
    user_password_hash,
)


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


# ---------------------------------------------------------------------------
# Benign legacy→Argon2id rehash races (password_hash byte-equality misfires)
# ---------------------------------------------------------------------------
# FIXED — regression test
@pytest.mark.parametrize("operation", ["change-password", "account-delete"])
async def test_benign_login_rehash_race_does_not_401_account_password_flows(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    """change_password/delete_account snapshotted password_hash from
    CurrentUser, then compared it byte-for-byte after the Argon2 verify. A
    concurrent successful LOGIN transparently upgrades a legacy pbkdf2 hash to
    Argon2id (rewriting the bytes WITHOUT bumping token_version, on a
    different reservation scope, so the two genuinely interleave), which made
    the byte-equality check reject a correct, unchanged current password with
    401 'Session is no longer valid'. The gate is now token_version-only,
    matching create_farm's revalidation.
    Repro: legacy-hash user opens change-password; phone logs in during the
    Argon window → was 401, must be 200/204."""
    email = f"rehash-{operation}@farm.in"
    headers = await register(client, email)
    # Reinstate a v1 legacy hash for the SAME password without touching
    # token_version: the bearer token stays valid, and the next login will
    # transparently upgrade the stored hash mid-request.
    async with get_sessionmaker()() as db:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one()
        user_id = user.id
        user.password_hash = make_pbkdf2_hash(OWNER_PW)
        await db.commit()

    started = asyncio.Event()
    release = asyncio.Event()

    async def stalled_verify(password: str, stored: str) -> tuple[bool, bool]:
        started.set()
        await release.wait()
        return await real_verify_password_async(password, stored)

    # change-password/delete-account verify via verify_password_async; the
    # concurrent login below uses verify_password_with_work_async and is
    # unaffected by the stall.
    monkeypatch.setattr(auth_api, "verify_password_async", stalled_verify)

    if operation == "change-password":
        method, path = "POST", "/api/auth/change-password"
        payload: dict[str, str] = {
            "current_password": OWNER_PW,
            "new_password": "rehash-race-new-pass1",
        }
    else:
        method, path = "DELETE", "/api/auth/account"
        payload = {"current_password": OWNER_PW}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()),
        base_url="http://test",
    ) as contender:
        attempt = asyncio.create_task(
            contender.request(method, path, json=payload, headers=headers)
        )
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
            relogin = await client.post(
                "/api/auth/login", json={"email": email, "password": OWNER_PW}
            )
            assert relogin.status_code == 200, relogin.text
            # The race is real: the login upgraded the stored hash bytes.
            assert not (await user_password_hash(user_id)).startswith("pbkdf2_sha256$")
        finally:
            release.set()
        response = await attempt

    if operation == "change-password":
        assert response.status_code == 200, response.text
        assert verify_password("rehash-race-new-pass1", await user_password_hash(user_id))[0]
    else:
        assert response.status_code == 204, response.text
        async with get_sessionmaker()() as db:
            deleted = await db.get(User, user_id)
            assert deleted is not None
            assert deleted.deleted_at is not None
            assert deleted.email.endswith("@deleted.invalid")


# FIXED — regression test
async def test_benign_rehash_race_login_succeeds_without_charging_ledger(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """login() had the same byte-equality flaw against its own snapshot: under
    ``--workers N`` two concurrent correct-password logins for a legacy account
    both snapshot the pbkdf2 hash; the first upgrades it to Argon2id and
    commits, and the second's locked reload saw different bytes → bogus 401
    'Invalid email or password' AND _record_login_failure charged the victim's
    own brute-force counters. On a hash mismatch with an unchanged
    token_version the password is now re-verified against the reloaded hash,
    the login succeeds, no failure is recorded, and the winner's fresh Argon2
    material is kept rather than clobbered."""
    email = "rehash-login-race@farm.in"
    user_id = await _insert_user(email, make_pbkdf2_hash(OWNER_PW))

    started = asyncio.Event()
    release = asyncio.Event()

    async def stalled_verify_with_work(password: str, stored: str) -> tuple[bool, bool, bool]:
        started.set()
        await release.wait()
        return await real_verify_password_with_work_async(password, stored)

    monkeypatch.setattr(auth_api, "verify_password_with_work_async", stalled_verify_with_work)

    failures: list[str] = []
    real_record = auth_api._record_login_failure

    def spying_record(request: object, failed_email: str) -> None:
        failures.append(failed_email)
        real_record(request, failed_email)  # type: ignore[arg-type]

    monkeypatch.setattr(auth_api, "_record_login_failure", spying_record)

    upgraded_hash = hash_password(OWNER_PW)
    attempt = asyncio.create_task(
        client.post("/api/auth/login", json={"email": email, "password": OWNER_PW})
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        # The "other worker's" login wins: commit current Argon2id material
        # for the SAME credential without bumping token_version — exactly
        # what login's transparent upgrade does.
        async with get_sessionmaker()() as db:
            user = await db.get(User, user_id)
            assert user is not None
            user.password_hash = upgraded_hash
            await db.commit()
    finally:
        release.set()
    response = await attempt

    assert response.status_code == 200, response.text
    assert failures == []
    # The winner's fresh material is kept, not clobbered by a redundant rehash.
    assert await user_password_hash(user_id) == upgraded_hash


async def test_login_mid_flight_credential_change_still_rejected_and_charged(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Boundary guard for the rehash-race fix above: when the reloaded hash is
    NOT the same credential (a genuine change won mid-verify), login must
    still reject with the generic 401 and charge the failure ledger — the
    re-verification only forgives format-only rehashes of the typed password.
    """
    email = "rehash-login-changed@farm.in"
    user_id = await _insert_user(email, make_pbkdf2_hash(OWNER_PW))

    started = asyncio.Event()
    release = asyncio.Event()

    async def stalled_verify_with_work(password: str, stored: str) -> tuple[bool, bool, bool]:
        started.set()
        await release.wait()
        return await real_verify_password_with_work_async(password, stored)

    monkeypatch.setattr(auth_api, "verify_password_with_work_async", stalled_verify_with_work)

    failures: list[str] = []
    real_record = auth_api._record_login_failure

    def spying_record(request: object, failed_email: str) -> None:
        failures.append(failed_email)
        real_record(request, failed_email)  # type: ignore[arg-type]

    monkeypatch.setattr(auth_api, "_record_login_failure", spying_record)

    attempt = asyncio.create_task(
        client.post("/api/auth/login", json={"email": email, "password": OWNER_PW})
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        async with get_sessionmaker()() as db:
            user = await db.get(User, user_id)
            assert user is not None
            user.password_hash = hash_password("a-different-credential-123")
            await db.commit()
    finally:
        release.set()
    response = await attempt

    assert response.status_code == 401, response.text
    assert response.json()["detail"] == "Invalid email or password."
    assert failures == [email]


# ---------------------------------------------------------------------------
# Expired-but-genuine refresh cookies must not spend invalid-token budgets
# ---------------------------------------------------------------------------
@pytest.fixture()
def rate_limit_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Re-enable the limiter for one test (conftest disables it suite-wide)
    with a low ceiling, and leave no cached settings/state behind."""
    monkeypatch.setenv("GOATFARM_AUTH_RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("GOATFARM_AUTH_RATE_LIMIT_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("GOATFARM_AUTH_RATE_LIMIT_WINDOW_SECONDS", "300")
    get_settings.cache_clear()
    auth_limiter.clear()
    yield
    auth_limiter.clear()
    get_settings.cache_clear()


# FIXED — regression test
@pytest.mark.usefixtures("rate_limit_on")
async def test_expired_refresh_cookie_does_not_charge_refresh_invalid_budget(
    client: httpx.AsyncClient,
) -> None:
    """decode_refresh_claims collapses expired and forged into the same None,
    so /refresh charged an authentic-but-expired cookie to the per-IP
    'refresh-invalid' bucket — unlike expired ACCESS tokens, which
    deps.current_user deliberately exempts. With a 14-day cookie TTL, ~10
    returning users behind one NAT filled the bucket and 429'd colleagues'
    still-VALID refreshes. Authentic-but-expired now stays 401 without
    recording; forged material (even when also expired) is still charged.
    Repro: N expired-cookie refreshes then a valid one → valid was 429."""
    resp = await client.post(
        "/api/auth/register",
        json={"email": "expired-refresh-return@farm.in", "password": OWNER_PW},
    )
    assert resp.status_code == 201, resp.text
    user_id = resp.json()["user"]["id"]
    cookie_name = get_settings().refresh_cookie_name
    valid_cookie = client.cookies.get(cookie_name)
    assert valid_cookie
    limit = get_settings().auth_rate_limit_max_attempts
    window = get_settings().auth_rate_limit_window_seconds

    expired_cookie = forge_token(user_id, kind="refresh", ttl_seconds=-120)
    for _ in range(limit):
        set_refresh_cookie(client, expired_cookie)
        resp = await client.post("/api/auth/refresh")
        assert resp.status_code == 401, resp.text
        assert resp.json()["detail"] == "Invalid or expired refresh token"
    assert not auth_limiter.is_blocked("refresh-invalid", "127.0.0.1", limit, window)

    # A neighbor's perfectly valid refresh from the same NAT IP still works
    # (the old code returned 429 here — the exact collateral being prevented).
    set_refresh_cookie(client, valid_cookie)
    resp = await client.post("/api/auth/refresh")
    assert resp.status_code == 200, resp.text

    # The exemption is narrow: an expired token signed by an ATTACKER key is
    # not "genuine" and still lands on the refresh-invalid ledger.
    attacker_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged_expired = forge_token(user_id, kind="refresh", ttl_seconds=-120, key=attacker_key)
    set_refresh_cookie(client, forged_expired)
    resp = await client.post("/api/auth/refresh")
    assert resp.status_code == 401
    assert auth_limiter.is_blocked("refresh-invalid", "127.0.0.1", 1, window)


# FIXED — regression test
@pytest.mark.usefixtures("rate_limit_on")
async def test_logout_with_expired_refresh_cookie_is_never_charged(
    client: httpx.AsyncClient,
) -> None:
    """Logout classified an authentic-but-expired refresh cookie as invalid
    and recorded it against the logout-refresh invalid-token ledgers, while
    the sibling access-token branch already exempted verified-expired tokens.
    A returning user clearing local state (repeated logouts, no live session)
    must always get 204, never a 429 from their own expired cookie."""
    resp = await client.post(
        "/api/auth/register",
        json={"email": "expired-logout-return@farm.in", "password": OWNER_PW},
    )
    assert resp.status_code == 201, resp.text
    user_id = resp.json()["user"]["id"]
    limit = get_settings().auth_rate_limit_max_attempts

    expired_cookie = forge_token(user_id, kind="refresh", ttl_seconds=-120)
    for _ in range(limit + 1):
        set_refresh_cookie(client, expired_cookie)
        resp = await client.post("/api/auth/logout")
        # The old code 429'd once the per-token ledger filled at `limit`.
        assert resp.status_code == 204, resp.text
    digest = hashlib.sha256(expired_cookie.encode("utf-8")).hexdigest()
    assert (INVALID_LOGOUT_REFRESH_TOKEN_SCOPE + "-token", digest) not in auth_limiter._hits
