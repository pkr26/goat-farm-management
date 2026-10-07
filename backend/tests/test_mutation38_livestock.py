"""Independent livestock policy probes prepared for the 38-domain campaign.

The expectations are literal farm policy and observable workflow outcomes.
Copy this file under backend/tests to use the existing PostgreSQL fixtures.
"""

from datetime import date, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import Animal, KiddingRecord, KidEntry, WeightRecord
from app.services.chronology import (
    require_purchase_before_recorded_facts,
    require_status_after_recorded_facts,
)
from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_extended import (
    all_tasks,
    get_animal,
    iso,
    kid_on_ekd,
    make_animal,
    pregnant_doe,
    tasks_by_category,
)
from .test_feeding_extended import get_plan
from .test_feeding_extended import make_animal as make_feeding_animal
from .test_health_safety import _seed_herd_round_task


def test_recorded_dob_precedes_estimate_for_age_and_birth_weight_history() -> None:
    animal = Animal(
        date_of_birth=date(2025, 10, 6),
        estimated_dob=date(2025, 9, 5),
        birth_weight=2.5,
        weight_records=[],
    )
    assert animal.effective_dob == date(2025, 10, 6)
    assert animal.age_months_on(date(2026, 10, 5)) == 11
    assert animal.age_months_on(date(2026, 10, 6)) == 12
    assert animal.latest_weight_kg_on(date(2025, 10, 5)) is None
    assert animal.latest_weight_kg_on(date(2025, 10, 6)) == 2.5


def test_same_day_weight_uses_latest_entry_but_never_future_measurement() -> None:
    older = WeightRecord(id=4, date=date(2026, 10, 4), weight_kg=21.0)
    corrected = WeightRecord(id=5, date=date(2026, 10, 4), weight_kg=22.0)
    future = WeightRecord(id=6, date=date(2026, 10, 6), weight_kg=30.0)
    animal = Animal(
        date_of_birth=date(2025, 1, 1),
        birth_weight=2.0,
        weight_records=[corrected, future, older],
    )
    assert animal.latest_weight is future
    assert animal.latest_weight_kg_on(date(2026, 10, 3)) == 2.0
    assert animal.latest_weight_kg_on(date(2026, 10, 4)) == 22.0
    assert animal.latest_weight_kg_on(date(2026, 10, 5)) == 22.0
    assert animal.latest_weight_kg_on(date(2026, 10, 6)) == 30.0
    animal.weight_records = [corrected, older]
    assert animal.latest_weight is corrected


@pytest.mark.parametrize(
    ("sex", "dob", "weight", "expected"),
    [
        ("F", date(2025, 10, 5), 22.0, True),
        ("F", date(2025, 10, 5), 21.99, False),
        ("F", date(2025, 10, 6), 22.0, False),
        ("M", date(2025, 10, 5), 25.0, True),
        ("M", date(2025, 10, 5), 24.99, False),
        ("M", date(2025, 10, 6), 25.0, False),
    ],
)
def test_literal_first_service_maturity_floors(
    sex: str, dob: date, weight: float, expected: bool
) -> None:
    reference_date = date(2026, 10, 5)
    animal = Animal(
        sex=sex,
        status="ACTIVE",
        current_bucket="FOUNDATION",
        date_of_birth=dob,
        movement_restricted=False,
        suspected_scheduled_disease=False,
        weight_records=[WeightRecord(id=1, date=reference_date, weight_kg=weight)],
        breedings_as_doe=[],
    )
    if sex == "F":
        assert animal.is_breeding_ready_on(reference_date) is expected
        assert animal.is_breeding_eligible_on(reference_date) is expected
    else:
        assert animal.is_buck_ready_on(reference_date) is expected


async def test_literal_adult_scale_ceiling_accepts_150_rejects_150_01(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    animal = await make_animal(client, headers, "SCALE-BOUNDARY")
    accepted = await client.post(
        f"/api/animals/{animal['id']}/weight", json={"weight_kg": 150.0}, headers=headers
    )
    assert accepted.status_code == 201, accepted.text
    rejected = await client.post(
        f"/api/animals/{animal['id']}/weight", json={"weight_kg": 150.01}, headers=headers
    )
    assert rejected.status_code == 422, rejected.text
    async with get_sessionmaker()() as db:
        weights = (
            (await db.execute(select(WeightRecord).where(WeightRecord.animal_id == animal["id"])))
            .scalars()
            .all()
        )
    assert [record.weight_kg for record in weights] == [150.0]


@pytest.mark.parametrize("distractor_scope", ["same_farm", "other_farm"])
async def test_kidding_chronology_uses_only_this_does_farm_and_identity(
    client: httpx.AsyncClient, distractor_scope: str
) -> None:
    headers = await owner_with_farm(client, email="kidding-chronology@farm.in")
    other_headers = (
        await owner_with_farm(client, email="kidding-chronology-other@farm.in")
        if distractor_scope == "other_farm"
        else headers
    )
    target = await make_animal(client, headers, "KIDDING-TARGET")
    distractor = await make_animal(client, other_headers, "KIDDING-DISTRACTOR")
    own_date = today() - timedelta(days=100)
    # Nullable pregnancy links represent legacy kidding facts. No weight,
    # health or subsequent bucket moves mask the boundary being exercised.
    async with get_sessionmaker()() as db:
        db.add_all(
            [
                KiddingRecord(
                    farm_id=int(headers["X-Farm-Id"]), doe_id=target["id"], date=own_date
                ),
                KiddingRecord(
                    farm_id=int(other_headers["X-Farm-Id"]),
                    doe_id=distractor["id"],
                    date=today() - timedelta(days=200),
                ),
                KiddingRecord(
                    farm_id=int(other_headers["X-Farm-Id"]),
                    doe_id=distractor["id"],
                    date=today() - timedelta(days=10),
                ),
            ]
        )
        await db.commit()
        animal = await db.get(Animal, target["id"])
        assert animal is not None
        await require_purchase_before_recorded_facts(db, animal, own_date - timedelta(days=1))
        await require_status_after_recorded_facts(db, animal, own_date + timedelta(days=1))
        with pytest.raises(ValueError, match="earliest recorded"):
            await require_purchase_before_recorded_facts(db, animal, own_date + timedelta(days=1))
        with pytest.raises(ValueError, match="latest recorded"):
            await require_status_after_recorded_facts(db, animal, own_date - timedelta(days=1))


@pytest.mark.parametrize("sibling_exit", ["SOLD", "CULLED", "DEAD"])
async def test_postpartum_completion_uses_current_litter_survivors(
    client: httpx.AsyncClient, sibling_exit: str
) -> None:
    headers = await owner_with_farm(client)
    doe, _buck, breeding = await pregnant_doe(client, headers, gestation_days=220)
    litter = await kid_on_ekd(client, headers, breeding, kids=[{"sex": "F"}, {"sex": "M"}])
    sibling_id, last_id = [entry["animal_id"] for entry in litter["kids"]]
    assert sibling_id is not None and last_id is not None
    payload: dict[str, Any] = {"new_status": sibling_exit}
    if sibling_exit == "SOLD":
        payload["sale_price"] = 3000.0
    elif sibling_exit == "DEAD":
        payload |= {
            "date": iso(today() - timedelta(days=20)),
            "mortality_reported_at": iso(today() - timedelta(days=20)),
            "mortality_cause": "illness",
        }
    exited = await client.post(f"/api/animals/{sibling_id}/status", json=payload, headers=headers)
    assert exited.status_code == 200, exited.text
    async with get_sessionmaker()() as db:
        birth_fact = (
            await db.execute(select(KidEntry).where(KidEntry.animal_id == sibling_id))
        ).scalar_one()
        assert birth_fact.status == ("DIED" if sibling_exit == "DEAD" else "ALIVE")
    last_death = today() - timedelta(days=15)
    died = await client.post(
        f"/api/animals/{last_id}/status",
        json={
            "new_status": "DEAD",
            "date": iso(last_death),
            "mortality_reported_at": iso(last_death),
            "mortality_cause": "illness",
        },
        headers=headers,
    )
    assert died.status_code == 200, died.text
    postpartum = [
        task
        for task in tasks_by_category(await all_tasks(client, headers), "BUCKET_MOVE")
        if task["status"] == "PENDING" and task["animal_id"] == doe["id"]
    ]
    assert len(postpartum) == 1
    assert postpartum[0]["due_date"] == iso(last_death + timedelta(days=14))
    completed = await client.post(f"/api/tasks/{postpartum[0]['id']}/complete", headers=headers)
    assert completed.status_code == 200, completed.text
    assert (await get_animal(client, headers, doe["id"]))["current_bucket"] == "RESTING"


async def test_et_only_evidence_leaves_hs_component_and_round_pending(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    active = await make_animal(client, headers, "ROUND-ACTIVE")
    inactive = await make_animal(client, headers, "ROUND-INACTIVE")
    dead = await client.post(
        f"/api/animals/{inactive['id']}/status", json={"new_status": "DEAD"}, headers=headers
    )
    assert dead.status_code == 200, dead.text
    round_id = await _seed_herd_round_task(
        headers, "ET + HS pre-monsoon round (2026) — all animals"
    )
    for component, template, remaining, covered, status in [
        ("ET", "Enterotoxaemia (ET)", 1, 0, "PENDING"),
        ("HS", "Haemorrhagic Septicaemia (HS)", 0, 1, "DONE"),
    ]:
        recorded = await client.post(
            "/api/health/events",
            json={
                "scope": "bucket",
                "bucket": "FOUNDATION",
                "expected_animal_ids": [active["id"]],
                "type": "VACCINE",
                "disease_target": component,
                "schedule_template_name": template,
                "task_id": round_id,
            },
            headers=headers,
        )
        assert recorded.status_code == 201, recorded.text
        progress = await client.get(f"/api/health/rounds/{round_id}", headers=headers)
        assert progress.status_code == 200, progress.text
        body = progress.json()
        assert body["required_components"] == [
            "Enterotoxaemia (ET)",
            "Haemorrhagic Septicaemia (HS)",
        ]
        assert body["total_targets"] == 1
        assert [target["animal_id"] for target in body["targets"]] == [active["id"]]
        assert body["remaining_units"] == remaining
        assert body["covered_targets"] == covered
        assert body["task_status"] == status
        expected_components = (
            ["Enterotoxaemia (ET)"]
            if component == "ET"
            else ["Enterotoxaemia (ET)", "Haemorrhagic Septicaemia (HS)"]
        )
        assert body["targets"][0]["covered_components"] == expected_components


@pytest.mark.parametrize(
    ("bucket", "weight", "ration"),
    [
        ("FOUNDATION", 34.61, 1.10),
        ("FOUNDATION", 34.62, 1.15),
        ("RESTING", 37.49, 1.10),
        ("RESTING", 37.50, 1.15),
        ("PREGNANCY_LATE", 32.14, 1.10),
        ("PREGNANCY_LATE", 32.15, 1.15),
    ],
)
async def test_ration_class_percentages_round_at_exact_50_gram_half_step(
    client: httpx.AsyncClient, bucket: str, weight: float, ration: float
) -> None:
    headers = await owner_with_farm(client)
    await make_feeding_animal(
        client, headers, "RATION-BOUNDARY", bucket=bucket, dob_days=800, weight_kg=weight
    )
    lines = (await get_plan(client, headers))["lines"]
    assert len(lines) == 1
    line = lines[0]
    assert line["bucket"] == bucket
    assert line["heads"] == 1
    assert line["basis"] == "weight"
    assert line["mean_weight_kg"] == pytest.approx(weight)
    assert line["kg_per_head"] == pytest.approx(ration)
    assert line["daily_kg"] == pytest.approx(ration)
