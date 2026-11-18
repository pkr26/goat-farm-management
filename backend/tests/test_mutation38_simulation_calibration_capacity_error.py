"""Calibration reports an internal model limit reached by a valid live register."""

from datetime import timedelta

import httpx
from sqlalchemy import func, select

from app.db import get_sessionmaker
from app.models import Animal, Farm
from app.utils import today

from .conftest import owner_with_farm
from .type_helpers import json_object


async def test_calibration_handles_a_real_register_above_the_model_cohort_limit(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        if farm is None:
            raise RuntimeError("The newly created owned farm is missing")
        purchased_on = today(farm.timezone) - timedelta(days=1)

    # Every stored animal comes from the real purchase endpoint. Unknown age
    # admits a male into the adult opening cohort; omit price and weight so
    # this experiment does not introduce unrelated calibration evidence.
    payload: dict[str, object] = {
        "date": purchased_on.isoformat(),
        "count": 1000,
        "sex": "M",
        "create_animals": True,
    }
    batch_ids: set[int] = set()
    for _ in range(100):
        purchase = await client.post("/api/purchases/new", headers=owner, json=payload)
        assert purchase.status_code == 201, purchase.text
        body = json_object(purchase.json())
        assert body["count"] == 1000
        batch_ids.add(int(body["id"]))
    assert len(batch_ids) == 100

    at_limit = await client.get("/api/simulation/calibration", headers=owner)
    assert at_limit.status_code == 200, at_limit.text
    at_limit_body = json_object(at_limit.json())
    assert at_limit_body["assumptions"]["herd"]["bucks"] == 100_000

    extra = await client.post("/api/purchases/new", headers=owner, json={**payload, "count": 1})
    assert extra.status_code == 201, extra.text
    extra_body = json_object(extra.json())
    assert extra_body["count"] == 1
    assert int(extra_body["id"]) not in batch_ids
    async with get_sessionmaker()() as db:
        actual = await db.scalar(
            select(func.count(Animal.id)).where(
                Animal.farm_id == farm_id,
                Animal.sex == "M",
                Animal.status == "ACTIVE",
                Animal.date_of_birth.is_(None),
                Animal.estimated_dob.is_(None),
            )
        )
    assert actual == 100_001

    above_limit = await client.get("/api/simulation/calibration", headers=owner)
    assert json_object(above_limit.json())["detail"] == (
        "Calibration produced an assumption set the model rejects."
    )
    assert above_limit.status_code == 500, "Internal calibration model rejection is a server error"
