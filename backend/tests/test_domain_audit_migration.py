"""Round-trip coverage for data repair in the domain-audit migration."""

import asyncio
import os
import subprocess
import sys
from datetime import timedelta

import asyncpg
import httpx

from app.db import get_engine
from app.utils import today

from .conftest import BACKEND_DIR, TEST_DB, owner_with_farm
from .test_health_extended import make_animal

PARENT_REVISION = "d1c2b3a4e5f6"


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
