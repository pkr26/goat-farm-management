"""Dashboard: herd counts by bucket, today's + overdue tasks, kiddings overdue
or due within 14 days, ultrasounds due in 7 days, cull candidates,
ready-to-move suggestions, recent weight records — plus the reports page (herd
summary, breeding performance, mortality). Read-only aggregates per farm.

Every section is filtered by the caller's effective permissions exactly as
``_shared.animal_out`` filters an animal profile: an aggregate view is not a
side door around field-level authorization.
"""

from datetime import date, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import ScalarSelect, Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload
from sqlalchemy.sql.elements import ColumnElement

from ..deps import CurrentFarm, CurrentUser, DbSession, require_perm
from ..models import (
    Animal,
    AnimalStatus,
    BreedingOutcome,
    BreedingRecord,
    BucketDefinition,
    KiddingRecord,
    KidEntry,
    KidStatus,
    Task,
    TaskCategory,
    WeightRecord,
)
from ..models.helpers import ASSESSED_OUTCOMES, CONCEIVED_OUTCOMES
from ..schemas.dashboard import (
    BreedingStatsOut,
    BucketCountOut,
    BucketReportRow,
    DashboardOut,
    MortalityOut,
    MoveSuggestionOut,
    ReportsOut,
)
from ..schemas.summaries import (
    AnimalIdentityOut,
    DashboardKiddingDueOut,
    DashboardWeightOut,
)
from ..services import actionable_pending_task_predicate, ready_to_move_suggestions, task_scope
from ..utils import today
from ._shared import task_out

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

DASHBOARD_PERM = Annotated[set[str], Depends(require_perm("dashboard.view"))]
REPORTS_PERM = Annotated[set[str], Depends(require_perm("reports.view"))]
DASHBOARD_PREVIEW_LIMIT = 100
RECENT_WEIGHTS_LIMIT = 10
KIDDING_DUE_WINDOW_DAYS = 14

DASHBOARD_TASK_LOADS = (
    joinedload(Task.assigned_role),
    joinedload(Task.assigned_user),
    joinedload(Task.animal),
)


def _animal_identity_out(animal: Animal) -> AnimalIdentityOut:
    return AnimalIdentityOut.model_validate(animal)


def _exact_total(base: Select[Any]) -> ScalarSelect[int]:
    """The preview's full-result count, computed inside the preview statement.

    ``func.count().over()`` yields the same number, but an unpartitioned window
    aggregate must drain its whole input before the LIMIT can emit anything: a
    10-row weight preview pushed every weight record the farm ever wrote
    through the WindowAgg and spilled to temp files. An uncorrelated scalar
    subquery is evaluated once, keeps the total exact and in the same round
    trip, and leaves the preview itself bounded by its own LIMIT.
    """
    return select(func.count()).select_from(base.order_by(None).subquery()).scalar_subquery()


async def _task_preview(
    db: AsyncSession,
    base: Select[tuple[Task]],
    *,
    predicates: tuple[ColumnElement[bool], ...],
    order_by: tuple[Any, ...],
) -> tuple[list[Task], int]:
    """Exact count plus a bounded, relationship-complete task preview."""
    stmt = base.where(*predicates)
    rows = (
        await db.execute(
            stmt.add_columns(_exact_total(stmt).label("preview_total"))
            .options(*DASHBOARD_TASK_LOADS)
            .order_by(*order_by)
            .limit(DASHBOARD_PREVIEW_LIMIT)
        )
    ).all()
    return [row[0] for row in rows], int(rows[0].preview_total) if rows else 0


async def _cull_preview(db: AsyncSession, farm_id: int) -> tuple[list[Animal], int]:
    base = select(Animal).where(
        Animal.farm_id == farm_id,
        Animal.cull_candidate.is_(True),
        Animal.status == AnimalStatus.ACTIVE.value,
    )
    rows = (
        await db.execute(
            base.add_columns(_exact_total(base).label("preview_total"))
            .order_by(Animal.tag_number, Animal.id)
            .limit(DASHBOARD_PREVIEW_LIMIT)
        )
    ).all()
    return [row[0] for row in rows], int(rows[0].preview_total) if rows else 0


async def _kidding_due_preview(
    db: AsyncSession, farm_id: int, reference_date: date
) -> tuple[list[DashboardKiddingDueOut], int]:
    """Kiddings already overdue plus those falling due inside the window.

    An overdue pregnancy is the more urgent of the two — nobody has recorded a
    kidding for a doe who was due — so it stays on the panel. But a single
    ``expected_kidding_date <= today + 14`` query ordered ascending gave the
    whole preview to the oldest overdue rows: a farm carrying a backlog of more
    than ``DASHBOARD_PREVIEW_LIMIT`` of them could not see a single upcoming
    kidding, which is what the panel exists for. Each side is therefore fetched
    nearest-to-today first and capped at half the preview, with whatever
    capacity one side leaves unused handed to the other; the rows themselves
    are returned in due-date order, most overdue first.
    """
    no_kidding = ~(
        select(KiddingRecord.id)
        .where(KiddingRecord.breeding_record_id == BreedingRecord.id)
        .correlate(BreedingRecord)
        .exists()
    )
    base = (
        select(
            BreedingRecord.id,
            BreedingRecord.doe_id,
            Animal.tag_number.label("doe_tag"),
            BreedingRecord.expected_kidding_date,
        )
        .join(Animal, BreedingRecord.doe_id == Animal.id)
        .where(
            BreedingRecord.farm_id == farm_id,
            BreedingRecord.outcome == BreedingOutcome.CONFIRMED_PREGNANT.value,
            # Defensive: pregnancies of sold/dead does are auto-resolved on
            # the status change, but legacy phantom rows must never list here.
            Animal.status == AnimalStatus.ACTIVE.value,
            no_kidding,
        )
    )

    async def _side(stmt: Select[Any], *order_by: Any) -> tuple[list[Any], int]:
        rows = (
            await db.execute(
                stmt.add_columns(_exact_total(stmt).label("preview_total"))
                .order_by(*order_by)
                .limit(DASHBOARD_PREVIEW_LIMIT)
            )
        ).all()
        return list(rows), int(rows[0].preview_total) if rows else 0

    overdue_rows, overdue_total = await _side(
        base.where(BreedingRecord.expected_kidding_date < reference_date),
        BreedingRecord.expected_kidding_date.desc(),
        BreedingRecord.id.desc(),
    )
    upcoming_rows, upcoming_total = await _side(
        base.where(
            BreedingRecord.expected_kidding_date >= reference_date,
            BreedingRecord.expected_kidding_date
            <= reference_date + timedelta(days=KIDDING_DUE_WINDOW_DAYS),
        ),
        BreedingRecord.expected_kidding_date,
        BreedingRecord.id,
    )
    overdue_take = min(
        len(overdue_rows),
        max(DASHBOARD_PREVIEW_LIMIT // 2, DASHBOARD_PREVIEW_LIMIT - len(upcoming_rows)),
    )
    upcoming_take = min(len(upcoming_rows), DASHBOARD_PREVIEW_LIMIT - overdue_take)
    # The overdue side was fetched newest-first to keep the freshest misses;
    # reversing restores the ascending due-date order the panel renders.
    rows = [*reversed(overdue_rows[:overdue_take]), *upcoming_rows[:upcoming_take]]
    return [
        DashboardKiddingDueOut(
            id=row.id,
            doe_id=row.doe_id,
            doe_tag=row.doe_tag,
            expected_kidding_date=row.expected_kidding_date,
        )
        for row in rows
    ], overdue_total + upcoming_total


@router.get("")
async def dashboard(
    db: DbSession, user: CurrentUser, farm: CurrentFarm, perms: DASHBOARD_PERM
) -> DashboardOut:
    """Exact herd counts plus deterministic, bounded operational previews.

    Each ``*_total`` is calculated in the same SQL statement as its list.
    Operational lists contain at most ``preview_limit`` rows; recent weights
    use ``recent_weights_limit``. The dedicated tasks, breeding and animals
    pages remain the full paginated/history views.

    Sections carrying a breeding-programme judgement — kiddings due, cull
    candidates and the suggestions derived from breeding readiness or an open
    pregnancy — need ``breeding.view``, the same permission that governs
    ``cull_candidate`` / ``is_breeding_ready`` / ``is_currently_pregnant`` in
    ``animal_out``. A caller without it gets empty lists and zero totals rather
    than a 403, so the page still renders for e.g. the cleaner preset.
    """
    defs = list(
        (await db.execute(select(BucketDefinition).order_by(BucketDefinition.sort_order))).scalars()
    )
    counts: dict[str, int] = {d.code: 0 for d in defs}
    sex_counts: dict[str, int] = {"M": 0, "F": 0}
    active_count_rows = (
        await db.execute(
            select(Animal.current_bucket, Animal.sex, func.count())
            .where(Animal.farm_id == farm.id, Animal.status == AnimalStatus.ACTIVE.value)
            .group_by(Animal.current_bucket, Animal.sex)
        )
    ).all()
    for bucket, sex, count in active_count_rows:
        counts[str(bucket)] = counts.get(str(bucket), 0) + int(count)
        sex_counts[str(sex)] = sex_counts.get(str(sex), 0) + int(count)
    total_active = sum(counts.values())

    now = today(farm.timezone)
    todays_tasks: list[Task] = []
    overdue_tasks: list[Task] = []
    ultrasounds_due: list[Task] = []
    todays_tasks_total = 0
    overdue_tasks_total = 0
    ultrasounds_due_total = 0
    if "tasks.view" in perms:
        pending = (await task_scope(db, farm, user)).where(actionable_pending_task_predicate())
        todays_tasks, todays_tasks_total = await _task_preview(
            db,
            pending,
            predicates=(Task.due_date == now,),
            order_by=(Task.id,),
        )
        overdue_tasks, overdue_tasks_total = await _task_preview(
            db,
            pending,
            predicates=(Task.due_date < now,),
            order_by=(Task.due_date, Task.id),
        )
        ultrasounds_due, ultrasounds_due_total = await _task_preview(
            db,
            pending,
            predicates=(
                Task.category == TaskCategory.ULTRASOUND.value,
                Task.due_date <= now + timedelta(days=7),
            ),
            order_by=(Task.due_date, Task.id),
        )

    can_view_breeding = "breeding.view" in perms
    kiddings_due: list[DashboardKiddingDueOut] = []
    kiddings_due_total = 0
    cull_candidates: list[Animal] = []
    # None (not 0) when the section is withheld: rendering a permission gate
    # as a factual zero would let herd decisions ride on truncated data.
    cull_candidates_total: int | None = None
    if can_view_breeding:
        kiddings_due, kiddings_due_total = await _kidding_due_preview(db, farm.id, now)
        cull_candidates, cull_candidates_total = await _cull_preview(db, farm.id)
    suggestions, suggestions_total = await ready_to_move_suggestions(
        db, farm, limit=DASHBOARD_PREVIEW_LIMIT, include_breeding=can_view_breeding
    )

    recent_weights_stmt = (
        select(WeightRecord, Animal)
        .join(Animal, WeightRecord.animal_id == Animal.id)
        .where(Animal.farm_id == farm.id)
    )
    recent_weight_rows = (
        await db.execute(
            recent_weights_stmt.add_columns(
                _exact_total(recent_weights_stmt).label("preview_total")
            )
            .order_by(WeightRecord.date.desc(), WeightRecord.id.desc())
            .limit(RECENT_WEIGHTS_LIMIT)
        )
    ).all()
    recent_weights_total = int(recent_weight_rows[0].preview_total) if recent_weight_rows else 0
    status_rows = (
        await db.execute(
            select(Animal.status, func.count())
            .where(Animal.farm_id == farm.id)
            .group_by(Animal.status)
            # A bare GROUP BY emits rows in hash-table order; the response is a
            # dict the UI iterates verbatim, so pin the row order.
            .order_by(Animal.status)
        )
    ).all()
    status_totals = {str(status): int(count) for status, count in status_rows}
    can_view_health = "health.view" in perms

    return DashboardOut(
        buckets=[
            BucketCountOut(code=d.code, name=d.name, count=counts.get(d.code, 0)) for d in defs
        ],
        total_active=total_active,
        sex_counts=sex_counts,
        status_totals=status_totals,
        todays_tasks=[task_out(t) for t in todays_tasks],
        todays_tasks_total=todays_tasks_total,
        overdue_tasks=[task_out(t) for t in overdue_tasks],
        overdue_tasks_total=overdue_tasks_total,
        ultrasounds_due=[task_out(t) for t in ultrasounds_due],
        ultrasounds_due_total=ultrasounds_due_total,
        kiddings_due=kiddings_due,
        kiddings_due_total=int(kiddings_due_total),
        cull_candidates=[_animal_identity_out(a) for a in cull_candidates],
        cull_candidates_total=cull_candidates_total,
        suggestions=[MoveSuggestionOut.model_validate(s) for s in suggestions],
        suggestions_total=suggestions_total,
        recent_weights=[
            DashboardWeightOut(
                id=row[0].id,
                date=row[0].date,
                weight_kg=row[0].weight_kg,
                bcs=row[0].bcs,
                animal=_animal_identity_out(row[1]),
                notes=(row[0].notes if "animals.view" in perms and can_view_health else None),
            )
            for row in recent_weight_rows
        ],
        recent_weights_total=int(recent_weights_total),
        preview_limit=DASHBOARD_PREVIEW_LIMIT,
        recent_weights_limit=RECENT_WEIGHTS_LIMIT,
    )


@router.get("/reports")
async def reports(db: DbSession, farm: CurrentFarm, perms: REPORTS_PERM) -> ReportsOut:
    """Herd summary, breeding performance, mortality — all aggregated in SQL;
    only a 100-row purpose-specific cull preview is hydrated as ORM rows and
    its exact count is returned separately."""

    # --- herd summary -------------------------------------------------------
    defs = list(
        (await db.execute(select(BucketDefinition).order_by(BucketDefinition.sort_order))).scalars()
    )
    # Latest weight record per animal (date, then id — same key as
    # Animal.latest_weight), falling back to birth_weight like the property.
    latest_weight = (
        select(WeightRecord.weight_kg)
        .where(WeightRecord.animal_id == Animal.id)
        .order_by(WeightRecord.date.desc(), WeightRecord.id.desc())
        # Scalar subquery must return at most one row per animal — without the
        # LIMIT, a second weight record raises CardinalityViolationError (500).
        .limit(1)
        .correlate(Animal)
        .scalar_subquery()
    )
    effective_weight = func.coalesce(latest_weight, Animal.birth_weight)
    bucket_stats = (
        await db.execute(
            select(
                Animal.current_bucket,
                func.count(),
                # avg over animals with a usable weight only (the old Python
                # version skipped None and 0.0 weights).
                func.avg(effective_weight).filter(
                    effective_weight.is_not(None), effective_weight != 0
                ),
            )
            .where(Animal.farm_id == farm.id, Animal.status == AnimalStatus.ACTIVE.value)
            .group_by(Animal.current_bucket)
        )
    ).all()
    per_bucket = {code: (int(count), avg) for code, count, avg in bucket_stats}
    bucket_rows: list[BucketReportRow] = []
    for d in defs:
        count, avg = per_bucket.get(d.code, (0, None))
        bucket_rows.append(
            BucketReportRow(
                name=d.name,
                code=d.code,
                count=count,
                avg_weight=round(float(avg), 1) if avg is not None else None,
            )
        )
    total_active = sum(count for count, _ in per_bucket.values())

    status_rows = (
        await db.execute(
            select(Animal.status, func.count())
            .where(Animal.farm_id == farm.id)
            .group_by(Animal.status)
            # Deterministic row order: the reports page renders these entries
            # in response order, and a bare GROUP BY does not specify one.
            .order_by(Animal.status)
        )
    ).all()
    status_counts = {str(status): int(count) for status, count in status_rows}
    sex_rows = (
        await db.execute(
            select(Animal.sex, func.count())
            .where(Animal.farm_id == farm.id, Animal.status == AnimalStatus.ACTIVE.value)
            .group_by(Animal.sex)
        )
    ).all()
    sex_counts: dict[str, int] = {"M": 0, "F": 0}
    for sex, count in sex_rows:
        sex_counts[str(sex)] = int(count)

    # --- breeding performance ----------------------------------------------
    # Mirrors models.helpers.conception_rate — same predicates, same result.
    completed = BreedingRecord.outcome.in_(sorted(ASSESSED_OUTCOMES))
    conceived = BreedingRecord.outcome.in_(sorted(CONCEIVED_OUTCOMES))
    first_cycle = BreedingRecord.heat_cycle_number == 1
    (
        total_records,
        completed_count,
        conceived_count,
        fc_completed,
        fc_conceived,
    ) = (
        await db.execute(
            select(
                func.count(),
                func.count().filter(completed),
                func.count().filter(conceived),
                func.count().filter(completed, first_cycle),
                func.count().filter(conceived, first_cycle),
            ).where(BreedingRecord.farm_id == farm.id)
        )
    ).one()

    def _rate(num: int, den: int) -> float | None:
        return round(100.0 * num / den, 1) if den else None

    # Per-kidding alive counts (kiddings with zero kid entries still count).
    alive_counts = (
        select(
            KiddingRecord.id.label("kidding_id"),
            func.count(KidEntry.id).filter(KidEntry.status == KidStatus.ALIVE.value).label("alive"),
        )
        .outerjoin(KidEntry, KidEntry.kidding_record_id == KiddingRecord.id)
        .where(KiddingRecord.farm_id == farm.id)
        .group_by(KiddingRecord.id)
        .subquery()
    )
    kiddings_count, multi_kid, total_alive = (
        await db.execute(
            select(
                func.count(),
                func.count().filter(alive_counts.c.alive >= 2),
                func.coalesce(func.sum(alive_counts.c.alive), 0),
            ).select_from(alive_counts)
        )
    ).one()

    cull_candidates: list[Animal] = []
    # None (not 0) when withheld — see the dashboard endpoint's twin comment.
    cull_candidates_total: int | None = None
    if "breeding.view" in perms:
        cull_candidates, cull_candidates_total = await _cull_preview(db, farm.id)

    breeding_stats = BreedingStatsOut(
        total_records=total_records,
        conception_rate=_rate(conceived_count, completed_count),
        first_cycle_rate=_rate(fc_conceived, fc_completed),
        kiddings=kiddings_count,
        kids_per_kidding=(
            round(float(total_alive) / kiddings_count, 2) if kiddings_count else None
        ),
        twin_rate=_rate(multi_kid, kiddings_count),
        cull_candidates=[_animal_identity_out(a) for a in cull_candidates],
        cull_candidates_total=cull_candidates_total,
        cull_candidates_limit=DASHBOARD_PREVIEW_LIMIT,
    )

    # --- mortality ------------------------------------------------------------
    month_col = func.to_char(Animal.status_date, "YYYY-MM")
    death_rows = (
        await db.execute(
            select(month_col, func.count())
            .where(
                Animal.farm_id == farm.id,
                Animal.status == AnimalStatus.DEAD.value,
                Animal.status_date.is_not(None),
            )
            .group_by(month_col)
        )
    ).all()
    deaths_by_month = sorted(
        ((str(month), int(count)) for month, count in death_rows), reverse=True
    )
    total_kids, stillborn = (
        await db.execute(
            select(
                func.count(KidEntry.id),
                func.count(KidEntry.id).filter(KidEntry.status == KidStatus.STILLBORN.value),
            )
            .join(KiddingRecord, KidEntry.kidding_record_id == KiddingRecord.id)
            .where(KiddingRecord.farm_id == farm.id)
        )
    ).one()

    mortality = MortalityOut(
        total_deaths=status_counts.get(AnimalStatus.DEAD.value, 0),
        deaths_by_month=deaths_by_month,
        total_kids_born=total_kids,
        stillborn=stillborn,
        stillborn_rate=_rate(stillborn, total_kids),
    )

    return ReportsOut(
        bucket_rows=bucket_rows,
        total_active=total_active,
        sex_counts=sex_counts,
        status_counts=status_counts,
        breeding=breeding_stats,
        mortality=mortality,
    )
