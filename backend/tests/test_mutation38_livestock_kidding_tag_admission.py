"""Real kid namespaces retain bounded fallback and native admission/defaults."""

import httpx
import pytest
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import Animal, BreedingRecord, Farm, KiddingRecord
from app.services.kidding import KidSpec, record_kidding
from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_extended import make_animal
from .test_kidding_husbandry import confirmed_pregnancy, post_kidding


def _fallback(base: str, entropy: str) -> str:
    suffix = f"-A{entropy}"
    return f"{base[: 50 - len(suffix)]}{suffix}"


@pytest.mark.parametrize(("collisions", "accepted"), [(3, True), (4, False)])
async def test_public_kid_tag_fallback_retains_its_four_attempt_budget(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, collisions: int, accepted: bool
) -> None:
    owner = await owner_with_farm(client)
    doe, _buck, breeding = await confirmed_pregnancy(
        client, owner, tag="KID-BUDGET", bred_days_ago=150, kid_count=1
    )
    base = f"{doe['tag_number']}-K1"
    entropy = [f"{index:012x}" for index in range(1, 7)]
    await make_animal(client, owner, base)
    for symbol in entropy[:collisions]:
        await make_animal(client, owner, _fallback(base, symbol))
    symbols = iter(entropy)

    def choose_entropy(byte_count: int) -> str:
        return next(symbols)

    # Only entropy is controlled. Every probe, INSERT, namespace trigger,
    # transaction rollback and persisted delivery is real.
    monkeypatch.setattr("app.services.kidding.secrets.token_hex", choose_entropy)
    response = await post_kidding(client, owner, int(breeding["id"]), today().isoformat())
    assert response.status_code == (201 if accepted else 409), response.text
    async with get_sessionmaker()() as db:
        records = list(
            (
                await db.execute(
                    select(KiddingRecord).where(KiddingRecord.breeding_record_id == breeding["id"])
                )
            ).scalars()
        )
        assert bool(records) is accepted
    if accepted:
        assert response.json()["kids"][0]["tag"] == _fallback(base, entropy[3])
    else:
        assert (
            response.json()["detail"]
            == "Could not allocate a unique kid tag; retry the kidding request"
        )


async def test_public_litter_retries_an_already_assigned_random_fallback(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client)
    # A valid 50-character dam tag makes both readable kid bases truncate to
    # her occupied tag. The second kid first draws the first kid's fallback.
    doe, _buck, breeding = await confirmed_pregnancy(
        client, owner, tag="Q" * 48, bred_days_ago=150, kid_count=2
    )
    assert len(doe["tag_number"]) == 50
    symbols = iter(["111111111111", "111111111111", "222222222222"])

    def choose_entropy(byte_count: int) -> str:
        return next(symbols)

    monkeypatch.setattr("app.services.kidding.secrets.token_hex", choose_entropy)
    response = await post_kidding(
        client, owner, int(breeding["id"]), today().isoformat(), kids=[{"sex": "F"}, {"sex": "M"}]
    )
    assert response.status_code == 201, response.text
    expected = {
        _fallback(f"{doe['tag_number']}-K1", "111111111111"),
        _fallback(f"{doe['tag_number']}-K2", "222222222222"),
    }
    assert {kid["tag"] for kid in response.json()["kids"]} == expected


def _native_kid(tag: str) -> KidSpec:
    return {
        "tag": tag,
        "tag_is_explicit": True,
        "sex": "F",
        "birth_weight": 2.6,
        "status": "ALIVE",
        "mortality_reported_at": None,
        "colostrum_within_2h": None,
        "navel_dipped": None,
        "dam_rejected": False,
    }


@pytest.mark.parametrize(
    "collision", [False, True], ids=["omitted-mastitis-default", "existing-explicit-tag"]
)
async def test_native_kidding_retains_omitted_clinical_default_and_predictable_tag_rejection(
    client: httpx.AsyncClient, collision: bool
) -> None:
    owner = await owner_with_farm(client)
    doe, buck, breeding = await confirmed_pregnancy(
        client, owner, tag="NATIVE-KID", bred_days_ago=150, kid_count=1
    )
    kid_tag = "EXISTING-KID-NAMESPACE" if collision else "NEW-NATIVE-KID"
    if collision:
        await make_animal(client, owner, kid_tag)
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, int(owner["X-Farm-Id"]))
        assert farm is not None
        await db.execute(
            select(Animal.id)
            .where(Animal.id.in_([doe["id"], buck["id"]]))
            .order_by(Animal.id)
            .with_for_update()
        )
        record = (
            await db.execute(
                select(BreedingRecord).where(BreedingRecord.id == breeding["id"]).with_for_update()
            )
        ).scalar_one()
        try:
            kidding = await record_kidding(
                db,
                farm,
                record,
                today(),
                "NORMAL",
                "Actual native delivery",
                [_native_kid(kid_tag)],
                created_by_id=farm.owner_id,
            )
        except ValueError as error:
            assert collision, str(error)
            assert str(error) == "A kid tag already exists in this farm"
            # A declared native preflight failure must leave an ordinary
            # transaction usable for its caller's deliberate rollback.
            assert (
                await db.scalar(select(Animal.id).where(Animal.tag_number == kid_tag)) is not None
            )
            await db.rollback()
        except Exception as error:
            pytest.fail(f"Native kid-tag admission must reject before storage failure: {error!r}")
        else:
            assert not collision, "A conflicting explicit native kid tag was admitted"
            assert kidding.mastitis_suspected is False
            await db.commit()
            saved = await db.get(KiddingRecord, kidding.id)
            assert saved is not None and saved.mastitis_suspected is False
    # HTTP validates explicit collisions and supplies the clinical flag
    # before calling this helper; these are its exported native contracts.
