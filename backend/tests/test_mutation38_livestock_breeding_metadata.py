"""Native natural-cover normalization and public heat-watch localization."""

from datetime import timedelta

import httpx
import pytest
from sqlalchemy.exc import IntegrityError

from app.db import get_sessionmaker
from app.models import Animal, BreedingRecord, Farm
from app.services.breeding import breeding_weights_as_of, create_breeding_record
from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_extended import all_tasks, make_animal, make_breeding


async def _mature_pair(client: httpx.AsyncClient, owner: dict[str, str]) -> tuple[int, int]:
    reference_date = today()
    identities = []
    for sex, bucket, tag, weight in (
        ("F", "FOUNDATION", "META-DOE", 26.0),
        ("M", "BREEDING", "META-BUCK", 30.0),
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
    return identities[0], identities[1]


@pytest.mark.parametrize("unused_semen_name", [None, "  unused artificial-insemination sire  "])
async def test_native_natural_cover_discards_unused_semen_metadata(
    client: httpx.AsyncClient, unused_semen_name: str | None
) -> None:
    owner = await owner_with_farm(client)
    doe_id, buck_id = await _mature_pair(client, owner)
    service_date = today()
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, int(owner["X-Farm-Id"]))
        doe = await db.get(Animal, doe_id)
        buck = await db.get(Animal, buck_id)
        assert farm is not None and doe is not None and buck is not None
        weights = await breeding_weights_as_of(db, [doe_id, buck_id], service_date)
        # The exported native service normalizes unused optional metadata
        # before inserting a natural cover. Its HTTP schema separately
        # refuses this combination; this is the native service contract.
        try:
            record = await create_breeding_record(
                db,
                farm,
                doe,
                buck,
                service_date,
                created_by_id=farm.owner_id,
                doe_latest_weight_kg=weights[doe_id],
                has_open_breeding=False,
                method="NATURAL",
                semen_sire_name=unused_semen_name,
                actor_is_owner=True,
            )
            await db.commit()
        except IntegrityError as error:
            pytest.fail(f"Native natural-cover normalization broke the sire constraint: {error}")
        stored = await db.get(BreedingRecord, record.id)
        assert stored is not None
        assert stored.method == "NATURAL"
        assert stored.buck_id == buck_id
        assert stored.semen_sire_name is None


async def test_public_heat_watch_localization_uses_the_actual_future_due_date(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    doe_id, buck_id = await _mature_pair(client, owner)
    service_date = today() - timedelta(days=3)
    breeding = await make_breeding(
        client, owner, doe_id, buck_id, breeding_date=service_date.isoformat()
    )
    duties = await all_tasks(client, owner)
    watches = [
        duty
        for duty in duties
        if duty["breeding_record_id"] == breeding["id"] and duty["category"] == "HEAT_WATCH"
    ]
    assert len(watches) == 1
    watch = watches[0]
    expected_due = (service_date + timedelta(days=18)).isoformat()
    assert watch["due_date"] == expected_due
    assert watch["title_key"] == "return_to_heat_watch"
    assert watch["title_args"] == {"tag": "META-DOE", "due_date": expected_due}
