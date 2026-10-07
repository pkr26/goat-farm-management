"""A stale native identity-map entry must not starve later genuine work."""

from datetime import date

import httpx
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import Animal, Farm, FeedInventory, Task
from app.services.cadence import _ensure_buck_rotations, _ensure_feed_reorders
from app.services.tasks import lock_manual_task_queue

from .conftest import owner_with_farm


async def test_cached_unconfigured_ingredient_does_not_starve_the_next_real_reorder(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client, email="cadence-cached-stock@example.test")
    farm_id = int(headers["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        inventory = list(
            (
                await db.execute(
                    select(FeedInventory)
                    .where(FeedInventory.farm_id == farm_id)
                    .order_by(FeedInventory.id)
                )
            ).scalars()
        )
        assert len(inventory) >= 2 and all(item.qty_on_hand == 0 for item in inventory)
        for item in inventory:
            item.reorder_level = None
        first_id, next_id = inventory[0].id, inventory[1].id
        next_ingredient = inventory[1].ingredient
        await db.commit()
    async with get_sessionmaker()() as caller:
        cached = await caller.get(FeedInventory, first_id)
        assert cached is not None and cached.reorder_level is None
        async with get_sessionmaker()() as configuration_writer:
            for row_id in (first_id, next_id):
                configured = await configuration_writer.get(FeedInventory, row_id)
                assert configured is not None
                configured.reorder_level = 5
            await configuration_writer.commit()
        assert cached.reorder_level is None
        farm = await caller.get(Farm, farm_id)
        assert farm is not None
        await lock_manual_task_queue(caller, farm)
        await _ensure_feed_reorders(caller, farm_id, date(2026, 11, 2))
        await caller.commit()
    async with get_sessionmaker()() as observer:
        duties = list(
            (await observer.execute(select(Task).where(Task.farm_id == farm_id))).scalars()
        )
        assert [task.title_args["ingredient"] for task in duties] == [next_ingredient], (
            "Skipping one stale cached configuration must not terminate the real eligible scan"
        )
        assert all(task.title_key == "feed_reorder" and task.status == "PENDING" for task in duties)
        for row_id in (first_id, next_id):
            persisted = await observer.get(FeedInventory, row_id)
            assert (
                persisted is not None
                and persisted.reorder_level == 5
                and persisted.qty_on_hand == 0
            )


async def test_cached_unknown_buck_age_does_not_starve_the_next_real_rotation(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client, email="cadence-cached-buck@example.test")
    farm_id = int(headers["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        initially_unknown = Animal(
            farm_id=farm_id,
            tag_number="CACHED-UNKNOWN-BUCK",
            sex="M",
            source="PURCHASED",
            current_bucket="FOUNDATION",
            status="ACTIVE",
            purchase_date=date(2026, 1, 1),
        )
        known = Animal(
            farm_id=farm_id,
            tag_number="NEXT-AGED-BUCK",
            sex="M",
            source="PURCHASED",
            current_bucket="FOUNDATION",
            status="ACTIVE",
            estimated_dob=date(2021, 1, 1),
            purchase_date=date(2026, 1, 1),
        )
        db.add_all([initially_unknown, known])
        await db.commit()
        cached_id, known_id = initially_unknown.id, known.id
    async with get_sessionmaker()() as caller:
        cached = await caller.get(Animal, cached_id)
        assert cached is not None and cached.estimated_dob is None and cached.date_of_birth is None
        async with get_sessionmaker()() as provenance_writer:
            amended = await provenance_writer.get(Animal, cached_id)
            assert amended is not None
            # Add the missing age estimate in a real native write. No known
            # age, status or clinical event is removed or retroactively erased.
            amended.estimated_dob = date(2020, 1, 1)
            await provenance_writer.commit()
        assert cached.estimated_dob is None
        farm = await caller.get(Farm, farm_id)
        assert farm is not None
        await lock_manual_task_queue(caller, farm)
        await _ensure_buck_rotations(caller, farm_id, date(2026, 11, 2))
        await caller.commit()
    async with get_sessionmaker()() as observer:
        duties = list(
            (await observer.execute(select(Task).where(Task.farm_id == farm_id))).scalars()
        )
        assert [(task.animal_id, task.title_key, task.status) for task in duties] == [
            (known_id, "buck_rotation", "PENDING")
        ], "One genuinely stale unknown-age identity must not starve the later eligible buck"
        amended = await observer.get(Animal, cached_id)
        retained = await observer.get(Animal, known_id)
        assert amended is not None and amended.estimated_dob == date(2020, 1, 1)
        assert retained is not None and retained.estimated_dob == date(2021, 1, 1)
        assert amended.status == retained.status == "ACTIVE"
