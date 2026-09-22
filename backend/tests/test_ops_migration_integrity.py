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
REFRESH_BOUNDS_PARENT = "e5f6a7b8c9d0"
REFRESH_BOUNDS = "f6a7b8c9d0e1"
PRESET_ROLE_PARENT = "c3d4e5f6a7b1"
PRESET_ROLE_INTEGRITY = "d5e7f9a1b3c4"
KIDDING_LOCK_ORDER_PARENT = PRESET_ROLE_INTEGRITY
KIDDING_LOCK_ORDER = "e7f9a1b3c5d8"
# Autogenerate-drift checks must run at the CURRENT head (backend-core audit
# wave: task provenance/title keys → updated_at/phenotype/vocabularies →
# exact weight numerics + index hygiene → kidding parity backfill →
# screening batches → residual numerics/next-due ceiling/jsonb shape →
# screening upload hardening + finance/planner integrity merge → durable
# screening-content claims).
# Must track the chain head; the 2026-09-20 vocabulary cleanup revision
# (cad1e2f3a4b5) made the previous pinned head stale, breaking every
# upgrade-HEAD + alembic-check assertion in this suite.
HEAD = (
    # screening_images bigint (chain tail: housekeeping -> movement alert)
    "b9d1f3a5c7e9"
)
SCREENING_CONTENT_CLAIMS_PARENT = "b7e8f9a0c1d2"
SCREENING_CONTENT_CLAIMS = "f7a9c1e3b5d7"
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


async def test_screening_content_claim_migration_backfills_canonical_rows() -> None:
    """The new uniqueness boundary preserves existing screening history."""
    database = _throwaway_name("screening_content_claims")
    await _admin(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
    await _admin(f'CREATE DATABASE "{database}"')
    database_url = f"postgresql://localhost:5432/{database}"
    digest = "a" * 64
    other_digest = "b" * 64
    try:
        await _alembic(database, "upgrade", SCREENING_CONTENT_CLAIMS_PARENT)
        connection = await asyncpg.connect(database_url)
        try:
            owner_id = await connection.fetchval(
                """
                INSERT INTO users (email, password_hash, created_at)
                VALUES ('content-claim-owner@example.test', 'not-used', timezone('UTC', now()))
                RETURNING id
                """
            )
            first_farm_id = await connection.fetchval(
                """
                INSERT INTO farms (name, owner_id, created_at, updated_at)
                VALUES ('First claim farm', $1, timezone('UTC', now()), timezone('UTC', now()))
                RETURNING id
                """,
                owner_id,
            )
            second_farm_id = await connection.fetchval(
                """
                INSERT INTO farms (name, owner_id, created_at, updated_at)
                VALUES ('Second claim farm', $1, timezone('UTC', now()), timezone('UTC', now()))
                RETURNING id
                """,
                owner_id,
            )
            first_image_id = await connection.fetchval(
                """
                INSERT INTO screening_images (farm_id, s3_bucket, s3_key, sha256, status)
                VALUES ($1, 'photos', 'raw/first.jpg', $2, 'HEALTHY')
                RETURNING id
                """,
                first_farm_id,
                digest,
            )
            # A historical duplicate keeps its own audit row, while the
            # migration deterministically gives the lowest id the claim.
            duplicate_image_id = await connection.fetchval(
                """
                INSERT INTO screening_images (farm_id, s3_bucket, s3_key, sha256, status)
                VALUES ($1, 'photos', 'raw/duplicate.jpg', $2, 'SKIPPED')
                RETURNING id
                """,
                first_farm_id,
                digest,
            )
            second_farm_image_id = await connection.fetchval(
                """
                INSERT INTO screening_images (farm_id, s3_bucket, s3_key, sha256, status)
                VALUES ($1, 'photos', 'raw/other-farm.jpg', $2, 'HEALTHY')
                RETURNING id
                """,
                second_farm_id,
                digest,
            )
        finally:
            await connection.close()

        await _alembic(database, "upgrade", SCREENING_CONTENT_CLAIMS)
        connection = await asyncpg.connect(database_url)
        try:
            claims = await connection.fetch(
                """
                SELECT farm_id, image_id, sha256
                FROM screening_content_claims
                ORDER BY farm_id, image_id
                """
            )
            assert [(row["farm_id"], row["image_id"], row["sha256"]) for row in claims] == [
                (first_farm_id, first_image_id, digest),
                (second_farm_id, second_farm_image_id, digest),
            ]
            assert duplicate_image_id != first_image_id

            with pytest.raises(asyncpg.UniqueViolationError) as duplicate_claim:
                await connection.execute(
                    """
                    INSERT INTO screening_content_claims (farm_id, image_id, sha256)
                    VALUES ($1, $2, $3)
                    """,
                    first_farm_id,
                    duplicate_image_id,
                    digest,
                )
            assert (
                duplicate_claim.value.constraint_name == "uq_screening_content_claims_farm_sha256"
            )

            with pytest.raises(asyncpg.ForeignKeyViolationError) as cross_tenant_claim:
                await connection.execute(
                    """
                    INSERT INTO screening_content_claims (farm_id, image_id, sha256)
                    VALUES ($1, $2, $3)
                    """,
                    second_farm_id,
                    first_image_id,
                    other_digest,
                )
            assert cross_tenant_claim.value.constraint_name == "fk_screening_content_claims_image"
        finally:
            await connection.close()

        await _alembic(database, "downgrade", SCREENING_CONTENT_CLAIMS_PARENT)
        connection = await asyncpg.connect(database_url)
        try:
            assert (
                await connection.fetchval("SELECT to_regclass('screening_content_claims') IS NULL")
                is True
            )
        finally:
            await connection.close()
    finally:
        await _admin(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')


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


async def test_refresh_compaction_uses_issuance_ids_when_wall_time_moves_backward() -> None:
    """The one-time bound must not log out the newest session/family by clock."""
    database = _throwaway_name("refresh_compaction_order")
    await _admin(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
    await _admin(f'CREATE DATABASE "{database}"')
    database_url = f"postgresql://localhost:5432/{database}"
    try:
        await _alembic(database, "upgrade", REFRESH_BOUNDS_PARENT)
        connection = await asyncpg.connect(database_url)
        try:
            family_user_id = await connection.fetchval(
                """
                INSERT INTO users (email, password_hash, created_at)
                VALUES ('family-order@example.test', 'not-used', timezone('UTC', now()))
                RETURNING id
                """
            )
            family_rows = await connection.fetch(
                """
                INSERT INTO refresh_sessions (
                    user_id, jti, family_id, expires_at, created_at
                )
                SELECT
                    $1,
                    'family-jti-' || ordinal,
                    'family-' || ordinal,
                    TIMESTAMP '2035-01-01 00:00:00',
                    CASE
                        WHEN ordinal = 11 THEN TIMESTAMP '2000-01-01 00:00:00'
                        ELSE TIMESTAMP '2030-01-01 00:00:00' + ordinal * INTERVAL '1 second'
                    END
                FROM generate_series(1, 11) AS ordinal
                RETURNING id, family_id
                """,
                family_user_id,
            )
            oldest_family_id = family_rows[0]["id"]
            newest_family_id = family_rows[-1]["id"]

            session_user_id = await connection.fetchval(
                """
                INSERT INTO users (email, password_hash, created_at)
                VALUES ('session-order@example.test', 'not-used', timezone('UTC', now()))
                RETURNING id
                """
            )
            session_rows = await connection.fetch(
                """
                INSERT INTO refresh_sessions (
                    user_id, jti, family_id, expires_at, created_at
                )
                SELECT
                    $1,
                    'session-jti-' || ordinal,
                    'long-family',
                    TIMESTAMP '2035-01-01 00:00:00',
                    CASE
                        WHEN ordinal = 1025 THEN TIMESTAMP '2000-01-01 00:00:00'
                        ELSE TIMESTAMP '2030-01-01 00:00:00'
                    END
                FROM generate_series(1, 1025) AS ordinal
                RETURNING id
                """,
                session_user_id,
            )
            oldest_session_id = session_rows[0]["id"]
            newest_session_id = session_rows[-1]["id"]

            collision_user_id = await connection.fetchval(
                """
                INSERT INTO users (email, password_hash, created_at)
                VALUES ('family-collision@example.test', 'not-used', timezone('UTC', now()))
                RETURNING id
                """
            )
            await connection.execute(
                """
                INSERT INTO refresh_sessions (
                    user_id, jti, family_id, expires_at, created_at
                ) VALUES
                    ($1, 'collision-jti-1', 'long-family',
                     TIMESTAMP '2035-01-01 00:00:00', TIMESTAMP '2030-01-01 00:00:00'),
                    ($1, 'collision-jti-2', 'long-family',
                     TIMESTAMP '2035-01-01 00:00:00', TIMESTAMP '2030-01-01 00:00:01')
                """,
                collision_user_id,
            )
        finally:
            await connection.close()

        await _alembic(database, "upgrade", REFRESH_BOUNDS)

        connection = await asyncpg.connect(database_url)
        try:
            retained_families = {
                row["id"]
                for row in await connection.fetch(
                    "SELECT id FROM refresh_sessions WHERE user_id = $1",
                    family_user_id,
                )
            }
            assert len(retained_families) == 10
            assert oldest_family_id not in retained_families
            assert newest_family_id in retained_families

            retained_sessions = {
                row["id"]
                for row in await connection.fetch(
                    "SELECT id FROM refresh_sessions WHERE user_id = $1",
                    session_user_id,
                )
            }
            assert len(retained_sessions) == 1024
            assert oldest_session_id not in retained_sessions
            assert newest_session_id in retained_sessions
            assert (
                await connection.fetchval(
                    "SELECT count(*) FROM refresh_sessions WHERE user_id = $1",
                    collision_user_id,
                )
                == 2
            )
        finally:
            await connection.close()
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


async def test_preset_role_code_migration_repairs_duplicates_and_preserves_references() -> None:
    database = _throwaway_name("preset_role_codes")
    await _admin(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
    await _admin(f'CREATE DATABASE "{database}"')
    database_url = f"postgresql://localhost:5432/{database}"
    try:
        await _alembic(database, "upgrade", PRESET_ROLE_PARENT)
        connection = await asyncpg.connect(database_url)
        try:
            owner_id = await connection.fetchval(
                """
                INSERT INTO users (email, password_hash, created_at)
                VALUES ('preset-owner@example.test', 'not-used', timezone('UTC', now()))
                RETURNING id
                """
            )
            worker_id = await connection.fetchval(
                """
                INSERT INTO users (email, password_hash, created_at)
                VALUES ('preset-worker@example.test', 'not-used', timezone('UTC', now()))
                RETURNING id
                """
            )
            farm_id = await connection.fetchval(
                """
                INSERT INTO farms (name, owner_id, created_at)
                VALUES ('Preset Integrity Farm', $1, timezone('UTC', now()))
                RETURNING id
                """,
                owner_id,
            )

            deleted_role_id = await connection.fetchval(
                """
                INSERT INTO roles (
                  farm_id, code, name, permissions, created_at, deleted_at
                ) VALUES (
                  $1, 'VET', 'Old deleted vet', '[]',
                  timezone('UTC', now()), timezone('UTC', now())
                ) RETURNING id
                """,
                farm_id,
            )
            canonical_role_id = await connection.fetchval(
                """
                INSERT INTO roles (farm_id, code, name, permissions, created_at)
                VALUES ($1, 'VET', 'Canonical active vet', '[]', timezone('UTC', now()))
                RETURNING id
                """,
                farm_id,
            )
            duplicate_role_id = await connection.fetchval(
                """
                INSERT INTO roles (farm_id, code, name, permissions, created_at)
                VALUES ($1, 'VET', 'Duplicate active vet', '[]', timezone('UTC', now()))
                RETURNING id
                """,
                farm_id,
            )
            unknown_role_id = await connection.fetchval(
                """
                INSERT INTO roles (farm_id, code, name, permissions, created_at)
                VALUES ($1, 'IMPORTED', 'Imported pseudo-preset', '[]', timezone('UTC', now()))
                RETURNING id
                """,
                farm_id,
            )
            membership_id = await connection.fetchval(
                """
                INSERT INTO farm_memberships (
                  user_id, farm_id, role_id, is_active,
                  account_provisioned_by_farm, created_at
                ) VALUES ($1, $2, $3, true, true, timezone('UTC', now()))
                RETURNING id
                """,
                worker_id,
                farm_id,
                duplicate_role_id,
            )
            task_id = await connection.fetchval(
                """
                INSERT INTO tasks (
                  farm_id, title, due_date, status, category,
                  auto_generated, assigned_role_id
                ) VALUES (
                  $1, 'Retained duplicate-role duty', CURRENT_DATE,
                  'PENDING', 'OTHER', true, $2
                ) RETURNING id
                """,
                farm_id,
                duplicate_role_id,
            )
        finally:
            await connection.close()

        await _alembic(database, "upgrade", PRESET_ROLE_INTEGRITY)
        connection = await asyncpg.connect(database_url)
        try:
            codes = {
                row["id"]: row["code"]
                for row in await connection.fetch(
                    "SELECT id, code FROM roles WHERE farm_id = $1 ORDER BY id",
                    farm_id,
                )
            }
            # A live row wins over an older tombstone; among live rows, the
            # immutable lowest id is canonical. Unknown pseudo-codes become
            # ordinary custom roles.
            assert codes[deleted_role_id] is None
            assert codes[canonical_role_id] == "VET"
            assert codes[duplicate_role_id] is None
            assert codes[unknown_role_id] is None
            assert (
                await connection.fetchval(
                    "SELECT role_id FROM farm_memberships WHERE id = $1", membership_id
                )
                == duplicate_role_id
            )
            assert (
                await connection.fetchval(
                    "SELECT assigned_role_id FROM tasks WHERE id = $1", task_id
                )
                == duplicate_role_id
            )

            index_state = await connection.fetchrow(
                """
                SELECT i.indisvalid, i.indisready, i.indislive
                FROM pg_class AS c
                JOIN pg_index AS i ON i.indexrelid = c.oid
                WHERE c.relname = 'uq_roles_farm_preset_code'
                """
            )
            assert index_state is not None
            assert tuple(index_state) == (True, True, True)

            with pytest.raises(asyncpg.UniqueViolationError) as duplicate_error:
                await connection.execute(
                    """
                    INSERT INTO roles (farm_id, code, name, permissions, created_at)
                    VALUES ($1, 'VET', 'Forbidden duplicate', '[]', timezone('UTC', now()))
                    """,
                    farm_id,
                )
            assert duplicate_error.value.constraint_name == "uq_roles_farm_preset_code"

            with pytest.raises(asyncpg.CheckViolationError) as unknown_error:
                await connection.execute(
                    """
                    INSERT INTO roles (farm_id, code, name, permissions, created_at)
                    VALUES ($1, 'UNKNOWN', 'Forbidden pseudo-preset', '[]', timezone('UTC', now()))
                    """,
                    farm_id,
                )
            assert unknown_error.value.constraint_name == "ck_roles_preset_code"
        finally:
            await connection.close()

        # ORM metadata mirrors both the partial unique index and the CHECK;
        # deployment-time autogenerate drift detection must stay clean.
        await _alembic(database, "upgrade", HEAD)
        await _alembic(database, "check")
        # A preset introduced after f8a2c4e6b1d9's vocabulary must normalize to
        # an ordinary custom role on the way down — never orphan the duties
        # and memberships that reference its row id.
        connection = await asyncpg.connect(database_url)
        try:
            post_f8_role_id = await connection.fetchval(
                """
                INSERT INTO roles (farm_id, code, name, permissions, created_at)
                VALUES ($1, 'ACCOUNTANT', 'Post-f8 preset', '[]', timezone('UTC', now()))
                RETURNING id
                """,
                farm_id,
            )
        finally:
            await connection.close()
        await _alembic(database, "downgrade", PRESET_ROLE_PARENT)
        connection = await asyncpg.connect(database_url)
        try:
            assert (
                await connection.fetchval("SELECT to_regclass('uq_roles_farm_preset_code') IS NULL")
                is True
            )
            assert (
                await connection.fetchval(
                    """
                    SELECT count(*) = 0
                    FROM pg_constraint
                    WHERE conname = 'ck_roles_preset_code'
                    """
                )
                is True
            )
            # Downgrade cannot truthfully reconstruct duplicate routing
            # identities, so the safe normalization remains in place.
            assert (
                await connection.fetchval("SELECT code FROM roles WHERE id = $1", duplicate_role_id)
                is None
            )
            # ...and the post-f8 preset code was normalized the same way, with
            # the row itself surviving for its FK references.
            assert (
                await connection.fetchval("SELECT code FROM roles WHERE id = $1", post_f8_role_id)
                is None
            )
        finally:
            await connection.close()
    finally:
        await _admin(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')


async def test_kidding_trigger_migration_splits_insert_and_update_lock_paths() -> None:
    database = _throwaway_name("kidding_lock_order")
    await _admin(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
    await _admin(f'CREATE DATABASE "{database}"')
    database_url = f"postgresql://localhost:5432/{database}"
    try:
        await _alembic(database, "upgrade", KIDDING_LOCK_ORDER_PARENT)
        connection = await asyncpg.connect(database_url)
        try:
            previous = await connection.fetchval(
                """
                SELECT pg_get_triggerdef(oid)
                FROM pg_trigger
                WHERE tgname = 'trg_kidding_reproductive_outcome'
                """
            )
            assert "BEFORE INSERT OR UPDATE" in previous
            assert "breeding_record_id, date, doe_id, farm_id" in previous
        finally:
            await connection.close()

        await _alembic(database, "upgrade", HEAD)
        connection = await asyncpg.connect(database_url)
        try:
            trigger_rows = await connection.fetch(
                """
                SELECT tgname, pg_get_triggerdef(oid) AS definition
                FROM pg_trigger
                WHERE tgrelid = 'kidding_records'::regclass
                  AND NOT tgisinternal
                ORDER BY tgname
                """
            )
            triggers = {row["tgname"]: row["definition"] for row in trigger_rows}
            assert (
                "BEFORE INSERT ON public.kidding_records"
                in triggers["trg_00_kidding_insert_lock_order"]
            )
            assert (
                "BEFORE INSERT ON public.kidding_records"
                in triggers["trg_kidding_reproductive_outcome"]
            )
            assert "UPDATE" not in triggers["trg_kidding_reproductive_outcome"]
            assert (
                "UPDATE OF farm_id, doe_id, breeding_record_id"
                in triggers["trg_00_kidding_relationship_immutable"]
            )
            assert "UPDATE OF date" in triggers["trg_kidding_date_mortality_guard"]

            breeding_relationship_guard = await connection.fetchval(
                """
                SELECT pg_get_triggerdef(oid)
                FROM pg_trigger
                WHERE tgname = 'trg_00_breeding_relationship_immutable'
                """
            )
            assert "BEFORE UPDATE OF farm_id, doe_id, buck_id" in breeding_relationship_guard

            lock_function = await connection.fetchval(
                """
                SELECT pg_get_functiondef(
                  'lock_kidding_parents_in_lifecycle_order()'::regprocedure
                )
                """
            )
            # Two explicit statements make the row order reviewable and
            # deterministic: the doe lock precedes the breeding-parent lock.
            assert lock_function.index("FROM animals") < lock_function.index(
                "FROM breeding_records"
            )
            assert lock_function.count("FOR UPDATE") == 2
        finally:
            await connection.close()

        # The trigger-only revision must not introduce ORM/table metadata
        # drift while changing the database's procedural lock contract.
        await _alembic(database, "check")
        await _alembic(database, "downgrade", KIDDING_LOCK_ORDER_PARENT)
        connection = await asyncpg.connect(database_url)
        try:
            restored = await connection.fetchval(
                """
                SELECT pg_get_triggerdef(oid)
                FROM pg_trigger
                WHERE tgname = 'trg_kidding_reproductive_outcome'
                """
            )
            assert "BEFORE INSERT OR UPDATE" in restored
            assert "breeding_record_id, date, doe_id, farm_id" in restored
            assert (
                await connection.fetchval(
                    """
                    SELECT count(*)
                    FROM pg_trigger
                    WHERE tgname IN (
                      'trg_00_kidding_insert_lock_order',
                      'trg_00_kidding_relationship_immutable',
                      'trg_kidding_date_mortality_guard'
                    )
                    """
                )
                == 0
            )
            assert (
                await connection.fetchval(
                    """
                    SELECT count(*)
                    FROM pg_trigger
                    WHERE tgname = 'trg_00_breeding_relationship_immutable'
                    """
                )
                == 0
            )
        finally:
            await connection.close()
    finally:
        await _admin(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')


PARITY_BACKFILL_PARENT = "d9e3f7a1b5c4"


async def test_kidding_parity_backfill_numbers_legacy_rows_per_doe() -> None:
    """e0f4a8b2c6d5 backfills NULL parities ordered by (date, id) per doe."""
    database = _throwaway_name("parity_backfill")
    await _admin(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
    await _admin(f'CREATE DATABASE "{database}"')
    database_url = f"postgresql://localhost:5432/{database}"
    try:
        await _alembic(database, "upgrade", PARITY_BACKFILL_PARENT)
        connection = await asyncpg.connect(database_url)
        try:
            user_id = await connection.fetchval(
                """
                INSERT INTO users (email, password_hash, created_at)
                VALUES ('parity-owner@farm.in', 'unused', timezone('UTC', now()))
                RETURNING id
                """
            )
            farm_id = await connection.fetchval(
                """
                INSERT INTO farms (name, owner_id, created_at, updated_at)
                VALUES ('Parity Farm', $1, timezone('UTC', now()), timezone('UTC', now()))
                RETURNING id
                """,
                user_id,
            )
            doe_id = await connection.fetchval(
                """
                INSERT INTO animals (
                    farm_id, tag_number, breed, sex, source, current_bucket, status,
                    cull_candidate, movement_restricted, suspected_scheduled_disease,
                    restriction_version, created_at, updated_at
                )
                VALUES ($1, 'PARITY-DOE', 'Osmanabadi', 'F', 'PURCHASED', 'BREEDING',
                        'ACTIVE', false, false, false, 0,
                        timezone('UTC', now()), timezone('UTC', now()))
                RETURNING id
                """,
                farm_id,
            )
            # Legacy rows: two kiddings on one recorded date (insertion order
            # breaks the tie) and one earlier, all with NULL parity.
            first_id = await connection.fetchval(
                """
                INSERT INTO kidding_records (farm_id, doe_id, date, ease, parity)
                VALUES ($1, $2, DATE '2025-11-02', 'NORMAL', NULL)
                RETURNING id
                """,
                farm_id,
                doe_id,
            )
            same_day_first = await connection.fetchval(
                """
                INSERT INTO kidding_records (farm_id, doe_id, date, ease, parity)
                VALUES ($1, $2, DATE '2026-04-20', 'NORMAL', NULL)
                RETURNING id
                """,
                farm_id,
                doe_id,
            )
            same_day_second = await connection.fetchval(
                """
                INSERT INTO kidding_records (farm_id, doe_id, date, ease, parity)
                VALUES ($1, $2, DATE '2026-04-20', 'NORMAL', NULL)
                RETURNING id
                """,
                farm_id,
                doe_id,
            )
            # A row the service already numbered must not be renumbered.
            kept = await connection.fetchval(
                """
                INSERT INTO kidding_records (farm_id, doe_id, date, ease, parity)
                VALUES ($1, $2, DATE '2026-05-01', 'NORMAL', 4)
                RETURNING id
                """,
                farm_id,
                doe_id,
            )
        finally:
            await connection.close()

        await _alembic(database, "upgrade", HEAD)
        connection = await asyncpg.connect(database_url)
        try:
            parity = {
                row["id"]: row["parity"]
                for row in await connection.fetch(
                    "SELECT id, parity FROM kidding_records WHERE farm_id = $1",
                    farm_id,
                )
            }
            assert parity[first_id] == 1
            assert parity[same_day_first] == 2
            assert parity[same_day_second] == 3
            assert parity[kept] == 4
        finally:
            await connection.close()
    finally:
        await _admin(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
