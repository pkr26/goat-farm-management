"""Farm-local and animal-lifecycle chronology policies."""

from datetime import date

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    Animal,
    BreedingRecord,
    BucketMove,
    Farm,
    HealthEvent,
    KiddingRecord,
    WeightRecord,
)
from ..utils import today


def require_farm_not_future(value: date, farm: Farm, field_name: str) -> None:
    """Reject a calendar event after this farm's current business date."""
    if value > today(farm.timezone):
        raise ValueError(f"{field_name} cannot be in the future for this farm")


def require_animal_event_chronology(animal: Animal, value: date, event_name: str) -> None:
    """An operational event cannot predate birth or farm acquisition."""
    if animal.effective_dob is not None and value < animal.effective_dob:
        raise ValueError(f"{event_name} cannot predate {animal.tag_number}'s recorded birth date")
    if animal.purchase_date is not None and value < animal.purchase_date:
        raise ValueError(
            f"{event_name} cannot predate {animal.tag_number}'s recorded purchase date"
        )


async def require_status_after_recorded_facts(
    db: AsyncSession, animal: Animal, status_date: date
) -> None:
    """Reject a terminal status dated before a later factual animal event.

    The caller holds the animal row lock. Weight, health, breeding, loss and
    kidding writers take that same lock before adding facts, so the maxima
    cannot race between this check and the status commit. Planned dates (task
    due dates, expected kidding, withdrawal end) are intentionally excluded.

    A sire's actual service date is a fact about him; later ultrasound/loss or
    kidding belongs to the doe's physiology and may legitimately occur after
    a sire was sold or died, so those later cycle boundaries constrain only
    the doe.
    """
    breeding_facts = (
        select(
            func.max(BreedingRecord.breeding_date).label("latest_breeding_date"),
            func.max(BreedingRecord.ultrasound_result_date)
            .filter(BreedingRecord.doe_id == animal.id)
            .label("latest_ultrasound_result_date"),
            func.max(BreedingRecord.loss_date)
            .filter(BreedingRecord.doe_id == animal.id)
            .label("latest_loss_date"),
        )
        .where(
            BreedingRecord.farm_id == animal.farm_id,
            or_(BreedingRecord.doe_id == animal.id, BreedingRecord.buck_id == animal.id),
        )
        .subquery()
    )
    latest_weight = (
        select(func.max(WeightRecord.date))
        .where(WeightRecord.animal_id == animal.id)
        .scalar_subquery()
    )
    latest_health = (
        select(func.max(HealthEvent.date))
        .where(
            HealthEvent.farm_id == animal.farm_id,
            HealthEvent.animal_id == animal.id,
        )
        .scalar_subquery()
    )
    latest_kidding = (
        select(func.max(KiddingRecord.date))
        .where(
            KiddingRecord.farm_id == animal.farm_id,
            KiddingRecord.doe_id == animal.id,
        )
        .scalar_subquery()
    )
    latest_move = (
        select(func.max(BucketMove.effective_date))
        # The initial placement records when the row entered this farm's
        # ledger; for historical imports it is not proof the animal was alive
        # on that entry date. Later bucket transitions are real lifecycle
        # facts and therefore do constrain a terminal status date.
        .where(
            BucketMove.animal_id == animal.id,
            BucketMove.from_bucket.is_not(None),
        )
        .scalar_subquery()
    )
    row = (
        await db.execute(
            select(
                latest_weight.label("latest_weight_date"),
                latest_health.label("latest_health_date"),
                breeding_facts.c.latest_breeding_date,
                breeding_facts.c.latest_ultrasound_result_date,
                breeding_facts.c.latest_loss_date,
                latest_kidding.label("latest_kidding_date"),
                latest_move.label("latest_bucket_move_date"),
            )
        )
    ).one()
    facts = [value for value in row if value is not None]
    latest_fact = max(facts) if facts else None
    if latest_fact is not None and status_date < latest_fact:
        raise ValueError(
            f"Status change cannot predate {animal.tag_number}'s latest recorded "
            f"lifecycle event on {latest_fact.isoformat()}"
        )


async def require_purchase_before_recorded_facts(
    db: AsyncSession, animal: Animal, purchase_date: date
) -> None:
    """Reject an acquisition date moved after an already-recorded animal fact.

    Writers of these facts lock the animal first. Finance correction holds the
    same row lock, so the earliest-fact boundary cannot change between this
    check and synchronising the animal and its initial bucket placement.
    """
    breeding_facts = (
        select(
            func.min(BreedingRecord.breeding_date).label("first_breeding_date"),
            func.min(BreedingRecord.ultrasound_result_date)
            .filter(BreedingRecord.doe_id == animal.id)
            .label("first_ultrasound_result_date"),
            func.min(BreedingRecord.loss_date)
            .filter(BreedingRecord.doe_id == animal.id)
            .label("first_loss_date"),
        )
        .where(
            BreedingRecord.farm_id == animal.farm_id,
            or_(BreedingRecord.doe_id == animal.id, BreedingRecord.buck_id == animal.id),
        )
        .subquery()
    )
    first_weight = (
        select(func.min(WeightRecord.date))
        .where(WeightRecord.animal_id == animal.id)
        .scalar_subquery()
    )
    first_health = (
        select(func.min(HealthEvent.date))
        .where(
            HealthEvent.farm_id == animal.farm_id,
            HealthEvent.animal_id == animal.id,
        )
        .scalar_subquery()
    )
    first_kidding = (
        select(func.min(KiddingRecord.date))
        .where(
            KiddingRecord.farm_id == animal.farm_id,
            KiddingRecord.doe_id == animal.id,
        )
        .scalar_subquery()
    )
    first_subsequent_move = (
        select(func.min(BucketMove.effective_date))
        .where(BucketMove.animal_id == animal.id, BucketMove.from_bucket.is_not(None))
        .scalar_subquery()
    )
    row = (
        await db.execute(
            select(
                first_weight.label("first_weight_date"),
                first_health.label("first_health_date"),
                breeding_facts.c.first_breeding_date,
                breeding_facts.c.first_ultrasound_result_date,
                breeding_facts.c.first_loss_date,
                first_kidding.label("first_kidding_date"),
                first_subsequent_move.label("first_subsequent_move_date"),
            )
        )
    ).one()
    facts = [value for value in row if value is not None]
    earliest_fact = min(facts) if facts else None
    if earliest_fact is not None and purchase_date > earliest_fact:
        raise ValueError(
            f"Purchase date cannot follow {animal.tag_number}'s earliest recorded "
            f"lifecycle event on {earliest_fact.isoformat()}"
        )
