"""Recovery of interrupted PostgreSQL CONCURRENTLY index migrations."""

import asyncio
import os
import subprocess
import sys

import asyncpg
import pytest

from app.db import get_engine

from .conftest import BACKEND_DIR, TEST_DB


async def _alembic(*args: str) -> subprocess.CompletedProcess[str]:
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env=os.environ.copy(),
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


@pytest.mark.parametrize(
    ("parent_revision", "index_name", "expected_table"),
    [
        ("f0a1b2c3d4e5", "ix_farm_memberships_active_user_id_id", "farm_memberships"),
        ("f0a1b2c3d4e5", "ix_users_deleted_id", "users"),
        ("e9f0a1b2c3d4", "ix_tasks_pending_animal_id_id", "tasks"),
        ("d7e8f9a0b1c2", "ix_feeding_records_farm_date_id", "feeding_records"),
        ("d4e5f6a7b8c9", "ix_bucket_moves_animal_moved_id_desc", "bucket_moves"),
        ("bd201c1cdc1b", "ix_transactions_farm_date_id", "transactions"),
        ("bd201c1cdc1b", "ix_health_events_farm_date_id", "health_events"),
    ],
)
async def test_online_index_upgrade_rebuilds_same_named_invalid_remnant(
    parent_revision: str,
    index_name: str,
    expected_table: str,
) -> None:
    # Re-enter each online revision exactly as a deploy retry would: its parent
    # is stamped, while a killed/failed CREATE INDEX CONCURRENTLY left an
    # invalid relation before Alembic could stamp the revision.
    await get_engine().dispose()
    await _alembic("downgrade", parent_revision)

    connection = await asyncpg.connect(f"postgresql://localhost:5432/{TEST_DB}")
    try:
        try:
            # Reference seeding gives this table many rows. The deliberately
            # impossible unique expression fails after creating its catalog
            # entry, reproducing PostgreSQL's interrupted-build remnant using
            # only supported SQL (no system-catalog mutation).
            await connection.execute(
                f'CREATE UNIQUE INDEX CONCURRENTLY "{index_name}" ON bucket_definitions ((1))'
            )
        except asyncpg.UniqueViolationError:
            pass
        invalid = await connection.fetchrow(
            """
            SELECT i.indisvalid, i.indisready, i.indislive,
                   indexed.relname AS indexed_table
            FROM pg_class AS idx
            JOIN pg_index AS i ON i.indexrelid = idx.oid
            JOIN pg_class AS indexed ON indexed.oid = i.indrelid
            WHERE idx.relname = $1
            """,
            index_name,
        )
        assert invalid is not None
        assert invalid["indisvalid"] is False
        assert invalid["indexed_table"] == "bucket_definitions"
    finally:
        await connection.close()

    await _alembic("upgrade", "head")

    connection = await asyncpg.connect(f"postgresql://localhost:5432/{TEST_DB}")
    try:
        rebuilt = await connection.fetchrow(
            """
            SELECT i.indisvalid, i.indisready, i.indislive,
                   indexed.relname AS indexed_table
            FROM pg_class AS idx
            JOIN pg_index AS i ON i.indexrelid = idx.oid
            JOIN pg_class AS indexed ON indexed.oid = i.indrelid
            WHERE idx.relname = $1
            """,
            index_name,
        )
        assert rebuilt is not None
        assert rebuilt["indisvalid"] is True
        assert rebuilt["indisready"] is True
        assert rebuilt["indislive"] is True
        assert rebuilt["indexed_table"] == expected_table
    finally:
        await connection.close()
