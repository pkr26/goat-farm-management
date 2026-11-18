"""A configured idempotency retention window accepts one hour and rejects zero."""

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
def _isolated_retention_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(os.environ):
        if name.upper().startswith("GOATFARM_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


def _retention_outcome(
    delivery: Literal["constructor", "environment"],
    hours: int,
    expected_rejection_minimum: int,
    monkeypatch: pytest.MonkeyPatch,
) -> Settings | ValidationError:
    values: dict[str, object] = {}
    if delivery == "environment":
        monkeypatch.setenv("GOATFARM_IDEMPOTENCY_RETENTION_HOURS", str(hours))
    else:
        monkeypatch.delenv("GOATFARM_IDEMPOTENCY_RETENTION_HOURS", raising=False)
        values["idempotency_retention_hours"] = hours
    try:
        return settings_from_input(
            Settings,
            values,
            env_file=None,
            environment="development",
            database_url="postgresql+asyncpg://release@127.0.0.1:1/herdly_retention_unused_test",
        )
    except ValidationError as actual:
        errors = actual.errors(include_url=False)
        if actual.title != "Settings" or len(errors) != 1:
            raise
        error = errors[0]
        original_input = error.get("input")
        context = error.get("ctx")
        if (
            error["loc"] != ("idempotency_retention_hours",)
            or error["type"] != "greater_than_equal"
            or not (
                (type(original_input) is int and original_input == hours)
                or (
                    delivery == "environment"
                    and type(original_input) is str
                    and original_input == str(hours)
                )
            )
            or not isinstance(context, dict)
            or set(context) != {"ge"}
            or type(context["ge"]) is not int
            or context["ge"] != expected_rejection_minimum
        ):
            raise
        return actual


def test_settings_accepts_one_hour_idempotency_retention_and_roundtrips(
    delivery: Literal["constructor", "environment"], monkeypatch: pytest.MonkeyPatch
) -> None:
    actual = _retention_outcome(delivery, 1, 2, monkeypatch)
    error_details = (
        actual.errors(include_url=False) if isinstance(actual, ValidationError) else None
    )
    assert not isinstance(actual, ValidationError), (
        "Settings must accept a positive one-hour idempotency retention window; "
        f"actual constructor errors: {error_details!r}"
    )
    assert type(actual.idempotency_retention_hours) is int
    assert actual.idempotency_retention_hours == 1
    dumped = actual.model_dump()
    assert type(dumped["idempotency_retention_hours"]) is int
    assert dumped["idempotency_retention_hours"] == 1
    restored = settings_from_input(Settings, dumped, env_file=None)
    assert restored.model_dump() == dumped, (
        "The complete Settings model must roundtrip its typed retention window"
    )
    monkeypatch.setenv("GOATFARM_IDEMPOTENCY_RETENTION_HOURS", "1")
    from_environment = settings_from_input(
        Settings,
        env_file=None,
        environment="development",
        database_url="postgresql+asyncpg://release@127.0.0.1:1/herdly_retention_unused_test",
    )
    assert type(from_environment.idempotency_retention_hours) is int
    assert from_environment.model_dump() == dumped, (
        "The actual process environment must preserve the complete Settings model"
    )


def test_settings_rejects_zero_hour_idempotency_retention(
    delivery: Literal["constructor", "environment"], monkeypatch: pytest.MonkeyPatch
) -> None:
    actual = _retention_outcome(delivery, 0, 1, monkeypatch)
    returned = None if isinstance(actual, ValidationError) else actual.idempotency_retention_hours
    assert isinstance(actual, ValidationError), (
        "Settings must reject a zero-hour idempotency retention window; "
        f"actual returned idempotency_retention_hours: {returned!r}"
    )
