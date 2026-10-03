"""Typed boundary for intentionally raw settings-constructor test inputs."""

from collections.abc import Mapping
from typing import Protocol, cast

from pydantic_settings import BaseSettings
from pydantic_settings.sources import ENV_FILE_SENTINEL, DotenvType


class _SettingsConstructor[T: BaseSettings](Protocol):
    def __call__(self, *, _env_file: DotenvType | None, **values: object) -> T: ...


def settings_from_input[T: BaseSettings](
    settings_type: type[T],
    inputs: Mapping[str, object] | None = None,
    /,
    *,
    env_file: DotenvType | None = ENV_FILE_SENTINEL,
    **values: object,
) -> T:
    """Use the real inherited constructor, including all environment sources.

    Pydantic's generated field-only static signature omits BaseSettings'
    runtime ``_env_file`` argument. This narrow constructor interface exposes
    that documented argument and accepts malformed raw inputs deliberately
    tested by the boot-validation suite. Pydantic still validates every input.
    """
    constructor = cast(_SettingsConstructor[T], settings_type)
    fields = dict(inputs or {}) | values
    return constructor(_env_file=env_file, **fields)
