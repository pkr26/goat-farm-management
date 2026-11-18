"""Real settings constructors retain the database port and TLS query contract."""

import os
from pathlib import Path

import pytest
from pydantic import ValidationError
from pydantic_settings import BaseSettings

from app.core.config import MigrationSettings, ScreeningWorkerSettings, Settings

from .settings_helpers import settings_from_input

pytestmark = pytest.mark.parametrize(
    ("settings_type", "field_name"),
    [
        pytest.param(Settings, "database_url", id="api-database"),
        pytest.param(Settings, "migration_database_url", id="api-migration"),
        pytest.param(MigrationSettings, "database_url", id="migration-database"),
        pytest.param(MigrationSettings, "migration_database_url", id="migration-ddl"),
        pytest.param(ScreeningWorkerSettings, "database_url", id="worker-database"),
    ],
)


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    """These constructor-only tests do not create or connect to a database."""


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    """Override shared truncation for this module's connection-free checks."""


@pytest.fixture(autouse=True)
def _isolated_url_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(os.environ):
        if name.upper().startswith("GOATFARM_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


def _settings_outcome(
    settings_type: type[BaseSettings], field_name: str, url: str
) -> BaseSettings | ValidationError:
    try:
        return settings_from_input(
            settings_type,
            {field_name: url},
            env_file=None,
            environment="development",
            db_sslmode="verify-full",
        )
    except ValidationError as actual:
        return actual


def _retain_only_the_expected_url_error(
    actual: ValidationError, field_name: str, policy: str
) -> None:
    """Keep unrelated constructor failures as their original actual errors."""
    errors = actual.errors(include_url=False, include_input=False)
    expected_message = f"Value error, GOATFARM_{field_name.upper()} {policy}"
    if (
        len(errors) != 1
        or errors[0]["loc"] != ()
        or errors[0]["type"] != "value_error"
        or errors[0]["msg"] != expected_message
    ):
        raise actual


def test_database_port_one_is_accepted_and_roundtrips(
    settings_type: type[BaseSettings], field_name: str
) -> None:
    url = "postgresql+asyncpg://reader@db.example.test:1/herd"
    actual = _settings_outcome(settings_type, field_name, url)
    if isinstance(actual, ValidationError):
        _retain_only_the_expected_url_error(actual, field_name, "must use a valid database port")
    assert not isinstance(actual, ValidationError), (
        "Database port 1 is a supported configured port and must be accepted"
    )
    assert actual.model_dump()[field_name] == url, "A supported database port must be retained"
    restored = settings_from_input(settings_type, actual.model_dump(), env_file=None)
    assert restored.model_dump() == actual.model_dump(), (
        "The complete accepted settings model must roundtrip without changing its database URL"
    )


def test_database_port_zero_remains_rejected(
    settings_type: type[BaseSettings], field_name: str
) -> None:
    actual = _settings_outcome(
        settings_type, field_name, "postgresql+asyncpg://reader@db.example.test:0/herd"
    )
    if isinstance(actual, ValidationError):
        _retain_only_the_expected_url_error(actual, field_name, "must use a valid database port")
    assert isinstance(actual, ValidationError), (
        "Database port 0 must be rejected by the configured database URL contract"
    )


def test_single_matching_sslmode_is_normalized_and_roundtrips(
    settings_type: type[BaseSettings], field_name: str
) -> None:
    base = "postgresql+asyncpg://reader@db.example.test:5432/herd"
    actual = settings_from_input(
        settings_type,
        {field_name: base + "?sslmode=verify-full"},
        env_file=None,
        environment="development",
        db_sslmode="verify-full",
    )
    assert actual.model_dump()[field_name] == base, (
        "One matching legacy sslmode must be removed from the actual configured driver URL"
    )
    restored = settings_from_input(settings_type, actual.model_dump(), env_file=None)
    assert restored.model_dump() == actual.model_dump(), (
        "The complete normalized settings model must remain valid on reconstruction"
    )


def test_exactly_two_matching_sslmode_entries_are_rejected(
    settings_type: type[BaseSettings], field_name: str
) -> None:
    actual = _settings_outcome(
        settings_type,
        field_name,
        "postgresql+asyncpg://reader@db.example.test:5432/herd"
        "?sslmode=verify-full&sslmode=verify-full",
    )
    if isinstance(actual, ValidationError):
        _retain_only_the_expected_url_error(
            actual, field_name, "must not declare sslmode more than once"
        )
    assert isinstance(actual, ValidationError), (
        "Exactly two matching sslmode entries must be rejected instead of silently merged"
    )


@pytest.mark.parametrize(
    ("query", "policy"),
    [
        pytest.param(
            "sslmode=verify-full&sslmode=",
            "must not declare sslmode more than once",
            id="matching-plus-blank-sslmode",
        ),
        pytest.param("sslmode=", "has an unsupported sslmode=''", id="blank-sslmode"),
        pytest.param(
            "sslrootcert=",
            "must not use URL TLS parameter(s) sslrootcert; configure GOATFARM_DB_SSLMODE "
            "and GOATFARM_DB_SSLROOTCERT_PATH instead",
            id="blank-sslrootcert",
        ),
        pytest.param(
            "ssl=",
            "must not use URL TLS parameter(s) ssl; configure GOATFARM_DB_SSLMODE "
            "and GOATFARM_DB_SSLROOTCERT_PATH instead",
            id="blank-ssl",
        ),
    ],
)
def test_blank_tls_query_entries_do_not_disappear_before_validation(
    settings_type: type[BaseSettings], field_name: str, query: str, policy: str
) -> None:
    actual = _settings_outcome(
        settings_type,
        field_name,
        "postgresql+asyncpg://reader@db.example.test:5432/herd?" + query,
    )
    if isinstance(actual, ValidationError):
        _retain_only_the_expected_url_error(actual, field_name, policy)
    assert isinstance(actual, ValidationError), (
        "Blank TLS query entries must remain visible to the database URL validation contract"
    )
