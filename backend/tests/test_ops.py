"""Ops readiness tests: unauthenticated
health/readiness probes, request-ID correlation, production-boot safety
validation, docs gating, the GOATFARM_TEST_DB footgun guard, and direct
coverage of seed_startup / backfill_task_assignments_batch (the lifespan path
the httpx ASGI transport never triggers)."""

import importlib.util
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import ValidationError
from sqlalchemy import delete, event, select, text, update
from sqlalchemy.exc import IntegrityError

import app.main as main_module
import app.seed as seed_module
from app.core.config import (
    DEVELOPMENT_IDEMPOTENCY_HMAC_SECRET,
    PRODUCTION_REFRESH_COOKIE_NAME,
    MigrationSettings,
    Settings,
    get_settings,
)
from app.db import get_engine, get_sessionmaker
from app.main import create_app, lifespan
from app.models import (
    BucketDefinition,
    Farm,
    FarmMembership,
    FeedInventory,
    FeedRecipe,
    FeedRecipeLine,
    Role,
    Task,
    TaskCategory,
    TaskStatus,
    User,
    VaccineTemplate,
)
from app.permissions import ROLE_PRESETS
from app.security import validate_jwt_keypair
from app.seed import (
    BUCKET_DEFINITIONS,
    FARM_INGREDIENTS,
    FEED_RECIPES,
    VACCINE_TEMPLATES,
    backfill_task_assignments_batch,
    repair_legacy_data_batch,
    seed_default_roles,
    seed_reference_data,
    seed_startup,
)
from app.utils import utcnow

VALID_IDEMPOTENCY_HMAC_SECRET = "production-idempotency-hmac-secret-0000000001"
VALID_PREVIOUS_IDEMPOTENCY_HMAC_SECRET = "previous-production-idempotency-hmac-secret-0001"

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
    )
    assert settings.environment == "production"
    assert settings.refresh_cookie_name == PRODUCTION_REFRESH_COOKIE_NAME


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
        await db.execute(delete(BucketDefinition).where(BucketDefinition.code == missing_bucket))
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
                select(BucketDefinition.id).where(BucketDefinition.code == missing_bucket)
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
                select(BucketDefinition.name).where(BucketDefinition.code == preserved_bucket)
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
        await seed_startup(db)
        await repair_legacy_data_batch(db, farm_batch_size=10, task_batch_size=100)
        await db.commit()

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
        await repair_legacy_data_batch(db, farm_batch_size=10, task_batch_size=100)
        await db.commit()
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


# --- legacy-repair worker robustness -----------------------------------------


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
        assert {role.code for role in roles if role.code} == {item["code"] for item in ROLE_PRESETS}
        vet = next(role for role in roles if role.code == "VET")
        assert vet.name != preset["name"]
        assert preset["name"] in vet.name
        # The unpoisoned farm in the same batch was repaired too — roles AND
        # the canonical feed inventory that used to be rolled back with it.
        assert {
            role.code
            for role in (await db.execute(select(Role).where(Role.farm_id == healthy_id))).scalars()
        } == {item["code"] for item in ROLE_PRESETS}
        inventory = (
            await db.execute(select(FeedInventory).where(FeedInventory.farm_id == healthy_id))
        ).scalars()
        assert len(list(inventory)) == len(FARM_INGREDIENTS)

    # Idempotent: a second pass has nothing left to claim.
    async with get_sessionmaker()() as db:
        farms, _tasks = await repair_legacy_data_batch(db, farm_batch_size=10, task_batch_size=100)
        await db.commit()
    assert farms == 0


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
