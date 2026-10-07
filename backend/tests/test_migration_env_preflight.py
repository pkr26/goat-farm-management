"""Target-aware Alembic environment preflight regressions."""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys

import asyncpg
import pytest

from .conftest import BACKEND_DIR, TEST_DB, _admin_sql, database_direct_url


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    """This module owns only its explicitly named disposable database."""


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    """No shared application database is created or truncated by this module."""


def test_migration_quiescence_preflight_is_target_and_command_aware() -> None:
    """Inspection, old schemas, multiple heads, and downgrades stay usable.

    The f9 review-history and fd screening-privacy cutovers still fail closed
    on every forward path that crosses them, including a real historical
    two-head state whose schema does not yet have
    ``screening_findings.review_revision``.
    """
    database = f"{TEST_DB}_migration_preflight"
    assert re.fullmatch(r"[A-Za-z0-9_]+", database)
    assert "_test" in database
    target_url = database_direct_url(database).replace("postgresql://", "postgresql+asyncpg://", 1)

    def database_sql(statement: str) -> None:
        async def execute() -> None:
            connection = await asyncpg.connect(database_direct_url(database))
            try:
                await connection.execute(statement)
            finally:
                await connection.close()

        asyncio.run(execute())

    def run_alembic(*args: str, writes_quiesced: bool = False) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["GOATFARM_DATABASE_URL"] = target_url
        env["GOATFARM_MIGRATION_DATABASE_URL"] = target_url
        env.pop("GOATFARM_MIGRATION_WRITES_QUIESCED", None)
        if writes_quiesced:
            env["GOATFARM_MIGRATION_WRITES_QUIESCED"] = "true"
        return subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=BACKEND_DIR,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )

    _admin_sql(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
    _admin_sql(f'CREATE DATABASE "{database}"')
    try:
        merge = run_alembic("upgrade", "b7e8f9a0c1d2")
        assert merge.returncode == 0, merge.stdout + merge.stderr

        # b7 is a no-op merge. Recreate the exact pre-merge two-head stamp;
        # its schema already contains both parent revisions.
        database_sql("DELETE FROM alembic_version")
        database_sql(
            "INSERT INTO alembic_version (version_num) VALUES ('a6d4e2f9c8b7'), ('c4d8e1f9a2b7')"
        )

        current = run_alembic("current")
        current_output = current.stdout + current.stderr
        assert current.returncode == 0, current_output
        assert "a6d4e2f9c8b7" in current_output
        assert "c4d8e1f9a2b7" in current_output
        assert "KeyError" not in current_output

        check_old = run_alembic("check")
        check_old_output = check_old.stdout + check_old.stderr
        assert check_old.returncode != 0
        assert "Target database is not up to date" in check_old_output
        assert "KeyError" not in check_old_output

        blocked = run_alembic("upgrade", "f9a3b7c1d5e2")
        blocked_output = blocked.stdout + blocked.stderr
        assert blocked.returncode != 0
        assert "require application writes to be drained" in blocked_output
        assert "eligible_legacy_review_rows=not-yet-queryable" in blocked_output

        intermediate = run_alembic("upgrade", "f8e2f6a0c5d3")
        assert intermediate.returncode == 0, intermediate.stdout + intermediate.stderr
        blocked_at_parent = run_alembic("upgrade", "f9a3b7c1d5e2")
        blocked_at_parent_output = blocked_at_parent.stdout + blocked_at_parent.stderr
        assert blocked_at_parent.returncode != 0
        assert "require application writes to be drained" in blocked_at_parent_output
        assert "eligible_legacy_review_rows=0" in blocked_at_parent_output

        allowed = run_alembic("upgrade", "f9a3b7c1d5e2", writes_quiesced=True)
        assert allowed.returncode == 0, allowed.stdout + allowed.stderr

        # A relative downgrade used to be parsed as an impossible relative
        # upgrade by the preflight and failed before Alembic could execute it.
        relative_downgrade = run_alembic("downgrade", "-1")
        assert relative_downgrade.returncode == 0, (
            relative_downgrade.stdout + relative_downgrade.stderr
        )
        after_downgrade = run_alembic("current")
        assert after_downgrade.returncode == 0, after_downgrade.stdout + after_downgrade.stderr
        assert "f8e2f6a0c5d3" in after_downgrade.stdout + after_downgrade.stderr

        # The requested intermediate stop is not allowed to preflight f9.
        intermediate_noop = run_alembic("upgrade", "f8e2f6a0c5d3")
        assert intermediate_noop.returncode == 0, (
            intermediate_noop.stdout + intermediate_noop.stderr
        )

        # Once the older f9 cutover is behind us, fd independently requires
        # the same explicit drained-writer release protocol. Stopping at its
        # fc parent must not accidentally preflight or execute fd.
        to_fd_parent = run_alembic("upgrade", "fc3d4e5f6a7b", writes_quiesced=True)
        assert to_fd_parent.returncode == 0, to_fd_parent.stdout + to_fd_parent.stderr
        blocked_fd = run_alembic("upgrade", "fd4e5f6a7b8c")
        blocked_fd_output = blocked_fd.stdout + blocked_fd.stderr
        assert blocked_fd.returncode != 0
        assert "fd4e5f6a7b8c" in blocked_fd_output
        assert "require application writes to be drained" in blocked_fd_output

        to_head = run_alembic("upgrade", "head", writes_quiesced=True)
        assert to_head.returncode == 0, to_head.stdout + to_head.stderr
        check_head = run_alembic("check")
        assert check_head.returncode == 0, check_head.stdout + check_head.stderr
        assert "No new upgrade operations detected" in check_head.stdout + check_head.stderr
    finally:
        _admin_sql(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
