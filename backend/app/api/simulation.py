"""Simulation: breed/system defaults, herd snapshot, ad-hoc runs, and
farm-scoped saved scenarios (CRUD + run + compare).

The engine itself is pure Python (``app.simulation``); this router only handles
transport, persistence (assumptions stored as JSON text, like
``Role.permissions``) and RBAC. Runs are synchronous CPU work — horizon and
Monte Carlo runs are bounded by the assumption schema (monte_carlo_runs <=
2000), which keeps a worst-case run in the low seconds.
"""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..deps import CurrentFarm, CurrentUser, DbSession, require_perm
from ..models import Animal, AnimalStatus, Sex, SimulationScenario
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
from ..simulation.defaults import PRESET_FACTORIES, get_preset
from ..simulation.engine import run_simulation
from ..simulation.results import SimulationResult

router = APIRouter(prefix="/api/simulation", tags=["simulation"])

SimView = Annotated[set[str], Depends(require_perm("simulation.view"))]
SimManage = Annotated[set[str], Depends(require_perm("simulation.manage"))]

_SYSTEMS = ["stall_fed", "semi_intensive"]


def _scenario_out(scenario: SimulationScenario) -> ScenarioOut:
    """ORM → schema; assumptions are JSON text on the row."""
    return ScenarioOut(
        id=scenario.id,
        farm_id=scenario.farm_id,
        name=scenario.name,
        notes=scenario.notes,
        assumptions=SimulationAssumptions.model_validate(json.loads(scenario.assumptions)),
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


def _run(
    assumptions: SimulationAssumptions, monte_carlo: bool, sensitivity: bool
) -> SimulationResult:
    return run_simulation(
        assumptions,
        with_monte_carlo=monte_carlo,
        with_sensitivity=sensitivity,
    )


@router.get("/defaults/breeds")
async def list_breeds(perms: SimView) -> BreedsOut:
    """Available breed presets and production systems."""
    return BreedsOut(breeds=sorted(PRESET_FACTORIES), systems=_SYSTEMS)


@router.get("/defaults")
async def breed_defaults(
    perms: SimView, breed: str = "osmanabadi", system: str = "stall_fed"
) -> SimulationAssumptions:
    """Default assumptions for a breed + production system (400 on unknown)."""
    try:
        return get_preset(breed, system)  # type: ignore[arg-type]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.get("/herd-snapshot")
async def herd_snapshot(db: DbSession, farm: CurrentFarm, perms: SimView) -> HerdSnapshotOut:
    """Group the farm's ACTIVE animals into simulation starting cohorts:
    kid 0-2 m, weaner 3-5 m, grower 6-11 m, doe/buck 12+ m (or unknown age)."""
    result = await db.execute(
        select(Animal).where(Animal.farm_id == farm.id, Animal.status == AnimalStatus.ACTIVE.value)
    )
    counts = dict.fromkeys(
        ["does", "bucks", "f_kids", "f_weaners", "f_growers", "m_kids", "m_weaners", "m_growers"],
        0,
    )
    for animal in result.scalars():
        age = animal.age_months
        if age is None or age >= 12:
            counts["does" if animal.sex == Sex.F.value else "bucks"] += 1
        elif age >= 6:
            counts["f_growers" if animal.sex == Sex.F.value else "m_growers"] += 1
        elif age >= 3:
            counts["f_weaners" if animal.sex == Sex.F.value else "m_weaners"] += 1
        else:
            counts["f_kids" if animal.sex == Sex.F.value else "m_kids"] += 1
    return HerdSnapshotOut(**counts, total_head=sum(counts.values()))


@router.post("/run")
async def run_adhoc(payload: RunIn, farm: CurrentFarm, perms: SimView) -> SimulationResult:
    """Run a simulation from posted assumptions (no persistence)."""
    return _run(payload.assumptions, payload.monte_carlo, payload.sensitivity)


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
    return [_scenario_out(scenario) for scenario in result.scalars()]


@router.get("/scenarios/compare")
async def compare_scenarios(
    db: DbSession, farm: CurrentFarm, perms: SimView, ids: str
) -> ScenarioCompareOut:
    """Run 2+ stored scenarios deterministically side by side (``ids=1,2``)."""
    try:
        id_list = [int(part) for part in ids.split(",") if part.strip()]
    except ValueError:
        raise HTTPException(
            status_code=400, detail="ids must be comma-separated integers"
        ) from None
    if not id_list:
        raise HTTPException(status_code=400, detail="ids must name at least one scenario")
    scenarios = [await _get_scenario(db, farm.id, scenario_id) for scenario_id in id_list]
    return ScenarioCompareOut(
        scenarios=[_scenario_out(scenario) for scenario in scenarios],
        results=[
            _run(
                SimulationAssumptions.model_validate(json.loads(scenario.assumptions)), False, False
            )
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
    assumptions = SimulationAssumptions.model_validate(json.loads(scenario.assumptions))
    return _run(assumptions, monte_carlo, sensitivity)
