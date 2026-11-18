"""Independent worker transactions and operator-facing configuration boundaries."""

import os

import pytest
from pydantic import ValidationError
from pydantic_settings import BaseSettings
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import MigrationSettings, ScreeningWorkerSettings, Settings
from app.db import create_sessionmaker
from app.models import User
from app.security import hash_password

from .conftest import TEST_URL
from .settings_helpers import settings_from_input


@pytest.fixture
def isolated_settings_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(os.environ):
        if name.startswith("GOATFARM_") or name in {"UVICORN_WORKERS", "WEB_CONCURRENCY"}:
            monkeypatch.delenv(name)


def _settings_outcome(
    settings_type: type[BaseSettings], **values: object
) -> BaseSettings | Exception:
    try:
        return settings_from_input(settings_type, env_file=None, **values)
    except Exception as exc:
        return exc


def _production_values() -> dict[str, object]:
    return {
        "environment": "production",
        "database_url": "postgresql+asyncpg://api@db.example.test/goatfarm",
        "db_sslmode": "verify-full",
        "auth_rate_limit_enabled": True,
        "cookie_secure": True,
        "cors_origins": ["https://app.example.test"],
        "allowed_hosts": ["api.example.test"],
        "min_password_length": 12,
        "idempotency_request_hmac_secret": "worker-contract-request-hmac-" + "x" * 32,
        "totp_encryption_key": "VFRUVFRUVFRUVFRUVFRUVFRUVFRUVFRUVFRUVFRUVFQ",
    }


def _worker_factory() -> tuple[async_sessionmaker[AsyncSession], AsyncEngine]:
    settings = settings_from_input(
        ScreeningWorkerSettings,
        env_file=None,
        environment="development",
        database_url=TEST_URL,
        db_sslmode="disable",
        screening_enabled=False,
    )
    factory = create_sessionmaker(settings)
    engine = factory.kw["bind"]
    assert isinstance(engine, AsyncEngine)
    return factory, engine


async def test_independent_worker_reads_do_not_flush_or_commit_pending_work() -> None:
    # Exercise the public factory directly; an API-global sessionmaker does
    # not prove the separate worker process's session behavior.
    factory, engine = _worker_factory()
    email = "worker-explicit-transaction@contract.test"
    try:
        async with factory() as writer, factory() as observer:
            pending = User(
                email=email,
                name="Pending worker row",
                password_hash=hash_password("worker-test-password"),
            )
            writer.add(pending)
            count = select(func.count(User.id)).where(User.email == email)
            assert await writer.scalar(count) == 0, "An ordinary read must not flush worker writes"
            assert pending in writer.new
            assert await observer.scalar(count) == 0

            await writer.flush()
            assert await writer.scalar(count) == 1
            assert await observer.scalar(count) == 0, "Flush must not commit a worker transaction"
            await writer.rollback()
            assert await observer.scalar(count) == 0
    finally:
        await engine.dispose()


async def test_independent_worker_committed_scalars_survive_session_close() -> None:
    factory, engine = _worker_factory()
    email = "worker-committed-scalars@contract.test"
    name = "Committed worker row"
    try:
        async with factory() as writer:
            created = User(
                email=email, name=name, password_hash=hash_password("worker-test-password")
            )
            writer.add(created)
            await writer.flush()
            created_id = created.id
            await writer.commit()
        async with factory() as observer:
            persisted = await observer.get(User, created_id)
            assert persisted is not None
            assert (persisted.email, persisted.name) == (email, name)
        try:
            loaded: tuple[int, str, str | None] | Exception = (
                created.id,
                created.email,
                created.name,
            )
        except Exception as exc:
            loaded = exc
        assert loaded == (created_id, email, name), (
            "A completed worker transaction must leave loaded scalar results usable "
            f"after its session closes: {loaded!r}"
        )
    finally:
        await engine.dispose()


@pytest.mark.usefixtures("isolated_settings_environment")
@pytest.mark.parametrize(
    ("settings_type", "field", "knob"),
    [
        (Settings, "database_url", "GOATFARM_DATABASE_URL"),
        (Settings, "migration_database_url", "GOATFARM_MIGRATION_DATABASE_URL"),
        (MigrationSettings, "database_url", "GOATFARM_DATABASE_URL"),
        (MigrationSettings, "migration_database_url", "GOATFARM_MIGRATION_DATABASE_URL"),
        (ScreeningWorkerSettings, "database_url", "GOATFARM_DATABASE_URL"),
    ],
)
def test_unknown_url_sslmode_is_a_controlled_settings_error(
    settings_type: type[BaseSettings], field: str, knob: str
) -> None:
    unknown = "unsupported-worker-mode"
    raw_url = f"postgresql+asyncpg://worker@db.example.test/goatfarm?sslmode={unknown}"
    outcome = _settings_outcome(
        settings_type, environment="development", db_sslmode="disable", **{field: raw_url}
    )
    assert isinstance(outcome, ValidationError), (
        f"Unsupported sslmode must yield a settings diagnostic, got {outcome!r}"
    )
    assert f"{knob} has an unsupported sslmode={unknown!r}" in str(outcome)


@pytest.mark.usefixtures("isolated_settings_environment")
def test_malformed_first_cors_origin_does_not_discard_later_canonical_origins() -> None:
    malformed = "https://broken.example.test:not-a-port"
    settings = settings_from_input(
        Settings,
        env_file=None,
        environment="development",
        cors_origins=[malformed, " HTTPS://APP.EXAMPLE.TEST:443 "],
    )
    assert settings.cors_origins == [malformed, "https://app.example.test"]


@pytest.mark.usefixtures("isolated_settings_environment")
def test_production_rejects_malformed_cors_port_with_the_original_operator_value() -> None:
    valid = _production_values()
    control = _settings_outcome(Settings, **valid)
    assert isinstance(control, Settings), f"Production control must be valid: {control!r}"
    malformed = "https://broken.example.test:not-a-port"
    outcome = _settings_outcome(Settings, **(valid | {"cors_origins": [malformed]}))
    assert isinstance(outcome, ValidationError), (
        f"Production must refuse an origin that no browser can send: {outcome!r}"
    )
    assert "GOATFARM_CORS_ORIGINS" in str(outcome) and malformed in str(outcome)


@pytest.mark.usefixtures("isolated_settings_environment")
@pytest.mark.parametrize("web_workers", [2, 4])
def test_unparseable_uvicorn_count_cannot_hide_unsafe_web_concurrency(
    monkeypatch: pytest.MonkeyPatch, web_workers: int
) -> None:
    monkeypatch.setenv("UVICORN_WORKERS", "not-a-worker-count")
    monkeypatch.setenv("WEB_CONCURRENCY", "1")
    valid = _production_values()
    control = _settings_outcome(Settings, **valid)
    assert isinstance(control, Settings), f"Single-worker control must be accepted: {control!r}"
    monkeypatch.setenv("WEB_CONCURRENCY", str(web_workers))
    outcome = _settings_outcome(Settings, **valid)
    assert isinstance(outcome, ValidationError), (
        f"An unparseable first variable must not bypass the second safety check: {outcome!r}"
    )
    assert f"WEB_CONCURRENCY={web_workers} is unsupported" in str(outcome)
    assert "run exactly one uvicorn worker" in str(outcome)
