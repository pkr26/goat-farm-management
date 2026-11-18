"""The API Settings migration budget accepts one millisecond and rejects zero."""

import os
from pathlib import Path
from typing import Literal

import pytest
from pydantic import ValidationError

from app.core.config import Settings

from .settings_helpers import settings_from_input

pytestmark = pytest.mark.parametrize("delivery", ["constructor", "environment"])


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    """These complete Settings constructors perform no SQL or schema setup."""


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    """This module does not open connections or truncate application tables."""


@pytest.fixture(autouse=True)
def _isolated_api_budget_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(os.environ):
        if name.upper().startswith("GOATFARM_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


def _api_budget_outcome(
    delivery: Literal["constructor", "environment"],
    budget: int,
    expected_rejection_minimum: int,
    monkeypatch: pytest.MonkeyPatch,
) -> Settings | ValidationError:
    values: dict[str, object] = {}
    if delivery == "environment":
        monkeypatch.setenv("GOATFARM_MIGRATION_STATEMENT_TIMEOUT_MS", str(budget))
    else:
        monkeypatch.delenv("GOATFARM_MIGRATION_STATEMENT_TIMEOUT_MS", raising=False)
        values["migration_statement_timeout_ms"] = budget
    try:
        return settings_from_input(
            Settings,
            values,
            env_file=None,
            environment="development",
            database_url="postgresql+asyncpg://release@127.0.0.1:1/herdly_budget_unused_test",
        )
    except ValidationError as actual:
        errors = actual.errors(include_url=False)
        if actual.title != "Settings" or len(errors) != 1:
            raise
        error = errors[0]
        original_input = error.get("input")
        context = error.get("ctx")
        if (
            error["loc"] != ("migration_statement_timeout_ms",)
            or error["type"] != "greater_than_equal"
            or not (
                (type(original_input) is int and original_input == budget)
                or (
                    delivery == "environment"
                    and type(original_input) is str
                    and original_input == str(budget)
                )
            )
            or not isinstance(context, dict)
            or set(context) != {"ge"}
            or type(context["ge"]) is not int
            or context["ge"] != expected_rejection_minimum
        ):
            raise
        return actual


def test_api_settings_accepts_one_millisecond_migration_budget_and_roundtrips(
    delivery: Literal["constructor", "environment"], monkeypatch: pytest.MonkeyPatch
) -> None:
    actual = _api_budget_outcome(delivery, 1, 2, monkeypatch)
    error_details = (
        actual.errors(include_url=False) if isinstance(actual, ValidationError) else None
    )
    assert not isinstance(actual, ValidationError), (
        "API Settings must accept a positive one-millisecond migration budget; "
        f"actual constructor errors: {error_details!r}"
    )
    assert type(actual.migration_statement_timeout_ms) is int
    assert actual.migration_statement_timeout_ms == 1
    dumped = actual.model_dump()
    assert type(dumped["migration_statement_timeout_ms"]) is int
    assert dumped["migration_statement_timeout_ms"] == 1
    restored = settings_from_input(Settings, dumped, env_file=None)
    assert restored.model_dump() == dumped, (
        "The complete API Settings model must roundtrip its typed migration budget"
    )
    monkeypatch.setenv("GOATFARM_MIGRATION_STATEMENT_TIMEOUT_MS", "1")
    from_environment = settings_from_input(
        Settings,
        env_file=None,
        environment="development",
        database_url="postgresql+asyncpg://release@127.0.0.1:1/herdly_budget_unused_test",
    )
    assert type(from_environment.migration_statement_timeout_ms) is int
    assert from_environment.model_dump() == dumped, (
        "The actual process environment must preserve the complete API Settings model"
    )


def test_api_settings_rejects_zero_millisecond_migration_budget(
    delivery: Literal["constructor", "environment"], monkeypatch: pytest.MonkeyPatch
) -> None:
    actual = _api_budget_outcome(delivery, 0, 1, monkeypatch)
    returned = (
        None if isinstance(actual, ValidationError) else actual.migration_statement_timeout_ms
    )
    assert isinstance(actual, ValidationError), (
        "API Settings must reject a zero migration budget; "
        f"actual returned migration_statement_timeout_ms: {returned!r}"
    )
