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
from ..permissions import task_role_codes

# Rest length after a doe's litter is weaned (or her no-survivor postpartum
# recovery ends) before the next service: 30 RESTING days covers the
# min-rest/flush window (GOAT_PROFILE.min_rest_flush_days) with margin, so
# the re-breeding prompt lands when she is biologically ready to return to
# the breeding pen.
REBREED_AFTER_RESTING_DAYS = 30


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
    """Map a task category to the farm's preset role (MOVER/VET/...), if seeded.

    task_role_codes returns the candidates in priority order; the first
    candidate with a live seeded role wins.
    """
    codes = task_role_codes(category)
    if not codes:
        return None
    result = await db.execute(
        select(Role).where(
            Role.farm_id == farm_id,
            Role.code.in_(codes),
            Role.deleted_at.is_(None),
        )
    )
    # uq_roles_farm_preset_code makes each candidate's cardinality exactly
    # zero-or-one; grouping by code just maps the rows for the priority walk
    # below (first live candidate wins).
    roles_by_code = {role.code: role for role in result.scalars().all()}
    for code in codes:
        role = roles_by_code.get(code)
        if role is not None:
            return role.id
    return None


async def _add_task(
    db: AsyncSession,
    farm_id: int,
    title: str,
    due_date: date,
    category: TaskCategory,
    animal_id: int | None = None,
    purchase_batch_id: int | None = None,
    breeding_record_id: int | None = None,
    *,
    title_key: str | None = None,
    title_args: dict[str, object] | None = None,
) -> Task:
    """Add one generated duty; ``title_key``/``title_args`` are the client's
    localization contract while ``title`` stays the English fallback."""
    task = Task(
        farm_id=farm_id,
        title=title,
        title_key=title_key,
        title_args=dict(title_args) if title_args is not None else {},
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


async def _schedule_rebreed(db: AsyncSession, farm_id: int, doe: Animal, due: date) -> None:
    """(Re-)date the doe's re-breeding prompt once her rest begins.

    Every RESTING entry that starts a rest — weaning, the no-survivor
    postpartum recovery, and a recorded abortion — funnels here. Dedupe is
    the manual ON-conFLICT equivalent: a still-PENDING REBREED duty for the
    doe is re-dated in place (row-locked like replan_dam_after_last_kid_death's
    reuse) rather than growing a second parallel prompt, so however many
    paths schedule the rest, exactly one re-breeding duty is ever open.
    Replay safety comes from complete_task's non-PENDING no-op: a re-completed
    weaning/postpartum duty never reaches this insert twice. Like every other
    generated duty there is no creator attribution — the linked weaning or
    postpartum row is the audit trail."""
    # Sessions run autoflush=False: persist any pending inserts (a prior
    # _schedule_rebreed in this same transaction) so the dedupe SELECT below
    # sees them — the same flush-before-lookup spawn_next_occurrence needs.
    await db.flush()
    existing = (
        await db.execute(
            select(Task)
            .where(
                Task.farm_id == farm_id,
                Task.animal_id == doe.id,
                Task.category == TaskCategory.REBREED.value,
                Task.status == TaskStatus.PENDING.value,
            )
            .with_for_update()
            .order_by(Task.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is None:
        await _add_task(
            db,
            farm_id,
            f"Re-breed {doe.tag_number} (resting complete — flush window done)",
            due,
            TaskCategory.REBREED,
            animal_id=doe.id,
            title_key="rebreed",
            title_args={"tag": doe.tag_number, "due_date": due.isoformat()},
        )
    else:
        existing.due_date = due


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
