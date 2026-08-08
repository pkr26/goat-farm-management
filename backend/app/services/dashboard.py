"""Dashboard helpers."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import Animal, AnimalStatus, BreedingOutcome, BreedingRecord, Bucket, Farm
from ..utils import today


async def ready_to_move_suggestions(
    db: AsyncSession, farm: Farm, animals: list[Animal] | None = None
) -> list[dict[str, Any]]:
    """Bucket-move suggestions per SPEC thresholds.

    `animals` may supply the caller's already-loaded ACTIVE herd (the
    dashboard counts the same rows anyway — one herd scan per request instead
    of two). The relationships the thresholds read must be eager-loaded on
    those rows (ANIMAL_OUT_LOADS — async sessions forbid implicit lazy loads);
    the fallback query keeps the explicit selectinloads for clarity."""
    if animals is None:
        result = await db.execute(
            select(Animal)
            .options(
                # is_breeding_ready / days_in_current_bucket / _gestation_days read
                # these relationships.
                selectinload(Animal.weight_records),
                selectinload(Animal.bucket_moves),
                selectinload(Animal.breedings_as_doe).selectinload(BreedingRecord.kidding_record),
            )
            .where(Animal.farm_id == farm.id, Animal.status == AnimalStatus.ACTIVE.value)
        )
        animals = list(result.scalars().all())
    suggestions: list[dict[str, Any]] = []
    reference_date = today(farm.timezone)

    def _gestation_days(animal: Animal) -> int | None:
        confirmed = [
            r
            for r in animal.breedings_as_doe
            if r.outcome == BreedingOutcome.CONFIRMED_PREGNANT.value and not r.kidding_record
        ]
        if not confirmed:
            return None
        latest = max(confirmed, key=lambda r: (r.breeding_date, r.id or 0))
        return (reference_date - latest.breeding_date).days

    for animal in animals:
        bucket = animal.current_bucket
        if (
            animal.sex == "F"
            and bucket in (Bucket.FOUNDATION.value, Bucket.FEMALE_KIDS.value)
            and animal.is_breeding_ready_on(reference_date)
        ):
            suggestions.append(
                {
                    "animal": animal,
                    "to": Bucket.BREEDING.value,
                    "reason": "Breeding-ready (≥10 mo, ≥22 kg)",
                }
            )
        elif (
            animal.sex == "F"
            and bucket == Bucket.RESTING.value
            and animal.days_in_current_bucket_on(reference_date, farm.timezone) >= 30
        ):
            bucket_days = animal.days_in_current_bucket_on(reference_date, farm.timezone)
            suggestions.append(
                {
                    "animal": animal,
                    "to": Bucket.BREEDING.value,
                    "reason": f"{bucket_days} days resting (flush done)",
                }
            )
        elif bucket == Bucket.PREGNANCY_EARLY.value:
            day = _gestation_days(animal)
            if day is not None and day >= 100:
                suggestions.append(
                    {
                        "animal": animal,
                        "to": Bucket.PREGNANCY_LATE.value,
                        "reason": f"Gestation day {day} (≥100)",
                    }
                )
        elif bucket == Bucket.PREGNANCY_LATE.value:
            day = _gestation_days(animal)
            if day is not None and day >= 135:
                suggestions.append(
                    {
                        "animal": animal,
                        "to": Bucket.DELIVERY.value,
                        "reason": f"Gestation day {day} (≥135, due soon)",
                    }
                )
        elif animal.sex == "M" and bucket == Bucket.MALE_KIDS.value:
            age, weight = animal.age_months_on(reference_date), animal.latest_weight_kg
            if age is not None and age >= 8 and weight is not None and weight >= 24:
                suggestions.append(
                    {
                        "animal": animal,
                        "to": "SELL",
                        "reason": f"{age} mo, {weight:.1f} kg — market ready",
                    }
                )
    return suggestions
