"""Assumption model for the bio-economic herd simulation.

Every field carries a default, so ``SimulationAssumptions()`` is a complete,
valid Osmanabadi stall-fed NABARD-style run (50 does + 2 bucks, 120 months).
All sub-models forbid extra keys so API payloads fail loudly on typos.
"""

import re
from itertools import pairwise
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Every numeric assumption must be finite and preserve its JSON type: NaN/inf
# would propagate through a
# run and crash JSON serialization of the result (500) — reject at 422 time.
FiniteFloat = Annotated[float, Field(strict=True, allow_inf_nan=False)]

# Head-count ceiling for starting cohorts / scheduled event counts: far above
# any real farm, small enough that cohort arithmetic can't overflow float64.
MAX_HEAD = 100_000

# Event-document ceiling shared by the schema bound on SimulationAssumptions
# and the planner's purchase-chunk budget arithmetic (RT-L8-1): one constant
# so the planner can never reject plans the schema admits, or over-admit
# plans the schema would refuse after materialization.
MAX_PLAN_EVENTS = 500

# Ceiling for the labour-scaling divisor only: float-exact (< 2**53) so the
# engine's ``total_herd / threshold`` stays finite, huge so stored scenarios
# that predate any bound keep revalidating. See CostsAssumptions.
MAX_LABOUR_PER_HEAD_THRESHOLD = 10**15

# Magnitude caps mirror schemas/common.py: finite-but-huge inputs (1e308)
# pass the NaN/inf guard yet overflow derived math (revenue = head × kg × ₹)
# into inf/NaN, which then crashes JSON serialization of the run (500).
# ₹1e9 (100 crore) for money/prices and 1000 kg for live weights are far past
# anything the domain can legitimately reach.
MAX_MONEY = 1_000_000_000
MAX_WEIGHT_KG = 1000
# A million acres / a thousand tonnes DM per acre: orders of magnitude past
# real fodder cultivation, small enough to keep area × yield products finite.
MAX_FODDER_ACRES = 1_000_000
MAX_FODDER_YIELD_T = 1000
# Agronomic floor (~10 kg DM/acre/yr): a denominator-sized yield turns the
# land requirement absurd or overflowing (1e-300 → 1e301 acres), so the bound
# is a floor, not merely > 0 (red-team RT-L8-4). Monte Carlo clamps that
# perturb this field must revalidate against this same floor.
MIN_FODDER_YIELD_T = 0.01
# Monte Carlo spread multipliers: a 100x swing is already absurd.
MAX_RISK_MULTIPLIER = 100.0
# Price/yield seasonality is deliberately bounded more tightly than an
# arbitrary Monte Carlo spread: a 10x month-to-month multiplier is already well
# beyond a useful farm-planning input while still allowing severe stress cases.
MAX_SEASONAL_MULTIPLIER = 10.0

# A live weight in kg, bounded so head × kg × ₹ products stay finite.
WeightKg = Annotated[FiniteFloat, Field(gt=0.0, le=MAX_WEIGHT_KG)]


def _normalized_seasonality(multipliers: list[float]) -> list[float]:
    """Rescale twelve monthly multipliers so their mean is exactly 1.0.

    The base price is documented as the *annual mean* live-weight price; a
    hand-written seasonal curve whose average is 0.98 quietly re-defines it as
    a peak-month price and understates every month of the year by 2%.
    """
    mean = sum(multipliers) / len(multipliers)
    if mean <= 0.0:
        return [1.0] * 12
    return [value / mean for value in multipliers]


class _Group(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class MetaAssumptions(_Group):
    """Run horizon and calendar anchoring."""

    horizon_months: int = Field(default=120, ge=12, le=240)
    # "YYYY-MM"; simulation month 1 falls in this calendar month. Used only for
    # the seasonal (Eid) price uplift.
    start_year_month: str = "2026-08"

    @field_validator("start_year_month")
    @classmethod
    def _valid_year_month(cls, value: str) -> str:
        match = re.fullmatch(r"(\d{4})-(\d{2})", value)
        if (
            match is None
            or not 1 <= int(match.group(2)) <= 12
            or not 1900 <= int(match.group(1)) <= 2200
        ):
            raise ValueError("start_year_month must be a real YYYY-MM (e.g. '2026-08')")
        return value


class HerdAssumptions(_Group):
    """Starting stock and replacement/purchase policy."""

    does: int = Field(default=50, ge=0, le=MAX_HEAD)
    bucks: int = Field(default=2, ge=0, le=MAX_HEAD)
    female_growers: int = Field(default=0, ge=0, le=MAX_HEAD)
    male_growers: int = Field(default=0, ge=0, le=MAX_HEAD)
    female_weaners: int = Field(default=0, ge=0, le=MAX_HEAD)
    male_weaners: int = Field(default=0, ge=0, le=MAX_HEAD)
    female_kids: int = Field(default=0, ge=0, le=MAX_HEAD)
    male_kids: int = Field(default=0, ge=0, le=MAX_HEAD)
    # Fraction of female growers reaching first-breeding age that are kept as
    # replacements; the rest are sold as meat. 0.60 (not the textbook 0.50):
    # with a 20% annual cull plus 5% adult mortality a 0.50 retention cannot
    # hold the doe pool flat, and the projected flock silently shrinks ~20%
    # over years 2-5 — exactly the years a lender watches. 0.60 of a ~2.8
    # female-graduates/month pipeline (~17/yr) covers the ~12.5/yr outflow.
    female_retention_fraction: FiniteFloat = Field(default=0.60, ge=0.0, le=1.0)
    # Cap on the breeding-doe pool; 0 = unlimited. Default 50 holds the flock
    # at the NABARD 50+2 unit size (model projects keep the breeding flock
    # constant and sell surplus replacements); set 0 for unconstrained growth.
    max_breeding_does: int = Field(default=50, ge=0, le=MAX_HEAD)
    # ₹. Telangana/Nizamabad 2025-26 rates: quality young Osmanabadi does
    # ₹8,000-15,000 (mandi listings ₹220-350/kg live; 14-month Nashik
    # replacement ~₹11,000); 9,500 is the conservative mid for a young proven
    # doe bought outside festival weeks.
    doe_purchase_price: FiniteFloat = Field(default=9500.0, ge=0.0, le=MAX_MONEY)
    # Pure-line Osmanabadi breeding bucks list ~₹15,000 (Raigad/Maharashtra
    # 2025); a farm-raised young sire is less, a proven one more.
    buck_purchase_price: FiniteFloat = Field(default=15000.0, ge=0.0, le=MAX_MONEY)
    # Buy a buck whenever the buck:doe ratio falls below 1:buck_doe_ratio.
    auto_purchase_bucks: bool = True
    # Age window over which foundation and later purchased adult does are
    # spread. 18-42 months (young proven, mixed ages) rather than the old
    # 24-60: a purchased flock averaging 42 months sends a rolling max-age
    # cull wave through the herd for three years, and no real buyer stocks up
    # on near-spent does. Both ends stay inside the doe-age tracking array.
    foundation_doe_age_min_months: int = Field(default=18, ge=0, le=180)
    foundation_doe_age_max_months: int = Field(default=42, ge=0, le=180)
    # A bought-in adult doe does not settle and cycle the day she lands:
    # transport stress, new ration, pecking order. She spends this many months
    # in a settling pool (maintenance feeding, adult mortality, no service)
    # before joining the ready-open pool. 0 restores same-month breeding.
    purchased_doe_settling_months: int = Field(default=1, ge=0, le=6)

    @model_validator(mode="after")
    def _foundation_age_window_is_valid(self) -> "HerdAssumptions":
        if self.foundation_doe_age_min_months > self.foundation_doe_age_max_months:
            raise ValueError(
                "foundation_doe_age_min_months must be <= foundation_doe_age_max_months"
            )
        return self

    # Foundation flock reproductive state: "mixed" spreads the starting does
    # uniformly across the reproductive cycle (realistic purchased flock —
    # some pregnant, some lactating, some open — so sales begin in year 1);
    # "open" starts every doe open and ready to breed in month 1 (clean
    # projection start, used by the golden unit tests).
    foundation_flock_state: Literal["open", "mixed"] = "mixed"


class ParityMultipliers(_Group):
    """Parity-structured (kidding-number) reproduction adjustments.

    The engine weights these over its doe-age ledger, so they apply as
    expected-value multipliers on the monthly litter size and conception
    rate. Index 0 is the first parity (maiden does), indexes 1-3 the mature
    parity-2-4 peak, and the LAST entry extends to every later parity
    (late-parity decline). Defaults are literature-anchored for Osmanabadi:
    first-parity litters ~1.4 kids vs ~1.65-1.7 mature (AICRP/NARI herd
    records) → 1.4/1.65 ≈ 0.85; pubertal does conceive ~8% below mature does
    per service; parity >= 6 does decline toward ~0.9 of mature performance.
    All-ones tables reproduce the flat (parity-free) model exactly.
    """

    litter_size: list[FiniteFloat] = Field(
        default_factory=lambda: [0.85, 1.0, 1.0, 1.0, 0.97, 0.94, 0.90],
        min_length=1,
        max_length=12,
    )
    conception_rate: list[FiniteFloat] = Field(
        default_factory=lambda: [0.92, 1.0, 1.0, 1.0, 0.98, 0.96, 0.90],
        min_length=1,
        max_length=12,
    )

    @field_validator("litter_size", "conception_rate")
    @classmethod
    def _multipliers_are_positive(cls, value: list[float]) -> list[float]:
        if any(multiplier <= 0.0 or multiplier > 2.0 for multiplier in value):
            raise ValueError("parity multipliers must be > 0 and <= 2.0")
        return value


class ReproductionAssumptions(_Group):
    """Breeding biology (monthly resolution)."""

    conception_rate: FiniteFloat = Field(
        default=0.85, ge=0.0, le=1.0
    )  # per service, ICAR herd models
    gestation_months: int = Field(default=5, ge=1, le=12)  # ~150 days goats
    # Months the doe stays in the lactating pool after kidding before her
    # rebreed wait begins — i.e. the WEANING-PLUS-REBREED interval, not a
    # saleable-milk lactation length. Default 2 aligns the projection with
    # the operational SPEC (GOAT_PROFILE.weaning_days = 60 → 2 months at
    # monthly resolution; daily_ops weans at day 60 with a 14-day VWP). The
    # old 3 modelled a 90-day weaning the farm does not practice and
    # stretched the kidding cycle to ~9-10 months against published
    # Osmanabadi kidding intervals of 232-297 days (7.7-9.8 months;
    # 5 + 2 + 1 = 8 sits inside that band). When ``weaning_days`` is 90 and
    # no explicit lactation length is given, this derives to 3.
    lactation_months: int = Field(default=2, ge=1, le=12)
    # Weaning policy: the operational day-60 wean (GOAT_PROFILE.weaning_days)
    # drives the ~8-month kidding interval above; 90 is the conservative
    # research standard (better kid thrift, longer doe recovery) and stretches
    # the cycle by a month. Setting 90 without an explicit lactation_months
    # moves that pool to 3 months so the projection honours the policy.
    weaning_days: Literal[60, 90] = 60
    # Months a doe waits after lactation before rebreeding (post-partum anoestrus).
    # 1 for Osmanabadi: published kidding intervals run 232-297 days (7.7-9.8
    # months; improved-management herds ~195 d), so a 5-month gestation + 1
    # month open + ~1 month to conceive lands the cycle at ~8-9 months —
    # the old 2-month wait pushed the interval to ~10.2 months, a kidding a
    # year, and understated offtake ~20%.
    months_open_before_breeding: int = Field(default=1, ge=0, le=12)
    litter_size: FiniteFloat = Field(default=1.6, ge=0.5, le=4.0)  # kids born per kidding
    # Fraction of births that are female under natural service (~0.5).
    sex_ratio_female: FiniteFloat = Field(default=0.5, ge=0.0, le=1.0)
    # Single-sourced with the operational breeding floor
    # (GOAT_PROFILE.min_breeding_age_months = 12): field puberty for
    # Osmanabadi is ~11.5 months and age-at-first-kidding norms run 19-20
    # months, so a 12-month first service plus the 22 kg weight gate kids a
    # maiden doe at ~17 months at the earliest.
    age_at_first_breeding_months: int = Field(default=12, ge=6, le=30)
    stillbirth_rate: FiniteFloat = Field(default=0.02, ge=0.0, le=0.5)
    # A doe whose breeding attempt fails this many consecutive services is
    # culled as a repeat breeder (the site plan's "3-service rule"). 0
    # disables — does are re-served indefinitely. Default 2 single-sources
    # the operational flag
    # (models.species.GOAT_PROFILE.failed_services_before_cull = 2: the
    # daily-ops write path flags a doe as a cull candidate after two failed
    # services), so the projection and the farm's own worklist enforce one
    # policy. Kept as a literal, not an import: app.simulation must stay
    # importable without the DB-layer models (mutmut profile); the parity is
    # pinned by tests/test_simulation_engine.py::test_repeat_cull_default_matches_species_profile.
    max_services_before_cull: int = Field(default=2, ge=0, le=12)
    # Parity (kidding-number) structure on litter size and conception rate;
    # default-on with literature-anchored multipliers. See ParityMultipliers.
    parity_multipliers: ParityMultipliers = Field(default_factory=ParityMultipliers)

    @model_validator(mode="before")
    @classmethod
    def _derive_lactation_from_weaning(cls, data: object) -> object:
        # A 90-day weaning with no explicit lactation length moves the
        # lactating pool to 3 months (90 days at monthly resolution); an
        # explicit lactation_months always wins, and a model_dump round trip
        # already carries the derived value, so this fires exactly once.
        if isinstance(data, dict) and data.get("weaning_days") == 90:
            data = {**data}
            data.setdefault("lactation_months", 3)
        return data


class MortalityAssumptions(_Group):
    """Mortality fractions per class.

    ``kid_pre_weaning`` and ``kid_post_weaning`` are WHOLE-PHASE rates: the
    fraction of a crop lost across the entire 3-month kid (0-2 m) / weaner
    (3-5 m) class, which is how the literature quotes them (5-15% pre-weaning
    stall-fed; NABARD bankable models use 15%). The engine spreads each rate
    over exactly its three monthly slots, so a documented 10% removes 10% of
    the crop — not the 2.6% an annual-rate conversion over three months
    realized. ``grower`` and ``adult`` are ANNUAL rates (compounded monthly):
    those classes have breed-dependent, open-ended durations, and survivors
    stay in them indefinitely.
    """

    kid_pre_weaning: FiniteFloat = Field(
        default=0.15, ge=0.0, le=0.9
    )  # per crop: NABARD bankable convention (field 10.9-20.4%)
    kid_post_weaning: FiniteFloat = Field(default=0.05, ge=0.0, le=0.9)  # per phase
    grower: FiniteFloat = Field(default=0.04, ge=0.0, le=0.9)  # annual
    adult: FiniteFloat = Field(default=0.05, ge=0.0, le=0.9)  # annual


class CullingAssumptions(_Group):
    """Disposal and sire-replacement policy."""

    # Annual fraction of breeding does culled; applied from simulation month 13
    # (the foundation stock gets one full year grace), NABARD/TNAU convention.
    doe_cull_rate_annual: FiniteFloat = Field(default=0.20, ge=0.0, le=1.0)
    # Floor 36, not 24: the engine spreads foundation does over ages
    # 24..min(60, max_doe_age - 12), which is an empty range below 36
    # (ZeroDivisionError mid-run). 36 keeps every schema-valid run alive.
    max_doe_age_months: int = Field(default=72, ge=36, le=180)
    buck_rotation_years: int = Field(default=3, ge=1, le=10)  # avoid inbreeding
    # Single-sourced with the operational mating policy (BUCK_DOE_RATIO = 20;
    # the write path refuses a sire's 21st open service). Published range is
    # 1:20-30 — the product states one policy everywhere.
    buck_doe_ratio: int = Field(default=20, ge=1, le=100)


# The two calibrated Osmanabadi live-weight curves (kg at ages 0..12 months).
# STALL_FED: managed stall-fed herds (ICAR-AICRP/NARI records; see
# GrowthAssumptions). SEMI_INTENSIVE: the CIRG field curve for grazing
# Osmanabadi (~6.3 kg at 3 months, ~19.6 kg at 12 months) — field flocks run
# at roughly half the stall-fed gains, so the same sale age sells a much
# lighter animal. Both start at the 2.5 kg Osmanabadi birth weight.
STALL_FED_WEIGHT_CURVE: tuple[float, ...] = (
    2.5,
    6.0,
    9.5,
    12.1,
    14.6,
    16.3,
    17.8,
    19.3,
    20.8,
    22.2,
    23.5,
    24.6,
    25.6,
)
SEMI_INTENSIVE_WEIGHT_CURVE: tuple[float, ...] = (
    2.5,
    3.8,
    5.1,
    6.3,
    8.0,
    9.7,
    11.3,
    12.9,
    14.5,
    16.1,
    17.3,
    18.5,
    19.6,
)


class GrowthAssumptions(_Group):
    """Live-weight curve and sale-age policy.

    ``weight_by_age_months`` gives live weight (kg) at ages 0..12 months; from
    month 13 the curve approaches the adult weight linearly, reaching it at
    ``adult_weight_age_months``. The default table is anchored on recorded
    field weights to 6 months (fast pre-weaning gain on dam's milk, ~3.2 kg/mo
    to ~12 kg at 3 m), then tracks the farm's commercial stall-fed finish so
    males reach the SPEC sale window (8-9 mo / 24-28 kg) on schedule.

    ``growth_regime`` selects which calibrated curve supplies the table when
    no explicit table is given: ``stall_fed`` (the managed-herd AICRP/NARI
    curve, default) or ``semi_intensive`` (the CIRG field curve of grazing
    Osmanabadi, roughly half the stall-fed gains to 3 months). Set the regime
    at construction time (or pass an explicit table); mutating the regime on
    an existing instance does not re-derive an already-materialized table.
    """

    # Which calibrated growth curve ``weight_by_age_months`` defaults to.
    # stall_fed: stall-fed managed herds (ICAR-AICRP/NARI records, 12.1 kg @
    # 3 m, ~20.8 kg @ 9 m) — the curve that finishes males into the SPEC sale
    # window. semi_intensive: CIRG field curve for grazing Osmanabadi
    # (~6.3 kg @ 3 m, ~19.6 kg @ 12 m) — field flocks grow at roughly half
    # the stall-fed rate, and a 9-month field male (~17.7 kg) is far below
    # the 24-28 kg SPEC sale window, so field-system plans should expect
    # later, lighter sales.
    growth_regime: Literal["stall_fed", "semi_intensive"] = "stall_fed"

    birth_weight_kg: WeightKg = Field(default=2.5, gt=0.0)
    # Osmanabadi breed descriptors: doe 27-36 kg (status paper ~33).
    adult_weight_doe_kg: WeightKg = Field(default=33.0, gt=0.0)
    # NBAGR/Osmanabadi descriptors 33.5-36 kg; TNAU 35-40 kg for large males.
    adult_weight_buck_kg: WeightKg = Field(default=35.0, gt=0.0)
    # Stall-fed curve anchored on recorded field weights to 6 months
    # (ICAR-AICRP/NARI: 12.1 kg @ 3 m, ~17 @ 6 m), then the farm's own
    # stall-fed finish target: the operational SPEC sells males at 8-9 months
    # / 24-28 kg, which the field-average yearling (20.5 kg) cannot reach —
    # well-managed stall-fed males are recorded ~30 kg at 12 months, and this
    # table tracks that commercial finishing so the projection prices the
    # same animal the dashboard tells the farmer to sell (male @ 9 mo =
    # 22.2 x 1.10 ≈ 24.4 kg). The semi_intensive regime substitutes the CIRG
    # field curve (SEMI_INTENSIVE_WEIGHT_CURVE) when no table is given.
    weight_by_age_months: list[WeightKg] = Field(
        default_factory=lambda: list(STALL_FED_WEIGHT_CURVE),
        min_length=13,
        # Exactly ages 0..12. Longer tables previously overrode adult weight
        # because weight_at_age consulted list length before its adult branch.
        max_length=13,
    )
    # Age at which the linear approach from the yearling weight reaches the
    # adult weight. 24 months for Osmanabadi goats.
    adult_weight_age_months: int = Field(default=24, ge=13, le=120)
    # Young males run heavier than female contemporaries (~10-20% in goats —
    # ICAR growth studies). Applied to male kid/weaner/grower weights only;
    # adult buck weight is set explicitly.
    young_male_weight_premium: FiniteFloat = Field(default=0.10, ge=0.0, le=0.5)
    # Age at which surplus males are sold for meat. Must be >= 6 so males pass
    # through the grower chain (weaning at 3, grower from 6). Single-sourced
    # with the operational SPEC window (MEAT_SALE_AGE_MONTHS = 8-9 months at
    # 24-28 kg): 9 months is the stall-fed finish whose male weight (~24.4 kg
    # on the default curve) sits inside that window. The old 10 sold a month
    # past the SPEC window at a weight below its floor.
    sale_age_months: int = Field(default=9, ge=6, le=24)

    @model_validator(mode="before")
    @classmethod
    def _apply_growth_regime_curve(cls, data: object) -> object:
        # The regime selects the curve only when the caller did not pass an
        # explicit table; a model_dump round trip carries the materialized
        # table, so the derivation fires exactly once.
        if isinstance(data, dict) and data.get("growth_regime") == "semi_intensive":
            data = {**data}
            data.setdefault("weight_by_age_months", list(SEMI_INTENSIVE_WEIGHT_CURVE))
        return data

    @field_validator("weight_by_age_months")
    @classmethod
    def _weight_curve_is_nondecreasing(cls, value: list[float]) -> list[float]:
        if any(later < earlier for earlier, later in pairwise(value)):
            raise ValueError("weight_by_age_months must be nondecreasing from birth to month 12")
        return value

    @model_validator(mode="after")
    def _birth_weight_matches_curve(self) -> "GrowthAssumptions":
        if self.weight_by_age_months[0] != self.birth_weight_kg:
            raise ValueError(
                "birth_weight_kg must equal weight_by_age_months[0] "
                "(both describe age-zero live weight)"
            )
        return self


class SalesAssumptions(_Group):
    """Market prices, seasonality and selling costs (₹).

    The twelve monthly multipliers are indexed by calendar month (January at
    index 0). ``festival_sale_months`` is indexed by simulation month instead,
    because Eid al-Adha moves through the Gregorian calendar. The legacy
    ``eid_month`` input remains supported for existing saved scenarios.
    """

    # ₹/kg live weight. Telangana 2025-26: Osmanabadi trades ₹350-550/kg in
    # Hyderabad mandis (Jiyaguda/Bowenpally trackers ₹420-490; Bakrid-season
    # ₹570-610) but the Nizamabad/Navipet rural shandy pays ₹250-350/kg for
    # 20-30 kg "cutting goats", and quality-young-male listings average
    # ≈₹386/kg (AnimStok/TradeIndia 2025-26). 370 is the farm-gate annual
    # mean for a stall-fed unit selling quality young males outside festival
    # weeks; the seasonal curve and the Bakrid uplift below carry the spikes.
    meat_price_per_kg: FiniteFloat = Field(default=370.0, ge=0.0, le=MAX_MONEY)
    # Premium-breed market toggle (e.g. Osmanabadi ₹50-100/kg premium over
    # local desi in Hyderabad mandis): fraction added to the meat price only.
    # Folded multiplicatively into meat_price_per_kg at validation time and
    # then reset to 0 — every downstream consumer (seasonality, escalation,
    # Eid uplift, Monte Carlo spread, break-even search) multiplies off the
    # base price, and resetting the toggle keeps a round-tripped saved
    # scenario from compounding the premium a second time. Cull prices are
    # deliberately excluded: they are calibrated separately from spent-animal
    # exits, not the young-male mandi tier this premium prices. 0 (default)
    # leaves every deterministic result bit-identical.
    breed_price_premium_pct: FiniteFloat = Field(default=0.0, ge=0.0, le=0.5)
    # Cull (spent) does and bucks: lower yield, older carcass. Female mandi
    # listings run ₹220-350/kg live; spent animals price near the floor.
    cull_doe_price_per_kg: FiniteFloat = Field(default=220.0, ge=0.0, le=MAX_MONEY)
    cull_buck_price_per_kg: FiniteFloat = Field(default=240.0, ge=0.0, le=MAX_MONEY)
    # January-indexed. Indian goat-market seasonality: monsoon (Jun-Sep) is
    # the demand trough (disease caution, supply glut); the Nov-Feb wedding
    # and festival window sustains the year's best non-Bakrid prices; Mar-May
    # sits at trend because the Bakrid spike is modelled separately (via
    # festival months) and must not be double-counted. Normalised so the
    # twelve multipliers average exactly 1.0 — the base price stays the
    # annual mean, not an accidental 2% under it.
    monthly_meat_price_multipliers: list[FiniteFloat] = Field(
        default_factory=lambda: _normalized_seasonality(
            [1.05, 1.00, 1.00, 1.00, 1.00, 0.93, 0.91, 0.91, 0.94, 0.99, 1.06, 1.09]
        ),
        min_length=12,
        max_length=12,
    )
    # Nominal escalation. Indian mutton/meat CPI has trended near general food
    # inflation (~5-6%/yr recent years); 4% is a conservative long-run plan
    # figure for a 10-year appraisal, paired with the same rate on feed.
    annual_livestock_price_growth_rate: FiniteFloat = Field(default=0.04, gt=-1.0, le=1.0)
    # Calendar month (1-12) in which the Bakrid price uplift applies; 0 disables it.
    eid_month: int = Field(default=0, ge=0, le=12)
    # Bakrid (Eid al-Adha) sacrificial demand: documented 30-60% live-price
    # premium in the weeks before the festival (Deonar/Jiyaguda mandi reports;
    # up to 75-100% on premium animals in some years). 0.35 is the
    # conservative mid for ordinary commercial males.
    eid_price_uplift: FiniteFloat = Field(default=0.35, ge=0.0, le=2.0)
    # Explicit 1-based simulation months are the accurate way to model a lunar
    # festival over a multi-year Gregorian forecast. ``None`` (the default) is
    # auto-filled from the embedded Bakrid calendar for meat scenarios —
    # Bakrid is the single largest price event of the year and a bare
    # default used to price the whole decade without it. An explicit empty
    # list disables the uplift; the same uplift is never applied twice in one
    # month.
    festival_sale_months: list[int] | None = Field(default=None, max_length=40)
    # Males whose sale age falls this many months BEFORE a festival month are
    # held and sold in the festival month at the festival price (Telangana
    # practice: the herd is managed so bucks finish into Bakrid). 0 sells
    # every male the month he finishes. Only fires when festival pricing is
    # active; held males keep growing, eating the grower ration and facing
    # grower mortality.
    festival_hold_months: int = Field(default=2, ge=0, le=12)
    # Direct selling/mandi commission on livestock revenue and per-head
    # transport/handling. Both are reported as selling cost, not netted out of
    # the observed market price, so the revenue bridge remains auditable.
    # Telangana 2025-26 defaults: shandy/mandi commission runs 2-4% of sale
    # value (0.03 mid), and hauling a batch to the Navipet Saturday market
    # (shared truck, ~5 AM loading) costs on the order of ₹100/head.
    selling_cost_fraction: FiniteFloat = Field(default=0.03, ge=0.0, le=0.5)
    transport_cost_per_head: FiniteFloat = Field(default=100.0, ge=0.0, le=MAX_MONEY)
    # Optional surplus-milk sale (meat mode). Osmanabadi is a meat breed, but
    # does genuinely yield 0.5-1.5 kg/day over their ~60-90 day lactation, so
    # a farm CAN sell the surplus above the kids' needs. This is the saleable
    # litres per lactating doe per day (0 = nothing sold, the default); the
    # engine multiplies it by the lactating-doe pool, the days in the month
    # and milk_price_per_litre. There is deliberately no lactation curve,
    # fat pricing or calf-milk accounting — this is a meat projection with a
    # small milk side-line, not a dairy model.
    milk_sale_litres_per_doe_day: FiniteFloat = Field(default=0.0, ge=0.0, le=10.0)
    # ₹/litre for the surplus-milk line (~₹30 farm-gate for goat milk sold
    # locally in Telangana).
    milk_price_per_litre: FiniteFloat = Field(default=30.0, ge=0.0, le=MAX_MONEY)
    # ₹, TNAU budgets.
    manure_income_per_adult_per_year: FiniteFloat = Field(default=900.0, ge=0.0, le=MAX_MONEY)

    @field_validator("monthly_meat_price_multipliers")
    @classmethod
    def _bounded_meat_multipliers(cls, value: list[float]) -> list[float]:
        if any(item <= 0.0 or item > MAX_SEASONAL_MULTIPLIER for item in value):
            raise ValueError(f"monthly multipliers must be > 0 and <= {MAX_SEASONAL_MULTIPLIER:g}")
        return value

    @model_validator(mode="after")
    def _fold_breed_price_premium(self) -> "SalesAssumptions":
        # Absorb the premium-breed toggle into the base meat price exactly
        # once. meat_price_per_kg is the single anchor every pricing path
        # multiplies off, so folding keeps the premium compounding coherently
        # with seasonality/escalation/Eid without touching the engine; the
        # min() clamp mirrors the Monte Carlo convention so a near-ceiling
        # price cannot cross MAX_MONEY. Resetting the toggle afterwards makes
        # re-validation of a dumped scenario a no-op (the premium is never
        # applied twice). Cull prices stay separate: they carry their own
        # spent-animal calibration and are not part of the mandi tier the
        # premium prices.
        if self.breed_price_premium_pct != 0.0:
            self.meat_price_per_kg = min(
                float(MAX_MONEY), self.meat_price_per_kg * (1.0 + self.breed_price_premium_pct)
            )
            self.breed_price_premium_pct = 0.0
        return self


class FeedAssumptions(_Group):
    """Dry-matter intake, ration split, feed prices and fodder production."""

    # DMI as a fraction of body weight, per physiological state (ICAR feeding standards).
    dmi_kid_creep: FiniteFloat = Field(default=0.015, gt=0.0, le=0.10)
    dmi_weaner: FiniteFloat = Field(default=0.03, gt=0.0, le=0.10)
    dmi_grower: FiniteFloat = Field(default=0.035, gt=0.0, le=0.10)
    dmi_doe_maintenance: FiniteFloat = Field(default=0.03, gt=0.0, le=0.10)
    dmi_doe_pregnant: FiniteFloat = Field(default=0.035, gt=0.0, le=0.10)
    # Lactating-doe DMI reconciled with the operational feeding module
    # (ICAR/TNAU zone 3.5-4.5% BW; the daily plan dispenses ~3% of BW as the
    # 60:40 mix, and 3.8% DM sits between that and the zone's upper bound).
    dmi_doe_lactating: FiniteFloat = Field(default=0.038, gt=0.0, le=0.10)
    dmi_buck: FiniteFloat = Field(default=0.035, gt=0.0, le=0.10)
    # Per-class concentrate share of DM (ICAR feeding standards / TNAU rations);
    # the remainder is green:dry fodder in a fixed 2:1 DM ratio, so each class's
    # shares sum to 1. Creep feed is mostly concentrate; dry/maintenance does
    # get almost none.
    concentrate_share_kid_creep: FiniteFloat = Field(default=0.60, ge=0.0, le=1.0)
    concentrate_share_weaner: FiniteFloat = Field(default=0.20, ge=0.0, le=1.0)
    concentrate_share_grower: FiniteFloat = Field(default=0.20, ge=0.0, le=1.0)
    concentrate_share_doe_maintenance: FiniteFloat = Field(default=0.05, ge=0.0, le=1.0)
    concentrate_share_doe_pregnant: FiniteFloat = Field(default=0.15, ge=0.0, le=1.0)
    concentrate_share_doe_lactating: FiniteFloat = Field(default=0.20, ge=0.0, le=1.0)
    concentrate_share_buck: FiniteFloat = Field(default=0.15, ge=0.0, le=1.0)
    # DM content of each feed, as-fed basis.
    green_dm_pct: FiniteFloat = Field(default=0.25, gt=0.0, le=1.0)
    dry_dm_pct: FiniteFloat = Field(default=0.88, gt=0.0, le=1.0)
    concentrate_dm_pct: FiniteFloat = Field(default=0.90, gt=0.0, le=1.0)
    # Prices per kg as-fed (₹). ``green_price_per_kg`` is the home-grown
    # production cost; a physical shortfall is purchased at the separate market
    # price. Keeping them separate fixes the old model's economically impossible
    # result where zero acres and ample fodder land had exactly the same cost.
    green_price_per_kg: FiniteFloat = Field(default=1.0, ge=0.0, le=MAX_MONEY)
    purchased_green_price_per_kg: FiniteFloat = Field(default=2.5, ge=0.0, le=MAX_MONEY)
    dry_price_per_kg: FiniteFloat = Field(default=5.0, ge=0.0, le=MAX_MONEY)  # paddy straw ₹4-6/kg
    concentrate_price_per_kg: FiniteFloat = Field(
        default=25.0, ge=0.0, le=MAX_MONEY
    )  # commercial goat feed ₹22-28
    annual_feed_price_growth_rate: FiniteFloat = Field(default=0.04, gt=-1.0, le=1.0)
    monthly_green_price_multipliers: list[FiniteFloat] = Field(
        default_factory=lambda: [1.0] * 12,
        min_length=12,
        max_length=12,
    )
    monthly_dry_price_multipliers: list[FiniteFloat] = Field(
        default_factory=lambda: [1.0] * 12,
        min_length=12,
        max_length=12,
    )
    monthly_concentrate_price_multipliers: list[FiniteFloat] = Field(
        default_factory=lambda: [1.0] * 12,
        min_length=12,
        max_length=12,
    )
    # Fraction of total DM obtained free from grazing (0 = stall-fed, ~0.3 semi-intensive).
    grazing_dm_fraction: FiniteFloat = Field(default=0.0, ge=0.0, le=1.0)
    # Cultivated fodder: area and DM yield (6 t DM/acre/yr ≈ hybrid napier/lucerne, TNAU).
    # 3 acres covers ~two-thirds of a 50+2 doe unit's green-DM need at home
    # cost (₹1/kg vs ₹2.5/kg purchased) — the Navipet site plan grows fodder
    # and tops up; the old 0-acre default priced every green kilogram at the
    # purchased rate for a decade, which no stall-fed unit with land does.
    cultivated_fodder_acres: FiniteFloat = Field(default=3.0, ge=0.0, le=MAX_FODDER_ACRES)
    # Floor via MIN_FODDER_YIELD_T (RT-L8-4): engine division cannot tell a
    # rounding artifact from an agronomic claim, so the bound must.
    fodder_yield_t_dm_per_acre_year: FiniteFloat = Field(
        default=6.0, ge=MIN_FODDER_YIELD_T, le=MAX_FODDER_YIELD_T
    )
    # Monthly production factors (average 1.0 is a full stated annual yield).
    # They need not sum to 12: the engine normalizes them, so the annual yield
    # remains exactly the user's stated agronomic assumption.
    monthly_fodder_yield_multipliers: list[FiniteFloat] = Field(
        default_factory=lambda: [1.0] * 12,
        min_length=12,
        max_length=12,
    )
    initial_fodder_stock_kg_dm: FiniteFloat = Field(default=0.0, ge=0.0, le=MAX_MONEY)
    fodder_storage_capacity_kg_dm: FiniteFloat = Field(default=0.0, ge=0.0, le=MAX_MONEY)
    fodder_storage_loss_fraction_monthly: FiniteFloat = Field(default=0.02, ge=0.0, le=1.0)
    # Water demand (litres/head/day) by class, so the resource plan prices
    # water alongside fodder. ICAR/National Farm Innovations guidance for
    # Deccan conditions: adults 5-10 L/day, but a LACTATING doe in the
    # Telangana summer genuinely drinks 10-15 L/day — the old plans that
    # carried only fodder acreage hid the single hardest summer constraint.
    # These are planning means; the engine reports monthly litres and the
    # peak daily demand by class.
    water_litres_kid_per_day: FiniteFloat = Field(default=2.0, ge=0.0, le=50.0)
    water_litres_weaner_per_day: FiniteFloat = Field(default=4.0, ge=0.0, le=50.0)
    water_litres_grower_per_day: FiniteFloat = Field(default=6.0, ge=0.0, le=50.0)
    water_litres_doe_per_day: FiniteFloat = Field(default=8.0, ge=0.0, le=50.0)
    water_litres_lactating_doe_per_day: FiniteFloat = Field(default=12.0, ge=0.0, le=50.0)
    water_litres_buck_per_day: FiniteFloat = Field(default=9.0, ge=0.0, le=50.0)

    @field_validator(
        "monthly_green_price_multipliers",
        "monthly_dry_price_multipliers",
        "monthly_concentrate_price_multipliers",
        "monthly_fodder_yield_multipliers",
    )
    @classmethod
    def _bounded_monthly_multipliers(cls, value: list[float]) -> list[float]:
        if any(item <= 0.0 or item > MAX_SEASONAL_MULTIPLIER for item in value):
            raise ValueError(f"monthly multipliers must be > 0 and <= {MAX_SEASONAL_MULTIPLIER:g}")
        return value

    @model_validator(mode="after")
    def _initial_fodder_fits_storage(self) -> "FeedAssumptions":
        if self.initial_fodder_stock_kg_dm > self.fodder_storage_capacity_kg_dm:
            raise ValueError("initial_fodder_stock_kg_dm must be <= fodder_storage_capacity_kg_dm")
        return self


class CostsAssumptions(_Group):
    """Recurring and capital costs (₹)."""

    # Private-practice retainer + vaccines + dewormers, 2025-26 Telangana
    # rates (₹400-600/head/yr; the old ₹250 was a pre-2020 NABARD table).
    vet_per_animal_per_year: FiniteFloat = Field(default=450.0, ge=0.0, le=MAX_MONEY)
    # Telangana 2025-26 statutory floor: unskilled monthly minimum ₹14,000
    # (Zone III) to ₹16,000 (Zone I) under the state's comprehensive minimum
    # wage fixation; a full-time livestock attendant earns the floor to
    # floor+skill. The old ₹10,000 was a pre-revision NABARD table figure.
    labour_per_month: FiniteFloat = Field(default=14000.0, ge=0.0, le=MAX_MONEY)
    # One labourer per this many head; labour count scales up with herd size.
    # TNAU/NABARD stall-fed budgets staff roughly one worker per 50 goats;
    # semi-intensive herds handle more. 60 splits the difference for a
    # Navipet-style semi-intensive unit (the old 75 assumed grazing herds).
    # The bound exists only so an arbitrary-size JSON integer cannot raise
    # OverflowError in the engine's float division — this is a divisor, not a
    # herd size: values far above MAX_HEAD are meaningful ("never scale
    # labour with head count") and were accepted before any bound existed, so
    # persisted scenarios carry them. 10**15 stays float-exact (< 2**53) and
    # grandfathers every previously-runnable stored value.
    labour_per_head_threshold: int = Field(default=60, ge=1, le=MAX_LABOUR_PER_HEAD_THRESHOLD)
    # Smallholder/hobby flocks are family-run: no hired attendant is paid.
    # True zeroes the CASH labour line (IRR-style metrics keep working off
    # the actual cash flows) while the narrative report discloses the market
    # wage the family's own labour forgoes. Labour units also scale in
    # half-attendant steps (ceil(2 x does / threshold) / 2, floored at 0.5
    # for any non-empty flock): a 3-doe hobby flock books a half-time
    # attendant, not a full ₹14,000/month hire.
    family_labour: bool = False
    # Breeding does/bucks bought during the projection are capitalized and
    # depreciated straight-line over this horizon (5 years: a young proven
    # doe bought at ~18 months is culled near the 72-month max age, so her
    # breeding life is ~5 asset years — NABARD goat-unit costing treats the
    # breeding herd as a 5-year productive asset). The cash outlay still hits
    # the purchase month; only the P&L (EBITDA/tax/DSCR) spreads the cost.
    breeding_stock_useful_life_months: int = Field(default=60, ge=1, le=240)
    insurance_pct_stock_value_annual: FiniteFloat = Field(default=0.04, ge=0.0, le=0.25)
    misc_overhead_per_month: FiniteFloat = Field(default=2000.0, ge=0.0, le=MAX_MONEY)
    # Labour, vet and misc overheads escalate with general inflation. The
    # meat/milk/feed price series all grow 4-6%/yr, so a 0% default let every
    # cost line sit still for a decade while revenue compounded. 5%
    # matches the recent Indian CPI trend these lines actually track.
    operating_cost_growth_rate_annual: FiniteFloat = Field(default=0.05, gt=-1.0, le=1.0)
    # 2025-26 construction rates for a raised-floor stall-fed goat shed
    # (₹5,500-7,000/place; the old ₹4,500 was a pre-2020 NABARD unit cost).
    shed_cost_per_animal_place: FiniteFloat = Field(default=6000.0, ge=0.0, le=MAX_MONEY)
    equipment_cost_per_animal: FiniteFloat = Field(default=500.0, ge=0.0, le=MAX_MONEY)
    # Capacity can be driven by the projected physical peak (recommended), a
    # user-approved plan, or the opening herd for legacy comparisons.
    capacity_basis: Literal["projected_peak", "planned", "opening_herd"] = "projected_peak"
    planned_capacity_head: int = Field(default=0, ge=0, le=MAX_HEAD)
    capacity_buffer_fraction: FiniteFloat = Field(default=0.10, ge=0.0, le=1.0)
    shed_useful_life_years: int = Field(default=20, ge=1, le=100)
    equipment_useful_life_years: int = Field(default=7, ge=1, le=50)
    shed_residual_fraction: FiniteFloat = Field(default=0.10, ge=0.0, le=1.0)
    equipment_residual_fraction: FiniteFloat = Field(default=0.05, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _planned_capacity_is_present(self) -> "CostsAssumptions":
        if self.capacity_basis == "planned" and self.planned_capacity_head <= 0:
            raise ValueError("planned_capacity_head must be > 0 when capacity_basis is 'planned'")
        return self


class FinanceAssumptions(_Group):
    """Project financing (NABARD refinance structure)."""

    # Explicit stock cost (₹); 0 = auto-computed from starting herd counts × prices.
    initial_stock_cost: FiniteFloat = Field(default=0.0, ge=0.0, le=MAX_MONEY)
    loan_fraction_of_project_cost: FiniteFloat = Field(default=0.85, ge=0.0, le=1.0)
    interest_rate_annual: FiniteFloat = Field(default=0.11, ge=0.0, le=0.5)
    loan_term_months: int = Field(default=72, ge=1, le=180)
    # Interest-only period at the start of the loan (NABARD goat schemes: ~12 months).
    moratorium_months: int = Field(default=12, ge=0, le=60)
    # Capital subsidy as a fraction of project cost; reduces the promoter's equity.
    subsidy_fraction: FiniteFloat = Field(default=0.0, ge=0.0, le=0.9)
    # National Livestock Mission (NLM) goat-unit toggle: when on, the engine
    # replaces subsidy_fraction with the scheme's 50% back-ended capital
    # subsidy, capped per unit size (eligible capital ~₹10,000 per breeding
    # head — the published bands run from a 100F+5M unit's ~₹10 lakh up to a
    # 500F+25M unit's ~₹50 lakh; shed, animals, fodder, equipment and
    # insurance are all eligible). The subsidy is further capped so loan +
    # subsidy never exceed the project cost (equity stays non-negative).
    nlm_subsidy: bool = False
    discount_rate_annual: FiniteFloat = Field(default=0.12, ge=0.0, le=0.5)
    # Months of operating cost held as working capital inside the project
    # cost. A breeding-start unit sells its first animal around month 11-12
    # (settle, breed, 5-month gestation, 10-month growth), so the NABARD
    # convention of a full carryover year — the same 12 months as the loan
    # moratorium — is the honest default. The old 3 left a ~₹5.5 lakh Y1 cash
    # hole that only surfaced in minimum_cash_balance.
    working_capital_months: int = Field(default=12, ge=0, le=24)
    # Tax is configurable rather than hard-coded: farm/entity tax treatment is
    # jurisdiction- and structure-specific. Straight-line depreciation is used
    # for the model accounts and tax shield.
    income_tax_rate: FiniteFloat = Field(default=0.0, ge=0.0, le=0.6)
    tax_loss_carryforward: bool = True
    # A continuing-business forecast still owns livestock, facilities and the
    # working-capital reserve at its horizon. Their recoverable value prevents
    # a short forecast from pretending every closing asset disappears.
    include_terminal_value: bool = True
    terminal_livestock_realization_fraction: FiniteFloat = Field(default=0.90, ge=0.0, le=1.0)
    terminal_asset_realization_fraction: FiniteFloat = Field(default=1.0, ge=0.0, le=1.0)
    terminal_working_capital_recovery_fraction: FiniteFloat = Field(default=1.0, ge=0.0, le=1.0)
    # Used by modified IRR, which remains meaningful for cash-flow patterns
    # where ordinary IRR is ambiguous or has multiple roots.
    reinvestment_rate_annual: FiniteFloat = Field(default=0.08, ge=0.0, le=0.5)

    @model_validator(mode="after")
    def _financing_is_coherent(self) -> "FinanceAssumptions":
        # Loan + subsidy above 100% of the project cost makes the equity
        # negative — the promoter is paid to take the project, which inverts
        # every return metric (month-0 inflow, garbage IRR, instant payback).
        if self.loan_fraction_of_project_cost + self.subsidy_fraction > 1.0:
            raise ValueError(
                "loan_fraction_of_project_cost + subsidy_fraction must be <= 1.0 "
                "(equity cannot be negative)"
            )
        # A moratorium covering the whole term leaves zero EMI months: the
        # schedule pays interest only and the principal silently vanishes from
        # every cash flow.
        if self.moratorium_months >= self.loan_term_months:
            raise ValueError(
                "moratorium_months must be shorter than loan_term_months "
                "(otherwise the principal is never repaid)"
            )
        return self


class RiskVariable(_Group):
    """Triangular spread (multiplier on the base value) for one Monte Carlo variable."""

    enabled: bool = True
    low: FiniteFloat = Field(default=0.8, gt=0.0, le=MAX_RISK_MULTIPLIER)
    high: FiniteFloat = Field(default=1.2, gt=0.0, le=MAX_RISK_MULTIPLIER)

    @model_validator(mode="after")
    def _brackets_mode(self) -> "RiskVariable":
        # The Monte Carlo draws rng.triangular(low, high, 1.0) — the mode is
        # fixed at the base value, so a spread that doesn't bracket 1.0 would
        # silently draw garbage.
        if not self.low <= 1.0 <= self.high:
            raise ValueError("low and high must bracket the base multiplier 1.0")
        return self


class RiskAssumptions(_Group):
    """Monte Carlo controls and per-variable triangular spreads."""

    monte_carlo_runs: int = Field(default=500, ge=1, le=2000)
    # Bounded so the value survives a browser round-trip: OpenAPI "integer"
    # becomes a TypeScript number, and anything past 2**53 silently changes
    # when the page loads a scenario and saves it back, quietly reseeding the
    # run. 2**31-1 is well inside the exactly-representable range.
    seed: int = Field(default=42, ge=0, le=2**31 - 1)
    meat_price: RiskVariable = Field(default_factory=lambda: RiskVariable(low=0.80, high=1.20))
    # Realized price of the optional surplus-milk side-line (local goat-milk
    # sales are thin, negotiated sales, so the spread stays tight).
    milk_price: RiskVariable = Field(default_factory=lambda: RiskVariable(low=0.85, high=1.15))
    feed_price: RiskVariable = Field(default_factory=lambda: RiskVariable(low=0.90, high=1.25))
    adult_mortality: RiskVariable = Field(default_factory=lambda: RiskVariable(low=0.60, high=1.80))
    kid_mortality: RiskVariable = Field(default_factory=lambda: RiskVariable(low=0.60, high=1.60))
    litter_size: RiskVariable = Field(default_factory=lambda: RiskVariable(low=0.85, high=1.10))
    conception_rate: RiskVariable = Field(default_factory=lambda: RiskVariable(low=0.80, high=1.05))
    fodder_yield: RiskVariable = Field(default_factory=lambda: RiskVariable(low=0.65, high=1.10))
    operating_cost: RiskVariable = Field(default_factory=lambda: RiskVariable(low=0.90, high=1.20))
    # Strength of the model's documented market, climate and disease latent
    # factors. A Gaussian copula maps those correlated factors into each
    # triangular marginal without changing its low/mode/high definition.
    correlation_strength: FiniteFloat = Field(default=0.60, ge=0.0, le=0.95)
    # Within-run annual price variation: when True, each Monte Carlo run adds
    # a mean-reverting AR(1) annual shock process on log meat and purchased
    # feed prices AROUND that run's drawn level, so a single run lives through
    # good and bad price years instead of one flat multiplier for the whole
    # horizon. The persistent (between-run) and annual (within-run)
    # components split the configured triangular variance 50/50, so the
    # marginal spread of realized annual multipliers still matches the
    # configured low/mode/high risk definition. False restores the legacy
    # single whole-horizon multiplier per run.
    within_run_price_variation: bool = True
    # Mean-reversion strength of that annual price process: 0 = independent
    # annual shocks, -> 1 = the first drawn year persists. 0.3 keeps a mild
    # year-to-year hangover (a drought/feed-price year carries into the next)
    # while reverting toward the run's own level.
    price_process_rho: FiniteFloat = Field(default=0.30, ge=0.0, le=0.95)
    disease_outbreak_probability_annual: FiniteFloat = Field(default=0.10, ge=0.0, le=1.0)
    disease_outbreak_duration_months: int = Field(default=3, ge=1, le=24)
    disease_adult_mortality_multiplier: FiniteFloat = Field(default=2.0, ge=1.0, le=20.0)
    disease_kid_mortality_multiplier: FiniteFloat = Field(default=2.5, ge=1.0, le=20.0)
    disease_conception_multiplier: FiniteFloat = Field(default=0.70, gt=0.0, le=1.0)
    # FMD/LSD outbreaks cut yield 14-25% on affected farms for the episode.
    disease_milk_yield_multiplier: FiniteFloat = Field(default=0.85, gt=0.0, le=1.0)
    drought_probability_annual: FiniteFloat = Field(default=0.15, ge=0.0, le=1.0)
    drought_duration_months: int = Field(default=4, ge=1, le=24)
    drought_fodder_yield_multiplier: FiniteFloat = Field(default=0.50, gt=0.0, le=1.0)
    drought_feed_price_multiplier: FiniteFloat = Field(default=1.30, ge=1.0, le=20.0)
    market_crash_probability_annual: FiniteFloat = Field(default=0.10, ge=0.0, le=1.0)
    market_crash_duration_months: int = Field(default=3, ge=1, le=24)
    market_crash_price_multiplier: FiniteFloat = Field(default=0.75, gt=0.0, le=1.0)


class OptimizationAssumptions(_Group):
    """Bounded decision search and lender-style feasibility constraints."""

    objective: Literal["balanced", "npv", "liquidity"] = "balanced"
    max_candidates: int = Field(default=120, ge=1, le=300)
    minimum_dscr: FiniteFloat = Field(default=1.20, ge=0.0, le=10.0)
    # None = no ceiling. MAX_MONEY is a *per-input* magnitude cap; reusing it as
    # the "unconstrained" sentinel silently failed every candidate for a large
    # farm, whose derived project cost is a product of several MAX_MONEY-bounded
    # inputs and legitimately exceeds it.
    maximum_project_cost: FiniteFloat | None = Field(default=None, ge=0.0)
    maximum_funding_gap: FiniteFloat | None = Field(default=None, ge=0.0)
    doe_scale_low: FiniteFloat = Field(default=0.75, gt=0.0, le=5.0)
    doe_scale_high: FiniteFloat = Field(default=1.25, gt=0.0, le=5.0)
    doe_scale_steps: int = Field(default=3, ge=1, le=9)
    sale_age_radius_months: int = Field(default=2, ge=0, le=9)
    retention_step: FiniteFloat = Field(default=0.25, ge=0.0, le=1.0)
    loan_fraction_step: FiniteFloat = Field(default=0.15, ge=0.0, le=1.0)
    # Integer-axis radii around the submitted policy: the search also tries
    # +/- this many festival-hold months and +/- this many services on the
    # repeat-breeder cull cap (clamped to the schema bounds; 0 pins the axis).
    # Both are decisions a Navipet farmer actually makes — holding bucks into
    # Bakrid and how long to persist with a repeat-breeder doe.
    festival_hold_radius_months: int = Field(default=1, ge=0, le=6)
    service_cull_radius_months: int = Field(default=1, ge=0, le=6)

    @model_validator(mode="after")
    def _range_order(self) -> "OptimizationAssumptions":
        if self.doe_scale_low > self.doe_scale_high:
            raise ValueError("doe_scale_low must be <= doe_scale_high")
        return self


class HerdEventAssumptions(_Group):
    """One scheduled herd event: a purchase or sale applied at the start of a
    simulation month (before that month's aging, breeding and mortality).

    Purchases are charged as operating cost in that month (not added to the
    project cost); sales are booked as meat/cull revenue. ``price_per_head``
    overrides the default valuation — purchases default to the class purchase
    price (adults) or live-weight meat value (young stock); sales default to
    live-weight meat value for young stock and cull value for adults.

    ``age_months`` is the arrival age of purchased young stock: it places the
    animals at the matching slot of their class's age chain (and prices them
    at that age's live weight) instead of the mid-class default — a 6-month-
    old grower has another ~6 months to the breeding gate, not ~3. ``None``
    keeps the historical mid-class placement. Adults are already aged by
    their own machinery (the foundation doe-age window), so the field is
    rejected for doe/buck events and for sales; the per-class bounds are
    validated in ``SimulationAssumptions`` (they depend on afb/sale age).
    """

    month: int = Field(ge=1)  # 1-based simulation month; <= meta.horizon_months
    kind: Literal["purchase", "sale"]
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
    count: FiniteFloat = Field(gt=0.0, le=MAX_HEAD)  # head (expected-value float, like all counts)
    # ₹; None = default valuation.
    price_per_head: FiniteFloat | None = Field(default=None, ge=0.0, le=MAX_MONEY)
    # Arrival age of purchased young stock; None = mid-class placement.
    # Schema bound only — the class-specific chain bounds (kid 0-2, weaner
    # 3-5, grower 6..chain end) are cross-validated against afb/sale age.
    age_months: int | None = Field(default=None, ge=0, le=30)


# Dairy/buffalo assumption keys retired when the model went goat-meat-only
# (see SimulationAssumptions._drop_retired_dairy_fields). Kept as data, not
# scattered string literals, so the API contract change is auditable.
_RETIRED_DAIRY_FIELDS: dict[str, frozenset[str]] = {
    "reproduction": frozenset(
        {
            "sexed_semen_services",
            "sexed_female_fraction",
            "sexed_conception_multiplier",
        }
    ),
    "sales": frozenset(
        {
            "lactation_milk_litres",
            "calf_milk_litres_per_day_per_calf",
            "milk_price_per_kg_fat",
            "milk_fat_pct",
            "milk_persistency_monthly",
            "milk_curve_shape",
            "milk_peak_day",
            "monthly_milk_yield_multipliers",
            "monthly_milk_price_multipliers",
            "annual_milk_price_growth_rate",
            "male_calf_sell_at_birth_fraction",
            "male_calf_price_per_head",
        }
    ),
}


class SimulationAssumptions(_Group):
    """Full assumption set; ``SimulationAssumptions()`` is a valid default run."""

    meta: MetaAssumptions = Field(default_factory=MetaAssumptions)
    herd: HerdAssumptions = Field(default_factory=HerdAssumptions)
    reproduction: ReproductionAssumptions = Field(default_factory=ReproductionAssumptions)
    mortality: MortalityAssumptions = Field(default_factory=MortalityAssumptions)
    culling: CullingAssumptions = Field(default_factory=CullingAssumptions)
    growth: GrowthAssumptions = Field(default_factory=GrowthAssumptions)
    sales: SalesAssumptions = Field(default_factory=SalesAssumptions)
    feed: FeedAssumptions = Field(default_factory=FeedAssumptions)
    costs: CostsAssumptions = Field(default_factory=CostsAssumptions)
    finance: FinanceAssumptions = Field(default_factory=FinanceAssumptions)
    risk: RiskAssumptions = Field(default_factory=RiskAssumptions)
    optimization: OptimizationAssumptions = Field(default_factory=OptimizationAssumptions)
    # Bounded like every other list input: an unbounded events payload is an
    # unbounded work/payload vector.
    events: list[HerdEventAssumptions] = Field(default_factory=list, max_length=MAX_PLAN_EVENTS)

    @model_validator(mode="before")
    @classmethod
    def _drop_retired_dairy_fields(cls, data: object) -> object:
        # The dairy/buffalo machinery was removed from this goat-meat model
        # (model 3.3.0). Stored scenarios and older frontends still carry the
        # retired keys; strip them so those payloads keep validating instead
        # of dying on the sub-models' extra="forbid". Any OTHER unknown key
        # still fails loudly, so typo protection is unchanged.
        if not isinstance(data, dict):
            return data
        data = {**data}
        for group, retired in _RETIRED_DAIRY_FIELDS.items():
            sub = data.get(group)
            if isinstance(sub, dict):
                data[group] = {key: value for key, value in sub.items() if key not in retired}
        return data

    @model_validator(mode="after")
    def _events_within_horizon(self) -> "SimulationAssumptions":
        for event in self.events:
            if event.month > self.meta.horizon_months:
                raise ValueError(
                    f"event month {event.month} exceeds the simulation horizon "
                    f"({self.meta.horizon_months} months)"
                )
        return self

    @model_validator(mode="after")
    def _event_age_within_class_chain(self) -> "SimulationAssumptions":
        afb = self.reproduction.age_at_first_breeding_months
        sale_age = self.growth.sale_age_months
        # Inclusive age bounds of each young-stock class's engine chain. The
        # grower chains end one month short of graduation (afb / sale age);
        # when graduation is at 6 the chain is empty and the boundary stock
        # is exactly that age.
        chain_bounds = {
            "female_kid": (0, 2),
            "male_kid": (0, 2),
            "female_weaner": (3, 5),
            "male_weaner": (3, 5),
            "female_grower": (6, max(6, afb - 1)),
            "male_grower": (6, max(6, sale_age - 1)),
        }
        for event in self.events:
            if event.age_months is None:
                continue
            if event.kind != "purchase":
                raise ValueError("event age_months is only meaningful for purchases")
            bounds = chain_bounds.get(event.animal_class)
            if bounds is None:
                raise ValueError(
                    f"event age_months is only meaningful for young-stock classes, "
                    f"not {event.animal_class}"
                )
            lo, hi = bounds
            if not lo <= event.age_months <= hi:
                raise ValueError(
                    f"event age_months {event.age_months} is outside the "
                    f"{event.animal_class} chain ({lo}-{hi} months)"
                )
        return self

    @model_validator(mode="after")
    def _festival_months_within_horizon(self) -> "SimulationAssumptions":
        if self.sales.festival_sale_months is None:
            # Every scenario is a meat scenario now, so the Bakrid calendar
            # auto-fills for the run's own horizon. Lazy import: market
            # imports this module, so a top-level import would be circular.
            from .market import bakrid_festival_months

            self.sales.festival_sale_months = bakrid_festival_months(
                self.meta.start_year_month, self.meta.horizon_months
            )
        if len(set(self.sales.festival_sale_months)) != len(self.sales.festival_sale_months):
            raise ValueError("sales.festival_sale_months must not contain duplicates")
        for month in self.sales.festival_sale_months:
            if month < 1:
                raise ValueError(
                    "sales.festival_sale_months must contain 1-based months inside the horizon"
                )
        # Months beyond the horizon are pruned, not rejected: they come from
        # the Bakrid calendar pre-filled for a 10-year horizon, and shrinking
        # the horizon to "see the first two years" must not turn the preset
        # into a 422 the user has to debug. A festival past the horizon simply
        # is not part of that plan.
        self.sales.festival_sale_months = [
            month for month in self.sales.festival_sale_months if month <= self.meta.horizon_months
        ]
        return self

    @model_validator(mode="after")
    def _breeding_age_within_doe_lifespan(self) -> "SimulationAssumptions":
        # The engine tracks doe ages in an array of max_doe_age_months + 1
        # slots and writes every doe entering the pool at index
        # age_at_first_breeding_months — afb beyond the max age indexes out of
        # range mid-run (and a doe culled before she can first breed is a
        # broken model anyway). The per-field floors already imply this
        # (afb <= 30 < 36 <= max_doe_age); the validator pins the invariant
        # against future bound changes.
        if self.reproduction.age_at_first_breeding_months > self.culling.max_doe_age_months:
            raise ValueError(
                "reproduction.age_at_first_breeding_months must be <= culling.max_doe_age_months"
            )
        return self

    @model_validator(mode="after")
    def _adult_weight_above_yearling(self) -> "SimulationAssumptions":
        # weight_at_age linearly interpolates from weight_by_age_months[12]
        # toward the adult weight between ages 13 and adult_weight_age_months;
        # an adult weight BELOW the yearling weight yields a monotonically
        # DECREASING curve past age 12 (a 20.5 kg yearling shrinking toward a
        # lighter adult) — no crash, but every feed/sale/insurance figure for
        # that class becomes nonsensical.
        table = self.growth.weight_by_age_months
        if len(table) > 12:
            yearling = max(table[:13])
            if self.growth.adult_weight_doe_kg < yearling:
                raise ValueError(
                    f"growth.adult_weight_doe_kg ({self.growth.adult_weight_doe_kg}) "
                    f"must be >= the max weight in weight_by_age_months[0..12] ({yearling})"
                )
            if self.growth.adult_weight_buck_kg < yearling:
                raise ValueError(
                    f"growth.adult_weight_buck_kg ({self.growth.adult_weight_buck_kg}) "
                    f"must be >= the max weight in weight_by_age_months[0..12] ({yearling})"
                )
        return self
