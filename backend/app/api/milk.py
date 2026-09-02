"""Milk: per-animal yield recording and herd totals (dairy farms)."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select

from ..deps import CurrentFarm, CurrentUser, DbSession, require_perm
from ..models import (
    Animal,
    AnimalSource,
    AnimalStatus,
    Bucket,
    FarmType,
    KiddingRecord,
    MilkRecord,
    species_profile,
)
from ..schemas.common import COMMON_ERROR_RESPONSES, MAX_INT32_ID, MAX_PAGE_OFFSET
from ..schemas.milk import (
    MilkAnimalSummaryOut,
    MilkDayTotalOut,
    MilkListOut,
    MilkRecordIn,
    MilkRecordOut,
    MilkSummaryOut,
)
from ..services import IdempotencyKey, execute_idempotent, milk_summary, record_milk
from ..services.chronology import require_animal_event_chronology
from ..services.milk import list_milk_records
from ..utils import today

router = APIRouter(prefix="/api/milk", tags=["milk"], responses=COMMON_ERROR_RESPONSES)

MilkView = Annotated[set[str], Depends(require_perm("milk.view"))]
MilkManage = Annotated[set[str], Depends(require_perm("milk.manage"))]

# Buckets in which a buffalo is definitionally not part of the milking string:
# calf-shed cohorts, the quarantine pen and the dry/close-up pen. Pregnant
# buckets are deliberately absent — a bred-back dam is milked through most of
# her pregnancy; the lactation-context check below is what fences heifers.
NON_MILKING_BUCKETS = frozenset(
    {
        Bucket.FEMALE_KIDS.value,
        Bucket.MALE_KIDS.value,
        Bucket.QUARANTINE.value,
        Bucket.DELIVERY.value,
    }
)


def _require_dairy_farm(farm: CurrentFarm) -> None:
    """Milk endpoints exist only for buffalo dairy farms.

    A goat farm here is an Osmanabadi meat herd: per-shift yield recording
    against it would fabricate dairy data (and dairy P&L expectations) for
    animals that are never milked, so every route in this module refuses the
    request outright rather than returning an empty parlour.
    """
    if farm.farm_type != FarmType.BUFFALO_DAIRY.value:
        raise HTTPException(
            status_code=422,
            detail="Milk is recorded on buffalo dairy farms only",
        )


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
    _require_dairy_farm(farm)
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
    _require_dairy_farm(farm)
    if animal_id is not None:
        animal = (
            await db.execute(
                select(Animal.id).where(Animal.farm_id == farm.id, Animal.id == animal_id)
            )
        ).scalar_one_or_none()
        if animal is None:
            raise HTTPException(status_code=404, detail="Animal not found")
    daily, animals, total_litres, avg_fat, animals_total = await milk_summary(
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
        animals_total=animals_total,
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
        _require_dairy_farm(farm)
        # Separation of duties on the fat number: litres are keyed by whoever
        # holds milk.manage (the parlour recorder), but fat_pct is the input
        # procurement pricing pays on (₹/kg fat), so setting or changing it
        # needs milk.quality. A recorder without it can still correct litres:
        # their re-submit passes the already-tested fat through untouched
        # instead of erasing it.
        fat_pct = payload.fat_pct
        if fat_pct is not None and "milk.quality" not in perms:
            raise HTTPException(
                status_code=403,
                detail="Recording a fat test requires the milk quality permission",
            )
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
        if animal.current_bucket in NON_MILKING_BUCKETS:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"{animal.tag_number} is in the {animal.current_bucket.lower().replace('_', ' ')} "
                    "cohort — milk is recorded for the milking string only"
                ),
            )
        # Lactation context: a yield reading asserts this buffalo is (or was
        # recently) lactating. She must have calved at least once on this
        # farm, or be an imported adult purchase (an in-milk foundation dam
        # bought in milk). A never-calved heifer — bred or not — has no
        # parlour ledger to write to.
        has_calved = (
            await db.execute(
                select(KiddingRecord.id)
                .where(KiddingRecord.farm_id == farm.id, KiddingRecord.doe_id == animal.id)
                .limit(1)
            )
        ).scalar_one_or_none() is not None
        if not has_calved:
            profile = species_profile(farm.farm_type)
            age_months = animal.age_months_on(today(farm.timezone))
            imported_adult = animal.source == AnimalSource.PURCHASED.value and (
                age_months is None or age_months >= profile.min_breeding_age_months
            )
            if not imported_adult:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"{animal.tag_number} has no recorded calving — milk is recorded "
                        "for dams in the milking string (imported in-milk purchases excepted)"
                    ),
                )
        try:
            # Parlour history is factual: a reading cannot predate the
            # animal's birth or her arrival on this farm (backdating beyond
            # acquisition would fabricate history shiftable across months).
            require_animal_event_chronology(animal, payload.date, "Milk record")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        if fat_pct is None and "milk.quality" not in perms:
            # Resolve the preserved fat UNDER the animal lock: reading it
            # earlier lets a quality role's fat test commit in between and be
            # erased by this correction — the exact loss the carry-forward
            # exists to prevent. Every writer of this milking takes the same
            # lock first, so the value read here is the latest committed one.
            fat_pct = (
                await db.execute(
                    select(MilkRecord.fat_pct).where(
                        MilkRecord.farm_id == farm.id,
                        MilkRecord.animal_id == payload.animal_id,
                        MilkRecord.date == payload.date,
                        MilkRecord.shift == payload.shift,
                    )
                )
            ).scalar_one_or_none()
        try:
            record = await record_milk(
                db,
                farm,
                animal,
                payload.date,
                payload.shift,
                payload.litres,
                fat_pct,
                payload.notes,
                created_by_id=user.id,
                correction_reason=payload.correction_reason,
            )
        except ValueError as exc:
            # Re-submitting a recorded milking without a stated reason.
            raise HTTPException(status_code=422, detail=str(exc)) from None
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
