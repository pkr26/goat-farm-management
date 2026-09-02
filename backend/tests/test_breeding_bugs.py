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
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import Animal, BreedingOutcome, BreedingRecord, BucketMove, KidEntry
from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_extended import (
    POSTPARTUM_RECOVERY_DAYS,
    all_tasks,
    backdate_latest_bucket_move,
    bred_doe,
    confirm,
    get_animal,
    get_breeding,
    iso,
    kid_on_ekd,
    kid_on_ekd_raw,
    kidding_list,
    list_animals,
    make_breeding,
    make_buck,
    make_doe,
    move_to,
    pregnant_doe,
    set_status,
    tasks_by_category,
    ultrasound,
)


async def test_positive_ultrasound_after_maximum_gestation_is_rejected(
    client: httpx.AsyncClient,
) -> None:
    """Finding #1: never create a pregnancy with no legal kidding date."""
    headers = await owner_with_farm(client)
    breeding_date = today() - timedelta(days=201)
    _doe, _buck, breeding = await bred_doe(client, headers, breeding_date=breeding_date)

    late = await client.post(
        f"/api/breeding/{breeding['id']}/ultrasound",
        json={"pregnant": True, "kid_count": 2, "date": iso(today())},
        headers=headers,
    )
    assert late.status_code == 409, late.text
    assert "200-day gestation window" in late.json()["detail"]
    unchanged = await get_breeding(client, headers, breeding["id"])
    assert unchanged["outcome"] == "PENDING"
    assert unchanged["ultrasound_done"] is False

    # The inclusive boundary still leaves one valid kidding date: day 200.
    boundary = await client.post(
        f"/api/breeding/{breeding['id']}/ultrasound",
        json={
            "pregnant": True,
            "kid_count": 2,
            "date": iso(breeding_date + timedelta(days=200)),
        },
        headers=headers,
    )
    assert boundary.status_code == 200, boundary.text
    assert boundary.json()["outcome"] == "CONFIRMED_PREGNANT"


async def test_negative_ultrasound_after_maximum_gestation_is_rejected(
    client: httpx.AsyncClient,
) -> None:
    """A late negative is no more factual than a late positive scan."""
    headers = await owner_with_farm(client)
    breeding_date = today() - timedelta(days=201)
    _doe, _buck, breeding = await bred_doe(client, headers, breeding_date=breeding_date)

    late = await client.post(
        f"/api/breeding/{breeding['id']}/ultrasound",
        json={"pregnant": False, "date": iso(today())},
        headers=headers,
    )
    assert late.status_code == 409, late.text
    assert "200-day gestation window" in late.json()["detail"]
    unchanged = await get_breeding(client, headers, breeding["id"])
    assert unchanged["outcome"] == "PENDING"
    assert unchanged["ultrasound_done"] is False


async def test_backdated_breeding_recordable_after_a_same_day_bucket_move(
    client: httpx.AsyncClient,
) -> None:
    """Moves are stamped with their *recording* day (MoveIn has no date), so a
    breeding that physically happened before the move must stay recordable —
    entering facts late is a supported daily workflow, and recording the
    breeding must never append a backdated service move."""
    headers = await owner_with_farm(client)
    doe = await make_doe(client, headers, tag="MOVE-ORDER-DOE")
    buck = await make_buck(client, headers, tag="MOVE-ORDER-BUCK")
    moved = await client.post(
        f"/api/animals/{doe['id']}/move",
        json={"to_bucket": "BREEDING", "reason": "Moved after inspection"},
        headers=headers,
    )
    assert moved.status_code == 200, moved.text

    recorded = await client.post(
        "/api/breeding",
        json={
            "doe_id": doe["id"],
            "buck_id": buck["id"],
            "breeding_date": iso(today() - timedelta(days=1)),
        },
        headers=headers,
    )
    assert recorded.status_code == 201, recorded.text
    assert recorded.json()["breeding_date"] == iso(today() - timedelta(days=1))

    async with get_sessionmaker()() as db:
        breedings = list(
            (
                await db.execute(select(BreedingRecord).where(BreedingRecord.doe_id == doe["id"]))
            ).scalars()
        )
        moves = list(
            (
                await db.execute(select(BucketMove).where(BucketMove.animal_id == doe["id"]))
            ).scalars()
        )
    assert len(breedings) == 1
    assert len(moves) == 2  # initial placement plus the explicit move; no backdated service move


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
async def test_negative_ultrasound_in_impossible_early_window_rejected(
    client: httpx.AsyncClient,
) -> None:
    """A NOT-pregnant result used to have no lower sanity bound at all: only
    `result_date < breeding_date` was rejected, so a "seen back in heat"
    observation dated 2–17 days after service — before any next heat can
    physically exist — was accepted, fabricated a FAILED cycle, moved the
    doe's reproductive boundary (permitting an immediate re-service), and two
    such entries could flag a healthy doe as a cull candidate. The negative
    path now enforces the oestrous window: same-heat closes (day 0/1) and
    return-to-heat observations (day >= 18) stay accepted."""
    headers = await owner_with_farm(client)
    breeding_date = today() - timedelta(days=32)
    _doe, _buck, br = await bred_doe(client, headers, breeding_date=breeding_date)

    for impossible_day in (2, 7, 17):
        resp = await ultrasound(
            client,
            headers,
            br["id"],
            pregnant=False,
            kid_count=None,
            date=iso(breeding_date + timedelta(days=impossible_day)),
        )
        assert resp.status_code == 409, resp.text
        assert "heat" in resp.json()["detail"].lower()
    unchanged = await get_breeding(client, headers, br["id"])
    assert unchanged["outcome"] == "PENDING"
    assert unchanged["ultrasound_done"] is False
    assert unchanged["ultrasound_date"] == iso(breeding_date + timedelta(days=32))

    # The earliest physically possible return to heat still closes the cycle
    # and brings the check date forward with it (existing behavior).
    accepted = await ultrasound(
        client,
        headers,
        br["id"],
        pregnant=False,
        kid_count=None,
        date=iso(breeding_date + timedelta(days=18)),
    )
    assert accepted.status_code == 200, accepted.text
    body = accepted.json()
    assert body["outcome"] == "FAILED"
    assert body["ultrasound_result_date"] == iso(breeding_date + timedelta(days=18))
    assert body["ultrasound_date"] == iso(breeding_date + timedelta(days=18))


async def test_same_heat_negative_still_closes_the_cycle(client: httpx.AsyncClient) -> None:
    """The lower edge of the window: a service observed to fail during its own
    standing heat (the day after breeding) remains recordable — the floor only
    rejects the physically impossible in-between days."""
    headers = await owner_with_farm(client)
    breeding_date = today() - timedelta(days=1)
    _doe, _buck, br = await bred_doe(client, headers, breeding_date=breeding_date)
    resp = await ultrasound(
        client, headers, br["id"], pregnant=False, kid_count=None, date=iso(today())
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["outcome"] == "FAILED"


async def test_ultrasound_rejected_after_doe_sold(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe, _buck, br = await bred_doe(client, headers)
    await set_status(client, headers, doe["id"], "SOLD")
    resp = await ultrasound(client, headers, br["id"], pregnant=True, kid_count=2)
    assert resp.status_code == 409, resp.text
    assert "sold" in resp.json()["detail"]
    final = await get_breeding(client, headers, br["id"])
    # The sale closes a never-scanned service as UNASSESSED so it stops sitting
    # PENDING forever holding the doe's open-pregnancy slot. The rejected
    # submit itself recorded nothing: no scan happened.
    assert final["outcome"] == "UNASSESSED"
    assert final["ultrasound_done"] is False
    related = [t for t in await all_tasks(client, headers) if t["breeding_record_id"] == br["id"]]
    # Only the ultrasound duty exists (skipped by the sale) — no pre-kidding
    # task set was spawned for a doe that is no longer on the farm.
    assert {t["category"] for t in related} == {"ULTRASOUND"}
    assert {t["status"] for t in related} == {"SKIPPED"}


# ---------------------------------------------------------------------------
# — a history_override round-trip through RECOVERY must not fake "weaned"
# ---------------------------------------------------------------------------
# replan_dam_after_last_kid_death's "already weaned" guard used to treat ANY
# BucketMove row with from_bucket=RECOVERY as proof the kid had weaned.
# history_override bypasses LEGAL_BUCKET_TRANSITIONS entirely (it is meant
# for correcting historical data-entry mistakes), so an owner fixing a
# mistake (RECOVERY -> FOUNDATION -> RECOVERY) left behind exactly that row
# shape without the kid ever actually weaning. When the kid later died as the
# last survivor, the guard wrongly reported "already weaned": the birth
# KidEntry stayed ALIVE forever and the dam was left on her stale WEANING
# task instead of the 14-day no-survivor recovery path.
async def test_history_override_round_trip_does_not_fake_weaning(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    doe, _buck, br = await pregnant_doe(client, headers, gestation_days=160)
    record = await kid_on_ekd(client, headers, br, kids=[{"sex": "M"}, {"sex": "F"}])
    kid_a_id = record["kids"][0]["animal_id"]
    kid_b_id = record["kids"][1]["animal_id"]

    # A data-entry correction, not a weaning: kid A never actually leaves its
    # birth RECOVERY cohort.
    await move_to(client, headers, kid_a_id, "FOUNDATION", history_override=True)
    await move_to(client, headers, kid_a_id, "RECOVERY", history_override=True)

    await set_status(client, headers, kid_b_id, "DEAD")
    await set_status(client, headers, kid_a_id, "DEAD")

    async with get_sessionmaker()() as db:
        entry = (
            await db.execute(
                select(KidEntry.status, KidEntry.mortality_reported_at).where(
                    KidEntry.animal_id == kid_a_id
                )
            )
        ).one()
    assert tuple(entry) == ("DIED", today())

    tasks = await all_tasks(client, headers)
    assert not any(
        t["animal_id"] == doe["id"] and t["category"] == "WEANING" and t["status"] == "PENDING"
        for t in tasks
    )
    postpartum = [
        t
        for t in tasks_by_category(tasks, "BUCKET_MOVE")
        if t["status"] == "PENDING" and t["animal_id"] == doe["id"]
    ]
    assert len(postpartum) == 1
    assert postpartum[0]["due_date"] == iso(today() + timedelta(days=POSTPARTUM_RECOVERY_DAYS))


async def _doe_with_two_retained_recovery_litters(
    client: httpx.AsyncClient, headers: dict
) -> tuple[dict, dict, dict, dict, dict]:
    """Create the supported historical-correction shape that exposed litter bleed.

    The first weaning duty is retained and overdue; its kid remains in RECOVERY.
    A history correction returns only the doe to RESTING so a second, current
    kidding can be recorded with its own dependent kid and future weaning duty.
    """
    doe, buck, first = await bred_doe(
        client,
        headers,
        tag="TWO-LITTER-DOE",
        breeding_date=today() - timedelta(days=340),
    )
    first = await confirm(client, headers, first["id"], kid_count=1)
    await kid_on_ekd(
        client,
        headers,
        first,
        kids=[{"tag": "OLDER-LITTER-KID", "sex": "M"}],
    )

    await move_to(client, headers, doe["id"], "RESTING", history_override=True)
    await backdate_latest_bucket_move(doe["id"], today() - timedelta(days=185))
    second = await make_breeding(
        client,
        headers,
        doe["id"],
        buck["id"],
        breeding_date=iso(today() - timedelta(days=176)),
    )
    second = await confirm(client, headers, second["id"], kid_count=1)
    await kid_on_ekd(
        client,
        headers,
        second,
        kids=[{"tag": "NEWER-LITTER-KID", "sex": "F"}],
    )
    animals = {animal["tag_number"]: animal for animal in await list_animals(client, headers)}
    return (
        doe,
        first,
        second,
        animals["OLDER-LITTER-KID"],
        animals["NEWER-LITTER-KID"],
    )


async def test_old_weaning_duty_does_not_wean_a_newer_litter(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    doe, first, second, older_kid, newer_kid = await _doe_with_two_retained_recovery_litters(
        client, headers
    )
    pending_weaning = sorted(
        (
            task
            for task in await all_tasks(client, headers)
            if task["category"] == "WEANING" and task["status"] == "PENDING"
        ),
        key=lambda task: task["due_date"],
    )
    assert [task["breeding_record_id"] for task in pending_weaning] == [
        first["id"],
        second["id"],
    ]

    # A history correction does not prove that the newer kid was weaned. The
    # old duty must still recognize it as dependent after this round trip.
    await move_to(client, headers, newer_kid["id"], "FOUNDATION", history_override=True)
    await move_to(client, headers, newer_kid["id"], "RECOVERY", history_override=True)

    completed = await client.post(
        f"/api/tasks/{pending_weaning[0]['id']}/complete",
        headers=headers,
    )
    assert completed.status_code == 200, completed.text

    assert (await get_animal(client, headers, older_kid["id"]))["current_bucket"] == "MALE_KIDS"
    assert (await get_animal(client, headers, newer_kid["id"]))["current_bucket"] == "RECOVERY"
    assert (await get_animal(client, headers, doe["id"]))["current_bucket"] == "RECOVERY"
    newer_duty = next(
        task for task in await all_tasks(client, headers) if task["id"] == pending_weaning[1]["id"]
    )
    assert newer_duty["status"] == "PENDING"


async def test_weaning_ignores_an_adult_daughter_in_her_own_recovery(
    client: httpx.AsyncClient,
) -> None:
    """A retained dam_id does not make an already-weaned adult dependent."""
    headers = await owner_with_farm(client)
    doe, _buck, breeding = await pregnant_doe(
        client,
        headers,
        tag="ADULT-DAUGHTER-DAM",
        gestation_days=220,
    )
    record = await kid_on_ekd(
        client,
        headers,
        breeding,
        kids=[{"tag": "CURRENT-DEPENDENT-KID", "sex": "M"}],
    )
    weaning = next(
        task
        for task in await all_tasks(client, headers)
        if task["category"] == "WEANING" and task["breeding_record_id"] == breeding["id"]
    )

    farm_id = int(headers["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        adult_daughter = Animal(
            farm_id=farm_id,
            tag_number="ADULT-DAUGHTER",
            sex="F",
            date_of_birth=today() - timedelta(days=400),
            birth_type="SINGLE",
            source="BORN",
            dam_id=doe["id"],
            current_bucket="RECOVERY",
        )
        db.add(adult_daughter)
        await db.flush()
        db.add_all(
            [
                BucketMove(
                    animal_id=adult_daughter.id,
                    from_bucket="RECOVERY",
                    to_bucket="FEMALE_KIDS",
                    reason="Weaned (day 60)",
                ),
                BucketMove(
                    animal_id=adult_daughter.id,
                    from_bucket="DELIVERY",
                    to_bucket="RECOVERY",
                    reason="Recorded her own kidding",
                ),
            ]
        )
        await db.commit()
        adult_daughter_id = adult_daughter.id

    completed = await client.post(f"/api/tasks/{weaning['id']}/complete", headers=headers)
    assert completed.status_code == 200, completed.text
    current_kid_id = record["kids"][0]["animal_id"]
    assert (await get_animal(client, headers, current_kid_id))["current_bucket"] == "MALE_KIDS"
    assert (await get_animal(client, headers, adult_daughter_id))["current_bucket"] == "RECOVERY"
    assert (await get_animal(client, headers, doe["id"]))["current_bucket"] == "RESTING"


async def test_old_litters_final_death_keeps_newer_litters_weaning_plan(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    doe, first, second, older_kid, newer_kid = await _doe_with_two_retained_recovery_litters(
        client, headers
    )

    await set_status(client, headers, older_kid["id"], "DEAD")

    tasks = await all_tasks(client, headers)
    old_duty = next(
        task
        for task in tasks
        if task["category"] == "WEANING" and task["breeding_record_id"] == first["id"]
    )
    new_duty = next(
        task
        for task in tasks
        if task["category"] == "WEANING" and task["breeding_record_id"] == second["id"]
    )
    assert old_duty["status"] == "SKIPPED"
    assert new_duty["status"] == "PENDING"
    assert not any(
        task["category"] == "BUCKET_MOVE"
        and task["breeding_record_id"] == first["id"]
        and task["status"] == "PENDING"
        for task in tasks
    )
    assert (await get_animal(client, headers, newer_kid["id"]))["current_bucket"] == "RECOVERY"
    assert (await get_animal(client, headers, doe["id"]))["current_bucket"] == "RECOVERY"
