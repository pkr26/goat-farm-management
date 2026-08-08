"""Duty engine: tabbed list (today / overdue / upcoming / awaiting
verification / completed), complete / skip / verify / reject actions, and
manual duty creation.

Port of v1 app/routers/tasks.py. Workers see only duties assigned to their
role or to them personally (the owner sees everything); verification views
are farm-wide for tasks.verify holders (a verifier reviews OTHER roles'
work). Duties whose completion means recording data (ULTRASOUND,
KIDDING_DUE, VACCINE, DEWORMING) must be closed through their linked form,
not the bare complete endpoint.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import CurrentFarm, CurrentMembership, CurrentUser, DbSession, require_perm
from ..models import (
    VERIFICATION_REQUIRED_CATEGORIES,
    Animal,
    Farm,
    FarmMembership,
    Role,
    Task,
    TaskStatus,
)
from ..schemas.common import MAX_INT32_ID
from ..schemas.tasks import TaskCreateIn, TaskOut, TaskRejectIn, TaskSkipIn, TaskTabsOut
from ..services import (
    complete_task,
    create_manual_task,
    reject_task,
    skip_task,
    task_scope,
    verify_task,
)
from ..utils import today
from ._shared import TASK_LOADS, task_action_url, task_out, visible_to

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

VIEW = Annotated[set[str], Depends(require_perm("tasks.view"))]
CREATE = Annotated[set[str], Depends(require_perm("tasks.create"))]
COMPLETE = Annotated[set[str], Depends(require_perm("tasks.complete"))]
VERIFY = Annotated[set[str], Depends(require_perm("tasks.verify"))]

# Display enrichment loads (TASK_LOADS), task_action_url, task_out and
# visible_to live in `._shared` — single source of truth shared
# with the dashboard/breeding/kidding/health routers.


async def _get_task(
    db: AsyncSession, farm: Farm, task_id: int, *, for_update: bool = False
) -> Task:
    # Ids above the int4 PK ceiling cannot exist — 404, never an asyncpg
    # int32 DataError (500).
    if task_id > MAX_INT32_ID:
        raise HTTPException(status_code=404, detail="Task not found")
    stmt = select(Task).options(*TASK_LOADS).where(Task.id == task_id)
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


@router.get("")
async def list_tasks(
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    perms: VIEW,
    completed_limit: Annotated[int, Query(ge=1, le=200)] = 100,
    completed_offset: Annotated[int, Query(ge=0)] = 0,
) -> TaskTabsOut:
    """All five v1 tabs in one payload; per-tab counts are the list lengths
    (the completed history keeps v1's 100-row cap)."""
    now = today(farm.timezone)
    scoped = (await task_scope(db, farm, user)).options(*TASK_LOADS)
    pending = scoped.where(Task.status == TaskStatus.PENDING.value)

    # Verification views are farm-wide for tasks.verify holders: a verifier
    # reviews OTHER roles' work, so assignment-scoping would hide exactly
    # what they must see.
    can_verify = "tasks.verify" in perms
    review_base = (
        select(Task).where(Task.farm_id == farm.id).options(*TASK_LOADS) if can_verify else scoped
    )
    done_base = review_base.where(Task.status == TaskStatus.DONE.value)

    today_rows = (await db.execute(pending.where(Task.due_date == now).order_by(Task.id))).scalars()
    overdue_rows = (
        await db.execute(pending.where(Task.due_date < now).order_by(Task.due_date, Task.id))
    ).scalars()
    upcoming_rows = (
        await db.execute(pending.where(Task.due_date > now).order_by(Task.due_date, Task.id))
    ).scalars()
    # Awaiting verification: DONE + a verification-required category (CLEANING)
    # — filtered in SQL, not by loading the whole DONE pile.
    awaiting = list(
        (
            await db.execute(
                done_base.where(Task.category.in_(VERIFICATION_REQUIRED_CATEGORIES)).order_by(
                    Task.completed_at.desc()
                )
            )
        ).scalars()
    )
    # The completed-tab predicate runs in SQL BEFORE the 100-row cap: skipped
    # duties (owner sees them), duties needing no verification, and verified
    # ones — previously the newest 100 rows were filtered in Python, so a DONE
    # pile dominated by unverified CLEANING rows starved the tab.
    finished = review_base.where(
        Task.status.in_(
            [TaskStatus.DONE.value, TaskStatus.VERIFIED.value, TaskStatus.SKIPPED.value]
        ),
        or_(
            Task.status == TaskStatus.SKIPPED.value,
            Task.status == TaskStatus.VERIFIED.value,
            Task.category.notin_(VERIFICATION_REQUIRED_CATEGORIES),
        ),
    )
    completed_total = (
        await db.execute(select(func.count()).select_from(finished.order_by(None).subquery()))
    ).scalar_one()
    completed = list(
        (
            await db.execute(
                finished.order_by(Task.completed_at.desc(), Task.id.desc())
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
        completed_total=completed_total,
        completed_limit=completed_limit,
        completed_offset=completed_offset,
    )


@router.post("", status_code=201)
async def create_task(
    payload: TaskCreateIn, db: DbSession, farm: CurrentFarm, perms: CREATE
) -> TaskOut:
    """Manual duty. v1 silently stripped unknown/cross-farm role or worker
    ids instead of rejecting — kept."""
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title is required")

    role_id = None
    if payload.assigned_role_id is not None and payload.assigned_role_id <= MAX_INT32_ID:
        role = await db.get(Role, payload.assigned_role_id)
        if role is not None and role.farm_id == farm.id:
            role_id = role.id

    worker_id = None
    if payload.assigned_user_id is not None and payload.assigned_user_id <= MAX_INT32_ID:
        result = await db.execute(
            select(FarmMembership).where(
                FarmMembership.farm_id == farm.id,
                FarmMembership.user_id == payload.assigned_user_id,
                FarmMembership.is_active.is_(True),
            )
        )
        membership = result.scalars().first()
        if membership is not None:
            worker_id = membership.user_id

    animal_id = None
    if payload.animal_id is not None and payload.animal_id <= MAX_INT32_ID:
        linked_animal = await db.get(Animal, payload.animal_id)
        if (
            linked_animal is not None
            and linked_animal.farm_id == farm.id
            and linked_animal.status == "ACTIVE"
        ):
            animal_id = linked_animal.id
    if payload.animal_id is not None and animal_id is None:
        raise HTTPException(status_code=400, detail="Assigned animal is not active on this farm")

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
    await db.commit()
    # Re-fetch with TASK_LOADS so task_out can read the relationships.
    return task_out(await _get_task(db, farm, task.id))


@router.post("/{task_id}/complete")
async def complete(
    task_id: int,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    membership: CurrentMembership,
    perms: COMPLETE,
) -> TaskOut:
    # Scalar peek (no ORM load — the locked fetch below must be the first ORM
    # load so its status comes from the post-lock read) to learn the linked
    # animal. Canonical lock order is ANIMAL → TASK: change_status locks the
    # animal then its tasks, so an animal-linked completion must take the
    # animal lock first too — completing a weaning/delivery-move duty while
    # the doe is concurrently sold otherwise inverts the order and deadlocks
    # (PostgreSQL kills one side with a 500).
    peek = (
        (await db.execute(select(Task.farm_id, Task.animal_id).where(Task.id == task_id))).first()
        if task_id <= MAX_INT32_ID
        else None
    )
    if peek is not None and peek.animal_id is not None and peek.farm_id == farm.id:
        await db.execute(select(Animal.id).where(Animal.id == peek.animal_id).with_for_update())
    task = await _get_task(db, farm, task_id, for_update=True)
    if task.status != TaskStatus.PENDING.value:
        raise HTTPException(status_code=400, detail="Task is not pending")
    if not visible_to(task, user, farm, membership):
        raise HTTPException(status_code=403, detail="This duty is not assigned to you")
    # Form-linked duties (see task_action_url) must be closed via their
    # form — a bare call would skip recording the ultrasound/kidding/health
    # data.
    if task_action_url(task) is not None:
        raise HTTPException(status_code=409, detail="Use the linked form to complete this duty")
    # Auto-generated duties (quarantine release, weaning, ...) unlock on
    # their due date; manual duties are exempt.
    if task.auto_generated and task.due_date > today(farm.timezone):
        raise HTTPException(status_code=409, detail="This duty is not due yet")
    try:
        await complete_task(db, task, user)
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
    task = await _get_task(db, farm, task_id, for_update=True)
    if task.status != TaskStatus.PENDING.value:
        raise HTTPException(status_code=400, detail="Task is not pending")
    if not visible_to(task, user, farm, membership):
        raise HTTPException(status_code=403, detail="This duty is not assigned to you")
    await skip_task(db, task, user, payload.reason if payload else None)
    await db.commit()
    return task_out(task)


@router.post("/{task_id}/verify")
async def verify(
    task_id: int, db: DbSession, user: CurrentUser, farm: CurrentFarm, perms: VERIFY
) -> TaskOut:
    task = await _get_task(db, farm, task_id, for_update=True)
    if task.status != TaskStatus.DONE.value or not task.needs_verification:
        raise HTTPException(status_code=400, detail="Task is not awaiting verification")
    # Two-person rule: the worker who did the duty cannot verify his own
    # work; the farm owner is exempt.
    if task.completed_by_id == user.id and farm.owner_id != user.id:
        raise HTTPException(status_code=409, detail="Someone else must verify this duty")
    await verify_task(db, task, user)
    await db.commit()
    return task_out(task)


@router.post("/{task_id}/reject")
async def reject(
    payload: TaskRejectIn, task_id: int, db: DbSession, farm: CurrentFarm, perms: VERIFY
) -> TaskOut:
    task = await _get_task(db, farm, task_id, for_update=True)
    if task.status != TaskStatus.DONE.value or not task.needs_verification:
        raise HTTPException(status_code=400, detail="Task is not awaiting verification")
    await reject_task(db, task, (payload.note or "").strip())
    await db.commit()
    return task_out(task)
