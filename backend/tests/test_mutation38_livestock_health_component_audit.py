"""Single-component health duties and explicit placement instants remain factual."""

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import Animal, Farm, MovementRestrictionAction
from app.services.health import (
    canonical_target_for_task,
    place_movement_restriction,
    target_matches_task,
)
from app.utils import utcnow

from .conftest import owner_with_farm
from .test_health_extended import make_animal


@pytest.mark.parametrize(
    "title,expected,matching,other",
    [
        ("HS pre-monsoon round (2026) — all animals", "Haemorrhagic Septicaemia (HS)", "HS", "ET"),
    ],
)
def test_single_component_duty_never_claims_or_accepts_the_other_vaccine(
    title: str,
    expected: str,
    matching: str,
    other: str,
) -> None:
    assert canonical_target_for_task(title, "VACCINE") == expected
    assert target_matches_task(matching, title, "VACCINE")
    assert not target_matches_task(other, title, "VACCINE")
    assert (
        canonical_target_for_task("ET + HS pre-monsoon round (2026)", "VACCINE")
        == "Enterotoxaemia (ET) + Haemorrhagic Septicaemia (HS)"
    )


async def test_native_restriction_placement_preserves_the_supplied_audit_instant(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    created = await make_animal(client, owner, "EXPLICIT-PLACEMENT-INSTANT")
    acted_at = utcnow() - timedelta(minutes=5)
    async with get_sessionmaker()() as db:
        animal = await db.get(Animal, created["id"])
        assert animal is not None
        farm = await db.get(Farm, animal.farm_id)
        assert farm is not None
        action = place_movement_restriction(
            db,
            animal,
            disease_target="FMD",
            restriction_reason="Veterinary movement hold",
            action_reference="Veterinary instruction HEALTH-42",
            acted_by_id=farm.owner_id,
            acted_at=acted_at,
        )
        assert action.acted_at == acted_at
        await db.commit()
    async with get_sessionmaker()() as db:
        stored = (await db.execute(select(MovementRestrictionAction))).scalar_one()
        animal = await db.get(Animal, created["id"])
        assert stored.acted_at == acted_at
        assert stored.action_reference == "Veterinary instruction HEALTH-42"
        assert stored.action == "PLACED" and stored.disease_target == "FMD"
        assert animal is not None and animal.movement_restricted
        assert stored.restriction_version == animal.restriction_version == 1
