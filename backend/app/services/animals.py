"""Animals: lifecycle transitions, pending-task skips, tag generation."""

import secrets
from datetime import date
from typing import Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    BREEDING_READY_BUCKETS,
    MIN_BREEDING_AGE_MONTHS,
    MIN_BREEDING_WEIGHT_KG,
    MIN_BUCK_BREEDING_AGE_MONTHS,
    MIN_BUCK_BREEDING_WEIGHT_KG,
    Animal,
    AnimalStatus,
    Bucket,
    BucketMove,
    Task,
    TaskStatus,
)
from ..utils import today, utcnow

TransitionContext = Literal[
    "manual",
    "history_override",
    "breeding",
    "ultrasound",
    "quarantine_release",
    "delivery",
    "kidding",
    "abortion",
    "postpartum",
    "weaning",
    "orphan_weaning",
]
TransitionFacts = tuple[float | None, bool]

# Each edge is intentional.  Workflow-only edges cannot be forged through
# POST /animals/{id}/move: the corresponding domain record/task must cause
# them.  ``history_override`` is handled separately after the factual guards.
LEGAL_BUCKET_TRANSITIONS: dict[tuple[str, str], frozenset[str]] = {
    (Bucket.QUARANTINE.value, Bucket.FOUNDATION.value): frozenset({"quarantine_release"}),
    (Bucket.FOUNDATION.value, Bucket.BREEDING.value): frozenset({"manual", "breeding"}),
    (Bucket.FEMALE_KIDS.value, Bucket.FOUNDATION.value): frozenset({"manual"}),
    (Bucket.FEMALE_KIDS.value, Bucket.BREEDING.value): frozenset({"manual", "breeding"}),
    (Bucket.MALE_KIDS.value, Bucket.BREEDING.value): frozenset({"manual"}),
    (Bucket.RESTING.value, Bucket.BREEDING.value): frozenset({"manual", "breeding"}),
    (Bucket.BREEDING.value, Bucket.RESTING.value): frozenset({"manual"}),
    (Bucket.BREEDING.value, Bucket.PREGNANCY_EARLY.value): frozenset({"ultrasound"}),
    (Bucket.PREGNANCY_EARLY.value, Bucket.PREGNANCY_LATE.value): frozenset({"manual"}),
    (Bucket.PREGNANCY_EARLY.value, Bucket.DELIVERY.value): frozenset({"delivery"}),
    (Bucket.PREGNANCY_LATE.value, Bucket.DELIVERY.value): frozenset({"manual", "delivery"}),
    (Bucket.PREGNANCY_EARLY.value, Bucket.RESTING.value): frozenset({"abortion"}),
    (Bucket.PREGNANCY_LATE.value, Bucket.RESTING.value): frozenset({"abortion"}),
    (Bucket.DELIVERY.value, Bucket.RESTING.value): frozenset({"abortion", "weaning"}),
    (Bucket.PREGNANCY_EARLY.value, Bucket.RECOVERY.value): frozenset({"kidding"}),
    (Bucket.PREGNANCY_LATE.value, Bucket.RECOVERY.value): frozenset({"kidding"}),
    (Bucket.DELIVERY.value, Bucket.RECOVERY.value): frozenset({"kidding"}),
    (Bucket.RECOVERY.value, Bucket.RESTING.value): frozenset({"postpartum", "weaning"}),
    (Bucket.RECOVERY.value, Bucket.MALE_KIDS.value): frozenset({"weaning", "orphan_weaning"}),
    (Bucket.RECOVERY.value, Bucket.FEMALE_KIDS.value): frozenset({"weaning", "orphan_weaning"}),
}


def bucket_transition_error(
    animal: Animal,
    to_bucket: str,
    *,
    context: TransitionContext = "manual",
    reference_date: date | None = None,
    facts: TransitionFacts | None = None,
    allow_restricted_reclassification: bool = False,
) -> str | None:
    """Return the model-level reason a lifecycle move must be blocked.

    The checks are deliberately factual rather than clinical: manual moves
    cannot override a hold/restriction or impossible sex mapping. Authoritative
    reproductive workflows must opt in explicitly when they reclassify an
    animal without clearing the physical movement hold.
    """
    if animal.status != AnimalStatus.ACTIVE.value:
        return f"{animal.tag_number} is {animal.status.lower()} — cannot move buckets"
    if (
        animal.movement_restricted or animal.suspected_scheduled_disease
    ) and not allow_restricted_reclassification:
        return f"{animal.tag_number} has a recorded movement restriction or disease hold"
    if animal.current_bucket == to_bucket:
        return None
    if to_bucket == Bucket.MALE_KIDS.value and animal.sex != "M":
        return "Only male animals may enter MALE_KIDS"
    if to_bucket == Bucket.FEMALE_KIDS.value and animal.sex != "F":
        return "Only female animals may enter FEMALE_KIDS"
    if to_bucket == Bucket.RESTING.value and animal.sex != "F":
        return "Only female animals may enter RESTING"
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
    if context == "history_override":
        return None
    allowed_contexts = LEGAL_BUCKET_TRANSITIONS.get((animal.current_bucket, to_bucket))
    if allowed_contexts is None or context not in allowed_contexts:
        return (
            f"Illegal lifecycle transition {animal.current_bucket} → {to_bucket}; "
            "use the required breeding, health, kidding or quarantine workflow"
        )
    when = reference_date or today()
    if to_bucket == Bucket.BREEDING.value:
        if animal.sex == "F":
            if facts is None:
                eligible = animal.is_breeding_eligible_on(when)
            else:
                latest_weight_kg, is_currently_pregnant = facts
                age = animal.age_months_on(when)
                eligible = bool(
                    animal.status == AnimalStatus.ACTIVE.value
                    and not animal.movement_restricted
                    and not animal.suspected_scheduled_disease
                    and animal.current_bucket in {*BREEDING_READY_BUCKETS, "BREEDING"}
                    and age is not None
                    and age >= MIN_BREEDING_AGE_MONTHS
                    and latest_weight_kg is not None
                    and latest_weight_kg >= MIN_BREEDING_WEIGHT_KG
                    and not is_currently_pregnant
                )
            if not eligible:
                return f"{animal.tag_number} is not eligible to enter BREEDING"
        # A mature buck must be able to graduate from MALE_KIDS into the
        # candidate bucket; actual breeding selection separately requires
        # current_bucket FOUNDATION/BREEDING via is_buck_eligible_on().
        if animal.sex == "M":
            if facts is None:
                ready = animal.is_buck_ready_on(when)
            else:
                latest_weight_kg, _is_currently_pregnant = facts
                age = animal.age_months_on(when)
                ready = bool(
                    animal.status == AnimalStatus.ACTIVE.value
                    and not animal.movement_restricted
                    and not animal.suspected_scheduled_disease
                    and age is not None
                    and age >= MIN_BUCK_BREEDING_AGE_MONTHS
                    and latest_weight_kg is not None
                    and latest_weight_kg >= MIN_BUCK_BREEDING_WEIGHT_KG
                )
            if not ready:
                return f"{animal.tag_number} is not eligible to enter BREEDING"
    return None


def require_bucket_transition(
    animal: Animal,
    to_bucket: str,
    *,
    context: TransitionContext = "manual",
    reference_date: date | None = None,
    facts: TransitionFacts | None = None,
    allow_restricted_reclassification: bool = False,
) -> None:
    if error := bucket_transition_error(
        animal,
        to_bucket,
        context=context,
        reference_date=reference_date,
        facts=facts,
        allow_restricted_reclassification=allow_restricted_reclassification,
    ):
        raise ValueError(error)


def move_animal(
    db: AsyncSession,
    animal: Animal,
    to_bucket: str,
    reason: str = "",
    created_by_id: int | None = None,
    *,
    context: TransitionContext = "manual",
    reference_date: date | None = None,
    facts: TransitionFacts | None = None,
    allow_restricted_reclassification: bool = False,
) -> None:
    """Record a BucketMove and update the animal's current bucket.
    Non-ACTIVE (sold/dead/culled) animals never move — they are out of the
    herd lifecycle, so task side effects and forged posts must not relocate
    them. (Synchronous: only session-state mutation, no I/O.)"""
    if animal.status != AnimalStatus.ACTIVE.value:
        return
    if (
        animal.movement_restricted or animal.suspected_scheduled_disease
    ) and not allow_restricted_reclassification:
        return
    if animal.current_bucket == to_bucket:
        return
    require_bucket_transition(
        animal,
        to_bucket,
        context=context,
        reference_date=reference_date,
        facts=facts,
        allow_restricted_reclassification=allow_restricted_reclassification,
    )
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
    *,
    batch_size: int = 500,
) -> int:
    """Cancel an animal's pending tasks (death/sale/cull): a dead or sold
    animal must not keep generating work (vaccines, moves, kidding due)."""
    if not 1 <= batch_size <= 10_000:
        raise ValueError("batch_size must be between 1 and 10000")
    # Materialize the finite locked ID set before UPDATE. Embedding a
    # FOR-UPDATE/LIMIT select directly inside the UPDATE predicate lets
    # PostgreSQL rescan it as rows change and can update beyond the apparent
    # limit—precisely the unbounded request work this helper prevents.
    candidate_ids = list(
        (
            await db.execute(
                select(Task.id)
                .where(
                    Task.farm_id == farm_id,
                    Task.animal_id == animal_id,
                    Task.status == TaskStatus.PENDING.value,
                )
                # Match the online partial cleanup index
                # (animal_id, id) WHERE status='PENDING'. A global id order
                # can force PostgreSQL to scan/sort the entire pending cohort
                # before returning this bounded worker batch.
                .order_by(Task.animal_id, Task.id)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
        ).scalars()
    )
    if not candidate_ids:
        return 0
    skipped = await db.execute(
        update(Task)
        .where(Task.id.in_(candidate_ids), Task.status == TaskStatus.PENDING.value)
        .values(
            status=TaskStatus.SKIPPED.value,
            skipped_by_id=None,
            skipped_at=utcnow(),
            skip_reason=reason[:255],
        )
        .returning(Task.id)
    )
    return len(skipped.scalars().all())


async def skip_inactive_animal_tasks_batch(db: AsyncSession, *, batch_size: int) -> int:
    """Skip one finite global batch left after high-cardinality retirements.

    The animal status change is authoritative immediately. Request paths hide
    and refuse stale pending duties linked to inactive animals; this worker
    converges their stored status without making the retirement request lock or
    rewrite an attacker-controlled number of Task rows.
    """
    if not 1 <= batch_size <= 10_000:
        raise ValueError("batch_size must be between 1 and 10000")
    candidate_ids = list(
        (
            await db.execute(
                select(Task.id)
                .join(Animal, Animal.id == Task.animal_id)
                .where(
                    Task.status == TaskStatus.PENDING.value,
                    Animal.status != AnimalStatus.ACTIVE.value,
                )
                .order_by(Task.id)
                .limit(batch_size)
                .with_for_update(skip_locked=True, of=Task)
            )
        ).scalars()
    )
    if not candidate_ids:
        return 0
    skipped = await db.execute(
        update(Task)
        .where(Task.id.in_(candidate_ids), Task.status == TaskStatus.PENDING.value)
        .values(
            status=TaskStatus.SKIPPED.value,
            skipped_by_id=None,
            skipped_at=utcnow(),
            skip_reason="Animal removed from active lifecycle",
        )
        .returning(Task.id)
    )
    return len(skipped.scalars().all())


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
