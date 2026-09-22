"""Owner-facing cross-farm schemas (ITEM 3, 2026-09-21 playbook)."""

from decimal import Decimal

from pydantic import BaseModel, Field

from .common import MAX_INT32_ID


class OwnerFarmOverviewOut(BaseModel):
    """One owned farm's headline row on the cross-farm overview."""

    farm_id: int = Field(ge=1, le=MAX_INT32_ID)
    farm_name: str
    timezone: str
    active_animals: int
    # PENDING duties dated before the farm's current business day.
    overdue_duties: int
    todays_duties_pending: int
    todays_duties_done: int
    # Does in the late-pregnancy / pre-kidding pens.
    kidding_watch: int
    movement_restricted: int
    open_screening_flags: int
    # Current farm-calendar month, ledger transactions only (immutable
    # register payments such as insurance premiums sit outside the ledger).
    month_income: Decimal
    month_expense: Decimal
    month_net: Decimal


class OwnerOverviewOut(BaseModel):
    farms: list[OwnerFarmOverviewOut]


class OwnerFarmBenchmarksOut(BaseModel):
    """One owned farm's benchmark figures over the requested window.

    None (not zero) means the farm has no data for a figure in the window —
    the same withheld-not-empty convention the reports endpoint uses."""

    farm_id: int = Field(ge=1, le=MAX_INT32_ID)
    farm_name: str
    conception_rate: float | None = Field(default=None, ge=0.0, le=100.0)
    kid_mortality_rate: float | None = Field(default=None, ge=0.0, le=100.0)
    avg_daily_gain_kg: float | None
    feed_cost_per_kg_gain: float | None
    profit_per_animal_sold: float | None
    animals_sold: int


class OwnerBenchmarksOut(BaseModel):
    days: int
    farms: list[OwnerFarmBenchmarksOut]
