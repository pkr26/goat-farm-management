"""The sire workload counter distinguishes open services from completed cycles."""

from datetime import timedelta

import httpx
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import BreedingRecord, KiddingRecord
from app.services.breeding import _buck_open_service_count
from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_extended import (
    confirm,
    fail_cycle,
    iso,
    kid_on_ekd,
    make_breeding,
    make_buck,
    make_doe,
)


async def test_sire_open_count_matches_real_pending_and_undelivered_cohorts(
    client: httpx.AsyncClient,
) -> None:
    """Only pending and confirmed pregnancies that have not delivered use a slot."""
    headers = await owner_with_farm(client)
    farm_id = int(headers["X-Farm-Id"])
    buck = await make_buck(client, headers, "OPEN-COUNT-BUCK")
    ids: dict[str, int] = {}
    for label in ("pending", "confirmed-one", "confirmed-two", "delivered", "failed"):
        doe = await make_doe(client, headers, f"OPEN-COUNT-{label}")
        breeding = await make_breeding(
            client,
            headers,
            doe["id"],
            buck["id"],
            breeding_date=iso(today() - timedelta(days=160)),
        )
        ids[label] = breeding["id"]
        if label.startswith("confirmed") or label == "delivered":
            breeding = await confirm(client, headers, breeding["id"])
        if label == "delivered":
            await kid_on_ekd(client, headers, breeding)
        if label == "failed":
            await fail_cycle(client, headers, breeding["id"])

    async with get_sessionmaker()() as db:
        records = list(
            (
                await db.execute(select(BreedingRecord).where(BreedingRecord.id.in_(ids.values())))
            ).scalars()
        )
        outcomes = {record.id: record.outcome for record in records}
        delivered_ids = set(
            (
                await db.execute(
                    select(KiddingRecord.breeding_record_id).where(
                        KiddingRecord.breeding_record_id.in_(ids.values())
                    )
                )
            ).scalars()
        )
        assert outcomes == {
            ids["pending"]: "PENDING",
            ids["confirmed-one"]: "CONFIRMED_PREGNANT",
            ids["confirmed-two"]: "CONFIRMED_PREGNANT",
            ids["delivered"]: "CONFIRMED_PREGNANT",
            ids["failed"]: "FAILED",
        }
        assert delivered_ids == {ids["delivered"]}
        assert await _buck_open_service_count(db, farm_id, buck["id"]) == 3
