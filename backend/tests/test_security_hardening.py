"""Security-hardening regression tests (third adversarial audit wave).

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
import threading
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db import get_sessionmaker
from app.models import FarmMembership, Role, User
from app.ratelimit import SlidingWindowRateLimiter, auth_limiter
from app.security import decode_token, issue_access_token

from .conftest import login, owner_with_farm, register
from .test_auth_extended import insert_user, make_pbkdf2_hash


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
    # LOW 1-4: one generic refusal — distinct messages leaked other farms'
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
    resp = await client.post(f"/api/team/workers/{resp.json()['id']}/toggle", headers=owner_b)
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
    assert "another farm" in resp.json()["detail"]
    # Password unchanged: the old one still works, the attacker's does not.
    await login(client, "victim@farm.in", "victimpass123")
    resp = await client.post(
        "/api/auth/login", json={"email": "victim@farm.in", "password": "pwnedpass123"}
    )
    assert resp.status_code == 401


async def test_unaffiliated_account_add_and_created_account_reset_still_work(
    client: httpx.AsyncClient,
) -> None:
    """Happy paths: an existing account with zero memberships anywhere may be
    added, and a created-via-worker-flow account's password may be reset."""
    owner = await owner_with_farm(client)
    await register(client, email="free@farm.in", password="hisownpass1")
    resp = await _add_worker(client, owner, "free@farm.in", password=None)
    assert resp.status_code == 201, resp.text
    await login(client, "free@farm.in", "hisownpass1")  # his own password intact

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
    real_verify = security.verify_password

    def spy(password: str, stored: str) -> tuple[bool, bool]:
        calls.append((password, stored))
        return real_verify(password, stored)

    monkeypatch.setattr(auth_api, "verify_password", spy)
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
    """LOW 0-5: a legacy pbkdf2 account with a wrong password returns after a
    fast pbkdf2 verify — measurably earlier than both the unknown-email and
    Argon2 paths, exposing "email exists, pre-migration". A failed legacy
    verify is now topped up with dummy Argon2 work."""
    from app import security
    from app.api import auth as auth_api

    calls: list[tuple[str, str]] = []
    real_verify = security.verify_password

    def spy(password: str, stored: str) -> tuple[bool, bool]:
        calls.append((password, stored))
        return real_verify(password, stored)

    await insert_user("legacy-timing@farm.in", make_pbkdf2_hash("realpass123"))
    monkeypatch.setattr(auth_api, "verify_password", spy)
    resp = await client.post(
        "/api/auth/login", json={"email": "legacy-timing@farm.in", "password": "wrongpass1"}
    )
    assert resp.status_code == 401
    assert len(calls) == 2  # the fast legacy verify plus dummy Argon2 work
    assert calls[0][1].startswith("pbkdf2_sha256$")
    assert calls[1][1].startswith("$argon2")


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
async def test_login_per_email_ceiling_across_rotating_ips(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MEDIUM 0-3(a): rotating source IPs must not reset the attack budget
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
    """MEDIUM 0-3(b): from one IP, spraying DISTINCT accounts must hit the
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

    priv = tmp_path / "jwt_private.pem"
    pub = tmp_path / "jwt_public.pem"
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


def test_first_boot_generation_takes_a_process_lock(
    tmp_jwt_keys: tuple[Path, Path],
) -> None:
    """_key_lock is per-process, so generation also serializes across
    PROCESSES via an flock on a sibling lock file — two first-booting
    processes can't interleave atomic writes into a mismatched keypair."""
    priv, _pub = tmp_jwt_keys
    issue_access_token(1)
    assert (priv.parent / ".jwt_keygen.lock").exists()
