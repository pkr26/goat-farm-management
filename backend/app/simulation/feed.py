"""Feed requirement and fodder-balance model.

Dry-matter intake is computed per class as ``headcount x weight x dmi_pct_bw``
per day, scaled by ``DAYS_PER_MONTH``. The grazed fraction of DM is free and is
netted out before the ration split and costing. Rations are per class: a
concentrate share of DM (physiological-state dependent) with the remaining DM
split green:dry in a fixed 2:1 ratio. The fodder balance compares the green-DM
requirement (after grazing) against cultivated supply.
"""

from dataclasses import dataclass

from .assumptions import FeedAssumptions

DAYS_PER_MONTH = 30.44  # 365.25/12, FAO/GBADs herd-model convention

# Non-concentrate DM is green:dry fodder in a 2:1 DM ratio (TNAU stall-fed rations).
GREEN_SHARE_OF_ROUGHAGE = 2.0 / 3.0
DRY_SHARE_OF_ROUGHAGE = 1.0 / 3.0


@dataclass(frozen=True)
class FeedBreakdown:
    """Monthly feed requirement for one class (or an aggregate of classes)."""

    dm_kg: float  # total DM requirement before grazing offset
    green_dm_kg: float  # green DM that must come from cultivation/purchase
    green_kg: float  # as-fed kg
    dry_kg: float  # as-fed kg
    concentrate_kg: float  # as-fed kg
    cost: float  # ₹


def class_feed(
    headcount: float,
    avg_weight_kg: float,
    dmi_pct_bw: float,
    concentrate_share: float,
    feed: FeedAssumptions,
) -> FeedBreakdown:
    """Monthly feed requirement and cost for one class of animals.

    ``DM = headcount x weight x dmi_pct_bw x 30.44``; the grazed fraction is
    free, so the remaining ``(1 - grazing)`` of DM is split into the class's
    concentrate share and a 2:1 green:dry roughage remainder, converted to
    as-fed kg via each feed's DM content and priced.
    """
    dm_kg = headcount * avg_weight_kg * dmi_pct_bw * DAYS_PER_MONTH
    purchased_dm = dm_kg * (1.0 - feed.grazing_dm_fraction)
    roughage_dm = purchased_dm * (1.0 - concentrate_share)
    conc_dm = purchased_dm * concentrate_share
    green_dm = roughage_dm * GREEN_SHARE_OF_ROUGHAGE
    dry_dm = roughage_dm * DRY_SHARE_OF_ROUGHAGE
    green_kg = green_dm / feed.green_dm_pct
    dry_kg = dry_dm / feed.dry_dm_pct
    conc_kg = conc_dm / feed.concentrate_dm_pct
    cost = (
        green_kg * feed.green_price_per_kg
        + dry_kg * feed.dry_price_per_kg
        + conc_kg * feed.concentrate_price_per_kg
    )
    return FeedBreakdown(
        dm_kg=dm_kg,
        green_dm_kg=green_dm,
        green_kg=green_kg,
        dry_kg=dry_kg,
        concentrate_kg=conc_kg,
        cost=cost,
    )


def combine_feed(parts: list[FeedBreakdown]) -> FeedBreakdown:
    """Sum per-class breakdowns into a herd-level monthly total."""
    return FeedBreakdown(
        dm_kg=sum(p.dm_kg for p in parts),
        green_dm_kg=sum(p.green_dm_kg for p in parts),
        green_kg=sum(p.green_kg for p in parts),
        dry_kg=sum(p.dry_kg for p in parts),
        concentrate_kg=sum(p.concentrate_kg for p in parts),
        cost=sum(p.cost for p in parts),
    )


def cultivated_green_supply_kg(feed: FeedAssumptions) -> float:
    """Monthly green DM (kg) from cultivated fodder: acres x yield(t) x 1000 / 12."""
    return feed.cultivated_fodder_acres * feed.fodder_yield_t_dm_per_acre_year * 1000.0 / 12.0


def land_requirement_acres(annual_green_dm_kg: float, yield_t_dm_per_acre_year: float) -> float:
    """Acres of cultivated fodder needed to cover an annual green-DM requirement."""
    return annual_green_dm_kg / (yield_t_dm_per_acre_year * 1000.0)
