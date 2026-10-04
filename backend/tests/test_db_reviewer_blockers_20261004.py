"""Independent regressions for the final database reviewer blockers."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from sqlalchemy import delete, insert, update
from sqlalchemy.exc import IntegrityError

from app.db import get_sessionmaker
from app.models import FarmMembership, ScreeningBatch

from .conftest import BACKEND_DIR, owner_with_farm


async def test_removed_member_keeps_existing_attribution_but_cannot_gain_new_rows(
    client: httpx.AsyncClient,
) -> None:
    """Current affiliation gates assignment; it does not rewrite history."""
    owner = await owner_with_farm(client, email="actor-history-owner@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    role = await client.post(
        "/api/team/roles",
        json={"name": "Historical reviewer", "permissions": ["dashboard.view"]},
        headers=owner,
    )
    assert role.status_code == 201, role.text
    worker = await client.post(
        "/api/team/workers",
        json={
            "name": "Former reviewer",
            "email": "former-actor@farm.in",
            "password": "worker-pass-123",
            "role_id": role.json()["id"],
        },
        headers=owner,
    )
    assert worker.status_code == 201, worker.text

    async with get_sessionmaker()() as db:
        membership = await db.get(FarmMembership, worker.json()["id"])
        assert membership is not None
        actor_id = membership.user_id
        batch_id = await db.scalar(
            insert(ScreeningBatch)
            .values(farm_id=farm_id, created_by_id=actor_id)
            .returning(ScreeningBatch.id)
        )
        assert batch_id is not None
        # Simulate a future retention/removal path. The application currently
        # deactivates memberships, but database history must remain sound even
        # if an obsolete membership is eventually physically removed.
        await db.execute(delete(FarmMembership).where(FarmMembership.id == membership.id))
        await db.commit()

    async with get_sessionmaker()() as db:
        # SQLAlchemy may include an unchanged actor field in a legitimate
        # update. This must not make an old row depend on today's roster.
        await db.execute(
            update(ScreeningBatch)
            .where(ScreeningBatch.id == batch_id)
            .values(created_by_id=actor_id)
        )
        await db.commit()

    # The exception is deliberately narrow: a removed member cannot be newly
    # assigned to evidence, so cross-tenant/current-affiliation validation is
    # not weakened by preserving the old row.
    async with get_sessionmaker()() as db:
        with pytest.raises(IntegrityError):
            await db.execute(insert(ScreeningBatch).values(farm_id=farm_id, created_by_id=actor_id))


def test_terminal_history_index_migration_is_online_and_restart_safe() -> None:
    migration = (
        Path(BACKEND_DIR) / "alembic" / "versions" / "fc3d4e5f6a7b_terminal_task_history_index.py"
    ).read_text(encoding="utf-8")

    assert "with op.get_context().autocommit_block():" in migration
    assert migration.count("with op.get_context().autocommit_block():") == 2
    assert "CREATE INDEX CONCURRENTLY IF NOT EXISTS" in migration
    assert "DROP INDEX CONCURRENTLY IF EXISTS" in migration
    assert "indisvalid" in migration
    assert "indisready" in migration
    assert "indislive" in migration
    assert 'DROP INDEX CONCURRENTLY "{INDEX_NAME}"' in migration
