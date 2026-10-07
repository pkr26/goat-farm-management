"""A dam's herd exit consumes only her own genuine reproductive history."""

from datetime import timedelta

import httpx
import pytest

from app.db import get_sessionmaker
from app.models import Animal, BreedingRecord
from app.utils import today

from .conftest import owner_with_farm
from .test_animals_status_husbandry import change_status
from .test_kidding_husbandry import confirmed_pregnancy, post_kidding


@pytest.mark.parametrize(
    "delivered", [False, True], ids=["open-confirmed-pregnancy", "dependent-new-litter"]
)
async def test_terminal_dam_status_ignores_an_unrelated_real_delivery_and_weaning_history(
    client: httpx.AsyncClient, delivered: bool
) -> None:
    owner = await owner_with_farm(client)
    event_date = today()
    other_dam, _buck, other_breeding = await confirmed_pregnancy(
        client, owner, tag="PREVIOUS-LITTER", bred_days_ago=170, kid_count=1
    )
    other_birth = await post_kidding(
        client,
        owner,
        int(other_breeding["id"]),
        (event_date - timedelta(days=20)).isoformat(),
        kids=[{"sex": "F"}],
    )
    assert other_birth.status_code == 201, other_birth.text
    unrelated_child_id = int(other_birth.json()["kids"][0]["animal_id"])
    other_exit = await change_status(
        client, owner, int(other_dam["id"]), "DEAD", date=event_date.isoformat()
    )
    assert other_exit.status_code == 200, other_exit.text
    async with get_sessionmaker()() as db:
        earlier_child = await db.get(Animal, unrelated_child_id)
        assert earlier_child is not None and earlier_child.current_bucket == "FEMALE_KIDS"

    dam, _buck, breeding = await confirmed_pregnancy(
        client, owner, tag="CURRENT-LITTER", bred_days_ago=170, kid_count=1
    )
    child_id: int | None = None
    if delivered:
        birth = await post_kidding(
            client,
            owner,
            int(breeding["id"]),
            (event_date - timedelta(days=20)).isoformat(),
            kids=[{"sex": "M"}],
        )
        assert birth.status_code == 201, birth.text
        child_id = int(birth.json()["kids"][0]["animal_id"])
    exit_response = await change_status(
        client, owner, int(dam["id"]), "DEAD", date=event_date.isoformat()
    )
    assert exit_response.status_code == 200, exit_response.text
    async with get_sessionmaker()() as db:
        closed = await db.get(BreedingRecord, breeding["id"])
        retired = await db.get(Animal, dam["id"])
        assert closed is not None and retired is not None and retired.status == "DEAD"
        if delivered:
            assert child_id is not None
            child = await db.get(Animal, child_id)
            assert child is not None and child.status == "ACTIVE"
            assert child.current_bucket == "MALE_KIDS"
            assert closed.outcome == "CONFIRMED_PREGNANT" and closed.loss_date is None
        else:
            assert closed.outcome == "ABORTED" and closed.loss_date == event_date
            assert closed.loss_cause == "ANIMAL_STATUS_CHANGE"
