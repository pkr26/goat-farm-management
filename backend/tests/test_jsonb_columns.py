"""jsonb storage for the JSON-typed columns (roles, planner, scenarios)."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys

import asyncpg
import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db import get_engine, get_sessionmaker
from app.models import PlannerPlan, Role, SimulationScenario

from .conftest import BACKEND_DIR, TEST_DB, owner_with_farm

# (table, column, jsonb shape, wrong-shape valid JSON for CHECK probes).
JSON_COLUMNS: tuple[tuple[str, str, str, str], ...] = (
    ("roles", "permissions", "array", '"a string"'),
    ("planner_plans", "targets", "array", '"a string"'),
    ("planner_plans", "assumptions", "object", "[1, 2]"),
    ("simulation_scenarios", "assumptions", "object", "[1, 2]"),
)

JSONB_REVISION = "c4f6a8b0d2e5"
JSONB_PARENT = "b6d8f0a2c4e6"


async def _alembic(*args: str, succeeds: bool = True) -> subprocess.CompletedProcess[str]:
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env=os.environ.copy(),
        check=False,
        capture_output=True,
        text=True,
    )
    if succeeds:
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        assert result.returncode != 0, result.stdout + result.stderr
    return result


async def _column_types() -> dict[tuple[str, str], str]:
    async with get_sessionmaker()() as db:
        rows = (
            await db.execute(
                text(
                    """
                    SELECT table_name, column_name, data_type
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND (table_name, column_name) IN (
                        ('roles', 'permissions'),
                        ('planner_plans', 'targets'),
                        ('planner_plans', 'assumptions'),
                        ('simulation_scenarios', 'assumptions')
                      )
                    """
                )
            )
        ).all()
    return {(table, column): data_type for table, column, data_type in rows}


async def test_json_columns_are_jsonb_with_validated_shape_checks() -> None:
    assert await _column_types() == {
        (table, column): "jsonb" for table, column, _shape, _wrong in JSON_COLUMNS
    }
    async with get_sessionmaker()() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT rel.relname, con.conname, con.convalidated "
                    "FROM pg_constraint con "
                    "JOIN pg_class rel ON rel.oid = con.conrelid "
                    "WHERE con.contype = 'c' AND con.conname LIKE 'ck_%_json_%'"
                )
            )
        ).all()
    validated = {(table, name) for table, name, is_validated in rows if is_validated}
    for table, column, shape, _wrong in JSON_COLUMNS:
        assert (table, f"ck_{table}_{column}_json_{shape}") in validated


async def test_json_columns_roundtrip_through_the_text_interface(
    client: httpx.AsyncClient,
) -> None:
    """Writers still json.dumps; readers still json.loads — on jsonb storage."""
    owner = await owner_with_farm(client, email="jsonb-roundtrip@farm.in")
    farm_id = int(owner["X-Farm-Id"])

    targets = json.dumps([{"kind": "cull", "month": 3, "count": 2}])
    assumptions = json.dumps({"meta": {"start_year_month": "2026-01", "horizon_months": 12}})
    async with get_sessionmaker()() as db:
        role = Role(farm_id=farm_id, name="Roundtrip role")
        db.add(role)
        await db.flush()
        # A writer-style edit after the default was applied.
        role.permissions = json.dumps(["animals.read", "animals.write"])
        db.add_all(
            [
                PlannerPlan(
                    farm_id=farm_id,
                    name="Roundtrip plan",
                    notes="",
                    start_year_month="2026-01",
                    targets=targets,
                    assumptions=assumptions,
                ),
                SimulationScenario(
                    farm_id=farm_id,
                    name="Roundtrip scenario",
                    notes="",
                    assumptions=assumptions,
                ),
            ]
        )
        await db.commit()

    async with get_sessionmaker()() as db:
        read_role = (
            await db.execute(select(Role).where(Role.name == "Roundtrip role"))
        ).scalar_one()
        assert isinstance(read_role.permissions, str)
        assert json.loads(read_role.permissions) == ["animals.read", "animals.write"]
        assert read_role.permission_set() == {"animals.read", "animals.write"}

        read_plan = (
            await db.execute(select(PlannerPlan).where(PlannerPlan.name == "Roundtrip plan"))
        ).scalar_one()
        assert json.loads(read_plan.targets) == json.loads(targets)
        assert json.loads(read_plan.assumptions) == json.loads(assumptions)

        read_scenario = (
            await db.execute(
                select(SimulationScenario).where(SimulationScenario.name == "Roundtrip scenario")
            )
        ).scalar_one()
        assert json.loads(read_scenario.assumptions) == json.loads(assumptions)

        shapes = (
            await db.execute(
                text("SELECT jsonb_typeof(permissions) FROM roles WHERE name = 'Roundtrip role'")
            )
        ).scalar_one()
        assert shapes == "array"


async def test_role_permissions_empty_array_default_is_preserved(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="jsonb-default@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        db.add(Role(farm_id=farm_id, name="Default permissions role"))
        await db.commit()
    async with get_sessionmaker()() as db:
        stored = (
            await db.execute(
                text(
                    "SELECT permissions::text, jsonb_typeof(permissions) FROM roles "
                    "WHERE name = 'Default permissions role'"
                )
            )
        ).one()
    assert stored == ("[]", "array")


async def test_raw_sql_corrupt_json_is_refused_by_the_database(
    client: httpx.AsyncClient,
) -> None:
    """The type itself rejects unparseable text — no application guard needed."""
    owner = await owner_with_farm(client, email="jsonb-corrupt@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        with pytest.raises(DBAPIError) as caught:
            await db.execute(
                text(
                    "INSERT INTO roles (farm_id, name, permissions) "
                    "VALUES (:farm_id, 'Corrupt role', 'not json {')"
                ),
                {"farm_id": farm_id},
            )
        await db.rollback()
    assert "invalid input syntax for type json" in str(caught.value)


# One INSERT per probed column: the probed column binds the wrong-shape value
# while every peer JSON column keeps a valid shape, so the rejection can only
# come from the probed CHECK.
_SHAPE_PROBE_STATEMENTS = {
    ("roles", "permissions"): (
        "INSERT INTO roles (farm_id, name, permissions, revision, created_at) "
        "VALUES (:farm_id, 'Shape probe role', :value, 1, now())"
    ),
    ("planner_plans", "targets"): (
        "INSERT INTO planner_plans (farm_id, name, notes, start_year_month, "
        "targets, assumptions, revision, created_at, updated_at) "
        "VALUES (:farm_id, 'Shape probe plan', '', '2026-01', :value, "
        "'{\"meta\": {}}', 1, now(), now())"
    ),
    ("planner_plans", "assumptions"): (
        "INSERT INTO planner_plans (farm_id, name, notes, start_year_month, "
        "targets, assumptions, revision, created_at, updated_at) "
        "VALUES (:farm_id, 'Shape probe plan', '', '2026-01', '[]', :value, "
        "1, now(), now())"
    ),
    ("simulation_scenarios", "assumptions"): (
        "INSERT INTO simulation_scenarios (farm_id, name, notes, assumptions, "
        "revision, created_at, updated_at) "
        "VALUES (:farm_id, 'Shape probe scenario', '', :value, 1, now(), now())"
    ),
}


@pytest.mark.parametrize(
    ("table", "column", "shape", "wrong_shape_value"),
    JSON_COLUMNS,
)
async def test_wrong_shape_json_is_refused_by_check_constraints(
    client: httpx.AsyncClient,
    table: str,
    column: str,
    shape: str,
    wrong_shape_value: str,
) -> None:
    """Valid JSON of the wrong jsonb_typeof fails the shape CHECK at write."""
    owner = await owner_with_farm(client, email=f"jsonb-shape-{table}-{column}@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        with pytest.raises(IntegrityError) as caught:
            await db.execute(
                text(_SHAPE_PROBE_STATEMENTS[(table, column)]),
                {"farm_id": farm_id, "value": wrong_shape_value},
            )
        await db.rollback()
    assert f"ck_{table}_{column}_json_{shape}" in str(caught.value)


async def test_migration_refuses_corrupt_legacy_json_then_reupgrades_cleanly(
    client: httpx.AsyncClient,
) -> None:
    """The preflight fails closed with row ids before any column is converted."""
    owner = await owner_with_farm(client, email="jsonb-preflight@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        db.add(Role(farm_id=farm_id, name="Preflight role"))
        await db.commit()
        role_id = (
            await db.execute(select(Role.id).where(Role.name == "Preflight role"))
        ).scalar_one()

    await get_engine().dispose()
    await _alembic("downgrade", JSONB_PARENT)
    assert (await _column_types())[("roles", "permissions")] == "text"

    connection = await asyncpg.connect(f"postgresql://localhost:5432/{TEST_DB}")
    try:
        await connection.execute(
            "UPDATE roles SET permissions = '{not json' WHERE id = $1", role_id
        )
    finally:
        await connection.close()

    refused = await _alembic("upgrade", "head", succeeds=False)
    output = refused.stdout + refused.stderr
    assert "Refusing jsonb migration: roles.permissions contains invalid-json values" in output
    assert f"row ids [{role_id}]" in output

    # The failed revision is transactional: no column may have been converted.
    assert (await _column_types())[("roles", "permissions")] == "text"

    connection = await asyncpg.connect(f"postgresql://localhost:5432/{TEST_DB}")
    try:
        await connection.execute("UPDATE roles SET permissions = '[]' WHERE id = $1", role_id)
    finally:
        await connection.close()

    await _alembic("upgrade", "head")
    assert (await _column_types())["roles", "permissions"] == "jsonb"
    # The model and migration remain in sync after the full downgrade/retry.
    await _alembic("check")
