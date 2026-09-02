"""Bounded dashboard query helpers."""

from datetime import date, timedelta
from typing import Any

from sqlalchemy import Date, and_, case, cast, false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    MEAT_SALE_AGE_MONTHS,
    MEAT_SALE_WEIGHT_KG,
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
    species_profile,
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

    # Species-aware thresholds: every write path resolves biology through
    # species_profile(farm.farm_type); the suggestion widget must not quote
    # goat numbers on a buffalo dairy (10 mo/22 kg breeding, 8 mo/24 kg sale,
    # gestation day 100/135). Gestation stage gates scale from the species'
    # gestation length (2/3 for EARLY→LATE). The due window mirrors the
    # authoritative delivery-move duty exactly — goats move to the kidding
    # pen ~2 weeks out (150−15=135), buffalo ride the dry-off point ~60 days
    # before calving (310−60=250) — never a generic 90% ratio, which on a
    # dairy would suggest the dry-group move 29 days AFTER dry therapy starts.
    profile = species_profile(farm.farm_type)
    age_cutoff = add_months(reference_date, -profile.min_breeding_age_months)
    breeding_weight = profile.min_breeding_weight_kg
    pregnancy_late_day = round(profile.gestation_days * 2 / 3)
    delivery_move_lead_days = 15 if profile.young_stay_with_dam else 60
    due_window_day = profile.gestation_days - delivery_move_lead_days
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
            context.c.latest_weight_as_of >= breeding_weight,
            context.c.open_pregnancy_date.is_(None),
        ),
        and_(
            context.c.sex == "F",
            context.c.current_bucket == Bucket.RESTING.value,
            bucket_started_local_date <= reference_date - timedelta(days=30),
            context.c.effective_dob.is_not(None),
            context.c.effective_dob <= age_cutoff,
            context.c.latest_weight_as_of >= breeding_weight,
            context.c.open_pregnancy_date.is_(None),
        ),
        and_(
            context.c.current_bucket == Bucket.PREGNANCY_EARLY.value,
            context.c.open_pregnancy_date <= reference_date - timedelta(days=pregnancy_late_day),
        ),
        and_(
            context.c.current_bucket == Bucket.PREGNANCY_LATE.value,
            context.c.open_pregnancy_date <= reference_date - timedelta(days=due_window_day),
        ),
    ]
    # The meat-market rule is the goat SPEC's male-kid exit; dairy males leave
    # through their own sale path (week-old bull calves or grown sires), so
    # the widget gates it to goat farms. Single-sourced from MEAT_SALE_* and
    # dated weight (latest_weight_as_of), so a future-dated typo cannot
    # inflate a market-ready suggestion.
    market_rule = (
        and_(
            context.c.sex == "M",
            context.c.current_bucket == Bucket.MALE_KIDS.value,
            context.c.effective_dob.is_not(None),
            context.c.effective_dob
            <= add_months(reference_date, -MEAT_SALE_AGE_MONTHS[0]),
            context.c.latest_weight_as_of >= MEAT_SALE_WEIGHT_KG[0],
            context.c.has_active_withdrawal.is_(False),
        )
        if profile.farm_type == "GOAT"
        else false()
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
            reason = (
                f"Breeding-ready (≥{profile.min_breeding_age_months} mo, "
                f"≥{profile.min_breeding_weight_kg:.0f} kg)"
            )
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
            reason = f"Gestation day {gestation_day} (≥{pregnancy_late_day})"
        elif bucket == Bucket.PREGNANCY_LATE.value:
            gestation_day = (reference_date - row.open_pregnancy_date).days
            target = Bucket.DELIVERY.value
            reason = f"Gestation day {gestation_day} (≥{due_window_day}, due soon)"
        else:
            age = _age_months(row.effective_dob, reference_date)
            target = "SELL"
            reason = (
                f"{age} mo, {row.latest_weight_as_of:.1f} kg — market ready "
                f"(window {MEAT_SALE_AGE_MONTHS[0]}–{MEAT_SALE_AGE_MONTHS[1]} mo, "
                f"{MEAT_SALE_WEIGHT_KG[0]:.0f}–{MEAT_SALE_WEIGHT_KG[1]:.0f} kg)"
            )
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
