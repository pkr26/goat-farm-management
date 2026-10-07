"""Ultrasound input errors retain farm-local dates and their public status."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.utils import today

from .conftest import register
from .test_breeding_extended import all_tasks, make_animal, make_breeding


@pytest.mark.parametrize("pregnant", [True, False])
async def test_ultrasound_farm_future_is_a_422_and_does_not_consume_the_pending_service(
    client: httpx.AsyncClient, pregnant: bool
) -> None:
    account = await register(client, "ultrasound-calendar@farm.in")
    created = await client.post(
        "/api/auth/farms",
        headers=account,
        json={"name": "West ultrasound farm", "timezone": "Pacific/Honolulu"},
    )
    assert created.status_code == 201, created.text
    owner = account | {"X-Farm-Id": str(created.json()["id"])}
    reference_date = datetime.now(UTC).date()
    identities = []
    for sex, bucket, tag, weight in (
        ("F", "FOUNDATION", "US-DATE-DOE", 26.0),
        ("M", "BREEDING", "US-DATE-BUCK", 30.0),
    ):
        animal = await make_animal(
            client,
            owner,
            tag,
            sex=sex,
            bucket=bucket,
            date_of_birth=(reference_date - timedelta(days=800)).isoformat(),
            weight_kg=weight,
            weight_date=(reference_date - timedelta(days=400)).isoformat(),
        )
        identities.append(int(animal["id"]))
    breeding = await make_breeding(
        client,
        owner,
        identities[0],
        identities[1],
        breeding_date=(reference_date - timedelta(days=40)).isoformat(),
    )
    # The shared schema admits UTC+1 for the world's eastern farms. This
    # same date is always in Honolulu's future, so the farm guard must own
    # the rejection before recording any clinical result or duty changes.
    invalid = await client.post(
        f"/api/breeding/{breeding['id']}/ultrasound",
        headers=owner,
        json={"pregnant": pregnant, "date": (reference_date + timedelta(days=1)).isoformat()},
    )
    assert invalid.status_code == 422, invalid.text
    assert invalid.json()["detail"] == "ultrasound date cannot be in the future for this farm"
    assert invalid.json()["code"] == "VALIDATION_ERROR"
    pending = await client.get(f"/api/breeding/{breeding['id']}", headers=owner)
    assert pending.status_code == 200, pending.text
    assert pending.json()["outcome"] == "PENDING"
    assert pending.json()["ultrasound_done"] is False
    assert pending.json()["ultrasound_result_date"] is None
    duties = await all_tasks(client, owner)
    scans = [
        duty
        for duty in duties
        if duty["breeding_record_id"] == breeding["id"] and duty["category"] == "ULTRASOUND"
    ]
    assert len(scans) == 1 and scans[0]["status"] == "PENDING"
    actual_date = today("Pacific/Honolulu").isoformat()
    accepted = await client.post(
        f"/api/breeding/{breeding['id']}/ultrasound",
        headers=owner,
        json={"pregnant": pregnant, "date": actual_date},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["ultrasound_result_date"] == actual_date
    assert accepted.json()["outcome"] == ("CONFIRMED_PREGNANT" if pregnant else "FAILED")
