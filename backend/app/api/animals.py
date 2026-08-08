"""Animals module: list/filters, create, profile, bucket moves, weights, status."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import CurrentFarm, CurrentUser, DbSession, require_perm
from ..models import (
    Animal,
    AnimalStatus,
    BreedingOutcome,
    BreedingRecord,
    Bucket,
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
from ..services import (
    ANIMAL_OUT_LOADS,
    generate_unique_tag,
    mark_aborted,
    move_animal,
    require_bucket_transition,
    skip_pending_tasks_for_animal,
)
from ..utils import money, today
from ._shared import animal_out

router = APIRouter(prefix="/api/animals", tags=["animals"])

NOT_FOUND = "Animal not found"


async def _get_animal(
    db: AsyncSession,
    farm_id: int,
    animal_id: int,
    *,
    for_update: bool = False,
    with_details: bool = False,
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
        stmt = select(Animal).where(Animal.id == animal_id)
        if with_details:
            # AnimalOut's computed fields read these collections.
            stmt = stmt.options(*ANIMAL_OUT_LOADS)
        result = await db.execute(stmt)
        animal = result.scalar_one_or_none()
    if animal is None or animal.farm_id != farm_id:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return animal


async def _animal_out(
    db: AsyncSession,
    animal: Animal,
    reference_date: date,
    timezone_name: str,
) -> AnimalOut:
    """AnimalOut with computed fields (age, latest weight, pregnancy state...).
    Expire + re-select so relationships are freshly selectin-loaded — FK-only
    child inserts (weight/move) leave previously loaded collections stale, and
    a never-loaded persistent animal would trigger a forbidden lazy load."""
    animal_id = animal.id  # read before expire: expired attrs can't be touched
    db.expire(animal)
    result = await db.execute(
        select(Animal).options(*ANIMAL_OUT_LOADS).where(Animal.id == animal_id)
    )
    return animal_out(result.scalar_one(), reference_date, timezone_name)


@router.get("")
async def list_animals(
    db: DbSession,
    farm: CurrentFarm,
    _perms: Annotated[set[str], Depends(require_perm("animals.view"))],
    bucket: BucketStr | None = None,
    sex: Sex | None = None,
    status: AnimalStatusStr | None = None,
    q: Annotated[str | None, Query(max_length=60)] = None,
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
        # Escape LIKE wildcards: a literal "%"/"_" in the query
        # must match itself, not act as a pattern metacharacter.
        escaped = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        stmt = stmt.where(
            or_(
                Animal.tag_number.ilike(pattern, escape="\\"),
                Animal.name.ilike(pattern, escape="\\"),
            )
        )
    stmt = stmt.order_by(Animal.current_bucket, Animal.tag_number)
    if limit is None and offset == 0:
        # Default (unpaginated) behavior: the full filtered list, as always.
        result = await db.execute(stmt.options(*ANIMAL_OUT_LOADS))
        reference_date = today(farm.timezone)
        animals = [animal_out(a, reference_date, farm.timezone) for a in result.scalars()]
        return AnimalListOut(animals=animals, total=len(animals))
    # Paginated: `total` stays the full filtered count so clients can page.
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    result = await db.execute(stmt.options(*ANIMAL_OUT_LOADS).offset(offset).limit(limit))
    reference_date = today(farm.timezone)
    animals = [animal_out(a, reference_date, farm.timezone) for a in result.scalars()]
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
    # Capture farm.id/user.id up front: a rollback in the retry path expires
    # every ORM object, and touching farm.id/user.id there would be a
    # forbidden sync refresh on the async session (MissingGreenlet 500
    # instead of the intended retry).
    farm_id = farm.id
    farm_timezone = farm.timezone
    user_id = user.id
    attempts = 1 if tag_number else 2
    for attempt in range(attempts):
        if not tag_number:
            tag_number = await generate_unique_tag(db, farm_id)
        animal = Animal(
            farm_id=farm_id,
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
            purchase_price=money(payload.purchase_price)
            if payload.purchase_price is not None
            else None,
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
                    created_by_id=user_id,
                )
            )
            if payload.weight_kg is not None and payload.weight_kg > 0:
                db.add(
                    WeightRecord(
                        animal_id=animal.id,
                        date=today(farm_timezone),
                        weight_kg=payload.weight_kg,
                        notes="Entry weight",
                        created_by_id=user_id,
                    )
                )
            # Individual and batch purchase entry points must have identical
            # ledger semantics. An explicitly supplied ₹0 is still a factual
            # purchase amount and receives an auditable source-linked row.
            if payload.source == "PURCHASED" and payload.purchase_price is not None:
                db.add(
                    Transaction(
                        farm_id=farm_id,
                        date=payload.purchase_date or today(farm_timezone),
                        type=TransactionType.EXPENSE.value,
                        category=TransactionCategory.ANIMAL_PURCHASE.value,
                        amount=money(payload.purchase_price),
                        related_animal_id=animal.id,
                        notes=f"Purchase of {animal.tag_number}"
                        + (f" from {animal.seller_name}" if animal.seller_name else ""),
                        created_by_id=user_id,
                        source_type="ANIMAL_PURCHASE",
                        source_id=animal.id,
                    )
                )
            await db.commit()
            return await _animal_out(db, animal, today(farm_timezone), farm_timezone)
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
    animal = await _get_animal(db, farm.id, animal_id, with_details=True)
    kids_result = await db.execute(
        select(Animal)
        .options(*ANIMAL_OUT_LOADS)
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
    # breedings_as_doe is eager-loaded (with_details); newest first for the client.
    breeding_ids = [
        br.id
        for br in sorted(
            animal.breedings_as_doe,
            key=lambda r: (r.breeding_date, r.id or 0),
            reverse=True,
        )
    ]
    reference_date = today(farm.timezone)
    return AnimalProfileOut(
        animal=animal_out(animal, reference_date, farm.timezone),
        kids=[animal_out(k, reference_date, farm.timezone) for k in kids_result.scalars()],
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
    # Lock the animal before checking its lifecycle state; status, move and
    # weight writes must serialize rather than append an after-the-fact move.
    animal = await _get_animal(db, farm.id, animal_id, for_update=True)
    try:
        require_bucket_transition(animal, payload.to_bucket)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    # Batch quarantine can only be released by its guarded day-45 task. A
    # standalone manually entered animal has no protocol task and remains
    # movable for historical-data correction.
    if (
        animal.current_bucket == Bucket.QUARANTINE.value
        and payload.to_bucket == Bucket.FOUNDATION.value
        and animal.purchase_batch_id is not None
    ):
        raise HTTPException(
            status_code=409,
            detail="Purchased quarantine animals must be released through the guarded batch task",
        )
    move_animal(
        db,
        animal,
        payload.to_bucket,
        reason=(payload.reason or "").strip(),
        created_by_id=user.id,
    )
    await db.commit()
    return await _animal_out(db, animal, today(farm.timezone), farm.timezone)


@router.post("/{animal_id}/weight", status_code=201)
async def record_weight(
    animal_id: int,
    payload: WeightIn,
    db: DbSession,
    farm: CurrentFarm,
    user: CurrentUser,
    _perms: Annotated[set[str], Depends(require_perm("animals.weight"))],
) -> WeightRecordOut:
    animal = await _get_animal(db, farm.id, animal_id, for_update=True)
    if animal.status != AnimalStatus.ACTIVE.value:
        raise HTTPException(
            status_code=400,
            detail=f"{animal.tag_number} is {animal.status.lower()} — cannot record a weight.",
        )
    # finite/positive/future-date/bcs-range guards from v1 live in WeightIn's validators.
    record = WeightRecord(
        animal_id=animal.id,
        date=payload.date or today(farm.timezone),
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
    if payload.new_status in (AnimalStatus.SOLD.value, AnimalStatus.CULLED.value) and (
        animal.movement_restricted or animal.suspected_scheduled_disease
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "Sale/cull is blocked by an active movement restriction; "
                "record an authorised health clearance first"
            ),
        )
    status_date = payload.date or today(farm.timezone)
    animal.status = payload.new_status
    animal.status_date = status_date
    animal.status_notes = (payload.notes or "").strip() or None
    if payload.new_status == AnimalStatus.DEAD.value:
        animal.mortality_cause = (payload.mortality_cause or "").strip() or None
        animal.mortality_reported_at = payload.mortality_reported_at
        if payload.suspected_scheduled_disease:
            animal.suspected_scheduled_disease = True
            animal.suspected_disease = (payload.suspected_disease or "").strip() or None
            animal.authority_notified_at = payload.authority_notified_at
            animal.movement_restricted = True
            animal.restriction_reason = "Scheduled-disease suspicion recorded with mortality"
    # new_status is never ACTIVE here: clear the cull flag and stop the
    # animal's pending tasks (a dead/sold animal must not generate work).
    animal.cull_candidate = False

    # A sold/dead/culled doe cannot carry a pregnancy to term: auto-resolve
    # any live confirmed pregnancy as ABORTED instead of leaving a phantom
    # pregnancy on the kidding due lists forever (record_kidding would reject
    # the non-ACTIVE doe, and nothing else prompted mark_aborted). Lock order
    # is canonical: the animal lock above → breeding rows here → task locks
    # inside mark_aborted / skip_pending_tasks_for_animal.
    if animal.sex == "F":
        open_result = await db.execute(
            select(BreedingRecord)
            .where(
                BreedingRecord.doe_id == animal.id,
                BreedingRecord.outcome == BreedingOutcome.CONFIRMED_PREGNANT.value,
            )
            .with_for_update()
        )
        for br in open_result.scalars():
            await mark_aborted(db, br)
            if br.outcome == BreedingOutcome.ABORTED.value:
                # mark_aborted calls move_animal → RESTING, but move_animal
                # short-circuits on non-ACTIVE animals (she's already
                # SOLD/DEAD/CULLED at this point). Without an explicit
                # BucketMove the auto-abort would leave no marker on the
                # animal's profile trail. from == to reflects that the
                # doe's bucket did not change — only the pregnancy ended.
                db.add(
                    BucketMove(
                        animal_id=animal.id,
                        from_bucket=animal.current_bucket,
                        to_bucket=animal.current_bucket,
                        reason=f"Pregnancy auto-aborted — doe marked {payload.new_status.lower()}",
                        created_by_id=user.id,
                    )
                )

        # Move any of this doe's kids still in RECOVERY (i.e. still on her
        # lactating recipe) into their weaning bucket — otherwise they linger
        # in RECOVERY forever, keep drawing the lactating ration and never
        # get a fresh WEANING task since hers was just skipped.
        # Move-by-sex mirrors the natural weaning transition in
        # complete_task (WEANING).
        orphans_result = await db.execute(
            select(Animal)
            .where(
                Animal.farm_id == farm.id,
                Animal.dam_id == animal.id,
                Animal.status == AnimalStatus.ACTIVE.value,
                Animal.current_bucket == Bucket.RECOVERY.value,
            )
            .with_for_update()
        )
        orphan_reason = f"Dam marked {payload.new_status.lower()} — early wean"
        for kid in orphans_result.scalars():
            target = Bucket.MALE_KIDS.value if kid.sex == "M" else Bucket.FEMALE_KIDS.value
            move_animal(db, kid, target, orphan_reason, created_by_id=user.id)

    await skip_pending_tasks_for_animal(db, farm.id, animal.id)

    if payload.new_status == AnimalStatus.SOLD.value:
        animal.sale_price = money(payload.sale_price) if payload.sale_price is not None else None
        animal.buyer_name = (payload.buyer_name or "").strip() or None
        if payload.sale_price is not None:
            db.add(
                Transaction(
                    farm_id=farm.id,
                    date=status_date,
                    type=TransactionType.INCOME.value,
                    category=TransactionCategory.ANIMAL_SALE.value,
                    amount=money(payload.sale_price),
                    related_animal_id=animal.id,
                    notes=f"Sale of {animal.tag_number}"
                    + (f" to {animal.buyer_name}" if animal.buyer_name else ""),
                    created_by_id=user.id,
                    source_type="ANIMAL_SALE",
                    source_id=animal.id,
                )
            )
    await db.commit()
    return await _animal_out(db, animal, today(farm.timezone), farm.timezone)
