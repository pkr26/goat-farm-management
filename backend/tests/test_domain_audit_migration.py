"""Round-trip coverage for data repair in the domain-audit migration."""

import asyncio
import os
import subprocess
import sys
from datetime import timedelta

import asyncpg
import httpx
import pytest

from app.db import get_engine
from app.utils import today

from .conftest import BACKEND_DIR, TEST_DB, owner_with_farm
from .test_health_extended import make_animal

PARENT_REVISION = "d1c2b3a4e5f6"
HEALTH_COMPLIANCE_PARENT_REVISION = "a6c9e2f4b7d1"


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
