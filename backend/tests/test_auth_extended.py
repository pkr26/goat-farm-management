"""Extended auth & security tests: register, login (JWT pair), refresh-cookie
rotation backed by server-side session rows (consumption, reuse detection,
revocation on logout / password change / worker reset), logout,
change-password, /me, /permissions, farm listing/creation, plus unit-level
coverage of app.security (Argon2id hashing, legacy pbkdf2 upgrade, RS256 JWT
issue/decode).

Route level (httpx + real PostgreSQL) wherever the behavior is reachable
through the API; direct function tests for the crypto/password helpers.
JWT forgeries are built with the dev keypair in backend/keys/ or with
attacker-controlled keys, mirroring test_adversarial.py.
"""

import hashlib
import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import delete, func, select, update

from app.core.config import get_settings
from app.db import get_sessionmaker
from app.deps import deactivate_deleted_user_memberships, purge_expired_refresh_sessions
from app.models import FarmMembership, RefreshSession, User
from app.permissions import ALL_PERMISSIONS, ROLE_PRESETS
from app.ratelimit import auth_limiter
from app.security import (
    decode_refresh_claims,
    decode_token,
    hash_password,
    issue_access_token,
    issue_refresh_token,
    issue_token,
    password_policy_error,
    verify_password,
)
from app.utils import today

from .conftest import OWNER_PW, login, owner_with_farm, register

ALREADY_REGISTERED = "That email is already registered."
MIN_LEN = get_settings().min_password_length
COOKIE = get_settings().refresh_cookie_name

MOVER_PRESET = next(r for r in ROLE_PRESETS if r["code"] == "MOVER")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def forge_token(
    user_id: int | str,
    kind: str = "access",
    ttl_seconds: int = 600,
    key: object = None,
    algorithm: str = "RS256",
    **extra: object,
) -> str:
    """A JWT signed with the app's dev private key (or a caller-supplied key)."""
    now = datetime.now(UTC)
    claims = {
        "sub": str(user_id),
        "kind": kind,
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + timedelta(seconds=ttl_seconds),
        "iss": get_settings().jwt_issuer,
        "aud": get_settings().jwt_audience,
    } | extra
    if kind == "access" and "ver" not in claims:
        claims["ver"] = 0
    if key is None:
        key = get_settings().jwt_private_key_path.read_text()
    return jwt.encode(claims, key, algorithm=algorithm)


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def make_pbkdf2_hash(password: str, iterations: int = 2600) -> str:
    """A v1-format legacy hash: 'pbkdf2_sha256$iterations$salt_hex$digest_hex'."""
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


async def insert_user(email: str, password_hash: str, name: str | None = None) -> int:
    """Insert a User row directly (for legacy-hash and deleted-user scenarios)."""
    async with get_sessionmaker()() as db:
        user = User(email=email, name=name, password_hash=password_hash)
        db.add(user)
        await db.flush()
        user_id = user.id
        await db.commit()
        return user_id


async def user_password_hash(user_id: int) -> str:
    async with get_sessionmaker()() as db:
        user = await db.get(User, user_id)
        assert user is not None
        return user.password_hash


async def add_worker(
    client: httpx.AsyncClient,
    owner: dict,
    role_code: str,
    email: str,
    password: str = "workerpass123",
) -> int:
    """Owner adds a worker with a preset role; returns the membership id."""
    resp = await client.get("/api/team", headers=owner)
    assert resp.status_code == 200, resp.text
    role_id = next(r["id"] for r in resp.json()["roles"] if r["code"] == role_code)
    resp = await client.post(
        "/api/team/workers",
        json={"name": "Worker", "email": email, "password": password, "role_id": role_id},
        headers=owner,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def worker_login(
    client: httpx.AsyncClient, email: str, password: str = "workerpass123"
) -> dict:
    return await login(client, email, password)


def set_refresh_cookie(client: httpx.AsyncClient, token: str) -> None:
    client.cookies.clear()
    client.cookies.set(COOKIE, token)


# ---------------------------------------------------------------------------
# POST /api/auth/register — happy paths
# ---------------------------------------------------------------------------
async def test_register_returns_token_pair_and_user(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/auth/register", json={"email": "new@farm.in", "password": OWNER_PW, "name": "Ramu"}
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert isinstance(body["access_token"], str) and body["access_token"]
    assert body["user"]["email"] == "new@farm.in"
    assert body["user"]["name"] == "Ramu"
    assert isinstance(body["user"]["id"], int)


async def test_register_user_object_never_exposes_password(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/auth/register", json={"email": "safe@farm.in", "password": OWNER_PW}
    )
    assert resp.status_code == 201, resp.text
    user = resp.json()["user"]
    assert set(user) == {"id", "email", "name"}
    assert "password" not in user and "password_hash" not in user


async def test_register_sets_refresh_cookie(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/auth/register", json={"email": "cookie@farm.in", "password": OWNER_PW}
    )
    assert resp.status_code == 201, resp.text
    set_cookie = resp.headers["set-cookie"]
    assert f"{COOKIE}=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie
    assert "Path=/api/auth" in set_cookie
    assert f"Max-Age={get_settings().refresh_token_ttl_seconds}" in set_cookie


async def test_register_access_token_is_immediately_usable(client: httpx.AsyncClient) -> None:
    headers = await register(client, "usable@farm.in")
    resp = await client.get("/api/auth/me", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["email"] == "usable@farm.in"


async def test_register_normalizes_email_case_and_whitespace(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/auth/register", json={"email": "  Mixed.Case@Farm.IN  ", "password": OWNER_PW}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["user"]["email"] == "mixed.case@farm.in"
    # and the normalized address is what login expects
    headers = await login(client, "mixed.case@farm.in", OWNER_PW)
    resp = await client.get("/api/auth/me", headers=headers)
    assert resp.json()["email"] == "mixed.case@farm.in"


async def test_register_name_defaults_to_none(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/auth/register", json={"email": "noname@farm.in", "password": OWNER_PW}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["user"]["name"] is None


async def test_register_blank_and_whitespace_names_become_none(client: httpx.AsyncClient) -> None:
    for i, name in enumerate(("", "   ", "  \t ")):
        resp = await client.post(
            "/api/auth/register",
            json={"email": f"blank{i}@farm.in", "password": OWNER_PW, "name": name},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["user"]["name"] is None, name


async def test_register_strips_name_whitespace(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/auth/register",
        json={"email": "strip@farm.in", "password": OWNER_PW, "name": "  Ramu Goud  "},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["user"]["name"] == "Ramu Goud"


async def test_register_unicode_and_emoji_name(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/auth/register",
        json={"email": "uni@farm.in", "password": OWNER_PW, "name": "రాము 🐐 గౌడ్"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["user"]["name"] == "రాము 🐐 గౌడ్"


async def test_register_sql_injection_looking_strings_stored_literally(
    client: httpx.AsyncClient,
) -> None:
    email = "o'brien@farm.in"
    name = "Robert'); DROP TABLE users;--"
    resp = await client.post(
        "/api/auth/register", json={"email": email, "password": OWNER_PW, "name": name}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["user"]["name"] == name
    headers = await login(client, email, OWNER_PW)
    resp = await client.get("/api/auth/me", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["name"] == name


async def test_register_two_users_get_distinct_ids(client: httpx.AsyncClient) -> None:
    ids = set()
    for email in ("first@farm.in", "second@farm.in"):
        resp = await client.post("/api/auth/register", json={"email": email, "password": OWNER_PW})
        assert resp.status_code == 201, resp.text
        ids.add(resp.json()["user"]["id"])
    assert len(ids) == 2


async def test_register_rejects_unknown_extra_fields(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/auth/register",
        json={
            "email": "extra@farm.in",
            "password": OWNER_PW,
            "is_admin": True,
            "role": "owner",
        },
    )
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# POST /api/auth/register — password policy
# ---------------------------------------------------------------------------
async def test_register_rejects_short_password(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/auth/register", json={"email": "short@farm.in", "password": "a" * (MIN_LEN - 1)}
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == f"Password must be at least {MIN_LEN} characters."
    # no account created
    resp = await client.post(
        "/api/auth/login", json={"email": "short@farm.in", "password": "a" * (MIN_LEN - 1)}
    )
    assert resp.status_code == 401


async def test_register_accepts_exactly_min_length_password(client: httpx.AsyncClient) -> None:
    password = "a" * MIN_LEN
    resp = await client.post(
        "/api/auth/register", json={"email": "exact@farm.in", "password": password}
    )
    assert resp.status_code == 201, resp.text
    headers = await login(client, "exact@farm.in", password)
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 200


async def test_register_rejects_whitespace_only_password(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/auth/register", json={"email": "ws@farm.in", "password": " " * MIN_LEN}
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Password cannot be only whitespace."


async def test_register_password_with_inner_spaces_is_fine(client: httpx.AsyncClient) -> None:
    password = "goat farm 123"
    resp = await client.post(
        "/api/auth/register", json={"email": "spaces@farm.in", "password": password}
    )
    assert resp.status_code == 201, resp.text
    headers = await login(client, "spaces@farm.in", password)
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 200


async def test_register_unicode_password_roundtrip(client: httpx.AsyncClient) -> None:
    password = "pässwörd🐐123"
    resp = await client.post(
        "/api/auth/register", json={"email": "unipw@farm.in", "password": password}
    )
    assert resp.status_code == 201, resp.text
    headers = await login(client, "unipw@farm.in", password)
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 200


async def test_register_very_long_password(client: httpx.AsyncClient) -> None:
    password = "g0at!" * 25 + "x" * 3  # 128 chars — exactly the cap (B6)
    resp = await client.post(
        "/api/auth/register", json={"email": "longpw@farm.in", "password": password}
    )
    assert resp.status_code == 201, resp.text
    headers = await login(client, "longpw@farm.in", password)
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 200
    resp = await client.post(
        "/api/auth/register", json={"email": "toolong@farm.in", "password": "g0at!" * 26}
    )
    assert resp.status_code == 422  # 130 chars — beyond the cap


# ---------------------------------------------------------------------------
# POST /api/auth/register — duplicate emails and validation
# ---------------------------------------------------------------------------
async def test_register_duplicate_email_rejected(client: httpx.AsyncClient) -> None:
    await register(client, "dup@farm.in")
    resp = await client.post(
        "/api/auth/register", json={"email": "dup@farm.in", "password": "otherpass123"}
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == ALREADY_REGISTERED


async def test_register_duplicate_email_case_insensitive(client: httpx.AsyncClient) -> None:
    await register(client, "dupe@farm.in")
    resp = await client.post(
        "/api/auth/register", json={"email": "DUPE@FARM.IN", "password": "otherpass123"}
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == ALREADY_REGISTERED


async def test_register_duplicate_email_with_padding_rejected(client: httpx.AsyncClient) -> None:
    await register(client, "pad@farm.in")
    resp = await client.post(
        "/api/auth/register", json={"email": "  pad@farm.in  ", "password": "otherpass123"}
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == ALREADY_REGISTERED


async def test_register_validation_garbage_payloads(client: httpx.AsyncClient) -> None:
    for payload in [
        {},  # everything missing
        {"password": OWNER_PW},  # no email
        {"email": "x@farm.in"},  # no password
        {"email": "x@farm.in", "password": ""},  # empty password
        {"email": "no-at-sign", "password": OWNER_PW},
        {"email": "   ", "password": OWNER_PW},
        {"email": "", "password": OWNER_PW},
        {"email": 123, "password": OWNER_PW},
        {"email": None, "password": OWNER_PW},
        {"email": "x@farm.in", "password": 12345678},
        {"email": "x@farm.in", "password": None},
        {"email": "x@farm.in", "password": OWNER_PW, "name": 42},
        {"email": ["x@farm.in"], "password": OWNER_PW},
    ]:
        resp = await client.post("/api/auth/register", json=payload)
        assert resp.status_code == 422, payload


async def test_register_non_json_body_is_422(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/auth/register",
        content=b"email=x@farm.in&password=ownerpass123",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 422


async def test_register_name_at_db_limit(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/auth/register",
        json={"email": "maxname@farm.in", "password": OWNER_PW, "name": "N" * 120},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["user"]["name"] == "N" * 120


# ---------------------------------------------------------------------------
# POST /api/auth/login
# ---------------------------------------------------------------------------
async def test_login_happy_path(client: httpx.AsyncClient) -> None:
    await register(client, "happy@farm.in", name="Happy")
    resp = await client.post(
        "/api/auth/login", json={"email": "happy@farm.in", "password": OWNER_PW}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["email"] == "happy@farm.in"
    assert body["user"]["name"] == "Happy"
    assert COOKIE in resp.headers["set-cookie"]


async def test_login_token_works_on_me(client: httpx.AsyncClient) -> None:
    await register(client, "tok@farm.in")
    headers = await login(client, "tok@farm.in", OWNER_PW)
    resp = await client.get("/api/auth/me", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["email"] == "tok@farm.in"


async def test_login_wrong_password(client: httpx.AsyncClient) -> None:
    await register(client, "wp@farm.in")
    resp = await client.post(
        "/api/auth/login", json={"email": "wp@farm.in", "password": "wrongpass123"}
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid email or password."


async def test_login_unknown_email_same_message(client: httpx.AsyncClient) -> None:
    """No user enumeration: unknown email and wrong password are identical."""
    await register(client, "known@farm.in")
    unknown = await client.post(
        "/api/auth/login", json={"email": "ghost@farm.in", "password": OWNER_PW}
    )
    wrong_pw = await client.post(
        "/api/auth/login", json={"email": "known@farm.in", "password": "nope12345"}
    )
    assert unknown.status_code == wrong_pw.status_code == 401
    assert unknown.json() == wrong_pw.json()


async def test_login_email_is_case_insensitive(client: httpx.AsyncClient) -> None:
    await register(client, "case@farm.in")
    resp = await client.post(
        "/api/auth/login", json={"email": "CASE@FARM.IN", "password": OWNER_PW}
    )
    assert resp.status_code == 200, resp.text


async def test_login_email_whitespace_trimmed(client: httpx.AsyncClient) -> None:
    await register(client, "trim@farm.in")
    resp = await client.post(
        "/api/auth/login", json={"email": "  trim@farm.in  ", "password": OWNER_PW}
    )
    assert resp.status_code == 200, resp.text


async def test_login_empty_password_is_401_not_422(client: httpx.AsyncClient) -> None:
    """LoginIn.password has no min_length — an empty password simply fails."""
    await register(client, "emptypw@farm.in")
    resp = await client.post("/api/auth/login", json={"email": "emptypw@farm.in", "password": ""})
    assert resp.status_code == 401


async def test_login_before_register_is_401(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/auth/login", json={"email": "never@farm.in", "password": OWNER_PW}
    )
    assert resp.status_code == 401


async def test_login_sql_injection_email(client: httpx.AsyncClient) -> None:
    await register(client, "real@farm.in")
    resp = await client.post(
        "/api/auth/login", json={"email": "' OR '1'='1' -- @x.in", "password": OWNER_PW}
    )
    assert resp.status_code == 422  # rejected before limiter/SQL lookup


async def test_login_validation_garbage_payloads(client: httpx.AsyncClient) -> None:
    for payload in [
        {},
        {"email": "x@farm.in"},
        {"password": OWNER_PW},
        {"email": "not-an-email", "password": OWNER_PW},
        {"email": None, "password": OWNER_PW},
        {"email": "x@farm.in", "password": None},
        {"email": 7, "password": OWNER_PW},
    ]:
        resp = await client.post("/api/auth/login", json=payload)
        assert resp.status_code == 422, payload


async def test_login_rejects_unknown_extra_fields(client: httpx.AsyncClient) -> None:
    await register(client, "xtra@farm.in")
    resp = await client.post(
        "/api/auth/login",
        json={"email": "xtra@farm.in", "password": OWNER_PW, "remember_me": True},
    )
    assert resp.status_code == 422, resp.text


async def test_login_password_is_exact_match(client: httpx.AsyncClient) -> None:
    """Trailing/leading spaces and case flips in the password must fail."""
    await register(client, "exactpw@farm.in", password=OWNER_PW)
    for bad in (OWNER_PW + " ", " " + OWNER_PW, OWNER_PW.upper()):
        resp = await client.post(
            "/api/auth/login", json={"email": "exactpw@farm.in", "password": bad}
        )
        assert resp.status_code == 401, bad


# ---------------------------------------------------------------------------
# Legacy pbkdf2 → Argon2id upgrade path (login rehash)
# ---------------------------------------------------------------------------
async def test_legacy_pbkdf2_login_upgrades_hash(client: httpx.AsyncClient) -> None:
    """v1-migrated user logs in with a pbkdf2 hash; the row is transparently
    re-hashed to Argon2id and later logins keep working."""
    user_id = await insert_user("legacy@farm.in", make_pbkdf2_hash(OWNER_PW))
    resp = await client.post(
        "/api/auth/login", json={"email": "legacy@farm.in", "password": OWNER_PW}
    )
    assert resp.status_code == 200, resp.text
    upgraded = await user_password_hash(user_id)
    assert upgraded.startswith("$argon2id$")
    # second login goes down the Argon2id path and still works
    headers = await login(client, "legacy@farm.in", OWNER_PW)
    resp = await client.get("/api/auth/me", headers=headers)
    assert resp.json()["email"] == "legacy@farm.in"


async def test_legacy_pbkdf2_wrong_password_keeps_hash(client: httpx.AsyncClient) -> None:
    stored = make_pbkdf2_hash(OWNER_PW)
    user_id = await insert_user("legacy2@farm.in", stored)
    resp = await client.post(
        "/api/auth/login", json={"email": "legacy2@farm.in", "password": "wrongpass123"}
    )
    assert resp.status_code == 401
    assert await user_password_hash(user_id) == stored  # untouched


async def test_legacy_pbkdf2_malformed_hashes_never_500(client: httpx.AsyncClient) -> None:
    for i, stored in enumerate(
        [
            "pbkdf2_sha256$only$three",  # wrong segment count
            "pbkdf2_sha256$notanint$abcd$1234",  # non-integer iterations
            "pbkdf2_sha256$2600$zzzz$digest",  # bad salt hex
            "pbkdf2_sha256$2600$aabb$nothexdigest!",  # bad digest hex
            "pbkdf2_sha256$",  # empty tail
        ]
    ):
        await insert_user(f"malformed{i}@farm.in", stored)
        resp = await client.post(
            "/api/auth/login", json={"email": f"malformed{i}@farm.in", "password": OWNER_PW}
        )
        assert resp.status_code == 401, stored


async def test_garbage_argon2_hash_login_is_401_not_500(client: httpx.AsyncClient) -> None:
    await insert_user("garbage@farm.in", "$argon2id$corrupted-hash-body")
    resp = await client.post(
        "/api/auth/login", json={"email": "garbage@farm.in", "password": OWNER_PW}
    )
    assert resp.status_code == 401


async def test_legacy_pbkdf2_high_iteration_count_still_verifies(
    client: httpx.AsyncClient,
) -> None:
    user_id = await insert_user("legacy3@farm.in", make_pbkdf2_hash(OWNER_PW, iterations=50000))
    resp = await client.post(
        "/api/auth/login", json={"email": "legacy3@farm.in", "password": OWNER_PW}
    )
    assert resp.status_code == 200, resp.text
    assert (await user_password_hash(user_id)).startswith("$argon2id$")


# ---------------------------------------------------------------------------
# POST /api/auth/refresh — rotating refresh cookie
# ---------------------------------------------------------------------------
async def test_refresh_without_cookie_is_401(client: httpx.AsyncClient) -> None:
    resp = await client.post("/api/auth/refresh")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid or expired refresh token"


async def test_refresh_with_valid_cookie_returns_new_tokens(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/auth/register", json={"email": "ref@farm.in", "password": OWNER_PW}
    )
    assert resp.status_code == 201, resp.text
    resp = await client.post("/api/auth/refresh")  # jar holds the register cookie
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["email"] == "ref@farm.in"
    headers = bearer(body["access_token"])
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 200


async def test_refresh_rotates_the_cookie(client: httpx.AsyncClient) -> None:
    await register(client, "rot@farm.in")
    old = client.cookies.get(COOKIE)
    assert old
    resp = await client.post("/api/auth/refresh")
    assert resp.status_code == 200, resp.text
    new = client.cookies.get(COOKIE)
    assert new and new != old  # fresh jti ⇒ fresh token
    # the rotated cookie works for the next refresh
    resp = await client.post("/api/auth/refresh")
    assert resp.status_code == 200, resp.text


async def test_refresh_old_token_reuse_revokes_family(client: httpx.AsyncClient) -> None:
    """Replaying a rotated-away refresh token is a theft signal (RFC 6819
    §5.2.2.3): the presented jti is already consumed, so the request 401s AND
    the whole rotation family is revoked — the legitimate client's current
    token dies with it, forcing re-login everywhere."""
    await register(client, "reuse@farm.in")
    old = client.cookies.get(COOKIE)
    resp = await client.post("/api/auth/refresh")
    assert resp.status_code == 200, resp.text
    rotated = client.cookies.get(COOKIE)
    assert rotated and rotated != old
    old_claims = decode_refresh_claims(old)
    assert old_claims is not None
    # Immediate duplicate delivery is the concurrent-tab grace case. Age the
    # consumed row beyond that narrow window to exercise theft detection.
    async with get_sessionmaker()() as db:
        await db.execute(
            update(RefreshSession)
            .where(RefreshSession.jti == old_claims.jti)
            .values(
                consumed_at=datetime.now(UTC).replace(tzinfo=None)
                - timedelta(seconds=get_settings().refresh_reuse_grace_seconds + 1)
            )
        )
        await db.commit()
    set_refresh_cookie(client, old)  # attacker replays the pre-rotation token
    resp = await client.post("/api/auth/refresh")
    assert resp.status_code == 401
    set_refresh_cookie(client, rotated)  # the legitimate successor is dead too
    resp = await client.post("/api/auth/refresh")
    assert resp.status_code == 401
    # and the account can still log in fresh (revocation ≠ lockout)
    await login(client, "reuse@farm.in", OWNER_PW)


async def test_refresh_rotation_and_family_history_are_hard_bounded(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "refresh_max_sessions_per_family", 3)
    await register(client, "bounded-rotation@farm.in")
    first = client.cookies.get(COOKIE)
    assert first is not None

    predecessor = first
    for _index in range(6):
        predecessor = client.cookies.get(COOKIE)
        response = await client.post("/api/auth/refresh")
        assert response.status_code == 200, response.text
    successor = client.cookies.get(COOKIE)
    assert predecessor is not None and successor is not None

    async with get_sessionmaker()() as db:
        user_id = (
            await db.execute(select(User.id).where(User.email == "bounded-rotation@farm.in"))
        ).scalar_one()
        sessions = list(
            (
                await db.execute(
                    select(RefreshSession)
                    .where(RefreshSession.user_id == user_id)
                    .order_by(RefreshSession.created_at, RefreshSession.id)
                )
            ).scalars()
        )
    assert len(sessions) == 3
    assert len({session.family_id for session in sessions}) == 1

    # The just-consumed predecessor remains inside the finite window, so the
    # normal concurrent-tab grace still reconstructs the exact successor.
    set_refresh_cookie(client, predecessor)
    grace = await client.post("/api/auth/refresh")
    assert grace.status_code == 200, grace.text
    assert grace.cookies.get(COOKIE) == successor

    # A much older compacted token still carries a signed family id. Replaying
    # it revokes the current family even though its individual row is gone.
    first_claims = decode_refresh_claims(first)
    assert first_claims is not None and first_claims.family_id is not None
    set_refresh_cookie(client, first)
    replay = await client.post("/api/auth/refresh")
    assert replay.status_code == 401
    set_refresh_cookie(client, successor)
    assert (await client.post("/api/auth/refresh")).status_code == 401


async def test_successful_logins_evict_old_refresh_families_at_hard_cap(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "refresh_max_families_per_user", 2)
    await register(client, "bounded-families@farm.in")
    oldest = client.cookies.get(COOKIE)
    assert oldest is not None
    await login(client, "bounded-families@farm.in", OWNER_PW)
    await login(client, "bounded-families@farm.in", OWNER_PW)
    newest = client.cookies.get(COOKIE)
    assert newest is not None

    async with get_sessionmaker()() as db:
        user_id = (
            await db.execute(select(User.id).where(User.email == "bounded-families@farm.in"))
        ).scalar_one()
        rows = list(
            (
                await db.execute(select(RefreshSession).where(RefreshSession.user_id == user_id))
            ).scalars()
        )
    assert len({row.family_id for row in rows}) == 2
    oldest_claims = decode_refresh_claims(oldest)
    assert oldest_claims is not None
    assert all(row.family_id != oldest_claims.family_id for row in rows)

    # Replaying an evicted family cannot affect a different current family.
    set_refresh_cookie(client, oldest)
    assert (await client.post("/api/auth/refresh")).status_code == 401
    set_refresh_cookie(client, newest)
    assert (await client.post("/api/auth/refresh")).status_code == 200


async def test_expired_refresh_cleanup_is_ordered_and_strictly_batched(
    client: httpx.AsyncClient,
) -> None:
    await register(client, "bounded-cleanup@farm.in")
    async with get_sessionmaker()() as db:
        user_id = (
            await db.execute(select(User.id).where(User.email == "bounded-cleanup@farm.in"))
        ).scalar_one()
        old = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=60)
        db.add_all(
            [
                RefreshSession(
                    user_id=user_id,
                    jti=f"expired-{index}",
                    family_id="expired-family",
                    expires_at=old + timedelta(seconds=index),
                    created_at=old,
                )
                for index in range(7)
            ]
        )
        await db.commit()

    async with get_sessionmaker()() as db:
        assert await purge_expired_refresh_sessions(db, batch_size=3) == 3
        await db.commit()
    async with get_sessionmaker()() as db:
        remaining = (
            await db.execute(
                select(func.count())
                .select_from(RefreshSession)
                .where(RefreshSession.family_id == "expired-family")
            )
        ).scalar_one()
        assert remaining == 4
        assert await purge_expired_refresh_sessions(db, batch_size=3) == 3
        await db.commit()


async def test_immediate_refresh_replay_returns_exact_successor(
    client: httpx.AsyncClient,
) -> None:
    """Two tabs can send the same cookie before either sees the rotation.
    The loser gets the already-created successor instead of revoking the
    legitimate family."""
    resp = await client.post(
        "/api/auth/register", json={"email": "tabs@farm.in", "password": OWNER_PW}
    )
    user_id = resp.json()["user"]["id"]
    old = client.cookies.get(COOKIE)
    assert old

    assert (await client.post("/api/auth/refresh")).status_code == 200
    successor = client.cookies.get(COOKIE)
    assert successor and successor != old

    set_refresh_cookie(client, old)
    replay = await client.post("/api/auth/refresh")
    assert replay.status_code == 200, replay.text
    assert replay.cookies.get(COOKIE) == successor

    async with get_sessionmaker()() as db:
        rows = list(
            (
                await db.execute(select(RefreshSession).where(RefreshSession.user_id == user_id))
            ).scalars()
        )
    assert len(rows) == 2
    assert sum(r.consumed_at is None and r.revoked_at is None for r in rows) == 1
    assert all(r.revoked_at is None for r in rows)


async def test_refresh_with_unknown_jti_is_401(client: httpx.AsyncClient) -> None:
    """A correctly signed refresh token whose jti has no live session row
    (forged with the dev key, or issued before session tracking) is refused."""
    resp = await client.post(
        "/api/auth/register", json={"email": "unknownjti@farm.in", "password": OWNER_PW}
    )
    user_id = resp.json()["user"]["id"]
    set_refresh_cookie(client, forge_token(user_id, kind="refresh"))
    resp = await client.post("/api/auth/refresh")
    assert resp.status_code == 401


async def test_refresh_consumes_the_presented_session_row(client: httpx.AsyncClient) -> None:
    """Rotation leaves exactly one unconsumed, unrevoked row in the family."""
    resp = await client.post(
        "/api/auth/register", json={"email": "rows@farm.in", "password": OWNER_PW}
    )
    user_id = resp.json()["user"]["id"]
    resp = await client.post("/api/auth/refresh")
    assert resp.status_code == 200, resp.text
    async with get_sessionmaker()() as db:
        rows = list(
            (
                await db.execute(select(RefreshSession).where(RefreshSession.user_id == user_id))
            ).scalars()
        )
    assert len(rows) == 2
    assert len({r.family_id for r in rows}) == 1  # one rotation family
    live = [r for r in rows if r.consumed_at is None and r.revoked_at is None]
    assert len(live) == 1  # only the freshly rotated token is usable


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


@pytest.mark.usefixtures("rate_limit_on")
async def test_refresh_is_rate_limited_per_ip(client: httpx.AsyncClient) -> None:
    """/refresh was unthrottled — token-grinding attempts from one
    IP now trip the same sliding-window limiter as login/register."""
    for _ in range(3):
        set_refresh_cookie(client, "garbage")
        resp = await client.post("/api/auth/refresh")
        assert resp.status_code == 401
    set_refresh_cookie(client, "garbage")
    resp = await client.post("/api/auth/refresh")
    assert resp.status_code == 429
    assert resp.headers["Retry-After"] == str(get_settings().auth_rate_limit_window_seconds)
    assert "Too many" in resp.json()["detail"]


@pytest.mark.usefixtures("rate_limit_on")
async def test_successful_refreshes_do_not_consume_invalid_attempt_budget(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.post(
        "/api/auth/register", json={"email": "reloads@farm.in", "password": OWNER_PW}
    )
    assert resp.status_code == 201, resp.text
    valid_cookie = client.cookies.get(COOKIE)
    assert valid_cookie

    # Fill the rejected-token budget for this shared IP.
    for _ in range(3):
        set_refresh_cookie(client, "garbage")
        assert (await client.post("/api/auth/refresh")).status_code == 401

    # A legitimate page reload is still allowed and rotates normally; repeated
    # successes stay allowed rather than eventually forcing a login redirect.
    set_refresh_cookie(client, valid_cookie)
    for _ in range(8):
        resp = await client.post("/api/auth/refresh")
        assert resp.status_code == 200, resp.text


async def test_refresh_malformed_cookie_is_401(client: httpx.AsyncClient) -> None:
    for bad in ("garbage", "a.b.c", "", "null", "eyJhbGciOiJIUzI1NiJ9"):
        set_refresh_cookie(client, bad)
        resp = await client.post("/api/auth/refresh")
        assert resp.status_code == 401, bad


async def test_refresh_access_token_as_cookie_is_401(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/auth/register", json={"email": "kind@farm.in", "password": OWNER_PW}
    )
    access = resp.json()["access_token"]
    set_refresh_cookie(client, access)  # right user, wrong token kind
    resp = await client.post("/api/auth/refresh")
    assert resp.status_code == 401


async def test_refresh_expired_token_is_401(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/auth/register", json={"email": "expref@farm.in", "password": OWNER_PW}
    )
    user_id = resp.json()["user"]["id"]
    set_refresh_cookie(client, forge_token(user_id, kind="refresh", ttl_seconds=-60))
    resp = await client.post("/api/auth/refresh")
    assert resp.status_code == 401


async def test_refresh_tampered_token_is_401(client: httpx.AsyncClient) -> None:
    await register(client, "tamp@farm.in")
    token = client.cookies.get(COOKIE)
    head, payload, sig = token.split(".")
    forged = f"{head}.{payload}.{sig[:-4]}{'A' if sig[-4] != 'A' else 'B'}aaa"
    set_refresh_cookie(client, forged)
    resp = await client.post("/api/auth/refresh")
    assert resp.status_code == 401


async def test_refresh_foreign_key_token_is_401(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/auth/register", json={"email": "fk@farm.in", "password": OWNER_PW}
    )
    user_id = resp.json()["user"]["id"]
    attacker_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    set_refresh_cookie(client, forge_token(user_id, kind="refresh", key=attacker_key))
    resp = await client.post("/api/auth/refresh")
    assert resp.status_code == 401


async def test_refresh_hs256_alg_confusion_is_401(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/auth/register", json={"email": "hs@farm.in", "password": OWNER_PW}
    )
    user_id = resp.json()["user"]["id"]
    pub_pem = get_settings().jwt_public_key_path.read_bytes()
    pub_key = serialization.load_pem_public_key(pub_pem)
    pub_der = pub_key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    set_refresh_cookie(client, forge_token(user_id, kind="refresh", key=pub_der, algorithm="HS256"))
    resp = await client.post("/api/auth/refresh")
    assert resp.status_code == 401


async def test_refresh_alg_none_is_401(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/auth/register", json={"email": "none@farm.in", "password": OWNER_PW}
    )
    user_id = resp.json()["user"]["id"]
    now = datetime.now(UTC)
    unsigned = jwt.encode(
        {
            "sub": str(user_id),
            "kind": "refresh",
            "jti": "x",
            "iat": now,
            "exp": now + timedelta(seconds=600),
        },
        None,
        algorithm="none",
    )
    set_refresh_cookie(client, unsigned)
    resp = await client.post("/api/auth/refresh")
    assert resp.status_code == 401


async def test_refresh_for_deleted_user_is_401(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/auth/register", json={"email": "gone@farm.in", "password": OWNER_PW}
    )
    user_id = resp.json()["user"]["id"]
    async with get_sessionmaker()() as db:
        await db.execute(delete(User).where(User.id == user_id))
        await db.commit()
    set_refresh_cookie(client, forge_token(user_id, kind="refresh"))
    resp = await client.post("/api/auth/refresh")
    assert resp.status_code == 401


async def test_refresh_token_for_nonexistent_user_id_is_401(client: httpx.AsyncClient) -> None:
    set_refresh_cookie(client, forge_token(999999, kind="refresh"))
    resp = await client.post("/api/auth/refresh")
    assert resp.status_code == 401


async def test_refresh_token_with_non_integer_sub_is_401(client: httpx.AsyncClient) -> None:
    set_refresh_cookie(client, forge_token("not-a-number", kind="refresh"))
    resp = await client.post("/api/auth/refresh")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/auth/logout
# ---------------------------------------------------------------------------
async def test_logout_clears_cookie(client: httpx.AsyncClient) -> None:
    await register(client, "out@farm.in")
    assert client.cookies.get(COOKIE)
    resp = await client.post("/api/auth/logout")
    assert resp.status_code == 204
    set_cookie = resp.headers["set-cookie"]
    assert f"{COOKIE}=" in set_cookie
    assert f'{COOKIE}=""' in set_cookie or f"{COOKIE}=;" in set_cookie
    assert not client.cookies.get(COOKIE)  # jar dropped it


async def test_logout_without_cookie_is_204(client: httpx.AsyncClient) -> None:
    resp = await client.post("/api/auth/logout")
    assert resp.status_code == 204


async def test_logout_needs_no_authorization(client: httpx.AsyncClient) -> None:
    resp = await client.post("/api/auth/logout", headers={"Authorization": "Bearer garbage"})
    assert resp.status_code == 204


async def test_refresh_after_logout_is_401(client: httpx.AsyncClient) -> None:
    await register(client, "out2@farm.in")
    resp = await client.post("/api/auth/logout")
    assert resp.status_code == 204
    resp = await client.post("/api/auth/refresh")  # jar is empty now
    assert resp.status_code == 401


async def test_logout_revokes_the_presented_session(client: httpx.AsyncClient) -> None:
    """Logout is server-side, not just a cookie delete — replaying
    the exfiltrated refresh token afterwards is refused."""
    await register(client, "out3@farm.in")
    stolen = client.cookies.get(COOKIE)
    resp = await client.post("/api/auth/logout")
    assert resp.status_code == 204
    set_refresh_cookie(client, stolen)
    resp = await client.post("/api/auth/refresh")
    assert resp.status_code == 401


async def test_logout_revokes_successor_when_presented_refresh_already_rotated(
    client: httpx.AsyncClient,
) -> None:
    """Model the refresh-wins serialization order deterministically.

    A refresh commits its successor first; logout then presents the consumed
    predecessor. The 204 must revoke the complete family, including that
    successor, rather than only marking the predecessor.
    """
    await register(client, "refresh-wins-logout@farm.in")
    predecessor = client.cookies.get(COOKIE)
    assert predecessor
    rotated = await client.post("/api/auth/refresh")
    assert rotated.status_code == 200, rotated.text
    successor = client.cookies.get(COOKIE)
    assert successor and successor != predecessor

    set_refresh_cookie(client, predecessor)
    assert (await client.post("/api/auth/logout")).status_code == 204
    set_refresh_cookie(client, successor)
    assert (await client.post("/api/auth/refresh")).status_code == 401


async def test_bearer_only_logout_revokes_every_refresh_family(
    client: httpx.AsyncClient,
) -> None:
    headers = await register(client, "bearer-only-logout@farm.in")
    refresh_cookie = client.cookies.get(COOKIE)
    assert refresh_cookie
    client.cookies.clear()
    assert (await client.post("/api/auth/logout", headers=headers)).status_code == 204

    # Merely advancing token_version would let this family mint a new access
    # token carrying that version and undo the logout. Bearer-only logout has
    # no family proof, so it revokes all bounded sessions for the identity.
    set_refresh_cookie(client, refresh_cookie)
    assert (await client.post("/api/auth/refresh")).status_code == 401


async def test_logout_invalidates_already_issued_access_token(client: httpx.AsyncClient) -> None:
    headers = await register(client, "accessout@farm.in")
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 200
    assert (await client.post("/api/auth/logout", headers=headers)).status_code == 204
    resp = await client.get("/api/auth/me", headers=headers)
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Session has been revoked"


# ---------------------------------------------------------------------------
# POST /api/auth/change-password
# ---------------------------------------------------------------------------
async def test_change_password_happy_path_revokes_other_sessions(
    client: httpx.AsyncClient,
) -> None:
    """A change needs the current password, rotates the hash,
    and kills every outstanding refresh session; the changing device gets a
    fresh pair in the response."""
    headers = await register(client, "chg@farm.in")
    pre_change_cookie = client.cookies.get(COOKIE)
    resp = await client.post(
        "/api/auth/change-password",
        json={"current_password": OWNER_PW, "new_password": "newpass1234"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    # The access token used to authorize the change is revoked immediately.
    stale = await client.get("/api/auth/me", headers=headers)
    assert stale.status_code == 401
    assert stale.json()["detail"] == "Session has been revoked"
    # fresh pair for this device, immediately usable
    assert (
        await client.get("/api/auth/me", headers=bearer(resp.json()["access_token"]))
    ).status_code == 200
    resp = await client.post("/api/auth/refresh")  # jar holds the fresh cookie
    assert resp.status_code == 200, resp.text
    # the pre-change session is revoked
    set_refresh_cookie(client, pre_change_cookie)
    assert (await client.post("/api/auth/refresh")).status_code == 401
    # old password dies, new one works
    resp = await client.post("/api/auth/login", json={"email": "chg@farm.in", "password": OWNER_PW})
    assert resp.status_code == 401
    await login(client, "chg@farm.in", "newpass1234")


async def test_change_password_wrong_current_password(client: httpx.AsyncClient) -> None:
    headers = await register(client, "chgwrong@farm.in")
    cookie_before = client.cookies.get(COOKIE)
    resp = await client.post(
        "/api/auth/change-password",
        json={"current_password": "notmypass123", "new_password": "newpass1234"},
        headers=headers,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Current password is incorrect."
    # nothing changed: old password and the live session still work
    await login(client, "chgwrong@farm.in", OWNER_PW)
    set_refresh_cookie(client, cookie_before)
    assert (await client.post("/api/auth/refresh")).status_code == 200


@pytest.mark.usefixtures("rate_limit_on")
async def test_change_password_wrong_current_password_is_rate_limited(
    client: httpx.AsyncClient,
) -> None:
    headers = await register(client, "chg-rate@farm.in")
    payload = {"current_password": "wrong-password", "new_password": "newpass1234"}
    for _ in range(3):
        resp = await client.post("/api/auth/change-password", json=payload, headers=headers)
        assert resp.status_code == 400
    resp = await client.post("/api/auth/change-password", json=payload, headers=headers)
    assert resp.status_code == 429
    assert resp.headers["Retry-After"] == str(get_settings().auth_rate_limit_window_seconds)


async def test_change_password_rejects_reusing_current_password(
    client: httpx.AsyncClient,
) -> None:
    headers = await register(client, "chg-reuse@farm.in")
    cookie_before = client.cookies.get(COOKIE)
    resp = await client.post(
        "/api/auth/change-password",
        json={"current_password": OWNER_PW, "new_password": OWNER_PW},
        headers=headers,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "New password must be different from the current password."
    # A rejected no-op does not revoke the caller's access or refresh session.
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 200
    set_refresh_cookie(client, cookie_before)
    assert (await client.post("/api/auth/refresh")).status_code == 200


async def test_change_password_policy_applies_to_new_password(
    client: httpx.AsyncClient,
) -> None:
    headers = await register(client, "chgpol@farm.in")
    resp = await client.post(
        "/api/auth/change-password",
        json={"current_password": OWNER_PW, "new_password": "short7c"},
        headers=headers,
    )
    assert resp.status_code == 400
    assert "at least" in resp.json()["detail"]


async def test_change_password_requires_auth(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/auth/change-password",
        json={"current_password": OWNER_PW, "new_password": "newpass1234"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/auth/me
# ---------------------------------------------------------------------------
async def test_me_happy_path(client: httpx.AsyncClient) -> None:
    headers = await register(client, "me@farm.in", name="Meena")
    resp = await client.get("/api/auth/me", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "me@farm.in"
    assert body["name"] == "Meena"
    assert set(body) == {"id", "email", "name"}


async def test_account_export_is_machine_readable_and_tenant_minimal(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="owner@farm.in", farm_name="Private Farm")
    team = (await client.get("/api/team", headers=owner)).json()
    role = next(role for role in team["roles"] if role["code"] == "CLEANER")
    created = await client.post(
        "/api/team/workers",
        json={
            "email": "exporter@farm.in",
            "password": "workerpass123",
            "role_id": role["id"],
        },
        headers=owner,
    )
    assert created.status_code == 201, created.text
    worker_id = created.json()["user_id"]
    task = await client.post(
        "/api/tasks",
        json={
            "title": "Commercially sensitive treatment note",
            "due_date": today().isoformat(),
            "assigned_user_id": worker_id,
        },
        headers=owner,
    )
    assert task.status_code == 201, task.text

    worker = await login(client, "exporter@farm.in", "workerpass123")
    resp = await client.get("/api/auth/account/export", headers=worker)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-disposition"].startswith("attachment;")
    body = resp.json()
    assert body["account"]["email"] == "exporter@farm.in"
    assert body["owned_farms"] == []
    assert body["memberships"] == [
        {
            "farm_id": int(owner["X-Farm-Id"]),
            "farm_name": "Private Farm",
            "role_id": role["id"],
            "role_name": "Cleaner",
            "is_active": True,
            "created_at": body["memberships"][0]["created_at"],
        }
    ]
    # Relationship metadata is personal data; the tenant's operational record
    # and sensitive task title are deliberately absent.
    assert "Commercially sensitive" not in resp.text


async def test_account_export_includes_owned_farm_metadata(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client, farm_name="My Farm")
    resp = await client.get("/api/auth/account/export", headers=owner)
    assert resp.status_code == 200, resp.text
    assert [farm["name"] for farm in resp.json()["owned_farms"]] == ["My Farm"]


async def test_account_delete_requires_password_rejects_owners_and_cleans_worker(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="owner@farm.in", farm_name="Alpha Farm")
    # Owners must explicitly dispose of or transfer tenant data first.
    resp = await client.request(
        "DELETE",
        "/api/auth/account",
        json={"current_password": OWNER_PW},
        headers=owner,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == (
        "Account deletion is unavailable while this account owns a farm; "
        "farm ownership cannot currently be transferred or deleted."
    )

    team = (await client.get("/api/team", headers=owner)).json()
    role = next(role for role in team["roles"] if role["code"] == "CLEANER")
    created = await client.post(
        "/api/team/workers",
        json={
            "email": "departing@farm.in",
            "password": "workerpass123",
            "role_id": role["id"],
        },
        headers=owner,
    )
    assert created.status_code == 201, created.text
    worker_id = created.json()["user_id"]
    membership_id = created.json()["id"]
    duty = await client.post(
        "/api/tasks",
        json={
            "title": "Still needs doing",
            "due_date": today().isoformat(),
            "assigned_user_id": worker_id,
        },
        headers=owner,
    )
    assert duty.status_code == 201, duty.text

    worker = await login(client, "departing@farm.in", "workerpass123")
    stolen_refresh = client.cookies.get(COOKIE)
    wrong = await client.request(
        "DELETE",
        "/api/auth/account",
        json={"current_password": "wrongpass123"},
        headers=worker,
    )
    assert wrong.status_code == 400
    assert (await client.get("/api/auth/me", headers=worker)).status_code == 200

    deleted = await client.request(
        "DELETE",
        "/api/auth/account",
        json={"current_password": "workerpass123"},
        headers=worker,
    )
    assert deleted.status_code == 204, deleted.text
    assert not client.cookies.get(COOKIE)
    assert (await client.get("/api/auth/me", headers=worker)).status_code == 401
    assert (
        await client.post(
            "/api/auth/login",
            json={"email": "departing@farm.in", "password": "workerpass123"},
        )
    ).status_code == 401
    set_refresh_cookie(client, stolen_refresh)
    assert (await client.post("/api/auth/refresh")).status_code == 401

    async with get_sessionmaker()() as db:
        tombstone = await db.get(User, worker_id)
        assert tombstone is not None
        assert tombstone.deleted_at is not None
        assert tombstone.name is None
        assert tombstone.email.startswith(f"deleted-{worker_id}-")
        assert tombstone.email.endswith("@deleted.invalid")
        assert not verify_password("workerpass123", tombstone.password_hash)[0]
        assert tombstone.display_name == "Deleted account"
        retained_membership = await db.get(FarmMembership, membership_id)
        assert retained_membership is not None
        # Deletion never enumerates an account's tenant history. The User
        # tombstone already revokes access; retained memberships converge in
        # bounded background batches.
        assert retained_membership.is_active is True
        assert retained_membership.role_id == role["id"]
        sessions = (
            (await db.execute(select(RefreshSession).where(RefreshSession.user_id == worker_id)))
            .scalars()
            .all()
        )
        assert sessions == []
        assert await deactivate_deleted_user_memberships(db, batch_size=10) == 1
        await db.commit()
        await db.refresh(retained_membership)
        assert retained_membership.is_active is False

    tasks = (await client.get("/api/tasks", headers=owner)).json()
    duty_id = duty.json()["id"]
    reassigned = next(
        row
        for rows in tasks.values()
        if isinstance(rows, list)
        for row in rows
        if row["id"] == duty_id
    )
    assert reassigned["assigned_user_id"] == worker_id
    assert reassigned["assigned_role_id"] == role["id"]

    # The scrubbed address is reusable, but starts a completely new identity;
    # retained farm attribution continues pointing at the tombstone above.
    registered_again = await client.post(
        "/api/auth/register",
        json={"email": "departing@farm.in", "password": "replacementpass123"},
    )
    assert registered_again.status_code == 201, registered_again.text
    assert registered_again.json()["user"]["id"] != worker_id


async def test_me_without_authorization_is_401(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Missing bearer token"


async def test_me_authorization_scheme_variants(client: httpx.AsyncClient) -> None:
    headers = await register(client, "scheme@farm.in")
    token = headers["Authorization"].removeprefix("Bearer ")
    for header_value in [
        token,  # no scheme at all
        "Bearer",  # scheme without token
        "Bearer ",  # trailing space, empty token
        f"bearer {token}",  # lowercase scheme
        f"BEARER {token}",  # uppercase scheme
        f"Token {token}",  # wrong scheme
        f"Bearer {token} extra",  # trailing garbage
        f"Bearer  {token}",  # double space
    ]:
        resp = await client.get("/api/auth/me", headers={"Authorization": header_value})
        assert resp.status_code == 401, header_value


async def test_me_malformed_token_is_401(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/auth/me", headers=bearer("not-a-jwt"))
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid or expired token"


async def test_me_refresh_token_is_not_an_access_token(client: httpx.AsyncClient) -> None:
    await register(client, "kindme@farm.in")
    refresh_token = client.cookies.get(COOKIE)
    resp = await client.get("/api/auth/me", headers=bearer(refresh_token))
    assert resp.status_code == 401


async def test_me_expired_access_token_is_401(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/auth/register", json={"email": "exp@farm.in", "password": OWNER_PW}
    )
    user_id = resp.json()["user"]["id"]
    resp = await client.get("/api/auth/me", headers=bearer(forge_token(user_id, ttl_seconds=-60)))
    assert resp.status_code == 401


async def test_me_tampered_token_is_401(client: httpx.AsyncClient) -> None:
    headers = await register(client, "tamper@farm.in")
    token = headers["Authorization"].removeprefix("Bearer ")
    head, payload, sig = token.split(".")
    # flip a char mid-signature (the last char only holds base64 padding bits)
    i = len(sig) // 2
    flipped = "A" if sig[i] != "A" else "B"
    forged = f"{head}.{payload}.{sig[:i]}{flipped}{sig[i + 1 :]}"
    resp = await client.get("/api/auth/me", headers=bearer(forged))
    assert resp.status_code == 401


async def test_me_foreign_key_token_is_401(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/auth/register", json={"email": "fkme@farm.in", "password": OWNER_PW}
    )
    user_id = resp.json()["user"]["id"]
    attacker_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    resp = await client.get("/api/auth/me", headers=bearer(forge_token(user_id, key=attacker_key)))
    assert resp.status_code == 401


async def test_me_token_with_non_integer_sub_is_401(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/auth/me", headers=bearer(forge_token("abc")))
    assert resp.status_code == 401


async def test_me_token_for_nonexistent_user_is_401(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/auth/me", headers=bearer(forge_token(424242)))
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Account no longer exists"


async def test_me_deleted_account_token_is_401(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/auth/register", json={"email": "del@farm.in", "password": OWNER_PW}
    )
    body = resp.json()
    headers = bearer(body["access_token"])
    async with get_sessionmaker()() as db:
        await db.execute(delete(User).where(User.id == body["user"]["id"]))
        await db.commit()
    resp = await client.get("/api/auth/me", headers=headers)
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Account no longer exists"


# ---------------------------------------------------------------------------
# GET /api/auth/permissions
# ---------------------------------------------------------------------------
async def test_permissions_owner_gets_everything(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await client.get("/api/auth/permissions", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_owner"] is True
    assert body["permissions"] == sorted(ALL_PERMISSIONS)
    assert body["permissions"] == sorted(body["permissions"])


async def test_permissions_worker_gets_role_bundle(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await add_worker(client, owner, "MOVER", "mover@farm.in")
    worker = (await worker_login(client, "mover@farm.in")) | {"X-Farm-Id": owner["X-Farm-Id"]}
    resp = await client.get("/api/auth/permissions", headers=worker)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_owner"] is False
    assert body["permissions"] == sorted(MOVER_PRESET["permissions"])
    assert "finance.view" not in body["permissions"]


async def test_permissions_without_auth_is_401(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/auth/permissions")
    assert resp.status_code == 401


async def test_permissions_without_farm_header_is_422(client: httpx.AsyncClient) -> None:
    headers = await register(client, "nofarm@farm.in")
    resp = await client.get("/api/auth/permissions", headers=headers)
    # The header is required by the contract (422, not 400).
    assert resp.status_code == 422
    assert "x-farm-id" in str(resp.json()["detail"]).lower()


async def test_permissions_farm_header_must_be_integer(client: httpx.AsyncClient) -> None:
    headers = await register(client, "int@farm.in")
    for bad in ("abc", "1.5", "1e3", "", "12a"):
        resp = await client.get("/api/auth/permissions", headers=headers | {"X-Farm-Id": bad})
        assert resp.status_code == 400, bad
        assert resp.json()["detail"] == "X-Farm-Id must be an integer"


async def test_permissions_farm_header_out_of_range(client: httpx.AsyncClient) -> None:
    headers = await register(client, "range@farm.in")
    for bad in ("0", "-1", str(-(2**62)), str(2**62), str(2**62 + 1), str(10**30)):
        resp = await client.get("/api/auth/permissions", headers=headers | {"X-Farm-Id": bad})
        assert resp.status_code == 400, bad
        assert resp.json()["detail"] == "X-Farm-Id out of range"


async def test_permissions_nonexistent_farm_is_404(client: httpx.AsyncClient) -> None:
    headers = await register(client, "ghost@farm.in")
    resp = await client.get("/api/auth/permissions", headers=headers | {"X-Farm-Id": "999999"})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Farm not found"


async def test_permissions_other_users_farm_is_404(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, email="pa@farm.in", farm_name="Farm A")
    headers_b = await register(client, "pb@farm.in")
    resp = await client.get(
        "/api/auth/permissions", headers=headers_b | {"X-Farm-Id": owner_a["X-Farm-Id"]}
    )
    # Forbidden farms answer exactly like unknown ones.
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Farm not found"


async def test_permissions_deactivated_worker_loses_access(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    membership_id = await add_worker(client, owner, "VET", "vet@farm.in")
    worker = (await worker_login(client, "vet@farm.in")) | {"X-Farm-Id": owner["X-Farm-Id"]}
    assert (await client.get("/api/auth/permissions", headers=worker)).status_code == 200
    resp = await client.put(
        f"/api/team/workers/{membership_id}/status",
        json={"is_active": False},
        headers=owner,
    )
    assert resp.status_code == 200, resp.text
    resp = await client.get("/api/auth/permissions", headers=worker)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Farm not found"
    assert (await client.get("/api/auth/me", headers=worker)).status_code == 200


async def test_permissions_owner_of_two_farms(client: httpx.AsyncClient) -> None:
    headers = await register(client, "two@farm.in")
    for name in ("Farm One", "Farm Two"):
        resp = await client.post("/api/auth/farms", json={"name": name}, headers=headers)
        assert resp.status_code == 201, resp.text
        farm_headers = headers | {"X-Farm-Id": str(resp.json()["id"])}
        perms = await client.get("/api/auth/permissions", headers=farm_headers)
        assert perms.status_code == 200, resp.text
        assert perms.json()["is_owner"] is True


# ---------------------------------------------------------------------------
# GET /api/auth/farms
# ---------------------------------------------------------------------------
async def test_farms_empty_for_new_user(client: httpx.AsyncClient) -> None:
    headers = await register(client, "empty@farm.in")
    resp = await client.get("/api/auth/farms", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []


async def test_farms_lists_owned_farms_with_none_role(client: httpx.AsyncClient) -> None:
    headers = await register(client, "own2@farm.in")
    created = []
    for name in ("Alpha", "Beta"):
        resp = await client.post("/api/auth/farms", json={"name": name}, headers=headers)
        assert resp.status_code == 201, resp.text
        created.append(resp.json()["id"])
    resp = await client.get("/api/auth/farms", headers=headers)
    assert resp.status_code == 200
    farms = resp.json()
    assert [f["id"] for f in farms] == created
    assert {f["name"] for f in farms} == {"Alpha", "Beta"}
    assert all(f["role"] is None for f in farms)  # None = owner
    assert all(set(f) == {"id", "name", "location", "timezone", "role"} for f in farms)


async def test_affiliation_list_and_export_fail_before_unbounded_hydration(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = await register(client, "bounded-affiliations@farm.in")
    for name in ("Bound One", "Bound Two"):
        created = await client.post("/api/auth/farms", json={"name": name}, headers=headers)
        assert created.status_code == 201, created.text
    monkeypatch.setattr(get_settings(), "max_account_affiliations_per_response", 1)

    for path in ("/api/auth/farms", "/api/auth/account/export"):
        response = await client.get(path, headers=headers)
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == (
            "Account has too many farm affiliations to return safely."
        )


async def test_farms_never_lists_other_users_farms(client: httpx.AsyncClient) -> None:
    await owner_with_farm(client, email="iso1@farm.in", farm_name="Secret Farm")
    headers = await register(client, "iso2@farm.in")
    resp = await client.get("/api/auth/farms", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []


async def test_farms_lists_membership_with_role_name(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await add_worker(client, owner, "MOVER", "member@farm.in")
    worker = await worker_login(client, "member@farm.in")
    resp = await client.get("/api/auth/farms", headers=worker)
    assert resp.status_code == 200
    farms = resp.json()
    assert len(farms) == 1
    assert farms[0]["id"] == int(owner["X-Farm-Id"])
    assert farms[0]["role"] == MOVER_PRESET["name"]  # "Animal Mover"


async def test_farms_owned_first_then_memberships(client: httpx.AsyncClient) -> None:
    """A user who owns a farm AND works on another sees owned farms first."""
    owner = await owner_with_farm(client, email="boss@farm.in", farm_name="Boss Farm")
    await add_worker(client, owner, "FEEDER", "both@farm.in")
    worker = await worker_login(client, "both@farm.in")
    resp = await client.post("/api/auth/farms", json={"name": "My Own"}, headers=worker)
    assert resp.status_code == 201, resp.text
    own_id = resp.json()["id"]
    resp = await client.get("/api/auth/farms", headers=worker)
    farms = resp.json()
    assert [f["id"] for f in farms] == [own_id, int(owner["X-Farm-Id"])]
    assert farms[0]["role"] is None
    assert farms[1]["role"] == "Feeder"


async def test_farms_excludes_deactivated_membership(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    membership_id = await add_worker(client, owner, "CLEANER", "off@farm.in")
    worker = await worker_login(client, "off@farm.in")
    assert len((await client.get("/api/auth/farms", headers=worker)).json()) == 1
    resp = await client.put(
        f"/api/team/workers/{membership_id}/status",
        json={"is_active": False},
        headers=owner,
    )
    assert resp.status_code == 200, resp.text
    resp = await client.get("/api/auth/farms", headers=worker)
    assert resp.status_code == 200
    assert resp.json() == []


async def test_farms_without_auth_is_401(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/auth/farms")
    assert resp.status_code == 401


async def test_farms_needs_no_farm_header(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    bare = {"Authorization": headers["Authorization"]}  # no X-Farm-Id at all
    resp = await client.get("/api/auth/farms", headers=bare)
    assert resp.status_code == 200
    assert len(resp.json()) == 1


# ---------------------------------------------------------------------------
# POST /api/auth/farms
# ---------------------------------------------------------------------------
async def test_create_farm_happy_path(client: httpx.AsyncClient) -> None:
    headers = await register(client, "cf@farm.in")
    resp = await client.post(
        "/api/auth/farms",
        json={"name": "Green Pastures", "location": "Nalgonda"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Green Pastures"
    assert body["location"] == "Nalgonda"
    assert body["role"] is None
    assert isinstance(body["id"], int)


async def test_create_farm_location_optional(client: httpx.AsyncClient) -> None:
    headers = await register(client, "noloc@farm.in")
    resp = await client.post("/api/auth/farms", json={"name": "No Loc"}, headers=headers)
    assert resp.status_code == 201, resp.text
    assert resp.json()["location"] is None


async def test_create_farm_blank_location_becomes_none(client: httpx.AsyncClient) -> None:
    headers = await register(client, "blankloc@farm.in")
    for location in ("", "   "):
        resp = await client.post(
            "/api/auth/farms", json={"name": "Farm", "location": location}, headers=headers
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["location"] is None, location


async def test_create_farm_strips_name_and_location(client: httpx.AsyncClient) -> None:
    headers = await register(client, "stripf@farm.in")
    resp = await client.post(
        "/api/auth/farms",
        json={"name": "  Kurnool Goat Farm  ", "location": "  Kurnool  "},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["name"] == "Kurnool Goat Farm"
    assert resp.json()["location"] == "Kurnool"


async def test_create_farm_unicode_emoji_name(client: httpx.AsyncClient) -> None:
    headers = await register(client, "unif@farm.in")
    resp = await client.post("/api/auth/farms", json={"name": "మేకల ఫార్మ్ 🐐"}, headers=headers)
    assert resp.status_code == 201, resp.text
    assert resp.json()["name"] == "మేకల ఫార్మ్ 🐐"


async def test_create_farm_sql_injection_name_stored_literally(client: httpx.AsyncClient) -> None:
    headers = await register(client, "sqlf@farm.in")
    name = "Farm'); DROP TABLE farms;--"
    resp = await client.post("/api/auth/farms", json={"name": name}, headers=headers)
    assert resp.status_code == 201, resp.text
    assert resp.json()["name"] == name
    farms = (await client.get("/api/auth/farms", headers=headers)).json()
    assert farms[0]["name"] == name  # table intact, value stored as text


async def test_create_farm_name_length_boundaries(client: httpx.AsyncClient) -> None:
    headers = await register(client, "bounds@farm.in")
    resp = await client.post("/api/auth/farms", json={"name": "N" * 120}, headers=headers)
    assert resp.status_code == 201, resp.text  # schema max is 120
    assert resp.json()["name"] == "N" * 120
    resp = await client.post("/api/auth/farms", json={"name": "N" * 121}, headers=headers)
    assert resp.status_code == 422
    resp = await client.post("/api/auth/farms", json={"name": "N" * 10_000}, headers=headers)
    assert resp.status_code == 422


async def test_create_farm_validation_garbage_payloads(client: httpx.AsyncClient) -> None:
    headers = await register(client, "fg@farm.in")
    for payload in [
        {},
        {"name": ""},
        {"name": None},
        {"name": 123},
        {"name": ["Farm"]},
        {"location": "L" * 201, "name": "Farm"},  # location schema max is 200
        {"name": "Farm", "location": 42},
    ]:
        resp = await client.post("/api/auth/farms", json=payload, headers=headers)
        assert resp.status_code == 422, payload
    assert (await client.get("/api/auth/farms", headers=headers)).json() == []


async def test_create_farm_location_at_db_limit(client: httpx.AsyncClient) -> None:
    headers = await register(client, "loc120@farm.in")
    resp = await client.post(
        "/api/auth/farms",
        json={"name": "Farm", "location": "L" * 120},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["location"] == "L" * 120


async def test_create_farm_rejects_unknown_extra_fields(client: httpx.AsyncClient) -> None:
    headers = await register(client, "extraf@farm.in")
    resp = await client.post(
        "/api/auth/farms",
        json={"name": "Farm", "owner_id": 999, "id": 5},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text
    assert (await client.get("/api/auth/farms", headers=headers)).json() == []


async def test_create_farm_without_auth_is_401(client: httpx.AsyncClient) -> None:
    resp = await client.post("/api/auth/farms", json={"name": "Farm"})
    assert resp.status_code == 401


async def test_create_farm_multiple_per_user_and_same_name_allowed(
    client: httpx.AsyncClient,
) -> None:
    headers = await register(client, "multi@farm.in")
    for _ in range(2):
        resp = await client.post("/api/auth/farms", json={"name": "Twin"}, headers=headers)
        assert resp.status_code == 201, resp.text
    farms = (await client.get("/api/auth/farms", headers=headers)).json()
    assert len(farms) == 2
    assert farms[0]["id"] != farms[1]["id"]


async def test_create_farm_seeds_preset_roles(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await client.get("/api/team", headers=headers)
    assert resp.status_code == 200, resp.text
    codes = {r["code"] for r in resp.json()["roles"]}
    assert {"MOVER", "VET", "CLEANER", "CLEANER_MANAGER", "FEEDER"} <= codes


async def test_create_farm_seeds_feed_inventory(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await client.get("/api/feeding/inventory", headers=headers)
    assert resp.status_code == 200, resp.text
    ingredients = {i["ingredient"] for i in resp.json()}
    assert "Mineral mix" in ingredients
    assert len(ingredients) >= 5


async def test_create_farm_is_immediately_usable(client: httpx.AsyncClient) -> None:
    headers = await register(client, "use@farm.in")
    resp = await client.post("/api/auth/farms", json={"name": "Usable"}, headers=headers)
    assert resp.status_code == 201, resp.text
    farm_headers = headers | {"X-Farm-Id": str(resp.json()["id"])}
    resp = await client.get("/api/buckets", headers=farm_headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 10  # global bucket definitions visible


async def test_created_farm_appears_in_farms_list(client: httpx.AsyncClient) -> None:
    headers = await register(client, "listed@farm.in")
    resp = await client.post(
        "/api/auth/farms", json={"name": "Listed", "location": "Warangal"}, headers=headers
    )
    farm_id = resp.json()["id"]
    farms = (await client.get("/api/auth/farms", headers=headers)).json()
    farm = next(f for f in farms if f["id"] == farm_id)
    assert farm["name"] == "Listed"
    assert farm["location"] == "Warangal"
    assert farm["role"] is None


# ---------------------------------------------------------------------------
# app.security — password policy & hashing (unit level)
# ---------------------------------------------------------------------------
def test_password_policy_error_boundaries() -> None:
    assert password_policy_error("a" * MIN_LEN) is None
    assert password_policy_error("a" * (MIN_LEN - 1)) == (
        f"Password must be at least {MIN_LEN} characters."
    )
    assert password_policy_error("") == f"Password must be at least {MIN_LEN} characters."
    assert password_policy_error(None) == f"Password must be at least {MIN_LEN} characters."
    assert password_policy_error(" " * MIN_LEN) == "Password cannot be only whitespace."
    assert password_policy_error("ok password 1") is None


def test_hash_password_produces_argon2id_with_salt() -> None:
    h1 = hash_password("some password 1")
    h2 = hash_password("some password 1")
    assert h1.startswith("$argon2id$")
    assert h2.startswith("$argon2id$")
    assert h1 != h2  # random salt per hash


def test_verify_password_argon2_roundtrip() -> None:
    stored = hash_password("correct horse 1")
    assert verify_password("correct horse 1", stored) == (True, False)
    assert verify_password("wrong horse 1", stored) == (False, False)
    assert verify_password("", stored) == (False, False)


def test_verify_password_legacy_pbkdf2_flags_rehash() -> None:
    stored = make_pbkdf2_hash("legacy secret 1")
    assert verify_password("legacy secret 1", stored) == (True, True)
    assert verify_password("nope wrong 1", stored) == (False, True)


def test_verify_password_legacy_malformed_never_raises() -> None:
    for stored in [
        "pbkdf2_sha256$only$three",
        "pbkdf2_sha256$NaN$abcd$1234",
        "pbkdf2_sha256$100$zz$digest",
        "pbkdf2_sha256$",
    ]:
        assert verify_password("anything", stored) == (False, True), stored


def test_verify_password_malformed_argon2_never_raises() -> None:
    assert verify_password("anything", "$argon2id$broken") == (False, False)


# ---------------------------------------------------------------------------
# app.security — JWT issue/decode (unit level)
# ---------------------------------------------------------------------------
def test_decode_token_access_roundtrip() -> None:
    token = issue_access_token(42)
    assert decode_token(token, "access") == 42


def test_decode_token_refresh_roundtrip() -> None:
    token = issue_refresh_token(7)
    assert decode_token(token, "refresh") == 7


def test_decode_refresh_claims_roundtrip() -> None:
    claims = decode_refresh_claims(issue_refresh_token(7, jti="abc123", family_id="family-123"))
    assert claims is not None
    assert claims.user_id == 7
    assert claims.jti == "abc123"
    assert claims.family_id == "family-123"
    assert claims.expires_at > datetime.now(UTC).replace(tzinfo=None)


def test_decode_refresh_claims_rejects_bad_tokens() -> None:
    assert decode_refresh_claims("garbage") is None
    assert decode_refresh_claims(issue_access_token(1)) is None  # wrong kind
    assert decode_refresh_claims(forge_token(1, kind="refresh", ttl_seconds=-60)) is None


def test_decode_token_rejects_wrong_kind() -> None:
    assert decode_token(issue_access_token(1), "refresh") is None
    assert decode_token(issue_refresh_token(1), "access") is None


def test_decode_token_rejects_expired() -> None:
    # jwt.decode is called with leeway=60 s,
    # so a token more than 60 s past exp is needed to hit the reject path.
    token = issue_token(1, "access", ttl_seconds=-120)
    assert decode_token(token, "access") is None


def test_decode_token_rejects_garbage() -> None:
    for bad in ("", "abc", "a.b.c", "🐐.🐐.🐐", "null"):
        assert decode_token(bad, "access") is None, bad


def test_decode_token_rejects_foreign_key_signature() -> None:
    attacker_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = forge_token(1, key=attacker_key)
    assert decode_token(forged, "access") is None


def test_decode_token_rejects_hs256_alg_confusion() -> None:
    pub_pem = get_settings().jwt_public_key_path.read_bytes()
    pub_key = serialization.load_pem_public_key(pub_pem)
    pub_der = pub_key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    confused = forge_token(1, key=pub_der, algorithm="HS256")
    assert decode_token(confused, "access") is None


def test_decode_token_rejects_alg_none() -> None:
    now = datetime.now(UTC)
    unsigned = jwt.encode(
        {"sub": "1", "kind": "access", "jti": "x", "iat": now, "exp": now + timedelta(seconds=60)},
        None,
        algorithm="none",
    )
    assert decode_token(unsigned, "access") is None


def test_decode_token_rejects_non_integer_and_missing_sub() -> None:
    assert decode_token(forge_token("abc"), "access") is None
    token = forge_token(1)
    # re-issue without sub at all
    now = datetime.now(UTC)
    no_sub = jwt.encode(
        {"kind": "access", "jti": "x", "iat": now, "exp": now + timedelta(seconds=60)},
        get_settings().jwt_private_key_path.read_text(),
        algorithm="RS256",
    )
    assert decode_token(no_sub, "access") is None
    assert token  # sanity: the helper produced something


def test_decode_token_rejects_tampered_payload() -> None:
    token = issue_access_token(1)
    head, payload, sig = token.split(".")
    forged = f"{head}.{payload[:-2]}xx.{sig}"
    assert decode_token(forged, "access") is None


def test_access_token_claims_shape() -> None:
    token = issue_access_token(99)
    payload = jwt.decode(
        token,
        get_settings().jwt_public_key_path.read_text(),
        algorithms=[get_settings().jwt_algorithm],
        audience=get_settings().jwt_audience,
        issuer=get_settings().jwt_issuer,
    )
    assert payload["sub"] == "99"
    assert payload["kind"] == "access"
    assert payload["iss"] == get_settings().jwt_issuer
    assert payload["aud"] == get_settings().jwt_audience
    assert payload["jti"]
    assert payload["exp"] > payload["iat"]
    ttl = payload["exp"] - payload["iat"]
    assert ttl == get_settings().access_token_ttl_seconds


def test_refresh_token_ttl_matches_settings() -> None:
    token = issue_refresh_token(99)
    payload = jwt.decode(
        token,
        get_settings().jwt_public_key_path.read_text(),
        algorithms=[get_settings().jwt_algorithm],
        audience=get_settings().jwt_audience,
        issuer=get_settings().jwt_issuer,
    )
    assert payload["kind"] == "refresh"
    assert payload["exp"] - payload["iat"] == get_settings().refresh_token_ttl_seconds


def test_tokens_have_unique_jti() -> None:
    public = get_settings().jwt_public_key_path.read_text()
    jtis = set()
    for _ in range(5):
        payload = jwt.decode(
            issue_access_token(1),
            public,
            algorithms=["RS256"],
            audience=get_settings().jwt_audience,
            issuer=get_settings().jwt_issuer,
        )
        jtis.add(payload["jti"])
    assert len(jtis) == 5


# ---------------------------------------------------------------------------
# Wave 2 — cross-cutting token/cookie behavior
# ---------------------------------------------------------------------------
async def test_login_sets_refresh_cookie_with_same_attributes(
    client: httpx.AsyncClient,
) -> None:
    await register(client, "logincookie@farm.in")
    client.cookies.clear()
    resp = await client.post(
        "/api/auth/login", json={"email": "logincookie@farm.in", "password": OWNER_PW}
    )
    assert resp.status_code == 200, resp.text
    set_cookie = resp.headers["set-cookie"]
    assert f"{COOKIE}=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie
    assert "Path=/api/auth" in set_cookie
    # the login cookie is usable for refresh
    resp = await client.post("/api/auth/refresh")
    assert resp.status_code == 200, resp.text


async def test_refresh_rejects_cross_site_origin_without_consuming_session(
    client: httpx.AsyncClient,
) -> None:
    await register(client, "csrf-refresh@farm.in")
    rejected = await client.post(
        "/api/auth/refresh",
        headers={"Origin": "https://evil.example.com", "Sec-Fetch-Site": "same-site"},
    )
    assert rejected.status_code == 403, rejected.text
    assert rejected.json()["detail"] == "Untrusted origin for cookie-authenticated request."

    # Rejection happens before rotation: the original cookie remains usable.
    accepted = await client.post(
        "/api/auth/refresh",
        headers={"Origin": get_settings().cors_origins[0]},
    )
    assert accepted.status_code == 200, accepted.text


async def test_cookie_auth_rejects_cross_site_fetch_metadata(
    client: httpx.AsyncClient,
) -> None:
    await register(client, "csrf-fetch-metadata@farm.in")
    rejected = await client.post(
        "/api/auth/logout",
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert rejected.status_code == 403, rejected.text
    assert (await client.post("/api/auth/refresh")).status_code == 200


async def test_logout_twice_is_still_204(client: httpx.AsyncClient) -> None:
    await register(client, "twice@farm.in")
    assert (await client.post("/api/auth/logout")).status_code == 204
    assert (await client.post("/api/auth/logout")).status_code == 204


async def test_register_login_refresh_tokens_are_distinct(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/auth/register", json={"email": "distinct@farm.in", "password": OWNER_PW}
    )
    t1 = resp.json()["access_token"]
    resp = await client.post(
        "/api/auth/login", json={"email": "distinct@farm.in", "password": OWNER_PW}
    )
    t2 = resp.json()["access_token"]
    resp = await client.post("/api/auth/refresh")
    t3 = resp.json()["access_token"]
    assert len({t1, t2, t3}) == 3  # unique jti ⇒ unique tokens


async def test_refresh_response_user_matches_me(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/auth/register",
        json={"email": "match@farm.in", "password": OWNER_PW, "name": "Matcher"},
    )
    registered_user = resp.json()["user"]
    resp = await client.post("/api/auth/refresh")
    assert resp.status_code == 200, resp.text
    assert resp.json()["user"] == registered_user
    headers = bearer(resp.json()["access_token"])
    assert (await client.get("/api/auth/me", headers=headers)).json() == registered_user


async def test_refresh_cookies_are_per_user(client: httpx.AsyncClient) -> None:
    """Refreshing with user A's cookie yields user A's tokens, not user B's."""
    resp = await client.post(
        "/api/auth/register", json={"email": "usera@farm.in", "password": OWNER_PW}
    )
    cookie_a = client.cookies.get(COOKIE)
    id_a = resp.json()["user"]["id"]
    resp = await client.post(
        "/api/auth/register", json={"email": "userb@farm.in", "password": OWNER_PW}
    )
    id_b = resp.json()["user"]["id"]
    set_refresh_cookie(client, cookie_a)
    resp = await client.post("/api/auth/refresh")
    assert resp.status_code == 200, resp.text
    assert resp.json()["user"]["id"] == id_a != id_b


async def test_farm_header_with_surrounding_whitespace_parses(client: httpx.AsyncClient) -> None:
    """int() tolerates whitespace, so '  <id> ' is accepted — documents the
    (harmless) parsing quirk of deps.current_farm."""
    headers = await owner_with_farm(client, email="wsint@farm.in")
    padded = headers | {"X-Farm-Id": f"  {headers['X-Farm-Id']}  "}
    resp = await client.get("/api/auth/permissions", headers=padded)
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_owner"] is True


async def test_farms_list_isolated_between_two_owners(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, email="fa@farm.in", farm_name="Farm A")
    owner_b = await owner_with_farm(client, email="fb@farm.in", farm_name="Farm B")
    farms_a = (await client.get("/api/auth/farms", headers=owner_a)).json()
    farms_b = (await client.get("/api/auth/farms", headers=owner_b)).json()
    assert [f["name"] for f in farms_a] == ["Farm A"]
    assert [f["name"] for f in farms_b] == ["Farm B"]
    assert farms_a[0]["id"] != farms_b[0]["id"]


async def test_create_farm_non_json_body_is_422(client: httpx.AsyncClient) -> None:
    headers = await register(client, "nonjson@farm.in")
    resp = await client.post(
        "/api/auth/farms",
        content=b"name=Farm",
        headers=headers | {"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 422


def test_decode_token_rejects_wrong_case_kind_and_missing_kind() -> None:
    assert decode_token(forge_token(1, kind="Access"), "access") is None
    now = datetime.now(UTC)
    no_kind = jwt.encode(
        {"sub": "1", "jti": "x", "iat": now, "exp": now + timedelta(seconds=60)},
        get_settings().jwt_private_key_path.read_text(),
        algorithm="RS256",
    )
    assert decode_token(no_kind, "access") is None


@pytest.mark.parametrize("missing", ["sub", "kind", "jti", "iat", "exp", "iss", "aud"])
def test_decode_token_rejects_every_missing_required_claim(missing: str) -> None:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": "1",
        "kind": "access",
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "iss": get_settings().jwt_issuer,
        "aud": get_settings().jwt_audience,
        "ver": 0,
    }
    del claims[missing]
    token = jwt.encode(
        claims,
        get_settings().jwt_private_key_path.read_text(),
        algorithm="RS256",
    )
    assert decode_token(token, "access") is None


def test_access_claims_require_strict_revocation_version() -> None:
    from app.security import decode_access_claims

    assert decode_access_claims(forge_token(1, ver="0")) is None
    assert decode_access_claims(forge_token(1, ver=True)) is None


def test_decode_token_rejects_wrong_issuer_or_audience() -> None:
    assert decode_token(forge_token(1, iss="another-api"), "access") is None
    assert decode_token(forge_token(1, aud="another-client"), "access") is None


def test_issue_token_refuses_reserved_claim_override() -> None:
    with pytest.raises(ValueError, match="reserved JWT claims"):
        issue_token(1, "access", 60, extra_claims={"sub": "2"})
