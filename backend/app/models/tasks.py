"""Tasks / alerts."""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    ColumnElement,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    and_,
    or_,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from ..utils import utcnow
from .constants import MAX_TASK_TITLE_LENGTH, VERIFICATION_REQUIRED_CATEGORIES
from .enums import TaskCategory, TaskStatus, sql_in_values

if TYPE_CHECKING:
    from .animals import Animal
    from .breeding import BreedingRecord
    from .core import Role, User


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint(
            "farm_id",
            "recurring_series_id",
            "due_date",
            name="uq_task_recurring_series_due",
        ),
        ForeignKeyConstraint(
            ["farm_id", "animal_id"],
            ["animals.farm_id", "animals.id"],
            name="fk_tasks_farm_animal",
        ),
        ForeignKeyConstraint(
            ["farm_id", "purchase_batch_id"],
            ["purchase_batches.farm_id", "purchase_batches.id"],
            name="fk_tasks_farm_purchase_batch",
        ),
        ForeignKeyConstraint(
            ["farm_id", "breeding_record_id"],
            ["breeding_records.farm_id", "breeding_records.id"],
            name="fk_tasks_farm_breeding_record",
        ),
        ForeignKeyConstraint(
            ["farm_id", "assigned_role_id"],
            ["roles.farm_id", "roles.id"],
            name="fk_tasks_farm_assigned_role",
        ),
        ForeignKeyConstraint(
            ["farm_id", "assigned_user_id"],
            ["farm_memberships.farm_id", "farm_memberships.user_id"],
            name="fk_tasks_farm_assigned_membership",
        ),
        Index("ix_tasks_farm_status_due", "farm_id", "status", "due_date"),
        CheckConstraint(
            f"status IN ({sql_in_values(TaskStatus)})",
            name="ck_tasks_status",
        ),
        CheckConstraint(
            f"category IN ({sql_in_values(TaskCategory)})",
            name="ck_tasks_category",
        ),
        CheckConstraint(
            "(recur_days IS NULL AND recurring_series_id IS NULL) OR "
            "(recur_days IS NOT NULL AND recur_days BETWEEN 1 AND 3650 "
            "AND recurring_series_id IS NOT NULL "
            "AND btrim(recurring_series_id) <> '')",
            name="ck_tasks_recurrence",
        ),
        CheckConstraint(
            "status NOT IN ('DONE', 'VERIFIED') OR completed_at IS NOT NULL",
            name="ck_tasks_completion_timestamp",
        ),
        CheckConstraint(
            "verified_by_id IS NULL OR verified_at IS NOT NULL",
            name="ck_tasks_verification_attribution",
        ),
        CheckConstraint(
            "(status = 'VERIFIED' AND verified_at IS NOT NULL) OR "
            "(status <> 'VERIFIED' AND verified_by_id IS NULL AND verified_at IS NULL)",
            name="ck_tasks_verification_state",
        ),
        CheckConstraint(
            "(status = 'SKIPPED' AND skipped_at IS NOT NULL) OR "
            "(status <> 'SKIPPED' AND skipped_by_id IS NULL AND skipped_at IS NULL "
            "AND skip_reason IS NULL)",
            name="ck_tasks_skip_state",
        ),
        CheckConstraint(
            "rejected_by_id IS NULL OR rejected_at IS NOT NULL",
            name="ck_tasks_rejection_attribution",
        ),
        CheckConstraint(
            "status = 'PENDING' OR (verification_note IS NULL "
            "AND rejected_by_id IS NULL AND rejected_at IS NULL)",
            name="ck_tasks_rejection_current_state",
        ),
        CheckConstraint(
            "status <> 'PENDING' OR assigned_user_id IS NULL OR assigned_role_id IS NOT NULL",
            name="ck_tasks_user_assignment_has_role",
        ),
        # jsonb shape guard: a wrong-shape payload fails in PostgreSQL at the
        # write, not at the next client-side catalog render (the same guard
        # every other JSONB column carries).
        CheckConstraint(
            "jsonb_typeof(title_args) = 'object'",
            name="ck_tasks_title_args_json_object",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # farm_id single kept deliberately — see the matching note on
    # Transaction in models/finance.py (2026-09-21 index review).
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    title: Mapped[str] = mapped_column(String(MAX_TASK_TITLE_LENGTH))
    # Localization contract for server-generated duties: a stable snake_case
    # key plus structured args the client renders in the worker's language
    # (Telugu catalog lives in the frontend); ``title`` stays the English
    # fallback. Manual duties leave title_key NULL and title_args {}.
    title_key: Mapped[str | None] = mapped_column(String(60))
    title_args: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    # Single-column status/due_date indexes were dropped: every query
    # combining them is farm-scoped and served by the composite
    # ix_tasks_farm_status_due or the PENDING partials below.
    due_date: Mapped[date]
    status: Mapped[str] = mapped_column(String(10), default=TaskStatus.PENDING.value)
    animal_id: Mapped[int | None] = mapped_column(ForeignKey("animals.id"), index=True)
    purchase_batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("purchase_batches.id"), index=True
    )
    breeding_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("breeding_records.id"), index=True
    )
    category: Mapped[str] = mapped_column(String(15), default=TaskCategory.OTHER.value)
    auto_generated: Mapped[bool] = mapped_column(default=False)

    # Duty assignment (RBAC): a role, a specific worker, or both null
    # (owner-visible only).
    assigned_role_id: Mapped[int | None] = mapped_column(ForeignKey("roles.id"), index=True)
    assigned_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)

    # Attribution + verification trail ("everyone's job is noted and digitized").
    completed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    completed_at: Mapped[datetime | None]
    verified_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    verified_at: Mapped[datetime | None]
    verification_note: Mapped[str | None] = mapped_column(String(255))  # reason when rejected
    # Who manually skipped the duty (service-side skips — abort, kidding
    # leftovers, death/sale — stay NULL: no single user made that call).
    skipped_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    skipped_at: Mapped[datetime | None]
    skip_reason: Mapped[str | None] = mapped_column(String(255))
    # Who sent a duty back to its worker, and when. Set together with
    # verification_note and cleared with it once the duty leaves the rejected
    # state, so they describe the rejection the row is currently carrying.
    rejected_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    rejected_at: Mapped[datetime | None]

    # Creation attribution: manual duties record their creator; generated
    # duties keep created_by_id NULL (no single user made that call).
    created_at: Mapped[datetime] = mapped_column(
        default=utcnow, server_default=text("timezone('UTC', now())")
    )
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    # Recurring duty: on completion the next occurrence is spawned this many
    # days after the current due_date (e.g. 1 = daily cleaning).
    recur_days: Mapped[int | None]
    # Stable identity for one recurrence chain. Title/assignment are editable
    # display data and cannot safely distinguish two otherwise identical
    # parallel duties.
    # No single-column series index: uq_task_recurring_series_due
    # (farm_id, recurring_series_id, due_date) serves every spawn-dedup
    # lookup, which is always tenant-scoped.
    recurring_series_id: Mapped[str | None] = mapped_column(String(36))

    animal: Mapped[Animal | None] = relationship(foreign_keys=[animal_id])
    breeding_record: Mapped[BreedingRecord | None] = relationship(foreign_keys=[breeding_record_id])
    assigned_role: Mapped[Role | None] = relationship(foreign_keys=[assigned_role_id])
    assigned_user: Mapped[User | None] = relationship(foreign_keys="Task.assigned_user_id")
    completed_by: Mapped[User | None] = relationship(foreign_keys="Task.completed_by_id")
    verified_by: Mapped[User | None] = relationship(foreign_keys="Task.verified_by_id")
    skipped_by: Mapped[User | None] = relationship(foreign_keys="Task.skipped_by_id")
    rejected_by: Mapped[User | None] = relationship(foreign_keys="Task.rejected_by_id")

    @property
    def needs_verification(self) -> bool:
        """DONE for these categories means 'awaiting verification', not final."""
        return (
            self.status == TaskStatus.DONE.value
            and self.category in VERIFICATION_REQUIRED_CATEGORIES
        )

    @classmethod
    def awaiting_verification_clause(cls) -> ColumnElement[bool]:
        """SQL twin of ``needs_verification`` — the single definition every
        query must share, or lifecycle changes reach one consumer and not
        another (a role became deletable while its duty awaited verification
        exactly this way)."""
        return and_(
            cls.status == TaskStatus.DONE.value,
            cls.category.in_(VERIFICATION_REQUIRED_CATEGORIES),
        )

    @classmethod
    def requires_action_clause(cls) -> ColumnElement[bool]:
        """A duty someone must still act on: open, or done but unverified."""
        return or_(
            cls.status == TaskStatus.PENDING.value,
            cls.awaiting_verification_clause(),
        )


Index(
    "ix_tasks_farm_pending_due_id",
    Task.farm_id,
    Task.due_date,
    Task.id,
    postgresql_where=text("status = 'PENDING'"),
)
Index(
    "ix_tasks_farm_pending_category_due_id",
    Task.farm_id,
    Task.category,
    Task.due_date,
    Task.id,
    postgresql_where=text("status = 'PENDING'"),
)
Index(
    "ix_tasks_farm_animal_pending",
    Task.farm_id,
    Task.animal_id,
    postgresql_where=text("status = 'PENDING' AND animal_id IS NOT NULL"),
)
Index(
    "ix_tasks_pending_farm_role",
    Task.farm_id,
    Task.assigned_role_id,
    postgresql_where=text("status = 'PENDING' AND assigned_role_id IS NOT NULL"),
)
Index(
    "ix_tasks_pending_animal_id_id",
    Task.animal_id,
    Task.id,
    postgresql_where=text("status = 'PENDING' AND animal_id IS NOT NULL"),
)
