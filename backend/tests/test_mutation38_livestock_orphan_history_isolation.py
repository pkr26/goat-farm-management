"""A different kid's completed weaning cannot block a cleared orphan."""

import httpx

from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_extended import (
    get_animal,
    iso,
    make_kidding,
    place_health_hold,
    pregnant_doe,
)


async def test_cleared_orphan_weans_despite_an_unrelated_kids_recovery_exit(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    round_date = today()
    other_dam, _buck, other_breeding = await pregnant_doe(
        client, headers, "UNRELATED-DAM", gestation_days=160
    )
    other_birth = await make_kidding(
        client,
        headers,
        other_breeding["id"],
        date=iso(round_date),
        kids=[{"tag": "UNRELATED-KID", "sex": "M", "status": "ALIVE"}],
    )
    other_kid = other_birth["kids"][0]["animal_id"]
    assert other_kid is not None
    retired = await client.post(
        f"/api/animals/{other_dam['id']}/status",
        headers=headers,
        json={"new_status": "DEAD", "date": iso(round_date)},
    )
    assert retired.status_code == 200, retired.text
    other_profile = await client.get(f"/api/animals/{other_kid}", headers=headers)
    assert other_profile.status_code == 200, other_profile.text
    assert (await get_animal(client, headers, other_kid))["current_bucket"] == "MALE_KIDS"
    assert any(move["from_bucket"] == "RECOVERY" for move in other_profile.json()["moves"])

    dam, _buck, breeding = await pregnant_doe(
        client, headers, "HELD-ORPHAN-DAM", gestation_days=160
    )
    birth = await make_kidding(
        client,
        headers,
        breeding["id"],
        date=iso(round_date),
        kids=[{"tag": "HELD-ORPHAN-KID", "sex": "M", "status": "ALIVE"}],
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
    cleared = await client.post(
        f"/api/health/restrictions/{kid_id}/clear",
        headers=headers,
        json={
            "clearance_reference": "District AHD unrelated-history clearance",
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
    profile = await client.get(f"/api/animals/{kid_id}", headers=headers)
    assert profile.status_code == 200, profile.text
    assert profile.json()["moves"][0]["reason"] == (
        "Dam no longer active — deferred early wean after hold clearance"
    )
