"""Complete Settings accepts a one-minute legacy repair interval and rejects 59 seconds."""

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
def _isolated_legacy_repair_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(os.environ):
        if name.upper().startswith("GOATFARM_") or name in {"UVICORN_WORKERS", "WEB_CONCURRENCY"}:
            monkeypatch.delenv(name)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


def _legacy_repair_interval_outcome(
    delivery: Literal["constructor", "environment"],
    seconds: int,
    expected_rejection_minimum: int,
    monkeypatch: pytest.MonkeyPatch,
) -> Settings | ValidationError:
    values: dict[str, object] = {}
    if delivery == "environment":
        monkeypatch.setenv("GOATFARM_LEGACY_REPAIR_INTERVAL_SECONDS", str(seconds))
    else:
        monkeypatch.delenv("GOATFARM_LEGACY_REPAIR_INTERVAL_SECONDS", raising=False)
        values["legacy_repair_interval_seconds"] = seconds
    try:
        return settings_from_input(
            Settings,
            values,
            env_file=None,
            environment="development",
            database_url="postgresql+asyncpg://repair@127.0.0.1:1/herdly_repair_unused_test",
        )
    except ValidationError as actual:
        errors = actual.errors(include_url=False)
        if actual.title != "Settings" or len(errors) != 1:
            raise
        error = errors[0]
        original_input = error.get("input")
        context = error.get("ctx")
        if (
            error["loc"] != ("legacy_repair_interval_seconds",)
            or error["type"] != "greater_than_equal"
            or not (
                (type(original_input) is int and original_input == seconds)
                or (
                    delivery == "environment"
                    and type(original_input) is str
                    and original_input == str(seconds)
                )
            )
            or not isinstance(context, dict)
            or set(context) != {"ge"}
            or type(context["ge"]) is not int
            or context["ge"] != expected_rejection_minimum
        ):
            raise
        return actual


def test_settings_accepts_sixty_second_legacy_repair_interval_and_roundtrips(
    delivery: Literal["constructor", "environment"], monkeypatch: pytest.MonkeyPatch
) -> None:
    actual = _legacy_repair_interval_outcome(delivery, 60, 61, monkeypatch)
    error_details = (
        actual.errors(include_url=False) if isinstance(actual, ValidationError) else None
    )
    assert not isinstance(actual, ValidationError), (
        "Settings must accept a positive one-minute legacy repair interval; "
        f"actual constructor errors: {error_details!r}"
    )
    assert type(actual.legacy_repair_interval_seconds) is int
    assert actual.legacy_repair_interval_seconds == 60
    dumped = actual.model_dump(mode="python")
    assert type(dumped["legacy_repair_interval_seconds"]) is int
    assert dumped["legacy_repair_interval_seconds"] == 60
    restored = settings_from_input(Settings, dumped, env_file=None)
    assert restored.model_dump(mode="python") == dumped, (
        "The complete Settings model must roundtrip its typed legacy repair interval"
    )
    monkeypatch.setenv("GOATFARM_LEGACY_REPAIR_INTERVAL_SECONDS", "60")
    from_environment = settings_from_input(
        Settings,
        env_file=None,
        environment="development",
        database_url="postgresql+asyncpg://repair@127.0.0.1:1/herdly_repair_unused_test",
    )
    assert type(from_environment.legacy_repair_interval_seconds) is int
    assert from_environment.model_dump(mode="python") == dumped, (
        "The actual process environment must preserve the complete Settings model"
    )


def test_settings_rejects_fifty_nine_second_legacy_repair_interval(
    delivery: Literal["constructor", "environment"], monkeypatch: pytest.MonkeyPatch
) -> None:
    actual = _legacy_repair_interval_outcome(delivery, 59, 60, monkeypatch)
    returned = (
        None if isinstance(actual, ValidationError) else actual.legacy_repair_interval_seconds
    )
    assert isinstance(actual, ValidationError), (
        "Settings must reject a legacy repair interval below one minute; "
        f"actual returned legacy_repair_interval_seconds: {returned!r}"
    )
