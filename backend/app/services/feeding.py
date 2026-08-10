"""Feeding."""

import math
from datetime import date
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import Date as SqlDate
from sqlalchemy import Numeric, case, cast, func, select, true
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
    BucketMove,
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
from ..utils import DEFAULT_BUSINESS_TIMEZONE, MONEY_QUANTUM, money, today

DRY_ROUGHAGE = "DRY_ROUGHAGE_ONLY"
# The virtual recipe is direct-fed from this seeded raw-inventory row. Keeping
# the ingredient explicit prevents a successful dispensing log from creating
# feed ex nihilo merely because no finished-mix recipe exists.
DRY_ROUGHAGE_INGREDIENT = "Dry jowar stover"
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

KG_QUANTUM = Decimal("0.001")
MAX_LEDGER_AMOUNT = Decimal("1000000000.00")


def _positive_kg(value: float, label: str) -> float:
    """Return a finite positive quantity at the stock ledger's 0.001 kg precision."""
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{label} must be a positive finite number")
    rounded = Decimal(str(value)).quantize(KG_QUANTUM, rounding=ROUND_HALF_UP)
    if rounded <= 0:
        raise ValueError(f"{label} must be at least 0.001 kg after rounding")
    return float(rounded)


def _allocate_recipe_grams(weights: list[Decimal], total_grams: int) -> list[int]:
    """Apportion a finished batch as whole ingredient grams.

    Hamilton/largest-remainder allocation prevents independent line rounding
    from consuming more or less ingredient than the finished stock credited.
    Every configured positive ingredient must receive at least one gram. A
    batch whose ordinary apportionment leaves any ingredient at zero is rejected instead
    of silently distorting the recipe to force a representable mix.
    """
    if total_grams < len(weights):
        raise ValueError("Batch is too small to account for every ingredient at 0.001 kg precision")
    total_weight = sum(weights, Decimal(0))
    exact = [Decimal(total_grams) * weight / total_weight for weight in weights]
    allocated = [int(share.to_integral_value(rounding=ROUND_FLOOR)) for share in exact]
    unallocated = total_grams - sum(allocated)
    ranked = sorted(
        range(len(weights)),
        # An exactly tied residual gram goes to a zero-floor line first. This
        # has the same Hamilton rounding error as the canonical tie-break but
        # avoids rejecting a representable recipe merely because its smallest
        # ingredient sorts after an already-positive line.
        key=lambda index: (
            -(exact[index] - Decimal(allocated[index])),
            allocated[index] != 0,
            index,
        ),
    )
    for index in ranked[:unallocated]:
        allocated[index] += 1

    if any(grams == 0 for grams in allocated):
        raise ValueError("Batch is too small to account for every ingredient at 0.001 kg precision")

    return allocated


def _restock_money(qty_kg: float, price_per_kg: float | None) -> tuple[Decimal, Decimal] | None:
    """Validate and derive the exact unit price and total before any write."""
    if price_per_kg is None:
        return None
    if not math.isfinite(price_per_kg) or price_per_kg < 0:
        raise ValueError("Price must be a non-negative finite number")
    raw_price = Decimal(str(price_per_kg))
    if raw_price > MAX_LEDGER_AMOUNT:
        raise ValueError("Price cannot exceed ₹1,000,000,000.00 per kg")
    exact_price = money(raw_price)
    if raw_price > 0 and exact_price < MONEY_QUANTUM:
        raise ValueError("A positive price must round to at least ₹0.01 per kg")

    total_cost = money(Decimal(str(qty_kg)) * exact_price)
    if raw_price > 0 and total_cost < MONEY_QUANTUM:
        raise ValueError("A positive-price restock must total at least ₹0.01 after rounding")
    if total_cost > MAX_LEDGER_AMOUNT:
        raise ValueError("Restock cost cannot exceed ₹1,000,000,000.00")
    return exact_price, total_cost


def _shift_quantities(daily_kg: float) -> dict[FeedingShift, float]:
    """Allocate whole grams by the configured shift percentages.

    Independent rounding can lose or invent feed for tiny rations. Largest-
    remainder allocation preserves the exact displayed daily total and keeps
    the configured 40/20/40 proportions as close as gram precision
    allows, with enum order as the deterministic tie-breaker.
    """
    total_units = int(
        (Decimal(str(daily_kg)) / KG_QUANTUM).to_integral_value(rounding=ROUND_HALF_UP)
    )
    weighted = [
        (shift, total_units * int(Decimal(str(pct)) * 100)) for shift, pct in SHIFT_SPLIT.items()
    ]
    allocated = {shift: numerator // 100 for shift, numerator in weighted}
    missing = total_units - sum(allocated.values())
    ranked = sorted(
        enumerate(weighted),
        key=lambda item: (-(item[1][1] % 100), item[0]),
    )
    for _index, (shift, _numerator) in ranked[:missing]:
        allocated[shift] += 1
    return {shift: float(Decimal(units) * KG_QUANTUM) for shift, units in allocated.items()}


def recipe_for_animal(
    animal: Animal,
    ref: date | None = None,
    timezone_name: str = DEFAULT_BUSINESS_TIMEZONE,
    *,
    bucket_days: int | None = None,
) -> str:
    """Which TMR recipe applies to this animal today (SPEC allocation rules).

    Pure python (stays synchronous). Callers may provide SQL-derived
    ``bucket_days``; otherwise date-sensitive buckets read the animal's loaded
    bucket history."""
    ref = ref or today(timezone_name)
    bucket = animal.current_bucket
    if bucket in (Bucket.QUARANTINE.value, Bucket.RESTING.value):
        bucket_days = (
            animal.days_in_current_bucket_on(ref, timezone_name)
            if bucket_days is None
            else bucket_days
        )
    return _recipe_for_context(bucket, animal.effective_dob, ref, bucket_days or 0)


def _recipe_for_context(
    bucket: str,
    effective_dob: date | None,
    ref: date,
    bucket_days: int,
) -> str:
    """Recipe rules over only the four fields today's plan actually needs."""
    if bucket == Bucket.QUARANTINE.value:
        return DRY_ROUGHAGE if bucket_days < 3 else "MAINTENANCE_75_25"
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
        return "FLUSH_70_30" if bucket_days >= 10 else "MAINTENANCE_75_25"
    if bucket == Bucket.MALE_KIDS.value:
        age_days = (ref - effective_dob).days if effective_dob else 999  # unknown → fattening
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
    # A correlated LATERAL LIMIT 1 reads at most the latest movement per active
    # animal. The previous selectinload hydrated every historical BucketMove,
    # so memory and wire volume grew with the farm's lifetime movement count.
    latest_move = (
        select(BucketMove.effective_date)
        .where(BucketMove.animal_id == Animal.id)
        .order_by(BucketMove.moved_at.desc(), BucketMove.id.desc())
        .limit(1)
        .correlate(Animal)
        .lateral("latest_bucket_move")
    )
    created_date = cast(
        func.timezone(farm.timezone, func.timezone("UTC", Animal.created_at)),
        SqlDate,
    )
    bucket_started_date = func.coalesce(latest_move.c.effective_date, created_date)
    bucket_days = func.greatest(ref - bucket_started_date, 0)
    effective_dob = func.coalesce(Animal.date_of_birth, Animal.estimated_dob)
    age_days = func.coalesce(ref - effective_dob, 999)
    bucket = Animal.current_bucket
    recipe_code = case(
        (
            (bucket == Bucket.QUARANTINE.value) & (bucket_days < 3),
            DRY_ROUGHAGE,
        ),
        (bucket == Bucket.QUARANTINE.value, "MAINTENANCE_75_25"),
        (
            bucket.in_(
                (
                    Bucket.FOUNDATION.value,
                    Bucket.FEMALE_KIDS.value,
                    Bucket.PREGNANCY_LATE.value,
                    Bucket.RECOVERY.value,
                    Bucket.DELIVERY.value,
                )
            ),
            "LACTATING_60_40",
        ),
        (
            bucket.in_((Bucket.BREEDING.value, Bucket.PREGNANCY_EARLY.value)),
            "MAINTENANCE_75_25",
        ),
        (
            (bucket == Bucket.RESTING.value) & (bucket_days >= 10),
            "FLUSH_70_30",
        ),
        (bucket == Bucket.RESTING.value, "MAINTENANCE_75_25"),
        (
            (bucket == Bucket.MALE_KIDS.value) & (age_days <= 90),
            "LACTATING_60_40",
        ),
        (bucket == Bucket.MALE_KIDS.value, "FATTENING_50_50"),
        else_="MAINTENANCE_75_25",
    )
    contexts = (
        select(
            bucket.label("bucket"),
            recipe_code.label("recipe_code"),
        )
        .outerjoin(latest_move, true())
        .where(Animal.farm_id == farm.id, Animal.status == AnimalStatus.ACTIVE.value)
        .subquery("feeding_contexts")
    )
    groups = (
        await db.execute(
            select(
                contexts.c.bucket,
                contexts.c.recipe_code,
                func.count().label("heads"),
            )
            .group_by(contexts.c.bucket, contexts.c.recipe_code)
            .order_by(contexts.c.bucket, contexts.c.recipe_code)
        )
    ).all()

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
    for bucket_code, recipe_code, heads in sorted(groups, key=lambda row: order.get(row[0], 99)):
        kg_per_head = kg_per_head_by_bucket.get(bucket_code, 1.2)
        daily_kg = float(
            (Decimal(heads) * Decimal(str(kg_per_head))).quantize(
                KG_QUANTUM, rounding=ROUND_HALF_UP
            )
        )
        shift_quantities = _shift_quantities(daily_kg)
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
                        "kg": shift_quantities[shift],
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
    batch_kg = _positive_kg(batch_kg, "Batch size")
    if not recipe.lines:
        raise ValueError(f"Recipe {recipe_code} has no ingredients")
    if any(not math.isfinite(line.kg_per_100kg) or line.kg_per_100kg <= 0 for line in recipe.lines):
        raise ValueError(f"Recipe {recipe_code} has an invalid ingredient quantity")
    # Inventory is one balance per ingredient. Aggregate duplicate configured
    # lines before apportionment so an insignificant duplicate does not create
    # an artificial one-gram minimum or a false tiny-batch rejection.
    weights_by_ingredient: dict[str, Decimal] = {}
    for line in sorted(recipe.lines, key=lambda recipe_line: recipe_line.ingredient):
        weights_by_ingredient[line.ingredient] = weights_by_ingredient.get(
            line.ingredient, Decimal(0)
        ) + Decimal(str(line.kg_per_100kg))
    recipe_total = sum(weights_by_ingredient.values(), Decimal(0))
    if abs(recipe_total - Decimal(100)) > Decimal("0.000001"):
        raise ValueError(
            f"Recipe {recipe_code} must total exactly 100 kg per 100 kg batch; "
            f"configured total is {recipe_total:g} kg"
        )

    total_grams = int(Decimal(str(batch_kg)) / KG_QUANTUM)
    allocated_grams = _allocate_recipe_grams(list(weights_by_ingredient.values()), total_grams)
    grams_by_ingredient = dict(zip(weights_by_ingredient, allocated_grams, strict=True))

    shortages = []
    planned: list[tuple[FeedInventory | None, Decimal]] = []
    # Lock stock rows in a canonical order (ingredient name), not the recipe's
    # line order: two recipes sharing ingredients in different orders would
    # otherwise invert the FOR UPDATE order and deadlock under concurrent mixes.
    for ingredient, grams in grams_by_ingredient.items():
        needed = Decimal(grams) * KG_QUANTUM
        # FOR UPDATE: concurrent mixes/restocks serialize on the stock row —
        # the loser re-reads the committed balance instead of a lost update.
        item_result = await db.execute(
            select(FeedInventory)
            .where(FeedInventory.farm_id == farm.id, FeedInventory.ingredient == ingredient)
            .with_for_update()
        )
        item = item_result.scalars().first()
        on_hand = Decimal(str(item.qty_on_hand)) if item else Decimal(0)
        if on_hand < needed:
            shortages.append(f"{ingredient}: need {needed:.3f} kg, have {on_hand:.3f} kg")
        planned.append((item, needed))

    if shortages:
        raise InsufficientFeedError(shortages)

    for item, needed in planned:
        if item:
            item.qty_on_hand = float(
                (Decimal(str(item.qty_on_hand)) - needed).quantize(
                    KG_QUANTUM, rounding=ROUND_HALF_UP
                )
            )
    # Upsert the ready-feed balance in the same transaction as ingredient
    # consumption. Concurrent mixes add to the committed balance rather than
    # overwriting one another.
    finished_insert = pg_insert(FeedFinishedStock).values(
        farm_id=farm.id,
        recipe_code=recipe.code,
        qty_on_hand=batch_kg,
    )
    await db.execute(
        finished_insert.on_conflict_do_update(
            constraint="uq_finished_feed_farm_recipe",
            set_={
                "qty_on_hand": func.round(
                    cast(
                        FeedFinishedStock.qty_on_hand + finished_insert.excluded.qty_on_hand,
                        Numeric,
                    ),
                    3,
                )
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
    qty_kg = _positive_kg(qty_kg, "Quantity")
    restock_money = _restock_money(qty_kg, price_per_kg)

    # All derived-value validation above deliberately precedes mutation. API
    # callers therefore receive a deterministic 400 without relying on a DB
    # CHECK/NUMERIC overflow to roll the transaction back.
    item.qty_on_hand = float(
        (Decimal(str(item.qty_on_hand)) + Decimal(str(qty_kg))).quantize(
            KG_QUANTUM, rounding=ROUND_HALF_UP
        )
    )
    # `is not None`: an explicit ₹0/kg is a real price (books a ₹0 expense and
    # zeroes the last price), not "no price given" — same rule as
    # create_purchase_batch's explicit-₹0 handling.
    if restock_money is not None:
        exact_price, total_cost = restock_money
        item.last_purchase_price_per_kg = exact_price
        purchase = Transaction(
            farm_id=farm.id,
            date=today(farm.timezone),
            type=TransactionType.EXPENSE.value,
            category=TransactionCategory.FEED.value,
            amount=total_cost,
            notes=(
                f"Feed purchase: {qty_kg:.3f} kg {item.ingredient} "
                f"@ ₹{exact_price:.2f}/kg = ₹{total_cost:.2f}"
            ),
            created_by_id=created_by_id,
        )
        db.add(purchase)
        # Provenance, as HEALTH_EVENT/PURCHASE_BATCH already record it: without
        # it the finance UI presents this system-generated expense as a hand
        # -typed "Manual entry". A restock persists no row of its own — it only
        # moves the running FeedInventory balance — and that row's id repeats on
        # every restock, which the partial unique index over the ACTIVE source
        # pair (farm_id, source_type, source_id) would reject the second time.
        # The ledger row is therefore its own source: unique by construction,
        # and an audited correction still inherits the pair once the original is
        # voided. Provenance is set after the insert because the source-pair
        # CHECK forbids a half-populated pair and the stable id is not yet known.
        await db.flush()
        purchase.source_type = "FEED_PURCHASE"
        purchase.source_id = purchase.id
        purchase.feed_inventory_id = item.id
        purchase.feed_quantity_kg = Decimal(str(qty_kg)).quantize(
            KG_QUANTUM, rounding=ROUND_HALF_UP
        )
        purchase.feed_unit_price_per_kg = exact_price
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
    qty_kg = _positive_kg(qty_kg, "Quantity")
    requested = Decimal(str(qty_kg))
    # The quarantine roughage instruction is direct-fed rather than mixed,
    # but it is still real stock. Lock and debit the canonical seeded dry
    # stover row in the same transaction as the dispensing record.
    if recipe_code == DRY_ROUGHAGE:
        inventory_result = await db.execute(
            select(FeedInventory)
            .where(
                FeedInventory.farm_id == farm.id,
                FeedInventory.ingredient == DRY_ROUGHAGE_INGREDIENT,
            )
            .with_for_update()
        )
        inventory = inventory_result.scalar_one_or_none()
        available = (
            Decimal(str(inventory.qty_on_hand)).quantize(KG_QUANTUM, rounding=ROUND_HALF_UP)
            if inventory
            else Decimal(0)
        )
        if inventory is None or available < requested:
            raise InsufficientFeedError(
                [f"{DRY_ROUGHAGE_INGREDIENT}: need {requested:.3f} kg, have {available:.3f} kg"]
            )
        inventory.qty_on_hand = float(
            (available - requested).quantize(KG_QUANTUM, rounding=ROUND_HALF_UP)
        )
    # Real recipes must first be mixed, then are debited from finished stock.
    elif recipe_code:
        stock_result = await db.execute(
            select(FeedFinishedStock)
            .where(
                FeedFinishedStock.farm_id == farm.id,
                FeedFinishedStock.recipe_code == recipe_code,
            )
            .with_for_update()
        )
        stock = stock_result.scalar_one_or_none()
        available = (
            Decimal(str(stock.qty_on_hand)).quantize(KG_QUANTUM, rounding=ROUND_HALF_UP)
            if stock
            else Decimal(0)
        )
        if stock is None or available < requested:
            raise InsufficientFeedError(
                [f"{recipe_code}: need {requested:.3f} kg ready feed, have {available:.3f} kg"]
            )
        stock.qty_on_hand = float(
            (available - requested).quantize(KG_QUANTUM, rounding=ROUND_HALF_UP)
        )
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
