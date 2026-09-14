"""Animals module: list/filters, create, profile, bucket moves, weights, status."""

import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, literal, or_, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import CurrentFarm, CurrentUser, DbSession, require_perm
from ..models import (
    HISTORY_OVERRIDE_REASON_PREFIX,
    MEAT_SALE_AGE_MONTHS,
    MEAT_SALE_WEIGHT_KG,
    Animal,
    AnimalStatus,
    BreedingOutcome,
    BreedingRecord,
    Bucket,
    BucketMove,
    HealthEvent,
    KiddingRecord,
    KidEntry,
    KidStatus,
    PurchaseBatch,
    Task,
    TaskStatus,
    Transaction,
    TransactionCategory,
    TransactionType,
    WeightRecord,
    quarantine_schedule,
)
from ..models.species import GOAT_PROFILE
from ..schemas.animals import (
    AnimalCreateIn,
    AnimalListOut,
    AnimalOffspringOut,
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
from ..schemas.common import COMMON_ERROR_RESPONSES, MAX_INT32_ID, MAX_PAGE_OFFSET, PostgresText
from ..schemas.health import HealthEventOut
from ..services import (
    IdempotencyKey,
    create_purchase_batch,
    doe_has_open_breeding,
    execute_idempotent,
    generate_unique_tag,
    mark_aborted,
    move_animal,
    place_movement_restriction,
    replan_dam_after_last_kid_death,
    require_animal_event_chronology,
    require_bucket_transition,
    require_farm_not_future,
    require_status_after_recorded_facts,
    schedule_quarantine_tasks,
    skip_pending_tasks_for_animal,
)

# Not re-exported from ``..services`` yet; imported from their modules so the
# batch-duty sweep can run beside the per-animal one and the status sweep can
# close a never-scanned service.
from ..services.animals import skip_pending_tasks_for_empty_batch
from ..services.breeding import mark_unassessed
from ..utils import MONEY_QUANTUM, money, today
from ._shared import AnimalComputedFacts, animal_computed_facts, animal_out, unique_constraint_name

router = APIRouter(prefix="/api/animals", tags=["animals"], responses=COMMON_ERROR_RESPONSES)

NOT_FOUND = "Animal not found"
PROFILE_HISTORY_DEFAULT_LIMIT = 25
PROFILE_HISTORY_MAX_LIMIT = 100
SALE_CAPABLE_STATUSES = frozenset({AnimalStatus.SOLD.value, AnimalStatus.CULLED.value})


async def _profile_computed_facts(
    db: AsyncSession,
    animal: Animal,
    reference_date: date,
    timezone_name: str,
) -> AnimalComputedFacts:
    """Current profile facts without lifetime relationship hydration."""
    return (await animal_computed_facts(db, [animal], reference_date, timezone_name))[animal.id]


async def _get_animal(
    db: AsyncSession,
    farm_id: int,
    animal_id: int,
    *,
    for_update: bool = False,
) -> Animal:
    # Ids above the int4 PK ceiling cannot exist — 404, never an asyncpg
    # int32 DataError (500).
    animal: Animal | None
    if not 1 <= animal_id <= MAX_INT32_ID:
        animal = None
    elif for_update:
        # SELECT ... FOR UPDATE: concurrent mutations (e.g. two sales) take
        # the row lock in turn — the loser re-reads the committed row and
        # fails the state check instead of double-applying side effects.
        stmt = select(Animal).where(Animal.id == animal_id, Animal.farm_id == farm_id)
        result = await db.execute(stmt.with_for_update())
        animal = result.scalar_one_or_none()
    else:
        stmt = select(Animal).where(Animal.id == animal_id, Animal.farm_id == farm_id)
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
    permissions: set[str],
) -> AnimalOut:
    """Serialize current facts without loading the animal's lifetime history."""
    animal_id = animal.id  # read before expire: expired attrs can't be touched
    db.expire(animal)
    result = await db.execute(select(Animal).where(Animal.id == animal_id))
    refreshed = result.scalar_one()
    computed = await _profile_computed_facts(db, refreshed, reference_date, timezone_name)
    return animal_out(
        refreshed,
        reference_date,
        timezone_name,
        permissions=permissions,
        computed=computed,
    )


_QUARANTINE_TITLE_PREFIX = re.compile(r"^\[[^\]]*\]\s*")


def _quarantine_title_phrase(title: str) -> str:
    """Title without its bracketed batch/supplier prefix.

    The pristine-protocol shape check compares expected schedule titles with
    stored rows; titles built since RT-HIJ-3 carry ``[Batch #id]`` while
    legacy rows may still carry ``[<supplier> #id]``. Comparing the phrase
    (everything after the first bracketed prefix — mirroring the frontend
    task-prefill parser) keeps the check meaningful for both generations
    without weakening it: due dates, categories, count and all completion
    metadata must still match exactly.
    """
    return _QUARANTINE_TITLE_PREFIX.sub("", title, count=1)


async def _lock_pristine_batch_protocol_for_quarantine_reentry(
    db: AsyncSession, farm_id: int, purchase_batch_id: int
) -> None:
    """Allow a batch animal to re-enter quarantine only before work starts.

    The caller already holds the Animal row.  Locking Batch and then its Tasks
    preserves the status/removal/release order ``Animal -> Batch -> Task`` and
    makes a concurrent protocol completion choose one deterministic outcome:
    either the re-entry commits first and is part of that later protocol fact,
    or the fact commits first and the re-entry is refused.

    A valid purchase owns exactly the generated quarantine schedule.  Fetch
    only ``expected + 1`` rows so corrupt legacy data cannot turn this guard
    into an unbounded request; the extra row is enough to prove overflow.
    Rejected verification work is PENDING again but has already recorded a
    fact, so completion/rejection metadata must also remain pristine.
    """
    batch = (
        await db.execute(
            select(PurchaseBatch)
            .where(
                PurchaseBatch.id == purchase_batch_id,
                PurchaseBatch.farm_id == farm_id,
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if batch is None:
        raise HTTPException(
            status_code=409,
            detail="This animal's purchase-batch quarantine protocol is unavailable.",
        )

    expected = quarantine_schedule(batch)
    tasks = list(
        (
            await db.execute(
                select(Task)
                .where(
                    Task.farm_id == farm_id,
                    Task.purchase_batch_id == batch.id,
                )
                .order_by(Task.id)
                .limit(len(expected) + 1)
                .execution_options(populate_existing=True)
                .with_for_update()
            )
        ).scalars()
    )
    expected_shape = sorted(
        (item["due_date"], item["category"], _quarantine_title_phrase(item["title"]))
        for item in expected
    )
    actual_shape = sorted(
        (task.due_date, task.category, _quarantine_title_phrase(task.title)) for task in tasks
    )
    pristine = actual_shape == expected_shape and all(
        task.auto_generated
        and task.animal_id is None
        and task.breeding_record_id is None
        and task.recur_days is None
        and task.recurring_series_id is None
        and task.status == TaskStatus.PENDING.value
        and task.completed_by_id is None
        and task.completed_at is None
        and task.verified_by_id is None
        and task.verified_at is None
        and task.skipped_by_id is None
        and task.skipped_at is None
        and task.skip_reason is None
        and task.rejected_by_id is None
        and task.rejected_at is None
        and task.verification_note is None
        for task in tasks
    )
    if not pristine:
        raise HTTPException(
            status_code=409,
            detail=(
                "This animal cannot leave quarantine by override because its "
                "purchase-batch protocol has started, ended, or is incomplete."
            ),
        )


@router.get("")
async def list_animals(
    db: DbSession,
    farm: CurrentFarm,
    perms: Annotated[set[str], Depends(require_perm("animals.view"))],
    bucket: BucketStr | None = None,
    sex: Sex | None = None,
    status: AnimalStatusStr | None = None,
    include_all_statuses: bool = False,
    q: Annotated[PostgresText | None, Query(max_length=60)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)] = 0,
) -> AnimalListOut:
    stmt = select(Animal).where(Animal.farm_id == farm.id)
    if bucket is not None:
        stmt = stmt.where(Animal.current_bucket == bucket)
    if sex is not None:
        stmt = stmt.where(Animal.sex == sex)
    if status is not None:
        stmt = stmt.where(Animal.status == status)
    elif not include_all_statuses:
        # Backward-compatible API default. Clients that label a filter "All
        # statuses" must opt in explicitly instead of relying on omission.
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
    # Every request is finite, including callers that omit pagination. `total`
    # remains the full filtered count so clients can always page honestly.
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    result = await db.execute(
        stmt.order_by(Animal.current_bucket, Animal.tag_number).offset(offset).limit(limit)
    )
    reference_date = today(farm.timezone)
    page_animals = list(result.scalars())
    computed = await animal_computed_facts(db, page_animals, reference_date, farm.timezone)
    animals = [
        animal_out(
            animal,
            reference_date,
            farm.timezone,
            permissions=perms,
            computed=computed[animal.id],
        )
        for animal in page_animals
    ]
    return AnimalListOut(animals=animals, total=total)


@router.post("", status_code=201)
async def create_animal(
    payload: AnimalCreateIn,
    db: DbSession,
    farm: CurrentFarm,
    user: CurrentUser,
    perms: Annotated[set[str], Depends(require_perm("animals.create"))],
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> AnimalOut:
    farm_date = today(farm.timezone)
    try:
        for field_name, value in (
            ("date_of_birth", payload.date_of_birth),
            ("estimated_dob", payload.estimated_dob),
            ("purchase_date", payload.purchase_date),
            ("weight_date", payload.weight_date),
        ):
            if value is not None:
                require_farm_not_future(value, farm, field_name)
        recorded_dob = payload.date_of_birth or payload.estimated_dob
        if (
            payload.purchase_date is not None
            and recorded_dob is not None
            and payload.purchase_date < recorded_dob
        ):
            raise ValueError("purchase_date cannot predate the recorded birth date")
        if payload.weight_date is not None and recorded_dob is not None:
            if payload.weight_date < recorded_dob:
                raise ValueError("weight_date cannot predate the recorded birth date")
        if (
            payload.weight_date is not None
            and payload.purchase_date is not None
            and payload.weight_date < payload.purchase_date
        ):
            raise ValueError("weight_date cannot predate purchase_date")
        if (
            payload.source == "PURCHASED"
            and not (payload.historical_import_reason or "").strip()
            and payload.weight_date is not None
            and payload.weight_date < (payload.purchase_date or farm_date)
        ):
            raise ValueError("weight_date cannot predate purchase_date")
        if (
            payload.source == "PURCHASED"
            and (payload.historical_import_reason or "").strip()
            and payload.purchase_price is not None
            and payload.purchase_date is None
        ):
            # A historical import stores purchase_date verbatim while the
            # expense fell back to today, so importing an old herd with prices
            # but no dates booked the whole acquisition into the current
            # accounting period against animals carrying no purchase date.
            raise ValueError(
                "purchase_date is required when a historical import records a purchase_price"
            )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    # enum/date/non-negativity guards from v1 now live in AnimalCreateIn's validators.
    # A blank tag gets an auto-generated one; that retries once on a lost race.
    # Capture farm.id/user.id up front: a rollback in the retry path expires
    # every ORM object, and touching farm.id/user.id there would be a
    # forbidden sync refresh on the async session (MissingGreenlet 500
    # instead of the intended retry).
    farm_id = farm.id
    farm_timezone = farm.timezone
    user_id = user.id
    historical_import_reason = (payload.historical_import_reason or "").strip()
    if historical_import_reason and user_id != farm.owner_id:
        raise HTTPException(
            status_code=403,
            detail="Only the farm owner may import historical animal lifecycle data",
        )
    managed_purchase = payload.source == "PURCHASED" and not historical_import_reason
    if managed_purchase:
        # The managed-purchase cascade books procurement (batch + quarantine
        # schedule + ANIMAL_PURCHASE expense) — writes that otherwise require
        # purchases.manage. A role holding only animals.create must not be
        # able to forge ledger entries through this endpoint (RT-C-1).
        if "purchases.manage" not in perms:
            raise HTTPException(
                status_code=403,
                detail=(
                    "Recording a purchased animal books a purchase batch and "
                    "expense and requires the purchase-management permission. "
                    "Use a historical import or ask the owner."
                ),
            )
        # Money-booking mutation with no natural key: the Idempotency-Key is
        # mandatory here for the same reason it is on /api/purchases/new —
        # a keyless retry would double-book the expense (RT-C-4).
        if idempotency_key is None:
            raise HTTPException(
                status_code=422,
                detail="An Idempotency-Key header is required for purchased-animal creation.",
            )
    initial_bucket = Bucket.QUARANTINE.value if managed_purchase else payload.current_bucket
    if historical_import_reason:
        # A direct import cannot fabricate a pregnancy, delivery or lactating
        # recovery state without its authoritative breeding/kidding records.
        # Import into an ordinary cohort, then record the historical domain
        # workflow so every reproductive fact remains linked and auditable.
        workflow_only_buckets = {
            Bucket.PREGNANCY_EARLY.value,
            Bucket.PREGNANCY_LATE.value,
            Bucket.DELIVERY.value,
            Bucket.RECOVERY.value,
        }
        if initial_bucket in workflow_only_buckets:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Historical animals cannot start in {initial_bucket}; "
                    "import an ordinary cohort and record the breeding/kidding workflow"
                ),
            )
        if initial_bucket == Bucket.BREEDING.value:
            dob = payload.date_of_birth or payload.estimated_dob
            age_months = None
            if dob is not None:
                age_months = (farm_date.year - dob.year) * 12 + (farm_date.month - dob.month)
                if farm_date.day < dob.day:
                    age_months -= 1
            profile = GOAT_PROFILE
            min_age = (
                profile.min_sire_breeding_age_months
                if payload.sex == "M"
                else profile.min_breeding_age_months
            )
            min_weight = (
                profile.min_sire_breeding_weight_kg
                if payload.sex == "M"
                else profile.min_breeding_weight_kg
            )
            if age_months is None or age_months < min_age or (payload.weight_kg or 0) < min_weight:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Historical BREEDING entry requires age at least {min_age} months "
                        f"and entry weight at least {min_weight:g} kg"
                    ),
                )
    # Species-scaled weight bands apply on this path too: a fabricated
    # 950-kg entry weight or birth weight here coalesces into "latest
    # weight" downstream and would permanently satisfy the breeding gates —
    # the same hole the /weight, /kidding and /purchases endpoints fence.
    weight_profile = GOAT_PROFILE
    if payload.birth_weight is not None and not (
        weight_profile.birth_weight_kg_range[0]
        <= payload.birth_weight
        <= weight_profile.birth_weight_kg_range[1]
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                f"{weight_profile.young} birth weight must be between "
                f"{weight_profile.birth_weight_kg_range[0]:g} and "
                f"{weight_profile.birth_weight_kg_range[1]:g} kg — "
                f"{payload.birth_weight:g} kg is not a credible newborn weight"
            ),
        )
    if payload.weight_kg is not None and payload.weight_kg > weight_profile.max_adult_weight_kg:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Weight {payload.weight_kg:g} kg exceeds the credible adult "
                f"scale for this farm's species ({weight_profile.max_adult_weight_kg:g} kg cap)"
            ),
        )
    requested_tag = (payload.tag_number or "").strip()

    async def mutate() -> AnimalOut:
        attempts = 1 if requested_tag else 2
        for attempt in range(attempts):
            tag_number = requested_tag or await generate_unique_tag(db, farm_id)
            try:
                # A savepoint keeps the durable idempotency claim alive if an
                # auto-generated tag loses its rare uniqueness race.
                async with db.begin_nested():
                    if requested_tag:
                        existing = await db.execute(
                            select(Animal.id).where(
                                Animal.farm_id == farm_id,
                                Animal.tag_number == tag_number,
                            )
                        )
                        if existing.scalar_one_or_none() is not None:
                            raise HTTPException(
                                status_code=400,
                                detail=f"Tag '{tag_number}' already exists on this farm.",
                            )

                    purchase_batch = None
                    if managed_purchase:
                        purchase_batch = await create_purchase_batch(
                            db,
                            farm,
                            payload.purchase_date or farm_date,
                            (payload.seller_name or "").strip(),
                            1,
                            None,
                            payload.weight_kg,
                            payload.purchase_price,
                            (payload.notes or "").strip(),
                            False,
                            created_by_id=user_id,
                            sex=payload.sex,
                        )
                    animal = Animal(
                        farm_id=farm_id,
                        tag_number=tag_number,
                        name=(payload.name or "").strip() or None,
                        sex=payload.sex,
                        source=payload.source,
                        current_bucket=initial_bucket,
                        date_of_birth=payload.date_of_birth,
                        estimated_dob=payload.estimated_dob,
                        birth_type=payload.birth_type,
                        breed=payload.breed.strip() or GOAT_PROFILE.default_breed,
                        birth_weight=payload.birth_weight,
                        purchase_date=(payload.purchase_date or farm_date)
                        if managed_purchase
                        else payload.purchase_date,
                        purchase_price=money(payload.purchase_price)
                        if payload.purchase_price is not None
                        else None,
                        seller_name=(payload.seller_name or "").strip() or None,
                        purchase_batch_id=(
                            purchase_batch.id if purchase_batch is not None else None
                        ),
                        notes=(payload.notes or "").strip() or None,
                        status=AnimalStatus.ACTIVE.value,
                    )
                    db.add(animal)
                    await db.flush()
                    db.add(
                        BucketMove(
                            animal_id=animal.id,
                            from_bucket=None,
                            to_bucket=initial_bucket,
                            effective_date=(
                                animal.purchase_date
                                or animal.date_of_birth
                                or animal.estimated_dob
                                or farm_date
                            ),
                            reason=(
                                f"Historical import: {historical_import_reason}"[:255]
                                if historical_import_reason
                                else (
                                    f"Purchase batch #{purchase_batch.id}"
                                    if purchase_batch is not None
                                    else "Initial entry"
                                )
                            ),
                            created_by_id=user_id,
                        )
                    )
                    if payload.weight_kg is not None and payload.weight_kg > 0:
                        db.add(
                            WeightRecord(
                                animal_id=animal.id,
                                date=payload.weight_date or farm_date,
                                weight_kg=payload.weight_kg,
                                notes="Entry weight",
                                created_by_id=user_id,
                            )
                        )
                    if purchase_batch is not None:
                        await schedule_quarantine_tasks(db, farm, purchase_batch)
                    elif payload.source == "PURCHASED" and payload.purchase_price is not None:
                        db.add(
                            Transaction(
                                farm_id=farm_id,
                                date=payload.purchase_date or farm_date,
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
                    # The session deliberately disables autoflush. Persist all
                    # entry facts before the SQL-backed serializer computes
                    # latest weight/move/provenance, otherwise an incorrect
                    # response would also be cached by durable idempotency.
                    await db.flush()
                    result = await _animal_out(db, animal, farm_date, farm_timezone, perms)
                return result
            except IntegrityError as exc:
                # Animal tags and tagged stillbirth records share one farm
                # namespace. The latter is enforced by a trigger-backed unique
                # violation, so it must follow the same bounded retry/error
                # path as the ordinary animals-table constraint. Otherwise a
                # committed or concurrently inserted stillborn tag escaped the
                # pre-check and surfaced as an unhandled 500.
                if unique_constraint_name(exc) not in {
                    "uq_animal_tag_per_farm",
                    "uq_stillborn_tag_farm_namespace",
                }:
                    raise
                if attempt + 1 == attempts:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Tag '{tag_number}' already exists on this farm.",
                    ) from None
        raise AssertionError("unreachable")

    return await execute_idempotent(
        db,
        http_response=response,
        key=idempotency_key,
        farm_id=farm_id,
        actor_id=user_id,
        operation="POST /api/animals",
        payload=payload,
        path_identity={},
        success_status=201,
        response_type=AnimalOut,
        mutate=mutate,
    )


@router.get("/{animal_id}")
async def animal_profile(
    animal_id: int,
    db: DbSession,
    farm: CurrentFarm,
    perms: Annotated[set[str], Depends(require_perm("animals.view"))],
    history_limit: Annotated[int, Query(ge=1, le=PROFILE_HISTORY_MAX_LIMIT)] = (
        PROFILE_HISTORY_DEFAULT_LIMIT
    ),
    kids_offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)] = 0,
    weights_offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)] = 0,
    moves_offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)] = 0,
    health_events_offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)] = 0,
    breedings_offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)] = 0,
) -> AnimalProfileOut:
    animal = await _get_animal(db, farm.id, animal_id)
    reference_date = today(farm.timezone)
    computed = await _profile_computed_facts(db, animal, reference_date, farm.timezone)

    # A kid row records both parents: dam_id at birth and sire_id from the
    # breeding's buck. Match either column so a buck's profile surfaces his
    # sired offspring — breedings_where below already shows him the services,
    # and a dam-only predicate here left the resulting kids invisible.
    kids_where = (
        Animal.farm_id == farm.id,
        or_(Animal.dam_id == animal.id, Animal.sire_id == animal.id),
    )
    weights_where = WeightRecord.animal_id == animal.id
    moves_where = BucketMove.animal_id == animal.id
    health_where = (HealthEvent.farm_id == farm.id, HealthEvent.animal_id == animal.id)
    breedings_where = (
        BreedingRecord.farm_id == farm.id,
        or_(
            BreedingRecord.doe_id == animal.id,
            BreedingRecord.buck_id == animal.id,
        ),
    )
    totals = (
        await db.execute(
            select(
                select(func.count(Animal.id)).where(*kids_where).scalar_subquery(),
                select(func.count(WeightRecord.id)).where(weights_where).scalar_subquery(),
                select(func.count(BucketMove.id)).where(moves_where).scalar_subquery(),
                (
                    select(func.count(HealthEvent.id)).where(*health_where).scalar_subquery()
                    if "health.view" in perms
                    else literal(0)
                ),
                (
                    select(func.count(BreedingRecord.id)).where(*breedings_where).scalar_subquery()
                    if "breeding.view" in perms
                    else literal(0)
                ),
            )
        )
    ).one()
    kids_total, weights_total, moves_total, health_total, breedings_total = map(int, totals)

    kids_result = await db.execute(
        select(Animal)
        .where(*kids_where)
        .order_by(Animal.tag_number, Animal.id)
        .offset(kids_offset)
        .limit(history_limit)
    )
    weights_result = await db.execute(
        select(WeightRecord)
        .where(weights_where)
        .order_by(WeightRecord.date.desc(), WeightRecord.id.desc())
        .offset(weights_offset)
        .limit(history_limit)
    )
    moves_result = await db.execute(
        select(BucketMove)
        .where(moves_where)
        .order_by(BucketMove.moved_at.desc(), BucketMove.id.desc())
        .offset(moves_offset)
        .limit(history_limit)
    )
    health_events: list[HealthEventOut] = []
    if "health.view" in perms:
        health_result = await db.execute(
            select(HealthEvent)
            .where(*health_where)
            .order_by(HealthEvent.date.desc(), HealthEvent.id.desc())
            .offset(health_events_offset)
            .limit(history_limit)
        )
        health_events = [HealthEventOut.model_validate(event) for event in health_result.scalars()]
    breeding_ids: list[int] = []
    if "breeding.view" in perms:
        breeding_ids = list(
            (
                await db.execute(
                    select(BreedingRecord.id)
                    .where(*breedings_where)
                    .order_by(BreedingRecord.breeding_date.desc(), BreedingRecord.id.desc())
                    .offset(breedings_offset)
                    .limit(history_limit)
                )
            ).scalars()
        )
    return AnimalProfileOut(
        animal=animal_out(
            animal,
            reference_date,
            farm.timezone,
            permissions=perms,
            computed=computed,
        ),
        kids=[AnimalOffspringOut.model_validate(kid) for kid in kids_result.scalars()],
        kids_total=kids_total,
        kids_offset=kids_offset,
        weights=[
            WeightRecordOut(
                id=weight.id,
                date=weight.date,
                weight_kg=weight.weight_kg,
                bcs=weight.bcs,
                # Weight and BCS are operational herd facts. Narrative is
                # free text and can carry clinical detail, so it follows the
                # same health.view boundary as dashboard weight narratives.
                notes=weight.notes if "health.view" in perms else None,
            )
            for weight in weights_result.scalars()
        ],
        weights_total=weights_total,
        weights_offset=weights_offset,
        moves=[
            BucketMoveOut(
                id=move.id,
                # from_bucket/to_bucket are Mapped[str] in the ORM; writers
                # only ever store Bucket enum values (BucketStr's members) —
                # move_animal validates the transition against that same
                # vocabulary — so the cast narrows, not loosens.
                from_bucket=(
                    cast(BucketStr, move.from_bucket) if move.from_bucket is not None else None
                ),
                to_bucket=cast(BucketStr, move.to_bucket),
                # Movement coordinates are ordinary herd operations. The
                # arbitrary narrative can contain diagnoses, commercial
                # provenance, or an owner's import rationale, so it follows
                # the health.view boundary used by other profile free text.
                reason=move.reason if "health.view" in perms else None,
                effective_date=move.effective_date,
                moved_at=move.moved_at,
            )
            for move in moves_result.scalars()
        ],
        moves_total=moves_total,
        moves_offset=moves_offset,
        health_events=health_events,
        health_events_total=health_total,
        health_events_offset=health_events_offset,
        breedings=breeding_ids,
        breedings_total=breedings_total,
        breedings_offset=breedings_offset,
        history_limit=history_limit,
    )


@router.post("/{animal_id}/move")
async def move_bucket(
    animal_id: int,
    payload: MoveIn,
    db: DbSession,
    farm: CurrentFarm,
    user: CurrentUser,
    perms: Annotated[set[str], Depends(require_perm("animals.move"))],
) -> AnimalOut:
    # Lock the animal before checking its lifecycle state; status, move and
    # weight writes must serialize rather than append an after-the-fact move.
    animal = await _get_animal(db, farm.id, animal_id, for_update=True)
    reference_date = today(farm.timezone)
    computed = await _profile_computed_facts(db, animal, reference_date, farm.timezone)
    transition_facts = (computed.latest_weight_kg, computed.is_currently_pregnant)
    if payload.history_override and user.id != farm.owner_id:
        raise HTTPException(status_code=403, detail="Only the farm owner may override history")
    context: Literal["history_override", "manual", "orphan_weaning"] = (
        "history_override" if payload.history_override else "manual"
    )
    if (
        animal.current_bucket == Bucket.QUARANTINE.value
        and payload.to_bucket != Bucket.QUARANTINE.value
        and animal.purchase_batch_id is not None
    ):
        # RT-C-3: an owner override no longer skips biosecurity sequencing
        # outright — it may move a batch animal out of quarantine only while
        # the protocol is still pristine (no work started); otherwise the
        # guarded day-45 batch task remains the only exit. The fence covers
        # every destination bucket: releasing to FOUNDATION and side-stepping
        # to BREEDING/KIDS/RESTING are the same biosecurity bypass. The check
        # locks Batch -> Tasks under the animal lock already held here.
        if not payload.history_override:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Purchased quarantine animals must leave quarantine through "
                    "the guarded batch task"
                ),
            )
        await _lock_pristine_batch_protocol_for_quarantine_reentry(
            db, farm.id, animal.purchase_batch_id
        )
    if (
        not payload.history_override
        and animal.current_bucket == Bucket.RECOVERY.value
        and payload.to_bucket in {Bucket.MALE_KIDS.value, Bucket.FEMALE_KIDS.value}
        and animal.dam_id is not None
    ):
        # A kid can remain in RECOVERY when its dam leaves the herd while the
        # kid is under a disease hold: the automatic early-wean correctly
        # cannot move it. After an authorised clearance, permit exactly the
        # sex-matched recovery transition, but only for a real kidding-linked
        # kid whose recorded dam is now terminal. This cannot be used to forge
        # ordinary manual lifecycle moves.
        orphan_provenance = (
            await db.execute(
                select(KidEntry.id)
                .join(KiddingRecord, KidEntry.kidding_record_id == KiddingRecord.id)
                .join(Animal, KiddingRecord.doe_id == Animal.id)
                .where(
                    KidEntry.farm_id == farm.id,
                    KidEntry.animal_id == animal.id,
                    KidEntry.status == KidStatus.ALIVE.value,
                    KidEntry.sex == animal.sex,
                    KiddingRecord.farm_id == farm.id,
                    KiddingRecord.doe_id == animal.dam_id,
                    Animal.farm_id == farm.id,
                    Animal.status != AnimalStatus.ACTIVE.value,
                    # KidEntry is an immutable birth fact: an adult daughter
                    # keeps it after weaning and can later return to RECOVERY
                    # for her own kidding. A genuine prior RECOVERY exit proves
                    # this is no longer a dependent orphan. History overrides
                    # are corrections and deliberately do not prove weaning.
                    ~select(BucketMove.id)
                    .where(
                        BucketMove.animal_id == animal.id,
                        BucketMove.from_bucket == Bucket.RECOVERY.value,
                        func.coalesce(BucketMove.reason, "").not_like(
                            f"{HISTORY_OVERRIDE_REASON_PREFIX}%"
                        ),
                    )
                    .exists(),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if orphan_provenance is not None:
            context = "orphan_weaning"
    resting_since: date | None = None
    if (
        not payload.history_override
        and animal.current_bucket == Bucket.RESTING.value
        and payload.to_bucket == Bucket.BREEDING.value
    ):
        # Flush-window residency: days since the doe's latest RESTING entry
        # (func.max over effective_date — the same bounded latest-move probe
        # the chronology service uses). The animal row carries no bucket-age
        # column and its bucket_moves relationship stays unloaded on request
        # paths, so the guard consumes this SQL-derived fact.
        resting_since = (
            await db.execute(
                select(func.max(BucketMove.effective_date)).where(
                    BucketMove.farm_id == farm.id,
                    BucketMove.animal_id == animal.id,
                    BucketMove.to_bucket == Bucket.RESTING.value,
                )
            )
        ).scalar_one_or_none()
    try:
        require_bucket_transition(
            animal,
            payload.to_bucket,
            context=context,
            reference_date=reference_date,
            facts=transition_facts,
            resting_since=resting_since,
            # The BREEDING-entry gate inside enforces the goat thresholds
            # (10 months / 22 kg), keeping juveniles out of the breeding
            # pool.
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    if (
        not payload.history_override
        and animal.sex == "F"
        and animal.current_bucket == Bucket.BREEDING.value
        and payload.to_bucket == Bucket.RESTING.value
        and await doe_has_open_breeding(db, farm.id, animal.id)
    ):
        raise HTTPException(
            status_code=409,
            detail="Resolve the doe's open breeding before moving her to RESTING",
        )
    if (
        not payload.history_override
        and payload.to_bucket in {Bucket.PREGNANCY_LATE.value, Bucket.DELIVERY.value}
        and not computed.is_currently_pregnant
    ):
        raise HTTPException(status_code=409, detail="Pregnancy movement requires a live pregnancy")
    if (
        payload.history_override
        and animal.sex == "F"
        and payload.to_bucket
        not in {
            Bucket.BREEDING.value,
            Bucket.PREGNANCY_EARLY.value,
            Bucket.PREGNANCY_LATE.value,
            Bucket.DELIVERY.value,
        }
        and (computed.is_currently_pregnant or await doe_has_open_breeding(db, farm.id, animal.id))
    ):
        # RT-DE-1/RT-C-5: the override deliberately bypasses the transition
        # matrix, but it must not strand an open service or pregnancy in a
        # bucket with no kidding/abort/ultrasound exit edge — that deadlock
        # leaves the doe on the overdue list forever. Resolve the service
        # first (negative ultrasound, abort, kidding) or sell/cull the doe.
        raise HTTPException(
            status_code=409,
            detail=(
                "This doe has an open breeding or pregnancy — a history override "
                "cannot move her out of the reproductive workflow buckets. Record "
                "the ultrasound result, abort the pregnancy, record the kidding, "
                "or exit her from the herd first."
            ),
        )
    move_reason = (payload.reason or "").strip()
    if payload.history_override:
        move_reason = f"{HISTORY_OVERRIDE_REASON_PREFIX}{move_reason}"[:255]
    elif context == "orphan_weaning" and not move_reason:
        move_reason = "Dam no longer active — deferred early wean after hold clearance"
    if (
        payload.to_bucket == Bucket.QUARANTINE.value
        and animal.current_bucket != Bucket.QUARANTINE.value
        and animal.purchase_batch_id is not None
    ):
        await _lock_pristine_batch_protocol_for_quarantine_reentry(
            db,
            farm.id,
            animal.purchase_batch_id,
        )
    move_animal(
        db,
        animal,
        payload.to_bucket,
        reason=move_reason,
        created_by_id=user.id,
        context=context,
        reference_date=reference_date,
        facts=transition_facts,
        resting_since=resting_since,
    )
    await db.commit()
    return await _animal_out(db, animal, today(farm.timezone), farm.timezone, perms)


@router.post("/{animal_id}/weight", status_code=201)
async def record_weight(
    animal_id: int,
    payload: WeightIn,
    response: Response,
    db: DbSession,
    farm: CurrentFarm,
    user: CurrentUser,
    _perms: Annotated[set[str], Depends(require_perm("animals.weight"))],
    idempotency_key: IdempotencyKey = None,
) -> WeightRecordOut:
    async def mutate() -> WeightRecordOut:
        animal = await _get_animal(db, farm.id, animal_id, for_update=True)
        if animal.status != AnimalStatus.ACTIVE.value:
            raise HTTPException(
                status_code=400,
                detail=f"{animal.tag_number} is {animal.status.lower()} — cannot record a weight.",
            )
        # finite/positive/future-date/bcs-range guards from v1 live in
        # WeightIn's validators; the species-scaled adult cap lives here —
        # a 999 kg reading on a 30-kg doe is not data, and it would poison
        # every eligibility gate and dashboard that reads "latest weight".
        adult_cap = GOAT_PROFILE.max_adult_weight_kg
        if payload.weight_kg > adult_cap:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Weight {payload.weight_kg:g} kg exceeds the credible adult "
                    f"scale for this farm's species ({adult_cap:g} kg cap)"
                ),
            )
        record_date = payload.date or today(farm.timezone)
        try:
            require_farm_not_future(record_date, farm, "weight date")
            require_animal_event_chronology(animal, record_date, "Weight record")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        record = WeightRecord(
            animal_id=animal.id,
            date=record_date,
            weight_kg=payload.weight_kg,
            bcs=payload.bcs,
            notes=(payload.notes or "").strip() or None,
            created_by_id=user.id,
        )
        db.add(record)
        await db.flush()
        return WeightRecordOut.model_validate(record)

    return await execute_idempotent(
        db,
        http_response=response,
        key=idempotency_key,
        farm_id=farm.id,
        actor_id=user.id,
        operation="animals.weight.create",
        payload=payload,
        path_identity={"animal_id": animal_id},
        success_status=201,
        response_type=WeightRecordOut,
        mutate=mutate,
    )


@router.post("/{animal_id}/status")
async def change_status(
    animal_id: int,
    payload: StatusChangeIn,
    db: DbSession,
    farm: CurrentFarm,
    user: CurrentUser,
    perms: Annotated[set[str], Depends(require_perm("animals.status"))],
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
    if payload.new_status in SALE_CAPABLE_STATUSES and (
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
    try:
        require_farm_not_future(status_date, farm, "status date")
        require_animal_event_chronology(animal, status_date, "Status change")
        await require_status_after_recorded_facts(db, animal, status_date)
        if payload.mortality_reported_at is not None:
            require_farm_not_future(payload.mortality_reported_at, farm, "mortality_reported_at")
            if payload.mortality_reported_at < status_date:
                raise ValueError("mortality_reported_at cannot predate the death date")
        if payload.authority_notified_at is not None:
            require_farm_not_future(payload.authority_notified_at, farm, "authority_notified_at")
            require_animal_event_chronology(
                animal, payload.authority_notified_at, "Authority notification"
            )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if payload.new_status in SALE_CAPABLE_STATUSES:
        withdrawal = (
            await db.execute(
                select(func.max(HealthEvent.withdrawal_until)).where(
                    HealthEvent.farm_id == farm.id,
                    HealthEvent.animal_id == animal.id,
                    HealthEvent.withdrawal_until >= status_date,
                )
            )
        ).scalar_one_or_none()
        if withdrawal is not None:
            raise HTTPException(
                status_code=409,
                detail=f"Sale/cull is blocked by medicine withdrawal through {withdrawal}",
            )
    status_note_advisories: list[str] = []
    if payload.new_status == AnimalStatus.SOLD.value:
        # Biosecurity fence: an animal still inside the 45-day quarantine
        # protocol (possibly incubating) must not enter the food chain. A
        # cull remains possible — destroying a sick quarantined animal is a
        # legitimate disease response.
        if animal.current_bucket == Bucket.QUARANTINE.value:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{animal.tag_number} is still in the 45-day quarantine protocol — "
                    "complete or skip the protocol before selling"
                ),
            )
        # Meat-sale window: a male kid below the SPEC's minimum sale age
        # cannot be liquidated as meat stock — regardless of bucket, so an
        # unweaned kid still riding in RECOVERY with its dam cannot slip the
        # gate (RT-C-2). Culling remains open (injury/illness), and the owner
        # can still record the exit through a cull with notes. The gate reads
        # the effective DOB (recorded or estimated), so an estimated birth
        # date closes the old unknown-DOB loophole.
        if animal.sex == "M":
            if animal.effective_dob is None:
                # Age is provable only when an effective DOB exists; the sale
                # proceeds, but the unverifiable age stays visible on the
                # record instead of silently passing the gate.
                status_note_advisories.append("age unverifiable — no birth/estimated date")
            else:
                age_months = animal.age_months_on(status_date)
                if age_months is not None and age_months < MEAT_SALE_AGE_MONTHS[0]:
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            f"{animal.tag_number} is {age_months} months old — the meat-sale "
                            f"window opens at {MEAT_SALE_AGE_MONTHS[0]} months and "
                            f"{MEAT_SALE_WEIGHT_KG[0]:.0f} kg (record a cull instead if the "
                            "animal must leave the herd now)"
                        ),
                    )
            if (
                payload.sale_weight_kg is not None
                and payload.sale_weight_kg < MEAT_SALE_WEIGHT_KG[0]
            ):
                # Soft weight window: the sale still proceeds — a light male
                # may legitimately leave the herd — but the advisory keeps
                # the below-window realization auditable.
                status_note_advisories.append(
                    "sold below the "
                    f"{MEAT_SALE_WEIGHT_KG[0]:.0f}–{MEAT_SALE_WEIGHT_KG[1]:.0f} kg market window"
                )
    animal.status = payload.new_status
    animal.status_date = status_date
    # Advisories join the operator's own narrative with the local "—" note
    # convention; the column is String(255), so the composition is truncated
    # like the other bounded note writers.
    animal.status_notes = (
        " — ".join(
            part for part in ((payload.notes or "").strip(), *status_note_advisories) if part
        )[:255]
        or None
    )
    if payload.new_status == AnimalStatus.DEAD.value:
        animal.mortality_cause = (payload.mortality_cause or "").strip() or None
        animal.mortality_cause_code = payload.mortality_cause_code
        animal.disposal_method = (payload.disposal_method or "").strip() or None
        animal.necropsy_done = payload.necropsy_done
        animal.necropsy_findings = (payload.necropsy_findings or "").strip() or None
        animal.mortality_reported_at = payload.mortality_reported_at
        if payload.suspected_scheduled_disease:
            place_movement_restriction(
                db,
                animal,
                disease_target=payload.suspected_disease or "",
                restriction_reason="Scheduled-disease suspicion recorded with mortality",
                action_reference=f"Mortality status change effective {status_date.isoformat()}",
                acted_by_id=user.id,
            )
            animal.authority_notified_at = payload.authority_notified_at
        try:
            await replan_dam_after_last_kid_death(db, farm, animal, status_date)
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) != "55P03":
                raise
            await db.rollback()
            raise HTTPException(
                status_code=409,
                detail="The dam's lifecycle is changing; retry the kid death update",
            ) from None
    # new_status is never ACTIVE here: clear the cull flag and stop the
    # animal's pending tasks (a dead/sold animal must not generate work).
    animal.cull_candidate = False

    # A sold/dead/culled doe cannot carry a pregnancy to term, and she can
    # never be scanned again either: resolve BOTH kinds of open service here
    # instead of leaving them on the kidding-due / ultrasound lists forever.
    #   CONFIRMED_PREGNANT → ABORTED   (record_kidding would reject her)
    #   PENDING            → UNASSESSED (record_ultrasound_result rejects her,
    #                                    so the row had no exit at all)
    # Lock order is canonical: the animal lock above → breeding rows here →
    # task locks inside mark_aborted / mark_unassessed /
    # skip_pending_tasks_for_animal.
    if animal.sex == "F":
        open_result = await db.execute(
            select(BreedingRecord)
            .where(
                BreedingRecord.farm_id == farm.id,
                BreedingRecord.doe_id == animal.id,
                BreedingRecord.outcome.in_(
                    [
                        BreedingOutcome.PENDING.value,
                        BreedingOutcome.CONFIRMED_PREGNANT.value,
                    ]
                ),
                ~select(KiddingRecord.id)
                .where(
                    KiddingRecord.farm_id == farm.id,
                    KiddingRecord.breeding_record_id == BreedingRecord.id,
                )
                .exists(),
            )
            .order_by(BreedingRecord.id)
            .with_for_update()
        )
        for br in open_result.scalars():
            if br.outcome == BreedingOutcome.PENDING.value:
                # Never scanned: there is no pregnancy to abort, only a
                # question nobody can answer any more. Closing it as
                # UNASSESSED asserts no scan result — the record is neither a
                # conception nor a failure to conceive.
                await mark_unassessed(db, br, closed_by_id=user.id)
                continue
            try:
                await mark_aborted(
                    db,
                    br,
                    loss_date=status_date,
                    loss_cause="ANIMAL_STATUS_CHANGE",
                    loss_notes=f"Pregnancy auto-resolved when doe was marked {payload.new_status}",
                    recorded_by_id=user.id,
                    # A terminal herd-status event must remain recordable even
                    # when a legacy confirmed pregnancy has already exceeded
                    # the biological gestation window. This narrowly scoped
                    # administrative close is never exposed by /abort.
                    allow_late_administrative_close=True,
                )
            except ValueError as exc:
                # The status request and its pregnancy resolution are one
                # atomic chronology: a backdated removal cannot silently
                # predate a later recorded pregnancy confirmation.
                await db.rollback()
                raise HTTPException(status_code=422, detail=str(exc)) from None
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
                        effective_date=status_date,
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
                # A farm-born adult retains dam_id and can later return to
                # RECOVERY for her own litter. Only children that have never
                # genuinely left their birth RECOVERY cohort are dependants of
                # this retiring dam; history overrides are data corrections,
                # not evidence of weaning.
                ~select(BucketMove.id)
                .where(
                    BucketMove.animal_id == Animal.id,
                    BucketMove.from_bucket == Bucket.RECOVERY.value,
                    func.coalesce(BucketMove.reason, "").not_like(
                        f"{HISTORY_OVERRIDE_REASON_PREFIX}%"
                    ),
                )
                .correlate(Animal)
                .exists(),
            )
            .order_by(Animal.id)
            .with_for_update()
        )
        orphan_reason = f"Dam marked {payload.new_status.lower()} — early wean"
        for kid in orphans_result.scalars():
            target = Bucket.MALE_KIDS.value if kid.sex == "M" else Bucket.FEMALE_KIDS.value
            move_animal(
                db,
                kid,
                target,
                orphan_reason,
                created_by_id=user.id,
                context="weaning",
                reference_date=status_date,
            )

    await skip_pending_tasks_for_animal(db, farm.id, animal.id)
    if animal.purchase_batch_id is not None:
        # The batch's 45-day protocol duties carry no animal_id, so the sweep
        # above cannot reach them; once the batch has no animals left in the
        # herd they are stale work nobody can complete. Autoflush is disabled,
        # so this animal's new status must reach the database before the
        # "any active animal left?" check runs.
        await db.flush()
        await skip_pending_tasks_for_empty_batch(db, farm.id, animal.purchase_batch_id)

    if payload.new_status in SALE_CAPABLE_STATUSES:
        # Sale weight is recorded at the ledger's gram-derived precision so
        # the persisted weight and any price derived from it cannot disagree
        # by a rounding step. (SOLD-only fields; the schema rejects them for
        # CULLED/DEAD, so the None default holds there.)
        sale_weight_kg = (
            Decimal(str(payload.sale_weight_kg)).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
            if payload.sale_weight_kg is not None
            else None
        )
        sale_price: Decimal | float | None = payload.sale_price
        if (
            sale_price is None
            and sale_weight_kg is not None
            and payload.sale_price_per_kg is not None
        ):
            # Market convention: price = live weight × ₹/kg, exact-paise via
            # the money helper (decimal multiplication of already-2dp inputs,
            # then ROUND_HALF_UP to the paise quantum).
            sale_price = money(sale_weight_kg * Decimal(str(payload.sale_price_per_kg)))
            if sale_price > 1_000_000_000:
                # Same ledger ceiling the explicit sale_price field carries
                # (NonNegativeMoneyFloat); without this a forged weight ×
                # rate would surface as the CHECK constraint's 500 instead
                # of a validation error.
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "The derived sale price (weight × ₹/kg) exceeds the "
                        "₹1,000,000,000 ledger cap"
                    ),
                )
        animal.sale_price = money(sale_price) if sale_price is not None else None
        animal.sale_weight_kg = sale_weight_kg
        animal.buyer_name = (payload.buyer_name or "").strip() or None
        # A SOLD/CULLED animal always lands in the ledger. When the price is
        # omitted the row books ₹0 and says so in plain text: the asset
        # leaving the herd must be countable from the finance views, never
        # silently invisible (the off-ledger-sale hole).
        sale_note = f"Sale of {animal.tag_number}"
        # float():g renders the persisted 2-dp weight without Decimal's
        # trailing-zero padding ("25.5", not "25.50").
        if sale_weight_kg is not None and payload.sale_price_per_kg is not None:
            sale_note += f" at {float(sale_weight_kg):g} kg @ ₹{payload.sale_price_per_kg:g}/kg"
        elif sale_weight_kg is not None:
            sale_note += f" at {float(sale_weight_kg):g} kg"
        if animal.buyer_name:
            sale_note += f" to {animal.buyer_name}"
        if sale_price is None:
            sale_note += " — no price recorded (₹0 booked)"
        db.add(
            Transaction(
                farm_id=farm.id,
                date=status_date,
                type=TransactionType.INCOME.value,
                category=TransactionCategory.ANIMAL_SALE.value,
                amount=money(sale_price) if sale_price is not None else money(0),
                related_animal_id=animal.id,
                notes=sale_note,
                created_by_id=user.id,
                source_type="ANIMAL_SALE",
                source_id=animal.id,
            )
        )
    await db.commit()
    return await _animal_out(db, animal, today(farm.timezone), farm.timezone, perms)
