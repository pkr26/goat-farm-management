"""Literal newborn scale limits across historical entry and kidding."""

from datetime import timedelta

import httpx

from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_extended import kid_on_ekd, make_animal, pregnant_doe


async def test_historical_birth_weight_accepts_eight_rejects_eight_point_zero_one(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    rejected = await client.post(
        "/api/animals",
        headers=owner,
        json={
            "tag_number": "HISTORICAL-OVERWEIGHT",
            "sex": "F",
            "source": "BORN",
            "current_bucket": "FEMALE_KIDS",
            "date_of_birth": (today() - timedelta(days=90)).isoformat(),
            "historical_import_reason": "Existing herd register",
            "birth_weight": 8.01,
        },
    )
    assert rejected.status_code == 422, rejected.text
    assert "not a credible newborn weight" in rejected.json()["detail"]
    accepted = await make_animal(
        client,
        owner,
        "HISTORICAL-BIRTH-CEILING",
        source="BORN",
        bucket="FEMALE_KIDS",
        date_of_birth=(today() - timedelta(days=90)).isoformat(),
        historical_import_reason="Existing herd register",
        birth_weight=8.0,
    )
    assert accepted["birth_weight"] == 8.0


async def test_kidding_birth_weight_accepts_eight_rejects_eight_point_zero_one(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    _, _, breeding = await pregnant_doe(client, owner, gestation_days=160)
    rejected = await client.post(
        "/api/kidding",
        headers=owner,
        json={
            "breeding_record_id": breeding["id"],
            "date": breeding["expected_kidding_date"],
            "kids": [{"tag": "NEWBORN-OVERWEIGHT", "sex": "F", "birth_weight": 8.01}],
        },
    )
    assert rejected.status_code == 422, rejected.text
    assert "not a credible newborn weight" in rejected.json()["detail"]
    accepted = await kid_on_ekd(
        client,
        owner,
        breeding,
        kids=[{"tag": "NEWBORN-BIRTH-CEILING", "sex": "F", "birth_weight": 8.0}],
    )
    assert accepted["kids"][0]["birth_weight"] == 8.0
