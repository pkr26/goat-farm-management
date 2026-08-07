"""Animals module: list/filters, create, profile, bucket moves, weights, status."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import CurrentFarm, CurrentUser, DbSession, require_perm
from ..models import (
    Animal,
    AnimalStatus,
    BucketMove,
    HealthEvent,
    Transaction,
    TransactionCategory,
    TransactionType,
    WeightRecord,
)
from ..schemas.animals import (
    AnimalCreateIn,
    AnimalListOut,
    AnimalOut,
    AnimalProfileOut,
    AnimalStatusStr,
    BucketMoveOut,
    BucketStr,
    MoveIn,
    Sex,
    StatusChangeIn,
    WeightIn,
    WeightRecordOut,
)
from ..schemas.common import MAX_INT32_ID
from ..schemas.health import HealthEventOut
from ..services import generate_unique_tag, move_animal, skip_pending_tasks_for_animal
from ..utils import today

router = APIRouter(prefix="/api/animals", tags=["animals"])

NOT_FOUND = "Animal not found"


async def _get_animal(
    db: AsyncSession, farm_id: int, animal_id: int, *, for_update: bool = False
) -> Animal:
    # Ids above the int4 PK ceiling cannot exist — 404, never an asyncpg
    # int32 DataError (500).
    animal: Animal | None
    if animal_id > MAX_INT32_ID:
        animal = None
    elif for_update:
        # SELECT ... FOR UPDATE: concurrent mutations (e.g. two sales) take
        # the row lock in turn — the loser re-reads the committed row and
        # fails the state check instead of double-applying side effects.
        result = await db.execute(select(Animal).where(Animal.id == animal_id).with_for_update())
        animal = result.scalar_one_or_none()
    else:
        animal = await db.get(Animal, animal_id)
    if animal is None or animal.farm_id != farm_id:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return animal


async def _animal_out(db: AsyncSession, animal: Animal) -> AnimalOut:
    """AnimalOut with computed fields (age, latest weight, pregnancy state...).
    Expire + re-select so relationships are freshly selectin-loaded — FK-only
    child inserts (weight/move) leave previously loaded collections stale, and
    a never-loaded persistent animal would trigger a forbidden lazy load."""
    animal_id = animal.id  # read before expire: expired attrs can't be touched
    db.expire(animal)
    result = await db.execute(select(Animal).where(Animal.id == animal_id))
    return AnimalOut.model_validate(result.scalar_one())


@router.get("")
async def list_animals(
    db: DbSession,
    farm: CurrentFarm,
    _perms: Annotated[set[str], Depends(require_perm("animals.view"))],
    bucket: BucketStr | None = None,
    sex: Sex | None = None,
    status: AnimalStatusStr | None = None,
    q: str | None = None,
    limit: Annotated[int | None, Query(ge=1, le=1000)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AnimalListOut:
    stmt = select(Animal).where(Animal.farm_id == farm.id)
    if bucket is not None:
        stmt = stmt.where(Animal.current_bucket == bucket)
    if sex is not None:
        stmt = stmt.where(Animal.sex == sex)
    if status is not None:
        stmt = stmt.where(Animal.status == status)
    else:  # v1 default: the herd list shows ACTIVE animals unless asked otherwise
        stmt = stmt.where(Animal.status == AnimalStatus.ACTIVE.value)
    if q and q.strip():
        stmt = stmt.where(Animal.tag_number.ilike(f"%{q.strip()}%"))
    stmt = stmt.order_by(Animal.current_bucket, Animal.tag_number)
    if limit is None and offset == 0:
        # Default (unpaginated) behavior: the full filtered list, as always.
        result = await db.execute(stmt)
        animals = [AnimalOut.model_validate(a) for a in result.scalars()]
        return AnimalListOut(animals=animals, total=len(animals))
    # Paginated: `total` stays the full filtered count so clients can page.
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    result = await db.execute(stmt.offset(offset).limit(limit))
    animals = [AnimalOut.model_validate(a) for a in result.scalars()]
    return AnimalListOut(animals=animals, total=total)


@router.post("", status_code=201)
async def create_animal(
    payload: AnimalCreateIn,
    db: DbSession,
    farm: CurrentFarm,
    user: CurrentUser,
    _perms: Annotated[set[str], Depends(require_perm("animals.create"))],
) -> AnimalOut:
    tag_number = (payload.tag_number or "").strip()
    if tag_number:
        existing = await db.execute(
            select(Animal.id).where(Animal.farm_id == farm.id, Animal.tag_number == tag_number)
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=400, detail=f"Tag '{tag_number}' already exists on this farm."
            )
    # enum/date/non-negativity guards from v1 now live in AnimalCreateIn's validators.
    # A blank tag gets an auto-generated one; that retries once on a lost race.
    attempts = 1 if tag_number else 2
    for attempt in range(attempts):
        if not tag_number:
            tag_number = await generate_unique_tag(db, farm.id)
        animal = Animal(
            farm_id=farm.id,
            tag_number=tag_number,
            name=(payload.name or "").strip() or None,
            sex=payload.sex,
            source=payload.source,
            current_bucket=payload.current_bucket,
            date_of_birth=payload.date_of_birth,
            estimated_dob=payload.estimated_dob,
            birth_type=payload.birth_type,
            breed=payload.breed.strip() or "Osmanabadi",
            birth_weight=payload.birth_weight,
            purchase_date=payload.purchase_date,
            purchase_price=payload.purchase_price,
            seller_name=(payload.seller_name or "").strip() or None,
            notes=(payload.notes or "").strip() or None,
            status=AnimalStatus.ACTIVE.value,
        )
        db.add(animal)
        try:
            await db.flush()
            db.add(
                BucketMove(
                    animal_id=animal.id,
                    from_bucket=None,
                    to_bucket=payload.current_bucket,
                    reason="Initial entry",
                    created_by_id=user.id,
                )
            )
            if payload.weight_kg is not None and payload.weight_kg > 0:
                db.add(
                    WeightRecord(
                        animal_id=animal.id,
                        date=today(),
                        weight_kg=payload.weight_kg,
                        notes="Entry weight",
                        created_by_id=user.id,
                    )
                )
            await db.commit()
            return await _animal_out(db, animal)
        except IntegrityError:
            # A concurrent insert won the tag race past the pre-check above
            # (uq_animal_tag_per_farm) — answer exactly like the pre-check,
            # never 500; auto tags retry once with a fresh generated tag.
            await db.rollback()
            if attempt + 1 == attempts:
                raise HTTPException(
                    status_code=400, detail=f"Tag '{tag_number}' already exists on this farm."
                ) from None
            tag_number = ""
    raise AssertionError("unreachable")  # the loop always returns or raises


@router.get("/{animal_id}")
async def animal_profile(
    animal_id: int,
    db: DbSession,
    farm: CurrentFarm,
    _perms: Annotated[set[str], Depends(require_perm("animals.view"))],
) -> AnimalProfileOut:
    animal = await _get_animal(db, farm.id, animal_id)
    kids_result = await db.execute(
        select(Animal)
        .where(Animal.farm_id == farm.id, Animal.dam_id == animal.id)
        .order_by(Animal.tag_number)
    )
    weights_result = await db.execute(
        select(WeightRecord)
        .where(WeightRecord.animal_id == animal.id)
        .order_by(WeightRecord.date.desc(), WeightRecord.id.desc())
    )
    moves_result = await db.execute(
        select(BucketMove)
        .where(BucketMove.animal_id == animal.id)
        .order_by(BucketMove.moved_at.desc(), BucketMove.id.desc())
    )
    health_result = await db.execute(
        select(HealthEvent)
        .where(HealthEvent.animal_id == animal.id)
        .order_by(HealthEvent.date.desc(), HealthEvent.id.desc())
    )
    # breedings_as_doe is selectin-loaded; newest first for the client's list.
    breeding_ids = [
        br.id
        for br in sorted(
            animal.breedings_as_doe,
            key=lambda r: (r.breeding_date, r.id or 0),
            reverse=True,
        )
    ]
    return AnimalProfileOut(
        animal=AnimalOut.model_validate(animal),
        kids=[AnimalOut.model_validate(k) for k in kids_result.scalars()],
        weights=[WeightRecordOut.model_validate(w) for w in weights_result.scalars()],
        moves=[BucketMoveOut.model_validate(m) for m in moves_result.scalars()],
        health_events=[HealthEventOut.model_validate(e) for e in health_result.scalars()],
        breedings=breeding_ids,
    )


@router.post("/{animal_id}/move")
async def move_bucket(
    animal_id: int,
    payload: MoveIn,
    db: DbSession,
    farm: CurrentFarm,
    user: CurrentUser,
    _perms: Annotated[set[str], Depends(require_perm("animals.move"))],
) -> AnimalOut:
    animal = await _get_animal(db, farm.id, animal_id)
    # Dead/sold/culled animals are out of the herd lifecycle — no moves.
    if animal.status != AnimalStatus.ACTIVE.value:
        raise HTTPException(
            status_code=400,
            detail=f"{animal.tag_number} is {animal.status.lower()} — cannot move buckets.",
        )
    # move_animal carries the v1 guards (non-ACTIVE / same-bucket no-op).
    move_animal(
        db,
        animal,
        payload.to_bucket,
        reason=(payload.reason or "").strip(),
        created_by_id=user.id,
    )
    await db.commit()
    return await _animal_out(db, animal)


@router.post("/{animal_id}/weight", status_code=201)
async def record_weight(
    animal_id: int,
    payload: WeightIn,
    db: DbSession,
    farm: CurrentFarm,
    user: CurrentUser,
    _perms: Annotated[set[str], Depends(require_perm("animals.weight"))],
) -> WeightRecordOut:
    animal = await _get_animal(db, farm.id, animal_id)
    if animal.status != AnimalStatus.ACTIVE.value:
        raise HTTPException(
            status_code=400,
            detail=f"{animal.tag_number} is {animal.status.lower()} — cannot record a weight.",
        )
    # finite/positive/future-date/bcs-range guards from v1 live in WeightIn's validators.
    record = WeightRecord(
        animal_id=animal.id,
        date=payload.date or today(),
        weight_kg=payload.weight_kg,
        bcs=payload.bcs,
        notes=(payload.notes or "").strip() or None,
        created_by_id=user.id,
    )
    db.add(record)
    await db.commit()
    return WeightRecordOut.model_validate(record)


@router.post("/{animal_id}/status")
async def change_status(
    animal_id: int,
    payload: StatusChangeIn,
    db: DbSession,
    farm: CurrentFarm,
    user: CurrentUser,
    _perms: Annotated[set[str], Depends(require_perm("animals.status"))],
) -> AnimalOut:
    animal = await _get_animal(db, farm.id, animal_id, for_update=True)
    # Only an ACTIVE animal can change status — replaying a sale on an
    # already-SOLD animal must not book a second income transaction. The row
    # lock makes two in-flight status changes serialize on this check.
    if animal.status != AnimalStatus.ACTIVE.value:
        raise HTTPException(
            status_code=400,
            detail=f"{animal.tag_number} is already {animal.status.lower()}.",
        )
    status_date = payload.date or today()
    animal.status = payload.new_status
    animal.status_date = status_date
    animal.status_notes = (payload.notes or "").strip() or None
    # new_status is never ACTIVE here: clear the cull flag and stop the
    # animal's pending tasks (a dead/sold animal must not generate work).
    animal.cull_candidate = False
    await skip_pending_tasks_for_animal(db, farm.id, animal.id)

    if payload.new_status == AnimalStatus.SOLD.value:
        animal.sale_price = payload.sale_price
        animal.buyer_name = (payload.buyer_name or "").strip() or None
        if payload.sale_price and payload.sale_price > 0:
            db.add(
                Transaction(
                    farm_id=farm.id,
                    date=status_date,
                    type=TransactionType.INCOME.value,
                    category=TransactionCategory.ANIMAL_SALE.value,
                    amount=payload.sale_price,
                    related_animal_id=animal.id,
                    notes=f"Sale of {animal.tag_number}"
                    + (f" to {animal.buyer_name}" if animal.buyer_name else ""),
                    created_by_id=user.id,
                )
            )
    await db.commit()
    return await _animal_out(db, animal)
