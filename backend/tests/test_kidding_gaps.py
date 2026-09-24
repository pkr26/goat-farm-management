"""Kidding gap tests (2026-09-23 verification plan, category 4).

The single most valuable concurrency test in this module: N parallel
kidding submissions for the SAME doe — exactly one may commit (parity
increments once, kids are created once, one income of ledger-free facts),
the rest must fail with a defined conflict, never a 500 or a duplicate
litter. The advisory-lock ordering (parents in id order, then the breeding
record) is what makes this safe; this test attacks it through the API.

Also pins the transitive chronology fence (a kidding can never predate the
dam's own birth because breeding itself cannot predate it) and the parity
derivation when history was backdated.
"""

import asyncio
from datetime import timedelta

import httpx
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import Animal, KiddingRecord, KidEntry
from app.utils import today

from .conftest import owner_with_farm
from .test_e2e_lifecycle_audit import breed, make_animal, record_kidding


async def _confirmed_pregnancy(
    client: httpx.AsyncClient, owner: dict, *, tag: str, bred_days_ago: int, kid_count: int = 2
) -> tuple[dict, dict]:
    doe = await make_animal(client, owner, f"{tag}-F")
    buck = await make_animal(client, owner, f"{tag}-M", sex="M", weight_kg=32.0)
    br = await breed(client, owner, doe["id"], buck["id"], today() - timedelta(days=bred_days_ago))
    detail = (await client.get(f"/api/breeding/{br['id']}", headers=owner)).json()
    scan = await client.post(
        f"/api/breeding/{br['id']}/ultrasound",
        json={"pregnant": True, "kid_count": kid_count, "date": detail["ultrasound_date"]},
        headers=owner,
    )
    assert scan.status_code == 200, scan.text
    return doe, br


def _kidding_body(breeding_id: int, on: str) -> dict:
    return {
        "breeding_record_id": breeding_id,
        "date": on,
        "ease": "NORMAL",
        "notes": "",
        "kids": [
            {"sex": "M", "status": "ALIVE", "birth_weight": 2.5},
            {"sex": "F", "status": "ALIVE", "birth_weight": 2.3},
        ],
    }


async def test_concurrent_kiddings_same_doe_exactly_one_commits(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="race@farm.in")
    doe, br = await _confirmed_pregnancy(client, owner, tag="RACE", bred_days_ago=150)
    kidding_date = today() - timedelta(days=2)
    body = _kidding_body(br["id"], kidding_date.isoformat())

    # 8 genuinely parallel submissions (distinct idempotency keys — this is
    # the double-submit / two-tablet race, not a replay).
    responses = await asyncio.gather(
        *(
            client.post(
                "/api/kidding",
                json=body,
                headers=owner | {"Idempotency-Key": f"race-{i}"},
            )
            for i in range(8)
        )
    )
    codes = sorted(r.status_code for r in responses)
    assert codes.count(201) == 1, (
        f"expected exactly one 201, got {codes}: {[r.text[:80] for r in responses]}"
    )
    for resp in responses:
        assert resp.status_code in (201, 409), (
            f"undefined concurrent-kidding outcome {resp.status_code}: {resp.text[:150]}"
        )

    # Exactly one kidding record, parity 1, exactly one litter of live kids.
    async with get_sessionmaker()() as db:
        kiddings = (
            (
                await db.execute(
                    select(KiddingRecord).where(KiddingRecord.breeding_record_id == br["id"])
                )
            )
            .scalars()
            .all()
        )
        assert len(kiddings) == 1, f"{len(kiddings)} kidding rows for one breeding"
        assert kiddings[0].parity == 1, kiddings[0].parity
        kids = (
            (await db.execute(select(KidEntry).where(KidEntry.kidding_record_id == kiddings[0].id)))
            .scalars()
            .all()
        )
        assert len(kids) == 2, f"{len(kids)} kids committed (expected the single 2-kid litter)"
        born = [
            a
            for a in (await db.execute(select(Animal).where(Animal.dam_id == doe["id"])))
            .scalars()
            .all()
            if a.source == "BORN"
        ]
        assert len(born) == 2, f"{len(born)} BORN animals created (expected exactly one litter)"

    refreshed = (await client.get(f"/api/animals/{doe['id']}", headers=owner)).json()["animal"]
    assert refreshed["current_bucket"] == "RECOVERY", refreshed["current_bucket"]


async def test_kidding_date_before_dams_own_birth_is_unreachable(
    client: httpx.AsyncClient,
) -> None:
    """A kidding predating the dam's own birth is inexpressible through the
    API: the chain is fenced upstream — breeding cannot predate the doe's
    birth, a kidding cannot predate its breeding, and gestation is banded.
    Pin each link, then probe the one legacy corruption the API cannot
    produce (a DOB edited after the fact) and record its behaviour."""
    owner = await owner_with_farm(client, email="chrono@farm.in")
    doe = await make_animal(client, owner, "CHRONO-F", dob_days=800)
    buck = await make_animal(client, owner, "CHRONO-M", sex="M", weight_kg=32.0)

    # Link 1: a service dated before the doe's own birth is refused.
    resp = await client.post(
        "/api/breeding",
        json={
            "doe_id": doe["id"],
            "buck_id": buck["id"],
            "breeding_date": (today() - timedelta(days=900)).isoformat(),
        },
        headers=owner,
    )
    assert resp.status_code in (400, 409, 422), resp.text[:200]

    # A legal service, then a kidding dated before that service.
    br = await breed(client, owner, doe["id"], buck["id"], today() - timedelta(days=150))
    detail = (await client.get(f"/api/breeding/{br['id']}", headers=owner)).json()
    scan = await client.post(
        f"/api/breeding/{br['id']}/ultrasound",
        json={"pregnant": True, "kid_count": 2, "date": detail["ultrasound_date"]},
        headers=owner,
    )
    assert scan.status_code == 200, scan.text
    early = await client.post(
        "/api/kidding",
        json=_kidding_body(br["id"], (today() - timedelta(days=160)).isoformat()),
        headers=owner,
    )
    assert early.status_code == 400, early.text[:200]
    assert "breeding date" in early.json()["detail"]

    # Link 3 (legacy corruption probe): DOB edited after the fact to sit
    # after the kidding date. The API itself cannot produce this state, but
    # the kidding endpoint now re-checks the dam's DOB (2026-09-23 fix) so
    # the corruption cannot deliver "before the dam was born" — the kid
    # rows copy the kidding date as their DOB and would inherit it.
    kidding_date = today() - timedelta(days=3)
    async with get_sessionmaker()() as db:
        row = await db.get(Animal, doe["id"])
        assert row is not None
        row.date_of_birth = today() - timedelta(days=1)  # "born yesterday", pregnant 150 days
        await db.commit()
    corrupted = await client.post(
        "/api/kidding", json=_kidding_body(br["id"], kidding_date.isoformat()), headers=owner
    )
    assert corrupted.status_code == 400, (
        f"corrupted-DOB kidding accepted: {corrupted.status_code} {corrupted.text[:150]}"
    )
    assert "dam's recorded birth" in corrupted.json()["detail"], corrupted.json()["detail"]


async def test_second_kidding_on_the_same_pregnancy_conflicts(client: httpx.AsyncClient) -> None:
    """The serial (non-race) version: after a committed kidding, a second
    submission for the same breeding answers the defined conflict."""
    owner = await owner_with_farm(client, email="serial@farm.in")
    _doe, br = await _confirmed_pregnancy(client, owner, tag="SER", bred_days_ago=150)
    kidding_date = today() - timedelta(days=2)
    await record_kidding(
        client,
        owner,
        br["id"],
        kidding_date,
        [
            {"sex": "M", "status": "ALIVE", "birth_weight": 2.5},
            {"sex": "F", "status": "ALIVE", "birth_weight": 2.3},
        ],
    )
    again = await client.post(
        "/api/kidding", json=_kidding_body(br["id"], kidding_date.isoformat()), headers=owner
    )
    assert again.status_code == 409, again.text
