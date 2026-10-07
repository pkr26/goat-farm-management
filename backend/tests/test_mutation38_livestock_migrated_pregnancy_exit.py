"""Unknown pre-a4 observation provenance retains inclusive administrative closure."""

import asyncio
import os
import re
import subprocess
import sys
from collections.abc import AsyncGenerator
from datetime import timedelta
from uuid import uuid4

import asyncpg
import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db import get_db
from app.main import create_app
from app.models import Animal, BreedingRecord, BucketMove, Transaction
from app.security import hash_password
from app.seed import seed_reference_data
from app.utils import today

from .conftest import ADMIN_URL, BACKEND_DIR, TEST_DB, database_direct_url

PRE_OBSERVATION_REVISION = "a7c9e2f4b1d8"


async def test_migrated_unknown_scan_date_allows_administrative_exit_on_service_date() -> None:
    database = f"{TEST_DB}_legacy_pregnancy_exit_{uuid4().hex[:8]}"
    assert re.fullmatch(r"[A-Za-z0-9_]+", database) and "_test" in database
    direct_url = database_direct_url(database)
    async_url = direct_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    admin = await asyncpg.connect(ADMIN_URL)
    engine = create_async_engine(async_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    try:
        await admin.execute(f'CREATE DATABASE "{database}"')
        migration_env = os.environ.copy()
        migration_env["GOATFARM_DATABASE_URL"] = async_url
        migration_env["GOATFARM_MIGRATION_DATABASE_URL"] = async_url
        migration_env["GOATFARM_MIGRATION_WRITES_QUIESCED"] = "true"

        async def migrate(revision: str) -> None:
            migrated = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, "-m", "alembic", "upgrade", revision],
                cwd=BACKEND_DIR,
                env=migration_env,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
            assert migrated.returncode == 0, migrated.stdout + migrated.stderr

        await migrate(PRE_OBSERVATION_REVISION)
        connection = await asyncpg.connect(direct_url)
        service_date = today() - timedelta(days=60)
        birth_date = today() - timedelta(days=800)
        email, password = "migrated-pregnancy-exit@example.test", "historicalownerpass123"
        try:
            # Persist under the actual old DDL. The observation-date column
            # does not exist yet, so no retained observation can be erased.
            assert (
                await connection.fetchval(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name='breeding_records' "
                    "AND column_name='ultrasound_result_date'"
                )
                == 0
            )
            owner_id = int(
                await connection.fetchval(
                    "INSERT INTO users(email,password_hash,created_at) "
                    "VALUES($1,$2,timezone('UTC',now())) RETURNING id",
                    email,
                    hash_password(password),
                )
            )
            farm_id = int(
                await connection.fetchval(
                    "INSERT INTO farms(name,owner_id,created_at) "
                    "VALUES('Migrated pregnancy provenance',$1,timezone('UTC',now())) RETURNING id",
                    owner_id,
                )
            )
            animals = []
            for tag, sex, bucket in (
                ("LEGACY-UNKNOWN-DOE", "F", "PREGNANCY_EARLY"),
                ("LEGACY-UNKNOWN-SIRE", "M", "BREEDING"),
            ):
                animal_id = int(
                    await connection.fetchval(
                        "INSERT INTO animals(farm_id,tag_number,breed,sex,date_of_birth,source,"
                        "current_bucket,status,cull_candidate,created_at) VALUES "
                        "($1,$2,'Osmanabadi',$3,$4,'PURCHASED',$5,'ACTIVE',false,"
                        "timezone('UTC',now())) RETURNING id",
                        farm_id,
                        tag,
                        sex,
                        birth_date,
                        bucket,
                    )
                )
                animals.append(animal_id)
            doe_id, sire_id = animals
            breeding_id = int(
                await connection.fetchval(
                    "INSERT INTO breeding_records(farm_id,doe_id,buck_id,breeding_date,method,"
                    "heat_cycle_number,ultrasound_date,ultrasound_done,pregnant,kid_count_detected,"
                    "expected_kidding_date,outcome,created_by_id) VALUES "
                    "($1,$2,$3,$4,'NATURAL',1,$5,true,true,2,$6,'CONFIRMED_PREGNANT',$7) "
                    "RETURNING id",
                    farm_id,
                    doe_id,
                    sire_id,
                    service_date,
                    service_date + timedelta(days=32),
                    service_date + timedelta(days=150),
                    owner_id,
                )
            )
            # This sparse old schema retained the confirmed result and plan,
            # but neither an observed day nor a cohort-history row. These
            # facts are asserted before upgrade and are never deleted later.
            assert await connection.fetchval("SELECT count(*) FROM bucket_moves") == 0
            assert await connection.fetchval("SELECT count(*) FROM kidding_records") == 0
        finally:
            await connection.close()

        await migrate("head")
        async with sessions() as db:
            await seed_reference_data(db)
            await db.commit()
            historical = await db.get(BreedingRecord, breeding_id)
            assert historical is not None and historical.outcome == "CONFIRMED_PREGNANT"
            assert historical.ultrasound_done and historical.pregnant is True
            assert historical.ultrasound_result_date is None
            assert historical.breeding_date == service_date
            assert historical.ultrasound_date == service_date + timedelta(days=32)
            assert not list((await db.execute(select(BucketMove.id))).scalars())
            assert (
                await db.scalar(
                    text(
                        "SELECT bool_and(convalidated) FROM pg_constraint "
                        "WHERE conrelid IN ('animals'::regclass,'breeding_records'::regclass)"
                    )
                )
            ) is True

        async def migrated_session() -> AsyncGenerator[AsyncSession]:
            async with sessions() as db:
                yield db

        app = create_app()
        app.dependency_overrides[get_db] = migrated_session
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://test",
            ) as migrated_client:
                authenticated = await migrated_client.post(
                    "/api/auth/login", json={"email": email, "password": password}
                )
                assert authenticated.status_code == 200, authenticated.text
                headers = {
                    "Authorization": f"Bearer {authenticated.json()['access_token']}",
                    "X-Farm-Id": str(farm_id),
                    "Idempotency-Key": "migrated-pregnancy-terminal-close",
                }
                exited = await migrated_client.post(
                    f"/api/animals/{doe_id}/status",
                    json={
                        "new_status": "SOLD",
                        "date": service_date.isoformat(),
                        "sale_price": 5000,
                    },
                    headers=headers,
                )
                assert exited.status_code == 200, exited.text
        finally:
            app.dependency_overrides.clear()

        async with sessions() as db:
            animal = await db.get(Animal, doe_id)
            breeding = await db.get(BreedingRecord, breeding_id)
            assert animal is not None and animal.status == "SOLD"
            assert animal.status_date == service_date
            assert breeding is not None and breeding.outcome == "ABORTED"
            assert breeding.loss_date == service_date
            assert breeding.loss_cause == "ANIMAL_STATUS_CHANGE"
            assert breeding.loss_recorded_by_id == owner_id
            assert breeding.loss_recorded_at is not None
            assert breeding.ultrasound_result_date is None
            sales = list(
                (
                    await db.execute(
                        select(Transaction).where(
                            Transaction.source_type == "ANIMAL_SALE",
                            Transaction.source_id == doe_id,
                        )
                    )
                ).scalars()
            )
            assert len(sales) == 1 and sales[0].date == service_date
            moves = list((await db.execute(select(BucketMove))).scalars())
            assert len(moves) == 1
            assert moves[0].animal_id == doe_id and moves[0].created_by_id == owner_id
            assert moves[0].effective_date == service_date
    finally:
        await engine.dispose()
        await admin.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
        await admin.close()
