"""Recurring husbandry cadence materialized onto the task board.

The vaccination/deworming schedule computed from ``VaccineTemplates`` is an
animal-level read model; the seasonal husbandry calendar (FMD rounds,
pre-monsoon ET+HS, deworming sweeps, hoof trimming, spraying, shed
disinfection, weighing, feed-room routine, water checks and buck rotation)
is farm-level operational work. This module turns that calendar into actual
duties the morning of the day they are due, idempotently, from a
short-interval background sweep (``cadence_materialization_*`` settings) —
never from a request path: GET /api/tasks is read-only.
"""

import calendar
import logging
from datetime import date, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from ..models import (
    GOAT_PROFILE,
    Animal,
    AnimalStatus,
    Farm,
    FeedInventory,
    PurchaseBatch,
    Sex,
    Task,
    TaskCategory,
    TaskStatus,
)
from ..utils import today
from ._common import _add_task
from .tasks import lock_manual_task_queue

_logger = logging.getLogger("goatfarm.cadence")

# Sweep safety bounds: every query this module runs is either an indexed
# LIMIT-1 existence probe or one bounded scan, because the background sweep
# walks every farm on a short interval.
_MAX_REORDER_INGREDIENTS = 100
_MAX_BUCK_SCAN = 500
_BUCK_ROTATION_LOOKBACK_DAYS = 365
# A missed seasonal round is only worth resurrecting while it is still the
# series' current cycle: an FMD gap from 18 months ago is stale history (the
# animal-level schedule has moved on), not an actionable duty.
_BACKFALL_MAX_AGE_DAYS = 365
# Interval-round dedupe probes this far AHEAD of today as well as back over
# the lookback: an operator-scheduled same-category round due next week must
# suppress today's auto round, or the board grows a duplicate pair.
_INTERVAL_FORWARD_DEDUPE_DAYS = 30

_MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

# (calendar month, task category, title key, title template). Herd-level
# rounds: the calendar title carries the year (and, for the multi-occurrence
# rounds, the month) because the exact title is the year-over-year dedupe
# key. The title key + args let clients localize the duty while ``title``
# stays the English fallback.
_CalendarRound = tuple[int, TaskCategory, str, str]
_CALENDAR_ROUNDS: tuple[_CalendarRound, ...] = (
    (
        3,
        TaskCategory.VACCINE,
        "fmd_vaccination_round",
        "FMD vaccination round ({month} {year}) — all animals; "
        "close via a bucket/batch vaccine health event",
    ),
    (
        9,
        TaskCategory.VACCINE,
        "fmd_vaccination_round",
        "FMD vaccination round ({month} {year}) — all animals; "
        "close via a bucket/batch vaccine health event",
    ),
    (
        5,
        TaskCategory.VACCINE,
        "et_hs_premonsoon_round",
        "ET + HS pre-monsoon round ({year}) — all animals",
    ),
    (11, TaskCategory.VACCINE, "goat_pox_round", "Goat Pox round ({year})"),
    (1, TaskCategory.VACCINE, "ccpp_round", "CCPP round ({year})"),
    (
        1,
        TaskCategory.DEWORMING,
        "deworming_round",
        "Deworming round ({month} {year}) — adults; kids 1–6 months every 3 months",
    ),
    (
        6,
        TaskCategory.DEWORMING,
        "deworming_round",
        "Deworming round ({month} {year}) — adults; kids 1–6 months every 3 months",
    ),
)

# (task category, lookback days, title key, title). Interval rounds fire when
# the farm has no duty of that category due inside the window (lookback days
# behind today through _INTERVAL_FORWARD_DEDUPE_DAYS ahead of it).
_IntervalRound = tuple[TaskCategory, int, str, str]
_INTERVAL_ROUNDS: tuple[_IntervalRound, ...] = (
    (
        TaskCategory.HOOF_TRIMMING,
        182,
        "hoof_trimming_round",
        "Hoof trimming round (6-monthly) — trim all ages, heel to toe",
    ),
    (
        TaskCategory.SPRAYING,
        182,
        "ectoparasite_spray_round",
        "Ectoparasite spray/dip round (Butox/deltamethrin) — never heavily pregnant does",
    ),
    (
        TaskCategory.DISINFECTION,
        91,
        "shed_disinfection_round",
        "Shed disinfection round — disinfect + lime; extra attention to kidding pens",
    ),
    (
        TaskCategory.WEIGHING,
        30,
        "monthly_weighing_round",
        "Monthly weighing round — record weights; grow-out buckets first",
    ),
    (
        TaskCategory.FAMACHA,
        30,
        "famacha_round",
        (
            "FAMACHA scoring round — check eyelid conjunctiva color (anemia); "
            "score and treat scores 4-5"
        ),
    ),
)

_DAILY_MORNING_FEED_TITLE_KEY = "morning_feed_routine"
_DAILY_MORNING_FEED_TITLE = (
    "Morning routine: sweep bunks before the 6:30 AM feeding; check and refill water troughs"
)
# Deccan summer: lactating does drink 10–15 L/day, so the troughs are checked
# on their own daily duty, not just as a line inside the feed routine.
_DAILY_WATER_CHECK_TITLE_KEY = "daily_water_check"
_DAILY_WATER_CHECK_TITLE = (
    "Water check: clean and fill all troughs morning and evening (lactating does need 10–15 L/day)"
)


def _month_bounds(reference: date) -> tuple[date, date]:
    """First and last calendar day of ``reference``'s month."""
    last_day = calendar.monthrange(reference.year, reference.month)[1]
    return reference.replace(day=1), reference.replace(day=last_day)


def _latest_occurrence(round_month: int, reference: date) -> date:
    """First day of a round series' most recent occurrence.

    Each calendar entry is a yearly series (FMD-March, FMD-September, the May
    ET+HS round, …): its latest occurrence is this year's month when it has
    already arrived, otherwise last year's.
    """
    year = reference.year if reference.month >= round_month else reference.year - 1
    return date(year, round_month, 1)


def _prefix_pattern(raw: str) -> str:
    """Literal, case-insensitive SQL prefix pattern (no wildcard injection)."""
    escaped = raw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"{escaped}%"


async def _task_exists(db: AsyncSession, farm_id: int, *conditions: ColumnElement[bool]) -> bool:
    """Bounded indexed existence probe for one dedupe key."""
    probe = (
        await db.execute(select(Task.id).where(Task.farm_id == farm_id, *conditions).limit(1))
    ).scalar_one_or_none()
    return probe is not None


async def _farm_has_active_animals(db: AsyncSession, farm_id: int) -> bool:
    probe = (
        await db.execute(
            select(Animal.id)
            .where(Animal.farm_id == farm_id, Animal.status == AnimalStatus.ACTIVE.value)
            .limit(1)
        )
    ).scalar_one_or_none()
    return probe is not None


async def _farm_earliest_introduction(db: AsyncSession, farm_id: int) -> date | None:
    """Earliest date the farm provably held animals, or ``None`` if it never did.

    One statement, two indexed scalar subqueries (the same sweep pass, no
    per-round probing): ``min(Animal.created_at)`` over every historical
    status — a since-sold or dead animal still proves stock stood — and
    ``min(PurchaseBatch.date)``, because a recorded batch can predate its
    animal rows when the animals are written asynchronously after the
    purchase. ``created_at`` is UTC while the sweep decides in the farm's
    business date; at month-round granularity that sub-day skew is noise.
    """
    first_animal_at, first_batch_on = (
        await db.execute(
            select(
                select(func.min(Animal.created_at))
                .where(Animal.farm_id == farm_id)
                .scalar_subquery()
                .label("first_animal"),
                select(func.min(PurchaseBatch.date))
                .where(PurchaseBatch.farm_id == farm_id)
                .scalar_subquery()
                .label("first_batch"),
            )
        )
    ).one()
    first_animal_on = first_animal_at.date() if first_animal_at is not None else None
    candidates = [day for day in (first_animal_on, first_batch_on) if day is not None]
    return min(candidates) if candidates else None


async def _ensure_calendar_rounds(
    db: AsyncSession, farm_id: int, reference: date, backfill_floor: date
) -> bool:
    """Seasonal vaccination/deworming rounds due in ``reference``'s month.

    Dedupe: any task of the round's category whose due date falls in the same
    calendar month and whose title carries the year suppresses creation, so a
    round appears once per (category, month, year) even across many board
    loads, and last year's differently-titled round never blocks this one.

    A round whose month passed without a sweep visit is not silently dropped:
    the series' latest occurrence is materialized late (due at that month's
    end, so it surfaces as overdue) when it is still inside the backfill
    window and the farm already had animals. Rounds older than that — or from
    before the farm's first animal was introduced — stay history; one late
    round per series is ever pending, because the probe finds the late copy
    on the next load.
    """
    created = False
    for round_month, category, title_key, title_template in _CALENDAR_ROUNDS:
        occurrence = _latest_occurrence(round_month, reference)
        is_current_month = (occurrence.year, occurrence.month) == (
            reference.year,
            reference.month,
        )
        month_start, month_end = _month_bounds(occurrence)
        if not is_current_month and month_end < backfill_floor:
            continue
        month_name = _MONTH_NAMES[occurrence.month - 1]
        year_text = str(occurrence.year)
        title = title_template.format(month=month_name, year=year_text)
        already = await _task_exists(
            db,
            farm_id,
            Task.category == category.value,
            Task.due_date >= month_start,
            Task.due_date <= month_end,
            Task.title.contains(year_text, autoescape=True),
        )
        if already:
            continue
        due = reference if is_current_month else month_end
        await _add_task(
            db,
            farm_id,
            title,
            due,
            category,
            title_key=title_key,
            title_args={"month": month_name, "year": occurrence.year, "due_date": due.isoformat()},
        )
        created = True
    return created


async def _ensure_interval_rounds(db: AsyncSession, farm_id: int, reference: date) -> bool:
    """Husbandry rounds whose cadence is a lookback window, not a calendar month.

    Dedupe: any task of the category (any status/title) due inside the window
    — the lookback ending today, extended _INTERVAL_FORWARD_DEDUPE_DAYS ahead
    — counts as the round having been scheduled, so completed history
    suppresses regeneration for the whole interval AND an operator-scheduled
    same-category round due in the coming weeks suppresses today's auto round
    instead of becoming its duplicate.
    """
    created = False
    forward_edge = reference + timedelta(days=_INTERVAL_FORWARD_DEDUPE_DAYS)
    for category, lookback_days, title_key, title in _INTERVAL_ROUNDS:
        window_start = reference - timedelta(days=lookback_days)
        already = await _task_exists(
            db,
            farm_id,
            Task.category == category.value,
            Task.due_date >= window_start,
            Task.due_date <= forward_edge,
        )
        if already:
            continue
        await _add_task(
            db,
            farm_id,
            title,
            reference,
            category,
            title_key=title_key,
            title_args={"due_date": reference.isoformat()},
        )
        created = True
    return created


async def _ensure_daily_feed_routine(db: AsyncSession, farm_id: int, reference: date) -> bool:
    """The feed-room morning routine, once per business day.

    Dedupe is any-status on the exact (title, due date). Yesterday's completed
    routine has a different due date, so it never stops today's copy — but a
    routine already completed (or skipped) today does, where a PENDING-only
    probe would re-mint a same-day duplicate on every later sweep visit.
    """
    already = await _task_exists(
        db,
        farm_id,
        Task.category == TaskCategory.FEED.value,
        Task.due_date == reference,
        Task.title == _DAILY_MORNING_FEED_TITLE,
    )
    if already:
        return False
    await _add_task(
        db,
        farm_id,
        _DAILY_MORNING_FEED_TITLE,
        reference,
        TaskCategory.FEED,
        title_key=_DAILY_MORNING_FEED_TITLE_KEY,
        title_args={"due_date": reference.isoformat()},
    )
    return True


async def _ensure_daily_water_check(db: AsyncSession, farm_id: int, reference: date) -> bool:
    """The twice-daily trough round, once per business day.

    Same any-status (title, due date) dedupe as the feed routine: today's
    completed/skipped copy must not be re-minted, yesterday's must not block
    today's.
    """
    already = await _task_exists(
        db,
        farm_id,
        Task.category == TaskCategory.WATER.value,
        Task.due_date == reference,
        Task.title == _DAILY_WATER_CHECK_TITLE,
    )
    if already:
        return False
    await _add_task(
        db,
        farm_id,
        _DAILY_WATER_CHECK_TITLE,
        reference,
        TaskCategory.WATER,
        title_key=_DAILY_WATER_CHECK_TITLE_KEY,
        title_args={"due_date": reference.isoformat()},
    )
    return True


async def _ensure_feed_reorders(db: AsyncSession, farm_id: int, reference: date) -> bool:
    """One reorder duty per ingredient stock row under its reorder level.

    Dedupe is PENDING-only on the ingredient name appearing in an open FEED
    duty's title: a pending reminder already covers the purchase, while a
    completed one means the stock was replenished (and the row should have
    left the under-level set) or is due for a fresh reminder.
    """
    under_level = (
        (
            await db.execute(
                select(FeedInventory)
                .where(
                    FeedInventory.farm_id == farm_id,
                    FeedInventory.reorder_level.is_not(None),
                    FeedInventory.qty_on_hand < FeedInventory.reorder_level,
                )
                .order_by(FeedInventory.id)
                .limit(_MAX_REORDER_INGREDIENTS)
            )
        )
        .scalars()
        .all()
    )
    created = False
    for item in under_level:
        level = item.reorder_level
        if level is None:  # pragma: no cover - excluded by the SQL filter
            continue
        title = (
            f"Reorder {item.ingredient}: {item.qty_on_hand:.0f} kg on hand "
            f"(reorder level {level:.0f} kg)"
        )
        # Prefix match on the generated "Reorder {ingredient}: " title: a bare
        # substring would let any pending FEED duty that merely mentions the
        # ingredient (the morning routine talks about bunks and water) swallow
        # the alert, and "Maize" must not suppress "Maize DDGS".
        already = await _task_exists(
            db,
            farm_id,
            Task.category == TaskCategory.FEED.value,
            Task.status == TaskStatus.PENDING.value,
            Task.title.ilike(_prefix_pattern(f"Reorder {item.ingredient}:"), escape="\\"),
        )
        if already:
            continue
        await _add_task(
            db,
            farm_id,
            title,
            reference,
            TaskCategory.FEED,
            title_key="feed_reorder",
            title_args={
                "ingredient": item.ingredient,
                "qty_on_hand": float(item.qty_on_hand),
                "reorder_level": float(level),
                "due_date": reference.isoformat(),
            },
        )
        created = True
    return created


async def _ensure_buck_rotations(db: AsyncSession, farm_id: int, reference: date) -> bool:
    """Rotate/replace reminders for aged ACTIVE bucks (inbreeding management).

    One duty per buck per 365 days (any status inside the lookback counts);
    bucks are scanned oldest-first so the bounded scan always reaches the
    rotation-relevant animals first.
    """
    rotation_floor = reference - timedelta(days=_BUCK_ROTATION_LOOKBACK_DAYS)
    rotated_rows = (
        (
            await db.execute(
                select(Task.animal_id)
                .where(
                    Task.farm_id == farm_id,
                    Task.category == TaskCategory.BUCK_ROTATION.value,
                    Task.due_date >= rotation_floor,
                    Task.animal_id.is_not(None),
                )
                .limit(_MAX_BUCK_SCAN)
            )
        )
        .scalars()
        .all()
    )
    rotated_ids = {animal_id for animal_id in rotated_rows if animal_id is not None}
    bucks = (
        (
            await db.execute(
                select(Animal)
                .where(
                    Animal.farm_id == farm_id,
                    Animal.sex == Sex.M.value,
                    Animal.status == AnimalStatus.ACTIVE.value,
                    or_(
                        Animal.date_of_birth.is_not(None),
                        Animal.estimated_dob.is_not(None),
                    ),
                )
                .order_by(func.coalesce(Animal.date_of_birth, Animal.estimated_dob), Animal.id)
                .limit(_MAX_BUCK_SCAN)
            )
        )
        .scalars()
        .all()
    )
    created = False
    for buck in bucks:
        age_months = buck.age_months_on(reference)
        if age_months is None or age_months < GOAT_PROFILE.buck_rotation_age_months:
            continue
        if buck.id in rotated_ids:
            continue
        await _add_task(
            db,
            farm_id,
            (
                f"Rotate/replace buck {buck.tag_number} — {age_months} months old "
                "(inbreeding management)"
            ),
            reference,
            TaskCategory.BUCK_ROTATION,
            animal_id=buck.id,
            title_key="buck_rotation",
            title_args={
                "tag": buck.tag_number,
                "age_months": age_months,
                "due_date": reference.isoformat(),
            },
        )
        created = True
    return created


async def ensure_cadence_tasks(db: AsyncSession, farm: Farm) -> None:
    """Bring the farm's recurring husbandry calendar onto the task board.

    Idempotent and safe to invoke repeatedly. The whole run is serialized by
    the same per-farm manual-duty queue advisory lock the recurrence path
    uses (``lock_manual_task_queue``), so two concurrent sweeps cannot
    double-create and a serialized second caller sees the winner's committed
    rows when re-probing. Every date decision uses the farm's business
    ``today``. A farm with no ACTIVE animals is a full no-op — there is
    nothing to dose, feed, weigh or rotate, and a newly created farm must
    not start life with a pile of overdue duties.

    Called from the background cadence sweep (and directly by tests). It
    commits only when it actually created rows, leaving a no-op sweep's
    transaction untouched.
    """
    await lock_manual_task_queue(db, farm)
    business_today = today(farm.timezone)
    if not await _farm_has_active_animals(db, farm.id):
        return
    # A round missed before the farm had any animals (or beyond one cycle
    # ago) is history, not a duty: late materialization never reaches past
    # this date. The floor is the farm's earliest animal-introduction fact —
    # first animal row or purchase batch — not merely farm creation: a farm
    # registered months before its first goat arrived otherwise materialized
    # every round since registration as "overdue", dosing a herd that never
    # stood. A farm with no introduction fact at all falls back to creation
    # (the active-animal gate above makes that path defensive only).
    creation_day = farm.created_at.date() if farm.created_at is not None else business_today
    earliest_stock = await _farm_earliest_introduction(db, farm.id)
    backfill_floor = max(
        business_today - timedelta(days=_BACKFALL_MAX_AGE_DAYS),
        creation_day,
        earliest_stock if earliest_stock is not None else creation_day,
    )
    created = False
    created |= await _ensure_calendar_rounds(db, farm.id, business_today, backfill_floor)
    created |= await _ensure_interval_rounds(db, farm.id, business_today)
    created |= await _ensure_daily_feed_routine(db, farm.id, business_today)
    created |= await _ensure_daily_water_check(db, farm.id, business_today)
    created |= await _ensure_feed_reorders(db, farm.id, business_today)
    created |= await _ensure_buck_rotations(db, farm.id, business_today)
    if created:
        await db.flush()
        await db.commit()


async def ensure_cadence_farm_batch(
    db: AsyncSession, *, batch_size: int, after_farm_id: int = 0
) -> tuple[int, int]:
    """Materialize one bounded keyset page of farms through the cadence sweep.

    Returns ``(farms_processed, last_farm_id)`` so the caller can page the
    whole tenant list with ``after_farm_id`` and stop on a short page. Farm
    rows are deliberately NOT locked here (a Farm row lock would add the
    inverse Farm -> Animal lock-order edge); the per-farm advisory lock
    inside ``ensure_cadence_tasks`` serializes any overlap between sweeps or
    with a concurrent recurrence run.
    """
    if not 1 <= batch_size <= 1000:
        raise ValueError("batch_size must be between 1 and 1000")
    farms = list(
        (
            await db.execute(
                select(Farm).where(Farm.id > after_farm_id).order_by(Farm.id).limit(batch_size)
            )
        )
        .scalars()
        .all()
    )
    # Snapshot ids before any per-farm rollback: rollback() expires the
    # ORM instances, and touching an expired attribute afterwards would
    # lazy-load outside the greenlet (MissingGreenlet).
    farm_ids = [farm.id for farm in farms]
    for farm, farm_id in zip(farms, farm_ids, strict=True):
        try:
            await ensure_cadence_tasks(db, farm)
        except Exception:
            # BIZ-2 (2026-09-16): one persistently failing farm must not
            # starve every higher-id farm's cadence materialization forever
            # (the whole keyset page aborted and the cursor reset each
            # interval). Isolate the failure: roll this farm's partial work
            # back, log loudly, and keep paging. The returned count stays
            # the FETCHED count so the caller's short-page detection still
            # sees the true end of the tenant list.
            await db.rollback()
            _logger.exception(
                "cadence materialization failed for farm_id=%s; skipping to "
                "the next farm (cursor still advances)",
                farm_id,
            )
    return len(farm_ids), (farm_ids[-1] if farm_ids else after_farm_id)
