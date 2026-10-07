"""Breeding wire bounds preserve natural-service and scan business facts."""

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select

from app.db import get_sessionmaker
from app.models import BreedingRecord, IdempotencyRecord
from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_extended import make_buck, make_doe


async def test_breeding_accepts_upper_wire_cycle_and_a_supported_quadruplet_scan(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner, "SCHEMA-DOE")
    buck = await make_buck(client, owner, "SCHEMA-BUCK")
    breeding_date = today() - timedelta(days=35)
    response = await client.post(
        "/api/breeding",
        headers=owner,
        json={
            "doe_id": doe["id"],
            "buck_id": buck["id"],
            "breeding_date": breeding_date.isoformat(),
            "heat_cycle_number": 99,
        },
    )
    assert response.status_code == 201, response.text
    record_id = response.json()["id"]
    # The accepted wire cycle does not override the server's observed history.
    assert response.json()["heat_cycle_number"] == 1
    scan = await client.post(
        f"/api/breeding/{record_id}/ultrasound",
        headers=owner,
        json={"pregnant": True, "kid_count": 4, "date": today().isoformat()},
    )
    assert scan.status_code == 200, scan.text
    assert scan.json()["kid_count_detected"] == 4
    assert scan.json()["outcome"] == "CONFIRMED_PREGNANT"
    assert scan.json()["pregnant"] is True
    async with get_sessionmaker()() as db:
        record = await db.get(BreedingRecord, record_id)
        assert record is not None
        assert record.kid_count_detected == 4
        assert record.outcome == "CONFIRMED_PREGNANT"


@pytest.mark.parametrize("name_length", [120, 121])
async def test_semen_identity_bound_distinguishes_input_shape_from_goat_protocol_refusal(
    client: httpx.AsyncClient, name_length: int
) -> None:
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner, "SEMEN-BOUNDARY-DOE")
    response = await client.post(
        "/api/breeding",
        headers=owner,
        json={
            "doe_id": doe["id"],
            "method": "AI",
            "semen_sire_name": "S" * name_length,
            "breeding_date": today().isoformat(),
        },
    )
    if name_length == 120:
        # AI is a reserved wire method with a deliberate business refusal for
        # goats. A bounded identity reaches that established species fence.
        assert response.status_code == 409, response.text
        assert "not part of the goat protocol" in response.json()["detail"]
    else:
        assert response.status_code == 422, response.text
        assert any(
            error["loc"] == ["body", "semen_sire_name"] and error["type"] == "too_long"
            for error in response.json()["detail"]
        ), response.text
    async with get_sessionmaker()() as db:
        assert (await db.execute(select(func.count(BreedingRecord.id)))).scalar_one() == 0


async def test_retained_older_breeding_response_keeps_missing_kidding_flag_false(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner, "RETAINED-SERVICE-DOE")
    buck = await make_buck(client, owner, "RETAINED-SERVICE-BUCK")
    farm_id = int(owner["X-Farm-Id"])
    headers = owner | {"Idempotency-Key": "retained-breeding-response"}
    payload = {
        "doe_id": doe["id"],
        "buck_id": buck["id"],
        "breeding_date": today().isoformat(),
    }
    original = await client.post("/api/breeding", json=payload, headers=headers)
    assert original.status_code == 201, original.text
    assert original.json()["has_kidding"] is False
    async with get_sessionmaker()() as db:
        claim = (
            await db.execute(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.farm_id == farm_id,
                    IdempotencyRecord.operation == "POST /api/breeding",
                )
            )
        ).scalar_one()
        assert claim.completed_at is not None
        assert claim.response_status == 201
        assert claim.response_body is not None
        retained = dict(claim.response_body)
        assert retained.pop("has_kidding") is False
        claim.response_body = retained
        await db.commit()
    replay = await client.post("/api/breeding", json=payload, headers=headers)
    assert replay.status_code == 201, replay.text
    assert replay.headers.get("Idempotency-Replayed") == "true"
    assert replay.json()["id"] == original.json()["id"]
    assert replay.json()["has_kidding"] is False
    async with get_sessionmaker()() as db:
        assert (await db.execute(select(func.count(BreedingRecord.id)))).scalar_one() == 1
