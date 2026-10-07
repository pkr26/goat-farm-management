"""Pregnancy loss retains native text bounds and the inclusive sanity window."""

from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import Animal, BreedingRecord, Farm
from app.services.breeding import mark_aborted
from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_extended import confirm, make_animal, make_breeding


async def _confirmed_service(
    client: httpx.AsyncClient, days_since_service: int
) -> tuple[dict[str, str], int, int, date]:
    owner = await owner_with_farm(client)
    observation_date = today()
    service_date = observation_date - timedelta(days=days_since_service)
    doe = await make_animal(
        client,
        owner,
        "LOSS-LIMIT-DOE",
        date_of_birth=(observation_date - timedelta(days=800)).isoformat(),
        weight_kg=26.0,
        weight_date=(service_date - timedelta(days=1)).isoformat(),
    )
    buck = await make_animal(
        client,
        owner,
        "LOSS-LIMIT-BUCK",
        sex="M",
        bucket="BREEDING",
        date_of_birth=(observation_date - timedelta(days=800)).isoformat(),
        weight_kg=30.0,
        weight_date=(service_date - timedelta(days=1)).isoformat(),
    )
    breeding = await make_breeding(
        client,
        owner,
        int(doe["id"]),
        int(buck["id"]),
        breeding_date=service_date.isoformat(),
    )
    await confirm(client, owner, int(breeding["id"]), kid_count=1)
    return owner, int(doe["id"]), int(breeding["id"]), observation_date


@pytest.mark.parametrize(("length", "accepted"), [(4000, True), (4001, False)])
async def test_native_pregnancy_loss_preserves_literal_four_thousand_character_notes_limit(
    client: httpx.AsyncClient, length: int, accepted: bool
) -> None:
    owner, doe_id, record_id, loss_date = await _confirmed_service(client, 40)
    notes = "N" * length
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, int(owner["X-Farm-Id"]))
        assert farm is not None
        # Respect the native caller's documented animal→breeding lock order.
        await db.execute(select(Animal.id).where(Animal.id == doe_id).with_for_update())
        record = (
            await db.execute(
                select(BreedingRecord).where(BreedingRecord.id == record_id).with_for_update()
            )
        ).scalar_one()
        rejected = False
        try:
            await mark_aborted(
                db,
                record,
                loss_date=loss_date,
                loss_cause="OTHER",
                loss_notes=notes,
                recorded_by_id=farm.owner_id,
            )
        except ValueError as error:
            assert "4000 characters" in str(error)
            rejected = True
        except Exception as error:
            pytest.fail(f"Native notes bounds must reject predictably, not fail storage: {error!r}")
        assert rejected is not accepted, f"Native loss notes length{length} changed admission"
        if accepted:
            assert record.outcome == "ABORTED"
            assert record.loss_notes == notes
            assert record.loss_recorded_by_id == farm.owner_id
            await db.commit()
        else:
            assert record.outcome == "CONFIRMED_PREGNANT"
            assert record.loss_notes is None
            assert not db.new
            await db.rollback()
    # HTTP DTOs already cap this field. The declared native helper accepts
    # arbitrary str inputs and must preserve the same domain rejection.


@pytest.mark.parametrize(
    ("days_since_service", "accepted"),
    [(199, True), (200, True), (201, False)],
    ids=["before-ceiling", "on-ceiling", "after-ceiling"],
)
async def test_public_pregnancy_loss_preserves_inclusive_two_hundred_day_sanity_ceiling(
    client: httpx.AsyncClient, days_since_service: int, accepted: bool
) -> None:
    owner, doe_id, record_id, loss_date = await _confirmed_service(client, days_since_service)
    response = await client.post(
        f"/api/breeding/{record_id}/abort",
        headers=owner,
        json={"loss_date": loss_date.isoformat(), "cause": "OTHER", "notes": "Observed loss"},
    )
    assert response.status_code == (200 if accepted else 422), response.text
    async with get_sessionmaker()() as db:
        record = await db.get(BreedingRecord, record_id)
        doe = await db.get(Animal, doe_id)
        assert record is not None and doe is not None
        assert record.outcome == ("ABORTED" if accepted else "CONFIRMED_PREGNANT")
        assert record.loss_date == (loss_date if accepted else None)
        assert doe.current_bucket == ("RESTING" if accepted else "PREGNANCY_EARLY")
