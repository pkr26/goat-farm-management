"""Out-builders and visibility helpers shared by the API routers.

These lived as near-verbatim private copies in `tasks.py` / `dashboard.py` /
`breeding.py` and were imported across routers (`kidding.py`, `health.py`).
This module is the single source of truth; names are public-style because the
routers share them.
"""

from sqlalchemy.orm import selectinload

from ..models import BreedingRecord, Farm, FarmMembership, Task, TaskCategory, User
from ..schemas.breeding import BreedingRecordOut
from ..schemas.tasks import TaskOut

# Display enrichment needs these loaded up front — async forbids lazy loads.
TASK_LOADS = (
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


def task_out(task: Task) -> TaskOut:
    """Out model + display enrichment. Callers must have loaded the
    assigned_role / assigned_user / animal relationships (TASK_LOADS)."""
    out = TaskOut.model_validate(task)
    out.assigned_role_name = task.assigned_role.name if task.assigned_role else None
    out.assigned_user_name = task.assigned_user.display_name if task.assigned_user else None
    out.animal_tag = task.animal.tag_number if task.animal else None
    out.needs_verification = task.needs_verification
    out.action_url = task_action_url(task)
    return out


def breeding_out(br: BreedingRecord) -> BreedingRecordOut:
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


def visible_to(task: Task, user: User, farm: Farm, membership: FarmMembership | None) -> bool:
    """Workers may act only on duties shown to them by task_scope."""
    if farm.owner_id == user.id:
        return True
    return task.assigned_user_id == user.id or bool(
        membership and task.assigned_role_id and task.assigned_role_id == membership.role_id
    )
