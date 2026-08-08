"""Simulation scenarios (bio-economic projections)."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from ..utils import utcnow

if TYPE_CHECKING:
    from .core import Farm, User


class SimulationScenario(Base):
    """Saved simulation assumption set for a farm.

    Assumptions are stored as JSON text (same pattern as Role.permissions):
    the ``app.simulation`` package owns the schema, the DB stores the dump.
    """

    __tablename__ = "simulation_scenarios"
    __table_args__ = (UniqueConstraint("farm_id", "name", name="uq_simulation_scenario_per_farm"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    notes: Mapped[str] = mapped_column(Text, default="")
    assumptions: Mapped[str] = mapped_column(Text)  # JSON: SimulationAssumptions dump
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    farm: Mapped[Farm] = relationship()
    created_by: Mapped[User | None] = relationship()
