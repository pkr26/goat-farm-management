"""Test fixtures: real PostgreSQL (`goatfarm_test` database), schema via
`alembic upgrade head` (subprocess, so the migration itself is under test),
per-test table truncation + reference-data seed, and an httpx AsyncClient
wired to the ASGI app. Async throughout (pytest-asyncio auto mode)."""

import asyncio
import os
import subprocess
from collections.abc import AsyncGenerator
from pathlib import Path

import asyncpg
import httpx
import pytest
from sqlalchemy import text

BACKEND_DIR = Path(__file__).resolve().parent.parent
TEST_DB = os.environ.get("GOATFARM_TEST_DB", "goatfarm_test")
ADMIN_URL = "postgresql://localhost:5432/postgres"
TEST_URL = f"postgresql+asyncpg://localhost:5432/{TEST_DB}"

os.environ["GOATFARM_DATABASE_URL"] = TEST_URL  # before any app import

from app.db import Base, get_sessionmaker, reset_engine  # noqa: E402
from app.main import create_app  # noqa: E402
from app.seed import seed_reference_data  # noqa: E402

OWNER_PW = "ownerpass123"


def _admin_sql(sql: str) -> None:
    async def run() -> None:
        conn = await asyncpg.connect(ADMIN_URL)
        try:
            await conn.execute(sql)
        finally:
            await conn.close()

    asyncio.run(run())


@pytest.fixture(scope="session", autouse=True)
def _database():
    _admin_sql(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
    _admin_sql(f'CREATE DATABASE "{TEST_DB}"')
    subprocess.run(
        [str(BACKEND_DIR / ".venv/bin/alembic"), "upgrade", "head"],
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

    asyncio.run(seed_once())
    reset_engine()  # seed_once bound the engine to a throwaway loop; tests rebind
    yield
    _admin_sql(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')


@pytest.fixture(autouse=True)
async def _clean_tables():
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
