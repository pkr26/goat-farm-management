"""Recurring husbandry cadence materialized onto the task board.

The vaccination/deworming schedule computed from ``VaccineTemplates`` is an
animal-level read model; the seasonal husbandry calendar (FMD rounds,
pre-monsoon ET+HS, deworming sweeps, hoof trimming, spraying, shed
disinfection, weighing, feed-room routine and buck rotation) is farm-level
operational work. This module turns that calendar into actual duties the
morning of the day they are due, idempotently, on task-board loads.
"""

import calendar
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
    Sex,
    Task,
    TaskCategory,
    TaskStatus,
)
from ..utils import today
from ._common import _add_task
from .tasks import lock_manual_task_queue

# Board-load safety bounds: every query this module runs is either an indexed
# LIMIT-1 existence probe or one bounded scan, because ensure_cadence_tasks
# executes on the hot GET /api/tasks path.
_MAX_REORDER_INGREDIENTS = 100
_MAX_BUCK_SCAN = 500
_BUCK_ROTATION_LOOKBACK_DAYS = 365

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

# (calendar month, task category, title template). Herd-level rounds: the
# calendar title carries the year (and, for the multi-occurrence rounds, the
# month) because the exact title is the year-over-year dedupe key.
_CalendarRound = tuple[int, TaskCategory, str]
_CALENDAR_ROUNDS: tuple[_CalendarRound, ...] = (
    (
        3,
        TaskCategory.VACCINE,
        "FMD vaccination round ({month} {year}) — all animals; "
        "close via a bucket/batch vaccine health event",
    ),
    (
        9,
        TaskCategory.VACCINE,
        "FMD vaccination round ({month} {year}) — all animals; "
        "close via a bucket/batch vaccine health event",
    ),
    (5, TaskCategory.VACCINE, "ET + HS pre-monsoon round ({year}) — all animals"),
    (11, TaskCategory.VACCINE, "Goat Pox round ({year})"),
    (1, TaskCategory.VACCINE, "CCPP round ({year})"),
    (
        1,
        TaskCategory.DEWORMING,
        "Deworming round ({month} {year}) — adults; kids 1–6 months every 3 months",
    ),
    (
        6,
        TaskCategory.DEWORMING,
        "Deworming round ({month} {year}) — adults; kids 1–6 months every 3 months",
    ),
)

# (task category, lookback days, title). Interval rounds fire when the farm
# has no duty of that category due inside the lookback window ending today.
_IntervalRound = tuple[TaskCategory, int, str]
_INTERVAL_ROUNDS: tuple[_IntervalRound, ...] = (
    (
        TaskCategory.HOOF_TRIMMING,
        182,
        "Hoof trimming round (6-monthly) — trim all ages, heel to toe",
    ),
    (
        TaskCategory.SPRAYING,
        182,
        "Ectoparasite spray/dip round (Butox/deltamethrin) — never heavily pregnant does",
    ),
    (
        TaskCategory.DISINFECTION,
        122,
        "Shed disinfection round — disinfect + lime; extra attention to kidding pens",
    ),
    (TaskCategory.WEIGHING, 45, "Monthly weighing round — record weights; grow-out buckets first"),
)

_DAILY_MORNING_FEED_TITLE = (
    "Morning routine: sweep bunks before the 6:30 AM feeding; check and refill water troughs"
)


def _month_bounds(reference: date) -> tuple[date, date]:
    """First and last calendar day of ``reference``'s month."""
    last_day = calendar.monthrange(reference.year, reference.month)[1]
    return reference.replace(day=1), reference.replace(day=last_day)


def _contains_pattern(raw: str) -> str:
    """Literal, case-insensitive SQL substring pattern (no wildcard injection)."""
    escaped = raw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


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


async def _ensure_calendar_rounds(db: AsyncSession, farm_id: int, reference: date) -> bool:
    """Seasonal vaccination/deworming rounds due in ``reference``'s month.

    Dedupe: any task of the round's category whose due date falls in the same
    calendar month and whose title carries the year suppresses creation, so a
    round appears once per (category, month, year) even across many board
    loads, and last year's differently-titled round never blocks this one.
    """
    month_start, month_end = _month_bounds(reference)
    month_name = _MONTH_NAMES[reference.month - 1]
    year_text = str(reference.year)
    created = False
    for round_month, category, title_template in _CALENDAR_ROUNDS:
        if round_month != reference.month:
            continue
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
        await _add_task(db, farm_id, title, reference, category)
        created = True
    return created


async def _ensure_interval_rounds(db: AsyncSession, farm_id: int, reference: date) -> bool:
    """Husbandry rounds whose cadence is a lookback window, not a calendar month.

    Dedupe: any task of the category (any status/title) due inside the window
    ending today counts as the round having been scheduled, so completed
    history suppresses regeneration for the whole interval.
    """
    created = False
    for category, lookback_days, title in _INTERVAL_ROUNDS:
        window_start = reference - timedelta(days=lookback_days)
        already = await _task_exists(
            db,
            farm_id,
            Task.category == category.value,
            Task.due_date >= window_start,
            Task.due_date <= reference,
        )
        if already:
            continue
        await _add_task(db, farm_id, title, reference, category)
        created = True
    return created


async def _ensure_daily_feed_routine(db: AsyncSession, farm_id: int, reference: date) -> bool:
    """The feed-room morning routine, once per business day.

    Dedupe is deliberately PENDING-only on the exact (title, due date): a
    completed routine is yesterday's history and must not stop today's copy.
    """
    already = await _task_exists(
        db,
        farm_id,
        Task.category == TaskCategory.FEED.value,
        Task.status == TaskStatus.PENDING.value,
        Task.due_date == reference,
        Task.title == _DAILY_MORNING_FEED_TITLE,
    )
    if already:
        return False
    await _add_task(db, farm_id, _DAILY_MORNING_FEED_TITLE, reference, TaskCategory.FEED)
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
        already = await _task_exists(
            db,
            farm_id,
            Task.category == TaskCategory.FEED.value,
            Task.status == TaskStatus.PENDING.value,
            Task.title.ilike(_contains_pattern(item.ingredient), escape="\\"),
        )
        if already:
            continue
        await _add_task(db, farm_id, title, reference, TaskCategory.FEED)
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
        )
        created = True
    return created


async def ensure_cadence_tasks(db: AsyncSession, farm: Farm) -> None:
    """Bring the farm's recurring husbandry calendar onto the task board.

    Idempotent and safe to invoke on every task-board load. The whole run is
    serialized by the same per-farm manual-duty queue advisory lock the
    recurrence path uses (``lock_manual_task_queue``), so two concurrent board
    loads cannot double-create and a serialized second caller sees the
    winner's committed rows when re-probing. Every date decision uses the
    farm's business ``today``. A farm with no ACTIVE animals is a full no-op —
    there is nothing to dose, feed, weigh or rotate, and a newly created farm
    must not start life with a pile of overdue duties.

    This is the one service that commits on a read path: it is hooked at the
    top of the task-board list endpoint, which owns no other commit, so a
    flush-only implementation would silently discard the duties it created at
    session close. It commits only when it actually created rows, leaving a
    no-op load's transaction untouched.
    """
    await lock_manual_task_queue(db, farm)
    business_today = today(farm.timezone)
    if not await _farm_has_active_animals(db, farm.id):
        return
    created = False
    created |= await _ensure_calendar_rounds(db, farm.id, business_today)
    created |= await _ensure_interval_rounds(db, farm.id, business_today)
    created |= await _ensure_daily_feed_routine(db, farm.id, business_today)
    created |= await _ensure_feed_reorders(db, farm.id, business_today)
    created |= await _ensure_buck_rotations(db, farm.id, business_today)
    if created:
        await db.flush()
        await db.commit()
