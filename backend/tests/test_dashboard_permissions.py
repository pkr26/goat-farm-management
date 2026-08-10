"""REGRESSION SUITE — dashboard field-level authorization.

GET /api/dashboard requires only dashboard.view, but every section must stay
behind the same permission that guards its data on the dedicated pages — the
module's own contract is that "an aggregate view is not a side door around
field-level authorization". The recent_weights section used to ship
weight_kg/BCS and the animal's identity to callers without animals.view (only
the free-text notes were gated); it is now withheld entirely for them — empty
list, null total — following the cull_candidates withheld-preview convention.
"""

import httpx

from app.utils import today

from .conftest import owner_with_farm
from .test_finance_extended import (
    get_dashboard,
    iso,
    make_animal,
    preset_role_id,
    worker_headers,
)


# FIXED — regression test
# Endpoint: GET /api/dashboard (recent_weights section).
# Repro: a CLEANER preset (dashboard.view + tasks.view/complete, no
# animals.view) received per-animal weight/BCS rows plus the animal's
# id/tag/name — data GET /api/animals and the animal profile refuse that
# caller. The section is now gated on animals.view: an empty list and a null
# total (None, not 0, so the permission gate is never rendered as a factual
# "no weights recorded").
async def test_recent_weights_withheld_without_animals_view(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    await make_animal(client, owner, tag="W-GATE", weight_kg=21.5)
    cleaner_role = await preset_role_id(client, owner, "CLEANER")
    cleaner = await worker_headers(client, owner, cleaner_role, "cleaner@farm.in")

    owner_dash = await get_dashboard(client, owner)
    assert owner_dash["recent_weights_total"] == 1
    assert owner_dash["recent_weights"][0]["weight_kg"] == 21.5

    cleaner_dash = await get_dashboard(client, cleaner)
    assert cleaner_dash["recent_weights"] == []
    # null, not 0: a withheld section must be distinguishable from a genuinely
    # empty weight history, or the UI presents the gate as fact.
    assert cleaner_dash["recent_weights_total"] is None
    # Withheld section, not a 403 — the cleaner's own page still works.
    assert cleaner_dash["total_active"] == owner_dash["total_active"]


# Companion pin: the gate is animals.view itself — the permission guarding
# weight history on the animal pages. A MOVER (animals.view, no health.view)
# still sees the rows; only the health-observation notes stay hidden from it.
async def test_recent_weights_visible_with_animals_view_notes_still_gated(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="W-OPEN", weight_kg=19.0)
    recorded = await client.post(
        f"/api/animals/{animal['id']}/weight",
        json={
            "date": iso(today()),
            "weight_kg": 20.5,
            "bcs": 3,
            "notes": "Private veterinary observation",
        },
        headers=owner,
    )
    assert recorded.status_code == 201, recorded.text
    mover_role = await preset_role_id(client, owner, "MOVER")
    mover = await worker_headers(client, owner, mover_role, "mover@farm.in")

    mover_dash = await get_dashboard(client, mover)
    assert mover_dash["recent_weights_total"] == 2
    assert mover_dash["recent_weights"][0]["weight_kg"] == 20.5
    assert mover_dash["recent_weights"][0]["animal"]["tag_number"] == "W-OPEN"
    # notes carry clinical observations: animals.view alone must not leak them.
    assert mover_dash["recent_weights"][0]["notes"] is None
