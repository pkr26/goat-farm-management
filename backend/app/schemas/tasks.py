"""Pydantic schemas for the tasks/duties module."""

from datetime import date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..models import MAX_RECUR_DAYS, MAX_TASK_TITLE_LENGTH  # single source of truth
from .common import BoundedId, StrictInputModel, StrictInt

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

# Manual duties are intentionally operational checklists. Workflow categories
# are created only by their authoritative domain services; accepting them here
# would let a tasks-only role manufacture a row whose completion can move an
# animal or masquerade as a clinical/reproductive protocol step.
ManualTaskCategoryStr = Literal["FEED", "CLEANING", "OTHER"]


class TaskCreateIn(StrictInputModel):
    title: str = Field(min_length=1, max_length=MAX_TASK_TITLE_LENGTH)
    due_date: date
    category: ManualTaskCategoryStr = "OTHER"
    animal_id: BoundedId | None = None
    assigned_role_id: BoundedId | None = None
    assigned_user_id: BoundedId | None = None
    recur_days: StrictInt | None = Field(default=None, ge=1, le=MAX_RECUR_DAYS)

    @model_validator(mode="after")
    def recurrence_must_have_representable_successor(self) -> "TaskCreateIn":
        if self.recur_days is not None and self.due_date > date.max - timedelta(
            days=self.recur_days
        ):
            raise ValueError("Recurring due date is too late to schedule its next occurrence")
        return self


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
    rejected_by_id: int | None
    rejected_at: datetime | None
    # enriched for display
    assigned_role_name: str | None = None
    assigned_user_name: str | None = None
    animal_tag: str | None = None
    needs_verification: bool = False
    action_url: str | None = None  # frontend path of the linked form, if any


class TaskRejectIn(StrictInputModel):
    note: str | None = Field(default=None, max_length=255)


class TaskSkipIn(StrictInputModel):
    reason: str | None = Field(default=None, max_length=255)


class TaskTabsOut(BaseModel):
    today: list[TaskOut]
    overdue: list[TaskOut]
    upcoming: list[TaskOut]
    awaiting: list[TaskOut]  # DONE, needing verification (tasks.verify holders)
    completed: list[TaskOut]  # VERIFIED/DONE/SKIPPED history
    today_total: int
    today_offset: int
    overdue_total: int
    overdue_offset: int
    upcoming_total: int
    upcoming_offset: int
    awaiting_total: int
    awaiting_offset: int
    active_limit: int
    completed_total: int
    completed_limit: int
    completed_offset: int
