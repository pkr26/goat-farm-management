"""Real kidding and rebreeding writers finish without a parent-lock deadlock."""

import asyncio
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from app.db import get_sessionmaker
from app.models import Animal, BreedingRecord, KiddingRecord, KidEntry
from app.utils import today

from .conftest import owner_with_farm
from .test_concurrency import (
    confirm_pregnancy,
    iso,
    make_animal,
    make_doe,
    second_client,
    wait_for_blocked_sessions,
    wait_until_blocked,
    warm,
)


async def test_real_kidding_and_rebreeding_complete_with_one_litter_and_one_live_kid(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    buck = await make_animal(client, owner, tag="PARENT-PROGRESS-B", sex="M", bucket="BREEDING")
    doe = await make_doe(client, owner, tag="PARENT-PROGRESS-D")
    assert buck < doe
    created = await client.post(
        "/api/breeding",
        json={"doe_id": doe, "buck_id": buck, "breeding_date": iso(today() - timedelta(days=150))},
        headers=owner,
    )
    assert created.status_code == 201, created.text
    breeding_id = created.json()["id"]
    await confirm_pregnancy(client, owner, breeding_id)
    holder = get_sessionmaker()()
    await holder.execute(
        select(BreedingRecord.id).where(BreedingRecord.id == breeding_id).with_for_update()
    )
    kidding: asyncio.Task[httpx.Response] | None = None
    rebreeding: asyncio.Task[httpx.Response] | None = None
    try:
        async with second_client() as other:
            await warm(other, owner)
            kidding = asyncio.create_task(
                client.post(
                    "/api/kidding",
                    json={
                        "breeding_record_id": breeding_id,
                        "date": iso(today()),
                        "kids": [{"sex": "F"}],
                    },
                    headers=owner,
                )
            )
            await wait_until_blocked()
            rebreeding = asyncio.create_task(
                other.post(
                    "/api/breeding",
                    json={"doe_id": doe, "buck_id": buck, "breeding_date": iso(today())},
                    headers=owner,
                )
            )
            await wait_for_blocked_sessions(2)
            await holder.commit()
            try:
                delivered, rejected = await asyncio.wait_for(
                    asyncio.gather(kidding, rebreeding), timeout=30
                )
            except DBAPIError as error:
                code = getattr(error.orig, "sqlstate", getattr(error.orig, "pgcode", None))
                if code != "40P01":
                    raise
                pytest.fail(
                    f"Valid reproductive writers must not form a parent-lock deadlock: {error}"
                )
    finally:
        await holder.rollback()
        await holder.close()
        requests = [request for request in (kidding, rebreeding) if request is not None]
        for request in requests:
            if not request.done():
                request.cancel()
        await asyncio.gather(*requests, return_exceptions=True)
    assert delivered.status_code == 201, delivered.text
    assert rejected.status_code == 400, rejected.text
    async with get_sessionmaker()() as db:
        litter_count = await db.scalar(
            select(func.count()).select_from(KiddingRecord).where(KiddingRecord.doe_id == doe)
        )
        offspring = list(
            (
                await db.execute(
                    select(KidEntry)
                    .join(KiddingRecord, KidEntry.kidding_record_id == KiddingRecord.id)
                    .where(KiddingRecord.doe_id == doe)
                )
            ).scalars()
        )
        live_animals = list(
            (await db.execute(select(Animal).where(Animal.dam_id == doe))).scalars()
        )
        assert litter_count == 1 and len(offspring) == len(live_animals) == 1
        assert offspring[0].status == "ALIVE" and offspring[0].animal_id == live_animals[0].id
        assert live_animals[0].sire_id == buck
