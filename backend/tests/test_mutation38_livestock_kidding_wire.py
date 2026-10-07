"""Public kidding pages and pregnancy links retain their declared wire contract."""

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import MultipleResultsFound
from sqlalchemy.orm import selectinload

from app.db import get_sessionmaker
from app.models import Animal, BreedingRecord, KiddingRecord
from app.schemas.kidding import KiddingCreateIn, KiddingRecordOut, KidIn
from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_extended import kid_on_ekd, post_kidding, pregnant_doe


async def test_kidding_pages_default_to_thirty_rows_and_zero_offsets(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    response = await client.get("/api/kidding", headers=owner)
    assert response.status_code == 200, response.text
    page = response.json()
    assert (page["limit"], page["upcoming_limit"], page["overdue_limit"]) == (30, 30, 30)
    assert (page["offset"], page["upcoming_offset"], page["overdue_offset"]) == (0, 0, 0)
    assert page["records"] == page["upcoming"] == page["overdue"] == []


@pytest.mark.parametrize("field", ["upcoming_limit", "overdue_limit"])
@pytest.mark.parametrize("limit,status", [(100, 200), (101, 422)])
async def test_kidding_due_pages_accept_one_hundred_and_reject_one_hundred_one(
    client: httpx.AsyncClient, field: str, limit: int, status: int
) -> None:
    owner = await owner_with_farm(client)
    response = await client.get("/api/kidding", params={field: limit}, headers=owner)
    assert response.status_code == status, response.text
    if status == 200:
        assert response.json()[field] == 100


@pytest.mark.parametrize("record_id", [-1, 2_147_483_648, 1])
async def test_kidding_pregnancy_missing_and_out_of_range_links_return404(
    client: httpx.AsyncClient, record_id: int
) -> None:
    owner = await owner_with_farm(client)
    response = await client.get(f"/api/kidding/pregnancies/{record_id}", headers=owner)
    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Breeding record not found"


async def test_kidding_creation_missing_record_returns404(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    response = await client.post(
        "/api/kidding",
        json={"breeding_record_id": 1, "date": today().isoformat(), "kids": [{"sex": "F"}]},
        headers=owner,
    )
    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Breeding record not found"


@pytest.mark.parametrize("restored_id,status", [(0, 404), (2_147_483_647, 200)])
async def test_kidding_pregnancy_links_apply_literal_int4_id_boundaries_to_restored_rows(
    client: httpx.AsyncClient, restored_id: int, status: int
) -> None:
    owner = await owner_with_farm(client)
    _doe, _buck, breeding = await pregnant_doe(client, owner)
    async with get_sessionmaker()() as db:
        original = await db.get(BreedingRecord, breeding["id"])
        assert original is not None and original.outcome == "CONFIRMED_PREGNANT"
        # Restore the same actual confirmed event under a legacy key. The
        # normal commit preserves all FKs/CHECKs and the original duties;
        # no second pregnancy or clinical completion is manufactured.
        restored = BreedingRecord(
            id=restored_id,
            created_at=original.created_at,
            farm_id=original.farm_id,
            doe_id=original.doe_id,
            buck_id=original.buck_id,
            semen_sire_name=original.semen_sire_name,
            breeding_date=original.breeding_date,
            method=original.method,
            heat_cycle_number=original.heat_cycle_number,
            ultrasound_date=original.ultrasound_date,
            ultrasound_result_date=original.ultrasound_result_date,
            ultrasound_done=original.ultrasound_done,
            pregnant=original.pregnant,
            kid_count_detected=original.kid_count_detected,
            expected_kidding_date=original.expected_kidding_date,
            outcome=original.outcome,
            created_by_id=original.created_by_id,
        )
        db.add(restored)
        await db.commit()
        assert restored.id == restored_id
    response = await client.get(f"/api/kidding/pregnancies/{restored_id}", headers=owner)
    assert response.status_code == status, response.text
    if status == 200:
        assert response.json()["id"] == 2_147_483_647


async def test_live_pregnancy_lookup_ignores_another_does_completed_litter(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    doe, _buck, live = await pregnant_doe(client, owner, tag="WIRE-LIVE")
    _other_doe, _other_buck, delivered = await pregnant_doe(
        client, owner, tag="WIRE-DELIVERED", gestation_days=160
    )
    await kid_on_ekd(client, owner, delivered)
    try:
        response = await client.get(f"/api/kidding/pregnancies/{live['id']}", headers=owner)
    except MultipleResultsFound as error:
        pytest.fail(f"A live pregnancy link must resolve once in a normal herd: {error}")
    assert response.status_code == 200, response.text
    assert response.json()["id"] == live["id"]
    assert response.json()["doe_id"] == doe["id"]
    completed = await client.get(f"/api/kidding/pregnancies/{delivered['id']}", headers=owner)
    assert completed.status_code == 404, completed.text


def test_native_kid_input_normalizes_identifier_whitespace() -> None:
    kid = KidIn.model_validate({"sex": "F", "tag": "  RESTORED-TAG  "})
    assert kid.tag == "RESTORED-TAG"


@pytest.mark.parametrize("length", [49, 50, 51])
def test_native_kid_identifier_accepts_fifty_characters_and_rejects_fifty_one(length: int) -> None:
    tag = "K" * length
    if length <= 50:
        try:
            kid = KidIn.model_validate({"sex": "F", "tag": tag})
        except ValidationError as error:
            pytest.fail(f"The 50-character kid identifier contract must accept this: {error}")
        assert kid.tag == tag
    else:
        with pytest.raises(ValidationError):
            KidIn.model_validate({"sex": "F", "tag": tag})


def test_native_kidding_input_requires_at_least_one_kid() -> None:
    with pytest.raises(ValidationError):
        KiddingCreateIn.model_validate(
            {"breeding_record_id": 1, "date": today().isoformat(), "kids": []}
        )


async def test_native_kidding_output_validates_real_eager_loaded_records(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    _doe, _buck, breeding = await pregnant_doe(client, owner, gestation_days=160)
    delivered = await kid_on_ekd(client, owner, breeding)
    async with get_sessionmaker()() as db:
        record = (
            await db.execute(
                select(KiddingRecord)
                .options(selectinload(KiddingRecord.kids))
                .where(KiddingRecord.id == delivered["id"])
            )
        ).scalar_one()
        try:
            response_model = KiddingRecordOut.model_validate(record)
        except ValidationError as error:
            pytest.fail(f"The output DTO must accept a real eager-loaded kidding record: {error}")
        assert response_model.id == delivered["id"]
        assert {kid.id for kid in response_model.kids} == {kid["id"] for kid in delivered["kids"]}


async def test_kid_birth_weight_accepts_half_a_kilo_and_reports_the_literal_credible_band(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    _doe, _buck, breeding = await pregnant_doe(client, owner, gestation_days=160)
    invalid = await post_kidding(
        client,
        owner,
        breeding["id"],
        date=breeding["expected_kidding_date"],
        kids=[{"sex": "F", "birth_weight": 0.49}],
    )
    assert invalid.status_code == 422, invalid.text
    assert invalid.json()["detail"] == (
        "kid birth weight must be between 0.5 and 8 kg — 0.49 kg is not a credible newborn weight"
    )
    accepted = await post_kidding(
        client,
        owner,
        breeding["id"],
        date=breeding["expected_kidding_date"],
        kids=[{"sex": "F", "birth_weight": 0.5}],
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["kids"][0]["birth_weight"] == 0.5


async def test_restored_dam_birth_date_equal_to_delivery_is_not_before_birth(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    doe, _buck, breeding = await pregnant_doe(client, owner, gestation_days=160)
    async with get_sessionmaker()() as db:
        recorded_doe = await db.get(Animal, doe["id"])
        assert recorded_doe is not None
        # This router expressly defends DOB metadata edited after the
        # confirmed event. Keep all constraints enabled and pin the literal
        # inclusive boundary: the delivery is not before the stored DOB.
        from datetime import date

        recorded_doe.date_of_birth = date.fromisoformat(breeding["expected_kidding_date"])
        await db.commit()
    response = await post_kidding(
        client, owner, breeding["id"], date=breeding["expected_kidding_date"]
    )
    assert response.status_code == 201, response.text
    assert response.json()["date"] == breeding["expected_kidding_date"]


async def test_another_farms_stillborn_tag_does_not_reserve_this_farms_namespace(
    client: httpx.AsyncClient,
) -> None:
    other = await owner_with_farm(client, email="foreign-kid-namespace@farm.in")
    _doe, _buck, foreign_pregnancy = await pregnant_doe(client, other, gestation_days=160)
    tag = "SHARED-NEWBORN-TAG"
    foreign = await post_kidding(
        client,
        other,
        foreign_pregnancy["id"],
        date=foreign_pregnancy["expected_kidding_date"],
        kids=[{"sex": "F", "status": "STILLBORN", "tag": tag}],
    )
    assert foreign.status_code == 201, foreign.text
    assert foreign.json()["kids"][0]["animal_id"] is None
    owner = await owner_with_farm(client)
    _own_doe, _own_buck, pregnancy = await pregnant_doe(client, owner, gestation_days=160)
    own = await post_kidding(
        client,
        owner,
        pregnancy["id"],
        date=pregnancy["expected_kidding_date"],
        kids=[{"sex": "F", "tag": tag}],
    )
    assert own.status_code == 201, own.text
    assert own.json()["kids"][0]["tag"] == tag
