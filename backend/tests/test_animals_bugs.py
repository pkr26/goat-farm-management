"""REGRESSION SUITE — the app bugs documented here are FIXED.

Each test asserts the SPEC/documented behavior and FAILED against the old
app (int32-overflow ids crashed with an asyncpg DataError → 500; over-long
notes/reasons crashed the INSERT → 500). Path/body ids above the int4 PK
ceiling now resolve to the documented 404, and schemas are capped at the
column widths → 422. Do not weaken these assertions.
"""

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api._shared import unique_constraint_name as _unique_constraint_name
from app.db import get_sessionmaker
from app.models import Animal, KidEntry
from app.utils import today

from .conftest import owner_with_farm, provisioned_worker_login
from .test_breeding_extended import (
    WORKER_PW,
    all_tasks,
    confirm,
    get_animal,
    iso,
    kid_on_ekd,
    make_breeding,
    make_buck,
    make_doe,
    place_health_hold,
)


async def _animals_view_only_viewer(
    client: httpx.AsyncClient, owner: dict, email: str, name: str
) -> dict:
    """Headers for a worker on a role granting only `animals.view`."""
    role = await client.post(
        "/api/team/roles",
        json={"name": name, "permissions": ["animals.view"]},
        headers=owner,
    )
    assert role.status_code == 201, role.text
    worker = await client.post(
        "/api/team/workers",
        json={
            "name": name,
            "email": email,
            "password": WORKER_PW,
            "role_id": role.json()["id"],
        },
        headers=owner,
    )
    assert worker.status_code == 201, worker.text
    headers, _ = await provisioned_worker_login(client, email, WORKER_PW)
    return headers | {"X-Farm-Id": owner["X-Farm-Id"]}


async def _make_animal(client: httpx.AsyncClient, headers: dict, tag: str = "A-001") -> dict:
    resp = await client.post(
        "/api/animals",
        json={
            "tag_number": tag,
            "sex": "F",
            "source": "PURCHASED",
            "current_bucket": "FOUNDATION",
            "historical_import_reason": "Existing-herd test fixture",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# FIXED — regression test
# GET /api/animals/{animal_id} with an id >= 2**31 crashes with an unhandled
# asyncpg DataError ("value out of int32 range") instead of a documented 404.
# Cause: Animal.id is a PG INTEGER (int32) but the path param is a plain
# unbounded `int` (schemas/common.py defines BoundedId with le=2**62 for
# exactly this, but the animals routes don't use it) and _get_animal passes the
# raw value to db.get(). Any out-of-int32 id 500s the request (via httpx it
# propagates as an exception). Expected: 404 "Animal not found" (or 422).
async def test_profile_huge_id_returns_404_not_500(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await client.get(f"/api/animals/{2**31}", headers=owner)
    assert resp.status_code == 404


# FIXED — regression test
# Same root cause as above on the mutating routes: POST
# /api/animals/{id}/move (and /weight, /status) call _get_animal with the
# unbounded path int, so an id >= 2**31 raises an unhandled asyncpg DataError
# (500) instead of 404.
async def test_move_huge_id_returns_404_not_500(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    resp = await client.post(
        f"/api/animals/{2**31}/move", json={"to_bucket": "BREEDING"}, headers=owner
    )
    assert resp.status_code == 404


# FIXED — regression test
# POST /api/animals/{id}/weight with notes longer than 255 chars crashes with
# an unhandled asyncpg DataError: WeightIn.notes has no max_length while the
# WeightRecord.notes column is String(255). Expected: 422 (schema bound) or the
# note stored (Text column) — never a 500.
async def test_weight_notes_over_255_chars_not_500(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await _make_animal(client, owner)
    resp = await client.post(
        f"/api/animals/{animal['id']}/weight",
        json={"weight_kg": 20, "notes": "n" * 300},
        headers=owner,
    )
    assert resp.status_code in (201, 422)


# FIXED — regression test
# POST /api/animals/{id}/move with a reason longer than 255 chars crashes with
# an unhandled asyncpg DataError: MoveIn.reason has no max_length while the
# BucketMove.reason column is String(255). Expected: 200/422, never a 500.
async def test_move_reason_over_255_chars_not_500(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await _make_animal(client, owner)
    resp = await client.post(
        f"/api/animals/{animal['id']}/move",
        json={"to_bucket": "BREEDING", "reason": "r" * 300},
        headers=owner,
    )
    assert resp.status_code in (200, 422)


# FIXED — regression test
# POST /api/animals/{id}/status with notes longer than 255 chars crashes with
# an unhandled asyncpg DataError: StatusChangeIn.notes has no max_length while
# Animal.status_notes is String(255). Expected: 200/422, never a 500.
async def test_status_notes_over_255_chars_not_500(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await _make_animal(client, owner)
    resp = await client.post(
        f"/api/animals/{animal['id']}/status",
        json={"new_status": "DEAD", "notes": "n" * 300},
        headers=owner,
    )
    assert resp.status_code in (200, 422)


# FIXED — regression test
# create_animal's auto-tag retry branch was dead code: after the collision
# rollback every ORM object is expired, so the retry's `farm.id` access was a
# forbidden sync refresh on the AsyncSession (MissingGreenlet → 500) instead
# of the intended retry with a fresh generated tag. farm.id is now captured
# before the loop.
async def test_auto_tag_retry_after_collision_succeeds(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client)
    await _make_animal(client, owner, tag="G-DUP01")
    tags = iter(["G-DUP01", "G-NEW01"])  # first generated tag collides, retry wins

    async def fake_generate_unique_tag(db: object, farm_id: int, attempts: int = 10) -> str:
        return next(tags)

    monkeypatch.setattr("app.api.animals.generate_unique_tag", fake_generate_unique_tag)
    resp = await client.post(
        "/api/animals",
        json={"sex": "F", "source": "PURCHASED", "current_bucket": "FOUNDATION"},
        headers=owner,
    )
    assert resp.status_code == 201, resp.text  # never the MissingGreenlet 500
    assert resp.json()["tag_number"] == "G-NEW01"


async def test_auto_tag_retry_after_stillborn_namespace_collision_succeeds(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The trigger-backed cross-table tag constraint uses the normal retry path."""
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner, tag="TAG-NS-DAM")
    buck = await make_buck(client, owner, tag="TAG-NS-SIRE")
    breeding = await make_breeding(
        client,
        owner,
        doe["id"],
        buck["id"],
        breeding_date=iso(today() - timedelta(days=150)),
    )
    breeding = await confirm(client, owner, breeding["id"], kid_count=1)
    kidding = await client.post(
        "/api/kidding",
        json={
            "breeding_record_id": breeding["id"],
            "date": iso(today()),
            "ease": "NORMAL",
            "kids": [{"tag": "G-STILL", "sex": "F", "status": "STILLBORN"}],
        },
        headers=owner,
    )
    assert kidding.status_code == 201, kidding.text

    tags = iter(["G-STILL", "G-LIVE1"])

    async def fake_generate_unique_tag(db: object, farm_id: int, attempts: int = 10) -> str:
        return next(tags)

    monkeypatch.setattr("app.api.animals.generate_unique_tag", fake_generate_unique_tag)
    created = await client.post(
        "/api/animals",
        json={"sex": "F", "source": "PURCHASED", "current_bucket": "FOUNDATION"},
        headers=owner,
    )
    assert created.status_code == 201, created.text
    assert created.json()["tag_number"] == "G-LIVE1"


async def test_explicit_animal_tag_conflicting_with_stillborn_is_a_400(
    client: httpx.AsyncClient,
) -> None:
    """A cross-table tag conflict is a client conflict, never an internal error."""
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner, tag="TAG-EXPLICIT-DAM")
    buck = await make_buck(client, owner, tag="TAG-EXPLICIT-SIRE")
    breeding = await make_breeding(
        client,
        owner,
        doe["id"],
        buck["id"],
        breeding_date=iso(today() - timedelta(days=150)),
    )
    breeding = await confirm(client, owner, breeding["id"], kid_count=1)
    kidding = await client.post(
        "/api/kidding",
        json={
            "breeding_record_id": breeding["id"],
            "date": iso(today()),
            "ease": "NORMAL",
            "kids": [{"tag": "STILL-TAKEN", "sex": "M", "status": "STILLBORN"}],
        },
        headers=owner,
    )
    assert kidding.status_code == 201, kidding.text

    conflict = await client.post(
        "/api/animals",
        json={
            "tag_number": "STILL-TAKEN",
            "sex": "M",
            "source": "PURCHASED",
            "current_bucket": "FOUNDATION",
        },
        headers=owner,
    )
    assert conflict.status_code == 400, conflict.text
    assert conflict.json()["detail"] == "Tag 'STILL-TAKEN' already exists on this farm."


def _asyncpg_shaped(message: str, constraint: str | None) -> IntegrityError:
    """The exact chain SQLAlchemy's asyncpg adapter produces.

    level 0 sqlalchemy.exc.IntegrityError (.orig == __cause__ == __context__)
    -> level 1 adapter DBAPI error (__cause__ == __context__)
    -> level 2 native asyncpg error, which alone carries ``constraint_name``.
    """
    native = Exception(message)
    native.constraint_name = constraint  # type: ignore[attr-defined]
    dbapi = Exception(f"<class 'asyncpg.exceptions.NotNullViolationError'>: {message}")
    dbapi.__cause__ = native
    dbapi.__context__ = native
    exc = IntegrityError("INSERT INTO animals ...", {}, dbapi)
    exc.__cause__ = dbapi
    exc.__context__ = dbapi
    return exc


def test_unique_constraint_name_returns_none_for_unnamed_violation() -> None:
    """A 23502 NOT NULL violation names no constraint: return None, never raise.

    create_animal re-raises such an IntegrityError unchanged, so the bounded
    cause walk must stop at the end of the chain instead of dereferencing None
    and demoting the DB error to an AttributeError inside the except block.
    """
    exc = _asyncpg_shaped(
        'null value in column "sex" of relation "animals" violates not-null constraint',
        None,
    )
    assert _unique_constraint_name(exc) is None


def test_unique_constraint_name_reads_the_native_cause() -> None:
    """The trigger-raised 23505 carries its name only on the native cause."""
    exc = _asyncpg_shaped(
        "animal tag conflicts with a stillborn tag in this farm",
        "uq_stillborn_tag_farm_namespace",
    )
    assert _unique_constraint_name(exc) == "uq_stillborn_tag_farm_namespace"


async def _complete_weaning_task(client: httpx.AsyncClient, headers: dict, dam_id: int) -> None:
    weaning = next(
        t
        for t in await all_tasks(client, headers)
        if t["category"] == "WEANING" and t["animal_id"] == dam_id and t["status"] == "PENDING"
    )
    resp = await client.post(f"/api/tasks/{weaning['id']}/complete", headers=headers)
    assert resp.status_code == 200, resp.text


# FIXED — regression test
# replan_dam_after_last_kid_death() flipped the animal's birth KidEntry to
# DIED for EVERY death routed through POST /{id}/status — including a weaned
# juvenile or adult born on the farm months earlier. KidStatus.DIED documents
# a *neonatal* mortality, so the flip falsified the immutable birth outcome
# and retroactively corrupted twin-rate / kids-per-kidding statistics (the
# dirty entry was committed even on the function's early `return False`
# paths). The realignment is now gated on the child still being a dependent
# kid living in its birth RECOVERY cohort.
async def test_weaned_kid_death_does_not_rewrite_birth_kid_entry(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner, tag="BIRTHFACT-DAM")
    buck = await make_buck(client, owner, tag="BIRTHFACT-SIRE")
    br = await make_breeding(
        client, owner, doe["id"], buck["id"], breeding_date=iso(today() - timedelta(days=260))
    )
    br = await confirm(client, owner, br["id"], kid_count=2)
    record = await kid_on_ekd(
        client,
        owner,
        br,
        kids=[{"sex": "M", "status": "ALIVE"}, {"sex": "F", "status": "ALIVE"}],
    )
    await _complete_weaning_task(client, owner, doe["id"])
    male_kid_id = next(k["animal_id"] for k in record["kids"] if k["sex"] == "M")
    assert (await get_animal(client, owner, male_kid_id))["current_bucket"] == "MALE_KIDS"

    dead = await client.post(
        f"/api/animals/{male_kid_id}/status", json={"new_status": "DEAD"}, headers=owner
    )
    assert dead.status_code == 200, dead.text

    # Both birth entries keep their live-twin delivery outcome: the weaned
    # kid's later death is an Animal-status fact, not a neonatal mortality.
    async with get_sessionmaker()() as db:
        rows = (
            await db.execute(
                select(KidEntry.status, KidEntry.mortality_reported_at).where(
                    KidEntry.kidding_record_id == record["id"]
                )
            )
        ).all()
    assert [tuple(row) for row in rows] == [("ALIVE", None), ("ALIVE", None)]


# FIXED — regression test
# The dependent-kid gate cannot rely on the RECOVERY bucket alone: a
# farm-born doe who later kids returns to RECOVERY as a *dam* while her own
# birth KidEntry still exists, so her death there used to rewrite her own
# birth outcome to a "neonatal" mortality dated years after her delivery.
async def test_farm_born_dam_dying_in_recovery_keeps_her_own_birth_entry(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    granddam = await make_doe(client, owner, tag="GRANDDAM", age_days=1500)
    buck = await make_buck(
        client,
        owner,
        tag="GRANDSIRE",
        date_of_birth=iso(today() - timedelta(days=1500)),
        weight_date=iso(today() - timedelta(days=1500)),
    )
    br1 = await make_breeding(
        client, owner, granddam["id"], buck["id"], breeding_date=iso(today() - timedelta(days=700))
    )
    br1 = await confirm(client, owner, br1["id"], kid_count=1)
    record1 = await kid_on_ekd(client, owner, br1, kids=[{"sex": "F", "status": "ALIVE"}])
    farm_born_doe_id = record1["kids"][0]["animal_id"]
    await _complete_weaning_task(client, owner, granddam["id"])
    assert (await get_animal(client, owner, farm_born_doe_id))["current_bucket"] == "FEMALE_KIDS"

    # Grow her into a breeding doe, confirm a pregnancy, and deliver: she is
    # now a dam standing in RECOVERY, exactly like a dependent kid would be.
    weighed = await client.post(
        f"/api/animals/{farm_born_doe_id}/weight",
        json={"weight_kg": 26.0, "date": iso(today() - timedelta(days=170))},
        headers=owner,
    )
    assert weighed.status_code == 201, weighed.text
    # An UNRELATED sire: breeding her back to her own father (GRANDSIRE) is
    # rejected by the inbreeding fence, and this test's subject is her birth
    # entry, not the mating policy.
    unrelated_sire = await make_buck(
        client,
        owner,
        tag="OUTSIDER-SIRE",
        date_of_birth=iso(today() - timedelta(days=1500)),
        weight_date=iso(today() - timedelta(days=1500)),
    )
    br2 = await make_breeding(
        client,
        owner,
        farm_born_doe_id,
        unrelated_sire["id"],
        breeding_date=iso(today() - timedelta(days=160)),
    )
    br2 = await confirm(client, owner, br2["id"], kid_count=1)
    await kid_on_ekd(client, owner, br2, kids=[{"sex": "M", "status": "ALIVE"}])
    assert (await get_animal(client, owner, farm_born_doe_id))["current_bucket"] == "RECOVERY"

    dead = await client.post(
        f"/api/animals/{farm_born_doe_id}/status", json={"new_status": "DEAD"}, headers=owner
    )
    assert dead.status_code == 200, dead.text

    async with get_sessionmaker()() as db:
        birth_entry = (
            await db.execute(
                select(KidEntry.status, KidEntry.mortality_reported_at).where(
                    KidEntry.animal_id == farm_born_doe_id
                )
            )
        ).one()
    assert tuple(birth_entry) == ("ALIVE", None)


# FIXED — regression test
# animal_profile's kids query filtered dam_id only, so a buck's profile always
# reported kids_total=0 and kids=[] even though every kid row stores him on
# sire_id — inconsistent with breedings_where, which already matches either
# parent so he sees the very services that produced those kids.
async def test_buck_profile_lists_sired_offspring(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner, tag="SIRED-DAM")
    buck = await make_buck(client, owner, tag="SIRED-BUCK")
    br = await make_breeding(
        client, owner, doe["id"], buck["id"], breeding_date=iso(today() - timedelta(days=260))
    )
    br = await confirm(client, owner, br["id"], kid_count=2)
    record = await kid_on_ekd(
        client,
        owner,
        br,
        kids=[{"sex": "M", "status": "ALIVE"}, {"sex": "F", "status": "ALIVE"}],
    )
    kid_ids = {k["animal_id"] for k in record["kids"]}

    buck_profile = await client.get(f"/api/animals/{buck['id']}", headers=owner)
    assert buck_profile.status_code == 200, buck_profile.text
    body = buck_profile.json()
    assert body["kids_total"] == 2
    assert {k["id"] for k in body["kids"]} == kid_ids
    assert br["id"] in body["breedings"]

    # The dam's maternal view is unchanged.
    doe_profile = (await client.get(f"/api/animals/{doe['id']}", headers=owner)).json()
    assert doe_profile["kids_total"] == 2
    assert {k["id"] for k in doe_profile["kids"]} == kid_ids


# FIXED — regression test
# animal_out's fail-closed health shield nulled every disease-hold field for
# callers without health.view but left restriction_version at its raw value —
# a monotonic per-episode counter, so any non-zero value disclosed that (and
# how many times) the animal carried scheduled-disease holds. It is only ever
# consumed as the clear-restriction concurrency token, a health-permission
# flow, so it now reads 0 alongside the other redactions.
async def test_restriction_version_redacted_without_health_view(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await _make_animal(client, owner, tag="HOLD-VERSION")
    await place_health_hold(client, owner, animal["id"])
    owner_view = await get_animal(client, owner, animal["id"])
    assert owner_view["restriction_version"] == 1  # health.view sees the real counter

    role = await client.post(
        "/api/team/roles",
        json={"name": "Animal Reader", "permissions": ["animals.view"]},
        headers=owner,
    )
    assert role.status_code == 201, role.text
    worker = await client.post(
        "/api/team/workers",
        json={
            "name": "Animal Reader",
            "email": "animal-reader@farm.in",
            "password": WORKER_PW,
            "role_id": role.json()["id"],
        },
        headers=owner,
    )
    assert worker.status_code == 201, worker.text
    viewer, _ = await provisioned_worker_login(client, "animal-reader@farm.in", WORKER_PW)
    viewer = viewer | {"X-Farm-Id": owner["X-Farm-Id"]}

    redacted = await get_animal(client, viewer, animal["id"])
    # The effective operational hold stays visible so the mover can act
    # safely, but the episode counter no longer leaks hold history.
    assert redacted["movement_restricted"] is True
    assert redacted["restriction_version"] == 0
    assert redacted["suspected_scheduled_disease"] is False
    assert redacted["restriction_reason"] is None


# FIXED — regression test
# animal_out's fail-closed breeding shield blanks cull_candidate for callers
# without breeding.view: the flag is a breeding-programme verdict (services
# raise it after two consecutive FAILED cycles), the same secret the cull
# dashboard withholds from restricted roles. It must read exactly False —
# AnimalOut types cull_candidate as a required bool, so neither the raw True
# nor a null is an acceptable redaction for the generated client.
async def test_cull_candidate_redacted_without_breeding_view(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await _make_animal(client, owner, tag="CULL-REDACT")
    async with get_sessionmaker()() as db:
        row = await db.get(Animal, animal["id"])
        assert row is not None
        row.cull_candidate = True
        await db.commit()
    assert (await get_animal(client, owner, animal["id"]))["cull_candidate"] is True

    viewer = await _animals_view_only_viewer(
        client, owner, "cull-reader@farm.in", "Cull Candidate Reader"
    )

    redacted = await get_animal(client, viewer, animal["id"])
    assert redacted["cull_candidate"] is False
    listing = await client.get("/api/animals", headers=viewer)
    assert listing.status_code == 200, listing.text
    listed = listing.json()["animals"]
    assert [entry["tag_number"] for entry in listed] == ["CULL-REDACT"]
    assert [entry["cull_candidate"] for entry in listed] == [False]


# FIXED — regression test
# The health shield reports the EFFECTIVE operational hold to callers without
# health.view: movement_restricted OR suspected_scheduled_disease, because the
# move guards refuse on either. A plain regulatory hold (movement_restricted
# with no disease suspicion) must therefore still read True — reporting False
# would tell the mover the animal is free while POST /api/animals/{id}/move
# keeps refusing it. Only the *reason* is withheld.
async def test_general_movement_hold_stays_visible_without_health_view(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await _make_animal(client, owner, tag="GENERAL-HOLD")
    async with get_sessionmaker()() as db:
        row = await db.get(Animal, animal["id"])
        assert row is not None
        # A general hold with no scheduled-disease suspicion: the only shape
        # that separates the effective-hold OR from an AND.
        row.movement_restricted = True
        row.restriction_reason = "Regulatory movement hold"
        await db.commit()
    owner_view = await get_animal(client, owner, animal["id"])
    assert owner_view["movement_restricted"] is True
    assert owner_view["suspected_scheduled_disease"] is False

    viewer = await _animals_view_only_viewer(
        client, owner, "hold-reader@farm.in", "Movement Hold Reader"
    )

    redacted = await get_animal(client, viewer, animal["id"])
    assert redacted["movement_restricted"] is True
    assert redacted["suspected_scheduled_disease"] is False
    assert redacted["restriction_reason"] is None
