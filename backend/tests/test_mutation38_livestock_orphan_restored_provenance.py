"""Duplicate historical links to one real birth still prove orphan provenance."""

import httpx
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import KidEntry
from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_extended import (
    get_animal,
    iso,
    make_kidding,
    place_health_hold,
    pregnant_doe,
)


async def test_cleared_orphan_weans_with_duplicate_restored_links_to_the_same_birth(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    round_date = today()
    dam, _buck, breeding = await pregnant_doe(
        client, headers, "RESTORED-ORPHAN-DAM", gestation_days=160
    )
    birth = await make_kidding(
        client,
        headers,
        breeding["id"],
        date=iso(round_date),
        kids=[{"tag": "RESTORED-ORPHAN-KID", "sex": "M", "status": "ALIVE"}],
    )
    kid_id = birth["kids"][0]["animal_id"]
    assert kid_id is not None
    await place_health_hold(client, headers, kid_id)
    retired = await client.post(
        f"/api/animals/{dam['id']}/status",
        headers=headers,
        json={"new_status": "DEAD", "date": iso(round_date)},
    )
    assert retired.status_code == 200, retired.text
    assert (await get_animal(client, headers, kid_id))["current_bucket"] == "RECOVERY"

    # Restores retain historical rows. Both model and migration schemas allow
    # more than one KidEntry to reference an animal, with a nullable tag. Keep
    # the exact real delivery facts and every current FK/check/trigger active:
    # this is a repeated provenance link, never a fictitious second birth.
    async with get_sessionmaker()() as db:
        original = (
            await db.execute(
                select(KidEntry).where(
                    KidEntry.farm_id == int(headers["X-Farm-Id"]),
                    KidEntry.animal_id == kid_id,
                )
            )
        ).scalar_one()
        db.add(
            KidEntry(
                farm_id=original.farm_id,
                kidding_record_id=original.kidding_record_id,
                animal_id=original.animal_id,
                tag=None,
                sex=original.sex,
                status=original.status,
                birth_weight=original.birth_weight,
                mortality_reported_at=original.mortality_reported_at,
                colostrum_within_2h=original.colostrum_within_2h,
                navel_dipped=original.navel_dipped,
                dam_rejected=original.dam_rejected,
            )
        )
        await db.commit()
    async with get_sessionmaker()() as db:
        links = list(
            (
                await db.execute(
                    select(KidEntry).where(
                        KidEntry.farm_id == int(headers["X-Farm-Id"]),
                        KidEntry.animal_id == kid_id,
                    )
                )
            ).scalars()
        )
        assert len(links) == 2
        assert {row.kidding_record_id for row in links} == {birth["id"]}
        assert {row.status for row in links} == {"ALIVE"}
        assert {row.sex for row in links} == {"M"}

    cleared = await client.post(
        f"/api/health/restrictions/{kid_id}/clear",
        headers=headers,
        json={
            "clearance_reference": "District AHD restored-record clearance",
            "expected_restriction_version": 1,
        },
    )
    assert cleared.status_code == 204, cleared.text
    moved = await client.post(
        f"/api/animals/{kid_id}/move",
        headers=headers,
        json={"to_bucket": "MALE_KIDS"},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["current_bucket"] == "MALE_KIDS"
    assert (await get_animal(client, headers, kid_id))["current_bucket"] == "MALE_KIDS"
