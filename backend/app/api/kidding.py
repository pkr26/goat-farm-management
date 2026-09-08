"""Kidding: upcoming/overdue due list, history, record a kidding."""

import re
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.elements import ColumnElement

from ..deps import CurrentFarm, CurrentUser, DbSession, require_perm
from ..models import (
    Animal,
    AnimalStatus,
    BreedingOutcome,
    BreedingRecord,
    KiddingRecord,
    KidEntry,
)
from ..models.species import GOAT_PROFILE
from ..schemas.breeding import BreedingRecordOut
from ..schemas.common import COMMON_ERROR_RESPONSES, MAX_INT32_ID, MAX_PAGE_OFFSET
from ..schemas.kidding import KiddingCreateIn, KiddingListOut, KiddingRecordOut, KidEntryOut
from ..services import (
    IdempotencyKey,
    KidSpec,
    LitterSizeError,
    execute_idempotent,
    record_kidding,
    require_farm_not_future,
)
from ..utils import today
from ._shared import breeding_out

router = APIRouter(prefix="/api/kidding", tags=["kidding"], responses=COMMON_ERROR_RESPONSES)

NOT_FOUND = "Breeding record not found"
ALREADY_KIDDED = "This pregnancy already has a kidding record"
DUE_DEFAULT_LIMIT = 30
DUE_MAX_LIMIT = 100


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
    limit: Annotated[int, Query(ge=1, le=200)] = 30,
    offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)] = 0,
    upcoming_limit: Annotated[int, Query(ge=1, le=DUE_MAX_LIMIT)] = DUE_DEFAULT_LIMIT,
    upcoming_offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)] = 0,
    overdue_limit: Annotated[int, Query(ge=1, le=DUE_MAX_LIMIT)] = DUE_DEFAULT_LIMIT,
    overdue_offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)] = 0,
) -> KiddingListOut:
    now = today(farm.timezone)
    horizon = now + timedelta(days=30)
    awaiting_where = (
        BreedingRecord.farm_id == farm.id,
        BreedingRecord.outcome == BreedingOutcome.CONFIRMED_PREGNANT.value,
        BreedingRecord.expected_kidding_date.is_not(None),
        # Defensive: a sold/dead doe's pregnancy is auto-resolved on the
        # status change, but legacy phantom rows must never list here.
        Animal.status == AnimalStatus.ACTIVE.value,
        KiddingRecord.id.is_(None),
    )

    async def due_page(
        *date_predicates: ColumnElement[bool], page_limit: int, page_offset: int
    ) -> tuple[list[BreedingRecord], int]:
        joins = (
            select(BreedingRecord)
            .join(Animal, BreedingRecord.doe_id == Animal.id)
            .outerjoin(
                KiddingRecord,
                KiddingRecord.breeding_record_id == BreedingRecord.id,
            )
            .where(*awaiting_where, *date_predicates)
        )
        total = (await db.execute(select(func.count()).select_from(joins.subquery()))).scalar_one()
        rows = await db.execute(
            joins.options(
                selectinload(BreedingRecord.doe),
                selectinload(BreedingRecord.buck),
                selectinload(BreedingRecord.kidding_record),
            )
            .order_by(BreedingRecord.expected_kidding_date, BreedingRecord.id)
            .offset(page_offset)
            .limit(page_limit)
        )
        return list(rows.scalars()), int(total)

    # Overdue pregnancies are not "upcoming" — each is an independent,
    # bounded SQL page rather than a Python slice of the farm's full history.
    upcoming, upcoming_total = await due_page(
        BreedingRecord.expected_kidding_date >= now,
        BreedingRecord.expected_kidding_date <= horizon,
        page_limit=upcoming_limit,
        page_offset=upcoming_offset,
    )
    overdue, overdue_total = await due_page(
        BreedingRecord.expected_kidding_date < now,
        page_limit=overdue_limit,
        page_offset=overdue_offset,
    )
    history_result = await db.execute(
        select(KiddingRecord)
        .options(selectinload(KiddingRecord.kids), selectinload(KiddingRecord.doe))
        .where(KiddingRecord.farm_id == farm.id)
        .order_by(KiddingRecord.date.desc(), KiddingRecord.id.desc())
        .offset(offset)
        .limit(limit)
    )
    total = (
        await db.execute(
            select(func.count()).select_from(KiddingRecord).where(KiddingRecord.farm_id == farm.id)
        )
    ).scalar_one()
    return KiddingListOut(
        records=[_kidding_out(r) for r in history_result.scalars()],
        upcoming=[breeding_out(r) for r in upcoming],
        upcoming_total=upcoming_total,
        upcoming_limit=upcoming_limit,
        upcoming_offset=upcoming_offset,
        overdue=[breeding_out(r) for r in overdue],
        overdue_total=overdue_total,
        overdue_limit=overdue_limit,
        overdue_offset=overdue_offset,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/pregnancies/{breeding_record_id}")
async def kidding_pregnancy(
    breeding_record_id: int,
    db: DbSession,
    farm: CurrentFarm,
    perms: Annotated[set[str], Depends(require_perm("kidding.view"))],
) -> BreedingRecordOut:
    """Resolve one live pregnancy for a task/deep-link independent of pages."""
    if not 1 <= breeding_record_id <= MAX_INT32_ID:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    record = (
        await db.execute(
            select(BreedingRecord)
            .join(Animal, BreedingRecord.doe_id == Animal.id)
            .outerjoin(
                KiddingRecord,
                KiddingRecord.breeding_record_id == BreedingRecord.id,
            )
            .options(
                selectinload(BreedingRecord.doe),
                selectinload(BreedingRecord.buck),
                selectinload(BreedingRecord.kidding_record),
            )
            .where(
                BreedingRecord.id == breeding_record_id,
                BreedingRecord.farm_id == farm.id,
                BreedingRecord.outcome == BreedingOutcome.CONFIRMED_PREGNANT.value,
                BreedingRecord.expected_kidding_date.is_not(None),
                Animal.status == AnimalStatus.ACTIVE.value,
                KiddingRecord.id.is_(None),
            )
        )
    ).scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return breeding_out(record)


@router.post("", status_code=201)
async def create_kidding(
    payload: KiddingCreateIn,
    response: Response,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    perms: Annotated[set[str], Depends(require_perm("kidding.manage"))],
    idempotency_key: IdempotencyKey = None,
) -> KiddingRecordOut:
    # Deterministic input-shape check: ids above the int4 PK ceiling cannot
    # exist — 404, never an asyncpg int32 DataError (500). Scalar pre-check
    # only: the locked ORM fetch below must be the first ORM load so its
    # attributes come from the post-lock read (an earlier ORM load would
    # poison the identity map). Everything state-dependent lives inside
    # mutate() so an idempotent replay never re-evaluates it.
    if payload.breeding_record_id > MAX_INT32_ID:
        raise HTTPException(status_code=404, detail=NOT_FOUND)

    async def mutate() -> KiddingRecordOut:
        row = (
            await db.execute(
                select(
                    BreedingRecord.doe_id,
                    BreedingRecord.buck_id,
                    BreedingRecord.farm_id,
                ).where(
                    BreedingRecord.id == payload.breeding_record_id,
                    BreedingRecord.farm_id == farm.id,
                )
            )
        ).first()
        if row is None:
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        # Canonical lock order (all referenced animals by id → breeding → task,
        # matching create-breeding): recording a live kid inserts both dam and sire
        # FKs. Locking only the doe allowed a concurrent re-breeding request to hold
        # the lower-id buck while waiting for this doe, while this transaction then
        # waited for the buck's FK KEY SHARE lock — a deterministic deadlock. Taking
        # both parents in one ordered statement closes that cycle and also keeps the
        # existing abort-vs-kidding serialization guarantee.
        # buck_id is NULL for AI services — the semen sire is not a herd animal.
        parent_ids = sorted({row.doe_id} | ({row.buck_id} if row.buck_id is not None else set()))
        locked_parent_ids = list(
            (
                await db.execute(
                    select(Animal.id)
                    .where(Animal.farm_id == farm.id, Animal.id.in_(parent_ids))
                    .order_by(Animal.id)
                    .with_for_update()
                )
            ).scalars()
        )
        if locked_parent_ids != parent_ids:  # defensive against corrupted legacy rows
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        result = await db.execute(
            select(BreedingRecord)
            .options(selectinload(BreedingRecord.doe), selectinload(BreedingRecord.kidding_record))
            .where(
                BreedingRecord.id == payload.breeding_record_id,
                BreedingRecord.farm_id == farm.id,
            )
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
        try:
            require_farm_not_future(payload.date, farm, "kidding date")
            for kid in payload.kids:
                if kid.mortality_reported_at is not None:
                    require_farm_not_future(
                        kid.mortality_reported_at, farm, "mortality_reported_at"
                    )
                    if kid.mortality_reported_at < payload.date:
                        raise ValueError("mortality_reported_at cannot predate the kidding date")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        if payload.date < br.breeding_date:
            raise HTTPException(
                status_code=400, detail="Kidding date cannot be before the breeding date"
            )

        kids: list[KidSpec] = []
        explicit_tags: list[str] = []  # user-set tags; blank stays auto-generated
        # Species-banded birth weights: a kid/calf is not born at 950 kg, and
        # birth weight coalesces into "latest weight" downstream, where a
        # fabricated value would permanently satisfy the breeding weight gates.
        profile = GOAT_PROFILE
        for i, kid in enumerate(payload.kids):
            if (
                kid.birth_weight is not None
                and not profile.birth_weight_kg_range[0]
                <= kid.birth_weight
                <= profile.birth_weight_kg_range[1]
            ):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"{profile.young} birth weight must be between "
                        f"{profile.birth_weight_kg_range[0]:g} and "
                        f"{profile.birth_weight_kg_range[1]:g} kg — "
                        f"{kid.birth_weight:g} kg is not a credible newborn weight"
                    ),
                )
            tag = (kid.tag or "").strip()
            if tag:
                explicit_tags.append(tag)
            kids.append(
                {
                    "tag": tag or f"{br.doe.tag_number}-K{i + 1}",
                    "tag_is_explicit": bool(tag),
                    "sex": kid.sex,
                    "birth_weight": kid.birth_weight,
                    "status": kid.status,
                    "mortality_reported_at": kid.mortality_reported_at,
                }
            )

        # Tags are unique per farm — reject EXPLICIT duplicates (within the request
        # or against existing animals) instead of crashing on the constraint. Blank
        # tags are not checked: the service uniquifies auto tags (D-1-K1-2), so a
        # doe's second kidding isn't blocked by her first kidding's auto tags.
        if len(set(explicit_tags)) != len(explicit_tags):
            raise HTTPException(status_code=400, detail="Duplicate kid tags")
        if explicit_tags:
            animal_clash = await db.execute(
                select(Animal.id).where(
                    Animal.farm_id == farm.id, Animal.tag_number.in_(explicit_tags)
                )
            )
            kid_clash = await db.execute(
                select(KidEntry.id).where(
                    KidEntry.farm_id == farm.id,
                    KidEntry.tag.in_(explicit_tags),
                )
            )
            if animal_clash.first() is not None or kid_clash.first() is not None:
                raise HTTPException(status_code=400, detail="A kid tag already exists in this farm")

        # SPEC defines ease as NORMAL | ASSISTED | DIFFICULT — the schema
        # (KiddingEaseStr) now matches KiddingEase exactly, so no coercion.
        ease = payload.ease
        try:
            record = await record_kidding(
                db, farm, br, payload.date, ease, payload.notes or "", kids, created_by_id=user.id
            )
            record_id = record.id
        except LitterSizeError as exc:
            # A litter above the species cap is input-shape validation.
            await db.rollback()
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except ValueError as exc:
            # Every other ValueError here is a raced lifecycle state → conflict.
            await db.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except IntegrityError as exc:
            # Two constraints can trip here: the breeding_record_id UNIQUE (a
            # double submit raced past the pre-check) or uq_animal_tag_per_farm
            # (a concurrent insert won an explicit kid tag, or an auto
            # <doe>-K<n> tag raced _unique_tag's pre-insert snapshot). Answer
            # each with its own pre-check's status/message, never a bare 500.
            await db.rollback()
            if _unique_constraint_name(exc) in {
                "uq_animal_tag_per_farm",
                "uq_kid_entries_farm_tag",
                "uq_stillborn_tag_farm_namespace",
            }:
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

    return await execute_idempotent(
        db,
        http_response=response,
        key=idempotency_key,
        farm_id=farm.id,
        actor_id=user.id,
        operation="POST /api/kidding",
        payload=payload,
        path_identity={},
        success_status=201,
        response_type=KiddingRecordOut,
        mutate=mutate,
    )
