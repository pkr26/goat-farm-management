"""Kidding."""

from datetime import date, timedelta
from typing import TypedDict

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    GESTATION_DAYS,
    MAX_GESTATION_DAYS,
    MIN_GESTATION_DAYS,
    WEANING_DAYS,
    Animal,
    AnimalSource,
    AnimalStatus,
    BirthType,
    BreedingOutcome,
    BreedingRecord,
    Bucket,
    BucketMove,
    Farm,
    KiddingRecord,
    KidEntry,
    KidStatus,
    TaskCategory,
    TaskStatus,
)
from ..utils import utcnow
from ._common import _add_task, _kidding_record_of, _load_doe, _pending_tasks_for
from .animals import _tag_exists, move_animal


class KidSpec(TypedDict):
    """One kid in a kidding record."""

    tag: str  # the router fills the auto "<doe>-K<n>" tag when left blank
    sex: str
    birth_weight: float | None
    status: str
    mortality_reported_at: date | None


async def record_kidding(
    db: AsyncSession,
    farm: Farm,
    br: BreedingRecord,
    kidding_date: date,
    ease: str,
    notes: str,
    kids: list[KidSpec],
    created_by_id: int | None = None,
) -> KiddingRecord:
    """Record a kidding. Alive kids auto-create Animal rows (source=BORN,
    dam/sire linked, bucket=RECOVERY). Doe → RECOVERY; WEANING task at +60d.

    Only a confirmed, not-yet-kidded pregnancy of an ACTIVE doe can kidd:
    a sold/dead doe must not "deliver" new stock onto the farm. The kidding
    date must imply a plausible gestation (MIN/MAX_GESTATION_DAYS) — the SPEC
    window is 145–155 days, so 100–200 accepts any real record while rejecting
    absurd dates that would silently corrupt gestation statistics. The caller
    must hold the breeding row's FOR UPDATE lock (and the doe's, in that
    order — animal → breeding → task), so the outcome/kidding guards above
    run against the latest committed state."""
    if br.outcome != BreedingOutcome.CONFIRMED_PREGNANT.value:
        raise ValueError("Kidding requires a confirmed pregnancy")
    if await _kidding_record_of(db, br) is not None:
        raise ValueError("This pregnancy already has a kidding record")
    doe = await _load_doe(db, br)
    if doe.status != AnimalStatus.ACTIVE.value:
        raise ValueError(f"{doe.tag_number} is {doe.status.lower()} — cannot record a kidding")
    gestation = (kidding_date - br.breeding_date).days
    if not MIN_GESTATION_DAYS <= gestation <= MAX_GESTATION_DAYS:
        raise ValueError(
            f"Kidding date implies a {gestation}-day gestation — goats kid at "
            f"~{GESTATION_DAYS} days (accepted window "
            f"{MIN_GESTATION_DAYS}–{MAX_GESTATION_DAYS} days)"
        )
    record = KiddingRecord(
        farm_id=farm.id,
        doe_id=doe.id,
        date=kidding_date,
        breeding_record_id=br.id,
        ease=ease,
        notes=notes or None,
        created_by_id=created_by_id,
    )
    db.add(record)
    await db.flush()

    alive_count = sum(1 for k in kids if k["status"] == KidStatus.ALIVE.value)
    born_count = sum(1 for k in kids if k["status"] != KidStatus.STILLBORN.value)
    if born_count >= 5:
        birth_type: BirthType | None = BirthType.MULTIPLET
    else:
        birth_type = {
            1: BirthType.SINGLE,
            2: BirthType.TWIN,
            3: BirthType.TRIPLET,
            4: BirthType.QUADRUPLET,
        }.get(born_count)

    # Tags are unique per farm; auto tags ("<doe>-K<n>") collide on a doe's
    # second kidding, so uniquify instead of crashing on the constraint. Probe
    # per candidate instead of scanning the farm's whole tag
    # column; `assigned_tags` covers tags taken by kids earlier in THIS request.
    assigned_tags: set[str] = set()

    async def _unique_tag(base: str) -> str:
        tag, n = base, 2
        while tag in assigned_tags or await _tag_exists(db, farm.id, tag):
            tag = f"{base}-{n}"
            n += 1
        assigned_tags.add(tag)
        return tag

    for kid in kids:
        entry = KidEntry(
            kidding_record_id=record.id,
            tag=kid["tag"] or None,
            sex=kid["sex"],
            birth_weight=kid["birth_weight"],
            status=kid["status"],
            mortality_reported_at=kid["mortality_reported_at"],
        )
        db.add(entry)
        await db.flush()
        # DIED is a neonatal mortality after a live birth, not a stillbirth:
        # retain an Animal record in DEAD state for lineage and mortality
        # traceability, while keeping it out of the active herd.
        if kid["status"] != KidStatus.STILLBORN.value:
            tag = await _unique_tag(kid["tag"])
            animal = Animal(
                farm_id=farm.id,
                tag_number=tag,
                sex=kid["sex"],
                breed=doe.breed,
                source=AnimalSource.BORN.value,
                date_of_birth=kidding_date,
                birth_type=birth_type.value if birth_type else None,
                dam_id=doe.id,
                sire_id=br.buck_id,
                birth_weight=kid["birth_weight"],
                current_bucket=Bucket.RECOVERY.value,
                status=(
                    AnimalStatus.ACTIVE.value
                    if kid["status"] == KidStatus.ALIVE.value
                    else AnimalStatus.DEAD.value
                ),
                status_date=(kidding_date if kid["status"] == KidStatus.DIED.value else None),
                status_notes=(
                    "Neonatal mortality recorded" if kid["status"] == KidStatus.DIED.value else None
                ),
                mortality_reported_at=kid["mortality_reported_at"],
            )
            db.add(animal)
            await db.flush()
            db.add(
                BucketMove(
                    animal_id=animal.id,
                    from_bucket=None,
                    to_bucket=Bucket.RECOVERY.value,
                    reason="Born",
                    created_by_id=created_by_id,
                )
            )
            entry.tag = tag
            entry.animal_id = animal.id

    # Doe goes to RECOVERY whether she was in DELIVERY or still in PREGNANCY_LATE.
    move_animal(db, doe, Bucket.RECOVERY.value, "Kidded")

    # Locked + re-checked like the leftover skip below: a concurrent user-skip
    # of the KIDDING_DUE duty (the skip endpoint allows form-linked duties)
    # must survive — never flip a committed SKIPPED back to DONE.
    for task in await _pending_tasks_for(
        db,
        farm.id,
        for_update=True,
        breeding_record_id=br.id,
        category=TaskCategory.KIDDING_DUE.value,
    ):
        if task.status == TaskStatus.PENDING.value:
            task.status = TaskStatus.DONE.value
            task.completed_by_id = created_by_id
            task.completed_at = utcnow()
    # Flush before the leftover query: sessions run autoflush=False, so this
    # keeps the DB row in step with the DONE stamped above (the re-check under
    # the lock below is the actual guard against clobbering it back to SKIPPED).
    await db.flush()
    # Leftover pre-kidding tasks (ET+TT vaccine, move to DELIVERY) are moot
    # once she has kidded — skip them so they don't linger as overdue noise.
    # Locked + re-checked like skip_pending_tasks_for_animal: a concurrently
    # committed completion must survive.
    for task in await _pending_tasks_for(db, farm.id, for_update=True, breeding_record_id=br.id):
        if task.status == TaskStatus.PENDING.value:
            task.status = TaskStatus.SKIPPED.value
            task.skipped_by_id = None
            task.skipped_at = utcnow()
            task.skip_reason = "Kidding recorded; remaining pregnancy duty no longer applies"

    if alive_count:
        await _add_task(
            db,
            farm.id,
            f"Wean kids of {doe.tag_number}; doe → RESTING",
            kidding_date + timedelta(days=WEANING_DAYS),
            TaskCategory.WEANING,
            animal_id=doe.id,
        )
    await db.flush()
    return record
