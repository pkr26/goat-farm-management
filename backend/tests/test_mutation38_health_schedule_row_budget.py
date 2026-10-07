"""A linked clinical programme retains primary/newest dates within its bounded row work."""

from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import event, select
from sqlalchemy.sql.selectable import CompoundSelect

from app.db import get_engine, get_sessionmaker
from app.models import Animal, HealthEvent
from app.services.health import vaccination_schedule_for_animal
from app.utils import today

from .conftest import owner_with_farm
from .test_health_extended import make_animal, record_event


async def test_four_actual_linked_doses_preserve_anchors_with_at_most_three_probe_rows(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client, email="schedule-bounded-linked-rows@farm.in")
    animal_data = await make_animal(
        client,
        headers,
        tag="FOUR-ACTUAL-FMD-DOSES",
        date_of_birth=(today() - timedelta(days=900)).isoformat(),
    )
    dates = [today() - timedelta(days=ago) for ago in (400, 375, 195, 15)]
    recorded_ids = []
    for administered in dates:
        events = await record_event(
            client,
            headers,
            animal_id=animal_data["id"],
            type="VACCINE",
            product_name="FMD vaccine",
            date=administered.isoformat(),
        )
        assert len(events) == 1
        recorded_ids.append(events[0]["id"])
    async with get_sessionmaker()() as db:
        animal = await db.get(Animal, animal_data["id"])
        assert animal is not None
        actual = list(
            (
                await db.execute(
                    select(HealthEvent)
                    .where(HealthEvent.animal_id == animal.id)
                    .order_by(HealthEvent.date, HealthEvent.id)
                )
            ).scalars()
        )
        assert [fact.id for fact in actual] == recorded_ids
        assert [fact.date for fact in actual] == dates
        assert len({fact.schedule_template_id for fact in actual}) == 1
        assert all(fact.schedule_template_id is not None for fact in actual)
        consumed_probe_rows: list[int] = []

        def observe_rows(
            connection: Any,
            cursor: Any,
            statement: str,
            parameters: Any,
            context: Any,
            executemany: bool,
        ) -> None:
            compiled = context.compiled
            if compiled is None:
                return
            query = compiled.statement
            if isinstance(query, CompoundSelect) and {
                "template_id",
                "event_id",
                "event_date",
                "is_primary",
            }.issubset(query.selected_columns.keys()):
                # Asyncpg's actual buffered SELECT cursor reports the number
                # of returned rows. Observe it without consuming, replacing,
                # reordering or manufacturing any database result.
                consumed_probe_rows.append(int(cursor.rowcount))

        engine = get_engine().sync_engine
        event.listen(engine, "after_cursor_execute", observe_rows)
        try:
            rows = await vaccination_schedule_for_animal(db, animal)
        finally:
            event.remove(engine, "after_cursor_execute", observe_rows)
        fmd = [row for row in rows if row["template"].name == "FMD"]
        assert len(fmd) == 1
        assert fmd[0]["last_done"] == dates[-1]
        assert fmd[0]["booster_due"] == dates[0] + timedelta(days=25)
        assert consumed_probe_rows and all(count >= 0 for count in consumed_probe_rows)
        # Only this one programme has any recorded facts, so the documented
        # newest-two plus primary-one work bound is at most three actual
        # returned probe rows, regardless of its longer clinical history.
        assert sum(consumed_probe_rows) <= 3, consumed_probe_rows
