"""Simulation scenarios (bio-economic projections)."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base, JSONText
from ..utils import utcnow

if TYPE_CHECKING:
    from .core import Farm, User


class SimulationScenario(Base):
    """Saved simulation assumption set for a farm.

    Assumptions are stored as jsonb behind a JSON-text ORM interface (same
    pattern as Role.permissions): the ``app.simulation`` package owns the
    schema, the DB stores the dump (CHECK-enforced object shape; see
    ck_simulation_scenarios_assumptions_json_object).
    """

    __tablename__ = "simulation_scenarios"
    __table_args__ = (
        UniqueConstraint("farm_id", "name", name="uq_simulation_scenario_per_farm"),
        CheckConstraint("revision >= 1", name="ck_simulation_scenarios_revision_positive"),
        # jsonb shape guard: a wrong-shape payload fails in PostgreSQL at the
        # write, not at the next json.loads/model_validate read.
        CheckConstraint(
            "jsonb_typeof(assumptions) = 'object'",
            name="ck_simulation_scenarios_assumptions_json_object",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    notes: Mapped[str] = mapped_column(Text, default="")
    assumptions: Mapped[str] = mapped_column(JSONText)  # JSON: SimulationAssumptions dump
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    # A saved scenario is a full JSON assumption set.  Require callers to
    # prove which version they edited so one browser tab cannot silently erase
    # another tab's accepted changes.
    revision: Mapped[int] = mapped_column(default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    farm: Mapped[Farm] = relationship()
    created_by: Mapped[User | None] = relationship()
