"""Regression coverage for bounded operations and production secret delivery."""

import asyncio
import os
import shutil
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
import yaml
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import metrics
from app.core.config import MigrationSettings, Settings, get_settings
from app.db import get_sessionmaker
from app.models import ScreeningRun, TaskStatus
from app.services.retention import run_retention_sweep
from app.utils import utcnow

from .conftest import owner_with_farm
from .test_redteam_spine_fixes import _production_env
from .test_retention import _seed_chain, _seed_task, _settings, _table_counts

REPO = Path(__file__).resolve().parents[2]


def _scrubbed_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith("GOATFARM_")}


@pytest.mark.parametrize(
    ("direction", "revision_range", "blocked"),
    [
        ("upgrade", "base:head", "c3d4e5f6a7b1"),
        ("upgrade", "b9c0d1e2f3a4:cad1e2f3a4b5", "cad1e2f3a4b5"),
        ("downgrade", "d4e5f6a7b8c9:d3f4a5b6c7d8", "d4e5f6a7b8c9"),
        ("downgrade", "f3d4e5f6a7b8:f2c3d4e5f6a7", "f3d4e5f6a7b8"),
    ],
)
def test_online_only_historical_ranges_fail_before_partial_sql(
    direction: str, revision_range: str, blocked: str
) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", direction, revision_range, "--sql"],
        cwd=REPO / "backend",
        env=_scrubbed_env(),
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode != 0
    assert blocked in result.stderr
    assert "online-only" in result.stderr
    for sql in ("BEGIN;", "CREATE TABLE", "ALTER TABLE", "INSERT INTO", "-- Running"):
        assert sql not in result.stdout


def test_migration_settings_do_not_require_metrics_or_api_secrets() -> None:
    assert MigrationSettings(_env_file=None).environment == "development"  # type: ignore[call-arg]


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_msg91_credentials_fail_at_boot_and_provider_construction(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    from app.services.notifications.providers import build_notification_provider

    _production_env(monkeypatch)
    with pytest.raises(ValueError, match="MSG91_AUTH_KEY"):
        Settings(
            _env_file=None,  # type: ignore[call-arg]
            notifications_enabled=True,
            notifications_provider="msg91",
            msg91_auth_key=SecretStr(value),
        )
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        environment="development",
        notifications_provider="msg91",
        msg91_auth_key=SecretStr(value),
    )
    with pytest.raises(ValueError, match="MSG91_AUTH_KEY"):
        build_notification_provider(settings)


def test_backup_exported_classifications_keep_private_ca_from_pinned_dotenv(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copyfile(REPO / "backend/scripts/dotenv_value.py", scripts / "dotenv_value.py")
    (tmp_path / ".env").write_text("GOATFARM_DB_SSLROOTCERT_PATH=/fixture/private-ca.pem\n")
    env = _scrubbed_env()
    env.update({"GOATFARM_ENVIRONMENT": "production", "GOATFARM_DB_SSLMODE": "verify-full"})
    command = [
        "bash",
        "-c",
        'SCRIPT_DIR="$1"; PYTHON_BIN="$2"; source "$3"; '
        'load_app_safety_settings; printf "%s\\n" "$DB_SSLROOTCERT_PATH"',
        "fixture",
        str(scripts),
        sys.executable,
        str(REPO / "backend/scripts/backup_env.sh"),
    ]
    result = subprocess.run(command, env=env, capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "/fixture/private-ca.pem"
    env["GOATFARM_DB_SSLROOTCERT_PATH"] = "/fixture/exported-ca.pem"
    result = subprocess.run(command, env=env, capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "/fixture/exported-ca.pem"


def test_secret_guard_respects_empty_overrides_and_rejects_shared_directories(
    tmp_path: Path,
) -> None:
    envfile = tmp_path / "deployment.env"
    plain = {
        "GOATFARM_DATABASE_URL": "postgresql+asyncpg://api:fixture@db/farm",
        "GOATFARM_MIGRATION_DATABASE_URL": "postgresql+asyncpg://ddl:fixture@db/farm",
        "GOATFARM_WORKER_DATABASE_URL": "postgresql+asyncpg://worker:fixture@db/farm",
        "GOATFARM_IDEMPOTENCY_REQUEST_HMAC_SECRET": "fixture" * 8,
    }
    envfile.write_text("".join(f"{k}={v}\n" for k, v in plain.items()))
    env = _scrubbed_env()
    env.update({"GOATFARM_DATABASE_URL": "", "GOATFARM_DATABASE_URL_FILE": "/run/secrets/app/db"})
    command = [sys.executable, str(REPO / "backend/scripts/compose_env_guard.py"), str(envfile)]
    result = subprocess.run(command, env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    envfile.write_text(
        "".join(f"{k}={v}\n" for k, v in plain.items())
        + "GOATFARM_DATABASE_URL_FILE=/run/secrets/app/stale\n"
    )
    env.update(
        {"GOATFARM_DATABASE_URL": plain["GOATFARM_DATABASE_URL"], "GOATFARM_DATABASE_URL_FILE": ""}
    )
    result = subprocess.run(command, env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    for extra, message in (
        ({"GOATFARM_APP_SECRET_DIR": "/legacy/shared"}, "is retired"),
        (
            {"GOATFARM_API_SECRET_DIR": "/shared", "GOATFARM_MIGRATION_SECRET_DIR": "/shared"},
            "distinct",
        ),
        (
            {"GOATFARM_API_SECRET_DIR": "/shared", "GOATFARM_WORKER_SECRET_DIR": "/shared/worker"},
            "contain",
        ),
    ):
        result = subprocess.run(
            command, env={**env, **extra}, capture_output=True, text=True, check=False
        )
        assert result.returncode == 2
        assert message in result.stderr


async def test_private_metrics_authentication_and_collection_are_independent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.main import create_app

    token = "metrics-fixture-independent-secret-000000001"
    path = tmp_path / "metrics-secret"
    path.write_text(token)
    monkeypatch.setenv("GOATFARM_METRICS_PUBLIC_ENABLED", "false")
    monkeypatch.setenv("GOATFARM_METRICS_BEARER_TOKEN_FILE", str(path))
    get_settings.cache_clear()
    try:
        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.get("/metrics")).status_code == 401
            assert (
                await client.get("/metrics", headers={"Authorization": "Bearer wrong"})
            ).status_code == 401
            assert (
                await client.get(
                    "/metrics",
                    headers=[
                        ("Authorization", f"Bearer {token}"),
                        ("Authorization", f"Bearer {token}"),
                    ],
                )
            ).status_code == 401
            before = metrics.MAINTENANCE_ROWS.labels(loop="retention")._value.get()
            metrics.record_maintenance_batch("retention", 7)
            response = await client.get("/metrics", headers={"Authorization": f"Bearer {token}"})
            assert response.status_code == 200
            assert metrics.MAINTENANCE_ROWS.labels(loop="retention")._value.get() == before + 7
            assert token not in response.text
    finally:
        get_settings.cache_clear()
    _production_env(monkeypatch)
    get_settings.cache_clear()
    try:
        settings = Settings(_env_file=None)  # type: ignore[call-arg]
        assert settings.metrics_enabled is True
        assert settings.metrics_public_enabled is False
        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://farm.example.com"
        ) as client:
            assert (await client.get("/metrics")).status_code == 401
            response = await client.get("/metrics", headers={"Authorization": f"Bearer {token}"})
            assert response.status_code == 200
            assert "goatfarm_maintenance_loop_rows_total" in response.text
        monkeypatch.delenv("GOATFARM_METRICS_BEARER_TOKEN_FILE")
        get_settings.cache_clear()
        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://farm.example.com"
        ) as client:
            assert (await client.get("/metrics")).status_code == 404
            assert get_settings().metrics_enabled is True
    finally:
        get_settings.cache_clear()


def test_release_rerun_refuses_to_replace_an_immutable_version(tmp_path: Path) -> None:
    workflow = yaml.safe_load((REPO / ".github/workflows/release.yml").read_text())
    step = next(
        step
        for step in workflow["jobs"]["release"]["steps"]
        if step["name"] == "Create signed checksums and immutable release"
    )
    bindir = tmp_path / "bin"
    bindir.mkdir()
    gh = bindir / "gh"
    gh.write_text(
        f"#!{sys.executable}\nimport sys\n"
        "raise SystemExit(0 if sys.argv[1:3] == ['release', 'view'] else 91)\n"
    )
    gh.chmod(0o700)
    env = {
        **os.environ,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "GITHUB_REF_NAME": "v1.2.3",
    }
    result = subprocess.run(
        ["bash", "-eu", "-c", step["run"]],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "Release v1.2.3 already exists and is immutable" in result.stdout
    assert "release upload" not in step["run"]
    assert "release edit" not in step["run"]
    assert "--clobber" not in step["run"]


async def test_retention_pages_distinct_farms_without_draining_one_tenant(
    client: httpx.AsyncClient,
) -> None:
    farms = [
        int((await owner_with_farm(client, email=f"retention-budget-{i}@farm.in"))["X-Farm-Id"])
        for i in range(3)
    ]
    async with get_sessionmaker()() as db:
        for farm_id in farms:
            for i in range(7 if farm_id == farms[0] else 1):
                await _seed_task(
                    db,
                    farm_id=farm_id,
                    title=f"old {i}",
                    status=TaskStatus.DONE.value,
                    completed_at=utcnow() - timedelta(days=400),
                )
        await db.commit()
    settings = _settings(
        retention_farm_batch_size=1, retention_delete_batch_size=2, retention_max_batches_per_farm=1
    )
    cursor = 0
    for i, farm_id in enumerate(farms):
        async with get_sessionmaker()() as db:
            summary = await run_retention_sweep(db, settings, after_farm_id=cursor)
        assert summary.last_farm_id == farm_id
        assert summary.exhausted is (i == 2)
        assert summary.terminal_tasks == (2 if i == 0 else 1)
        cursor = summary.last_farm_id
    assert (await _table_counts(farms[0]))["tasks"] == 5
    assert (await _table_counts(farms[-1]))["tasks"] == 0


async def test_retention_child_lock_rolls_back_prior_deletes_and_serves_later_farm(
    client: httpx.AsyncClient,
) -> None:
    farm = int((await owner_with_farm(client, email="retention-lock@farm.in"))["X-Farm-Id"])
    later = int((await owner_with_farm(client, email="retention-lock-later@farm.in"))["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        await _seed_chain(
            db, farm_id=farm, image_created_at=utcnow() - timedelta(days=200), key_suffix="locked"
        )
        await _seed_task(
            db,
            farm_id=later,
            title="old",
            status=TaskStatus.DONE.value,
            completed_at=utcnow() - timedelta(days=400),
        )
        await db.commit()
    async with get_sessionmaker()() as locker:
        await locker.execute(
            select(ScreeningRun.id).where(ScreeningRun.farm_id == farm).limit(1).with_for_update()
        )
        async with get_sessionmaker()() as db:
            summary = await asyncio.wait_for(
                run_retention_sweep(db, _settings(retention_lock_timeout_ms=100)), timeout=3
            )
        await locker.rollback()
    assert summary.failed_farms == 1
    assert summary.screening_findings == 0
    assert summary.screening_images == 0
    assert summary.terminal_tasks == 1
    assert (await _table_counts(farm))["findings"] == 2
    assert (await _table_counts(later))["tasks"] == 0


async def test_retention_commit_failure_does_not_count_uncommitted_deletions(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    farm = int((await owner_with_farm(client, email="retention-commit@farm.in"))["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        await _seed_task(
            db,
            farm_id=farm,
            title="old",
            status=TaskStatus.DONE.value,
            completed_at=utcnow() - timedelta(days=400),
        )
        await db.commit()
    real_commit = AsyncSession.commit
    calls = 0

    async def fail_cohort_commit(db: AsyncSession) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:  # discovery commit succeeds, first deletion cohort fails
            raise RuntimeError("fixture commit failed")
        await real_commit(db)

    monkeypatch.setattr(AsyncSession, "commit", fail_cohort_commit)
    async with get_sessionmaker()() as db:
        summary = await run_retention_sweep(db, _settings())
    assert summary.failed_farms == 1
    assert summary.total_deleted == 0
    assert (await _table_counts(farm))["tasks"] == 1
