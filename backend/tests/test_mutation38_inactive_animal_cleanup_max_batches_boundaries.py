"""Complete Settings keeps inactive-animal task cleanup able to make progress."""

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
def _isolated_cleanup_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(os.environ):
        if name.upper().startswith("GOATFARM_") or name in {"UVICORN_WORKERS", "WEB_CONCURRENCY"}:
            monkeypatch.delenv(name)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


def _cleanup_max_batches_outcome(
    delivery: Literal["constructor", "environment"],
    max_batches: int,
    expected_rejection_minimum: int,
    monkeypatch: pytest.MonkeyPatch,
) -> Settings | ValidationError:
    values: dict[str, object] = {}
    if delivery == "environment":
        monkeypatch.setenv("GOATFARM_INACTIVE_ANIMAL_TASK_CLEANUP_MAX_BATCHES", str(max_batches))
    else:
        monkeypatch.delenv("GOATFARM_INACTIVE_ANIMAL_TASK_CLEANUP_MAX_BATCHES", raising=False)
        values["inactive_animal_task_cleanup_max_batches"] = max_batches
    try:
        return settings_from_input(
            Settings,
            values,
            env_file=None,
            environment="development",
            database_url="postgresql+asyncpg://cleanup@127.0.0.1:1/herdly_inactive_budget_unused_test",
        )
    except ValidationError as actual:
        errors = actual.errors(include_url=False)
        if actual.title != "Settings" or len(errors) != 1:
            raise
        error = errors[0]
        original_input = error.get("input")
        context = error.get("ctx")
        if (
            error["loc"] != ("inactive_animal_task_cleanup_max_batches",)
            or error["type"] != "greater_than_equal"
            or not (
                (type(original_input) is int and original_input == max_batches)
                or (
                    delivery == "environment"
                    and type(original_input) is str
                    and original_input == str(max_batches)
                )
            )
            or not isinstance(context, dict)
            or set(context) != {"ge"}
            or type(context["ge"]) is not int
            or context["ge"] != expected_rejection_minimum
        ):
            raise
        return actual


def test_settings_accepts_one_inactive_animal_cleanup_batch_and_roundtrips(
    delivery: Literal["constructor", "environment"], monkeypatch: pytest.MonkeyPatch
) -> None:
    actual = _cleanup_max_batches_outcome(delivery, 1, 2, monkeypatch)
    error_details = (
        actual.errors(include_url=False) if isinstance(actual, ValidationError) else None
    )
    assert not isinstance(actual, ValidationError), (
        "Settings must accept one inactive-animal task cleanup batch per scheduled tick; "
        f"actual constructor errors: {error_details!r}"
    )
    assert type(actual.inactive_animal_task_cleanup_max_batches) is int
    assert actual.inactive_animal_task_cleanup_max_batches == 1
    dumped = actual.model_dump(mode="python")
    assert type(dumped["inactive_animal_task_cleanup_max_batches"]) is int
    assert dumped["inactive_animal_task_cleanup_max_batches"] == 1
    restored = settings_from_input(Settings, dumped, env_file=None)
    assert restored.model_dump(mode="python") == dumped, (
        "The complete Settings model must roundtrip its typed inactive-animal cleanup budget"
    )
    monkeypatch.setenv("GOATFARM_INACTIVE_ANIMAL_TASK_CLEANUP_MAX_BATCHES", "1")
    from_environment = settings_from_input(
        Settings,
        env_file=None,
        environment="development",
        database_url="postgresql+asyncpg://cleanup@127.0.0.1:1/herdly_inactive_budget_unused_test",
    )
    assert type(from_environment.inactive_animal_task_cleanup_max_batches) is int
    assert from_environment.model_dump(mode="python") == dumped, (
        "The actual process environment must preserve the complete Settings model"
    )


def test_settings_rejects_zero_inactive_animal_cleanup_batches(
    delivery: Literal["constructor", "environment"], monkeypatch: pytest.MonkeyPatch
) -> None:
    actual = _cleanup_max_batches_outcome(delivery, 0, 1, monkeypatch)
    returned = (
        None
        if isinstance(actual, ValidationError)
        else actual.inactive_animal_task_cleanup_max_batches
    )
    assert isinstance(actual, ValidationError), (
        "Settings must reject zero inactive-animal task cleanup batches per scheduled tick; "
        f"actual returned inactive_animal_task_cleanup_max_batches: {returned!r}"
    )
