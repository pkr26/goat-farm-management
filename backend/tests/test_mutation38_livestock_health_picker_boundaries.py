"""Public health selectors preserve literal search and supported int4 ceilings."""

import httpx
from sqlalchemy import select, text

from app.db import get_sessionmaker
from app.models import Animal, MovementRestrictionAction

from .conftest import owner_with_farm
from .test_scoped_picker_lookups import create_animal, create_batch


async def test_health_pickers_accept_omitted_empty_and_whitespace_search(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await create_animal(client, owner, "OPTION-A")
    batch = await create_batch(client, owner, "Option Supplier", count=2)
    for path, field, expected_total in (
        ("animals", "animals", 3),
        ("purchase-batches", "batches", 1),
    ):
        previous_ids: list[int] | None = None
        for query in (None, "", "   "):
            response = await client.get(
                f"/api/health/{path}", headers=owner, params={} if query is None else {"q": query}
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["total"] == expected_total
            ids = [row["id"] for row in body[field]]
            assert len(ids) == expected_total
            if previous_ids is not None:
                assert ids == previous_ids
            previous_ids = ids
        assert previous_ids is not None
        assert (animal["id"] if path == "animals" else batch["id"]) in previous_ids


async def test_health_animal_search_accepts_the_actual_int4_ceiling_identity(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    ordinary = await create_animal(client, owner, "UNRELATED-HEALTH-ANIMAL")
    async with get_sessionmaker()() as db:
        # This is the real serial allocator's last supported positive int4
        # identity. Normal public insertion retains every enforced FK and
        # its actual BucketMove history; no response/schema is fabricated.
        await db.execute(text("SELECT setval('animals_id_seq', 2147483647, false)"))
        await db.commit()
    ceiling = await create_animal(client, owner, "CEILING-HEALTH-ANIMAL")
    assert ceiling["id"] == 2_147_483_647
    for query in ("2147483647", "#2147483647"):
        response = await client.get("/api/health/animals", headers=owner, params={"q": query})
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 1
        assert [row["id"] for row in response.json()["animals"]] == [ceiling["id"]]
        assert ordinary["id"] not in {row["id"] for row in response.json()["animals"]}
    overflow = await client.get("/api/health/animals", headers=owner, params={"q": "#2147483648"})
    assert overflow.status_code == 200, overflow.text
    assert overflow.json()["total"] == 0 and overflow.json()["animals"] == []


async def test_health_batch_search_accepts_the_actual_int4_ceiling_identity(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    ordinary = await create_batch(client, owner, "Unrelated Batch")
    async with get_sessionmaker()() as db:
        await db.execute(text("SELECT setval('purchase_batches_id_seq', 2147483647, false)"))
        await db.commit()
    ceiling = await create_batch(client, owner, "Ceiling Batch", count=2)
    assert ceiling["id"] == 2_147_483_647
    for query in ("2147483647", "#2147483647"):
        response = await client.get(
            "/api/health/purchase-batches", headers=owner, params={"q": query}
        )
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 1
        assert response.json()["batches"] == [
            {"id": ceiling["id"], "active_quarantine_animal_count": 2}
        ]
        assert ordinary["id"] not in {row["id"] for row in response.json()["batches"]}


async def test_health_pickers_have_literal_100_row_admission_limit(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    await create_animal(client, owner, "LOOKUP-LIMIT")
    await create_batch(client, owner, "Lookup Limit")
    for path in ("animals", "purchase-batches"):
        for limit, expected in ((1, 200), (100, 200), (101, 422)):
            response = await client.get(
                f"/api/health/{path}", headers=owner, params={"limit": limit}
            )
            assert response.status_code == expected, response.text
            if expected == 200:
                assert response.json()["limit"] == limit


async def test_health_animal_picker_discloses_a_retained_general_regulatory_hold(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await create_animal(client, owner, "HEALTH-GENERAL-HOLD")
    async with get_sessionmaker()() as db:
        held = (
            await db.execute(select(Animal).where(Animal.id == animal["id"]).with_for_update())
        ).scalar_one()
        # Migration d3f4's explicit general-hold backfill: the restriction
        # reason stands without inventing a suspected disease diagnosis.
        held.movement_restricted = True
        held.restriction_reason = "Regulatory movement hold"
        held.restriction_version = 1
        assert held.suspected_scheduled_disease is False
        db.add(
            MovementRestrictionAction(
                farm_id=held.farm_id,
                animal_id=held.id,
                restriction_version=1,
                action="PLACED",
                acted_at=held.created_at,
                acted_by_id=None,
                action_reference="Legacy restriction placement backfill",
                disease_target="Regulatory movement hold",
                health_event_id=None,
            )
        )
        await db.commit()
    response = await client.get(
        "/api/health/animals", headers=owner, params={"q": f"#{animal['id']}"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1
    selected = response.json()["animals"][0]
    assert selected["id"] == animal["id"]
    assert selected["movement_restricted"] is True and selected["restriction_version"] == 1
