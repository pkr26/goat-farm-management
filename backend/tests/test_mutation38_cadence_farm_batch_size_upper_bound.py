"""Complete Settings must agree with the cadence service's inclusive farm-page maximum."""

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
def _isolated_cadence_farm_batch_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in tuple(os.environ):
        if name.upper().startswith("GOATFARM_") or name in {"UVICORN_WORKERS", "WEB_CONCURRENCY"}:
            monkeypatch.delenv(name)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


def _cadence_farm_batch_outcome(
    delivery: Literal["constructor", "environment"],
    farms: int,
    expected_rejection_maximum: int,
    monkeypatch: pytest.MonkeyPatch,
) -> Settings | ValidationError:
    values: dict[str, object] = {}
    if delivery == "environment":
        monkeypatch.setenv("GOATFARM_CADENCE_MATERIALIZATION_FARM_BATCH_SIZE", str(farms))
    else:
        monkeypatch.delenv("GOATFARM_CADENCE_MATERIALIZATION_FARM_BATCH_SIZE", raising=False)
        values["cadence_materialization_farm_batch_size"] = farms
    try:
        return settings_from_input(
            Settings,
            values,
            env_file=None,
            environment="development",
            database_url="postgresql+asyncpg://repair@127.0.0.1:1/herdly_cadence_batch_unused_test",
        )
    except ValidationError as actual:
        errors = actual.errors(include_url=False)
        if actual.title != "Settings" or len(errors) != 1:
            raise
        error = errors[0]
        original_input = error.get("input")
        context = error.get("ctx")
        if (
            error["loc"] != ("cadence_materialization_farm_batch_size",)
            or error["type"] != "less_than_equal"
            or not (
                (type(original_input) is int and original_input == farms)
                or (
                    delivery == "environment"
                    and type(original_input) is str
                    and original_input == str(farms)
                )
            )
            or not isinstance(context, dict)
            or set(context) != {"le"}
            or type(context["le"]) is not int
            or context["le"] != expected_rejection_maximum
        ):
            raise
        return actual


def test_settings_accepts_one_thousand_cadence_farms_and_roundtrips(
    delivery: Literal["constructor", "environment"], monkeypatch: pytest.MonkeyPatch
) -> None:
    actual = _cadence_farm_batch_outcome(delivery, 1_000, 999, monkeypatch)
    error_details = (
        actual.errors(include_url=False) if isinstance(actual, ValidationError) else None
    )
    assert not isinstance(actual, ValidationError), (
        "Settings must accept a cadence farm batch of one thousand; "
        f"actual constructor errors: {error_details!r}"
    )
    assert type(actual.cadence_materialization_farm_batch_size) is int
    assert actual.cadence_materialization_farm_batch_size == 1_000
    dumped = actual.model_dump(mode="python")
    assert type(dumped["cadence_materialization_farm_batch_size"]) is int
    assert dumped["cadence_materialization_farm_batch_size"] == 1_000
    restored = settings_from_input(Settings, dumped, env_file=None)
    assert restored.model_dump(mode="python") == dumped, (
        "The complete Settings model must roundtrip its supported cadence farm batch"
    )
    monkeypatch.setenv("GOATFARM_CADENCE_MATERIALIZATION_FARM_BATCH_SIZE", "1000")
    from_environment = settings_from_input(
        Settings,
        env_file=None,
        environment="development",
        database_url="postgresql+asyncpg://repair@127.0.0.1:1/herdly_cadence_batch_unused_test",
    )
    assert type(from_environment.cadence_materialization_farm_batch_size) is int
    assert from_environment.model_dump(mode="python") == dumped, (
        "The actual process environment must preserve the complete Settings model"
    )


def test_settings_rejects_one_thousand_one_cadence_farms(
    delivery: Literal["constructor", "environment"], monkeypatch: pytest.MonkeyPatch
) -> None:
    actual = _cadence_farm_batch_outcome(delivery, 1_001, 1_000, monkeypatch)
    returned = (
        None
        if isinstance(actual, ValidationError)
        else actual.cadence_materialization_farm_batch_size
    )
    assert isinstance(actual, ValidationError), (
        "Settings must reject a cadence farm batch "
        "above the service limit of one thousand; "
        f"actual returned cadence_materialization_farm_batch_size: {returned!r}"
    )
