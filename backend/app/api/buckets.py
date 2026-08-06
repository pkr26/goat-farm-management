"""Buckets board: all 10 buckets with their active-animal lists."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select

from ..deps import CurrentFarm, DbSession, require_perm
from ..models import Animal, AnimalStatus, BucketDefinition
from ..schemas.animals import AnimalOut, BucketBoardRow
from ..services import get_daily_kg_per_head

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
        .where(Animal.farm_id == farm.id, Animal.status == AnimalStatus.ACTIVE.value)
        .order_by(Animal.tag_number)
    )
    by_bucket: dict[str, list[Animal]] = {d.code: [] for d in defs}
    for animal in active_result.scalars():
        by_bucket.setdefault(animal.current_bucket, []).append(animal)

    rows = []
    for d in defs:
        rows.append(
            BucketBoardRow(
                bucket=d.code,
                name=d.name,
                who=d.who or "",
                exit_rule=d.exit_rule or "",
                # effective per-farm setting (BucketFeedSetting override wins)
                daily_kg_per_head=await get_daily_kg_per_head(db, farm.id, d.code),
                animals=[AnimalOut.model_validate(a) for a in by_bucket.get(d.code, [])],
            )
        )
    return rows
