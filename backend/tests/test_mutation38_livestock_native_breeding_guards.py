"""Native breeding calls retain secure defaults and the postpartum date floor."""

from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from app.db import get_sessionmaker
from app.models import Animal, BreedingRecord, Farm
from app.services.breeding import breeding_weights_as_of, create_breeding_record
from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_extended import confirm, fail_cycle, make_animal, make_breeding


async def _mature_pair(
    client: httpx.AsyncClient, owner: dict[str, str], reference_date: date
) -> tuple[int, int]:
    ids = []
    for sex, bucket, tag, weight in (
        ("F", "FOUNDATION", "NATIVE-BREED-DOE", 26.0),
        ("M", "BREEDING", "NATIVE-BREED-BUCK", 30.0),
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
        ids.append(int(animal["id"]))
    return ids[0], ids[1]


async def test_omitting_native_owner_authority_cannot_override_a_real_cull_candidate(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    reference_date = today()
    doe_id, buck_id = await _mature_pair(client, owner, reference_date)
    for days_ago in (120, 60):
        breeding = await make_breeding(
            client,
            owner,
            doe_id,
            buck_id,
            breeding_date=(reference_date - timedelta(days=days_ago)).isoformat(),
        )
        await fail_cycle(client, owner, int(breeding["id"]))
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, int(owner["X-Farm-Id"]))
        doe = await db.get(Animal, doe_id)
        buck = await db.get(Animal, buck_id)
        assert farm is not None and doe is not None and buck is not None
        assert doe.cull_candidate is True
        old_records = await db.scalar(
            select(func.count()).select_from(BreedingRecord).where(BreedingRecord.doe_id == doe_id)
        )
        assert old_records == 2
        weights = await breeding_weights_as_of(db, [doe_id, buck_id], reference_date)
        assert weights[doe_id] == 26.0
        # A native caller that supplies no positive owner authority must
        # fail closed; an unknown actor cannot inherit the owner's override.
        with pytest.raises(ValueError, match="only the farm owner"):
            await create_breeding_record(
                db,
                farm,
                doe,
                buck,
                reference_date,
                doe_latest_weight_kg=weights[doe_id],
                has_open_breeding=False,
            )
        assert not db.new
        # A genuine owner explicitly supplies the authority and attribution.
        admitted = await create_breeding_record(
            db,
            farm,
            doe,
            buck,
            reference_date,
            created_by_id=farm.owner_id,
            doe_latest_weight_kg=weights[doe_id],
            has_open_breeding=False,
            actor_is_owner=True,
        )
        await db.commit()
        assert admitted.created_by_id == farm.owner_id
        assert admitted.doe_id == doe_id and admitted.buck_id == buck_id


@pytest.mark.parametrize(("postpartum_days", "accepted"), [(13, False), (14, True), (15, True)])
async def test_native_service_preserves_the_actual_postpartum_waiting_floor(
    client: httpx.AsyncClient, postpartum_days: int, accepted: bool
) -> None:
    owner = await owner_with_farm(client)
    reference_date = today()
    doe_id, buck_id = await _mature_pair(client, owner, reference_date)
    birth_date = reference_date - timedelta(days=20)
    breeding = await make_breeding(
        client,
        owner,
        doe_id,
        buck_id,
        breeding_date=(birth_date - timedelta(days=150)).isoformat(),
    )
    await confirm(client, owner, int(breeding["id"]), kid_count=1)
    kidding = await client.post(
        "/api/kidding",
        headers=owner,
        json={
            "breeding_record_id": breeding["id"],
            "date": birth_date.isoformat(),
            "ease": "NORMAL",
            "kids": [{"sex": "F", "status": "STILLBORN"}],
        },
    )
    assert kidding.status_code == 201, kidding.text
    # The authorized public history correction changes the cohort without
    # rewriting the authoritative kidding date. Native backdated service
    # still retains its independent biological waiting-period guard.
    corrected = await client.post(
        f"/api/animals/{doe_id}/move",
        headers=owner,
        json={
            "to_bucket": "FOUNDATION",
            "history_override": True,
            "reason": "Owner correction of the archived recovery cohort",
        },
    )
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["current_bucket"] == "FOUNDATION"
    service_date = birth_date + timedelta(days=postpartum_days)
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, int(owner["X-Farm-Id"]))
        doe = await db.get(Animal, doe_id)
        buck = await db.get(Animal, buck_id)
        assert farm is not None and doe is not None and buck is not None
        weights = await breeding_weights_as_of(db, [doe_id, buck_id], service_date)
        assert weights[doe_id] == 26.0
        if not accepted:
            with pytest.raises(ValueError, match="voluntary waiting period"):
                await create_breeding_record(
                    db,
                    farm,
                    doe,
                    buck,
                    service_date,
                    created_by_id=farm.owner_id,
                    doe_latest_weight_kg=weights[doe_id],
                    has_open_breeding=False,
                    actor_is_owner=True,
                )
            assert not db.new
            assert doe.current_bucket == "FOUNDATION"
        else:
            try:
                admitted = await create_breeding_record(
                    db,
                    farm,
                    doe,
                    buck,
                    service_date,
                    created_by_id=farm.owner_id,
                    doe_latest_weight_kg=weights[doe_id],
                    has_open_breeding=False,
                    actor_is_owner=True,
                )
            except ValueError as exc:
                pytest.fail(f"A service at the supported postpartum floor must succeed: {exc}")
            await db.commit()
            assert admitted.breeding_date == service_date
            assert admitted.doe_id == doe_id and admitted.buck_id == buck_id
            assert doe.current_bucket == "BREEDING"
