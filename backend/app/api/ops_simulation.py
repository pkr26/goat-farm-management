"""Daily operations simulation (Business → Ops Sim): one farm, day by day.

``POST /api/ops-sim/run`` answers the operational question the monthly
planner cannot: with this herd standing in these buckets — one building per
bucket, each with its dedicated vet area — what exactly happens on day N?
Feed mixed and delivered per building in the 40/20/40 shift split, pens
cleaned morning and night with verification, the vet's due-duties round, and
every animal's legal lifecycle move with the workflow context that caused it.

Goat farms only in this version: the engine's tasks and buildings are the
seeded goat lifecycle, so a buffalo dairy gets a deliberate 422 rather than a
mislabelled simulation. Runs are priced, admitted and offloaded exactly like
simulation runs (``_run_limits``): one in-flight run per farm/user, a sliding
CPU budget, and a hard 422 on non-finite results.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError

from ..deps import CurrentFarm, CurrentUser, DbSession, require_perm
from ..models.species import GOAT
from ..schemas.common import COMMON_ERROR_RESPONSES
from ..schemas.ops_simulation import DailyOpsRunIn, DailyOpsRunOut
from ..simulation.daily_ops import (
    DailyOpsInput,
    build_daily_ledger,
    run_daily_ops,
)
from ._run_limits import (
    _charge_run_budget,
    _check_run_budget,
    _finite_payload,
    _offload,
    _with_run_limits,
)

router = APIRouter(prefix="/api/ops-sim", tags=["ops-simulation"], responses=COMMON_ERROR_RESPONSES)

SimView = Annotated[set[str], Depends(require_perm("simulation.view"))]

GOAT_ONLY_DETAIL = (
    "The daily operations simulation currently models goat farms only; "
    "dairy milking duties arrive with the buffalo version."
)


@router.post("/run", response_model=DailyOpsRunOut)
async def run_daily_ops_simulation(
    payload: DailyOpsRunIn,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    perms: SimView,
) -> DailyOpsRunOut:
    """Simulate the farm day by day: the full duty schedule per building,
    every bucket move with its cause, feed manifests, births and exits."""
    if farm.farm_type != GOAT:
        raise HTTPException(status_code=422, detail=GOAT_ONLY_DETAIL)
    farm_id = farm.id
    user_id = user.id
    # Snapshot/rollback before any CPU work: the auth transaction must not
    # span the simulation (same reason as the planner endpoints).
    await db.rollback()

    try:
        daily_input = DailyOpsInput(
            start_date=payload.start_date,
            horizon_days=payload.horizon_days,
            seed=payload.seed,
            animals=payload.animals,
            params=payload.params,
        )
    except ValidationError as exc:
        # Herd-coherence rejections (sex/bucket mismatch, gestation windows,
        # overstayed quarantine, duplicate tags) are client-input problems.
        # Keep only JSON-serializable fields: raw errors carry the whole
        # input (including model objects) under "input"/"ctx".
        errors = [
            {key: value for key, value in error.items() if key in ("type", "loc", "msg")}
            for error in exc.errors(include_url=False)
        ]
        raise HTTPException(status_code=422, detail=errors[:3]) from exc

    # Priced per simulated day: each day plans feed, cleaning and duties for
    # at most ten buildings, so this stays deliberately cheap next to a
    # monthly engine run while still throttling runaway horizons.
    cost = payload.horizon_days

    async def run() -> DailyOpsRunOut:
        _check_run_budget(farm_id, user_id, cost)
        _charge_run_budget(farm_id, user_id, cost)
        result = await _offload(lambda: run_daily_ops(daily_input))
        ledger = build_daily_ledger(result) if payload.include_ledger else None
        # Same defense as /run: a non-finite figure would crash JSON encoding.
        if not _finite_payload(result.model_dump()):
            raise HTTPException(status_code=422, detail="These inputs produce non-finite results.")
        return DailyOpsRunOut(result=result, ledger=ledger)

    return await _with_run_limits(farm_id, user_id, run)
