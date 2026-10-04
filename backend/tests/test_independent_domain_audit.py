"""Independent adversarial cases for the last commit's domain guarantees."""

from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select

from app.db import get_sessionmaker
from app.models import (
    ScreeningCallReservation,
    ScreeningDailyBudget,
    ScreeningImage,
    ScreeningRun,
    Task,
)
from app.services.screening.budget import ScreeningBudgetExhausted, reserve_provider_attempt
from app.simulation.finance import assess_irr, irr, npv
from app.utils import today, utcnow

from .conftest import owner_with_farm
from .test_health_extended import make_animal
from .test_health_safety import _seed_herd_round_task


async def test_all_new_round_routes_guard_impossible_int4_task_ids(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="BOUND-ROUND")
    for task_id in (0, 2_147_483_648, 9_223_372_036_854_775_808):
        base = f"/api/health/rounds/{task_id}"
        assert (await client.get(base, headers=owner)).status_code == 404
        assert (await client.post(f"{base}/start", headers=owner)).status_code == 404
        for operation in ("targets", "exclusions"):
            response = await client.post(
                f"{base}/{operation}",
                headers=owner,
                json={"animal_ids": [animal["id"]], "reason": "Reviewed target"},
            )
            assert response.status_code == 404, response.text


async def test_new_round_payload_fields_reject_invalid_ids_and_postgres_text(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="TEXT-ROUND")
    task_id = await _seed_herd_round_task(owner, "PPR round", initialize=False)
    started = await client.post(f"/api/health/rounds/{task_id}/start", headers=owner)
    assert started.status_code == 200, started.text
    for operation in ("targets", "exclusions"):
        for invalid_id in (True, "1", 0, 2_147_483_648):
            response = await client.post(
                f"/api/health/rounds/{task_id}/{operation}",
                headers=owner,
                json={"animal_ids": [invalid_id], "reason": "Reviewed target"},
            )
            assert response.status_code == 422, response.text
        for invalid_reason in ("\x00", " ", "x" * 4001):
            response = await client.post(
                f"/api/health/rounds/{task_id}/{operation}",
                headers=owner,
                json={"animal_ids": [animal["id"]], "reason": invalid_reason},
            )
            assert response.status_code == 422, response.text
    component = await client.post(
        "/api/health/events/preview",
        headers=owner,
        json={
            "scope": "bucket",
            "bucket": "FOUNDATION",
            "task_id": task_id,
            "round_component": "PPR\x00",
        },
    )
    assert component.status_code == 422, component.text
    unpaired = await client.post(
        f"/api/health/rounds/{task_id}/exclusions",
        headers={**owner, "Content-Type": "application/json"},
        content=b'{"animal_ids":[1],"reason":"\\ud800"}',
    )
    assert unpaired.status_code == 422, unpaired.text
    progress = await client.get(f"/api/health/rounds/{task_id}", headers=owner)
    assert progress.json()["excluded_targets"] == 0


@pytest.mark.parametrize("period", [1.0, 0.5, 1.0 / 12.0])
def test_tangent_zero_is_not_discarded_when_certifying_irr_uniqueness(period: float) -> None:
    # In z=(1+r)^(-period), (z-1)^2*(z-1.25) has distinct zeros at 1 and 1.25.
    # The zero at 1 is a valid IRR even though NPV does not change sign there.
    flows = [-1.25, 3.5, -3.25, 1.0]
    times = [index * period for index in range(4)]
    expected = (1.25 ** (-1.0 / period) - 1.0, 0.0)
    assert all(npv(rate, flows, times) == pytest.approx(0.0, abs=1e-8) for rate in expected)
    assessment = assess_irr(flows, times)
    assert assessment.status == "multiple_roots"
    assert assessment.roots == pytest.approx(expected, abs=1e-10)
    assert irr(flows, times) is None


def test_a_single_tangent_irr_is_a_zero_instead_of_no_root() -> None:
    assert assess_irr([1.0, -2.0, 1.0], [0.0, 1.0, 2.0]).status == "unique"
    assert irr([1.0, -2.0, 1.0], [0.0, 1.0, 2.0]) == pytest.approx(0.0, abs=1e-10)


@pytest.mark.parametrize("cap", [0, 10])
async def test_migration_cutover_hold_survives_a_disabled_call_cap(
    client: httpx.AsyncClient, cap: int
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    instant = utcnow()
    local_date = today("America/Phoenix")
    async with get_sessionmaker()() as db:
        db.add(
            ScreeningDailyBudget(
                farm_id=farm_id, local_date=local_date, reserved_calls=2_147_483_647
            )
        )
        await db.commit()
        with pytest.raises(ScreeningBudgetExhausted):
            await reserve_provider_attempt(
                db,
                farm_id=farm_id,
                timezone_name="America/Phoenix",
                provider="test-provider",
                cap=cap,
                now=instant,
            )
        # A denied attempt neither overflows the counter nor gets a receipt.
        assert (
            await db.execute(select(func.count()).select_from(ScreeningCallReservation))
        ).scalar_one() == 0
        persisted = await db.get(ScreeningDailyBudget, (farm_id, local_date))
        assert persisted is not None and persisted.reserved_calls == 2_147_483_647
        # The hold belongs to this local date, not to all later work.
        await reserve_provider_attempt(
            db,
            farm_id=farm_id,
            timezone_name="America/Phoenix",
            provider="test-provider",
            cap=cap,
            now=instant + timedelta(days=1),
        )


@pytest.mark.parametrize("latest_is_unusable", [True, False])
async def test_quality_verdict_order_agrees_between_list_filter_and_detail(
    client: httpx.AsyncClient, latest_is_unusable: bool
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    instant = utcnow()
    async with get_sessionmaker()() as db:
        image = ScreeningImage(
            farm_id=farm_id,
            s3_bucket="audit-photos",
            s3_key="audit-chronology.jpg",
            status="HEALTHY",
        )
        db.add(image)
        await db.flush()
        # IDs are insertion order; delayed/imported historical evidence can
        # arrive later while its actual observation timestamp remains older.
        for days_ago, quality in [(0, latest_is_unusable), (1, not latest_is_unusable)]:
            db.add(
                ScreeningRun(
                    farm_id=farm_id,
                    image_id=image.id,
                    stage="GATE",
                    run_status="OK",
                    verdict="healthy",
                    confidence=Decimal("0.9"),
                    provider="audit-provider",
                    model="audit-model",
                    prompt_version="audit",
                    latency_ms=1,
                    detail={"quality_problem": quality},
                    created_at=instant - timedelta(days=days_ago),
                )
            )
            await db.flush()
        image_id = image.id
        await db.commit()
    expected = "UNASSESSABLE" if latest_is_unusable else "HEALTHY"
    detail = await client.get(f"/api/screening/images/{image_id}", headers=owner)
    listing = await client.get("/api/screening/images", headers=owner)
    filtered = await client.get(f"/api/screening/images?status={expected}", headers=owner)
    assert detail.status_code == listing.status_code == filtered.status_code == 200
    assert detail.json()["status"] == expected
    assert listing.json()["images"][0]["status"] == expected
    assert filtered.json()["total"] == 1


async def test_exclusions_cannot_complete_a_future_health_duty(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="FUTURE-ROUND")
    async with get_sessionmaker()() as db:
        task = Task(
            farm_id=int(owner["X-Farm-Id"]),
            title="PPR round",
            category="VACCINE",
            due_date=today() + timedelta(days=30),
            auto_generated=True,
        )
        db.add(task)
        await db.commit()
        task_id = task.id
    started = await client.post(f"/api/health/rounds/{task_id}/start", headers=owner)
    assert started.status_code == 200, started.text
    response = await client.post(
        f"/api/health/rounds/{task_id}/exclusions",
        headers=owner,
        json={"animal_ids": [animal["id"]], "reason": "Vet deferred this animal"},
    )
    assert response.status_code == 409, response.text
    progress = await client.get(f"/api/health/rounds/{task_id}", headers=owner)
    assert progress.json()["task_status"] == "PENDING"
    assert progress.json()["excluded_targets"] == 0
