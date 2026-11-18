"""Supported runtime/configuration boundaries behind original function survivors."""

import asyncio
import base64
import logging
import stat
from collections.abc import Awaitable, Callable
from datetime import UTC, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import asyncpg
import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from pydantic import ValidationError
from pydantic_settings import BaseSettings
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import db as database
from app import main, metrics, security
from app.core import config
from app.core.config import MigrationSettings, ScreeningWorkerSettings, Settings
from app.models import Farm, MaintenanceProgress, Role, ScreeningImage, User
from app.services.maintenance_progress import MaintenanceJob, MaintenancePage, run_maintenance_page
from app.services.notifications.providers import ConsoleNotificationProvider
from app.services.screening.raw_cleanup import RawCleanupSummary, run_raw_cleanup_batch
from app.services.screening.s3 import ScreeningStorage
from app.utils import utcnow

from .conftest import TEST_URL, owner_with_farm
from .settings_helpers import settings_from_input
from .test_maintenance_progress import _farms
from .test_screening_raw_cleanup import ReplayableCleanupStorage, _insert_due_image
from .test_worker_and_settings_uncovered_contracts import (
    _production_values,
    _settings_outcome,
    isolated_settings_environment,  # noqa: F401 - imported fixture
)


def _diagnostic_messages(error: ValidationError) -> str:
    # str(ValidationError) also echoes the complete input. That echo cannot
    # prove that the validator actually diagnosed each offending entry.
    return " ".join(str(detail["msg"]) for detail in error.errors(include_input=False))


@pytest.mark.usefixtures("isolated_settings_environment")
@pytest.mark.parametrize("projection", [Settings, MigrationSettings, ScreeningWorkerSettings])
def test_supported_non_tls_driver_options_are_preserved(projection: type[BaseSettings]) -> None:
    url = TEST_URL + "?prepared_statement_cache_size=0"
    outcome = _settings_outcome(projection, environment="development", database_url=url)
    assert isinstance(outcome, projection), (
        f"A supported SQLAlchemy driver option was refused: {outcome!r}"
    )
    assert outcome.model_dump()["database_url"] == url


@pytest.mark.usefixtures("isolated_settings_environment")
def test_totp_key_has_one_canonical_unpadded_urlsafe_representation() -> None:
    material = b"\xfb" * 32
    canonical = base64.urlsafe_b64encode(material).rstrip(b"=").decode()
    control = _settings_outcome(Settings, totp_encryption_key=canonical)
    assert isinstance(control, Settings), control
    for invalid in (base64.b64encode(material).decode(), canonical + "="):
        outcome = _settings_outcome(Settings, totp_encryption_key=invalid)
        assert isinstance(outcome, ValidationError), (
            "A second spelling must not bypass key rotation duplicate checks"
        )
        assert "canonical unpadded base64url" in str(outcome)


def _screening_values() -> dict[str, object]:
    return {
        "environment": "development",
        "screening_enabled": True,
        "s3_bucket": "goat-photos",
        "s3_access_key_id": "access-key",
        "s3_secret_access_key": "secret-key",
        "screening_anthropic_api_key": "provider-key",
    }


@pytest.mark.usefixtures("isolated_settings_environment")
@pytest.mark.parametrize("projection", [Settings, ScreeningWorkerSettings])
def test_openai_screening_accepts_its_own_key_and_refuses_an_absent_key(
    projection: type[BaseSettings],
) -> None:
    values = _screening_values() | {"screening_provider": "openai_compatible"}
    valid = _settings_outcome(projection, **(values | {"screening_openai_api_key": "provider-key"}))
    assert isinstance(valid, projection), valid
    missing = _settings_outcome(projection, **values)
    assert isinstance(missing, ValidationError) and "GOATFARM_SCREENING_OPENAI_API_KEY" in str(
        missing
    )


@pytest.mark.usefixtures("isolated_settings_environment")
@pytest.mark.parametrize("projection", [Settings, ScreeningWorkerSettings])
@pytest.mark.parametrize("providers", [1, 2])
def test_screening_stale_claim_horizon_covers_the_documented_full_cascade(
    projection: type[BaseSettings], providers: int
) -> None:
    # backend/.env.example documents detection+gate chains and six specialist
    # timeouts: (2 x providers + 6) x provider timeout. Empty rotation means
    # the supported single-provider fallback, never zero providers.
    values = _screening_values() | {"screening_provider_timeout_seconds": 100}
    if providers > 1:
        values["screening_provider_rotation"] = [
            {
                "kind": "anthropic",
                "name": f"provider-{i}",
                "model": "screening-model",
                "api_key": "provider-key",
            }
            for i in range(providers)
        ]
    horizon = (2 * providers + 6) * 100
    valid = _settings_outcome(
        projection, **(values | {"screening_stale_processing_after_seconds": horizon})
    )
    assert isinstance(valid, projection), valid
    unsafe = _settings_outcome(
        projection, **(values | {"screening_stale_processing_after_seconds": horizon - 1})
    )
    assert isinstance(unsafe, ValidationError) and "worst-case cascade" in str(unsafe)


@pytest.mark.usefixtures("isolated_settings_environment")
@pytest.mark.parametrize("projection", [Settings, ScreeningWorkerSettings])
@pytest.mark.parametrize("prefix", ["/", "raw/../outside"])
def test_screening_prefix_cannot_normalize_to_empty_or_escape_its_namespace(
    projection: type[BaseSettings], prefix: str
) -> None:
    outcome = _settings_outcome(projection, screening_s3_prefix=prefix)
    assert isinstance(outcome, ValidationError) and "GOATFARM_SCREENING_S3_PREFIX" in str(outcome)


@pytest.mark.usefixtures("isolated_settings_environment")
def test_allowed_hosts_keeps_later_entries_after_a_legal_development_wildcard() -> None:
    settings = settings_from_input(
        Settings, env_file=None, allowed_hosts=["*", " API.Example.test. "]
    )
    assert settings.allowed_hosts == ["*", "api.example.test"]


@pytest.mark.usefixtures("isolated_settings_environment")
@pytest.mark.parametrize(
    "invalid",
    [
        ["bad*.example.test", "https://api.example.test"],
        ["https://api.example.test", "bad*.example.test"],
    ],
)
def test_allowed_host_diagnostics_name_every_invalid_operator_entry(invalid: list[str]) -> None:
    outcome = _settings_outcome(Settings, allowed_hosts=invalid)
    assert isinstance(outcome, ValidationError), outcome
    assert all(value in _diagnostic_messages(outcome) for value in invalid)


@pytest.mark.usefixtures("isolated_settings_environment")
def test_a_maximum_length_dns_name_is_a_supported_trusted_host() -> None:
    hostname = ".".join(["a" * 63, "b" * 63, "c" * 63, "d" * 61])
    outcome = _settings_outcome(Settings, allowed_hosts=[hostname])
    assert isinstance(outcome, Settings), "A legal 253-character DNS authority must remain usable"
    assert outcome.allowed_hosts == [hostname]


@pytest.mark.usefixtures("isolated_settings_environment")
@pytest.mark.parametrize(
    "first", ["https://api.example.test/path", "https://operator:password@api.example.test"]
)
def test_development_cors_keeps_later_canonical_entries_after_a_malformed_origin(
    first: str,
) -> None:
    settings = settings_from_input(
        Settings, env_file=None, cors_origins=[first, " HTTPS://APP.EXAMPLE.TEST:443 "]
    )
    assert settings.cors_origins == [first, "https://app.example.test"]


@pytest.mark.usefixtures("isolated_settings_environment")
def test_unicode_cors_and_proxy_diagnostics_do_not_hide_later_unsafe_entries() -> None:
    origins = ["https://münich.example", "https://faß.example"]
    cors = _settings_outcome(Settings, cors_origins=origins)
    assert isinstance(cors, ValidationError)
    assert all(origin in _diagnostic_messages(cors) for origin in origins)
    proxy = _settings_outcome(Settings, trusted_proxy_hosts="proxy.internal,0.0.0.0/0")
    assert isinstance(proxy, ValidationError)
    assert "proxy.internal" in _diagnostic_messages(proxy)
    assert "0.0.0.0/0" in _diagnostic_messages(proxy)


@pytest.mark.usefixtures("isolated_settings_environment")
@pytest.mark.parametrize("projection", [Settings, ScreeningWorkerSettings])
def test_private_metrics_credentials_accept_exactly_the_documented_minimum(
    projection: type[BaseSettings],
) -> None:
    values = _production_values() if projection is Settings else {"environment": "development"}
    accepted = _settings_outcome(projection, **(values | {"metrics_bearer_token": "x" * 32}))
    assert isinstance(accepted, projection), accepted
    refused = _settings_outcome(projection, **(values | {"metrics_bearer_token": "x" * 31}))
    assert isinstance(refused, ValidationError) and "GOATFARM_METRICS_BEARER_TOKEN" in str(refused)


@pytest.mark.usefixtures("isolated_settings_environment")
def test_api_projection_reads_a_file_delivered_migration_url(tmp_path: Path) -> None:
    secret = tmp_path / "migration-url"
    secret.write_text(TEST_URL + "\n")
    outcome = _settings_outcome(Settings, migration_database_url_file=secret)
    assert isinstance(outcome, Settings), outcome
    assert outcome.migration_database_url == TEST_URL


@pytest.mark.usefixtures("isolated_settings_environment")
def test_production_notifications_accept_msg91_with_a_real_configured_credential() -> None:
    values = _production_values() | {
        "notifications_enabled": True,
        "notifications_provider": "msg91",
        "msg91_auth_key": "operator-provider-credential",
    }
    accepted = _settings_outcome(Settings, **values)
    assert isinstance(accepted, Settings), accepted
    refused = _settings_outcome(Settings, **(values | {"notifications_provider": "console"}))
    assert isinstance(refused, ValidationError) and "requires" in str(refused)


@pytest.mark.usefixtures("isolated_settings_environment")
@pytest.mark.parametrize("projection", [Settings, ScreeningWorkerSettings])
def test_worker_heartbeat_margin_cannot_expire_before_a_healthy_poll_finishes(
    projection: type[BaseSettings],
) -> None:
    values = {"screening_poll_interval_seconds": 300}
    accepted = _settings_outcome(
        projection, **(values | {"screening_worker_health_max_age_seconds": 330})
    )
    assert isinstance(accepted, projection), accepted
    refused = _settings_outcome(
        projection, **(values | {"screening_worker_health_max_age_seconds": 329})
    )
    assert isinstance(refused, ValidationError) and "at least 30 seconds" in str(refused)


@pytest.mark.usefixtures("isolated_settings_environment")
def test_production_current_and_rotation_hmac_keys_accept_the_documented_minimum() -> None:
    values = _production_values() | {
        "idempotency_request_hmac_secret": "c" * 32,
        "idempotency_request_hmac_previous_secrets": ["p" * 32],
    }
    accepted = _settings_outcome(Settings, **values)
    assert isinstance(accepted, Settings), accepted
    for altered in (
        {"idempotency_request_hmac_secret": "c" * 31},
        {"idempotency_request_hmac_previous_secrets": ["p" * 31]},
    ):
        refused = _settings_outcome(Settings, **(values | altered))
        assert isinstance(refused, ValidationError) and "at least 32" in str(refused)


@pytest.mark.usefixtures("isolated_settings_environment")
def test_the_documented_production_argon_minimum_remains_a_usable_password_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcome = _settings_outcome(
        Settings,
        **(_production_values() | {"argon2_time_cost": 2, "argon2_memory_cost": 19 * 1024}),
    )
    assert isinstance(outcome, Settings), outcome
    monkeypatch.setattr(security, "get_settings", lambda: outcome)
    encoded = security.hash_password("a-real-minimum-policy-password")
    assert security.verify_password("a-real-minimum-policy-password", encoded)[0] is True


@pytest.mark.usefixtures("isolated_settings_environment")
def test_wildcard_localhost_cannot_bypass_the_production_host_refusal() -> None:
    outcome = _settings_outcome(
        Settings, **(_production_values() | {"allowed_hosts": ["*.localhost"]})
    )
    assert isinstance(outcome, ValidationError) and "GOATFARM_ALLOWED_HOSTS" in str(outcome)


@pytest.mark.usefixtures("isolated_settings_environment")
def test_missing_first_worker_variable_cannot_hide_web_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WEB_CONCURRENCY", "1")
    control = _settings_outcome(Settings, **_production_values())
    assert isinstance(control, Settings), control
    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    unsafe = _settings_outcome(Settings, **_production_values())
    assert isinstance(unsafe, ValidationError) and "WEB_CONCURRENCY=2 is unsupported" in str(unsafe)


@pytest.mark.usefixtures("isolated_settings_environment")
def test_shared_worker_dotenv_ignores_non_app_keys_but_process_typos_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("EXTERNAL_PROFILE=shared\nGOATFARM_COOKIE_SECURE=true\n")
    monkeypatch.setattr(config, "BACKEND_DIR", tmp_path)
    try:
        accepted: BaseSettings | Exception = settings_from_input(
            ScreeningWorkerSettings, env_file=dotenv
        )
    except Exception as exc:
        accepted = exc
    assert isinstance(accepted, ScreeningWorkerSettings), (
        f"A shared file's unrelated non-application variables must remain supported: {accepted!r}"
    )
    assert accepted.environment == "development"
    monkeypatch.setenv("GOATFARM_SCREENING_ENALBED", "true")
    outcome = _settings_outcome(ScreeningWorkerSettings)
    assert isinstance(outcome, ValidationError) and "GOATFARM_SCREENING_ENALBED" in str(outcome)


async def test_an_independent_pool_recovers_when_its_idle_physical_connection_has_closed() -> None:
    settings = settings_from_input(
        Settings, env_file=None, database_url=TEST_URL, db_pool_size=1, db_max_overflow=0
    )
    engine = database.create_engine(settings)
    try:
        async with engine.connect() as connection:
            raw = await connection.get_raw_connection()
            driver = raw.driver_connection
            assert isinstance(driver, asyncpg.Connection)
            assert await connection.scalar(text("SELECT 41")) == 41
        # Close only this test-owned returned connection. No server process
        # signal, shared API pool invalidation, or active-campaign DB is used.
        await driver.close()
        async with engine.connect() as recovered:
            recovered_raw = await recovered.get_raw_connection()
            recovered_driver = recovered_raw.driver_connection
            assert isinstance(recovered_driver, asyncpg.Connection)
            assert recovered_driver is not driver and not recovered_driver.is_closed(), (
                "A known closed idle connection must be replaced with a live physical connection "
                "before serving work"
            )
            assert await recovered.scalar(text("SELECT 42")) == 42
    finally:
        await engine.dispose()


async def test_api_session_factory_keeps_explicit_flush_and_detached_scalar_contracts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = settings_from_input(Settings, env_file=None, database_url=TEST_URL)
    engine = database.create_engine(settings)
    monkeypatch.setattr(database, "_engine", engine)
    monkeypatch.setattr(database, "_sessionmaker", None)
    factory = database.get_sessionmaker()
    try:
        async with factory() as db:
            user = User(
                email="api-session-contract@farm.in",
                name="API transaction",
                password_hash="not-an-auth-test",
            )
            db.add(user)
            assert (
                await db.scalar(
                    select(func.count()).select_from(User).where(User.email == user.email)
                )
                == 0
            )
            assert user in db.new
            await db.flush()
            uid = user.id
            await db.commit()
        try:
            scalars: object = (user.id, user.email, user.name)
        except Exception as exc:
            scalars = exc
        assert scalars == (uid, "api-session-contract@farm.in", "API transaction"), scalars
    finally:
        await engine.dispose()


async def test_json_text_preserves_a_real_nullable_outer_join_result() -> None:
    async with database.get_sessionmaker()() as db:
        owner = User(email="outer-join-owner@farm.in", password_hash="not-an-auth-test")
        db.add(owner)
        await db.flush()
        farm = Farm(name="Historical farm before default roles", owner_id=owner.id)
        db.add(farm)
        await db.commit()
        # A nullable SQL projection is legitimate even when the underlying
        # role.permissions column itself cannot store SQL NULL.
        permissions = await db.scalar(
            select(Role.permissions)
            .select_from(Farm)
            .outerjoin(Role, Role.farm_id == Farm.id)
            .where(Farm.id == farm.id)
        )
        assert permissions is None, "An absent role must not decode as the non-JSON string 'None'"


def _key_pair(tmp_path: Path) -> tuple[Path, Path]:
    private, public = tmp_path / "jwt-private.pem", tmp_path / "jwt-public.pem"
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    private.chmod(0o600)
    public.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    return private, public


class _PreflightComplete(Exception):
    """Stop after actual policy/key preflight and before any serving lifetime."""


async def _preflight(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    def finished() -> None:
        raise _PreflightComplete

    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(security, "get_settings", lambda: settings)
    # The preceding real JWT validation, mode guard and startup diagnostics
    # are under test. No connection/worker is claimed to have started.
    monkeypatch.setattr(main, "prime_dummy_password_hash", finished)
    with pytest.raises(_PreflightComplete):
        async with main.lifespan(FastAPI()):
            raise AssertionError("The serving lifetime must not be entered by this preflight test")


@pytest.mark.usefixtures("isolated_settings_environment")
@pytest.mark.parametrize(
    ("workers", "web", "warning"), [("1", "1", False), ("2", "1", True), ("1", "2", True)]
)
async def test_startup_warns_for_each_declared_multi_process_launcher(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
    workers: str,
    web: str,
    warning: bool,
) -> None:
    private, public = _key_pair(tmp_path)
    settings = settings_from_input(
        Settings, env_file=None, jwt_private_key_path=private, jwt_public_key_path=public
    )
    monkeypatch.setenv("UVICORN_WORKERS", workers)
    monkeypatch.setenv("WEB_CONCURRENCY", web)
    caplog.set_level(logging.WARNING, logger="goatfarm")
    await _preflight(monkeypatch, settings)
    messages = [
        r.getMessage()
        for r in caplog.records
        if "indicates a multi-process deployment" in r.getMessage()
    ]
    assert bool(messages) is warning, (
        "The operator warning must reflect either independently declared worker count"
    )


@pytest.mark.usefixtures("isolated_settings_environment")
@pytest.mark.parametrize(
    ("environment", "proxy", "warning"),
    [("production", "", True), ("production", "127.0.0.1", False), ("development", "", False)],
)
async def test_production_proxy_warning_matches_the_actual_trust_configuration(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
    environment: str,
    proxy: str,
    warning: bool,
) -> None:
    private, public = _key_pair(tmp_path)
    values = _production_values() if environment == "production" else {"environment": "development"}
    settings = settings_from_input(
        Settings,
        env_file=None,
        **(
            values
            | {
                "trusted_proxy_hosts": proxy,
                "jwt_private_key_path": private,
                "jwt_public_key_path": public,
            }
        ),
    )
    caplog.set_level(logging.WARNING, logger="goatfarm")
    await _preflight(monkeypatch, settings)
    messages = [
        r.getMessage()
        for r in caplog.records
        if "TRUSTED_PROXY_HOSTS is empty in production" in r.getMessage()
    ]
    assert bool(messages) is warning


@pytest.mark.usefixtures("isolated_settings_environment")
@pytest.mark.parametrize("mode", [0o600, 0o601, 0o640, 0o641])
def test_production_key_permissions_and_diagnostics_report_the_real_file_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mode: int,
) -> None:
    private, public = _key_pair(tmp_path)
    settings = settings_from_input(
        Settings,
        env_file=None,
        **(_production_values() | {"jwt_private_key_path": private, "jwt_public_key_path": public}),
    )
    private.chmod(mode)
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    outcome: object = None
    try:
        main._enforce_production_private_key_mode()
    except Exception as exc:
        outcome = exc
    observed = stat.S_IMODE(private.stat().st_mode)
    if mode == 0o600:
        assert outcome is None, "An owner-only readable private key must be accepted"
    else:
        assert isinstance(outcome, RuntimeError), (
            "No group/other permission is permitted on the production signing key"
        )
        assert f"mode {oct(observed)}" in str(outcome), (
            "The remediation diagnostic must report the file's actual permissions"
        )


async def test_raw_cleanup_driver_reports_committed_real_pages_and_all_outcome_totals(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    owner = await owner_with_farm(client, email="runtime-raw-cleanup@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    storage = ReplayableCleanupStorage()
    ids = []
    for index in range(3):
        key = f"raw/{farm_id}/runtime-{index}.jpg"
        ids.append(
            await _insert_due_image(
                farm_id=farm_id,
                key=key,
                due_at=utcnow() - timedelta(minutes=5),
                status="HEALTHY",
                direct_registration=False,
            )
        )
        storage.objects[key] = b"expired presigned raw upload"
    settings = Settings(environment="development", s3_bucket=storage.bucket)
    pages: list[RawCleanupSummary] = []

    async def batch(
        factory: async_sessionmaker[AsyncSession], transport: ScreeningStorage, *, batch_size: int
    ) -> RawCleanupSummary:
        summary = await run_raw_cleanup_batch(factory, transport, batch_size=batch_size)
        pages.append(summary)
        return summary

    async def interval(_delay: float) -> None:
        async with database.get_sessionmaker()() as observer:
            images = list(
                (
                    await observer.execute(select(ScreeningImage).where(ScreeningImage.id.in_(ids)))
                ).scalars()
            )
            assert len(images) == 3 and all(
                image.raw_cleanup_completed_at is not None for image in images
            )
        assert [(page.claimed, page.completed) for page in pages] == [(2, 2), (1, 1)]
        assert not storage.objects and len(storage.purge_calls) == 3
        messages = [
            r.getMessage()
            for r in caplog.records
            if r.getMessage().startswith("raw screening cleanup claimed=")
        ]
        assert messages == [
            "raw screening cleanup claimed=3 completed=3 in_progress=0 failed=0 "
            "acknowledgement_failed=0"
        ]
        raise asyncio.CancelledError

    caplog.set_level(logging.INFO, logger="goatfarm")
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(
        main, "storage_for_settings", lambda _settings: cast(ScreeningStorage, storage)
    )
    monkeypatch.setattr(main, "run_raw_cleanup_batch", batch)
    monkeypatch.setattr(
        main, "asyncio", SimpleNamespace(sleep=interval, CancelledError=asyncio.CancelledError)
    )
    with pytest.raises(asyncio.CancelledError):
        await main._screening_raw_cleanup_loop(300, batch_size=2, max_batches=3)


async def test_cadence_driver_reports_real_tenants_and_stops_after_an_exhausted_page(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    farms = await _farms(3, with_animals=True)
    pages: list[MaintenancePage | None] = []
    ticks = 0

    async def page(
        db: AsyncSession, job: MaintenanceJob, work: Callable[[int], Awaitable[MaintenancePage]]
    ) -> MaintenancePage | None:
        result = await run_maintenance_page(db, job, work)
        pages.append(result)
        return result

    async def interval(_delay: float) -> None:
        nonlocal ticks
        ticks += 1
        if ticks == 1:
            return
        async with database.get_sessionmaker()() as observer:
            from app.models import Task

            visited = set((await observer.execute(select(Task.farm_id).distinct())).scalars())
        assert visited == set(farms)
        assert [result.processed for result in pages if result is not None] == [2, 1]
        messages = [
            r.getMessage()
            for r in caplog.records
            if r.getMessage().startswith("cadence sweep materialized")
        ]
        assert messages == [f"cadence sweep materialized duties for {len(farms)} farms"]
        raise asyncio.CancelledError

    caplog.set_level(logging.INFO, logger="goatfarm")
    monkeypatch.setattr(main, "run_maintenance_page", page)
    monkeypatch.setattr(
        main, "asyncio", SimpleNamespace(sleep=interval, CancelledError=asyncio.CancelledError)
    )
    with pytest.raises(asyncio.CancelledError):
        await main._cadence_materialization_loop(300, farm_batch_size=2, max_batches=3)


async def test_periodic_alert_driver_records_the_real_completed_farm_page(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await owner_with_farm(client, email="runtime-alert-page@farm.in")
    settings = Settings(environment="development", notifications_enabled=True)
    provider = ConsoleNotificationProvider()
    measured: list[int] = []
    real_metric = metrics.record_maintenance_batch
    fixed = utcnow()

    def metric(operation: str, count: int) -> None:
        if operation == "notification_alerts":
            measured.append(count)
        real_metric(operation, count)

    async def interval(_delay: float) -> None:
        async with database.get_sessionmaker()() as observer:
            farm_count = await observer.scalar(select(func.count()).select_from(Farm))
            checkpoint = await observer.get(MaintenanceProgress, "notification_alerts")
            assert checkpoint is not None and checkpoint.completed_hour == fixed.replace(
                minute=0, second=0, microsecond=0, tzinfo=UTC
            )
        assert measured == [farm_count], (
            "The maintenance metric must include the actual successfully visited farm page"
        )
        raise asyncio.CancelledError

    monkeypatch.setattr(main, "utcnow", lambda: fixed)
    monkeypatch.setattr(metrics, "record_maintenance_batch", metric)
    monkeypatch.setattr(
        main,
        "asyncio",
        SimpleNamespace(
            sleep=interval,
            CancelledError=asyncio.CancelledError,
            BoundedSemaphore=asyncio.BoundedSemaphore,
            gather=asyncio.gather,
        ),
    )
    with pytest.raises(asyncio.CancelledError):
        await main._notification_periodic_alert_loop(settings, provider)


@pytest.mark.parametrize("ownership_lost", [False, True])
async def test_singleton_release_reports_only_a_genuinely_missing_physical_lock(
    caplog: pytest.LogCaptureFixture,
    ownership_lost: bool,
) -> None:
    connection = await database.get_engine().connect()
    caplog.set_level(logging.ERROR, logger="goatfarm")
    try:
        acquired = await connection.scalar(
            text("SELECT pg_try_advisory_lock(:key)"),
            {"key": main._PRODUCTION_API_SINGLETON_LOCK_ID},
        )
        assert acquired is True
        pid = await connection.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(pid, int)
        await connection.commit()
        lease = main._ProductionSingletonLease(connection=connection, backend_pid=pid)
        if ownership_lost:
            assert (
                await connection.scalar(
                    text("SELECT pg_advisory_unlock(:key)"),
                    {"key": main._PRODUCTION_API_SINGLETON_LOCK_ID},
                )
                is True
            )
            await connection.commit()
        await main._release_production_singleton_lease(lease)
        assert connection.closed
        reported = [
            record
            for record in caplog.records
            if record.getMessage() == "production API singleton lease was not owned at shutdown"
        ]
        assert bool(reported) is ownership_lost
    finally:
        if not connection.closed:
            # Any interrupted test must release its actual physical session's
            # advisory lock rather than silently return it to a shared pool.
            await connection.invalidate()
            await connection.close()
