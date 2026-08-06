"""Kidding: upcoming/overdue due list, history, record a kidding."""

from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from ..deps import CurrentFarm, CurrentUser, DbSession, require_perm
from ..models import (
    Animal,
    BreedingOutcome,
    BreedingRecord,
    KiddingRecord,
    KidStatus,
)
from ..schemas.common import MAX_INT32_ID
from ..schemas.kidding import KiddingCreateIn, KiddingListOut, KiddingRecordOut, KidEntryOut
from ..services import record_kidding
from ..utils import today
from .breeding import _breeding_out

router = APIRouter(prefix="/api/kidding", tags=["kidding"])

NOT_FOUND = "Breeding record not found"
ALREADY_KIDDED = "This pregnancy already has a kidding record"


def _kidding_out(record: KiddingRecord) -> KiddingRecordOut:
    """Response model for a record whose kids/doe were eager-loaded (async
    sessions forbid implicit lazy loads)."""
    return KiddingRecordOut(
        id=record.id,
        doe_id=record.doe_id,
        date=record.date,
        breeding_record_id=record.breeding_record_id,
        ease=record.ease,
        notes=record.notes,
        kids=[KidEntryOut.model_validate(kid) for kid in record.kids],
        doe_tag=record.doe.tag_number,
    )


@router.get("")
async def kidding_list(
    db: DbSession,
    farm: CurrentFarm,
    perms: Annotated[set[str], Depends(require_perm("kidding.view"))],
) -> KiddingListOut:
    awaiting_result = await db.execute(
        select(BreedingRecord)
        .options(
            selectinload(BreedingRecord.doe),
            selectinload(BreedingRecord.buck),
            selectinload(BreedingRecord.kidding_record),
        )
        .where(
            BreedingRecord.farm_id == farm.id,
            BreedingRecord.outcome == BreedingOutcome.CONFIRMED_PREGNANT.value,
        )
        .order_by(BreedingRecord.expected_kidding_date)
    )
    awaiting = [r for r in awaiting_result.scalars() if r.kidding_record is None]
    now = today()
    horizon = now + timedelta(days=30)
    # Overdue pregnancies are not "upcoming" — they are listed separately.
    upcoming = [
        r for r in awaiting if r.expected_kidding_date and now <= r.expected_kidding_date <= horizon
    ]
    overdue = [r for r in awaiting if r.expected_kidding_date and r.expected_kidding_date < now]
    history_result = await db.execute(
        select(KiddingRecord)
        .options(selectinload(KiddingRecord.kids), selectinload(KiddingRecord.doe))
        .where(KiddingRecord.farm_id == farm.id)
        .order_by(KiddingRecord.date.desc(), KiddingRecord.id.desc())
        .limit(30)
    )
    return KiddingListOut(
        records=[_kidding_out(r) for r in history_result.scalars()],
        upcoming=[_breeding_out(r) for r in upcoming],
        overdue=[_breeding_out(r) for r in overdue],
    )


@router.post("", status_code=201)
async def create_kidding(
    payload: KiddingCreateIn,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    perms: Annotated[set[str], Depends(require_perm("kidding.manage"))],
) -> KiddingRecordOut:
    # Ids above the int4 PK ceiling cannot exist — 404, never an asyncpg
    # int32 DataError (500).
    if payload.breeding_record_id > MAX_INT32_ID:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    result = await db.execute(
        select(BreedingRecord)
        .options(selectinload(BreedingRecord.doe), selectinload(BreedingRecord.kidding_record))
        .where(BreedingRecord.id == payload.breeding_record_id)
    )
    br = result.scalar_one_or_none()
    if br is None or br.farm_id != farm.id:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    if br.kidding_record is not None:
        raise HTTPException(status_code=409, detail=ALREADY_KIDDED)
    # A kidding only makes sense against an ultrasound-confirmed pregnancy —
    # a forged request against a PENDING/FAILED/ABORTED breeding is rejected.
    if br.outcome != BreedingOutcome.CONFIRMED_PREGNANT.value:
        raise HTTPException(status_code=400, detail="Kidding requires a confirmed pregnancy")
    if payload.date > today():
        raise HTTPException(status_code=400, detail="Kidding date cannot be in the future")
    if payload.date < br.breeding_date:
        raise HTTPException(
            status_code=400, detail="Kidding date cannot be before the breeding date"
        )

    kids: list[dict[str, Any]] = []
    explicit_tags: list[str] = []  # alive-kid tags the user actually set (blank stays auto)
    for i, kid in enumerate(payload.kids):
        tag = (kid.tag or "").strip()
        if tag and kid.status == KidStatus.ALIVE.value:
            explicit_tags.append(tag)
        kids.append(
            {
                "tag": tag or f"{br.doe.tag_number}-K{i + 1}",
                "sex": kid.sex,
                "birth_weight": kid.birth_weight,
                "status": kid.status,
            }
        )

    # Tags are unique per farm — reject EXPLICIT duplicates (within the request
    # or against existing animals) instead of crashing on the constraint. Blank
    # tags are not checked: the service uniquifies auto tags (D-1-K1-2), so a
    # doe's second kidding isn't blocked by her first kidding's auto tags.
    if len(set(explicit_tags)) != len(explicit_tags):
        raise HTTPException(status_code=400, detail="Duplicate kid tags")
    if explicit_tags:
        clash = await db.execute(
            select(Animal.id).where(Animal.farm_id == farm.id, Animal.tag_number.in_(explicit_tags))
        )
        if clash.first() is not None:
            raise HTTPException(status_code=400, detail="A kid tag already exists in this farm")

    # SPEC defines ease as NORMAL | ASSISTED | DIFFICULT — the schema
    # (KiddingEaseStr) now matches KiddingEase exactly, so no coercion.
    ease = payload.ease
    try:
        record = await record_kidding(
            db, farm, br, payload.date, ease, payload.notes or "", kids, created_by_id=user.id
        )
        record_id = record.id
        await db.commit()
    except ValueError as exc:
        # Not a confirmed/kiddable pregnancy (or non-ACTIVE doe) — raced past the pre-checks.
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except IntegrityError:
        # A double submit raced past the pre-check into the breeding_record_id UNIQUE.
        await db.rollback()
        raise HTTPException(status_code=409, detail=ALREADY_KIDDED) from None

    # The service adds KidEntry rows without populating record.kids in memory —
    # re-fetch with eager loads for the response.
    refreshed = await db.execute(
        select(KiddingRecord)
        .options(selectinload(KiddingRecord.kids), selectinload(KiddingRecord.doe))
        .where(KiddingRecord.id == record_id)
    )
    return _kidding_out(refreshed.scalar_one())
