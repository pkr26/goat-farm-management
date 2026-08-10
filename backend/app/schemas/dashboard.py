"""Pydantic schemas for dashboard and reports."""

from pydantic import BaseModel

from .summaries import AnimalIdentityOut, DashboardKiddingDueOut, DashboardWeightOut
from .tasks import TaskOut


class MoveSuggestionOut(BaseModel):
    animal: AnimalIdentityOut
    to: str
    reason: str


class BucketCountOut(BaseModel):
    code: str
    name: str
    count: int


class DashboardOut(BaseModel):
    buckets: list[BucketCountOut]
    total_active: int
    sex_counts: dict[str, int]
    status_totals: dict[str, int]
    todays_tasks: list[TaskOut]
    todays_tasks_total: int
    overdue_tasks: list[TaskOut]
    overdue_tasks_total: int
    ultrasounds_due: list[TaskOut]
    ultrasounds_due_total: int
    kiddings_due: list[DashboardKiddingDueOut]
    kiddings_due_total: int
    cull_candidates: list[AnimalIdentityOut]
    # None means the caller lacks breeding.view — the cull preview was
    # withheld, not empty. A literal 0 must always mean "genuinely none".
    cull_candidates_total: int | None
    suggestions: list[MoveSuggestionOut]
    suggestions_total: int
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
    total_deaths: int
    deaths_by_month: list[tuple[str, int]]
    total_kids_born: int
    stillborn: int
    stillborn_rate: float | None


class ReportsOut(BaseModel):
    bucket_rows: list[BucketReportRow]
    total_active: int
    sex_counts: dict[str, int]
    status_counts: dict[str, int]
    breeding: BreedingStatsOut
    mortality: MortalityOut
