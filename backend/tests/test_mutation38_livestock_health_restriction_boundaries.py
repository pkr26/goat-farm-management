"""Restriction reads and clearance preserve identity and retained factual provenance."""

import httpx
import pytest
from sqlalchemy import select, text

from app.db import get_sessionmaker
from app.models import Animal, HealthEvent, MovementRestrictionAction
from app.utils import today

from .conftest import owner_with_farm
from .test_health_extended import make_animal


async def test_restriction_reads_and_clear_accept_the_public_int4_ceiling(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    async with get_sessionmaker()() as db:
        await db.execute(text("SELECT setval('animals_id_seq', 2147483647, false)"))
        await db.commit()
    animal = await make_animal(client, owner, "MAX-RESTRICTION-IDENTITY")
    assert animal["id"] == 2_147_483_647
    event = await client.post(
        "/api/health/events",
        headers=owner,
        json={
            "animal_id": animal["id"],
            "type": "TREATMENT",
            "disease_target": "FMD",
            "suspected_scheduled_disease": True,
        },
    )
    assert event.status_code == 201, event.text
    history = await client.get(f"/api/health/restrictions/{animal['id']}", headers=owner)
    assert history.status_code == 200, history.text
    assert history.json()["active"] is True and history.json()["total"] == 1
    cleared = await client.post(
        f"/api/health/restrictions/{animal['id']}/clear",
        headers=owner,
        json={"expected_restriction_version": 1, "clearance_reference": "Authority MAX-1"},
    )
    assert cleared.status_code == 204, cleared.text
    final = await client.get(f"/api/health/restrictions/{animal['id']}", headers=owner)
    assert final.status_code == 200, final.text
    assert final.json()["active"] is False and final.json()["total"] == 2


async def test_restriction_paths_reserve_zero_even_when_legacy_storage_has_that_identity(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    async with get_sessionmaker()() as db:
        db.add(
            Animal(
                id=0,
                farm_id=int(owner["X-Farm-Id"]),
                tag_number="ZERO-RESTRICTION-LEGACY",
                sex="F",
                source="PURCHASED",
                current_bucket="FOUNDATION",
            )
        )
        await db.commit()
    register = await client.get("/api/animals", headers=owner)
    assert register.status_code == 200, register.text
    assert register.json()["animals"][0]["id"] == 0
    history = await client.get("/api/health/restrictions/0", headers=owner)
    assert history.status_code == 404, history.text
    cleared = await client.post(
        "/api/health/restrictions/0/clear",
        headers=owner,
        json={"expected_restriction_version": 1, "clearance_reference": "Authority ZERO-1"},
    )
    assert cleared.status_code == 404, cleared.text
    assert history.json()["detail"] == cleared.json()["detail"] == "Animal not found"


@pytest.mark.parametrize("foreign", [False, True])
async def test_unknown_or_other_farm_restriction_identity_is_404_for_read_and_clear(
    client: httpx.AsyncClient,
    foreign: bool,
) -> None:
    owner = await owner_with_farm(client)
    animal_id = 12345
    if foreign:
        other = await owner_with_farm(client, "foreign-restrictions@example.test")
        animal_id = int((await make_animal(client, other, "OTHER-FARM-HOLD"))["id"])
    history = await client.get(f"/api/health/restrictions/{animal_id}", headers=owner)
    assert history.status_code == 404, history.text
    clear = await client.post(
        f"/api/health/restrictions/{animal_id}/clear",
        headers=owner,
        json={"expected_restriction_version": 1, "clearance_reference": "Authority ID-404"},
    )
    assert clear.status_code == 404, clear.text
    assert history.json()["detail"] == clear.json()["detail"] == "Animal not found"


async def test_retained_general_hold_is_active_and_can_be_referenced_cleared(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, "GENERAL-RESTRICTION")
    async with get_sessionmaker()() as db:
        row = await db.get(Animal, animal["id"])
        assert row is not None
        # Exact general-hold shape admitted and backfilled by revision d3f4:
        # this operational hold does not invent statutory disease suspicion.
        row.movement_restricted = True
        row.restriction_reason = "Regulatory movement hold"
        row.restriction_version = 1
        assert not row.suspected_scheduled_disease
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
    history = await client.get(f"/api/health/restrictions/{animal['id']}", headers=owner)
    assert history.status_code == 200, history.text
    assert history.json()["active"] is True and history.json()["restriction_version"] == 1
    cleared = await client.post(
        f"/api/health/restrictions/{animal['id']}/clear",
        headers=owner,
        json={"expected_restriction_version": 1, "clearance_reference": "Authority HOLD-1"},
    )
    assert cleared.status_code == 204, cleared.text
    async with get_sessionmaker()() as db:
        row = await db.get(Animal, animal["id"])
        assert row is not None and not row.movement_restricted
        actions = list((await db.execute(select(MovementRestrictionAction))).scalars())
        assert {action.action for action in actions} == {"PLACED", "CLEARED"}


async def test_clear_keeps_a_retained_late_notification_absent_from_the_immutable_event(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, "RETAINED-LATE-NOTIFICATION")
    recorded = await client.post(
        "/api/health/events",
        headers=owner,
        json={
            "animal_id": animal["id"],
            "type": "TREATMENT",
            "disease_target": "FMD",
            "suspected_scheduled_disease": True,
        },
    )
    assert recorded.status_code == 201, recorded.text
    event_id = recorded.json()[0]["id"]
    notified_on = today()
    async with get_sessionmaker()() as db:
        row = await db.get(Animal, animal["id"])
        event = await db.get(HealthEvent, event_id)
        placed = (await db.execute(select(MovementRestrictionAction))).scalar_one()
        assert row is not None and event is not None
        assert event.authority_notified_at is None and row.authority_notified_at is None
        assert placed.health_event_id == event_id and placed.restriction_version == 1
        original_event = {
            column.key: getattr(event, column.key) for column in HealthEvent.__table__.columns
        }
        original_placement = {
            column.key: getattr(placed, column.key)
            for column in MovementRestrictionAction.__table__.columns
        }
        # Retained/restored administrative notification provenance: the
        # immutable event truthfully preserves the date unknown at entry;
        # the animal stores the separately known later factual notification.
        # There is no current HTTP late-notification setter claimed here.
        row.authority_notified_at = notified_on
        await db.commit()
    clear = await client.post(
        f"/api/health/restrictions/{animal['id']}/clear",
        headers=owner,
        json={"expected_restriction_version": 1, "clearance_reference": "Authority LATE-1"},
    )
    assert clear.status_code == 204, clear.text
    async with get_sessionmaker()() as db:
        row = await db.get(Animal, animal["id"])
        event = await db.get(HealthEvent, event_id)
        retained_placement = await db.get(MovementRestrictionAction, original_placement["id"])
        assert row is not None and not row.movement_restricted
        assert row.authority_notified_at == notified_on
        assert (
            event is not None
            and {column.key: getattr(event, column.key) for column in HealthEvent.__table__.columns}
            == original_event
        )
        assert (
            retained_placement is not None
            and {
                column.key: getattr(retained_placement, column.key)
                for column in MovementRestrictionAction.__table__.columns
            }
            == original_placement
        )
