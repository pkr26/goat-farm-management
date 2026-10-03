"""Durable keyset positions for the two bounded background farm sweeps."""

from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ..utils import utcnow


class MaintenanceProgress(Base):
    __tablename__ = "maintenance_progress"
    __table_args__ = (
        CheckConstraint(
            "job_name IN ('cadence', 'notification_alerts')",
            name="ck_maintenance_progress_job",
        ),
        CheckConstraint("after_farm_id >= 0", name="ck_maintenance_progress_cursor_nonnegative"),
    )

    job_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    # This is an ordered position, not a relationship. Deleting that farm
    # must not invalidate the position or restart the sweep at the first farm.
    after_farm_id: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    completed_hour: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: utcnow().replace(tzinfo=UTC),
        server_default=func.now(),
    )
