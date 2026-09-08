"""Daily operations simulation (Business → Ops Sim): one farm, day by day.

``POST /api/ops-sim/run`` answers the operational question the monthly
planner cannot: with this herd standing in these buckets — one building per
bucket, each with its dedicated vet area — what exactly happens on day N?
Feed mixed and delivered per building in the 40/20/40 shift split, pens
cleaned morning and night with verification, the vet's due-duties round, and
every animal's legal lifecycle move with the workflow context that caused it.

The engine's tasks and buildings are the seeded goat lifecycle. Runs are
priced, admitted and offloaded exactly like
simulation runs (``_run_limits``): one in-flight run per farm/user, a sliding
CPU budget, and a hard 422 on non-finite results.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError

from ..deps import CurrentFarm, CurrentUser, DbSession, require_perm
from ..schemas.common import COMMON_ERROR_RESPONSES
from ..schemas.ops_simulation import (
    MAX_LEDGER_HEAD_DAYS,
    DailyOpsRunIn,
    DailyOpsRunOut,
)
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

    if payload.include_ledger:
        head_days = payload.horizon_days * len(payload.animals)
        if head_days > MAX_LEDGER_HEAD_DAYS:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"The day-by-day ledger is available for runs up to "
                    f"{MAX_LEDGER_HEAD_DAYS:,} head-days; this run is {head_days:,}. "
                    "Re-run without the ledger or narrow the herd or horizon."
                ),
            )

    # Priced by simulated days × herd size: engine work, result size and (with
    # the ledger) response size all grow with both, so a per-day-only price let
    # one 500-head year cost the same budget as a single-animal run.
    cost = payload.horizon_days * (1 + len(payload.animals) // 10)

    def work() -> DailyOpsRunOut:
        result = run_daily_ops(daily_input)
        # model_dump/ledger of a maximal herd is seconds of CPU and tens of MB;
        # serialize off the event loop (same defense as /run: a non-finite
        # figure would crash JSON encoding).
        dumped = result.model_dump()
        if not _finite_payload(dumped):
            raise HTTPException(status_code=422, detail="These inputs produce non-finite results.")
        ledger = build_daily_ledger(result) if payload.include_ledger else None
        return DailyOpsRunOut(result=result, ledger=ledger)

    async def run() -> DailyOpsRunOut:
        _check_run_budget(farm_id, user_id, cost)
        _charge_run_budget(farm_id, user_id, cost)
        return await _offload(work)

    return await _with_run_limits(farm_id, user_id, run)
