"""Mutation-campaign gap tests for chronology probe scoping (2026-09-30).

The surviving mutants flipped `==` to `!=` on the farm/animal predicates of
``require_purchase_before_recorded_facts`` and
``require_status_after_recorded_facts``. The existing farm-scoped test only
greps the emitted SQL for a `farm_id` token — which a `!=` predicate also
satisfies — so these tests pin the BEHAVIOUR on multi-farm and
sibling-animal data instead.

Relative-date design (make_doe anchors the target's own earliest fact — her
weight at acquisition — at today-800d):
  * foreign/sibling rows carry BOTH an ancient event (today-900d) and a
    recent one (today-10d);
  * a purchase date of today-810d is before her own earliest fact (accepted
    by correct code) but after the ancient foreign/sibling event — a
    wrong-scope min() boundary would reject it;
  * a status date of today-795d is after her own latest fact (accepted by
    correct code) but before the recent foreign/sibling event — a wrong-
    scope max() boundary would reject it.
"""

from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_sessionmaker
from app.models import Animal, HealthEvent
from app.services.chronology import (
    require_purchase_before_recorded_facts,
    require_status_after_recorded_facts,
)
from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_extended import make_doe


async def _animal(db: AsyncSession, farm_id: int, tag: str) -> Animal:
    return (
        await db.execute(select(Animal).where(Animal.farm_id == farm_id, Animal.tag_number == tag))
    ).scalar_one()


async def _seed_scoped_events(
    client: httpx.AsyncClient,
    owner: dict[str, Any],
    *,
    foreign_owner: dict[str, Any] | None,
    target_tag: str,
    sibling_tag: str | None,
    foreign_tag: str | None,
) -> tuple[int, dict[str, Any]]:
    """Target doe via API; sibling/foreign does via API; their ancient +
    recent health events inserted directly (the API's own chronology guard
    would refuse the ancient ones)."""
    target = await make_doe(client, owner, target_tag)
    sibling = await make_doe(client, owner, sibling_tag) if sibling_tag else None
    foreign_res: dict[str, Any] | None = None
    if foreign_owner is not None and foreign_tag is not None:
        foreign_res = await make_doe(client, foreign_owner, foreign_tag)
    farm_id = int(owner["X-Farm-Id"])

    ancient = today() - timedelta(days=900)
    recent = today() - timedelta(days=10)
    async with get_sessionmaker()() as db:
        rows: list[HealthEvent] = []
        if sibling is not None and sibling_tag is not None:
            sib = await _animal(db, farm_id, sibling_tag)
            rows += [
                HealthEvent(farm_id=farm_id, animal_id=sib.id, date=ancient, type="TREATMENT"),
                HealthEvent(farm_id=farm_id, animal_id=sib.id, date=recent, type="TREATMENT"),
            ]
        if foreign_res is not None and foreign_tag is not None:
            foreign_farm = int(foreign_owner["X-Farm-Id"]) if foreign_owner else farm_id
            other = await _animal(db, foreign_farm, foreign_tag)
            rows += [
                HealthEvent(
                    farm_id=foreign_farm, animal_id=other.id, date=ancient, type="TREATMENT"
                ),
                HealthEvent(
                    farm_id=foreign_farm, animal_id=other.id, date=recent, type="TREATMENT"
                ),
            ]
        db.add_all(rows)
        await db.commit()
    return farm_id, {"target": target}


async def test_purchase_probe_ignores_other_farms_facts(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client, email="chrono-xfarm@farm.in", farm_name="Farm X")
    other = await owner_with_farm(client, email="chrono-yfarm@farm.in", farm_name="Farm Y")
    farm_id, _ctx = await _seed_scoped_events(
        client,
        owner,
        foreign_owner=other,
        target_tag="CHRONO-X",
        sibling_tag=None,
        foreign_tag="CHRONO-Y",
    )

    async with get_sessionmaker()() as db:
        animal = await _animal(db, farm_id, "CHRONO-X")
        # Before her own earliest fact (weight at acquisition, today-800d)…
        accepted = today() - timedelta(days=810)
        await require_purchase_before_recorded_facts(db, animal, accepted)
        # …but one day after her own earliest fact is still rejected.
        with pytest.raises(ValueError, match="earliest recorded"):
            await require_purchase_before_recorded_facts(
                db, animal, today() - timedelta(days=799)
            )


async def test_purchase_probe_ignores_sibling_animals_in_same_farm(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="chrono-sibp@farm.in")
    farm_id, _ctx = await _seed_scoped_events(
        client,
        owner,
        foreign_owner=None,
        target_tag="CHRONO-TP",
        sibling_tag="CHRONO-SP",
        foreign_tag=None,
    )

    async with get_sessionmaker()() as db:
        animal = await _animal(db, farm_id, "CHRONO-TP")
        await require_purchase_before_recorded_facts(db, animal, today() - timedelta(days=810))
        with pytest.raises(ValueError, match="earliest recorded"):
            await require_purchase_before_recorded_facts(db, animal, today() - timedelta(days=799))


async def test_status_probe_ignores_sibling_and_foreign_facts(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client, email="chrono-stats@farm.in", farm_name="Farm S")
    other = await owner_with_farm(client, email="chrono-stats2@farm.in", farm_name="Farm T")
    farm_id, _ctx = await _seed_scoped_events(
        client,
        owner,
        foreign_owner=other,
        target_tag="CHRONO-SX",
        sibling_tag="CHRONO-SY",
        foreign_tag="CHRONO-TZ",
    )

    async with get_sessionmaker()() as db:
        animal = await _animal(db, farm_id, "CHRONO-SX")
        # After her own latest fact (today-800d): fine — even though the
        # sibling's and the foreign farm's RECENT events (today-10d) are
        # later and would wrongly gate a mis-scoped max() probe.
        await require_status_after_recorded_facts(db, animal, today() - timedelta(days=795))
        with pytest.raises(ValueError, match="latest recorded"):
            await require_status_after_recorded_facts(db, animal, today() - timedelta(days=801))
