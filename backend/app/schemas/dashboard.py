"""Pydantic schemas for dashboard and reports."""

import datetime as dt
from typing import Any

from pydantic import BaseModel

from .summaries import AnimalIdentityOut, DashboardKiddingDueOut, DashboardWeightOut
from .tasks import TaskOut


class MoveSuggestionOut(BaseModel):
    animal: AnimalIdentityOut
    to: str
    reason: str


class RestrictedAnimalOut(BaseModel):
    """One animal currently frozen by a movement restriction / disease hold.

    A hold silently blocks move, breeding, sale and cull until a referenced
    clearance — this list is the farm-wide surface that makes it impossible
    to forget. The clinical narrative (`reason`) is only populated for a
    caller with health.view; the operational fact of the hold is visible to
    anyone who can already see the animal.
    """

    animal: AnimalIdentityOut
    current_bucket: str
    # When the current hold was placed (latest PLACED action).
    held_since: dt.datetime | None
    reason: str | None = None


class BucketCountOut(BaseModel):
    code: str
    name: str
    count: int


class InsuranceExpiringOut(BaseModel):
    """One active policy whose renewal falls inside the dashboard window.

    Insurance is money at risk, so the block sits behind finance.view — the
    same permission that guards the finance register it summarizes. The
    animal tag is present only for per-animal policies."""

    id: int
    policy_number: str
    insurer: str
    renewal_date: dt.date
    animal_id: int | None = None
    animal_tag: str | None = None


class DashboardAdvisoryOut(BaseModel):
    """A structured ops advisory: a stable key plus its arguments, so the
    client renders it localized (same contract as task title keys)."""

    key: str
    args: dict[str, Any]


class DashboardOut(BaseModel):
    # Herd counts by bucket (the pregnancy/breeding-programme buckets among
    # them), the active total and the sex split restate animal-register facts
    # the animals pages hold behind animals.view. None means the caller lacks
    # it — the whole herd-summary block was withheld, not empty.
    buckets: list[BucketCountOut] | None
    total_active: int | None
    sex_counts: dict[str, int] | None
    status_totals: dict[str, int]
    todays_tasks: list[TaskOut]
    # None means the caller lacks tasks.view — the section was withheld, not
    # empty. A literal 0 must always mean "genuinely none today".
    todays_tasks_total: int | None
    overdue_tasks: list[TaskOut]
    overdue_tasks_total: int | None
    ultrasounds_due: list[TaskOut]
    ultrasounds_due_total: int | None
    kiddings_due: list[DashboardKiddingDueOut]
    # None means the caller lacks breeding.view — withheld, not empty.
    kiddings_due_total: int | None
    cull_candidates: list[AnimalIdentityOut]
    # None means the caller lacks breeding.view — the cull preview was
    # withheld, not empty. A literal 0 must always mean "genuinely none".
    cull_candidates_total: int | None
    suggestions: list[MoveSuggestionOut]
    # None means the caller lacks animals.view — withheld, not empty.
    suggestions_total: int | None
    # Animals currently under an active movement restriction / disease hold.
    # None means the caller lacks animals.view — the preview was withheld,
    # not empty. A literal 0 must always mean "genuinely none".
    restricted_animals: list[RestrictedAnimalOut]
    restricted_animals_total: int | None
    recent_weights: list[DashboardWeightOut]
    # None means the caller lacks animals.view — the weights preview was
    # withheld, not empty. A literal 0 must always mean "genuinely none".
    recent_weights_total: int | None
    # Active insurance policies renewing inside the expiry window. None means
    # the caller lacks finance.view — withheld, not empty (same convention as
    # the totals above; the finance register holds the full list).
    insurance_expiring: list[InsuranceExpiringOut]
    insurance_expiring_total: int | None
    # Optional, additive ops advisory (Bakrid hold window): males whose
    # projected market finish lands in the two months before the next Bakrid
    # are worth holding for the festival premium. None when no animal
    # qualifies, when the caller lacks animals.view, or when the calendar
    # has no next date.
    advisory: DashboardAdvisoryOut | None = None
    # All operational lists above except recent_weights use this cap.
    preview_limit: int
    recent_weights_limit: int


class BucketReportRow(BaseModel):
    name: str
    code: str
    count: int
    avg_weight: float | None


class BreedingStatsOut(BaseModel):
    # None means the caller lacks breeding.view — withheld, not zero. The
    # counts are breeding-derived aggregates like the rates beside them, so
    # they cannot stay ungated while every sibling field is withheld (B4,
    # 2026-09-21 audit).
    total_records: int | None
    conception_rate: float | None
    first_cycle_rate: float | None
    kiddings: int | None
    kids_per_kidding: float | None
    twin_rate: float | None
    cull_candidates: list[AnimalIdentityOut]
    # None means the caller lacks breeding.view — withheld, not empty.
    cull_candidates_total: int | None
    cull_candidates_limit: int


class MortalityOut(BaseModel):
    # None means the caller lacks health.view — withheld, not empty/zero.
    total_deaths: int | None
    deaths_by_month: list[tuple[str, int]]
    # Same withheld convention as stillborn beside it: kids born is a
    # clinical (kidding-outcome) aggregate, not a register count.
    total_kids_born: int | None
    stillborn: int | None
    stillborn_rate: float | None


class ReportsOut(BaseModel):
    # Per-bucket occupancy (incl. the pregnancy buckets) and the per-bucket
    # mean live weights restate bucket-board / animal-register facts —
    # avg_weight is an animals.view-derived aggregate. None means the caller
    # lacks animals.view — the whole herd-summary block was withheld, the
    # same convention the dashboard endpoint applies to these figures.
    bucket_rows: list[BucketReportRow] | None
    total_active: int | None
    sex_counts: dict[str, int] | None
    status_counts: dict[str, int]
    breeding: BreedingStatsOut
    mortality: MortalityOut
