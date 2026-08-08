"""Pydantic schemas for the tasks/duties module."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..models import MAX_RECUR_DAYS  # single source of truth
from .common import BoundedId

TaskCategoryStr = Literal[
    "VACCINE",
    "DEWORMING",
    "ULTRASOUND",
    "KIDDING_DUE",
    "WEANING",
    "BUCKET_MOVE",
    "QUARANTINE",
    "FEED",
    "CLEANING",
    "OTHER",
]


class TaskCreateIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    due_date: date
    category: TaskCategoryStr = "OTHER"
    animal_id: BoundedId | None = None
    assigned_role_id: BoundedId | None = None
    assigned_user_id: BoundedId | None = None
    recur_days: int | None = Field(default=None, ge=1, le=MAX_RECUR_DAYS)


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    due_date: date
    status: str  # PENDING | DONE | SKIPPED | VERIFIED
    category: str
    auto_generated: bool
    animal_id: int | None
    purchase_batch_id: int | None
    breeding_record_id: int | None
    assigned_role_id: int | None
    assigned_user_id: int | None
    recur_days: int | None
    recurring_series_id: str | None
    completed_by_id: int | None
    completed_at: datetime | None
    verified_by_id: int | None
    verified_at: datetime | None
    verification_note: str | None
    skipped_by_id: int | None
    skipped_at: datetime | None
    skip_reason: str | None
    # enriched for display
    assigned_role_name: str | None = None
    assigned_user_name: str | None = None
    animal_tag: str | None = None
    needs_verification: bool = False
    action_url: str | None = None  # frontend path of the linked form, if any


class TaskRejectIn(BaseModel):
    note: str | None = Field(default=None, max_length=255)


class TaskSkipIn(BaseModel):
    reason: str | None = Field(default=None, max_length=255)


class TaskTabsOut(BaseModel):
    today: list[TaskOut]
    overdue: list[TaskOut]
    upcoming: list[TaskOut]
    awaiting: list[TaskOut]  # DONE, needing verification (tasks.verify holders)
    completed: list[TaskOut]  # VERIFIED/DONE/SKIPPED history
    completed_total: int
    completed_limit: int
    completed_offset: int
