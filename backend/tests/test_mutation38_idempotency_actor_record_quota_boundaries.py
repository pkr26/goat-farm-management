"""Complete Settings enforces the supported per-actor idempotency record quota."""

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
def _isolated_actor_quota_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(os.environ):
        if name.upper().startswith("GOATFARM_") or name in {"UVICORN_WORKERS", "WEB_CONCURRENCY"}:
            monkeypatch.delenv(name)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


def _actor_quota_outcome(
    delivery: Literal["constructor", "environment"],
    quota: int,
    expected_error_type: Literal["greater_than_equal", "less_than_equal"],
    expected_context_key: Literal["ge", "le"],
    expected_bound: int,
    monkeypatch: pytest.MonkeyPatch,
) -> Settings | ValidationError:
    values: dict[str, object] = {}
    if delivery == "environment":
        monkeypatch.setenv("GOATFARM_IDEMPOTENCY_MAX_OPEN_RECORDS_PER_ACTOR", str(quota))
    else:
        monkeypatch.delenv("GOATFARM_IDEMPOTENCY_MAX_OPEN_RECORDS_PER_ACTOR", raising=False)
        values["idempotency_max_open_records_per_actor"] = quota
    try:
        return settings_from_input(
            Settings,
            values,
            env_file=None,
            environment="development",
            database_url="postgresql+asyncpg://quota@127.0.0.1:1/herdly_actor_quota_unused_test",
        )
    except ValidationError as actual:
        errors = actual.errors(include_url=False)
        if actual.title != "Settings" or len(errors) != 1:
            raise
        error = errors[0]
        original_input = error.get("input")
        context = error.get("ctx")
        if (
            error["loc"] != ("idempotency_max_open_records_per_actor",)
            or error["type"] != expected_error_type
            or not (
                (type(original_input) is int and original_input == quota)
                or (
                    delivery == "environment"
                    and type(original_input) is str
                    and original_input == str(quota)
                )
            )
            or not isinstance(context, dict)
            or set(context) != {expected_context_key}
            or type(context[expected_context_key]) is not int
            or context[expected_context_key] != expected_bound
        ):
            raise
        return actual


def _assert_actor_quota_roundtrips(
    actual: Settings, quota: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert type(actual.idempotency_max_open_records_per_actor) is int
    assert actual.idempotency_max_open_records_per_actor == quota
    dumped = actual.model_dump(mode="python")
    assert type(dumped["idempotency_max_open_records_per_actor"]) is int
    assert dumped["idempotency_max_open_records_per_actor"] == quota
    restored = settings_from_input(Settings, dumped, env_file=None)
    assert restored.model_dump(mode="python") == dumped, (
        "The complete Settings model must roundtrip its typed per-actor record quota"
    )
    monkeypatch.setenv("GOATFARM_IDEMPOTENCY_MAX_OPEN_RECORDS_PER_ACTOR", str(quota))
    from_environment = settings_from_input(
        Settings,
        env_file=None,
        environment="development",
        database_url="postgresql+asyncpg://quota@127.0.0.1:1/herdly_actor_quota_unused_test",
    )
    assert type(from_environment.idempotency_max_open_records_per_actor) is int
    assert from_environment.model_dump(mode="python") == dumped, (
        "The actual process environment must preserve the complete Settings model"
    )


def test_settings_accepts_one_actor_record_and_roundtrips(
    delivery: Literal["constructor", "environment"], monkeypatch: pytest.MonkeyPatch
) -> None:
    actual = _actor_quota_outcome(delivery, 1, "greater_than_equal", "ge", 2, monkeypatch)
    error_details = (
        actual.errors(include_url=False) if isinstance(actual, ValidationError) else None
    )
    assert not isinstance(actual, ValidationError), (
        "Settings must accept one open idempotency record per actor; "
        f"actual constructor errors: {error_details!r}"
    )
    _assert_actor_quota_roundtrips(actual, 1, monkeypatch)


def test_settings_rejects_zero_actor_records(
    delivery: Literal["constructor", "environment"], monkeypatch: pytest.MonkeyPatch
) -> None:
    actual = _actor_quota_outcome(delivery, 0, "greater_than_equal", "ge", 1, monkeypatch)
    returned = (
        None
        if isinstance(actual, ValidationError)
        else actual.idempotency_max_open_records_per_actor
    )
    assert isinstance(actual, ValidationError), (
        "Settings must reject a zero open-idempotency-record quota per actor; "
        f"actual returned idempotency_max_open_records_per_actor: {returned!r}"
    )


def test_settings_accepts_one_hundred_thousand_actor_records_and_roundtrips(
    delivery: Literal["constructor", "environment"], monkeypatch: pytest.MonkeyPatch
) -> None:
    actual = _actor_quota_outcome(delivery, 100_000, "less_than_equal", "le", 99_999, monkeypatch)
    error_details = (
        actual.errors(include_url=False) if isinstance(actual, ValidationError) else None
    )
    assert not isinstance(actual, ValidationError), (
        "Settings must accept a quota of one hundred thousand open idempotency records per actor; "
        f"actual constructor errors: {error_details!r}"
    )
    _assert_actor_quota_roundtrips(actual, 100_000, monkeypatch)


def test_settings_rejects_more_than_one_hundred_thousand_actor_records(
    delivery: Literal["constructor", "environment"], monkeypatch: pytest.MonkeyPatch
) -> None:
    actual = _actor_quota_outcome(delivery, 100_001, "less_than_equal", "le", 100_000, monkeypatch)
    returned = (
        None
        if isinstance(actual, ValidationError)
        else actual.idempotency_max_open_records_per_actor
    )
    assert isinstance(actual, ValidationError), (
        "Settings must reject an open-idempotency-record quota "
        "above one hundred thousand per actor; "
        f"actual returned idempotency_max_open_records_per_actor: {returned!r}"
    )
