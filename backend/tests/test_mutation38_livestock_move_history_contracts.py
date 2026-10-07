"""Owner history correction preserves context and the full movement audit field."""

import httpx

from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_extended import get_animal, iso, make_kidding, place_health_hold, pregnant_doe


async def test_cleared_orphan_owner_history_correction_keeps_override_context(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    round_date = today()
    dam, _buck, breeding = await pregnant_doe(
        client, headers, "HISTORY-CORRECTION-DAM", gestation_days=160
    )
    birth = await make_kidding(
        client,
        headers,
        breeding["id"],
        date=iso(round_date),
        kids=[{"tag": "HISTORY-CORRECTION-KID", "sex": "M", "status": "ALIVE"}],
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
            "clearance_reference": "District AHD correction clearance",
            "expected_restriction_version": 1,
        },
    )
    assert cleared.status_code == 204, cleared.text
    moved = await client.post(
        f"/api/animals/{kid_id}/move",
        headers=headers,
        json={
            "to_bucket": "FOUNDATION",
            "history_override": True,
            "reason": "Owner corrected the imported lifecycle classification",
        },
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["current_bucket"] == "FOUNDATION"
    profile = await client.get(f"/api/animals/{kid_id}", headers=headers)
    assert profile.status_code == 200, profile.text
    assert profile.json()["moves"][0]["reason"] == (
        "[HISTORY OVERRIDE] Owner corrected the imported lifecycle classification"
    )


async def test_owner_history_move_retains_the_255th_audit_character(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    created = await client.post(
        "/api/animals",
        headers=owner,
        json={
            "tag_number": "HISTORY-MOVE-CAPACITY",
            "sex": "F",
            "source": "BORN",
            "current_bucket": "FOUNDATION",
            "historical_import_reason": "Existing herd migration",
        },
    )
    assert created.status_code == 201, created.text
    animal_id = created.json()["id"]
    moved = await client.post(
        f"/api/animals/{animal_id}/move",
        headers=owner,
        json={
            "to_bucket": "RESTING",
            "history_override": True,
            "reason": "x" * 235 + "Z" + " after capacity",
        },
    )
    assert moved.status_code == 200, moved.text
    profile = await client.get(f"/api/animals/{animal_id}", headers=owner)
    assert profile.status_code == 200, profile.text
    reason = profile.json()["moves"][0]["reason"]
    assert reason == "[HISTORY OVERRIDE] " + "x" * 235 + "Z"
    assert len(reason) == 255
