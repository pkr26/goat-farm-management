"""Dashboard: herd counts by bucket, today's + overdue tasks, kiddings due in
14 days, ultrasounds due in 7 days, cull candidates, ready-to-move suggestions,
recent weight records — plus the reports page (herd summary, breeding
performance, mortality). Read-only aggregates per farm."""

from collections import defaultdict
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
    conception_rate,
)
from ..schemas.animals import AnimalOut, WeightRecordOut
from ..schemas.breeding import BreedingRecordOut
from ..schemas.dashboard import (
    BreedingStatsOut,
    BucketCountOut,
    BucketReportRow,
    DashboardOut,
    MortalityOut,
    MoveSuggestionOut,
    ReportsOut,
)
from ..schemas.tasks import TaskOut
from ..services import ready_to_move_suggestions, task_scope
from ..utils import today

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

DASHBOARD_PERM = Annotated[set[str], Depends(require_perm("dashboard.view"))]
REPORTS_PERM = Annotated[set[str], Depends(require_perm("reports.view"))]


def _task_action_url(task: Task) -> str | None:
    """Where a task links when completing it means filling in a form."""
    if task.category == TaskCategory.ULTRASOUND.value and task.breeding_record_id:
        return f"/breeding/{task.breeding_record_id}/ultrasound"
    if task.category == TaskCategory.KIDDING_DUE.value and task.breeding_record_id:
        return f"/kidding/new?breeding_id={task.breeding_record_id}"
    if task.category in (TaskCategory.VACCINE.value, TaskCategory.DEWORMING.value):
        params = f"task_id={task.id}"
        if task.animal_id:
            params += f"&animal_id={task.animal_id}"
        if task.purchase_batch_id:
            params += f"&purchase_batch_id={task.purchase_batch_id}"
        return f"/health/new?{params}"
    return None


def _task_out(task: Task) -> TaskOut:
    out = TaskOut.model_validate(task)
    out.action_url = _task_action_url(task)
    return out


def _breeding_out(record: BreedingRecord) -> BreedingRecordOut:
    out = BreedingRecordOut.model_validate(record)
    out.has_kidding = record.kidding_record is not None
    out.doe_tag = record.doe.tag_number
    return out


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
                select(Animal).where(
                    Animal.farm_id == farm.id, Animal.status == AnimalStatus.ACTIVE.value
                )
            )
        ).scalars()
    )
    counts: dict[str, int] = {d.code: 0 for d in defs}
    sex_counts: dict[str, int] = {"M": 0, "F": 0}
    for animal in active_animals:
        counts[animal.current_bucket] = counts.get(animal.current_bucket, 0) + 1
        sex_counts[animal.sex] = sex_counts.get(animal.sex, 0) + 1

    now = today()
    pending = (await task_scope(db, farm, user)).where(Task.status == TaskStatus.PENDING.value)
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
        .options(selectinload(BreedingRecord.doe))
        .where(
            BreedingRecord.farm_id == farm.id,
            BreedingRecord.outcome == BreedingOutcome.CONFIRMED_PREGNANT.value,
            BreedingRecord.expected_kidding_date <= now + timedelta(days=14),
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
                .where(
                    Animal.farm_id == farm.id,
                    Animal.cull_candidate.is_(True),
                    Animal.status == AnimalStatus.ACTIVE.value,
                )
                .order_by(Animal.tag_number)
            )
        ).scalars()
    )
    suggestions = await ready_to_move_suggestions(db, farm)

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
        todays_tasks=[_task_out(t) for t in todays_tasks],
        overdue_tasks=[_task_out(t) for t in overdue_tasks],
        ultrasounds_due=[_task_out(t) for t in ultrasounds_due],
        kiddings_due=[_breeding_out(r) for r in kiddings_due],
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
    animals = list((await db.execute(select(Animal).where(Animal.farm_id == farm.id))).scalars())
    active = [a for a in animals if a.status == AnimalStatus.ACTIVE.value]

    # --- herd summary -------------------------------------------------------
    defs = list(
        (await db.execute(select(BucketDefinition).order_by(BucketDefinition.sort_order))).scalars()
    )
    bucket_rows: list[BucketReportRow] = []
    for d in defs:
        members = [a for a in active if a.current_bucket == d.code]
        weights = [w for a in members if (w := a.latest_weight_kg)]
        bucket_rows.append(
            BucketReportRow(
                name=d.name,
                code=d.code,
                count=len(members),
                avg_weight=round(sum(weights) / len(weights), 1) if weights else None,
            )
        )
    sex_counts: dict[str, int] = {"M": 0, "F": 0}
    status_counts: dict[str, int] = defaultdict(int)
    for a in animals:
        status_counts[a.status] += 1
        if a.status == AnimalStatus.ACTIVE.value:
            sex_counts[a.sex] = sex_counts.get(a.sex, 0) + 1

    # --- breeding performance ----------------------------------------------
    records = list(
        (
            await db.execute(select(BreedingRecord).where(BreedingRecord.farm_id == farm.id))
        ).scalars()
    )
    first_cycle = [r for r in records if r.heat_cycle_number == 1]
    kiddings = list(
        (
            await db.execute(
                select(KiddingRecord)
                .options(selectinload(KiddingRecord.kids))
                .where(KiddingRecord.farm_id == farm.id)
            )
        ).scalars()
    )
    kid_entries = list(
        (
            await db.execute(
                select(KidEntry)
                .join(KiddingRecord, KidEntry.kidding_record_id == KiddingRecord.id)
                .where(KiddingRecord.farm_id == farm.id)
            )
        ).scalars()
    )
    alive_per_kidding = [
        sum(1 for e in k.kids if e.status == KidStatus.ALIVE.value) for k in kiddings
    ]
    multi_kid = sum(1 for n in alive_per_kidding if n >= 2)
    cull_candidates = [a for a in active if a.cull_candidate]

    breeding_stats = BreedingStatsOut(
        total_records=len(records),
        conception_rate=conception_rate(records),
        first_cycle_rate=conception_rate(first_cycle),
        kiddings=len(kiddings),
        kids_per_kidding=(
            round(sum(alive_per_kidding) / len(alive_per_kidding), 2) if alive_per_kidding else None
        ),
        twin_rate=round(100.0 * multi_kid / len(kiddings), 1) if kiddings else None,
        cull_candidates=[AnimalOut.model_validate(a) for a in cull_candidates],
    )

    # --- mortality ------------------------------------------------------------
    deaths_by_month: dict[str, int] = defaultdict(int)
    for a in animals:
        if a.status == AnimalStatus.DEAD.value and a.status_date:
            deaths_by_month[a.status_date.strftime("%Y-%m")] += 1
    total_kids = len(kid_entries)
    stillborn = sum(1 for e in kid_entries if e.status == KidStatus.STILLBORN.value)

    mortality = MortalityOut(
        total_deaths=status_counts.get(AnimalStatus.DEAD.value, 0),
        deaths_by_month=sorted(deaths_by_month.items(), reverse=True),
        total_kids_born=total_kids,
        stillborn=stillborn,
        stillborn_rate=round(100.0 * stillborn / total_kids, 1) if total_kids else None,
    )

    return ReportsOut(
        bucket_rows=bucket_rows,
        total_active=len(active),
        sex_counts=sex_counts,
        status_counts=dict(status_counts),
        breeding=breeding_stats,
        mortality=mortality,
    )
