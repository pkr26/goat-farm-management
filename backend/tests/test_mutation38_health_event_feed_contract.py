"""A health event feed returns precisely its farm's recorded clinical facts."""

import httpx
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import HealthEvent

from .conftest import owner_with_farm
from .test_health_extended import make_animal


async def test_health_event_feed_returns_own_fact_and_keeps_foreign_fact_out(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="health-feed-owner@farm.in")
    other = await owner_with_farm(client, email="health-feed-other@farm.in")
    ids = []
    for headers, tag, note in (
        (owner, "OWN-EXAM", "Own farm's recorded examination"),
        (other, "OTHER-EXAM", "Other farm's recorded examination"),
    ):
        animal = await make_animal(client, headers, tag=tag)
        recorded = await client.post(
            "/api/health/events",
            headers=headers,
            json={"scope": "animal", "animal_id": animal["id"], "type": "EXAM", "notes": note},
        )
        assert recorded.status_code == 201, recorded.text
        async with get_sessionmaker()() as db:
            event = (
                await db.execute(
                    select(HealthEvent).where(HealthEvent.farm_id == int(headers["X-Farm-Id"]))
                )
            ).scalar_one()
            assert event.animal_id == animal["id"] and event.notes == note
            ids.append(event.id)
    for headers, expected, foreign in ((owner, ids[0], ids[1]), (other, ids[1], ids[0])):
        read = await client.get("/api/health/events", headers=headers)
        assert read.status_code == 200, read.text
        body = read.json()
        assert body["total"] == 1
        assert [row["id"] for row in body["events"]] == [expected]
        assert foreign not in {row["id"] for row in body["events"]}
        assert body["limit"] == 100 and body["offset"] == 0
