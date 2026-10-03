"""Declared herd cohorts and per-animal, per-component treatment evidence.

The caller locks the task before changing a round. Existing completed tasks
are never backfilled: their historical scope cannot be reconstructed honestly.
"""

from sqlalchemy import exists, func, insert, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    Animal,
    AnimalStatus,
    HealthEvent,
    HealthRound,
    HealthRoundCoverage,
    HealthRoundExclusion,
    HealthRoundTarget,
    Task,
)
from ..utils import utcnow
from .health import target_matches_template, template_names_for_task


def is_herd_round(task: Task) -> bool:
    return (
        task.category in ("VACCINE", "DEWORMING")
        and task.animal_id is None
        and task.purchase_batch_id is None
    )


async def ensure_round_snapshot(db: AsyncSession, task: Task) -> HealthRound:
    """Snapshot active members once, under the caller's task/creation lock."""
    current = await db.get(HealthRound, task.id)
    if current is not None:
        return current
    if not is_herd_round(task) or task.status != "PENDING":
        raise ValueError("Only a pending herd health duty can start a round")
    components = template_names_for_task(task.title, task.category)
    if not components:
        raise ValueError("Declare a recognized vaccine/deworming programme before starting a round")
    snapshot_at = utcnow()
    current = HealthRound(
        farm_id=task.farm_id,
        task_id=task.id,
        snapshot_at=snapshot_at,
        required_components=list(components),
    )
    db.add(current)
    await db.flush()
    # Set based: a large herd does not hydrate its entire clinical history.
    await db.execute(
        insert(HealthRoundTarget).from_select(
            ["task_id", "animal_id", "farm_id", "added_at"],
            select(literal(task.id), Animal.id, Animal.farm_id, literal(snapshot_at)).where(
                Animal.farm_id == task.farm_id, Animal.status == AnimalStatus.ACTIVE.value
            ),
            include_defaults=False,
        )
    )
    return current


def recorded_components(round_: HealthRound, target: str, template_name: str | None) -> list[str]:
    """An explicit combined target covers both; a blank covers one chosen item."""
    if target.strip():
        components = [
            name for name in round_.required_components if target_matches_template(target, name)
        ]
    else:
        components = [template_name] if template_name in round_.required_components else []
    if not components:
        raise ValueError("Health evidence must name a required round component")
    return components


async def require_round_targets(
    db: AsyncSession, round_: HealthRound, animal_ids: list[int], components: list[str]
) -> None:
    """Reject unsnapshotted/excluded members and already recorded components."""
    targets = set(
        (
            await db.execute(
                select(HealthRoundTarget.animal_id).where(
                    HealthRoundTarget.task_id == round_.task_id,
                    HealthRoundTarget.animal_id.in_(animal_ids),
                    ~exists().where(
                        HealthRoundExclusion.task_id == HealthRoundTarget.task_id,
                        HealthRoundExclusion.animal_id == HealthRoundTarget.animal_id,
                    ),
                )
            )
        ).scalars()
    )
    if targets != set(animal_ids):
        raise ValueError(
            "Round targets changed: add new arrivals explicitly and omit excluded members"
        )
    if (
        await db.execute(
            select(HealthRoundCoverage.animal_id)
            .where(
                HealthRoundCoverage.task_id == round_.task_id,
                HealthRoundCoverage.animal_id.in_(animal_ids),
                HealthRoundCoverage.component.in_(components),
            )
            .limit(1)
        )
    ).first() is not None:
        raise ValueError(
            "A selected round component is already recorded; review the remaining targets"
        )


async def add_round_coverage(
    db: AsyncSession, round_: HealthRound, events: list[HealthEvent], components: list[str]
) -> None:
    for event in events:
        if event.animal_id is None:
            raise ValueError("Round coverage requires individual-animal evidence")
        for component in components:
            db.add(
                HealthRoundCoverage(
                    farm_id=round_.farm_id,
                    task_id=round_.task_id,
                    animal_id=event.animal_id,
                    component=component,
                    health_event_id=event.id,
                )
            )
    await db.flush()


async def round_counts(db: AsyncSession, round_: HealthRound) -> tuple[int, int, int, int]:
    """Return total members, exclusions, covered members and remaining units."""
    total = (
        await db.execute(
            select(func.count())
            .select_from(HealthRoundTarget)
            .where(HealthRoundTarget.task_id == round_.task_id)
        )
    ).scalar_one()
    excluded = (
        await db.execute(
            select(func.count())
            .select_from(HealthRoundExclusion)
            .where(HealthRoundExclusion.task_id == round_.task_id)
        )
    ).scalar_one()
    eligible_coverage = (
        select(HealthRoundCoverage.animal_id, func.count().label("units"))
        .where(
            HealthRoundCoverage.task_id == round_.task_id,
            HealthRoundCoverage.component.in_(round_.required_components),
            ~exists().where(
                HealthRoundExclusion.task_id == HealthRoundCoverage.task_id,
                HealthRoundExclusion.animal_id == HealthRoundCoverage.animal_id,
            ),
        )
        .group_by(HealthRoundCoverage.animal_id)
        .subquery()
    )
    covered, units = (
        await db.execute(
            select(
                func.count().filter(eligible_coverage.c.units == len(round_.required_components)),
                func.coalesce(func.sum(eligible_coverage.c.units), 0),
            )
        )
    ).one()
    remaining = (total - excluded) * len(round_.required_components) - int(units)
    return total, excluded, covered, remaining


async def round_is_complete(db: AsyncSession, round_: HealthRound) -> bool:
    total, _excluded, _covered, remaining = await round_counts(db, round_)
    # An empty farm is no evidence of a completed treatment round.
    return total > 0 and remaining == 0
