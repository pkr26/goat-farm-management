"""Pydantic schemas for the simulation module.

The assumption/result models live in the pure ``app.simulation`` package and
are re-exported here so the OpenAPI schema picks them up next to the
transport schemas.
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from ..simulation.assumptions import SimulationAssumptions
from ..simulation.planner import PlanReport, SaleTarget
from ..simulation.results import MonteCarloResult, SensitivityItem, SimulationResult
from .common import FiniteFloat, PostgresText, StrictBool, StrictInputModel, StrictInt

__all__ = [
    "BreedsOut",
    "CalibrationEvidence",
    "FarmCalibrationOut",
    "HerdSnapshotOut",
    "MonteCarloResult",
    "PlanIn",
    "PlanReport",
    "RunIn",
    "SaleTarget",
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
    name: PostgresText = Field(
        min_length=1, max_length=120
    )  # simulation_scenarios.name String(120)
    notes: PostgresText = Field(default="", max_length=2000)
    assumptions: SimulationAssumptions


class ScenarioUpdateIn(StrictInputModel):
    # Saved assumptions are a complete JSON document. The revision token is
    # the only way to distinguish an intentional replacement from a stale tab
    # re-sending its old document.
    # Legacy clients omit this field. Every migrated row starts at revision 1,
    # allowing one safe bridge update; subsequent no-token writes conflict.
    expected_revision: StrictInt = Field(default=1, ge=1)
    name: PostgresText | None = Field(default=None, min_length=1, max_length=120)
    notes: PostgresText | None = Field(default=None, max_length=2000)
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
    revision: int
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
    optimization: StrictBool = False


class PlanTargetIn(StrictInputModel):
    """One sale target: ``count`` head of one class in one simulation month."""

    month: StrictInt = Field(ge=1)
    animal_class: Literal[
        "doe",
        "buck",
        "female_kid",
        "male_kid",
        "female_weaner",
        "male_weaner",
        "female_grower",
        "male_grower",
    ]
    count: FiniteFloat = Field(gt=0.0, le=100_000)


class PlanIn(StrictInputModel):
    """A plan: the assumptions it runs against plus the sale targets."""

    assumptions: SimulationAssumptions
    targets: list[PlanTargetIn] = Field(min_length=1, max_length=50)
    close_gaps: StrictBool = True
    # 0 skips the risk pass; the cap keeps one request priced like a run.
    risk_runs: StrictInt = Field(default=0, ge=0, le=500)


class CalibrationEvidence(BaseModel):
    """One farm-derived assumption and the evidence behind it."""

    path: str
    previous_value: int | float | list[float]
    calibrated_value: int | float | list[float]
    sample_size: int
    confidence: Literal["low", "medium", "high"]
    method: str
    source: str
    period_start: date | None = None
    period_end: date | None = None


class FarmCalibrationOut(BaseModel):
    """A complete runnable assumption set calibrated from one farm."""

    assumptions: SimulationAssumptions
    evidence: list[CalibrationEvidence]
    warnings: list[str]
    coverage_score: float = Field(ge=0.0, le=1.0)
    reference_date: date
    lookback_months: int


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
