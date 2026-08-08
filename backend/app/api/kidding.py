"""Kidding: upcoming/overdue due list, history, record a kidding."""

import re
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from ..deps import CurrentFarm, CurrentUser, DbSession, require_perm
from ..models import (
    Animal,
    AnimalStatus,
    BreedingOutcome,
    BreedingRecord,
    KiddingRecord,
    KidStatus,
)
from ..schemas.common import MAX_INT32_ID
from ..schemas.kidding import KiddingCreateIn, KiddingListOut, KiddingRecordOut, KidEntryOut
from ..services import KidSpec, record_kidding
from ..utils import today
from ._shared import breeding_out

router = APIRouter(prefix="/api/kidding", tags=["kidding"])

NOT_FOUND = "Breeding record not found"
ALREADY_KIDDED = "This pregnancy already has a kidding record"


def _unique_constraint_name(exc: IntegrityError) -> str | None:
    """Name of the unique constraint an IntegrityError tripped, or None.
    SQLAlchemy's asyncpg adaptation stringifies the driver error (no
    ``diag.constraint_name`` like psycopg), so the name is recovered from
    the message when the driver attribute is absent."""
    orig = getattr(exc, "orig", None)
    name = getattr(orig, "constraint_name", None)
    if isinstance(name, str):
        return name
    match = re.search(r'violates unique constraint "([^"]+)"', str(orig))
    return match.group(1) if match else None


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
        .join(Animal, BreedingRecord.doe_id == Animal.id)
        .where(
            BreedingRecord.farm_id == farm.id,
            BreedingRecord.outcome == BreedingOutcome.CONFIRMED_PREGNANT.value,
            # Defensive: a sold/dead doe's pregnancy is auto-resolved on the
            # status change, but legacy phantom rows must never list here.
            Animal.status == AnimalStatus.ACTIVE.value,
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
        upcoming=[breeding_out(r) for r in upcoming],
        overdue=[breeding_out(r) for r in overdue],
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
    # int32 DataError (500). Scalar pre-check only: the locked ORM fetch below
    # must be the first ORM load so its attributes come from the post-lock
    # read (an earlier ORM load would poison the identity map).
    if payload.breeding_record_id > MAX_INT32_ID:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    row = (
        await db.execute(
            select(BreedingRecord.doe_id, BreedingRecord.farm_id).where(
                BreedingRecord.id == payload.breeding_record_id
            )
        )
    ).first()
    if row is None or row.farm_id != farm.id:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    # Canonical lock order (animal → breeding → task, same as the abort and
    # ultrasound flows): a concurrent abort serializes against this — the
    # loser re-reads the committed outcome and fails its state guard, so a
    # pregnancy can never end ABORTED with live born kids.
    await db.execute(select(Animal.id).where(Animal.id == row.doe_id).with_for_update())
    result = await db.execute(
        select(BreedingRecord)
        .options(selectinload(BreedingRecord.doe), selectinload(BreedingRecord.kidding_record))
        .where(BreedingRecord.id == payload.breeding_record_id)
        .with_for_update()
    )
    br = result.scalar_one_or_none()
    if br is None:  # pragma: no cover — the scalar pre-check found the row
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    if br.kidding_record is not None:
        raise HTTPException(status_code=409, detail=ALREADY_KIDDED)
    # A kidding only makes sense against an ultrasound-confirmed pregnancy —
    # a forged request against a PENDING/FAILED/ABORTED breeding is rejected.
    if br.outcome != BreedingOutcome.CONFIRMED_PREGNANT.value:
        raise HTTPException(status_code=400, detail="Kidding requires a confirmed pregnancy")
    # "Not in the future" is enforced by the schema (PastOrTodayDate, with the
    # one-day east-of-UTC headroom); only the breeding-date bound remains here.
    if payload.date < br.breeding_date:
        raise HTTPException(
            status_code=400, detail="Kidding date cannot be before the breeding date"
        )

    kids: list[KidSpec] = []
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
    except IntegrityError as exc:
        # Two constraints can trip here: the breeding_record_id UNIQUE (a
        # double submit raced past the pre-check) or uq_animal_tag_per_farm
        # (a concurrent insert won an explicit kid tag, or an auto
        # <doe>-K<n> tag raced _unique_tag's pre-insert snapshot). Answer
        # each with its own pre-check's status/message, never a bare 500.
        await db.rollback()
        if _unique_constraint_name(exc) == "uq_animal_tag_per_farm":
            raise HTTPException(
                status_code=400, detail="A kid tag already exists in this farm"
            ) from None
        raise HTTPException(status_code=409, detail=ALREADY_KIDDED) from None

    # The service adds KidEntry rows without populating record.kids in memory —
    # re-fetch with eager loads for the response.
    refreshed = await db.execute(
        select(KiddingRecord)
        .options(selectinload(KiddingRecord.kids), selectinload(KiddingRecord.doe))
        .where(KiddingRecord.id == record_id)
    )
    return _kidding_out(refreshed.scalar_one())
