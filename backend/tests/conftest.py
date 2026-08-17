"""Test fixtures: real PostgreSQL (`goatfarm_test` database), schema via
`alembic upgrade head` (subprocess, so the migration itself is under test),
per-test table truncation + reference-data seed, and an httpx AsyncClient
wired to the ASGI app. Async throughout (pytest-asyncio auto mode)."""

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncGenerator
from pathlib import Path

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
    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    async with get_sessionmaker()() as db:
        await db.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))
        await seed_reference_data(db)
        await db.commit()


@pytest.fixture()
async def client() -> AsyncGenerator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
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


async def login(client: httpx.AsyncClient, email: str, password: str) -> dict:
    resp = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def create_farm(client: httpx.AsyncClient, headers: dict, name: str = "Alpha Farm") -> dict:
    """Create a farm → headers with X-Farm-Id added."""
    resp = await client.post("/api/auth/farms", json={"name": name}, headers=headers)
    assert resp.status_code == 201, resp.text
    return headers | {"X-Farm-Id": str(resp.json()["id"])}


async def owner_with_farm(
    client: httpx.AsyncClient, email: str = "owner@farm.in", farm_name: str = "Alpha Farm"
) -> dict:
    headers = await register(client, email)
    return await create_farm(client, headers, farm_name)
