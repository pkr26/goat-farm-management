"""Bounded dashboard query helpers."""

from datetime import date, timedelta
from typing import Any

from sqlalchemy import Date, ScalarSelect, Select, and_, case, cast, func, or_, select
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
)
from ..models.species import GOAT_PROFILE
from ..simulation.market import BAKRID_DATES_BY_YEAR  # read-only calendar import
from ..utils import add_months, business_date, today

# The Bakrid hold window: males whose projected market finish lands inside
# the two months before the festival are worth holding for the premium.
BAKRID_HOLD_WINDOW_MONTHS = 2


def next_bakrid_date(reference: date) -> date | None:
    """First Bakrid (Eid al-Adha) date strictly after ``reference``.

    The embedded calendar (simulation/market.py) is month-resolution and
    verified through 2032; beyond its last year there is no honest next date.
    """
    for year in sorted(BAKRID_DATES_BY_YEAR):
        month, day = BAKRID_DATES_BY_YEAR[year]
        candidate = date(year, month, day)
        if candidate > reference:
            return candidate
    return None


async def bakrid_hold_advisory(db: AsyncSession, farm: Farm) -> dict[str, Any] | None:
    """Males approaching the sale window whose finish lands before Bakrid.

    A male's projected finish date is his effective DOB plus the meat-sale
    window's upper age (9 months). When that date falls inside the two months
    before the next Bakrid, holding him for the festival market beats selling
    into the regular one. Returns the structured advisory
    ``{"key": "bakrid_hold", "args": {"count": n, "festival_date": iso}}``,
    or None when no male qualifies (or the calendar has no next date).
    """
    reference_date = today(farm.timezone)
    festival = next_bakrid_date(reference_date)
    if festival is None:
        return None
    window_start = add_months(festival, -BAKRID_HOLD_WINDOW_MONTHS)
    effective_dob = func.coalesce(Animal.date_of_birth, Animal.estimated_dob)
    finish_date = cast(
        effective_dob + func.make_interval(0, MEAT_SALE_AGE_MONTHS[1]),
        Date,
    )
    count = (
        await db.execute(
            select(func.count(Animal.id)).where(
                Animal.farm_id == farm.id,
                Animal.status == AnimalStatus.ACTIVE.value,
                Animal.sex == "M",
                effective_dob.is_not(None),
                # Still approaching: not yet past the sale window today.
                effective_dob > add_months(reference_date, -MEAT_SALE_AGE_MONTHS[1]),
                finish_date >= window_start,
                finish_date <= festival,
            )
        )
    ).scalar_one()
    if not count:
        return None
    return {
        "key": "bakrid_hold",
        "args": {"count": int(count), "festival_date": festival.isoformat()},
    }


def _age_months(dob: date | None, reference_date: date) -> int | None:
    if dob is None:
        return None
    months = (reference_date.year - dob.year) * 12 + (reference_date.month - dob.month)
    if reference_date.day < dob.day:
        months -= 1
    return max(months, 0)


def _exact_total(base: Select[Any]) -> ScalarSelect[int]:
    """The preview's full-result count, computed inside the preview statement.

    Same shape as ``api.dashboard._exact_total`` (kept local — the service
    layer must not import the API layer). ``func.count().over()`` yields the
    same number, but an unpartitioned window aggregate must drain its whole
    input before the LIMIT can emit anything, and the
    ``dashboard_animal_context`` subquery below carries five correlated
    scalar subqueries per row — every dashboard load would pay them for every
    ACTIVE animal, not just the bounded preview. An uncorrelated scalar
    subquery is evaluated once, keeps the total exact and in the same round
    trip, and leaves the preview itself bounded by its own LIMIT.
    """
    return select(func.count()).select_from(base.order_by(None).subquery()).scalar_subquery()


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

    # Biology thresholds mirror every write path's GOAT_PROFILE (12 mo/22 kg
    # breeding, gestation day 100/135; goats move to the kidding pen ~2 weeks
    # out, 150−15=135).
    profile = GOAT_PROFILE
    age_cutoff = add_months(reference_date, -profile.min_breeding_age_months)
    breeding_weight = profile.min_breeding_weight_kg
    pregnancy_late_day = profile.pregnancy_late_day
    due_window_day = profile.gestation_days - profile.prepartum_move_lead_days
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
    # RESTING→BREEDING readiness: the ~30-day dry-off + flush program.
    resting_ready = bucket_started_local_date <= reference_date - timedelta(days=30)
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
            resting_ready,
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
    # The meat-market rule is the goat SPEC's male-kid exit.
    # Single-sourced from MEAT_SALE_* and dated weight (latest_weight_as_of),
    # so a future-dated typo cannot inflate a market-ready suggestion.
    market_rule = and_(
        context.c.sex == "M",
        context.c.current_bucket == Bucket.MALE_KIDS.value,
        context.c.effective_dob.is_not(None),
        context.c.effective_dob <= add_months(reference_date, -MEAT_SALE_AGE_MONTHS[0]),
        context.c.latest_weight_as_of >= MEAT_SALE_WEIGHT_KG[0],
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
    candidates = select(context).where(qualifies)
    rows = (
        await db.execute(
            candidates.add_columns(_exact_total(candidates).label("suggestions_total"))
            .order_by(context.c.tag_number, context.c.animal_id)
            .limit(limit)
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
