"""Duty engine: tabbed list (today / overdue / upcoming / awaiting
verification / completed), complete / skip / verify / reject actions, and
manual duty creation.

Port of v1 app/routers/tasks.py. Workers see only duties assigned to their
role or to them personally (the owner sees everything); only the actionable
awaiting-review queue is farm-wide for tasks.verify holders. Completed
history remains assignment-scoped. Duties whose completion means recording data (ULTRASOUND,
KIDDING_DUE, VACCINE, DEWORMING) must be closed through their linked form,
not the bare complete endpoint.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import Select, and_, case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import CurrentFarm, CurrentMembership, CurrentUser, DbSession, require_perm
from ..models import (
    VERIFICATION_REQUIRED_CATEGORIES,
    Animal,
    AnimalStatus,
    Bucket,
    Farm,
    FarmMembership,
    PurchaseBatch,
    Role,
    Task,
    TaskCategory,
    TaskStatus,
    User,
)
from ..schemas.common import COMMON_ERROR_RESPONSES, MAX_INT32_ID, MAX_PAGE_OFFSET
from ..schemas.tasks import TaskCreateIn, TaskOut, TaskRejectIn, TaskSkipIn, TaskTabsOut
from ..services import (
    IdempotencyKey,
    ManualTaskCapacityError,
    actionable_pending_task_predicate,
    complete_task,
    create_manual_task,
    execute_idempotent,
    find_live_recurring_successor,
    guard_manual_task_capacity_locked,
    lock_manual_task_queue,
    reject_task,
    resolve_personal_task_role_fallback,
    skip_task,
    task_scope,
    verify_task,
)
from ..utils import today
from ._shared import TASK_LOADS, task_action_url, task_out, visible_to

router = APIRouter(prefix="/api/tasks", tags=["tasks"], responses=COMMON_ERROR_RESPONSES)

VIEW = Annotated[set[str], Depends(require_perm("tasks.view"))]
CREATE = Annotated[set[str], Depends(require_perm("tasks.create"))]
COMPLETE = Annotated[set[str], Depends(require_perm("tasks.complete"))]
VERIFY = Annotated[set[str], Depends(require_perm("tasks.verify"))]

# Display enrichment loads (TASK_LOADS), task_action_url, task_out and
# visible_to live in `._shared` — single source of truth shared
# with the dashboard/breeding/kidding/health routers.


async def _guard_manual_task_capacity_locked(db: AsyncSession, farm: Farm) -> None:
    """Bound outstanding manual duties while the caller holds the Farm lock.

    Creation, verification rejection, and the successor a verification spawns
    can each add one PENDING manual row.
    Requiring their shared Farm lock before any Task lock makes the count/update
    invariant concurrency-safe and keeps recurring completion's successor
    insert on the same canonical Farm -> Animal -> Task lock path.
    """
    try:
        await guard_manual_task_capacity_locked(db, farm)
    except ManualTaskCapacityError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from None


async def _lock_farm_for_recurring_transition(
    db: AsyncSession,
    farm: Farm,
    task_id: int,
) -> None:
    """Lock Farm before rows touched by a transition that can spawn a Task.

    ``recur_days`` has no mutation endpoint, so a lock-free scalar discovery is
    sufficient to decide whether the immutable series flag requires the Farm
    lock. The Task itself is reloaded and locked by the caller afterward.
    """
    if not 1 <= task_id <= MAX_INT32_ID:
        return
    recurrence = (
        await db.execute(select(Task.recur_days).where(Task.id == task_id, Task.farm_id == farm.id))
    ).one_or_none()
    if recurrence is not None and recurrence.recur_days is not None:
        await lock_manual_task_queue(db, farm)


async def _get_task(
    db: AsyncSession, farm: Farm, task_id: int, *, for_update: bool = False
) -> Task:
    # Ids above the int4 PK ceiling cannot exist — 404, never an asyncpg
    # int32 DataError (500).
    if not 1 <= task_id <= MAX_INT32_ID:
        raise HTTPException(status_code=404, detail="Task not found")
    stmt = select(Task).options(*TASK_LOADS).where(Task.id == task_id, Task.farm_id == farm.id)
    if for_update:
        # SELECT ... FOR UPDATE: concurrent complete/skip/verify/reject calls
        # serialize on the row — the loser re-reads the committed status and
        # fails its state check instead of re-applying the side effects
        # (bucket moves, spawned occurrences) a second time.
        stmt = stmt.with_for_update()
    result = await db.execute(stmt)
    task = result.scalar_one_or_none()
    if task is None or task.farm_id != farm.id:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


async def _lock_completion_animals(
    db: AsyncSession,
    farm: Farm,
    task_id: int,
) -> list[Animal]:
    """Pre-lock every animal a completion can mutate or whose cohort must stay stable.

    The scalar task read intentionally takes no row lock; it only discovers
    the immutable target columns needed to acquire the canonical first locks.
    The caller locks and re-checks the Task afterwards. Every multi-animal
    scope uses one ordered SELECT so overlapping quarantine/weaning requests
    cannot lock the same goats in opposite orders.
    """
    if not 1 <= task_id <= MAX_INT32_ID:
        return []
    target = (
        await db.execute(
            select(
                Task.category,
                Task.animal_id,
                Task.purchase_batch_id,
            ).where(Task.id == task_id, Task.farm_id == farm.id)
        )
    ).one_or_none()
    if target is None:
        return []

    batch_lock_id: int | None = None
    if target.category == TaskCategory.BUCKET_MOVE.value and target.purchase_batch_id is not None:
        batch_lock_id = int(target.purchase_batch_id)
        animal_filter = and_(
            Animal.purchase_batch_id == target.purchase_batch_id,
            Animal.status == AnimalStatus.ACTIVE.value,
        )
    elif target.category == TaskCategory.WEANING.value and target.animal_id is not None:
        # Kids await weaning in RECOVERY with the dam; the completion's
        # weaning branch moves exactly those animals, and an animal the route
        # never locked can never be moved by the completion.
        young_buckets = (Bucket.RECOVERY.value,)
        animal_filter = or_(
            Animal.id == target.animal_id,
            and_(
                Animal.dam_id == target.animal_id,
                Animal.status == AnimalStatus.ACTIVE.value,
                Animal.current_bucket.in_(young_buckets),
            ),
        )
    elif target.animal_id is not None:
        animal_filter = Animal.id == target.animal_id
    else:
        return []

    locked_animals = list(
        (
            await db.execute(
                select(Animal)
                .where(Animal.farm_id == farm.id, animal_filter)
                .order_by(Animal.id)
                .with_for_update()
            )
        ).scalars()
    )

    # Serialize against skip_pending_tasks_for_empty_batch, which locks
    # animal -> batch -> tasks (ascending id). This path then locks the
    # release duty — always the HIGHEST id in the protocol series — before
    # _guard_quarantine_release locks its siblings ascending, i.e. the exact
    # inverse. The paths normally serialize on the batch's ACTIVE animals
    # first, but the empty-batch sweep exists precisely when that set is empty,
    # so no animal row can order the pair. Taking the batch row here supplies
    # the stable shared animal -> batch -> task order even at zero occupancy.
    if batch_lock_id is not None:
        await db.execute(
            select(PurchaseBatch.id)
            .where(PurchaseBatch.id == batch_lock_id, PurchaseBatch.farm_id == farm.id)
            .with_for_update()
        )

    return locked_animals


async def _batch_has_active_animal(db: AsyncSession, farm: Farm, purchase_batch_id: int) -> bool:
    """Does the purchase batch still hold at least one ACTIVE animal?"""
    remaining = (
        await db.execute(
            select(Animal.id)
            .where(
                Animal.farm_id == farm.id,
                Animal.purchase_batch_id == purchase_batch_id,
                Animal.status == AnimalStatus.ACTIVE.value,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return remaining is not None


def _require_locked_linked_animal_active(task: Task, locked_animals: list[Animal]) -> None:
    """Reject direct actions on legacy pending duties for inactive animals."""
    if task.animal_id is not None and not any(
        animal.id == task.animal_id and animal.status == AnimalStatus.ACTIVE.value
        for animal in locked_animals
    ):
        # Status changes normally sweep pending duties, and task lists hide
        # any legacy residue. Re-check under the canonical Animal -> Task locks
        # so a direct/forged completion or recurring skip cannot act or spawn.
        raise HTTPException(status_code=409, detail="This duty's animal is no longer active")


@router.get("")
async def list_tasks(
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    perms: VIEW,
    active_limit: Annotated[int, Query(ge=1, le=200)] = 100,
    today_offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)] = 0,
    overdue_offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)] = 0,
    upcoming_offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)] = 0,
    awaiting_offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)] = 0,
    completed_limit: Annotated[int, Query(ge=1, le=200)] = 100,
    completed_offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)] = 0,
) -> TaskTabsOut:
    """All five v1 tabs as deterministic, independently pageable lists."""
    now = today(farm.timezone)
    scoped = (await task_scope(db, farm, user)).options(*TASK_LOADS)
    pending = scoped.where(actionable_pending_task_predicate())

    # Only the actionable review queue is farm-wide for tasks.verify holders.
    # Historical rows retain normal assignment scope; verification authority
    # is not permission to enumerate other teams' titles, assignees or goats.
    can_verify = "tasks.verify" in perms
    awaiting_base = (
        select(Task).where(Task.farm_id == farm.id).options(*TASK_LOADS) if can_verify else scoped
    )
    done_base = awaiting_base.where(Task.status == TaskStatus.DONE.value)

    today_query = pending.where(Task.due_date == now)
    overdue_query = pending.where(Task.due_date < now)
    upcoming_query = pending.where(Task.due_date > now)

    async def total(statement: Select[tuple[Task]]) -> int:
        # Keeping this local helper avoids duplicating the order-free count
        # wrapper five times.
        query = statement.order_by(None)
        return (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()

    today_total = await total(today_query)
    overdue_total = await total(overdue_query)
    upcoming_total = await total(upcoming_query)
    today_rows = list(
        (
            await db.execute(today_query.order_by(Task.id).offset(today_offset).limit(active_limit))
        ).scalars()
    )
    overdue_rows = list(
        (
            await db.execute(
                overdue_query.order_by(Task.due_date, Task.id)
                .offset(overdue_offset)
                .limit(active_limit)
            )
        ).scalars()
    )
    upcoming_rows = list(
        (
            await db.execute(
                upcoming_query.order_by(Task.due_date, Task.id)
                .offset(upcoming_offset)
                .limit(active_limit)
            )
        ).scalars()
    )
    # Awaiting verification (see Task.awaiting_verification_clause) — filtered
    # in SQL, not by loading the whole DONE pile.
    awaiting_query = done_base.where(Task.awaiting_verification_clause())
    awaiting_total = await total(awaiting_query)
    awaiting = list(
        (
            await db.execute(
                awaiting_query.order_by(Task.completed_at.desc(), Task.id.desc())
                .offset(awaiting_offset)
                .limit(active_limit)
            )
        ).scalars()
    )
    # The completed-tab predicate runs in SQL BEFORE the 100-row cap: skipped
    # duties (owner sees them), duties needing no verification, and verified
    # ones — previously the newest 100 rows were filtered in Python, so a DONE
    # pile dominated by unverified CLEANING rows starved the tab.
    finished = scoped.where(
        Task.status.in_(
            [TaskStatus.DONE.value, TaskStatus.VERIFIED.value, TaskStatus.SKIPPED.value]
        ),
        or_(
            Task.status == TaskStatus.SKIPPED.value,
            Task.status == TaskStatus.VERIFIED.value,
            Task.category.notin_(VERIFICATION_REQUIRED_CATEGORIES),
        ),
    )
    completed_total = await total(finished)
    # Order on the instant the row actually finished, which is what the UI
    # renders: a SKIPPED duty only carries skipped_at, and PostgreSQL sorts the
    # resulting NULL completed_at FIRST under DESC — so every bulk service-side
    # skip (sale/death sweeps, aborted pregnancies) would otherwise monopolise
    # page 1 ahead of genuinely completed work.
    finished_at = case(
        (Task.status == TaskStatus.SKIPPED.value, Task.skipped_at),
        else_=Task.completed_at,
    )
    completed = list(
        (
            await db.execute(
                finished.order_by(finished_at.desc(), Task.id.desc())
                .offset(completed_offset)
                .limit(completed_limit)
            )
        ).scalars()
    )
    return TaskTabsOut(
        today=[task_out(t) for t in today_rows],
        overdue=[task_out(t) for t in overdue_rows],
        upcoming=[task_out(t) for t in upcoming_rows],
        awaiting=[task_out(t) for t in awaiting],
        completed=[task_out(t) for t in completed],
        today_total=today_total,
        today_offset=today_offset,
        overdue_total=overdue_total,
        overdue_offset=overdue_offset,
        upcoming_total=upcoming_total,
        upcoming_offset=upcoming_offset,
        awaiting_total=awaiting_total,
        awaiting_offset=awaiting_offset,
        active_limit=active_limit,
        completed_total=completed_total,
        completed_limit=completed_limit,
        completed_offset=completed_offset,
    )


@router.get("/{task_id}")
async def get_task(
    task_id: int,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    _perms: VIEW,
) -> TaskOut:
    """Resolve one visible farm task independently of bounded tab pages."""
    if not 1 <= task_id <= MAX_INT32_ID:
        raise HTTPException(status_code=404, detail="Task not found")
    scoped = (await task_scope(db, farm, user)).options(*TASK_LOADS)
    task = (await db.execute(scoped.where(Task.id == task_id))).scalar_one_or_none()
    if task is None:
        # Missing, cross-farm and assignment-inaccessible tasks deliberately
        # share one response: a deep link only resolves data already visible
        # to this caller's tasks scope.
        raise HTTPException(status_code=404, detail="Task not found")
    return task_out(task)


@router.post("", status_code=201)
async def create_task(
    payload: TaskCreateIn,
    response: Response,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    perms: CREATE,
    idempotency_key: IdempotencyKey = None,
) -> TaskOut:
    """Create a manual duty with explicit, farm-scoped assignments.

    Assignment is operational state, not optional enrichment.  Silently
    dropping an invalid worker or role leaves an apparently successful duty
    visible only to the owner, so reject stale, inactive, and cross-farm
    references instead.
    """

    async def mutate() -> TaskOut:
        title = payload.title.strip()
        if not title:
            raise HTTPException(status_code=400, detail="Title is required")
        # Every distinct-key creation takes the farm row before counting. Two
        # requests at the boundary cannot both observe the final free slot.
        await _guard_manual_task_capacity_locked(db, farm)

        # Canonical assignment lock order is Farm -> Animal -> target
        # Membership -> User -> Role. The locks are held through the domain
        # commit, making every ACTIVE/non-tombstone/role check durable against
        # concurrent animal removal, account deletion, worker deactivation,
        # role reassignment, and role tombstoning.
        animal_id = None
        if payload.animal_id is not None and payload.animal_id <= MAX_INT32_ID:
            linked_animal = (
                await db.execute(
                    select(Animal)
                    .where(
                        Animal.id == payload.animal_id,
                        Animal.farm_id == farm.id,
                        Animal.status == AnimalStatus.ACTIVE.value,
                    )
                    .with_for_update(read=True)
                )
            ).scalar_one_or_none()
            if linked_animal is not None:
                animal_id = linked_animal.id
        if payload.animal_id is not None and animal_id is None:
            raise HTTPException(
                status_code=400, detail="Assigned animal is not active on this farm"
            )

        worker_id = None
        worker_membership = None
        if payload.assigned_user_id is not None:
            worker_membership = (
                (
                    await db.execute(
                        select(FarmMembership)
                        .where(
                            FarmMembership.farm_id == farm.id,
                            FarmMembership.user_id == payload.assigned_user_id,
                            FarmMembership.is_active.is_(True),
                        )
                        .with_for_update(read=True)
                    )
                ).scalar_one_or_none()
                if payload.assigned_user_id <= MAX_INT32_ID
                else None
            )
            if worker_membership is None:
                raise HTTPException(
                    status_code=400,
                    detail="Assigned worker is not an active member of this farm",
                )
            target_user = (
                await db.execute(
                    select(User)
                    .where(
                        User.id == worker_membership.user_id,
                        User.deleted_at.is_(None),
                    )
                    .with_for_update(read=True)
                )
            ).scalar_one_or_none()
            if target_user is None:
                raise HTTPException(
                    status_code=400,
                    detail="Assigned worker is not an active member of this farm",
                )
            worker_id = worker_membership.user_id

        requested_role_id = payload.assigned_role_id
        effective_role_id = requested_role_id or (
            worker_membership.role_id if worker_membership is not None else None
        )
        role_id = None
        if effective_role_id is not None:
            role = (
                (
                    await db.execute(
                        select(Role)
                        .where(
                            Role.id == effective_role_id,
                            Role.farm_id == farm.id,
                            Role.deleted_at.is_(None),
                        )
                        .with_for_update(read=True)
                    )
                ).scalar_one_or_none()
                if effective_role_id <= MAX_INT32_ID
                else None
            )
            if role is None:
                detail = (
                    "Assigned role is not on this farm"
                    if requested_role_id is not None
                    else "Assigned worker's role is no longer active on this farm"
                )
                raise HTTPException(status_code=400, detail=detail)
            role_id = role.id

        if (
            worker_membership is not None
            and requested_role_id is not None
            and worker_membership.role_id != role_id
        ):
            raise HTTPException(
                status_code=400,
                detail="Assigned worker does not hold the assigned role",
            )

        try:
            task = await create_manual_task(
                db,
                farm,
                title,
                payload.due_date,
                payload.category,
                animal_id=animal_id,
                assigned_role_id=role_id,
                assigned_user_id=worker_id,
                recur_days=payload.recur_days,
            )
            # Re-fetch with TASK_LOADS while the idempotency transaction is
            # still open so its exact first response is persisted atomically.
            return task_out(await _get_task(db, farm, task.id))
        except IntegrityError:
            raise HTTPException(
                status_code=409,
                detail="Task assignment changed; refresh the team list and try again",
            ) from None

    # Take the queue mutex before the idempotency claim inserts its FK to Farm,
    # so the count/create invariant inside mutate() is serialized farm-wide.
    # Replays still bypass the count/mutation after this constant lock.
    await lock_manual_task_queue(db, farm)
    return await execute_idempotent(
        db,
        http_response=response,
        key=idempotency_key,
        farm_id=farm.id,
        actor_id=user.id,
        operation="tasks.create",
        payload=payload,
        path_identity={},
        success_status=201,
        response_type=TaskOut,
        mutate=mutate,
    )


@router.post("/{task_id}/complete")
async def complete(
    task_id: int,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    membership: CurrentMembership,
    perms: COMPLETE,
) -> TaskOut:
    # A recurrence inserts another Task and therefore starts with FARM. The
    # remainder of the canonical order is every affected ANIMAL (ascending id)
    # -> TASK. Non-recurring transitions avoid the farm-wide lock.
    # This includes batch quarantine release and a weaning doe plus all of her
    # eligible kids, not only Task.animal_id.
    await _lock_farm_for_recurring_transition(db, farm, task_id)
    locked_animals = await _lock_completion_animals(db, farm, task_id)
    task = await _get_task(db, farm, task_id, for_update=True)
    if task.status != TaskStatus.PENDING.value:
        raise HTTPException(status_code=400, detail="Task is not pending")
    _require_locked_linked_animal_active(task, locked_animals)
    try:
        await resolve_personal_task_role_fallback(db, task)
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from None
    if not await visible_to(db, task, user, farm, membership, lock_assignee=True):
        raise HTTPException(status_code=403, detail="This duty is not assigned to you")
    # Form-linked duties (see task_action_url) must be closed via their
    # form — a bare call would skip recording the ultrasound/kidding/health
    # data.
    if task_action_url(task) is not None:
        raise HTTPException(status_code=409, detail="Use the linked form to complete this duty")
    # Auto-generated duties (quarantine release, weaning, ...) and every
    # recurring occurrence unlock on their due date. A one-off manual duty may
    # still be closed early, but completing a freshly spawned recurrence early
    # must not manufacture another future PENDING row.
    if (task.auto_generated or task.recur_days is not None) and task.due_date > today(
        farm.timezone
    ):
        raise HTTPException(status_code=409, detail="This duty is not due yet")
    try:
        await complete_task(
            db,
            task,
            user,
            locked_animals=locked_animals,
            reference_date=today(farm.timezone),
        )
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from None
    await db.commit()
    return task_out(task)


@router.post("/{task_id}/skip")
async def skip(
    task_id: int,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    membership: CurrentMembership,
    perms: COMPLETE,
    payload: TaskSkipIn | None = None,
) -> TaskOut:
    # A recurring skip inserts its successor and therefore starts with FARM and
    # takes FK KEY SHARE on the linked animal. Match completion/status ordering
    # (FARM -> ANIMAL -> TASK)
    # so a concurrent sale cannot hold Animal while waiting for this Task as
    # this request holds Task while waiting to insert against Animal.
    await _lock_farm_for_recurring_transition(db, farm, task_id)
    locked_animals = await _lock_completion_animals(db, farm, task_id)
    task = await _get_task(db, farm, task_id, for_update=True)
    if task.status != TaskStatus.PENDING.value:
        raise HTTPException(status_code=400, detail="Task is not pending")
    _require_locked_linked_animal_active(task, locked_animals)
    try:
        await resolve_personal_task_role_fallback(db, task)
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from None
    if not await visible_to(db, task, user, farm, membership, lock_assignee=True):
        raise HTTPException(status_code=403, detail="This duty is not assigned to you")
    if task.recur_days is not None and task.due_date > today(farm.timezone):
        raise HTTPException(status_code=409, detail="This duty is not due yet")
    if task.auto_generated and task.purchase_batch_id is not None:
        # Every batch-linked generated row is an auditable quarantine gate.
        # The final release accepts only DONE/VERIFIED prerequisites and there
        # is intentionally no "reopen skipped health work" shortcut, so
        # allowing SKIPPED here would permanently deadlock a safe release.
        # Once the batch has no ACTIVE animal left there is nothing to release
        # and no health record can be written for it (a linked bulk event needs
        # a non-empty ACTIVE+QUARANTINE snapshot), so the gate would instead
        # stay permanently overdue. An animal never returns to ACTIVE, making
        # this lock-free read safe in the only direction it can move.
        if await _batch_has_active_animal(db, farm, task.purchase_batch_id):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Quarantine protocol duties cannot be skipped; complete the required workflow"
                ),
            )
    if (
        task.auto_generated
        and task.category in (TaskCategory.WEANING.value, TaskCategory.BUCKET_MOVE.value)
        and any(animal.current_bucket == Bucket.RECOVERY.value for animal in locked_animals)
    ):
        # RECOVERY is a closed bucket: LEGAL_BUCKET_TRANSITIONS opens it only
        # for the weaning/postpartum contexts this very duty produces, and no
        # replacement duty can be created. Skipping it would strand the doe and
        # her kids there for good — out of the breeding lifecycle and on the
        # lactating ration — with no remaining API path back.
        raise HTTPException(
            status_code=409,
            detail=(
                "This duty is the only way out of postpartum recovery; "
                "complete it once the animals can be moved"
            ),
        )
    try:
        await skip_task(db, task, user, payload.reason if payload else None)
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from None
    await db.commit()
    return task_out(task)


@router.post("/{task_id}/verify")
async def verify(
    task_id: int, db: DbSession, user: CurrentUser, farm: CurrentFarm, perms: VERIFY
) -> TaskOut:
    # Verifying a recurring verification-required duty is the transition that
    # spawns its successor (see verify_task) — completion cannot, because a
    # reject can reopen it. The successor insert takes FK KEY SHARE on the
    # linked animal, so this route follows the same canonical FARM -> ANIMAL
    # -> TASK lock order as complete/skip; locking the Task row first would
    # invert against animal-first writes (sales, status sweeps) and deadlock.
    await _lock_farm_for_recurring_transition(db, farm, task_id)
    locked_animals = await _lock_completion_animals(db, farm, task_id)
    task = await _get_task(db, farm, task_id, for_update=True)
    if task.status != TaskStatus.DONE.value or not task.needs_verification:
        raise HTTPException(status_code=400, detail="Task is not awaiting verification")
    # Two-person rule: the worker who did the duty cannot verify his own
    # work; the farm owner is exempt.
    if task.completed_by_id == user.id and farm.owner_id != user.id:
        raise HTTPException(status_code=409, detail="Someone else must verify this duty")
    # Completion could only act while the linked animal was ACTIVE; by review
    # time the animal may be sold/dead and its pending duties already swept.
    # Spawning then would plant a PENDING row no sweep revisits and no action
    # can close (complete/skip 409 on an inactive animal), permanently holding
    # one slot of the farm's manual-duty capacity — the series ends instead.
    spawn_successor = task.recur_days is not None and (
        task.animal_id is None
        or any(
            animal.id == task.animal_id and animal.status == AnimalStatus.ACTIVE.value
            for animal in locked_animals
        )
    )
    live_successor = await find_live_recurring_successor(db, task) if spawn_successor else None
    if spawn_successor and not task.auto_generated and live_successor is None:
        # Unlike completion/skip, verification does not close a PENDING row to
        # pay for the successor it spawns — the occurrence being verified is
        # already DONE — so a genuinely net-new manual PENDING row is bounded
        # exactly like creation and rejection, under the same held Farm lock.
        # A retained occurrence may already have produced the series' one live
        # successor; reusing that row consumes no slot and must remain possible
        # even when the queue is currently full.
        await _guard_manual_task_capacity_locked(db, farm)
    try:
        await verify_task(db, task, user, spawn_successor=spawn_successor)
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from None
    await db.commit()
    return task_out(task)


@router.post("/{task_id}/reject")
async def reject(
    payload: TaskRejectIn,
    task_id: int,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    perms: VERIFY,
) -> TaskOut:
    # Reject is the other transition that can add a PENDING manual duty. Take
    # Farm before Task so it serializes with create and recurring successor
    # insertion without forming a Farm/Task lock cycle.
    await lock_manual_task_queue(db, farm)
    # Match complete/skip's canonical FARM -> ANIMAL -> TASK order: reject also
    # sends the duty back to PENDING, so it needs the same re-check that the
    # linked animal is still ACTIVE or the row becomes a stranded PENDING duty
    # no list/sweep can ever reach again.
    locked_animals = await _lock_completion_animals(db, farm, task_id)
    task = await _get_task(db, farm, task_id, for_update=True)
    if task.status != TaskStatus.DONE.value or not task.needs_verification:
        raise HTTPException(status_code=400, detail="Task is not awaiting verification")
    _require_locked_linked_animal_active(task, locked_animals)
    # Rejection returns the duty to PENDING, which is exactly the state
    # ck_tasks_user_assignment_has_role constrains. Repair a pre-D9 personal row
    # first, like complete/skip do: without it the flush raises IntegrityError
    # and the duty can never be sent back to its worker. reject_task repeats the
    # repair for callers other than this route; resolving here maps the failure
    # to a 409 before any capacity check runs.
    try:
        await resolve_personal_task_role_fallback(db, task)
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from None
    if not task.auto_generated:
        await _guard_manual_task_capacity_locked(db, farm)
    await reject_task(db, task, user, (payload.note or "").strip())
    await db.commit()
    return task_out(task)
