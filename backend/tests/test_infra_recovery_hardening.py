"""Isolated evidence for database/deployment/recovery hardening.

These tests never connect to PostgreSQL, object storage, GitHub, or a container
registry. They exercise the recovery artifacts with temporary local files and
the production singleton lease with a fake SQLAlchemy connection.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
import yaml
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncConnection

from app import main as main_module
from app.core.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
RECOVERY_INVENTORY = BACKEND_DIR / "scripts" / "recovery_inventory.py"
BACKUP_FRESHNESS = BACKEND_DIR / "scripts" / "check_backup_freshness.py"
KEY_MATERIAL = (
    "jwt_private",
    "jwt_public",
    "totp_encryption",
    "idempotency_hmac",
    "database_ca",
    "backup_gpg",
)


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    """Override the integration fixture: this module must never touch a DB."""


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    """Override the global per-test truncation fixture for this module."""


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *arguments],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )


def _capture_inventory(tmp_path: Path) -> Path:
    manifest = tmp_path / "screening-object-manifest.jsonl"
    manifest.write_text('{"key":"raw/1/photo.jpg","version":"v1"}\n')
    inventory = tmp_path / "recovery-unbound.json"
    command = [
        str(RECOVERY_INVENTORY),
        "capture",
        "--output",
        str(inventory),
        "--object-bucket",
        "goatfarm-screening-replica",
        "--object-prefix",
        "raw/",
        "--object-recovery-point",
        "replica-snapshot-2026-10-04",
        "--object-restore-receipt",
        "quarterly-drill-2026-q4",
        "--object-manifest",
        str(manifest),
    ]
    for name in KEY_MATERIAL:
        command.extend(("--identity", f"{name}=secret-manager-version-{name}"))
        command.extend(("--escrow-receipt", f"{name}=vault-receipt-{name}"))
    result = _run(*command)
    assert result.returncode == 0, result.stderr
    assert inventory.stat().st_mode & 0o777 == 0o600
    return inventory


def _bind_inventory(source: Path, archive: Path) -> Path:
    bound = archive.with_name(f"{archive.name}.recovery.json")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    result = _run(
        str(RECOVERY_INVENTORY),
        "bind",
        "--inventory",
        str(source),
        "--output",
        str(bound),
        "--archive-name",
        archive.name,
        "--archive-sha256",
        digest,
    )
    assert result.returncode == 0, result.stderr
    return bound


def test_recovery_inventory_binds_database_objects_and_key_custody(tmp_path: Path) -> None:
    source = _capture_inventory(tmp_path)
    payload = json.loads(source.read_text())
    assert set(payload["key_material"]) == set(KEY_MATERIAL)
    assert "secret-manager-version" not in source.read_text()
    assert payload["screening_objects"]["versioning"] == "Enabled"

    archive = tmp_path / "goatfarm-2026-10-04T00-00-00Z-test.dump.gpg"
    archive.write_bytes(b"authenticated encrypted database artifact")
    bound = _bind_inventory(source, archive)
    verified = _run(
        str(RECOVERY_INVENTORY),
        "verify",
        "--inventory",
        str(bound),
        "--archive",
        str(archive),
    )
    assert verified.returncode == 0, verified.stderr

    archive.write_bytes(b"substituted database artifact")
    rejected = _run(
        str(RECOVERY_INVENTORY),
        "verify",
        "--inventory",
        str(bound),
        "--archive",
        str(archive),
    )
    assert rejected.returncode == 2
    assert "does not match" in rejected.stderr


def test_backup_freshness_requires_valid_checksum_and_inventory(tmp_path: Path) -> None:
    source = _capture_inventory(tmp_path)
    archive = tmp_path / "goatfarm-2026-10-04T00-00-00Z-test.dump.gpg"
    archive.write_bytes(b"fresh database artifact")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_name(f"{archive.name}.sha256").write_text(f"{digest}  {archive.name}\n")
    _bind_inventory(source, archive)
    metric = tmp_path / "metrics" / "goatfarm_backup.prom"

    fresh = _run(
        str(BACKUP_FRESHNESS),
        str(tmp_path),
        "--max-age-hours",
        "26",
        "--textfile",
        str(metric),
    )
    assert fresh.returncode == 0, fresh.stderr
    assert "goatfarm_backup_fresh 1" in metric.read_text()
    assert "goatfarm_backup_recovery_timestamp_seconds " in metric.read_text()
    assert "goatfarm_backup_invalid_sets 0" in metric.read_text()

    archive.write_bytes(b"tampered after sidecars were published")
    invalid = _run(
        str(BACKUP_FRESHNESS),
        str(tmp_path),
        "--max-age-hours",
        "26",
        "--textfile",
        str(metric),
    )
    assert invalid.returncode == 2
    assert "archive checksum mismatch" in invalid.stderr
    assert "goatfarm_backup_fresh 0" in metric.read_text()
    assert "goatfarm_backup_invalid_sets 1" in metric.read_text()


def test_backup_freshness_ignores_a_touched_archive_mtime(tmp_path: Path) -> None:
    source = _capture_inventory(tmp_path)
    archive = tmp_path / "goatfarm-2026-10-02T00-00-00Z-old.dump.gpg"
    archive.write_bytes(b"old but otherwise valid database artifact")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_name(f"{archive.name}.sha256").write_text(f"{digest}  {archive.name}\n")
    bound = _bind_inventory(source, archive)

    payload = json.loads(bound.read_text())
    payload["generated_at"] = (datetime.now(UTC) - timedelta(hours=30)).isoformat()
    bound.write_text(json.dumps(payload))
    # This is the precise false-green regression: mutable filesystem metadata
    # says "now", while the verified recovery inventory says the set is old.
    os.utime(archive, None)

    metric = tmp_path / "metrics" / "goatfarm_backup.prom"
    stale = _run(
        str(BACKUP_FRESHNESS),
        str(tmp_path),
        "--max-age-hours",
        "26",
        "--textfile",
        str(metric),
    )

    assert stale.returncode == 2
    assert "30.0h old" in stale.stderr
    assert "goatfarm_backup_fresh 0" in metric.read_text()


class _LeaseConnection:
    def __init__(self, *answers: bool | int) -> None:
        self.answers = list(answers)
        self.statements: list[str] = []
        self.commits = 0
        self.closed = False

    async def scalar(self, statement: object, parameters: object = None) -> bool | int:
        self.statements.append(str(statement))
        return self.answers.pop(0)

    async def commit(self) -> None:
        self.commits += 1

    async def close(self) -> None:
        self.closed = True


class _LeaseEngine:
    def __init__(self, connection: _LeaseConnection) -> None:
        self.connection = connection

    async def connect(self) -> AsyncConnection:
        # The fake implements the narrow connection surface exercised here.
        return cast(AsyncConnection, self.connection)


async def test_production_singleton_lease_is_held_and_released(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _LeaseConnection(True, 4242, True)
    monkeypatch.setattr(main_module, "get_engine", lambda: _LeaseEngine(connection))
    settings = cast(Settings, SimpleNamespace(environment="production"))

    held = await main_module._acquire_production_singleton_lease(settings)
    assert held is not None
    assert cast(object, held.connection) is connection
    assert held.backend_pid == 4242
    assert "pg_try_advisory_lock" in connection.statements[0]
    assert "pg_backend_pid" in connection.statements[1]
    assert connection.closed is False

    await main_module._release_production_singleton_lease(held)
    assert "pg_advisory_unlock" in connection.statements[2]
    assert connection.commits == 2
    assert connection.closed is True


async def test_second_production_replica_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _LeaseConnection(False)
    monkeypatch.setattr(main_module, "get_engine", lambda: _LeaseEngine(connection))
    settings = cast(Settings, SimpleNamespace(environment="production"))

    with pytest.raises(RuntimeError, match="another production API replica"):
        await main_module._acquire_production_singleton_lease(settings)
    assert connection.closed is True


async def test_singleton_watchdog_terminates_when_the_original_session_loses_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _LeaseConnection(False)
    lease = main_module._ProductionSingletonLease(
        connection=cast(AsyncConnection, connection), backend_pid=4242
    )
    terminated: list[bool] = []
    monkeypatch.setattr(
        main_module,
        "_terminate_after_singleton_lease_loss",
        lambda: terminated.append(True),
    )
    monkeypatch.setattr(main_module, "_production_singleton_lease_healthy", True)

    await main_module._production_singleton_lease_watchdog(lease, interval_seconds=0)

    assert terminated == [True]
    assert main_module._production_singleton_lease_healthy is False
    assert "pg_locks" in connection.statements[0]


async def test_readiness_fails_closed_after_singleton_lease_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module, "_production_singleton_lease_healthy", False)

    response = await main_module.readyz()

    assert isinstance(response, JSONResponse)
    assert response.status_code == 503


def test_alembic_online_mode_requires_an_explicit_migration_target() -> None:
    env = os.environ.copy()
    env.pop("GOATFARM_MIGRATION_DATABASE_URL", None)
    env.pop("GOATFARM_MIGRATION_DATABASE_URL_FILE", None)
    env["GOATFARM_DATABASE_URL"] = (
        "postgresql+asyncpg://localhost:5432/goatfarm_test_must_not_be_contacted"
    )
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "current"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode != 0
    assert "explicit GOATFARM_MIGRATION_DATABASE_URL" in result.stderr


def test_supported_deployment_and_release_artifacts_are_fail_closed() -> None:
    production = yaml.safe_load((REPO_ROOT / "docker-compose.production.yml").read_text())
    local = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())
    backend = production["services"]["backend"]
    edge = production["services"]["edge"]
    assert backend["deploy"]["replicas"] == 1
    assert edge["image"] == (
        "${GOATFARM_EDGE_IMAGE_REPOSITORY:?set the edge registry repository}"
        "@${GOATFARM_EDGE_IMAGE_DIGEST:?set its sha256 digest}"
    )
    assert local["services"]["edge"]["build"]["dockerfile"] == "docker/edge/Dockerfile"

    edge_dockerfile = (REPO_ROOT / "docker" / "edge" / "Dockerfile").read_text()
    assert "nginx:1.30.5-alpine3.24@sha256:" in edge_dockerfile
    assert "libexpat=2.8.5-r0" in edge_dockerfile
    assert "pcre2=10.49-r0" in edge_dockerfile
    security = (REPO_ROOT / ".github" / "workflows" / "security.yml").read_text()
    release = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text()
    assert "file: docker/edge/Dockerfile" in security
    assert "actions/workflows/security.yml/runs?head_sha=${TAGGED_SHA}" in release
    assert release.count("cosign sign --yes") == 3
    assert "cosign sign-blob --yes" in release
    assert "--format '{{json .Manifest}}'" in release
    assert "goatfarm-release-assembly-owned" in release
    assert "--clobber" not in release
    assert "gh release create" in release


def test_operations_and_governance_baseline_is_shipped() -> None:
    required = (
        "LICENSE",
        "SECURITY.md",
        "CONTRIBUTING.md",
        "THIRD_PARTY_NOTICES.md",
        ".github/CODEOWNERS",
        "ops/systemd/goatfarm-backup.service",
        "ops/systemd/goatfarm-backup.timer",
        "ops/systemd/goatfarm-backup-freshness.service",
        "ops/systemd/goatfarm-backup-freshness.timer",
        "ops/prometheus/prometheus.scrape.example.yml",
        "ops/prometheus/goatfarm.rules.yml",
    )
    for relative in required:
        assert (REPO_ROOT / relative).is_file(), relative
    assert "private" in (REPO_ROOT / "SECURITY.md").read_text().lower()
    assert '"license": "UNLICENSED"' in (REPO_ROOT / "frontend" / "package.json").read_text()
    assert (
        'license = { text = "Proprietary" }'
        in (REPO_ROOT / "backend" / "pyproject.toml").read_text()
    )
    backup_unit = (REPO_ROOT / "ops" / "systemd" / "goatfarm-backup.service").read_text()
    assert "StateDirectory=goatfarm-backup" in backup_unit
    assert "GNUPGHOME=/var/lib/goatfarm-backup/gnupg" in backup_unit
    assert "ReadOnlyPaths=/etc/goatfarm" in backup_unit
    freshness_unit = (
        REPO_ROOT / "ops" / "systemd" / "goatfarm-backup-freshness.service"
    ).read_text()
    assert "backend/.venv/bin/python" in freshness_unit
    rules = yaml.safe_load((REPO_ROOT / "ops" / "prometheus" / "goatfarm.rules.yml").read_text())
    alerts = {rule["alert"]: rule["expr"] for group in rules["groups"] for rule in group["rules"]}
    stale_expression = alerts["GoatFarmBackupMissingOrStale"]
    assert "time() - goatfarm_backup_recovery_timestamp_seconds > 93600" in stale_expression
    assert 'node_textfile_mtime_seconds{file="goatfarm_backup.prom"} > 7200' in stale_expression
    assert "GoatFarmBackupFreshnessUnitFailed" in alerts
    assert "GoatFarmBackupTimersInactive" in alerts
