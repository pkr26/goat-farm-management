"""Animals: bucket moves, pending-task skips, tag generation."""

import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Animal, AnimalStatus, Bucket, BucketMove, TaskStatus
from ..utils import utcnow
from ._common import _pending_tasks_for


def bucket_transition_error(animal: Animal, to_bucket: str) -> str | None:
    """Return the model-level reason a lifecycle move must be blocked.

    The checks are deliberately factual rather than clinical: a recorded
    hold/restriction and impossible sex-to-bucket mappings are never silently
    overridden through the current API.
    """
    if animal.status != AnimalStatus.ACTIVE.value:
        return f"{animal.tag_number} is {animal.status.lower()} — cannot move buckets"
    if animal.movement_restricted or animal.suspected_scheduled_disease:
        return f"{animal.tag_number} has a recorded movement restriction or disease hold"
    if to_bucket == Bucket.MALE_KIDS.value and animal.sex != "M":
        return "Only male animals may enter MALE_KIDS"
    if to_bucket == Bucket.FEMALE_KIDS.value and animal.sex != "F":
        return "Only female animals may enter FEMALE_KIDS"
    if (
        to_bucket
        in {
            Bucket.PREGNANCY_EARLY.value,
            Bucket.PREGNANCY_LATE.value,
            Bucket.DELIVERY.value,
        }
        and animal.sex != "F"
    ):
        return f"Only female animals may enter {to_bucket}"
    return None


def require_bucket_transition(animal: Animal, to_bucket: str) -> None:
    if error := bucket_transition_error(animal, to_bucket):
        raise ValueError(error)


def move_animal(
    db: AsyncSession,
    animal: Animal,
    to_bucket: str,
    reason: str = "",
    created_by_id: int | None = None,
) -> None:
    """Record a BucketMove and update the animal's current bucket.
    Non-ACTIVE (sold/dead/culled) animals never move — they are out of the
    herd lifecycle, so task side effects and forged posts must not relocate
    them. (Synchronous: only session-state mutation, no I/O.)"""
    if animal.status != AnimalStatus.ACTIVE.value:
        return
    if animal.movement_restricted or animal.suspected_scheduled_disease:
        return
    if animal.current_bucket == to_bucket:
        return
    db.add(
        BucketMove(
            animal_id=animal.id,
            from_bucket=animal.current_bucket,
            to_bucket=to_bucket,
            reason=reason or None,
            created_by_id=created_by_id,
        )
    )
    animal.current_bucket = to_bucket


async def skip_pending_tasks_for_animal(
    db: AsyncSession,
    farm_id: int,
    animal_id: int,
    reason: str = "Animal removed from active lifecycle",
) -> None:
    """Cancel an animal's pending tasks (death/sale/cull): a dead or sold
    animal must not keep generating work (vaccines, moves, kidding due)."""
    for task in await _pending_tasks_for(db, farm_id, for_update=True, animal_id=animal_id):
        # Re-check under the row lock: a concurrent completion that committed
        # DONE while we waited must survive — never overwrite it to SKIPPED.
        if task.status == TaskStatus.PENDING.value:
            task.status = TaskStatus.SKIPPED.value
            task.skipped_by_id = None
            task.skipped_at = utcnow()
            task.skip_reason = reason


# Placeholder tag scheme until RFID scanning is introduced; manual tags are
# still accepted — this only fills in tags the user leaves blank.
TAG_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"  # digits+uppercase minus 0/O/1/I


async def _tag_exists(db: AsyncSession, farm_id: int, tag: str) -> bool:
    """Existence probe against uq_animal_tag_per_farm: scanning the
    farm's whole tag column per insert was O(herd size) rows per write."""
    result = await db.execute(
        select(Animal.id).where(Animal.farm_id == farm_id, Animal.tag_number == tag)
    )
    return result.scalar_one_or_none() is not None


async def generate_unique_tag(db: AsyncSession, farm_id: int, attempts: int = 10) -> str:
    """Random ``G-XXXXX`` tag, unique within the farm; retries on collision."""
    for _ in range(attempts):
        tag = "G-" + "".join(secrets.choice(TAG_ALPHABET) for _ in range(5))
        if not await _tag_exists(db, farm_id, tag):
            return tag
    raise RuntimeError("could not generate a unique tag")  # practically unreachable
