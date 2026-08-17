"""Migration coverage for monetized CULLED animal dispositions."""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys

import asyncpg

from .conftest import BACKEND_DIR, TEST_DB

PARENT_REVISION = "d3b5f7c9e024"
CULL_SALE_REVISION = "e6f8a0b2c4d7"
ADMIN_URL = "postgresql://localhost:5432/postgres"


def _throwaway_name() -> str:
    database = f"{TEST_DB}_monetized_cull"
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


async def test_monetized_cull_constraint_and_downgrade_guard() -> None:
    database = _throwaway_name()
    await _admin(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
    await _admin(f'CREATE DATABASE "{database}"')
    database_url = f"postgresql://localhost:5432/{database}"
    try:
        await _alembic(database, "upgrade", PARENT_REVISION)
        connection = await asyncpg.connect(database_url)
        try:
            user_id = await connection.fetchval(
                """
                INSERT INTO users (email, password_hash, created_at)
                VALUES ('monetized-cull@example.test', 'not-used', timezone('UTC', now()))
                RETURNING id
                """
            )
            farm_id = await connection.fetchval(
                """
                INSERT INTO farms (name, owner_id, created_at)
                VALUES ('Monetized Cull', $1, timezone('UTC', now()))
                RETURNING id
                """,
                user_id,
            )
            animal_id = await connection.fetchval(
                """
                INSERT INTO animals (
                  farm_id, tag_number, breed, sex, source, current_bucket,
                  status, cull_candidate, created_at, movement_restricted,
                  suspected_scheduled_disease
                ) VALUES (
                  $1, 'CULL-MIG', 'Test', 'M', 'PURCHASED', 'BREEDING',
                  'ACTIVE', false, timezone('UTC', now()), false, false
                ) RETURNING id
                """,
                farm_id,
            )
        finally:
            await connection.close()

        await _alembic(database, "upgrade", CULL_SALE_REVISION)
        connection = await asyncpg.connect(database_url)
        try:
            await connection.execute(
                """
                UPDATE animals
                SET status = 'CULLED', status_date = CURRENT_DATE,
                    sale_price = 7500.00, buyer_name = 'Cull buyer'
                WHERE id = $1
                """,
                animal_id,
            )
            validated = await connection.fetchval(
                """
                SELECT convalidated FROM pg_constraint
                WHERE conname = 'ck_animals_sale_fields'
                """
            )
            assert validated is True
        finally:
            await connection.close()

        refused = await _alembic(database, "downgrade", PARENT_REVISION, succeeds=False)
        output = refused.stdout + refused.stderr
        assert "Cannot downgrade monetized culls" in output
        assert f"CULLED animal {animal_id}" in output

        connection = await asyncpg.connect(database_url)
        try:
            # The failed revision is transactional: the expanded constraint
            # remains installed and the recorded cull proceeds remain intact.
            row = await connection.fetchrow(
                "SELECT sale_price::text, buyer_name FROM animals WHERE id = $1",
                animal_id,
            )
            assert row == ("7500.00", "Cull buyer")
            await connection.execute(
                "UPDATE animals SET sale_price = NULL, buyer_name = NULL WHERE id = $1",
                animal_id,
            )
        finally:
            await connection.close()

        await _alembic(database, "downgrade", PARENT_REVISION)
        connection = await asyncpg.connect(database_url)
        try:
            try:
                await connection.execute(
                    "UPDATE animals SET sale_price = 1 WHERE id = $1",
                    animal_id,
                )
            except asyncpg.CheckViolationError as exc:
                assert exc.constraint_name == "ck_animals_sale_fields"
            else:
                raise AssertionError("the downgraded constraint accepted a CULLED sale price")
        finally:
            await connection.close()
    finally:
        await _admin(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
