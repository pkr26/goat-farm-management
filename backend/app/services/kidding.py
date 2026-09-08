"""Kidding."""

import secrets
from datetime import date, timedelta
from typing import TypedDict

from sqlalchemy import func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    HISTORY_OVERRIDE_REASON_PREFIX,
    MAX_ANIMAL_TAG_LENGTH,
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
    Task,
    TaskCategory,
    TaskStatus,
)
from ..models.species import GOAT_PROFILE
from ..utils import today, utcnow
from ._common import (
    _add_task,
    _clear_task_rejection,
    _kidding_record_of,
    _load_doe,
    _pending_tasks_for,
)
from .animals import _tag_exists, move_animal


class LitterSizeError(ValueError):
    """A litter above the species' biological cap (goat ≤4).

    Input-shape validation, not a raced lifecycle state: routers map it to
    422 while every other ValueError from ``record_kidding`` stays a 409.
    """


class KidSpec(TypedDict):
    """One kid in a kidding record."""

    tag: str  # the router fills the auto "<doe>-K<n>" tag when left blank
    tag_is_explicit: bool
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

    Only a confirmed, not-yet-delivered pregnancy of an ACTIVE doe can deliver:
    a sold/dead doe must not "deliver" new stock onto the farm. The kidding
    date must imply a plausible gestation (goats ~150 days) — the accepted
    window covers any real record while rejecting absurd dates that would
    silently corrupt gestation
    statistics. The caller must hold the breeding row's FOR UPDATE lock (and
    the doe's, in that order — animal → breeding → task), so the
    outcome/kidding guards above run against the latest committed state."""
    if br.outcome != BreedingOutcome.CONFIRMED_PREGNANT.value:
        raise ValueError("Kidding requires a confirmed pregnancy")
    if await _kidding_record_of(db, br) is not None:
        raise ValueError("This pregnancy already has a kidding record")
    doe = await _load_doe(db, br)
    if doe.status != AnimalStatus.ACTIVE.value:
        raise ValueError(f"{doe.tag_number} is {doe.status.lower()} — cannot record a kidding")
    profile = GOAT_PROFILE
    gestation = (kidding_date - br.breeding_date).days
    if not profile.min_gestation_days <= gestation <= profile.max_gestation_days:
        raise ValueError(
            f"Delivery date implies a {gestation}-day gestation — goat "
            f"gestation is ~{profile.gestation_days} days (accepted window "
            f"{profile.min_gestation_days}–{profile.max_gestation_days} days)"
        )
    # A doe cannot deliver before the scan that confirmed she was carrying.
    # The loss path enforces the same ordering (mark_aborted plus the
    # ck_breeding_loss_after_ultrasound CHECK); a late-entered result date
    # would otherwise leave a delivery predating its own confirmation.
    if br.ultrasound_result_date is not None and kidding_date < br.ultrasound_result_date:
        raise ValueError("Kidding date cannot predate the pregnancy confirmation")
    if len(kids) > profile.max_litter_size:
        raise LitterSizeError(
            f"A kidding cannot deliver more than "
            f"{profile.max_litter_size} kids (recorded {len(kids)})"
        )
    for kid in kids:
        mortality_date = kid["mortality_reported_at"]
        if kid["status"] == KidStatus.DIED.value:
            if mortality_date is None:
                raise ValueError("A died kid requires a mortality date")
            if mortality_date < kidding_date:
                raise ValueError("Kid mortality date cannot predate the kidding date")
            if mortality_date > today(farm.timezone):
                raise ValueError("Kid mortality date cannot be in the future")
        elif mortality_date is not None:
            raise ValueError("Only a died kid may have a mortality date")

    # The tag-namespace triggers (migration d3b5f7c9e024) key their advisory
    # locks on farm_id alone: an `animals` INSERT takes SHARE, while a tagged
    # STILLBORN `kid_entries` INSERT takes EXCLUSIVE. The kid loop below walks
    # the payload in order, so a litter listing a live kid before a stillborn
    # one asks for SHARE and then EXCLUSIVE on the same key inside a single
    # transaction. Two such kiddings in one farm each hold SHARE and each wait
    # for the other to drop it — a lock-upgrade deadlock that rolls the entire
    # kidding back and surfaces as a 500. Acquire the exclusive lock once, up
    # front: it is the very lock the stillborn insert would take anyway, so
    # this adds no new contention class, it only removes the upgrade.
    if any(kid["status"] == KidStatus.STILLBORN.value for kid in kids):
        await db.execute(select(func.pg_advisory_xact_lock(literal(farm.id))))

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
    delivered_count = len(kids)
    if delivered_count >= 5:
        birth_type: BirthType | None = BirthType.MULTIPLET
    else:
        birth_type = {
            1: BirthType.SINGLE,
            2: BirthType.TWIN,
            3: BirthType.TRIPLET,
            4: BirthType.QUADRUPLET,
        }.get(delivered_count)

    # Tags are unique per farm. Preserve the readable "<doe>-K<n>" when free,
    # then use a cryptographically unpredictable bounded fallback. A sequential
    # suffix loop lets an attacker pre-fill 50,000 tags and force 50,000 round
    # trips while the pregnancy/doe locks are held.
    assigned_tags: set[str] = set()

    def _fit_tag(base: str, suffix: str = "") -> str:
        """Fit a generated base and any collision suffix in VARCHAR(50)."""
        return f"{base[: MAX_ANIMAL_TAG_LENGTH - len(suffix)]}{suffix}"

    async def exists(candidate: str) -> bool:
        if await _tag_exists(db, farm.id, candidate):
            return True
        return (
            await db.execute(
                select(KidEntry.id).where(
                    KidEntry.farm_id == farm.id,
                    KidEntry.tag == candidate,
                )
            )
        ).scalar_one_or_none() is not None

    async def _unique_tag(base: str) -> str:
        readable = _fit_tag(base)
        if readable not in assigned_tags and not await exists(readable):
            assigned_tags.add(readable)
            return readable
        for _attempt in range(4):
            suffix = f"-A{secrets.token_hex(6)}"
            candidate = _fit_tag(base, suffix)
            if candidate in assigned_tags:
                continue
            if not await exists(candidate):
                assigned_tags.add(candidate)
                return candidate
        raise ValueError("Could not allocate a unique kid tag; retry the kidding request")

    for kid in kids:
        if kid["tag_is_explicit"]:
            tag = kid["tag"]
            if len(tag) > MAX_ANIMAL_TAG_LENGTH:
                raise ValueError(f"Kid tags cannot exceed {MAX_ANIMAL_TAG_LENGTH} characters")
            if tag in assigned_tags or await exists(tag):
                raise ValueError("A kid tag already exists in this farm")
            assigned_tags.add(tag)
        else:
            tag = await _unique_tag(kid["tag"])

        entry = KidEntry(
            farm_id=farm.id,
            kidding_record_id=record.id,
            tag=tag or None,
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
            # Kids stay with the doe in RECOVERY until weaning.
            born_bucket = Bucket.RECOVERY.value
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
                current_bucket=born_bucket,
                status=(
                    AnimalStatus.ACTIVE.value
                    if kid["status"] == KidStatus.ALIVE.value
                    else AnimalStatus.DEAD.value
                ),
                status_date=(
                    kid["mortality_reported_at"] if kid["status"] == KidStatus.DIED.value else None
                ),
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
                    to_bucket=born_bucket,
                    effective_date=kidding_date,
                    reason="Born",
                    created_by_id=created_by_id,
                )
            )
            entry.animal_id = animal.id

    # Doe goes to RECOVERY (fresh pen) whether she was in DELIVERY or still
    # in PREGNANCY_LATE.
    move_animal(
        db,
        doe,
        Bucket.RECOVERY.value,
        "Kidded",
        created_by_id=created_by_id,
        context="kidding",
        reference_date=kidding_date,
        # Delivery is an authoritative lifecycle fact. A hold remains active,
        # but it must not leave the doe classified as pregnant after delivery.
        allow_restricted_reclassification=True,
    )

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
            _clear_task_rejection(task)
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
            _clear_task_rejection(task)

    if alive_count:
        await _add_task(
            db,
            farm.id,
            f"Wean kids of {doe.tag_number}; doe → RESTING",
            kidding_date + timedelta(days=profile.weaning_days),
            TaskCategory.WEANING,
            animal_id=doe.id,
            breeding_record_id=br.id,
        )
    else:
        mortality_dates = [
            kid["mortality_reported_at"]
            for kid in kids
            if kid["status"] == KidStatus.DIED.value and kid["mortality_reported_at"] is not None
        ]
        recovery_anchor = max([kidding_date, *mortality_dates])
        await _add_task(
            db,
            farm.id,
            f"Move {doe.tag_number} to RESTING after postpartum recovery",
            recovery_anchor + timedelta(days=profile.postpartum_recovery_days),
            TaskCategory.BUCKET_MOVE,
            animal_id=doe.id,
            breeding_record_id=br.id,
        )
    await db.flush()
    return record


async def replan_dam_after_last_kid_death(
    db: AsyncSession,
    farm: Farm,
    child: Animal,
    death_date: date,
) -> bool:
    profile = GOAT_PROFILE
    """Replace a stale weaning plan when a kidding's final survivor dies.

    The animal status row and its KidEntry are two views of the same neonatal
    outcome. Keep them aligned, then schedule the same 14-day no-survivor
    recovery path used when a kidding starts with no live kids. Returns true
    only when the dam was actually replanned.
    """
    # KidStatus.DIED records a *neonatal* mortality after a live birth, yet
    # every death routes through here (change_status calls it for any DEAD
    # transition) and KidEntry.animal_id is set once at birth and never
    # cleared. Realign the birth entry only while the child is still a
    # dependent young — one that has lived in its birth cohort since
    # delivery. A weaned juvenile or adult born on this farm keeps its ALIVE
    # birth outcome: that row is an immutable delivery fact, and flipping it
    # on a later death silently corrupts twin-rate / kids-per-kidding stats.
    # The bucket alone is not enough — a farm-born doe who later kids returns
    # to RECOVERY as a dam while her own birth entry still exists — so also
    # require that the child never left its birth cohort. A from_bucket row
    # by itself is not proof of that: history_override moves bypass
    # LEGAL_BUCKET_TRANSITIONS entirely (bucket_transition_error returns None
    # for that context), so an owner correcting a data-entry mistake can
    # round-trip the birth bucket without the kid ever weaning. So the test
    # is "left the birth cohort for any reason that is not a history
    # override". Kids are born into RECOVERY (with the dam) and only the
    # weaning duty (or a manual move) ever takes them out.
    if child.current_bucket != Bucket.RECOVERY.value:
        return False
    birth_cohorts = (Bucket.RECOVERY.value,)
    weaned_out = (
        await db.execute(
            select(BucketMove.id)
            .where(
                BucketMove.animal_id == child.id,
                BucketMove.from_bucket.in_(birth_cohorts),
                func.coalesce(BucketMove.reason, "").not_like(f"{HISTORY_OVERRIDE_REASON_PREFIX}%"),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if weaned_out is not None:
        return False
    entry = (
        await db.execute(
            select(KidEntry)
            .where(KidEntry.farm_id == farm.id, KidEntry.animal_id == child.id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if entry is None:
        return False
    kidding = (
        await db.execute(
            select(KiddingRecord)
            .where(
                KiddingRecord.id == entry.kidding_record_id,
                KiddingRecord.farm_id == farm.id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if kidding is None:
        return False

    entry.status = KidStatus.DIED.value
    entry.mortality_reported_at = death_date

    # Every sibling-death transaction takes the shared kidding mutex, then
    # the dam, before asking whether a survivor remains. Two simultaneous
    # final deaths therefore cannot each observe the other kid as ACTIVE and
    # both leave the obsolete weaning plan behind.
    dam = (
        await db.execute(
            select(Animal)
            .where(Animal.id == kidding.doe_id, Animal.farm_id == farm.id)
            # A dam-retirement request already owns this row and later locks
            # her RECOVERY kids. This child-death request owns one such kid,
            # so waiting here would invert the lock order and deadlock. Fail
            # fast; the API maps PostgreSQL's lock-not-available to a retryable
            # 409 and rolls this death transaction back intact.
            .with_for_update(nowait=True)
        )
    ).scalar_one_or_none()

    surviving_id = (
        await db.execute(
            select(Animal.id)
            .join(KidEntry, KidEntry.animal_id == Animal.id)
            .where(
                KidEntry.farm_id == farm.id,
                KidEntry.kidding_record_id == kidding.id,
                Animal.farm_id == farm.id,
                Animal.id != child.id,
                Animal.status == AnimalStatus.ACTIVE.value,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if surviving_id is not None:
        return False

    now = utcnow()

    if (
        dam is None
        or dam.status != AnimalStatus.ACTIVE.value
        or dam.current_bucket != Bucket.RECOVERY.value
    ):
        return False

    for task in await _pending_tasks_for(
        db,
        farm.id,
        for_update=True,
        animal_id=dam.id,
        category=TaskCategory.WEANING.value,
    ):
        # A doe can have retained historical duties from more than one
        # kidding. Close only this litter's duty: cancelling every duty for the
        # dam can erase a newer live litter's day-60 plan. New rows carry the
        # breeding link; the due-date fallback repairs pre-link legacy rows.
        if task.breeding_record_id not in (None, kidding.breeding_record_id):
            continue
        if task.breeding_record_id is None and task.due_date != kidding.date + timedelta(
            days=profile.weaning_days
        ):
            continue
        task.status = TaskStatus.SKIPPED.value
        task.skipped_by_id = None
        task.skipped_at = now
        task.skip_reason = "Final surviving kid died; weaning no longer applies"
        _clear_task_rejection(task)

    # Historical correction can put a doe through a second kidding while an
    # older litter still has a retained RECOVERY kid. If that older litter then
    # loses its final survivor, the newer dependent litter must keep the dam in
    # RECOVERY and retain its own weaning plan. A genuine prior RECOVERY exit
    # proves that an adult child merely returned to this bucket later and is no
    # longer dependent; history overrides deliberately do not prove weaning.
    other_dependent_id = (
        await db.execute(
            select(Animal.id)
            .where(
                Animal.farm_id == farm.id,
                Animal.dam_id == dam.id,
                Animal.id != child.id,
                Animal.status == AnimalStatus.ACTIVE.value,
                Animal.current_bucket == Bucket.RECOVERY.value,
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
            .limit(1)
        )
    ).scalar_one_or_none()
    if other_dependent_id is not None:
        await db.flush()
        return False

    recorded_mortalities = [
        value
        for value in (
            (
                await db.execute(
                    select(KidEntry.mortality_reported_at).where(
                        KidEntry.farm_id == farm.id,
                        KidEntry.kidding_record_id == kidding.id,
                        KidEntry.mortality_reported_at.is_not(None),
                    )
                )
            ).scalars()
        )
        if value is not None
    ]
    recovery_anchor: date = max([kidding.date, death_date, *recorded_mortalities])
    due: date = recovery_anchor + timedelta(days=profile.postpartum_recovery_days)
    existing = (
        await db.execute(
            select(Task)
            .where(
                Task.farm_id == farm.id,
                Task.animal_id == dam.id,
                Task.breeding_record_id == kidding.breeding_record_id,
                Task.category == TaskCategory.BUCKET_MOVE.value,
                Task.status == TaskStatus.PENDING.value,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if existing is None:
        await _add_task(
            db,
            farm.id,
            f"Move {dam.tag_number} to RESTING after postpartum recovery",
            due,
            TaskCategory.BUCKET_MOVE,
            animal_id=dam.id,
            breeding_record_id=kidding.breeding_record_id,
        )
    else:
        existing.due_date = due
    await db.flush()
    return True
