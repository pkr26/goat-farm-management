"""Husbandry-standards regressions for the kidding record path.

Wave-1A additions to record_kidding: server-derived parity, CAESAREAN ease,
neonatal care facts on kids (colostrum / navel dip / dam rejection),
postpartum dam facts (placenta / mastitis), and the generated duties
(postpartum dam check, stall cleanout, conditional kid support).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import (
    Bucket,
    BucketMove,
    KiddingRecord,
    KidEntry,
    Task,
    TaskCategory,
    TaskStatus,
)
from app.models.species import GOAT_PROFILE
from app.schemas.kidding import KiddingCreateIn
from app.utils import today

from .conftest import owner_with_farm


def iso(d: date) -> str:
    return d.isoformat()


async def make_animal(
    client: httpx.AsyncClient, headers: dict[str, str], tag: str, *, sex: str
) -> dict:
    payload = {
        "tag_number": tag,
        "sex": sex,
        "source": "PURCHASED",
        "current_bucket": "FOUNDATION",
        "date_of_birth": iso(today() - timedelta(days=800)),
        "historical_import_reason": "Kidding-husbandry fixture",
        "weight_kg": 26.0 if sex == "F" else 32.0,
        "weight_date": iso(today() - timedelta(days=800)),
    }
    resp = await client.post("/api/animals", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def breed(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    doe_id: int,
    buck_id: int,
    breeding_date: date,
) -> dict:
    resp = await client.post(
        "/api/breeding",
        json={
            "doe_id": doe_id,
            "buck_id": buck_id,
            "breeding_date": iso(breeding_date),
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def confirm_pregnancy(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    breeding_id: int,
    *,
    kid_count: int = 2,
) -> dict:
    detail = (await client.get(f"/api/breeding/{breeding_id}", headers=headers)).json()
    scan = await client.post(
        f"/api/breeding/{breeding_id}/ultrasound",
        json={
            "pregnant": True,
            "kid_count": kid_count,
            "date": detail["ultrasound_date"],
        },
        headers=headers,
    )
    assert scan.status_code == 200, scan.text
    return (await client.get(f"/api/breeding/{breeding_id}", headers=headers)).json()


async def confirmed_pregnancy(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    *,
    tag: str,
    bred_days_ago: int,
    kid_count: int = 2,
) -> tuple[dict, dict, dict]:
    """Doe + buck, service `bred_days_ago` back, ultrasound-confirmed."""
    doe = await make_animal(client, headers, f"{tag}-F", sex="F")
    buck = await make_animal(client, headers, f"{tag}-M", sex="M")
    br = await breed(
        client, headers, doe["id"], buck["id"], today() - timedelta(days=bred_days_ago)
    )
    detail = await confirm_pregnancy(client, headers, br["id"], kid_count=kid_count)
    return doe, buck, detail


async def post_kidding(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    breeding_id: int,
    on: str,
    **overrides: Any,
) -> httpx.Response:
    payload: dict[str, Any] = {
        "breeding_record_id": breeding_id,
        "date": on,
        "ease": "NORMAL",
        "kids": [{"sex": "F"}],
    } | overrides
    return await client.post("/api/kidding", json=payload, headers=headers)


async def breeding_tasks(farm_id: int, breeding_record_id: int) -> list[Task]:
    async with get_sessionmaker()() as db:
        return list(
            (
                await db.execute(
                    select(Task).where(
                        Task.farm_id == farm_id,
                        Task.breeding_record_id == breeding_record_id,
                    )
                )
            ).scalars()
        )


# ---------------------------------------------------------------------------
# Backward compatibility: legacy payloads and server-owned parity
# ---------------------------------------------------------------------------


async def test_legacy_payload_gets_wave1a_defaults(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await confirmed_pregnancy(client, headers, tag="HUSP-1", bred_days_ago=160)
    legacy = await post_kidding(
        client,
        headers,
        br["id"],
        br["expected_kidding_date"],
        kids=[{"sex": "M"}, {"sex": "F"}],
    )
    assert legacy.status_code == 201, legacy.text
    body = legacy.json()
    assert body["parity"] == 1
    assert body["placenta_passed"] is None
    assert body["mastitis_suspected"] is False
    assert [kid["colostrum_within_2h"] for kid in body["kids"]] == [None, None]
    assert [kid["navel_dipped"] for kid in body["kids"]] == [None, None]
    assert [kid["dam_rejected"] for kid in body["kids"]] == [False, False]


def test_parity_is_not_client_writable() -> None:
    with pytest.raises(ValidationError):
        KiddingCreateIn.model_validate(
            {
                "breeding_record_id": 1,
                "date": "2026-01-01",
                "parity": 3,
                "kids": [{"sex": "F"}],
            }
        )


async def test_parity_increments_across_two_kiddings(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    farm_id = int(headers["X-Farm-Id"])
    doe = await make_animal(client, headers, "HUSP-2-F", sex="F")
    buck = await make_animal(client, headers, "HUSP-2-M", sex="M")

    # First pregnancy: kidded 190 days ago with a stillborn litter (no
    # survivors → the doe recovers through her postpartum duty, not weaning).
    br1 = await breed(client, headers, doe["id"], buck["id"], today() - timedelta(days=340))
    detail1 = await confirm_pregnancy(client, headers, br1["id"], kid_count=1)
    first = await post_kidding(
        client,
        headers,
        br1["id"],
        detail1["expected_kidding_date"],
        kids=[{"sex": "F", "status": "STILLBORN"}],
    )
    assert first.status_code == 201, first.text
    assert first.json()["parity"] == 1

    async with get_sessionmaker()() as db:
        recovery = (
            await db.execute(
                select(Task).where(
                    Task.farm_id == farm_id,
                    Task.breeding_record_id == br1["id"],
                    Task.category == TaskCategory.BUCKET_MOVE.value,
                    Task.status == TaskStatus.PENDING.value,
                )
            )
        ).scalar_one()
    done = await client.post(f"/api/tasks/{recovery.id}/complete", headers=headers)
    assert done.status_code == 200, done.text
    # Completing the duty today stamps the RESTING entry with today's date
    # (task completion is an event on the farm's business calendar), but the
    # paper timeline has the doe resting since her recovery due date —
    # kidding day + postpartum_recovery_days, 176 days ago. Backdate the
    # entry like any late-recorded fact; the rest-and-flush window is
    # effective-date based, so the re-breed move below reflects the real
    # calendar instead of being blocked for a 0-day residency.
    kidding_day = date.fromisoformat(detail1["expected_kidding_date"])
    async with get_sessionmaker()() as db:
        resting_entry = (
            await db.execute(
                select(BucketMove)
                .where(
                    BucketMove.farm_id == farm_id,
                    BucketMove.animal_id == doe["id"],
                    BucketMove.to_bucket == Bucket.RESTING.value,
                )
                .order_by(BucketMove.id.desc())
                .limit(1)
            )
        ).scalar_one()
        resting_entry.effective_date = kidding_day + timedelta(
            days=GOAT_PROFILE.postpartum_recovery_days
        )
        await db.commit()
    moved = await client.post(
        f"/api/animals/{doe['id']}/move",
        json={"to_bucket": "BREEDING", "reason": "re-breed after recovery"},
        headers=headers,
    )
    assert moved.status_code == 200, moved.text

    # Second pregnancy (well past the 14-day voluntary waiting period).
    br2 = await breed(client, headers, doe["id"], buck["id"], today() - timedelta(days=170))
    detail2 = await confirm_pregnancy(client, headers, br2["id"], kid_count=1)
    second = await post_kidding(client, headers, br2["id"], detail2["expected_kidding_date"])
    assert second.status_code == 201, second.text
    assert second.json()["parity"] == 2


# ---------------------------------------------------------------------------
# Stillborn litter validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "care_field",
    [
        {"colostrum_within_2h": True},
        {"navel_dipped": True},
        {"dam_rejected": True},
    ],
)
async def test_stillborn_kid_rejects_neonatal_care_fields(
    client: httpx.AsyncClient, care_field: dict[str, bool]
) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await confirmed_pregnancy(client, headers, tag="HUSP-3", bred_days_ago=160)
    resp = await post_kidding(
        client,
        headers,
        br["id"],
        br["expected_kidding_date"],
        kids=[{"sex": "F", "status": "STILLBORN"} | care_field],
    )
    assert resp.status_code == 422, resp.text


async def test_stillborn_kid_without_care_fields_records_nulls(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await confirmed_pregnancy(client, headers, tag="HUSP-4", bred_days_ago=160)
    resp = await post_kidding(
        client,
        headers,
        br["id"],
        br["expected_kidding_date"],
        kids=[{"sex": "F", "status": "STILLBORN"}],
    )
    assert resp.status_code == 201, resp.text
    kid = resp.json()["kids"][0]
    assert kid["colostrum_within_2h"] is None
    assert kid["navel_dipped"] is None
    assert kid["dam_rejected"] is False


# ---------------------------------------------------------------------------
# CAESAREAN ease and postpartum dam facts
# ---------------------------------------------------------------------------


async def test_caesarean_ease_accepted_and_persisted(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await confirmed_pregnancy(client, headers, tag="HUSP-5", bred_days_ago=160)
    resp = await post_kidding(
        client, headers, br["id"], br["expected_kidding_date"], ease="CAESAREAN"
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["ease"] == "CAESAREAN"
    async with get_sessionmaker()() as db:
        row = (
            await db.execute(select(KiddingRecord).where(KiddingRecord.id == resp.json()["id"]))
        ).scalar_one()
    assert row.ease == "CAESAREAN"


async def test_postpartum_dam_facts_persisted(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    _doe, _buck, br = await confirmed_pregnancy(client, headers, tag="HUSP-6", bred_days_ago=160)
    resp = await post_kidding(
        client,
        headers,
        br["id"],
        br["expected_kidding_date"],
        placenta_passed=True,
        mastitis_suspected=True,
        kids=[{"sex": "F", "colostrum_within_2h": True, "navel_dipped": True}],
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["placenta_passed"] is True
    assert body["mastitis_suspected"] is True
    async with get_sessionmaker()() as db:
        record = (
            await db.execute(select(KiddingRecord).where(KiddingRecord.id == body["id"]))
        ).scalar_one()
        entry = (
            await db.execute(select(KidEntry).where(KidEntry.kidding_record_id == record.id))
        ).scalar_one()
    assert record.placenta_passed is True
    assert record.mastitis_suspected is True
    assert entry.colostrum_within_2h is True
    assert entry.navel_dipped is True
    assert entry.dam_rejected is False


# ---------------------------------------------------------------------------
# Generated postpartum duties
# ---------------------------------------------------------------------------


async def test_postpartum_dam_check_and_stall_cleanout_duties(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    doe, _buck, br = await confirmed_pregnancy(client, headers, tag="HUSP-7", bred_days_ago=160)
    kidding_date = date.fromisoformat(br["expected_kidding_date"])
    resp = await post_kidding(
        client,
        headers,
        br["id"],
        br["expected_kidding_date"],
        kids=[
            {"sex": "M", "colostrum_within_2h": True, "navel_dipped": True},
            {"sex": "F", "colostrum_within_2h": True},
        ],
    )
    assert resp.status_code == 201, resp.text

    tasks = await breeding_tasks(int(headers["X-Farm-Id"]), br["id"])
    due = kidding_date + timedelta(days=GOAT_PROFILE.postpartum_care_lead_days)

    dam_checks = [t for t in tasks if t.category == TaskCategory.HEALTH_CHECK.value]
    assert len(dam_checks) == 1
    dam_check = dam_checks[0]
    assert dam_check.title == (
        f"Post-kidding dam check: {doe['tag_number']} — placenta passed? "
        "udder/mastitis check, warm water, light feed, clean hindquarters"
    )
    assert dam_check.due_date == due
    assert dam_check.animal_id == doe["id"]
    assert dam_check.breeding_record_id == br["id"]
    assert dam_check.auto_generated is True

    cleanings = [t for t in tasks if t.category == TaskCategory.CLEANING.value]
    assert len(cleanings) == 1
    cleaning = cleanings[0]
    assert cleaning.title == (
        f"Clean & disinfect kidding stall: {doe['tag_number']} — "
        "remove soiled bedding, disinfect, re-bed dry"
    )
    assert cleaning.due_date == due
    assert cleaning.animal_id == doe["id"]
    assert cleaning.breeding_record_id == br["id"]
    assert cleaning.auto_generated is True

    # Healthy litter (colostrum inside 2h, dam nursing) → no support duty.
    assert not any(t.title.startswith("Kid support:") for t in tasks)


@pytest.mark.parametrize(
    ("kid", "expect_support"),
    [
        ({"sex": "M", "colostrum_within_2h": False}, True),
        ({"sex": "M", "colostrum_within_2h": True, "dam_rejected": True}, True),
        ({"sex": "M", "colostrum_within_2h": True}, False),
        ({"sex": "M"}, False),  # unrecorded colostrum is not a missed feeding
    ],
)
async def test_kid_support_duty_follows_colostrum_and_rejection(
    client: httpx.AsyncClient, kid: dict[str, Any], expect_support: bool
) -> None:
    headers = await owner_with_farm(client)
    doe, _buck, br = await confirmed_pregnancy(client, headers, tag="HUSP-8", bred_days_ago=160)
    kidding_date = date.fromisoformat(br["expected_kidding_date"])
    resp = await post_kidding(client, headers, br["id"], br["expected_kidding_date"], kids=[kid])
    assert resp.status_code == 201, resp.text

    tasks = await breeding_tasks(int(headers["X-Farm-Id"]), br["id"])
    support = [t for t in tasks if t.title.startswith("Kid support:")]
    if not expect_support:
        assert support == []
        return
    assert len(support) == 1
    duty = support[0]
    assert duty.title == (
        f"Kid support: bottle-feed / colostrum replacer for {doe['tag_number']}'s litter"
    )
    assert duty.category == TaskCategory.HEALTH_CHECK.value
    assert duty.due_date == kidding_date + timedelta(days=GOAT_PROFILE.postpartum_care_lead_days)
    assert duty.animal_id == doe["id"]
    assert duty.breeding_record_id == br["id"]


async def test_kid_support_duty_ignores_non_alive_kids(client: httpx.AsyncClient) -> None:
    """A kid that died after birth never triggers the bottle-feed plan."""
    headers = await owner_with_farm(client)
    kidding_day = today() - timedelta(days=10)
    _doe, _buck, br = await confirmed_pregnancy(client, headers, tag="HUSP-9", bred_days_ago=170)
    resp = await post_kidding(
        client,
        headers,
        br["id"],
        iso(kidding_day),
        kids=[
            {
                "sex": "M",
                "status": "DIED",
                "colostrum_within_2h": False,
                "mortality_reported_at": iso(today() - timedelta(days=5)),
            }
        ],
    )
    assert resp.status_code == 201, resp.text
    tasks = await breeding_tasks(int(headers["X-Farm-Id"]), br["id"])
    assert not any(t.title.startswith("Kid support:") for t in tasks)
