"""Executable regression tests for deployment and disaster-recovery artifacts.

PostgreSQL, GPG, AWS, and containers are never contacted.  Private mock
executables exercise the shell scripts' real ordering, cleanup, argument, and
failure behavior.
"""

from __future__ import annotations

import errno
import hashlib
import ipaddress
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from sqlalchemy.engine import make_url

from app.core.config import Settings
from scripts import backup_legacy_lock, dotenv_value, healthcheck, pinned_copy

from .conftest import _admin_sql

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKUP = REPO_ROOT / "backend" / "scripts" / "backup.sh"
RESTORE = REPO_ROOT / "backend" / "scripts" / "restore.sh"
URL_HELPER = REPO_ROOT / "backend" / "scripts" / "libpq_url.py"
DOTENV_HELPER = REPO_ROOT / "backend" / "scripts" / "dotenv_value.py"
FLOCK_HELPER = REPO_ROOT / "backend" / "scripts" / "backup_flock.py"
LEGACY_LOCK_HELPER = REPO_ROOT / "backend" / "scripts" / "backup_legacy_lock.py"
PINNED_COPY_HELPER = REPO_ROOT / "backend" / "scripts" / "pinned_copy.py"
ENV_LIB = REPO_ROOT / "backend" / "scripts" / "backup_env.sh"
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
if "--list" not in sys.argv:
    output = next(
        (
            argument.split("=", 1)[1]
            for argument in sys.argv
            if argument.startswith("--file=")
        ),
        None,
    )
    if output is None:
        print("mock pg_restore expected a private SQL output file", file=sys.stderr)
        raise SystemExit(7)
    Path(output).write_text("-- mock restore SQL\\n")
""",
    )
    _write_executable(
        mock_bin / "psql",
        f"""#!/usr/bin/env python3
{common}
record("psql")
commands = [
    argument.split("=", 1)[1]
    for argument in sys.argv[1:]
    if argument.startswith("--command=")
]
for index, argument in enumerate(sys.argv):
    if argument == "--command" and index + 1 < len(sys.argv):
        commands.append(sys.argv[index + 1])
command = "\\n".join(commands)
if "atomic_restore_guard" in command:
    if os.environ.get("MOCK_ATOMIC_USER_OBJECT_COUNT", "0") != "0":
        print(
            "target database acquired user schema objects before the atomic restore",
            file=sys.stderr,
        )
        raise SystemExit(6)
    if os.environ.get("MOCK_PG_RESTORE_FAIL") == "1":
        print("mock restore SQL failed", file=sys.stderr)
        raise SystemExit(7)
elif "user_namespaces" in command:
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
if os.environ.get("MOCK_AWS_FAIL_RM") == "1" and "rm" in sys.argv:
    raise SystemExit(6)
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


def _ephemeral_backup_state(destination: Path) -> list[Path]:
    """Private work products must vanish; the kernel-lock inode persists."""
    return [
        path
        for path in destination.glob(".goatfarm-backup.*")
        if path.name != ".goatfarm-backup.flock"
    ]


def test_backup_restore_and_url_helper_parse() -> None:
    for script in (BACKUP, RESTORE):
        result = subprocess.run(
            ["bash", "-n", str(script)], capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, result.stderr
    compile(URL_HELPER.read_text(), str(URL_HELPER), "exec")
    compile(DOTENV_HELPER.read_text(), str(DOTENV_HELPER), "exec")
    compile(FLOCK_HELPER.read_text(), str(FLOCK_HELPER), "exec")
    compile(LEGACY_LOCK_HELPER.read_text(), str(LEGACY_LOCK_HELPER), "exec")
    compile(PINNED_COPY_HELPER.read_text(), str(PINNED_COPY_HELPER), "exec")

    # The regular-file check and shell open are necessarily separate. A FIFO
    # raced into the name must not make the open block before the Python helper
    # can fstat and reject it; O_RDWR (`<>`) is nonblocking for a FIFO.
    backup_source = BACKUP.read_text()
    assert 'exec 9<> "${FLOCK_PATH}"' in backup_source
    assert 'exec 9>> "${FLOCK_PATH}"' not in backup_source


def test_pinned_copy_rejects_fifo_without_blocking(tmp_path: Path) -> None:
    source = tmp_path / "raced-source"
    destination = tmp_path / "private-copy"
    os.mkfifo(source)

    result = subprocess.run(
        [sys.executable, str(PINNED_COPY_HELPER), str(source), str(destination)],
        capture_output=True,
        text=True,
        check=False,
        timeout=2,
    )

    assert result.returncode == 2
    assert "not a regular file" in result.stderr
    assert not destination.exists()


def test_pinned_copy_bounds_and_rejects_a_growing_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "growing-source"
    destination = tmp_path / "private-copy"
    source.write_bytes(b"A" * (pinned_copy._COPY_CHUNK_BYTES + 1))
    real_read = pinned_copy.os.read
    first_read = True

    def append_after_first_read(file_descriptor: int, size: int) -> bytes:
        nonlocal first_read
        content = real_read(file_descriptor, size)
        if first_read:
            first_read = False
            with source.open("ab") as stream:
                stream.write(b"raced growth")
        return content

    monkeypatch.setattr(pinned_copy.os, "read", append_after_first_read)

    with pytest.raises(pinned_copy.PinnedCopyError, match="grew"):
        pinned_copy.copy_pinned_regular_file(source, destination)
    assert not destination.exists()


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


def test_url_helper_preserves_allowed_query_parameters(tmp_path: Path) -> None:
    passfile = tmp_path / "pgpass"
    database_url = (
        "postgresql://db.invalid/goatfarm?application_name=goatfarm-backup&connect_timeout=15"
    )

    result = subprocess.run(
        ["python3", str(URL_HELPER), str(passfile)],
        input=database_url,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [database_url, "goatfarm"]
    assert passfile.exists()


@pytest.mark.parametrize(
    "query",
    [
        # libpq TLS-trust material: each of these redirects *what the enforced
        # verify-full gate verifies against* (RT-N-1) — a denylist missed them.
        "sslrootcert=/shared/attacker-ca.pem",
        "sslcert=/shared/client.pem&sslkey=/shared/client.key",
        "sslpassword=hunter2",
        "sslcrl=/shared/crl.pem",
        "sslcrldir=/shared/crls",
        "gssencmode=require",
        "channel_binding=require",
        "sslnegotiation=direct",
        "krbsrvname=evil",
        "requirepeer=postgres",
        # Original denylist members must stay rejected after the inversion.
        "sslmode=disable",
        "passfile=/etc/passwd",
        "service=prod",
        "servicefile=/etc/service.conf",
        "host=db.other.invalid",
        "hostaddr=10.0.0.1",
        "port=5433",
        "dbname=goatfarm_prod",
        "user=postgres",
        "password=secret",
        # libpq `options` can smuggle arbitrary -c settings, sslmode included.
        "options=-c%20sslmode%3Ddisable",
        # Anything unclassified fails closed rather than passing verbatim.
        "future_param=value",
    ],
)
def test_url_helper_rejects_every_query_key_outside_the_allowlist(
    tmp_path: Path, query: str
) -> None:
    passfile = tmp_path / "pgpass"

    result = subprocess.run(
        ["python3", str(URL_HELPER), str(passfile)],
        input=f"postgresql://user:secret@db.invalid:5432/goatfarm?{query}",
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "Invalid PostgreSQL URL" in result.stderr
    assert "is not permitted" in result.stderr
    assert not passfile.exists()


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
    assert not _ephemeral_backup_state(destination)
    flock_path = destination / ".goatfarm-backup.flock"
    assert flock_path.is_file() and not flock_path.is_symlink()
    assert flock_path.stat().st_mode & 0o777 == 0o600


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
    legacy_lock = destination / ".goatfarm-backup.lock"
    assert (legacy_lock / "pid").read_text() == f"{first.pid}\n"
    assert (legacy_lock / ".flock-owner").is_file()

    second = _run_backup(destination, env)
    assert second.returncode == 3
    assert "Backup already running" in second.stderr

    release.touch()
    first_stdout, first_stderr = first.communicate(timeout=10)
    assert first.returncode == 0, first_stdout + first_stderr
    assert len(list(destination.glob("goatfarm-*.dump"))) == 1
    assert not legacy_lock.exists()
    flock_path = destination / ".goatfarm-backup.flock"
    lock_inode = flock_path.stat().st_ino

    # The named inode is permanent and reusable; successful cleanup releases
    # only the kernel lock. Remove timestamped test artifacts so an immediate
    # second producer cannot collide on the same one-second archive name.
    for artifact in destination.glob("goatfarm-*.dump*"):
        artifact.unlink()
    third = _run_backup(destination, env)
    assert third.returncode == 0, third.stderr
    assert flock_path.stat().st_ino == lock_inode
    assert _log_text(env).count('"tool": "pg_dump"') == 2


def test_backup_kernel_lock_releases_after_crashed_process_group(tmp_path: Path) -> None:
    mock_bin = _install_mock_tools(tmp_path)
    env = _base_env(tmp_path, mock_bin)
    ready = tmp_path / "crash-dump-ready"
    never_release = tmp_path / "crash-dump-release"
    env.update({"MOCK_DUMP_READY": str(ready), "MOCK_DUMP_RELEASE": str(never_release)})
    destination = tmp_path / "backups"
    first = subprocess.Popen(
        ["bash", str(BACKUP), str(destination)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    deadline = time.monotonic() + 5
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert ready.exists(), first.communicate(timeout=1)
    flock_path = destination / ".goatfarm-backup.flock"
    lock_inode = flock_path.stat().st_ino
    legacy_lock = destination / ".goatfarm-backup.lock"
    assert (legacy_lock / "pid").read_text() == f"{first.pid}\n"

    os.killpg(first.pid, signal.SIGKILL)
    first.communicate(timeout=5)
    assert legacy_lock.is_dir()

    retry_env = _base_env(tmp_path, mock_bin)
    result = _run_backup(destination, retry_env)

    assert result.returncode == 0, result.stderr
    assert flock_path.stat().st_ino == lock_inode
    assert len(list(destination.glob("goatfarm-*.dump"))) == 1
    assert not legacy_lock.exists()


@pytest.mark.parametrize(
    ("pid_record", "expected_returncode", "message", "legacy_lock_remains"),
    [
        ("999999999\n", 3, "requires manual verification", True),
        (f"{os.getpid()}\n", 3, "live legacy lock", True),
        ("not-a-pid\n", 3, "malformed or has untrusted identity", True),
    ],
    ids=["dead", "live", "malformed"],
)
def test_backup_handles_legacy_directory_only_after_kernel_lock(
    tmp_path: Path,
    pid_record: str,
    expected_returncode: int,
    message: str,
    legacy_lock_remains: bool,
) -> None:
    mock_bin = _install_mock_tools(tmp_path)
    env = _base_env(tmp_path, mock_bin)
    destination = tmp_path / "backups"
    legacy_lock = destination / ".goatfarm-backup.lock"
    legacy_lock.mkdir(parents=True)
    (legacy_lock / "pid").write_text(pid_record)

    result = _run_backup(destination, env)

    assert result.returncode == expected_returncode, result.stderr
    assert message in result.stderr
    assert (destination / ".goatfarm-backup.flock").is_file()
    assert legacy_lock.exists() is legacy_lock_remains
    assert _log_text(env).count('"tool": "pg_dump"') == (1 if expected_returncode == 0 else 0)


def test_backup_reclaims_new_format_legacy_lock_despite_recycled_live_pid(
    tmp_path: Path,
) -> None:
    """Holding the flock proves a new-format producer's whole tree exited: its
    recorded PID being recycled by a live unrelated process (near-certain
    after a reboot) must not wedge every subsequent backup behind a lock
    nothing owns."""
    mock_bin = _install_mock_tools(tmp_path)
    env = _base_env(tmp_path, mock_bin)
    destination = tmp_path / "backups"
    legacy_lock = destination / ".goatfarm-backup.lock"
    legacy_lock.mkdir(parents=True)
    live_pid = os.getpid()  # definitely alive, definitely not a backup producer
    (legacy_lock / "pid").write_text(f"{live_pid}\n")
    (legacy_lock / ".flock-owner").write_text(f"{live_pid}-1-2\n")

    result = _run_backup(destination, env)

    assert result.returncode == 0, result.stderr
    assert "Reclaiming complete new-format legacy backup lock" in result.stderr
    assert len(list(destination.glob("goatfarm-*.dump"))) == 1
    assert not legacy_lock.exists()
    assert not list(destination.glob(".goatfarm-backup.legacy-stale.*"))


def test_backup_fails_if_late_old_producer_wins_legacy_claim(tmp_path: Path) -> None:
    """A pre-flock snapshot cannot admit an old mkdir-lock-only producer."""
    mock_bin = _install_mock_tools(tmp_path)
    env = _base_env(tmp_path, mock_bin)
    destination = tmp_path / "backups"
    legacy_lock = destination / ".goatfarm-backup.lock"
    legacy_lock.mkdir(parents=True)
    (legacy_lock / "pid").write_text("999999999\n")
    (legacy_lock / ".flock-owner").write_text("999999999-1-2\n")

    claim_ready = tmp_path / "new-producer-at-mkdir"
    old_ready = tmp_path / "old-producer-holds-lock"
    old_release = tmp_path / "release-old-producer"
    staged = _stage_scripts(tmp_path, None)
    env.update(
        {
            "MOCK_LEGACY_LOCK_DIR": str(legacy_lock),
            "MOCK_LEGACY_CLAIM_READY": str(claim_ready),
            "MOCK_LEGACY_OLD_READY": str(old_ready),
        }
    )
    _write_executable(
        staged / LEGACY_LOCK_HELPER.name,
        f"""#!/usr/bin/env python3
import os
import sys
import time
from pathlib import Path

if (
    sys.argv[1] == "move-verified"
    and ".legacy-claim." in Path(sys.argv[2]).name
    and Path(sys.argv[3]) == Path(os.environ["MOCK_LEGACY_LOCK_DIR"])
):
    Path(os.environ["MOCK_LEGACY_CLAIM_READY"]).touch()
    deadline = time.monotonic() + 5
    old_ready = Path(os.environ["MOCK_LEGACY_OLD_READY"])
    while not old_ready.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
os.execv(
    sys.executable,
    [sys.executable, {str(LEGACY_LOCK_HELPER)!r}, *sys.argv[1:]],
)
""",
    )
    old_producer = subprocess.Popen(
        [
            sys.executable,
            "-c",
            """
import os
import sys
import time
from pathlib import Path

claim_ready, old_ready, release, lock_dir = map(Path, sys.argv[1:])
deadline = time.monotonic() + 5
while not claim_ready.exists() and time.monotonic() < deadline:
    time.sleep(0.01)
if not claim_ready.exists():
    raise SystemExit(90)
lock_dir.mkdir()
(lock_dir / "pid").write_text(f"{os.getpid()}\\n")
old_ready.touch()
deadline = time.monotonic() + 10
while not release.exists() and time.monotonic() < deadline:
    time.sleep(0.01)
if not release.exists():
    raise SystemExit(91)
(lock_dir / "pid").unlink()
lock_dir.rmdir()
""",
            str(claim_ready),
            str(old_ready),
            str(old_release),
            str(legacy_lock),
        ]
    )
    try:
        result = subprocess.run(
            ["bash", str(staged / BACKUP.name), str(destination)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )

        assert result.returncode == 3, result.stderr
        assert "legacy lock claim was won concurrently" in result.stderr
        assert (legacy_lock / "pid").read_text() == f"{old_producer.pid}\n"
        assert old_producer.poll() is None
        assert '"tool": "pg_dump"' not in _log_text(env)
        assert (destination / ".goatfarm-backup.flock").is_file()
        assert not list(destination.glob(".goatfarm-backup.legacy-stale.*"))
    finally:
        old_release.touch()
        old_producer.wait(timeout=5)


def test_backup_rejects_legacy_identity_replacement_before_quarantine(
    tmp_path: Path,
) -> None:
    """The moved lock must be the exact dead inode/content that was checked."""
    mock_bin = _install_mock_tools(tmp_path)
    env = _base_env(tmp_path, mock_bin)
    destination = tmp_path / "backups"
    legacy_lock = destination / ".goatfarm-backup.lock"
    legacy_lock.mkdir(parents=True)
    (legacy_lock / "pid").write_text("999999999\n")
    (legacy_lock / ".flock-owner").write_text("999999999-1-2\n")

    staged = _stage_scripts(tmp_path, None)
    injected = tmp_path / "legacy-replacement-injected"
    displaced_dead_lock = tmp_path / "original-dead-legacy-lock"
    env.update(
        {
            "MOCK_LEGACY_REPLACEMENT_INJECTED": str(injected),
            "MOCK_DISPLACED_DEAD_LOCK": str(displaced_dead_lock),
            "MOCK_LIVE_REPLACEMENT_PID": str(os.getpid()),
        }
    )
    _write_executable(
        staged / LEGACY_LOCK_HELPER.name,
        f"""#!/usr/bin/env python3
import os
import sys
from pathlib import Path

injected = Path(os.environ["MOCK_LEGACY_REPLACEMENT_INJECTED"])
if (
    sys.argv[1] == "move-verified"
    and Path(sys.argv[2]).name == ".goatfarm-backup.lock"
    and not injected.exists()
):
    source = Path(sys.argv[2])
    source.rename(Path(os.environ["MOCK_DISPLACED_DEAD_LOCK"]))
    source.mkdir()
    (source / "pid").write_text(os.environ["MOCK_LIVE_REPLACEMENT_PID"] + "\\n")
    injected.touch()
os.execv(
    sys.executable,
    [sys.executable, {str(LEGACY_LOCK_HELPER)!r}, *sys.argv[1:]],
)
""",
    )

    result = subprocess.run(
        ["bash", str(staged / BACKUP.name), str(destination)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )

    assert result.returncode == 3, result.stderr
    assert "identity changed during verified quarantine" in result.stderr
    assert (legacy_lock / "pid").read_text() == f"{os.getpid()}\n"
    assert displaced_dead_lock.is_dir()
    assert '"tool": "pg_dump"' not in _log_text(env)
    assert not list(destination.glob(".goatfarm-backup.legacy-stale.*"))
    assert (destination / ".goatfarm-backup.flock").is_file()


def test_legacy_helper_restores_replacement_raced_after_final_lstat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / ".goatfarm-backup.lock"
    quarantine = tmp_path / ".goatfarm-backup.legacy-stale"
    displaced = tmp_path / "verified-dead-lock"
    source.mkdir()
    (source / "pid").write_text("999999999\n")
    (source / ".flock-owner").write_text("999999999-1-2\n")
    token = backup_legacy_lock.encode_snapshot(backup_legacy_lock.snapshot(source))
    real_rename = backup_legacy_lock._platform_rename_noreplace
    replacement_injected = False

    def replace_after_lstat(move_source: Path, move_destination: Path) -> None:
        nonlocal replacement_injected
        if not replacement_injected:
            replacement_injected = True
            source.rename(displaced)
            source.mkdir()
            (source / "pid").write_text(f"{os.getpid()}\n")
        real_rename(move_source, move_destination)

    monkeypatch.setattr(
        backup_legacy_lock,
        "_platform_rename_noreplace",
        replace_after_lstat,
    )

    with pytest.raises(
        backup_legacy_lock.LegacyLockError,
        match="restored unexpected lock",
    ):
        backup_legacy_lock.move_verified(source, quarantine, token)

    assert (source / "pid").read_text() == f"{os.getpid()}\n"
    assert displaced.is_dir()
    assert not quarantine.exists()


def test_legacy_helper_never_degrades_to_racy_check_then_rename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unsupported atomic rename must stop, never enter the racy fallback.

    A plain directory rename can replace an empty directory that a legacy
    producer just created.  That producer may populate its path only after our
    post-move verification, so re-reading the destination cannot close the
    race. Fail before moving anything when the filesystem lacks
    RENAME_NOREPLACE/RENAME_EXCL.
    """
    source = tmp_path / ".goatfarm-backup.legacy-claim.private"
    destination = tmp_path / ".goatfarm-backup.lock"
    source.mkdir()
    (source / "pid").write_text("999999999\n")
    (source / ".flock-owner").write_text("999999999-1-2\n")

    def unsupported(_source: bytes, _destination: bytes) -> None:
        raise OSError(errno.EINVAL, "exclusive rename unsupported")

    monkeypatch.setattr(backup_legacy_lock, "_rename_noreplace_darwin", unsupported)
    monkeypatch.setattr(backup_legacy_lock.sys, "platform", "darwin")

    with pytest.raises(OSError, match="exclusive rename unsupported"):
        backup_legacy_lock._platform_rename_noreplace(source, destination)

    assert source.is_dir()
    assert set(path.name for path in source.iterdir()) == {"pid", ".flock-owner"}
    assert not destination.exists()


def test_legacy_helper_rejects_fifo_metadata_without_blocking(tmp_path: Path) -> None:
    lock_path = tmp_path / ".goatfarm-backup.lock"
    lock_path.mkdir()
    os.mkfifo(lock_path / "pid")

    result = subprocess.run(
        [sys.executable, str(LEGACY_LOCK_HELPER), "snapshot", str(lock_path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=2,
    )

    assert result.returncode == 3
    assert "legacy PID file is not a regular file" in result.stderr


def test_backup_killed_while_private_claim_is_complete_never_publishes_partial(
    tmp_path: Path,
) -> None:
    mock_bin = _install_mock_tools(tmp_path)
    env = _base_env(tmp_path, mock_bin)
    destination = tmp_path / "backups"
    staged = _stage_scripts(tmp_path, None)
    publish_ready = tmp_path / "private-claim-complete"
    env["MOCK_PRIVATE_CLAIM_READY"] = str(publish_ready)
    _write_executable(
        staged / LEGACY_LOCK_HELPER.name,
        f"""#!/usr/bin/env python3
import os
import sys
import time
from pathlib import Path

if sys.argv[1] == "move-verified" and ".legacy-claim." in Path(sys.argv[2]).name:
    Path(os.environ["MOCK_PRIVATE_CLAIM_READY"]).touch()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        time.sleep(0.05)
    raise SystemExit(98)
os.execv(
    sys.executable,
    [sys.executable, {str(LEGACY_LOCK_HELPER)!r}, *sys.argv[1:]],
)
""",
    )
    producer = subprocess.Popen(
        ["bash", str(staged / BACKUP.name), str(destination)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    deadline = time.monotonic() + 5
    while not publish_ready.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert publish_ready.exists(), producer.communicate(timeout=1)
    canonical = destination / ".goatfarm-backup.lock"
    private_claims = list(destination.glob(".goatfarm-backup.legacy-claim.*"))
    assert not canonical.exists()
    assert len(private_claims) == 1
    assert (private_claims[0] / "pid").read_text() == f"{producer.pid}\n"
    assert (private_claims[0] / "pid").stat().st_mode & 0o777 == 0o600
    assert (private_claims[0] / ".flock-owner").stat().st_mode & 0o777 == 0o600
    flock_path = destination / ".goatfarm-backup.flock"
    flock_inode = flock_path.stat().st_ino

    os.killpg(producer.pid, signal.SIGKILL)
    producer.communicate(timeout=5)
    assert not canonical.exists()

    retry = _run_backup(destination, _base_env(tmp_path, mock_bin))

    assert retry.returncode == 0, retry.stderr
    assert not canonical.exists()
    assert flock_path.stat().st_ino == flock_inode
    assert _log_text(env).count('"tool": "pg_dump"') == 1


def test_backup_cleanup_does_not_remove_replaced_legacy_claim(tmp_path: Path) -> None:
    mock_bin = _install_mock_tools(tmp_path)
    env = _base_env(tmp_path, mock_bin)
    ready = tmp_path / "cleanup-dump-ready"
    release = tmp_path / "cleanup-dump-release"
    env.update({"MOCK_DUMP_READY": str(ready), "MOCK_DUMP_RELEASE": str(release)})
    destination = tmp_path / "backups"
    legacy_lock = destination / ".goatfarm-backup.lock"
    displaced_lock = destination / ".goatfarm-backup.displaced-claim"
    producer = subprocess.Popen(
        ["bash", str(BACKUP), str(destination)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 5
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert ready.exists(), producer.communicate(timeout=1)

    legacy_lock.rename(displaced_lock)
    legacy_lock.mkdir()
    (legacy_lock / "pid").write_text(f"{os.getpid()}\n")
    (legacy_lock / ".flock-owner").write_text("replacement-owner\n")
    release.touch()
    stdout, stderr = producer.communicate(timeout=10)

    assert producer.returncode == 0, stdout + stderr
    assert "Refusing to clean a legacy lock not owned by this run" in stderr
    assert (legacy_lock / "pid").read_text() == f"{os.getpid()}\n"
    assert (legacy_lock / ".flock-owner").read_text() == "replacement-owner\n"
    assert displaced_lock.is_dir()


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


def test_health_compliance_migration_renders_safe_offline_preflight_and_validation() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "upgrade",
            "a6c9e2f4b7d1:b7c8d9e0f1a2",
            "--sql",
        ],
        cwd=REPO_ROOT / "backend",
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "DO $health_compliance_preflight$" in result.stdout
    assert "Sample health_event ids" in result.stdout
    assert "LIMIT 20" in result.stdout
    assert "ck_health_events_compliance_requires_suspicion" in result.stdout
    assert "NOT VALID" in result.stdout
    assert "VALIDATE CONSTRAINT ck_health_events_compliance_requires_suspicion" in result.stdout


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


def test_failed_offsite_cleanup_names_the_orphaned_object_keys(
    tmp_path: Path,
) -> None:
    """RT-N-2: when the rollback deletion of a half-published remote pair
    itself fails (e.g. credentials revoked mid-run), the run must say so on
    stderr with the exact object keys — a silent failure accumulates orphaned
    archive objects forever with no operator signal."""
    mock_bin = _install_mock_tools(tmp_path)
    env = _base_env(tmp_path, mock_bin)
    env.update(
        {
            "GOATFARM_BACKUP_GPG_RECIPIENT": SIGNER_B,
            "GOATFARM_BACKUP_GPG_SIGNER_FINGERPRINT": SIGNER_A,
            "GOATFARM_BACKUP_S3_URI": "s3://example/goatfarm",
            "MOCK_AWS_FAIL_CHECKSUM": "1",
            "MOCK_AWS_FAIL_RM": "1",
        }
    )
    destination = tmp_path / "backups"

    result = _run_backup(destination, env)

    assert result.returncode != 0
    # The archive upload succeeded before the checksum upload failed, so the
    # orphaned object is the remote archive key; it must be named on stderr.
    assert "failed to remove partially published remote object" in result.stderr
    assert "s3://example/goatfarm/goatfarm-" in result.stderr
    assert ".dump.gpg" in result.stderr


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


def test_offsite_backups_use_distinct_remote_keys_for_same_second_producers(
    tmp_path: Path,
) -> None:
    """Independent destination locks must not make the same S3 object name.

    Both producers see an empty remote listing and deliberately receive the
    same UTC second.  The run suffix must still make their archive/checksum
    destinations distinct; otherwise two hosts would race the unconditional
    S3 PUTs and could leave a mismatched archive/sidecar pair.
    """
    mock_bin = _install_mock_tools(tmp_path)
    _write_executable(
        mock_bin / "date",
        """#!/usr/bin/env bash
if [[ "$1" == "-u" ]]; then
    echo "2026-08-17T12-00-00Z"
else
    echo "2026-08-17T12:00:00+00:00"
fi
""",
    )
    env = _base_env(tmp_path, mock_bin)
    env.update(
        {
            "GOATFARM_BACKUP_GPG_RECIPIENT": SIGNER_B,
            "GOATFARM_BACKUP_GPG_SIGNER_FINGERPRINT": SIGNER_A,
            "GOATFARM_BACKUP_S3_URI": "s3://example/goatfarm",
        }
    )

    first = _run_backup(tmp_path / "producer-one", env)
    second = _run_backup(tmp_path / "producer-two", env)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    aws_calls = [
        json.loads(line)
        for line in _log_text(env).splitlines()
        if json.loads(line)["tool"] == "aws" and "cp" in json.loads(line)["argv"]
    ]
    remote_archives = {
        call["argv"][-1] for call in aws_calls if not call["argv"][-1].endswith(".sha256")
    }
    remote_checksums = {
        call["argv"][-1] for call in aws_calls if call["argv"][-1].endswith(".sha256")
    }
    assert len(remote_archives) == 2
    assert {f"{archive}.sha256" for archive in remote_archives} == remote_checksums
    assert all("goatfarm-2026-08-17T12-00-00Z-" in archive for archive in remote_archives)


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
    assert '"tool": "pg_restore"' not in log
    assert '"tool": "psql"' not in log


def test_restore_uses_private_snapshot_paths(tmp_path: Path) -> None:
    mock_bin = _install_mock_tools(tmp_path)
    env = _base_env(tmp_path, mock_bin)
    archive = tmp_path / "goatfarm.dump"
    archive.write_bytes(b"original verified archive")
    _write_checksum(archive)
    result = _run_restore(archive, env)

    assert result.returncode == 0, result.stderr
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


def test_restore_rechecks_emptiness_under_release_lock_in_restore_transaction(
    tmp_path: Path,
) -> None:
    """A DDL writer racing the friendly preflight must not leave an extra
    object beside an otherwise successful restore."""
    mock_bin = _install_mock_tools(tmp_path)
    env = _base_env(tmp_path, mock_bin)
    env["MOCK_ATOMIC_USER_OBJECT_COUNT"] = "1"
    archive = tmp_path / "goatfarm.dump"
    archive.write_bytes(b"archive")
    _write_checksum(archive)

    result = _run_restore(archive, env)

    assert result.returncode != 0
    assert "acquired user schema objects" in result.stderr
    calls = [json.loads(line) for line in _log_text(env).splitlines()]
    transaction_calls = [
        call for call in calls if call["tool"] == "psql" and "--single-transaction" in call["argv"]
    ]
    assert len(transaction_calls) == 1
    transaction_args = "\n".join(transaction_calls[0]["argv"])
    assert "pg_try_advisory_xact_lock(718204614)" in transaction_args
    assert "atomic_restore_guard" in transaction_args
    assert "--file=" in transaction_args
    assert "FROM alembic_version" not in _log_text(env)
    assert not list((tmp_path / "restore-tmp").iterdir())


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
    parsed_compose = yaml.safe_load(compose)
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

    db_probe = parsed_compose["services"]["db"]["healthcheck"]["test"]
    assert db_probe[0] == "CMD-SHELL"
    assert "pg_isready -h 127.0.0.1" in db_probe[1]
    assert parsed_compose["services"]["backend"]["stop_grace_period"] == "40s"

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


def test_backend_docker_context_excludes_operator_secrets_and_backup_artifacts() -> None:
    """Docker sends its whole nonignored context to the builder before COPY.

    The backend image's narrow COPY instructions do not protect a root .env or
    the backup.sh default ./backups directory from a remote BuildKit context
    upload. Keep these exclusions explicit instead of relying on .gitignore.
    """
    patterns = {
        line.strip()
        for line in (REPO_ROOT / ".dockerignore").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert {
        ".env",
        ".env.*",
        "backups/",
        "*.dump",
        "*.dump.gpg",
        "*.dump.sha256",
        "*.dump.gpg.sha256",
    } <= patterns


def test_frontend_is_a_standalone_node_container() -> None:
    config = (REPO_ROOT / "frontend" / "next.config.ts").read_text()
    dockerfile = (REPO_ROOT / "frontend" / "Dockerfile").read_text()
    assert 'output: "standalone"' in config
    assert "poweredByHeader: false" in config
    assert 'CMD ["node", "server.js"]' in dockerfile

    # npm is required while building, but not by Next's standalone runtime.
    # Shipping the base image's global npm tree needlessly exposed all of its
    # transitive packages to production image vulnerability scans.
    runner = dockerfile.split(" AS runner", maxsplit=1)[1]
    assert "/usr/local/lib/node_modules/npm" in runner
    assert "/usr/local/bin/npm" in runner
    assert "/usr/local/bin/npx" in runner
    assert runner.index("rm -rf /usr/local/lib/node_modules/npm") < runner.index(
        'CMD ["node", "server.js"]'
    )


def _stage_scripts(tmp_path: Path, env_file: str | None) -> Path:
    """Copy the scripts under a private backend/ tree so a test can control the
    backend/.env they read without touching the repository's own file."""
    staged = tmp_path / "staged-backend" / "scripts"
    staged.mkdir(parents=True)
    for source in (
        BACKUP,
        RESTORE,
        URL_HELPER,
        DOTENV_HELPER,
        FLOCK_HELPER,
        LEGACY_LOCK_HELPER,
        PINNED_COPY_HELPER,
        ENV_LIB,
    ):
        shutil.copy(source, staged / source.name)
    if env_file is not None:
        (staged.parent / ".env").write_text(env_file)
    return staged


def test_dotenv_helper_parses_a_pinned_snapshot_across_path_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A name race must not silently downgrade deployment safety settings."""
    env_file = tmp_path / ".env"
    env_file.write_text("GOATFARM_ENVIRONMENT=production\n")
    real_dotenv_values = __import__("dotenv").dotenv_values

    def remove_name_before_parse(*args: object, **kwargs: object) -> object:
        env_file.unlink()
        return real_dotenv_values(*args, **kwargs)

    monkeypatch.setattr(__import__("dotenv"), "dotenv_values", remove_name_before_parse)
    monkeypatch.setattr(
        sys,
        "argv",
        ["dotenv_value.py", str(env_file), "GOATFARM_ENVIRONMENT"],
    )

    assert dotenv_value.main() == 0
    assert capsys.readouterr().out == "production"


def test_dotenv_helper_bounds_a_growing_or_oversized_config_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_bytes(b"A" * (dotenv_value._MAX_ENV_FILE_BYTES + 1))
    monkeypatch.setattr(
        sys,
        "argv",
        ["dotenv_value.py", str(env_file), "GOATFARM_ENVIRONMENT"],
    )

    assert dotenv_value.main() == 2
    assert "file exceeds" in capsys.readouterr().err


def test_dotenv_helper_rejects_fifo_without_blocking(tmp_path: Path) -> None:
    """Regular-file validation must happen without waiting for a FIFO writer."""
    env_file = tmp_path / ".env"
    os.mkfifo(env_file)

    result = subprocess.run(
        [sys.executable, str(DOTENV_HELPER), str(env_file), "GOATFARM_ENVIRONMENT"],
        capture_output=True,
        text=True,
        check=False,
        timeout=2,
    )

    assert result.returncode == 2
    assert "not a regular file" in result.stderr


@pytest.mark.parametrize(
    ("operation", "expected_error"),
    [
        ("backup", "authenticated GPG encryption"),
        ("restore", "authenticated GPG backup"),
    ],
)
def test_deployment_safety_settings_come_from_one_env_file_snapshot(
    tmp_path: Path,
    operation: str,
    expected_error: str,
) -> None:
    """Atomic replacement/removal cannot mix two .env generations.

    Before the safety settings were loaded together, the first helper could
    read ``verify-full`` from a production file that disappeared before the
    second helper. The second lookup then defaulted to ``development`` and both
    scripts permitted an unsigned artifact. This Python shim deterministically
    removes the pathname after the helper's first completed read: one snapshot
    retains both production values, while the old two-call implementation
    reproduced the unsafe mixed configuration.
    """
    mock_bin = _install_mock_tools(tmp_path)
    env = _base_env(tmp_path, mock_bin)
    env.pop("GOATFARM_ENVIRONMENT")
    env.pop("GOATFARM_DB_SSLMODE")
    staged = _stage_scripts(
        tmp_path,
        "GOATFARM_ENVIRONMENT=production\nGOATFARM_DB_SSLMODE=verify-full\n",
    )
    env_file = staged.parent / ".env"
    marker = tmp_path / "dotenv-helper-returned"
    env.update(
        {
            "REAL_PYTHON": sys.executable,
            "RACE_ENV_FILE": str(env_file),
            "RACE_MARKER": str(marker),
        }
    )
    _write_executable(
        mock_bin / "python3",
        """#!/bin/sh
"${REAL_PYTHON}" "$@"
status=$?
if [ "${1##*/}" = "dotenv_value.py" ] && [ ! -e "${RACE_MARKER}" ]; then
    rm -f -- "${RACE_ENV_FILE}"
    : > "${RACE_MARKER}"
fi
exit "${status}"
""",
    )

    if operation == "backup":
        command = ["bash", str(staged / "backup.sh"), str(tmp_path / "dest")]
    else:
        archive = tmp_path / "goatfarm.dump"
        archive.write_bytes(b"archive")
        _write_checksum(archive)
        env["GOATFARM_RESTORE_DATABASE_URL"] = (
            "postgresql://restore_user:secret@db.invalid:5432/goatfarm_restore_test"
        )
        env["GOATFARM_RESTORE_CONFIRM"] = "goatfarm_restore_test"
        command = ["bash", str(staged / "restore.sh"), str(archive)]

    result = subprocess.run(
        command,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )

    assert result.returncode == 2
    assert expected_error in result.stderr
    assert marker.exists()
    assert _log_text(env) == ""  # the mixed snapshot never reaches a database tool


@pytest.mark.parametrize(
    "env_file",
    [
        "export GOATFARM_ENVIRONMENT=production\nexport GOATFARM_DB_SSLMODE=verify-full\n",
        'GOATFARM_ENVIRONMENT="production" # deployment mode\n'
        "GOATFARM_DB_SSLMODE='verify-full' # authenticated TLS\n",
        "GOATFARM_ENVIRONMENT=production # deployment mode\n"
        "GOATFARM_DB_SSLMODE=verify-full # authenticated TLS\n",
        "goatfarm_environment=production\ngoatfarm_db_sslmode=verify-full\n",
    ],
    ids=["export", "quoted-and-commented", "unquoted-comments", "case-insensitive"],
)
def test_backup_reads_the_application_env_file_for_its_safety_gates(
    tmp_path: Path,
    env_file: str,
) -> None:
    """The TLS and mandatory-GPG gates key off GOATFARM_ENVIRONMENT and
    GOATFARM_DB_SSLMODE, which config.py reads from backend/.env. A cron entry
    that exports only GOATFARM_DATABASE_URL used to silently degrade a
    production host to an unsigned plaintext dump over a connection this script
    then forces to `disable` — and exit 0."""
    mock_bin = _install_mock_tools(tmp_path)
    env = _base_env(tmp_path, mock_bin)
    env.pop("GOATFARM_ENVIRONMENT")
    env.pop("GOATFARM_DB_SSLMODE")
    staged = _stage_scripts(tmp_path, env_file)

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


@pytest.mark.parametrize(
    "env_file",
    [
        "export GOATFARM_ENVIRONMENT=production\nexport GOATFARM_DB_SSLMODE=verify-full\n",
        'GOATFARM_ENVIRONMENT="production" # deployment mode\n'
        "GOATFARM_DB_SSLMODE='verify-full' # authenticated TLS\n",
        "GOATFARM_ENVIRONMENT=production # deployment mode\n"
        "GOATFARM_DB_SSLMODE=verify-full # authenticated TLS\n",
        "goatfarm_environment=production\ngoatfarm_db_sslmode=verify-full\n",
    ],
    ids=["export", "quoted-and-commented", "unquoted-comments", "case-insensitive"],
)
def test_restore_reads_the_application_env_file_for_its_safety_gates(
    tmp_path: Path,
    env_file: str,
) -> None:
    mock_bin = _install_mock_tools(tmp_path)
    env = _base_env(tmp_path, mock_bin)
    env.pop("GOATFARM_ENVIRONMENT")
    env.pop("GOATFARM_DB_SSLMODE")
    staged = _stage_scripts(tmp_path, env_file)
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


@pytest.mark.parametrize("operation", ["backup", "restore"])
def test_absent_env_file_requires_explicit_classification(tmp_path: Path, operation: str) -> None:
    """RT-N-4: with backend/.env absent and neither classification exported,
    the run must fail instead of silently declassifying itself to development
    (plaintext local backups of password hashes, no GPG, sslmode disable)."""
    mock_bin = _install_mock_tools(tmp_path)
    env = _base_env(tmp_path, mock_bin)
    env.pop("GOATFARM_ENVIRONMENT")
    env.pop("GOATFARM_DB_SSLMODE")
    staged = _stage_scripts(tmp_path, None)

    if operation == "backup":
        command = ["bash", str(staged / "backup.sh"), str(tmp_path / "dest")]
    else:
        archive = tmp_path / "goatfarm.dump"
        archive.write_bytes(b"archive")
        _write_checksum(archive)
        env["GOATFARM_RESTORE_DATABASE_URL"] = (
            "postgresql://restore_user:secret@db.invalid:5432/goatfarm_restore_test"
        )
        env["GOATFARM_RESTORE_CONFIRM"] = "goatfarm_restore_test"
        command = ["bash", str(staged / "restore.sh"), str(archive)]

    result = subprocess.run(
        command,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )

    assert result.returncode == 2
    assert "backend/.env is missing" in result.stderr
    assert _log_text(env) == ""  # never reached a database tool


def test_env_file_removed_before_the_pinned_read_fails_closed(tmp_path: Path) -> None:
    """RT-N-4: the absence guard must not be a check-then-default race.  A
    file removed between the existence check and the pinned snapshot read
    resolves to the sentinel default, not to development."""
    mock_bin = _install_mock_tools(tmp_path)
    env = _base_env(tmp_path, mock_bin)
    env.pop("GOATFARM_ENVIRONMENT")
    env.pop("GOATFARM_DB_SSLMODE")
    staged = _stage_scripts(tmp_path, "GOATFARM_ENVIRONMENT=production\n")
    env_file = staged.parent / ".env"
    env.update({"REAL_PYTHON": sys.executable, "RACE_ENV_FILE": str(env_file)})
    _write_executable(
        mock_bin / "python3",
        """#!/bin/sh
if [ "${1##*/}" = "dotenv_value.py" ]; then
    rm -f -- "${RACE_ENV_FILE}"
fi
"${REAL_PYTHON}" "$@"
""",
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
    assert "backend/.env is missing" in result.stderr
    assert _log_text(env) == ""


def test_present_env_file_without_classification_keys_keeps_defaults(
    tmp_path: Path,
) -> None:
    """Only absence fails closed: a present backend/.env that simply omits the
    classification keys keeps the documented development defaults (mirroring
    config.py), so ordinary dev boxes with a bare .env keep working."""
    mock_bin = _install_mock_tools(tmp_path)
    env = _base_env(tmp_path, mock_bin)
    env.pop("GOATFARM_ENVIRONMENT")
    env.pop("GOATFARM_DB_SSLMODE")
    staged = _stage_scripts(tmp_path, "GOATFARM_DATABASE_URL=postgresql://x@db:5432/dev\n")

    result = subprocess.run(
        ["bash", str(staged / "backup.sh"), str(tmp_path / "dest")],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr


def _render_compose_network(
    *,
    subnet: str = "198.18.243.0/24",
    edge_address: str = "198.18.243.10",
    public_scheme: str = "http",
    edge_bind_host: str = "127.0.0.1",
    trusted_proxy_hosts: str | None = None,
) -> dict:
    """Emulate Compose interpolation for the network variables.

    Inner defaults resolve first; the trusted-proxy value is the nested form
    `${GOATFARM_TRUSTED_PROXY_HOSTS:-${GOATFARM_EDGE_PROXY_IP:-...}}`, so an
    unset override falls back to the edge address and an explicit override
    (an outer load balancer's addresses) wins.
    """
    compose = (REPO_ROOT / "docker-compose.yml").read_text()
    compose = compose.replace(
        "${GOATFARM_DOCKER_SUBNET:-198.18.243.0/24}",
        subnet,
    ).replace(
        "${GOATFARM_EDGE_PROXY_IP:-198.18.243.10}",
        edge_address,
    )
    compose = compose.replace(
        f"${{GOATFARM_TRUSTED_PROXY_HOSTS:-{edge_address}}}",
        trusted_proxy_hosts if trusted_proxy_hosts is not None else edge_address,
    )
    compose = compose.replace("${GOATFARM_EDGE_PUBLIC_SCHEME:-http}", public_scheme)
    compose = compose.replace("${GOATFARM_EDGE_BIND_HOST:-127.0.0.1}", edge_bind_host)
    return yaml.safe_load(compose)


def test_compose_publishes_only_one_edge_that_forwards_the_real_client_address() -> None:
    """Next's rewrite proxy never emits X-Forwarded-For, so routing browser
    /api traffic through the SPA container made every client share the Next
    container's address: the 11th signup in five minutes — from anyone — got
    429, and 100 bad logins locked the whole deployment out."""
    compose = _render_compose_network()
    services = compose["services"]

    # The SPA and API containers are internal-only entry points.
    assert "ports" not in services["frontend"]
    assert "ports" not in services["backend"]
    assert "8000" in services["backend"]["expose"]
    edge = services["edge"]
    assert "127.0.0.1:3000:3000" in edge["ports"]

    # `$$` is Compose's escape; nginx receives single-dollar variables.
    proxy_conf = compose["configs"]["edge_proxy"]["content"].replace("$$", "$")
    assert "proxy_set_header Host $http_host;" in proxy_conf
    assert "proxy_set_header Host $host;" not in proxy_conf
    assert "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;" in proxy_conf
    assert "proxy_set_header X-Forwarded-Proto http;" in proxy_conf
    assert "$http_x_forwarded_proto" not in proxy_conf
    assert "$$" not in proxy_conf
    assert "location /api/" in proxy_conf
    # Both upstreams must be re-resolved per request. nginx caches a literal
    # proxy_pass hostname for the worker's lifetime, so a redeployed backend
    # (a new container IP that Docker's IPAM does not reissue) left this edge
    # — which compose does not recreate with its dependencies — 502'ing every
    # request until it was restarted by hand. A variable upstream plus the
    # embedded DNS resolver is what forces re-resolution, and a variable in
    # proxy_pass drops the automatic URI pass-through, so $request_uri has to
    # be restated or every path would collapse to "/".
    assert "resolver 127.0.0.11" in proxy_conf
    assert "proxy_pass http://backend:8000;" not in proxy_conf
    assert "proxy_pass http://frontend:3000;" not in proxy_conf
    assert "set $backend_upstream backend;" in proxy_conf
    assert "proxy_pass http://$backend_upstream:8000$request_uri;" in proxy_conf
    assert "set $frontend_upstream frontend;" in proxy_conf
    assert "proxy_pass http://$frontend_upstream:3000$request_uri;" in proxy_conf

    # The backend trusts exactly the edge's fixed address — not the bridge
    # range, which also covers the docker gateway and other containers.
    edge_address = edge["networks"]["goatfarm_app"]["ipv4_address"]
    trusted = services["backend"]["environment"]["GOATFARM_TRUSTED_PROXY_HOSTS"]
    assert trusted == edge_address
    subnet = compose["networks"]["goatfarm_app"]["ipam"]["config"][0]["subnet"]
    assert ipaddress.ip_address(edge_address) in ipaddress.ip_network(subnet)

    # RT-R-2: two segments — the database is reachable only from the backend
    # and the migration job; the edge and frontend cannot route to it.
    def _nets(name: str) -> set[str]:
        attached = services[name]["networks"]
        return set(attached)

    assert _nets("db") == {"goatfarm_data"}
    assert _nets("migrate") == {"goatfarm_data"}
    assert _nets("frontend") == {"goatfarm_app"}
    assert _nets("edge") == {"goatfarm_app"}
    assert _nets("backend") == {"goatfarm_app", "goatfarm_data"}
    assert compose["networks"]["goatfarm_data"]["ipam"]["config"][0]["subnet"] != subnet
    # And the value the compose file ships must satisfy the settings contract.
    assert Settings(trusted_proxy_hosts=edge_address).trusted_proxy_hosts == edge_address


def test_compose_production_edge_requires_an_asserted_https_terminator() -> None:
    """Avoid silently serving production bearer responses over raw HTTP.

    Compose cannot prove what an outer load balancer does, but it can make the
    required assertion explicit and refuse the dangerous production+HTTP
    default before nginx starts.  The loopback binding keeps the raw listener
    inaccessible from the network unless an operator deliberately overrides
    it for a documented TLS topology.
    """
    compose = _render_compose_network(public_scheme="https")
    edge = compose["services"]["edge"]
    command = "\n".join(edge["command"])

    assert edge["ports"] == ["127.0.0.1:3000:3000"]
    assert edge["environment"]["GOATFARM_ENVIRONMENT"] == "${GOATFARM_ENVIRONMENT:-development}"
    assert edge["environment"]["GOATFARM_EDGE_PUBLIC_SCHEME"] == "https"
    assert '"$$GOATFARM_ENVIRONMENT" = "production"' in command
    assert '"$$GOATFARM_EDGE_PUBLIC_SCHEME" != "https"' in command
    assert "Refusing production edge without an asserted HTTPS terminator" in command

    public = _render_compose_network(edge_bind_host="0.0.0.0", public_scheme="https")
    assert public["services"]["edge"]["ports"] == ["0.0.0.0:3000:3000"]


def test_edge_auth_flood_zone_is_scoped_and_explicit() -> None:
    """RT-R-1: the nginx auth flood shaping must stay scoped and explicit.

    The zone keys on the edge's own view of the client, applies only to the
    unauthenticated password endpoints, and answers 429 (nginx's default
    limit_req_status is 503, which clients and dashboards misread).
    """
    compose = _render_compose_network()
    proxy_conf = compose["configs"]["edge_proxy"]["content"].replace("$$", "$")

    assert "limit_req_zone $binary_remote_addr zone=auth_flood:10m rate=5r/s;" in proxy_conf

    # Extract each location block and require exactly one throttled one.
    locations = re.findall(r"location (\S+) \{", proxy_conf)
    assert "/api/auth/" in locations
    auth_block = re.search(r"location /api/auth/ \{(.*?)\n\s*\}", proxy_conf, re.DOTALL)
    assert auth_block is not None
    assert "limit_req zone=auth_flood burst=20 nodelay;" in auth_block.group(1)
    assert "limit_req_status 429;" in auth_block.group(1)
    # No other location may throttle: shaping the whole API would couple
    # normal traffic to the login-flood budget.
    for name in locations:
        if name == "/api/auth/":
            continue
        other_block = re.search(
            rf"location {re.escape(name)} \{{(.*?)\n\s*\}}", proxy_conf, re.DOTALL
        )
        assert other_block is not None
        assert "limit_req " not in other_block.group(1), name


def test_edge_refuses_dev_public_bind_with_escape_hatch() -> None:
    """RT-R-4: a non-loopback bind outside production refuses with exit 2.

    The refusal names both remediations and honours the explicit
    GOATFARM_ALLOW_DEV_PUBLIC_BIND=true opt-out for firewalled staging boxes.
    """
    compose = _render_compose_network()
    command = "\n".join(compose["services"]["edge"]["command"])

    # Loopback spellings pass the case-list without triggering the guard
    # (the renderer resolves the bind-host interpolation to its default).
    assert "127.0.0.1|localhost|::1) ;;" in command
    # The guard itself: production-exempt, opt-out-aware, exit 2, loud.
    assert '"$$GOATFARM_ENVIRONMENT" != "production"' in command
    assert '"$${GOATFARM_ALLOW_DEV_PUBLIC_BIND:-false}" != "true"' in command
    assert "Refusing to bind the edge" in command
    assert "GOATFARM_ALLOW_DEV_PUBLIC_BIND=true" in command
    assert "exit 2" in command


def test_compose_network_override_avoids_collision_without_weakening_proxy_trust() -> None:
    default = _render_compose_network()
    overridden = _render_compose_network(
        subnet="198.19.88.0/24",
        edge_address="198.19.88.37",
    )

    default_network = ipaddress.ip_network(
        default["networks"]["goatfarm_app"]["ipam"]["config"][0]["subnet"]
    )
    override_network = ipaddress.ip_network(
        overridden["networks"]["goatfarm_app"]["ipam"]["config"][0]["subnet"]
    )
    override_edge = overridden["services"]["edge"]["networks"]["goatfarm_app"]["ipv4_address"]
    override_trust = overridden["services"]["backend"]["environment"][
        "GOATFARM_TRUSTED_PROXY_HOSTS"
    ]

    assert not default_network.overlaps(override_network)
    assert ipaddress.ip_address(override_edge) in override_network
    assert override_trust == override_edge
    assert Settings(trusted_proxy_hosts=override_trust).trusted_proxy_hosts == override_edge

    # An explicit GOATFARM_TRUSTED_PROXY_HOSTS still wins over the edge-IP
    # default: deployments behind an additional outer proxy/load balancer
    # must be able to trust its address too, or every client keys the per-IP
    # auth ceilings as the balancer's single IP.
    lb = _render_compose_network(trusted_proxy_hosts="198.18.243.10,203.0.113.9")
    lb_trust = lb["services"]["backend"]["environment"]["GOATFARM_TRUSTED_PROXY_HOSTS"]
    assert lb_trust == "198.18.243.10,203.0.113.9"


def test_compose_public_scheme_is_static_and_operator_controlled() -> None:
    local = _render_compose_network(public_scheme="http")
    tls_terminated = _render_compose_network(public_scheme="https")

    local_proxy = local["configs"]["edge_proxy"]["content"].replace("$$", "$")
    tls_proxy = tls_terminated["configs"]["edge_proxy"]["content"].replace("$$", "$")
    assert "proxy_set_header X-Forwarded-Proto http;" in local_proxy
    assert "proxy_set_header X-Forwarded-Proto https;" in tls_proxy
    assert "$http_x_forwarded_proto" not in local_proxy + tls_proxy


def test_ci_cancels_only_superseded_pull_requests() -> None:
    for workflow_name in ("ci.yml", "security.yml"):
        workflow = (REPO_ROOT / ".github" / "workflows" / workflow_name).read_text()
        assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in workflow
        assert "cancel-in-progress: true" not in workflow


def test_private_repository_codeql_keeps_results_without_unavailable_upload() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "security.yml").read_text()

    assert "actions: read" in workflow
    assert "github/codeql-action/init@24c7eb380a2dc368f2d129e4c65e51d172983a1e # v4" in workflow
    assert "github/codeql-action/analyze@24c7eb380a2dc368f2d129e4c65e51d172983a1e # v4" in workflow
    assert "upload: never" in workflow
    assert "upload-database: false" in workflow
    assert "output: codeql-results/${{ matrix.language }}" in workflow
    assert "name: codeql-${{ matrix.language }}-sarif" in workflow
    assert "path: ${{ steps.codeql-analyze.outputs.sarif-output }}" in workflow
    assert "if-no-files-found: error" in workflow


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


def test_migrations_and_restores_share_the_same_release_writer_lock() -> None:
    alembic_env = (REPO_ROOT / "backend" / "alembic" / "env.py").read_text()
    restore = RESTORE.read_text()

    assert "RELEASE_WRITER_ADVISORY_LOCK_ID = 718204614" in alembic_env
    assert "pg_try_advisory_lock({RELEASE_WRITER_ADVISORY_LOCK_ID})" in alembic_env
    assert "pg_try_advisory_xact_lock(718204614)" in restore


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
