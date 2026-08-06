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

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..deps import CurrentFarm, CurrentMembership, CurrentUser, DbSession, require_perm
from ..models import (
    Farm,
    FarmMembership,
    Role,
    Task,
    TaskCategory,
    TaskStatus,
    User,
)
from ..schemas.common import MAX_INT32_ID
from ..schemas.tasks import TaskCreateIn, TaskOut, TaskRejectIn, TaskTabsOut
from ..services import (
    complete_task,
    create_manual_task,
    reject_task,
    spawn_next_occurrence,
    task_scope,
    verify_task,
)
from ..utils import today

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

VIEW = Annotated[set[str], Depends(require_perm("tasks.view"))]
CREATE = Annotated[set[str], Depends(require_perm("tasks.create"))]
COMPLETE = Annotated[set[str], Depends(require_perm("tasks.complete"))]
VERIFY = Annotated[set[str], Depends(require_perm("tasks.verify"))]

# Display enrichment needs these loaded up front — async forbids lazy loads.
_TASK_LOADS = (
    selectinload(Task.assigned_role),
    selectinload(Task.assigned_user),
    selectinload(Task.animal),
)


def task_action_url(task: Task) -> str | None:
    """Frontend path of the form that closes this duty, when completing it
    means recording data (v1's task_action_url, paths unchanged)."""
    if task.category == TaskCategory.ULTRASOUND.value and task.breeding_record_id:
        return f"/breeding/{task.breeding_record_id}/ultrasound"
    if task.category == TaskCategory.KIDDING_DUE.value and task.breeding_record_id:
        return f"/kidding/new?breeding_id={task.breeding_record_id}"
    if task.category in (TaskCategory.VACCINE.value, TaskCategory.DEWORMING.value):
        params = f"task_id={task.id}"
        if task.animal_id:
            params += f"&animal_id={task.animal_id}"
        if task.purchase_batch_id:
            params += f"&purchase_batch_id={task.purchase_batch_id}"
        return f"/health/new?{params}"
    return None


def _task_out(task: Task) -> TaskOut:
    """Out model + display enrichment. Callers must have loaded the
    assigned_role / assigned_user / animal relationships (_TASK_LOADS)."""
    out = TaskOut.model_validate(task)
    out.assigned_role_name = task.assigned_role.name if task.assigned_role else None
    out.assigned_user_name = task.assigned_user.display_name if task.assigned_user else None
    out.animal_tag = task.animal.tag_number if task.animal else None
    out.needs_verification = task.needs_verification
    out.action_url = task_action_url(task)
    return out


async def _get_task(db: AsyncSession, farm: Farm, task_id: int) -> Task:
    # Ids above the int4 PK ceiling cannot exist — 404, never an asyncpg
    # int32 DataError (500).
    if task_id > MAX_INT32_ID:
        raise HTTPException(status_code=404, detail="Task not found")
    result = await db.execute(select(Task).options(*_TASK_LOADS).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if task is None or task.farm_id != farm.id:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


def _visible_to(task: Task, user: User, farm: Farm, membership: FarmMembership | None) -> bool:
    """Workers may act only on duties shown to them by task_scope."""
    if farm.owner_id == user.id:
        return True
    return task.assigned_user_id == user.id or bool(
        membership and task.assigned_role_id and task.assigned_role_id == membership.role_id
    )


@router.get("")
async def list_tasks(
    db: DbSession, user: CurrentUser, farm: CurrentFarm, perms: VIEW
) -> TaskTabsOut:
    """All five v1 tabs in one payload; per-tab counts are the list lengths
    (the completed history keeps v1's 100-row cap)."""
    now = today()
    scoped = (await task_scope(db, farm, user)).options(*_TASK_LOADS)
    pending = scoped.where(Task.status == TaskStatus.PENDING.value)

    # Verification views are farm-wide for tasks.verify holders: a verifier
    # reviews OTHER roles' work, so assignment-scoping would hide exactly
    # what they must see.
    can_verify = "tasks.verify" in perms
    review_base = (
        select(Task).where(Task.farm_id == farm.id).options(*_TASK_LOADS) if can_verify else scoped
    )
    done_base = review_base.where(Task.status == TaskStatus.DONE.value)

    today_rows = (await db.execute(pending.where(Task.due_date == now).order_by(Task.id))).scalars()
    overdue_rows = (
        await db.execute(pending.where(Task.due_date < now).order_by(Task.due_date, Task.id))
    ).scalars()
    upcoming_rows = (
        await db.execute(pending.where(Task.due_date > now).order_by(Task.due_date, Task.id))
    ).scalars()
    awaiting = [
        t
        for t in (await db.execute(done_base.order_by(Task.completed_at.desc()))).scalars()
        if t.needs_verification
    ]
    finished = review_base.where(
        Task.status.in_(
            [TaskStatus.DONE.value, TaskStatus.VERIFIED.value, TaskStatus.SKIPPED.value]
        )
    )
    completed = [
        t
        for t in (
            await db.execute(finished.order_by(Task.completed_at.desc()).limit(100))
        ).scalars()
        if t.status == TaskStatus.SKIPPED.value  # owner sees skipped duties too
        or not t.needs_verification
        or t.status == TaskStatus.VERIFIED.value
    ]
    return TaskTabsOut(
        today=[_task_out(t) for t in today_rows],
        overdue=[_task_out(t) for t in overdue_rows],
        upcoming=[_task_out(t) for t in upcoming_rows],
        awaiting=[_task_out(t) for t in awaiting],
        completed=[_task_out(t) for t in completed],
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

    task = await create_manual_task(
        db,
        farm,
        title,
        payload.due_date,
        payload.category,
        assigned_role_id=role_id,
        assigned_user_id=worker_id,
        recur_days=payload.recur_days,
    )
    await db.commit()
    # Re-fetch with _TASK_LOADS so _task_out can read the relationships.
    return _task_out(await _get_task(db, farm, task.id))


@router.post("/{task_id}/complete")
async def complete(
    task_id: int,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    membership: CurrentMembership,
    perms: COMPLETE,
) -> TaskOut:
    task = await _get_task(db, farm, task_id)
    if task.status != TaskStatus.PENDING.value:
        raise HTTPException(status_code=400, detail="Task is not pending")
    if not _visible_to(task, user, farm, membership):
        raise HTTPException(status_code=403, detail="This duty is not assigned to you")
    # Form-linked duties (see task_action_url) must be closed via their
    # form — a bare call would skip recording the ultrasound/kidding/health
    # data.
    if task_action_url(task) is not None:
        raise HTTPException(status_code=409, detail="Use the linked form to complete this duty")
    # Auto-generated duties (quarantine release, weaning, ...) unlock on
    # their due date; manual duties are exempt.
    if task.auto_generated and task.due_date > today():
        raise HTTPException(status_code=409, detail="This duty is not due yet")
    await complete_task(db, task, user)
    await db.commit()
    return _task_out(task)


@router.post("/{task_id}/skip")
async def skip(
    task_id: int,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    membership: CurrentMembership,
    perms: COMPLETE,
) -> TaskOut:
    task = await _get_task(db, farm, task_id)
    if task.status != TaskStatus.PENDING.value:
        raise HTTPException(status_code=400, detail="Task is not pending")
    if not _visible_to(task, user, farm, membership):
        raise HTTPException(status_code=403, detail="This duty is not assigned to you")
    task.status = TaskStatus.SKIPPED.value
    if task.recur_days:
        # A skipped occurrence must not kill the series.
        await spawn_next_occurrence(db, task)
    await db.commit()
    return _task_out(task)


@router.post("/{task_id}/verify")
async def verify(
    task_id: int, db: DbSession, user: CurrentUser, farm: CurrentFarm, perms: VERIFY
) -> TaskOut:
    task = await _get_task(db, farm, task_id)
    if task.status != TaskStatus.DONE.value or not task.needs_verification:
        raise HTTPException(status_code=400, detail="Task is not awaiting verification")
    # Two-person rule: the worker who did the duty cannot verify his own
    # work; the farm owner is exempt.
    if task.completed_by_id == user.id and farm.owner_id != user.id:
        raise HTTPException(status_code=409, detail="Someone else must verify this duty")
    await verify_task(db, task, user)
    await db.commit()
    return _task_out(task)


@router.post("/{task_id}/reject")
async def reject(
    payload: TaskRejectIn, task_id: int, db: DbSession, farm: CurrentFarm, perms: VERIFY
) -> TaskOut:
    task = await _get_task(db, farm, task_id)
    if task.status != TaskStatus.DONE.value or not task.needs_verification:
        raise HTTPException(status_code=400, detail="Task is not awaiting verification")
    await reject_task(db, task, (payload.note or "").strip())
    await db.commit()
    return _task_out(task)
