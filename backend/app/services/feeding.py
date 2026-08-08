"""Feeding."""

import math
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import (
    SHIFT_SPLIT,
    Animal,
    AnimalStatus,
    Bucket,
    BucketDefinition,
    BucketFeedSetting,
    Farm,
    FeedFinishedStock,
    FeedingRecord,
    FeedingShift,
    FeedInventory,
    FeedRecipe,
    Transaction,
    TransactionCategory,
    TransactionType,
)
from ..utils import DEFAULT_BUSINESS_TIMEZONE, money, today

DRY_ROUGHAGE = "DRY_ROUGHAGE_ONLY"
RECIPE_DISPLAY = {
    "FATTENING_50_50": "Fattening 50:50",
    "LACTATING_60_40": "Lactating 60:40",
    "MAINTENANCE_75_25": "Maintenance 75:25",
    "FLUSH_70_30": "Flush 70:30",
    "CREEP": "Creep feed",
    DRY_ROUGHAGE: "Dry roughage only (days 1–3, zero grain)",
}

# SPEC "Feed allocation per bucket" — reference table for the recipes page.
BUCKET_ALLOCATION_REFERENCE = [
    ("QUARANTINE", "Dry roughage only (days 1–3) → transition to MAINTENANCE_75_25"),
    ("FOUNDATION", "LACTATING_60_40"),
    ("BREEDING", "MAINTENANCE_75_25 (incl. dry bucks)"),
    ("PREGNANCY_EARLY", "MAINTENANCE_75_25"),
    ("PREGNANCY_LATE", "LACTATING_60_40"),
    ("DELIVERY", "LACTATING_60_40"),
    ("RECOVERY", "LACTATING_60_40 (lactating)"),
    ("RESTING", "Days 1–10 MAINTENANCE_75_25, days 10–30 FLUSH_70_30"),
    ("MALE_KIDS", "Day ≤90 LACTATING_60_40 (frame-builder), day 91+ FATTENING_50_50"),
    ("FEMALE_KIDS", "LACTATING_60_40"),
]

SHIFT_TIMES = {
    FeedingShift.MORNING.value: "6:30 AM (sweep bunks first)",
    FeedingShift.AFTERNOON.value: "1:30 PM",
    FeedingShift.NIGHT.value: "7:30 PM",
}


def recipe_for_animal(
    animal: Animal,
    ref: date | None = None,
    timezone_name: str = DEFAULT_BUSINESS_TIMEZONE,
) -> str:
    """Which TMR recipe applies to this animal today (SPEC allocation rules).

    Pure python (stays synchronous): reads bucket history, so the
    animal's bucket_moves must already be loaded — async sessions forbid
    implicit lazy loads (feeding_plan selectinloads them)."""
    ref = ref or today()
    bucket = animal.current_bucket
    if bucket == Bucket.QUARANTINE.value:
        return (
            DRY_ROUGHAGE
            if animal.days_in_current_bucket_on(ref, timezone_name) < 3
            else "MAINTENANCE_75_25"
        )
    if bucket in (
        Bucket.FOUNDATION.value,
        Bucket.FEMALE_KIDS.value,
        Bucket.PREGNANCY_LATE.value,
        Bucket.RECOVERY.value,
        Bucket.DELIVERY.value,
    ):
        return "LACTATING_60_40"
    if bucket in (Bucket.BREEDING.value, Bucket.PREGNANCY_EARLY.value):
        return "MAINTENANCE_75_25"
    if bucket == Bucket.RESTING.value:
        return (
            "FLUSH_70_30"
            if animal.days_in_current_bucket_on(ref, timezone_name) >= 10
            else "MAINTENANCE_75_25"
        )
    if bucket == Bucket.MALE_KIDS.value:
        dob = animal.effective_dob
        age_days = (ref - dob).days if dob else 999  # unknown age → fattening
        return "LACTATING_60_40" if age_days <= 90 else "FATTENING_50_50"
    return "MAINTENANCE_75_25"


async def get_daily_kg_per_head(db: AsyncSession, farm_id: int, bucket_code: str) -> float:
    override_result = await db.execute(
        select(BucketFeedSetting).where(
            BucketFeedSetting.farm_id == farm_id, BucketFeedSetting.bucket == bucket_code
        )
    )
    override = override_result.scalars().first()
    if override:
        return override.daily_kg_per_head
    definition_result = await db.execute(
        select(BucketDefinition).where(BucketDefinition.code == bucket_code)
    )
    definition = definition_result.scalars().first()
    return definition.daily_kg_per_head if definition else 1.2


async def set_daily_kg_per_head(
    db: AsyncSession, farm_id: int, bucket_code: str, kg: float
) -> None:
    # INSERT ... ON CONFLICT (not check-then-insert): two concurrent
    # first-time saves of the same bucket serialize inside the statement —
    # the loser updates the winner's row instead of crashing on
    # uq_feed_setting_per_bucket with an unhandled IntegrityError (500).
    stmt = pg_insert(BucketFeedSetting).values(
        farm_id=farm_id, bucket=bucket_code, daily_kg_per_head=kg
    )
    await db.execute(
        stmt.on_conflict_do_update(
            constraint="uq_feed_setting_per_bucket",
            set_={"daily_kg_per_head": kg},
        )
    )
    await db.flush()


async def feeding_plan(
    db: AsyncSession, farm: Farm, ref: date | None = None
) -> list[dict[str, Any]]:
    """Today's plan: one line per (bucket, recipe) with headcount, daily kg
    (heads × per-head setting) and the 40/20/40 shift split."""
    ref = ref or today(farm.timezone)
    animals_result = await db.execute(
        select(Animal)
        # recipe_for_animal reads days_in_current_bucket (bucket_moves).
        .options(selectinload(Animal.bucket_moves))
        .where(Animal.farm_id == farm.id, Animal.status == AnimalStatus.ACTIVE.value)
    )
    animals = list(animals_result.scalars().all())
    groups: dict[tuple[str, str], int] = {}
    for animal in animals:
        key = (animal.current_bucket, recipe_for_animal(animal, ref, farm.timezone))
        groups[key] = groups.get(key, 0) + 1

    # Two bulk queries, then join in Python — no per-bucket awaits.
    definitions = list((await db.execute(select(BucketDefinition))).scalars())
    order = {d.code: d.sort_order for d in definitions}
    kg_per_head_by_bucket = {d.code: d.daily_kg_per_head for d in definitions}
    settings_result = await db.execute(
        select(BucketFeedSetting).where(BucketFeedSetting.farm_id == farm.id)
    )
    for setting in settings_result.scalars():
        kg_per_head_by_bucket[setting.bucket] = setting.daily_kg_per_head
    lines = []
    for (bucket_code, recipe_code), heads in sorted(
        groups.items(), key=lambda kv: order.get(kv[0][0], 99)
    ):
        kg_per_head = kg_per_head_by_bucket.get(bucket_code, 1.2)
        daily_kg = round(heads * kg_per_head, 2)
        lines.append(
            {
                "bucket": bucket_code,
                "recipe_code": recipe_code,
                "recipe_name": RECIPE_DISPLAY.get(recipe_code, recipe_code),
                "heads": heads,
                "kg_per_head": kg_per_head,
                "daily_kg": daily_kg,
                "shifts": [
                    {
                        "shift": shift.value,
                        "pct": int(pct * 100),
                        "kg": round(daily_kg * pct, 2),
                        "time": SHIFT_TIMES[shift.value],
                    }
                    for shift, pct in SHIFT_SPLIT.items()
                ],
            }
        )
    return lines


class InsufficientFeedError(Exception):
    def __init__(self, shortages: list[str]):
        self.shortages = shortages
        super().__init__("; ".join(shortages))


async def mix_feed_batch(
    db: AsyncSession, farm: Farm, recipe_code: str, batch_kg: float
) -> FeedRecipe:
    """Mix a batch: decrement inventory per recipe lines. Refuses (no writes)
    if any ingredient is insufficient."""
    recipe_result = await db.execute(
        select(FeedRecipe)
        .options(selectinload(FeedRecipe.lines))
        .where(FeedRecipe.code == recipe_code)
    )
    recipe = recipe_result.scalars().first()
    if recipe is None:
        raise ValueError(f"Unknown recipe {recipe_code}")
    if not math.isfinite(batch_kg) or batch_kg <= 0:
        raise ValueError("Batch size must be a positive finite number")
    if not recipe.lines:
        raise ValueError(f"Recipe {recipe_code} has no ingredients")

    shortages = []
    planned: list[tuple[FeedInventory | None, float]] = []
    # Lock stock rows in a canonical order (ingredient name), not the recipe's
    # line order: two recipes sharing ingredients in different orders would
    # otherwise invert the FOR UPDATE order and deadlock under concurrent mixes.
    for line in sorted(recipe.lines, key=lambda recipe_line: recipe_line.ingredient):
        needed = round(line.kg_per_100kg / 100.0 * batch_kg, 3)
        # Inventory is maintained to the gram.  Accepting a batch for which
        # any positive recipe line rounds to zero would create finished stock
        # without consuming that ingredient.
        if line.kg_per_100kg > 0 and needed <= 0:
            raise ValueError(
                "Batch is too small to account for every ingredient at 0.001 kg precision"
            )
        # FOR UPDATE: concurrent mixes/restocks serialize on the stock row —
        # the loser re-reads the committed balance instead of a lost update.
        item_result = await db.execute(
            select(FeedInventory)
            .where(FeedInventory.farm_id == farm.id, FeedInventory.ingredient == line.ingredient)
            .with_for_update()
        )
        item = item_result.scalars().first()
        on_hand = item.qty_on_hand if item else 0.0
        if on_hand < needed:
            shortages.append(f"{line.ingredient}: need {needed:.1f} kg, have {on_hand:.1f} kg")
        planned.append((item, needed))

    if shortages:
        raise InsufficientFeedError(shortages)

    for item, needed in planned:
        if item:
            item.qty_on_hand = round(item.qty_on_hand - needed, 3)
    # Upsert the ready-feed balance in the same transaction as ingredient
    # consumption. Concurrent mixes add to the committed balance rather than
    # overwriting one another.
    finished_insert = pg_insert(FeedFinishedStock).values(
        farm_id=farm.id,
        recipe_code=recipe.code,
        qty_on_hand=round(batch_kg, 3),
    )
    await db.execute(
        finished_insert.on_conflict_do_update(
            constraint="uq_finished_feed_farm_recipe",
            set_={
                "qty_on_hand": FeedFinishedStock.qty_on_hand + finished_insert.excluded.qty_on_hand
            },
        )
    )
    await db.flush()
    return recipe


async def add_feed_stock(
    db: AsyncSession,
    farm: Farm,
    item: FeedInventory,
    qty_kg: float,
    price_per_kg: float | None,
    created_by_id: int | None = None,
) -> None:
    """Purchase stock: increase qty, update last price, book a FEED expense."""
    if not math.isfinite(qty_kg) or qty_kg <= 0:
        raise ValueError("Quantity must be a positive finite number")
    if price_per_kg is not None and not math.isfinite(price_per_kg):
        raise ValueError("Price must be finite")
    item.qty_on_hand = round(item.qty_on_hand + qty_kg, 3)
    # `is not None`: an explicit ₹0/kg is a real price (books a ₹0 expense and
    # zeroes the last price), not "no price given" — same rule as
    # create_purchase_batch's explicit-₹0 handling.
    if price_per_kg is not None:
        exact_price = money(price_per_kg)
        item.last_purchase_price_per_kg = exact_price
        db.add(
            Transaction(
                farm_id=farm.id,
                date=today(farm.timezone),
                type=TransactionType.EXPENSE.value,
                category=TransactionCategory.FEED.value,
                amount=money(Decimal(str(qty_kg)) * exact_price),
                notes=f"Feed purchase: {qty_kg:.1f} kg {item.ingredient}",
                created_by_id=created_by_id,
            )
        )
    await db.flush()


async def record_dispensing(
    db: AsyncSession,
    farm: Farm,
    bucket: str,
    shift: str,
    recipe_code: str | None,
    qty_kg: float,
    dispense_date: date,
    created_by_id: int | None = None,
) -> FeedingRecord:
    if not math.isfinite(qty_kg) or qty_kg <= 0:
        raise ValueError("Quantity must be a positive finite number")
    # Real recipes must first be mixed.  The quarantine roughage instruction
    # is a direct-fed virtual recipe, so it is intentionally not backed by a
    # finished-mix row.
    if recipe_code and recipe_code != DRY_ROUGHAGE:
        stock_result = await db.execute(
            select(FeedFinishedStock)
            .where(
                FeedFinishedStock.farm_id == farm.id,
                FeedFinishedStock.recipe_code == recipe_code,
            )
            .with_for_update()
        )
        stock = stock_result.scalar_one_or_none()
        available = stock.qty_on_hand if stock else 0.0
        if stock is None or available < qty_kg:
            raise InsufficientFeedError(
                [f"{recipe_code}: need {qty_kg:.3f} kg ready feed, have {available:.3f} kg"]
            )
        stock.qty_on_hand = round(available - qty_kg, 3)
    record = FeedingRecord(
        farm_id=farm.id,
        date=dispense_date,
        shift=shift,
        bucket=bucket,
        recipe_code=recipe_code or None,
        qty_kg=qty_kg,
        created_by_id=created_by_id,
    )
    db.add(record)
    await db.flush()
    return record
