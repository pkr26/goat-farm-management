"""Pydantic schemas for the planner module (Business → Planner).

The engine models live in the pure ``app.simulation`` package
(``backward_planner`` for target plans, ``milk_planner`` for dairy milk
targets) and are re-exported here so the OpenAPI schema picks them up next to
the transport schemas — the same arrangement as ``schemas/simulation.py``.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from ..simulation.assumptions import SimulationAssumptions
from ..simulation.backward_planner import BackwardPlanReport, PlannerTarget
from ..simulation.milk_planner import MilkPlanReport
from .common import FiniteFloat, PostgresText, StrictBool, StrictInputModel, StrictInt

__all__ = [
    "BackwardPlanIn",
    "BackwardPlanReport",
    "MilkPlanIn",
    "MilkPlanReport",
    "PlannerPlanCreateIn",
    "PlannerPlanListOut",
    "PlannerPlanOut",
    "PlannerPlanUpdateIn",
    "PlannerTarget",
    "PlannerTargetIn",
    "SimulationAssumptions",
]


class PlannerTargetIn(StrictInputModel):
    """One sale target: ``count`` head of one class in one calendar month."""

    # Years bounded to the engine's 1900-2200 calendar range, so a plan can
    # never be stored with a date the engine rejects on every later run.
    year_month: PostgresText = Field(pattern=r"^(19\d{2}|20\d{2}|21[0-1]\d|2200)-(0[1-9]|1[0-2])$")
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


class BackwardPlanIn(StrictInputModel):
    """A backward plan: the assumptions it runs against plus calendar-dated
    sale targets."""

    assumptions: SimulationAssumptions
    targets: list[PlannerTargetIn] = Field(min_length=1, max_length=50)
    close_gaps: StrictBool = True
    # 0 skips the risk pass; the cap keeps one request priced like a run.
    risk_runs: StrictInt = Field(default=0, ge=0, le=500)


class MilkPlanIn(StrictInputModel):
    """A milk plan: a daily litres target the dairy herd must deliver."""

    assumptions: SimulationAssumptions
    # Litres per day the farm must ship (e.g. a procurement contract).
    daily_target_litres: FiniteFloat = Field(gt=0.0, le=1_000_000)
    # Months over which in-milk purchases are staged while building to the
    # target herd; 1 = buy the full tranche in month 1.
    ramp_months: StrictInt = Field(default=1, ge=1, le=60)
    # Months of projection rows to return (clamped to the horizon).
    projection_months: StrictInt = Field(default=36, ge=12, le=240)
    # Size the herd for the worst seasonal (heat-stress) month instead of the
    # 12-month average.
    hold_year_round: StrictBool = False


class PlannerPlanCreateIn(StrictInputModel):
    name: PostgresText = Field(min_length=1, max_length=120)  # planner_plans.name String(120)
    notes: PostgresText = Field(default="", max_length=2000)
    start_year_month: PostgresText = Field(
        pattern=r"^(19\d{2}|20\d{2}|21[0-1]\d|2200)-(0[1-9]|1[0-2])$"
    )
    targets: list[PlannerTargetIn] = Field(min_length=1, max_length=50)
    assumptions: SimulationAssumptions


class PlannerPlanUpdateIn(StrictInputModel):
    # Same optimistic-concurrency contract as scenario updates: the revision
    # token is the only way to distinguish an intentional replacement from a
    # stale tab re-sending its old document.
    expected_revision: StrictInt = Field(default=1, ge=1)
    name: PostgresText | None = Field(default=None, min_length=1, max_length=120)
    notes: PostgresText | None = Field(default=None, max_length=2000)
    start_year_month: PostgresText | None = Field(
        default=None,
        pattern=r"^(19\d{2}|20\d{2}|21[0-1]\d|2200)-(0[1-9]|1[0-2])$",
    )
    targets: list[PlannerTargetIn] | None = Field(default=None, min_length=1, max_length=50)
    assumptions: SimulationAssumptions | None = None


class PlannerPlanOut(BaseModel):
    """Built explicitly by the router (targets/assumptions are JSON text on
    the ORM row)."""

    id: int
    farm_id: int
    name: str
    notes: str
    start_year_month: str
    targets: list[PlannerTarget] | None
    assumptions: SimulationAssumptions | None
    valid: bool = True
    validation_error: str | None = None
    revision: int
    created_at: datetime
    updated_at: datetime


class PlannerPlanListOut(BaseModel):
    """One bounded page of saved plans plus the full farm-scoped count."""

    items: list[PlannerPlanOut]
    total: int
    limit: int
    offset: int
