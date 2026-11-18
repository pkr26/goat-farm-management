"""Complete Settings must agree with the task repair service's 1..10,000 range."""

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
def _isolated_task_batch_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(os.environ):
        if name.upper().startswith("GOATFARM_") or name in {"UVICORN_WORKERS", "WEB_CONCURRENCY"}:
            monkeypatch.delenv(name)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


def _task_batch_outcome(
    delivery: Literal["constructor", "environment"],
    tasks: int,
    expected_rejection_maximum: int,
    monkeypatch: pytest.MonkeyPatch,
) -> Settings | ValidationError:
    values: dict[str, object] = {}
    if delivery == "environment":
        monkeypatch.setenv("GOATFARM_LEGACY_REPAIR_TASK_BATCH_SIZE", str(tasks))
    else:
        monkeypatch.delenv("GOATFARM_LEGACY_REPAIR_TASK_BATCH_SIZE", raising=False)
        values["legacy_repair_task_batch_size"] = tasks
    try:
        return settings_from_input(
            Settings,
            values,
            env_file=None,
            environment="development",
            database_url="postgresql+asyncpg://repair@127.0.0.1:1/herdly_task_batch_unused_test",
        )
    except ValidationError as actual:
        errors = actual.errors(include_url=False)
        if actual.title != "Settings" or len(errors) != 1:
            raise
        error = errors[0]
        original_input = error.get("input")
        context = error.get("ctx")
        if (
            error["loc"] != ("legacy_repair_task_batch_size",)
            or error["type"] != "less_than_equal"
            or not (
                (type(original_input) is int and original_input == tasks)
                or (
                    delivery == "environment"
                    and type(original_input) is str
                    and original_input == str(tasks)
                )
            )
            or not isinstance(context, dict)
            or set(context) != {"le"}
            or type(context["le"]) is not int
            or context["le"] != expected_rejection_maximum
        ):
            raise
        return actual


def test_settings_accepts_ten_thousand_tasks_per_repair_batch_and_roundtrips(
    delivery: Literal["constructor", "environment"], monkeypatch: pytest.MonkeyPatch
) -> None:
    actual = _task_batch_outcome(delivery, 10_000, 9_999, monkeypatch)
    error_details = (
        actual.errors(include_url=False) if isinstance(actual, ValidationError) else None
    )
    assert not isinstance(actual, ValidationError), (
        "Settings must accept a legacy repair task batch of ten thousand; "
        f"actual constructor errors: {error_details!r}"
    )
    assert type(actual.legacy_repair_task_batch_size) is int
    assert actual.legacy_repair_task_batch_size == 10_000
    dumped = actual.model_dump(mode="python")
    assert type(dumped["legacy_repair_task_batch_size"]) is int
    assert dumped["legacy_repair_task_batch_size"] == 10_000
    restored = settings_from_input(Settings, dumped, env_file=None)
    assert restored.model_dump(mode="python") == dumped, (
        "The complete Settings model must roundtrip its supported task repair batch"
    )
    monkeypatch.setenv("GOATFARM_LEGACY_REPAIR_TASK_BATCH_SIZE", "10000")
    from_environment = settings_from_input(
        Settings,
        env_file=None,
        environment="development",
        database_url="postgresql+asyncpg://repair@127.0.0.1:1/herdly_task_batch_unused_test",
    )
    assert type(from_environment.legacy_repair_task_batch_size) is int
    assert from_environment.model_dump(mode="python") == dumped, (
        "The actual process environment must preserve the complete Settings model"
    )


def test_settings_rejects_ten_thousand_one_tasks_per_repair_batch(
    delivery: Literal["constructor", "environment"], monkeypatch: pytest.MonkeyPatch
) -> None:
    actual = _task_batch_outcome(delivery, 10_001, 10_000, monkeypatch)
    returned = None if isinstance(actual, ValidationError) else actual.legacy_repair_task_batch_size
    assert isinstance(actual, ValidationError), (
        "Settings must reject a legacy repair task batch above the service limit of ten thousand; "
        f"actual returned legacy_repair_task_batch_size: {returned!r}"
    )
