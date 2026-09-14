"""Test fixtures: real PostgreSQL (`goatfarm_test` database), schema via
`alembic upgrade head` (subprocess, so the migration itself is under test),
per-test table truncation + reference-data seed, and an httpx AsyncClient
wired to the ASGI app. Async throughout (pytest-asyncio auto mode)."""

import asyncio
import json
import os
import re
import subprocess
import sys
from collections.abc import AsyncGenerator
from pathlib import Path
from uuid import uuid4

import asyncpg
import httpx
import pytest
from sqlalchemy import text

BACKEND_DIR = Path(__file__).resolve().parent.parent
TEST_DB = os.environ.get("GOATFARM_TEST_DB", "goatfarm_test")
if not (TEST_DB.endswith("_test") or "_test_" in TEST_DB):
    # The suite drops the database and truncates every table per test —
    # an unchecked GOATFARM_TEST_DB=goatfarm would wipe the dev/prod data.
    # Parallel test runs use suffixed throwaway DBs (goatfarm_test_ops, …).
    raise RuntimeError(
        f"Refusing to run the test suite against database {TEST_DB!r}: "
        "GOATFARM_TEST_DB must name a throwaway database ending in '_test'."
    )
ADMIN_URL = "postgresql://localhost:5432/postgres"
TEST_URL = f"postgresql+asyncpg://localhost:5432/{TEST_DB}"

os.environ["GOATFARM_DATABASE_URL"] = TEST_URL  # before any app import
# The suite logs in/registers constantly from one client — the auth rate
# limiter stays off globally; its tests re-enable it per-test (monkeypatch).
os.environ["GOATFARM_AUTH_RATE_LIMIT_ENABLED"] = "false"

from app.db import Base, get_engine, get_sessionmaker, reset_engine  # noqa: E402
from app.main import create_app  # noqa: E402
from app.seed import seed_reference_data  # noqa: E402

OWNER_PW = "ownerpass123"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--mutation-pure",
        action="store_true",
        default=False,
        help="run only deterministic tests that do not require PostgreSQL",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Keep mutmut's per-mutant test runs isolated from integration fixtures.

    The regular suite remains unchanged. The mutation profile selects files
    with substantial synchronous unit coverage; any async integration cases in
    those mixed files are excluded before fixtures run.
    """
    if not config.getoption("--mutation-pure"):
        return
    integration_items = [
        item for item in items if asyncio.iscoroutinefunction(getattr(item, "obj", None))
    ]
    if not integration_items:
        return
    items[:] = [item for item in items if item not in integration_items]
    config.hook.pytest_deselected(items=integration_items)


def _admin_sql(sql: str) -> None:
    async def run() -> None:
        conn = await asyncpg.connect(ADMIN_URL)
        try:
            await conn.execute(sql)
        finally:
            await conn.close()

    asyncio.run(run())


@pytest.fixture(scope="session", autouse=True)
def _database(request: pytest.FixtureRequest):
    if request.config.getoption("--mutation-pure"):
        yield
        return
    _admin_sql(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
    _admin_sql(f'CREATE DATABASE "{TEST_DB}"')
    # Invoke alembic via the current interpreter so this works both under
    # a local `.venv` and CI's system-level `pip install -e '.[dev]'`.
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env=os.environ.copy(),
        check=True,
        capture_output=True,
        text=True,
    )

    async def seed_once() -> None:
        async with get_sessionmaker()() as db:
            await seed_reference_data(db)
            await db.commit()
        # Dispose on the loop that opened asyncpg connections. Disposing after
        # asyncio.run() closes it logs cross-loop errors during short reruns.
        await get_engine().dispose()

    asyncio.run(seed_once())
    reset_engine()  # seed_once bound the engine to a throwaway loop; tests rebind
    yield
    _admin_sql(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')


@pytest.fixture(autouse=True)
async def _clean_tables(request: pytest.FixtureRequest):
    if request.config.getoption("--mutation-pure"):
        yield
        return
    yield
    _ROTATED_EMAILS.clear()
    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    async with get_sessionmaker()() as db:
        await db.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))
        await seed_reference_data(db)
        await db.commit()


# Emails auto-rotated during the current test (gates the 401 retry).
_ROTATED_EMAILS: set[str] = set()


async def _rotate_provisioned_password(response: httpx.Response) -> None:
    """Transparent forced-rotation for owner-provisioned worker logins.

    Production blocks domain mutations until the worker changes an
    owner-set password; the test suite's workers act immediately, so the
    shared client finishes the rotation with a deterministic derived
    password ("{original}!r1"). A 401 on a repeat login transparently
    retries the derived form, so tests that keep using the original
    password work both before and after rotation. Owner resets re-flag the
    account and the next login simply rotates again.
    """
    import json as _json

    if not str(response.request.url).endswith("/api/auth/login"):
        return
    if response.request.headers.get("x-no-auto-rotate"):
        # The test drives the rotation itself (it needs the post-rotation
        # refresh cookie in THIS client's jar, which a hook cannot deliver).
        return
    try:
        request_content = response.request.content
    except httpx.RequestNotRead:
        # Streaming uploads (oversized-body probes in test_ops) never
        # materialize request content; nothing to inspect.
        return
    if not hasattr(response, "_content"):
        await response.aread()
    try:
        payload = _json.loads(request_content)
    except ValueError:
        return
    if not isinstance(payload, dict) or "email" not in payload or "password" not in payload:
        # Validation-garbage probes: not a rotation candidate.
        return
    email = payload["email"]
    submitted = payload["password"]

    async def _fresh_login(password: str) -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
        ) as probe:
            return await probe.post("/api/auth/login", json={"email": email, "password": password})

    if response.status_code == 401 and email in _ROTATED_EMAILS:
        # This account was auto-rotated earlier in the test; a login with the
        # original password retries the derived form. Never probe otherwise:
        # deliberate wrong-password tests count their verifies.
        retried = await _fresh_login(f"{submitted}!r1")
        if retried.status_code != 200:
            return  # genuinely wrong password: leave the 401 for the test
        response.status_code = 200
        response._content = retried.content
        response.headers["content-length"] = str(len(response._content))
        return  # already rotated on an earlier login

    if response.status_code != 200:
        return
    body = _json.loads(response.content)
    user = body.get("user") or {}
    if not user.get("must_change_password"):
        return
    rotated = f"{submitted}!r1"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as rotator:
        changed = await rotator.post(
            "/api/auth/change-password",
            json={"current_password": submitted, "new_password": rotated},
            headers={"Authorization": f"Bearer {body['access_token']}"},
        )
    assert changed.status_code == 200, changed.text
    new_body = changed.json()
    _ROTATED_EMAILS.add(email)
    patched = {**body, "access_token": new_body["access_token"], "user": new_body["user"]}
    response._content = _json.dumps(patched).encode()
    response.headers["content-length"] = str(len(response._content))
    # Carry the post-rotation session's cookies onto the login response so
    # the caller's client holds the NEW refresh cookie, not the pre-rotation
    # one the server set on this response.
    # Replace the pre-rotation refresh cookie with the post-rotation one.
    new_cookies = changed.headers.get_list("set-cookie")
    kept = [
        (k, v)
        for k, v in response.headers.raw
        if k.lower() != b"set-cookie" or b"refresh" not in v.lower()
    ]
    response.headers.raw[:] = kept + [(b"set-cookie", v.encode()) for v in new_cookies]


# Mutations whose Idempotency-Key the server now REQUIRES (no DB natural key
# backs them, so the key is the only replay defense). The test client injects
# a fresh key when one is absent so existing call sites keep exercising the
# business logic; the 422-on-keyless contract itself is pinned by dedicated
# tests in test_redteam_remediation_2026_09_04.py, which empty this set via
# monkeypatch to send genuinely keyless requests.
IDEMPOTENCY_REQUIRED_PATHS = {
    "/api/finance/new",
    "/api/feeding/dispense",
    "/api/feeding/mix",
    "/api/feeding/inventory-add",  # normalized: /api/feeding/inventory/{id}/add
    "/api/purchases/new",
    "/api/auth/farms",
}

# POST /api/animals requires the key only on the money-booking managed-purchase
# branch; the hook inspects the JSON body for that shape. Dedicated keyless
# tests disable this flag (and empty the set above) via monkeypatch.
AUTO_KEY_MANAGED_PURCHASE = True


def _is_managed_purchase_body(request: httpx.Request) -> bool:
    try:
        body = json.loads(request.content) if request.content else {}
    except ValueError:
        return False
    return (
        isinstance(body, dict)
        and body.get("source") == "PURCHASED"
        and not (body.get("historical_import_reason") or "").strip()
    )


async def _auto_idempotency_key(request: httpx.Request) -> None:
    if request.method.upper() != "POST":
        return
    path = request.url.path
    if re.fullmatch(r"/api/feeding/inventory/-?\d+/add", path):
        path = "/api/feeding/inventory-add"
    requires_key = path in IDEMPOTENCY_REQUIRED_PATHS or (
        path == "/api/animals" and AUTO_KEY_MANAGED_PURCHASE and _is_managed_purchase_body(request)
    )
    if not requires_key:
        return
    # Presence check, not truthiness: an explicitly empty or otherwise
    # invalid key must reach the server (and its 422), never be silently
    # replaced by a fresh valid one.
    if "idempotency-key" in request.headers:
        return
    request.headers["Idempotency-Key"] = f"test-auto-{uuid4().hex}"


@pytest.fixture()
async def client() -> AsyncGenerator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        event_hooks={
            "request": [_auto_idempotency_key],
            "response": [_rotate_provisioned_password],
        },
    ) as c:
        yield c


async def register(
    client: httpx.AsyncClient,
    email: str = "owner@farm.in",
    password: str = OWNER_PW,
    name: str | None = None,
) -> dict:
    """Register (which also logs in) → bearer headers."""
    resp = await client.post(
        "/api/auth/register", json={"email": email, "password": password, "name": name}
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def login_and_rotate(client: httpx.AsyncClient, email: str, password: str) -> dict:
    """Log in an owner-provisioned worker and complete the forced rotation
    through THIS client, so its cookie jar holds the post-rotation refresh
    session (session-lifecycle tests need exactly that)."""
    resp = await client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
        headers={"X-No-Auto-Rotate": "1"},
    )
    if resp.status_code == 401:
        # An earlier hook rotation already changed this credential.
        password = f"{password}!r1"
        resp = await client.post(
            "/api/auth/login",
            json={"email": email, "password": password},
            headers={"X-No-Auto-Rotate": "1"},
        )
    assert resp.status_code == 200, resp.text
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    changed = await client.post(
        "/api/auth/change-password",
        json={"current_password": password, "new_password": f"{password}!r1"},
        headers=headers,
    )
    assert changed.status_code == 200, changed.text
    return {"Authorization": f"Bearer {changed.json()['access_token']}"}


async def login(client: httpx.AsyncClient, email: str, password: str) -> dict:
    resp = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def create_farm(
    client: httpx.AsyncClient,
    headers: dict,
    name: str = "Alpha Farm",
) -> dict:
    """Create a farm → headers with X-Farm-Id added."""
    resp = await client.post("/api/auth/farms", json={"name": name}, headers=headers)
    assert resp.status_code == 201, resp.text
    return headers | {"X-Farm-Id": str(resp.json()["id"])}


async def owner_with_farm(
    client: httpx.AsyncClient, email: str = "owner@farm.in", farm_name: str = "Alpha Farm"
) -> dict:
    headers = await register(client, email)
    return await create_farm(client, headers, farm_name)
