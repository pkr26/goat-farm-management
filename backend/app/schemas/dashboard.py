"""Pydantic schemas for dashboard and reports."""

from pydantic import BaseModel

from .animals import AnimalOut, WeightRecordOut
from .breeding import BreedingRecordOut
from .tasks import TaskOut


class MoveSuggestionOut(BaseModel):
    animal: AnimalOut
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
    overdue_tasks: list[TaskOut]
    ultrasounds_due: list[TaskOut]
    kiddings_due: list[BreedingRecordOut]
    cull_candidates: list[AnimalOut]
    suggestions: list[MoveSuggestionOut]
    recent_weights: list[WeightRecordOut]


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
    cull_candidates: list[AnimalOut]


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
