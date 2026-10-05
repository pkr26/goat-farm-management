"""Chronology migration preserves legacy facts and fails rather than inventing dates."""

from __future__ import annotations

from datetime import timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import httpx
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from app.db import get_engine
from app.utils import today
from tests.conftest import owner_with_farm


def _migration(connection: Connection, direction: str) -> None:
    path = (
        Path(__file__).parents[1]
        / "alembic/versions/ff6a7b8c9d01_animal_purchase_birth_chronology.py"
    )
    spec = spec_from_file_location("birth_chronology", path)
    assert spec is not None and spec.loader is not None
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    with Operations.context(MigrationContext.configure(connection)):
        getattr(migration, direction)()


async def test_purchase_birth_migration_upgrade_downgrade_and_transactional_legacy_refusal(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    created = await client.post(
        "/api/animals",
        headers=owner,
        json={
            "tag_number": "MIGRATION-CHRONOLOGY",
            "sex": "F",
            "source": "PURCHASED",
            "current_bucket": "FOUNDATION",
            "historical_import_reason": "Existing acquired goat",
            "purchase_date": (today() - timedelta(days=100)).isoformat(),
        },
    )
    assert created.status_code == 201, created.text
    animal_id = created.json()["id"]
    async with get_engine().connect() as connection:
        transaction = await connection.begin()
        await connection.run_sync(_migration, "downgrade")
        await connection.run_sync(_migration, "upgrade")
        exists = (
            await connection.execute(
                text(
                    "SELECT convalidated FROM pg_constraint "
                    "WHERE conname='ck_animals_purchase_after_birth'"
                )
            )
        ).scalar_one()
        assert exists is True
        await connection.run_sync(_migration, "downgrade")
        await connection.execute(
            text("UPDATE animals SET estimated_dob = :born WHERE id = :id"),
            {"born": today() - timedelta(days=50), "id": animal_id},
        )
        with pytest.raises(DBAPIError, match="reconcile recorded"):
            await connection.run_sync(_migration, "upgrade")
        await transaction.rollback()
        # The entire failed migration transaction preserved the original dates
        # and restored the already-installed constraint when it rolled back.
        estimate = (
            await connection.execute(
                text("SELECT estimated_dob FROM animals WHERE id = :id"), {"id": animal_id}
            )
        ).scalar_one()
        assert estimate is None
        assert (
            await connection.execute(
                text(
                    "SELECT convalidated FROM pg_constraint "
                    "WHERE conname='ck_animals_purchase_after_birth'"
                )
            )
        ).scalar_one() is True
