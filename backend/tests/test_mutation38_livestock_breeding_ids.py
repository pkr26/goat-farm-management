"""Breeding accepts legal restored INTEGER keys and rejects unrepresentable ones."""

from datetime import date

import httpx
import pytest
from sqlalchemy import func, select

from app.db import get_sessionmaker
from app.models import Animal, BreedingRecord, WeightRecord
from app.utils import today

from .conftest import owner_with_farm


@pytest.mark.parametrize("endpoint", ["doe", "buck"])
@pytest.mark.parametrize("representable", [True, False], ids=["integer-ceiling", "above-ceiling"])
async def test_breeding_identity_endpoints_preserve_supported_stored_participants(
    client: httpx.AsyncClient, endpoint: str, representable: bool
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    ceiling = 2_147_483_647
    doe_id = ceiling if endpoint == "doe" else 1
    buck_id = ceiling if endpoint == "buck" else 1
    service_date = today()
    # A restored positive int4 key at its ceiling is constraint-valid. The
    # real sequence would otherwise take years to reach this identity bound.
    async with get_sessionmaker()() as db:
        db.add_all(
            [
                Animal(
                    id=doe_id,
                    farm_id=farm_id,
                    tag_number="BOUNDARY-DOE",
                    sex="F",
                    source="PURCHASED",
                    current_bucket="FOUNDATION",
                    date_of_birth=date(2024, 1, 1),
                ),
                Animal(
                    id=buck_id,
                    farm_id=farm_id,
                    tag_number="BOUNDARY-BUCK",
                    sex="M",
                    source="PURCHASED",
                    current_bucket="BREEDING",
                    date_of_birth=date(2024, 1, 1),
                ),
            ]
        )
        await db.flush()
        db.add_all(
            [
                WeightRecord(animal_id=doe_id, date=service_date, weight_kg=26.0),
                WeightRecord(animal_id=buck_id, date=service_date, weight_kg=30.0),
            ]
        )
        await db.commit()
    if not representable:
        if endpoint == "doe":
            doe_id += 1
        else:
            buck_id += 1
    response = await client.post(
        "/api/breeding",
        headers=owner,
        json={
            "doe_id": doe_id,
            "buck_id": buck_id,
            "breeding_date": service_date.isoformat(),
        },
    )
    assert response.status_code == (201 if representable else 404), response.text
    if representable:
        assert response.json()["doe_id"] == doe_id
        assert response.json()["buck_id"] == buck_id
    async with get_sessionmaker()() as db:
        count = (
            await db.execute(
                select(func.count())
                .select_from(BreedingRecord)
                .join(Animal, Animal.id == BreedingRecord.doe_id)
                .where(Animal.farm_id == farm_id)
            )
        ).scalar_one()
    assert count == (1 if representable else 0)
