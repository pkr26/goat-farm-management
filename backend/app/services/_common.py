"""Cross-domain private helpers shared by the services submodules."""

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    Animal,
    BreedingRecord,
    KiddingRecord,
    Role,
    Task,
    TaskCategory,
    TaskStatus,
)
from ..permissions import TASK_CATEGORY_ROLE_MAP


def _clear_task_rejection(task: Task) -> None:
    """Clear rejection metadata when a task leaves its returned-to-PENDING state."""
    task.verification_note = None
    task.rejected_by_id = None
    task.rejected_at = None


async def _pending_tasks_for(
    db: AsyncSession, farm_id: int, for_update: bool = False, **filters: object
) -> list[Task]:
    stmt = select(Task).where(Task.farm_id == farm_id, Task.status == TaskStatus.PENDING.value)
    for key, value in filters.items():
        stmt = stmt.where(getattr(Task, key) == value)
    if for_update:
        # SELECT ... FOR UPDATE serializes against concurrent completions on
        # the same rows (PostgreSQL re-checks the WHERE after the lock wait,
        # so a task flipped non-PENDING meanwhile drops out of the result).
        stmt = stmt.with_for_update()
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def _default_role_id_for_category(
    db: AsyncSession, farm_id: int, category: str
) -> int | None:
    """Map a task category to the farm's preset role (MOVER/VET/...) if seeded."""
    role_code = TASK_CATEGORY_ROLE_MAP.get(category)
    if not role_code:
        return None
    result = await db.execute(
        select(Role).where(
            Role.farm_id == farm_id,
            Role.code == role_code,
            Role.deleted_at.is_(None),
        )
    )
    # uq_roles_farm_preset_code makes the stable preset identity cardinality
    # exactly zero-or-one. Do not hide schema damage behind an arbitrary
    # ``first()`` selection.
    role = result.scalar_one_or_none()
    return role.id if role else None


async def _add_task(
    db: AsyncSession,
    farm_id: int,
    title: str,
    due_date: date,
    category: TaskCategory,
    animal_id: int | None = None,
    purchase_batch_id: int | None = None,
    breeding_record_id: int | None = None,
) -> Task:
    task = Task(
        farm_id=farm_id,
        title=title,
        due_date=due_date,
        category=category.value,
        animal_id=animal_id,
        purchase_batch_id=purchase_batch_id,
        breeding_record_id=breeding_record_id,
        auto_generated=True,
        assigned_role_id=await _default_role_id_for_category(db, farm_id, category.value),
    )
    db.add(task)
    return task


async def _load_doe(db: AsyncSession, br: BreedingRecord) -> Animal:
    """``br.doe``, loaded explicitly (async sessions forbid implicit lazy loads).
    doe_id is a non-nullable FK, so a missing row means corrupt data."""
    doe = await db.get(Animal, br.doe_id)
    if doe is None:
        raise ValueError(f"Breeding record {br.id} references missing doe {br.doe_id}")
    return doe


async def _kidding_record_of(db: AsyncSession, br: BreedingRecord) -> KiddingRecord | None:
    """``br.kidding_record`` (one-to-one), loaded explicitly (async sessions
    forbid implicit lazy loads)."""
    result = await db.execute(
        select(KiddingRecord).where(KiddingRecord.breeding_record_id == br.id)
    )
    return result.scalars().first()
