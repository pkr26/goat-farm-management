"""Ops readiness tests: unauthenticated
health/readiness probes, request-ID correlation, production-boot safety
validation, docs gating, the GOATFARM_TEST_DB footgun guard, and direct
coverage of seed_startup / backfill_task_assignments_batch (the lifespan path
the httpx ASGI transport never triggers)."""

import asyncio
import importlib.util
import json
import logging
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import ValidationError
from sqlalchemy import delete, event, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import Message, Receive, Scope, Send

import app.core.config as config_module
import app.db as db_module
import app.main as main_module
import app.seed as seed_module
from app.core.config import (
    DEVELOPMENT_IDEMPOTENCY_HMAC_SECRET,
    PRODUCTION_REFRESH_COOKIE_NAME,
    MigrationSettings,
    ScreeningWorkerSettings,
    Settings,
    get_settings,
)
from app.db import get_engine, get_sessionmaker
from app.main import (
    CORS_EXPOSE_HEADERS,
    RequestBodyLimitMiddleware,
    create_app,
    lifespan,
)
from app.models import (
    BucketDefinition,
    Farm,
    FarmMembership,
    FeedInventory,
    FeedRecipe,
    FeedRecipeLine,
    IngredientCategory,
    Role,
    Task,
    TaskCategory,
    TaskStatus,
    User,
    VaccineTemplate,
)
from app.permissions import ROLE_PRESETS, preset_codes
from app.security import validate_jwt_keypair
from app.seed import (
    BUCKET_DEFINITIONS,
    FARM_INGREDIENTS,
    FEED_RECIPES,
    VACCINE_TEMPLATES,
    backfill_task_assignments_batch,
    repair_legacy_data_batch,
    repair_legacy_farms_batch,
    seed_default_roles,
    seed_farm_inventory,
    seed_new_farm,
    seed_reference_data,
    seed_startup,
)
from app.services._common import _default_role_id_for_category
from app.utils import utcnow

from .conftest import owner_with_farm

VALID_IDEMPOTENCY_HMAC_SECRET = "production-idempotency-hmac-secret-0000000001"
VALID_PREVIOUS_IDEMPOTENCY_HMAC_SECRET = "previous-production-idempotency-hmac-secret-0001"
VALID_TOTP_ENCRYPTION_KEY = "VFRUVFRUVFRUVFRUVFRUVFRUVFRUVFRUVFRUVFRUVFQ"
VALID_PREVIOUS_TOTP_ENCRYPTION_KEY = "UFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFA"

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _load_alembic_revision(module_name: str) -> ModuleType:
    """Import one revision file so a test can run the exact shipped SQL."""
    path = BACKEND_DIR / "alembic" / "versions" / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- health / readiness probes (11-H1) ---------------------------------------


async def test_healthz_is_unauthenticated(client: httpx.AsyncClient) -> None:
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_readyz_checks_the_pool(client: httpx.AsyncClient) -> None:
    resp = await client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready"}


async def test_generated_task_role_lookup_ignores_tombstoned_preset(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="tombstoned-preset-owner@example.test")
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        role = (
            await db.execute(select(Role).where(Role.farm_id == farm_id, Role.code == "VET"))
        ).scalar_one()
        role.deleted_at = utcnow()
        await db.commit()

    async with get_sessionmaker()() as db:
        assert await _default_role_id_for_category(db, farm_id, "VACCINE") is None


async def test_weaning_duty_routes_to_the_mover(client: httpx.AsyncClient) -> None:
    """Weaning (a day-60 pen move) is mover work."""
    goat_owner = await owner_with_farm(client, email="weaning-route-goat@example.test")
    goat_id = int(goat_owner["X-Farm-Id"])

    async def role_id(db: AsyncSession, farm_id: int, code: str) -> int:
        return (
            await db.execute(select(Role.id).where(Role.farm_id == farm_id, Role.code == code))
        ).scalar_one()

    async with get_sessionmaker()() as db:
        assert await _default_role_id_for_category(db, goat_id, "WEANING") == await role_id(
            db, goat_id, "MOVER"
        )


async def test_readyz_returns_documented_unavailable_body_when_pool_fails(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnavailableSession:
        async def __aenter__(self) -> None:
            raise RuntimeError("database unavailable")

        async def __aexit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(main_module, "get_sessionmaker", lambda: UnavailableSession)

    resp = await client.get("/readyz")

    assert resp.status_code == 503
    assert resp.json() == {"status": "unavailable"}


async def test_readyz_logs_the_failure_reason_when_the_pool_is_down(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The 503 body says nothing about why: pin the one operator-facing ERROR
    line — its exact text (what alert rules grep for) and its traceback."""

    class UnavailableSession:
        async def __aenter__(self) -> None:
            raise RuntimeError("database unavailable")

        async def __aexit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(main_module, "get_sessionmaker", lambda: UnavailableSession)
    caplog.handler.addFilter(main_module._RequestIdFilter())

    with caplog.at_level(logging.ERROR, logger="goatfarm"):
        resp = await client.get("/readyz")

    assert resp.status_code == 503
    records = [r for r in caplog.records if r.name == "goatfarm" and r.levelno == logging.ERROR]
    # Equality on the whole list also pins that the probe logs exactly once.
    assert [r.getMessage() for r in records] == ["readiness probe failed"]
    assert records[0].exc_info is not None
    assert "RuntimeError: database unavailable" in caplog.text


def test_probe_openapi_documents_success_and_readiness_failure_models() -> None:
    schema = create_app().openapi()
    health_responses = schema["paths"]["/healthz"]["get"]["responses"]
    ready_responses = schema["paths"]["/readyz"]["get"]["responses"]

    assert health_responses["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/HealthStatusOut"
    }
    assert ready_responses["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ReadinessStatusOut"
    }
    assert ready_responses["503"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ReadinessUnavailableOut"
    }
    for model_name in (
        "HealthStatusOut",
        "ReadinessStatusOut",
        "ReadinessUnavailableOut",
    ):
        assert schema["components"]["schemas"][model_name]["required"] == ["status"]


async def test_request_id_round_trip(client: httpx.AsyncClient) -> None:
    resp = await client.get("/healthz")
    assert resp.headers["X-Request-ID"]  # generated UUID

    honored = await client.get("/healthz", headers={"X-Request-ID": "lb-trace-42"})
    assert honored.headers["X-Request-ID"] == "lb-trace-42"

    # Malformed inbound IDs are replaced, never echoed into logs/responses.
    rejected = await client.get("/healthz", headers={"X-Request-ID": "not an id!"})
    assert rejected.headers["X-Request-ID"] != "not an id!"


async def test_backend_responses_have_baseline_browser_security_headers(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/healthz")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Permissions-Policy"] == ("camera=(), microphone=(), geolocation=()")


async def test_docs_available_in_development(client: httpx.AsyncClient) -> None:
    # Default environment is development: schema + interactive docs served
    # (Playwright and the Orval contract export depend on them).
    assert (await client.get("/openapi.json")).status_code == 200
    assert (await client.get("/docs")).status_code == 200


async def test_untrusted_host_is_rejected_before_routing(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz", headers={"Host": "attacker.example"})
    assert response.status_code == 400
    assert response.text == "Invalid host header"


async def test_api_responses_are_never_cached(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401
    assert resp.headers["cache-control"] == "no-store"
    assert resp.headers["pragma"] == "no-cache"


async def test_validation_errors_do_not_reflect_rejected_sensitive_input(
    client: httpx.AsyncClient,
) -> None:
    secret = "do-not-echo-this-password"
    resp = await client.post(
        "/api/auth/login",
        json={"email": "person@example.com", "password": [secret]},
    )
    assert resp.status_code == 422
    assert secret not in resp.text
    assert resp.json()["detail"]
    assert all(set(error) <= {"type", "loc", "msg"} for error in resp.json()["detail"])


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


# --- request body / target limit middleware (11-H2) --------------------------


def _http_scope(
    *,
    method: str = "GET",
    path: str = "/api/ping",
    raw_path: bytes | None = None,
    query_string: bytes = b"",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> dict[str, Any]:
    """Build a minimal ASGI http scope for driving the limit middleware directly.

    httpx.ASGITransport only ever builds ASCII `type: "http"` scopes whose
    raw_path mirrors the decoded path, so a hand-built scope is the only way to
    exercise the raw_path/fallback/boundary arithmetic.
    """
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8") if raw_path is None else raw_path,
        "query_string": query_string,
        "root_path": "",
        "headers": list(headers or []),
        "client": ("127.0.0.1", 5000),
        "server": ("testserver", 80),
    }


async def _drive_body_limit(
    scope: dict[str, Any],
    *,
    body: bytes = b"",
    max_bytes: int = 1024,
    max_target_bytes: int = 64,
) -> tuple[int, bytes, int]:
    """Run RequestBodyLimitMiddleware over one hand-built scope.

    Returns (status, response body, downstream invocation count) so a test can
    pin that the middleware answered *before* the app ever ran.
    """
    calls = 0
    status = 0
    chunks: list[bytes] = []

    async def downstream(inner_scope: Scope, inner_receive: Receive, inner_send: Send) -> None:
        nonlocal calls
        calls += 1
        await inner_receive()
        await inner_send({"type": "http.response.start", "status": 200, "headers": []})
        await inner_send({"type": "http.response.body", "body": b"downstream"})

    async def receive() -> Message:
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: Message) -> None:
        nonlocal status
        if message["type"] == "http.response.start":
            status = int(message["status"])
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))

    middleware = RequestBodyLimitMiddleware(
        downstream, max_bytes=max_bytes, max_target_bytes=max_target_bytes
    )
    await middleware(scope, receive, send)
    return status, b"".join(chunks), calls


async def test_non_http_scope_is_forwarded_to_the_app_untouched() -> None:
    """Lifespan (and any non-http) scope reaches the app with all three args intact."""
    seen: list[tuple[str, Any]] = []

    async def receive() -> Message:
        return {"type": "lifespan.startup"}

    async def send(message: Message) -> None:
        seen.append(("send", message))

    async def downstream(scope: Scope, inner_receive: Receive, inner_send: Send) -> None:
        seen.append(("scope", scope["type"]))
        message = await inner_receive()
        seen.append(("receive", message["type"]))
        await inner_send({"type": "lifespan.startup.complete"})

    middleware = RequestBodyLimitMiddleware(downstream, max_bytes=1, max_target_bytes=1)
    await middleware({"type": "lifespan", "asgi": {"version": "3.0"}}, receive, send)

    assert seen == [
        ("scope", "lifespan"),
        ("receive", "lifespan.startup"),
        ("send", {"type": "lifespan.startup.complete"}),
    ]


async def test_request_target_bound_measures_raw_path_not_decoded_path() -> None:
    """A percent-encoded target must not escape the bound by decoding shorter."""
    scope = _http_scope(raw_path=b"/" + b"%20" * 40, path="/" + " " * 40)

    status, payload, calls = await _drive_body_limit(scope)

    assert status == 414
    assert json.loads(payload) == {"detail": "Request target is too long"}
    assert calls == 0


async def test_request_target_bound_holds_when_the_server_omits_raw_path() -> None:
    """raw_path is optional in the ASGI scope; the decoded path still bounds the target."""
    scope = _http_scope(path="/" + "x" * 100)
    del scope["raw_path"]

    status, payload, calls = await _drive_body_limit(scope)

    assert status == 414
    assert json.loads(payload) == {"detail": "Request target is too long"}
    assert calls == 0


async def test_request_target_exactly_at_the_limit_is_allowed() -> None:
    """Accept side of the target frontier: path + '?' + query == the limit passes."""
    scope = _http_scope(raw_path=b"/" + b"a" * 31, query_string=b"q=" + b"b" * 29)
    assert len(scope["raw_path"]) + 1 + len(scope["query_string"]) == 64

    status, payload, calls = await _drive_body_limit(scope)

    assert (status, calls) == (200, 1)
    assert payload == b"downstream"


async def test_request_target_one_byte_over_the_limit_is_rejected() -> None:
    """Reject side of the target frontier: one byte past the limit is a 414."""
    scope = _http_scope(raw_path=b"/" + b"a" * 31, query_string=b"q=" + b"b" * 30)

    status, payload, calls = await _drive_body_limit(scope)

    assert (status, calls) == (414, 0)
    assert json.loads(payload) == {"detail": "Request target is too long"}


async def test_query_less_target_is_charged_no_separator_byte() -> None:
    """No query string means no '?' byte is charged against the target bound."""
    scope = _http_scope(raw_path=b"/" + b"a" * 63, query_string=b"")

    status, payload, calls = await _drive_body_limit(scope)

    assert (status, calls) == (200, 1)
    assert payload == b"downstream"


async def test_declared_content_length_short_circuits_before_the_app_runs() -> None:
    """The Content-Length pre-check answers 413 without invoking the app at all."""
    scope = _http_scope(method="POST", headers=[(b"content-length", b"999999")])

    status, payload, calls = await _drive_body_limit(scope, body=b"{}")

    assert status == 413
    assert json.loads(payload) == {"detail": "Request body is too large"}
    assert calls == 0


async def test_content_length_header_lookup_is_case_insensitive() -> None:
    """Header names are folded before the Content-Length lookup, not compared raw."""
    scope = _http_scope(method="POST", headers=[(b"Content-Length", b"999999")])

    status, payload, calls = await _drive_body_limit(scope, body=b"{}")

    assert (status, calls) == (413, 0)
    assert json.loads(payload) == {"detail": "Request body is too large"}


async def test_declared_oversize_content_length_is_rejected_without_reading_body(
    client: httpx.AsyncClient,
) -> None:
    """A small body with a lying Content-Length is refused by the header pre-check."""
    limit = get_settings().max_request_body_bytes

    resp = await client.post(
        "/api/auth/login",
        content=b'{"email":"a@b.in","password":"x"}',
        headers={"content-type": "application/json", "content-length": str(limit + 1)},
    )

    assert resp.status_code == 413, resp.text
    assert resp.json() == {"detail": "Request body is too large"}


async def test_oversized_content_length_is_rejected_before_a_body_byte_is_read(
    client: httpx.AsyncClient,
) -> None:
    """The declared-size 413 fires before the body stream is pulled even once."""
    limit = get_settings().max_request_body_bytes
    pulled: list[int] = []

    async def body() -> AsyncIterator[bytes]:
        pulled.append(1)
        yield b"x" * (limit + 1)

    resp = await client.post(
        "/api/auth/login",
        content=body(),
        headers={"Content-Type": "application/json", "Content-Length": str(limit + 1)},
    )

    assert resp.status_code == 413, resp.text
    assert resp.json() == {"detail": "Request body is too large"}
    assert pulled == []


@pytest.mark.parametrize("raw_length", ["abc", "-1"])
async def test_malformed_content_length_is_rejected_with_400(
    client: httpx.AsyncClient, raw_length: str
) -> None:
    """A non-integer or negative Content-Length is a framing error, not a 422/500."""
    resp = await client.post(
        "/api/auth/login",
        content=b"{}",
        headers={"Content-Type": "application/json", "Content-Length": raw_length},
    )

    assert resp.status_code == 400, resp.text
    assert resp.json() == {"detail": "Invalid Content-Length"}


async def test_body_exactly_at_the_limit_is_accepted(client: httpx.AsyncClient) -> None:
    """Accept side of the body frontier: exactly max_request_body_bytes reaches the route."""
    limit = get_settings().max_request_body_bytes

    sized = await client.post(
        "/api/auth/login",
        content=b"x" * limit,
        headers={"Content-Type": "application/json"},
    )
    assert sized.status_code == 422, sized.status_code

    async def body() -> AsyncIterator[bytes]:
        yield b"x" * (limit // 2)
        yield b"y" * (limit - limit // 2)

    streamed = await client.post(
        "/api/auth/login",
        content=body(),
        headers={"Content-Type": "application/json"},
    )
    assert streamed.status_code == 422, streamed.status_code


# --- production-boot safety (11-H3) ------------------------------------------


def test_production_refuses_insecure_cookie() -> None:
    with pytest.raises(ValidationError, match="GOATFARM_COOKIE_SECURE"):
        Settings(
            environment="production",
            cookie_secure=False,
            cors_origins=["https://app.example.com"],
            db_sslmode="verify-full",
            min_password_length=12,
        )


def test_production_refuses_localhost_cors() -> None:
    with pytest.raises(ValidationError, match="exact non-loopback HTTPS origins"):
        Settings(
            environment="production",
            cookie_secure=True,
            cors_origins=["http://localhost:3000"],
            db_sslmode="verify-full",
            min_password_length=12,
        )


def test_production_refuses_empty_cors() -> None:
    with pytest.raises(ValidationError, match="must not be empty"):
        Settings(
            environment="production",
            cookie_secure=True,
            cors_origins=[],
            db_sslmode="verify-full",
            min_password_length=12,
        )


@pytest.mark.parametrize("sslmode", ["disable", "allow", "prefer", "require", "verify-ca"])
def test_production_refuses_db_sslmode_without_hostname_verification(sslmode: str) -> None:
    with pytest.raises(ValidationError, match="GOATFARM_DB_SSLMODE"):
        Settings(
            environment="production",
            cookie_secure=True,
            cors_origins=["https://app.example.com"],
            db_sslmode=sslmode,  # type: ignore[arg-type]
            min_password_length=12,
        )


@pytest.mark.parametrize("sslmode", ["disable", "allow", "prefer", "require", "verify-ca"])
def test_production_migration_refuses_db_without_hostname_verification(sslmode: str) -> None:
    with pytest.raises(ValidationError, match=r"Refusing migration.*GOATFARM_DB_SSLMODE"):
        MigrationSettings(
            environment="production",
            migration_database_url="postgresql+asyncpg://migrator@db:5432/goatfarm",
            db_sslmode=sslmode,  # type: ignore[arg-type]
        )


def test_production_migration_accepts_tls_without_api_secrets() -> None:
    settings = MigrationSettings(
        environment="production",
        migration_database_url="postgresql+asyncpg://migrator@db:5432/goatfarm",
        db_sslmode="verify-full",
    )
    assert settings.environment == "production"


def test_production_migration_requires_the_dedicated_ddl_url() -> None:
    """Production Alembic must never fall back to the API database identity."""
    with pytest.raises(ValidationError, match="GOATFARM_MIGRATION_DATABASE_URL is required"):
        MigrationSettings(
            environment="production",
            database_url="postgresql+asyncpg://api@db.example.test:5432/goatfarm",
            db_sslmode="verify-full",
        )


def test_database_verify_modes_build_a_context_without_asyncpg_home_ca_lookup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Container verify-full must use system/mounted trust, not ~/.postgresql.

    asyncpg only searches a per-user ``root.crt`` when handed a mode string;
    the production image deliberately has no writable home. The database
    layer must hand it a fully configured stdlib context instead.
    """

    class FakeContext:
        check_hostname = True

    calls: list[str | None] = []

    def fake_default_context(*, cafile: str | None = None) -> FakeContext:
        calls.append(cafile)
        return FakeContext()

    monkeypatch.setattr(db_module.ssl, "create_default_context", fake_default_context)

    system_context = db_module.database_ssl_connect_arg(Settings(db_sslmode="verify-full"))
    assert isinstance(system_context, FakeContext)
    assert system_context.check_hostname is True
    assert calls == [None]

    private_ca = tmp_path / "postgres-ca.pem"
    private_ca.write_text("test-only-readable-ca")
    custom_context = db_module.database_ssl_connect_arg(
        Settings(db_sslmode="verify-full", db_sslrootcert_path=private_ca)
    )
    assert isinstance(custom_context, FakeContext)
    assert custom_context.check_hostname is True
    assert calls[-1] == str(private_ca)

    verify_ca_context = db_module.database_ssl_connect_arg(
        Settings(db_sslmode="verify-ca", db_sslrootcert_path=private_ca)
    )
    assert isinstance(verify_ca_context, FakeContext)
    assert verify_ca_context.check_hostname is False
    assert db_module.database_ssl_connect_arg(Settings(db_sslmode="prefer")) == "prefer"

    with pytest.raises(ValidationError, match="GOATFARM_DB_SSLROOTCERT_PATH requires"):
        Settings(db_sslmode="disable", db_sslrootcert_path=private_ca)
    with pytest.raises(ValidationError, match="GOATFARM_DB_SSLROOTCERT_PATH must name"):
        Settings(db_sslmode="verify-full", db_sslrootcert_path=tmp_path / "missing.pem")

    migration = MigrationSettings(
        environment="production",
        migration_database_url="postgresql+asyncpg://migrator@db.example.test:5432/goatfarm",
        db_sslmode="verify-full",
        db_sslrootcert_path=private_ca,
    )
    migration_context = db_module.database_ssl_connect_arg(migration)
    assert isinstance(migration_context, FakeContext)
    assert migration_context.check_hostname is True
    # Alembic must actually CALL the shared SSL helper, not merely mention it:
    # walk env.py's AST for a real call (a comment or an import used to
    # satisfy the old text grep — 2026-09-18 audit L-9).
    import ast as _ast

    env_tree = _ast.parse((BACKEND_DIR / "alembic" / "env.py").read_text())
    called_names = {
        node.func.id
        for node in _ast.walk(env_tree)
        if isinstance(node, _ast.Call) and isinstance(node.func, _ast.Name)
    }
    assert "database_ssl_connect_arg" in called_names


def test_scheme_only_https_urls_fail_fast_at_settings_validation() -> None:
    """``https://`` (no host) must not pass the boot gate (audit L-10).

    Such a value previously sailed through every https-or-loopback validator
    and only surfaced as recurring per-request provider/S3 errors, contrary
    to the config module's fail-fast contract."""
    from pydantic import ValidationError

    for kwargs in (
        {"screening_anthropic_base_url": "https://"},
        {"screening_openai_base_url": "https://:443"},
        {"s3_endpoint_url": "https://"},
    ):
        with pytest.raises(ValidationError, match="must include a host"):
            Settings(_env_file=None, **kwargs)
        worker_field = next(iter(kwargs))
        with pytest.raises(ValidationError, match="must include a host"):
            ScreeningWorkerSettings(_env_file=None, **{worker_field: kwargs[worker_field]})
    with pytest.raises(ValidationError, match="must include a host"):
        config_module.ScreeningRotationProvider(
            kind="openai_compatible",
            name="edge",
            base_url="https://",
            api_key="k",
            model="m",
        )
    # Real hosts on https (and loopback http) still validate.
    assert (
        Settings(
            _env_file=None, screening_anthropic_base_url="https://api.example.test"
        ).screening_anthropic_base_url
        == "https://api.example.test"
    )
    assert (
        ScreeningWorkerSettings(
            _env_file=None, s3_endpoint_url="http://127.0.0.1:9000"
        ).s3_endpoint_url
        == "http://127.0.0.1:9000"
    )


def test_empty_previous_totp_keys_env_is_treated_as_no_predecessors() -> None:
    """Compose's optional interpolation yields ""; that must boot (audit note)."""
    settings = Settings(_env_file=None, totp_encryption_previous_keys="")
    assert settings.totp_encryption_previous_keys == []


@pytest.mark.parametrize(
    ("settings_type", "field_name"),
    [
        (Settings, "database_url"),
        (MigrationSettings, "database_url"),
        (MigrationSettings, "migration_database_url"),
        (ScreeningWorkerSettings, "database_url"),
    ],
)
def test_every_database_settings_projection_requires_the_asyncpg_url_scheme(
    settings_type: type[Settings] | type[MigrationSettings] | type[ScreeningWorkerSettings],
    field_name: str,
) -> None:
    with pytest.raises(ValidationError, match=r"postgresql\+asyncpg"):
        settings_type(_env_file=None, **{field_name: "postgresql://db.example.test/goatfarm"})


def test_matching_libpq_sslmode_is_removed_before_asyncpg_receives_the_url() -> None:
    raw_url = "postgresql+asyncpg://api@db.example.test:5432/goatfarm?sslmode=verify-full"
    expected_url = "postgresql+asyncpg://api@db.example.test:5432/goatfarm"

    api = Settings(_env_file=None, database_url=raw_url, db_sslmode="verify-full")
    migration = MigrationSettings(
        _env_file=None,
        migration_database_url=raw_url,
        db_sslmode="verify-full",
    )
    worker = ScreeningWorkerSettings(_env_file=None, database_url=raw_url, db_sslmode="verify-full")

    assert api.database_url == expected_url
    assert migration.migration_database_url == expected_url
    assert worker.database_url == expected_url

    engine = db_module.create_engine(api)
    try:
        connect_parameters = engine.sync_engine.dialect.create_connect_args(engine.url)[1]
        assert "sslmode" not in connect_parameters
        assert engine.url.drivername == "postgresql+asyncpg"
    finally:
        engine.sync_engine.dispose()


def test_database_url_rejects_conflicting_or_driver_incompatible_tls_query_settings() -> None:
    with pytest.raises(ValidationError, match=r"sslmode=.*conflicts with GOATFARM_DB_SSLMODE"):
        Settings(
            _env_file=None,
            database_url=("postgresql+asyncpg://api@db.example.test/goatfarm?sslmode=require"),
            db_sslmode="verify-full",
        )
    with pytest.raises(ValidationError, match=r"URL TLS parameter\(s\) sslrootcert"):
        Settings(
            _env_file=None,
            database_url=(
                "postgresql+asyncpg://api@db.example.test/goatfarm?sslrootcert=/tmp/ca.pem"
            ),
            db_sslmode="verify-full",
        )


def test_migration_refuses_unknown_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo must not silently select development defaults for the DDL job."""
    monkeypatch.delenv("GOATFARM_ENVIRONMENT", raising=False)
    monkeypatch.setenv("GOATFARM_ENVIRONMNET", "production")
    monkeypatch.setenv("GOATFARM_DB_SSLMODE", "disable")
    with pytest.raises(ValidationError, match="GOATFARM_ENVIRONMNET"):
        MigrationSettings(_env_file=None)


def test_api_accepts_known_compose_edge_only_environment_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The API typo guard must not reject the edge's intentionally shared knobs."""
    monkeypatch.setenv("GOATFARM_EDGE_MAX_BODY_SIZE", "1m")
    monkeypatch.setenv("GOATFARM_CSP_CONNECT_ORIGINS", "https://bucket.example.test")
    monkeypatch.setenv("GOATFARM_CSP_IMG_ORIGINS", "https://bucket.example.test")
    assert Settings(_env_file=None).environment == "development"


def test_migration_refuses_unknown_shared_dotenv_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The least-privilege migration projection may ignore API keys, not typos."""
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "GOATFARM_ENVIRONMENT=production\n"
        "GOATFARM_DB_SSLMODE=verify-full\n"
        "GOATFARM_ENVIRONMNET=production\n"
    )
    monkeypatch.setattr(config_module, "BACKEND_DIR", tmp_path)

    with pytest.raises(ValidationError, match="GOATFARM_ENVIRONMNET"):
        MigrationSettings(_env_file=dotenv_path)


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
            db_sslmode="verify-full",
            min_password_length=12,
        )


def test_production_requires_twelve_character_password_minimum() -> None:
    with pytest.raises(ValidationError, match="MIN_PASSWORD_LENGTH"):
        Settings(
            environment="production",
            cookie_secure=True,
            cors_origins=["https://app.example.com"],
            db_sslmode="verify-full",
            min_password_length=11,
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("argon2_time_cost", 1, "GOATFARM_ARGON2_TIME_COST"),
        ("argon2_memory_cost", 19 * 1024 - 1, "GOATFARM_ARGON2_MEMORY_COST"),
        ("argon2_hash_len", 31, "GOATFARM_ARGON2_HASH_LEN"),
    ],
)
def test_production_refuses_weak_argon2_profile(
    field: str,
    value: int,
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        Settings(
            environment="production",
            cookie_secure=True,
            cors_origins=["https://app.example.com"],
            allowed_hosts=["api.example.com"],
            db_sslmode="verify-full",
            min_password_length=12,
            **{field: value},  # type: ignore[arg-type]
        )


def test_production_accepts_valid_config() -> None:
    settings = Settings(
        environment="production",
        cookie_secure=True,
        cors_origins=["https://app.example.com"],
        allowed_hosts=["api.example.com"],
        db_sslmode="verify-full",
        min_password_length=12,
        idempotency_request_hmac_secret=VALID_IDEMPOTENCY_HMAC_SECRET,
        idempotency_request_hmac_previous_secrets=[VALID_PREVIOUS_IDEMPOTENCY_HMAC_SECRET],
        totp_encryption_key=VALID_TOTP_ENCRYPTION_KEY,
    )
    assert settings.environment == "production"
    assert settings.refresh_cookie_name == PRODUCTION_REFRESH_COOKIE_NAME


def _valid_production_totp_kwargs() -> dict[str, object]:
    return {
        "environment": "production",
        "cookie_secure": True,
        "cors_origins": ["https://app.example.com"],
        "allowed_hosts": ["api.example.com"],
        "db_sslmode": "verify-full",
        "min_password_length": 12,
        "idempotency_request_hmac_secret": VALID_IDEMPOTENCY_HMAC_SECRET,
        "totp_encryption_key": VALID_TOTP_ENCRYPTION_KEY,
    }


def test_production_requires_an_independent_totp_encryption_key() -> None:
    kwargs = _valid_production_totp_kwargs()
    del kwargs["totp_encryption_key"]
    with pytest.raises(ValidationError, match="GOATFARM_TOTP_ENCRYPTION_KEY is required"):
        Settings(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "key",
    ["not-base64url", "VFRUVFRUVFRUVFRUVFRUVFRUVFRUVFRUVFRUVFRUV"],
)
def test_totp_encryption_key_requires_canonical_32_byte_base64url(key: str) -> None:
    with pytest.raises(ValidationError, match="GOATFARM_TOTP_ENCRYPTION_KEY"):
        Settings(totp_encryption_key=key)


def test_totp_encryption_keyring_rejects_duplicate_or_current_predecessor() -> None:
    duplicate_kwargs = _valid_production_totp_kwargs()
    duplicate_kwargs["totp_encryption_previous_keys"] = [
        VALID_PREVIOUS_TOTP_ENCRYPTION_KEY,
        VALID_PREVIOUS_TOTP_ENCRYPTION_KEY,
    ]
    with pytest.raises(ValidationError, match="must not contain duplicates"):
        Settings(**duplicate_kwargs)  # type: ignore[arg-type]

    current_kwargs = _valid_production_totp_kwargs()
    current_kwargs["totp_encryption_previous_keys"] = [VALID_TOTP_ENCRYPTION_KEY]
    with pytest.raises(ValidationError, match="must not also appear"):
        Settings(**current_kwargs)  # type: ignore[arg-type]


def test_totp_encryption_previous_keyring_is_bounded_and_needs_a_current_key() -> None:
    with pytest.raises(ValidationError):
        Settings(totp_encryption_previous_keys=[VALID_TOTP_ENCRYPTION_KEY] * 4)
    with pytest.raises(ValidationError, match="requires a current"):
        Settings(totp_encryption_previous_keys=[VALID_TOTP_ENCRYPTION_KEY])


def test_production_rejects_refresh_cookie_without_host_prefix() -> None:
    with pytest.raises(ValidationError, match="GOATFARM_REFRESH_COOKIE_NAME"):
        Settings(
            environment="production",
            cookie_secure=True,
            refresh_cookie_name="legacy_refresh",
            cors_origins=["https://app.example.com"],
            allowed_hosts=["api.example.com"],
            db_sslmode="verify-full",
            min_password_length=12,
            idempotency_request_hmac_secret=VALID_IDEMPOTENCY_HMAC_SECRET,
        )


@pytest.mark.parametrize("name", ["", "refresh cookie", "refresh;legacy", "refresh=legacy"])
def test_settings_rejects_invalid_refresh_cookie_name(name: str) -> None:
    with pytest.raises(ValidationError, match="valid cookie token"):
        Settings(refresh_cookie_name=name)


def test_host_prefixed_refresh_cookie_requires_secure_transport() -> None:
    with pytest.raises(ValidationError, match="GOATFARM_COOKIE_SECURE"):
        Settings(refresh_cookie_name="__Host-goatfarm_refresh", cookie_secure=False)


@pytest.mark.parametrize(
    ("current", "previous", "message"),
    [
        (None, [], "externally supplied"),
        ("too-short", [], "at least 32 characters"),
        (
            VALID_IDEMPOTENCY_HMAC_SECRET,
            [VALID_PREVIOUS_IDEMPOTENCY_HMAC_SECRET] * 2,
            "must not contain duplicates",
        ),
        (
            VALID_IDEMPOTENCY_HMAC_SECRET,
            [VALID_IDEMPOTENCY_HMAC_SECRET],
            "must not also appear",
        ),
        (
            VALID_IDEMPOTENCY_HMAC_SECRET,
            [DEVELOPMENT_IDEMPOTENCY_HMAC_SECRET],
            "known development fallback",
        ),
    ],
)
def test_production_rejects_invalid_idempotency_hmac_keyring(
    current: str | None,
    previous: list[str],
    message: str,
) -> None:
    kwargs: dict[str, object] = {
        "environment": "production",
        "cookie_secure": True,
        "cors_origins": ["https://app.example.com"],
        "allowed_hosts": ["api.example.com"],
        "db_sslmode": "verify-full",
        "min_password_length": 12,
        "idempotency_request_hmac_previous_secrets": previous,
    }
    if current is not None:
        kwargs["idempotency_request_hmac_secret"] = current
    with pytest.raises(ValidationError, match=message):
        Settings(**kwargs)  # type: ignore[arg-type]


def test_idempotency_hmac_previous_keyring_is_bounded() -> None:
    with pytest.raises(ValidationError):
        Settings(
            idempotency_request_hmac_previous_secrets=[
                f"previous-idempotency-secret-{index:020d}" for index in range(4)
            ]
        )


@pytest.mark.parametrize("host", [[], ["*"], ["localhost"], ["https://api.example.com"]])
def test_production_refuses_unsafe_allowed_hosts(host: list[str]) -> None:
    with pytest.raises(ValidationError, match="GOATFARM_ALLOWED_HOSTS"):
        Settings(
            environment="production",
            cookie_secure=True,
            cors_origins=["https://app.example.com"],
            allowed_hosts=host,
            db_sslmode="verify-full",
            min_password_length=12,
        )


def test_settings_reject_unknown_keys() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        Settings(cookie_secur=True)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("access_token_ttl_seconds", 60 * 60 * 24 + 1),
        ("refresh_token_ttl_seconds", 60 * 60 * 24 * 365 + 1),
        ("argon2_time_cost", 7),
        ("argon2_memory_cost", 131_073),
        ("argon2_parallelism", 9),
        ("argon2_hash_len", 65),
        ("min_password_length", 129),
        ("max_pending_manual_tasks_per_farm", 100_001),
    ],
)
def test_security_settings_reject_dangerous_or_impossible_upper_bounds(
    field: str,
    value: int,
) -> None:
    with pytest.raises(ValidationError):
        Settings(**{field: value})  # type: ignore[arg-type]


def test_argon2_memory_must_cover_every_parallel_lane() -> None:
    with pytest.raises(ValidationError, match="8 \\* GOATFARM_ARGON2_PARALLELISM"):
        Settings(argon2_memory_cost=8, argon2_parallelism=2)


def test_pending_manual_task_limit_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(max_pending_manual_tasks_per_farm=0)


def test_docs_gated_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOATFARM_ENVIRONMENT", "production")
    monkeypatch.setenv("GOATFARM_COOKIE_SECURE", "true")
    monkeypatch.setenv("GOATFARM_CORS_ORIGINS", '["https://app.example.com"]')
    monkeypatch.setenv("GOATFARM_ALLOWED_HOSTS", '["api.example.com"]')
    monkeypatch.setenv("GOATFARM_DB_SSLMODE", "verify-full")
    monkeypatch.setenv("GOATFARM_MIN_PASSWORD_LENGTH", "12")
    monkeypatch.setenv(
        "GOATFARM_IDEMPOTENCY_REQUEST_HMAC_SECRET",
        VALID_IDEMPOTENCY_HMAC_SECRET,
    )
    monkeypatch.setenv("GOATFARM_TOTP_ENCRYPTION_KEY", VALID_TOTP_ENCRYPTION_KEY)
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
    monkeypatch.setenv("GOATFARM_ALLOWED_HOSTS", '["api.example.com"]')
    monkeypatch.setenv("GOATFARM_DB_SSLMODE", "verify-full")
    monkeypatch.setenv("GOATFARM_MIN_PASSWORD_LENGTH", "12")
    monkeypatch.setenv(
        "GOATFARM_IDEMPOTENCY_REQUEST_HMAC_SECRET",
        VALID_IDEMPOTENCY_HMAC_SECRET,
    )
    monkeypatch.setenv("GOATFARM_TOTP_ENCRYPTION_KEY", VALID_TOTP_ENCRYPTION_KEY)
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


def test_reset_engine_disposes_the_cached_engine_exactly_once() -> None:
    """The hook conftest uses between event loops must actually drain the pool
    (and stay a quiet no-op when nothing is cached), not silently skip it."""
    disposed: list[int] = []

    class FakeEngine:
        async def dispose(self) -> None:
            disposed.append(1)

    previous_engine, previous_sessionmaker = db_module._engine, db_module._sessionmaker
    try:
        db_module._engine = FakeEngine()  # type: ignore[assignment]
        db_module._sessionmaker = object()  # type: ignore[assignment]

        db_module.reset_engine()

        assert disposed == [1], "cached engine must be disposed exactly once"
        assert db_module._engine is None
        assert db_module._sessionmaker is None

        # Nothing cached any more: a second reset is a no-op, never an
        # AttributeError on None.
        db_module.reset_engine()

        assert disposed == [1], "no engine cached -> nothing to dispose"
        assert db_module._engine is None
    finally:
        db_module._engine, db_module._sessionmaker = previous_engine, previous_sessionmaker


# --- startup seeding / task backfill (10-H2) ----------------------------------


async def test_lifespan_boots_with_all_bounded_maintenance_signatures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Signature drift in a cleanup helper must never make every process fail boot."""
    monkeypatch.setattr(main_module, "validate_jwt_keypair", lambda: None)
    monkeypatch.setattr(main_module, "prime_dummy_password_hash", lambda: None)

    app = create_app()
    async with lifespan(app):
        # Reaching the yield proves fixed-size reference seeding and the finite
        # startup refresh purge accepted their configured arguments. Exiting
        # also proves every post-readiness worker cancels cleanly.
        assert app is not None


async def test_reference_seed_repairs_missing_release_rows_without_rewriting_existing() -> None:
    missing_bucket = BUCKET_DEFINITIONS[-1][0].value
    missing_recipe, *_ = FEED_RECIPES[-1]
    missing_vaccine = VACCINE_TEMPLATES[-1][0]
    preserved_bucket = BUCKET_DEFINITIONS[0][0].value

    async with get_sessionmaker()() as db:
        recipe_id = (
            await db.execute(select(FeedRecipe.id).where(FeedRecipe.code == missing_recipe))
        ).scalar_one()
        await db.execute(delete(FeedRecipeLine).where(FeedRecipeLine.recipe_id == recipe_id))
        await db.execute(delete(FeedRecipe).where(FeedRecipe.id == recipe_id))
        await db.execute(
            delete(BucketDefinition).where(
                BucketDefinition.code == missing_bucket,
            )
        )
        await db.execute(delete(VaccineTemplate).where(VaccineTemplate.name == missing_vaccine))
        await db.execute(
            update(BucketDefinition)
            .where(BucketDefinition.code == preserved_bucket)
            .values(name="Operator-preserved label")
        )
        await db.commit()

    async with get_sessionmaker()() as db:
        await seed_reference_data(db)

    async with get_sessionmaker()() as db:
        assert (
            await db.execute(
                select(BucketDefinition.id).where(
                    BucketDefinition.code == missing_bucket,
                )
            )
        ).scalar_one()
        recipe = (
            await db.execute(select(FeedRecipe).where(FeedRecipe.code == missing_recipe))
        ).scalar_one()
        line_count = len(
            (
                await db.execute(
                    select(FeedRecipeLine.id).where(FeedRecipeLine.recipe_id == recipe.id)
                )
            )
            .scalars()
            .all()
        )
        expected_line_count = len(
            next(lines for code, _, _, lines in FEED_RECIPES if code == missing_recipe)
        )
        assert line_count == expected_line_count
        assert (
            await db.execute(
                select(VaccineTemplate.id).where(VaccineTemplate.name == missing_vaccine)
            )
        ).scalar_one()
        preserved_name = (
            await db.execute(
                select(BucketDefinition.name).where(
                    BucketDefinition.code == preserved_bucket,
                )
            )
        ).scalar_one()
        assert preserved_name == "Operator-preserved label"


async def test_seed_startup_repairs_inventory_for_partial_and_empty_existing_farms() -> None:
    """A release-added ingredient must reach every farm without resetting stock."""
    async with get_sessionmaker()() as db:
        owners = [
            User(email="partial-stock-owner@farm.in", password_hash="argon2-placeholder"),
            User(email="empty-stock-owner@farm.in", password_hash="argon2-placeholder"),
        ]
        db.add_all(owners)
        await db.flush()
        farms = [
            Farm(name="Partial Inventory Farm", owner_id=owners[0].id),
            Farm(name="Empty Inventory Farm", owner_id=owners[1].id),
        ]
        db.add_all(farms)
        await db.flush()
        preserved_ingredient, preserved_category = FARM_INGREDIENTS[0]
        db.add(
            FeedInventory(
                farm_id=farms[0].id,
                ingredient=preserved_ingredient,
                category=preserved_category,
                unit="kg",
                qty_on_hand=12.345,
                reorder_level=7.5,
                last_purchase_price_per_kg=Decimal("23.45"),
            )
        )
        await db.commit()
        farm_ids = [farm.id for farm in farms]

    async with get_sessionmaker()() as db:
        await seed_startup(db)
        await repair_legacy_data_batch(db, farm_batch_size=10, task_batch_size=100)
        await db.commit()

    expected = dict(FARM_INGREDIENTS)
    async with get_sessionmaker()() as db:
        rows = list(
            (
                await db.execute(
                    select(FeedInventory)
                    .where(FeedInventory.farm_id.in_(farm_ids))
                    .order_by(FeedInventory.farm_id, FeedInventory.ingredient)
                )
            ).scalars()
        )
        for farm_id in farm_ids:
            inventory = {row.ingredient: row for row in rows if row.farm_id == farm_id}
            assert set(inventory) == set(expected)
            assert len(inventory) == len(FARM_INGREDIENTS)
            for ingredient, category in expected.items():
                assert inventory[ingredient].category == category

        preserved = next(
            row
            for row in rows
            if row.farm_id == farm_ids[0] and row.ingredient == preserved_ingredient
        )
        assert preserved.qty_on_hand == pytest.approx(12.345)
        assert preserved.reorder_level == pytest.approx(7.5)
        assert preserved.last_purchase_price_per_kg == Decimal("23.45")
        newly_repaired = next(
            row
            for row in rows
            if row.farm_id == farm_ids[0] and row.ingredient != preserved_ingredient
        )
        assert newly_repaired.qty_on_hand == 0.0
        assert newly_repaired.reorder_level == 100.0
        assert newly_repaired.last_purchase_price_per_kg is None

    # A second boot is a no-op: no duplicate ingredient balances are created.
    async with get_sessionmaker()() as db:
        await seed_startup(db)
        await repair_legacy_data_batch(db, farm_batch_size=10, task_batch_size=100)
        await db.commit()
    async with get_sessionmaker()() as db:
        rows_after = list(
            (
                await db.execute(select(FeedInventory).where(FeedInventory.farm_id.in_(farm_ids)))
            ).scalars()
        )
        assert len(rows_after) == len(farm_ids) * len(FARM_INGREDIENTS)


async def test_boot_seed_never_scans_or_locks_tenant_farms() -> None:
    async with get_sessionmaker()() as db:
        owner = User(email="bounded-boot-owner@farm.in", password_hash="argon2-placeholder")
        db.add(owner)
        await db.flush()
        farm = Farm(name="Bounded boot farm", owner_id=owner.id)
        db.add(farm)
        await db.flush()
        farm_id = farm.id
        await db.commit()

    statements: list[str] = []

    def capture_statement(
        _conn: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        statements.append(statement)

    engine = get_engine().sync_engine
    event.listen(engine, "before_cursor_execute", capture_statement)
    try:
        async with get_sessionmaker()() as db:
            await seed_startup(db)
    finally:
        event.remove(engine, "before_cursor_execute", capture_statement)

    assert not any(
        "FROM farms" in statement or "UPDATE farms" in statement for statement in statements
    )
    async with get_sessionmaker()() as db:
        assert (
            await db.execute(select(Role.id).where(Role.farm_id == farm_id))
        ).scalar_one_or_none() is None
        farms, tasks = await repair_legacy_data_batch(
            db,
            farm_batch_size=1,
            task_batch_size=1,
        )
        await db.commit()
    assert (farms, tasks) == (1, 0)


async def test_legacy_data_batch_releases_farm_locks_before_task_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Farm repair and task repair must not form a Farm -> Task lock edge.

    A recurring task transition holds Task before its successor INSERT takes a
    Farm FK key-share lock. If maintenance carried its Farm update lock into
    the Task backfill, the two transactions could deadlock in opposite order.
    Probe the exact phase boundary with a NOWAIT key-share acquisition.
    """
    async with get_sessionmaker()() as db:
        owner = User(email="phase-lock-owner@farm.in", password_hash="argon2-placeholder")
        db.add(owner)
        await db.flush()
        farm = Farm(name="Phase Lock Legacy Farm", owner_id=owner.id)
        db.add(farm)
        await db.commit()
        farm_id = farm.id

    async def probe_released_farm_lock(_db, *, batch_size: int) -> int:
        assert batch_size == 1
        async with get_sessionmaker()() as probe:
            locked_id = (
                await probe.execute(
                    select(Farm.id)
                    .where(Farm.id == farm_id)
                    .with_for_update(read=True, key_share=True, nowait=True)
                )
            ).scalar_one()
            assert locked_id == farm_id
            await probe.rollback()
        return 0

    monkeypatch.setattr(
        seed_module,
        "backfill_task_assignments_batch",
        probe_released_farm_lock,
    )
    async with get_sessionmaker()() as db:
        farms, tasks = await repair_legacy_data_batch(
            db,
            farm_batch_size=1,
            task_batch_size=1,
        )
        await db.commit()

    assert (farms, tasks) == (1, 0)


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
        # …and manually created unassigned duties, which must be left alone.
        manual = Task(
            farm_id=farm.id,
            title="Fix the fence",
            due_date=date(2026, 1, 12),
            category=TaskCategory.OTHER.value,
            auto_generated=False,
        )
        # OTHER is absent from TASK_CATEGORY_ROLE_MAP, so it is unclaimable on
        # category alone; only a MAPPED category can tell the `auto_generated`
        # and `assigned_user_id` guards apart from a missing predicate.
        manual_mapped = Task(
            farm_id=farm.id,
            title="Scrub the shed",
            due_date=date(2026, 1, 13),
            category=TaskCategory.CLEANING.value,
            auto_generated=False,
        )
        db.add_all([orphan_vaccine, orphan_move, manual, manual_mapped])
        await db.commit()
        farm_id = farm.id

    async with get_sessionmaker()() as db:
        await seed_startup(db)
        await repair_legacy_data_batch(db, farm_batch_size=10, task_batch_size=100)
        await db.commit()

    async with get_sessionmaker()() as db:
        roles = {
            role.code: role.id
            for role in (await db.execute(select(Role).where(Role.farm_id == farm_id))).scalars()
        }
        assert set(roles) == PRESET_ROLE_CODES
        tasks = {
            task.title: task
            for task in (await db.execute(select(Task).where(Task.farm_id == farm_id))).scalars()
        }
        assert tasks["PPR vaccine due"].assigned_role_id == roles["VET"]
        assert tasks["Move to FOUNDATION"].assigned_role_id == roles["MOVER"]
        assert tasks["Fix the fence"].assigned_role_id is None
        assert tasks["Scrub the shed"].assigned_role_id is None
        vet_role_id = roles["VET"]

    # Idempotency: a second startup run creates no roles and reassigns nothing.
    async with get_sessionmaker()() as db:
        await seed_startup(db)
        await repair_legacy_data_batch(db, farm_batch_size=10, task_batch_size=100)
        await db.commit()
    async with get_sessionmaker()() as db:
        roles_after = (
            (await db.execute(select(Role).where(Role.farm_id == farm_id))).scalars().all()
        )
        assert len(roles_after) == len(PRESET_ROLE_CODES)
        task_after = (
            await db.execute(
                select(Task).where(Task.farm_id == farm_id, Task.title == "PPR vaccine due")
            )
        ).scalar_one()
        assert task_after.assigned_role_id == vet_role_id


async def test_task_backfill_converges_and_never_restamps_an_assigned_duty() -> None:
    """A duty that already carries a role must never be re-claimed.

    Both claim branches require `assigned_role_id IS NULL`. Without it — or
    with the whole candidate predicate gone — every resolvable duty is
    re-claimed, FOR UPDATE-locked and rewritten on every hourly pass, so the
    worker never reports an empty batch. Worse, a personal duty whose role
    deliberately differs from its assignee's current membership role (roles
    are only cross-checked at create time, and memberships change afterwards)
    is silently overwritten with that membership role.
    """
    async with get_sessionmaker()() as db:
        owner = User(email="converge-owner@farm.in", password_hash="argon2-placeholder")
        worker = User(email="converge-worker@farm.in", password_hash="argon2-placeholder")
        db.add_all([owner, worker])
        await db.flush()
        farm = Farm(name="Converging Backfill Farm", owner_id=owner.id)
        db.add(farm)
        await db.flush()
        await seed_default_roles(db, farm.id)
        roles = {
            role.code: role.id
            for role in (await db.execute(select(Role).where(Role.farm_id == farm.id))).scalars()
        }
        db.add(FarmMembership(user_id=worker.id, farm_id=farm.id, role_id=roles["CLEANER"]))
        await db.flush()
        db.add_all(
            [
                Task(
                    farm_id=farm.id,
                    title="PPR vaccine due",
                    due_date=date(2026, 1, 10),
                    category=TaskCategory.VACCINE.value,
                    auto_generated=True,
                ),
                Task(
                    farm_id=farm.id,
                    title="Move to FOUNDATION",
                    due_date=date(2026, 1, 11),
                    category=TaskCategory.BUCKET_MOVE.value,
                    auto_generated=True,
                ),
            ]
        )
        # A legal PENDING personal duty whose role deliberately disagrees with
        # the assignee's membership role: repair must leave it exactly as-is.
        personal = Task(
            farm_id=farm.id,
            title="Escort the vet",
            due_date=date(2026, 1, 12),
            category=TaskCategory.OTHER.value,
            status=TaskStatus.PENDING.value,
            assigned_user_id=worker.id,
            assigned_role_id=roles["VET"],
        )
        db.add(personal)
        await db.commit()
        personal_id, vet_role_id = personal.id, roles["VET"]

    async with get_sessionmaker()() as db:
        first = await repair_legacy_data_batch(db, farm_batch_size=10, task_batch_size=100)
        await db.commit()
    # Only the two orphan auto-generated duties are candidates.
    assert first == (1, 2)

    async with get_sessionmaker()() as db:
        second = await repair_legacy_data_batch(db, farm_batch_size=10, task_batch_size=100)
        await db.commit()
    assert second == (0, 0)

    async with get_sessionmaker()() as db:
        untouched = await db.get(Task, personal_id)
        assert untouched is not None
        assert untouched.assigned_role_id == vet_role_id


# --- legacy-repair worker robustness -----------------------------------------


# Every farm these repair tests builds is a default (GOAT) farm, so the
# expected preset vocabulary is the goat one.
PRESET_ROLE_CODES = preset_codes()
CANONICAL_INGREDIENTS = {ingredient for ingredient, _category in FARM_INGREDIENTS}


async def _farm_role_codes(db: AsyncSession, farm_id: int) -> set[str | None]:
    """Every role code a farm holds (custom roles read back as None)."""
    return set((await db.execute(select(Role.code).where(Role.farm_id == farm_id))).scalars())


async def _farm_ingredients(db: AsyncSession, farm_id: int) -> set[str]:
    """Every feed-inventory ingredient name a farm holds."""
    return set(
        (
            await db.execute(
                select(FeedInventory.ingredient).where(FeedInventory.farm_id == farm_id)
            )
        ).scalars()
    )


async def test_preset_role_repair_survives_a_custom_role_holding_a_preset_name() -> None:
    """`uq_roles_farm_active_name` is a real partial unique index and nothing
    reserves preset display names, so a tenant may already own a custom role
    called "Veterinarian". Inserting the colliding name raised IntegrityError,
    rolled the whole 25-farm batch back, and — because the claim query is
    deterministic and main.py swallows the exception — wedged the worker
    forever. One poisoned tenant must not stop the others."""
    async with get_sessionmaker()() as db:
        owner = User(email="collide-owner@farm.in", password_hash="argon2-placeholder")
        db.add(owner)
        await db.flush()
        poisoned = Farm(name="Poisoned Farm", owner_id=owner.id)
        healthy = Farm(name="Healthy Legacy Farm", owner_id=owner.id)
        db.add_all([poisoned, healthy])
        await db.flush()
        preset = next(item for item in ROLE_PRESETS if item["code"] == "VET")
        db.add(Role(farm_id=poisoned.id, code=None, name=preset["name"], permissions="[]"))
        await db.commit()
        poisoned_id, healthy_id = poisoned.id, healthy.id

    async with get_sessionmaker()() as db:
        farms, _tasks = await repair_legacy_data_batch(db, farm_batch_size=10, task_batch_size=100)
        await db.commit()
    assert farms == 2

    async with get_sessionmaker()() as db:
        roles = list((await db.execute(select(Role).where(Role.farm_id == poisoned_id))).scalars())
        # Every preset code exists; the colliding preset took a decorated name.
        assert {role.code for role in roles if role.code} == PRESET_ROLE_CODES
        vet = next(role for role in roles if role.code == "VET")
        assert vet.name != preset["name"]
        assert preset["name"] in vet.name
        # The unpoisoned farm in the same batch was repaired too — roles AND
        # the canonical feed inventory that used to be rolled back with it.
        assert {
            role.code
            for role in (await db.execute(select(Role).where(Role.farm_id == healthy_id))).scalars()
        } == PRESET_ROLE_CODES
        inventory = (
            await db.execute(select(FeedInventory).where(FeedInventory.farm_id == healthy_id))
        ).scalars()
        assert len(list(inventory)) == len(FARM_INGREDIENTS)

    # Idempotent: a second pass has nothing left to claim.
    async with get_sessionmaker()() as db:
        farms, _tasks = await repair_legacy_data_batch(db, farm_batch_size=10, task_batch_size=100)
        await db.commit()
    assert farms == 0


async def test_claim_predicates_are_scoped_per_farm() -> None:
    """Both claim disjuncts must be correlated to the candidate farm itself.

    Every other legacy fixture is missing BOTH its preset roles and its
    inventory, so neither disjunct is ever the deciding one. An uncorrelated
    role EXISTS reads "does ANY farm have this code" and an uncorrelated
    inventory count sums the whole table, so as soon as one healthy tenant
    exists the half-seeded ones are never claimed again — and stay broken.
    """
    async with get_sessionmaker()() as db:
        owner = User(email="scoped-claim-owner@farm.in", password_hash="argon2-placeholder")
        db.add(owner)
        await db.flush()
        seeded = Farm(name="Fully Seeded Farm", owner_id=owner.id)
        roles_only = Farm(name="Roles Only Farm", owner_id=owner.id)
        inventory_only = Farm(name="Inventory Only Farm", owner_id=owner.id)
        db.add_all([seeded, roles_only, inventory_only])
        await db.flush()
        await seed_new_farm(db, seeded)
        await seed_default_roles(db, roles_only.id)
        await seed_farm_inventory(db, inventory_only.id)
        await db.commit()
        seeded_id, roles_only_id, inventory_only_id = seeded.id, roles_only.id, inventory_only.id

    async with get_sessionmaker()() as db:
        claimed = await repair_legacy_farms_batch(db, batch_size=10)
        await db.commit()
    # The healthy farm satisfies both disjuncts and must not be claimed.
    assert claimed == 2

    async with get_sessionmaker()() as db:
        assert await _farm_role_codes(db, inventory_only_id) == PRESET_ROLE_CODES
        assert await _farm_ingredients(db, roles_only_id) == CANONICAL_INGREDIENTS
        assert await _farm_role_codes(db, seeded_id) == PRESET_ROLE_CODES
        assert await _farm_ingredients(db, seeded_id) == CANONICAL_INGREDIENTS


async def test_partially_seeded_role_set_is_completed() -> None:
    """A farm holding SOME preset codes must be claimed and topped up.

    The role probe asks per code ("is MOVER missing?"), not per farm ("has any
    role"), and the snapshot read must return each row's real `code` — reading
    it back as NULL makes every preset look missing, so the re-insert collides
    with `uq_roles_farm_preset_code` and the savepoint silently drops the whole
    tenant's repair.
    """
    async with get_sessionmaker()() as db:
        owner = User(email="partial-roles-owner@farm.in", password_hash="argon2-placeholder")
        db.add(owner)
        await db.flush()
        farm = Farm(name="Partial Roles Farm", owner_id=owner.id)
        db.add(farm)
        await db.flush()
        for code in ("MOVER", "VET"):
            preset = next(item for item in ROLE_PRESETS if item["code"] == code)
            db.add(Role(farm_id=farm.id, code=code, name=preset["name"], permissions="[]"))
        await seed_farm_inventory(db, farm.id)
        await db.commit()
        farm_id = farm.id

    async with get_sessionmaker()() as db:
        claimed = await repair_legacy_farms_batch(db, batch_size=10)
        await db.commit()
    assert claimed == 1

    async with get_sessionmaker()() as db:
        assert await _farm_role_codes(db, farm_id) == PRESET_ROLE_CODES
        rows = list((await db.execute(select(Role).where(Role.farm_id == farm_id))).scalars())
        # Exactly one row per preset: the two pre-existing ones were not
        # duplicated and no preset was skipped by a rolled-back savepoint.
        assert len(rows) == len(PRESET_ROLE_CODES)


async def test_tombstoned_preset_name_is_reused() -> None:
    """`uq_roles_farm_active_name` is partial on `deleted_at IS NULL`, so a
    retired role's display name is free again. Reading the tombstone as live
    makes every legacy tenant that ever deleted a preset-named role receive a
    permanently decorated "Veterinarian (VET)" instead of the plain name."""
    async with get_sessionmaker()() as db:
        owner = User(email="tombstone-name-owner@farm.in", password_hash="argon2-placeholder")
        db.add(owner)
        await db.flush()
        farm = Farm(name="Tombstoned Name Farm", owner_id=owner.id)
        db.add(farm)
        await db.flush()
        preset = next(item for item in ROLE_PRESETS if item["code"] == "VET")
        db.add(
            Role(
                farm_id=farm.id,
                code=None,
                name=preset["name"],
                permissions="[]",
                deleted_at=utcnow() - timedelta(days=1),
            )
        )
        await db.commit()
        farm_id, preset_name = farm.id, preset["name"]

    async with get_sessionmaker()() as db:
        assert await repair_legacy_farms_batch(db, batch_size=10) == 1
        await db.commit()

    async with get_sessionmaker()() as db:
        vet = (
            await db.execute(select(Role).where(Role.farm_id == farm_id, Role.code == "VET"))
        ).scalar_one()
        assert vet.name == preset_name


async def test_only_canonical_ingredients_count_toward_the_claim() -> None:
    """The inventory gate counts CANONICAL ingredients, not stock rows.

    Farms seeded under an older FARM_INGREDIENTS list are exactly the
    population this repair exists for: they hold a full complement of retired
    ingredient rows and none of the current ones. Counting every row makes
    them look complete, so they never receive a single canonical balance.
    """
    async with get_sessionmaker()() as db:
        owner = User(email="custom-ingredient-owner@farm.in", password_hash="argon2-placeholder")
        db.add(owner)
        await db.flush()
        farm = Farm(name="Custom Ingredient Farm", owner_id=owner.id)
        db.add(farm)
        await db.flush()
        await seed_default_roles(db, farm.id)
        legacy_names = {f"Legacy ingredient {index}" for index in range(len(FARM_INGREDIENTS))}
        for ingredient in sorted(legacy_names):
            db.add(
                FeedInventory(
                    farm_id=farm.id,
                    ingredient=ingredient,
                    category=IngredientCategory.CONCENTRATE.value,
                    unit="kg",
                    qty_on_hand=0.0,
                    reorder_level=100.0,
                )
            )
        await db.commit()
        farm_id = farm.id

    async with get_sessionmaker()() as db:
        assert await repair_legacy_farms_batch(db, batch_size=10) == 1
        await db.commit()

    async with get_sessionmaker()() as db:
        # Retired rows are kept (they still carry stock) and every canonical
        # ingredient is now present alongside them.
        assert await _farm_ingredients(db, farm_id) == legacy_names | CANONICAL_INGREDIENTS


async def test_only_the_claimed_batch_is_touched() -> None:
    """`batch_size` bounds the whole repair unit, inventory included.

    No other fixture has more claimable farms than the batch allows, so an
    unbounded LIMIT and an unscoped inventory INSERT..SELECT both look
    identical. The unclaimed farm must keep zero roles AND zero inventory
    rows: writing it anyway takes FK locks on tenants this pass deliberately
    did not claim.
    """
    async with get_sessionmaker()() as db:
        owner = User(email="batch-bound-owner@farm.in", password_hash="argon2-placeholder")
        db.add(owner)
        await db.flush()
        first = Farm(name="First Legacy Farm", owner_id=owner.id)
        second = Farm(name="Second Legacy Farm", owner_id=owner.id)
        db.add_all([first, second])
        await db.commit()
        first_id, second_id = first.id, second.id

    async with get_sessionmaker()() as db:
        claimed = await repair_legacy_farms_batch(db, batch_size=1)
        await db.commit()
    assert claimed == 1

    async with get_sessionmaker()() as db:
        assert await _farm_role_codes(db, first_id) == PRESET_ROLE_CODES
        assert await _farm_ingredients(db, first_id) == CANONICAL_INGREDIENTS
        assert await _farm_role_codes(db, second_id) == set()
        assert await _farm_ingredients(db, second_id) == set()


async def test_locked_farm_is_skipped_not_waited_on() -> None:
    """The maintenance batch must never block on a contended tenant row.

    Farm creation and role edits already hold `farms FOR UPDATE`. Without SKIP
    LOCKED the hourly worker parks on the first such row it meets, so a single
    long-running tenant transaction stalls every other tenant's repair for as
    long as it lives.
    """
    async with get_sessionmaker()() as db:
        owner = User(email="skip-locked-owner@farm.in", password_hash="argon2-placeholder")
        db.add(owner)
        await db.flush()
        locked = Farm(name="Locked Legacy Farm", owner_id=owner.id)
        free = Farm(name="Free Legacy Farm", owner_id=owner.id)
        db.add_all([locked, free])
        await db.commit()
        locked_id, free_id = locked.id, free.id

    async with get_sessionmaker()() as holder:
        held = (
            await holder.execute(select(Farm.id).where(Farm.id == locked_id).with_for_update())
        ).scalar_one()
        assert held == locked_id
        async with get_sessionmaker()() as db:
            claimed = await asyncio.wait_for(
                repair_legacy_farms_batch(db, batch_size=10), timeout=5
            )
            await db.commit()
        await holder.rollback()

    assert claimed == 1
    async with get_sessionmaker()() as db:
        assert await _farm_role_codes(db, free_id) == PRESET_ROLE_CODES
        assert await _farm_role_codes(db, locked_id) == set()


async def test_claim_statement_is_ordered_and_limited() -> None:
    """Pin the claim query's shape, which state assertions cannot reach.

    On a freshly truncated table heap order equals id order, so a dropped
    ORDER BY still returns the lowest ids and no fixture can observe it. The
    emitted statement is the only witness that a bounded pass is deterministic
    about which tenants it repairs first — and that it skips locked rows.
    """
    async with get_sessionmaker()() as db:
        owner = User(email="claim-shape-owner@farm.in", password_hash="argon2-placeholder")
        db.add(owner)
        await db.flush()
        db.add(Farm(name="Shape Legacy Farm", owner_id=owner.id))
        await db.commit()

    statements: list[str] = []

    def capture(
        _conn: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        statements.append(statement)

    engine = get_engine().sync_engine
    event.listen(engine, "before_cursor_execute", capture)
    try:
        async with get_sessionmaker()() as db:
            assert await repair_legacy_farms_batch(db, batch_size=7) == 1
            await db.commit()
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    claim = [sql for sql in statements if "FROM farms" in sql and "FOR UPDATE" in sql]
    assert len(claim) == 1
    assert "ORDER BY farms.id" in claim[0]
    assert "LIMIT" in claim[0]
    assert claim[0].rstrip().endswith("FOR UPDATE SKIP LOCKED")


async def test_conflicting_role_insert_only_skips_that_tenant(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The per-farm savepoint is the only thing the operator ever sees.

    `_free_preset_role_name` avoids collisions it can see, so the
    `except IntegrityError` arm is only reachable when an external writer takes
    a preset display name after the batch read its snapshot. Simulate that: the
    poisoned tenant must roll back alone, every other tenant in the batch must
    still be repaired, and the skip must be reported with the farm id — an
    unformattable or id-less warning leaves a silently unrepaired tenant.
    """
    async with get_sessionmaker()() as db:
        owner = User(email="savepoint-owner@farm.in", password_hash="argon2-placeholder")
        db.add(owner)
        await db.flush()
        poisoned = Farm(name="Poisoned Savepoint Farm", owner_id=owner.id)
        healthy = Farm(name="Healthy Savepoint Farm", owner_id=owner.id)
        db.add_all([poisoned, healthy])
        await db.flush()
        preset = next(item for item in ROLE_PRESETS if item["code"] == "VET")
        db.add(Role(farm_id=poisoned.id, code=None, name=preset["name"], permissions="[]"))
        await db.commit()
        poisoned_id, healthy_id = poisoned.id, healthy.id

    def take_the_preset_name(preset_name: str, code: str, taken: set[str]) -> str:
        return preset_name

    monkeypatch.setattr(seed_module, "_free_preset_role_name", take_the_preset_name)

    with caplog.at_level(logging.WARNING, logger="goatfarm.seed"):
        async with get_sessionmaker()() as db:
            claimed = await repair_legacy_farms_batch(db, batch_size=10)
            await db.commit()

    assert claimed == 2
    async with get_sessionmaker()() as db:
        assert await _farm_role_codes(db, healthy_id) == PRESET_ROLE_CODES
        # Only the poisoned tenant's roles were rolled back…
        assert await _farm_role_codes(db, poisoned_id) == {None}
        # …and the inventory phase still ran for both.
        assert await _farm_ingredients(db, healthy_id) == CANONICAL_INGREDIENTS
        assert await _farm_ingredients(db, poisoned_id) == CANONICAL_INGREDIENTS

    warnings = [record for record in caplog.records if record.name == "goatfarm.seed"]
    assert [record.getMessage() for record in warnings] == [
        f"legacy preset-role repair skipped farm_id={poisoned_id} (conflicting role row)"
    ]


async def test_task_backfill_skips_unresolvable_rows_without_starving_later_work() -> None:
    """SKIP LOCKED does not make an unchanged row progress across commits.

    The old ordered query repeatedly claimed the same unresolvable lowest-ID
    duty, exhausting every maintenance pass while later repairable duties were
    never reached.  Eligibility must be part of the claim query itself.
    """
    async with get_sessionmaker()() as db:
        owner = User(email="claimed-count-owner@farm.in", password_hash="argon2-placeholder")
        db.add(owner)
        await db.flush()
        unresolved_farm = Farm(name="Unresolved Farm", owner_id=owner.id)
        repairable_farm = Farm(name="Repairable Farm", owner_id=owner.id)
        db.add_all([unresolved_farm, repairable_farm])
        await db.flush()
        unresolved = Task(
            farm_id=unresolved_farm.id,
            title="Unresolvable duty",
            due_date=date(2026, 1, 10),
            category=TaskCategory.CLEANING.value,
            auto_generated=True,
        )
        db.add(unresolved)
        await db.flush()  # it must be the deterministic lowest-ID candidate

        await seed_default_roles(db, repairable_farm.id)
        repairable = Task(
            farm_id=repairable_farm.id,
            title="Repairable duty",
            due_date=date(2026, 1, 11),
            category=TaskCategory.CLEANING.value,
            auto_generated=True,
        )
        db.add(repairable)
        await db.commit()
        unresolved_id, repairable_id = unresolved.id, repairable.id

    async with get_sessionmaker()() as db:
        claimed = await backfill_task_assignments_batch(db, batch_size=1)
        await db.commit()
    assert claimed == 1

    async with get_sessionmaker()() as db:
        unresolved_after = await db.get(Task, unresolved_id)
        repairable_after = await db.get(Task, repairable_id)
        assert unresolved_after is not None and unresolved_after.assigned_role_id is None
        assert repairable_after is not None and repairable_after.assigned_role_id is not None

        # Once its missing source is repaired, the skipped row becomes eligible
        # without a marker rewrite or an unbounded scan.
        await seed_default_roles(db, unresolved_farm.id)
        assert await backfill_task_assignments_batch(db, batch_size=1) == 1
        await db.commit()

    async with get_sessionmaker()() as db:
        eventually_repaired = await db.get(Task, unresolved_id)
        assert eventually_repaired is not None
        assert eventually_repaired.assigned_role_id is not None


async def test_task_backfill_share_locks_membership_role_snapshot() -> None:
    """Finding #31: startup repair uses the request path's parent-row lock."""
    async with get_sessionmaker()() as db:
        owner = User(email="share-owner@farm.in", password_hash="argon2-placeholder")
        worker = User(email="share-worker@farm.in", password_hash="argon2-placeholder")
        db.add_all([owner, worker])
        await db.flush()
        farm = Farm(name="Share Lock Farm", owner_id=owner.id)
        db.add(farm)
        await db.flush()
        await seed_default_roles(db, farm.id)
        role = (
            await db.execute(select(Role).where(Role.farm_id == farm.id, Role.code == "CLEANER"))
        ).scalar_one()
        db.add(FarmMembership(user_id=worker.id, farm_id=farm.id, role_id=role.id))
        await db.flush()
        db.add(
            Task(
                farm_id=farm.id,
                title="Legacy personal cleaning",
                due_date=date(2026, 1, 10),
                category=TaskCategory.CLEANING.value,
                status=TaskStatus.DONE.value,
                assigned_user_id=worker.id,
                assigned_role_id=None,
                auto_generated=True,
                completed_by_id=worker.id,
                completed_at=utcnow(),
            )
        )
        await db.commit()

    statements: list[str] = []

    def capture(
        _conn: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        statements.append(statement)

    engine = get_engine().sync_engine
    event.listen(engine, "before_cursor_execute", capture)
    try:
        async with get_sessionmaker()() as db:
            claimed = await backfill_task_assignments_batch(db, batch_size=1)
            await db.commit()
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert claimed == 1
    membership_reads = [
        sql for sql in statements if "FROM farm_memberships" in sql and "FOR SHARE" in sql
    ]
    assert len(membership_reads) == 1


async def test_task_backfill_claims_a_duty_only_for_its_own_live_category_preset() -> None:
    """The preset probe must pair each category with ITS OWN live role code.

    Every other fixture farm holds either the full goat preset set or none, so a probe
    that matched any category against any preset code — or that ignored
    `deleted_at` — still found a live role and looked correct. A CLEANING duty
    is repairable only by a live CLEANER on the same farm: claiming it on a
    VET-only or tombstoned-CLEANER farm burns the pass on a row the writer
    then cannot resolve, while refusing the live-CLEANER farm never repairs
    the one duty that could be.
    """
    async with get_sessionmaker()() as db:
        owner = User(email="category-preset-owner@farm.in", password_hash="argon2-placeholder")
        db.add(owner)
        await db.flush()
        live = Farm(name="Live Cleaner Farm", owner_id=owner.id)
        other_code = Farm(name="Vet Only Farm", owner_id=owner.id)
        tombstoned = Farm(name="Retired Cleaner Farm", owner_id=owner.id)
        db.add_all([live, other_code, tombstoned])
        await db.flush()
        cleaner_preset = next(item for item in ROLE_PRESETS if item["code"] == "CLEANER")
        vet_preset = next(item for item in ROLE_PRESETS if item["code"] == "VET")
        cleaner = Role(
            farm_id=live.id,
            code=cleaner_preset["code"],
            name=cleaner_preset["name"],
            permissions="[]",
        )
        db.add_all(
            [
                cleaner,
                Role(
                    farm_id=other_code.id,
                    code=vet_preset["code"],
                    name=vet_preset["name"],
                    permissions="[]",
                ),
                Role(
                    farm_id=tombstoned.id,
                    code=cleaner_preset["code"],
                    name=cleaner_preset["name"],
                    permissions="[]",
                    deleted_at=utcnow(),
                ),
            ]
        )
        duties = [
            Task(
                farm_id=farm.id,
                title="Clean the pen",
                due_date=date(2026, 1, 10),
                category=TaskCategory.CLEANING.value,
                auto_generated=True,
            )
            for farm in (live, other_code, tombstoned)
        ]
        db.add_all(duties)
        await db.commit()
        cleaner_id = cleaner.id
        live_duty_id, other_duty_id, tombstoned_duty_id = (duty.id for duty in duties)

    async with get_sessionmaker()() as db:
        claimed = await backfill_task_assignments_batch(db, batch_size=10)
        await db.commit()
    assert claimed == 1

    async with get_sessionmaker()() as db:
        repaired = await db.get(Task, live_duty_id)
        assert repaired is not None and repaired.assigned_role_id == cleaner_id
        for unresolved_id in (other_duty_id, tombstoned_duty_id):
            unresolved = await db.get(Task, unresolved_id)
            assert unresolved is not None and unresolved.assigned_role_id is None


async def test_task_backfill_claims_at_most_batch_size_in_id_order() -> None:
    """`batch_size` is the worker's per-pass budget, not a hint.

    Every other fixture offers exactly one resolvable row to a one-row batch,
    so an unbounded LIMIT is invisible. With three repairable duties the claim
    must take (and FOR UPDATE lock) only the lowest-id one and leave the rest
    for the next finite pass.
    """
    async with get_sessionmaker()() as db:
        owner = User(email="batch-budget-owner@farm.in", password_hash="argon2-placeholder")
        db.add(owner)
        await db.flush()
        farm = Farm(name="Batch Budget Farm", owner_id=owner.id)
        db.add(farm)
        await db.flush()
        await seed_default_roles(db, farm.id)
        duties = [
            Task(
                farm_id=farm.id,
                title=f"Clean the pen {index}",
                due_date=date(2026, 1, 10 + index),
                category=TaskCategory.CLEANING.value,
                auto_generated=True,
            )
            for index in range(3)
        ]
        db.add_all(duties)
        await db.commit()
        duty_ids = [duty.id for duty in duties]

    async with get_sessionmaker()() as db:
        claimed = await backfill_task_assignments_batch(db, batch_size=1)
        await db.commit()
    assert claimed == 1

    async with get_sessionmaker()() as db:
        rows = list(
            (
                await db.execute(select(Task).where(Task.id.in_(duty_ids)).order_by(Task.id))
            ).scalars()
        )
        assert [row.assigned_role_id is not None for row in rows] == [True, False, False]


async def test_task_backfill_claim_is_ordered_bounded_and_skips_locked() -> None:
    """Pin the claim query's shape, which state assertions cannot reach.

    On a freshly truncated table heap order equals id order, so a dropped
    ORDER BY still returns the lowest ids and no fixture can observe it. The
    emitted statement is the only witness that each pass is ordered and
    bounded; SKIP LOCKED is then pinned behaviourally — with a contended
    lowest-id duty the worker must repair the next one instead of parking on
    the lock (which a 250ms `lock_timeout` turns into a hard error).
    """
    async with get_sessionmaker()() as db:
        owner = User(email="claim-shape-duty-owner@farm.in", password_hash="argon2-placeholder")
        db.add(owner)
        await db.flush()
        farm = Farm(name="Claim Shape Farm", owner_id=owner.id)
        db.add(farm)
        await db.flush()
        await seed_default_roles(db, farm.id)
        contended = Task(
            farm_id=farm.id,
            title="Clean the contended pen",
            due_date=date(2026, 1, 10),
            category=TaskCategory.CLEANING.value,
            auto_generated=True,
        )
        db.add(contended)
        await db.flush()  # it must be the deterministic lowest-ID candidate
        free = Task(
            farm_id=farm.id,
            title="Clean the free pen",
            due_date=date(2026, 1, 11),
            category=TaskCategory.CLEANING.value,
            auto_generated=True,
        )
        db.add(free)
        await db.commit()
        contended_id, free_id = contended.id, free.id

    statements: list[str] = []

    def capture(
        _conn: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        statements.append(statement)

    engine = get_engine().sync_engine
    async with get_sessionmaker()() as holder:
        held = (
            await holder.execute(select(Task.id).where(Task.id == contended_id).with_for_update())
        ).scalar_one()
        assert held == contended_id
        event.listen(engine, "before_cursor_execute", capture)
        try:
            async with get_sessionmaker()() as db:
                await db.execute(text("SET LOCAL lock_timeout = '250ms'"))
                claimed = await backfill_task_assignments_batch(db, batch_size=1)
                await db.commit()
        finally:
            event.remove(engine, "before_cursor_execute", capture)
        await holder.rollback()

    assert claimed == 1
    claim = [sql for sql in statements if "FROM tasks" in sql and "FOR UPDATE" in sql]
    assert len(claim) == 1
    assert "ORDER BY tasks.id" in claim[0]
    assert "LIMIT" in claim[0]
    # OF tasks is load-bearing: the claim joins Farm for the type probe, and a
    # bare FOR UPDATE would lock those farm rows for the whole worker
    # transaction — the Task->Farm / Farm->Task deadlock cycle the phased
    # repair exists to prevent.
    assert claim[0].rstrip().endswith("FOR UPDATE OF tasks SKIP LOCKED")

    async with get_sessionmaker()() as db:
        skipped = await db.get(Task, contended_id)
        repaired = await db.get(Task, free_id)
        assert skipped is not None and skipped.assigned_role_id is None
        assert repaired is not None and repaired.assigned_role_id is not None


async def test_task_backfill_share_locks_only_the_claimed_membership_pairs() -> None:
    """The FOR SHARE snapshot read must be scoped to the claimed pairs.

    Dropping the (farm_id, user_id) tuple predicate resolves the same roles —
    the dict is looked up by pair — but takes a FOR SHARE lock on EVERY
    membership row in the database, so one hourly maintenance batch stalls
    every other tenant's membership and role writes.
    """
    async with get_sessionmaker()() as db:
        owner = User(email="pair-scope-owner@farm.in", password_hash="argon2-placeholder")
        worker = User(email="pair-scope-worker@farm.in", password_hash="argon2-placeholder")
        stranger = User(email="pair-scope-stranger@farm.in", password_hash="argon2-placeholder")
        db.add_all([owner, worker, stranger])
        await db.flush()
        farm = Farm(name="Pair Scope Farm", owner_id=owner.id)
        unrelated = Farm(name="Unrelated Tenant Farm", owner_id=owner.id)
        db.add_all([farm, unrelated])
        await db.flush()
        await seed_default_roles(db, farm.id)
        await seed_default_roles(db, unrelated.id)
        cleaner_id = (
            await db.execute(select(Role.id).where(Role.farm_id == farm.id, Role.code == "CLEANER"))
        ).scalar_one()
        unrelated_role_id = (
            await db.execute(
                select(Role.id).where(Role.farm_id == unrelated.id, Role.code == "CLEANER")
            )
        ).scalar_one()
        db.add(FarmMembership(user_id=worker.id, farm_id=farm.id, role_id=cleaner_id))
        unrelated_membership = FarmMembership(
            user_id=stranger.id, farm_id=unrelated.id, role_id=unrelated_role_id
        )
        db.add(unrelated_membership)
        await db.flush()
        db.add(
            Task(
                farm_id=farm.id,
                title="Legacy personal cleaning",
                due_date=date(2026, 1, 10),
                category=TaskCategory.CLEANING.value,
                status=TaskStatus.DONE.value,
                assigned_user_id=worker.id,
                assigned_role_id=None,
                auto_generated=True,
                completed_by_id=worker.id,
                completed_at=utcnow(),
            )
        )
        await db.commit()
        unrelated_membership_id = unrelated_membership.id

    statements: list[str] = []

    def capture(
        _conn: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        statements.append(statement)

    engine = get_engine().sync_engine
    async with get_sessionmaker()() as holder:
        held = (
            await holder.execute(
                select(FarmMembership.id)
                .where(FarmMembership.id == unrelated_membership_id)
                .with_for_update()
            )
        ).scalar_one()
        assert held == unrelated_membership_id
        event.listen(engine, "before_cursor_execute", capture)
        try:
            async with get_sessionmaker()() as db:
                await db.execute(text("SET LOCAL lock_timeout = '250ms'"))
                claimed = await backfill_task_assignments_batch(db, batch_size=1)
                await db.commit()
        finally:
            event.remove(engine, "before_cursor_execute", capture)
        await holder.rollback()

    assert claimed == 1
    membership_reads = [
        sql for sql in statements if "FROM farm_memberships" in sql and "FOR SHARE" in sql
    ]
    assert len(membership_reads) == 1
    assert "(farm_memberships.farm_id, farm_memberships.user_id) IN" in membership_reads[0]


async def test_task_backfill_reports_partial_resolution_and_never_stamps_a_dead_role(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A source that dies between the claim and the source read must not be used.

    The claim locks tasks, not roles, and the engine runs READ COMMITTED, so a
    role that satisfied the correlated eligibility probe can be tombstoned
    before the batch reads it back. The duty must simply stay unclaimed-shaped
    (role still NULL, eligible again later) rather than being stamped with a
    defunct role, and the shortfall must be reported exactly once with the
    real resolved/claimed counts.
    """
    async with get_sessionmaker()() as db:
        owner = User(email="mid-batch-owner@farm.in", password_hash="argon2-placeholder")
        db.add(owner)
        await db.flush()
        stable = Farm(name="Stable Preset Farm", owner_id=owner.id)
        racing = Farm(name="Racing Preset Farm", owner_id=owner.id)
        db.add_all([stable, racing])
        await db.flush()
        await seed_default_roles(db, stable.id)
        cleaner_preset = next(item for item in ROLE_PRESETS if item["code"] == "CLEANER")
        db.add(
            Role(
                farm_id=racing.id,
                code=cleaner_preset["code"],
                name=cleaner_preset["name"],
                permissions="[]",
            )
        )
        stable_duties = [
            Task(
                farm_id=stable.id,
                title=f"Clean the stable pen {index}",
                due_date=date(2026, 1, 10 + index),
                category=TaskCategory.CLEANING.value,
                auto_generated=True,
            )
            for index in range(2)
        ]
        racing_duty = Task(
            farm_id=racing.id,
            title="Clean the racing pen",
            due_date=date(2026, 1, 12),
            category=TaskCategory.CLEANING.value,
            auto_generated=True,
        )
        db.add_all([*stable_duties, racing_duty])
        await db.commit()
        racing_id = racing.id
        stable_cleaner_id = (
            await db.execute(
                select(Role.id).where(Role.farm_id == stable.id, Role.code == "CLEANER")
            )
        ).scalar_one()
        stable_duty_ids = [duty.id for duty in stable_duties]
        racing_duty_id = racing_duty.id

    async with get_sessionmaker()() as db:
        original_execute = db.execute
        executed = 0

        async def tombstone_after_the_claim(*args: Any, **kwargs: Any) -> Any:
            """Soft-delete the racing farm's only source right after the claim."""
            nonlocal executed
            result = await original_execute(*args, **kwargs)
            executed += 1
            if executed == 1:
                async with get_sessionmaker()() as racer:
                    await racer.execute(
                        update(Role)
                        .where(Role.farm_id == racing_id, Role.code == "CLEANER")
                        .values(deleted_at=utcnow())
                    )
                    await racer.commit()
            return result

        monkeypatch.setattr(db, "execute", tombstone_after_the_claim)
        with caplog.at_level(logging.INFO, logger="goatfarm.seed"):
            claimed = await backfill_task_assignments_batch(db, batch_size=10)
        await db.commit()

    assert claimed == 3
    async with get_sessionmaker()() as db:
        for duty_id in stable_duty_ids:
            repaired = await db.get(Task, duty_id)
            assert repaired is not None and repaired.assigned_role_id == stable_cleaner_id
        unresolved = await db.get(Task, racing_duty_id)
        assert unresolved is not None and unresolved.assigned_role_id is None

    records = [record for record in caplog.records if record.name == "goatfarm.seed"]
    assert [record.getMessage() for record in records] == [
        "task role backfill resolved 2 of 3 claimed duties"
    ]


def test_superseded_unbatched_backfill_stays_deleted() -> None:
    """The dead non-batch backfill looked category roles up through an
    UNFILTERED `{role.code: role.id}` dict: every custom role shares code=None,
    so an unmapped category (e.g. OTHER) resolved via `roles.get(None)` to an
    arbitrary custom role instead of staying unassigned. The batch variant is
    the only wired-in path and guards that lookup, so the unbatched twin was
    deleted outright — a future caller must revive the guarded variant, never
    this one."""
    assert not hasattr(seed_module, "backfill_task_assignments")


async def test_task_backfill_never_resolves_a_soft_deleted_membership_role() -> None:
    """Account deletion retains membership rows while `_member_count` only
    counts live users, so a custom role whose sole holder was tombstoned can
    itself be soft-deleted with a retained membership still pointing at it.
    The membership fallback used to stamp that dead role onto a duty (and kept
    re-claiming rows it could never resolve); it must fall through to the live
    category preset — or leave the row unclaimed when no preset source exists —
    exactly like the request path, which refuses roles with `deleted_at` set."""
    async with get_sessionmaker()() as db:
        owner = User(email="dead-role-owner@farm.in", password_hash="argon2-placeholder")
        worker = User(email="dead-role-worker@farm.in", password_hash="argon2-placeholder")
        db.add_all([owner, worker])
        await db.flush()
        preset_farm = Farm(name="Dead Role Preset Farm", owner_id=owner.id)
        bare_farm = Farm(name="Dead Role Bare Farm", owner_id=owner.id)
        db.add_all([preset_farm, bare_farm])
        await db.flush()
        await seed_default_roles(db, preset_farm.id)
        tombstoned = [
            Role(farm_id=preset_farm.id, code=None, name="Night watch", permissions="[]"),
            Role(farm_id=bare_farm.id, code=None, name="Night watch", permissions="[]"),
        ]
        db.add_all(tombstoned)
        await db.flush()
        db.add_all(
            [
                FarmMembership(user_id=worker.id, farm_id=preset_farm.id, role_id=tombstoned[0].id),
                FarmMembership(user_id=worker.id, farm_id=bare_farm.id, role_id=tombstoned[1].id),
            ]
        )
        for farm in (preset_farm, bare_farm):
            db.add(
                Task(
                    farm_id=farm.id,
                    title="Legacy personal cleaning",
                    due_date=date(2026, 1, 10),
                    category=TaskCategory.CLEANING.value,
                    status=TaskStatus.DONE.value,
                    assigned_user_id=worker.id,
                    assigned_role_id=None,
                    auto_generated=True,
                    completed_by_id=worker.id,
                    completed_at=utcnow(),
                )
            )
        # Tombstone the worker first (memberships are retained), then the now
        # holder-less custom roles — the exact shape delete_role permits.
        # The scrub mirrors delete_account so ck_users_deleted_profile_scrubbed
        # holds.
        worker.deleted_at = utcnow()
        worker.email = f"deleted-{worker.id}@deleted.invalid"
        worker.name = None
        for role in tombstoned:
            role.deleted_at = utcnow()
        await db.commit()
        preset_farm_id, bare_farm_id = preset_farm.id, bare_farm.id
        dead_role_ids = {role.id for role in tombstoned}

    async with get_sessionmaker()() as db:
        claimed = await backfill_task_assignments_batch(db, batch_size=10)
        await db.commit()
    # Only the preset-farm duty has a live source; the bare-farm row must not
    # be claimed at all (a dead-role membership is not a resolvable source).
    assert claimed == 1

    async with get_sessionmaker()() as db:
        cleaner_id = (
            await db.execute(
                select(Role.id).where(
                    Role.farm_id == preset_farm_id,
                    Role.code == "CLEANER",
                    Role.deleted_at.is_(None),
                )
            )
        ).scalar_one()
        repaired = (
            await db.execute(select(Task).where(Task.farm_id == preset_farm_id))
        ).scalar_one()
        assert repaired.assigned_role_id == cleaner_id
        assert repaired.assigned_role_id not in dead_role_ids
        unclaimed = (
            await db.execute(select(Task).where(Task.farm_id == bare_farm_id))
        ).scalar_one()
        assert unclaimed.assigned_role_id is None


async def test_non_pending_personal_duty_is_repaired_so_rejection_stays_possible() -> None:
    """ck_tasks_user_assignment_has_role also fires on an UPDATE that moves a
    row INTO PENDING, which is exactly what verification rejection does. The
    earlier PENDING-only backfill left DONE/VERIFIED/SKIPPED personal duties as
    landmines: rejecting one raised CheckViolation and 500'd forever."""
    async with get_sessionmaker()() as db:
        owner = User(email="reject-legacy-owner@farm.in", password_hash="argon2-placeholder")
        worker = User(email="reject-legacy-worker@farm.in", password_hash="argon2-placeholder")
        db.add_all([owner, worker])
        await db.flush()
        farm = Farm(name="Reject Legacy Farm", owner_id=owner.id)
        db.add(farm)
        await db.flush()
        await seed_default_roles(db, farm.id)
        role = (
            await db.execute(select(Role).where(Role.farm_id == farm.id, Role.code == "CLEANER"))
        ).scalar_one()
        db.add(FarmMembership(user_id=worker.id, farm_id=farm.id, role_id=role.id))
        await db.flush()
        task = Task(
            farm_id=farm.id,
            title="Clean the pen",
            due_date=date(2026, 1, 10),
            category=TaskCategory.CLEANING.value,
            status=TaskStatus.DONE.value,
            assigned_user_id=worker.id,
            assigned_role_id=None,  # the legacy shape this revision repairs
            completed_by_id=worker.id,
            completed_at=utcnow(),
        )
        db.add(task)
        await db.commit()
        task_id, role_id = task.id, role.id

    # Without the repair the duty cannot be sent back to the worker at all.
    async with get_sessionmaker()() as db:
        with pytest.raises(IntegrityError):
            await db.execute(
                update(Task).where(Task.id == task_id).values(status=TaskStatus.PENDING.value)
            )
            await db.flush()
        await db.rollback()

    revision = _load_alembic_revision("a1b2c3d4e5f7_repair_non_pending_personal_task_roles")
    async with get_sessionmaker()() as db:
        await db.execute(text(revision.REPAIR_PERSONAL_TASK_ROLES))
        await db.commit()

    async with get_sessionmaker()() as db:
        repaired = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one()
        assert repaired.assigned_role_id == role_id
        await db.execute(
            update(Task).where(Task.id == task_id).values(status=TaskStatus.PENDING.value)
        )
        await db.commit()


# --- unhandled errors stay correlatable --------------------------------------


async def test_unhandled_errors_keep_the_request_id_and_security_headers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Starlette routes the `Exception` handler to ServerErrorMiddleware, which
    wraps every user middleware from the outside — so a 500 used to carry no
    X-Request-ID, no security headers and no Cache-Control, and its traceback
    was logged with request id `-`."""
    app = create_app()

    @app.get("/api/_boom")
    async def boom() -> None:  # pragma: no cover - raises by design
        raise RuntimeError("kaboom")

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    # The id reaches log records through the app's own filter; attach it to the
    # capture handler so the assertion does not depend on handler ordering.
    caplog.handler.addFilter(main_module._RequestIdFilter())
    with caplog.at_level("ERROR", logger="goatfarm"):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as failing:
            response = await failing.get(
                "/api/_boom",
                headers={
                    "X-Request-ID": "trace-500",
                    "Origin": "http://localhost:3000",
                },
            )

    # The traceback is logged while the id is still bound, not as `[-]`.
    traceback_record = next(
        record for record in caplog.records if record.message.startswith("unhandled error on")
    )
    assert getattr(traceback_record, "request_id", "-") == "trace-500"

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    assert response.headers["X-Request-ID"] == "trace-500"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Access-Control-Allow-Origin"] == "http://localhost:3000"
    assert response.headers["Access-Control-Allow-Credentials"] == "true"
    assert "X-Request-ID" in response.headers["Access-Control-Expose-Headers"]
    assert "Origin" in response.headers["Vary"]


async def test_unhandled_error_does_not_reflect_untrusted_cors_origin() -> None:
    app = create_app()

    @app.get("/api/_cors-boom")
    async def boom() -> None:  # pragma: no cover - raises by design
        raise RuntimeError("kaboom")

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as failing:
        response = await failing.get(
            "/api/_cors-boom",
            headers={"Origin": "https://untrusted.example"},
        )

    assert response.status_code == 500
    assert "Access-Control-Allow-Origin" not in response.headers


async def test_unhandled_error_log_names_the_route_and_carries_the_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An escaped 500 must stay diagnosable: the log line names the failing
    method and path, and carries the exception itself, not just a bare label."""
    app = create_app()

    @app.post("/api/_boom-log")
    async def boom() -> None:  # pragma: no cover - raises by design
        raise RuntimeError("kaboom")

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    with caplog.at_level("ERROR", logger="goatfarm"):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as failing:
            response = await failing.post("/api/_boom-log")

    assert response.status_code == 500
    # Select on the template, so a degraded interpolation is caught rather than
    # filtered out by the very text under test.
    record = next(r for r in caplog.records if r.msg == "unhandled error on %s %s")
    assert record.getMessage() == "unhandled error on POST /api/_boom-log"
    assert record.exc_info is not None
    assert record.exc_info[0] is RuntimeError
    assert "kaboom" in caplog.text


async def test_unhandled_error_log_sanitizes_line_separators_in_path(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """RT-M-4 must cover the 500 sink too. ``\\n``/``\\r`` cannot route (no
    route regex crosses a newline), but U+2028/U+2029 percent-decode into
    ``scope["path"]`` AND still match ``{rest:path}`` routes — a literal line
    separator in the unhandled-error line forges records in log viewers."""
    app = create_app()

    @app.post("/api/_boom-forge/{rest:path}")
    async def boom(rest: str) -> None:  # pragma: no cover - raises by design
        raise RuntimeError("kaboom")

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    with caplog.at_level("ERROR", logger="goatfarm"):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as failing:
            response = await failing.post(
                "/api/_boom-forge/%E2%80%A82026-09-16 INFO "
                "goatfarm.audit security_event event='forged'"
            )

    assert response.status_code == 500
    record = next(r for r in caplog.records if r.msg == "unhandled error on %s %s")
    assert record.getMessage() == (
        "unhandled error on POST /api/_boom-forge/\\u20282026-09-16 INFO "
        "goatfarm.audit security_event event='forged'"
    )
    # The separator never reached the log stream verbatim, and no additional
    # records were forged by the payload.
    assert "\u2028" not in caplog.text


async def test_unhandled_error_cors_header_values_are_exact() -> None:
    """Both header values the 500 path mints are parsed as structured fields by
    browsers and caches, so nothing but exact equality pins them."""
    app = create_app()

    @app.get("/api/_boom-cors-exact")
    async def boom() -> None:  # pragma: no cover - raises by design
        raise RuntimeError("kaboom")

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as failing:
        response = await failing.get(
            "/api/_boom-cors-exact",
            headers={"Origin": "http://localhost:3000"},
        )

    assert response.status_code == 500
    assert response.headers["Access-Control-Expose-Headers"] == ", ".join(CORS_EXPOSE_HEADERS)
    assert response.headers["Vary"] == "Origin"


async def test_unhandled_error_mirrors_wildcard_cors_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wildcard arm of the 500 CORS mirror: with `["*"]` configured (legal
    outside production) an SPA must still be able to read the opaque 500."""
    monkeypatch.setenv("GOATFARM_CORS_ORIGINS", '["*"]')
    get_settings.cache_clear()
    try:
        app = create_app()

        @app.get("/api/_boom-wildcard")
        async def boom() -> None:  # pragma: no cover - raises by design
            raise RuntimeError("kaboom")

        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as failing:
            response = await failing.get(
                "/api/_boom-wildcard",
                headers={"Origin": "https://spa.example.test"},
            )
    finally:
        get_settings.cache_clear()

    assert response.status_code == 500
    assert response.headers["Access-Control-Allow-Origin"] == "https://spa.example.test"
    assert response.headers["Access-Control-Allow-Credentials"] == "true"
    assert response.headers["Access-Control-Expose-Headers"] == ", ".join(CORS_EXPOSE_HEADERS)
