"""Cross-farm owner overview and benchmarks (ITEM 3, 2026-09-21 playbook).

One screen across every farm the caller OWNS (`Farm.owner_id`), so a 20-farm
owner stops switching farm context 20 times. Two endpoints:

- ``GET /api/owner/overview`` — per-farm attention headlines (overdue duties,
  today's duty progress, kidding watch, holds, screening flags, month P&L).
- ``GET /api/owner/benchmarks?days=90`` — per-farm performance figures
  (conception, kid mortality, daily gain, feed cost per kg gained, profit per
  animal sold) for ranking farms against each other.

Access is ownership, not a permission string: the caller must own at least
one farm (a worker with every grant still gets 403 — cross-farm views are an
owner-operator lens by design). Every aggregate is one grouped query over the
owned-farm set (never a per-farm request loop), bounded by
``GOATFARM_MAX_FARMS_PER_USER``, and every ``due_date``/month boundary is
evaluated in the FARM's own timezone inside SQL, not the deployment default.
"""

from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings
from ..deps import CurrentUser, DbSession
from ..models import (
    Animal,
    AnimalStatus,
    BreedingRecord,
    Bucket,
    Farm,
    KiddingRecord,
    KidEntry,
    ScreeningFinding,
    Task,
    TaskStatus,
    Transaction,
    WeightRecord,
)
from ..models.enums import KidStatus
from ..models.helpers import ASSESSED_OUTCOMES, CONCEIVED_OUTCOMES
from ..schemas.common import COMMON_ERROR_RESPONSES
from ..schemas.owner import (
    OwnerBenchmarksOut,
    OwnerFarmBenchmarksOut,
    OwnerFarmOverviewOut,
    OwnerOverviewOut,
)

router = APIRouter(prefix="/api/owner", tags=["owner"], responses=COMMON_ERROR_RESPONSES)


async def _owned_farms(db: AsyncSession, user_id: int) -> list[Farm]:
    """The farms this user OWNS, bounded by the deployment's affiliation cap."""
    cap = get_settings().max_farms_per_user
    return list(
        (
            await db.execute(
                select(Farm).where(Farm.owner_id == user_id).order_by(Farm.id).limit(cap)
            )
        )
        .scalars()
        .all()
    )


def _require_owned(farms: list[Farm]) -> list[Farm]:
    if not farms:
        # Ownership is the permission: a worker (even with every grant) has no
        # cross-farm lens.
        raise HTTPException(
            status_code=403, detail="Only a farm owner can view the cross-farm overview."
        )
    return farms


def _farm_local_today() -> Any:
    """SQL expression: the farm's current business date, when joined to Farm."""
    return cast(func.timezone(Farm.timezone, func.now()), Date)


def _rate(numerator: int | Decimal | None, denominator: int | Decimal | None) -> float | None:
    if not denominator:
        return None
    return round(float(numerator or 0) / float(denominator) * 100, 1)


@router.get("/overview")
async def owner_overview(
    response: Response,
    db: DbSession,
    user: CurrentUser,
) -> OwnerOverviewOut:
    """Attention headlines for every farm the caller owns, in one response."""
    response.headers["Cache-Control"] = "no-store"
    farms = _require_owned(await _owned_farms(db, user.id))
    farm_ids = [farm.id for farm in farms]
    farms_by_id = {farm.id: farm for farm in farms}

    # Herd headlines: one grouped pass over animals.
    animal_rows = (
        await db.execute(
            select(
                Animal.farm_id,
                func.count().filter(Animal.status == AnimalStatus.ACTIVE.value),
                func.count().filter(
                    Animal.status == AnimalStatus.ACTIVE.value,
                    Animal.current_bucket.in_([Bucket.PREGNANCY_LATE.value, Bucket.DELIVERY.value]),
                ),
                func.count().filter(Animal.movement_restricted.is_(True)),
            )
            .where(Animal.farm_id.in_(farm_ids))
            .group_by(Animal.farm_id)
        )
    ).all()

    # Duty board: overdue / due-today pending / due-today done, each against
    # the farm's own calendar day.
    local_today = _farm_local_today()
    duty_rows = (
        await db.execute(
            select(
                Task.farm_id,
                func.count().filter(
                    Task.status == TaskStatus.PENDING.value,
                    Task.due_date < local_today,
                ),
                func.count().filter(
                    Task.status == TaskStatus.PENDING.value,
                    Task.due_date == local_today,
                ),
                func.count().filter(
                    Task.status == TaskStatus.DONE.value,
                    Task.due_date == local_today,
                ),
            )
            .join(Farm, Task.farm_id == Farm.id)
            .where(Task.farm_id.in_(farm_ids))
            .group_by(Task.farm_id)
        )
    ).all()

    flag_rows = (
        await db.execute(
            select(ScreeningFinding.farm_id, func.count())
            .where(
                ScreeningFinding.farm_id.in_(farm_ids),
                ScreeningFinding.status == "PENDING_REVIEW",
            )
            .group_by(ScreeningFinding.farm_id)
        )
    ).all()

    month_start = cast(func.date_trunc("month", func.timezone(Farm.timezone, func.now())), Date)
    finance_rows = (
        await db.execute(
            select(
                Transaction.farm_id,
                Transaction.type,
                func.sum(Transaction.amount),
            )
            .join(Farm, Transaction.farm_id == Farm.id)
            .where(Transaction.farm_id.in_(farm_ids), Transaction.date >= month_start)
            .group_by(Transaction.farm_id, Transaction.type)
        )
    ).all()

    animals: dict[int, tuple[int, int, int]] = {
        row[0]: (row[1], row[2], row[3]) for row in animal_rows
    }
    duties: dict[int, tuple[int, int, int]] = {
        row[0]: (row[1], row[2], row[3]) for row in duty_rows
    }
    flags: dict[int, int] = {row[0]: int(row[1]) for row in flag_rows}
    income: dict[int, Decimal] = {}
    expense: dict[int, Decimal] = {}
    for farm_id, txn_type, total in finance_rows:
        amount = Decimal(total or 0)
        if str(txn_type) == "INCOME":
            income[farm_id] = income.get(farm_id, Decimal(0)) + amount
        else:
            expense[farm_id] = expense.get(farm_id, Decimal(0)) + amount

    out = []
    for farm_id in farm_ids:
        active, watch, restricted = animals.get(farm_id, (0, 0, 0))
        overdue, due_pending, due_done = duties.get(farm_id, (0, 0, 0))
        farm = farms_by_id[farm_id]
        out.append(
            OwnerFarmOverviewOut(
                farm_id=farm_id,
                farm_name=farm.name,
                timezone=farm.timezone,
                active_animals=int(active),
                overdue_duties=int(overdue),
                todays_duties_pending=int(due_pending),
                todays_duties_done=int(due_done),
                kidding_watch=int(watch),
                movement_restricted=int(restricted),
                open_screening_flags=flags.get(farm_id, 0),
                month_income=income.get(farm_id, Decimal(0)),
                month_expense=expense.get(farm_id, Decimal(0)),
                month_net=income.get(farm_id, Decimal(0)) - expense.get(farm_id, Decimal(0)),
            )
        )
    return OwnerOverviewOut(farms=out)


@router.get("/benchmarks")
async def owner_benchmarks(
    response: Response,
    db: DbSession,
    user: CurrentUser,
    days: Annotated[int, Query(ge=1, le=365)] = 90,
) -> OwnerBenchmarksOut:
    """Per-farm performance figures over the trailing window, for ranking."""
    response.headers["Cache-Control"] = "no-store"
    farms = _require_owned(await _owned_farms(db, user.id))
    farm_ids = [farm.id for farm in farms]
    farms_by_id = {farm.id: farm for farm in farms}

    window_start = cast(
        func.timezone(Farm.timezone, func.now()) - func.make_interval(0, 0, 0, days), Date
    )

    # Conception: services ASSESSED inside the window (same outcome sets the
    # dashboard's reports endpoint uses).
    completed = BreedingRecord.outcome.in_(sorted(ASSESSED_OUTCOMES))
    conceived = BreedingRecord.outcome.in_(sorted(CONCEIVED_OUTCOMES))
    breeding_rows = (
        await db.execute(
            select(
                BreedingRecord.farm_id,
                func.count().filter(completed),
                func.count().filter(conceived),
            )
            .join(Farm, BreedingRecord.farm_id == Farm.id)
            .where(
                BreedingRecord.farm_id.in_(farm_ids),
                BreedingRecord.ultrasound_result_date >= window_start,
            )
            .group_by(BreedingRecord.farm_id)
        )
    ).all()

    # Kid mortality: kids born inside the window (litter date on the farm
    # calendar), stillborn + died after birth vs total born.
    mortality_rows = (
        await db.execute(
            select(
                KiddingRecord.farm_id,
                func.count(KidEntry.id),
                func.count(KidEntry.id).filter(
                    KidEntry.status.in_([KidStatus.STILLBORN.value, KidStatus.DIED.value])
                ),
            )
            .join(Farm, KiddingRecord.farm_id == Farm.id)
            .join(KidEntry, KidEntry.kidding_record_id == KiddingRecord.id)
            .where(KiddingRecord.farm_id.in_(farm_ids), KiddingRecord.date >= window_start)
            .group_by(KiddingRecord.farm_id)
        )
    ).all()

    # Daily gain: per animal (max-min weight)/(days between), averaged per
    # farm. Scoped to the owned farms and each farm's local calendar like
    # every other benchmark; animals weighed once contribute nothing
    # (HAVING count >= 2 — a single weighing has no interval to gain over).
    weight_window = (
        select(
            WeightRecord.farm_id.label("farm_id"),
            WeightRecord.animal_id.label("animal_id"),
            func.min(WeightRecord.weight_kg).label("first_weight"),
            func.max(WeightRecord.weight_kg).label("last_weight"),
            func.min(WeightRecord.date).label("first_date"),
            func.max(WeightRecord.date).label("last_date"),
        )
        .join(Farm, WeightRecord.farm_id == Farm.id)
        .where(
            WeightRecord.farm_id.in_(farm_ids),
            WeightRecord.date >= window_start,
        )
        .group_by(WeightRecord.farm_id, WeightRecord.animal_id)
        .having(func.count() >= 2)
        .subquery()
    )
    gain_rows = (
        await db.execute(
            select(
                weight_window.c.farm_id,
                func.avg(
                    (weight_window.c.last_weight - weight_window.c.first_weight)
                    / func.nullif(
                        cast(weight_window.c.last_date, Date)
                        - cast(weight_window.c.first_date, Date)
                        + 1,
                        0,
                    )
                ),
                func.sum(weight_window.c.last_weight - weight_window.c.first_weight),
            ).group_by(weight_window.c.farm_id)
        )
    ).all()

    # Feed spend in the window (ledger FEED rows) — divided by kg gained.
    feed_rows = (
        await db.execute(
            select(Transaction.farm_id, func.sum(Transaction.amount))
            .join(Farm, Transaction.farm_id == Farm.id)
            .where(
                Transaction.farm_id.in_(farm_ids),
                Transaction.date >= window_start,
                Transaction.category == "FEED",
            )
            .group_by(Transaction.farm_id)
        )
    ).all()

    # Realized margin per animal sold in the window. Home-bred stock has no
    # purchase price; treating an absent purchase cost as ₹0 keeps those
    # sales in the average instead of silently dropping them.
    sale_rows = (
        await db.execute(
            select(
                Animal.farm_id,
                func.count(),
                func.avg(Animal.sale_price - func.coalesce(Animal.purchase_price, 0)),
            )
            .join(Farm, Animal.farm_id == Farm.id)
            .where(
                Animal.farm_id.in_(farm_ids),
                Animal.status == AnimalStatus.SOLD.value,
                Animal.status_date >= window_start,
            )
            .group_by(Animal.farm_id)
        )
    ).all()

    breeding: dict[int, tuple[int, int]] = {row[0]: (row[1], row[2]) for row in breeding_rows}
    mortality: dict[int, tuple[int, int]] = {row[0]: (row[1], row[2]) for row in mortality_rows}
    gains: dict[int, tuple[Any, Any]] = {row[0]: (row[1], row[2]) for row in gain_rows}
    feed: dict[int, Decimal] = {row[0]: Decimal(row[1] or 0) for row in feed_rows}
    sales: dict[int, tuple[int, Any]] = {row[0]: (row[1], row[2]) for row in sale_rows}

    out = []
    for farm_id in farm_ids:
        assessed, conceptions = breeding.get(farm_id, (0, 0))
        born, dead_kids = mortality.get(farm_id, (0, 0))
        avg_gain, total_gain = gains.get(farm_id, (None, None))
        sold_count, avg_margin = sales.get(farm_id, (0, None))
        feed_cost = feed.get(farm_id, Decimal(0))
        cost_per_kg = (
            round(float(feed_cost) / float(total_gain), 2)
            if total_gain and float(total_gain) > 0
            else None
        )
        out.append(
            OwnerFarmBenchmarksOut(
                farm_id=farm_id,
                farm_name=farms_by_id[farm_id].name,
                conception_rate=_rate(conceptions, assessed),
                kid_mortality_rate=_rate(dead_kids, born),
                avg_daily_gain_kg=round(float(avg_gain), 3) if avg_gain is not None else None,
                feed_cost_per_kg_gain=cost_per_kg,
                profit_per_animal_sold=(
                    round(float(avg_margin), 2) if avg_margin is not None else None
                ),
                animals_sold=int(sold_count),
            )
        )
    return OwnerBenchmarksOut(days=days, farms=out)
