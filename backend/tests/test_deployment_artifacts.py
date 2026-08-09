"""Executable regression tests for deployment and disaster-recovery artifacts.

PostgreSQL, GPG, AWS, and containers are never contacted.  Private mock
executables exercise the shell scripts' real ordering, cleanup, argument, and
failure behavior.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from sqlalchemy.engine import make_url

from app.core.config import Settings
from scripts import healthcheck

from .conftest import _admin_sql

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKUP = REPO_ROOT / "backend" / "scripts" / "backup.sh"
RESTORE = REPO_ROOT / "backend" / "scripts" / "restore.sh"
URL_HELPER = REPO_ROOT / "backend" / "scripts" / "libpq_url.py"
SIGNER_A = "A" * 40
SIGNER_B = "B" * 40


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    """Override the global integration fixture: these tests must never touch a DB."""


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    """Override the global per-test truncation fixture for this artifact-only module."""


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source)
    path.chmod(0o755)


def _install_mock_tools(tmp_path: Path) -> Path:
    mock_bin = tmp_path / "mock-bin"
    mock_bin.mkdir()
    common = """
import json
import os
import stat
import sys
from pathlib import Path

def record(tool):
    passfile = os.environ.get("PGPASSFILE")
    passfile_mode = "missing"
    if passfile and Path(passfile).exists():
        passfile_mode = oct(stat.S_IMODE(Path(passfile).stat().st_mode))
    entry = {
        "tool": tool,
        "argv": sys.argv[1:],
        "database_env": os.environ.get("GOATFARM_DATABASE_URL", "<unset>"),
        "pgpassword_env": os.environ.get("PGPASSWORD", "<unset>"),
        "restore_database_env": os.environ.get(
            "GOATFARM_RESTORE_DATABASE_URL", "<unset>"
        ),
        "passfile_mode": passfile_mode,
    }
    with Path(os.environ["MOCK_LOG"]).open("a") as stream:
        stream.write(json.dumps(entry, sort_keys=True) + "\\n")
"""
    _write_executable(
        mock_bin / "pg_dump",
        f"""#!/usr/bin/env python3
{common}
import time

record("pg_dump")
output = next(arg.split("=", 1)[1] for arg in sys.argv if arg.startswith("--file="))
Path(output).write_bytes(b"mock custom archive")
ready = os.environ.get("MOCK_DUMP_READY")
release = os.environ.get("MOCK_DUMP_RELEASE")
if ready:
    Path(ready).touch()
if release:
    deadline = time.monotonic() + 10
    while not Path(release).exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    if not Path(release).exists():
        raise SystemExit(98)
if os.environ.get("MOCK_PG_DUMP_FAIL") == "1":
    raise SystemExit(9)
""",
    )
    _write_executable(
        mock_bin / "pg_restore",
        f"""#!/usr/bin/env python3
{common}
record("pg_restore")
if "--list" in sys.argv and os.environ.get("MOCK_PG_RESTORE_LIST_FAIL") == "1":
    raise SystemExit(8)
if "--list" not in sys.argv and os.environ.get("MOCK_PG_RESTORE_FAIL") == "1":
    raise SystemExit(7)
""",
    )
    _write_executable(
        mock_bin / "psql",
        f"""#!/usr/bin/env python3
{common}
record("psql")
command = ""
for index, argument in enumerate(sys.argv):
    if argument == "--command" and index + 1 < len(sys.argv):
        command = sys.argv[index + 1]
if "user_namespaces" in command:
    print(os.environ.get("MOCK_USER_OBJECT_COUNT", "0"))
elif "FROM alembic_version" in command:
    print(os.environ.get("MOCK_ALEMBIC_REVISION", "f7d8c9b0a1e2"))
else:
    print("unexpected psql command", file=sys.stderr)
    raise SystemExit(6)
""",
    )
    _write_executable(
        mock_bin / "gpg",
        f"""#!/usr/bin/env python3
{common}
record("gpg")
arguments = sys.argv[1:]
output = arguments[arguments.index("--output") + 1]
if "--decrypt" in arguments:
    Path(output).write_bytes(b"mock decrypted custom archive")
    fingerprint = os.environ.get("MOCK_GPG_STATUS_FINGERPRINT", "{SIGNER_A}")
    status = os.environ.get(
        "MOCK_GPG_STATUS",
        f"[GNUPG:] VALIDSIG {{fingerprint}} 0 0 0 0 0 0 0 00 {{fingerprint}}\\n",
    )
    os.write(3, status.encode())
else:
    Path(output).write_bytes(b"mock signed encrypted archive")
if os.environ.get("MOCK_GPG_FAIL") == "1":
    raise SystemExit(5)
""",
    )
    _write_executable(
        mock_bin / "aws",
        f"""#!/usr/bin/env python3
{common}
record("aws")
if "list-objects-v2" in sys.argv:
    if os.environ.get("MOCK_AWS_LIST_FAIL") == "1":
        raise SystemExit(3)
    print(os.environ.get("MOCK_REMOTE_LIST", "None"))
if (
    os.environ.get("MOCK_AWS_FAIL_CHECKSUM") == "1"
    and "cp" in sys.argv
    and any(argument.endswith(".sha256") for argument in sys.argv)
):
    raise SystemExit(4)
""",
    )
    _write_executable(
        mock_bin / "cp",
        f"""#!/usr/bin/env python3
{common}
import shutil

record("cp")
operands = [argument for argument in sys.argv[1:] if argument != "--"]
source, destination = operands[-2:]
shutil.copyfile(source, destination)
replace_source = os.environ.get("MOCK_REPLACE_SOURCE_AFTER_COPY")
if replace_source and Path(source).resolve() == Path(replace_source).resolve():
    Path(source).write_bytes(b"source replaced after private snapshot")
""",
    )
    return mock_bin


def _base_env(tmp_path: Path, mock_bin: Path) -> dict[str, str]:
    env = os.environ.copy()
    for key in (
        "GOATFARM_BACKUP_GPG_RECIPIENT",
        "GOATFARM_BACKUP_GPG_SIGNER_FINGERPRINT",
        "GOATFARM_BACKUP_S3_URI",
        "GOATFARM_RESTORE_GPG_SIGNER_FINGERPRINT",
        "GOATFARM_RESTORE_DATABASE_URL",
        "GOATFARM_RESTORE_CONFIRM",
        "PGPASSWORD",
        "PGPASSFILE",
        "PGSERVICE",
        "PGSERVICEFILE",
    ):
        env.pop(key, None)
    env.update(
        {
            "GOATFARM_DATABASE_URL": (
                "postgresql+asyncpg://backup_user:super%3Asecret@db.invalid:5432/goatfarm"
            ),
            "GOATFARM_DB_SSLMODE": "disable",
            "GOATFARM_ENVIRONMENT": "development",
            "MOCK_LOG": str(tmp_path / "calls.jsonl"),
            "PATH": f"{mock_bin}{os.pathsep}{env['PATH']}",
        }
    )
    return env


def _run_backup(destination: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(BACKUP), str(destination)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )


def _write_checksum(
    archive: Path,
    *,
    recorded_name: str | None = None,
    extra_record: str | None = None,
) -> None:
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    content = f"{digest}  {recorded_name or archive.name}\n"
    if extra_record is not None:
        content += f"{extra_record}\n"
    archive.with_name(f"{archive.name}.sha256").write_text(content)


def _run_restore(
    archive: Path,
    env: dict[str, str],
    *,
    target_url: str = (
        "postgresql://restore_user:restore%3Asecret@db.invalid:5432/goatfarm_restore_test"
    ),
) -> subprocess.CompletedProcess[str]:
    restore_env = env.copy()
    restore_env["GOATFARM_RESTORE_CONFIRM"] = "goatfarm_restore_test"
    restore_env["GOATFARM_RESTORE_DATABASE_URL"] = target_url
    tmp_root = archive.parent / "restore-tmp"
    tmp_root.mkdir(exist_ok=True)
    restore_env["TMPDIR"] = str(tmp_root)
    return subprocess.run(
        ["bash", str(RESTORE), str(archive)],
        env=restore_env,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )


def _log_text(env: dict[str, str]) -> str:
    path = Path(env["MOCK_LOG"])
    return path.read_text() if path.exists() else ""


def test_backup_restore_and_url_helper_parse() -> None:
    for script in (BACKUP, RESTORE):
        result = subprocess.run(
            ["bash", "-n", str(script)], capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, result.stderr
    compile(URL_HELPER.read_text(), str(URL_HELPER), "exec")


def test_url_helper_uses_stdin_and_writes_escaped_private_passfile(tmp_path: Path) -> None:
    passfile = tmp_path / "pgpass"
    database_url = "postgresql+asyncpg://db%3Auser:p%3Aass%5Cword@[2001:db8::1]:5432/goatfarm"

    result = subprocess.run(
        ["python3", str(URL_HELPER), str(passfile)],
        input=database_url,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "postgresql://db%3Auser@[2001:db8::1]:5432/goatfarm",
        "goatfarm",
    ]
    assert "p:ass" not in result.stdout
    assert passfile.read_text() == ("2001\\:db8\\:\\:1:5432:goatfarm:db\\:user:p\\:ass\\\\word\n")
    assert passfile.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("failure", ["dump", "archive-list", "gpg"])
def test_backup_failure_never_publishes_and_cleans_private_state(
    tmp_path: Path, failure: str
) -> None:
    mock_bin = _install_mock_tools(tmp_path)
    env = _base_env(tmp_path, mock_bin)
    if failure == "dump":
        env["MOCK_PG_DUMP_FAIL"] = "1"
    elif failure == "archive-list":
        env["MOCK_PG_RESTORE_LIST_FAIL"] = "1"
    else:
        env.update(
            {
                "GOATFARM_BACKUP_GPG_RECIPIENT": "backup@example.invalid",
                "GOATFARM_BACKUP_GPG_SIGNER_FINGERPRINT": SIGNER_A,
                "MOCK_GPG_FAIL": "1",
            }
        )

    destination = tmp_path / "backups"
    result = _run_backup(destination, env)

    assert result.returncode != 0
    assert not list(destination.glob("goatfarm-*.dump*"))
    assert not list(destination.glob(".goatfarm-backup.*"))
    assert not (destination / ".goatfarm-backup.lock").exists()


def test_backup_lock_serializes_concurrent_producers(tmp_path: Path) -> None:
    mock_bin = _install_mock_tools(tmp_path)
    env = _base_env(tmp_path, mock_bin)
    ready = tmp_path / "dump-ready"
    release = tmp_path / "dump-release"
    env.update({"MOCK_DUMP_READY": str(ready), "MOCK_DUMP_RELEASE": str(release)})
    destination = tmp_path / "backups"
    first = subprocess.Popen(
        ["bash", str(BACKUP), str(destination)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 5
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert ready.exists(), first.communicate(timeout=1)

    second = _run_backup(destination, env)
    assert second.returncode == 3
    assert "Backup already running" in second.stderr

    release.touch()
    first_stdout, first_stderr = first.communicate(timeout=10)
    assert first.returncode == 0, first_stdout + first_stderr
    assert len(list(destination.glob("goatfarm-*.dump"))) == 1
    assert not (destination / ".goatfarm-backup.lock").exists()
    assert _log_text(env).count('"tool": "pg_dump"') == 1


def test_backup_is_validated_signed_encrypted_and_hides_database_password(
    tmp_path: Path,
) -> None:
    mock_bin = _install_mock_tools(tmp_path)
    env = _base_env(tmp_path, mock_bin)
    env.update(
        {
            "GOATFARM_BACKUP_GPG_RECIPIENT": "backup@example.invalid",
            "GOATFARM_BACKUP_GPG_SIGNER_FINGERPRINT": SIGNER_A,
        }
    )
    destination = tmp_path / "backups"

    result = _run_backup(destination, env)

    assert result.returncode == 0, result.stderr
    archives = list(destination.glob("goatfarm-*.dump.gpg"))
    assert len(archives) == 1
    sidecar = archives[0].with_name(f"{archives[0].name}.sha256")
    assert sidecar.is_file()
    checksum_records = sidecar.read_text().splitlines()
    assert len(checksum_records) == 1
    assert checksum_records[0].endswith(f"  {archives[0].name}")
    assert not list(destination.glob("goatfarm-*.dump"))
    log = _log_text(env)
    assert log.index('"tool": "pg_dump"') < log.index('"tool": "pg_restore"')
    assert log.index('"tool": "pg_restore"') < log.index('"tool": "gpg"')
    assert '"--sign"' in log
    assert '"--encrypt"' in log
    assert SIGNER_A in log
    assert "super:secret" not in log
    assert "super%3Asecret" not in log
    assert '"database_env": "<unset>"' in log
    assert '"pgpassword_env": "<unset>"' in log
    assert '"passfile_mode": "0o600"' in log


def test_production_and_offsite_backup_require_authenticated_encryption(
    tmp_path: Path,
) -> None:
    mock_bin = _install_mock_tools(tmp_path)
    for updates, error in (
        (
            {"GOATFARM_BACKUP_S3_URI": "s3://example/goatfarm"},
            "authenticated GPG encryption",
        ),
        (
            {"GOATFARM_ENVIRONMENT": "production", "GOATFARM_DB_SSLMODE": "verify-full"},
            "authenticated GPG encryption",
        ),
        (
            {
                "GOATFARM_BACKUP_S3_URI": "s3://example/goatfarm",
                "GOATFARM_BACKUP_GPG_RECIPIENT": "mutable@example.invalid",
                "GOATFARM_BACKUP_GPG_SIGNER_FINGERPRINT": SIGNER_A,
            },
            "complete fingerprint",
        ),
    ):
        env = _base_env(tmp_path, mock_bin)
        env.update(updates)
        result = _run_backup(tmp_path / "backups", env)
        assert result.returncode == 2
        assert error in result.stderr
    assert _log_text(env) == ""


@pytest.mark.parametrize("sslmode", ["require", "verify-ca"])
def test_production_database_jobs_require_hostname_verification(
    tmp_path: Path,
    sslmode: str,
) -> None:
    mock_bin = _install_mock_tools(tmp_path)
    env = _base_env(tmp_path, mock_bin)
    env.update(
        {
            "GOATFARM_ENVIRONMENT": "production",
            "GOATFARM_DB_SSLMODE": sslmode,
            "GOATFARM_BACKUP_GPG_RECIPIENT": SIGNER_B,
            "GOATFARM_BACKUP_GPG_SIGNER_FINGERPRINT": SIGNER_A,
            "GOATFARM_RESTORE_GPG_SIGNER_FINGERPRINT": SIGNER_A,
        }
    )

    backup = _run_backup(tmp_path / "backups", env)
    assert backup.returncode == 2
    assert "GOATFARM_DB_SSLMODE=verify-full" in backup.stderr

    archive = tmp_path / "goatfarm.dump.gpg"
    archive.write_bytes(b"encrypted archive")
    _write_checksum(archive)
    restore = _run_restore(archive, env)
    assert restore.returncode == 2
    assert "GOATFARM_DB_SSLMODE=verify-full" in restore.stderr
    assert _log_text(env) == ""


def test_production_alembic_refuses_unsafe_tls_before_engine_creation() -> None:
    env = os.environ.copy()
    env.update(
        {
            "GOATFARM_ENVIRONMENT": "production",
            "GOATFARM_DB_SSLMODE": "disable",
            "GOATFARM_MIGRATION_DATABASE_URL": (
                "postgresql+asyncpg://migrator@must-not-connect.invalid:5432/goatfarm"
            ),
        }
    )

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        cwd=REPO_ROOT / "backend",
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode != 0
    assert "Refusing migration: GOATFARM_DB_SSLMODE='disable'" in result.stderr


def test_failed_second_offsite_upload_removes_remote_partial_only(
    tmp_path: Path,
) -> None:
    mock_bin = _install_mock_tools(tmp_path)
    env = _base_env(tmp_path, mock_bin)
    env.update(
        {
            "GOATFARM_BACKUP_GPG_RECIPIENT": SIGNER_B,
            "GOATFARM_BACKUP_GPG_SIGNER_FINGERPRINT": SIGNER_A,
            "GOATFARM_BACKUP_S3_URI": "s3://example/goatfarm",
            "MOCK_AWS_FAIL_CHECKSUM": "1",
        }
    )
    destination = tmp_path / "backups"

    result = _run_backup(destination, env)

    assert result.returncode != 0
    assert len(list(destination.glob("goatfarm-*.dump.gpg"))) == 1
    log = _log_text(env)
    assert log.count('"tool": "aws"') == 4
    assert '"rm"' in log
    assert not (destination / ".goatfarm-backup.lock").exists()


def test_offsite_backup_refuses_remote_key_collision_without_deleting(
    tmp_path: Path,
) -> None:
    mock_bin = _install_mock_tools(tmp_path)
    env = _base_env(tmp_path, mock_bin)
    env.update(
        {
            "GOATFARM_BACKUP_GPG_RECIPIENT": SIGNER_B,
            "GOATFARM_BACKUP_GPG_SIGNER_FINGERPRINT": SIGNER_A,
            "GOATFARM_BACKUP_S3_URI": "s3://example/goatfarm",
            "MOCK_REMOTE_LIST": "goatfarm/pre-existing-backup.dump.gpg",
        }
    )
    destination = tmp_path / "backups"

    result = _run_backup(destination, env)

    assert result.returncode == 2
    assert "Refusing to overwrite existing remote backup key" in result.stderr
    assert len(list(destination.glob("goatfarm-*.dump.gpg"))) == 1
    aws_calls = [
        json.loads(line)
        for line in _log_text(env).splitlines()
        if json.loads(line)["tool"] == "aws"
    ]
    assert len(aws_calls) == 1
    assert "list-objects-v2" in aws_calls[0]["argv"]
    assert "rm" not in aws_calls[0]["argv"]


@pytest.mark.parametrize(
    ("recorded_name", "extra_record"),
    [
        ("different.dump", None),
        ("../goatfarm.dump", None),
        (None, f"{'0' * 64}  injected.dump"),
    ],
)
def test_restore_rejects_non_exact_checksum_records_before_tools(
    tmp_path: Path, recorded_name: str | None, extra_record: str | None
) -> None:
    mock_bin = _install_mock_tools(tmp_path)
    env = _base_env(tmp_path, mock_bin)
    archive = tmp_path / "goatfarm.dump"
    archive.write_bytes(b"archive")
    _write_checksum(archive, recorded_name=recorded_name, extra_record=extra_record)

    result = _run_restore(archive, env)

    assert result.returncode == 2
    assert "checksum" in result.stderr
    log = _log_text(env)
    assert '"tool": "cp"' in log
    assert '"tool": "gpg"' not in log
    assert '"tool": "pg_restore"' not in log
    assert '"tool": "psql"' not in log
    assert not list((tmp_path / "restore-tmp").iterdir())


def test_restore_rejects_checksum_tampering_before_archive_or_database_tools(
    tmp_path: Path,
) -> None:
    mock_bin = _install_mock_tools(tmp_path)
    env = _base_env(tmp_path, mock_bin)
    archive = tmp_path / "goatfarm.dump"
    archive.write_bytes(b"original")
    _write_checksum(archive)
    archive.write_bytes(b"tampered")

    result = _run_restore(archive, env)

    assert result.returncode == 2
    assert "checksum mismatch" in result.stderr
    log = _log_text(env)
    assert '"tool": "cp"' in log
    assert '"tool": "pg_restore"' not in log
    assert '"tool": "psql"' not in log


def test_restore_uses_private_snapshot_after_source_replacement(tmp_path: Path) -> None:
    mock_bin = _install_mock_tools(tmp_path)
    env = _base_env(tmp_path, mock_bin)
    archive = tmp_path / "goatfarm.dump"
    archive.write_bytes(b"original verified archive")
    _write_checksum(archive)
    env["MOCK_REPLACE_SOURCE_AFTER_COPY"] = str(archive)

    result = _run_restore(archive, env)

    assert result.returncode == 0, result.stderr
    assert archive.read_bytes() == b"source replaced after private snapshot"
    calls = [json.loads(line) for line in _log_text(env).splitlines()]
    restore_calls = [call for call in calls if call["tool"] == "pg_restore"]
    assert len(restore_calls) == 2
    assert all(str(archive) not in call["argv"] for call in restore_calls)
    assert all("/source/goatfarm.dump" in " ".join(call["argv"]) for call in restore_calls)


@pytest.mark.parametrize(
    "gpg_status",
    [
        {"MOCK_GPG_STATUS_FINGERPRINT": SIGNER_B},
        {"MOCK_GPG_STATUS": "[GNUPG:] BADSIG DEADBEEF attacker\n"},
    ],
    ids=["unexpected-signer", "bad-signature"],
)
def test_restore_rejects_invalid_gpg_authentication_and_removes_plaintext(
    tmp_path: Path, gpg_status: dict[str, str]
) -> None:
    mock_bin = _install_mock_tools(tmp_path)
    env = _base_env(tmp_path, mock_bin)
    env.update({"GOATFARM_RESTORE_GPG_SIGNER_FINGERPRINT": SIGNER_A, **gpg_status})
    archive = tmp_path / "goatfarm.dump.gpg"
    archive.write_bytes(b"encrypted")
    _write_checksum(archive)

    result = _run_restore(archive, env)

    assert result.returncode == 2
    assert "unexpected signer" in result.stderr
    assert '"tool": "gpg"' in _log_text(env)
    assert '"tool": "pg_restore"' not in _log_text(env)
    assert not list((tmp_path / "restore-tmp").iterdir())


def test_restore_rejects_objects_in_any_user_schema_before_mutation(
    tmp_path: Path,
) -> None:
    mock_bin = _install_mock_tools(tmp_path)
    env = _base_env(tmp_path, mock_bin)
    env["MOCK_USER_OBJECT_COUNT"] = "1"
    archive = tmp_path / "goatfarm.dump"
    archive.write_bytes(b"archive")
    _write_checksum(archive)

    result = _run_restore(archive, env)

    assert result.returncode == 2
    assert "contains user schema objects" in result.stderr
    log = _log_text(env)
    assert "user_namespaces" in log
    assert log.count('"tool": "pg_restore"') == 1
    assert '"--single-transaction"' not in log


def test_restore_is_single_transaction_sanitizes_credentials_and_checks_alembic(
    tmp_path: Path,
) -> None:
    mock_bin = _install_mock_tools(tmp_path)
    env = _base_env(tmp_path, mock_bin)
    env.update(
        {
            "GOATFARM_ENVIRONMENT": "production",
            "GOATFARM_DB_SSLMODE": "verify-full",
            "GOATFARM_RESTORE_GPG_SIGNER_FINGERPRINT": SIGNER_A,
        }
    )
    archive = tmp_path / "goatfarm.dump.gpg"
    archive.write_bytes(b"encrypted archive")
    _write_checksum(archive)

    result = _run_restore(archive, env)

    assert result.returncode == 0, result.stderr
    assert "Alembic revision f7d8c9b0a1e2" in result.stdout
    log = _log_text(env)
    assert '"--single-transaction"' in log
    assert '"--exit-on-error"' in log
    assert '"--no-password"' in log
    assert "restore:secret" not in log
    assert "restore%3Asecret" not in log
    assert all("restore:secret" not in argument for argument in result.args)
    assert all("restore%3Asecret" not in argument for argument in result.args)
    assert '"database_env": "<unset>"' in log
    assert '"pgpassword_env": "<unset>"' in log
    assert '"restore_database_env": "<unset>"' in log
    assert '"passfile_mode": "0o600"' in log
    assert "FROM alembic_version" in log
    assert '"tool": "gpg"' in log
    assert not list((tmp_path / "restore-tmp").iterdir())


def test_restore_failure_uses_atomic_pg_restore_and_skips_success_sanity(
    tmp_path: Path,
) -> None:
    mock_bin = _install_mock_tools(tmp_path)
    env = _base_env(tmp_path, mock_bin)
    env["MOCK_PG_RESTORE_FAIL"] = "1"
    archive = tmp_path / "goatfarm.dump"
    archive.write_bytes(b"archive")
    _write_checksum(archive)

    result = _run_restore(archive, env)

    assert result.returncode != 0
    log = _log_text(env)
    assert '"--single-transaction"' in log
    assert '"--exit-on-error"' in log
    assert "FROM alembic_version" not in log
    assert not list((tmp_path / "restore-tmp").iterdir())


def test_restore_fails_closed_when_alembic_marker_is_invalid(tmp_path: Path) -> None:
    mock_bin = _install_mock_tools(tmp_path)
    env = _base_env(tmp_path, mock_bin)
    env["MOCK_ALEMBIC_REVISION"] = ""
    archive = tmp_path / "goatfarm.dump"
    archive.write_bytes(b"archive")
    _write_checksum(archive)

    result = _run_restore(archive, env)

    assert result.returncode == 1
    assert "valid Alembic revision" in result.stderr
    assert '"--single-transaction"' in _log_text(env)


def test_production_restore_requires_signed_encrypted_artifact(tmp_path: Path) -> None:
    mock_bin = _install_mock_tools(tmp_path)
    env = _base_env(tmp_path, mock_bin)
    env.update({"GOATFARM_ENVIRONMENT": "production", "GOATFARM_DB_SSLMODE": "verify-full"})
    archive = tmp_path / "goatfarm.dump"
    archive.write_bytes(b"archive")
    _write_checksum(archive)

    result = _run_restore(archive, env)

    assert result.returncode == 2
    assert "authenticated GPG backup" in result.stderr
    assert _log_text(env) == ""


def test_backend_container_separates_migration_and_readiness() -> None:
    dockerfile = (REPO_ROOT / "Dockerfile").read_text()
    dockerignore = (REPO_ROOT / ".dockerignore").read_text()
    assert "COPY backend/scripts/healthcheck.py ./healthcheck.py" in dockerfile
    assert "CMD python healthcheck.py" in dockerfile
    assert "backend/scripts/*" in dockerignore
    assert "!backend/scripts/healthcheck.py" in dockerignore
    assert '"--no-proxy-headers"' in dockerfile
    assert '"--no-server-header"' in dockerfile
    assert "alembic upgrade head &&" not in dockerfile

    compose = (REPO_ROOT / "docker-compose.yml").read_text()
    for setting in (
        "GOATFARM_DATABASE_URL",
        "GOATFARM_MIGRATION_DATABASE_URL",
        "GOATFARM_COOKIE_SECURE",
        "GOATFARM_CORS_ORIGINS",
        "GOATFARM_ALLOWED_HOSTS",
        "GOATFARM_MIN_PASSWORD_LENGTH",
        "GOATFARM_IDEMPOTENCY_REQUEST_HMAC_SECRET",
        "GOATFARM_IDEMPOTENCY_REQUEST_HMAC_PREVIOUS_SECRETS",
        "GOATFARM_TRUSTED_PROXY_HOSTS",
    ):
        assert setting in compose

    # Next's external rewrite sets changeOrigin, so the upstream Host is the
    # BACKEND_URL hostname. The Compose default must admit that internal name;
    # otherwise readiness stays green while every proxied browser API call is
    # rejected by Starlette's TrustedHostMiddleware.
    allowed_line = next(line for line in compose.splitlines() if "GOATFARM_ALLOWED_HOSTS:" in line)
    allowed_defaults = allowed_line.split(":-", 1)[1].split("}'", 1)[0]
    backend_line = next(line for line in compose.splitlines() if "BACKEND_URL:" in line)
    backend_url = backend_line.split("BACKEND_URL:", 1)[1].strip()
    backend_host = backend_url.split("://", 1)[1].split(":", 1)[0]
    assert backend_host in json.loads(allowed_defaults)


@pytest.mark.parametrize(
    ("allowed_host", "expected_host"),
    [
        ("api.example.com", "api.example.com"),
        ("*.example.com", "healthcheck.example.com"),
    ],
)
def test_container_readiness_uses_an_allowed_virtual_host(
    monkeypatch: pytest.MonkeyPatch,
    allowed_host: str,
    expected_host: str,
) -> None:
    requests: list[tuple[str, str, dict[str, str]]] = []
    closed: list[bool] = []

    class Response:
        status = 200

        @staticmethod
        def read() -> bytes:
            return b"ready"

    class Connection:
        def __init__(self, host: str, port: int, timeout: int) -> None:
            assert (host, port, timeout) == ("127.0.0.1", 8000, 4)

        def request(self, method: str, path: str, *, headers: dict[str, str]) -> None:
            requests.append((method, path, headers))

        @staticmethod
        def getresponse() -> Response:
            return Response()

        @staticmethod
        def close() -> None:
            closed.append(True)

    monkeypatch.setattr(
        healthcheck, "get_settings", lambda: SimpleNamespace(allowed_hosts=[allowed_host])
    )
    monkeypatch.setattr(healthcheck.http.client, "HTTPConnection", Connection)

    healthcheck.main()

    assert requests == [("GET", "/readyz", {"Host": expected_host})]
    assert closed == [True]


def test_frontend_is_a_standalone_node_container() -> None:
    config = (REPO_ROOT / "frontend" / "next.config.ts").read_text()
    dockerfile = (REPO_ROOT / "frontend" / "Dockerfile").read_text()
    assert 'output: "standalone"' in config
    assert "poweredByHeader: false" in config
    assert 'CMD ["node", "server.js"]' in dockerfile


def _stage_scripts(tmp_path: Path, env_file: str | None) -> Path:
    """Copy the scripts under a private backend/ tree so a test can control the
    backend/.env they read without touching the repository's own file."""
    staged = tmp_path / "staged-backend" / "scripts"
    staged.mkdir(parents=True)
    for source in (BACKUP, RESTORE, URL_HELPER):
        shutil.copy(source, staged / source.name)
    if env_file is not None:
        (staged.parent / ".env").write_text(env_file)
    return staged


def test_backup_reads_the_application_env_file_for_its_safety_gates(tmp_path: Path) -> None:
    """The TLS and mandatory-GPG gates key off GOATFARM_ENVIRONMENT and
    GOATFARM_DB_SSLMODE, which config.py reads from backend/.env. A cron entry
    that exports only GOATFARM_DATABASE_URL used to silently degrade a
    production host to an unsigned plaintext dump over a connection this script
    then forces to `disable` — and exit 0."""
    mock_bin = _install_mock_tools(tmp_path)
    env = _base_env(tmp_path, mock_bin)
    env.pop("GOATFARM_ENVIRONMENT")
    env.pop("GOATFARM_DB_SSLMODE")
    staged = _stage_scripts(
        tmp_path, "GOATFARM_ENVIRONMENT=production\nGOATFARM_DB_SSLMODE=verify-full\n"
    )

    result = subprocess.run(
        ["bash", str(staged / "backup.sh"), str(tmp_path / "dest")],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )

    assert result.returncode == 2
    assert "authenticated GPG encryption" in result.stderr
    assert _log_text(env) == ""  # never connected to the database


def test_restore_reads_the_application_env_file_for_its_safety_gates(tmp_path: Path) -> None:
    mock_bin = _install_mock_tools(tmp_path)
    env = _base_env(tmp_path, mock_bin)
    env.pop("GOATFARM_ENVIRONMENT")
    env.pop("GOATFARM_DB_SSLMODE")
    staged = _stage_scripts(
        tmp_path, "GOATFARM_ENVIRONMENT=production\nGOATFARM_DB_SSLMODE=verify-full\n"
    )
    archive = tmp_path / "goatfarm.dump"
    archive.write_bytes(b"archive")
    _write_checksum(archive)
    restore_env = env.copy()
    restore_env["GOATFARM_RESTORE_CONFIRM"] = "goatfarm_restore_test"
    restore_env["GOATFARM_RESTORE_DATABASE_URL"] = (
        "postgresql://restore_user:restore%3Asecret@db.invalid:5432/goatfarm_restore_test"
    )
    tmp_root = tmp_path / "restore-tmp"
    tmp_root.mkdir()
    restore_env["TMPDIR"] = str(tmp_root)

    result = subprocess.run(
        ["bash", str(staged / "restore.sh"), str(archive)],
        env=restore_env,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )

    assert result.returncode == 2
    assert "authenticated GPG backup" in result.stderr
    assert _log_text(env) == ""


def test_exported_environment_still_wins_over_the_env_file(tmp_path: Path) -> None:
    """The documented production invocation passes both values on the command
    line; an explicit export must keep overriding backend/.env."""
    mock_bin = _install_mock_tools(tmp_path)
    env = _base_env(tmp_path, mock_bin)  # development / disable
    staged = _stage_scripts(
        tmp_path, "GOATFARM_ENVIRONMENT=production\nGOATFARM_DB_SSLMODE=verify-full\n"
    )

    result = subprocess.run(
        ["bash", str(staged / "backup.sh"), str(tmp_path / "dest")],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr


def test_compose_publishes_only_one_edge_that_forwards_the_real_client_address() -> None:
    """Next's rewrite proxy never emits X-Forwarded-For, so routing browser
    /api traffic through the SPA container made every client share the Next
    container's address: the 11th signup in five minutes — from anyone — got
    429, and 100 bad logins locked the whole deployment out."""
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())
    services = compose["services"]

    # The SPA and API containers are internal-only entry points.
    assert "ports" not in services["frontend"]
    assert "ports" not in services["backend"]
    assert "8000" in services["backend"]["expose"]
    edge = services["edge"]
    assert "3000:3000" in edge["ports"]

    # `$$` is Compose's escape; nginx receives single-dollar variables.
    proxy_conf = compose["configs"]["edge_proxy"]["content"].replace("$$", "$")
    assert "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;" in proxy_conf
    assert "$$" not in proxy_conf
    assert "location /api/" in proxy_conf
    assert "proxy_pass http://backend:8000;" in proxy_conf

    # The backend trusts exactly the edge's fixed address — not the bridge
    # range, which also covers the docker gateway and other containers.
    edge_address = edge["networks"]["default"]["ipv4_address"]
    trusted = services["backend"]["environment"]["GOATFARM_TRUSTED_PROXY_HOSTS"]
    assert trusted.endswith(f":-{edge_address}}}"), trusted
    subnet = compose["networks"]["default"]["ipam"]["config"][0]["subnet"]
    assert ipaddress.ip_address(edge_address) in ipaddress.ip_network(subnet)
    # And the value the compose file ships must satisfy the settings contract.
    assert Settings(trusted_proxy_hosts=edge_address).trusted_proxy_hosts == edge_address


def test_compose_keeps_api_and_migration_credentials_separate_and_url_safe() -> None:
    compose_path = REPO_ROOT / "docker-compose.yml"
    compose_text = compose_path.read_text()
    services = yaml.safe_load(compose_text)["services"]
    migration_env = services["migrate"]["environment"]
    api_env = services["backend"]["environment"]

    assert "GOATFARM_DATABASE_URL" not in migration_env
    assert "GOATFARM_MIGRATION_DATABASE_URL" in migration_env
    assert migration_env["GOATFARM_ENVIRONMENT"] == "${GOATFARM_ENVIRONMENT:-development}"
    assert "GOATFARM_DATABASE_URL" in api_env
    assert "GOATFARM_MIGRATION_DATABASE_URL" not in api_env
    assert "postgresql+asyncpg://${POSTGRES" not in compose_text
    assert "${POSTGRES_PASSWORD" not in migration_env["GOATFARM_MIGRATION_DATABASE_URL"]
    assert "${POSTGRES_PASSWORD" not in api_env["GOATFARM_DATABASE_URL"]

    # Full, percent-encoded URLs preserve reserved credential characters; raw
    # string concatenation in Compose did not.
    parsed = make_url("postgresql+asyncpg://api:p%40ss%3Aword%2Fmore@db:5432/goatfarm")
    assert parsed.username == "api"
    assert parsed.password == "p@ss:word/more"

    compose_example = (REPO_ROOT / ".env.example").read_text()
    assert "GOATFARM_DATABASE_URL=postgresql+asyncpg://" in compose_example
    assert "GOATFARM_MIGRATION_DATABASE_URL=postgresql+asyncpg://" in compose_example
    assert "Percent-encode reserved characters" in compose_example


def test_migrations_do_not_inherit_the_request_path_statement_timeout(tmp_path: Path) -> None:
    """`statement_timeout` is an OLTP backstop. Applying it to DDL cancels any
    table rewrite, constraint validation or CREATE INDEX CONCURRENTLY that runs
    longer than a request may, aborting the release job — and CONCURRENTLY
    builds wait for concurrent transactions to drain, so even a small table
    trips it."""
    assert Settings().migration_statement_timeout_ms == 0
    alembic_env = (REPO_ROOT / "backend" / "alembic" / "env.py").read_text()
    assert "get_migration_settings" in alembic_env
    assert "get_settings" not in alembic_env

    database = f"{os.environ.get('GOATFARM_TEST_DB', 'goatfarm_test')}_migration_timeout"
    _admin_sql(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
    _admin_sql(f'CREATE DATABASE "{database}"')
    try:
        env = os.environ.copy()
        env["GOATFARM_DATABASE_URL"] = f"postgresql+asyncpg://localhost:5432/{database}"
        env["GOATFARM_DB_STATEMENT_TIMEOUT_MS"] = "1"
        allowed = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "ed5efe13a516"],
            cwd=REPO_ROOT / "backend",
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        assert allowed.returncode == 0, allowed.stderr

        # ...and the migration-specific knob really is the one in force.
        env["GOATFARM_MIGRATION_STATEMENT_TIMEOUT_MS"] = "1"
        capped = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=REPO_ROOT / "backend",
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        assert capped.returncode != 0
        assert "statement timeout" in capped.stderr
    finally:
        _admin_sql(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
