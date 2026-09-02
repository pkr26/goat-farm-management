"""Saved planner plans (target-based backward planning)."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from ..utils import utcnow

if TYPE_CHECKING:
    from .core import Farm, User


class PlannerPlan(Base):
    """Saved sale-target plan for a farm.

    A plan is its targets plus the assumptions it runs against (both stored as
    JSON text, the same pattern as ``SimulationScenario.assumptions`` and
    ``Role.permissions``): the report is always recomputed on open, so a saved
    plan automatically re-plans against today's biology when assumptions
    schemas or the farm's herd change.
    """

    __tablename__ = "planner_plans"
    __table_args__ = (
        UniqueConstraint("farm_id", "name", name="uq_planner_plan_per_farm"),
        CheckConstraint("revision >= 1", name="ck_planner_plans_revision_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    notes: Mapped[str] = mapped_column(Text, default="")
    # "YYYY-MM" the plan anchors on (simulation month 1 falls in this month).
    start_year_month: Mapped[str] = mapped_column(String(7))
    # JSON: list of PlannerTarget dumps.
    targets: Mapped[str] = mapped_column(Text)
    # JSON: SimulationAssumptions dump.
    assumptions: Mapped[str] = mapped_column(Text)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    # Require callers to prove which version they edited so one browser tab
    # cannot silently erase another tab's accepted changes.
    revision: Mapped[int] = mapped_column(default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    farm: Mapped[Farm] = relationship()
    created_by: Mapped[User | None] = relationship()
