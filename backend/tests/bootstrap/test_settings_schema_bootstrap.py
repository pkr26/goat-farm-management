"""The complete Settings module must build its supported schema before startup.

The same test also works after normal-suite application imports. A separate
bootstrap executor must prove an uncached import and actual pre-startup flags.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import traceback
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest


def test_complete_settings_schema_imports_and_constructs_supported_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = Path(__file__).resolve().parents[2]
    loaded_paths = {
        Path(module_file).resolve()
        for module in tuple(sys.modules.values())
        if isinstance(module_file := getattr(module, "__file__", None), str)
    }
    before_call = {
        "global_application_conftest_loaded": backend / "tests/conftest.py" in loaded_paths,
        "app_main_loaded": "app.main" in sys.modules,
        "app_security_loaded": "app.security" in sys.modules,
        "settings_module_cached": "app.core.config" in sys.modules,
    }
    for name in tuple(os.environ):
        if name.upper().startswith("GOATFARM_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    from pydantic_core import SchemaError

    configuration: ModuleType | None = None
    observed_schema_error: object = None
    try:
        configuration = importlib.import_module("app.core.config")
    except SchemaError as actual:
        actual_lines = tuple(line.strip() for line in str(actual).splitlines())
        expected_lines = (
            'Error building "function-after" validator:',
            'SchemaError: Error building "model" validator:',
            'SchemaError: Error building "model-fields" validator:',
            'SchemaError: Field "idempotency_retention_hours":',
            'SchemaError: Error building "default" validator:',
            'SchemaError: Error building "int" validator:',
            "ValueError: 'le' must be coercible to an integer",
        )
        frames = traceback.extract_tb(actual.__traceback__)
        if (
            type(actual) is not SchemaError
            or actual_lines != expected_lines
            or not any(
                Path(frame.filename).resolve() == backend / "app/core/config.py" for frame in frames
            )
        ):
            raise
        observed_schema_error = {
            "exception_type": f"{type(actual).__module__}.{type(actual).__name__}",
            "actual_message": str(actual),
            "actual_error_count": actual.error_count(),
            "actual_errors_repr": repr(actual.errors()),
            "actual_traceback_frames": [
                {"file": frame.filename, "line": frame.lineno, "name": frame.name}
                for frame in frames
            ],
        }

    print(
        "CP4357_REAL_SETTINGS_SCHEMA "
        + json.dumps(
            {
                "before_CALL": before_call,
                "complete_module_imported": configuration is not None,
                "actual_schema_error": observed_schema_error,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    assert configuration is not None, (
        "The supported complete Settings module must build its real schema; "
        f"actual schema error: {observed_schema_error!r}"
    )

    from app.core.config import Settings
    from tests.settings_helpers import settings_from_input

    actual_settings = settings_from_input(
        Settings,
        env_file=None,
        environment="development",
        database_url="postgresql+asyncpg://bootstrap@127.0.0.1:1/herdly_schema_unused_test",
    )
    assert type(actual_settings.idempotency_retention_hours) is int
    assert actual_settings.idempotency_retention_hours == 168
    dumped = actual_settings.model_dump()
    restored = settings_from_input(Settings, dumped, env_file=None)
    assert restored.model_dump() == dumped
