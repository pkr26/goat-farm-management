"""Buckets board: all 10 buckets with bounded active-animal previews."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, true

from ..deps import CurrentFarm, DbSession, require_perm
from ..models import (
    Animal,
    AnimalStatus,
    BucketDefinition,
    BucketFeedSetting,
    BucketMove,
    WeightRecord,
)
from ..schemas.buckets import BucketBoardRow
from ..schemas.summaries import BucketAnimalOut
from ..utils import business_date, today

router = APIRouter(prefix="/api/buckets", tags=["buckets"])
BUCKET_ANIMAL_PREVIEW_LIMIT = 100


@router.get("")
async def buckets_board(
    db: DbSession,
    farm: CurrentFarm,
    _perms: Annotated[set[str], Depends(require_perm("buckets.view"))],
) -> list[BucketBoardRow]:
    """All buckets with exact occupancy and a tag-ordered 100-animal preview.

    Every caller receives the same purpose-specific animal shape. The full,
    independently-authorized register is the paginated ``/animals`` page in
    ``animals_page_path``.
    """
    defs_result = await db.execute(select(BucketDefinition).order_by(BucketDefinition.sort_order))
    defs = list(defs_result.scalars())

    # Rank/count cheap Animal rows first. Only the selected preview then probes
    # history, so deep histories outside the first 100 tags do no lookup work.
    ranked_animals = (
        select(
            Animal.id.label("animal_id"),
            Animal.tag_number,
            Animal.name,
            Animal.sex,
            Animal.current_bucket,
            Animal.created_at,
            Animal.birth_weight,
            func.count().over(partition_by=Animal.current_bucket).label("animals_total"),
            func.row_number()
            .over(
                partition_by=Animal.current_bucket,
                order_by=(Animal.tag_number, Animal.id),
            )
            .label("preview_rank"),
        )
        .where(Animal.farm_id == farm.id, Animal.status == AnimalStatus.ACTIVE.value)
        .subquery("ranked_bucket_animals")
    )
    preview_animals = (
        select(ranked_animals)
        .where(ranked_animals.c.preview_rank <= BUCKET_ANIMAL_PREVIEW_LIMIT)
        .subquery("bucket_animal_preview")
    )
    latest_weight = (
        select(WeightRecord.weight_kg)
        .where(WeightRecord.animal_id == preview_animals.c.animal_id)
        .order_by(WeightRecord.date.desc(), WeightRecord.id.desc())
        .limit(1)
        .correlate(preview_animals)
        .lateral("bucket_latest_weight")
    )
    latest_move = (
        select(BucketMove.effective_date)
        .where(BucketMove.animal_id == preview_animals.c.animal_id)
        .order_by(BucketMove.moved_at.desc(), BucketMove.id.desc())
        .limit(1)
        .correlate(preview_animals)
        .lateral("bucket_latest_move")
    )
    active_rows = (
        await db.execute(
            select(
                preview_animals,
                func.coalesce(latest_weight.c.weight_kg, preview_animals.c.birth_weight).label(
                    "latest_weight_kg"
                ),
                latest_move.c.effective_date.label("latest_effective_date"),
            )
            .select_from(preview_animals)
            .outerjoin(latest_weight, true())
            .outerjoin(latest_move, true())
            .order_by(
                preview_animals.c.current_bucket,
                preview_animals.c.tag_number,
                preview_animals.c.animal_id,
            )
        )
    ).all()
    reference_date = today(farm.timezone)
    by_bucket: dict[str, list[BucketAnimalOut]] = {d.code: [] for d in defs}
    totals: dict[str, int] = {}
    for row in active_rows:
        bucket_started = row.latest_effective_date or business_date(row.created_at, farm.timezone)
        by_bucket.setdefault(row.current_bucket, []).append(
            BucketAnimalOut(
                id=row.animal_id,
                tag_number=row.tag_number,
                name=row.name,
                sex=row.sex,
                latest_weight_kg=row.latest_weight_kg,
                days_in_current_bucket=max((reference_date - bucket_started).days, 0),
            )
        )
        totals[row.current_bucket] = int(row.animals_total)

    # One bulk query for the farm's feed-setting overrides, then
    # join in Python — no per-bucket await of get_daily_kg_per_head.
    settings_result = await db.execute(
        select(BucketFeedSetting).where(BucketFeedSetting.farm_id == farm.id)
    )
    kg_overrides = {s.bucket: s.daily_kg_per_head for s in settings_result.scalars()}

    rows = []
    for d in defs:
        rows.append(
            BucketBoardRow(
                bucket=d.code,
                name=d.name,
                who=d.who or "",
                exit_rule=d.exit_rule or "",
                # effective per-farm setting (BucketFeedSetting override wins)
                daily_kg_per_head=kg_overrides.get(d.code, d.daily_kg_per_head),
                animals=by_bucket.get(d.code, []),
                animals_total=totals.get(d.code, 0),
                animals_limit=BUCKET_ANIMAL_PREVIEW_LIMIT,
                # Sold/dead/culled animals keep their last bucket forever, so
                # the register must open on the same ACTIVE population this
                # board counted — otherwise the two headcounts disagree.
                animals_page_path=f"/animals?bucket={d.code}&status=ACTIVE",
            )
        )
    return rows
