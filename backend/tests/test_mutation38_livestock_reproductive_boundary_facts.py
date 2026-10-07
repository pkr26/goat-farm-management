"""Native chronology reads retain the actual, tenant-scoped delivery date."""

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import Animal, BreedingRecord, KiddingRecord
from app.services.breeding import _latest_doe_reproductive_boundary
from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_extended import confirm, make_animal, make_breeding


async def test_native_reproductive_boundary_includes_real_kidding_and_preserves_tenant_scope(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="reproductive-facts@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    reference_date = today()
    birth_date = reference_date - timedelta(days=20)
    service_date = birth_date - timedelta(days=150)
    doe = await make_animal(
        client,
        owner,
        "BOUNDARY-FACT-DOE",
        date_of_birth=(reference_date - timedelta(days=800)).isoformat(),
        weight_kg=26.0,
        weight_date=(service_date - timedelta(days=1)).isoformat(),
    )
    buck = await make_animal(
        client,
        owner,
        "BOUNDARY-FACT-BUCK",
        sex="M",
        bucket="BREEDING",
        date_of_birth=(reference_date - timedelta(days=800)).isoformat(),
        weight_kg=30.0,
        weight_date=(service_date - timedelta(days=1)).isoformat(),
    )
    doe_id = int(doe["id"])
    breeding = await make_breeding(
        client,
        owner,
        doe_id,
        int(buck["id"]),
        breeding_date=service_date.isoformat(),
    )
    await confirm(client, owner, int(breeding["id"]), kid_count=1)
    delivered = await client.post(
        "/api/kidding",
        headers=owner,
        json={
            "breeding_record_id": breeding["id"],
            "date": birth_date.isoformat(),
            "ease": "NORMAL",
            "kids": [{"sex": "F", "status": "STILLBORN"}],
        },
    )
    assert delivered.status_code == 201, delivered.text
    other_owner = await owner_with_farm(client, email="other-reproductive-farm@farm.in")
    other_farm_id = int(other_owner["X-Farm-Id"])
    assert other_farm_id != farm_id
    async with get_sessionmaker()() as db:
        # The native reader documents a doe lock across its chronology
        # snapshot. Both actual recorded service/scan and birth are retained;
        # nothing is restored, removed or re-dated for this maximum-fact pin.
        await db.execute(select(Animal.id).where(Animal.id == doe_id).with_for_update())
        record = await db.get(BreedingRecord, int(breeding["id"]))
        kidding = (
            await db.execute(
                select(KiddingRecord).where(KiddingRecord.breeding_record_id == breeding["id"])
            )
        ).scalar_one()
        assert record is not None
        assert record.breeding_date == service_date
        assert record.ultrasound_result_date is not None
        assert record.ultrasound_result_date < birth_date
        assert kidding.date == birth_date and kidding.farm_id == farm_id
        try:
            own_boundary = await _latest_doe_reproductive_boundary(db, farm_id, doe_id)
            other_boundary = await _latest_doe_reproductive_boundary(db, other_farm_id, doe_id)
        except Exception as error:
            pytest.fail(f"Committed chronological facts must remain readable: {error!r}")
        await db.rollback()
    assert own_boundary == birth_date
    assert other_boundary is None
