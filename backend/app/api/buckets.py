"""Buckets board: all 10 buckets with their active-animal lists."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select

from ..deps import CurrentFarm, DbSession, require_perm
from ..models import Animal, AnimalStatus, BucketDefinition, BucketFeedSetting
from ..schemas.animals import BucketBoardRow
from ..services import ANIMAL_OUT_LOADS
from ..utils import today
from ._shared import animal_out

router = APIRouter(prefix="/api/buckets", tags=["buckets"])


@router.get("")
async def buckets_board(
    db: DbSession,
    farm: CurrentFarm,
    _perms: Annotated[set[str], Depends(require_perm("buckets.view"))],
) -> list[BucketBoardRow]:
    defs_result = await db.execute(select(BucketDefinition).order_by(BucketDefinition.sort_order))
    defs = list(defs_result.scalars())
    active_result = await db.execute(
        select(Animal)
        # AnimalOut's computed fields read these collections.
        .options(*ANIMAL_OUT_LOADS)
        .where(Animal.farm_id == farm.id, Animal.status == AnimalStatus.ACTIVE.value)
        .order_by(Animal.tag_number)
    )
    by_bucket: dict[str, list[Animal]] = {d.code: [] for d in defs}
    for animal in active_result.scalars():
        by_bucket.setdefault(animal.current_bucket, []).append(animal)

    # One bulk query for the farm's feed-setting overrides, then
    # join in Python — no per-bucket await of get_daily_kg_per_head.
    settings_result = await db.execute(
        select(BucketFeedSetting).where(BucketFeedSetting.farm_id == farm.id)
    )
    kg_overrides = {s.bucket: s.daily_kg_per_head for s in settings_result.scalars()}

    rows = []
    reference_date = today(farm.timezone)
    for d in defs:
        rows.append(
            BucketBoardRow(
                bucket=d.code,
                name=d.name,
                who=d.who or "",
                exit_rule=d.exit_rule or "",
                # effective per-farm setting (BucketFeedSetting override wins)
                daily_kg_per_head=kg_overrides.get(d.code, d.daily_kg_per_head),
                animals=[
                    animal_out(a, reference_date, farm.timezone) for a in by_bucket.get(d.code, [])
                ],
            )
        )
    return rows
