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

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import ValidationError
from sqlalchemy import case, func, literal, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.elements import ColumnElement

from ..core.config import get_settings
from ..deps import CurrentFarm, CurrentUser, DbSession, require_perm
from ..models import Animal, AnimalStatus, SimulationScenario
from ..schemas.common import COMMON_ERROR_RESPONSES, MAX_INT32_ID, MAX_PAGE_OFFSET, ErrorOut
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
from ..simulation.vocabulary import GOAT_NOUNS, SpeciesNouns, nouns_for_farm_type
from ..utils import today
from ._run_limits import (
    _charge_run_budget,
    _check_run_budget,
    _finite_payload,
    _offload,
    _with_run_limits,
)

router = APIRouter(prefix="/api/simulation", tags=["simulation"], responses=COMMON_ERROR_RESPONSES)

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
            revision=scenario.revision,
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
        revision=scenario.revision,
        created_at=scenario.created_at,
        updated_at=scenario.updated_at,
    )


async def _get_scenario(
    db: DbSession, farm_id: int, scenario_id: int, *, for_update: bool = False
) -> SimulationScenario:
    """Farm-scoped fetch: unknown or foreign ids both 404."""
    if not 1 <= scenario_id <= MAX_INT32_ID:
        scenario = None
    else:
        stmt = select(SimulationScenario).where(
            SimulationScenario.id == scenario_id,
            SimulationScenario.farm_id == farm_id,
        )
        if for_update:
            stmt = stmt.execution_options(populate_existing=True).with_for_update()
        scenario = (await db.execute(stmt)).scalar_one_or_none()
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


def _run(
    assumptions: SimulationAssumptions,
    monte_carlo: bool,
    sensitivity: bool,
    optimization: bool,
    nouns: SpeciesNouns = GOAT_NOUNS,
) -> SimulationResult:
    result = run_simulation(
        assumptions,
        with_monte_carlo=monte_carlo,
        with_sensitivity=sensitivity,
        with_optimization=optimization,
        nouns=nouns,
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
    nouns: SpeciesNouns = GOAT_NOUNS,
) -> SimulationResult:
    """Offload one standard run (see ``_offload`` for the machinery)."""
    return await _offload(
        lambda: _run(assumptions, monte_carlo, sensitivity, optimization, nouns)
    )


# One run per farm and per user, plus a process-wide ceiling. This prevents a
# user with several farms from consuming the whole shared threadpool. A
# distributed job queue remains the deployment path for multi-replica scale.
# The lock stores, global slots, CPU budget window and offload machinery now
# live in ``._run_limits`` (shared with the planner router); they are imported
# above so tests that reach for them via this module keep working.

_BREAK_EVEN_PASSES = 52  # npv_at(0), npv_at(schema ceiling) + 50 bisection steps
_SENSITIVITY_PASSES = 17  # base + 8 parameters x (low, high)


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


async def _run_for_farm(
    farm_id: int,
    user_id: int,
    assumptions: SimulationAssumptions,
    monte_carlo: bool,
    sensitivity: bool,
    optimization: bool,
    nouns: SpeciesNouns = GOAT_NOUNS,
) -> SimulationResult:
    cost = _run_cost(assumptions, monte_carlo, sensitivity, optimization)

    async def run() -> SimulationResult:
        # Charged on admission, not at the gate: a request the concurrency
        # limiter turns away never runs and must not spend the budget.
        _check_run_budget(farm_id, user_id, cost)
        _charge_run_budget(farm_id, user_id, cost)
        return await _run_offloaded(
            assumptions, monte_carlo, sensitivity, optimization, nouns
        )

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
    db: DbSession,
    farm: CurrentFarm,
    perms: SimView,
    # Reads the farm's animal register (nine per-sex/per-age cohort counts plus
    # total head): the same-table reads the sibling /calibration endpoint
    # gates behind animals.view, so a consultant role holding only
    # simulation.view must not be able to reconstruct herd structure here.
    animal_perms: AnimalsView,
    breed: str = "osmanabadi",
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


@router.get(
    "/calibration",
    responses={
        500: {"model": ErrorOut, "description": "Calibration data is internally inconsistent"},
    },
)
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
    payload: RunIn,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    perms: SimView,
) -> SimulationResult:
    """Run a simulation from posted assumptions (no persistence)."""
    farm_id = farm.id
    user_id = user.id
    # Immutable request snapshot, captured before the rollback below expires
    # the ORM row (a post-rollback attribute read re-queries the detached
    # object and raises MissingGreenlet inside the worker thread).
    nouns = nouns_for_farm_type(farm.farm_type)
    # Unsafe-request authorization deliberately pins Membership/User/Role rows
    # only for the database mutation it authorizes. A simulation is CPU-only
    # after admission; retaining that transaction for a worst-case ~25-second
    # worker run needlessly blocks revocation/role changes and keeps one pool
    # connection checked out. Capture the immutable request snapshot, then
    # release both the authorization locks and connection before CPU work.
    await db.rollback()
    return await _run_for_farm(
        farm_id,
        user_id,
        payload.assumptions,
        payload.monte_carlo,
        payload.sensitivity,
        payload.optimization,
        nouns,
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

    farm_id = farm.id
    user_id = user.id
    nouns = nouns_for_farm_type(farm.farm_type)

    async def run_compare() -> ScenarioCompareOut:
        scenarios = [await _get_scenario(db, farm_id, scenario_id) for scenario_id in id_list]
        loaded = [_load_assumptions(scenario) for scenario in scenarios]
        scenario_snapshots = [_scenario_out(scenario) for scenario in scenarios]
        cost = sum(_run_cost(a, False, False, False) for a in loaded)
        # The response models and validated assumptions are detached snapshots
        # now. Do not pin a pool connection for the sequential off-thread runs.
        await db.rollback()
        # Charged once the scenarios are known, before any engine work starts.
        _check_run_budget(farm_id, user_id, cost)
        _charge_run_budget(farm_id, user_id, cost)
        return ScenarioCompareOut(
            scenarios=scenario_snapshots,
            results=[
                await _run_offloaded(a, False, False, False, nouns) for a in loaded
            ],
        )

    return await _with_run_limits(farm_id, user_id, run_compare)


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
    scenario = await _get_scenario(db, farm.id, scenario_id, for_update=True)
    if scenario.revision != payload.expected_revision:
        raise HTTPException(
            status_code=409,
            detail="This scenario changed since you opened it; refresh before saving.",
        )
    changed = False
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Name is required.")
        await _check_name_free(db, farm.id, name, exclude_id=scenario.id)
        scenario.name = name
        changed = True
    if payload.notes is not None:
        scenario.notes = payload.notes
        changed = True
    if payload.assumptions is not None:
        scenario.assumptions = payload.assumptions.model_dump_json()
        changed = True
    if changed:
        scenario.revision += 1
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
    farm_id = farm.id
    user_id = user.id
    nouns = nouns_for_farm_type(farm.farm_type)
    scenario = await _get_scenario(db, farm_id, scenario_id)
    assumptions = _load_assumptions(scenario)
    # The validated assumptions are a complete point-in-time scenario snapshot;
    # the simulation no longer needs the ORM row or its authorization
    # transaction. Release the connection and auth SHARE locks before CPU work.
    await db.rollback()
    return await _run_for_farm(
        farm_id,
        user_id,
        assumptions,
        monte_carlo,
        sensitivity,
        optimization,
        nouns,
    )
