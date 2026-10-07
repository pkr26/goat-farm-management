"""A confirmed public pregnancy exposes the complete literal goat duty calendar."""

from datetime import timedelta

import httpx

from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_extended import all_tasks, bred_doe, confirm


async def test_confirmed_pregnancy_publishes_consistent_dates_and_localized_duties(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    service_date = today() - timedelta(days=60)
    doe, _, breeding = await bred_doe(client, owner, tag="CALENDAR-DOE", breeding_date=service_date)
    confirmed = await confirm(client, owner, int(breeding["id"]))
    kidding_date = service_date + timedelta(days=150)
    assert confirmed["outcome"] == "CONFIRMED_PREGNANT"
    assert confirmed["expected_kidding_date"] == kidding_date.isoformat()

    pending = [
        task
        for task in await all_tasks(client, owner)
        if task["breeding_record_id"] == breeding["id"] and task["status"] == "PENDING"
    ]
    # The policy is specified literally here: two vaccine doses 15 days
    # apart, day100 ration change, delivery pen15 days before kidding,
    # kit seven days before, six daily watches, and the delivery itself.
    expected = [
        ("pre_kidding_vaccine", "VACCINE", 40, False),
        ("pre_kidding_vaccine_booster", "VACCINE", 25, False),
        ("move_to_delivery", "BUCKET_MOVE", 15, True),
        ("move_to_pregnancy_late", "BUCKET_MOVE", 50, False),
        ("birthing_kit_check", "BIRTHING_KIT", 7, True),
        ("kidding_due", "KIDDING_DUE", 0, False),
    ]
    assert len(pending) == 12
    assert {task["title_key"] for task in pending} == {key for key, _, _, _ in expected} | {
        "kidding_watch"
    }
    for key, category, lead_days, include_kidding_date in expected:
        duties = [task for task in pending if task["title_key"] == key]
        assert len(duties) == 1, key
        duty = duties[0]
        due_date = (kidding_date - timedelta(days=lead_days)).isoformat()
        args: dict[str, str | int] = {"tag": "CALENDAR-DOE", "due_date": due_date}
        if include_kidding_date:
            args["kidding_date"] = kidding_date.isoformat()
        assert duty["animal_id"] == doe["id"]
        assert duty["category"] == category
        assert duty["due_date"] == due_date, key
        assert duty["title_args"] == args, key

    watches = [task for task in pending if task["title_key"] == "kidding_watch"]
    expected_watches = [
        {
            "tag": "CALENDAR-DOE",
            "kidding_date": kidding_date.isoformat(),
            "days_before": offset,
            "due_date": (kidding_date - timedelta(days=offset)).isoformat(),
        }
        for offset in (5, 4, 3, 2, 1, 0)
    ]
    assert len(watches) == 6
    assert sorted(watch["due_date"] for watch in watches) == sorted(
        expected_watch["due_date"] for expected_watch in expected_watches
    )
    for watch in watches:
        assert watch["animal_id"] == doe["id"]
        assert watch["category"] == "KIDDING_WATCH"
        assert watch["title_args"] in expected_watches
        assert watch["due_date"] == watch["title_args"]["due_date"]
