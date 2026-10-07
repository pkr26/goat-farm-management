"""Documented cadence scan budgets and reminder identity conserve actual native cohorts."""

from datetime import date, datetime

import httpx
from sqlalchemy import select, update

from app.db import get_sessionmaker
from app.models import Animal, Farm, FeedInventory, Task
from app.seed import seed_new_farm
from app.services.cadence import _ensure_buck_rotations, _ensure_feed_reorders
from app.services.tasks import lock_manual_task_queue

from .conftest import owner_with_farm


async def _initial_restored_cadence_farm(
    client: httpx.AsyncClient, label: str, introduction: date = date(2025, 1, 2)
) -> tuple[int, int]:
    headers = await owner_with_farm(client, email=f"cadence-bounds-{label}@example.test")
    async with get_sessionmaker()() as db:
        bootstrap = await db.get(Farm, int(headers["X-Farm-Id"]))
        assert bootstrap is not None
        # Initially restored tenant/stock facts predate these genuine native
        # historical calendar references. No contemporary date is rewritten.
        farm = Farm(
            owner_id=bootstrap.owner_id,
            name=f"Restored calendar {label}",
            timezone="Asia/Kolkata",
            created_at=datetime(2025, 1, 1),
        )
        db.add(farm)
        await db.flush()
        await seed_new_farm(db, farm)
        animal = Animal(
            farm_id=farm.id,
            tag_number=f"CALENDAR-{label}",
            sex="F",
            source="PURCHASED",
            current_bucket="FOUNDATION",
            status="ACTIVE",
            estimated_dob=date(2023, 1, 1),
            purchase_date=introduction,
            created_at=datetime.combine(introduction, datetime.min.time()),
        )
        db.add(animal)
        await db.commit()
        return farm.id, animal.id


async def _disable_unreferenced_default_reorder_policy(farm_id: int) -> None:
    async with get_sessionmaker()() as db:
        # Only the freshly seeded reminder configuration changes. Actual
        # zero-stock balances and all ledger/clinical provenance stay intact.
        await db.execute(
            update(FeedInventory).where(FeedInventory.farm_id == farm_id).values(reorder_level=None)
        )
        await db.commit()


async def test_feed_reorder_scan_conserves_the_documented_first_hundred_ingredient_duties(
    client: httpx.AsyncClient,
) -> None:
    farm_id, _ = await _initial_restored_cadence_farm(client, "ingredient-budget")
    await _disable_unreferenced_default_reorder_policy(farm_id)
    names = [f"Restored ingredient {index:03d}" for index in range(101)]
    async with get_sessionmaker()() as db:
        stocks = [
            FeedInventory(
                farm_id=farm_id,
                ingredient=name,
                category="CONCENTRATE",
                qty_on_hand=0,
                reorder_level=5,
            )
            for name in names
        ]
        db.add_all(stocks)
        await db.commit()
        assert [stock.ingredient for stock in sorted(stocks, key=lambda stock: stock.id)] == names
    async with get_sessionmaker()() as caller:
        farm = await caller.get(Farm, farm_id)
        assert farm is not None
        await lock_manual_task_queue(caller, farm)
        created = await _ensure_feed_reorders(caller, farm_id, date(2026, 9, 14))
        if created:
            await caller.commit()
        else:
            await caller.rollback()
    async with get_sessionmaker()() as observer:
        duties = list(
            (
                await observer.execute(
                    select(Task).where(Task.farm_id == farm_id).order_by(Task.id)
                )
            ).scalars()
        )
        assert len(duties) == 100, (
            "The bounded sweep must retain exactly its first100 actual ingredient reminders"
        )
        assert [duty.title_args["ingredient"] for duty in duties] == names[:100]
        assert all(duty.status == "PENDING" and duty.title_key == "feed_reorder" for duty in duties)
        inventories = list(
            (
                await observer.execute(
                    select(FeedInventory)
                    .where(FeedInventory.farm_id == farm_id, FeedInventory.ingredient.in_(names))
                    .order_by(FeedInventory.id)
                )
            ).scalars()
        )
        assert [stock.ingredient for stock in inventories] == names
        assert all(stock.qty_on_hand == 0 and stock.reorder_level == 5 for stock in inventories)


async def test_reorder_threshold_is_strict_and_existing_identity_does_not_starve_later_stock(
    client: httpx.AsyncClient,
) -> None:
    farm_id, _ = await _initial_restored_cadence_farm(client, "ingredient-identity")
    await _disable_unreferenced_default_reorder_policy(farm_id)
    async with get_sessionmaker()() as db:
        db.add_all(
            [
                FeedInventory(
                    farm_id=farm_id,
                    ingredient="First actual low stock",
                    category="CONCENTRATE",
                    qty_on_hand=9,
                    reorder_level=10,
                ),
                FeedInventory(
                    farm_id=farm_id,
                    ingredient="At configured level",
                    category="CONCENTRATE",
                    qty_on_hand=10,
                    reorder_level=10,
                ),
                FeedInventory(
                    farm_id=farm_id,
                    ingredient="Above configured level",
                    category="CONCENTRATE",
                    qty_on_hand=11,
                    reorder_level=10,
                ),
            ]
        )
        await db.commit()
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        await lock_manual_task_queue(db, farm)
        assert await _ensure_feed_reorders(db, farm_id, date(2026, 9, 14))
        await db.commit()
    async with get_sessionmaker()() as db:
        initial = list((await db.execute(select(Task).where(Task.farm_id == farm_id))).scalars())
        assert len(initial) == 1 and initial[0].title_args["ingredient"] == "First actual low stock"
        preserved_id = initial[0].id
        db.add(
            FeedInventory(
                farm_id=farm_id,
                ingredient="Later actual low stock",
                category="CONCENTRATE",
                qty_on_hand=8,
                reorder_level=10,
            )
        )
        await db.commit()
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        await lock_manual_task_queue(db, farm)
        await _ensure_feed_reorders(db, farm_id, date(2026, 9, 14))
        await db.commit()
    async with get_sessionmaker()() as db:
        actual = list(
            (
                await db.execute(select(Task).where(Task.farm_id == farm_id).order_by(Task.id))
            ).scalars()
        )
        assert len(actual) == 2
        assert [(task.id, task.title_args["ingredient"]) for task in actual] == [
            (preserved_id, "First actual low stock"),
            (actual[1].id, "Later actual low stock"),
        ]
        assert all(task.status == "PENDING" and task.title_key == "feed_reorder" for task in actual)


async def test_buck_rotation_scan_conserves_its_documented_first_five_hundred_actual_bucks(
    client: httpx.AsyncClient,
) -> None:
    farm_id, _ = await _initial_restored_cadence_farm(client, "buck-budget")
    async with get_sessionmaker()() as db:
        bucks = [
            Animal(
                farm_id=farm_id,
                tag_number=f"RESTORED-BUCK-{index:03d}",
                sex="M",
                source="PURCHASED",
                current_bucket="FOUNDATION",
                status="ACTIVE",
                estimated_dob=date(2020, 1, 1),
                purchase_date=date(2025, 1, 2),
                created_at=datetime(2025, 1, 2),
            )
            for index in range(501)
        ]
        db.add_all(bucks)
        await db.commit()
        ids = [buck.id for buck in bucks]
        assert ids == sorted(ids)
    async with get_sessionmaker()() as caller:
        farm = await caller.get(Farm, farm_id)
        assert farm is not None
        await lock_manual_task_queue(caller, farm)
        created = await _ensure_buck_rotations(caller, farm_id, date(2026, 9, 14))
        if created:
            await caller.commit()
        else:
            await caller.rollback()
    async with get_sessionmaker()() as observer:
        duties = list(
            (
                await observer.execute(
                    select(Task).where(Task.farm_id == farm_id).order_by(Task.id)
                )
            ).scalars()
        )
        assert len(duties) == 500, (
            "The bounded rotation sweep must retain exactly its first500 eligible bucks"
        )
        assert [duty.animal_id for duty in duties] == ids[:500]
        assert all(
            duty.status == "PENDING" and duty.title_key == "buck_rotation" for duty in duties
        )
        actual = list(
            (
                await observer.execute(select(Animal).where(Animal.id.in_(ids)).order_by(Animal.id))
            ).scalars()
        )
        assert [buck.id for buck in actual] == ids
        assert all(
            buck.status == "ACTIVE" and buck.estimated_dob == date(2020, 1, 1) for buck in actual
        )
