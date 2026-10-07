"""Terminal status honors general holds and returns retryable care-lock conflicts."""

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import Animal, KidEntry, MovementRestrictionAction
from app.utils import today

from .conftest import owner_with_farm
from .test_animals_status_husbandry import change_status, make_animal
from .test_kidding_husbandry import confirmed_pregnancy, post_kidding


@pytest.mark.parametrize("terminal_status", ["SOLD", "CULLED"])
async def test_public_sale_and_cull_respect_a_restored_general_regulatory_hold_without_suspicion(
    client: httpx.AsyncClient, terminal_status: str
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, "GENERAL-REGULATORY-HOLD")
    async with get_sessionmaker()() as db:
        row = (
            await db.execute(select(Animal).where(Animal.id == animal["id"]).with_for_update())
        ).scalar_one()
        # Exact valid general-hold shape retained by migration d3f4a5b6c7d8:
        # it backfills PLACED using the reason when no suspected disease is
        # named. It is an operational regulatory hold, not a fake diagnosis.
        row.movement_restricted = True
        row.restriction_reason = "Regulatory movement hold"
        row.restriction_version = 1
        assert row.suspected_scheduled_disease is False
        db.add(
            MovementRestrictionAction(
                farm_id=row.farm_id,
                animal_id=row.id,
                restriction_version=1,
                action="PLACED",
                acted_at=row.created_at,
                acted_by_id=None,
                action_reference="Legacy restriction placement backfill",
                disease_target="Regulatory movement hold",
                health_event_id=None,
            )
        )
        await db.commit()
    response = await change_status(
        client, owner, int(animal["id"]), terminal_status, sale_price=5000
    )
    assert response.status_code == 409, response.text
    assert "active movement restriction" in response.json()["detail"]
    async with get_sessionmaker()() as db:
        held = await db.get(Animal, animal["id"])
        assert held is not None and held.status == "ACTIVE" and held.sale_price is None
        assert held.movement_restricted and not held.suspected_scheduled_disease
    # Clearance is the real supported remedy; a plain general hold must be
    # reversible with an attributed reference before the terminal operation.
    clearance = await client.post(
        f"/api/health/restrictions/{animal['id']}/clear",
        headers=owner,
        json={"expected_restriction_version": 1, "clearance_reference": "Authority order HOLD-17"},
    )
    assert clearance.status_code == 204, clearance.text
    permitted = await change_status(
        client, owner, int(animal["id"]), terminal_status, sale_price=5000
    )
    assert permitted.status_code == 200, permitted.text


async def test_public_dependent_kid_death_returns_409_and_rolls_back_while_dam_is_pinned(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    doe, _buck, breeding = await confirmed_pregnancy(
        client, owner, tag="DEPENDENT-DEATH-CONFLICT", bred_days_ago=170, kid_count=1
    )
    born = await post_kidding(
        client,
        owner,
        int(breeding["id"]),
        (today() - timedelta(days=20)).isoformat(),
        kids=[{"sex": "F"}],
    )
    assert born.status_code == 201, born.text
    child_id = int(born.json()["kids"][0]["animal_id"])
    entry_id = int(born.json()["kids"][0]["id"])
    async with get_sessionmaker()() as dam_owner:
        await dam_owner.execute(select(Animal.id).where(Animal.id == doe["id"]).with_for_update())
        # This is the actual Animal mutex used by parent lifecycle writers.
        # Kid death already owns its child; it must translate PostgreSQL's
        # real55P03 from the opposite dam pin into a retryable HTTP409.
        response = await change_status(client, owner, child_id, "DEAD", date=today().isoformat())
        assert response.status_code == 409, response.text
        assert (
            response.json()["detail"]
            == "The dam's lifecycle is changing; retry the kid death update"
        )
        async with get_sessionmaker()() as observer:
            child = await observer.get(Animal, child_id)
            entry = await observer.get(KidEntry, entry_id)
            assert child is not None and entry is not None
            assert child.status == "ACTIVE" and child.status_date is None
            assert entry.status == "ALIVE" and entry.mortality_reported_at is None
        await dam_owner.rollback()
    retry = await change_status(client, owner, child_id, "DEAD", date=today().isoformat())
    assert retry.status_code == 200, retry.text
    async with get_sessionmaker()() as observer:
        child = await observer.get(Animal, child_id)
        entry = await observer.get(KidEntry, entry_id)
        assert child is not None and entry is not None
        assert child.status == "DEAD" and child.status_date == today()
        assert entry.status == "DIED" and entry.mortality_reported_at == today()
