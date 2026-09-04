"""The legal bucket-transition graph (pure: no ORM imports).

Single source of truth for which lifecycle moves exist and which workflow
contexts may cause them. ``services.animals`` consumes it for its factual
guards; ``app.simulation.daily_ops`` consumes it to validate every simulated
move, so the operational state machine and the daily simulation can never
disagree about what a legal transition is.
"""

from __future__ import annotations

from typing import Literal

from .enums import Bucket

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
    # A batch animal is still released only by the guarded day-45 duty —
    # move_bucket refuses the manual form for anything carrying a
    # purchase_batch_id. "manual" exists for an animal entered straight into
    # QUARANTINE (a historical mid-quarantine import owns no batch, so no
    # protocol series and no release duty were ever generated for it) which
    # would otherwise be stuck in the bucket for good.
    (Bucket.QUARANTINE.value, Bucket.FOUNDATION.value): frozenset({"manual", "quarantine_release"}),
    (Bucket.FOUNDATION.value, Bucket.BREEDING.value): frozenset({"manual", "breeding"}),
    # "weaning" is the dairy milk-weaning graduation (day-90 heifers leave the
    # calf shed); goat weaning never targets FOUNDATION from FEMALE_KIDS.
    (Bucket.FEMALE_KIDS.value, Bucket.FOUNDATION.value): frozenset({"manual", "weaning"}),
    (Bucket.FEMALE_KIDS.value, Bucket.BREEDING.value): frozenset({"manual", "breeding"}),
    (Bucket.MALE_KIDS.value, Bucket.BREEDING.value): frozenset({"manual"}),
    (Bucket.RESTING.value, Bucket.BREEDING.value): frozenset({"manual", "breeding"}),
    (Bucket.BREEDING.value, Bucket.PREGNANCY_EARLY.value): frozenset({"ultrasound"}),
    (Bucket.PREGNANCY_EARLY.value, Bucket.PREGNANCY_LATE.value): frozenset({"manual"}),
    (Bucket.PREGNANCY_EARLY.value, Bucket.DELIVERY.value): frozenset({"delivery"}),
    (Bucket.PREGNANCY_LATE.value, Bucket.DELIVERY.value): frozenset({"manual", "delivery"}),
    (Bucket.PREGNANCY_EARLY.value, Bucket.RESTING.value): frozenset({"abortion"}),
    (Bucket.PREGNANCY_LATE.value, Bucket.RESTING.value): frozenset({"abortion"}),
    # A doe history-overridden from PREGNANCY_* back to BREEDING still carries
    # her confirmed pregnancy; recording its loss must remain possible.
    (Bucket.BREEDING.value, Bucket.RESTING.value): frozenset({"manual", "abortion"}),
    (Bucket.DELIVERY.value, Bucket.RESTING.value): frozenset({"abortion", "weaning"}),
    (Bucket.PREGNANCY_EARLY.value, Bucket.RECOVERY.value): frozenset({"kidding"}),
    (Bucket.PREGNANCY_LATE.value, Bucket.RECOVERY.value): frozenset({"kidding"}),
    (Bucket.DELIVERY.value, Bucket.RECOVERY.value): frozenset({"kidding"}),
    (Bucket.RECOVERY.value, Bucket.RESTING.value): frozenset({"postpartum", "weaning"}),
    (Bucket.RECOVERY.value, Bucket.MALE_KIDS.value): frozenset({"weaning", "orphan_weaning"}),
    (Bucket.RECOVERY.value, Bucket.FEMALE_KIDS.value): frozenset({"weaning", "orphan_weaning"}),
}
