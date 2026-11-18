"""Complete Settings accepts a one-row cleanup batch and rejects a zero-row batch."""

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
def _isolated_batch_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(os.environ):
        if name.upper().startswith("GOATFARM_") or name in {"UVICORN_WORKERS", "WEB_CONCURRENCY"}:
            monkeypatch.delenv(name)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


def _batch_settings(
    delivery: Literal["constructor", "environment"],
    batch_size: int,
    monkeypatch: pytest.MonkeyPatch,
) -> Settings:
    values: dict[str, object] = {}
    if delivery == "environment":
        monkeypatch.setenv("GOATFARM_IDEMPOTENCY_CLEANUP_BATCH_SIZE", str(batch_size))
    else:
        monkeypatch.delenv("GOATFARM_IDEMPOTENCY_CLEANUP_BATCH_SIZE", raising=False)
        values["idempotency_cleanup_batch_size"] = batch_size
    return settings_from_input(
        Settings,
        values,
        env_file=None,
        environment="development",
        database_url="postgresql+asyncpg://cleanup@127.0.0.1:1/herdly_batch_unused_test",
    )


def _zero_batch_outcome(
    delivery: Literal["constructor", "environment"], monkeypatch: pytest.MonkeyPatch
) -> Settings | ValidationError:
    try:
        return _batch_settings(delivery, 0, monkeypatch)
    except ValidationError as actual:
        errors = actual.errors(include_url=False)
        if actual.title != "Settings" or len(errors) != 1:
            raise
        error = errors[0]
        original_input = error.get("input")
        context = error.get("ctx")
        if (
            error["loc"] != ("idempotency_cleanup_batch_size",)
            or error["type"] != "greater_than_equal"
            or not (
                (type(original_input) is int and original_input == 0)
                or (
                    delivery == "environment"
                    and type(original_input) is str
                    and original_input == "0"
                )
            )
            or not isinstance(context, dict)
            or set(context) != {"ge"}
            or type(context["ge"]) is not int
            or context["ge"] != 1
        ):
            raise
        return actual


def test_settings_accepts_one_row_cleanup_batch_and_roundtrips(
    delivery: Literal["constructor", "environment"], monkeypatch: pytest.MonkeyPatch
) -> None:
    actual = _batch_settings(delivery, 1, monkeypatch)
    assert type(actual.idempotency_cleanup_batch_size) is int
    assert actual.idempotency_cleanup_batch_size == 1
    dumped = actual.model_dump(mode="python")
    assert type(dumped["idempotency_cleanup_batch_size"]) is int
    assert dumped["idempotency_cleanup_batch_size"] == 1
    restored = settings_from_input(Settings, dumped, env_file=None)
    assert restored.model_dump(mode="python") == dumped, (
        "The complete Settings model must roundtrip its typed cleanup batch size"
    )
    monkeypatch.setenv("GOATFARM_IDEMPOTENCY_CLEANUP_BATCH_SIZE", "1")
    from_environment = settings_from_input(
        Settings,
        env_file=None,
        environment="development",
        database_url="postgresql+asyncpg://cleanup@127.0.0.1:1/herdly_batch_unused_test",
    )
    assert type(from_environment.idempotency_cleanup_batch_size) is int
    assert from_environment.model_dump(mode="python") == dumped, (
        "The actual process environment must preserve the complete Settings model"
    )


def test_settings_rejects_zero_row_cleanup_batch(
    delivery: Literal["constructor", "environment"], monkeypatch: pytest.MonkeyPatch
) -> None:
    actual = _zero_batch_outcome(delivery, monkeypatch)
    returned = (
        None if isinstance(actual, ValidationError) else actual.idempotency_cleanup_batch_size
    )
    assert isinstance(actual, ValidationError), (
        "Settings must reject an idempotency cleanup batch with no rows; "
        f"actual returned idempotency_cleanup_batch_size: {returned!r}"
    )
