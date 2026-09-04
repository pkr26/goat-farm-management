"""Pydantic schemas for the daily operations simulation (Business → Ops Sim).

The engine models live in the pure ``app.simulation.daily_ops`` module and are
re-exported here so the OpenAPI schema picks them up next to the transport
schemas — the same arrangement as ``schemas/planner.py``.
"""

from datetime import date

from pydantic import BaseModel, Field

from ..simulation.daily_ops import (
    AnimalStartSpec,
    DailyOpsInput,
    DailyOpsParams,
    DailyOpsResult,
)
from .common import MAX_ID, StrictBool, StrictInputModel, StrictInt

__all__ = [
    "AnimalStartSpec",
    "DailyOpsInput",
    "DailyOpsParams",
    "DailyOpsResult",
    "DailyOpsRunIn",
    "DailyOpsRunOut",
]

# Seed parity with the app's integer bounds (schemas.common.MAX_ID): the seed
# is echoed back in the result, explanation and ledger, so an unbounded int
# would amplify a small request body into megabytes of repeated digits.
MAX_SEED = MAX_ID
MAX_LEDGER_HEAD_DAYS = 50_000


class DailyOpsRunIn(StrictInputModel):
    """One daily-operations simulation run: the starting herd standing in its
    buckets, the run window and the optional Markdown ledger."""

    start_date: date
    horizon_days: StrictInt = Field(default=90, ge=7, le=365)
    seed: StrictInt = Field(default=2026, ge=-MAX_SEED, le=MAX_SEED)
    animals: list[AnimalStartSpec] = Field(min_length=1, max_length=500)
    params: DailyOpsParams = Field(default_factory=DailyOpsParams)
    # The ledger re-renders the whole run as one Markdown audit document; it
    # can be large, so it is opt-in and capped by run size (head × days).
    include_ledger: StrictBool = False


class DailyOpsRunOut(BaseModel):
    """The simulation result plus the optional audit ledger."""

    result: DailyOpsResult
    ledger: str | None = None
