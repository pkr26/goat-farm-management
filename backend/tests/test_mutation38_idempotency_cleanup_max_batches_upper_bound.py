"""Complete Settings accepts one hundred cleanup passes and rejects one hundred one."""

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
def _isolated_maximum_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(os.environ):
        if name.upper().startswith("GOATFARM_") or name in {"UVICORN_WORKERS", "WEB_CONCURRENCY"}:
            monkeypatch.delenv(name)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


def _maximum_settings(
    delivery: Literal["constructor", "environment"],
    max_batches: int,
    monkeypatch: pytest.MonkeyPatch,
) -> Settings:
    values: dict[str, object] = {}
    if delivery == "environment":
        monkeypatch.setenv("GOATFARM_IDEMPOTENCY_CLEANUP_MAX_BATCHES", str(max_batches))
    else:
        monkeypatch.delenv("GOATFARM_IDEMPOTENCY_CLEANUP_MAX_BATCHES", raising=False)
        values["idempotency_cleanup_max_batches"] = max_batches
    return settings_from_input(
        Settings,
        values,
        env_file=None,
        environment="development",
        database_url="postgresql+asyncpg://cleanup@127.0.0.1:1/herdly_maximum_unused_test",
    )


def _above_maximum_outcome(
    delivery: Literal["constructor", "environment"], monkeypatch: pytest.MonkeyPatch
) -> Settings | ValidationError:
    try:
        return _maximum_settings(delivery, 101, monkeypatch)
    except ValidationError as actual:
        errors = actual.errors(include_url=False)
        if actual.title != "Settings" or len(errors) != 1:
            raise
        error = errors[0]
        original_input = error.get("input")
        context = error.get("ctx")
        if (
            error["loc"] != ("idempotency_cleanup_max_batches",)
            or error["type"] != "less_than_equal"
            or not (
                (type(original_input) is int and original_input == 101)
                or (
                    delivery == "environment"
                    and type(original_input) is str
                    and original_input == "101"
                )
            )
            or not isinstance(context, dict)
            or set(context) != {"le"}
            or type(context["le"]) is not int
            or context["le"] != 100
        ):
            raise
        return actual


def test_settings_accepts_one_hundred_cleanup_passes_and_roundtrips(
    delivery: Literal["constructor", "environment"], monkeypatch: pytest.MonkeyPatch
) -> None:
    actual = _maximum_settings(delivery, 100, monkeypatch)
    assert type(actual.idempotency_cleanup_max_batches) is int
    assert actual.idempotency_cleanup_max_batches == 100
    dumped = actual.model_dump(mode="python")
    assert type(dumped["idempotency_cleanup_max_batches"]) is int
    assert dumped["idempotency_cleanup_max_batches"] == 100
    restored = settings_from_input(Settings, dumped, env_file=None)
    assert restored.model_dump(mode="python") == dumped, (
        "The complete Settings model must roundtrip its typed cleanup pass ceiling"
    )
    monkeypatch.setenv("GOATFARM_IDEMPOTENCY_CLEANUP_MAX_BATCHES", "100")
    from_environment = settings_from_input(
        Settings,
        env_file=None,
        environment="development",
        database_url="postgresql+asyncpg://cleanup@127.0.0.1:1/herdly_maximum_unused_test",
    )
    assert type(from_environment.idempotency_cleanup_max_batches) is int
    assert from_environment.model_dump(mode="python") == dumped, (
        "The actual process environment must preserve the complete Settings model"
    )


def test_settings_rejects_one_hundred_one_cleanup_passes(
    delivery: Literal["constructor", "environment"], monkeypatch: pytest.MonkeyPatch
) -> None:
    actual = _above_maximum_outcome(delivery, monkeypatch)
    returned = (
        None if isinstance(actual, ValidationError) else actual.idempotency_cleanup_max_batches
    )
    assert isinstance(actual, ValidationError), (
        "Settings must reject more than one hundred cleanup passes per tick; "
        f"actual returned idempotency_cleanup_max_batches: {returned!r}"
    )
