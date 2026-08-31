"""Milk: per-animal yield recording and herd totals (dairy farms)."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select

from ..deps import CurrentFarm, CurrentUser, DbSession, require_perm
from ..models import Animal, AnimalStatus, MilkRecord
from ..schemas.common import MAX_INT32_ID, MAX_PAGE_OFFSET
from ..schemas.milk import (
    MilkAnimalSummaryOut,
    MilkDayTotalOut,
    MilkListOut,
    MilkRecordIn,
    MilkRecordOut,
    MilkSummaryOut,
)
from ..services import IdempotencyKey, execute_idempotent, milk_summary, record_milk
from ..services.milk import list_milk_records
from ..utils import today

router = APIRouter(prefix="/api/milk", tags=["milk"])

MilkView = Annotated[set[str], Depends(require_perm("milk.view"))]
MilkManage = Annotated[set[str], Depends(require_perm("milk.manage"))]


def _milk_out(record: MilkRecord, animal_tag: str | None) -> MilkRecordOut:
    out = MilkRecordOut.model_validate(record)
    out.animal_tag = animal_tag
    return out


@router.get("")
async def milk_list(
    db: DbSession,
    farm: CurrentFarm,
    _perms: MilkView,
    animal_id: int | None = Query(default=None, gt=0, le=MAX_INT32_ID),
    shift: str | None = Query(default=None, pattern="^(MORNING|AFTERNOON|NIGHT)$"),
    date_from: date | None = Query(default=None),  # noqa: B008
    date_to: date | None = Query(default=None),  # noqa: B008
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=MAX_PAGE_OFFSET),
) -> MilkListOut:
    """Yield readings, newest first, with the filtered-set litre total."""
    if animal_id is not None:
        animal = (
            await db.execute(
                select(Animal.id).where(Animal.farm_id == farm.id, Animal.id == animal_id)
            )
        ).scalar_one_or_none()
        if animal is None:
            raise HTTPException(status_code=404, detail="Animal not found")
    if date_from is not None and date_to is not None and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from cannot be after date_to")
    rows, total, total_litres = await list_milk_records(
        db,
        farm,
        animal_id=animal_id,
        shift=shift,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )
    return MilkListOut(
        records=[_milk_out(record, tag) for record, tag in rows],
        total=total,
        limit=limit,
        offset=offset,
        total_litres=round(total_litres, 3),
    )


@router.get("/summary")
async def milk_summary_endpoint(
    db: DbSession,
    farm: CurrentFarm,
    _perms: MilkView,
    days: int = Query(default=30, ge=1, le=365),
    animal_id: int | None = Query(default=None, gt=0, le=MAX_INT32_ID),
) -> MilkSummaryOut:
    """Daily herd totals and per-animal averages over the last N days."""
    if animal_id is not None:
        animal = (
            await db.execute(
                select(Animal.id).where(Animal.farm_id == farm.id, Animal.id == animal_id)
            )
        ).scalar_one_or_none()
        if animal is None:
            raise HTTPException(status_code=404, detail="Animal not found")
    daily, animals, total_litres, avg_fat = await milk_summary(
        db, farm, days=days, animal_id=animal_id
    )
    return MilkSummaryOut(
        days=days,
        total_litres=round(total_litres, 3),
        avg_daily_litres=round(total_litres / days, 3),
        avg_fat_pct=round(avg_fat, 2) if avg_fat is not None else None,
        daily=[
            MilkDayTotalOut(
                date=row.date,
                litres=round(float(row.litres), 3),
                recorded_animals=int(row.recorded_animals),
                avg_fat_pct=round(float(row.avg_fat_pct), 2) if row.avg_fat_pct else None,
            )
            for row in daily
        ],
        animals=[
            MilkAnimalSummaryOut(
                animal_id=row.animal_id,
                animal_tag=row.animal_tag,
                total_litres=round(float(row.total_litres), 3),
                avg_daily_litres=round(float(row.total_litres) / max(int(row.days_recorded), 1), 3),
                days_recorded=int(row.days_recorded),
                avg_fat_pct=round(float(row.avg_fat_pct), 2) if row.avg_fat_pct else None,
            )
            for row in animals
        ],
    )


@router.post("/new", status_code=201)
async def add_milk_record(
    payload: MilkRecordIn,
    response: Response,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    perms: MilkManage,
    idempotency_key: IdempotencyKey = None,
) -> MilkRecordOut:
    """Record (or correct) one animal's yield for a milking shift."""

    async def mutate() -> MilkRecordOut:
        if payload.date > today(farm.timezone):
            raise HTTPException(status_code=422, detail="Milk date cannot be in the future")
        # Lock the animal row: it both verifies farm membership and serializes
        # concurrent submissions of the same milking.
        animal = (
            await db.execute(
                select(Animal)
                .where(Animal.farm_id == farm.id, Animal.id == payload.animal_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if animal is None:
            raise HTTPException(status_code=404, detail="Animal not found")
        if animal.status != AnimalStatus.ACTIVE.value:
            raise HTTPException(
                status_code=409,
                detail=f"{animal.tag_number} is {animal.status.lower()} — cannot record milk",
            )
        if animal.sex != "F":
            raise HTTPException(status_code=422, detail="Milk is recorded for female animals")
        record = await record_milk(
            db,
            farm,
            animal,
            payload.date,
            payload.shift,
            payload.litres,
            payload.fat_pct,
            payload.notes,
            created_by_id=user.id,
        )
        return _milk_out(record, animal.tag_number)

    return await execute_idempotent(
        db,
        http_response=response,
        key=idempotency_key,
        farm_id=farm.id,
        actor_id=user.id,
        operation="POST /api/milk/new",
        payload=payload,
        path_identity={},
        success_status=201,
        response_type=MilkRecordOut,
        mutate=mutate,
    )
