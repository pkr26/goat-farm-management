"""Purchases & quarantine."""

# MAX_BATCH_COUNT / MAX_AGE_MONTHS live in models.py.

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    MAX_AGE_MONTHS,
    MAX_BATCH_COUNT,
    Animal,
    AnimalSource,
    AnimalStatus,
    Bucket,
    BucketMove,
    Farm,
    PurchaseBatch,
    TaskCategory,
    Transaction,
    TransactionCategory,
    TransactionType,
    quarantine_schedule,
)
from ..utils import add_months, money
from ._common import _add_task


async def create_purchase_batch(
    db: AsyncSession,
    farm: Farm,
    batch_date: date,
    supplier: str,
    count: int,
    avg_age_months: float | None,
    avg_weight_kg: float | None,
    total_price: float | None,
    notes: str,
    create_animals: bool,
    created_by_id: int | None = None,
    sex: str = "F",
) -> PurchaseBatch:
    """Create a purchase batch, optionally stub N animals into QUARANTINE,
    auto-generate the 45-day quarantine task schedule, and book the expense.
    Stub sex is explicit (a bought buck must not become a breeding-candidate
    "doe"); an explicit ₹0 price books a ₹0 expense, None books nothing."""
    if count < 1:
        raise ValueError("Batch count must be at least 1")
    if count > MAX_BATCH_COUNT:
        raise ValueError(f"Batch count is capped at {MAX_BATCH_COUNT} per batch")
    if sex not in ("M", "F"):
        raise ValueError("Batch sex must be M or F")
    if total_price is not None and total_price < 0:
        raise ValueError("Total price cannot be negative")
    if avg_age_months is not None and not 0 <= avg_age_months <= MAX_AGE_MONTHS:
        raise ValueError(f"Average age must be between 0 and {MAX_AGE_MONTHS} months")
    if avg_weight_kg is not None and avg_weight_kg < 0:
        raise ValueError("Average weight cannot be negative")
    exact_total_price = money(total_price) if total_price is not None else None
    batch = PurchaseBatch(
        farm_id=farm.id,
        date=batch_date,
        supplier=supplier or None,
        count=count,
        avg_age_months=avg_age_months,
        avg_weight_kg=avg_weight_kg,
        total_price=exact_total_price,
        notes=notes or None,
    )
    db.add(batch)
    await db.flush()

    if create_animals:
        # Per-head price: even split, but the first animal absorbs the paise
        # rounding remainder so Σ purchase_price equals the booked expense
        # (same trick as record_health_event's cost split).
        per_head = money(exact_total_price / count) if exact_total_price is not None else None
        first_head = (
            money(exact_total_price - per_head * (count - 1))
            if exact_total_price is not None and per_head is not None
            else None
        )
        for i in range(1, count + 1):
            animal = Animal(
                farm_id=farm.id,
                tag_number=f"B{batch.id}-{i:03d}",
                sex=sex,
                source=AnimalSource.PURCHASED.value,
                current_bucket=Bucket.QUARANTINE.value,
                status=AnimalStatus.ACTIVE.value,
                purchase_date=batch_date,
                purchase_price=first_head if i == 1 else per_head,
                seller_name=supplier or None,
                purchase_batch_id=batch.id,
                estimated_dob=(
                    # `is not None`: a 0-month (newborn) average age means the
                    # batch date itself, not "unknown age" — age-based vaccine
                    # scheduling breaks on a missing DOB.
                    add_months(batch_date, -int(avg_age_months))
                    if avg_age_months is not None
                    else None
                ),
            )
            db.add(animal)
            await db.flush()
            db.add(
                BucketMove(
                    animal_id=animal.id,
                    from_bucket=None,
                    to_bucket=Bucket.QUARANTINE.value,
                    reason=f"Purchase batch #{batch.id}",
                )
            )

    for item in quarantine_schedule(batch):
        await _add_task(
            db,
            farm.id,
            item["title"],
            item["due_date"],
            TaskCategory(item["category"]),
            purchase_batch_id=batch.id,
        )

    if exact_total_price is not None:  # an explicit ₹0 still books a ₹0 expense
        db.add(
            Transaction(
                farm_id=farm.id,
                date=batch_date,
                type=TransactionType.EXPENSE.value,
                category=TransactionCategory.ANIMAL_PURCHASE.value,
                amount=exact_total_price,
                notes=f"Purchase batch #{batch.id}: {count} animals"
                + (f" from {supplier}" if supplier else ""),
                created_by_id=created_by_id,
                source_type="PURCHASE_BATCH",
                source_id=batch.id,
            )
        )
    await db.flush()
    return batch
