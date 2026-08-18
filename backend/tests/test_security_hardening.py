"""Security-hardening regression tests.

B1 (critical): cross-tenant account takeover — `POST /api/team/workers`
    absorbed ANY registered account without consent and
    `POST /api/team/workers/{id}/reset-password` rewrote that account's
    GLOBAL password, handing the attacker the victim's access to other farms.
B6 (medium): auth surface — login timing oracle for user enumeration, no
    password length cap, no login/register rate limiting, unbounded farms
    per user.
B7 (medium): JWT key handling — PEM files re-read from disk on every
    sign/verify, and a first-boot race could write a mismatched keypair.

Follow-up wave: the login limiter keyed only on IP, so one shared proxy IP
(the Next dev proxy, a NAT) gave every user one bucket — 10 bad logins by
anyone 429'd everyone (self-DoS). Login is now throttled per (IP, email),
register stays per-IP, and X-Forwarded-For is honored only from configured
trusted proxies. Emptied limiter buckets are garbage-collected, and the
farm cap serializes concurrent creations on the user row.
"""

import asyncio
import hashlib
import os
import stat
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import ValidationError
from sqlalchemy import select

import app.api.auth as auth_api
from app.core.config import Settings, get_settings
from app.db import get_sessionmaker
from app.models import FarmMembership, Role, User
from app.ratelimit import SlidingWindowRateLimiter, auth_limiter
from app.security import decode_access_claims, decode_token, issue_access_token

from .conftest import login, owner_with_farm, register
from .test_auth_extended import forge_token, insert_user, make_pbkdf2_hash, set_refresh_cookie

PRODUCTION_IDEMPOTENCY_HMAC_SECRET = "production-idempotency-hmac-secret-0000000001"


async def _role_id(client: httpx.AsyncClient, owner: dict, code: str) -> int:
    resp = await client.get("/api/team", headers=owner)
    assert resp.status_code == 200, resp.text
    return next(r["id"] for r in resp.json()["roles"] if r["code"] == code)


async def _add_worker(
    client: httpx.AsyncClient,
    owner: dict,
    email: str,
    password: str | None = "workerpass123",
    role_code: str = "VET",
) -> httpx.Response:
    rid = await _role_id(client, owner, role_code)
    return await client.post(
        "/api/team/workers",
        json={"name": "Worker", "email": email, "password": password, "role_id": rid},
        headers=owner,
    )


async def _seed_membership(email: str, farm_id: int) -> None:
    """Fabricate a membership row directly — the HTTP layer (correctly) has
    no route to tie one account to two farms."""
    async with get_sessionmaker()() as db:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one()
        role = (await db.execute(select(Role).where(Role.farm_id == farm_id))).scalars().first()
        assert role is not None
        db.add(FarmMembership(farm_id=farm_id, user_id=user.id, role_id=role.id, is_active=True))
        await db.commit()


# ---------------------------------------------------------------------------
# B1 — cross-tenant account takeover
# ---------------------------------------------------------------------------
async def test_worker_of_another_farm_cannot_be_absorbed(client: httpx.AsyncClient) -> None:
    """The attack chain: learn a worker's email on farm B, absorb him into
    farm A, then reset his global password to log in as him — inheriting his
    access to farm B. Blocked at the absorb step now."""
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Alpha Farm")
    owner_b = await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    resp = await _add_worker(client, owner_b, "victim@farm.in", password="victimpass123")
    assert resp.status_code == 201, resp.text

    resp = await _add_worker(client, owner_a, "victim@farm.in", password="pwnedpass123")
    assert resp.status_code == 400
    # One generic refusal — distinct messages leaked other farms'
    # roster state to anyone probing arbitrary emails.
    assert resp.json()["detail"] == "That email can't be added to this farm's team."

    # The victim's account is untouched: his own password still works, the
    # attacker's never landed, and farm A's team never gained him.
    await login(client, "victim@farm.in", "victimpass123")
    resp = await client.post(
        "/api/auth/login", json={"email": "victim@farm.in", "password": "pwnedpass123"}
    )
    assert resp.status_code == 401
    team = (await client.get("/api/team", headers=owner_a)).json()
    assert [m["email"] for m in team["memberships"]] == []
    victim = await login(client, "victim@farm.in", "victimpass123")
    farms = (await client.get("/api/auth/farms", headers=victim)).json()
    assert [f["name"] for f in farms] == ["Beta Farm"]


async def test_inactive_membership_elsewhere_still_blocks_absorb(
    client: httpx.AsyncClient,
) -> None:
    """A deactivated membership still ties the account to the other farm —
    reactivating it later would restore the victim's access there."""
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Alpha Farm")
    owner_b = await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    resp = await _add_worker(client, owner_b, "victim@farm.in", password="victimpass123")
    assert resp.status_code == 201, resp.text
    resp = await client.put(
        f"/api/team/workers/{resp.json()['id']}/status",
        json={"is_active": False},
        headers=owner_b,
    )
    assert resp.status_code == 200, resp.text  # membership now inactive

    resp = await _add_worker(client, owner_a, "victim@farm.in", password="pwnedpass123")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "That email can't be added to this farm's team."


async def test_reset_password_blocked_for_cross_farm_affiliated_account(
    client: httpx.AsyncClient,
) -> None:
    """Even with fabricated shared-membership state (reachable in legacy
    data), a farm may only reset passwords of accounts whose sole farm
    affiliation is itself."""
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Alpha Farm")
    owner_b = await owner_with_farm(client, email="b@farm.in", farm_name="Beta Farm")
    resp = await _add_worker(client, owner_a, "victim@farm.in", password="victimpass123")
    assert resp.status_code == 201, resp.text
    membership_id = resp.json()["id"]
    # HTTP can't build this anymore (the absorb guard above), so seed it.
    await _seed_membership("victim@farm.in", int(owner_b["X-Farm-Id"]))

    resp = await client.post(
        f"/api/team/workers/{membership_id}/reset-password",
        json={"password": "pwnedpass123"},
        headers=owner_a,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "This account must use self-service password recovery."
    # Password unchanged: the old one still works, the attacker's does not.
    await login(client, "victim@farm.in", "victimpass123")
    resp = await client.post(
        "/api/auth/login", json={"email": "victim@farm.in", "password": "pwnedpass123"}
    )
    assert resp.status_code == 401


async def test_legacy_membership_without_provisioning_provenance_cannot_reset(
    client: httpx.AsyncClient,
) -> None:
    """Affiliation counts are not proof that a farm created the identity.
    Existing/legacy membership rows default false and fail closed."""
    owner = await owner_with_farm(client)
    await register(client, email="legacy-member@farm.in", password="hisownpass1")
    await _seed_membership("legacy-member@farm.in", int(owner["X-Farm-Id"]))
    async with get_sessionmaker()() as db:
        membership = (
            await db.execute(
                select(FarmMembership).where(FarmMembership.farm_id == int(owner["X-Farm-Id"]))
            )
        ).scalar_one()

    resp = await client.post(
        f"/api/team/workers/{membership.id}/reset-password",
        json={"password": "pwnedpass123"},
        headers=owner,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "This account must use self-service password recovery."
    await login(client, "legacy-member@farm.in", "hisownpass1")
    assert (
        await client.post(
            "/api/auth/login",
            json={"email": "legacy-member@farm.in", "password": "pwnedpass123"},
        )
    ).status_code == 401


async def test_unaffiliated_account_add_and_created_account_reset_still_work(
    client: httpx.AsyncClient,
) -> None:
    """A pre-existing identity requires a future consent/invitation flow;
    owner-provisioned worker accounts remain safely resettable."""
    owner = await owner_with_farm(client)
    await register(client, email="free@farm.in", password="hisownpass1")
    resp = await _add_worker(client, owner, "free@farm.in", password="pwnedpass123")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "That email can't be added to this farm's team."
    await login(client, "free@farm.in", "hisownpass1")  # his own password intact
    assert (
        await client.post(
            "/api/auth/login",
            json={"email": "free@farm.in", "password": "pwnedpass123"},
        )
    ).status_code == 401

    resp = await _add_worker(client, owner, "new@farm.in", password="workerpass123")
    assert resp.status_code == 201, resp.text
    membership_id = resp.json()["id"]
    resp = await client.post(
        f"/api/team/workers/{membership_id}/reset-password",
        json={"password": "resetpass123"},
        headers=owner,
    )
    assert resp.status_code == 200, resp.text
    await login(client, "new@farm.in", "resetpass123")
    resp = await client.post(
        "/api/auth/login", json={"email": "new@farm.in", "password": "workerpass123"}
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# B6 — auth surface
# ---------------------------------------------------------------------------
async def test_login_unknown_email_takes_the_hashing_path(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unknown email must do the same Argon2 work as a known one, so the
    response time can't reveal whether an account exists."""
    from app import security
    from app.api import auth as auth_api

    calls: list[tuple[str, str]] = []
    real_verify = security.verify_password_with_work_async

    async def spy(password: str, stored: str) -> tuple[bool, bool, bool]:
        calls.append((password, stored))
        return await real_verify(password, stored)

    monkeypatch.setattr(auth_api, "verify_password_with_work_async", spy)
    resp = await client.post(
        "/api/auth/login", json={"email": "ghost@farm.in", "password": "whatever123"}
    )
    assert resp.status_code == 401
    assert len(calls) == 1  # no early return: the dummy-hash verify ran
    assert calls[0][1].startswith("$argon2")
    # control: a known email with a wrong password verifies against HIS hash
    await register(client, "real@farm.in", "realpass123")
    resp = await client.post(
        "/api/auth/login", json={"email": "real@farm.in", "password": "wrongpass1"}
    )
    assert resp.status_code == 401
    assert len(calls) == 2


async def test_login_legacy_hash_wrong_password_still_pays_argon2_cost(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A legacy rejection pays Argon2 plus the same fixed PBKDF2 budget."""
    from app import security
    from app.api import auth as auth_api

    calls: list[tuple[str, str]] = []
    completions: list[tuple[str, str, bool]] = []
    real_verify = security.verify_password_with_work_async
    real_complete = security.complete_rejected_login_timing_async

    async def spy(password: str, stored: str) -> tuple[bool, bool, bool]:
        calls.append((password, stored))
        return await real_verify(password, stored)

    async def complete_spy(
        password: str, stored: str, dummy_hash: str, did_argon_work: bool
    ) -> None:
        completions.append((stored, dummy_hash, did_argon_work))
        await real_complete(password, stored, dummy_hash, did_argon_work)

    await insert_user("legacy-timing@farm.in", make_pbkdf2_hash("realpass123"))
    monkeypatch.setattr(auth_api, "verify_password_with_work_async", spy)
    monkeypatch.setattr(auth_api, "complete_rejected_login_timing_async", complete_spy)
    resp = await client.post(
        "/api/auth/login", json={"email": "legacy-timing@farm.in", "password": "wrongpass1"}
    )
    assert resp.status_code == 401
    assert len(calls) == 1  # initial verification is executor submission one
    assert calls[0][1].startswith("pbkdf2_sha256$")
    assert len(completions) == 1  # Argon2 top-up + PBKDF remainder share submission two
    assert completions[0][0].startswith("pbkdf2_sha256$")
    assert completions[0][1].startswith("$argon2")
    assert completions[0][2] is False


async def test_malformed_encoded_argon_hash_gets_real_argon_top_up(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Parsing parameters is not proof that libargon2 performed its work."""
    from app import security
    from app.api import auth as auth_api

    # Structurally parseable parameters, but salt/digest payloads that native
    # verification rejects immediately with VerificationError.
    malformed = "$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$AA"
    await insert_user("malformed-argon-timing@farm.in", malformed)
    observed: list[bool] = []
    real_complete = security.complete_rejected_login_timing_async

    async def complete_spy(
        password: str, stored: str, dummy_hash: str, did_argon_work: bool
    ) -> None:
        observed.append(did_argon_work)
        await real_complete(password, stored, dummy_hash, did_argon_work)

    monkeypatch.setattr(auth_api, "complete_rejected_login_timing_async", complete_spy)
    response = await client.post(
        "/api/auth/login",
        json={"email": "malformed-argon-timing@farm.in", "password": "wrongpass1"},
    )
    assert response.status_code == 401, response.text
    assert observed == [False]


def test_rejected_login_pbkdf2_work_is_account_independent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Finding #26: legacy verification consumes, rather than adds to, padding."""
    from app import security

    budget = security.get_settings().rejected_login_pbkdf2_work_budget
    legacy_iterations = 30_000
    assert legacy_iterations < budget
    legacy = make_pbkdf2_hash("realpass123", iterations=legacy_iterations)
    calls: list[int] = []

    def fake_pbkdf2(_name: str, _password: bytes, _salt: bytes, iterations: int) -> bytes:
        calls.append(iterations)
        return b"\x00" * 32

    monkeypatch.setattr(security.hashlib, "pbkdf2_hmac", fake_pbkdf2)
    assert security._verify_legacy_pbkdf2("wrongpass1", legacy) is False
    security._pad_rejected_login_pbkdf2("wrongpass1", legacy)
    assert calls == [
        legacy_iterations,
        budget - legacy_iterations,
    ]
    assert sum(calls) == budget

    calls.clear()
    security._pad_rejected_login_pbkdf2("wrongpass1", "$argon2id$stored")
    assert calls == [budget]

    # A legacy hash costlier than the padding budget clamps to zero extra
    # work instead of passing a negative iteration count into OpenSSL.
    calls.clear()
    over_budget = f"pbkdf2_sha256${budget + 1}$00${'00' * 32}"
    security._verify_legacy_pbkdf2("wrongpass1", over_budget)
    security._pad_rejected_login_pbkdf2("wrongpass1", over_budget)
    assert calls == [budget + 1]


def test_legacy_pbkdf2_ceiling_rejects_unbounded_or_malformed_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The compatibility ceiling is separate from, and covered by, timing work."""
    from app import security

    calls: list[int] = []

    def fake_pbkdf2(_name: str, _password: bytes, _salt: bytes, iterations: int) -> bytes:
        calls.append(iterations)
        return b"\x00" * 32

    monkeypatch.setattr(security.hashlib, "pbkdf2_hmac", fake_pbkdf2)
    budget = security.get_settings().rejected_login_pbkdf2_work_budget

    def encoded(iterations: str) -> str:
        return f"pbkdf2_sha256${iterations}$00${'00' * 32}"

    # The published maximum remains a supported hash, not just a padding cap.
    assert security._verify_legacy_pbkdf2(
        "password", encoded(str(security.LEGACY_PBKDF2_MAX_ITERATIONS))
    )
    assert calls == [security.LEGACY_PBKDF2_MAX_ITERATIONS]

    # Iteration spellings int() accepted in the v1 verifier keep verifying:
    # rejecting them would permanently lock out imported accounts.
    for raw_iterations, parsed in [("100_001", 100_001), ("+50000", 50_000), (" 50000", 50_000)]:
        calls.clear()
        assert security._verify_legacy_pbkdf2("password", encoded(raw_iterations))
        assert calls == [parsed]

    calls.clear()
    unsupported = [
        str(security.LEGACY_PBKDF2_MAX_ITERATIONS + 1),
        "9" * 180,
        "-1",
        "not-a-number",
    ]
    for raw_iterations in unsupported:
        stored = encoded(raw_iterations)
        assert security._verify_legacy_pbkdf2("password", stored) is False
        assert calls == []  # never pass the stored count into OpenSSL
        security._pad_rejected_login_pbkdf2("password", stored)
        assert calls == [budget]
        calls.clear()


async def test_each_rejected_login_uses_exactly_two_password_work_submissions(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unknown, legacy, and malformed rows have the same executor shape."""
    from app import security

    real_run = security._run_password_work
    submissions = 0

    async def counted_run(work: Callable[[], object]) -> object:
        nonlocal submissions
        submissions += 1
        return await real_run(work)

    monkeypatch.setattr(security, "_run_password_work", counted_run)
    cases: list[tuple[str, str | None]] = [
        ("two-submit-unknown@farm.in", None),
        ("two-submit-legacy@farm.in", make_pbkdf2_hash("right-password")),
        (
            "two-submit-malformed@farm.in",
            "$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$AA",
        ),
    ]
    for email, stored in cases:
        if stored is not None:
            await insert_user(email, stored)
        before = submissions
        response = await client.post(
            "/api/auth/login",
            json={"email": email, "password": "wrong-password"},
        )
        assert response.status_code == 401, response.text
        assert submissions - before == 2


async def test_password_max_length_128(client: httpx.AsyncClient) -> None:
    """No unbounded password into the (CPU-expensive) Argon2 hasher."""
    owner = await owner_with_farm(client)
    too_long = "x" * 129
    resp = await client.post(
        "/api/auth/register", json={"email": "cap@farm.in", "password": too_long}
    )
    assert resp.status_code == 422
    resp = await client.post("/api/auth/login", json={"email": "cap@farm.in", "password": too_long})
    assert resp.status_code == 422
    resp = await _add_worker(client, owner, "w@farm.in", password=too_long)
    assert resp.status_code == 422
    resp = await _add_worker(client, owner, "w@farm.in", password="workerpass123")
    assert resp.status_code == 201, resp.text
    resp = await client.post(
        f"/api/team/workers/{resp.json()['id']}/reset-password",
        json={"password": too_long},
        headers=owner,
    )
    assert resp.status_code == 422
    # exactly 128 chars is fine
    resp = await client.post(
        "/api/auth/register", json={"email": "edge@farm.in", "password": "y" * 128}
    )
    assert resp.status_code == 201, resp.text


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
async def test_login_failures_trip_the_limiter(client: httpx.AsyncClient) -> None:
    for _ in range(3):
        resp = await client.post(
            "/api/auth/login", json={"email": "ghost@farm.in", "password": "wrongpass1"}
        )
        assert resp.status_code == 401
    resp = await client.post(
        "/api/auth/login", json={"email": "ghost@farm.in", "password": "wrongpass1"}
    )
    assert resp.status_code == 429
    assert "Too many" in resp.json()["detail"]


@pytest.mark.usefixtures("rate_limit_on")
async def test_login_success_resets_the_failure_count(client: httpx.AsyncClient) -> None:
    await register(client, "resettable@farm.in", "realpass123")
    for password in ("wrongpass1", "wrongpass2"):
        resp = await client.post(
            "/api/auth/login", json={"email": "resettable@farm.in", "password": password}
        )
        assert resp.status_code == 401
    await login(client, "resettable@farm.in", "realpass123")  # success clears the count
    for password in ("wrongpass1", "wrongpass2", "wrongpass3"):
        resp = await client.post(
            "/api/auth/login", json={"email": "resettable@farm.in", "password": password}
        )
        assert resp.status_code == 401  # 3 fresh failures: still under the ceiling
    resp = await client.post(
        "/api/auth/login", json={"email": "resettable@farm.in", "password": "wrongpass4"}
    )
    assert resp.status_code == 429  # the 4th consecutive failure trips it


@pytest.mark.usefixtures("rate_limit_on")
async def test_register_attempts_trip_the_limiter(client: httpx.AsyncClient) -> None:
    for i in range(3):
        resp = await client.post(
            "/api/auth/register", json={"email": f"user{i}@farm.in", "password": "whatever123"}
        )
        assert resp.status_code == 201, resp.text
    resp = await client.post(
        "/api/auth/register", json={"email": "user3@farm.in", "password": "whatever123"}
    )
    assert resp.status_code == 429
    assert "Too many" in resp.json()["detail"]


@pytest.mark.usefixtures("rate_limit_on")
async def test_login_limiter_is_keyed_per_ip_and_email(client: httpx.AsyncClient) -> None:
    """Same email from the same IP trips the limiter, but a shared proxy IP
    must not lock out unrelated accounts: bystanders from the same IP still
    get real attempts."""
    for _ in range(3):
        resp = await client.post(
            "/api/auth/login", json={"email": "target@farm.in", "password": "wrongpass1"}
        )
        assert resp.status_code == 401
    resp = await client.post(
        "/api/auth/login", json={"email": "target@farm.in", "password": "wrongpass1"}
    )
    assert resp.status_code == 429  # the attacked (IP, email) pair is blocked
    resp = await client.post(
        "/api/auth/login", json={"email": "bystander@farm.in", "password": "wrongpass1"}
    )
    assert resp.status_code == 401  # same IP, different email: a real attempt, not 429


@pytest.mark.usefixtures("rate_limit_on")
async def test_forwarded_for_ignored_without_trusted_proxies(
    client: httpx.AsyncClient,
) -> None:
    """Default (trust nothing): a spoofed X-Forwarded-For must not move the
    attacker to a fresh bucket."""
    for _ in range(3):
        resp = await client.post(
            "/api/auth/login",
            json={"email": "ghost@farm.in", "password": "wrongpass1"},
            headers={"X-Forwarded-For": "9.9.9.9"},
        )
        assert resp.status_code == 401
    resp = await client.post(
        "/api/auth/login",
        json={"email": "ghost@farm.in", "password": "wrongpass1"},
        headers={"X-Forwarded-For": "8.8.8.8"},  # rotated spoof: same real bucket
    )
    assert resp.status_code == 429


@pytest.mark.usefixtures("rate_limit_on")
async def test_proxy_headers_honored_when_trusted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With trusted_proxy_hosts configured, the forwarded client IP keys the
    limiter: blocking one real IP must not block another."""
    from app.main import create_app

    monkeypatch.setenv("GOATFARM_TRUSTED_PROXY_HOSTS", "127.0.0.1")
    get_settings.cache_clear()
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as proxied:
        for _ in range(3):
            resp = await proxied.post(
                "/api/auth/login",
                json={"email": "ghost@farm.in", "password": "wrongpass1"},
                headers={"X-Forwarded-For": "1.2.3.4"},
            )
            assert resp.status_code == 401
        resp = await proxied.post(
            "/api/auth/login",
            json={"email": "ghost@farm.in", "password": "wrongpass1"},
            headers={"X-Forwarded-For": "1.2.3.4"},
        )
        assert resp.status_code == 429  # forwarded IP 1.2.3.4 is now limited
        resp = await proxied.post(
            "/api/auth/login",
            json={"email": "ghost@farm.in", "password": "wrongpass1"},
            headers={"X-Forwarded-For": "5.6.7.8"},
        )
        assert resp.status_code == 401  # a different real client IP is unaffected


@pytest.fixture()
def rate_limit_one(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Like rate_limit_on but with max_attempts=1, so the 0-3 multiplied
    ceilings (per-email 3×, per-IP 10×) trip after few requests."""
    monkeypatch.setenv("GOATFARM_AUTH_RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("GOATFARM_AUTH_RATE_LIMIT_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("GOATFARM_AUTH_RATE_LIMIT_WINDOW_SECONDS", "300")
    get_settings.cache_clear()
    auth_limiter.clear()
    yield
    auth_limiter.clear()
    get_settings.cache_clear()


@pytest.mark.usefixtures("rate_limit_one")
async def test_invalid_access_tokens_are_blocked_before_repeated_signature_work(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every protected route used to verify unlimited attacker JWTs. Kidless
    legacy-shaped tokens can try every rotation key, making this an unbounded
    RSA-work endpoint before authentication or database authorization runs."""
    from app import deps

    real_decode = deps.decode_access_claims_result
    calls = 0

    def counted_decode(token: str):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return real_decode(token)

    monkeypatch.setattr(deps, "decode_access_claims_result", counted_decode)
    # Two invalid-token ledgers exactly fill this deliberately tiny map. A
    # valid new principal must still be able to register and authenticate;
    # saturation is bookkeeping pressure, not a global auth kill switch.
    monkeypatch.setattr(auth_limiter, "_max_keys", 2)
    attacker_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    headers = {"Authorization": f"Bearer {forge_token(1, key=attacker_key)}"}
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 401
    assert len(auth_limiter._hits) == 2
    valid_headers = await register(client, "invalid-jwt-neighbor@farm.in")
    # The narrow per-token threshold must not lock a valid bystander on the
    # same IP; only the wider anti-spray IP ceiling is shared.
    assert (await client.get("/api/auth/me", headers=valid_headers)).status_code == 200
    blocked = await client.get("/api/auth/me", headers=headers)
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"] == "300"
    assert calls == 2


@pytest.mark.usefixtures("rate_limit_one")
async def test_verified_expired_access_token_stays_401_without_spending_invalid_budget(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A normal browser can issue several requests just after access expiry;
    each 401 must remain refresh-triggering rather than becoming a limiter 429.
    The token is signature/claim valid and therefore is not attacker input.
    """
    from app import deps

    live_headers = await register(client, "expired-access-race@farm.in")
    live_token = live_headers["Authorization"].removeprefix("Bearer ")
    live_claims = decode_access_claims(live_token)
    assert live_claims is not None
    expired_token = forge_token(
        live_claims.user_id,
        ttl_seconds=-120,
        ver=live_claims.token_version,
    )

    real_decode = deps.decode_access_claims_result
    calls = 0

    def counted_decode(token: str):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return real_decode(token)

    monkeypatch.setattr(deps, "decode_access_claims_result", counted_decode)
    headers = {"Authorization": f"Bearer {expired_token}"}
    responses = await asyncio.gather(
        *(client.get("/api/auth/me", headers=headers) for _ in range(4))
    )
    assert [response.status_code for response in responses] == [401, 401, 401, 401]
    assert all(response.json()["detail"] == "Invalid or expired token" for response in responses)
    assert calls == 4
    digest = hashlib.sha256(expired_token.encode("utf-8")).hexdigest()
    assert ("access-token-invalid-token", digest) not in auth_limiter._hits


@pytest.mark.usefixtures("rate_limit_one")
async def test_invalid_jwt_ip_spray_cannot_block_unclassified_authentic_tokens(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shared-IP history applies only after the current token is classified.

    Ten distinct forgeries saturate the wide IP ledger. A live neighbor, an
    authentic expired token, and logout must retain their normal semantics;
    only a repeated *known-bad* token is stopped before another RSA decode.
    """
    from app import deps

    live_headers = await register(client, "invalid-jwt-nat-neighbor@farm.in")
    live_token = live_headers["Authorization"].removeprefix("Bearer ")
    live_claims = decode_access_claims(live_token)
    assert live_claims is not None
    valid_refresh = client.cookies.get(get_settings().refresh_cookie_name)
    assert valid_refresh is not None
    expired_token = forge_token(
        live_claims.user_id,
        ttl_seconds=-120,
        ver=live_claims.token_version,
    )

    real_decode = deps.decode_access_claims_result
    calls = 0

    def counted_decode(token: str):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return real_decode(token)

    monkeypatch.setattr(deps, "decode_access_claims_result", counted_decode)
    attacker_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    bad_headers = [
        {"Authorization": f"Bearer {forge_token(live_claims.user_id, key=attacker_key)}"}
        for _ in range(10)
    ]
    assert len({headers["Authorization"] for headers in bad_headers}) == 10
    spray = [await client.get("/api/auth/me", headers=headers) for headers in bad_headers]
    assert [response.status_code for response in spray] == [*([401] * 9), 429]
    assert calls == 10

    assert (await client.get("/api/auth/me", headers=live_headers)).status_code == 200
    expired = await client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert expired.status_code == 401
    assert calls == 12

    set_refresh_cookie(client, valid_refresh)
    assert (await client.post("/api/auth/logout", headers=live_headers)).status_code == 204
    assert (await client.get("/api/auth/me", headers=live_headers)).status_code == 401
    calls_after_logout_check = calls

    repeated = await client.get("/api/auth/me", headers=bad_headers[0])
    assert repeated.status_code == 429
    assert calls == calls_after_logout_check


@pytest.mark.usefixtures("rate_limit_one")
async def test_logout_invalid_tokens_are_blocked_before_repeated_signature_work(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Logout is unauthenticated by design, but that must not make its access
    and refresh JWT decoders an unlimited public cryptographic-work oracle."""
    from app.api import auth as auth_api

    real_decode = auth_api.decode_access_claims_result
    calls = 0

    def counted_decode(token: str):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return real_decode(token)

    monkeypatch.setattr(auth_api, "decode_access_claims_result", counted_decode)
    attacker_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    headers = {"Authorization": f"Bearer {forge_token(1, key=attacker_key)}"}
    assert (await client.post("/api/auth/logout", headers=headers)).status_code == 204
    blocked = await client.post("/api/auth/logout", headers=headers)
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"] == "300"
    assert calls == 1


@pytest.mark.usefixtures("rate_limit_one")
async def test_logout_bounds_each_invalid_token_when_the_other_component_is_authentic(
    client: httpx.AsyncClient,
) -> None:
    """A valid component must not give the other decoder an unlimited pass.

    This covers both mixed directions, including a verified-expired access JWT:
    expiry itself remains uncharged, while an arbitrary refresh cookie beside
    it is still bounded independently.
    """
    live_headers = await register(client, "mixed-logout-tokens@farm.in")
    live_token = live_headers["Authorization"].removeprefix("Bearer ")
    live_claims = decode_access_claims(live_token)
    assert live_claims is not None
    valid_refresh = client.cookies.get(get_settings().refresh_cookie_name)
    assert valid_refresh is not None

    invalid_access = {"Authorization": "Bearer attacker-access"}
    set_refresh_cookie(client, valid_refresh)
    assert (await client.post("/api/auth/logout", headers=invalid_access)).status_code == 204
    set_refresh_cookie(client, valid_refresh)
    assert (await client.post("/api/auth/logout", headers=invalid_access)).status_code == 429

    auth_limiter.clear()
    expired_access = forge_token(
        live_claims.user_id,
        ttl_seconds=-120,
        ver=live_claims.token_version,
    )
    expired_headers = {"Authorization": f"Bearer {expired_access}"}
    set_refresh_cookie(client, "attacker-refresh")
    assert (await client.post("/api/auth/logout", headers=expired_headers)).status_code == 204
    set_refresh_cookie(client, "attacker-refresh")
    assert (await client.post("/api/auth/logout", headers=expired_headers)).status_code == 429


@pytest.mark.usefixtures("rate_limit_one")
async def test_login_per_email_ceiling_across_rotating_ips(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rotating source IPs must not reset the attack budget
    against ONE account — the IP-agnostic per-email counter (3× the composite
    budget) caps distributed brute force even when every (IP, email) pair is
    fresh."""
    from app.main import create_app

    monkeypatch.setenv("GOATFARM_TRUSTED_PROXY_HOSTS", "127.0.0.1")
    get_settings.cache_clear()
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as proxied:
        for ip in ("1.1.1.1", "2.2.2.2", "3.3.3.3"):
            resp = await proxied.post(
                "/api/auth/login",
                json={"email": "victim@farm.in", "password": "wrongpass1"},
                headers={"X-Forwarded-For": ip},
            )
            assert resp.status_code == 401  # fresh composite key each time
        resp = await proxied.post(
            "/api/auth/login",
            json={"email": "victim@farm.in", "password": "wrongpass1"},
            headers={"X-Forwarded-For": "4.4.4.4"},
        )
        assert resp.status_code == 429  # the per-email ceiling tripped


@pytest.mark.usefixtures("rate_limit_one")
async def test_login_per_ip_ceiling_across_sprayed_emails(
    client: httpx.AsyncClient,
) -> None:
    """From one IP, spraying DISTINCT accounts must hit the
    email-agnostic per-IP counter (10× the composite budget) — each pair below
    is fresh, so only the global per-IP cap can stop it."""
    for i in range(10):
        resp = await client.post(
            "/api/auth/login", json={"email": f"spray{i}@farm.in", "password": "wrongpass1"}
        )
        assert resp.status_code == 401
    resp = await client.post(
        "/api/auth/login", json={"email": "spray10@farm.in", "password": "wrongpass1"}
    )
    assert resp.status_code == 429  # the per-IP ceiling tripped


def test_sliding_window_expires() -> None:
    now = [1000.0]
    limiter = SlidingWindowRateLimiter(clock=lambda: now[0])
    for _ in range(3):
        limiter.record("login", "ip", 300)
    assert limiter.is_blocked("login", "ip", 3, 300)
    now[0] += 299  # still inside the window
    assert limiter.is_blocked("login", "ip", 3, 300)
    now[0] += 2  # window slid past the oldest hit
    assert not limiter.is_blocked("login", "ip", 3, 300)


def test_pruned_buckets_are_forgotten() -> None:
    """Emptied buckets must leave the map: one permanent entry per distinct
    sprayed IP/email is a slow memory leak."""
    now = [1000.0]
    limiter = SlidingWindowRateLimiter(clock=lambda: now[0])
    for i in range(100):
        limiter.record("login", f"ip-{i}", 300)
    assert len(limiter._hits) == 100
    now[0] += 301  # every window has expired
    for i in range(100):
        assert not limiter.is_blocked("login", f"ip-{i}", 3, 300)
    assert limiter._hits == {}  # the map shrank back, no stale entries
    # recording still works after a prune-to-empty (the deque is re-stored)
    limiter.record("login", "ip-fresh", 300)
    assert limiter.is_blocked("login", "ip-fresh", 1, 300)


def test_global_sweep_forgets_stale_unique_keys_without_revisiting_them() -> None:
    """A unique-key spray must not live forever merely because none of the
    original keys is queried again."""
    now = [1000.0]
    limiter = SlidingWindowRateLimiter(clock=lambda: now[0], sweep_interval_seconds=10)
    for i in range(100):
        limiter.record("login", f"one-shot-{i}", 30)
    now[0] += 31
    # Touch one entirely different key after the sweep interval.
    assert not limiter.is_blocked("login", "unrelated", 3, 30)
    assert limiter._hits == {}


def test_sliding_window_cardinality_is_hard_bounded() -> None:
    now = [1000.0]
    limiter = SlidingWindowRateLimiter(clock=lambda: now[0], max_keys=25, sweep_interval_seconds=60)
    for i in range(500):
        limiter.record("register", f"unique-{i}", 300)
    assert len(limiter._hits) == 25
    assert len(limiter._windows) == 25
    assert len(limiter._limits) <= 25


def test_cardinality_spray_cannot_evict_a_live_brute_force_counter() -> None:
    """A full limiter preserves a hot victim without denying a new identity.

    The old eviction policy made the target below the oldest inserted bucket:
    one unique-key record evicted all three of its failures and reopened the
    password-guessing budget immediately.
    """
    now = [1000.0]
    limiter = SlidingWindowRateLimiter(clock=lambda: now[0], max_keys=2)
    for _ in range(3):
        limiter.record("login-email", "victim@farm.in", 300, max_attempts=3)
    limiter.record("login-email", "spray-1@farm.in", 300, max_attempts=3)

    # A pure probe never allocates and is not globally denied merely because
    # bookkeeping is full. If it later fails, the cold one-shot spray bucket
    # is evicted while the threshold-reaching victim survives unchanged.
    assert not limiter.is_blocked("login-email", "benign@farm.in", 3, 300)
    assert ("login-email", "victim@farm.in") in limiter._hits
    limiter.record("login-email", "spray-2@farm.in", 300, max_attempts=3)
    assert limiter.is_blocked("login-email", "victim@farm.in", 3, 300)
    assert ("login-email", "victim@farm.in") in limiter._hits
    assert ("login-email", "spray-1@farm.in") not in limiter._hits
    assert len(limiter._hits) == 2

    # The protected counter is still a sliding window, not a permanent block.
    now[0] += 301
    assert not limiter.is_blocked("login-email", "spray-2@farm.in", 3, 300)


def test_limiter_sweep_preserves_lru_order_and_demotes_aged_blocks_first() -> None:
    """Expiry maintenance must not impersonate traffic and refresh every key.

    Reclassifying all survivors with move_to_end reset cold LRU order to map
    creation order every sweep. A protected bucket that ages below threshold
    is older still and belongs at the cold tier's eviction front.
    """
    now = [1000.0]
    limiter = SlidingWindowRateLimiter(
        clock=lambda: now[0],
        max_keys=10,
        sweep_interval_seconds=5,
    )
    limiter.record("login", "cold-a", 300, max_attempts=3)
    now[0] += 1
    limiter.record("login", "cold-b", 300, max_attempts=3)
    # A real probe touches A, making B the older cold entry.
    now[0] += 1
    assert not limiter.is_blocked("login", "cold-a", 3, 300)
    assert list(limiter._cold) == [("login", "cold-b"), ("login", "cold-a")]

    now[0] += 5
    assert not limiter.is_blocked("login", "unseen-probe", 3, 300)
    assert list(limiter._cold) == [("login", "cold-b"), ("login", "cold-a")]

    # Build a blocked counter whose oldest hit expires while its newer hit
    # survives. Passive demotion places it ahead of both existing cold keys.
    limiter.record("login", "aging", 10, max_attempts=2)
    now[0] += 2
    limiter.record("login", "aging", 10, max_attempts=2)
    assert ("login", "aging") in limiter._protected
    now[0] += 9  # cutoff expires the first hit but retains the second
    assert not limiter.is_blocked("different", "sweep-trigger", 3, 10)
    assert next(iter(limiter._cold)) == ("login", "aging")
    assert ("login", "aging") not in limiter._protected


def test_password_admission_reservations_are_nonwaiting_bounded_and_reusable() -> None:
    limiter = SlidingWindowRateLimiter(max_keys=2)
    assert limiter.try_reserve("password", "same-email") is True
    assert limiter.try_reserve("password", "same-email") is False
    assert limiter.try_reserve("password", "second-email") is True
    assert limiter.try_reserve("password", "third-email") is False
    limiter.release("password", "same-email")
    assert limiter.try_reserve("password", "same-email") is True
    limiter.clear()
    assert limiter._reservations == {}


def test_client_key_falls_back_when_client_is_none() -> None:
    from starlette.requests import Request

    from app.api.auth import _client_key

    scope = {"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""}
    assert Request(scope).client is None
    assert _client_key(Request(scope)) == "unknown"


async def test_eleventh_farm_rejected(client: httpx.AsyncClient) -> None:
    headers = await register(client, "rich@farm.in")
    for i in range(10):
        resp = await client.post("/api/auth/farms", json={"name": f"Farm {i}"}, headers=headers)
        assert resp.status_code == 201, resp.text
    resp = await client.post("/api/auth/farms", json={"name": "Farm 11"}, headers=headers)
    assert resp.status_code == 400
    assert "10" in resp.json()["detail"]
    farms = (await client.get("/api/auth/farms", headers=headers)).json()
    assert len(farms) == 10


async def test_concurrent_farm_creation_never_exceeds_cap(client: httpx.AsyncClient) -> None:
    """The count-then-insert cap serializes on the user row: three in-flight
    creations competing for the last slot must not all pass the pre-count."""
    headers = await register(client, "almostfull@farm.in")
    for i in range(9):
        resp = await client.post("/api/auth/farms", json={"name": f"Farm {i}"}, headers=headers)
        assert resp.status_code == 201, resp.text
    responses = await asyncio.gather(
        *(
            client.post("/api/auth/farms", json={"name": f"Racer {i}"}, headers=headers)
            for i in range(3)
        )
    )
    assert sorted(r.status_code for r in responses) == [201, 400, 400]
    farms = (await client.get("/api/auth/farms", headers=headers)).json()
    assert len(farms) == 10  # the cap held exactly, never 11+


# ---------------------------------------------------------------------------
# B7 — JWT key handling
# ---------------------------------------------------------------------------
@pytest.fixture()
def tmp_jwt_keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[tuple[Path, Path]]:
    """Point the JWT settings at a fresh keypair location; restore both the
    settings cache and the in-memory key cache afterwards."""
    from app import security

    key_dir = tmp_path / "keys"
    priv = key_dir / "jwt_private.pem"
    pub = key_dir / "jwt_public.pem"
    monkeypatch.setenv("GOATFARM_JWT_PRIVATE_KEY_PATH", str(priv))
    monkeypatch.setenv("GOATFARM_JWT_PUBLIC_KEY_PATH", str(pub))
    get_settings.cache_clear()
    security._key_cache.clear()
    yield priv, pub
    security._key_cache.clear()
    get_settings.cache_clear()


def test_keys_are_read_from_disk_at_most_once(
    tmp_jwt_keys: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Signing/verifying must not re-read the PEM files per token."""
    priv, pub = tmp_jwt_keys
    reads: list[Path] = []
    real_read_text = Path.read_text

    def spy(self: Path, *args: object, **kwargs: object) -> str:
        reads.append(self)
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", spy)
    for _ in range(20):
        token = issue_access_token(7)
        assert decode_token(token, "access") == 7
    assert reads.count(priv) == 1  # generated once, read once
    assert reads.count(pub) == 1


def test_concurrent_first_boot_yields_one_consistent_keypair(
    tmp_jwt_keys: tuple[Path, Path],
) -> None:
    """Threads racing the first-boot generation must end up with ONE keypair:
    every issued token verifies against the public key that landed on disk."""
    priv, _pub = tmp_jwt_keys
    barrier = threading.Barrier(8)
    tokens: list[str] = []
    errors: list[Exception] = []

    def worker() -> None:
        try:
            barrier.wait(timeout=10)
            tokens.append(issue_access_token(1))
        except Exception as exc:  # collect, don't lose them in the threads
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(tokens) == 8
    for token in tokens:
        assert decode_token(token, "access") == 1  # mismatched pair would fail here
    assert priv.stat().st_mode & 0o777 == 0o600  # private key never world-readable
    assert priv.parent.stat().st_mode & 0o777 == 0o700


def test_private_key_temp_is_private_before_atomic_replace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A normal umask must not expose PEM bytes before chmod/replace.

    Inspect the source file at the last possible instant before ``os.replace``;
    checking only the final pathname misses the old predictable-.tmp race.
    """
    from app import security

    target = tmp_path / "jwt_private.pem"
    observed_modes: list[int] = []
    real_replace = security.os.replace

    def inspect_replace(source: object, destination: object) -> None:
        observed_modes.append(stat.S_IMODE(Path(source).stat().st_mode))
        real_replace(source, destination)

    monkeypatch.setattr(security.os, "replace", inspect_replace)
    previous_umask = os.umask(0o022)
    try:
        security._write_atomic(target, b"private-key-material", mode=0o600)
    finally:
        os.umask(previous_umask)

    assert observed_modes == [0o600]
    assert target.read_bytes() == b"private-key-material"
    assert target.stat().st_mode & 0o777 == 0o600


def test_default_development_key_directory_is_private(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Tighten the app-owned legacy default, but not arbitrary configured parents."""
    from app import security

    key_dir = tmp_path / "keys"
    key_dir.mkdir(mode=0o755)
    key_dir.chmod(0o755)
    monkeypatch.setattr(security, "BACKEND_DIR", tmp_path)
    monkeypatch.setenv("GOATFARM_JWT_PRIVATE_KEY_PATH", str(key_dir / "jwt_private.pem"))
    monkeypatch.setenv("GOATFARM_JWT_PUBLIC_KEY_PATH", str(key_dir / "jwt_public.pem"))
    get_settings.cache_clear()
    security._key_cache.clear()
    try:
        issue_access_token(1)
        assert key_dir.stat().st_mode & 0o777 == 0o700
    finally:
        security._key_cache.clear()
        get_settings.cache_clear()


def test_first_boot_generation_takes_a_process_lock(
    tmp_jwt_keys: tuple[Path, Path],
) -> None:
    """_key_lock is per-process, so generation also serializes across
    PROCESSES via an flock on a sibling lock file — two first-booting
    processes can't interleave atomic writes into a mismatched keypair."""
    priv, _pub = tmp_jwt_keys
    issue_access_token(1)
    assert (priv.parent / ".jwt_keygen.lock").exists()


# --- config-level proxy / host / origin contracts ----------------------------


@pytest.mark.parametrize(
    "value",
    ["*", "0.0.0.0/0", "::/0", "proxy.internal", "10.0.0.0\\8", "127.0.0.1,not-an-ip"],
)
def test_trusted_proxy_hosts_rejects_wildcards_and_hostnames(value: str) -> None:
    """uvicorn treats "*" (and any /0 network) as always-trust, so the leftmost
    attacker-supplied X-Forwarded-For entry becomes the limiter key; anything
    that is not an IP/CIDR lands in a literal set that can never match a peer
    address and silently trusts nothing. Both must fail at boot."""
    with pytest.raises(ValidationError, match="trusted_proxy_hosts"):
        Settings(trusted_proxy_hosts=value)


def test_trusted_proxy_hosts_accepts_and_normalizes_addresses() -> None:
    settings = Settings(trusted_proxy_hosts=" 127.0.0.1 , 10.0.0.0/8 ")
    assert settings.trusted_proxy_hosts == "127.0.0.1,10.0.0.0/8"


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("API.Example.com", "api.example.com"),
        (" api.example.com ", "api.example.com"),
        ("api.example.com.", "api.example.com"),
        ("*.Example.com", "*.example.com"),
    ],
)
def test_allowed_hosts_are_stored_the_way_trustedhost_compares_them(
    configured: str, expected: str
) -> None:
    """TrustedHostMiddleware compares the Host header byte-exactly. Validating
    a normalized copy while installing the raw string let a production config
    boot green and then 400 every browser request."""
    settings = Settings(
        environment="production",
        cookie_secure=True,
        cors_origins=["https://app.example.com"],
        allowed_hosts=[configured],
        db_sslmode="verify-full",
        min_password_length=12,
        idempotency_request_hmac_secret=PRODUCTION_IDEMPOTENCY_HMAC_SECRET,
    )
    assert settings.allowed_hosts == [expected]


@pytest.mark.parametrize(
    "configured",
    [
        "*example.com",
        "api*.example.com",
        ".example.com",
        "api..example.com",
        "api.example.com..",
        "-api.example.com",
        "api-.example.com",
        "api_example.com",
        "api.example.com:443",
        "https://api.example.com",
        "*.192.0.2.1",
        "999.999.999.999",
        "2001:db8::1",
    ],
)
def test_allowed_hosts_rejects_patterns_trustedhost_cannot_safely_match(
    configured: str,
) -> None:
    with pytest.raises(ValidationError, match="GOATFARM_ALLOWED_HOSTS"):
        Settings(allowed_hosts=[configured])


def test_allowed_hosts_rejects_duplicates_after_canonicalization() -> None:
    with pytest.raises(ValidationError, match="duplicate host patterns"):
        Settings(allowed_hosts=["API.Example.com", "api.example.com."])


async def test_uppercase_allowed_host_still_serves_browser_traffic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end proof of the same defect: the container probe used to be the
    only request that matched, so readiness stayed green during a total
    outage."""
    from app.main import create_app

    monkeypatch.setenv("GOATFARM_ALLOWED_HOSTS", '["API.Example.com"]')
    get_settings.cache_clear()
    try:
        transport = httpx.ASGITransport(app=create_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as hosted:
            response = await hosted.get("/healthz", headers={"Host": "api.example.com"})
        assert response.status_code == 200
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("https://App.Example.com", "https://app.example.com"),
        ("https://app.example.com:443", "https://app.example.com"),
        ("http://localhost:3000", "http://localhost:3000"),
    ],
)
def test_cors_origins_are_stored_the_way_a_browser_sends_them(
    configured: str, expected: str
) -> None:
    """CORSMiddleware matches `origin in allow_origins` exactly, so a value the
    production gate accepts must also be the value a browser presents."""
    assert Settings(cors_origins=[configured]).cors_origins == [expected]


def test_cors_origins_reject_unicode_hosts_and_accept_explicit_punycode() -> None:
    """Browsers put the WHATWG/Punycode host in Origin, never this Unicode key."""
    with pytest.raises(ValidationError, match="ASCII/Punycode"):
        Settings(cors_origins=["https://münich.example"])

    punycode = "https://xn--mnich-kva.example"
    assert Settings(cors_origins=[punycode]).cors_origins == [punycode]


# --- account password workflows: budget and pool admission -------------------


@pytest.mark.usefixtures("rate_limit_one")
async def test_change_password_per_account_ceiling_across_rotating_ips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Current-password confirmation is the control that stops a stolen access
    token from becoming a permanent takeover, and it was keyed on
    (IP, account) alone — a caller varying their source address got a fresh
    10-attempt budget on every request. The IP-agnostic per-account ceiling
    caps the total, exactly as login's per-email counter does."""
    from app.main import create_app

    monkeypatch.setenv("GOATFARM_TRUSTED_PROXY_HOSTS", "127.0.0.1")
    get_settings.cache_clear()
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as proxied:
        created = await proxied.post(
            "/api/auth/register",
            json={"email": "rotate-change@farm.in", "password": "ownerpass123"},
            headers={"X-Forwarded-For": "9.9.9.9"},
        )
        assert created.status_code == 201, created.text
        headers = {"Authorization": f"Bearer {created.json()['access_token']}"}
        payload = {"current_password": "wrong-password", "new_password": "newpass1234"}
        for ip in ("1.1.1.1", "2.2.2.2", "3.3.3.3"):
            resp = await proxied.post(
                "/api/auth/change-password",
                json=payload,
                headers=headers | {"X-Forwarded-For": ip},
            )
            assert resp.status_code == 400, resp.text  # fresh composite key each time
        resp = await proxied.post(
            "/api/auth/change-password",
            json=payload,
            headers=headers | {"X-Forwarded-For": "4.4.4.4"},
        )
        assert resp.status_code == 429  # the per-account ceiling tripped


@pytest.mark.usefixtures("rate_limit_one")
async def test_rejected_replacement_still_charges_the_change_password_budget(
    client: httpx.AsyncClient,
) -> None:
    """A correct current password with an identical new password performs a
    full 64 MiB Argon2id verify and returns 400. Clearing the budget before
    that check let one authenticated account loop the endpoint forever,
    occupying a slot in the deliberately non-queuing global password pool."""
    headers = await register(client, "noop-change@farm.in")
    payload = {"current_password": "ownerpass123", "new_password": "ownerpass123"}
    first = await client.post("/api/auth/change-password", json=payload, headers=headers)
    assert first.status_code == 400
    assert first.json()["detail"].startswith("New password must be different")
    second = await client.post("/api/auth/change-password", json=payload, headers=headers)
    assert second.status_code == 429


async def test_account_password_workflows_share_one_argon_reservation(
    client: httpx.AsyncClient,
) -> None:
    """change-password and account-delete used separate reservation scopes, so
    one account could hold two of the global Argon slots at once and 429 every
    other user's login. They now share one per-account admission."""
    headers = await register(client, "shared-reservation@farm.in")
    async with get_sessionmaker()() as db:
        user_id = (
            await db.execute(select(User.id).where(User.email == "shared-reservation@farm.in"))
        ).scalar_one()

    scope = auth_api.ACCOUNT_PASSWORD_RESERVATION_SCOPE
    assert auth_limiter.try_reserve(scope, str(user_id))
    try:
        change = await client.post(
            "/api/auth/change-password",
            json={"current_password": "ownerpass123", "new_password": "newpass1234"},
            headers=headers,
        )
        assert change.status_code == 429
        delete_account = await client.request(
            "DELETE",
            "/api/auth/account",
            json={"current_password": "ownerpass123"},
            headers=headers,
        )
        assert delete_account.status_code == 429
    finally:
        auth_limiter.release(scope, str(user_id))
