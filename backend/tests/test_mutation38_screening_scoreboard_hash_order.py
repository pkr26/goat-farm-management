"""Stable native scoreboard ordering survives genuine PostgreSQL hash aggregation."""

import httpx
from sqlalchemy import select, text

from app.api import screening as screening_api
from app.db import get_sessionmaker
from app.models import Farm, ScreeningImage

from .test_mutation38_screening_scoreboard_sparse_groups import (
    test_real_model_version_history_has_stable_provider_then_model_tie_order as _seed_history,
)


async def test_native_stats_keeps_model_ties_when_postgres_chooses_hash_aggregation(
    client: httpx.AsyncClient,
) -> None:
    # The unchanged companion contract creates nine model deployments and
    # genuinely screens nine distinct camera photos. It asserts the public
    # result before this separate native projected-query contract begins.
    await _seed_history(client)
    async with get_sessionmaker()() as db:
        # Normal supported transaction-local planner settings choose genuine
        # PostgreSQL aggregation. They change no row, query or return value.
        await db.execute(text("SET LOCAL enable_sort = off"))
        await db.execute(text("SET LOCAL enable_hashagg = on"))
        farm_id = (await db.execute(select(ScreeningImage.farm_id).distinct())).scalar_one()
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        result = await screening_api.provider_stats(db=db, farm=farm, _perms=set(), days=30)
        assert [(row.provider, row.model) for row in result.providers] == [
            ("alfa", "zzz-model"),
            *[("shared", f"m{i:02d}") for i in range(7)],
            ("zulu", "aaa-model"),
        ]
        assert all(row.gate_runs == 1 for row in result.providers)
