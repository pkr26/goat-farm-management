"""Migration coverage for actor-scoped idempotency and task-role closure."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from typing import Any

import asyncpg
import httpx
from sqlalchemy import text

from app.db import get_engine, get_sessionmaker
from app.utils import today

from .conftest import BACKEND_DIR, TEST_DB, create_farm, owner_with_farm, register

F2_REVISION = "f2c3d4e5f6a7"
F3_REVISION = "f3d4e5f6a7b8"
F4_REVISION = "f4e5f6a7b8c9"


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


async def _catalog_state() -> dict[str, Any]:
    async with get_sessionmaker()() as db:
        farm_nullable = (
            await db.execute(
                text(
                    """
                    SELECT is_nullable
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = 'idempotency_records'
                      AND column_name = 'farm_id'
                    """
                )
            )
        ).scalar_one()
        constraints = {
            name: (validated, definition)
            for name, validated, definition in (
                await db.execute(
                    text(
                        """
                        SELECT con.conname, con.convalidated,
                               pg_get_constraintdef(con.oid)
                        FROM pg_constraint AS con
                        JOIN pg_class AS rel ON rel.oid = con.conrelid
                        WHERE rel.relname IN ('idempotency_records', 'tasks')
                          AND con.conname IN (
                            'ck_idempotency_scope_kind',
                            'ck_tasks_user_assignment_has_role'
                          )
                        """
                    )
                )
            ).all()
        }
        indexes = {
            name: (unique, valid, predicate, definition)
            for name, unique, valid, predicate, definition in (
                await db.execute(
                    text(
                        """
                        SELECT idx.relname, ind.indisunique, ind.indisvalid,
                               pg_get_expr(ind.indpred, ind.indrelid),
                               pg_get_indexdef(ind.indexrelid)
                        FROM pg_class AS idx
                        JOIN pg_index AS ind ON ind.indexrelid = idx.oid
                        WHERE idx.relname IN (
                            'uq_idempotency_actor_scope_key',
                            'ix_tasks_pending_farm_role'
                        )
                        """
                    )
                )
            ).all()
        }
    return {
        "farm_nullable": farm_nullable,
        "constraints": constraints,
        "indexes": indexes,
    }


def _assert_f3_catalog(state: dict[str, Any]) -> None:
    assert state["farm_nullable"] == "YES"
    scope_validated, scope_definition = state["constraints"]["ck_idempotency_scope_kind"]
    assert scope_validated is True
    assert "farm_id IS NULL" in scope_definition
    assert "auth.farms.create" in scope_definition
    assert state["constraints"]["ck_tasks_user_assignment_has_role"][0] is True

    actor_unique, actor_valid, actor_predicate, actor_definition = state["indexes"][
        "uq_idempotency_actor_scope_key"
    ]
    assert actor_unique is True
    assert actor_valid is True
    assert actor_predicate == "(farm_id IS NULL)"
    assert "(actor_id, operation, key_digest)" in actor_definition

    task_unique, task_valid, task_predicate, task_definition = state["indexes"][
        "ix_tasks_pending_farm_role"
    ]
    assert task_unique is False
    assert task_valid is True
    assert task_predicate == (
        "(((status)::text = 'PENDING'::text) AND (assigned_role_id IS NOT NULL))"
    )
    assert "(farm_id, assigned_role_id)" in task_definition


async def test_f3_catalog_downgrade_guard_roundtrip_and_autogenerate(
    client: httpx.AsyncClient,
) -> None:
    headers = await register(client, "migration-downgrade@farm.in")
    created = await client.post(
        "/api/auth/farms",
        json={"name": "Downgrade Guard Farm"},
        headers=headers | {"Idempotency-Key": "downgrade-guard-key"},
    )
    assert created.status_code == 201, created.text
    _assert_f3_catalog(await _catalog_state())

    await get_engine().dispose()
    refused = await _alembic("downgrade", F2_REVISION, succeeds=False)
    output = refused.stdout + refused.stderr
    assert "Cannot downgrade actor-scoped idempotency" in output
    assert "Sample record ids" in output

    connection = await asyncpg.connect(f"postgresql://localhost:5432/{TEST_DB}")
    try:
        await connection.execute("DELETE FROM idempotency_records WHERE farm_id IS NULL")
    finally:
        await connection.close()

    try:
        await _alembic("downgrade", F2_REVISION)
        parent = await _catalog_state()
        assert parent["farm_nullable"] == "NO"
        assert "ck_idempotency_scope_kind" not in parent["constraints"]
        assert "uq_idempotency_actor_scope_key" not in parent["indexes"]
        assert "ix_tasks_pending_farm_role" not in parent["indexes"]
        # F3 only validates this older D9 check; downgrade must never make an
        # already-proven invariant NOT VALID again.
        assert parent["constraints"]["ck_tasks_user_assignment_has_role"][0] is True

        await _alembic("upgrade", "head")
        _assert_f3_catalog(await _catalog_state())
        await _alembic("check")
    finally:
        await get_engine().dispose()
        await _alembic("upgrade", "head")


async def test_f4_purges_only_sensitive_rows_and_is_irreversible_roundtrip(
    client: httpx.AsyncClient,
) -> None:
    assert TEST_DB.endswith("_test")
    owner = await owner_with_farm(
        client,
        email="f4-sensitive-purge-owner@farm.in",
        farm_name="F4 Sensitive Purge Farm",
    )
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        actor_id = int(
            (
                await db.execute(
                    text("SELECT id FROM users WHERE email = 'f4-sensitive-purge-owner@farm.in'")
                )
            ).scalar_one()
        )

    await get_engine().dispose()
    try:
        await _alembic("downgrade", F3_REVISION)
        connection = await asyncpg.connect(f"postgresql://localhost:5432/{TEST_DB}")
        try:
            for operation, key_digest, request_hash in (
                ("team.workers.create", "1" * 64, "2" * 64),
                ("finance.transactions.create", "3" * 64, "4" * 64),
            ):
                await connection.execute(
                    """
                    INSERT INTO idempotency_records (
                      farm_id, actor_id, operation, key_digest, request_hash,
                      created_at, expires_at
                    ) VALUES ($1, $2, $3, $4, $5, now(), now() + interval '1 day')
                    """,
                    farm_id,
                    actor_id,
                    operation,
                    key_digest,
                    request_hash,
                )
        finally:
            await connection.close()

        await _alembic("upgrade", "head")
        connection = await asyncpg.connect(f"postgresql://localhost:5432/{TEST_DB}")
        try:
            operations = await connection.fetch(
                "SELECT operation FROM idempotency_records WHERE actor_id = $1",
                actor_id,
            )
        finally:
            await connection.close()
        assert [row["operation"] for row in operations] == ["finance.transactions.create"]

        heads = await _alembic("heads")
        assert f"{F4_REVISION} (head)" in heads.stdout
        await _alembic("check")

        # Downgrade cannot reconstruct the purged secret-bearing replay row;
        # the ordinary record remains across the full F4 round trip.
        await _alembic("downgrade", F3_REVISION)
        await _alembic("upgrade", "head")
        connection = await asyncpg.connect(f"postgresql://localhost:5432/{TEST_DB}")
        try:
            counts = dict(
                await connection.fetch(
                    """
                    SELECT operation, count(*)
                    FROM idempotency_records
                    WHERE actor_id = $1
                    GROUP BY operation
                    """,
                    actor_id,
                )
            )
        finally:
            await connection.close()
        assert counts == {"finance.transactions.create": 1}
    finally:
        await get_engine().dispose()
        await _alembic("upgrade", "head")


async def test_f3_refuses_invalid_and_duplicate_null_scopes_then_recovers(
    client: httpx.AsyncClient,
) -> None:
    headers = await register(client, "migration-dirty-scope@farm.in")
    del headers
    async with get_sessionmaker()() as db:
        actor_id = (
            await db.execute(
                text("SELECT id FROM users WHERE email = 'migration-dirty-scope@farm.in'")
            )
        ).scalar_one()

    await get_engine().dispose()
    await _alembic("downgrade", F2_REVISION)
    connection = await asyncpg.connect(f"postgresql://localhost:5432/{TEST_DB}")
    try:
        await connection.execute(
            "ALTER TABLE idempotency_records ALTER COLUMN farm_id DROP NOT NULL"
        )
        first_id = await connection.fetchval(
            """
            INSERT INTO idempotency_records (
              farm_id, actor_id, operation, key_digest, request_hash,
              created_at, expires_at
            ) VALUES (NULL, $1, 'tenant.operation', $2, $3, now(), now() + interval '1 day')
            RETURNING id
            """,
            actor_id,
            "a" * 64,
            "b" * 64,
        )
    finally:
        await connection.close()

    try:
        invalid = await _alembic("upgrade", "head", succeeds=False)
        output = invalid.stdout + invalid.stderr
        assert "farm scope and operation disagree" in output
        assert str(first_id) in output

        connection = await asyncpg.connect(f"postgresql://localhost:5432/{TEST_DB}")
        try:
            await connection.execute(
                "UPDATE idempotency_records SET operation = 'auth.farms.create' WHERE id = $1",
                first_id,
            )
            duplicate_id = await connection.fetchval(
                """
                INSERT INTO idempotency_records (
                  farm_id, actor_id, operation, key_digest, request_hash,
                  created_at, expires_at
                ) VALUES (
                  NULL, $1, 'auth.farms.create', $2, $3,
                  now(), now() + interval '1 day'
                ) RETURNING id
                """,
                actor_id,
                "a" * 64,
                "b" * 64,
            )
        finally:
            await connection.close()

        duplicate = await _alembic("upgrade", "head", succeeds=False)
        output = duplicate.stdout + duplicate.stderr
        assert "duplicate NULL-farm actor/operation/key scopes" in output
        assert str(first_id) in output
        assert str(duplicate_id) in output

        connection = await asyncpg.connect(f"postgresql://localhost:5432/{TEST_DB}")
        try:
            await connection.execute(
                "DELETE FROM idempotency_records WHERE id = $1",
                duplicate_id,
            )
        finally:
            await connection.close()

        await _alembic("upgrade", "head")
        _assert_f3_catalog(await _catalog_state())

        connection = await asyncpg.connect(f"postgresql://localhost:5432/{TEST_DB}")
        try:
            try:
                await connection.execute(
                    """
                    INSERT INTO idempotency_records (
                      farm_id, actor_id, operation, key_digest, request_hash,
                      created_at, expires_at
                    ) VALUES (
                      NULL, $1, 'auth.farms.create', $2, $3,
                      now(), now() + interval '1 day'
                    )
                    """,
                    actor_id,
                    "a" * 64,
                    "c" * 64,
                )
            except asyncpg.UniqueViolationError:
                pass
            else:
                raise AssertionError("partial unique actor scope accepted a duplicate")
        finally:
            await connection.close()
    finally:
        connection = await asyncpg.connect(f"postgresql://localhost:5432/{TEST_DB}")
        try:
            await connection.execute("DELETE FROM idempotency_records WHERE farm_id IS NULL")
        finally:
            await connection.close()
        await get_engine().dispose()
        await _alembic("upgrade", "head")


async def _worker(
    client: httpx.AsyncClient,
    farm_headers: dict[str, str],
    email: str,
) -> tuple[int, int]:
    team = await client.get("/api/team", headers=farm_headers)
    assert team.status_code == 200, team.text
    role_id = next(role["id"] for role in team.json()["roles"] if role["code"] == "CLEANER")
    created = await client.post(
        "/api/team/workers",
        json={
            "email": email,
            "name": "Migration worker",
            "password": "migration-worker-pass-123",
            "role_id": role_id,
        },
        headers=farm_headers,
    )
    assert created.status_code == 201, created.text
    return int(created.json()["user_id"]), int(role_id)


async def test_f3_task_repair_rejects_cross_farm_and_tombstoned_roles_then_recovers(
    client: httpx.AsyncClient,
) -> None:
    primary = await owner_with_farm(
        client,
        email="migration-task-owner@farm.in",
        farm_name="Primary Repair Farm",
    )
    primary_user_id, primary_role_id = await _worker(
        client,
        primary,
        "migration-primary-worker@farm.in",
    )
    secondary = await create_farm(
        client,
        {"Authorization": primary["Authorization"]},
        name="Secondary Repair Farm",
    )
    cross_farm_user_id, _cross_role_id = await _worker(
        client,
        secondary,
        "migration-cross-worker@farm.in",
    )

    task_ids: list[int] = []
    for title in ("Tombstoned role legacy duty", "Cross-farm legacy duty"):
        response = await client.post(
            "/api/tasks",
            json={
                "title": title,
                "due_date": today().isoformat(),
                "category": "OTHER",
                "assigned_user_id": primary_user_id,
            },
            headers=primary,
        )
        assert response.status_code == 201, response.text
        task_ids.append(int(response.json()["id"]))

    await get_engine().dispose()
    await _alembic("downgrade", F2_REVISION)
    connection = await asyncpg.connect(f"postgresql://localhost:5432/{TEST_DB}")
    try:
        await connection.execute(
            "ALTER TABLE tasks DROP CONSTRAINT ck_tasks_user_assignment_has_role"
        )
        await connection.execute(
            "ALTER TABLE tasks DROP CONSTRAINT fk_tasks_farm_assigned_membership"
        )
        await connection.execute(
            "UPDATE roles SET deleted_at = now() WHERE id = $1",
            primary_role_id,
        )
        await connection.execute(
            "UPDATE tasks SET assigned_role_id = NULL WHERE id = $1",
            task_ids[0],
        )
        await connection.execute(
            "UPDATE tasks SET assigned_user_id = $1, assigned_role_id = NULL WHERE id = $2",
            cross_farm_user_id,
            task_ids[1],
        )
        await connection.execute(
            """
            ALTER TABLE tasks
            ADD CONSTRAINT ck_tasks_user_assignment_has_role
            CHECK (
              status <> 'PENDING'
              OR assigned_user_id IS NULL
              OR assigned_role_id IS NOT NULL
            ) NOT VALID
            """
        )
    finally:
        await connection.close()

    try:
        refused = await _alembic("upgrade", "head", succeeds=False)
        output = refused.stdout + refused.stderr
        assert "same-farm retained-membership/active-role repair" in output
        assert "2 PENDING personal task(s)" in output
        assert all(str(task_id) in output for task_id in task_ids)

        connection = await asyncpg.connect(f"postgresql://localhost:5432/{TEST_DB}")
        try:
            # The failed revision is atomic: it must not retain the role filled
            # for either dirty row or mark the legacy check validated.
            assert await connection.fetchval(
                "SELECT bool_and(assigned_role_id IS NULL) FROM tasks WHERE id = ANY($1::int[])",
                task_ids,
            )
            assert (
                await connection.fetchval(
                    """
                SELECT convalidated FROM pg_constraint
                WHERE conname = 'ck_tasks_user_assignment_has_role'
                """
                )
                is False
            )

            await connection.execute(
                "UPDATE roles SET deleted_at = NULL WHERE id = $1",
                primary_role_id,
            )
            # NOT VALID still enforces the check on every changed row. Drop
            # and recreate it around this deliberate legacy-data repair so
            # F3, not the test setup, remains responsible for filling roles.
            await connection.execute(
                "ALTER TABLE tasks DROP CONSTRAINT ck_tasks_user_assignment_has_role"
            )
            await connection.execute(
                "UPDATE tasks SET assigned_user_id = $1 WHERE id = $2",
                primary_user_id,
                task_ids[1],
            )
            await connection.execute(
                """
                ALTER TABLE tasks
                ADD CONSTRAINT ck_tasks_user_assignment_has_role
                CHECK (
                  status <> 'PENDING'
                  OR assigned_user_id IS NULL
                  OR assigned_role_id IS NOT NULL
                ) NOT VALID
                """
            )
            await connection.execute(
                """
                ALTER TABLE tasks
                ADD CONSTRAINT fk_tasks_farm_assigned_membership
                FOREIGN KEY (farm_id, assigned_user_id)
                REFERENCES farm_memberships (farm_id, user_id)
                NOT VALID
                """
            )
            await connection.execute(
                "ALTER TABLE tasks VALIDATE CONSTRAINT fk_tasks_farm_assigned_membership"
            )
        finally:
            await connection.close()

        await _alembic("upgrade", "head")
        _assert_f3_catalog(await _catalog_state())
        connection = await asyncpg.connect(f"postgresql://localhost:5432/{TEST_DB}")
        try:
            repaired = await connection.fetch(
                "SELECT id, assigned_role_id FROM tasks WHERE id = ANY($1::int[]) ORDER BY id",
                task_ids,
            )
        finally:
            await connection.close()
        assert [(int(row["id"]), int(row["assigned_role_id"])) for row in repaired] == [
            (task_id, primary_role_id) for task_id in sorted(task_ids)
        ]
    finally:
        connection = await asyncpg.connect(f"postgresql://localhost:5432/{TEST_DB}")
        try:
            await connection.execute(
                "UPDATE roles SET deleted_at = NULL WHERE id = $1",
                primary_role_id,
            )
            await connection.execute(
                "UPDATE tasks SET assigned_user_id = $1, assigned_role_id = $2 "
                "WHERE id = ANY($3::int[])",
                primary_user_id,
                primary_role_id,
                task_ids,
            )
            has_membership_fk = await connection.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_constraint "
                "WHERE conname = 'fk_tasks_farm_assigned_membership')"
            )
            if not has_membership_fk:
                await connection.execute(
                    """
                    ALTER TABLE tasks
                    ADD CONSTRAINT fk_tasks_farm_assigned_membership
                    FOREIGN KEY (farm_id, assigned_user_id)
                    REFERENCES farm_memberships (farm_id, user_id)
                    NOT VALID
                    """
                )
                await connection.execute(
                    "ALTER TABLE tasks VALIDATE CONSTRAINT fk_tasks_farm_assigned_membership"
                )
            has_task_check = await connection.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_constraint "
                "WHERE conname = 'ck_tasks_user_assignment_has_role')"
            )
            if not has_task_check:
                await connection.execute(
                    """
                    ALTER TABLE tasks
                    ADD CONSTRAINT ck_tasks_user_assignment_has_role
                    CHECK (
                      status <> 'PENDING'
                      OR assigned_user_id IS NULL
                      OR assigned_role_id IS NOT NULL
                    ) NOT VALID
                    """
                )
        finally:
            await connection.close()
        await get_engine().dispose()
        await _alembic("upgrade", "head")
