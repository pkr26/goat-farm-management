"""The supported seven-day Settings default constructs in either collection context.

Real settings imports occur inside the CALL. Report actual prior startup state;
only a separately bounded bootstrap executor may claim pre-conftest evidence.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest


def test_seven_day_settings_default_constructs_and_roundtrips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = Path(__file__).resolve().parents[2]
    loaded_paths = {
        Path(module_file).resolve()
        for module in tuple(sys.modules.values())
        if isinstance(module_file := getattr(module, "__file__", None), str)
    }
    conftest_loaded_before_call = backend / "tests/conftest.py" in loaded_paths
    app_main_loaded_before_call = "app.main" in sys.modules
    app_security_loaded_before_call = "app.security" in sys.modules

    from pydantic import ValidationError

    from app.core.config import Settings
    from tests.settings_helpers import settings_from_input

    for name in tuple(os.environ):
        if name.upper().startswith("GOATFARM_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    inputs = {
        "environment": "development",
        "database_url": "postgresql+asyncpg://bootstrap@127.0.0.1:1/herdly_bootstrap_unused_test",
    }

    explicit = settings_from_input(Settings, inputs, env_file=None, idempotency_retention_hours=168)
    assert type(explicit.idempotency_retention_hours) is int
    assert explicit.idempotency_retention_hours == 168
    explicit_dump = explicit.model_dump()
    assert type(explicit_dump["idempotency_retention_hours"]) is int
    assert explicit_dump["idempotency_retention_hours"] == 168
    explicit_restored = settings_from_input(Settings, explicit_dump, env_file=None)
    assert explicit_restored.model_dump() == explicit_dump

    default_settings: Settings | None = None
    observed_errors: object = None
    try:
        default_settings = settings_from_input(Settings, inputs, env_file=None)
    except ValidationError as actual:
        errors = actual.errors(include_url=False)
        if (
            actual.title != "Settings"
            or len(errors) != 1
            or errors[0]["loc"] != ("idempotency_retention_hours",)
            or errors[0]["type"] != "int_from_float"
            or type(errors[0].get("input")) is not float
            or errors[0]["input"] != 24 / 7
        ):
            raise
        observed_errors = errors

    print(
        "CP4352_REAL_RETENTION_DEFAULT "
        + json.dumps(
            {
                "complete_model": Settings.__name__,
                "default_constructed": default_settings is not None,
                "actual_retention_hours": (
                    default_settings.idempotency_retention_hours
                    if default_settings is not None
                    else None
                ),
                "actual_validation_errors": observed_errors,
                "global_application_conftest_loaded_before_CALL": conftest_loaded_before_call,
                "app_main_loaded_before_CALL": app_main_loaded_before_call,
                "app_security_loaded_before_CALL": app_security_loaded_before_call,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    assert default_settings is not None, (
        "The supported seven-day Settings default must construct; "
        f"actual constructor errors: {observed_errors!r}"
    )
    assert type(default_settings.idempotency_retention_hours) is int
    assert default_settings.idempotency_retention_hours == 168
    default_dump = default_settings.model_dump()
    assert type(default_dump["idempotency_retention_hours"]) is int
    assert default_dump["idempotency_retention_hours"] == 168
    default_restored = settings_from_input(Settings, default_dump, env_file=None)
    assert default_restored.model_dump() == default_dump
