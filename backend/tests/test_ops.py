"""Ops readiness tests: unauthenticated
health/readiness probes, request-ID correlation, production-boot safety
validation, docs gating, the GOATFARM_TEST_DB footgun guard, and direct
coverage of seed_startup / backfill_task_assignments (the lifespan path the
httpx ASGI transport never triggers)."""

import os
import subprocess
import sys
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import ValidationError
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.db import get_sessionmaker
from app.main import create_app
from app.models import Farm, Role, Task, TaskCategory, User
from app.permissions import ROLE_PRESETS
from app.security import validate_jwt_keypair
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


async def test_api_responses_are_never_cached(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401
    assert resp.headers["cache-control"] == "no-store"
    assert resp.headers["pragma"] == "no-cache"


async def test_oversized_content_length_is_rejected_before_parsing(
    client: httpx.AsyncClient,
) -> None:
    limit = get_settings().max_request_body_bytes
    resp = await client.post(
        "/api/auth/login",
        content=b"x" * (limit + 1),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 413
    assert resp.json() == {"detail": "Request body is too large"}
    assert resp.headers["cache-control"] == "no-store"


async def test_oversized_chunked_body_is_rejected_while_streaming(
    client: httpx.AsyncClient,
) -> None:
    limit = get_settings().max_request_body_bytes

    async def body() -> AsyncIterator[bytes]:
        yield b"x" * (limit // 2 + 1)
        yield b"y" * (limit // 2 + 1)

    resp = await client.post(
        "/api/auth/login",
        content=body(),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 413
    assert resp.json() == {"detail": "Request body is too large"}


# --- production-boot safety (11-H3) ------------------------------------------


def test_production_refuses_insecure_cookie() -> None:
    with pytest.raises(ValidationError, match="GOATFARM_COOKIE_SECURE"):
        Settings(
            environment="production",
            cookie_secure=False,
            cors_origins=["https://app.example.com"],
            db_sslmode="require",
            min_password_length=12,
        )


def test_production_refuses_localhost_cors() -> None:
    with pytest.raises(ValidationError, match="exact non-loopback HTTPS origins"):
        Settings(
            environment="production",
            cookie_secure=True,
            cors_origins=["http://localhost:3000"],
            db_sslmode="require",
            min_password_length=12,
        )


def test_production_refuses_empty_cors() -> None:
    with pytest.raises(ValidationError, match="must not be empty"):
        Settings(
            environment="production",
            cookie_secure=True,
            cors_origins=[],
            db_sslmode="require",
            min_password_length=12,
        )


def test_production_refuses_plaintext_db_sslmode() -> None:
    with pytest.raises(ValidationError, match="GOATFARM_DB_SSLMODE"):
        Settings(
            environment="production",
            cookie_secure=True,
            cors_origins=["https://app.example.com"],
            db_sslmode="disable",
            min_password_length=12,
        )


@pytest.mark.parametrize(
    "origin",
    [
        "*",
        "http://app.example.com",
        "https://127.0.0.1:3000",
        "https://user:pass@app.example.com",
        "https://app.example.com/path",
        "https://app.example.com?debug=1",
    ],
)
def test_production_refuses_non_exact_https_cors(origin: str) -> None:
    with pytest.raises(ValidationError, match="exact non-loopback HTTPS origins"):
        Settings(
            environment="production",
            cookie_secure=True,
            cors_origins=[origin],
            db_sslmode="require",
            min_password_length=12,
        )


def test_production_requires_twelve_character_password_minimum() -> None:
    with pytest.raises(ValidationError, match="MIN_PASSWORD_LENGTH"):
        Settings(
            environment="production",
            cookie_secure=True,
            cors_origins=["https://app.example.com"],
            db_sslmode="require",
            min_password_length=11,
        )


def test_production_accepts_valid_config() -> None:
    settings = Settings(
        environment="production",
        cookie_secure=True,
        cors_origins=["https://app.example.com"],
        db_sslmode="require",
        min_password_length=12,
    )
    assert settings.environment == "production"


def test_docs_gated_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOATFARM_ENVIRONMENT", "production")
    monkeypatch.setenv("GOATFARM_COOKIE_SECURE", "true")
    monkeypatch.setenv("GOATFARM_CORS_ORIGINS", '["https://app.example.com"]')
    monkeypatch.setenv("GOATFARM_DB_SSLMODE", "require")
    monkeypatch.setenv("GOATFARM_MIN_PASSWORD_LENGTH", "12")
    get_settings.cache_clear()
    try:
        app = create_app()
        assert app.openapi_url is None
        assert app.docs_url is None
        assert app.redoc_url is None
    finally:
        get_settings.cache_clear()


def _write_rsa_pair(private_path: Path, public_path: Path) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    public_path.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )


def _production_key_env(monkeypatch: pytest.MonkeyPatch, private: Path, public: Path) -> None:
    monkeypatch.setenv("GOATFARM_ENVIRONMENT", "production")
    monkeypatch.setenv("GOATFARM_COOKIE_SECURE", "true")
    monkeypatch.setenv("GOATFARM_CORS_ORIGINS", '["https://app.example.com"]')
    monkeypatch.setenv("GOATFARM_DB_SSLMODE", "require")
    monkeypatch.setenv("GOATFARM_MIN_PASSWORD_LENGTH", "12")
    monkeypatch.setenv("GOATFARM_JWT_PRIVATE_KEY_PATH", str(private))
    monkeypatch.setenv("GOATFARM_JWT_PUBLIC_KEY_PATH", str(public))
    get_settings.cache_clear()


def test_production_jwt_keys_are_required_at_startup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _production_key_env(
        monkeypatch, tmp_path / "missing-private.pem", tmp_path / "missing-public.pem"
    )
    try:
        with pytest.raises(RuntimeError, match="keypair is missing"):
            validate_jwt_keypair()
    finally:
        get_settings.cache_clear()


def test_production_jwt_keypair_must_match(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    private = tmp_path / "private.pem"
    unused_public = tmp_path / "unused-public.pem"
    other_private = tmp_path / "other-private.pem"
    public = tmp_path / "public.pem"
    _write_rsa_pair(private, unused_public)
    _write_rsa_pair(other_private, public)
    _production_key_env(monkeypatch, private, public)
    try:
        with pytest.raises(RuntimeError, match="do not match"):
            validate_jwt_keypair()
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
