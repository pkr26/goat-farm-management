"""Complete supported settings models retain their provider and pool boundaries.

These tests construct real settings only. They never create an app, provider
client, engine, session or connection, and do not measure remote model support.
"""

import os
from collections.abc import Iterator, Mapping
from typing import Literal, cast

import pytest
from pydantic import ValidationError
from pydantic_settings import BaseSettings

from app.core.config import ScreeningRotationProvider, ScreeningWorkerSettings, Settings

from .settings_helpers import settings_from_input


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    """Constructor-only module: no shared database creation or migrations."""


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    """Constructor-only module: no shared database truncation."""


@pytest.fixture(autouse=True)
def _isolated_settings_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Restore the exact process environment even when an assertion fails."""
    with monkeypatch.context() as isolated:
        for name in tuple(os.environ):
            if name.startswith("GOATFARM_") or name in {"UVICORN_WORKERS", "WEB_CONCURRENCY"}:
                isolated.delenv(name)
        yield


type _ExpectedError = tuple[tuple[str | int, ...], str, dict[str, int], str | int]


def _construct(
    projection: type[BaseSettings],
    values: Mapping[str, object],
    *,
    expected_errors: tuple[_ExpectedError, ...] = (),
) -> BaseSettings | ValidationError:
    try:
        return settings_from_input(projection, values, env_file=None)
    except ValidationError as observed_error:
        # Only one declared boundary error may become an ordinary assertion
        # outcome. Controls and full reconstructions declare none. Unknown or
        # additional validation errors keep their actual exception/traceback.
        details = observed_error.errors(include_input=True, include_url=False)
        if len(details) != 1:
            raise
        detail = details[0]
        for location, kind, context, expected_input in expected_errors:
            actual_input = detail.get("input")
            if (
                detail["loc"] == location
                and detail["type"] == kind
                and detail.get("ctx") == context
                and type(actual_input) is type(expected_input)
                and actual_input == expected_input
            ):
                return observed_error
        raise


def _screening_values(
    *, model: str = "screening-model", name: str = "boundary-provider"
) -> dict[str, object]:
    return {
        "environment": "development",
        "database_url": "postgresql+asyncpg://boundary:boundary@127.0.0.1:1/config_boundary",
        "screening_enabled": True,
        "s3_bucket": "boundary-config-bucket",
        "s3_access_key_id": "test-owned-access-key",
        "s3_secret_access_key": "test-owned-secret-key",
        "screening_stale_processing_after_seconds": 1800,
        "screening_provider_timeout_seconds": 120,
        "screening_provider_rotation": [
            {
                "kind": "anthropic",
                "name": name,
                "model": model,
                "api_key": "test-owned-provider-key",
            }
        ],
    }


def _entry(settings: BaseSettings) -> ScreeningRotationProvider:
    # The cast changes typing only; the caller has required the actual
    # parametrized Settings or ScreeningWorkerSettings constructor result.
    complete = cast("Settings | ScreeningWorkerSettings", settings)
    entries = complete.screening_provider_rotation
    assert len(entries) == 1, "A complete one-provider configuration must retain its entry"
    return entries[0]


def _roundtrip(
    projection: type[BaseSettings], original: BaseSettings
) -> BaseSettings | ValidationError:
    # Python mode retains actual SecretStr values. A JSON dump would mask
    # them and would not reconstruct the same real configuration.
    return _construct(projection, original.model_dump(mode="python"))


@pytest.mark.parametrize("projection", [Settings, ScreeningWorkerSettings], ids=["api", "worker"])
def test_full_settings_reject_an_empty_rotation_model(
    projection: type[BaseSettings],
) -> None:
    control = _construct(projection, _screening_values())
    assert isinstance(control, projection), "A complete supported screening control must configure"
    assert _entry(control).model == "screening-model", (
        "The control must retain its model identifier"
    )
    reconstructed = _roundtrip(projection, control)
    assert isinstance(reconstructed, projection), (
        "A complete valid screening control must reconstruct"
    )
    assert _entry(reconstructed).model == "screening-model", (
        "Reconstruction must retain the control model"
    )

    invalid = _construct(
        projection,
        _screening_values(model=""),
        expected_errors=(
            (
                ("screening_provider_rotation", 0, "model"),
                "string_too_short",
                {"min_length": 1},
                "",
            ),
        ),
    )
    assert isinstance(invalid, ValidationError), (
        "A configured screening provider must reject an empty model identifier"
    )
    details = invalid.errors(include_input=False, include_url=False)
    assert [
        (detail["loc"], detail["type"], detail.get("ctx", {}).get("min_length"))
        for detail in details
    ] == [(("screening_provider_rotation", 0, "model"), "string_too_short", 1)], (
        "The configuration diagnostic must identify only the empty nested model"
    )


@pytest.mark.parametrize("projection", [Settings, ScreeningWorkerSettings], ids=["api", "worker"])
def test_full_settings_preserve_a_120_character_rotation_model(
    projection: type[BaseSettings],
) -> None:
    control = _construct(projection, _screening_values())
    assert isinstance(control, projection), "A complete supported screening control must configure"
    model = "model-" + "m" * 114
    accepted = _construct(
        projection,
        _screening_values(model=model),
        expected_errors=(
            (
                ("screening_provider_rotation", 0, "model"),
                "string_too_long",
                {"max_length": 119},
                model,
            ),
        ),
    )
    assert isinstance(accepted, projection), (
        "A supported 120-character model identifier must configure"
    )
    entry = _entry(accepted)
    assert entry.model == model, "The complete settings model must retain the supported identifier"
    reconstructed = _roundtrip(projection, accepted)
    assert isinstance(reconstructed, projection), (
        "A complete supported model configuration must reconstruct"
    )
    again = _entry(reconstructed)
    assert again.model == model, (
        "Full configuration reconstruction must preserve the model identifier"
    )
    assert again.api_key.get_secret_value() == entry.api_key.get_secret_value(), (
        "Full configuration reconstruction must preserve the actual test-owned provider key"
    )


@pytest.mark.parametrize("projection", [Settings, ScreeningWorkerSettings], ids=["api", "worker"])
def test_full_settings_preserve_a_40_character_provider_slug(
    projection: type[BaseSettings],
) -> None:
    control = _construct(projection, _screening_values())
    assert isinstance(control, projection), "A complete supported screening control must configure"
    name = "provider-" + "a" * 31
    accepted = _construct(
        projection,
        _screening_values(name=name),
        expected_errors=(
            (
                ("screening_provider_rotation", 0, "name"),
                "string_too_long",
                {"max_length": 39},
                name,
            ),
        ),
    )
    assert isinstance(accepted, projection), "A supported 40-character provider slug must configure"
    entry = _entry(accepted)
    assert entry.name == name, "The real slug validator must retain the supported normalized name"
    reconstructed = _roundtrip(projection, accepted)
    assert isinstance(reconstructed, projection), (
        "A complete supported provider configuration must reconstruct"
    )
    again = _entry(reconstructed)
    assert again.name == name, (
        "Full configuration reconstruction must preserve the normalized provider name"
    )
    assert again.api_key.get_secret_value() == entry.api_key.get_secret_value(), (
        "Full configuration reconstruction must preserve the actual test-owned provider key"
    )


def test_full_settings_accept_a_single_connection_without_overflow() -> None:
    values: dict[str, object] = {
        "environment": "development",
        "database_url": "postgresql+asyncpg://boundary:boundary@127.0.0.1:1/config_boundary",
    }
    control = _construct(Settings, values | {"db_pool_size": 2, "db_max_overflow": 1})
    assert isinstance(control, Settings), "A supported finite pool control must configure"
    accepted = _construct(
        Settings,
        values | {"db_pool_size": 1, "db_max_overflow": 0},
        expected_errors=(
            (("db_pool_size",), "greater_than_equal", {"ge": 2}, 1),
            (("db_max_overflow",), "greater_than_equal", {"ge": 1}, 0),
        ),
    )
    assert isinstance(accepted, Settings), (
        "A supported one-connection pool without overflow must configure"
    )
    assert (accepted.db_pool_size, accepted.db_max_overflow) == (1, 0), (
        "The real settings must retain one admitted connection and zero overflow"
    )
    reconstructed = _roundtrip(Settings, accepted)
    assert isinstance(reconstructed, Settings), "A supported one-connection pool must reconstruct"
    assert (reconstructed.db_pool_size, reconstructed.db_max_overflow) == (1, 0), (
        "Full settings reconstruction must preserve the configured pool bounds"
    )


@pytest.mark.parametrize("delivery", ["constructor", "environment"])
def test_full_settings_reject_a_zero_connection_pool(
    delivery: Literal["constructor", "environment"], monkeypatch: pytest.MonkeyPatch
) -> None:
    values: dict[str, object] = {
        "environment": "development",
        "database_url": "postgresql+asyncpg://boundary:boundary@127.0.0.1:1/config_boundary",
    }
    with monkeypatch.context() as supplied:
        if delivery == "environment":
            supplied.setenv("GOATFARM_DB_POOL_SIZE", "1")
            supplied.setenv("GOATFARM_DB_MAX_OVERFLOW", "0")
            control_values = values
        else:
            control_values = values | {"db_pool_size": 1, "db_max_overflow": 0}
        # Every ValidationError from the valid control or its reconstruction
        # propagates: neither call has an expected-error allowance.
        control = _construct(Settings, control_values)
        assert isinstance(control, Settings), "The valid one-connection control must configure"
        assert (control.db_pool_size, control.db_max_overflow) == (1, 0), (
            "The actual constructor must retain the finite one-connection control"
        )
        reconstructed = _roundtrip(Settings, control)
        assert isinstance(reconstructed, Settings), "The valid finite-pool control must reconstruct"
        assert (reconstructed.db_pool_size, reconstructed.db_max_overflow) == (1, 0), (
            "Full reconstruction must retain the valid finite-pool control"
        )

        expected_input: str | int
        if delivery == "environment":
            supplied.setenv("GOATFARM_DB_POOL_SIZE", "0")
            invalid_values = values
            # Preserve the actual process-environment input type in the real
            # Pydantic diagnostic; do not cast it to a typed constructor input.
            expected_input = "0"
        else:
            invalid_values = values | {"db_pool_size": 0, "db_max_overflow": 0}
            expected_input = 0
        invalid = _construct(
            Settings,
            invalid_values,
            expected_errors=((("db_pool_size",), "greater_than_equal", {"ge": 1}, expected_input),),
        )
        assert isinstance(invalid, ValidationError), (
            "A configured finite pool must reject zero admitted connections"
        )
        details = invalid.errors(include_input=True, include_url=False)
        assert [
            (detail["loc"], detail["type"], detail.get("ctx"), detail["input"])
            for detail in details
        ] == [(("db_pool_size",), "greater_than_equal", {"ge": 1}, expected_input)], (
            "The configuration diagnostic must identify only the zero pool-size boundary"
        )
