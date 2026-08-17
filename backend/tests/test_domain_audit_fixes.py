"""Focused regressions for the 2026-08-09 backend domain audit."""

import asyncio
from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

import app.api.animals as animals_api
import app.services.tasks as task_service
from app.db import get_sessionmaker
from app.main import create_app
from app.models import (
    Animal,
    BreedingOutcome,
    BreedingRecord,
    BucketMove,
    KiddingRecord,
    KidEntry,
    Task,
    TaskCategory,
    TaskStatus,
    conception_rate,
)
from app.services import complete_task
from app.utils import today, utcnow

from .conftest import owner_with_farm
from .test_breeding_extended import confirm, make_breeding, make_buck, make_doe
from .test_finance_extended import correction_payload
from .test_finance_extended import make_animal as make_finance_animal
from .test_health_extended import (
    get_batch,
    get_schedule,
    make_batch,
    record_event,
    row_by_name,
)
from .test_health_extended import (
    make_animal as make_health_animal,
)
from .test_tasks_extended import complete_duty, get_tabs, make_duty


async def _seed_rejection_metadata(
    farm_id: int, *, breeding_record_id: int | None = None
) -> list[int]:
    """Create the valid PENDING rejection state handled by terminal workflows."""
    async with get_sessionmaker()() as db:
        query = select(Task).where(
            Task.farm_id == farm_id,
            Task.status == TaskStatus.PENDING.value,
        )
        if breeding_record_id is not None:
            query = query.where(Task.breeding_record_id == breeding_record_id)
        tasks = list((await db.execute(query)).scalars())
        assert tasks
        for task in tasks:
            task.verification_note = "Returned for correction"
        await db.commit()
        return [task.id for task in tasks]


async def _assert_rejection_cleared(task_ids: list[int]) -> None:
    async with get_sessionmaker()() as db:
        tasks = list((await db.execute(select(Task).where(Task.id.in_(task_ids)))).scalars())
    assert tasks
    assert all(task.verification_note is None for task in tasks)
    assert all(task.rejected_by_id is None for task in tasks)
    assert all(task.rejected_at is None for task in tasks)


async def test_unassessed_services_are_not_kpi_failures(client: httpx.AsyncClient) -> None:
    assert conception_rate([BreedingRecord(outcome=BreedingOutcome.UNASSESSED.value)]) is None

    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner, tag="KPI-UNASSESSED")
    buck = await make_buck(client, owner, tag="KPI-BUCK")
    breeding = await make_breeding(
        client,
        owner,
        doe["id"],
        buck["id"],
        breeding_date=(today() - timedelta(days=40)).isoformat(),
    )
    rejected_task_ids = await _seed_rejection_metadata(
        int(owner["X-Farm-Id"]), breeding_record_id=breeding["id"]
    )
    retired = await client.post(
        f"/api/animals/{doe['id']}/status",
        json={"new_status": "SOLD"},
        headers=owner,
    )
    assert retired.status_code == 200, retired.text
    closed = await client.get(f"/api/breeding/{breeding['id']}", headers=owner)
    assert closed.json()["outcome"] == "UNASSESSED"
    stats = (await client.get("/api/dashboard/reports", headers=owner)).json()["breeding"]
    assert stats["conception_rate"] is None
    assert stats["first_cycle_rate"] is None
    await _assert_rejection_cleared(rejected_task_ids)


async def test_schedule_inference_uses_words_and_first_dose_anchors_booster(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    unrelated = await make_health_animal(
        client,
        owner,
        tag="SAFE-PPR",
        date_of_birth=(today() - timedelta(days=400)).isoformat(),
    )
    await record_event(
        client,
        owner,
        animal_id=unrelated["id"],
        type="VACCINE",
        product_name="Suppressor vaccine",
    )
    ppr = row_by_name(await get_schedule(client, owner, unrelated["id"]), "PPR")
    assert ppr["last_done"] is None

    first_dose = await make_health_animal(
        client,
        owner,
        tag="ACTUAL-BOOSTER",
        date_of_birth=(today() - timedelta(days=400)).isoformat(),
    )
    await record_event(
        client,
        owner,
        animal_id=first_dose["id"],
        type="VACCINE",
        product_name="FMD vaccine",
    )
    fmd = row_by_name(await get_schedule(client, owner, first_dose["id"]), "FMD")
    # FMD's seeded booster_weeks is 3.5; timedelta(weeks=3.5) truncates to 24
    # days (date arithmetic drops the timedelta's sub-day remainder), but the
    # schedule rounds the 24.5-day interval half-up to 25.
    actual_booster = today() + timedelta(days=25)
    assert fmd["booster_due"] == actual_booster.isoformat()
    assert fmd["next_due"] == actual_booster.isoformat()


async def test_task_aware_batch_preview_is_directly_submittable(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    batch = await make_batch(
        client,
        owner,
        date=(today() - timedelta(days=15)).isoformat(),
        count=2,
        create_animals=True,
    )
    detail = await get_batch(client, owner, batch["id"])
    ppr_task = next(task for task in detail["tasks"] if "PPR" in task["title"])
    moved, quarantined = detail["animals"]
    override = await client.post(
        f"/api/animals/{moved['id']}/move",
        json={
            "to_bucket": "FOUNDATION",
            "history_override": True,
            "reason": "Correct imported bucket history",
        },
        headers=owner,
    )
    assert override.status_code == 200, override.text
    preview = await client.post(
        "/api/health/events/preview",
        json={
            "scope": "batch",
            "purchase_batch_id": batch["id"],
            "task_id": ppr_task["id"],
        },
        headers=owner,
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["task_id"] == ppr_task["id"]
    assert preview.json()["target_animal_ids"] == [quarantined["id"]]
    written = await client.post(
        "/api/health/events",
        json={
            "scope": "batch",
            "purchase_batch_id": batch["id"],
            "expected_animal_ids": preview.json()["target_animal_ids"],
            "type": "VACCINE",
            "task_id": ppr_task["id"],
        },
        headers=owner,
    )
    assert written.status_code == 201, written.text


async def test_death_authority_notification_cannot_predate_birth(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    dob = today() - timedelta(days=100)
    animal = await client.post(
        "/api/animals",
        json={
            "tag_number": "AUTH-CHRONOLOGY",
            "sex": "F",
            "source": "BORN",
            "current_bucket": "FOUNDATION",
            "date_of_birth": dob.isoformat(),
            "historical_import_reason": "Existing herd import",
        },
        headers=owner,
    )
    assert animal.status_code == 201, animal.text
    dead = await client.post(
        f"/api/animals/{animal.json()['id']}/status",
        json={
            "new_status": "DEAD",
            "suspected_scheduled_disease": True,
            "suspected_disease": "Anthrax",
            "authority_notified_at": (dob - timedelta(days=1)).isoformat(),
        },
        headers=owner,
    )
    assert dead.status_code == 422, dead.text


async def test_stillborn_explicit_tags_share_the_farm_tag_namespace(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner, tag="STILL-DAM")
    buck = await make_buck(client, owner, tag="STILL-SIRE")
    breeding = await make_breeding(
        client,
        owner,
        doe["id"],
        buck["id"],
        breeding_date=(today() - timedelta(days=150)).isoformat(),
    )
    breeding = await confirm(client, owner, breeding["id"], kid_count=2)
    duplicate = await client.post(
        "/api/kidding",
        json={
            "breeding_record_id": breeding["id"],
            "date": today().isoformat(),
            "ease": "NORMAL",
            "kids": [
                {"tag": "STILL-DUP", "sex": "M", "status": "STILLBORN"},
                {"tag": "STILL-DUP", "sex": "F", "status": "STILLBORN"},
            ],
        },
        headers=owner,
    )
    assert duplicate.status_code == 400, duplicate.text

    valid = await client.post(
        "/api/kidding",
        json={
            "breeding_record_id": breeding["id"],
            "date": today().isoformat(),
            "ease": "NORMAL",
            "kids": [
                {"tag": "STILL-LOCK-1", "sex": "M", "status": "STILLBORN"},
                {"tag": "STILL-LOCK-2", "sex": "F", "status": "STILLBORN"},
            ],
        },
        headers=owner,
    )
    assert valid.status_code == 201, valid.text

    # The database boundary protects both insert directions, not just the API
    # pre-check: imports cannot create an Animal with a stillborn identifier or
    # a stillborn KidEntry carrying an existing animal identifier.
    async with get_sessionmaker()() as db:
        db.add(
            Animal(
                farm_id=int(owner["X-Farm-Id"]),
                tag_number="STILL-LOCK-1",
                sex="F",
                source="BORN",
                current_bucket="FOUNDATION",
                status="ACTIVE",
            )
        )
        with pytest.raises(IntegrityError):
            await db.commit()

    async with get_sessionmaker()() as db:
        db.add(
            KidEntry(
                farm_id=int(owner["X-Farm-Id"]),
                kidding_record_id=valid.json()["id"],
                tag="STILL-DAM",
                sex="F",
                status="STILLBORN",
            )
        )
        with pytest.raises(IntegrityError):
            await db.commit()


async def test_reproductive_trigger_requires_confirmed_same_doe(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner, tag="TRIGGER-DAM-1")
    wrong_doe = await make_doe(client, owner, tag="TRIGGER-DAM-2")
    buck = await make_buck(client, owner, tag="TRIGGER-SIRE")
    breeding = await make_breeding(
        client,
        owner,
        doe["id"],
        buck["id"],
        breeding_date=(today() - timedelta(days=150)).isoformat(),
    )
    async with get_sessionmaker()() as db:
        db.add(
            KiddingRecord(
                farm_id=int(owner["X-Farm-Id"]),
                doe_id=wrong_doe["id"],
                date=today(),
                breeding_record_id=breeding["id"],
                ease="NORMAL",
            )
        )
        with pytest.raises(IntegrityError):
            await db.commit()

    confirmed = await confirm(client, owner, breeding["id"], kid_count=1)
    kidding = await client.post(
        "/api/kidding",
        json={
            "breeding_record_id": confirmed["id"],
            "date": today().isoformat(),
            "ease": "NORMAL",
            "kids": [{"sex": "F", "status": "ALIVE"}],
        },
        headers=owner,
    )
    assert kidding.status_code == 201, kidding.text

    # Both parent and child mutation paths remain guarded after the initially
    # valid pair has been inserted.
    async with get_sessionmaker()() as db:
        with pytest.raises(IntegrityError):
            await db.execute(
                update(KiddingRecord)
                .where(KiddingRecord.id == kidding.json()["id"])
                .values(doe_id=wrong_doe["id"])
            )
            await db.commit()
    async with get_sessionmaker()() as db:
        with pytest.raises(IntegrityError):
            await db.execute(
                update(BreedingRecord)
                .where(BreedingRecord.id == breeding["id"])
                .values(outcome=BreedingOutcome.UNASSESSED.value)
            )
            await db.commit()


async def test_backdated_purchase_sets_effective_age_and_estimated_weights(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    batch = await make_batch(
        client,
        owner,
        date=(today() - timedelta(days=10)).isoformat(),
        count=2,
        avg_age_months=12,
        avg_weight_kg=24.5,
        create_animals=True,
    )
    detail = await get_batch(client, owner, batch["id"])
    for animal in detail["animals"]:
        profile = await client.get(f"/api/animals/{animal['id']}", headers=owner)
        assert profile.status_code == 200, profile.text
        assert profile.json()["animal"]["days_in_current_bucket"] == 10
        assert profile.json()["animal"]["latest_weight_kg"] == 24.5
        assert profile.json()["weights_total"] == 1
    plan = await client.get("/api/feeding/plan", headers=owner)
    quarantine = next(line for line in plan.json()["lines"] if line["bucket"] == "QUARANTINE")
    assert quarantine["recipe_code"] == "MAINTENANCE_75_25"


async def test_backdated_abortion_and_kidding_retain_effective_dates(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    loss_doe = await make_doe(client, owner, tag="EFFECTIVE-LOSS-DAM")
    loss_buck = await make_buck(client, owner, tag="EFFECTIVE-LOSS-SIRE")
    loss = await make_breeding(
        client,
        owner,
        loss_doe["id"],
        loss_buck["id"],
        breeding_date=(today() - timedelta(days=100)).isoformat(),
    )
    loss = await confirm(client, owner, loss["id"], kid_count=1)
    loss_task_ids = await _seed_rejection_metadata(
        int(owner["X-Farm-Id"]), breeding_record_id=loss["id"]
    )
    loss_date = today() - timedelta(days=12)
    aborted = await client.post(
        f"/api/breeding/{loss['id']}/abort",
        json={"loss_date": loss_date.isoformat(), "cause": "UNKNOWN"},
        headers=owner,
    )
    assert aborted.status_code == 200, aborted.text
    await _assert_rejection_cleared(loss_task_ids)
    loss_profile = await client.get(f"/api/animals/{loss_doe['id']}", headers=owner)
    assert loss_profile.json()["animal"]["days_in_current_bucket"] == 12

    kid_doe = await make_doe(client, owner, tag="EFFECTIVE-KID-DAM")
    kid_buck = await make_buck(client, owner, tag="EFFECTIVE-KID-SIRE")
    pregnancy = await make_breeding(
        client,
        owner,
        kid_doe["id"],
        kid_buck["id"],
        breeding_date=(today() - timedelta(days=160)).isoformat(),
    )
    pregnancy = await confirm(client, owner, pregnancy["id"], kid_count=1)
    pregnancy_task_ids = await _seed_rejection_metadata(
        int(owner["X-Farm-Id"]), breeding_record_id=pregnancy["id"]
    )
    kidding_date = today() - timedelta(days=10)
    kidding = await client.post(
        "/api/kidding",
        json={
            "breeding_record_id": pregnancy["id"],
            "date": kidding_date.isoformat(),
            "ease": "NORMAL",
            "kids": [{"sex": "F", "status": "ALIVE"}],
        },
        headers=owner,
    )
    assert kidding.status_code == 201, kidding.text
    await _assert_rejection_cleared(pregnancy_task_ids)
    kid_profile = await client.get(f"/api/animals/{kid_doe['id']}", headers=owner)
    assert kid_profile.json()["animal"]["days_in_current_bucket"] == 10


async def test_final_surviving_kid_death_replaces_weaning_plan(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner, tag="FINAL-KID-DAM")
    buck = await make_buck(client, owner, tag="FINAL-KID-SIRE")
    breeding = await make_breeding(
        client,
        owner,
        doe["id"],
        buck["id"],
        breeding_date=(today() - timedelta(days=170)).isoformat(),
    )
    breeding = await confirm(client, owner, breeding["id"], kid_count=1)
    kidding = await client.post(
        "/api/kidding",
        json={
            "breeding_record_id": breeding["id"],
            "date": (today() - timedelta(days=20)).isoformat(),
            "ease": "NORMAL",
            "kids": [{"sex": "M", "status": "ALIVE"}],
        },
        headers=owner,
    )
    assert kidding.status_code == 201, kidding.text
    died_on = today() - timedelta(days=15)
    dead = await client.post(
        f"/api/animals/{kidding.json()['kids'][0]['animal_id']}/status",
        json={"new_status": "DEAD", "date": died_on.isoformat()},
        headers=owner,
    )
    assert dead.status_code == 200, dead.text
    async with get_sessionmaker()() as db:
        pending = list(
            (
                await db.execute(
                    select(Task).where(
                        Task.farm_id == int(owner["X-Farm-Id"]),
                        Task.animal_id == doe["id"],
                        Task.status == TaskStatus.PENDING.value,
                    )
                )
            ).scalars()
        )
    assert not any(task.category == TaskCategory.WEANING.value for task in pending)
    assert any(
        task.category == TaskCategory.BUCKET_MOVE.value
        and task.due_date == died_on + timedelta(days=14)
        for task in pending
    )


async def test_concurrent_final_two_kid_deaths_cannot_leave_weaning_plan(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner, tag="CONCURRENT-KID-DAM")
    buck = await make_buck(client, owner, tag="CONCURRENT-KID-SIRE")
    breeding = await make_breeding(
        client,
        owner,
        doe["id"],
        buck["id"],
        breeding_date=(today() - timedelta(days=170)).isoformat(),
    )
    breeding = await confirm(client, owner, breeding["id"], kid_count=2)
    kidding = await client.post(
        "/api/kidding",
        json={
            "breeding_record_id": breeding["id"],
            "date": (today() - timedelta(days=20)).isoformat(),
            "ease": "NORMAL",
            "kids": [
                {"sex": "M", "status": "ALIVE"},
                {"sex": "F", "status": "ALIVE"},
            ],
        },
        headers=owner,
    )
    assert kidding.status_code == 201, kidding.text
    kid_ids = [kid["animal_id"] for kid in kidding.json()["kids"]]
    died_on = today() - timedelta(days=1)
    async with (
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
        ) as first_client,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
        ) as second_client,
    ):
        responses = await asyncio.gather(
            first_client.post(
                f"/api/animals/{kid_ids[0]}/status",
                json={"new_status": "DEAD", "date": died_on.isoformat()},
                headers=owner,
            ),
            second_client.post(
                f"/api/animals/{kid_ids[1]}/status",
                json={"new_status": "DEAD", "date": died_on.isoformat()},
                headers=owner,
            ),
        )
    assert [response.status_code for response in responses] == [200, 200]

    async with get_sessionmaker()() as db:
        pending = list(
            (
                await db.execute(
                    select(Task).where(
                        Task.farm_id == int(owner["X-Farm-Id"]),
                        Task.animal_id == doe["id"],
                        Task.status == TaskStatus.PENDING.value,
                    )
                )
            ).scalars()
        )
        kid_statuses = list(
            (
                await db.execute(
                    select(KidEntry.status).where(
                        KidEntry.kidding_record_id == kidding.json()["id"]
                    )
                )
            ).scalars()
        )
    assert kid_statuses == ["DIED", "DIED"]
    assert not any(task.category == TaskCategory.WEANING.value for task in pending)
    postpartum = [task for task in pending if task.category == TaskCategory.BUCKET_MOVE.value]
    assert len(postpartum) == 1
    assert postpartum[0].due_date == died_on + timedelta(days=14)


async def test_kid_death_fails_fast_while_dam_retirement_owns_the_dam(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner, tag="LOCK-ORDER-DAM")
    buck = await make_buck(client, owner, tag="LOCK-ORDER-SIRE")
    breeding = await make_breeding(
        client,
        owner,
        doe["id"],
        buck["id"],
        breeding_date=(today() - timedelta(days=170)).isoformat(),
    )
    breeding = await confirm(client, owner, breeding["id"], kid_count=1)
    kidding = await client.post(
        "/api/kidding",
        json={
            "breeding_record_id": breeding["id"],
            "date": (today() - timedelta(days=20)).isoformat(),
            "ease": "NORMAL",
            "kids": [{"sex": "M", "status": "ALIVE"}],
        },
        headers=owner,
    )
    assert kidding.status_code == 201, kidding.text
    kid_id = kidding.json()["kids"][0]["animal_id"]

    dam_locked = asyncio.Event()
    release_dam = asyncio.Event()
    real_replan = animals_api.replan_dam_after_last_kid_death

    async def parking_replan(*args: object, **kwargs: object) -> bool:
        animal = args[2]
        if isinstance(animal, Animal) and animal.id == doe["id"]:
            dam_locked.set()
            await release_dam.wait()
        return await real_replan(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(animals_api, "replan_dam_after_last_kid_death", parking_replan)
    async with (
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
        ) as dam_client,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
        ) as kid_client,
    ):
        dam_request = asyncio.create_task(
            dam_client.post(
                f"/api/animals/{doe['id']}/status",
                json={"new_status": "DEAD"},
                headers=owner,
            )
        )
        await asyncio.wait_for(dam_locked.wait(), timeout=5)
        conflict = await asyncio.wait_for(
            kid_client.post(
                f"/api/animals/{kid_id}/status",
                json={"new_status": "DEAD"},
                headers=owner,
            ),
            timeout=5,
        )
        assert conflict.status_code == 409, conflict.text
        assert "retry" in conflict.json()["detail"].lower()
        release_dam.set()
        dam_response = await asyncio.wait_for(dam_request, timeout=5)
        assert dam_response.status_code == 200, dam_response.text

        retry = await kid_client.post(
            f"/api/animals/{kid_id}/status",
            json={"new_status": "DEAD"},
            headers=owner,
        )
        assert retry.status_code == 200, retry.text


async def test_task_driven_moves_use_the_farm_business_date(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner, tag="TASK-DATE-DAM")
    buck = await make_buck(client, owner, tag="TASK-DATE-SIRE")
    breeding = await make_breeding(
        client,
        owner,
        doe["id"],
        buck["id"],
        breeding_date=(today() - timedelta(days=170)).isoformat(),
    )
    breeding = await confirm(client, owner, breeding["id"], kid_count=1)
    kidding = await client.post(
        "/api/kidding",
        json={
            "breeding_record_id": breeding["id"],
            "date": (today() - timedelta(days=20)).isoformat(),
            "ease": "NORMAL",
            "kids": [{"sex": "F", "status": "ALIVE"}],
        },
        headers=owner,
    )
    assert kidding.status_code == 201, kidding.text
    kid_id = kidding.json()["kids"][0]["animal_id"]

    farm_business_date = today() - timedelta(days=2)
    fallback_date = farm_business_date - timedelta(days=1)
    timezone_calls: list[str | None] = []

    def fake_today(timezone_name: str | None = None) -> date:
        timezone_calls.append(timezone_name)
        return farm_business_date if timezone_name is not None else fallback_date

    monkeypatch.setattr(task_service, "today", fake_today)
    async with get_sessionmaker()() as db:
        task = (
            await db.execute(
                select(Task)
                .where(
                    Task.farm_id == int(owner["X-Farm-Id"]),
                    Task.animal_id == doe["id"],
                    Task.category == TaskCategory.WEANING.value,
                    Task.status == TaskStatus.PENDING.value,
                )
                .with_for_update()
            )
        ).scalar_one()
        animals = list(
            (
                await db.execute(
                    select(Animal)
                    .where(Animal.id.in_([doe["id"], kid_id]))
                    .order_by(Animal.id)
                    .with_for_update()
                )
            ).scalars()
        )
        await complete_task(db, task, locked_animals=animals)
        await db.commit()
    assert len(timezone_calls) == 1
    assert timezone_calls[0] is not None

    async with get_sessionmaker()() as db:
        effective_dates = list(
            (
                await db.execute(
                    select(BucketMove.effective_date).where(
                        BucketMove.animal_id.in_([doe["id"], kid_id]),
                        BucketMove.from_bucket.is_not(None),
                        BucketMove.reason.in_(["Weaned (day 60)", "Kids weaned"]),
                    )
                )
            ).scalars()
        )
    assert effective_dates == [farm_business_date, farm_business_date]


async def test_animal_source_correction_preserves_link_and_syncs_date(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    source = await make_finance_animal(
        client,
        owner,
        tag="SOURCE-A",
        purchase_price=100.0,
        purchase_date=(today() - timedelta(days=10)).isoformat(),
    )
    other = await make_finance_animal(client, owner, tag="SOURCE-B")
    subsequent_date = today() - timedelta(days=5)
    async with get_sessionmaker()() as db:
        db.add(
            BucketMove(
                animal_id=source["id"],
                from_bucket="FOUNDATION",
                to_bucket="RESTING",
                effective_date=subsequent_date,
                moved_at=utcnow() - timedelta(days=100),
                reason="Imported later movement",
            )
        )
        await db.commit()
    rows = (await client.get("/api/finance", headers=owner)).json()["transactions"]
    booked = next(row for row in rows if row["source_type"] == "ANIMAL_PURCHASE")
    corrected_date = today() - timedelta(days=9)
    corrected = await client.post(
        f"/api/finance/transactions/{booked['id']}/correct",
        json=correction_payload(
            date=corrected_date.isoformat(),
            type="EXPENSE",
            category="ANIMAL_PURCHASE",
            amount=90.0,
        ),
        headers=owner,
    )
    assert corrected.status_code == 201, corrected.text
    assert corrected.json()["related_animal_id"] == source["id"]
    profile = await client.get(f"/api/animals/{source['id']}", headers=owner)
    assert profile.json()["animal"]["purchase_date"] == corrected_date.isoformat()
    assert profile.json()["animal"]["purchase_price"] == 90.0
    async with get_sessionmaker()() as db:
        initial_move = (
            await db.execute(
                select(BucketMove)
                .where(
                    BucketMove.animal_id == source["id"],
                    BucketMove.from_bucket.is_(None),
                )
                .limit(1)
            )
        ).scalar_one()
        subsequent_move = (
            await db.execute(
                select(BucketMove).where(
                    BucketMove.animal_id == source["id"],
                    BucketMove.from_bucket.is_not(None),
                )
            )
        ).scalar_one()
    assert initial_move.effective_date == corrected_date
    assert subsequent_move.effective_date == subsequent_date

    relink = await client.post(
        f"/api/finance/transactions/{corrected.json()['id']}/correct",
        json=correction_payload(
            date=corrected_date.isoformat(),
            type="EXPENSE",
            category="ANIMAL_PURCHASE",
            amount=90.0,
            related_animal_id=other["id"],
        ),
        headers=owner,
    )
    assert relink.status_code == 422, relink.text

    after_move = await client.post(
        f"/api/finance/transactions/{corrected.json()['id']}/correct",
        json=correction_payload(
            date=(subsequent_date + timedelta(days=1)).isoformat(),
            type="EXPENSE",
            category="ANIMAL_PURCHASE",
            amount=90.0,
        ),
        headers=owner,
    )
    assert after_move.status_code == 422, after_move.text

    weight_date = today() - timedelta(days=7)
    weight = await client.post(
        f"/api/animals/{source['id']}/weight",
        json={"date": weight_date.isoformat(), "weight_kg": 25},
        headers=owner,
    )
    assert weight.status_code == 201, weight.text
    after_fact = await client.post(
        f"/api/finance/transactions/{corrected.json()['id']}/correct",
        json=correction_payload(
            date=(weight_date + timedelta(days=1)).isoformat(),
            type="EXPENSE",
            category="ANIMAL_PURCHASE",
            amount=90.0,
        ),
        headers=owner,
    )
    assert after_fact.status_code == 422, after_fact.text


async def test_sale_correction_respects_medicine_withdrawal(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_finance_animal(
        client,
        owner,
        tag="SALE-WITHDRAWAL",
        purchase_date=(today() - timedelta(days=30)).isoformat(),
    )
    treated = await client.post(
        "/api/health/events",
        json={
            "scope": "animal",
            "animal_id": animal["id"],
            "type": "TREATMENT",
            "date": (today() - timedelta(days=10)).isoformat(),
            "withdrawal_until": (today() - timedelta(days=5)).isoformat(),
        },
        headers=owner,
    )
    assert treated.status_code == 201, treated.text
    sold = await client.post(
        f"/api/animals/{animal['id']}/status",
        json={"new_status": "SOLD", "date": today().isoformat(), "sale_price": 500},
        headers=owner,
    )
    assert sold.status_code == 200, sold.text
    sale = next(
        row
        for row in (await client.get("/api/finance", headers=owner)).json()["transactions"]
        if row["source_type"] == "ANIMAL_SALE"
    )
    blocked = await client.post(
        f"/api/finance/transactions/{sale['id']}/correct",
        json=correction_payload(
            date=(today() - timedelta(days=7)).isoformat(),
            type="INCOME",
            category="ANIMAL_SALE",
            amount=450,
        ),
        headers=owner,
    )
    assert blocked.status_code == 409, blocked.text
    assert "withdrawal" in blocked.json()["detail"].lower()


async def test_status_and_sale_correction_cannot_predate_bucket_move(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_finance_animal(
        client,
        owner,
        tag="SALE-AFTER-MOVE",
        purchase_date=(today() - timedelta(days=30)).isoformat(),
    )
    moved_on = today() - timedelta(days=2)
    async with get_sessionmaker()() as db:
        db.add(
            BucketMove(
                animal_id=animal["id"],
                from_bucket="FOUNDATION",
                to_bucket="RESTING",
                effective_date=moved_on,
                reason="Imported lifecycle move",
            )
        )
        await db.commit()

    before_move = moved_on - timedelta(days=1)
    blocked_status = await client.post(
        f"/api/animals/{animal['id']}/status",
        json={"new_status": "SOLD", "date": before_move.isoformat(), "sale_price": 500},
        headers=owner,
    )
    assert blocked_status.status_code == 422, blocked_status.text

    sold = await client.post(
        f"/api/animals/{animal['id']}/status",
        json={"new_status": "SOLD", "date": today().isoformat(), "sale_price": 500},
        headers=owner,
    )
    assert sold.status_code == 200, sold.text
    sale = next(
        row
        for row in (await client.get("/api/finance", headers=owner)).json()["transactions"]
        if row["source_type"] == "ANIMAL_SALE"
    )
    blocked_correction = await client.post(
        f"/api/finance/transactions/{sale['id']}/correct",
        json=correction_payload(
            date=before_move.isoformat(),
            type="INCOME",
            category="ANIMAL_SALE",
            amount=450,
        ),
        headers=owner,
    )
    assert blocked_correction.status_code == 422, blocked_correction.text


async def test_zero_animal_purchase_batch_money_and_date_can_be_corrected(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    batch = await client.post(
        "/api/purchases/new",
        json={
            "date": today().isoformat(),
            "count": 4,
            "total_price": 1000.0,
            "create_animals": False,
        },
        headers=owner,
    )
    assert batch.status_code == 201, batch.text
    booked = next(
        row
        for row in (await client.get("/api/finance", headers=owner)).json()["transactions"]
        if row["source_type"] == "PURCHASE_BATCH"
    )
    ancient = await client.post(
        f"/api/finance/transactions/{booked['id']}/correct",
        json=correction_payload(
            date="1999-12-31",
            type="EXPENSE",
            category="ANIMAL_PURCHASE",
            amount=900.0,
        ),
        headers=owner,
    )
    assert ancient.status_code == 422, ancient.text
    assert "year 2000 or later" in ancient.json()["detail"]
    unchanged = await client.get(f"/api/purchases/{batch.json()['id']}", headers=owner)
    assert unchanged.json()["batch"]["total_price"] == 1000.0
    assert unchanged.json()["batch"]["date"] == today().isoformat()

    changed_date = today() - timedelta(days=1)
    corrected = await client.post(
        f"/api/finance/transactions/{booked['id']}/correct",
        json=correction_payload(
            date=changed_date.isoformat(),
            type="EXPENSE",
            category="ANIMAL_PURCHASE",
            amount=900.0,
        ),
        headers=owner,
    )
    assert corrected.status_code == 201, corrected.text
    detail = await client.get(f"/api/purchases/{batch.json()['id']}", headers=owner)
    assert detail.json()["batch"]["total_price"] == 900.0
    assert detail.json()["batch"]["date"] == changed_date.isoformat()


async def test_reject_skip_clears_rejection_and_orders_by_skip_time(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    first = await make_duty(client, owner, "Rejected then skipped", category="CLEANING")
    assert (await complete_duty(client, owner, first["id"])).status_code == 200
    rejected = await client.post(
        f"/api/tasks/{first['id']}/reject",
        json={"note": "Still dirty"},
        headers=owner,
    )
    assert rejected.status_code == 200, rejected.text
    second = await make_duty(client, owner, "Completed between")
    assert (await complete_duty(client, owner, second["id"])).status_code == 200
    skipped = await client.post(
        f"/api/tasks/{first['id']}/skip",
        json={"reason": "No water"},
        headers=owner,
    )
    assert skipped.status_code == 200, skipped.text
    assert skipped.json()["needs_verification"] is False
    assert skipped.json()["verification_note"] is None
    assert skipped.json()["rejected_by_id"] is None
    assert skipped.json()["rejected_at"] is None
    tabs = await get_tabs(client, owner)
    assert [task["id"] for task in tabs["completed"][:2]] == [first["id"], second["id"]]
    deep_link = await client.get(f"/api/tasks/{first['id']}", headers=owner)
    assert deep_link.status_code == 200, deep_link.text
    assert deep_link.json()["id"] == first["id"]

    other_owner = await owner_with_farm(
        client,
        email="task-deep-link-other@farm.in",
        farm_name="Other task farm",
    )
    inaccessible = await client.get(f"/api/tasks/{first['id']}", headers=other_owner)
    assert inaccessible.status_code == 404, inaccessible.text


async def test_animal_retirement_bulk_skip_clears_rejection_state(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_health_animal(client, owner, tag="REJECTED-RETIREMENT")
    duty = await make_duty(
        client,
        owner,
        "Rejected animal cleaning",
        category="CLEANING",
        animal_id=animal["id"],
    )
    assert (await complete_duty(client, owner, duty["id"])).status_code == 200
    rejected = await client.post(
        f"/api/tasks/{duty['id']}/reject",
        json={"note": "Repeat cleaning"},
        headers=owner,
    )
    assert rejected.status_code == 200, rejected.text
    retired = await client.post(
        f"/api/animals/{animal['id']}/status",
        json={"new_status": "DEAD"},
        headers=owner,
    )
    assert retired.status_code == 200, retired.text
    task = await client.get(f"/api/tasks/{duty['id']}", headers=owner)
    assert task.status_code == 200, task.text
    assert task.json()["status"] == "SKIPPED"
    assert task.json()["verification_note"] is None
    assert task.json()["rejected_by_id"] is None
    assert task.json()["rejected_at"] is None


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        (
            "POST",
            "/api/purchases/new",
            {"date": today().isoformat(), "count": 1, "supplier": "Bad\x00Supplier"},
        ),
        (
            "POST",
            "/api/tasks",
            {"title": "Bad\x00Task", "due_date": today().isoformat()},
        ),
    ],
)
async def test_postgres_text_controls_are_validation_errors(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    payload: dict[str, object],
) -> None:
    owner = await owner_with_farm(client)
    response = await client.request(method, path, json=payload, headers=owner)
    assert response.status_code == 422, response.text


async def test_task_reject_and_skip_controls_are_validation_errors(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    cleaning = await make_duty(client, owner, "Validate rejection", category="CLEANING")
    assert (await complete_duty(client, owner, cleaning["id"])).status_code == 200
    rejected = await client.post(
        f"/api/tasks/{cleaning['id']}/reject",
        json={"note": "Bad\x00rejection"},
        headers=owner,
    )
    assert rejected.status_code == 422, rejected.text

    skippable = await make_duty(client, owner, "Validate skip")
    skipped = await client.post(
        f"/api/tasks/{skippable['id']}/skip",
        json={"reason": "Bad\x00skip"},
        headers=owner,
    )
    assert skipped.status_code == 422, skipped.text


async def test_purchase_search_nul_is_validation_error(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    response = await client.get("/api/purchases", params={"q": "Bad\x00"}, headers=owner)
    assert response.status_code == 422, response.text
