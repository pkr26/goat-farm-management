"""Frozen defaults regression for every BaseSettings class.

The 2026-09-30 mutation campaign showed the shipped settings defaults were
almost entirely unpinned: any ``Field(default=N)`` integer could drift by ±1
(and defaults like ``24 * 7`` could silently become ``24 + 7``) without a
single test failing, because every test that exercises a setting sets it via
env explicitly. These tables freeze today's shipped defaults so changing one
is a conscious act that touches this file.

Regenerating after an *intentional* change: scrub ``GOATFARM_*`` from the
environment, construct the class with ``_env_file=None``, and paste the new
values into the table (keep every field listed — the test fails on unknown
fields on purpose so new settings cannot land untested).
"""

import os
from typing import Any

import pytest

from app.core import config
from app.core.config import (
    DEVELOPMENT_IDEMPOTENCY_HMAC_SECRET,
    MIN_IDEMPOTENCY_HMAC_SECRET_LENGTH,
    MigrationSettings,
    ScreeningWorkerSettings,
    Settings,
)

SETTINGS_DEFAULTS: dict[str, object] = {
    "access_token_ttl_seconds": 1800,
    "allowed_hosts": ["localhost", "127.0.0.1", "test", "testserver"],
    "argon2_hash_len": 32,
    "argon2_memory_cost": 65536,
    "argon2_parallelism": 4,
    "argon2_time_cost": 3,
    "argon2_worker_threads": 2,
    "auth_rate_limit_enabled": True,
    "auth_rate_limit_max_attempts": 10,
    "auth_rate_limit_window_seconds": 300,
    "cadence_materialization_farm_batch_size": 100,
    "cadence_materialization_interval_seconds": 300,
    "cadence_materialization_max_batches": 10,
    "cookie_secure": False,
    "cors_origins": ["http://localhost:3000", "http://127.0.0.1:3000"],
    "database_url": "postgresql+asyncpg://localhost:5432/goatfarm",
    "database_url_file": None,
    "db_max_overflow": 10,
    "db_pool_size": 5,
    "db_pool_timeout": 30,
    "db_sslmode": "disable",
    "db_sslrootcert_path": None,
    "db_statement_timeout_ms": 30000,
    "deleted_membership_cleanup_batch_size": 500,
    "deleted_membership_cleanup_interval_seconds": 60,
    "deleted_membership_cleanup_max_batches": 10,
    "environment": "development",
    "idempotency_cleanup_batch_size": 500,
    "idempotency_cleanup_interval_seconds": 3600,
    "idempotency_cleanup_max_batches": 10,
    "idempotency_max_open_records_per_actor": 1000,
    "idempotency_request_hmac_previous_secrets": [],
    "idempotency_request_hmac_secret_file": None,
    "idempotency_retention_hours": 168,
    "inactive_animal_task_cleanup_batch_size": 500,
    "inactive_animal_task_cleanup_interval_seconds": 60,
    "inactive_animal_task_cleanup_max_batches": 10,
    "jwt_algorithm": "RS256",
    "jwt_audience": "goatfarm-web",
    "jwt_issuer": "goatfarm-api",
    "jwt_previous_public_key_paths": [],
    "legacy_repair_farm_batch_size": 25,
    "legacy_repair_interval_seconds": 3600,
    "legacy_repair_max_batches": 10,
    "legacy_repair_task_batch_size": 500,
    "max_account_affiliations_per_response": 500,
    "max_farms_per_user": 10,
    "max_pending_manual_tasks_per_farm": 5000,
    "max_planner_plans_per_farm": 25,
    "max_request_body_bytes": 1048576,
    "max_request_target_bytes": 8192,
    "max_roles_per_farm": 50,
    "max_simulation_scenarios_per_farm": 25,
    "max_team_members_per_farm": 200,
    "metrics_enabled": True,
    "metrics_public_enabled": True,
    "metrics_bearer_token": None,
    "metrics_bearer_token_file": None,
    "migration_database_url": None,
    "migration_database_url_file": None,
    "migration_statement_timeout_ms": 900000,
    "min_password_length": 8,
    "msg91_auth_key": None,
    "msg91_auth_key_file": None,
    "msg91_sender_id": "HERDLY",
    "msg91_template_id": None,
    "notifications_digest_hour": 6,
    "notifications_digest_minute": 30,
    "notifications_enabled": False,
    "notifications_farm_daily_cap": 50,
    "notifications_loop_batch_size": 100,
    "notifications_delivery_concurrency": 8,
    "notifications_per_farm_delivery_concurrency": 2,
    "notifications_provider": "console",
    "notifications_quiet_end_hour": 6,
    "notifications_quiet_start_hour": 21,
    "notifications_send_retry_attempts": 2,
    "notifications_send_retry_backoff_seconds": 2.0,
    "rate_limit_backend": "memory",
    "refresh_cookie_name": "goatfarm_refresh",
    "refresh_max_families_per_user": 10,
    "refresh_max_sessions_per_family": 1024,
    "refresh_reuse_grace_seconds": 3,
    "refresh_session_cleanup_batch_size": 500,
    "refresh_session_cleanup_interval_seconds": 3600,
    "refresh_session_cleanup_max_batches": 10,
    "refresh_token_ttl_seconds": 1209600,
    "rejected_login_pbkdf2_work_budget": 50000,
    "retention_delete_batch_size": 500,
    "retention_farm_batch_size": 100,
    "retention_max_batches_per_farm": 4,
    "retention_lock_timeout_ms": 250,
    "retention_statement_timeout_ms": 5000,
    "retention_screening_days": 180,
    "retention_screening_batch_days": 365,
    "retention_screening_budget_days": 90,
    "retention_notification_days": 400,
    "retention_sweep_enabled": True,
    "retention_sweep_interval_seconds": 86400,
    "retention_terminal_task_days": 365,
    "s3_access_key_id": None,
    "s3_access_key_id_file": None,
    "s3_bucket": None,
    "s3_endpoint_url": None,
    "s3_region": "us-east-1",
    "s3_secret_access_key": None,
    "s3_secret_access_key_file": None,
    "screening_anthropic_api_key": None,
    "screening_anthropic_api_key_file": None,
    "screening_anthropic_base_url": "https://api.anthropic.com",
    "screening_anthropic_model": "claude-sonnet-4-5",
    "screening_crop_detection_enabled": True,
    "screening_daily_call_budget_per_farm": 400,
    "screening_enabled": False,
    "screening_image_max_edge_px": 1568,
    "screening_max_crops_per_image": 8,
    "screening_max_images_per_cycle": 50,
    "screening_openai_api_key": None,
    "screening_openai_api_key_file": None,
    "screening_openai_base_url": "https://api.openai.com/v1",
    "screening_openai_model": "gpt-5",
    "screening_poll_interval_seconds": 300,
    "screening_presign_expiry_seconds": 900,
    "screening_provider": "anthropic",
    "screening_provider_rotation": [],
    "screening_provider_timeout_seconds": 120,
    "screening_raw_cleanup_batch_size": 25,
    "screening_raw_cleanup_interval_seconds": 60,
    "screening_raw_cleanup_max_batches": 4,
    "screening_s3_prefix": "raw",
    "screening_stale_processing_after_seconds": 1800,
    "screening_worker_health_max_age_seconds": 900,
    "screening_worker_max_consecutive_cycle_failures": 3,
    "totp_encryption_key": None,
    "totp_encryption_key_file": None,
    "totp_encryption_previous_keys": [],
    "trusted_proxy_hosts": "",
    "worker_pin_min_length": 4,
    "worker_pin_rate_limit_max_attempts": 10,
    "worker_pin_rate_limit_window_seconds": 300,
    "worker_roster_enabled": False,
}

MIGRATION_DEFAULTS: dict[str, object] = {
    "database_url": "postgresql+asyncpg://localhost:5432/goatfarm",
    "database_url_file": None,
    "db_sslmode": "disable",
    "db_sslrootcert_path": None,
    "environment": "development",
    "migration_database_url": None,
    "migration_database_url_file": None,
    "migration_statement_timeout_ms": 900000,
    "migration_writes_quiesced": False,
}

WORKER_DEFAULTS: dict[str, object] = {
    "database_url": "postgresql+asyncpg://localhost:5432/goatfarm",
    "database_url_file": None,
    "db_max_overflow": 2,
    "db_pool_size": 2,
    "db_pool_timeout": 30,
    "db_sslmode": "disable",
    "db_sslrootcert_path": None,
    "db_statement_timeout_ms": 30000,
    "environment": "development",
    "metrics_bearer_token": None,
    "metrics_bearer_token_file": None,
    "metrics_enabled": True,
    "s3_access_key_id": None,
    "s3_access_key_id_file": None,
    "s3_bucket": None,
    "s3_endpoint_url": None,
    "s3_region": "us-east-1",
    "s3_secret_access_key": None,
    "s3_secret_access_key_file": None,
    "screening_anthropic_api_key": None,
    "screening_anthropic_api_key_file": None,
    "screening_anthropic_base_url": "https://api.anthropic.com",
    "screening_anthropic_model": "claude-sonnet-4-5",
    "screening_crop_detection_enabled": True,
    "screening_daily_call_budget_per_farm": 400,
    "screening_enabled": False,
    "screening_image_max_edge_px": 1568,
    "screening_max_crops_per_image": 8,
    "screening_max_images_per_cycle": 50,
    "screening_openai_api_key": None,
    "screening_openai_api_key_file": None,
    "screening_openai_base_url": "https://api.openai.com/v1",
    "screening_openai_model": "gpt-5",
    "screening_poll_interval_seconds": 300,
    "screening_presign_expiry_seconds": 900,
    "screening_provider": "anthropic",
    "screening_provider_rotation": [],
    "screening_provider_timeout_seconds": 120,
    "screening_s3_prefix": "raw",
    "screening_stale_processing_after_seconds": 1800,
    "screening_worker_health_max_age_seconds": 900,
    "screening_worker_max_consecutive_cycle_failures": 3,
}

# Fields whose defaults are machine-dependent (home/cache/temp directories) or
# secret constants — pinned structurally in dedicated tests below rather than
# by literal value here.
STRUCTURAL_FIELDS = {
    "jwt_private_key_path",
    "jwt_public_key_path",
    "screening_worker_heartbeat_path",
    "idempotency_request_hmac_secret",  # pinned against its constant below
}


@pytest.fixture(autouse=True)
def _scrub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ambient GOATFARM_* leakage: pin defaults, not conftest's overrides."""
    for name in [k for k in os.environ if k.startswith("GOATFARM_")]:
        monkeypatch.delenv(name)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)


def _assert_frozen(cls: Any, table: dict[str, object], structural: set[str]) -> Any:
    settings = cls(_env_file=None)
    fields = set(type(settings).model_fields)
    unknown_table_keys = set(table) - fields
    missing = fields - set(table) - structural
    assert not unknown_table_keys, f"table lists non-existent fields: {sorted(unknown_table_keys)}"
    assert not missing, (
        f"{cls.__name__} fields missing from the frozen table: {sorted(missing)} — "
        "pin them (or add to STRUCTURAL_FIELDS with a dedicated assertion)"
    )
    for name, expected in table.items():
        assert getattr(settings, name) == expected, f"{cls.__name__}.{name} default changed"
    return settings


def test_settings_defaults_frozen() -> None:
    _assert_frozen(Settings, SETTINGS_DEFAULTS, STRUCTURAL_FIELDS)


def test_migration_settings_defaults_frozen() -> None:
    _assert_frozen(MigrationSettings, MIGRATION_DEFAULTS, set())


def test_screening_worker_settings_defaults_frozen() -> None:
    _assert_frozen(ScreeningWorkerSettings, WORKER_DEFAULTS, {"screening_worker_heartbeat_path"})


def test_settings_security_module_constants() -> None:
    # Rotation-list ceilings and minimum lengths are guardrails imported all
    # over the API; pin them literally so a ±1 drift fails here first.
    assert config.MAX_PREVIOUS_JWT_PUBLIC_KEYS == 3
    assert config.MAX_PREVIOUS_IDEMPOTENCY_HMAC_SECRETS == 3
    assert config.MAX_PREVIOUS_TOTP_ENCRYPTION_KEYS == 3
    assert config.MIN_IDEMPOTENCY_HMAC_SECRET_LENGTH == 32
    assert config.TOTP_ENCRYPTION_KEY_BYTES == 32


def test_settings_secret_and_path_defaults() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    # The development HMAC fallback must be the exact committed constant and
    # must itself satisfy the minimum-length invariant it is validated against.
    assert settings.idempotency_request_hmac_secret.get_secret_value() == (
        DEVELOPMENT_IDEMPOTENCY_HMAC_SECRET
    )
    assert len(DEVELOPMENT_IDEMPOTENCY_HMAC_SECRET) >= MIN_IDEMPOTENCY_HMAC_SECRET_LENGTH
    assert len(DEVELOPMENT_IDEMPOTENCY_HMAC_SECRET) == 50
    # JWT keys default into the user cache under goatfarm/keys (or the
    # legacy repo keys/ dir when present) — never the repo root itself.
    assert settings.jwt_private_key_path.name == "jwt_private.pem"
    assert settings.jwt_public_key_path.name == "jwt_public.pem"
    assert settings.jwt_private_key_path.parent == settings.jwt_public_key_path.parent
    assert settings.jwt_private_key_path.parent.name == "keys"
    # Heartbeat file lives in the system temp dir, not the repo.
    import tempfile
    from pathlib import Path

    heartbeat = settings.screening_worker_heartbeat_path
    assert heartbeat.parent == Path(tempfile.gettempdir())
    assert heartbeat.name.startswith("goatfarm-screening-worker")
