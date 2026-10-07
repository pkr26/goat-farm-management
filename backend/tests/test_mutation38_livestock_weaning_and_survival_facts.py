"""Real weaning audit attribution and conservative retained birth provenance."""

import httpx
import pytest
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import BucketMove, Farm, KidEntry
from app.services.tasks import _litter_has_surviving_kid
from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_extended import all_tasks, kid_on_ekd, pregnant_doe


async def test_public_weaning_attributes_each_real_movement_to_the_completing_owner(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    doe, _, breeding = await pregnant_doe(client, owner, "WEAN-AUDIT", gestation_days=220)
    delivery = await kid_on_ekd(client, owner, breeding)
    kids = delivery["kids"]
    family = [int(doe["id"]), *[int(kid["animal_id"]) for kid in kids]]
    expected = {int(doe["id"]): "RESTING"} | {
        int(kid["animal_id"]): "MALE_KIDS" if kid["sex"] == "M" else "FEMALE_KIDS" for kid in kids
    }
    duties = [
        task
        for task in await all_tasks(client, owner)
        if task["breeding_record_id"] == breeding["id"] and task["category"] == "WEANING"
    ]
    assert len(duties) == 1
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, int(owner["X-Farm-Id"]))
        assert farm is not None
        actor_id = farm.owner_id
        movement_date = today(farm.timezone)
        existing_ids = set(
            (
                await db.execute(select(BucketMove.id).where(BucketMove.animal_id.in_(family)))
            ).scalars()
        )
    response = await client.post(f"/api/tasks/{duties[0]['id']}/complete", headers=owner)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "DONE"
    assert response.json()["completed_by_id"] == actor_id
    async with get_sessionmaker()() as db:
        moves = list(
            (await db.execute(select(BucketMove).where(BucketMove.animal_id.in_(family)))).scalars()
        )
        created = [move for move in moves if move.id not in existing_ids]
        assert len(created) == 3
        assert {move.animal_id: move.to_bucket for move in created} == expected
        assert all(move.from_bucket == "RECOVERY" for move in created)
        assert all(move.effective_date == movement_date for move in created)
        assert all(move.created_by_id == actor_id for move in created)


@pytest.mark.parametrize("birth_status", ["ALIVE", "STILLBORN"])
async def test_survival_assessment_keeps_unknown_legacy_live_births_fail_closed(
    client: httpx.AsyncClient, birth_status: str
) -> None:
    owner = await owner_with_farm(client)
    _, _, breeding = await pregnant_doe(client, owner, "UNKNOWN-LINK", gestation_days=160)
    delivery = await kid_on_ekd(
        client, owner, breeding, kids=[{"sex": "F", "status": birth_status}]
    )
    async with get_sessionmaker()() as db:
        entry = (
            await db.execute(
                select(KidEntry).where(
                    KidEntry.farm_id == int(owner["X-Farm-Id"]),
                    KidEntry.kidding_record_id == int(delivery["id"]),
                )
            )
        ).scalar_one()
        # Older retained birth facts can lack the later Animal link. Keep
        # the real ALIVE/STILLBORN birth fact and every FK/CHECK enabled;
        # absence of a link is not evidence of a live kid's death or exit.
        entry.animal_id = None
        await db.commit()
    async with get_sessionmaker()() as db:
        entries = list(
            (
                await db.execute(
                    select(KidEntry).where(
                        KidEntry.farm_id == int(owner["X-Farm-Id"]),
                        KidEntry.kidding_record_id == int(delivery["id"]),
                    )
                )
            ).scalars()
        )
        assert len(entries) == 1
        assert entries[0].status == birth_status
        assert entries[0].animal_id is None
        result = await _litter_has_surviving_kid(db, int(owner["X-Farm-Id"]), entries)
        assert result is (birth_status == "ALIVE")
