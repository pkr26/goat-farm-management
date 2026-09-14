"""Pydantic schemas for dashboard and reports."""

import datetime as dt

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
    # All operational lists above except recent_weights use this cap.
    preview_limit: int
    recent_weights_limit: int


class BucketReportRow(BaseModel):
    name: str
    code: str
    count: int
    avg_weight: float | None


class BreedingStatsOut(BaseModel):
    total_records: int
    conception_rate: float | None
    first_cycle_rate: float | None
    kiddings: int
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
    total_kids_born: int
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
