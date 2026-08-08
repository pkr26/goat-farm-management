"""Computed domain logic (shared by routes, tests, and later phases)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta
from typing import TYPE_CHECKING, TypedDict

from .constants import GESTATION_DAYS, ULTRASOUND_AFTER_BREEDING_DAYS
from .enums import BreedingOutcome, TaskCategory

if TYPE_CHECKING:
    from .breeding import BreedingRecord
    from .purchases import PurchaseBatch


def expected_kidding_date(breeding_date: date) -> date:
    return breeding_date + timedelta(days=GESTATION_DAYS)


def planned_ultrasound_date(breeding_date: date) -> date:
    return breeding_date + timedelta(days=ULTRASOUND_AFTER_BREEDING_DAYS)


def conception_rate(records: Iterable[BreedingRecord]) -> float | None:
    """Confirmed / total completed breedings (PENDING excluded). Percent or None."""
    completed = [r for r in records if r.outcome != BreedingOutcome.PENDING.value]
    if not completed:
        return None
    confirmed = sum(1 for r in completed if r.outcome == BreedingOutcome.CONFIRMED_PREGNANT.value)
    return round(100.0 * confirmed / len(completed), 1)


# 45-day quarantine protocol (day offsets relative to batch arrival date).
QUARANTINE_PROTOCOL = [
    (
        1,
        TaskCategory.QUARANTINE,
        "Days 1–3: rest, electrolyte/jaggery water, dry roughage only, zero grain",
    ),
    (4, TaskCategory.DEWORMING, "Day 4: deworm — Albendazole/Closantel oral + Ivermectin SC"),
    (5, TaskCategory.QUARANTINE, "Days 5–9: liver tonic in water + Vitamin AD3E injection"),
    (10, TaskCategory.VACCINE, "Day 10: vaccinate PPR (live viral, SC)"),
    (20, TaskCategory.VACCINE, "Day 20: vaccinate ET + Tetanus (toxoid, SC)"),
    (30, TaskCategory.VACCINE, "Day 30: vaccinate Goat Pox (live viral, SC)"),
    (40, TaskCategory.VACCINE, "Day 40: vaccinate FMD (killed, SC)"),
    (45, TaskCategory.BUCKET_MOVE, "Day 45: 10% zinc sulfate footbath → release to FOUNDATION"),
]


class QuarantineTaskSpec(TypedDict):
    """One auto-generated quarantine task (AUDIT 4-L2: replaces dict[str, object])."""

    due_date: date
    category: str
    title: str


def quarantine_schedule(batch: PurchaseBatch) -> list[QuarantineTaskSpec]:
    """Due-dated quarantine task definitions for a purchase batch."""
    return [
        {
            "due_date": batch.date + timedelta(days=day_offset - 1),
            "category": category.value,
            "title": f"[{batch.supplier or 'Purchase'} #{batch.id}] {title}",
        }
        for day_offset, category, title in QUARANTINE_PROTOCOL
    ]
