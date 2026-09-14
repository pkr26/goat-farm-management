"""RED TEAM REMEDIATION REGRESSION — 2026-09-13 audit 08 (RT-KL-1/3/4).

RT-KL-1 (Medium): the two aggregate endpoints returned bucket occupancy (the
pregnancy buckets among them), the active/sex herd counts and per-bucket
average weights gated only by dashboard.view / reports.view — a cleaner or a
reports-only custom role read breeding-programme occupancy and weight stats
that /api/buckets and /api/animals both refuse them. The herd-summary blocks
are now behind animals.view (the register permission that governs the same
figures on their dedicated pages), withheld as null following the
cull_candidates convention.

RT-KL-4 (Low): withheld task/kidding/suggestion totals rendered as factual 0;
they now use the null withheld sentinel like every sibling section, so a gate
is never indistinguishable from "genuinely none".

RT-KL-3 (Low): ready_to_move_suggestions computed its exact total with a
count().over() window aggregate — the exact anti-pattern
dashboard._exact_total was written to eliminate (it drains the correlated
per-animal context for every ACTIVE animal before the LIMIT can emit). The
total now comes from the same uncorrelated scalar subquery, which must keep
it exact over the filtered base while the preview stays bounded.
"""

from datetime import timedelta

import httpx

from app.db import get_sessionmaker
from app.models import Animal, Bucket
from app.utils import today

from .conftest import owner_with_farm
from .test_finance_extended import (
    create_duty,
    custom_role_id,
    get_dashboard,
    get_reports,
    make_animal,
    make_breeding,
    make_buck,
    make_doe,
    preset_role_id,
    ultrasound,
    worker_headers,
)


# FIXED — regression test (RT-KL-1, dashboard side)
# Endpoint: GET /api/dashboard (buckets / total_active / sex_counts).
# Repro: a CLEANER preset (dashboard.view, no animals.view) received
# per-bucket occupancy including PREGNANCY_EARLY/PREGNANCY_LATE/DELIVERY —
# the scale and phase of the breeding programme that /api/buckets (403) and
# /api/animals (403) refuse it, and which kiddings_due withholds behind
# breeding.view. The block is now gated on animals.view and withheld as null.
async def test_dashboard_herd_summary_withheld_without_animals_view(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner, tag="KL1-DOE")
    buck = await make_buck(client, owner, tag="KL1-BUCK")
    br = await make_breeding(client, owner, doe["id"], buck["id"], today() - timedelta(days=140))
    await ultrasound(client, owner, br["id"], pregnant=True)  # doe → PREGNANCY_EARLY

    owner_dash = await get_dashboard(client, owner)
    occupancy = {row["code"]: row["count"] for row in owner_dash["buckets"]}
    assert occupancy["PREGNANCY_EARLY"] == 1
    assert occupancy["BREEDING"] == 1
    assert owner_dash["total_active"] == 2
    assert owner_dash["sex_counts"] == {"M": 1, "F": 1}

    cleaner_role = await preset_role_id(client, owner, "CLEANER")
    cleaner = await worker_headers(client, owner, cleaner_role, "kl1-cleaner@farm.in")
    cleaner_dash = await get_dashboard(client, cleaner)
    # null for the whole block, not zeroed-out counts — a withheld herd
    # summary must be distinguishable from an empty farm.
    assert cleaner_dash["buckets"] is None
    assert cleaner_dash["total_active"] is None
    assert cleaner_dash["sex_counts"] is None
    # Status totals are outside the withheld block (their own health.view
    # DEAD/CULLED strip is pinned in test_dashboard_permissions.py), so the
    # page still renders rather than 403ing.
    assert cleaner_dash["status_totals"].get("ACTIVE") == 2

    # The gate is animals.view specifically — a MOVER holds it and keeps the
    # herd summary in full.
    mover_role = await preset_role_id(client, owner, "MOVER")
    mover = await worker_headers(client, owner, mover_role, "kl1-mover@farm.in")
    mover_dash = await get_dashboard(client, mover)
    assert mover_dash["buckets"] == owner_dash["buckets"]
    assert mover_dash["total_active"] == owner_dash["total_active"]
    assert mover_dash["sex_counts"] == owner_dash["sex_counts"]


# FIXED — regression test (RT-KL-1, reports side)
# Endpoint: GET /api/dashboard/reports (bucket_rows incl. avg_weight).
# Repro: a custom role with only dashboard.view + reports.view received
# per-bucket counts and per-bucket mean live weights — an animals.view-derived
# aggregate plus the bucket-board occupancy. Both are now behind animals.view.
async def test_reports_herd_summary_withheld_without_animals_view(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    await make_animal(client, owner, tag="KL1-R1", bucket="FOUNDATION", weight_kg=20.0)
    await make_animal(client, owner, tag="KL1-R2", bucket="FOUNDATION", weight_kg=30.0)

    owner_rep = await get_reports(client, owner)
    rows = {row["code"]: row for row in owner_rep["bucket_rows"]}
    assert rows["FOUNDATION"]["count"] == 2
    assert rows["FOUNDATION"]["avg_weight"] == 25.0
    assert owner_rep["total_active"] == 2
    assert owner_rep["sex_counts"] == {"M": 0, "F": 2}

    analyst_role = await custom_role_id(
        client, owner, "Reports Only KL1", ["dashboard.view", "reports.view"]
    )
    analyst = await worker_headers(client, owner, analyst_role, "kl1-reports@farm.in")
    analyst_rep = await get_reports(client, analyst)
    # null for the whole herd-summary block — neither the occupancy nor the
    # weight aggregate may leak to a reports-only caller.
    assert analyst_rep["bucket_rows"] is None
    assert analyst_rep["total_active"] is None
    assert analyst_rep["sex_counts"] is None
    # The reports page still renders: status counts (own health.view strip)
    # and the deliberately ungated raw breeding counts stay visible.
    assert analyst_rep["status_counts"] == owner_rep["status_counts"]
    assert analyst_rep["breeding"]["total_records"] == owner_rep["breeding"]["total_records"]

    # ACCOUNTANT holds animals.view alongside reports.view — nothing changes
    # for any preset that already had the register permission.
    accountant_role = await preset_role_id(client, owner, "ACCOUNTANT")
    accountant = await worker_headers(client, owner, accountant_role, "kl1-accountant@farm.in")
    accountant_rep = await get_reports(client, accountant)
    assert accountant_rep["bucket_rows"] == owner_rep["bucket_rows"]
    assert accountant_rep["total_active"] == owner_rep["total_active"]
    assert accountant_rep["sex_counts"] == owner_rep["sex_counts"]


# FIXED — regression test (RT-KL-4)
# Endpoint: GET /api/dashboard (withheld section totals). The task, kidding
# and suggestion totals defaulted to a factual 0 when their gating permission
# was missing — an ACCOUNTANT with no tasks.view received
# todays_tasks_total: 0, indistinguishable on the wire from "no duties
# today". They now use the None withheld sentinel the sibling sections
# (cull_candidates_total, recent_weights_total) already use.
async def test_withheld_section_totals_are_null_not_zero(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    await create_duty(client, owner, "KL4 duty", today())
    doe = await make_doe(client, owner, tag="KL4-DOE")
    buck = await make_buck(client, owner, tag="KL4-BUCK")
    br = await make_breeding(client, owner, doe["id"], buck["id"], today() - timedelta(days=140))
    await ultrasound(client, owner, br["id"], pregnant=True)

    owner_dash = await get_dashboard(client, owner)
    assert owner_dash["todays_tasks_total"] == 1
    assert owner_dash["kiddings_due_total"] == 1

    # dashboard.view + reports.view only: every operational section is
    # withheld, and each withheld total must be null.
    analyst_role = await custom_role_id(
        client, owner, "Reports Only KL4", ["dashboard.view", "reports.view"]
    )
    analyst = await worker_headers(client, owner, analyst_role, "kl4-analyst@farm.in")
    analyst_dash = await get_dashboard(client, analyst)
    for key in (
        "todays_tasks_total",
        "overdue_tasks_total",
        "ultrasounds_due_total",
        "kiddings_due_total",
        "suggestions_total",
    ):
        # null, not 0: the gate must never render as "genuinely none".
        assert analyst_dash[key] is None
    for key in ("todays_tasks", "overdue_tasks", "ultrasounds_due", "kiddings_due", "suggestions"):
        assert analyst_dash[key] == []

    # The gates are the section permissions themselves. The cleaner preset
    # holds tasks.view, so its task totals stay factual ints (the duty above
    # is owner-visible only), while the sections it lacks the gate for stay
    # null.
    cleaner_role = await preset_role_id(client, owner, "CLEANER")
    cleaner = await worker_headers(client, owner, cleaner_role, "kl4-cleaner@farm.in")
    cleaner_dash = await get_dashboard(client, cleaner)
    assert cleaner_dash["todays_tasks_total"] == 0
    assert cleaner_dash["overdue_tasks_total"] == 0
    assert cleaner_dash["ultrasounds_due_total"] == 0
    assert cleaner_dash["kiddings_due_total"] is None
    assert cleaner_dash["suggestions_total"] is None


# FIXED — regression test (RT-KL-3)
# Endpoint: GET /api/dashboard (suggestions section). The exact total was
# computed with count().over() — an unpartitioned window aggregate that must
# drain the five-correlated-subquery-per-row animal context for every ACTIVE
# animal before the preview LIMIT can emit. It now uses the _exact_total
# uncorrelated scalar subquery; this pins the property both shapes must
# agree on — the total is exact over the filtered base while the preview
# stays bounded by its own LIMIT.
async def test_suggestions_total_exact_beyond_preview_limit(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    # 120 market-ready male kids — more than the 100-row preview cap. Seeded
    # straight through the ORM: every other test here exercises the animal
    # API; only the count needs the volume.
    async with get_sessionmaker()() as db:
        db.add_all(
            [
                Animal(
                    farm_id=farm_id,
                    tag_number=f"KL3-MARKET-{i:03d}",
                    sex="M",
                    date_of_birth=today() - timedelta(days=300),
                    source="BORN",
                    birth_type="SINGLE",
                    birth_weight=26.0,
                    current_bucket=Bucket.MALE_KIDS.value,
                )
                for i in range(120)
            ]
        )
        await db.commit()

    dash = await get_dashboard(client, owner)
    assert dash["preview_limit"] == 100
    assert len(dash["suggestions"]) == 100
    # The total counts the whole filtered base, not the limited preview.
    assert dash["suggestions_total"] == 120
