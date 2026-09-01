"""Purchases & quarantine."""

# MAX_BATCH_COUNT / MAX_AGE_MONTHS live in models.py.

import secrets
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    MAX_AGE_MONTHS,
    MAX_ANIMAL_TAG_LENGTH,
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
    WeightRecord,
    quarantine_schedule,
)
from ..utils import add_months, allocate_money, money
from ._common import _add_task


def _purchase_batch_tag(batch_id: int, batch_tag_nonce: str, index: int) -> str:
    """Return the bounded, sortable internal tag for one purchased animal."""
    tag = f"B{batch_id}-{batch_tag_nonce}-{index:04d}"
    if len(tag) > MAX_ANIMAL_TAG_LENGTH:
        raise ValueError(f"Generated purchase tag exceeds {MAX_ANIMAL_TAG_LENGTH} characters")
    return tag


def _estimated_dob_from_age(batch_date: date, avg_age_months: float) -> date:
    """Approximate a decimal-month age without discarding its fraction.

    Whole months use calendar arithmetic (and therefore retain the existing
    month-end clamping rule).  The remaining fraction interpolates between the
    two adjacent whole-month anchors on that *same* calendar scale, with
    ordinary half-up rounding to a whole day — e.g. ``7.5`` means seven
    calendar months plus about half of the preceding calendar month.

    The fraction deliberately does NOT use a fixed mean month length: a mean
    30.44-day fraction measured against a calendar-month base overshoots short
    months, so "0.99 of a month" subtracted across a February landed EARLIER
    than the next whole-month anchor — an animal declared younger was dated
    older, shifting age-based vaccine due dates and recipe-day boundaries in
    the wrong direction.  Interpolating over the anchors' actual day span
    keeps estimated_dob monotonically earlier-or-equal as stated age grows.
    """
    age = Decimal(str(avg_age_months))
    whole_months = int(age)
    newer_anchor = add_months(batch_date, -whole_months)
    older_anchor = add_months(batch_date, -(whole_months + 1))
    fractional_days = int(
        ((age - whole_months) * (newer_anchor - older_anchor).days).to_integral_value(
            rounding=ROUND_HALF_UP
        )
    )
    return newer_anchor - timedelta(days=fractional_days)


async def schedule_quarantine_tasks(db: AsyncSession, farm: Farm, batch: PurchaseBatch) -> None:
    """Create one auditable protocol series for a batch that has animals."""
    for item in quarantine_schedule(batch, farm.farm_type):
        await _add_task(
            db,
            farm.id,
            farm.farm_type,
            item["title"],
            item["due_date"],
            TaskCategory(item["category"]),
            purchase_batch_id=batch.id,
        )


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
        sex=sex,
        avg_age_months=avg_age_months,
        avg_weight_kg=avg_weight_kg,
        total_price=exact_total_price,
        notes=notes or None,
    )
    db.add(batch)
    await db.flush()

    if create_animals:
        # A batch id is predictable and PostgreSQL sequences advance even when
        # a transaction rolls back.  A lower-privilege animal creator could
        # therefore pre-squat ``B{next_batch_id}-001`` and deny procurement.
        # One CSPRNG nonce per batch makes all generated tags unguessable while
        # keeping them compact, sortable and cheap to construct in bulk.
        batch_tag_nonce = secrets.token_hex(6)
        # Allocate integer paise rather than rounding N independent divisions:
        # for a tiny total (₹0.02 / 4), ordinary rounding makes three ₹0.01
        # shares and a negative remainder. Every share here is non-negative
        # and the exact sum remains the booked expense.
        per_head_prices: list[Decimal | None] = []
        if exact_total_price is not None:
            per_head_prices.extend(allocate_money(exact_total_price, count))
        else:
            per_head_prices.extend([None] * count)
        animals = [
            Animal(
                farm_id=farm.id,
                tag_number=_purchase_batch_tag(batch.id, batch_tag_nonce, i),
                sex=sex,
                source=AnimalSource.PURCHASED.value,
                current_bucket=Bucket.QUARANTINE.value,
                status=AnimalStatus.ACTIVE.value,
                purchase_date=batch_date,
                purchase_price=per_head_price,
                seller_name=supplier or None,
                purchase_batch_id=batch.id,
                estimated_dob=(
                    # `is not None`: a 0-month (newborn) average age means the
                    # batch date itself, not "unknown age" — age-based vaccine
                    # scheduling breaks on a missing DOB.
                    _estimated_dob_from_age(batch_date, avg_age_months)
                    if avg_age_months is not None
                    else None
                ),
            )
            for i, per_head_price in enumerate(per_head_prices, start=1)
        ]
        # One ORM flush lets PostgreSQL/SQLAlchemy's insertmanyvalues path
        # persist and RETURN all generated animal ids as a set.  Flushing in
        # this loop used to turn a valid 1,000-head purchase into 1,000
        # sequential network round trips before any moves could be written.
        db.add_all(animals)
        await db.flush()
        db.add_all(
            [
                BucketMove(
                    animal_id=animal.id,
                    from_bucket=None,
                    to_bucket=Bucket.QUARANTINE.value,
                    effective_date=batch_date,
                    reason=f"Purchase batch #{batch.id}",
                    created_by_id=created_by_id,
                )
                for animal in animals
            ]
        )
        if avg_weight_kg is not None and avg_weight_kg > 0:
            db.add_all(
                [
                    WeightRecord(
                        animal_id=animal.id,
                        date=batch_date,
                        weight_kg=avg_weight_kg,
                        notes="Estimated from purchase batch average",
                        created_by_id=created_by_id,
                    )
                    for animal in animals
                ]
            )
        await schedule_quarantine_tasks(db, farm, batch)

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
