"""Simulation: breed/system defaults, herd snapshot, ad-hoc runs, and
farm-scoped saved scenarios (CRUD + run + compare).

The engine itself is pure Python (``app.simulation``); this router only handles
transport, persistence (assumptions stored as JSON text, like
``Role.permissions``) and RBAC. Runs are synchronous CPU work — horizon and
Monte Carlo runs are bounded by the assumption schema (monte_carlo_runs <=
2000), which still leaves a worst-case run (240-month horizon + max Monte
Carlo + sensitivity + optimization + break-even) at ~25 s of single-threaded
CPU, measured. Runs are offloaded to a worker thread, but the work is pure
Python: the GIL is held throughout, so offloading bounds neither latency nor
CPU on its own.

``_run_cost`` prices a request as engine passes x simulated months, which is
only honest while one pass is linear in the horizon. It is: IRR — the one
super-linear metric, and formerly ~86% of a 240-month pass — is now computed
lazily off ``_CoreResult`` and solved with a sampled scan past the term count
the exact Decimal isolation was designed for. Keep it that way, or re-derive
the budget.

Three limits therefore apply to every run/compare request:
- one in-flight run per farm and per user, plus two process-wide, so
  concurrent requests get a 429 instead of piling onto the threadpool;
- a per-user and per-farm sliding CPU budget (``_RunCostWindow``) priced by
  what the request will actually cost, so a caller cannot simply loop
  expensive runs back to back and starve every other tenant;
- a hard 422 on any non-finite result.

A distributed job queue (and a separate process for Monte Carlo) remains the
deployment path for multi-replica scale.
"""

import asyncio
import json
import math
import time
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable, Mapping
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import ValidationError
from sqlalchemy import case, func, literal, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.elements import ColumnElement
from starlette.concurrency import run_in_threadpool

from ..core.config import get_settings
from ..deps import CurrentFarm, CurrentUser, DbSession, require_perm
from ..models import Animal, AnimalStatus, SimulationScenario
from ..schemas.common import MAX_INT32_ID, MAX_PAGE_OFFSET
from ..schemas.simulation import (
    BreedsOut,
    FarmCalibrationOut,
    HerdSnapshotOut,
    RunIn,
    ScenarioCompareOut,
    ScenarioCreateIn,
    ScenarioListOut,
    ScenarioOut,
    ScenarioUpdateIn,
)
from ..services import IdempotencyKey, execute_idempotent
from ..services.simulation_calibration import calibrate_farm_assumptions
from ..simulation.assumptions import SimulationAssumptions
from ..simulation.defaults import PRESET_FACTORIES, SYSTEMS, System, get_preset
from ..simulation.engine import run_simulation
from ..simulation.results import SimulationResult
from ..utils import today

router = APIRouter(prefix="/api/simulation", tags=["simulation"])

# Namespace for the per-farm scenario-quota mutex. Advisory lock keys are
# global to the database, so every acquisition of this counter must pass it.
SCENARIO_QUOTA_LOCK_NAMESPACE = 4713

SimView = Annotated[set[str], Depends(require_perm("simulation.view"))]
SimManage = Annotated[set[str], Depends(require_perm("simulation.manage"))]
AnimalsView = Annotated[set[str], Depends(require_perm("animals.view"))]
BreedingView = Annotated[set[str], Depends(require_perm("breeding.view"))]
KiddingView = Annotated[set[str], Depends(require_perm("kidding.view"))]
FeedingView = Annotated[set[str], Depends(require_perm("feeding.view"))]
FinanceView = Annotated[set[str], Depends(require_perm("finance.view"))]

# A compare re-runs a full simulation per id — cap the work per request.
MAX_COMPARE_IDS = 5
SCENARIO_CAPACITY_REASON = "This farm has reached its saved-scenario limit."


def _load_assumptions(scenario: SimulationScenario) -> SimulationAssumptions:
    """Stored assumptions JSON → validated model.

    Rows stored under an older, looser schema can fail revalidation after
    the schema tightens (e.g. when magnitude caps were added). That must
    surface as a 422 for the affected scenario — and be skipped in the list
    endpoint — never a bare 500 for the whole farm.
    """
    try:
        return SimulationAssumptions.model_validate(json.loads(scenario.assumptions))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Stored assumptions for scenario {scenario.name!r} no longer "
                "validate against the current schema; update or delete it."
            ),
        ) from exc


def _scenario_out(scenario: SimulationScenario, *, allow_invalid: bool = False) -> ScenarioOut:
    """ORM → schema; assumptions are JSON text on the row."""
    try:
        assumptions = _load_assumptions(scenario)
    except HTTPException as exc:
        if not allow_invalid:
            raise
        return ScenarioOut(
            id=scenario.id,
            farm_id=scenario.farm_id,
            name=scenario.name,
            notes=scenario.notes,
            assumptions=None,
            valid=False,
            validation_error=str(exc.detail),
            created_at=scenario.created_at,
            updated_at=scenario.updated_at,
        )
    return ScenarioOut(
        id=scenario.id,
        farm_id=scenario.farm_id,
        name=scenario.name,
        notes=scenario.notes,
        assumptions=assumptions,
        valid=True,
        validation_error=None,
        created_at=scenario.created_at,
        updated_at=scenario.updated_at,
    )


async def _get_scenario(db: DbSession, farm_id: int, scenario_id: int) -> SimulationScenario:
    """Farm-scoped fetch: unknown or foreign ids both 404."""
    scenario = (
        await db.get(SimulationScenario, scenario_id) if 1 <= scenario_id <= MAX_INT32_ID else None
    )
    if scenario is None or scenario.farm_id != farm_id:
        raise HTTPException(status_code=404, detail="Scenario not found")
    return scenario


async def _check_name_free(db: DbSession, farm_id: int, name: str, exclude_id: int = 0) -> None:
    clash = (
        (
            await db.execute(
                select(SimulationScenario).where(
                    SimulationScenario.farm_id == farm_id,
                    SimulationScenario.name == name,
                    SimulationScenario.id != exclude_id,
                )
            )
        )
        .scalars()
        .first()
    )
    if clash is not None:
        raise HTTPException(status_code=400, detail="A scenario with that name already exists.")


def _finite_payload(value: object) -> bool:
    """False if any float anywhere in a ``model_dump``'d payload is NaN/±inf."""
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, Mapping):
        return all(_finite_payload(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return all(_finite_payload(item) for item in value)
    return True


def _run(
    assumptions: SimulationAssumptions,
    monte_carlo: bool,
    sensitivity: bool,
    optimization: bool,
) -> SimulationResult:
    result = run_simulation(
        assumptions,
        with_monte_carlo=monte_carlo,
        with_sensitivity=sensitivity,
        with_optimization=optimization,
    )
    # Defense in depth past the input caps: bounded inputs can still overflow
    # derived math (a near-zero fodder yield makes the land requirement 1/ε →
    # inf), and Starlette's allow_nan=False JSONResponse turns any non-finite
    # float in the payload into a 500. Answer a clean 422 instead.
    if not _finite_payload(result.model_dump()):
        raise HTTPException(status_code=422, detail="These inputs produce non-finite results.")
    return result


async def _run_offloaded(
    assumptions: SimulationAssumptions,
    monte_carlo: bool,
    sensitivity: bool,
    optimization: bool,
) -> SimulationResult:
    """Runs are synchronous CPU work — push them off the event loop so the
    request handler itself does not block. The engine is pure Python and holds
    the GIL, so this alone does not protect other requests; the concurrency
    caps and the CPU budget above are what bound the damage."""
    return await run_in_threadpool(_run, assumptions, monte_carlo, sensitivity, optimization)


# One run per farm and per user, plus a process-wide ceiling. This prevents a
# user with several farms from consuming the whole shared threadpool. A
# distributed job queue remains the deployment path for multi-replica scale.
_farm_run_locks: dict[int, asyncio.Lock] = {}
_user_run_locks: dict[int, asyncio.Lock] = {}
_global_run_slots = asyncio.BoundedSemaphore(2)

# Concurrency caps bound parallelism, not request *rate*: without a budget a
# caller can loop maximum-cost runs forever and hold both process-wide slots,
# which measures as a ~200x latency hit on every other tenant's ordinary read.
# So each run is priced before it starts and charged against a sliding window.
# The unit is one engine pass over one simulated month, which tracks measured
# CPU closely: a default 120-month run costs ~6.4k, while the schema-maximal
# body with Monte Carlo, sensitivity and optimization costs under 570k.
_RUN_BUDGET_WINDOW_SECONDS = 300
_RUN_BUDGET_UNITS = 650_000
_BREAK_EVEN_PASSES = 52  # npv_at(0), npv_at(schema ceiling) + 50 bisection steps
_SENSITIVITY_PASSES = 17  # base + 8 parameters x (low, high)
_RUN_BUDGET_MAX_KEYS = 50_000  # cardinality ceiling, mirroring app.ratelimit


def _run_cost(
    assumptions: SimulationAssumptions,
    monte_carlo: bool,
    sensitivity: bool,
    optimization: bool = False,
) -> int:
    """Engine passes x simulated months — what this request will cost."""
    passes = 1 + _BREAK_EVEN_PASSES
    if monte_carlo:
        passes += assumptions.risk.monte_carlo_runs
    if sensitivity:
        passes += _SENSITIVITY_PASSES
    if optimization:
        passes += assumptions.optimization.max_candidates
    return passes * assumptions.meta.horizon_months


class _RunCostWindow:
    """Sliding-window CPU budget keyed by ``(scope, principal id)``.

    Unlike the attempt counter in ``app.ratelimit`` the cost of one simulation
    request spans three orders of magnitude, so the window accumulates cost
    rather than requests: a caller may spend its budget on a single maximal
    run or on many cheap ones. Per-process and non-persistent, with the same
    single-process caveat as the auth limiter.
    """

    def __init__(
        self,
        *,
        window_seconds: int,
        budget: int,
        max_keys: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._window_seconds = window_seconds
        self._budget = budget
        self._max_keys = max_keys
        self._clock = clock
        self._spend: dict[tuple[str, int], deque[tuple[float, int]]] = {}
        self._totals: dict[tuple[str, int], int] = {}
        # O(1) LRU eviction tiers. A principal with repeated runs or a spent
        # budget is protected from one-shot cardinality spray; if every slot
        # is protected, the oldest protected principal is retired so map
        # saturation can never become a process-wide admission outage.
        self._cold: OrderedDict[tuple[str, int], None] = OrderedDict()
        self._protected: OrderedDict[tuple[str, int], None] = OrderedDict()

    def is_over_budget(self, scope: str, key: int) -> bool:
        return self._spent(scope, key) >= self._budget

    def would_exceed_budget(self, scope: str, key: int, cost: int) -> bool:
        """Whether admitting ``cost`` would cross the window ceiling."""
        return self.would_exceed_any(((scope, key, cost),))

    def would_exceed_any(self, charges: tuple[tuple[str, int, int], ...]) -> bool:
        """Whether a complete request would cross any principal's budget.

        A pure probe never rejects an unseen principal merely because the
        bounded bookkeeping map is full. ``charge_many`` makes room only once
        an admitted request actually needs to enter the ledger.
        """
        prospective: dict[tuple[str, int], int] = {}
        for scope, key, cost in charges:
            bucket = (scope, key)
            prospective[bucket] = prospective.get(bucket, 0) + cost
        return any(
            self._spent(scope, key) + cost > self._budget
            for (scope, key), cost in prospective.items()
        )

    def charge(self, scope: str, key: int, cost: int) -> None:
        self.charge_many(((scope, key, cost),))

    def charge_many(self, charges: tuple[tuple[str, int, int], ...]) -> None:
        """Atomically book all principals with bounded, tiered LRU eviction."""
        requested = {(scope, key) for scope, key, _cost in charges}
        if len(requested) > self._max_keys:
            raise RuntimeError("Simulation CPU budget cannot track this principal set")
        # Direct test/internal callers may charge without a preceding probe.
        # Lazily expire the requested keys before computing how many new slots
        # are needed; the public API performs the same work during preflight.
        for bucket in requested:
            self._prune_bucket(bucket, touch=True)
        # Iterate the request's (normally two) principals, never the 50k-key
        # ledger. ``requested - self._spend.keys()`` looks equivalent but
        # CPython's mixed set/dict-view difference walks the large operand.
        missing = {bucket for bucket in requested if bucket not in self._spend}
        while len(self._spend) + len(missing) > self._max_keys:
            if not self._evict_one(exclude=requested):
                raise RuntimeError("Simulation CPU budget principal capacity exhausted")
        charged_at = self._clock()
        for scope, key, cost in charges:
            bucket = (scope, key)
            self._spend.setdefault(bucket, deque()).append((charged_at, cost))
            self._totals[bucket] = self._totals.get(bucket, 0) + cost
            self._reclassify(bucket, touch=True)

    def clear(self) -> None:
        """Test hook: drop all recorded spend."""
        self._spend.clear()
        self._totals.clear()
        self._cold.clear()
        self._protected.clear()

    def _spent(self, scope: str, key: int) -> int:
        return self._prune_bucket((scope, key), touch=True)

    def _prune_bucket(self, bucket: tuple[str, int], *, touch: bool) -> int:
        entries = self._spend.get(bucket)
        if entries is None:
            return 0  # a pure budget probe must not allocate a dictionary key
        cutoff = self._clock() - self._window_seconds
        total = self._totals[bucket]
        while entries and entries[0][0] <= cutoff:
            _recorded_at, expired_cost = entries.popleft()
            total -= expired_cost
        if not entries:
            self._drop(bucket)
            return 0
        self._totals[bucket] = total
        self._reclassify(bucket, touch=touch)
        return total

    def _drop(self, bucket: tuple[str, int]) -> None:
        self._spend.pop(bucket, None)
        self._totals.pop(bucket, None)
        self._cold.pop(bucket, None)
        self._protected.pop(bucket, None)

    def _is_protected(self, bucket: tuple[str, int]) -> bool:
        # Two runs establish a hot principal even if both were cheap. A single
        # run that spends at least half the window budget is equally important:
        # evicting it would let successive schema-maximal runs reset their
        # accounting under one-shot spray pressure.
        return len(self._spend[bucket]) >= 2 or self._totals[bucket] * 2 >= self._budget

    def _reclassify(self, bucket: tuple[str, int], *, touch: bool) -> None:
        """Refresh one principal's eviction tier and optional LRU recency."""
        if bucket not in self._spend:
            return
        target = self._protected if self._is_protected(bucket) else self._cold
        other = self._cold if target is self._protected else self._protected
        was_in_target = bucket in target
        if not was_in_target:
            other.pop(bucket, None)
            target[bucket] = None
            if not touch:
                # Expiry can demote an old protected key. It belongs at the
                # cold tier's eviction front, not among recently used keys.
                target.move_to_end(bucket, last=False)
        elif touch:
            target.move_to_end(bucket)

    def _evict_one(self, *, exclude: set[tuple[str, int]]) -> bool:
        """Retire one least-valuable principal without scanning the key map."""
        for tier in (self._cold, self._protected):
            candidate = next((bucket for bucket in tier if bucket not in exclude), None)
            if candidate is None:
                continue
            # Expiry is lazy and bounded to the selected LRU candidate. If it
            # is still live, evict it; either path frees exactly one slot.
            self._prune_bucket(candidate, touch=False)
            if candidate in self._spend:
                self._drop(candidate)
            return True
        return False


_run_budget = _RunCostWindow(
    window_seconds=_RUN_BUDGET_WINDOW_SECONDS,
    budget=_RUN_BUDGET_UNITS,
    max_keys=_RUN_BUDGET_MAX_KEYS,
)


def _check_run_budget(farm_id: int, user_id: int, cost: int) -> None:
    """429 when admitting ``cost`` would exceed either principal's budget."""
    if _run_budget.would_exceed_any(
        (("user", user_id, cost), ("farm", farm_id, cost)),
    ):
        raise HTTPException(
            status_code=429,
            detail="Simulation CPU budget exhausted; try again shortly.",
            headers={"Retry-After": str(_RUN_BUDGET_WINDOW_SECONDS)},
        )


def _charge_run_budget(farm_id: int, user_id: int, cost: int) -> None:
    """Book a request's cost before it runs, so its own spend counts."""
    _run_budget.charge_many(
        (("user", user_id, cost), ("farm", farm_id, cost)),
    )


def _farm_run_lock(farm_id: int) -> asyncio.Lock:
    lock = _farm_run_locks.get(farm_id)
    if lock is None:
        lock = asyncio.Lock()
        _farm_run_locks[farm_id] = lock
    return lock


def _run_lock(store: dict[int, asyncio.Lock], key: int) -> asyncio.Lock:
    lock = store.get(key)
    if lock is None:
        lock = asyncio.Lock()
        store[key] = lock
    return lock


def _release_run_lock(store: dict[int, asyncio.Lock], key: int) -> None:
    """Drop an idle keyed lock so tenant/user cardinality cannot leak memory."""
    lock = store.get(key)
    if lock is not None and not lock.locked():
        store.pop(key, None)


async def _with_run_limits[RunResult](
    farm_id: int,
    user_id: int,
    operation: Callable[[], Awaitable[RunResult]],
) -> RunResult:
    """Execute one bounded CPU operation or fail fast instead of queueing."""
    lock = _farm_run_lock(farm_id)
    user_lock = _run_lock(_user_run_locks, user_id)
    # The busy check lives inside the try so the two locks just created are
    # dropped again on the 429 fast path — that path is reached precisely with
    # unseen farm/user keys (the global semaphore is full for everyone), and
    # leaking one entry per key is the cardinality growth _release_run_lock
    # exists to prevent.
    try:
        if lock.locked() or user_lock.locked() or _global_run_slots.locked():
            raise HTTPException(
                status_code=429,
                detail="Simulation capacity is busy; wait for the current run to finish.",
            )
        async with user_lock, lock, _global_run_slots:
            return await operation()
    finally:
        _release_run_lock(_farm_run_locks, farm_id)
        _release_run_lock(_user_run_locks, user_id)


async def _run_for_farm(
    farm_id: int,
    user_id: int,
    assumptions: SimulationAssumptions,
    monte_carlo: bool,
    sensitivity: bool,
    optimization: bool,
) -> SimulationResult:
    cost = _run_cost(assumptions, monte_carlo, sensitivity, optimization)

    async def run() -> SimulationResult:
        # Charged on admission, not at the gate: a request the concurrency
        # limiter turns away never runs and must not spend the budget.
        _check_run_budget(farm_id, user_id, cost)
        _charge_run_budget(farm_id, user_id, cost)
        return await _run_offloaded(assumptions, monte_carlo, sensitivity, optimization)

    return await _with_run_limits(farm_id, user_id, run)


@router.get("/defaults/breeds")
async def list_breeds(user: CurrentUser) -> BreedsOut:
    """Available breed presets and production systems (global reference data —
    any authenticated user, no farm context needed)."""
    return BreedsOut(breeds=sorted(PRESET_FACTORIES), systems=SYSTEMS)


@router.get("/defaults")
async def breed_defaults(
    user: CurrentUser, breed: str = "osmanabadi", system: System = "stall_fed"
) -> SimulationAssumptions:
    """Default assumptions for a breed + production system.

    Global reference data: any authenticated user, no farm context needed."""
    try:
        return get_preset(breed, system)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.get("/herd-snapshot")
async def herd_snapshot(
    db: DbSession, farm: CurrentFarm, perms: SimView, breed: str = "osmanabadi"
) -> HerdSnapshotOut:
    """Group the farm's ACTIVE animals into simulation starting cohorts:
    kid 0-2 m, weaner 3-5 m, grower 6 m up to breeding age, adult at breeding
    age (doe threshold = the breed's age-at-first-breeding, buck at 12 m;
    unknown age counts as adult). The bucketing itself lives in
    ``app.simulation.snapshot.herd_cohorts``."""
    try:
        afb = get_preset(breed, "stall_fed").reproduction.age_at_first_breeding_months
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    reference_date = today(farm.timezone)
    dob = func.coalesce(Animal.date_of_birth, Animal.estimated_dob)
    # Exact whole-month arithmetic matching Animal.age_months_on(), including
    # the day-of-month boundary. A NULL DOB remains NULL and is classified as
    # adult below, preserving the simulation contract.
    age_months = (
        (reference_date.year - func.extract("year", dob)) * 12
        + reference_date.month
        - func.extract("month", dob)
        - case((func.extract("day", dob) > reference_date.day, 1), else_=0)
    )
    female = Animal.sex == "F"
    male = Animal.sex == "M"
    known_age = dob.is_not(None)

    def cohort_count(predicate: ColumnElement[bool], label: str) -> ColumnElement[int]:
        return func.count(Animal.id).filter(predicate).label(label)

    counts_row = (
        await db.execute(
            select(
                cohort_count(female & (~known_age | (age_months >= afb)), "does"),
                cohort_count(male & (~known_age | (age_months >= 12)), "bucks"),
                cohort_count(female & known_age & (age_months < 3), "f_kids"),
                cohort_count(
                    female & known_age & (age_months >= 3) & (age_months < 6),
                    "f_weaners",
                ),
                cohort_count(
                    female & known_age & (age_months >= 6) & (age_months < afb),
                    "f_growers",
                ),
                cohort_count(male & known_age & (age_months < 3), "m_kids"),
                cohort_count(
                    male & known_age & (age_months >= 3) & (age_months < 6),
                    "m_weaners",
                ),
                cohort_count(
                    male & known_age & (age_months >= 6) & (age_months < 12),
                    "m_growers",
                ),
                func.count(Animal.id).label("total_head"),
            ).where(
                Animal.farm_id == farm.id,
                Animal.status == AnimalStatus.ACTIVE.value,
            )
        )
    ).one()
    return HerdSnapshotOut(**{field: int(value) for field, value in counts_row._mapping.items()})


@router.get("/calibration")
async def farm_calibration(
    db: DbSession,
    farm: CurrentFarm,
    sim_perms: SimView,
    animal_perms: AnimalsView,
    breeding_perms: BreedingView,
    kidding_perms: KiddingView,
    feeding_perms: FeedingView,
    finance_perms: FinanceView,
    breed: str = "osmanabadi",
    system: System = "stall_fed",
    lookback_months: Annotated[int, Query(ge=6, le=60)] = 24,
) -> FarmCalibrationOut:
    """Calibrate a complete model from this farm's operational evidence.

    The endpoint reads animal, breeding, kidding, feeding and finance history,
    so each corresponding view permission is required in addition to
    ``simulation.view``. Results are advisory and never mutate a saved scenario
    or farm record.
    """
    try:
        return await calibrate_farm_assumptions(
            db,
            farm,
            breed=breed,
            system=system,
            lookback_months=lookback_months,
        )
    except ValidationError as exc:
        # pydantic's ValidationError subclasses ValueError, so the branch below
        # used to swallow it and answer 400 with a raw pydantic dump — turning
        # an internal bug into what looked like a caller error. A calibrated
        # value that its own schema rejects is ours to fix, not the caller's.
        raise HTTPException(
            status_code=500,
            detail="Calibration produced an assumption set the model rejects.",
        ) from exc
    except ValueError as exc:
        # Deliberate rejections only (unknown breed / system from get_preset).
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.post("/run")
async def run_adhoc(
    payload: RunIn, user: CurrentUser, farm: CurrentFarm, perms: SimView
) -> SimulationResult:
    """Run a simulation from posted assumptions (no persistence)."""
    return await _run_for_farm(
        farm.id,
        user.id,
        payload.assumptions,
        payload.monte_carlo,
        payload.sensitivity,
        payload.optimization,
    )


@router.post("/scenarios", status_code=201)
async def create_scenario(
    payload: ScenarioCreateIn,
    response: Response,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    perms: SimManage,
    idempotency_key: IdempotencyKey = None,
) -> ScenarioOut:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required.")
    # Serialize the count-and-create decision per tenant. A plain COUNT is
    # raceable and simultaneous requests could all consume the final slot; this
    # lock keeps the quota a real bound.
    # Deliberately an ADVISORY lock, not `SELECT farms.id ... FOR UPDATE`:
    # inserting any farm-scoped child row takes FOR KEY SHARE on that Farm row,
    # so a Farm row lock held across other locks inverts against every
    # animal-first write and PostgreSQL deadlocks. The advisory lock serializes
    # the same counter, self-conflicts exactly as the row lock did, and never
    # conflicts with an FK key-share lock. It is still taken before the
    # idempotency claim so distinct keyed requests cannot lock-upgrade.
    await db.execute(
        select(func.pg_advisory_xact_lock(literal(SCENARIO_QUOTA_LOCK_NAMESPACE), literal(farm.id)))
    )

    async def mutate() -> ScenarioOut:
        scenario_count = (
            await db.execute(
                select(func.count())
                .select_from(SimulationScenario)
                .where(SimulationScenario.farm_id == farm.id)
            )
        ).scalar_one()
        if scenario_count >= get_settings().max_simulation_scenarios_per_farm:
            raise HTTPException(status_code=409, detail=SCENARIO_CAPACITY_REASON)
        await _check_name_free(db, farm.id, name)
        scenario = SimulationScenario(
            farm_id=farm.id,
            name=name,
            notes=payload.notes,
            assumptions=payload.assumptions.model_dump_json(),
            created_by_id=user.id,
        )
        db.add(scenario)
        try:
            await db.flush()
        except IntegrityError:  # concurrent/manual name collision
            raise HTTPException(
                status_code=400, detail="A scenario with that name already exists."
            ) from None
        return _scenario_out(scenario)

    return await execute_idempotent(
        db,
        http_response=response,
        key=idempotency_key,
        farm_id=farm.id,
        actor_id=user.id,
        operation="simulation.scenarios.create",
        payload=payload,
        path_identity={},
        success_status=201,
        response_type=ScenarioOut,
        mutate=mutate,
    )


@router.get("/scenarios")
async def list_scenarios(
    db: DbSession,
    farm: CurrentFarm,
    perms: SimView,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)] = 0,
) -> ScenarioListOut:
    total = (
        await db.execute(
            select(func.count())
            .select_from(SimulationScenario)
            .where(SimulationScenario.farm_id == farm.id)
        )
    ).scalar_one()
    result = await db.execute(
        select(SimulationScenario)
        .where(SimulationScenario.farm_id == farm.id)
        # Preserve the endpoint's original oldest-first ordering and make the
        # page boundary deterministic even when timestamps are identical.
        .order_by(SimulationScenario.id)
        .offset(offset)
        .limit(limit)
    )
    out: list[ScenarioOut] = []
    for scenario in result.scalars():
        try:
            out.append(_scenario_out(scenario, allow_invalid=True))
        except HTTPException:  # pragma: no cover - allow_invalid handles validation
            raise
    return ScenarioListOut(items=out, total=total, limit=limit, offset=offset)


@router.get("/scenarios/compare")
async def compare_scenarios(
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    perms: SimView,
    ids: Annotated[str, Query(max_length=128)],
) -> ScenarioCompareOut:
    """Run stored scenarios deterministically side by side (``ids=1,2``).

    Duplicate ids are collapsed (a repeated id must not re-run a simulation);
    a single distinct id is accepted and simply returns that scenario's run;
    more than MAX_COMPARE_IDS distinct ids is a 400."""
    try:
        id_list = list(dict.fromkeys(int(part) for part in ids.split(",") if part.strip()))
    except ValueError:
        raise HTTPException(
            status_code=400, detail="ids must be comma-separated integers"
        ) from None
    if not id_list:
        raise HTTPException(status_code=400, detail="ids must name at least one scenario")
    if any(not 1 <= scenario_id <= MAX_INT32_ID for scenario_id in id_list):
        raise HTTPException(
            status_code=400,
            detail="ids must be positive PostgreSQL integer scenario ids",
        )
    if len(id_list) > MAX_COMPARE_IDS:
        raise HTTPException(
            status_code=400, detail=f"compare is limited to {MAX_COMPARE_IDS} scenarios"
        )

    async def run_compare() -> ScenarioCompareOut:
        scenarios = [await _get_scenario(db, farm.id, scenario_id) for scenario_id in id_list]
        loaded = [_load_assumptions(scenario) for scenario in scenarios]
        cost = sum(_run_cost(a, False, False, False) for a in loaded)
        # Charged once the scenarios are known, before any engine work starts.
        _check_run_budget(farm.id, user.id, cost)
        _charge_run_budget(farm.id, user.id, cost)
        return ScenarioCompareOut(
            scenarios=[_scenario_out(scenario) for scenario in scenarios],
            results=[await _run_offloaded(a, False, False, False) for a in loaded],
        )

    return await _with_run_limits(farm.id, user.id, run_compare)


@router.get("/scenarios/{scenario_id}")
async def get_scenario(
    db: DbSession, farm: CurrentFarm, perms: SimView, scenario_id: int
) -> ScenarioOut:
    return _scenario_out(await _get_scenario(db, farm.id, scenario_id))


@router.patch("/scenarios/{scenario_id}")
async def update_scenario(
    payload: ScenarioUpdateIn,
    db: DbSession,
    farm: CurrentFarm,
    perms: SimManage,
    scenario_id: int,
) -> ScenarioOut:
    scenario = await _get_scenario(db, farm.id, scenario_id)
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Name is required.")
        await _check_name_free(db, farm.id, name, exclude_id=scenario.id)
        scenario.name = name
    if payload.notes is not None:
        scenario.notes = payload.notes
    if payload.assumptions is not None:
        scenario.assumptions = payload.assumptions.model_dump_json()
    try:
        await db.commit()
    except IntegrityError:  # concurrent rename collided with another scenario
        await db.rollback()
        raise HTTPException(
            status_code=400, detail="A scenario with that name already exists."
        ) from None
    # The write is already committed, so a row whose stored assumptions predate
    # a schema tightening must not answer 422 as though nothing happened —
    # report it the way the list endpoint does, with valid=False.
    return _scenario_out(scenario, allow_invalid=True)


@router.delete("/scenarios/{scenario_id}", status_code=204)
async def delete_scenario(
    db: DbSession, farm: CurrentFarm, perms: SimManage, scenario_id: int
) -> Response:
    scenario = await _get_scenario(db, farm.id, scenario_id)
    await db.delete(scenario)
    await db.commit()
    return Response(status_code=204)


@router.post("/scenarios/{scenario_id}/run")
async def run_scenario(
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    perms: SimView,
    scenario_id: int,
    monte_carlo: bool = False,
    sensitivity: bool = False,
    optimization: bool = False,
) -> SimulationResult:
    """Run a stored scenario's assumptions (optionally with MC / sensitivity)."""
    scenario = await _get_scenario(db, farm.id, scenario_id)
    return await _run_for_farm(
        farm.id,
        user.id,
        _load_assumptions(scenario),
        monte_carlo,
        sensitivity,
        optimization,
    )
