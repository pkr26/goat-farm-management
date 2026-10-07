"""Model weight recency follows measurement date and assigned entry identity."""

import httpx
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db import get_sessionmaker
from app.models import Animal, WeightRecord
from app.utils import today

from .conftest import owner_with_farm
from .test_concurrency import make_animal


async def test_latest_weight_same_day_uses_persisted_entry_order_not_collection_order(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="same-day-weight-order@farm.in")
    animal_id = await make_animal(client, owner, tag="SAME-DAY-WEIGHT")
    measurement_date = today()
    ids = []
    for value in (26.0, 30.0):
        response = await client.post(
            f"/api/animals/{animal_id}/weight",
            headers=owner,
            json={"date": measurement_date.isoformat(), "weight_kg": value},
        )
        assert response.status_code == 201, response.text
        ids.append(response.json()["id"])
    assert 0 < ids[0] < ids[1]
    async with get_sessionmaker()() as db:
        animal = (
            await db.execute(
                select(Animal)
                .where(Animal.id == animal_id)
                .options(selectinload(Animal.weight_records))
            )
        ).scalar_one()
        # Relationship ordering is by date alone. Same-day rows may arrive
        # in either identity order; the model's tie rule must be independent.
        animal.weight_records.sort(key=lambda record: record.id)
        assert [record.id for record in animal.weight_records] == ids
        latest = animal.latest_weight
        assert latest is not None
        assert latest.id == ids[1]
        assert latest.weight_kg == 30.0
        assert animal.latest_weight_kg == 30.0
        assert animal.latest_weight_kg_on(measurement_date) == 30.0


async def test_latest_weight_keeps_assigned_identity_ahead_of_an_unflushed_same_day_reading(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="pending-weight-order@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    animal_id = await make_animal(client, owner, tag="PENDING-WEIGHT")
    measurement_date = today()
    response = await client.post(
        f"/api/animals/{animal_id}/weight",
        headers=owner,
        json={"date": measurement_date.isoformat(), "weight_kg": 27.5},
    )
    assert response.status_code == 201, response.text
    # A genuinely generated first PK, rather than a restored or invented ID.
    assert response.json()["id"] == 1
    async with get_sessionmaker()() as db:
        animal = (
            await db.execute(
                select(Animal)
                .where(Animal.id == animal_id)
                .options(selectinload(Animal.weight_records))
            )
        ).scalar_one()
        assert len(animal.weight_records) == 1
        persisted = animal.weight_records[0]
        pending = WeightRecord(
            farm_id=farm_id,
            animal_id=animal_id,
            date=measurement_date,
            weight_kg=29.0,
        )
        animal.weight_records.insert(0, pending)
        assert pending in db.new
        assert pending.id is None
        # Pending measurements have no assigned entry identity. The public
        # model helper and its existing as-of twin prefer an assigned record
        # until the new row is flushed and receives its real sequence ID.
        assert animal.latest_weight is persisted
        assert animal.latest_weight_kg == 27.5
        assert animal.latest_weight_kg_on(measurement_date) == 27.5
        await db.flush()
        assert pending.id is not None
        assert pending.id > persisted.id
        assert animal.latest_weight is pending
        assert animal.latest_weight_kg == 29.0
        assert animal.latest_weight_kg_on(measurement_date) == 29.0
