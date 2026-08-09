"""Bounded dashboard query helpers."""

from datetime import date, timedelta
from typing import Any

from sqlalchemy import Date, and_, case, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    MIN_BREEDING_AGE_MONTHS,
    MIN_BREEDING_WEIGHT_KG,
    Animal,
    AnimalStatus,
    BreedingOutcome,
    BreedingRecord,
    Bucket,
    BucketMove,
    Farm,
    HealthEvent,
    KiddingRecord,
    WeightRecord,
)
from ..utils import add_months, business_date, today


def _age_months(dob: date | None, reference_date: date) -> int | None:
    if dob is None:
        return None
    months = (reference_date.year - dob.year) * 12 + (reference_date.month - dob.month)
    if reference_date.day < dob.day:
        months -= 1
    return max(months, 0)


async def ready_to_move_suggestions(
    db: AsyncSession,
    farm: Farm,
    *,
    limit: int,
    include_breeding: bool,
) -> tuple[list[dict[str, Any]], int]:
    """Return a deterministic suggestion preview plus its exact SQL count.

    Only scalar animal context and each history's latest relevant row cross
    the database boundary. The former implementation hydrated every active
    animal with every weight, bucket move, breeding and kidding row before it
    could decide whether even one suggestion existed.

    ``include_breeding`` is mandatory and keyword-only for the same reason
    ``_shared.animal_out`` demands ``permissions``: a suggestion to move a doe
    into BREEDING restates ``is_breeding_ready``, and one to move her on
    through gestation restates ``is_currently_pregnant`` down to the day, so a
    caller without ``breeding.view`` must not receive either. The rule is
    applied in SQL so the returned count matches the returned rows. Selling a
    grown male kid is an age/weight judgement carrying no breeding fact and
    stays visible.
    """
    reference_date = today(farm.timezone)
    effective_dob = func.coalesce(Animal.date_of_birth, Animal.estimated_dob)
    latest_weight_recorded = (
        select(WeightRecord.weight_kg)
        .where(WeightRecord.animal_id == Animal.id)
        .order_by(WeightRecord.date.desc(), WeightRecord.id.desc())
        .limit(1)
        .correlate(Animal)
        .scalar_subquery()
    )
    latest_weight_as_of = (
        select(WeightRecord.weight_kg)
        .where(
            WeightRecord.animal_id == Animal.id,
            WeightRecord.date <= reference_date,
        )
        .order_by(WeightRecord.date.desc(), WeightRecord.id.desc())
        .limit(1)
        .correlate(Animal)
        .scalar_subquery()
    )
    birth_weight_as_of = case(
        (effective_dob <= reference_date, Animal.birth_weight),
        else_=None,
    )
    latest_move = (
        select(BucketMove.effective_date)
        .where(BucketMove.animal_id == Animal.id)
        .order_by(BucketMove.moved_at.desc(), BucketMove.id.desc())
        .limit(1)
        .correlate(Animal)
        .scalar_subquery()
    )
    # A confirmed pregnancy ceases to be open as soon as its kidding row is
    # recorded. Ordering matches the prior Python max(breeding_date, id).
    latest_open_pregnancy = (
        select(BreedingRecord.breeding_date)
        .outerjoin(KiddingRecord, KiddingRecord.breeding_record_id == BreedingRecord.id)
        .where(
            BreedingRecord.farm_id == farm.id,
            BreedingRecord.doe_id == Animal.id,
            BreedingRecord.outcome == BreedingOutcome.CONFIRMED_PREGNANT.value,
            KiddingRecord.id.is_(None),
        )
        .order_by(BreedingRecord.breeding_date.desc(), BreedingRecord.id.desc())
        .limit(1)
        .correlate(Animal)
        .scalar_subquery()
    )
    active_withdrawal = (
        select(HealthEvent.id)
        .where(
            HealthEvent.farm_id == farm.id,
            HealthEvent.animal_id == Animal.id,
            HealthEvent.withdrawal_until >= reference_date,
        )
        .correlate(Animal)
        .exists()
    )
    context = (
        select(
            Animal.id.label("animal_id"),
            Animal.tag_number,
            Animal.name,
            Animal.sex,
            Animal.current_bucket,
            Animal.created_at,
            Animal.movement_restricted,
            Animal.suspected_scheduled_disease,
            effective_dob.label("effective_dob"),
            func.coalesce(latest_weight_recorded, Animal.birth_weight).label("latest_weight"),
            func.coalesce(latest_weight_as_of, birth_weight_as_of).label("latest_weight_as_of"),
            latest_move.label("latest_effective_date"),
            latest_open_pregnancy.label("open_pregnancy_date"),
            active_withdrawal.label("has_active_withdrawal"),
        )
        .where(Animal.farm_id == farm.id, Animal.status == AnimalStatus.ACTIVE.value)
        .subquery("dashboard_animal_context")
    )

    age_cutoff = add_months(reference_date, -MIN_BREEDING_AGE_MONTHS)
    male_sale_age_cutoff = add_months(reference_date, -8)
    created_local_date = cast(
        func.timezone(
            farm.timezone,
            func.timezone("UTC", context.c.created_at),
        ),
        Date,
    )
    bucket_started_local_date = func.coalesce(
        context.c.latest_effective_date,
        created_local_date,
    )
    breeding_rules = [
        and_(
            context.c.sex == "F",
            context.c.current_bucket.in_([Bucket.FOUNDATION.value, Bucket.FEMALE_KIDS.value]),
            context.c.effective_dob.is_not(None),
            context.c.effective_dob <= age_cutoff,
            context.c.latest_weight_as_of >= MIN_BREEDING_WEIGHT_KG,
            context.c.open_pregnancy_date.is_(None),
        ),
        and_(
            context.c.sex == "F",
            context.c.current_bucket == Bucket.RESTING.value,
            bucket_started_local_date <= reference_date - timedelta(days=30),
            context.c.effective_dob.is_not(None),
            context.c.effective_dob <= age_cutoff,
            context.c.latest_weight_as_of >= MIN_BREEDING_WEIGHT_KG,
            context.c.open_pregnancy_date.is_(None),
        ),
        and_(
            context.c.current_bucket == Bucket.PREGNANCY_EARLY.value,
            context.c.open_pregnancy_date <= reference_date - timedelta(days=100),
        ),
        and_(
            context.c.current_bucket == Bucket.PREGNANCY_LATE.value,
            context.c.open_pregnancy_date <= reference_date - timedelta(days=135),
        ),
    ]
    market_rule = and_(
        context.c.sex == "M",
        context.c.current_bucket == Bucket.MALE_KIDS.value,
        context.c.effective_dob.is_not(None),
        context.c.effective_dob <= male_sale_age_cutoff,
        context.c.latest_weight >= 24.0,
        context.c.has_active_withdrawal.is_(False),
    )
    qualifies = and_(
        # Suggestions must never contradict the authoritative write paths:
        # every lifecycle move and sale is blocked while either safety hold
        # is active, regardless of which transition rule would otherwise fit.
        context.c.movement_restricted.is_(False),
        context.c.suspected_scheduled_disease.is_(False),
        or_(*breeding_rules, market_rule) if include_breeding else market_rule,
    )
    candidates = select(context, func.count().over().label("suggestions_total")).where(qualifies)
    rows = (
        await db.execute(
            candidates.order_by(context.c.tag_number, context.c.animal_id).limit(limit)
        )
    ).all()

    suggestions: list[dict[str, Any]] = []
    for row in rows:
        bucket = row.current_bucket
        if bucket in (Bucket.FOUNDATION.value, Bucket.FEMALE_KIDS.value):
            target = Bucket.BREEDING.value
            reason = "Breeding-ready (≥10 mo, ≥22 kg)"
        elif bucket == Bucket.RESTING.value:
            started_at = row.latest_effective_date
            started_date = (
                started_at
                if started_at is not None
                else business_date(row.created_at, farm.timezone)
            )
            bucket_days = max(
                (reference_date - started_date).days,
                0,
            )
            target = Bucket.BREEDING.value
            reason = f"{bucket_days} days resting (flush done)"
        elif bucket == Bucket.PREGNANCY_EARLY.value:
            gestation_day = (reference_date - row.open_pregnancy_date).days
            target = Bucket.PREGNANCY_LATE.value
            reason = f"Gestation day {gestation_day} (≥100)"
        elif bucket == Bucket.PREGNANCY_LATE.value:
            gestation_day = (reference_date - row.open_pregnancy_date).days
            target = Bucket.DELIVERY.value
            reason = f"Gestation day {gestation_day} (≥135, due soon)"
        else:
            age = _age_months(row.effective_dob, reference_date)
            target = "SELL"
            reason = f"{age} mo, {row.latest_weight:.1f} kg — market ready"
        suggestions.append(
            {
                "animal": {
                    "id": row.animal_id,
                    "tag_number": row.tag_number,
                    "name": row.name,
                },
                "to": target,
                "reason": reason,
            }
        )
    return suggestions, int(rows[0].suggestions_total) if rows else 0
