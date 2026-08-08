"""Ops readiness tests (audit 10-H2, 11-H1, 11-H3, 11-M9): unauthenticated
health/readiness probes, request-ID correlation, production-boot safety
validation, docs gating, the GOATFARM_TEST_DB footgun guard, and direct
coverage of seed_startup / backfill_task_assignments (the lifespan path the
httpx ASGI transport never triggers)."""

import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.db import get_sessionmaker
from app.main import create_app
from app.models import Farm, Role, Task, TaskCategory, User
from app.permissions import ROLE_PRESETS
from app.seed import seed_startup

BACKEND_DIR = Path(__file__).resolve().parent.parent

# --- health / readiness probes (11-H1) ---------------------------------------


async def test_healthz_is_unauthenticated(client: httpx.AsyncClient) -> None:
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_readyz_checks_the_pool(client: httpx.AsyncClient) -> None:
    resp = await client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready"}


async def test_request_id_round_trip(client: httpx.AsyncClient) -> None:
    resp = await client.get("/healthz")
    assert resp.headers["X-Request-ID"]  # generated UUID

    honored = await client.get("/healthz", headers={"X-Request-ID": "lb-trace-42"})
    assert honored.headers["X-Request-ID"] == "lb-trace-42"

    # Malformed inbound IDs are replaced, never echoed into logs/responses.
    rejected = await client.get("/healthz", headers={"X-Request-ID": "not an id!"})
    assert rejected.headers["X-Request-ID"] != "not an id!"


async def test_docs_available_in_development(client: httpx.AsyncClient) -> None:
    # Default environment is development: schema + interactive docs served
    # (Playwright and the Orval contract export depend on them).
    assert (await client.get("/openapi.json")).status_code == 200
    assert (await client.get("/docs")).status_code == 200


# --- production-boot safety (11-H3) ------------------------------------------


def test_production_refuses_insecure_cookie() -> None:
    with pytest.raises(ValidationError, match="GOATFARM_COOKIE_SECURE"):
        Settings(
            environment="production",
            cookie_secure=False,
            cors_origins=["https://app.example.com"],
            db_sslmode="require",
        )


def test_production_refuses_localhost_cors() -> None:
    with pytest.raises(ValidationError, match="must not include dev origins"):
        Settings(
            environment="production",
            cookie_secure=True,
            cors_origins=["http://localhost:3000"],
            db_sslmode="require",
        )


def test_production_refuses_empty_cors() -> None:
    with pytest.raises(ValidationError, match="must not be empty"):
        Settings(
            environment="production",
            cookie_secure=True,
            cors_origins=[],
            db_sslmode="require",
        )


def test_production_refuses_plaintext_db_sslmode() -> None:
    with pytest.raises(ValidationError, match="GOATFARM_DB_SSLMODE"):
        Settings(
            environment="production",
            cookie_secure=True,
            cors_origins=["https://app.example.com"],
            db_sslmode="disable",
        )


def test_production_accepts_valid_config() -> None:
    settings = Settings(
        environment="production",
        cookie_secure=True,
        cors_origins=["https://app.example.com"],
        db_sslmode="require",
    )
    assert settings.environment == "production"


def test_docs_gated_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOATFARM_ENVIRONMENT", "production")
    monkeypatch.setenv("GOATFARM_COOKIE_SECURE", "true")
    monkeypatch.setenv("GOATFARM_CORS_ORIGINS", '["https://app.example.com"]')
    monkeypatch.setenv("GOATFARM_DB_SSLMODE", "require")
    get_settings.cache_clear()
    try:
        app = create_app()
        assert app.openapi_url is None
        assert app.docs_url is None
        assert app.redoc_url is None
    finally:
        get_settings.cache_clear()


# --- test-DB footgun guard (10-M6 / 11-M9) ------------------------------------


def test_suite_refuses_database_not_ending_in_test() -> None:
    env = os.environ.copy()
    env["GOATFARM_TEST_DB"] = "goatfarm"  # the dev database — must be refused
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "tests/test_ops.py", "-q"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode != 0
    assert "must name a throwaway database" in result.stderr + result.stdout


# --- startup seeding / task backfill (10-H2) ----------------------------------


async def test_seed_startup_backfills_roles_and_is_idempotent() -> None:
    async with get_sessionmaker()() as db:
        owner = User(email="backfill-owner@farm.in", password_hash="argon2-placeholder")
        db.add(owner)
        await db.flush()
        farm = Farm(name="Backfill Farm", owner_id=owner.id)
        db.add(farm)
        await db.flush()
        # Orphan auto-generated tasks (created before role assignment existed)…
        orphan_vaccine = Task(
            farm_id=farm.id,
            title="PPR vaccine due",
            due_date=date(2026, 1, 10),
            category=TaskCategory.VACCINE.value,
            auto_generated=True,
        )
        orphan_move = Task(
            farm_id=farm.id,
            title="Move to FOUNDATION",
            due_date=date(2026, 1, 11),
            category=TaskCategory.BUCKET_MOVE.value,
            auto_generated=True,
        )
        # …and a manually created unassigned duty, which must be left alone.
        manual = Task(
            farm_id=farm.id,
            title="Fix the fence",
            due_date=date(2026, 1, 12),
            category=TaskCategory.OTHER.value,
            auto_generated=False,
        )
        db.add_all([orphan_vaccine, orphan_move, manual])
        await db.commit()
        farm_id = farm.id

    async with get_sessionmaker()() as db:
        await seed_startup(db)  # commits internally

    async with get_sessionmaker()() as db:
        roles = {
            role.code: role.id
            for role in (await db.execute(select(Role).where(Role.farm_id == farm_id))).scalars()
        }
        assert set(roles) == {preset["code"] for preset in ROLE_PRESETS}
        tasks = {
            task.title: task
            for task in (await db.execute(select(Task).where(Task.farm_id == farm_id))).scalars()
        }
        assert tasks["PPR vaccine due"].assigned_role_id == roles["VET"]
        assert tasks["Move to FOUNDATION"].assigned_role_id == roles["MOVER"]
        assert tasks["Fix the fence"].assigned_role_id is None
        vet_role_id = roles["VET"]

    # Idempotency: a second startup run creates no roles and reassigns nothing.
    async with get_sessionmaker()() as db:
        await seed_startup(db)
    async with get_sessionmaker()() as db:
        roles_after = (
            (await db.execute(select(Role).where(Role.farm_id == farm_id))).scalars().all()
        )
        assert len(roles_after) == len(ROLE_PRESETS)
        task_after = (
            await db.execute(
                select(Task).where(Task.farm_id == farm_id, Task.title == "PPR vaccine due")
            )
        ).scalar_one()
        assert task_after.assigned_role_id == vet_role_id
