"""Feeding gap tests (2026-09-23 verification plan, category 6).

Recipes are seeded reference data — there is no recipe-write API, so the
"totals must be exactly 100 kg per 100 kg" invariant is enforced at MIX
time against whatever the DB holds. That guard has never been proven to
fire; the first test corrupts a recipe through direct DB writes (the only
way it can drift) and pins the mix rejection for 99.99, 100.01, and a
negative line.

The second test is the rounding-attack A/B the SQL-level loop can't prove:
1000 small mixes versus one big mix of the same total must leave the
inventory EXACTLY equal (gram-identical), i.e. no cumulative rounding
drift on the request path.
"""

from decimal import Decimal

import httpx
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import FeedInventory, FeedRecipe, FeedRecipeLine
from app.utils import today

from .conftest import owner_with_farm


async def _recipe_codes(client: httpx.AsyncClient, owner: dict) -> list[str]:
    resp = await client.get("/api/feeding/recipes", headers=owner)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    recipes = body["recipes"] if isinstance(body, dict) else body
    return [r["code"] for r in recipes]


async def _bump_first_line(code: str, delta: Decimal) -> None:
    """Nudge one existing line's quantity by `delta` kg — the realistic way a
    recipe drifts off exactly-100 (keep names/categories/stock wiring)."""
    async with get_sessionmaker()() as db:
        recipe = (await db.execute(select(FeedRecipe).where(FeedRecipe.code == code))).scalar_one()
        line = (
            await db.execute(
                select(FeedRecipeLine).where(FeedRecipeLine.recipe_id == recipe.id).order_by(FeedRecipeLine.id)
            )
        ).scalars().first()
        assert line is not None
        line.kg_per_100kg = float(Decimal(str(line.kg_per_100kg)) + delta)
        await db.commit()


async def _negate_first_line(code: str) -> None:
    async with get_sessionmaker()() as db:
        recipe = (await db.execute(select(FeedRecipe).where(FeedRecipe.code == code))).scalar_one()
        line = (
            await db.execute(
                select(FeedRecipeLine).where(FeedRecipeLine.recipe_id == recipe.id).order_by(FeedRecipeLine.id)
            )
        ).scalars().first()
        assert line is not None
        line.kg_per_100kg = -abs(float(line.kg_per_100kg))
        await db.commit()


async def _stock_up(client: httpx.AsyncClient, owner: dict, ingredient: str, kg: float) -> None:
    listing = await client.get("/api/feeding/inventory", headers=owner)
    items = listing.json()["items"] if isinstance(listing.json(), dict) else listing.json()
    match = next((i for i in items if i["ingredient"] == ingredient), None)
    path = f"/api/feeding/inventory/{match['id']}/add" if match else None
    if path is None:  # ingredient not stocked for this farm yet — seed via a mix later
        raise AssertionError(f"ingredient {ingredient} not in inventory")
    resp = await client.post(
        path, json={"qty_kg": kg, "price_per_kg": 10.0}, headers=owner
    )
    assert resp.status_code in (200, 201), resp.text


async def test_mix_rejects_drifted_recipe_totals(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client, email="recipe@farm.in")
    code = (await _recipe_codes(client, owner))[0]

    for label, corrupt, restore in (
        ("under", Decimal("-0.01"), Decimal("0.01")),
        ("over", Decimal("0.01"), Decimal("-0.01")),
    ):
        await _bump_first_line(code, corrupt)
        resp = await client.post(
            "/api/feeding/mix",
            json={"recipe_code": code, "batch_kg": 10.0},
            headers=owner | {"Idempotency-Key": f"recipe-{label}"},
        )
        assert resp.status_code in (400, 422), (
            f"{label}-total recipe mixed anyway: {resp.status_code} {resp.text[:150]}"
        )
        assert "100" in resp.json().get("detail", ""), (
            f"{label}: unexpected denial {resp.text[:150]}"
        )
        await _bump_first_line(code, restore)

    # A negative (or zero) line cannot even be persisted: the DB's own
    # ck_feed_recipe_lines_kg CHECK fires before any service sees it — the
    # mix-time guard is the second line of defense, not the first.
    import pytest
    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError, match="ck_feed_recipe_lines_kg"):
        await _negate_first_line(code)

    # Exactly-100 mixes cleanly once the ingredients are stocked (the probes
    # above never reached the stock check — the total guard fires first).
    listing = await client.get("/api/feeding/inventory", headers=owner)
    for item in listing.json():
        stock_resp = await client.post(
            f"/api/feeding/inventory/{item['id']}/add",
            json={"qty_kg": 100.0, "price_per_kg": 10.0},
            headers=owner | {"Idempotency-Key": f"stock-{item['id']}"},
        )
        assert stock_resp.status_code in (200, 201), (
            f"stock add for {item['ingredient']}: {stock_resp.status_code} {stock_resp.text[:150]}"
        )
    resp = await client.post(
        "/api/feeding/mix",
        json={"recipe_code": code, "batch_kg": 10.0},
        headers=owner | {"Idempotency-Key": "recipe-exact"},
    )
    assert resp.status_code in (200, 201), resp.text


async def test_thousand_tiny_mixes_equal_one_big_mix_exactly(client: httpx.AsyncClient) -> None:
    """500 × 1 kg mixes versus 1 × 500 kg mix of the same recipe (1000 would exceed the
    account idempotency-record retention cap): final ingredient inventories
    must be gram-identical (no cumulative rounding drift through the API)."""
    totals: dict[str, Decimal] = {}

    for label, batches, size in (("tiny", 500, 1.0), ("big", 1, 500.0)):
        owner = await owner_with_farm(client, email=f"drift-{label}@farm.in")
        code = (await _recipe_codes(client, owner))[0]

        # Stock every ingredient of the recipe far above the mix total.
        listing = await client.get("/api/feeding/inventory", headers=owner)
        items = listing.json()["items"] if isinstance(listing.json(), dict) else listing.json()
        stocked = {i["ingredient"] for i in items}
        async with get_sessionmaker()() as db:
            recipe = (
                await db.execute(select(FeedRecipe).where(FeedRecipe.code == code))
            ).scalar_one()
            lines = (
                await db.execute(
                    select(FeedRecipeLine).where(FeedRecipeLine.recipe_id == recipe.id)
                )
            ).scalars().all()
        for line in lines:
            if line.ingredient not in stocked:
                raise AssertionError(f"seeded recipe ingredient {line.ingredient} not stocked")
        for item in items:
            if item["ingredient"] in {line.ingredient for line in lines}:
                await _stock_up(client, owner, item["ingredient"], 2000.0)

        for i in range(batches):
            resp = await client.post(
                "/api/feeding/mix",
                json={"recipe_code": code, "batch_kg": size},
                headers=owner | {"Idempotency-Key": f"{label}-{i}"},
            )
            assert resp.status_code in (200, 201), f"mix {i}: {resp.text[:150]}"

        async with get_sessionmaker()() as db:
            rows = (
                await db.execute(select(FeedInventory).where(FeedInventory.farm_id == int(owner["X-Farm-Id"])))
            ).scalars().all()
        totals[label] = {
            r.ingredient: r.qty_on_hand for r in rows if r.ingredient in {l.ingredient for l in lines}
        }
        _ = today()

    # Both farms mixed exactly 500 kg of the same recipe: per-ingredient
    # consumption must agree to the gram.
    for ingredient in totals["tiny"]:
        tiny_left = totals["tiny"][ingredient]
        big_left = totals["big"][ingredient]
        assert tiny_left == big_left, (
            f"{ingredient}: 500 tiny mixes left {tiny_left} kg vs one big mix "
            f"{big_left} kg — rounding drift of "
            f"{abs(Decimal(str(tiny_left)) - Decimal(str(big_left)))} kg"
        )
