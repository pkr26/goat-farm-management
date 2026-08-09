"""Dashboard: herd counts by bucket, today's + overdue tasks, kiddings due in
14 days, ultrasounds due in 7 days, cull candidates, ready-to-move suggestions,
recent weight records — plus the reports page (herd summary, breeding
performance, mortality). Read-only aggregates per farm."""

from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import Select, func, select
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

DASHBOARD_TASK_LOADS = (
    joinedload(Task.assigned_role),
    joinedload(Task.assigned_user),
    joinedload(Task.animal),
)


def _animal_identity_out(animal: Animal) -> AnimalIdentityOut:
    return AnimalIdentityOut.model_validate(animal)


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
            stmt.add_columns(func.count().over().label("preview_total"))
            .options(*DASHBOARD_TASK_LOADS)
            .order_by(*order_by)
            .limit(DASHBOARD_PREVIEW_LIMIT)
        )
    ).all()
    return [row[0] for row in rows], int(rows[0].preview_total) if rows else 0


async def _cull_preview(db: AsyncSession, farm_id: int) -> tuple[list[Animal], int]:
    stmt = select(Animal, func.count().over().label("preview_total")).where(
        Animal.farm_id == farm_id,
        Animal.cull_candidate.is_(True),
        Animal.status == AnimalStatus.ACTIVE.value,
    )
    rows = (
        await db.execute(stmt.order_by(Animal.tag_number, Animal.id).limit(DASHBOARD_PREVIEW_LIMIT))
    ).all()
    return [row[0] for row in rows], int(rows[0].preview_total) if rows else 0


@router.get("")
async def dashboard(
    db: DbSession, user: CurrentUser, farm: CurrentFarm, perms: DASHBOARD_PERM
) -> DashboardOut:
    """Exact herd counts plus deterministic, bounded operational previews.

    Each ``*_total`` is calculated in the same SQL statement as its list.
    Operational lists contain at most ``preview_limit`` rows; recent weights
    use ``recent_weights_limit``. The dedicated tasks, breeding and animals
    pages remain the full paginated/history views.
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

    no_kidding = ~(
        select(KiddingRecord.id)
        .where(KiddingRecord.breeding_record_id == BreedingRecord.id)
        .correlate(BreedingRecord)
        .exists()
    )
    kiddings_stmt = (
        select(
            BreedingRecord.id,
            BreedingRecord.doe_id,
            Animal.tag_number.label("doe_tag"),
            BreedingRecord.expected_kidding_date,
        )
        .join(Animal, BreedingRecord.doe_id == Animal.id)
        .where(
            BreedingRecord.farm_id == farm.id,
            BreedingRecord.outcome == BreedingOutcome.CONFIRMED_PREGNANT.value,
            BreedingRecord.expected_kidding_date <= now + timedelta(days=14),
            # Defensive: pregnancies of sold/dead does are auto-resolved on
            # the status change, but legacy phantom rows must never list here.
            Animal.status == AnimalStatus.ACTIVE.value,
            no_kidding,
        )
    )
    kidding_rows = (
        await db.execute(
            kiddings_stmt.add_columns(func.count().over().label("preview_total"))
            .order_by(BreedingRecord.expected_kidding_date, BreedingRecord.id)
            .limit(DASHBOARD_PREVIEW_LIMIT)
        )
    ).all()
    kiddings_due = [
        DashboardKiddingDueOut(
            id=row.id,
            doe_id=row.doe_id,
            doe_tag=row.doe_tag,
            expected_kidding_date=row.expected_kidding_date,
        )
        for row in kidding_rows
    ]
    kiddings_due_total = int(kidding_rows[0].preview_total) if kidding_rows else 0
    cull_candidates, cull_candidates_total = await _cull_preview(db, farm.id)
    suggestions, suggestions_total = await ready_to_move_suggestions(
        db, farm, limit=DASHBOARD_PREVIEW_LIMIT
    )

    recent_weights_stmt = (
        select(WeightRecord, Animal)
        .join(Animal, WeightRecord.animal_id == Animal.id)
        .where(Animal.farm_id == farm.id)
    )
    recent_weight_rows = (
        await db.execute(
            recent_weights_stmt.add_columns(func.count().over().label("preview_total"))
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
async def reports(db: DbSession, farm: CurrentFarm, _perms: REPORTS_PERM) -> ReportsOut:
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
    completed = BreedingRecord.outcome != BreedingOutcome.PENDING.value
    confirmed = BreedingRecord.outcome == BreedingOutcome.CONFIRMED_PREGNANT.value
    first_cycle = BreedingRecord.heat_cycle_number == 1
    (
        total_records,
        completed_count,
        confirmed_count,
        fc_completed,
        fc_confirmed,
    ) = (
        await db.execute(
            select(
                func.count(),
                func.count().filter(completed),
                func.count().filter(confirmed),
                func.count().filter(completed, first_cycle),
                func.count().filter(confirmed, first_cycle),
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

    cull_candidates, cull_candidates_total = await _cull_preview(db, farm.id)

    breeding_stats = BreedingStatsOut(
        total_records=total_records,
        conception_rate=_rate(confirmed_count, completed_count),
        first_cycle_rate=_rate(fc_confirmed, fc_completed),
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
