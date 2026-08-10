"""Regression coverage for audit-found one-time migration hazards."""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
from datetime import UTC, datetime

import asyncpg
import pytest

from .conftest import BACKEND_DIR, TEST_DB

A4_PARENT = "a4d9e6f2b701"
C8_MONEY = "c8f1d3a5e709"
B9_PARENT = "f7d8c9b0a1e2"
CORRECTION_PARENT = "e3f4a5b6c7d9"
CORRECTION = "a6c9e2f4b7d1"
LEGACY_LOSS_NOTE = "Legacy pregnancy-loss row; original date and cause were not captured."
ADMIN_URL = "postgresql://localhost:5432/postgres"


def _throwaway_name(suffix: str) -> str:
    database = f"{TEST_DB}_{suffix}"
    assert re.fullmatch(r"[A-Za-z0-9_]+", database)
    assert "_test" in database
    return database


async def _admin(sql: str) -> None:
    connection = await asyncpg.connect(ADMIN_URL)
    try:
        await connection.execute(sql)
    finally:
        await connection.close()


async def _alembic(
    database: str,
    *args: str,
    succeeds: bool = True,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GOATFARM_DATABASE_URL"] = f"postgresql+asyncpg://localhost:5432/{database}"
    env.pop("GOATFARM_MIGRATION_DATABASE_URL", None)
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if succeeds:
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        assert result.returncode != 0, result.stdout + result.stderr
    return result


async def test_exact_money_migration_refuses_lossy_rows_and_backfills_utc() -> None:
    database = _throwaway_name("exact_money")
    await _admin(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
    await _admin(f'CREATE DATABASE "{database}"')
    await _admin(f"ALTER DATABASE \"{database}\" SET timezone TO 'America/Phoenix'")
    database_url = f"postgresql://localhost:5432/{database}"
    try:
        await _alembic(database, "upgrade", A4_PARENT)
        connection = await asyncpg.connect(database_url)
        try:
            actor_id = await connection.fetchval(
                """
                INSERT INTO users (email, password_hash, created_at)
                VALUES ('money-migration@example.test', 'not-used', timezone('UTC', now()))
                RETURNING id
                """
            )
            farm_id = await connection.fetchval(
                """
                INSERT INTO farms (name, owner_id, created_at)
                VALUES ('Money Migration', $1, timezone('UTC', now()))
                RETURNING id
                """,
                actor_id,
            )
            transaction_id = await connection.fetchval(
                """
                INSERT INTO transactions (farm_id, date, type, category, amount)
                VALUES ($1, CURRENT_DATE, 'EXPENSE', 'OTHER', 1.001)
                RETURNING id
                """,
                farm_id,
            )
            task_id = await connection.fetchval(
                """
                INSERT INTO tasks (
                  farm_id, title, due_date, status, category, auto_generated
                ) VALUES ($1, 'Legacy skipped task', CURRENT_DATE, 'SKIPPED', 'OTHER', false)
                RETURNING id
                """,
                farm_id,
            )
        finally:
            await connection.close()

        subcent = await _alembic(database, "upgrade", C8_MONEY, succeeds=False)
        output = subcent.stdout + subcent.stderr
        assert "transactions.amount contains sub-cent values" in output
        assert f"row ids [{transaction_id}]" in output

        connection = await asyncpg.connect(database_url)
        try:
            # The entire failed revision rolls back before changing a column.
            assert (
                await connection.fetchval(
                    """
                    SELECT data_type FROM information_schema.columns
                    WHERE table_name = 'transactions' AND column_name = 'amount'
                    """
                )
                == "double precision"
            )
            await connection.execute(
                "UPDATE transactions SET amount = 'NaN' WHERE id = $1",
                transaction_id,
            )
        finally:
            await connection.close()

        nonfinite = await _alembic(database, "upgrade", C8_MONEY, succeeds=False)
        output = nonfinite.stdout + nonfinite.stderr
        assert "transactions.amount contains non-finite values" in output
        assert f"row ids [{transaction_id}]" in output

        connection = await asyncpg.connect(database_url)
        try:
            await connection.execute(
                "UPDATE transactions SET amount = 1.01 WHERE id = $1",
                transaction_id,
            )
        finally:
            await connection.close()

        before = datetime.now(UTC).replace(tzinfo=None)
        await _alembic(database, "upgrade", C8_MONEY)
        after = datetime.now(UTC).replace(tzinfo=None)

        connection = await asyncpg.connect(database_url)
        try:
            stored_amount, skipped_at, session_timezone = await connection.fetchrow(
                """
                SELECT tx.amount::text, task.skipped_at, current_setting('TimeZone')
                FROM transactions tx CROSS JOIN tasks task
                WHERE tx.id = $1 AND task.id = $2
                """,
                transaction_id,
                task_id,
            )
        finally:
            await connection.close()
        assert stored_amount == "1.01"
        assert before <= skipped_at <= after
        assert session_timezone == "America/Phoenix"
    finally:
        await _admin(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')


async def test_corrective_migration_clears_fabricated_actor_and_serializes_dates() -> None:
    database = _throwaway_name("reproductive_correction")
    await _admin(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
    await _admin(f'CREATE DATABASE "{database}"')
    database_url = f"postgresql://localhost:5432/{database}"
    try:
        await _alembic(database, "upgrade", B9_PARENT)
        connection = await asyncpg.connect(database_url)
        try:
            actor_id = await connection.fetchval(
                """
                INSERT INTO users (email, password_hash, created_at)
                VALUES ('loss-migration@example.test', 'not-used', timezone('UTC', now()))
                RETURNING id
                """
            )
            farm_id = await connection.fetchval(
                """
                INSERT INTO farms (name, owner_id, created_at)
                VALUES ('Loss Migration', $1, timezone('UTC', now()))
                RETURNING id
                """,
                actor_id,
            )
            animal_ids: list[int] = []
            for tag, sex in (("DOE-MIG", "F"), ("BUCK-MIG", "M")):
                animal_ids.append(
                    await connection.fetchval(
                        """
                        INSERT INTO animals (
                          farm_id, tag_number, breed, sex, source, current_bucket,
                          status, cull_candidate, created_at, movement_restricted,
                          suspected_scheduled_disease
                        ) VALUES (
                          $1, $2, 'Test', $3, 'PURCHASED', 'FOUNDATION',
                          'ACTIVE', false, timezone('UTC', now()), false, false
                        ) RETURNING id
                        """,
                        farm_id,
                        tag,
                        sex,
                    )
                )
            legacy_breeding_id = await connection.fetchval(
                """
                INSERT INTO breeding_records (
                  farm_id, doe_id, buck_id, breeding_date, method,
                  heat_cycle_number, ultrasound_done, pregnant, outcome,
                  created_by_id
                ) VALUES (
                  $1, $2, $3, DATE '2026-01-01', 'NATURAL',
                  1, true, false, 'ABORTED', $4
                ) RETURNING id
                """,
                farm_id,
                animal_ids[0],
                animal_ids[1],
                actor_id,
            )
        finally:
            await connection.close()

        await _alembic(database, "upgrade", "head")
        connection = await asyncpg.connect(database_url)
        try:
            repaired = await connection.fetchrow(
                """
                SELECT loss_cause, loss_notes, loss_recorded_by_id
                FROM breeding_records WHERE id = $1
                """,
                legacy_breeding_id,
            )
            assert repaired == ("UNKNOWN", LEGACY_LOSS_NOTE, None)

            function_definition = await connection.fetchval(
                "SELECT pg_get_functiondef('enforce_kid_mortality_chronology()'::regprocedure)"
            )
            assert "FOR UPDATE" in function_definition
            assert "FOR KEY SHARE" not in function_definition

            schema_objects = {
                row[0]
                for row in await connection.fetch(
                    """
                    SELECT conname FROM pg_constraint
                    WHERE conname IN (
                      'uq_feed_inventory_farm_id_id',
                      'fk_transactions_farm_feed_inventory',
                      'ck_transactions_feed_purchase_provenance'
                    )
                    UNION ALL
                    SELECT indexname FROM pg_indexes
                    WHERE indexname = 'ix_transactions_feed_inventory_id'
                    """
                )
            }
            assert schema_objects == {
                "uq_feed_inventory_farm_id_id",
                "fk_transactions_farm_feed_inventory",
                "ck_transactions_feed_purchase_provenance",
                "ix_transactions_feed_inventory_id",
            }
            assert (
                await connection.fetchval(
                    """
                    SELECT count(*) FROM information_schema.columns
                    WHERE table_name = 'transactions'
                      AND column_name IN (
                        'feed_inventory_id',
                        'feed_quantity_kg',
                        'feed_unit_price_per_kg'
                      )
                    """
                )
                == 3
            )

            confirmed_breeding_id = await connection.fetchval(
                """
                INSERT INTO breeding_records (
                  farm_id, doe_id, buck_id, breeding_date, method,
                  heat_cycle_number, ultrasound_date, ultrasound_result_date,
                  ultrasound_done, pregnant, kid_count_detected,
                  expected_kidding_date, outcome, created_by_id
                ) VALUES (
                  $1, $2, $3, DATE '2026-02-01', 'NATURAL', 1,
                  DATE '2026-03-05', DATE '2026-03-05', true, true, 1,
                  DATE '2026-07-01', 'CONFIRMED_PREGNANT', $4
                ) RETURNING id
                """,
                farm_id,
                animal_ids[0],
                animal_ids[1],
                actor_id,
            )
            kidding_id = await connection.fetchval(
                """
                INSERT INTO kidding_records (
                  farm_id, doe_id, date, breeding_record_id, ease, created_by_id
                ) VALUES ($1, $2, DATE '2026-07-01', $3, 'NORMAL', $4)
                RETURNING id
                """,
                farm_id,
                animal_ids[0],
                confirmed_breeding_id,
                actor_id,
            )
        finally:
            await connection.close()

        kid_writer = await asyncpg.connect(database_url)
        date_writer = await asyncpg.connect(database_url)
        kid_transaction = kid_writer.transaction()
        date_transaction = date_writer.transaction()
        try:
            await kid_transaction.start()
            await kid_writer.execute(
                """
                INSERT INTO kid_entries (
                  farm_id, kidding_record_id, sex, status, mortality_reported_at
                ) VALUES ($1, $2, 'F', 'DIED', DATE '2026-07-02')
                """,
                farm_id,
                kidding_id,
            )

            await date_transaction.start()
            await date_writer.execute("SET LOCAL lock_timeout = '250ms'")
            with pytest.raises(asyncpg.LockNotAvailableError):
                await date_writer.execute(
                    "UPDATE kidding_records SET date = DATE '2026-07-03' WHERE id = $1",
                    kidding_id,
                )
            await date_transaction.rollback()
            await kid_transaction.rollback()
        finally:
            if date_writer.is_in_transaction():
                await date_transaction.rollback()
            if kid_writer.is_in_transaction():
                await kid_transaction.rollback()
            await date_writer.close()
            await kid_writer.close()

        # Schema/function downgrade is reversible, but truth-preserving actor
        # clearing deliberately is not.
        await _alembic(database, "downgrade", CORRECTION_PARENT)
        connection = await asyncpg.connect(database_url)
        try:
            downgraded_function = await connection.fetchval(
                "SELECT pg_get_functiondef('enforce_kid_mortality_chronology()'::regprocedure)"
            )
            assert "FOR KEY SHARE" in downgraded_function
            assert (
                await connection.fetchval(
                    """
                    SELECT count(*) FROM information_schema.columns
                    WHERE table_name = 'transactions'
                      AND column_name IN (
                        'feed_inventory_id',
                        'feed_quantity_kg',
                        'feed_unit_price_per_kg'
                      )
                    """
                )
                == 0
            )
            assert (
                await connection.fetchval(
                    "SELECT loss_recorded_by_id FROM breeding_records WHERE id = $1",
                    legacy_breeding_id,
                )
                is None
            )
        finally:
            await connection.close()

        await _alembic(database, "upgrade", CORRECTION)
    finally:
        await _admin(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
