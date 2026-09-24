"""Pydantic schemas for the tasks/duties module."""

from datetime import date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..models import MAX_RECUR_DAYS, MAX_TASK_TITLE_LENGTH  # single source of truth
from .common import BoundedId, PostgresText, StrictInputModel, StrictInt

TaskStatusStr = Literal["PENDING", "DONE", "SKIPPED", "VERIFIED"]
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
    "KIDDING_WATCH",
    "BIRTHING_KIT",
    "HEALTH_CHECK",
    "HEAT_WATCH",
    "HOOF_TRIMMING",
    "SPRAYING",
    "DISINFECTION",
    "WEIGHING",
    "REBREED",
    "BUCK_ROTATION",
    "INSURANCE",
    "WATER",
    "FAMACHA",
]

# Manual duties are intentionally operational checklists. Workflow categories
# are created only by their authoritative domain services; accepting them here
# would let a tasks-only role manufacture a row whose completion can move an
# animal or masquerade as a clinical/reproductive protocol step.
ManualTaskCategoryStr = Literal["FEED", "CLEANING", "OTHER"]


class TaskCreateIn(StrictInputModel):
    title: PostgresText = Field(min_length=1, max_length=MAX_TASK_TITLE_LENGTH)
    due_date: date
    category: ManualTaskCategoryStr = "OTHER"
    animal_id: BoundedId | None = None
    assigned_role_id: BoundedId | None = None
    assigned_user_id: BoundedId | None = None
    recur_days: StrictInt | None = Field(default=None, ge=1, le=MAX_RECUR_DAYS)

    @field_validator("due_date")
    @classmethod
    def _sane_year_band(cls, value: date) -> date:
        # A year-1 due date is permanently "overdue" and poisons every
        # dashboard aggregate that counts it; year 2000 mirrors the
        # purchase-date floor (any real duty predating the app is fiction).
        if not 2000 <= value.year <= 2100:
            raise ValueError("due_date must be between year 2000 and 2100")
        return value

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
    # Localization contract for generated duties: render the key+args in the
    # worker's language; ``title`` remains the English fallback.
    title_key: str | None
    title_args: dict[str, Any]
    due_date: date
    status: TaskStatusStr
    category: TaskCategoryStr
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
    created_at: datetime
    created_by_id: int | None
    # enriched for display
    assigned_role_name: str | None = None
    assigned_user_name: str | None = None
    animal_tag: str | None = None
    needs_verification: bool = False
    action_url: str | None = None  # frontend path of the linked form, if any


class TaskRejectIn(StrictInputModel):
    """Rejection returns a duty to its worker — the note is the only
    explanation they ever see, so it is required (non-blank, <= 255 chars)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    note: PostgresText = Field(min_length=1, max_length=255)


class TaskCompleteIn(StrictInputModel):
    """Degenerate body for the bodyless completion (the idempotency claim's
    request fingerprint: the identity is (actor, farm, task, key))."""

    pass


class TaskSkipIn(StrictInputModel):
    """A skip is an auditable exception to scheduled work; the reason is the
    audit trail, so it is required (non-blank, <= 255 chars)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    reason: PostgresText = Field(min_length=1, max_length=255)


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
