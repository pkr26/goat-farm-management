"""Breeding: records list/detail, add breeding, ultrasound result, abort."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..deps import CurrentFarm, CurrentUser, DbSession, require_perm
from ..models import Animal, AnimalStatus, BreedingOutcome, BreedingRecord, Farm
from ..schemas.breeding import (
    BreedingCreateIn,
    BreedingListOut,
    BreedingRecordOut,
    UltrasoundIn,
)
from ..schemas.common import MAX_INT32_ID
from ..services import (
    breeding_candidate_does,
    create_breeding_record,
    doe_has_open_breeding,
    is_breeding_candidate,
    mark_aborted,
    record_ultrasound_result,
)
from ._shared import breeding_out

router = APIRouter(prefix="/api/breeding", tags=["breeding"])

NOT_FOUND = "Breeding record not found"

# History is newest-first and capped: the unbounded list grew by
# ~2 cycles/doe/year. The picker payloads (candidate does, active bucks) ride
# along unchanged.
BREEDING_HISTORY_LIMIT = 100


# breeding_out lives in `._shared`.


async def _get_breeding_record(
    db: AsyncSession, farm: Farm, record_id: int, *, for_update: bool = False
) -> BreedingRecord:
    """Farm-scoped fetch with v1's `_bad_id` guard: a huge forged id must 404,
    never 500 (integer bindings overflow the int4 PK far below Python's
    unbounded int)."""
    if not 1 <= record_id <= MAX_INT32_ID:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    stmt = (
        select(BreedingRecord)
        .options(
            selectinload(BreedingRecord.doe),
            selectinload(BreedingRecord.buck),
            selectinload(BreedingRecord.kidding_record),
        )
        .where(BreedingRecord.id == record_id)
    )
    if for_update:
        # SELECT ... FOR UPDATE: concurrent ultrasound/abort/kidding calls
        # serialize on the row — the loser re-reads the committed outcome and
        # fails its state check instead of double-applying the side effects
        # (follow-up tasks, bucket moves) a second time. Same pattern as
        # api/tasks.py's _get_task.
        stmt = stmt.with_for_update()
    result = await db.execute(stmt)
    br = result.scalar_one_or_none()
    if br is None or br.farm_id != farm.id:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return br


async def _lock_doe_then_breeding_record(
    db: AsyncSession, farm: Farm, record_id: int
) -> BreedingRecord:
    """Fetch the record under locks in the canonical order: ANIMAL first,
    then the breeding row (task locks come later, inside the services).
    change_status locks animal → (breeding) → tasks; taking the same order
    here is what keeps ultrasound/abort/kidding from deadlocking against a
    concurrent sale/death of the doe."""
    # Scalar pre-check only: loading the ORM row here would poison the
    # identity map with pre-lock state, and the locked fetch below must be
    # the first ORM load so its attributes come from the post-lock read.
    if not 1 <= record_id <= MAX_INT32_ID:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    row = (
        await db.execute(
            select(BreedingRecord.doe_id, BreedingRecord.farm_id).where(
                BreedingRecord.id == record_id
            )
        )
    ).first()
    if row is None or row.farm_id != farm.id:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    await db.execute(select(Animal.id).where(Animal.id == row.doe_id).with_for_update())
    return await _get_breeding_record(db, farm, record_id, for_update=True)


@router.get("")
async def breeding_list(
    db: DbSession,
    farm: CurrentFarm,
    perms: Annotated[set[str], Depends(require_perm("breeding.view"))],
) -> BreedingListOut:
    records_result = await db.execute(
        select(BreedingRecord)
        .options(
            selectinload(BreedingRecord.doe),
            selectinload(BreedingRecord.buck),
            selectinload(BreedingRecord.kidding_record),
        )
        .where(BreedingRecord.farm_id == farm.id)
        .order_by(BreedingRecord.breeding_date.desc(), BreedingRecord.id.desc())
        .limit(BREEDING_HISTORY_LIMIT)
    )
    # Candidate pickers from v1's "new breeding" form ride along in the list
    # payload: eligible does per SPEC rules, active males as bucks.
    does = await breeding_candidate_does(db, farm)
    bucks_result = await db.execute(
        select(Animal.id)
        .where(
            Animal.farm_id == farm.id,
            Animal.sex == "M",
            Animal.status == AnimalStatus.ACTIVE.value,
        )
        .order_by(Animal.tag_number)
    )
    return BreedingListOut(
        records=[breeding_out(br) for br in records_result.scalars()],
        candidate_doe_ids=[doe.id for doe in does],
        active_buck_ids=list(bucks_result.scalars()),
    )


@router.get("/{record_id}")
async def get_breeding_record(
    record_id: int,
    db: DbSession,
    farm: CurrentFarm,
    perms: Annotated[set[str], Depends(require_perm("breeding.view"))],
) -> BreedingRecordOut:
    return breeding_out(await _get_breeding_record(db, farm, record_id))


@router.post("", status_code=201)
async def create_breeding(
    payload: BreedingCreateIn,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    perms: Annotated[set[str], Depends(require_perm("breeding.manage"))],
) -> BreedingRecordOut:
    # Ids above the int4 PK ceiling cannot exist — 404, never an asyncpg
    # int32 DataError (500). The doe row is locked FOR UPDATE: a concurrent
    # sale/death must serialize against the breeding — the loser re-reads the
    # committed status and fails the eligibility check below instead of
    # leaving an open PENDING breeding on a non-ACTIVE doe.
    doe: Animal | None = None
    if payload.doe_id <= MAX_INT32_ID:
        doe_result = await db.execute(
            select(Animal)
            # The eligibility predicate reads these relationships.
            .options(
                selectinload(Animal.weight_records),
                selectinload(Animal.breedings_as_doe).selectinload(BreedingRecord.kidding_record),
            )
            .where(Animal.id == payload.doe_id)
            .with_for_update()
        )
        doe = doe_result.scalar_one_or_none()
    buck = await db.get(Animal, payload.buck_id) if payload.buck_id <= MAX_INT32_ID else None
    if doe is None or buck is None or doe.farm_id != farm.id or buck.farm_id != farm.id:
        raise HTTPException(status_code=404, detail="Doe or buck not found")
    # Same eligibility rules as v1's doe/buck pickers — a forged request
    # cannot breed a male, a sold doe, or an already-pregnant doe. Targeted
    # one-doe check: no full candidate-set build per create.
    if (
        not is_breeding_candidate(doe, has_open_breeding=await doe_has_open_breeding(db, doe.id))
        or buck.sex != "M"
        or buck.status != AnimalStatus.ACTIVE.value
    ):
        raise HTTPException(
            status_code=400,
            detail="Doe is not eligible for breeding, or the buck is not an active male",
        )
    doe_tag = doe.tag_number  # capture pre-rollback: rollback expires ORM attrs
    try:
        br = await create_breeding_record(
            db,
            farm,
            doe,
            buck,
            payload.breeding_date,
            payload.heat_cycle_number,
            created_by_id=user.id,
        )
        br_id = br.id
        await db.commit()
    except ValueError as exc:
        # Doe already has an unresolved breeding/pregnancy (raced/forged request).
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except IntegrityError:
        # A concurrent create raced the pre-check into the
        # uq_breeding_open_pregnancy partial UNIQUE (one PENDING per doe).
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"{doe_tag} already has an unresolved breeding/pregnancy",
        ) from None
    # Re-fetch with eager loads: the fresh row has no relationships loaded, and
    # async sessions forbid the lazy load a response build would trigger.
    refreshed = await db.execute(
        select(BreedingRecord)
        .options(
            selectinload(BreedingRecord.doe),
            selectinload(BreedingRecord.buck),
            selectinload(BreedingRecord.kidding_record),
        )
        .where(BreedingRecord.id == br_id)
    )
    return breeding_out(refreshed.scalar_one())


@router.post("/{record_id}/ultrasound")
async def submit_ultrasound(
    record_id: int,
    payload: UltrasoundIn,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    perms: Annotated[set[str], Depends(require_perm("breeding.manage"))],
) -> BreedingRecordOut:
    br = await _lock_doe_then_breeding_record(db, farm, record_id)
    if br.outcome != BreedingOutcome.PENDING.value:
        # Result already recorded — no replays (the service would no-op anyway).
        # Re-checked under the row lock, so a raced double submit serializes:
        # the loser re-reads the committed outcome and lands here.
        raise HTTPException(status_code=409, detail="Ultrasound result already recorded")
    # kid_count is ignored unless pregnant — the service nulls it otherwise.
    try:
        await record_ultrasound_result(
            db, br, payload.pregnant, payload.kid_count, created_by_id=user.id
        )
    except ValueError as exc:
        # The doe was sold/died with this PENDING breeding still open.
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from None
    await db.commit()
    return breeding_out(br)


@router.post("/{record_id}/abort")
async def abort_pregnancy(
    record_id: int,
    db: DbSession,
    farm: CurrentFarm,
    perms: Annotated[set[str], Depends(require_perm("breeding.manage"))],
) -> BreedingRecordOut:
    br = await _lock_doe_then_breeding_record(db, farm, record_id)
    if br.outcome != BreedingOutcome.CONFIRMED_PREGNANT.value or br.kidding_record is not None:
        # mark_aborted no-ops here — surface the rejection, never a fake success.
        # Re-checked under the row lock: a raced double-abort (or a kidding
        # that committed while we waited) re-reads and lands here.
        raise HTTPException(
            status_code=409,
            detail="Only a confirmed pregnancy without a kidding record can be aborted",
        )
    await mark_aborted(db, br)
    await db.commit()
    return breeding_out(br)
