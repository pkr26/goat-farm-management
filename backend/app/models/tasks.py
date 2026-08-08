"""Tasks / alerts."""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from .constants import VERIFICATION_REQUIRED_CATEGORIES
from .enums import TaskCategory, TaskStatus

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
        Index("ix_tasks_farm_status_due", "farm_id", "status", "due_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    due_date: Mapped[date] = mapped_column(index=True)
    status: Mapped[str] = mapped_column(String(10), default=TaskStatus.PENDING.value, index=True)
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

    # Recurring duty: on completion the next occurrence is spawned this many
    # days after the current due_date (e.g. 1 = daily cleaning).
    recur_days: Mapped[int | None]
    # Stable identity for one recurrence chain. Title/assignment are editable
    # display data and cannot safely distinguish two otherwise identical
    # parallel duties.
    recurring_series_id: Mapped[str | None] = mapped_column(String(36), index=True)

    animal: Mapped[Animal | None] = relationship()
    breeding_record: Mapped[BreedingRecord | None] = relationship()
    assigned_role: Mapped[Role | None] = relationship()
    assigned_user: Mapped[User | None] = relationship(foreign_keys="Task.assigned_user_id")
    completed_by: Mapped[User | None] = relationship(foreign_keys="Task.completed_by_id")
    verified_by: Mapped[User | None] = relationship(foreign_keys="Task.verified_by_id")
    skipped_by: Mapped[User | None] = relationship(foreign_keys="Task.skipped_by_id")

    @property
    def needs_verification(self) -> bool:
        """DONE for these categories means 'awaiting verification', not final."""
        return self.category in VERIFICATION_REQUIRED_CATEGORIES
