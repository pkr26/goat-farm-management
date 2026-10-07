"""A real one-pregnancy farm must complete the operational dashboard read."""

from datetime import date, timedelta

import httpx
from sqlalchemy import Date, cast, func, select

from app.db import get_sessionmaker
from app.models import BreedingRecord, Farm

from .conftest import owner_with_farm
from .test_finance_extended import make_animal


async def test_single_actual_due_pregnancy_completes_and_retains_its_identity(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="mutation-dashboard-single-pregnancy@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    doe = await make_animal(client, owner, tag="NATIVE-SINGLE-DUE")
    async with get_sessionmaker()() as db:
        day = (
            await db.execute(
                select(cast(func.timezone(Farm.timezone, func.now()), Date)).where(
                    Farm.id == farm_id
                )
            )
        ).scalar_one()
        assert isinstance(day, date)
        service = day - timedelta(days=150)
        record = BreedingRecord(
            farm_id=farm_id,
            doe_id=doe["id"],
            method="AI",
            breeding_date=service,
            ultrasound_done=True,
            ultrasound_result_date=service + timedelta(days=32),
            pregnant=True,
            outcome="CONFIRMED_PREGNANT",
            expected_kidding_date=day,
        )
        db.add(record)
        await db.commit()
        expected_id = record.id
    response = await client.get("/api/dashboard", headers=owner)
    assert response.status_code == 200, response.text
    document = response.json()
    assert document["kiddings_due_total"] == 1
    assert [row["id"] for row in document["kiddings_due"]] == [expected_id]
    assert document["kiddings_due"][0]["doe_id"] == doe["id"]
    assert document["kiddings_due"][0]["expected_kidding_date"] == day.isoformat()
