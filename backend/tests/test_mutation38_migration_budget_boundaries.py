"""A configured migration budget is a positive typed number of milliseconds."""

import os
from typing import Literal

import pytest
from pydantic import ValidationError

from app.core.config import MigrationSettings

from .settings_helpers import settings_from_input

pytestmark = pytest.mark.parametrize("delivery", ["constructor", "environment"])


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    """Constructor validation does not create a database or execute a migration."""


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    """Override the shared connection-based truncation for this pure module."""


@pytest.fixture(autouse=True)
def _isolated_migration_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(os.environ):
        if name.upper().startswith("GOATFARM_"):
            monkeypatch.delenv(name)


def _settings_outcome(
    delivery: Literal["constructor", "environment"],
    budget: int,
    monkeypatch: pytest.MonkeyPatch,
) -> MigrationSettings | ValidationError:
    values: dict[str, object] = {}
    if delivery == "environment":
        monkeypatch.setenv("GOATFARM_MIGRATION_STATEMENT_TIMEOUT_MS", str(budget))
    else:
        monkeypatch.delenv("GOATFARM_MIGRATION_STATEMENT_TIMEOUT_MS", raising=False)
        values["migration_statement_timeout_ms"] = budget
    try:
        return settings_from_input(
            MigrationSettings,
            values,
            env_file=None,
            environment="development",
            database_url="postgresql+asyncpg://release@db.example.test:5432/herd",
        )
    except ValidationError as actual:
        return actual


def _retain_only_the_actual_budget_boundary_error(actual: ValidationError, offered: int) -> None:
    """Other fields, inputs and validation failures keep their actual errors."""
    errors = actual.errors(include_url=False)
    if len(errors) != 1:
        raise actual
    error = errors[0]
    original_input = error.get("input")
    context = error.get("ctx")
    minimum = context.get("ge") if isinstance(context, dict) else None
    if (
        error["loc"] != ("migration_statement_timeout_ms",)
        or error["type"] != "greater_than_equal"
        or not (
            (type(original_input) is int and original_input == offered)
            or (type(original_input) is str and original_input == str(offered))
        )
        or type(minimum) is not int
        or minimum < 1
        or minimum <= offered
    ):
        raise actual


def test_one_millisecond_migration_budget_roundtrips_typed_and_process_environment(
    delivery: Literal["constructor", "environment"], monkeypatch: pytest.MonkeyPatch
) -> None:
    actual = _settings_outcome(delivery, 1, monkeypatch)
    if isinstance(actual, ValidationError):
        _retain_only_the_actual_budget_boundary_error(actual, 1)
    assert not isinstance(actual, ValidationError), (
        "A positive one-millisecond migration statement budget must be accepted"
    )
    assert type(actual.migration_statement_timeout_ms) is int, (
        "The real migration settings must expose the budget as a typed integer"
    )
    assert actual.migration_statement_timeout_ms == 1, (
        "The configured one-millisecond budget must be preserved without clamping"
    )
    dumped = actual.model_dump()
    restored = settings_from_input(MigrationSettings, dumped, env_file=None)
    assert restored.model_dump() == dumped, (
        "The complete typed migration settings must roundtrip without changing the budget"
    )
    monkeypatch.setenv(
        "GOATFARM_MIGRATION_STATEMENT_TIMEOUT_MS", str(actual.migration_statement_timeout_ms)
    )
    from_environment = settings_from_input(
        MigrationSettings,
        env_file=None,
        environment="development",
        database_url="postgresql+asyncpg://release@db.example.test:5432/herd",
    )
    assert type(from_environment.migration_statement_timeout_ms) is int, (
        "The real process environment must produce a typed migration budget"
    )
    assert from_environment.model_dump() == dumped, (
        "Serializing the accepted budget to its real environment variable must preserve settings"
    )


def test_zero_millisecond_migration_budget_is_rejected(
    delivery: Literal["constructor", "environment"], monkeypatch: pytest.MonkeyPatch
) -> None:
    actual = _settings_outcome(delivery, 0, monkeypatch)
    if isinstance(actual, ValidationError):
        _retain_only_the_actual_budget_boundary_error(actual, 0)
    assert isinstance(actual, ValidationError), (
        "A zero migration statement budget must be rejected instead of allowing an unbounded wait"
    )
