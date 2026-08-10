"""Round-trip coverage for data repair in the domain-audit migration."""

import asyncio
import os
import subprocess
import sys
from datetime import date, timedelta

import asyncpg
import httpx
import pytest

from app.db import get_engine
from app.models import Farm
from app.utils import today

from .conftest import BACKEND_DIR, TEST_DB, owner_with_farm
from .test_health_extended import make_animal

# The legacy-era fixtures below live ~20 revisions beneath head. Downgrading
# the shared suite database that far (and back) is slow and would tear through
# state other tests rely on, so those tests borrow the throwaway-database
# helpers instead of this module's shared-database `_alembic`.
from .test_ops_migration_integrity import _admin, _throwaway_name
from .test_ops_migration_integrity import _alembic as _alembic_on

PARENT_REVISION = "d1c2b3a4e5f6"
HEALTH_COMPLIANCE_PARENT_REVISION = "a6c9e2f4b7d1"
REPRODUCTIVE_PARENT_REVISION = "f7d8c9b0a1e2"
EXACT_MONEY_REVISION = "c8f1d3a5e709"
EXACT_MONEY_PARENT_REVISION = "a4d9e6f2b701"


async def _alembic(*args: str) -> None:
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


async def test_migration_unlinks_unsafe_inference_but_retains_word_match(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    acquired_on = today() - timedelta(days=45)
    animal = await make_animal(
        client,
        owner,
        tag="MIGRATION-SCHEDULE",
        purchase_date=acquired_on.isoformat(),
    )
    born_on = today() - timedelta(days=30)
    born = await make_animal(
        client,
        owner,
        tag="MIGRATION-BORN",
        source="BORN",
        date_of_birth=born_on.isoformat(),
        historical_import_reason="Existing born animal",
    )
    await get_engine().dispose()
    try:
        await _alembic("downgrade", PARENT_REVISION)
        connection = await asyncpg.connect(f"postgresql://localhost:5432/{TEST_DB}")
        try:
            downgraded_trigger = await connection.fetchval(
                """
                SELECT pg_get_triggerdef(oid)
                FROM pg_trigger
                WHERE tgname = 'trg_kidding_reproductive_outcome'
                """
            )
            assert "UPDATE OF breeding_record_id, date ON" in downgraded_trigger
            assert "doe_id" not in downgraded_trigger
            assert "farm_id" not in downgraded_trigger
            ppr_id = await connection.fetchval(
                "SELECT id FROM vaccine_templates WHERE name = 'PPR'"
            )
            unsafe_id = await connection.fetchval(
                """
                INSERT INTO health_events (
                  farm_id, animal_id, date, type, product_name,
                  schedule_template_id, suspected_scheduled_disease
                ) VALUES ($1, $2, $3, 'VACCINE', 'Suppressor vaccine', $4, false)
                RETURNING id
                """,
                int(owner["X-Farm-Id"]),
                animal["id"],
                today(),
                ppr_id,
            )
            safe_id = await connection.fetchval(
                """
                INSERT INTO health_events (
                  farm_id, animal_id, date, type, product_name,
                  schedule_template_id, suspected_scheduled_disease
                ) VALUES ($1, $2, $3, 'VACCINE', 'PPR vaccine', $4, false)
                RETURNING id
                """,
                int(owner["X-Farm-Id"]),
                animal["id"],
                today(),
                ppr_id,
            )
        finally:
            await connection.close()

        await _alembic("upgrade", "head")
        connection = await asyncpg.connect(f"postgresql://localhost:5432/{TEST_DB}")
        try:
            rows = {
                row["id"]: row["schedule_template_id"]
                for row in await connection.fetch(
                    """
                    SELECT id, schedule_template_id
                    FROM health_events
                    WHERE id = ANY($1::int[])
                    """,
                    [unsafe_id, safe_id],
                )
            }
            effective_date = await connection.fetchval(
                """
                SELECT effective_date
                FROM bucket_moves
                WHERE animal_id = $1 AND from_bucket IS NULL
                """,
                animal["id"],
            )
            born_effective_date = await connection.fetchval(
                """
                SELECT effective_date
                FROM bucket_moves
                WHERE animal_id = $1 AND from_bucket IS NULL
                """,
                born["id"],
            )
            upgraded_trigger = await connection.fetchval(
                """
                SELECT pg_get_triggerdef(oid)
                FROM pg_trigger
                WHERE tgname = 'trg_kidding_reproductive_outcome'
                """
            )
        finally:
            await connection.close()
        assert rows[unsafe_id] is None
        assert rows[safe_id] == ppr_id
        assert effective_date == acquired_on
        assert born_effective_date == born_on
        assert "UPDATE OF breeding_record_id, date, doe_id, farm_id ON" in upgraded_trigger
    finally:
        await get_engine().dispose()
        await _alembic("upgrade", "head")


async def test_health_compliance_migration_fails_safely_until_legacy_evidence_is_reconciled(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="MIGRATION-COMPLIANCE")
    event_date = today()
    await get_engine().dispose()
    try:
        await _alembic("downgrade", HEALTH_COMPLIANCE_PARENT_REVISION)
        connection = await asyncpg.connect(f"postgresql://localhost:5432/{TEST_DB}")
        try:
            event_id = await connection.fetchval(
                """
                INSERT INTO health_events (
                  farm_id, animal_id, date, type,
                  suspected_scheduled_disease,
                  authority_notified_at, isolation_started_at
                ) VALUES ($1, $2, $3, 'TREATMENT', false, $3, $3)
                RETURNING id
                """,
                int(owner["X-Farm-Id"]),
                animal["id"],
                event_date,
            )
        finally:
            await connection.close()

        with pytest.raises(AssertionError, match="Sample health_event ids"):
            await _alembic("upgrade", "head")
        connection = await asyncpg.connect(f"postgresql://localhost:5432/{TEST_DB}")
        try:
            row = await connection.fetchrow(
                """
                SELECT suspected_scheduled_disease, disease_target,
                       authority_notified_at, isolation_started_at
                FROM health_events
                WHERE id = $1
                """,
                event_id,
            )
            constraint_exists = await connection.fetchval(
                """
                SELECT EXISTS (
                  SELECT 1
                  FROM pg_constraint
                  WHERE conname = 'ck_health_events_compliance_requires_suspicion'
                )
                """
            )
            await connection.execute(
                """
                UPDATE health_events
                SET suspected_scheduled_disease = true,
                    disease_target = 'Operator-reconciled scheduled disease'
                WHERE id = $1
                """,
                event_id,
            )
        finally:
            await connection.close()
        assert row is not None
        assert row["suspected_scheduled_disease"] is False
        assert row["disease_target"] is None
        assert row["authority_notified_at"] == event_date
        assert row["isolation_started_at"] == event_date
        assert constraint_exists is False

        await _alembic("upgrade", "head")
        connection = await asyncpg.connect(f"postgresql://localhost:5432/{TEST_DB}")
        try:
            reconciled = await connection.fetchrow(
                """
                SELECT suspected_scheduled_disease, disease_target,
                       authority_notified_at, isolation_started_at
                FROM health_events
                WHERE id = $1
                """,
                event_id,
            )
            constraint_validated = await connection.fetchval(
                """
                SELECT convalidated
                FROM pg_constraint
                WHERE conname = 'ck_health_events_compliance_requires_suspicion'
                """
            )
            with pytest.raises(
                asyncpg.CheckViolationError,
                match="ck_health_events_compliance_requires_suspicion",
            ):
                await connection.execute(
                    """
                    INSERT INTO health_events (
                      farm_id, animal_id, date, type,
                      suspected_scheduled_disease, authority_notified_at
                    ) VALUES ($1, $2, $3, 'TREATMENT', false, $3)
                    """,
                    int(owner["X-Farm-Id"]),
                    animal["id"],
                    event_date,
                )
        finally:
            await connection.close()
        assert reconciled is not None
        assert reconciled["suspected_scheduled_disease"] is True
        assert reconciled["disease_target"] == "Operator-reconciled scheduled disease"
        assert reconciled["authority_notified_at"] == event_date
        assert reconciled["isolation_started_at"] == event_date
        assert constraint_validated is True
    finally:
        await get_engine().dispose()
        await _alembic("upgrade", "head")


async def test_reproductive_migration_preserves_recorded_death_dates_and_skips_living() -> None:
    """b9's dead-animal alignment must not rewrite history it did not create.

    A DIED birth entry can belong to an animal that survived the neonatal
    window and died much later with an accurate recorded status_date, or to an
    animal that is still alive. The backfilled kidding date is only a floor
    for animals that never captured any death date; stamping the others either
    corrupts the recorded death date or (for a living animal) violates
    ck_animals_status_date when c1d2e3f4a5b6 validates it, aborting the
    release.
    """
    database = _throwaway_name("b9_death_alignment")
    await _admin(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
    await _admin(f'CREATE DATABASE "{database}"')
    database_url = f"postgresql://localhost:5432/{database}"
    kidding_date = date(2024, 12, 1)
    adult_death_date = date(2025, 8, 1)
    try:
        await _alembic_on(database, "upgrade", REPRODUCTIVE_PARENT_REVISION)
        connection = await asyncpg.connect(database_url)
        try:
            actor_id = await connection.fetchval(
                """
                INSERT INTO users (email, password_hash, created_at)
                VALUES ('death-alignment@example.test', 'not-used', timezone('UTC', now()))
                RETURNING id
                """
            )
            farm_id = await connection.fetchval(
                """
                INSERT INTO farms (name, owner_id, created_at)
                VALUES ('Death Alignment', $1, timezone('UTC', now()))
                RETURNING id
                """,
                actor_id,
            )
            doe_id = await connection.fetchval(
                """
                INSERT INTO animals (
                  farm_id, tag_number, breed, sex, source, current_bucket,
                  status, cull_candidate, created_at, movement_restricted,
                  suspected_scheduled_disease
                ) VALUES (
                  $1, 'DOE-B9', 'Test', 'F', 'PURCHASED', 'FOUNDATION',
                  'ACTIVE', false, timezone('UTC', now()), false, false
                ) RETURNING id
                """,
                farm_id,
            )
            kidding_id = await connection.fetchval(
                """
                INSERT INTO kidding_records (farm_id, doe_id, date, ease, created_by_id)
                VALUES ($1, $2, $3, 'NORMAL', $4)
                RETURNING id
                """,
                farm_id,
                doe_id,
                kidding_date,
                actor_id,
            )
            kid_ids: dict[str, int] = {}
            for tag, status, status_date in (
                # Survived weaning, died as an adult: the app already recorded
                # the real death date, which the migration must preserve.
                ("KID-GROWN-DEAD", "DEAD", adult_death_date),
                # Entry flipped to DIED but the animal was never transitioned:
                # it must not receive death fields while ACTIVE.
                ("KID-LIVING", "ACTIVE", None),
                # Genuine legacy neonatal death with no date anywhere: the
                # kidding-date floor is the intended, still-working backfill.
                ("KID-NEONATAL", "DEAD", None),
            ):
                kid_ids[tag] = await connection.fetchval(
                    """
                    INSERT INTO animals (
                      farm_id, tag_number, breed, sex, date_of_birth, source,
                      dam_id, current_bucket, status, status_date,
                      cull_candidate, created_at, movement_restricted,
                      suspected_scheduled_disease
                    ) VALUES (
                      $1, $2, 'Test', 'F', $3, 'BORN', $4, 'FEMALE_KIDS',
                      $5, $6, false, timezone('UTC', now()), false, false
                    ) RETURNING id
                    """,
                    farm_id,
                    tag,
                    kidding_date,
                    doe_id,
                    status,
                    status_date,
                )
                await connection.execute(
                    """
                    INSERT INTO kid_entries (
                      farm_id, kidding_record_id, sex, status, animal_id,
                      mortality_reported_at
                    ) VALUES ($1, $2, 'F', 'DIED', $3, NULL)
                    """,
                    farm_id,
                    kidding_id,
                    kid_ids[tag],
                )
        finally:
            await connection.close()

        # Fails under the unguarded UPDATE: the living animal gets stamped and
        # c1d2e3f4a5b6's ck_animals_status_date preflight aborts the release.
        await _alembic_on(database, "upgrade", "head")

        connection = await asyncpg.connect(database_url)
        try:
            animals = {
                row["tag_number"]: row
                for row in await connection.fetch(
                    """
                    SELECT tag_number, status, status_date, mortality_reported_at
                    FROM animals
                    WHERE id = ANY($1::int[])
                    """,
                    list(kid_ids.values()),
                )
            }
            entry_mortalities = [
                row["mortality_reported_at"]
                for row in await connection.fetch(
                    "SELECT mortality_reported_at FROM kid_entries WHERE kidding_record_id = $1",
                    kidding_id,
                )
            ]
        finally:
            await connection.close()

        grown = animals["KID-GROWN-DEAD"]
        assert grown["status"] == "DEAD"
        assert grown["status_date"] == adult_death_date
        assert grown["mortality_reported_at"] == adult_death_date

        living = animals["KID-LIVING"]
        assert living["status"] == "ACTIVE"
        assert living["status_date"] is None
        assert living["mortality_reported_at"] is None

        neonatal = animals["KID-NEONATAL"]
        assert neonatal["status"] == "DEAD"
        assert neonatal["status_date"] == kidding_date
        assert neonatal["mortality_reported_at"] == kidding_date

        # The kid-entry backfill itself (kidding date as the earliest-known
        # mortality floor) is unchanged by the animal-side guards.
        assert entry_mortalities == [kidding_date] * 3
    finally:
        await _admin(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')


async def test_exact_money_migration_preflights_the_amount_business_cap() -> None:
    """c8's preflight must cover the 1e9 bound its own constraint installs.

    A legacy amount in (1e9, ~1e12] was permitted before c8 (only
    ``amount >= 0`` constrained it) and is finite, whole-cent, and below the
    Numeric(14,2) overflow arm — so without a dedicated arm it sails through
    the preflight and aborts the release as a raw check_violation when
    ck_transactions_amount_bounded validates existing rows.
    """
    database = _throwaway_name("c8_amount_cap")
    await _admin(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
    await _admin(f'CREATE DATABASE "{database}"')
    database_url = f"postgresql://localhost:5432/{database}"
    try:
        await _alembic_on(database, "upgrade", EXACT_MONEY_PARENT_REVISION)
        connection = await asyncpg.connect(database_url)
        try:
            actor_id = await connection.fetchval(
                """
                INSERT INTO users (email, password_hash, created_at)
                VALUES ('amount-cap@example.test', 'not-used', timezone('UTC', now()))
                RETURNING id
                """
            )
            farm_id = await connection.fetchval(
                """
                INSERT INTO farms (name, owner_id, created_at)
                VALUES ('Amount Cap', $1, timezone('UTC', now()))
                RETURNING id
                """,
                actor_id,
            )
            transaction_id = await connection.fetchval(
                """
                INSERT INTO transactions (farm_id, date, type, category, amount)
                VALUES ($1, CURRENT_DATE, 'EXPENSE', 'OTHER', 2000000000)
                RETURNING id
                """,
                farm_id,
            )
        finally:
            await connection.close()

        over_limit = await _alembic_on(database, "upgrade", EXACT_MONEY_REVISION, succeeds=False)
        output = over_limit.stdout + over_limit.stderr
        assert "transactions.amount contains over-limit values" in output
        assert f"row ids [{transaction_id}]" in output

        connection = await asyncpg.connect(database_url)
        try:
            # The refusal happens before any DDL: the column is untouched.
            assert (
                await connection.fetchval(
                    """
                    SELECT data_type FROM information_schema.columns
                    WHERE table_name = 'transactions' AND column_name = 'amount'
                    """
                )
                == "double precision"
            )
            # Exactly at the cap is legal; the arm must use a strict >.
            await connection.execute(
                "UPDATE transactions SET amount = 1000000000 WHERE id = $1",
                transaction_id,
            )
        finally:
            await connection.close()

        await _alembic_on(database, "upgrade", EXACT_MONEY_REVISION)
        connection = await asyncpg.connect(database_url)
        try:
            stored_amount = await connection.fetchval(
                "SELECT amount::text FROM transactions WHERE id = $1",
                transaction_id,
            )
        finally:
            await connection.close()
        assert stored_amount == "1000000000.00"
    finally:
        await _admin(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')


async def test_farms_timezone_server_default_and_not_valid_checks_are_installed() -> None:
    """c8's farms.timezone server_default is permanent (unlike a4d9's
    temporary migration-safety booleans), so the ORM must declare it too or
    compare_server_default drift goes uncaught. Likewise the two-phase
    NOT VALID + VALIDATE checks from e3 and a6 must land as fully validated
    constraints, not silently skip the validate step.
    """
    assert Farm.__table__.columns["timezone"].server_default is not None
    connection = await asyncpg.connect(f"postgresql://localhost:5432/{TEST_DB}")
    try:
        rows = await connection.fetch(
            """
            SELECT conname, convalidated FROM pg_constraint
            WHERE contype = 'c'
              AND conname = ANY($1::text[])
            """,
            [
                "ck_tasks_rejection_current_state",
                "ck_transactions_feed_purchase_provenance",
            ],
        )
    finally:
        await connection.close()
    validated = {row["conname"]: row["convalidated"] for row in rows}
    assert validated == {
        "ck_tasks_rejection_current_state": True,
        "ck_transactions_feed_purchase_provenance": True,
    }
