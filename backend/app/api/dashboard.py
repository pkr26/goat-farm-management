"""Dashboard: herd counts by bucket, today's + overdue tasks, kiddings due in
14 days, ultrasounds due in 7 days, cull candidates, ready-to-move suggestions,
recent weight records — plus the reports page (herd summary, breeding
performance, mortality). Read-only aggregates per farm."""

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

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
    TaskStatus,
    WeightRecord,
)
from ..schemas.animals import AnimalOut, WeightRecordOut
from ..schemas.dashboard import (
    BreedingStatsOut,
    BucketCountOut,
    BucketReportRow,
    DashboardOut,
    MortalityOut,
    MoveSuggestionOut,
    ReportsOut,
)
from ..services import ANIMAL_OUT_LOADS, ready_to_move_suggestions, task_scope
from ..utils import today
from ._shared import TASK_LOADS, breeding_out, task_out

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

DASHBOARD_PERM = Annotated[set[str], Depends(require_perm("dashboard.view"))]
REPORTS_PERM = Annotated[set[str], Depends(require_perm("reports.view"))]


@router.get("")
async def dashboard(
    db: DbSession, user: CurrentUser, farm: CurrentFarm, perms: DASHBOARD_PERM
) -> DashboardOut:
    defs = list(
        (await db.execute(select(BucketDefinition).order_by(BucketDefinition.sort_order))).scalars()
    )
    active_animals = list(
        (
            await db.execute(
                select(Animal)
                # Counts reuse these rows for ready_to_move_suggestions and
                # AnimalOut serialization — both read the history collections.
                .options(*ANIMAL_OUT_LOADS)
                .where(Animal.farm_id == farm.id, Animal.status == AnimalStatus.ACTIVE.value)
            )
        ).scalars()
    )
    counts: dict[str, int] = {d.code: 0 for d in defs}
    sex_counts: dict[str, int] = {"M": 0, "F": 0}
    for animal in active_animals:
        counts[animal.current_bucket] = counts.get(animal.current_bucket, 0) + 1
        sex_counts[animal.sex] = sex_counts.get(animal.sex, 0) + 1

    now = today()
    pending = (
        (await task_scope(db, farm, user))
        .options(*TASK_LOADS)
        .where(Task.status == TaskStatus.PENDING.value)
    )
    todays_tasks = list(
        (await db.execute(pending.where(Task.due_date == now).order_by(Task.id))).scalars()
    )
    overdue_tasks = list(
        (await db.execute(pending.where(Task.due_date < now).order_by(Task.due_date))).scalars()
    )
    ultrasounds_due = list(
        (
            await db.execute(
                pending.where(
                    Task.category == TaskCategory.ULTRASOUND.value,
                    Task.due_date <= now + timedelta(days=7),
                ).order_by(Task.due_date)
            )
        ).scalars()
    )

    kiddings_stmt = (
        select(BreedingRecord)
        .options(
            selectinload(BreedingRecord.doe),
            selectinload(BreedingRecord.buck),
            selectinload(BreedingRecord.kidding_record),
        )
        .join(Animal, BreedingRecord.doe_id == Animal.id)
        .where(
            BreedingRecord.farm_id == farm.id,
            BreedingRecord.outcome == BreedingOutcome.CONFIRMED_PREGNANT.value,
            BreedingRecord.expected_kidding_date <= now + timedelta(days=14),
            # Defensive: pregnancies of sold/dead does are auto-resolved on
            # the status change, but legacy phantom rows must never list here.
            Animal.status == AnimalStatus.ACTIVE.value,
        )
        .order_by(BreedingRecord.expected_kidding_date)
    )
    kiddings_due = [
        r for r in (await db.execute(kiddings_stmt)).scalars() if r.kidding_record is None
    ]

    cull_candidates = list(
        (
            await db.execute(
                select(Animal)
                .options(*ANIMAL_OUT_LOADS)
                .where(
                    Animal.farm_id == farm.id,
                    Animal.cull_candidate.is_(True),
                    Animal.status == AnimalStatus.ACTIVE.value,
                )
                .order_by(Animal.tag_number)
            )
        ).scalars()
    )
    suggestions = await ready_to_move_suggestions(db, farm, active_animals)

    recent_weights = list(
        (
            await db.execute(
                select(WeightRecord)
                .join(Animal, WeightRecord.animal_id == Animal.id)
                .where(Animal.farm_id == farm.id)
                .order_by(WeightRecord.date.desc(), WeightRecord.id.desc())
                .limit(10)
            )
        ).scalars()
    )
    status_rows = (
        await db.execute(
            select(Animal.status, func.count())
            .where(Animal.farm_id == farm.id)
            .group_by(Animal.status)
        )
    ).all()
    status_totals = {str(status): int(count) for status, count in status_rows}

    return DashboardOut(
        buckets=[
            BucketCountOut(code=d.code, name=d.name, count=counts.get(d.code, 0)) for d in defs
        ],
        total_active=len(active_animals),
        sex_counts=sex_counts,
        status_totals=status_totals,
        todays_tasks=[task_out(t) for t in todays_tasks],
        overdue_tasks=[task_out(t) for t in overdue_tasks],
        ultrasounds_due=[task_out(t) for t in ultrasounds_due],
        kiddings_due=[breeding_out(r) for r in kiddings_due],
        cull_candidates=[AnimalOut.model_validate(a) for a in cull_candidates],
        suggestions=[
            MoveSuggestionOut(
                animal=AnimalOut.model_validate(s["animal"]), to=s["to"], reason=s["reason"]
            )
            for s in suggestions
        ],
        recent_weights=[WeightRecordOut.model_validate(w) for w in recent_weights],
    )


@router.get("/reports")
async def reports(db: DbSession, farm: CurrentFarm, perms: REPORTS_PERM) -> ReportsOut:
    """Herd summary, breeding performance, mortality — all aggregated in SQL
    (AUDIT 5-H2); only the cull-candidate list is hydrated as ORM rows."""

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

    cull_candidates = list(
        (
            await db.execute(
                select(Animal)
                .options(*ANIMAL_OUT_LOADS)
                .where(
                    Animal.farm_id == farm.id,
                    Animal.cull_candidate.is_(True),
                    Animal.status == AnimalStatus.ACTIVE.value,
                )
                .order_by(Animal.tag_number)
            )
        ).scalars()
    )

    breeding_stats = BreedingStatsOut(
        total_records=total_records,
        conception_rate=_rate(confirmed_count, completed_count),
        first_cycle_rate=_rate(fc_confirmed, fc_completed),
        kiddings=kiddings_count,
        kids_per_kidding=(
            round(float(total_alive) / kiddings_count, 2) if kiddings_count else None
        ),
        twin_rate=_rate(multi_kid, kiddings_count),
        cull_candidates=[AnimalOut.model_validate(a) for a in cull_candidates],
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
