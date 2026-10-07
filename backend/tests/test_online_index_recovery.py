"""Recovery of interrupted PostgreSQL CONCURRENTLY index migrations."""

import asyncio
import os
import subprocess
import sys
from contextlib import suppress

import asyncpg
import pytest

from app.db import get_engine

from .conftest import BACKEND_DIR, TEST_DIRECT_URL


async def _alembic(*args: str, succeeds: bool = True) -> subprocess.CompletedProcess[str]:
    migration_env = os.environ.copy()
    # Every database used by this module is created and destroyed by the test
    # session. Crossing the legacy-review cutover after a deliberate downgrade
    # is therefore the rehearsed/quiesced case that the production gate asks
    # operators to acknowledge.
    migration_env["GOATFARM_MIGRATION_WRITES_QUIESCED"] = "true"
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env=migration_env,
        check=False,
        capture_output=True,
        text=True,
    )
    if succeeds:
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        assert result.returncode != 0, result.stdout + result.stderr
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
        ("fb2c3d4e5f6a", "ix_tasks_farm_terminal_finished_id", "tasks"),
        (
            "fd4e5f6a7b8c",
            "ix_screening_images_raw_cleanup_due",
            "screening_images",
        ),
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

    connection = await asyncpg.connect(TEST_DIRECT_URL)
    try:
        # Reference seeding gives this table many rows. The deliberately
        # impossible unique expression fails after creating its catalog
        # entry, reproducing PostgreSQL's interrupted-build remnant using
        # only supported SQL (no system-catalog mutation).
        with suppress(asyncpg.UniqueViolationError):
            await connection.execute(
                f'CREATE UNIQUE INDEX CONCURRENTLY "{index_name}" ON bucket_definitions ((1))'
            )
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

    connection = await asyncpg.connect(TEST_DIRECT_URL)
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


async def test_terminal_history_upgrade_rebuilds_valid_wrong_definition() -> None:
    """A name-only IF NOT EXISTS match must not stamp the wrong index."""
    await get_engine().dispose()
    await _alembic("downgrade", "fb2c3d4e5f6a")

    connection = await asyncpg.connect(TEST_DIRECT_URL)
    try:
        await connection.execute(
            "CREATE INDEX CONCURRENTLY ix_tasks_farm_terminal_finished_id ON tasks (id)"
        )
    finally:
        await connection.close()

    await _alembic("upgrade", "head")

    connection = await asyncpg.connect(TEST_DIRECT_URL)
    try:
        definition = await connection.fetchval(
            """
            SELECT pg_get_indexdef(indexrelid)
            FROM pg_index
            WHERE indexrelid = 'ix_tasks_farm_terminal_finished_id'::regclass
            """
        )
        assert definition is not None
        assert "farm_id" in definition
        assert "CASE" in definition
        assert "status" in definition
        assert "skipped_at" in definition
        assert "completed_at" in definition
        assert "WHERE" in definition
    finally:
        await connection.close()


async def test_terminal_history_upgrade_preserves_valid_cross_table_collision() -> None:
    """A migration retry must not delete a valid unrelated schema object."""
    await get_engine().dispose()
    await _alembic("downgrade", "fb2c3d4e5f6a")

    connection = await asyncpg.connect(TEST_DIRECT_URL)
    try:
        await connection.execute(
            "CREATE INDEX CONCURRENTLY ix_tasks_farm_terminal_finished_id "
            "ON bucket_definitions (id)"
        )
    finally:
        await connection.close()

    failed = await _alembic("upgrade", "head", succeeds=False)
    assert "refusing to drop an unrelated schema object" in failed.stderr

    connection = await asyncpg.connect(TEST_DIRECT_URL)
    try:
        indexed_table = await connection.fetchval(
            """
            SELECT indexed.relname
            FROM pg_class AS idx
            JOIN pg_index AS i ON i.indexrelid = idx.oid
            JOIN pg_class AS indexed ON indexed.oid = i.indrelid
            WHERE idx.relname = 'ix_tasks_farm_terminal_finished_id'
            """
        )
        assert indexed_table == "bucket_definitions"
        await connection.execute("DROP INDEX CONCURRENTLY ix_tasks_farm_terminal_finished_id")
    finally:
        await connection.close()

    await _alembic("upgrade", "head")


async def test_raw_cleanup_due_upgrade_rebuilds_valid_wrong_definition() -> None:
    """A name-only match cannot hide a wrong due-queue index."""
    await get_engine().dispose()
    await _alembic("downgrade", "fd4e5f6a7b8c")

    connection = await asyncpg.connect(TEST_DIRECT_URL)
    try:
        await connection.execute(
            "CREATE INDEX CONCURRENTLY ix_screening_images_raw_cleanup_due ON screening_images (id)"
        )
    finally:
        await connection.close()

    await _alembic("upgrade", "head")

    connection = await asyncpg.connect(TEST_DIRECT_URL)
    try:
        definition = await connection.fetchval(
            """
            SELECT pg_get_indexdef(indexrelid)
            FROM pg_index
            WHERE indexrelid = 'ix_screening_images_raw_cleanup_due'::regclass
            """
        )
        assert definition is not None
        normalized = " ".join(str(definition).lower().split())
        assert "raw_cleanup_next_attempt_at" in normalized
        assert "raw_cleanup_completed_at is null" in normalized
        assert "raw_cleanup_next_attempt_at is not null" in normalized
    finally:
        await connection.close()


async def test_raw_cleanup_due_upgrade_preserves_valid_cross_table_collision() -> None:
    """Retry recovery must never drop a valid unrelated index."""
    await get_engine().dispose()
    await _alembic("downgrade", "fd4e5f6a7b8c")

    connection = await asyncpg.connect(TEST_DIRECT_URL)
    try:
        await connection.execute(
            "CREATE INDEX CONCURRENTLY ix_screening_images_raw_cleanup_due "
            "ON bucket_definitions (id)"
        )
    finally:
        await connection.close()

    failed = await _alembic("upgrade", "head", succeeds=False)
    assert "refusing to drop an unrelated schema object" in failed.stderr

    connection = await asyncpg.connect(TEST_DIRECT_URL)
    try:
        indexed_table = await connection.fetchval(
            """
            SELECT indexed.relname
            FROM pg_class AS idx
            JOIN pg_index AS i ON i.indexrelid = idx.oid
            JOIN pg_class AS indexed ON indexed.oid = i.indrelid
            WHERE idx.relname = 'ix_screening_images_raw_cleanup_due'
            """
        )
        assert indexed_table == "bucket_definitions"
        await connection.execute("DROP INDEX CONCURRENTLY ix_screening_images_raw_cleanup_due")
    finally:
        await connection.close()

    await _alembic("upgrade", "head")
