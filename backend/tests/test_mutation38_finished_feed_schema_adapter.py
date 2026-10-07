"""The finished-feed schema adapts persisted recipe/stock query projections."""

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import FeedFinishedStock, FeedRecipe
from app.schemas.feeding import FinishedFeedStockOut

from .conftest import owner_with_farm
from .test_feeding_extended import mix_ready


async def test_finished_feed_projection_matches_the_public_ready_stock(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="finished-schema@farm.in")
    await mix_ready(client, owner, "CREEP", batch_kg=10.0)
    response = await client.get("/api/feeding/finished-stock", headers=owner)
    assert response.status_code == 200, response.text
    assert len(response.json()) == 1
    async with get_sessionmaker()() as db:
        # The actual stock model stores a recipe code; its displayed recipe
        # name comes from the persisted recipe, as in the public stock query.
        # This Row projection supplies every declared output field naturally.
        row = (
            await db.execute(
                select(
                    FeedFinishedStock.recipe_code,
                    FeedRecipe.name.label("recipe_name"),
                    FeedFinishedStock.qty_on_hand,
                )
                .join(FeedRecipe, FeedRecipe.code == FeedFinishedStock.recipe_code)
                .where(FeedFinishedStock.farm_id == int(owner["X-Farm-Id"]))
            )
        ).one()
        assert row.recipe_code == "CREEP" and row.qty_on_hand == 10.0
        assert response.json() == [
            {"recipe_code": "CREEP", "recipe_name": row.recipe_name, "qty_on_hand": 10.0}
        ]
        try:
            adapted = FinishedFeedStockOut.model_validate(row)
        except ValidationError as exc:
            pytest.fail(f"The declared finished-feed Row adapter must preserve mixed stock: {exc}")
        assert adapted.model_dump(mode="json") == response.json()[0]
