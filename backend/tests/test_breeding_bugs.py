"""Kidding-date guards — formerly "reviewed suspected bugs", now enforced.

This module used to pin the ACCEPTANCE of kiddings recorded far outside
the gestation window (a 0-day or 100-day "gestation" returned 201),
because the pre-existing suite relied on same-day kiddings and real farm
record-keeping is backdated. That decision was reversed:
`services.record_kidding` now enforces a generous
100–200 day gestation sanity band (150-day gestation, window 145–155) — wide
enough for any plausible backdated entry, tight enough that a "kidding"
recorded 1 day or 3 years post-breeding is rejected as a data-entry error.
The tests that needed same-day kiddings were updated to realistic ~150-day
offsets, and the boundary behavior is pinned in
tests/test_adversarial.py::test_kidding_gestation_window.

SPEC's "Gestation: 150 days (kidding window 145–155)" is ALSO enforced where
it is actionable for planning: expected_kidding_date = breeding_date + 150
(app/models.py), and the kidding list's upcoming/overdue windows.
"""

from datetime import timedelta

import httpx

from app.db import get_sessionmaker
from app.models import BreedingOutcome, BreedingRecord
from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_extended import (
    all_tasks,
    bred_doe,
    confirm,
    get_animal,
    get_breeding,
    iso,
    kid_on_ekd_raw,
    kidding_list,
    pregnant_doe,
    set_status,
    ultrasound,
)


# Boundary acceptance: a kidding at exactly breeding + 100 days (the floor of
# the sanity band, e.g. an early preterm record entered after the fact) is
# still accepted — the band rejects only physiologically absurd dates.
async def test_kidding_at_gestation_floor_accepted(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    # bred 250 days ago, ultrasound-confirmed; kidding at breeding + 100 days
    _doe, _buck, br = await bred_doe(client, headers, breeding_date=today() - timedelta(days=250))
    br = await confirm(client, headers, br["id"])
    floor_date = today() - timedelta(days=150)  # breeding_date + 100
    resp = await kid_on_ekd_raw(client, headers, br, date=iso(floor_date))
    assert resp.status_code == 201
    assert resp.json()["date"] == iso(floor_date)


# A "kidding" on the breeding date itself (0-day gestation) is below the
# 100-day floor and now rejected (409 from the service guard) instead of
# silently corrupting gestation statistics.
async def test_kidding_on_breeding_date_rejected(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    breeding_date = today() - timedelta(days=160)
    _doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    resp = await kid_on_ekd_raw(client, headers, br, date=iso(breeding_date))
    assert resp.status_code == 409
    assert "gestation" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# — selling/culling a confirmed-pregnant doe ends the pregnancy
# ---------------------------------------------------------------------------
# change_status used to skip the doe's pending tasks but leave her
# BreedingRecord CONFIRMED_PREGNANT forever: record_kidding rejects a
# non-ACTIVE doe and nothing prompted mark_aborted, so sold/dead pregnant
# does sat on the kidding due lists indefinitely. The status change now
# auto-resolves the open pregnancy as ABORTED (with a move-history note),
# and both due lists defensively filter to ACTIVE does.
async def test_selling_confirmed_pregnant_doe_auto_aborts(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe, _buck, br = await pregnant_doe(client, headers, gestation_days=140)
    # Sanity: while ACTIVE she is on both due lists (EKD = today + 10).
    lst = await kidding_list(client, headers)
    assert any(r["id"] == br["id"] for r in lst["upcoming"])
    resp = await client.get("/api/dashboard", headers=headers)
    assert any(r["id"] == br["id"] for r in resp.json()["kiddings_due"])

    await set_status(client, headers, doe["id"], "SOLD")

    final = await get_breeding(client, headers, br["id"])
    assert final["outcome"] == "ABORTED"
    assert final["pregnant"] is False
    # The pregnancy follow-up duties were skipped, not left pending.
    related = [t for t in await all_tasks(client, headers) if t["breeding_record_id"] == br["id"]]
    assert related
    assert {t["status"] for t in related} <= {"DONE", "SKIPPED"}
    # The auto-resolution leaves an audit note in the doe's move history.
    resp = await client.get(f"/api/animals/{doe['id']}", headers=headers)
    reasons = [m["reason"] or "" for m in resp.json()["moves"]]
    assert any("auto-aborted" in r for r in reasons), reasons
    # ... and she no longer pollutes either due list.
    lst = await kidding_list(client, headers)
    assert not any(r["id"] == br["id"] for r in lst["upcoming"] + lst["overdue"])
    resp = await client.get("/api/dashboard", headers=headers)
    assert not any(r["id"] == br["id"] for r in resp.json()["kiddings_due"])


async def test_due_lists_exclude_legacy_phantom_pregnancies(client: httpx.AsyncClient) -> None:
    """A CONFIRMED_PREGNANT row on a non-ACTIVE doe (written before the
    auto-resolution existed) must not list on the kidding due lists."""
    headers = await owner_with_farm(client)
    doe, buck, _br = await pregnant_doe(client, headers, gestation_days=140)
    await set_status(client, headers, doe["id"], "SOLD")
    farm_id = int(headers["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        phantom = BreedingRecord(
            farm_id=farm_id,
            doe_id=doe["id"],
            buck_id=buck["id"],
            breeding_date=today() - timedelta(days=140),
            ultrasound_date=today() - timedelta(days=108),
            ultrasound_done=True,
            pregnant=True,
            expected_kidding_date=today() + timedelta(days=10),
            outcome=BreedingOutcome.CONFIRMED_PREGNANT.value,
        )
        db.add(phantom)
        await db.commit()
        phantom_id = phantom.id
    lst = await kidding_list(client, headers)
    assert not any(r["id"] == phantom_id for r in lst["upcoming"] + lst["overdue"])
    resp = await client.get("/api/dashboard", headers=headers)
    assert not any(r["id"] == phantom_id for r in resp.json()["kiddings_due"])


# ---------------------------------------------------------------------------
# — the DELIVERY-move duty also accepts PREGNANCY_EARLY
# ---------------------------------------------------------------------------
# The EARLY→LATE transition is only a dashboard suggestion, so a doe whose
# owner skipped it saw her "Move to DELIVERY" duty go green while she stayed
# in PREGNANCY_EARLY. The duty now moves her from either pregnancy bucket.
async def test_delivery_move_task_moves_doe_from_pregnancy_early(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    doe, _buck, br = await pregnant_doe(client, headers, gestation_days=136)
    # EKD = today + 14 → the move duty (EKD − 15) fell due yesterday.
    assert (await get_animal(client, headers, doe["id"]))["current_bucket"] == "PREGNANCY_EARLY"
    move_task = next(
        t
        for t in await all_tasks(client, headers)
        if t["breeding_record_id"] == br["id"]
        and t["category"] == "BUCKET_MOVE"
        and t["status"] == "PENDING"
    )
    resp = await client.post(f"/api/tasks/{move_task['id']}/complete", headers=headers)
    assert resp.status_code == 200, resp.text
    assert (await get_animal(client, headers, doe["id"]))["current_bucket"] == "DELIVERY"


# ---------------------------------------------------------------------------
# — no ultrasound result for a sold/dead doe's PENDING breeding
# ---------------------------------------------------------------------------
# Selling skips pending tasks but left the BreedingRecord PENDING, and
# submit_ultrasound never checked the doe: recording "pregnant" spawned the
# full pre-kidding task set for a non-existent animal. The submit now
# mirrors record_kidding's ACTIVE guard.
async def test_ultrasound_rejected_after_doe_sold(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe, _buck, br = await bred_doe(client, headers)
    await set_status(client, headers, doe["id"], "SOLD")
    resp = await ultrasound(client, headers, br["id"], pregnant=True, kid_count=2)
    assert resp.status_code == 409, resp.text
    assert "sold" in resp.json()["detail"]
    final = await get_breeding(client, headers, br["id"])
    assert final["outcome"] == "PENDING"  # untouched by the rejected submit
    assert final["ultrasound_done"] is False
    related = [t for t in await all_tasks(client, headers) if t["breeding_record_id"] == br["id"]]
    # Only the ultrasound duty exists (skipped by the sale) — no pre-kidding
    # task set was spawned for a doe that is no longer on the farm.
    assert {t["category"] for t in related} == {"ULTRASOUND"}
    assert {t["status"] for t in related} == {"SKIPPED"}
