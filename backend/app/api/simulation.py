"""Simulation: breed/system defaults, herd snapshot, ad-hoc runs, and
farm-scoped saved scenarios (CRUD + run + compare).

The engine itself is pure Python (``app.simulation``); this router only handles
transport, persistence (assumptions stored as JSON text, like
``Role.permissions``) and RBAC. Runs are synchronous CPU work — horizon and
Monte Carlo runs are bounded by the assumption schema (monte_carlo_runs <=
2000), which still leaves a worst-case run (240-month horizon + max Monte
Carlo + sensitivity + break-even) at roughly 12 s of single-threaded CPU.
Every run is offloaded to a worker thread so it can't block the event loop,
and each farm is limited to one in-flight run: concurrent run/compare
requests for the same farm get a 429 instead of piling onto the threadpool.
"""

import asyncio
import json
import math
from collections.abc import Mapping
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from ..deps import CurrentFarm, CurrentUser, DbSession, require_perm
from ..models import Animal, AnimalStatus, SimulationScenario
from ..schemas.common import MAX_INT32_ID
from ..schemas.simulation import (
    BreedsOut,
    HerdSnapshotOut,
    RunIn,
    ScenarioCompareOut,
    ScenarioCreateIn,
    ScenarioOut,
    ScenarioUpdateIn,
)
from ..simulation.assumptions import SimulationAssumptions
from ..simulation.defaults import PRESET_FACTORIES, SYSTEMS, System, get_preset
from ..simulation.engine import run_simulation
from ..simulation.results import SimulationResult
from ..simulation.snapshot import herd_cohorts

router = APIRouter(prefix="/api/simulation", tags=["simulation"])

SimView = Annotated[set[str], Depends(require_perm("simulation.view"))]
SimManage = Annotated[set[str], Depends(require_perm("simulation.manage"))]

# A compare re-runs a full simulation per id — cap the work per request.
MAX_COMPARE_IDS = 5


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


def _scenario_out(scenario: SimulationScenario) -> ScenarioOut:
    """ORM → schema; assumptions are JSON text on the row."""
    return ScenarioOut(
        id=scenario.id,
        farm_id=scenario.farm_id,
        name=scenario.name,
        notes=scenario.notes,
        assumptions=_load_assumptions(scenario),
        created_at=scenario.created_at,
        updated_at=scenario.updated_at,
    )


async def _get_scenario(db: DbSession, farm_id: int, scenario_id: int) -> SimulationScenario:
    """Farm-scoped fetch: unknown or foreign ids both 404."""
    scenario = (
        await db.get(SimulationScenario, scenario_id) if scenario_id <= MAX_INT32_ID else None
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
    assumptions: SimulationAssumptions, monte_carlo: bool, sensitivity: bool
) -> SimulationResult:
    result = run_simulation(
        assumptions,
        with_monte_carlo=monte_carlo,
        with_sensitivity=sensitivity,
    )
    # Defense in depth past the input caps: bounded inputs can still overflow
    # derived math (a near-zero fodder yield makes the land requirement 1/ε →
    # inf), and Starlette's allow_nan=False JSONResponse turns any non-finite
    # float in the payload into a 500. Answer a clean 422 instead.
    if not _finite_payload(result.model_dump()):
        raise HTTPException(status_code=422, detail="These inputs produce non-finite results.")
    return result


async def _run_offloaded(
    assumptions: SimulationAssumptions, monte_carlo: bool, sensitivity: bool
) -> SimulationResult:
    """Runs are synchronous CPU work — push them off the event loop so a long
    horizon / Monte Carlo batch can't stall every other request."""
    return await run_in_threadpool(_run, assumptions, monte_carlo, sensitivity)


# One in-flight run per farm (keyed by farm id). A worst-case run holds a CPU
# core for ~12 s; without this a worker holding only simulation.view could
# keep every threadpool worker busy by firing max-size runs repeatedly.
# Locks are dropped when no one holds them (audit 2026-08-08 LOW-4): a bare
# dict grew one lock per farm ever seen; sweeping unheld locks caps memory
# while keeping the "one run per farm" guarantee for concurrent requests.
_farm_run_locks: dict[int, asyncio.Lock] = {}


def _farm_run_lock(farm_id: int) -> asyncio.Lock:
    lock = _farm_run_locks.get(farm_id)
    if lock is None:
        lock = asyncio.Lock()
        _farm_run_locks[farm_id] = lock
    return lock


def _release_farm_run_lock(farm_id: int) -> None:
    """Drop the lock if no one else holds it (memory hygiene)."""
    lock = _farm_run_locks.get(farm_id)
    if lock is not None and not lock.locked():
        _farm_run_locks.pop(farm_id, None)


async def _run_for_farm(
    farm_id: int, assumptions: SimulationAssumptions, monte_carlo: bool, sensitivity: bool
) -> SimulationResult:
    """Run at most one simulation per farm at a time (429 while busy)."""
    lock = _farm_run_lock(farm_id)
    if lock.locked():
        raise HTTPException(
            status_code=429,
            detail="A simulation run is already in progress for this farm; wait for it to finish.",
        )
    try:
        async with lock:
            return await _run_offloaded(assumptions, monte_carlo, sensitivity)
    finally:
        _release_farm_run_lock(farm_id)


@router.get("/defaults/breeds")
async def list_breeds(user: CurrentUser) -> BreedsOut:
    """Available breed presets and production systems (global reference data —
    any authenticated user, no farm context needed)."""
    return BreedsOut(breeds=sorted(PRESET_FACTORIES), systems=SYSTEMS)


@router.get("/defaults")
async def breed_defaults(
    user: CurrentUser, breed: str = "osmanabadi", system: System = "stall_fed"
) -> SimulationAssumptions:
    """Default assumptions for a breed + production system (400 on unknown breed;
    422 on an unknown system — the Literal query type validates it, AUDIT 4-L2).

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
    result = await db.execute(
        select(Animal).where(Animal.farm_id == farm.id, Animal.status == AnimalStatus.ACTIVE.value)
    )
    counts = herd_cohorts(
        ((animal.sex, animal.age_months) for animal in result.scalars()), doe_adult_age=afb
    )
    return HerdSnapshotOut(**counts, total_head=sum(counts.values()))


@router.post("/run")
async def run_adhoc(payload: RunIn, farm: CurrentFarm, perms: SimView) -> SimulationResult:
    """Run a simulation from posted assumptions (no persistence)."""
    return await _run_for_farm(
        farm.id, payload.assumptions, payload.monte_carlo, payload.sensitivity
    )


@router.post("/scenarios", status_code=201)
async def create_scenario(
    payload: ScenarioCreateIn,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    perms: SimManage,
) -> ScenarioOut:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required.")
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
        await db.commit()
    except IntegrityError:  # concurrent create with the same name
        await db.rollback()
        raise HTTPException(
            status_code=400, detail="A scenario with that name already exists."
        ) from None
    return _scenario_out(scenario)


@router.get("/scenarios")
async def list_scenarios(db: DbSession, farm: CurrentFarm, perms: SimView) -> list[ScenarioOut]:
    result = await db.execute(
        select(SimulationScenario)
        .where(SimulationScenario.farm_id == farm.id)
        .order_by(SimulationScenario.id)
    )
    out: list[ScenarioOut] = []
    for scenario in result.scalars():
        try:
            out.append(_scenario_out(scenario))
        except HTTPException:
            # Stale row stored under a looser schema — skip it rather than
            # 500 the whole list (get/run/compare still surface the 422).
            continue
    return out


@router.get("/scenarios/compare")
async def compare_scenarios(
    db: DbSession, farm: CurrentFarm, perms: SimView, ids: str
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
    if len(id_list) > MAX_COMPARE_IDS:
        raise HTTPException(
            status_code=400, detail=f"compare is limited to {MAX_COMPARE_IDS} scenarios"
        )
    lock = _farm_run_lock(farm.id)
    if lock.locked():
        raise HTTPException(
            status_code=429,
            detail="A simulation run is already in progress for this farm; wait for it to finish.",
        )
    async with lock:  # one lock for the whole batch (a compare is N runs)
        scenarios = [await _get_scenario(db, farm.id, scenario_id) for scenario_id in id_list]
        return ScenarioCompareOut(
            scenarios=[_scenario_out(scenario) for scenario in scenarios],
            results=[
                await _run_offloaded(_load_assumptions(scenario), False, False)
                for scenario in scenarios
            ],
        )


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
    return _scenario_out(scenario)


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
    farm: CurrentFarm,
    perms: SimView,
    scenario_id: int,
    monte_carlo: bool = False,
    sensitivity: bool = False,
) -> SimulationResult:
    """Run a stored scenario's assumptions (optionally with MC / sensitivity)."""
    scenario = await _get_scenario(db, farm.id, scenario_id)
    return await _run_for_farm(farm.id, _load_assumptions(scenario), monte_carlo, sensitivity)
