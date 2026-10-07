"""Public litter outcomes schedule the literal postpartum care and recovery calendar."""

from datetime import timedelta

import httpx
import pytest

from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_extended import all_tasks, make_kidding, pregnant_doe


@pytest.mark.parametrize("litter", ["alive", "needs-support", "stillborn", "neonatal-loss"])
async def test_public_postpartum_duties_follow_real_litter_outcomes_and_localized_dates(
    client: httpx.AsyncClient, litter: str
) -> None:
    owner = await owner_with_farm(client)
    reference_date = today()
    delivery_date = reference_date - timedelta(days=10)
    doe, _, breeding = await pregnant_doe(client, owner, tag="POSTPARTUM-DOE", gestation_days=160)
    kids: list[dict[str, object]]
    if litter == "alive":
        kids = [{"sex": "M", "colostrum_within_2h": True}, {"sex": "F"}]
    elif litter == "needs-support":
        kids = [
            {"sex": "M", "dam_rejected": True, "colostrum_within_2h": False},
            {"sex": "F", "status": "STILLBORN"},
        ]
    elif litter == "stillborn":
        kids = [{"sex": "F", "status": "STILLBORN"}]
    else:
        kids = [
            {
                "sex": sex,
                "status": "DIED",
                "mortality_reported_at": (delivery_date + timedelta(days=days)).isoformat(),
            }
            for sex, days in (("M", 2), ("F", 4))
        ]
    delivery = await make_kidding(
        client, owner, int(breeding["id"]), date=delivery_date.isoformat(), kids=kids
    )
    assert delivery["date"] == delivery_date.isoformat()
    assert len(delivery["kids"]) == len(kids)
    pending = [
        task
        for task in await all_tasks(client, owner)
        if task["breeding_record_id"] == breeding["id"] and task["status"] == "PENDING"
    ]
    care_date = (delivery_date + timedelta(days=1)).isoformat()
    expected = {
        "post_kidding_dam_check": ("HEALTH_CHECK", care_date),
        "kidding_stall_cleanout": ("CLEANING", care_date),
    }
    if litter in {"alive", "needs-support"}:
        expected["wean_kids"] = ("WEANING", (delivery_date + timedelta(days=60)).isoformat())
    else:
        # A real delayed neonatal loss extends dam recovery from the last
        # mortality fact; a stillbirth anchors recovery at delivery itself.
        lead_days = 18 if litter == "neonatal-loss" else 14
        expected["move_to_resting"] = (
            "BUCKET_MOVE",
            (delivery_date + timedelta(days=lead_days)).isoformat(),
        )
    if litter == "needs-support":
        expected["kid_support"] = ("HEALTH_CHECK", care_date)
    assert len(pending) == len(expected)
    assert {task["title_key"] for task in pending} == expected.keys()
    for key, (category, due_date) in expected.items():
        duties = [task for task in pending if task["title_key"] == key]
        assert len(duties) == 1, key
        duty = duties[0]
        assert duty["animal_id"] == doe["id"]
        assert duty["category"] == category
        assert duty["due_date"] == due_date, key
        assert duty["title_args"] == {"tag": "POSTPARTUM-DOE", "due_date": due_date}, key
