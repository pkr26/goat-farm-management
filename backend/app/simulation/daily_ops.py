"""Buckets & Tasks daily operations simulation (goats, v1).

The planning engine (``engine.py``) answers money questions in monthly
expected values. This module answers the *operational* question the farm
lives every morning: with this herd standing in these buckets — one building
per bucket, each with its dedicated vet area — what exactly happens on day
N? Which feed is mixed and delivered where, which pens are cleaned when,
which duties the vet / mover / feeder / cleaner crews perform, and which
animals move between buildings and why.

Every rule is reused from the operational single sources of truth — never
restated:

* bucket graph + move contexts  → ``models.lifecycle.LEGAL_BUCKET_TRANSITIONS``
* biology timings               → ``models.species.GOAT_PROFILE``
  (gestation 150d, scan +32d, pregnancy-late day 100, prepartum lead 15d,
  weaning day 60, postpartum recovery 14d, 2 failed services → cull)
* feed recipes per bucket       → ``models.feed_rules.recipe_for_context``
* per-head rations + buildings  → ``models.feed_rules.GOAT_BUCKET_KG_PER_HEAD``
  / ``GOAT_BUILDING_NAMES`` (mirroring the seeded goat BucketDefinitions)
* shift split 40/20/40          → ``models.constants.SHIFT_SPLIT``
* quarantine protocol days      → ``models.helpers.QUARANTINE_PROTOCOL``
* task → crew routing           → ``permissions.TASK_CATEGORY_ROLE_MAP``

Determinism: one ``random.Random(seed)``; stochastic draws (conception,
litter, kid sex, stillbirth, abortion, mortality) happen in a fixed phase
order over animals sorted by tag, so a given input always replays identically
— the property the manual-verification ledger (``build_daily_ledger``) and
the hand-derived golden tests rely on.

Day model: days are 1-based; day ``d``'s date is ``start_date + (d-1)``.
Each day executes seven phases, in order:

1. Morning feed 06:30 — mixing manifest per recipe, then one delivery task
   per (building, recipe) with the 40% share.
2. Morning cleaning 07:15 — every occupied building, plus a cleaner-manager
   verification duty (CLEANING is a verification-required category).
3. Due-duties round 09:00 — quarantine protocol steps, pregnancy checks
   (+32d, with the conception draw), pre-kidding vaccines (EKD−40 / −25),
   the move to DELIVERY (EKD−15) and the kidding-due watch (EKD).
4. Lifecycle events — breeding-readiness moves and services, kidding,
   weaning (day 60), postpartum moves (day 14), mortality, age culls and
   male-kid sales (8 months).
5. Afternoon feed 13:30 — the 20% share.
6. Night feed 19:30 — the 40% share.
7. Night cleaning 20:15 — every occupied building, plus verification.

Goat farms only in v1: kids stay with the dam in RECOVERY until weaning and
there is no milking string. Weight-gated rules (≥22 kg breeding weight,
24–28 kg sale weight) degrade to their age proxies; that limitation is
echoed in the result notes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from random import Random
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..models.constants import BUCK_DOE_RATIO, MEAT_SALE_AGE_MONTHS, SHIFT_SPLIT
from ..models.enums import Bucket, FeedingShift
from ..models.feed_rules import (
    CREEP_KG_PER_HEAD,
    DRY_ROUGHAGE,
    GOAT_BUCKET_KG_PER_HEAD,
    GOAT_BUILDING_NAMES,
    RECIPE_DISPLAY,
    recipe_for_context,
)
from ..models.helpers import QUARANTINE_PROTOCOL
from ..models.lifecycle import LEGAL_BUCKET_TRANSITIONS
from ..models.species import GOAT_PROFILE
from ..permissions import TASK_CATEGORY_ROLE_MAP
from .feed import DAYS_PER_MONTH
from .results import MetricExplanation

DAILY_OPS_MODEL_VERSION = "1.0.0"

_PROFILE = GOAT_PROFILE

# --- day schedule labels -----------------------------------------------------
TIME_MORNING_FEED = "06:30"
TIME_MORNING_CLEAN = "07:15"
TIME_DUTIES = "09:00"
TIME_AFTERNOON_FEED = "13:30"
TIME_NIGHT_FEED = "19:30"
TIME_NIGHT_CLEAN = "20:15"

# Heat-cycle wait after a failed pregnancy check before the doe is re-served
# (operational does return to service on the next observed heat).
_HEAT_CYCLE_DAYS = 21
# Resting / dry-off + flush stay before returning to the breeding bucket
# (seeded RESTING bucket rule: "Post-weaning does, ~30 days").
_RESTING_FLUSH_DAYS = 30
# Maximum doe age before the age cull fires (mirrors CullingAssumptions
# default max_doe_age_months used by the monthly engine).
_MAX_DOE_AGE_MONTHS = 72

MAX_START_HEAD = 500
MAX_HORIZON_DAYS = 365
MIN_HORIZON_DAYS = 7
_MAX_AGE_MONTHS_START = 240  # models.constants.MAX_AGE_MONTHS parity

_BUCKET_CODES = tuple(member.value for member in Bucket)

BucketCode = Literal[
    "QUARANTINE",
    "FOUNDATION",
    "BREEDING",
    "PREGNANCY_EARLY",
    "PREGNANCY_LATE",
    "DELIVERY",
    "RECOVERY",
    "RESTING",
    "MALE_KIDS",
    "FEMALE_KIDS",
]

FEMALE_ONLY_BUCKETS = frozenset(
    {
        Bucket.FEMALE_KIDS.value,
        Bucket.RESTING.value,
        Bucket.PREGNANCY_EARLY.value,
        Bucket.PREGNANCY_LATE.value,
        Bucket.DELIVERY.value,
    }
)
MALE_ONLY_BUCKETS = frozenset({Bucket.MALE_KIDS.value})
PREGNANT_BUCKETS = frozenset(
    {
        Bucket.PREGNANCY_EARLY.value,
        Bucket.PREGNANCY_LATE.value,
        Bucket.DELIVERY.value,
    }
)
# Buckets a doe may occupy while awaiting her pregnancy check (bred but not
# yet scanned): only BREEDING, because the scan itself moves her out.
UNSCANNED_BUCKETS = frozenset({Bucket.BREEDING.value})

# The feed-mixing station tasks are attributed to (not a bucket building).
FEED_STORE = "FEED_STORE"
FEED_STORE_NAME = "Feed Store & Mixing Area"


def _role_for(category: str) -> str:
    return TASK_CATEGORY_ROLE_MAP.get(category, "MANAGER")


def _daily_hazard(whole_period_rate: float, period_days: int) -> float:
    """Per-day probability that reproduces ``whole_period_rate`` over the
    period (constant hazard), so defaults stay anchored to the operational
    phase figures (15% kid loss over the 60-day pre-weaning window, 5% adult
    mortality per year)."""
    if whole_period_rate <= 0.0:
        return 0.0
    if whole_period_rate >= 1.0:
        return 1.0
    return 1.0 - math.pow(1.0 - whole_period_rate, 1.0 / period_days)


def _litter_probabilities(mean: float) -> tuple[float, float, float, float]:
    """Probability ladder for litter sizes 1..4 with exactly ``mean`` expected
    kids: piecewise-linear between the single-size corners, so ``1.6`` (the
    Osmanabadi default) means 40% singles / 60% twins and never invents a
    distribution the caller did not ask for."""
    mean = min(4.0, max(1.0, mean))
    if mean <= 2.0:
        return (2.0 - mean, mean - 1.0, 0.0, 0.0)
    if mean <= 3.0:
        return (0.0, 3.0 - mean, mean - 2.0, 0.0)
    return (0.0, 0.0, 4.0 - mean, mean - 3.0)


# --- inputs -------------------------------------------------------------------


class AnimalStartSpec(BaseModel):
    """One animal standing in a bucket on day 1."""

    model_config = ConfigDict(extra="forbid", strict=True)

    tag: str = Field(min_length=1, max_length=50)
    sex: Literal["M", "F"]
    bucket: BucketCode
    age_months: int = Field(ge=0, le=_MAX_AGE_MONTHS_START)
    # Days already spent in ``bucket`` before day 1 (drives the date-sensitive
    # rules: quarantine protocol steps, resting flush, quarantine dry-roughage).
    days_in_bucket: int = Field(default=0, ge=0, le=3650)
    # Days since this doe's service, measured on day 1 (i.e. gestation day).
    # None = not bred. Required for the pregnant buckets; for BREEDING it
    # places her between 0 and 31 gestation days (awaiting the +32d scan).
    bred_days_ago: int | None = Field(default=None, ge=0, le=_PROFILE.gestation_days)
    # Marks an unweaned kid housed with its dam in RECOVERY (goat farms):
    # its ration is the CREEP line, not the doe's lactating TMR.
    dependent_kid: bool = False

    @model_validator(mode="after")
    def _check_sex_and_state(self) -> AnimalStartSpec:
        if self.bucket in MALE_ONLY_BUCKETS and self.sex != "M":
            raise ValueError("Only male animals may start in MALE_KIDS")
        if self.bucket in FEMALE_ONLY_BUCKETS and self.sex != "F":
            raise ValueError(f"Only female animals may start in {self.bucket}")
        if self.dependent_kid and self.bucket != Bucket.RECOVERY.value:
            raise ValueError("dependent_kid only applies in RECOVERY (kids with their dam)")
        # Operationally the only males in RECOVERY are unweaned kids with their
        # dam; anything else has no exit from the bucket in the engine (sales
        # fire from MALE_KIDS, culls apply to does), so it must be rejected
        # here rather than strand the animal for the whole run.
        if self.sex == "M" and self.bucket == Bucket.RECOVERY.value and not self.dependent_kid:
            raise ValueError(
                "A male in RECOVERY must be an unweaned kid with its dam "
                "(dependent_kid=true); growers belong in MALE_KIDS"
            )
        return self


class DailyOpsParams(BaseModel):
    """Stochastic and policy knobs. Timings are NOT here — they come from
    GOAT_PROFILE; these are the rates a user may reasonably tune."""

    model_config = ConfigDict(extra="forbid", strict=True)

    conception_rate: float = Field(default=0.85, ge=0.0, le=1.0)
    litter_size_mean: float = Field(default=1.6, ge=1.0, le=4.0)
    female_fraction_at_birth: float = Field(default=0.5, ge=0.0, le=1.0)
    stillbirth_rate: float = Field(default=0.02, ge=0.0, le=1.0)
    abortion_rate: float = Field(default=0.02, ge=0.0, le=1.0)
    kid_pre_weaning_mortality: float = Field(default=0.15, ge=0.0, le=1.0)
    adult_annual_mortality: float = Field(default=0.05, ge=0.0, le=1.0)
    failed_services_before_cull: int = Field(
        default=_PROFILE.failed_services_before_cull, ge=1, le=6
    )
    max_doe_age_months: int = Field(default=_MAX_DOE_AGE_MONTHS, ge=12, le=240)
    male_sale_age_months: int = Field(default=MEAT_SALE_AGE_MONTHS[0], ge=1, le=36)
    buck_doe_ratio: int = Field(default=BUCK_DOE_RATIO, ge=1, le=100)


class DailyOpsInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    start_date: date
    horizon_days: int = Field(default=90, ge=MIN_HORIZON_DAYS, le=MAX_HORIZON_DAYS)
    # Bounds mirror schemas.common.MAX_ID (the API layer re-validates).
    seed: int = Field(default=2026, ge=-(2**62), le=2**62)
    animals: list[AnimalStartSpec] = Field(min_length=1, max_length=MAX_START_HEAD)
    params: DailyOpsParams = Field(default_factory=DailyOpsParams)

    @field_validator("start_date")
    @classmethod
    def _realistic_start(cls, value: date) -> date:
        if not date(2000, 1, 1) <= value <= date(2099, 12, 31):
            raise ValueError("start_date must be between 2000-01-01 and 2099-12-31")
        return value

    @model_validator(mode="after")
    def _check_herd(self) -> DailyOpsInput:
        tags = [a.tag for a in self.animals]
        if len(set(tags)) != len(tags):
            raise ValueError("Animal tags must be unique")
        scan = _PROFILE.pregnancy_check_after_service_days
        late = _PROFILE.pregnancy_late_day
        prepartum = _PROFILE.gestation_days - _PROFILE.prepartum_move_lead_days
        gestation = _PROFILE.gestation_days
        for a in self.animals:
            if a.bucket == Bucket.QUARANTINE.value and a.days_in_bucket > 44:
                raise ValueError(
                    f"{a.tag}: quarantine releases at protocol day 45; "
                    "days_in_bucket cannot exceed 44"
                )
            gestation_day = a.bred_days_ago
            if a.bucket in PREGNANT_BUCKETS:
                if gestation_day is None:
                    raise ValueError(f"{a.tag}: {a.bucket} needs bred_days_ago")
                low, high = {
                    Bucket.PREGNANCY_EARLY.value: (scan, late - 1),
                    Bucket.PREGNANCY_LATE.value: (late, prepartum - 1),
                    Bucket.DELIVERY.value: (prepartum, gestation - 1),
                }[a.bucket]
                if not low <= gestation_day <= high:
                    raise ValueError(
                        f"{a.tag}: {a.bucket} implies {low}..{high} gestation days, "
                        f"got bred_days_ago={gestation_day}"
                    )
            elif (
                a.bucket in UNSCANNED_BUCKETS
                and gestation_day is not None
                and gestation_day >= scan
            ):
                raise ValueError(
                    f"{a.tag}: a doe {gestation_day} days past service would already "
                    f"have had her +{scan}d pregnancy check — start her in PREGNANCY_EARLY"
                )
            elif gestation_day is not None and a.bucket not in (
                Bucket.BREEDING.value,
                *PREGNANT_BUCKETS,
            ):
                raise ValueError(
                    f"{a.tag}: bred_days_ago belongs on a doe awaiting her scan "
                    "(BREEDING) or in a pregnancy bucket"
                )
            elif gestation_day is not None and a.sex != "F":
                raise ValueError(f"{a.tag}: bred_days_ago applies to does only")
        return self


# --- outputs ------------------------------------------------------------------


class SimTask(BaseModel):
    """One duty performed on a day, routed to a crew like a live task row."""

    model_config = ConfigDict(extra="forbid")

    day: int
    date: str
    time: str
    category: str  # TaskCategory value
    building: str  # bucket code or FEED_STORE
    building_name: str
    role: str  # role preset code
    animals: list[str] = Field(default_factory=list)
    headline: str
    detail: str


class SimMove(BaseModel):
    """One animal crossing buildings, with the workflow context that caused
    it (contexts are the legal ones from models.lifecycle)."""

    model_config = ConfigDict(extra="forbid")

    day: int
    date: str
    tag: str
    from_bucket: str | None
    to_bucket: str
    context: str
    reason: str


class SimKidBirth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tag: str
    sex: str
    status: str  # ALIVE | STILLBORN


class SimBirth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day: int
    date: str
    dam_tag: str
    kids: list[SimKidBirth]
    live_kids: int


class SimExit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day: int
    date: str
    tag: str
    kind: str  # SOLD | CULLED | DEAD
    reason: str


class FeedLine(BaseModel):
    """One (building, recipe) ration line for a day: the per-head rate, the
    daily total and the 40/20/40 shift split (gram-exact: the daily total is
    the sum of the three shifts)."""

    model_config = ConfigDict(extra="forbid")

    day: int
    building: str
    recipe: str
    recipe_display: str
    heads: int
    kg_per_head: float
    daily_kg: float
    morning_kg: float
    afternoon_kg: float
    night_kg: float


class OccupancyRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    building: str
    heads: int


class DayRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day: int
    date: str
    tasks: list[SimTask] = Field(default_factory=list)
    moves: list[SimMove] = Field(default_factory=list)
    births: list[SimBirth] = Field(default_factory=list)
    exits: list[SimExit] = Field(default_factory=list)
    feeding: list[FeedLine] = Field(default_factory=list)
    occupancy: list[OccupancyRow] = Field(default_factory=list)


class JourneyHop(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day: int
    from_bucket: str | None
    to_bucket: str
    context: str


class AnimalJourney(BaseModel):
    """One animal's whole simulated life: every hop between buildings."""

    model_config = ConfigDict(extra="forbid")

    tag: str
    sex: str
    born_day: int | None
    dam_tag: str | None
    start_bucket: str | None
    final_status: str  # ACTIVE | SOLD | CULLED | DEAD
    final_bucket: str | None
    exit_day: int | None
    exit_kind: str | None
    exit_reason: str
    hops: list[JourneyHop] = Field(default_factory=list)


class TransitionCount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_bucket: str
    to_bucket: str
    context: str
    count: int


class DailyOpsTotals(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feed_kg_by_recipe: dict[str, float]
    tasks_by_category: dict[str, int]
    tasks_by_role: dict[str, int]
    vet_tasks_by_building: dict[str, int]
    building_days: dict[str, int]
    services: int
    conceptions: int
    failed_services: int
    kids_born_alive: int
    kids_born_dead: int
    deaths: int
    culls: int
    sales: int
    moves: int


class DailyOpsResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_version: str
    species: str
    start_date: str
    horizon_days: int
    seed: int
    head_start: int
    days: list[DayRecord]
    journeys: list[AnimalJourney]
    transition_counts: list[TransitionCount]
    totals: DailyOpsTotals
    explanations: list[MetricExplanation]
    notes: list[str]


# --- internal state -----------------------------------------------------------


@dataclass
class _Hop:
    day: int
    from_bucket: str | None
    to_bucket: str
    context: str


@dataclass
class _Animal:
    tag: str
    sex: str
    dob_day: int  # sim-day index of birth; <= 0 for starting animals
    bucket: str
    entered_day: int
    hops: list[_Hop] = field(default_factory=list)
    # reproductive state
    bred_day: int | None = None
    pregnant: bool = False
    assigned_buck: str | None = None
    failed_services: int = 0
    rebreed_from_day: int | None = None
    kidding_day: int | None = None
    kids_with_dam: list[str] = field(default_factory=list)
    postpartum_due_day: int | None = None
    litter_ordinal: int = 0
    # lifecycle milestones already fired for the current pregnancy
    # gestation day at simulation start: milestones whose day already passed
    # pre-sim (e.g. the primary vaccine for a late-pregnancy arrival) never
    # fire retroactively
    milestone_floor: int = -1
    moved_to_late: bool = False
    vaccine_primary_done: bool = False
    vaccine_booster_done: bool = False
    moved_to_delivery: bool = False
    kidding_watched: bool = False
    # identity / exit
    born_in_sim: bool = False
    dam_tag: str | None = None
    dependent_kid: bool = False
    quarantine_arrival_day: int | None = None
    quarantine_steps_fired: set[int] = field(default_factory=set)
    status: str = "ACTIVE"
    exit_day: int | None = None
    exit_kind: str | None = None
    exit_reason: str = ""

    def age_days(self, day: int) -> int:
        return day - self.dob_day

    def age_months(self, day: int) -> float:
        return self.age_days(day) / DAYS_PER_MONTH

    def bucket_days(self, day: int) -> int:
        return day - self.entered_day

    def gestation_day(self, day: int) -> int | None:
        if self.bred_day is None:
            return None
        return day - self.bred_day

    def active(self) -> bool:
        return self.status == "ACTIVE"


class _DailyOpsRun:
    """All mutable state for one simulation; ``run()`` produces the result."""

    def __init__(self, payload: DailyOpsInput) -> None:
        self.payload = payload
        self.params = payload.params
        self.rng = Random(payload.seed)
        self.animals: dict[str, _Animal] = {}
        self.days: list[DayRecord] = []
        self.conceptions = 0
        self.failed_services = 0
        self.services = 0
        self.kids_alive_total = 0
        self.kids_dead_total = 0
        self.kid_hazard = _daily_hazard(
            self.params.kid_pre_weaning_mortality, _PROFILE.weaning_days
        )
        self.adult_hazard = _daily_hazard(self.params.adult_annual_mortality, 365)
        # The draw window is scan → kidding (gestation days 32..149): a loss
        # before the scan is indistinguishable from a failed conception, so
        # the nominal rate anchors to the exposed window — not all 150 days —
        # or the effective rate would undershoot the configured one.
        self.abortion_hazard = _daily_hazard(
            self.params.abortion_rate,
            _PROFILE.gestation_days - _PROFILE.pregnancy_check_after_service_days,
        )
        self._litter_p = _litter_probabilities(self.params.litter_size_mean)

        for spec in payload.animals:
            dob_day = 1 - round(spec.age_months * DAYS_PER_MONTH)
            bred_day = None
            pregnant = False
            if spec.bred_days_ago is not None:
                bred_day = 1 - spec.bred_days_ago
                pregnant = spec.bucket in PREGNANT_BUCKETS
            # A started doe standing in RECOVERY without a dependent kid is
            # mid postpartum recovery: without this clock she could never
            # leave the bucket (weaning needs an in-sim kidding record).
            postpartum_due = None
            if spec.bucket == Bucket.RECOVERY.value and not spec.dependent_kid:
                postpartum_due = 1 + max(0, _PROFILE.postpartum_recovery_days - spec.days_in_bucket)
            self.animals[spec.tag] = _Animal(
                tag=spec.tag,
                sex=spec.sex,
                dob_day=dob_day,
                bucket=spec.bucket,
                entered_day=1 - spec.days_in_bucket,
                hops=[],
                bred_day=bred_day,
                pregnant=pregnant,
                dependent_kid=spec.dependent_kid,
                postpartum_due_day=postpartum_due,
                milestone_floor=(spec.bred_days_ago if spec.bred_days_ago is not None else -1)
                if pregnant
                else -1,
                quarantine_arrival_day=(1 - spec.days_in_bucket)
                if spec.bucket == Bucket.QUARANTINE.value
                else None,
            )

    # -- helpers --------------------------------------------------------------

    def _date(self, day: int) -> str:
        return (self.payload.start_date + timedelta(days=day - 1)).isoformat()

    def _dob_date(self, animal: _Animal) -> date:
        # recipe_for_context reasons about calendar dates for age-gated mixes;
        # anchor the dob to the same timeline the day labels use.
        return self.payload.start_date + timedelta(days=animal.dob_day - 1)

    def _active(self) -> list[_Animal]:
        return [a for a in self.animals.values() if a.active()]

    def _occupancy(self) -> list[OccupancyRow]:
        counts: dict[str, int] = {}
        for a in self._active():
            counts[a.bucket] = counts.get(a.bucket, 0) + 1
        return [OccupancyRow(building=b, heads=counts[b]) for b in _BUCKET_CODES if counts.get(b)]

    def _record(
        self,
        day: int,
        time: str,
        category: str,
        building: str,
        animals: list[str],
        headline: str,
        detail: str,
    ) -> None:
        record = self.days[day - 1]
        name = FEED_STORE_NAME if building == FEED_STORE else GOAT_BUILDING_NAMES[building]
        record.tasks.append(
            SimTask(
                day=day,
                date=record.date,
                time=time,
                category=category,
                building=building,
                building_name=name,
                role=_role_for(category),
                animals=animals,
                headline=headline,
                detail=detail,
            )
        )

    def _move(self, animal: _Animal, day: int, to_bucket: str, context: str, reason: str) -> None:
        """Execute a move; every hop is validated against the legal graph —
        an illegal edge is an engine bug and must crash, never paper over."""
        allowed = LEGAL_BUCKET_TRANSITIONS.get((animal.bucket, to_bucket))
        if allowed is None or context not in allowed:
            raise AssertionError(
                f"Illegal simulated move {animal.tag}: {animal.bucket} → {to_bucket} "
                f"via {context!r}"
            )
        record = self.days[day - 1]
        record.moves.append(
            SimMove(
                day=day,
                date=record.date,
                tag=animal.tag,
                from_bucket=animal.bucket,
                to_bucket=to_bucket,
                context=context,
                reason=reason,
            )
        )
        animal.hops.append(
            _Hop(day=day, from_bucket=animal.bucket, to_bucket=to_bucket, context=context)
        )
        animal.bucket = to_bucket
        animal.entered_day = day
        if to_bucket == Bucket.RESTING.value:
            animal.rebreed_from_day = day + _RESTING_FLUSH_DAYS

    def _exit(self, animal: _Animal, day: int, kind: str, reason: str) -> None:
        animal.status = kind
        animal.exit_day = day
        animal.exit_kind = kind
        animal.exit_reason = reason
        self.days[day - 1].exits.append(
            SimExit(day=day, date=self._date(day), tag=animal.tag, kind=kind, reason=reason)
        )
        if animal.sex == "F" and animal.kids_with_dam:
            # The dam's unweaned kids lose her; schedule the orphan path.
            self._orphan_kids(animal, day)

    def _orphan_kids(self, dam: _Animal, day: int) -> None:
        for tag in dam.kids_with_dam:
            kid = self.animals.get(tag)
            if kid is not None and kid.active() and kid.bucket == Bucket.RECOVERY.value:
                # Operational orphan/early wean: kids graduate to the sexed
                # growing pens without the day-60 ceremony. Graduation ends
                # both the creep ration and the pre-weaning mortality hazard,
                # exactly like the day-60 weaning below.
                kid.dependent_kid = False
                self._move(
                    kid,
                    day,
                    Bucket.MALE_KIDS.value if kid.sex == "M" else Bucket.FEMALE_KIDS.value,
                    "orphan_weaning",
                    f"Dam {dam.tag} left the herd — orphan wean",
                )
        dam.kids_with_dam = []

    # -- phase 1/5/6: feeding ---------------------------------------------------

    def _feed_lines(self, day: int) -> list[FeedLine]:
        record = self.days[day - 1]
        groups: dict[tuple[str, str], list[_Animal]] = {}
        for animal in sorted(self._active(), key=lambda a: a.tag):
            recipe = recipe_for_context(
                animal.bucket,
                self._dob_date(animal),
                self.payload.start_date + timedelta(days=day - 1),
                animal.bucket_days(day),
                "GOAT",
                is_dependent_kid=animal.dependent_kid,
            )
            groups.setdefault((animal.bucket, recipe), []).append(animal)

        lines: list[FeedLine] = []
        for building, recipe in sorted(groups):
            members = groups[(building, recipe)]
            if recipe == "CREEP":
                kg_per_head = CREEP_KG_PER_HEAD
            else:
                kg_per_head = GOAT_BUCKET_KG_PER_HEAD[building]
            daily = len(members) * kg_per_head
            morning = round(daily * SHIFT_SPLIT[FeedingShift.MORNING], 3)
            afternoon = round(daily * SHIFT_SPLIT[FeedingShift.AFTERNOON], 3)
            night = round(daily * SHIFT_SPLIT[FeedingShift.NIGHT], 3)
            lines.append(
                FeedLine(
                    day=day,
                    building=building,
                    recipe=recipe,
                    recipe_display=RECIPE_DISPLAY.get(recipe, recipe),
                    heads=len(members),
                    kg_per_head=kg_per_head,
                    daily_kg=round(morning + afternoon + night, 3),
                    morning_kg=morning,
                    afternoon_kg=afternoon,
                    night_kg=night,
                )
            )
        record.feeding.extend(lines)
        return lines

    def _feed_round(self, day: int, shift: FeedingShift, time: str) -> None:
        lines = self.days[day - 1].feeding
        share = {"MORNING": "morning_kg", "AFTERNOON": "afternoon_kg", "NIGHT": "night_kg"}[
            shift.value
        ]
        if shift == FeedingShift.MORNING:
            # Mixing manifest first: one batch per recipe across buildings.
            by_recipe: dict[str, float] = {}
            for line in lines:
                by_recipe[line.recipe] = round(by_recipe.get(line.recipe, 0.0) + line.daily_kg, 3)
            for recipe in sorted(by_recipe):
                if recipe == DRY_ROUGHAGE:
                    detail = "Direct-fed from dry roughage stock — no mixing (zero-grain ration)."
                else:
                    detail = "One batch covers all buildings today; sweep bunks before feeding."
                self._record(
                    day,
                    time,
                    "FEED",
                    FEED_STORE,
                    [],
                    f"Mix {by_recipe[recipe]:.3f} kg — {RECIPE_DISPLAY.get(recipe, recipe)}",
                    detail,
                )
        for line in lines:
            kg = getattr(line, share)
            if kg <= 0:
                continue
            self._record(
                day,
                time,
                "FEED",
                line.building,
                [],
                (
                    f"Deliver {kg:.3f} kg {line.recipe_display} — "
                    f"{GOAT_BUILDING_NAMES[line.building]}"
                ),
                (
                    f"{line.heads} head × {line.kg_per_head} kg/day × "
                    f"{round(SHIFT_SPLIT[shift] * 100)}% shift share"
                ),
            )

    # -- phase 2/7: cleaning ------------------------------------------------------

    def _cleaning_round(self, day: int, time: str, when: str) -> None:
        for row in self._occupancy():
            self._record(
                day,
                time,
                "CLEANING",
                row.building,
                [],
                f"Clean {GOAT_BUILDING_NAMES[row.building]} — {when}",
                "Remove soiled bedding, sweep troughs and lanes, refresh lime in wet spots.",
            )
            # CLEANING is verification-required: the cleaner manager signs off.
            self._record(
                day,
                time,
                "CLEANING",
                row.building,
                [],
                f"Verify {when} cleaning — {GOAT_BUILDING_NAMES[row.building]}",
                "Cleaner manager inspection (CLEANING duties require verification).",
            )

    # -- phase 3: due duties ------------------------------------------------------

    def _quarantine_duties(self, day: int) -> None:
        for animal in sorted(self._active(), key=lambda a: a.tag):
            if animal.bucket != Bucket.QUARANTINE.value:
                continue
            assert animal.quarantine_arrival_day is not None
            protocol_day = day - animal.quarantine_arrival_day + 1
            for offset, category, title in QUARANTINE_PROTOCOL:
                if protocol_day != offset or offset in animal.quarantine_steps_fired:
                    continue
                animal.quarantine_steps_fired.add(offset)
                if offset == 45:
                    self._record(
                        day,
                        TIME_DUTIES,
                        "BUCKET_MOVE",
                        animal.bucket,
                        [animal.tag],
                        f"Release {animal.tag} to FOUNDATION — {title}",
                        "Day 45 of the quarantine protocol: footbath then release.",
                    )
                    self._move(
                        animal,
                        day,
                        Bucket.FOUNDATION.value,
                        "quarantine_release",
                        "45-day quarantine protocol complete",
                    )
                else:
                    self._record(
                        day,
                        TIME_DUTIES,
                        category.value,
                        animal.bucket,
                        [animal.tag],
                        f"{animal.tag}: {title}",
                        f"Quarantine protocol day {protocol_day}.",
                    )

    def _pregnancy_check_duties(self, day: int) -> None:
        for animal in sorted(self._active(), key=lambda a: a.tag):
            if animal.sex != "F" or animal.bred_day is None or animal.pregnant:
                continue
            gestation_day = animal.gestation_day(day)
            if gestation_day is None or gestation_day < _PROFILE.pregnancy_check_after_service_days:
                continue
            # Once the +32d gate is past, the scan happens the same day (the
            # schedule never slips in the simulation).
            self._record(
                day,
                TIME_DUTIES,
                "ULTRASOUND",
                animal.bucket,
                [animal.tag],
                f"Pregnancy check: {animal.tag}",
                (
                    f"Bred {self._date(animal.bred_day)} "
                    f"(+{_PROFILE.pregnancy_check_after_service_days}d check)."
                ),
            )
            scan_building = animal.bucket
            conceived = self.rng.random() < self.params.conception_rate
            if conceived:
                animal.pregnant = True
                animal.failed_services = 0
                self.conceptions += 1
                self._move(
                    animal,
                    day,
                    Bucket.PREGNANCY_EARLY.value,
                    "ultrasound",
                    "Pregnancy confirmed",
                )
                self._record(
                    day,
                    TIME_DUTIES,
                    "ULTRASOUND",
                    scan_building,
                    [animal.tag],
                    f"{animal.tag}: scan POSITIVE",
                    (
                        f"Conceived (draw < {self.params.conception_rate:.2f}); "
                        f"expected kidding {self._date(animal.bred_day + _PROFILE.gestation_days)}."
                    ),
                )
            else:
                animal.failed_services += 1
                self.failed_services += 1
                animal.bred_day = None
                animal.assigned_buck = None
                if animal.failed_services >= self.params.failed_services_before_cull:
                    self._record(
                        day,
                        TIME_DUTIES,
                        "OTHER",
                        animal.bucket,
                        [animal.tag],
                        f"{animal.tag}: scan NEGATIVE — cull review",
                        (
                            f"{animal.failed_services} consecutive failed services "
                            f"(limit {self.params.failed_services_before_cull})."
                        ),
                    )
                    self._exit(animal, day, "CULLED", "Repeated failed services")
                else:
                    animal.rebreed_from_day = day + _HEAT_CYCLE_DAYS
                    self._record(
                        day,
                        TIME_DUTIES,
                        "ULTRASOUND",
                        animal.bucket,
                        [animal.tag],
                        f"{animal.tag}: scan NEGATIVE",
                        (
                            f"Failed service {animal.failed_services}/"
                            f"{self.params.failed_services_before_cull}; re-serve on the next heat "
                            f"(day {animal.rebreed_from_day})."
                        ),
                    )

    def _gestation_duties(self, day: int) -> None:
        """Milestone duties for confirmed pregnancies: the day-100 move,
        pre-kidding vaccines (EKD−40 / −25), the prepartum DELIVERY move
        (EKD−15) and the kidding-due watch (EKD). Also the daily abortion
        draw, which ends the pregnancy into RESTING."""
        for animal in sorted(self._active(), key=lambda a: a.tag):
            if not animal.pregnant:
                continue
            gestation_day = animal.gestation_day(day)
            if gestation_day is None:
                continue
            if gestation_day < _PROFILE.gestation_days and self.rng.random() < self.abortion_hazard:
                self._record(
                    day,
                    TIME_DUTIES,
                    "OTHER",
                    animal.bucket,
                    [animal.tag],
                    f"Pregnancy loss: {animal.tag}",
                    f"Gestation day {gestation_day}; record loss and treat.",
                )
                animal.pregnant = False
                animal.bred_day = None
                animal.assigned_buck = None
                self._move(animal, day, Bucket.RESTING.value, "abortion", "Pregnancy lost")
                continue
            if (
                animal.bucket == Bucket.PREGNANCY_EARLY.value
                and not animal.moved_to_late
                and gestation_day >= _PROFILE.pregnancy_late_day
                and _PROFILE.pregnancy_late_day > animal.milestone_floor
            ):
                animal.moved_to_late = True
                self._move(
                    animal,
                    day,
                    Bucket.PREGNANCY_LATE.value,
                    "manual",
                    f"Gestation day {gestation_day} (pregnancy-late gate "
                    f"{_PROFILE.pregnancy_late_day})",
                )
            # pregnant implies bred_day is set (kidding clears both together)
            assert animal.bred_day is not None
            ekd = animal.bred_day + _PROFILE.gestation_days
            if (
                not animal.vaccine_primary_done
                and gestation_day >= _PROFILE.gestation_days - 40
                and _PROFILE.gestation_days - 40 > animal.milestone_floor
            ):
                animal.vaccine_primary_done = True
                self._record(
                    day,
                    TIME_DUTIES,
                    "VACCINE",
                    animal.bucket,
                    [animal.tag],
                    f"Pre-kidding ET+TT vaccine: {animal.tag}",
                    f"Primary dose, 40 days before the expected kidding ({self._date(ekd)}).",
                )
            if (
                not animal.vaccine_booster_done
                and gestation_day >= _PROFILE.gestation_days - 25
                and _PROFILE.gestation_days - 25 > animal.milestone_floor
            ):
                animal.vaccine_booster_done = True
                self._record(
                    day,
                    TIME_DUTIES,
                    "VACCINE",
                    animal.bucket,
                    [animal.tag],
                    f"Pre-kidding ET+TT vaccine booster: {animal.tag}",
                    (
                        "Booster 15 days after the primary dose."
                        if animal.vaccine_primary_done
                        else "Booster only — her primary dose pre-dates this run."
                    ),
                )
            if (
                not animal.moved_to_delivery
                and gestation_day >= _PROFILE.gestation_days - _PROFILE.prepartum_move_lead_days
                and _PROFILE.gestation_days - _PROFILE.prepartum_move_lead_days
                > animal.milestone_floor
            ):
                animal.moved_to_delivery = True
                self._record(
                    day,
                    TIME_DUTIES,
                    "BUCKET_MOVE",
                    animal.bucket,
                    [animal.tag],
                    f"Move {animal.tag} to DELIVERY (kidding in ~2 weeks)",
                    f"Prepartum lead {_PROFILE.prepartum_move_lead_days} days before "
                    f"{self._date(ekd)}.",
                )
                self._move(
                    animal,
                    day,
                    Bucket.DELIVERY.value,
                    "delivery",
                    f"{_PROFILE.prepartum_move_lead_days} days before expected kidding",
                )
            if not animal.kidding_watched and gestation_day >= _PROFILE.gestation_days:
                animal.kidding_watched = True
                self._record(
                    day,
                    TIME_DUTIES,
                    "KIDDING_DUE",
                    animal.bucket,
                    [animal.tag],
                    f"Kidding due: {animal.tag}",
                    f"Expected today ({self._date(ekd)}); monitor through the night.",
                )

    # -- phase 4: lifecycle events --------------------------------------------------

    def _breeding(self, day: int) -> None:
        # Young sires graduate out of FOUNDATION into the breeding pen at the
        # sire age gate — the mirror of the doe graduation below, and the only
        # path that can put a buck into BREEDING mid-run (without it a herd
        # that starts with no standing buck can never breed, however many
        # growers mature).
        for animal in sorted(self._active(), key=lambda a: a.tag):
            if animal.sex != "M" or animal.bucket != Bucket.FOUNDATION.value:
                continue
            if animal.age_months(day) < _PROFILE.min_sire_breeding_age_months:
                continue
            self._record(
                day,
                TIME_DUTIES,
                "BUCKET_MOVE",
                animal.bucket,
                [animal.tag],
                f"Graduate {animal.tag} to BREEDING — sire age",
                (
                    f"Sire age gate {_PROFILE.min_sire_breeding_age_months} months "
                    f"reached (age ~{animal.age_months(day):.1f} months); "
                    "he joins the breeding pen."
                ),
            )
            self._move(
                animal,
                day,
                Bucket.BREEDING.value,
                "breeding",
                f"Sire age reached (~{animal.age_months(day):.1f} months)",
            )
        # Collect eligible does WITHOUT moving anyone yet: operationally the
        # breeding record (which needs a sire) is what moves a doe into
        # BREEDING, so with no buck standing nobody graduates either.
        ready: list[tuple[_Animal, str]] = []
        unbred: list[_Animal] = []
        for animal in sorted(self._active(), key=lambda a: a.tag):
            if animal.sex != "F":
                continue
            if animal.bucket in (
                Bucket.FOUNDATION.value,
                Bucket.FEMALE_KIDS.value,
                Bucket.RESTING.value,
            ):
                if animal.bucket == Bucket.RESTING.value:
                    # Seeded RESTING rule: dry-off + flush, then back to the
                    # breeding bucket after ~30 days.
                    ready_gate = animal.bucket_days(day) >= _RESTING_FLUSH_DAYS
                else:
                    ready_gate = animal.age_months(day) >= _PROFILE.min_breeding_age_months
                if ready_gate and animal.age_months(day) >= _PROFILE.min_breeding_age_months:
                    reason = (
                        f"Flush complete ({_RESTING_FLUSH_DAYS} days in RESTING)"
                        if animal.bucket == Bucket.RESTING.value
                        else f"Breeding-ready at ~{animal.age_months(day):.1f} months"
                    )
                    ready.append((animal, reason))
            elif animal.bucket == Bucket.BREEDING.value and animal.bred_day is None:
                if animal.rebreed_from_day is not None and day < animal.rebreed_from_day:
                    continue
                if animal.age_months(day) < _PROFILE.min_breeding_age_months:
                    continue
                unbred.append(animal)
        if not ready and not unbred:
            return
        bucks = [
            a
            for a in sorted(self._active(), key=lambda a: a.tag)
            if a.sex == "M"
            and a.bucket == Bucket.BREEDING.value
            # The live app gates sires on age (and weight); the age proxy keeps
            # a starter buck kid from serving does months too early.
            and a.age_months(day) >= _PROFILE.min_sire_breeding_age_months
        ]
        if not bucks:
            return
        does = sorted(unbred + [animal for animal, _ in ready], key=lambda a: a.tag)
        for animal, reason in ready:
            self._move(animal, day, Bucket.BREEDING.value, "breeding", reason)
        open_load = {b.tag: 0 for b in bucks}
        for a in self.animals.values():
            if (
                a.active()
                and a.sex == "F"
                and a.bred_day is not None
                and not a.pregnant
                and a.assigned_buck in open_load
            ):
                open_load[a.assigned_buck] += 1
        for doe in does:
            buck = next((b for b in bucks if open_load[b.tag] < self.params.buck_doe_ratio), None)
            if buck is None:
                self._record(
                    day,
                    TIME_DUTIES,
                    "OTHER",
                    doe.bucket,
                    [doe.tag],
                    f"Hold {doe.tag} — sire capacity reached",
                    (
                        f"Every buck already covers {self.params.buck_doe_ratio} open "
                        "services; wait for scans to free capacity."
                    ),
                )
                continue
            doe.bred_day = day
            doe.assigned_buck = buck.tag
            doe.moved_to_late = False
            doe.vaccine_primary_done = False
            doe.vaccine_booster_done = False
            doe.moved_to_delivery = False
            doe.kidding_watched = False
            open_load[buck.tag] += 1
            self.services += 1
            self._record(
                day,
                TIME_DUTIES,
                "OTHER",
                doe.bucket,
                [doe.tag, buck.tag],
                f"Breed {doe.tag} — sire {buck.tag}",
                (
                    f"Natural service; pregnancy check due "
                    f"{self._date(day + _PROFILE.pregnancy_check_after_service_days)}."
                ),
            )

    def _kidding(self, day: int) -> None:
        for animal in sorted(self._active(), key=lambda a: a.tag):
            if not animal.pregnant or animal.sex != "F":
                continue
            gestation_day = animal.gestation_day(day)
            if gestation_day is None or gestation_day < _PROFILE.gestation_days:
                continue
            litter = self._draw_litter()
            kids: list[SimKidBirth] = []
            for _ in range(litter):
                animal.litter_ordinal += 1
                tag = f"{animal.tag}-{animal.litter_ordinal}"
                # self.animals is keyed by tag: a starter already owning this
                # kid tag must never be silently replaced by the newborn.
                while tag in self.animals:
                    animal.litter_ordinal += 1
                    tag = f"{animal.tag}-{animal.litter_ordinal}"
                female = self.rng.random() < self.params.female_fraction_at_birth
                sex = "F" if female else "M"
                stillborn = self.rng.random() < self.params.stillbirth_rate
                kids.append(
                    SimKidBirth(tag=tag, sex=sex, status="STILLBORN" if stillborn else "ALIVE")
                )
                if stillborn:
                    self.kids_dead_total += 1
                    continue
                self.animals[tag] = _Animal(
                    tag=tag,
                    sex=sex,
                    dob_day=day,
                    bucket=Bucket.RECOVERY.value,
                    entered_day=day,
                    born_in_sim=True,
                    dam_tag=animal.tag,
                    dependent_kid=True,
                )
                animal.kids_with_dam.append(tag)
                self.kids_alive_total += 1
            live = sum(1 for k in kids if k.status == "ALIVE")
            self.days[day - 1].births.append(
                SimBirth(
                    day=day,
                    date=self._date(day),
                    dam_tag=animal.tag,
                    kids=kids,
                    live_kids=live,
                )
            )
            self._record(
                day,
                TIME_DUTIES,
                "OTHER",
                animal.bucket,
                [animal.tag],
                f"Record kidding: {animal.tag} — {live} live of {litter}",
                (
                    f"Kids stay with the dam in RECOVERY until weaning day "
                    f"{_PROFILE.weaning_days}; creep feed starts now."
                ),
            )
            animal.pregnant = False
            animal.bred_day = None
            animal.assigned_buck = None
            animal.kidding_day = day
            self._move(animal, day, Bucket.RECOVERY.value, "kidding", "Kidding recorded")
            if live == 0:
                animal.postpartum_due_day = day + _PROFILE.postpartum_recovery_days

    def _draw_litter(self) -> int:
        roll = self.rng.random()
        cumulative = 0.0
        for size, probability in enumerate(self._litter_p, start=1):
            cumulative += probability
            if roll < cumulative:
                return size
        return 4

    def _weaning_and_postpartum(self, day: int) -> None:
        for animal in sorted(self._active(), key=lambda a: a.tag):
            if animal.sex != "F" or animal.bucket != Bucket.RECOVERY.value:
                continue
            if animal.kidding_day is not None and animal.kids_with_dam:
                if day >= animal.kidding_day + _PROFILE.weaning_days:
                    self._record(
                        day,
                        TIME_DUTIES,
                        "WEANING",
                        animal.bucket,
                        [animal.tag, *animal.kids_with_dam],
                        f"Wean kids of {animal.tag}; doe → RESTING",
                        f"Weaning day {_PROFILE.weaning_days} after kidding.",
                    )
                    for kid_tag in list(animal.kids_with_dam):
                        kid = self.animals.get(kid_tag)
                        if kid is None or not kid.active():
                            continue
                        kid.dependent_kid = False
                        self._move(
                            kid,
                            day,
                            Bucket.MALE_KIDS.value if kid.sex == "M" else Bucket.FEMALE_KIDS.value,
                            "weaning",
                            f"Day-{_PROFILE.weaning_days} weaning",
                        )
                    animal.kids_with_dam = []
                    self._move(
                        animal,
                        day,
                        Bucket.RESTING.value,
                        "weaning",
                        "Kids weaned",
                    )
            elif animal.postpartum_due_day is not None and day >= animal.postpartum_due_day:
                self._record(
                    day,
                    TIME_DUTIES,
                    "BUCKET_MOVE",
                    animal.bucket,
                    [animal.tag],
                    f"Move {animal.tag} to RESTING after postpartum recovery",
                    (
                        f"Postpartum recovery window "
                        f"({_PROFILE.postpartum_recovery_days} days) complete."
                    ),
                )
                animal.postpartum_due_day = None
                self._move(
                    animal,
                    day,
                    Bucket.RESTING.value,
                    "postpartum",
                    "Postpartum recovery complete (no surviving kids)",
                )
            elif animal.dependent_kid and animal.age_days(day) >= _PROFILE.weaning_days:
                # Started unweaned kids carry no in-sim dam link, so the day-60
                # clock runs on their own age; without this fallback they would
                # stand in RECOVERY (on creep feed) forever.
                animal.dependent_kid = False
                self._record(
                    day,
                    TIME_DUTIES,
                    "WEANING",
                    animal.bucket,
                    [animal.tag],
                    f"Age wean {animal.tag}",
                    (
                        f"Starter kid with no dam link; weaning age "
                        f"{_PROFILE.weaning_days} days reached."
                    ),
                )
                self._move(
                    animal,
                    day,
                    Bucket.MALE_KIDS.value if animal.sex == "M" else Bucket.FEMALE_KIDS.value,
                    "weaning",
                    f"Age {animal.age_days(day)} days (starter kid, no dam link)",
                )

    def _mortality(self, day: int) -> None:
        for animal in sorted(self._active(), key=lambda a: a.tag):
            hazard = self.kid_hazard if animal.dependent_kid else self.adult_hazard
            if self.rng.random() >= hazard:
                continue
            reason = (
                f"Pre-weaning kid loss (phase rate {self.params.kid_pre_weaning_mortality:.0%})"
                if animal.dependent_kid
                else f"Adult mortality (annual rate {self.params.adult_annual_mortality:.0%})"
            )
            self._record(
                day,
                TIME_DUTIES,
                "OTHER",
                animal.bucket,
                [animal.tag],
                f"Attend casualty: {animal.tag} died",
                f"{reason}; record mortality and dispose per protocol.",
            )
            dam = self.animals.get(animal.dam_tag) if animal.dam_tag else None
            if dam is not None and animal.tag in dam.kids_with_dam:
                dam.kids_with_dam.remove(animal.tag)
                if not dam.kids_with_dam and dam.bucket == Bucket.RECOVERY.value:
                    dam.postpartum_due_day = day + _PROFILE.postpartum_recovery_days
            self._exit(animal, day, "DEAD", reason)

    def _age_culls(self, day: int) -> None:
        for animal in sorted(self._active(), key=lambda a: a.tag):
            if animal.sex != "F" or animal.pregnant:
                continue
            if animal.age_months(day) >= self.params.max_doe_age_months:
                self._record(
                    day,
                    TIME_DUTIES,
                    "OTHER",
                    animal.bucket,
                    [animal.tag],
                    f"Cull {animal.tag} — age",
                    f"Reached {self.params.max_doe_age_months} months.",
                )
                self._exit(animal, day, "CULLED", f"Age {self.params.max_doe_age_months} months")

    def _sales(self, day: int) -> None:
        for animal in sorted(self._active(), key=lambda a: a.tag):
            if animal.sex != "M" or animal.bucket != Bucket.MALE_KIDS.value:
                continue
            if animal.age_months(day) >= self.params.male_sale_age_months:
                self._record(
                    day,
                    TIME_DUTIES,
                    "OTHER",
                    animal.bucket,
                    [animal.tag],
                    f"Sell {animal.tag} — meat window",
                    (
                        f"Age ~{animal.age_months(day):.1f} months "
                        f"(sale window {self.params.male_sale_age_months}–"
                        f"{self.params.male_sale_age_months + 1} months)."
                    ),
                )
                self._exit(
                    animal,
                    day,
                    "SOLD",
                    f"Meat sale at ~{animal.age_months(day):.1f} months",
                )

    # -- driver -----------------------------------------------------------------

    def run(self) -> DailyOpsResult:
        for day in range(1, self.payload.horizon_days + 1):
            self.days.append(DayRecord(day=day, date=self._date(day)))
            self._feed_lines(day)
            self._feed_round(day, FeedingShift.MORNING, TIME_MORNING_FEED)
            self._cleaning_round(day, TIME_MORNING_CLEAN, "morning (after feeding)")
            self._quarantine_duties(day)
            self._pregnancy_check_duties(day)
            self._gestation_duties(day)
            self._breeding(day)
            self._kidding(day)
            self._weaning_and_postpartum(day)
            self._mortality(day)
            self._age_culls(day)
            self._sales(day)
            self._feed_round(day, FeedingShift.AFTERNOON, TIME_AFTERNOON_FEED)
            self._feed_round(day, FeedingShift.NIGHT, TIME_NIGHT_FEED)
            self._cleaning_round(day, TIME_NIGHT_CLEAN, "night (after the night feed)")
            self.days[day - 1].occupancy = self._occupancy()
        return self._build_result()

    # -- aggregation --------------------------------------------------------------

    def _build_result(self) -> DailyOpsResult:
        journeys: list[AnimalJourney] = []
        for tag in sorted(self.animals):
            a = self.animals[tag]
            journeys.append(
                AnimalJourney(
                    tag=tag,
                    sex=a.sex,
                    born_day=a.dob_day if a.born_in_sim else None,
                    dam_tag=a.dam_tag,
                    start_bucket=None
                    if a.born_in_sim
                    else (a.hops[0].from_bucket if a.hops else a.bucket),
                    final_status=a.status,
                    final_bucket=a.bucket if a.active() else None,
                    exit_day=a.exit_day,
                    exit_kind=a.exit_kind,
                    exit_reason=a.exit_reason,
                    hops=[
                        JourneyHop(
                            day=hop.day,
                            from_bucket=hop.from_bucket,
                            to_bucket=hop.to_bucket,
                            context=hop.context,
                        )
                        for hop in a.hops
                    ],
                )
            )
        transition_counter: dict[tuple[str, str, str], int] = {}
        for record in self.days:
            for move in record.moves:
                key = (move.from_bucket or "", move.to_bucket, move.context)
                transition_counter[key] = transition_counter.get(key, 0) + 1
        transition_counts = [
            TransitionCount(from_bucket=k[0], to_bucket=k[1], context=k[2], count=v)
            for k, v in sorted(transition_counter.items())
        ]

        feed_by_recipe: dict[str, float] = {}
        tasks_by_category: dict[str, int] = {}
        tasks_by_role: dict[str, int] = {}
        vet_by_building: dict[str, int] = {}
        building_days: dict[str, int] = {}
        for record in self.days:
            for line in record.feeding:
                feed_by_recipe[line.recipe] = round(
                    feed_by_recipe.get(line.recipe, 0.0) + line.daily_kg, 3
                )
            for task in record.tasks:
                tasks_by_category[task.category] = tasks_by_category.get(task.category, 0) + 1
                tasks_by_role[task.role] = tasks_by_role.get(task.role, 0) + 1
                if task.role == "VET":
                    vet_by_building[task.building] = vet_by_building.get(task.building, 0) + 1
            for row in record.occupancy:
                building_days[row.building] = building_days.get(row.building, 0) + 1

        deaths = sum(1 for a in self.animals.values() if a.exit_kind == "DEAD")
        culls = sum(1 for a in self.animals.values() if a.exit_kind == "CULLED")
        sales = sum(1 for a in self.animals.values() if a.exit_kind == "SOLD")
        moves = sum(len(r.moves) for r in self.days)
        totals = DailyOpsTotals(
            feed_kg_by_recipe={k: v for k, v in sorted(feed_by_recipe.items())},
            tasks_by_category=dict(sorted(tasks_by_category.items())),
            tasks_by_role=dict(sorted(tasks_by_role.items())),
            vet_tasks_by_building=dict(sorted(vet_by_building.items())),
            building_days=dict(sorted(building_days.items())),
            services=self.services,
            conceptions=self.conceptions,
            failed_services=self.failed_services,
            kids_born_alive=self.kids_alive_total,
            kids_born_dead=self.kids_dead_total,
            deaths=deaths,
            culls=culls,
            sales=sales,
            moves=moves,
        )
        result = DailyOpsResult(
            model_version=DAILY_OPS_MODEL_VERSION,
            species=_PROFILE.farm_type,
            start_date=self.payload.start_date.isoformat(),
            horizon_days=self.payload.horizon_days,
            seed=self.payload.seed,
            head_start=len(self.payload.animals),
            days=self.days,
            journeys=journeys,
            transition_counts=transition_counts,
            totals=totals,
            explanations=[],
            notes=[],
        )
        result.explanations.extend(_build_explanations(result, self))
        result.notes.extend(_build_notes(result, self))
        return result


# --- explanations and notes -----------------------------------------------------


def _build_explanations(result: DailyOpsResult, run: _DailyOpsRun) -> list[MetricExplanation]:
    totals = result.totals
    head_end = sum(r.heads for r in result.days[-1].occupancy) if result.days else 0
    busiest_vet_building = (
        GOAT_BUILDING_NAMES.get(
            max(totals.vet_tasks_by_building.items(), key=lambda kv: kv[1])[0], "—"
        )
        if totals.vet_tasks_by_building
        else "— (no vet duties this run)"
    )
    building_day_text = (
        ", ".join(f"{GOAT_BUILDING_NAMES.get(b, b)} {d}" for b, d in totals.building_days.items())
        or "none"
    )
    explanations: list[MetricExplanation] = [
        MetricExplanation(
            key="routine",
            title="The daily routine",
            explanation=(
                f"Each of the {result.horizon_days} simulated days follows the same seven "
                "phases: morning feed 6:30 AM (40% of the day's ration), morning cleaning "
                "after it, the 9:00 AM duties round (vet work in each building's vet area), "
                "lifecycle events, afternoon feed 1:30 PM (20%), night feed 7:30 PM (40%) "
                "and night cleaning. Every occupied building is cleaned twice a day and "
                "each cleaning is verified by the cleaner manager."
            ),
            figures={
                "horizon_days": result.horizon_days,
                "buildings_occupied_last_day": len(result.days[-1].occupancy),
            },
        ),
        MetricExplanation(
            key="buildings",
            title="One building per bucket, each with a vet area",
            explanation=(
                "The ten lifecycle buckets are modelled as ten buildings using the seeded "
                "goat names (Quarantine Ward, Foundation / Grow-out, …). The vet's round is "
                f"routed building by building; the busiest vet area this run: "
                f"{busiest_vet_building}. Building-days of occupancy: {building_day_text}."
            ),
            figures=dict(totals.vet_tasks_by_building),
        ),
        MetricExplanation(
            key="feed",
            title="Feed mixed once, delivered three times",
            explanation=(
                "Each morning the day's ration per recipe is mixed at the feed store and "
                "then delivered per building in the 40/20/40 shift split (6:30 AM / 1:30 PM "
                "7:30 PM). Per-head rates are the seeded bucket defaults (kids 1.0 kg, "
                "does 1.2 kg, late pregnancy 1.4 kg, delivery/recovery 1.5 kg, quarantine "
                "1.1 kg); unweaned kids in RECOVERY are fed the creep line at 0.3 kg, "
                "never the doe's lactating TMR."
            ),
            figures=dict(totals.feed_kg_by_recipe),
        ),
        MetricExplanation(
            key="transitions",
            title="Every move is a legal lifecycle transition",
            explanation=(
                f"{totals.moves} moves were executed, each validated against the same legal "
                "bucket graph the live app enforces (models.lifecycle.LEGAL_BUCKET_TRANSITIONS) "
                "and stamped with the workflow context that caused it — manual, ultrasound, "
                "delivery, kidding, weaning, orphan weaning, quarantine release, abortion, "
                "postpartum or breeding."
            ),
            figures={"moves": totals.moves},
        ),
        MetricExplanation(
            key="reproduction",
            title="Reproduction: breed → scan +32d → kidding +150d",
            explanation=(
                f"{totals.services} services were recorded; {totals.conceptions} scans came "
                f"back positive (conception rate {run.params.conception_rate:.2f}) and "
                f"{totals.failed_services} failed. Confirmed does move BREEDING → "
                "PREGNANCY_EARLY at the scan, → PREGNANCY_LATE at gestation day 100, → "
                "DELIVERY 15 days before the expected kidding, kidd into RECOVERY on the "
                f"due date and wean at day {GOAT_PROFILE.weaning_days}. "
                f"{totals.kids_born_alive} kids were born alive "
                f"({totals.kids_born_dead} stillborn)."
            ),
            figures={
                "services": totals.services,
                "conceptions": totals.conceptions,
                "failed_services": totals.failed_services,
                "kids_born_alive": totals.kids_born_alive,
            },
        ),
        MetricExplanation(
            key="exits",
            title="Leaving the herd: sales, culls, deaths",
            explanation=(
                f"{totals.sales} male kids were sold entering the "
                f"{run.params.male_sale_age_months}–{run.params.male_sale_age_months + 1} "
                f"month meat window; {totals.culls} does were culled "
                f"({run.params.failed_services_before_cull} failed services or age "
                f"{run.params.max_doe_age_months} months); {totals.deaths} animals died "
                "(background mortality hazards converted from the operational phase rates: "
                f"{run.params.kid_pre_weaning_mortality:.0%} kid loss across the "
                f"{GOAT_PROFILE.weaning_days}-day pre-weaning window, "
                f"{run.params.adult_annual_mortality:.0%} adult loss per year). "
                f"The herd stands at {head_end} head on the final day, from "
                f"{result.head_start} at the start."
            ),
            figures={
                "sales": totals.sales,
                "culls": totals.culls,
                "deaths": totals.deaths,
                "head_start": result.head_start,
                "head_final": head_end,
            },
        ),
        MetricExplanation(
            key="determinism",
            title="Same input, same farm",
            explanation=(
                f"Every stochastic outcome (conception, litter size 1–4 with mean "
                f"{run.params.litter_size_mean}, kid sex, stillbirth, abortion, mortality) "
                f"is drawn from one Random({result.seed}) stream in a fixed phase order "
                "over tags sorted alphabetically — re-running this input reproduces the "
                "ledger byte for byte."
            ),
            figures={"seed": result.seed},
        ),
    ]
    return explanations


def _build_notes(result: DailyOpsResult, run: _DailyOpsRun) -> list[str]:
    notes = [
        (
            f"Goat farms only in this version (species={result.species}); "
            "dairy milking duties arrive with the buffalo profile."
        ),
        (
            "Breeding eligibility is age-gated (≥10 months); "
            "the live 22 kg weight gate is not modelled."
        ),
        (
            f"Meat sales fire entering the {run.params.male_sale_age_months}–"
            f"{run.params.male_sale_age_months + 1} month window; "
            "the 24–28 kg weight band is not modelled."
        ),
        (
            "Quarantine follows the seeded 45-day protocol; "
            "day-45 release moves the animal to FOUNDATION."
        ),
        (
            "A failed scan re-serves the doe on her next heat (21 days); "
            f"{run.params.failed_services_before_cull} consecutive failures cull her."
        ),
    ]
    # Judged on the START state (a buck that matured during the run did not
    # stand at the start): BREEDING bucks stand, and FOUNDATION/QUARANTINE
    # growers are the only paths that can still put one in BREEDING.
    standing_buck = any(
        spec.sex == "M"
        and spec.bucket == Bucket.BREEDING.value
        and spec.age_months >= GOAT_PROFILE.min_sire_breeding_age_months
        for spec in run.payload.animals
    )
    if not standing_buck:
        future_buck = any(
            spec.sex == "M"
            and spec.bucket
            in (Bucket.BREEDING.value, Bucket.FOUNDATION.value, Bucket.QUARANTINE.value)
            for spec in run.payload.animals
        )
        notes.append(
            "No buck stood in BREEDING at the start — services begin once a young "
            "sire reaches the "
            f"{GOAT_PROFILE.min_sire_breeding_age_months}-month age gate (Foundation "
            "growers graduate into the breeding pen)."
            if future_buck
            else "No buck in this herd — no does can be served this run."
        )
    return notes


# --- manual-verification ledger ---------------------------------------------------


def build_daily_ledger(result: DailyOpsResult) -> str:
    """Deterministic Markdown audit ledger: one section per day plus the
    aggregates. Built only from the result, so it is exactly replayable."""
    lines: list[str] = []
    lines.append("# Buckets & Tasks — daily operations ledger")
    lines.append("")
    lines.append(
        f"Model {result.model_version} · species {result.species} · start "
        f"{result.start_date} · horizon {result.horizon_days} days · seed {result.seed} · "
        f"{result.head_start} head at start"
    )
    lines.append("")
    for record in result.days:
        lines.append(f"## Day {record.day} — {record.date}")
        lines.append("")
        if record.occupancy:
            occupancy = ", ".join(
                f"{GOAT_BUILDING_NAMES.get(r.building, r.building)} {r.heads}"
                for r in record.occupancy
            )
            lines.append(f"**Occupancy:** {occupancy}")
            lines.append("")
        if record.feeding:
            lines.append(
                "| Building | Recipe | Heads | kg/head | Morning | Afternoon | Night | Daily |"
            )
            lines.append("|---|---|---:|---:|---:|---:|---:|---:|")
            for line in record.feeding:
                building = (
                    FEED_STORE_NAME
                    if line.building == FEED_STORE
                    else GOAT_BUILDING_NAMES.get(line.building, line.building)
                )
                lines.append(
                    f"| {building} | {line.recipe_display} | {line.heads} | "
                    f"{line.kg_per_head:g} | {line.morning_kg:g} | {line.afternoon_kg:g} | "
                    f"{line.night_kg:g} | {line.daily_kg:g} |"
                )
            lines.append("")
        if record.tasks:
            lines.append("| Time | Duty | Building | Crew | Animals | Detail |")
            lines.append("|---|---|---|---|---|---|")
            for task in record.tasks:
                building = (
                    FEED_STORE_NAME
                    if task.building == FEED_STORE
                    else GOAT_BUILDING_NAMES.get(task.building, task.building)
                )
                animals = ", ".join(task.animals) if task.animals else "—"
                lines.append(
                    f"| {task.time} | {task.headline} | {building} | {task.role} | "
                    f"{animals} | {task.detail} |"
                )
            lines.append("")
        for birth in record.births:
            kids = ", ".join(f"{k.tag} ({k.sex}, {k.status})" for k in birth.kids)
            lines.append(f"- **Kidding** {birth.dam_tag}: {birth.live_kids} live — {kids}")
        for move in record.moves:
            lines.append(
                f"- **Move** {move.tag}: {move.from_bucket or '—'} → {move.to_bucket} "
                f"({move.context}) — {move.reason}"
            )
        for exit_ in record.exits:
            lines.append(f"- **{exit_.kind}** {exit_.tag} — {exit_.reason}")
        if record.births or record.moves or record.exits:
            lines.append("")

    totals = result.totals
    lines.append("## Totals")
    lines.append("")
    lines.append(
        f"- Services: {totals.services} "
        f"(conceptions {totals.conceptions}, failed {totals.failed_services})"
    )
    lines.append(f"- Kids born: {totals.kids_born_alive} alive, {totals.kids_born_dead} stillborn")
    lines.append(f"- Deaths {totals.deaths} · culls {totals.culls} · sales {totals.sales}")
    lines.append(f"- Moves: {totals.moves}")
    if totals.feed_kg_by_recipe:
        feed = ", ".join(
            f"{RECIPE_DISPLAY.get(k, k)} {v:g} kg" for k, v in totals.feed_kg_by_recipe.items()
        )
        lines.append(f"- Feed: {feed}")
    lines.append("")

    lines.append("## Transition matrix")
    lines.append("")
    lines.append("| From | To | Context | Count |")
    lines.append("|---|---|---|---:|")
    for row in result.transition_counts:
        lines.append(f"| {row.from_bucket} | {row.to_bucket} | {row.context} | {row.count} |")
    lines.append("")

    lines.append("## Animal journeys")
    lines.append("")
    lines.append("| Tag | Sex | Born | Hops | Final |")
    lines.append("|---|---|---|---|---|")
    for journey in result.journeys:
        hops = (
            " → ".join(
                f"d{h.day} {h.to_bucket} ({h.context})"
                if h.from_bucket is None
                else f"d{h.day} {h.from_bucket}→{h.to_bucket} ({h.context})"
                for h in journey.hops
            )
            or "—"
        )
        final = (
            f"{journey.final_bucket} (active)"
            if journey.final_bucket
            else f"{journey.exit_kind} on day {journey.exit_day} — {journey.exit_reason}"
        )
        lines.append(
            f"| {journey.tag} | {journey.sex} | {journey.born_day or 'start'} | {hops} | {final} |"
        )
    lines.append("")

    lines.append("## Explanations")
    lines.append("")
    for explanation in result.explanations:
        lines.append(f"### {explanation.title}")
        lines.append("")
        lines.append(explanation.explanation)
        lines.append("")
    return "\n".join(lines) + "\n"


def run_daily_ops(payload: DailyOpsInput) -> DailyOpsResult:
    """Validate and run the daily operations simulation."""
    return _DailyOpsRun(payload).run()
