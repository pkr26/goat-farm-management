"""Displayed breeding lines have stable clinical order across configured storage ordering."""

import asyncio
import os
import re
import subprocess
import sys
from collections.abc import AsyncGenerator
from uuid import uuid4

import asyncpg
import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db import get_db
from app.main import create_app
from app.models import Animal, Farm
from app.seed import seed_reference_data
from app.services.feeding import feeding_plan
from app.utils import today

from .conftest import ADMIN_URL, BACKEND_DIR, TEST_DB, database_direct_url, owner_with_farm
from .test_feeding_extended import _orm_animal


async def test_breeding_display_order_is_independent_of_configured_database_collation() -> None:
    database = f"{TEST_DB}_feeding_storage_order_{uuid4().hex[:8]}"
    assert re.fullmatch(r"[A-Za-z0-9_]+", database) and "_test" in database
    direct_url = database_direct_url(database)
    async_url = direct_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    admin = await asyncpg.connect(ADMIN_URL)
    engine = create_async_engine(async_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    try:
        # A distinct initially configured database, never the shared fixture
        # database. The full applied migration chain is unchanged and all
        # clinical/money/enum/FK constraints remain enabled and validated.
        # PostgreSQL16 supports deterministic ICU default collation rules.
        await admin.execute(
            f'CREATE DATABASE "{database}" TEMPLATE template0 '
            "ENCODING 'UTF8' LOCALE_PROVIDER icu ICU_LOCALE 'und' ICU_RULES '&M < F'"
        )
        migration_env = os.environ.copy()
        migration_env["GOATFARM_DATABASE_URL"] = async_url
        migration_env["GOATFARM_MIGRATION_DATABASE_URL"] = async_url
        migration_env["GOATFARM_MIGRATION_WRITES_QUIESCED"] = "true"
        migrated = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=BACKEND_DIR,
            env=migration_env,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        assert migrated.returncode == 0, migrated.stdout + migrated.stderr
        async with sessions() as db:
            await seed_reference_data(db)
            await db.commit()
            assert await db.scalar(text("SELECT 'M'::text < 'F'::text")) is True
            assert (
                await db.scalar(
                    text(
                        "SELECT count(*) FROM pg_constraint c JOIN pg_namespace n "
                        "ON n.oid=c.connamespace WHERE n.nspname='public' AND NOT c.convalidated"
                    )
                )
                == 0
            )

        async def configured_session() -> AsyncGenerator[AsyncSession]:
            async with sessions() as db:
                yield db

        app = create_app()
        app.dependency_overrides[get_db] = configured_session
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://test",
                headers={"Idempotency-Key": "configured-storage-order-public-farm"},
            ) as configured_client:
                headers = await owner_with_farm(
                    configured_client,
                    email="feeding-configured-storage-order@example.test",
                    farm_name="Configured storage order",
                )
        finally:
            app.dependency_overrides.clear()
        farm_id = int(headers["X-Farm-Id"])
        async with sessions() as db:
            animals = [
                _orm_animal(farm_id, "CONFIGURED-M", sex="M", bucket="BREEDING", dob_days=900),
                *[
                    _orm_animal(
                        farm_id,
                        f"CONFIGURED-F-{n}",
                        sex="F",
                        bucket="BREEDING",
                        dob_days=800,
                    )
                    for n in range(3)
                ],
            ]
            db.add_all(animals)
            await db.commit()
            original_ids = [animal.id for animal in animals]
            stored = list(
                (
                    await db.execute(
                        select(Animal)
                        .where(Animal.farm_id == farm_id)
                        .order_by(Animal.sex, Animal.id)
                    )
                ).scalars()
            )
            assert [animal.sex for animal in stored] == ["M", "F", "F", "F"]
            assert {animal.id for animal in stored} == set(original_ids)
            assert all(
                animal.status == "ACTIVE" and animal.current_bucket == "BREEDING"
                for animal in stored
            )
            farm = await db.get(Farm, farm_id)
            assert farm is not None
            lines = await feeding_plan(db, farm, today())
            # Displayed clinical order and head/ration identity must be stable
            # even when the configured database's own sex sort order differs.
            assert [line["segment"] for line in lines] == ["FEMALE", "MALE"], lines
            assert [line["heads"] for line in lines] == [3, 1]
            assert [line["kg_per_head"] for line in lines] == [1.2, 1.7]
            assert sum(line["heads"] for line in lines) == len(original_ids)
            remaining = list(
                (
                    await db.execute(
                        select(Animal.id).where(Animal.farm_id == farm_id).order_by(Animal.id)
                    )
                ).scalars()
            )
            assert remaining == sorted(original_ids)
    finally:
        await engine.dispose()
        await admin.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
        await admin.close()
