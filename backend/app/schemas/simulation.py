"""Pydantic schemas for the simulation module.

The assumption/result models live in the pure ``app.simulation`` package and
are re-exported here so the OpenAPI schema picks them up next to the
transport schemas.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from ..simulation.assumptions import SimulationAssumptions
from ..simulation.results import MonteCarloResult, SensitivityItem, SimulationResult
from .common import StrictBool, StrictInputModel

__all__ = [
    "BreedsOut",
    "HerdSnapshotOut",
    "MonteCarloResult",
    "RunIn",
    "ScenarioCompareOut",
    "ScenarioCreateIn",
    "ScenarioListOut",
    "ScenarioOut",
    "ScenarioUpdateIn",
    "SensitivityItem",
    "SimulationAssumptions",
    "SimulationResult",
]


class ScenarioCreateIn(StrictInputModel):
    name: str = Field(min_length=1, max_length=120)  # simulation_scenarios.name String(120)
    notes: str = Field(default="", max_length=2000)
    assumptions: SimulationAssumptions


class ScenarioUpdateIn(StrictInputModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    notes: str | None = Field(default=None, max_length=2000)
    assumptions: SimulationAssumptions | None = None


class ScenarioOut(BaseModel):
    """Built explicitly by the router (assumptions are JSON text on the ORM row)."""

    id: int
    farm_id: int
    name: str
    notes: str
    assumptions: SimulationAssumptions | None
    valid: bool = True
    validation_error: str | None = None
    created_at: datetime
    updated_at: datetime


class ScenarioListOut(BaseModel):
    """One bounded page of saved scenarios plus the full farm-scoped count."""

    items: list[ScenarioOut]
    total: int
    limit: int
    offset: int


class RunIn(StrictInputModel):
    assumptions: SimulationAssumptions
    monte_carlo: StrictBool = False
    sensitivity: StrictBool = False


class HerdSnapshotOut(BaseModel):
    """The farm's ACTIVE animals grouped into simulation starting cohorts."""

    does: int
    bucks: int
    f_kids: int
    f_weaners: int
    f_growers: int
    m_kids: int
    m_weaners: int
    m_growers: int
    total_head: int


class BreedsOut(BaseModel):
    breeds: list[str]
    systems: list[str]


class ScenarioCompareOut(BaseModel):
    scenarios: list[ScenarioOut]
    results: list[SimulationResult]
