"""A committed hold can meet a genuinely cached earlier Animal observation."""

from datetime import date

import httpx
from sqlalchemy import Date, cast, event, func, select
from sqlalchemy.orm import ORMExecuteState, Session
from sqlalchemy.util.concurrency import await_only

from app.db import get_sessionmaker
from app.main import app
from app.models import Animal, Farm, WeightRecord
from app.services.health import place_movement_restriction

from .conftest import owner_with_farm
from .test_finance_extended import make_animal


async def test_native_dashboard_exposes_committed_clinical_reason_after_real_concurrent_hold(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="mutation-dashboard-concurrent-hold@farm.in")
    animal = await make_animal(client, owner, tag="DASH-CONCURRENT-HOLD")
    farm_id = int(owner["X-Farm-Id"])
    animal_id = int(animal["id"])
    async with get_sessionmaker()() as db:
        day: date = (
            await db.execute(
                select(cast(func.timezone(Farm.timezone, func.now()), Date)).where(
                    Farm.id == farm_id
                )
            )
        ).scalar_one()
        db.add(WeightRecord(farm_id=farm_id, animal_id=animal_id, date=day, weight_kg=17.5))
        await db.commit()

    committed = False
    cached_reason: str | None = "unobserved"
    cached_restricted: bool | None = None

    async def place_actual_hold() -> None:
        async with get_sessionmaker()() as writer:
            actual = (
                await writer.execute(select(Animal).where(Animal.id == animal_id))
            ).scalar_one()
            action = place_movement_restriction(
                writer,
                actual,
                disease_target="PPR",
                restriction_reason="Concurrent clinical hold",
                action_reference="DASH-CONCURRENT-NATIVE",
                acted_by_id=None,
            )
            await writer.commit()
            assert actual.movement_restricted is True
            assert actual.restriction_reason == "Concurrent clinical hold"
            assert actual.suspected_disease == "PPR"
            assert action.id > 0 and action.action == "PLACED"

    def before_held_query(state: ORMExecuteState) -> None:
        nonlocal committed, cached_reason, cached_restricted
        # This hook only schedules a real second transaction before the real
        # held SELECT. It never replaces statements, rows or application code.
        if committed or "animals.movement_restricted IS true" not in str(state.statement):
            return
        cached = next(
            row
            for row in state.session.identity_map.values()
            if isinstance(row, Animal) and row.id == animal_id
        )
        cached_reason = cached.restriction_reason
        cached_restricted = cached.movement_restricted
        assert cached_reason is None and cached_restricted is False
        await_only(place_actual_hold())
        committed = True

    event.listen(Session, "do_orm_execute", before_held_query)
    try:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as reader:
            response = await reader.get("/api/dashboard", headers=owner)
    finally:
        event.remove(Session, "do_orm_execute", before_held_query)

    assert committed is True and cached_reason is None and cached_restricted is False
    assert response.status_code == 200, response.text
    document = response.json()
    assert document["restricted_animals_total"] == 1
    assert len(document["restricted_animals"]) == 1
    held = document["restricted_animals"][0]
    assert held["animal"]["id"] == animal_id
    assert held["reason"] == "Concurrent clinical hold"
    assert document["recent_weights_total"] == 1
    assert document["recent_weights"][0]["animal"]["id"] == animal_id
