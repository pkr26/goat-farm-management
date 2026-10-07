"""The native clinical fetch owns its pregnancy row until its transaction ends."""

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from app.api.breeding import _lock_doe_then_breeding_record
from app.db import get_sessionmaker
from app.models import BreedingRecord, Farm
from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_extended import make_animal


async def test_native_clinical_fetch_retains_exclusive_pregnancy_write_ownership(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    service_date = today()
    birth_date = service_date - timedelta(days=800)
    doe = await make_animal(
        client,
        owner,
        "PREGNANCY-ROW-DOE",
        date_of_birth=birth_date.isoformat(),
        weight_kg=26.0,
        weight_date=(service_date - timedelta(days=1)).isoformat(),
    )
    buck = await make_animal(
        client,
        owner,
        "PREGNANCY-ROW-BUCK",
        sex="M",
        bucket="BREEDING",
        date_of_birth=birth_date.isoformat(),
        weight_kg=30.0,
        weight_date=(service_date - timedelta(days=1)).isoformat(),
    )
    created = await client.post(
        "/api/breeding",
        headers=owner,
        json={
            "doe_id": doe["id"],
            "buck_id": buck["id"],
            "breeding_date": service_date.isoformat(),
        },
    )
    assert created.status_code == 201, created.text
    record_id = int(created.json()["id"])
    refused_second_writer = False
    async with get_sessionmaker()() as holder:
        farm = await holder.get(Farm, int(owner["X-Farm-Id"]))
        assert farm is not None
        try:
            record = await _lock_doe_then_breeding_record(holder, farm, record_id)
        except Exception as error:
            pytest.fail(f"Valid clinical fetch must retain a writable pregnancy: {error!r}")
        assert record.id == record_id and record.outcome == "PENDING"
        # Ordinary HTTP clinical writers also serialize on the doe. This
        # checks the additional, explicitly documented native guarantee:
        # the returned pregnancy itself is exclusively owned for this
        # transaction. The second real connection cannot gain that row's
        # write ownership until the clinical holder commits/rolls back.
        async with get_sessionmaker()() as other_writer:
            try:
                await other_writer.execute(
                    select(BreedingRecord.id)
                    .where(BreedingRecord.id == record_id, BreedingRecord.farm_id == farm.id)
                    .with_for_update(nowait=True)
                )
            except DBAPIError as error:
                assert getattr(error.orig, "sqlstate", None) == "55P03", repr(error)
                refused_second_writer = True
            finally:
                await other_writer.rollback()
        await holder.rollback()
    assert refused_second_writer, "A second real transaction acquired the clinical pregnancy row"
    # Ownership ends with the actual holder transaction, so the same row
    # remains available to the next legitimate transaction.
    async with get_sessionmaker()() as next_writer:
        acquired = (
            await next_writer.execute(
                select(BreedingRecord.id)
                .where(BreedingRecord.id == record_id)
                .with_for_update(nowait=True)
            )
        ).scalar_one()
        assert acquired == record_id
        await next_writer.rollback()
