"""Computed domain logic (shared by routes, tests, and later phases)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta
from typing import TYPE_CHECKING, TypedDict

from .constants import MAX_TASK_TITLE_LENGTH
from .enums import BreedingOutcome, TaskCategory
from .species import GOAT_PROFILE

if TYPE_CHECKING:
    from .breeding import BreedingRecord
    from .purchases import PurchaseBatch


def expected_kidding_date(breeding_date: date) -> date:
    """Expected kidding date."""
    return breeding_date + timedelta(days=GOAT_PROFILE.gestation_days)


def planned_ultrasound_date(breeding_date: date) -> date:
    """Planned pregnancy-check date after a service (ultrasound / PD)."""
    return breeding_date + timedelta(days=GOAT_PROFILE.pregnancy_check_after_service_days)


# A service counts as a conception once an ultrasound confirmed it, even if
# the pregnancy was later lost: ``mark_aborted`` returns early unless the
# record is already CONFIRMED_PREGNANT, so every ABORTED row *did* conceive.
# Pregnancy loss stays a separate fact (loss_date/loss_cause). Counting an
# abortion as a failure to conceive let an ordinary sale of a pregnant doe —
# which auto-aborts her pregnancy — retroactively rewrite this KPI.
CONCEIVED_OUTCOMES = frozenset(
    {BreedingOutcome.CONFIRMED_PREGNANT.value, BreedingOutcome.ABORTED.value}
)
ASSESSED_OUTCOMES = frozenset(
    {
        BreedingOutcome.CONFIRMED_PREGNANT.value,
        BreedingOutcome.ABORTED.value,
        BreedingOutcome.FAILED.value,
    }
)


def conception_rate(records: Iterable[BreedingRecord]) -> float | None:
    """Conceived / assessed breedings. Percent or ``None`` when none were assessed.

    ``GET /api/dashboard/reports`` computes the same metric in SQL from
    ``CONCEIVED_OUTCOMES``; the two implementations must never drift.
    """
    completed = [r for r in records if r.outcome in ASSESSED_OUTCOMES]
    if not completed:
        return None
    conceived = sum(1 for r in completed if r.outcome in CONCEIVED_OUTCOMES)
    return round(100.0 * conceived / len(completed), 1)


# 45-day quarantine protocols (day offsets relative to batch arrival date).
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
    """One auto-generated quarantine task."""

    due_date: date
    category: str
    title: str


def quarantine_schedule(batch: PurchaseBatch) -> list[QuarantineTaskSpec]:
    """Due-dated quarantine task definitions for a purchase batch."""
    schedule: list[QuarantineTaskSpec] = []
    supplier = (batch.supplier or "Purchase").strip() or "Purchase"
    protocol = QUARANTINE_PROTOCOL
    for day_offset, category, protocol_title in protocol:
        # Supplier accepts 120 characters while Task.title is 200. Preserve
        # the operational protocol and stable batch id in full, truncating
        # only the display label so purchase creation cannot overflow midway.
        suffix = f" #{batch.id}] {protocol_title}"
        supplier_budget = MAX_TASK_TITLE_LENGTH - len("[") - len(suffix)
        safe_supplier = supplier[: max(0, supplier_budget)].rstrip()
        schedule.append(
            {
                "due_date": batch.date + timedelta(days=day_offset - 1),
                "category": category.value,
                "title": f"[{safe_supplier}{suffix}",
            }
        )
    return schedule
