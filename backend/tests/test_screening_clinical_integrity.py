"""Clinical evidence, provider rotation, and screening budget regressions."""

from __future__ import annotations

import asyncio
import json
from typing import Any, cast
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_sessionmaker
from app.models import (
    Farm,
    HealthRoundCoverage,
    ScreeningCallReservation,
    ScreeningCrop,
    ScreeningDailyBudget,
    ScreeningFinding,
    ScreeningFindingReview,
    ScreeningImage,
    ScreeningRun,
    Task,
)
from app.services.screening.budget import ScreeningBudgetExhausted, reserve_provider_attempt
from app.services.screening.detect import DETECT_SYSTEM_PROMPT
from app.services.screening.gate import GATE_SYSTEM_PROMPT
from app.services.screening.pipeline import run_screening_cycle
from app.services.screening.providers import (
    OpenAICompatibleProvider,
    ProviderAnswer,
    VisionProvider,
)
from app.services.screening.rotation import ProviderRotation
from app.services.screening.s3 import ScreeningStorage
from app.utils import today

from .conftest import owner_with_farm
from .test_health_extended import make_animal
from .test_health_safety import _seed_herd_round_task
from .test_screening import (
    CountingProvider,
    FakeStorage,
    _cycle_settings,
    _jpeg_bytes,
    _openai_payload,
    _provider_settings,
    _register_fake_objects,
)


@pytest.mark.parametrize(
    "prompt",
    [GATE_SYSTEM_PROMPT, DETECT_SYSTEM_PROMPT, "specialist schema"],
    ids=["gate", "detection", "specialist"],
)
async def test_actual_openai_request_carries_the_stage_contract(prompt: str) -> None:
    requests: list[dict[str, Any]] = []

    def transport(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=_openai_payload("{}"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        await OpenAICompatibleProvider(_provider_settings(), client=client).complete(
            _jpeg_bytes(80, 80), prompt
        )
    assert requests[0]["messages"][0] == {"role": "system", "content": prompt}
    assert requests[0]["messages"][1]["role"] == "user"


@pytest.mark.parametrize("crop_detection", [False, True])
async def test_unusable_gate_never_reports_healthy(
    client: httpx.AsyncClient, crop_detection: bool
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    storage = FakeStorage()
    storage.objects[f"raw/{farm_id}/{today().isoformat()}/bad-quality.jpg"] = _jpeg_bytes(500, 300)

    class Unusable(CountingProvider):
        async def complete(self, image_jpeg: bytes, system_prompt: str) -> ProviderAnswer:
            if "Find every goat" in system_prompt:
                return await super().complete(image_jpeg, system_prompt)
            self.calls += 1
            return ProviderAnswer(
                text=json.dumps(
                    {
                        "flagged": False,
                        "quality_problem": True,
                        "confidence": 0.95,
                        "observations": [],
                    }
                ),
                provider=self.name,
                model=self.model,
                latency_ms=1,
            )

    async with get_sessionmaker()() as db:
        await _register_fake_objects(db, farm_id, storage)
        result = await run_screening_cycle(
            db,
            _cycle_settings(crop_detection=crop_detection),
            cast(ScreeningStorage, storage),
            ProviderRotation([Unusable(name="quality")]),
        )
        image = (await db.execute(select(ScreeningImage))).scalar_one()
        gates = (
            (await db.execute(select(ScreeningRun).where(ScreeningRun.stage == "GATE")))
            .scalars()
            .all()
        )
        assert image.status == "UNASSESSABLE"
        assert image.error is not None and "clearer photo" in image.error
        assert result.healthy == 0 and result.unassessable == 1
        assert {run.verdict for run in gates} == {"unassessable"}
    response = await client.get("/api/screening/images", headers=owner)
    assert response.json()["images"][0]["status"] == "UNASSESSABLE"


async def test_multi_crop_cascade_never_exceeds_actual_call_cap(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    storage = FakeStorage()
    storage.objects[f"raw/{farm_id}/{today().isoformat()}/three-goats.jpg"] = _jpeg_bytes(
        2000, 1000
    )
    boxes = [[0, 0, 1000, 250], [0, 375, 1000, 250], [0, 750, 1000, 250]]
    providers: list[VisionProvider] = [
        CountingProvider(name="cap-a", detect_boxes=boxes),
        CountingProvider(name="cap-b", detect_boxes=boxes),
    ]
    settings = _cycle_settings(crop_detection=True)
    settings.screening_daily_call_budget_per_farm = 8
    async with get_sessionmaker()() as db:
        await _register_fake_objects(db, farm_id, storage)
        result = await run_screening_cycle(
            db, settings, cast(ScreeningStorage, storage), ProviderRotation(providers)
        )
        image = (await db.execute(select(ScreeningImage))).scalar_one()
        findings = (await db.execute(select(ScreeningFinding))).scalars().all()
        receipts = (
            await db.execute(select(func.count()).select_from(ScreeningCallReservation))
        ).scalar_one()
    assert sum(cast(CountingProvider, provider).calls for provider in providers) == receipts == 8
    # The cap can interrupt a later crop after valid findings have already
    # been written. Keep that evidence, but do not publish a partial parent
    # verdict as FLAGGED: the unfinished image must remain retryable.
    assert image.status == "ERROR" and findings
    assert image.error is not None and "Daily screening call budget reached" in image.error
    assert result.budget_deferred > 0


async def test_transport_retry_requires_another_durable_charge(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    storage = FakeStorage()
    storage.objects[f"raw/{farm_id}/{today().isoformat()}/retry.jpg"] = _jpeg_bytes(200, 100)
    calls: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(503, json={"error": "invented test outage"})

    settings = _cycle_settings()
    settings.screening_daily_call_budget_per_farm = 1
    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http,
        get_sessionmaker()() as db,
    ):
        await _register_fake_objects(db, farm_id, storage)
        result = await run_screening_cycle(
            db,
            settings,
            cast(ScreeningStorage, storage),
            ProviderRotation([OpenAICompatibleProvider(_provider_settings(), client=http)]),
        )
        image = (await db.execute(select(ScreeningImage))).scalar_one()
        budget = (await db.execute(select(ScreeningDailyBudget))).scalar_one()
    assert len(calls) == budget.reserved_calls == 1
    assert result.budget_deferred == 1 and image.screening_attempts == 0


async def test_concurrent_attempt_admission_and_rollback_cannot_reset_cap(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    attempt = str(uuid4())

    async def charge(key: str) -> str:
        async with get_sessionmaker()() as db:
            receipt = await reserve_provider_attempt(
                db,
                farm_id=farm_id,
                timezone_name="America/Phoenix",
                provider="fake",
                cap=5,
                attempt_id=key,
            )
            await db.rollback()
            return receipt

    outcomes = await asyncio.gather(
        *(charge(str(uuid4())) for _ in range(12)), return_exceptions=True
    )
    assert sum(isinstance(result, str) for result in outcomes) == 5
    assert sum(isinstance(result, ScreeningBudgetExhausted) for result in outcomes) == 7
    async with get_sessionmaker()() as db:
        receipt = (await db.execute(select(ScreeningCallReservation).limit(1))).scalar_one()
        attempt = receipt.attempt_id
    assert await charge(attempt) == attempt  # same actual attempt cannot double charge
    async with get_sessionmaker()() as db:
        budget = (await db.execute(select(ScreeningDailyBudget))).scalar_one()
        assert budget.reserved_calls == 5


async def _finding(client: httpx.AsyncClient) -> tuple[dict[str, str], int]:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    storage = FakeStorage()
    storage.objects[f"raw/{farm_id}/{today().isoformat()}/review.jpg"] = _jpeg_bytes(600, 300)
    async with get_sessionmaker()() as db:
        await _register_fake_objects(db, farm_id, storage)
        await run_screening_cycle(
            db,
            _cycle_settings(),
            cast(ScreeningStorage, storage),
            ProviderRotation([CountingProvider(name="review")]),
        )
        finding = (await db.execute(select(ScreeningFinding))).scalar_one()
        return owner, finding.id


async def test_same_status_and_aba_reviews_preserve_every_accepted_author_decision(
    client: httpx.AsyncClient,
) -> None:
    owner, finding_id = await _finding(client)
    url = f"/api/screening/findings/{finding_id}/review"
    for revision, old, new in [
        (0, "PENDING_REVIEW", "CONFIRMED"),
        (1, "CONFIRMED", "REJECTED"),
        (2, "REJECTED", "CONFIRMED"),
    ]:
        response = await client.post(
            url,
            headers=owner,
            json={
                "status": new,
                "expected_status": old,
                "expected_revision": revision,
                "review_note": f"decision {revision + 1}",
            },
        )
        assert response.status_code == 200, response.text
    stale = await client.post(
        url,
        headers=owner,
        json={
            "status": "CONFIRMED",
            "expected_status": "CONFIRMED",
            "expected_revision": 1,
            "review_note": "stale ABA edit",
        },
    )
    assert stale.status_code == 409
    a, b = await asyncio.gather(
        *(
            client.post(
                url,
                headers=owner,
                json={
                    "status": "CONFIRMED",
                    "expected_status": "CONFIRMED",
                    "expected_revision": 3,
                    "review_note": note,
                },
            )
            for note in ["first concurrent edit", "second concurrent edit"]
        )
    )
    assert sorted([a.status_code, b.status_code]) == [200, 409]
    history = await client.get(
        f"/api/screening/findings/{finding_id}/reviews?limit=2", headers=owner
    )
    assert history.json()["total"] == 4
    assert [review["revision"] for review in history.json()["reviews"]] == [4, 3]
    assert history.json()["legacy_review"] is False
    nul = await client.post(
        url,
        headers=owner,
        json={
            "status": "REJECTED",
            "expected_status": "CONFIRMED",
            "expected_revision": 4,
            "review_note": "bad\u0000note",
        },
    )
    assert nul.status_code == 422
    async with get_sessionmaker()() as db:
        reviews = (
            await db.execute(select(func.count()).select_from(ScreeningFindingReview))
        ).scalar_one()
        assert reviews == 4
        with pytest.raises(DBAPIError):
            await db.execute(
                text(
                    "UPDATE screening_finding_reviews SET review_note='rewritten' "
                    "WHERE finding_id=:id"
                ),
                {"id": finding_id},
            )
        await db.rollback()


async def _round_event(
    client: httpx.AsyncClient,
    owner: dict[str, str],
    task_id: int,
    animal_ids: list[int],
    bucket: str,
    component: str,
) -> httpx.Response:
    return await client.post(
        "/api/health/events",
        headers=owner,
        json={
            "task_id": task_id,
            "scope": "bucket",
            "bucket": bucket,
            "expected_animal_ids": animal_ids,
            "type": "VACCINE",
            "disease_target": component,
        },
    )


async def test_round_requires_both_components_for_declared_cohort_and_explicit_changes(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    first = await make_animal(client, owner, tag="ROUND-A")
    second = await make_animal(client, owner, tag="ROUND-B", sex="M", bucket="MALE_KIDS")
    task_id = await _seed_herd_round_task(
        owner, "ET + HS pre-monsoon round (2026) — all animals", initialize=False
    )
    read = await client.get(f"/api/health/rounds/{task_id}", headers=owner)
    assert read.json()["initialized"] is False
    unstarted = await _round_event(client, owner, task_id, [first["id"]], "FOUNDATION", "HS")
    assert unstarted.status_code == 409
    assert "Start the herd round" in unstarted.json()["detail"]
    started = await client.post(f"/api/health/rounds/{task_id}/start", headers=owner)
    assert started.json()["total_targets"] == 2 and started.json()["remaining_units"] == 4
    entrant = await make_animal(client, owner, tag="ROUND-LATE")
    foreign_member = await _round_event(
        client, owner, task_id, [first["id"], entrant["id"]], "FOUNDATION", "HS"
    )
    assert foreign_member.status_code == 409
    add = await client.post(
        f"/api/health/rounds/{task_id}/targets",
        headers=owner,
        json={"animal_ids": [entrant["id"]], "reason": "New arrival reviewed by vet"},
    )
    assert add.json()["total_targets"] == 3
    hs = await _round_event(
        client, owner, task_id, [first["id"], entrant["id"]], "FOUNDATION", "HS"
    )
    assert hs.status_code == 201, hs.text
    hs_preview = await client.post(
        "/api/health/events/preview",
        headers=owner,
        json={
            "scope": "bucket",
            "bucket": "FOUNDATION",
            "task_id": task_id,
            "round_component": "Haemorrhagic Septicaemia (HS)",
        },
    )
    assert hs_preview.status_code == 400  # every declared pen member has this component
    et_preview = await client.post(
        "/api/health/events/preview",
        headers=owner,
        json={
            "scope": "bucket",
            "bucket": "FOUNDATION",
            "task_id": task_id,
            "round_component": "Enterotoxaemia (ET)",
        },
    )
    assert et_preview.status_code == 200, et_preview.text
    assert et_preview.json()["target_animal_ids"] == [first["id"], entrant["id"]]
    progress = (await client.get(f"/api/health/rounds/{task_id}", headers=owner)).json()
    assert progress["task_status"] == "PENDING" and progress["remaining_units"] == 4
    duplicate = await _round_event(client, owner, task_id, [first["id"]], "FOUNDATION", "HS")
    assert duplicate.status_code == 409
    et = await _round_event(
        client, owner, task_id, [first["id"], entrant["id"]], "FOUNDATION", "ET"
    )
    assert et.status_code == 201, et.text
    progress = (await client.get(f"/api/health/rounds/{task_id}", headers=owner)).json()
    assert progress["task_status"] == "PENDING" and progress["covered_targets"] == 2
    exclusion = await client.post(
        f"/api/health/rounds/{task_id}/exclusions",
        headers=owner,
        json={"animal_ids": [second["id"]], "reason": "Vet deferred both doses for this kid"},
    )
    assert exclusion.status_code == 200, exclusion.text
    assert exclusion.json()["task_status"] == "DONE"
    assert exclusion.json()["excluded_targets"] == 1 and exclusion.json()["remaining_units"] == 0
    async with get_sessionmaker()() as db:
        with pytest.raises(DBAPIError):
            await db.execute(
                text("UPDATE health_round_coverage SET component='PPR' WHERE task_id=:id"),
                {"id": task_id},
            )
        await db.rollback()
        assert (
            await db.execute(select(func.count()).select_from(HealthRoundCoverage))
        ).scalar_one() == 4


async def test_sql_rejects_partial_reviewer_and_false_crop_ancestry(
    client: httpx.AsyncClient,
) -> None:
    owner, finding_id = await _finding(client)
    async with get_sessionmaker()() as db:
        with pytest.raises(DBAPIError):
            await db.execute(
                text(
                    "UPDATE screening_findings SET status='CONFIRMED', reviewed_at=now(), "
                    "reviewed_by_id=NULL WHERE id=:id"
                ),
                {"id": finding_id},
            )
        await db.rollback()
        image = (await db.execute(select(ScreeningImage))).scalar_one()
        other = ScreeningImage(
            farm_id=int(owner["X-Farm-Id"]),
            s3_bucket="goat-photos",
            s3_key="unrelated-test-photo",
            status="PENDING",
        )
        db.add(other)
        await db.flush()
        crop = ScreeningCrop(
            farm_id=image.farm_id,
            image_id=other.id,
            crop_index=0,
            box_x=0,
            box_y=0,
            box_w=100,
            box_h=100,
            status="PENDING",
        )
        db.add(crop)
        await db.flush()
        crop_id = crop.id
        other_id = other.id
        await db.commit()
        with pytest.raises(DBAPIError):
            await db.execute(
                text(
                    "INSERT INTO screening_runs (farm_id,image_id,crop_id,stage,run_status,"
                    "provider,model,prompt_version) VALUES "
                    "(:farm,:image,:crop,'GATE','ERROR','fake','fake','test')"
                ),
                {"farm": image.farm_id, "image": image.id, "crop": crop_id},
            )
        await db.rollback()
        run_id = (
            await db.execute(
                select(ScreeningFinding.run_id).where(ScreeningFinding.id == finding_id)
            )
        ).scalar_one()
        with pytest.raises(DBAPIError):
            await db.execute(
                text("UPDATE screening_findings SET crop_id=:crop WHERE id=:id"),
                {"crop": crop_id, "id": finding_id},
            )
        await db.rollback()
        with pytest.raises(DBAPIError):
            await db.execute(
                text("UPDATE screening_runs SET image_id=:image WHERE id=:id"),
                {"image": other_id, "id": run_id},
            )
        await db.rollback()


async def test_round_start_waits_for_animals_without_holding_the_duty_lock(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lifecycle writer can finish Animal -> Task while a start is waiting."""
    from app.api import health
    from app.services.tasks import lock_manual_task_queue

    owner = await owner_with_farm(client)
    animal = await make_animal(client, owner, tag="SNAPSHOT-LOCK")
    task_id = await _seed_herd_round_task(owner, "PPR round", initialize=False)
    admitted = asyncio.Event()
    real_lock = lock_manual_task_queue

    async def signal_admission(db: AsyncSession, farm: Farm) -> None:
        await real_lock(db, farm)
        admitted.set()

    monkeypatch.setattr(health, "lock_manual_task_queue", signal_admission)
    async with get_sessionmaker()() as lifecycle:
        await lifecycle.execute(
            text("SELECT id FROM animals WHERE id=:id FOR UPDATE"), {"id": animal["id"]}
        )
        start = asyncio.create_task(
            client.post(f"/api/health/rounds/{task_id}/start", headers=owner)
        )
        try:
            await asyncio.wait_for(admitted.wait(), timeout=5)
            await lifecycle.execute(text("SET LOCAL lock_timeout='500ms'"))
            # The start must not hold this Task while waiting for our Animal.
            await lifecycle.execute(
                text("SELECT id FROM tasks WHERE id=:id FOR UPDATE"), {"id": task_id}
            )
            await lifecycle.commit()
            response = await asyncio.wait_for(start, timeout=5)
            assert response.status_code == 200, response.text
        finally:
            if not start.done():
                start.cancel()
                await asyncio.gather(start, return_exceptions=True)


async def test_poisoned_first_farm_does_not_expire_later_materialization(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import cadence

    first = await owner_with_farm(client, email="cadence-poison-clinical@example.test")
    second = await owner_with_farm(client, email="cadence-healthy-clinical@example.test")
    await make_animal(client, second, tag="NEEDS-DUTIES")
    real = cadence.ensure_cadence_tasks

    async def fail_first(db: AsyncSession, farm: Farm) -> None:
        if farm.id == int(first["X-Farm-Id"]):
            raise RuntimeError("invented isolated farm failure")
        await real(db, farm)

    monkeypatch.setattr(cadence, "ensure_cadence_tasks", fail_first)
    async with get_sessionmaker()() as db:
        visited, cursor = await cadence.ensure_cadence_farm_batch(db, batch_size=100)
        tasks = (
            (await db.execute(select(Task).where(Task.farm_id == int(second["X-Farm-Id"]))))
            .scalars()
            .all()
        )
    assert visited >= 2 and cursor >= int(second["X-Farm-Id"])
    assert any(task.category == "WATER" for task in tasks)
