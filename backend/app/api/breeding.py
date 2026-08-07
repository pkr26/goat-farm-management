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
    mark_aborted,
    record_ultrasound_result,
)

router = APIRouter(prefix="/api/breeding", tags=["breeding"])

NOT_FOUND = "Breeding record not found"


def _breeding_out(br: BreedingRecord) -> BreedingRecordOut:
    """Response model for a record whose doe/buck/kidding_record were
    eager-loaded (async sessions forbid implicit lazy loads)."""
    return BreedingRecordOut(
        id=br.id,
        doe_id=br.doe_id,
        buck_id=br.buck_id,
        breeding_date=br.breeding_date,
        method=br.method,
        heat_cycle_number=br.heat_cycle_number,
        ultrasound_date=br.ultrasound_date,
        ultrasound_done=br.ultrasound_done,
        pregnant=br.pregnant,
        kid_count_detected=br.kid_count_detected,
        expected_kidding_date=br.expected_kidding_date,
        outcome=br.outcome,
        has_kidding=br.kidding_record is not None,
        doe_tag=br.doe.tag_number,
        buck_tag=br.buck.tag_number,
    )


async def _get_breeding_record(db: AsyncSession, farm: Farm, record_id: int) -> BreedingRecord:
    """Farm-scoped fetch with v1's `_bad_id` guard: a huge forged id must 404,
    never 500 (integer bindings overflow the int4 PK far below Python's
    unbounded int)."""
    if not 1 <= record_id <= MAX_INT32_ID:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    result = await db.execute(
        select(BreedingRecord)
        .options(
            selectinload(BreedingRecord.doe),
            selectinload(BreedingRecord.buck),
            selectinload(BreedingRecord.kidding_record),
        )
        .where(BreedingRecord.id == record_id)
    )
    br = result.scalar_one_or_none()
    if br is None or br.farm_id != farm.id:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return br


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
        records=[_breeding_out(br) for br in records_result.scalars()],
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
    return _breeding_out(await _get_breeding_record(db, farm, record_id))


@router.post("", status_code=201)
async def create_breeding(
    payload: BreedingCreateIn,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    perms: Annotated[set[str], Depends(require_perm("breeding.manage"))],
) -> BreedingRecordOut:
    # Ids above the int4 PK ceiling cannot exist — 404, never an asyncpg
    # int32 DataError (500).
    doe = await db.get(Animal, payload.doe_id) if payload.doe_id <= MAX_INT32_ID else None
    buck = await db.get(Animal, payload.buck_id) if payload.buck_id <= MAX_INT32_ID else None
    if doe is None or buck is None or doe.farm_id != farm.id or buck.farm_id != farm.id:
        raise HTTPException(status_code=404, detail="Doe or buck not found")
    # Same eligibility rules as v1's doe/buck pickers — a forged request
    # cannot breed a male, a sold doe, or an already-pregnant doe.
    eligible_doe_ids = {d.id for d in await breeding_candidate_does(db, farm)}
    if (
        doe.id not in eligible_doe_ids
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
    return _breeding_out(refreshed.scalar_one())


@router.post("/{record_id}/ultrasound")
async def submit_ultrasound(
    record_id: int,
    payload: UltrasoundIn,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    perms: Annotated[set[str], Depends(require_perm("breeding.manage"))],
) -> BreedingRecordOut:
    br = await _get_breeding_record(db, farm, record_id)
    if br.outcome != BreedingOutcome.PENDING.value:
        # Result already recorded — no replays (the service would no-op anyway).
        raise HTTPException(status_code=409, detail="Ultrasound result already recorded")
    # kid_count is ignored unless pregnant — the service nulls it otherwise.
    await record_ultrasound_result(
        db, br, payload.pregnant, payload.kid_count, created_by_id=user.id
    )
    await db.commit()
    return _breeding_out(br)


@router.post("/{record_id}/abort")
async def abort_pregnancy(
    record_id: int,
    db: DbSession,
    farm: CurrentFarm,
    perms: Annotated[set[str], Depends(require_perm("breeding.manage"))],
) -> BreedingRecordOut:
    br = await _get_breeding_record(db, farm, record_id)
    if br.outcome != BreedingOutcome.CONFIRMED_PREGNANT.value or br.kidding_record is not None:
        # mark_aborted no-ops here — surface the rejection, never a fake success.
        raise HTTPException(
            status_code=409,
            detail="Only a confirmed pregnancy without a kidding record can be aborted",
        )
    await mark_aborted(db, br)
    await db.commit()
    return _breeding_out(br)
