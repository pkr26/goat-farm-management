"""The create endpoint distinguishes the int4 ceiling from impossible IDs."""

import httpx

from app.db import get_sessionmaker
from app.models import BreedingRecord
from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_extended import pregnant_doe


async def test_kidding_creation_id_above_int4_returns404(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    response = await client.post(
        "/api/kidding",
        json={
            "breeding_record_id": 2_147_483_648,
            "date": today().isoformat(),
            "kids": [{"sex": "F"}],
        },
        headers=owner,
    )
    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Breeding record not found"


async def test_kidding_creation_accepts_actual_restored_int4_ceiling(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    _doe, _buck, breeding = await pregnant_doe(client, owner, gestation_days=160)
    async with get_sessionmaker()() as db:
        original = await db.get(BreedingRecord, breeding["id"])
        assert original is not None and original.outcome == "CONFIRMED_PREGNANT"
        restored = BreedingRecord(
            id=2_147_483_647,
            created_at=original.created_at,
            farm_id=original.farm_id,
            doe_id=original.doe_id,
            buck_id=original.buck_id,
            semen_sire_name=original.semen_sire_name,
            breeding_date=original.breeding_date,
            method=original.method,
            heat_cycle_number=original.heat_cycle_number,
            ultrasound_date=original.ultrasound_date,
            ultrasound_result_date=original.ultrasound_result_date,
            ultrasound_done=original.ultrasound_done,
            pregnant=original.pregnant,
            kid_count_detected=original.kid_count_detected,
            expected_kidding_date=original.expected_kidding_date,
            outcome=original.outcome,
            created_by_id=original.created_by_id,
        )
        # Repeat this actual confirmed event under its restored storage key;
        # keep current constraints and the original duty graph intact.
        db.add(restored)
        await db.commit()
        assert restored.id == 2_147_483_647
    response = await client.post(
        "/api/kidding",
        json={
            "breeding_record_id": 2_147_483_647,
            "date": breeding["expected_kidding_date"],
            "kids": [{"sex": "F"}],
        },
        headers=owner,
    )
    assert response.status_code == 201, response.text
    assert response.json()["breeding_record_id"] == 2_147_483_647
