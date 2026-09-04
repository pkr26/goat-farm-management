"""Lactation yield curves, shared by the engine and the milk planner.

One function — :func:`monthly_milk_curve` — defines the litres a milking doe
yields in each month of her lactation, so a projection and a plan built from
the same assumptions always agree on the biology.

Two shapes are supported:

- ``geometric`` (the original engine shape): peak in the first month of milk,
  then a fixed monthly decline factor (``milk_persistency_monthly``).
- ``wood``: Wood's incomplete gamma function, the standard lactation-curve
  model in the dairy-literature for river buffalo including Murrah —

      Y(t) = a * t^b * e^(-c*t)   (litres/day at t days in milk)

  with peak yield at t* = b/c. Published Wood fits put Murrah and comparable
  river-buffalo peaks at day 57-73 (comparative lactation-curve studies report
  ~day 65 for Murrah; Bangladeshi river-buffalo Wood fits give b = 0.465-0.677,
  c = 0.006-0.010, peaking day 57-73), so the shape is anchored by the single
  observable a farmer can act on — the peak day — with curvature b fixed at
  0.6 (the middle of the published range) and c = b / peak_day.

Both shapes are normalised so the whole lactation sums to exactly
``lactation_milk_litres``; the planner's herd arithmetic depends on that
identity. Note the identity holds for the CURVE itself: the engine then
applies the farm's ``monthly_milk_yield_multipliers`` (heat-stress trough,
lean-season premium) which are NOT mean-normalized, so a preset whose
multipliers average below 1.0 realizes slightly less than
``lactation_milk_litres`` per lactation-year (the Murrah preset's yield
curve averages ~0.989 → ≈1.1% below the stated total). That seasonality
drift is intentional biology, not a rounding bug.
"""

import math

from .assumptions import SalesAssumptions
from .feed import DAYS_PER_MONTH

# Curvature of Wood's curve, from published river-buffalo Wood fits
# (b = 0.465-0.677). Fixed rather than exposed: with the peak day given, b
# only controls how peaked the curve is, and 0.6 reproduces the recorded
# Murrah peak-to-average ratio (~1.5 for a 305-day lactation).
WOOD_CURVATURE_B = 0.6


def wood_daily_yield(dim_days: float, peak_day: float) -> float:
    """Unnormalised Wood yield at ``dim_days`` days in milk (arbitrary units).

    Zero at calving, rising to a single peak at ``peak_day`` (t* = b/c), then
    declining; the asymptotic late-lactation decline is c per day.
    """
    if dim_days <= 0.0:
        return 0.0
    c = WOOD_CURVATURE_B / peak_day
    return math.pow(dim_days, WOOD_CURVATURE_B) * math.exp(-c * dim_days)


def wood_monthly_weights(
    lactation_months: int,
    peak_day: float,
    days_per_month: float = DAYS_PER_MONTH,
) -> list[float]:
    """Unnormalised Wood-curve mass in each month bucket of the lactation.

    Each bucket integrates the daily curve over its days (sampled at daily
    midpoints, which is exact for a smooth curve at this resolution).
    """
    weights: list[float] = []
    for month_index in range(lactation_months):
        start = month_index * days_per_month
        mass = 0.0
        steps = max(1, round(days_per_month))
        for step in range(steps):
            dim = start + (step + 0.5) * days_per_month / steps
            mass += wood_daily_yield(dim, peak_day)
        weights.append(mass * days_per_month / steps)
    return weights


def monthly_milk_curve(
    lactation_litres: float,
    lactation_months: int,
    *,
    shape: str = "geometric",
    persistency_monthly: float = 0.93,
    peak_day: float = 65.0,
) -> list[float]:
    """Litres per head in each month of lactation (index 0 = first month in
    milk), summing to exactly ``lactation_litres``.

    ``shape`` selects the curve family; the keyword defaults mirror the
    assumption schema so a bare call reproduces the legacy engine curve.
    """
    if lactation_months <= 0 or lactation_litres <= 0.0:
        return [0.0] * max(0, lactation_months)
    if shape == "wood":
        weights = wood_monthly_weights(lactation_months, peak_day)
    else:
        weights = [math.pow(persistency_monthly, i) for i in range(lactation_months)]
    total = sum(weights)
    if total <= 0.0:
        # Unreachable through the schema (milk_peak_day >= 1.0 keeps the
        # curve far from underflow), but a direct library call must fail
        # loudly instead of dividing by zero.
        raise ValueError(f"lactation curve degenerated to zero ({shape=}, {peak_day=})")
    return [lactation_litres * weight / total for weight in weights]


def curve_from_assumptions(sales: SalesAssumptions, lactation_months: int) -> list[float]:
    """Monthly lactation curve from a SalesAssumptions block.

    The single construction point shared by the engine and the milk planner:
    changing the curve here changes everywhere at once.
    """
    return monthly_milk_curve(
        sales.lactation_milk_litres,
        lactation_months,
        shape=sales.milk_curve_shape,
        persistency_monthly=sales.milk_persistency_monthly,
        peak_day=sales.milk_peak_day,
    )
