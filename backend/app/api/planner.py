"""Planner (Business → Planner): target-based backward planning.

Three concerns live here, all built on the pure ``app.simulation`` engine:

- ``POST /plan`` — the farmer's question, in the farmer's calendar: "I want
  to sell 200 animals in Jan 2027; what must I do starting now?" The engine
  works the biology backward (breeding, gestation, mortality, culling, growth
  stages) and answers with feasibility, the month-by-month stage plan, dated
  actions and per-target requirement chains.
- ``/plans`` CRUD — saved target lists plus the assumptions they run against;
  the report itself is always recomputed on open.

Runs are priced, admitted and offloaded exactly like simulation runs
(``_run_limits``): one in-flight run per farm/user, a sliding CPU budget, and
a hard 422 on non-finite results.
"""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import ValidationError
from sqlalchemy import func, literal, select
from sqlalchemy.exc import IntegrityError

from ..core.config import get_settings
from ..deps import CurrentFarm, CurrentUser, DbSession, require_perm
from ..models import PlannerPlan
from ..schemas.common import COMMON_ERROR_RESPONSES, MAX_INT32_ID, MAX_PAGE_OFFSET
from ..schemas.planner import (
    BackwardPlanIn,
    BackwardPlanReport,
    PlannerPlanCreateIn,
    PlannerPlanListOut,
    PlannerPlanOut,
    PlannerPlanUpdateIn,
    PlannerTargetIn,
)
from ..services import IdempotencyKey, execute_idempotent
from ..simulation.assumptions import SimulationAssumptions
from ..simulation.backward_planner import (
    PlannerTarget,
    build_backward_plan,
    month_offset,
)
from ..simulation.engine import run_simulation
from ..simulation.planner import build_dpr_markdown
from ..simulation.vocabulary import GOAT_NOUNS
from ._run_limits import (
    _charge_run_budget,
    _check_run_budget,
    _finite_payload,
    _offload,
    _with_run_limits,
)

router = APIRouter(prefix="/api/planner", tags=["planner"], responses=COMMON_ERROR_RESPONSES)

# Namespace for the per-farm plan-quota mutex. Advisory lock keys are global
# to the database (4711 tasks, 4712 team provisioning, 4713 simulation
# scenarios), so this counter must keep 4715 to itself.
PLAN_QUOTA_LOCK_NAMESPACE = 4715

PLAN_CAPACITY_REASON = "This farm has reached its saved-plan limit."

SimView = Annotated[set[str], Depends(require_perm("simulation.view"))]
SimManage = Annotated[set[str], Depends(require_perm("simulation.manage"))]


def _load_plan_parts(plan: PlannerPlan) -> tuple[list[PlannerTarget], SimulationAssumptions]:
    """Stored JSON → validated models.

    Rows stored under an older, looser schema can fail revalidation after the
    schema tightens. That must surface as a 422 for the affected plan — and be
    skipped in the list endpoint — never a bare 500 for the whole farm.
    """
    try:
        targets = [PlannerTarget.model_validate(item) for item in json.loads(plan.targets)]
    except (json.JSONDecodeError, ValidationError) as exc:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Stored targets for plan {plan.name!r} no longer validate against "
                "the current schema; update or delete it."
            ),
        ) from exc
    try:
        assumptions = SimulationAssumptions.model_validate(json.loads(plan.assumptions))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Stored assumptions for plan {plan.name!r} no longer validate against "
                "the current schema; update or delete it."
            ),
        ) from exc
    if not targets:
        raise HTTPException(
            status_code=422,
            detail=(f"Plan {plan.name!r} has no targets; update or delete it."),
        )
    return targets, assumptions


def _plan_out(plan: PlannerPlan, *, allow_invalid: bool = False) -> PlannerPlanOut:
    """ORM → schema; targets and assumptions are JSON text on the row."""
    try:
        targets, assumptions = _load_plan_parts(plan)
    except HTTPException as exc:
        if not allow_invalid:
            raise
        return PlannerPlanOut(
            id=plan.id,
            farm_id=plan.farm_id,
            name=plan.name,
            notes=plan.notes,
            start_year_month=plan.start_year_month,
            targets=None,
            assumptions=None,
            valid=False,
            validation_error=str(exc.detail),
            revision=plan.revision,
            created_at=plan.created_at,
            updated_at=plan.updated_at,
        )
    return PlannerPlanOut(
        id=plan.id,
        farm_id=plan.farm_id,
        name=plan.name,
        notes=plan.notes,
        start_year_month=plan.start_year_month,
        targets=targets,
        assumptions=assumptions,
        valid=True,
        validation_error=None,
        revision=plan.revision,
        created_at=plan.created_at,
        updated_at=plan.updated_at,
    )


async def _get_plan(
    db: DbSession, farm_id: int, plan_id: int, *, for_update: bool = False
) -> PlannerPlan:
    """Farm-scoped fetch: unknown or foreign ids both 404."""
    if not 1 <= plan_id <= MAX_INT32_ID:
        plan = None
    else:
        stmt = select(PlannerPlan).where(
            PlannerPlan.id == plan_id,
            PlannerPlan.farm_id == farm_id,
        )
        if for_update:
            stmt = stmt.execution_options(populate_existing=True).with_for_update()
        plan = (await db.execute(stmt)).scalar_one_or_none()
    if plan is None or plan.farm_id != farm_id:
        raise HTTPException(status_code=404, detail="Plan not found")
    return plan


async def _check_name_free(db: DbSession, farm_id: int, name: str, exclude_id: int = 0) -> None:
    clash = (
        (
            await db.execute(
                select(PlannerPlan).where(
                    PlannerPlan.farm_id == farm_id,
                    PlannerPlan.name == name,
                    PlannerPlan.id != exclude_id,
                )
            )
        )
        .scalars()
        .first()
    )
    if clash is not None:
        raise HTTPException(status_code=400, detail="A plan with that name already exists.")


def _targets_json(payload_targets: list[PlannerTargetIn]) -> str:
    return json.dumps([target.model_dump() for target in payload_targets])


@router.post("/plan")
async def plan_sales(
    payload: BackwardPlanIn,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    perms: SimView,
) -> BackwardPlanReport:
    """Work backward from calendar-dated sale targets to a dated to-do list:
    feasibility, the stage plan month by month, purchases/breeding/sales
    actions, and each target's requirement chain."""
    farm_id = farm.id
    user_id = user.id
    # Snapshot before the rollback expires the ORM row (see run_adhoc in the
    # simulation router for why the auth transaction must not span CPU work).
    nouns = GOAT_NOUNS
    await db.rollback()

    start = payload.assumptions.meta.start_year_month
    try:
        targets = [
            PlannerTarget(
                year_month=target.year_month,
                animal_class=target.animal_class,
                count=target.count,
            )
            for target in payload.targets
        ]
        # Price against the horizon the engine will actually run: it extends
        # to cover the last target (and rejects anything past the 20-year
        # ceiling), so the budget must too.
        offsets = [month_offset(start, target.year_month) for target in targets]
    except ValueError as exc:
        # Malformed dates, targets at/before the plan start, or a target past
        # the 20-year horizon: all deliberate client-input rejections.
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if max(offsets) > 240:
        # Same rejection the engine would make, but BEFORE the budget charge:
        # a doomed request must not spend admission units.
        raise HTTPException(
            status_code=422,
            detail=(
                f"Target month {targets[offsets.index(max(offsets))].year_month} is beyond "
                "the simulation's 20-year horizon."
            ),
        )
    horizon = min(240, max(payload.assumptions.meta.horizon_months, max(offsets), 12))
    # Priced like the sale planner plus the one extra deterministic pass the
    # stage plan needs: one evaluation, at most nine gap-closing passes, the
    # stage run, then the requested risk replays.
    cost = (2 + 9 + payload.risk_runs) * horizon

    async def run() -> BackwardPlanReport:
        _check_run_budget(farm_id, user_id, cost)
        _charge_run_budget(farm_id, user_id, cost)
        try:
            report = await _offload(
                lambda: build_backward_plan(
                    payload.assumptions,
                    targets,
                    close_gaps_enabled=payload.close_gaps,
                    risk_runs=payload.risk_runs,
                    nouns=nouns,
                )
            )
        except ValidationError as exc:
            # The planner composes new event documents inside the worker; a
            # plan that cannot be represented inside the schema (event-cap or
            # head-count ceilings) is a client-input problem, not a 500.
            raise HTTPException(
                status_code=422,
                detail=(
                    "This plan cannot be represented within the simulation's "
                    f"limits: {exc.errors()[:3]}"
                ),
            ) from exc
        except ValueError as exc:
            # Deliberate rejections: targets at/before the plan start or
            # beyond the 20-year horizon.
            raise HTTPException(status_code=422, detail=str(exc)) from None
        # Same defense as /run: a non-finite figure would crash JSON encoding.
        if not _finite_payload(report.model_dump()):
            raise HTTPException(status_code=422, detail="These inputs produce non-finite results.")
        return report

    return await _with_run_limits(farm_id, user_id, run)


@router.post("/plans", status_code=201)
async def create_plan(
    payload: PlannerPlanCreateIn,
    response: Response,
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    perms: SimManage,
    idempotency_key: IdempotencyKey = None,
) -> PlannerPlanOut:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required.")
    # Serialize the count-and-create decision per tenant with the same
    # advisory-lock reasoning as scenario creation (see the simulation router).
    await db.execute(
        select(func.pg_advisory_xact_lock(literal(PLAN_QUOTA_LOCK_NAMESPACE), literal(farm.id)))
    )

    async def mutate() -> PlannerPlanOut:
        plan_count = (
            await db.execute(
                select(func.count()).select_from(PlannerPlan).where(PlannerPlan.farm_id == farm.id)
            )
        ).scalar_one()
        if plan_count >= get_settings().max_planner_plans_per_farm:
            raise HTTPException(status_code=409, detail=PLAN_CAPACITY_REASON)
        await _check_name_free(db, farm.id, name)
        # The plan's anchor column is the anchor of record: bake it into the
        # stored assumptions so the two can never disagree.
        payload.assumptions.meta.start_year_month = payload.start_year_month
        plan = PlannerPlan(
            farm_id=farm.id,
            name=name,
            notes=payload.notes,
            start_year_month=payload.start_year_month,
            targets=_targets_json(payload.targets),
            assumptions=payload.assumptions.model_dump_json(),
            created_by_id=user.id,
        )
        db.add(plan)
        try:
            await db.flush()
        except IntegrityError:  # concurrent/manual name collision
            raise HTTPException(
                status_code=400, detail="A plan with that name already exists."
            ) from None
        return _plan_out(plan)

    return await execute_idempotent(
        db,
        http_response=response,
        key=idempotency_key,
        farm_id=farm.id,
        actor_id=user.id,
        operation="planner.plans.create",
        payload=payload,
        path_identity={},
        success_status=201,
        response_type=PlannerPlanOut,
        mutate=mutate,
    )


@router.get("/plans")
async def list_plans(
    db: DbSession,
    farm: CurrentFarm,
    perms: SimView,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)] = 0,
) -> PlannerPlanListOut:
    total = (
        await db.execute(
            select(func.count()).select_from(PlannerPlan).where(PlannerPlan.farm_id == farm.id)
        )
    ).scalar_one()
    result = await db.execute(
        select(PlannerPlan)
        .where(PlannerPlan.farm_id == farm.id)
        # Oldest-first with a deterministic page boundary, like scenarios.
        .order_by(PlannerPlan.id)
        .offset(offset)
        .limit(limit)
    )
    out = [_plan_out(plan, allow_invalid=True) for plan in result.scalars()]
    return PlannerPlanListOut(items=out, total=total, limit=limit, offset=offset)


@router.get("/plans/{plan_id}")
async def get_plan(
    db: DbSession, farm: CurrentFarm, perms: SimView, plan_id: int
) -> PlannerPlanOut:
    return _plan_out(await _get_plan(db, farm.id, plan_id))


@router.patch("/plans/{plan_id}")
async def update_plan(
    payload: PlannerPlanUpdateIn,
    db: DbSession,
    farm: CurrentFarm,
    perms: SimManage,
    plan_id: int,
) -> PlannerPlanOut:
    plan = await _get_plan(db, farm.id, plan_id, for_update=True)
    if plan.revision != payload.expected_revision:
        raise HTTPException(
            status_code=409,
            detail="This plan changed since you opened it; refresh before saving.",
        )
    changed = False
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Name is required.")
        await _check_name_free(db, farm.id, name, exclude_id=plan.id)
        plan.name = name
        changed = True
    if payload.notes is not None:
        plan.notes = payload.notes
        changed = True
    if payload.start_year_month is not None:
        plan.start_year_month = payload.start_year_month
        changed = True
    if payload.targets is not None:
        plan.targets = _targets_json(payload.targets)
        changed = True
    if payload.assumptions is not None:
        plan.assumptions = payload.assumptions.model_dump_json()
        changed = True
    if payload.start_year_month is not None:
        # Keep the anchor column authoritative in the stored document (the
        # assumptions may have been edited in the same request or not at all).
        # A stored document that no longer validates must answer 422 like
        # every other read path, never a bare 500 mid-update.
        plan_name = plan.name  # snapshot before the rollback expires the row
        try:
            stored = SimulationAssumptions.model_validate(json.loads(plan.assumptions))
        except (json.JSONDecodeError, ValidationError) as exc:
            await db.rollback()
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Stored assumptions for plan {plan_name!r} no longer validate against "
                    "the current schema; update or delete it."
                ),
            ) from exc
        stored.meta.start_year_month = payload.start_year_month
        plan.assumptions = stored.model_dump_json()
    if changed:
        plan.revision += 1
    try:
        await db.commit()
    except IntegrityError:  # concurrent rename collided with another plan
        await db.rollback()
        raise HTTPException(
            status_code=400, detail="A plan with that name already exists."
        ) from None
    # The write is already committed, so a row whose stored JSON predates a
    # schema tightening must not answer 422 as though nothing happened —
    # report it the way the list endpoint does, with valid=False.
    return _plan_out(plan, allow_invalid=True)


@router.get("/plans/{plan_id}/dpr")
async def plan_dpr(
    db: DbSession,
    user: CurrentUser,
    farm: CurrentFarm,
    perms: SimView,
    plan_id: int,
) -> Response:
    """DPR-style markdown projection summary of the plan's assumptions —
    unit size, capital outlay, subsidy and the 10-year NPV/DSCR figures —
    suitable for a NABARD/NLM loan application."""
    plan = await _get_plan(db, farm.id, plan_id)
    # Targets are validated by the loader but the DPR prices the assumptions,
    # not the sale targets.
    _, assumptions = _load_plan_parts(plan)
    plan_name = plan.name  # snapshot before the auth transaction ends
    farm_id = farm.id
    user_id = user.id
    await db.rollback()

    # One deterministic pass plus the break-even bisection (52 passes),
    # priced like a /run without Monte Carlo.
    cost = 53 * assumptions.meta.horizon_months

    async def run() -> str:
        _check_run_budget(farm_id, user_id, cost)
        _charge_run_budget(farm_id, user_id, cost)

        def build() -> str:
            result = run_simulation(assumptions, nouns=GOAT_NOUNS)
            if not _finite_payload(result.model_dump()):
                raise HTTPException(
                    status_code=422, detail="These inputs produce non-finite results."
                )
            return build_dpr_markdown(assumptions, result, plan_name=plan_name)

        return await _offload(build)

    markdown = await _with_run_limits(farm_id, user_id, run)
    return Response(
        content=markdown,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="dpr-{plan_id}.md"'},
    )


@router.delete("/plans/{plan_id}", status_code=204)
async def delete_plan(db: DbSession, farm: CurrentFarm, perms: SimManage, plan_id: int) -> Response:
    plan = await _get_plan(db, farm.id, plan_id)
    await db.delete(plan)
    await db.commit()
    return Response(status_code=204)
