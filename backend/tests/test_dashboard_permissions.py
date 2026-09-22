"""REGRESSION SUITE — dashboard field-level authorization.

GET /api/dashboard requires only dashboard.view, but every section must stay
behind the same permission that guards its data on the dedicated pages — the
module's own contract is that "an aggregate view is not a side door around
field-level authorization". The recent_weights section used to ship
weight_kg/BCS and the animal's identity to callers without animals.view (only
the free-text notes were gated); it is now withheld entirely for them — empty
list, null total — following the cull_candidates withheld-preview convention.
The same convention now covers the herd-summary block (buckets /
total_active / sex_counts) and every withheld section total: null means
"requires permission", 0 always means "genuinely none" (see
test_dashboard_redteam.py for the RT-KL-1/RT-KL-4 pins).
"""

from datetime import timedelta

import httpx

from app.utils import today

from .conftest import owner_with_farm
from .test_finance_extended import (
    change_status,
    custom_role_id,
    get_dashboard,
    get_reports,
    iso,
    make_animal,
    make_breeding,
    make_buck,
    make_doe,
    preset_role_id,
    record_kidding,
    ultrasound,
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
    # Withheld section, not a 403 — the cleaner's own page still renders the
    # sections it is entitled to (its herd counts are now animals.view-gated
    # too; pinned in test_dashboard_redteam.py).
    assert isinstance(cleaner_dash["todays_tasks_total"], int)


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


# FIXED — regression test
# Endpoint: GET /api/dashboard/reports (breeding-performance + mortality
# blocks). reports() gated cull_candidates on breeding.view but left
# conception_rate/first_cycle_rate/kids_per_kidding/twin_rate and the whole
# mortality block (total_deaths/deaths_by_month/stillborn/stillborn_rate)
# ungated — a reports.view-only caller got the herd's breeding and mortality
# aggregates in full. Both blocks now follow the cull_candidates convention:
# withheld fields come back None (list fields empty), never a fabricated
# zero. B4 (2026-09-21 audit) closed the last side doors: the raw counts
# (total_records, kiddings, total_kids_born) are breeding/health-derived
# aggregates like their siblings and are withheld too.
async def test_reports_breeding_and_mortality_aggregates_require_permission(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner, tag="AGG-1")
    buck = await make_buck(client, owner, tag="AGG-1-BUCK")
    br = await make_breeding(client, owner, doe["id"], buck["id"], today() - timedelta(days=160))
    await ultrasound(client, owner, br["id"], pregnant=True, kid_count=2)
    await record_kidding(
        client,
        owner,
        br["id"],
        today(),
        [
            {"tag": "AGG-1-A", "sex": "F", "birth_weight": 2.5, "status": "ALIVE"},
            {"sex": "M", "status": "STILLBORN"},
        ],
    )
    dead = await make_animal(client, owner, tag="AGG-DEAD")
    await change_status(client, owner, dead["id"], "DEAD")

    owner_reports = await get_reports(client, owner)
    breeding = owner_reports["breeding"]
    assert breeding["conception_rate"] == 100.0
    assert breeding["first_cycle_rate"] == 100.0
    assert breeding["kids_per_kidding"] == 1.0
    assert breeding["twin_rate"] == 0.0
    mortality = owner_reports["mortality"]
    assert mortality["total_deaths"] == 1
    assert mortality["deaths_by_month"] != []
    assert mortality["stillborn"] == 1
    assert mortality["stillborn_rate"] == 50.0

    analyst_role = await custom_role_id(
        client, owner, "Reports Only", ["dashboard.view", "reports.view"]
    )
    analyst = await worker_headers(client, owner, analyst_role, "reports-only-agg@farm.in")
    delegated = await get_reports(client, analyst)

    delegated_breeding = delegated["breeding"]
    # null, not the owner's real figures and not a fabricated zero — a
    # withheld section must be distinguishable from genuinely empty history.
    assert delegated_breeding["conception_rate"] is None
    assert delegated_breeding["first_cycle_rate"] is None
    assert delegated_breeding["kids_per_kidding"] is None
    assert delegated_breeding["twin_rate"] is None
    # B4: the raw counts behind the rates are breeding-derived aggregates —
    # they are withheld like the rates, never handed back ungated.
    assert delegated_breeding["total_records"] is None
    assert delegated_breeding["kiddings"] is None

    delegated_mortality = delegated["mortality"]
    assert delegated_mortality["total_deaths"] is None
    assert delegated_mortality["deaths_by_month"] == []
    assert delegated_mortality["stillborn"] is None
    assert delegated_mortality["stillborn_rate"] is None
    # Births are a kidding-outcome aggregate; health.view gates it too (B4).
    assert delegated_mortality["total_kids_born"] is None


async def test_dashboard_withholds_clinical_status_totals_without_health_view(
    client: httpx.AsyncClient,
) -> None:
    """DEAD/CULLED are clinical outcomes, not inventory facts.

    GET /api/dashboard/reports already withholds exactly these counts from a
    caller without health.view; GET /api/dashboard returned the same figures
    raw, so the second endpoint was a side door around the first's gate.
    """
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="STATUS-GATE", weight_kg=20.0)
    await change_status(client, owner, animal["id"], "DEAD", date=iso(today()))
    cleaner_role = await preset_role_id(client, owner, "CLEANER")
    cleaner = await worker_headers(client, owner, cleaner_role, "status-gate@farm.in")

    owner_dash = await get_dashboard(client, owner)
    assert owner_dash["status_totals"].get("DEAD") == 1

    cleaner_dash = await get_dashboard(client, cleaner)
    assert "DEAD" not in cleaner_dash["status_totals"]
    assert "CULLED" not in cleaner_dash["status_totals"]
    # The reports endpoint has always withheld this figure; the dashboard now
    # matches it. (A CLEANER holds no reports.view, so compare on the owner.)
    owner_reports = await get_reports(client, owner)
    assert owner_reports["status_counts"].get("DEAD") == 1


async def test_move_suggestions_withheld_without_animals_view(
    client: httpx.AsyncClient,
) -> None:
    """Each suggestion names the animal and its exact latest weight.

    That is the same identity/weight pair `recent_weights` withholds one block
    below, so the suggestions list must sit behind the same animals.view gate —
    especially since acting on one needs animals.move, which depends on it.
    """
    owner = await owner_with_farm(client)
    # A male kid past the sale-age cutoff and over the 24 kg market weight is
    # what the market rule actually fires on.
    await make_animal(
        client,
        owner,
        tag="MOVE-GATE",
        sex="M",
        bucket="MALE_KIDS",
        date_of_birth=iso(today() - timedelta(days=400)),
        weight_kg=26.0,
    )

    # The owner must genuinely see the suggestion, or this test proves nothing.
    owner_dash = await get_dashboard(client, owner)
    assert owner_dash["suggestions_total"] >= 1
    owner_blob = str(owner_dash["suggestions"])
    assert "MOVE-GATE" in owner_blob
    assert "26.0 kg" in owner_blob

    cleaner_role = await preset_role_id(client, owner, "CLEANER")
    cleaner = await worker_headers(client, owner, cleaner_role, "move-gate@farm.in")
    cleaner_dash = await get_dashboard(client, cleaner)
    assert cleaner_dash["suggestions"] == []
    # null, not 0: the withheld total must not render as "nothing to move".
    assert cleaner_dash["suggestions_total"] is None
    # Neither the tag nor the exact weight leaks through the reason strings.
    assert "MOVE-GATE" not in str(cleaner_dash["suggestions"])

    # A MOVER holds animals.view, so it keeps the section.
    mover_role = await preset_role_id(client, owner, "MOVER")
    mover = await worker_headers(client, owner, mover_role, "mover-gate@farm.in")
    mover_dash = await get_dashboard(client, mover)
    assert mover_dash["suggestions_total"] >= 1
    assert "MOVE-GATE" in str(mover_dash["suggestions"])
