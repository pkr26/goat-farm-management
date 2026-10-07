"""The native mix boundary rejects an unsaved invalid reference definition before effects."""

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db import get_sessionmaker
from app.models import Farm, FeedInventory, FeedRecipe, FeedRecipeLine, Transaction
from app.services.feeding import mix_feed_batch

from .conftest import owner_with_farm


async def test_native_mix_reports_an_invalid_pending_ingredient_before_stock_or_ledger_effects(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        assert db.autoflush is False
        farm = await db.get(Farm, farm_id)
        recipe = (
            await db.execute(
                select(FeedRecipe)
                .where(FeedRecipe.code == "CREEP")
                .options(selectinload(FeedRecipe.lines))
            )
        ).scalar_one()
        assert farm is not None and len(recipe.lines) >= 2
        original_lines = {line.id: line.kg_per_100kg for line in recipe.lines}
        inventory_before = {
            item.id: item.qty_on_hand
            for item in (await db.execute(select(FeedInventory))).scalars()
        }
        assert not list((await db.execute(select(Transaction))).scalars())
        # Native reference edits remain pending under the application's real
        # autoflush=False session. They must be validated by the mix helper;
        # no invalid reference line is committed or DB constraint disabled.
        first, second = recipe.lines[:2]
        second.kg_per_100kg += first.kg_per_100kg
        first.kg_per_100kg = 0
        assert sum(line.kg_per_100kg for line in recipe.lines) == 100
        try:
            try:
                await mix_feed_batch(db, farm, recipe.code, 1)
            except ValueError as exc:
                assert str(exc) == "Recipe CREEP has an invalid ingredient quantity"
            except Exception as exc:
                pytest.fail(
                    f"Invalid reference quantities require their native ValueError: {exc!r}"
                )
            else:
                pytest.fail("The pending zero-quantity recipe definition was accepted")
            assert {
                item.id: item.qty_on_hand
                for item in (await db.execute(select(FeedInventory))).scalars()
            } == inventory_before
            assert not list((await db.execute(select(Transaction))).scalars())
        finally:
            await db.rollback()
    async with get_sessionmaker()() as db:
        retained = {
            line.id: line.kg_per_100kg
            for line in (
                await db.execute(
                    select(FeedRecipeLine).where(FeedRecipeLine.id.in_(original_lines))
                )
            ).scalars()
        }
        assert retained == original_lines
        assert {
            item.id: item.qty_on_hand
            for item in (await db.execute(select(FeedInventory))).scalars()
        } == inventory_before
        assert not list((await db.execute(select(Transaction))).scalars())
