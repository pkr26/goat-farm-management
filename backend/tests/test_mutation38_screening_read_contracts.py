"""Retained screening evidence stays scoped, reviewable and privacy safe.

Initial native evidence rows model the documented legacy/imported read surface;
no contemporary clinical facts are removed or edited. All FKs and checks remain
active. URL tests use actual local SigV4 signing, without object-store requests.
"""

import asyncio
from datetime import timedelta
from decimal import Decimal
from urllib.parse import unquote, urlsplit

import httpx
import pytest
from sqlalchemy import func, select, text

from app.api import screening as screening_api
from app.core.config import Settings
from app.db import get_sessionmaker
from app.models import ScreeningBatch, ScreeningCrop, ScreeningFinding, ScreeningImage, ScreeningRun
from app.models.screening import HEALTHY_CONTROL_LABEL
from app.utils import utcnow

from .conftest import owner_with_farm
from .test_screening import _cycle_settings

QUALITY_ERROR = "Photo cannot be assessed; upload a clearer photo (QUALITY_PROBLEM)"
MAX_IMAGE_KEY = 9_223_372_036_854_775_807


def _run(
    farm_id: int,
    image_id: int,
    *,
    crop_id: int | None = None,
    quality: bool = False,
    age_minutes: int = 0,
    failed: bool = False,
) -> ScreeningRun:
    return ScreeningRun(
        farm_id=farm_id,
        image_id=image_id,
        crop_id=crop_id,
        stage="GATE",
        run_status="ERROR" if failed else "OK",
        verdict=None if failed else "healthy",
        confidence=None if failed else Decimal("0.900"),
        provider="retained-camera-provider",
        model="retained-camera-model",
        prompt_version="retained",
        latency_ms=7,
        detail={"quality_problem": quality},
        error="Provider parse failed" if failed else None,
        created_at=utcnow() - timedelta(minutes=age_minutes),
    )


async def test_list_preserves_farm_filters_latest_evidence_and_review_kinds(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="photo-review-owner@farm.in")
    other = await owner_with_farm(client, email="photo-review-other@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        reviewed = ScreeningImage(
            farm_id=farm_id,
            bucket="RESTING",
            s3_bucket="retained-photos",
            s3_key="raw/reviewed.jpg",
            status="HEALTHY",
        )
        pending = ScreeningImage(
            farm_id=farm_id,
            bucket="BREEDING",
            s3_bucket="retained-photos",
            s3_key="raw/pending.jpg",
            status="PENDING",
        )
        unusable = ScreeningImage(
            farm_id=farm_id,
            bucket="RESTING",
            s3_bucket="retained-photos",
            s3_key="raw/unusable.jpg",
            status="UNASSESSABLE",
            error="Retained camera blur",
        )
        foreign = ScreeningImage(
            farm_id=int(other["X-Farm-Id"]),
            bucket="RESTING",
            s3_bucket="retained-photos",
            s3_key="raw/foreign.jpg",
            status="HEALTHY",
        )
        db.add_all([reviewed, pending, unusable, foreign])
        await db.flush()
        old_run = _run(farm_id, reviewed.id, age_minutes=2)
        latest = _run(farm_id, reviewed.id, age_minutes=1)
        db.add_all([old_run, latest])
        await db.flush()
        db.add_all(
            [
                ScreeningFinding(
                    farm_id=farm_id, run_id=latest.id, label="Mouth lesion", status="PENDING_REVIEW"
                ),
                ScreeningFinding(
                    farm_id=farm_id,
                    run_id=latest.id,
                    label=HEALTHY_CONTROL_LABEL,
                    status="PENDING_REVIEW",
                ),
            ]
        )
        keys = reviewed.id, pending.id, unusable.id, foreign.id
        latest_id = latest.id
        await db.commit()
    response = await client.get("/api/screening/images", headers=owner)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 3
    rows = {r["id"]: r for r in body["images"]}
    assert set(rows) == set(keys[:3]) and keys[3] not in rows
    assert rows[keys[0]]["latest_run"]["id"] == latest_id
    assert rows[keys[0]]["latest_run"]["image_id"] == keys[0]
    assert (rows[keys[0]]["pending_findings"], rows[keys[0]]["pending_healthy_controls"]) == (2, 1)
    assert rows[keys[0]]["error"] is None
    assert rows[keys[1]]["latest_run"] is None
    assert (rows[keys[1]]["pending_findings"], rows[keys[1]]["pending_healthy_controls"]) == (0, 0)
    assert rows[keys[2]]["error"] == QUALITY_ERROR
    for query, expected in [
        ("status=PENDING", [keys[1]]),
        ("bucket=BREEDING", [keys[1]]),
        ("bucket=RESTING&status=HEALTHY", [keys[0]]),
        ("status=ERROR", []),
    ]:
        filtered = await client.get(f"/api/screening/images?{query}", headers=owner)
        assert filtered.status_code == 200, filtered.text
        assert filtered.json()["total"] == len(expected)
        assert [r["id"] for r in filtered.json()["images"]] == expected
    foreign_detail = await client.get(f"/api/screening/images/{keys[3]}", headers=owner)
    assert foreign_detail.status_code == 404, foreign_detail.text


async def test_retained_crop_quality_reads_only_successful_gate_evidence_and_immutable_urls(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    settings = _cycle_settings(crop_detection=True)
    monkeypatch.setattr(screening_api, "get_settings", lambda: settings)
    async with get_sessionmaker()() as db:
        image = ScreeningImage(
            farm_id=farm_id,
            s3_bucket="goat-photos",
            s3_key="raw/crops.jpg",
            normalized_key="normalized/photo.jpg",
            status="HEALTHY",
        )
        db.add(image)
        await db.flush()
        crops = [
            ScreeningCrop(
                farm_id=farm_id,
                image_id=image.id,
                crop_index=i,
                box_x=i * 100,
                box_y=0,
                box_w=100,
                box_h=100,
                status=status,
                normalized_key=f"normalized/crop-{i}.jpg" if i != 3 else None,
                error="Retained decode failure" if status == "ERROR" else None,
            )
            for i, status in enumerate(["HEALTHY", "HEALTHY", "FLAGGED", "ERROR"])
        ]
        db.add_all(crops)
        await db.flush()
        # A legacy healthy summary is normalized using the retained successful
        # quality gate. A later failed invocation retains diagnostic detail,
        # but cannot replace the earlier successful clinical verdict.
        db.add_all(
            [
                _run(farm_id, image.id, crop_id=crops[0].id, quality=True, age_minutes=3),
                _run(farm_id, image.id, crop_id=crops[1].id, quality=False, age_minutes=2),
                _run(
                    farm_id, image.id, crop_id=crops[1].id, quality=True, age_minutes=1, failed=True
                ),
            ]
        )
        image_id = image.id
        crop_ids = [c.id for c in crops]
        await db.commit()
    response = await client.get(f"/api/screening/images/{image_id}", headers=owner)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "UNASSESSABLE" and body["error"] == QUALITY_ERROR
    assert unquote(urlsplit(body["image_url"]).path).endswith("/normalized/photo.jpg")
    assert "X-Amz-Signature=" in body["image_url"]
    assert [c["id"] for c in body["crops"]] == crop_ids
    assert [c["status"] for c in body["crops"]] == ["UNASSESSABLE", "HEALTHY", "FLAGGED", "ERROR"]
    assert [c["error"] for c in body["crops"]] == [
        QUALITY_ERROR,
        None,
        None,
        "Retained decode failure",
    ]
    for i, crop in enumerate(body["crops"]):
        if i == 3:
            assert crop["image_url"] is None
        else:
            assert unquote(urlsplit(crop["image_url"]).path).endswith(f"/normalized/crop-{i}.jpg")
            assert "X-Amz-Signature=" in crop["image_url"]
    assert len(body["runs"]) == 3 and body["findings"] == []


async def test_disabled_runtime_can_read_retained_crop_derivatives_without_minting_urls(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    disabled = Settings(screening_enabled=False)
    monkeypatch.setattr(screening_api, "get_settings", lambda: disabled)
    async with get_sessionmaker()() as db:
        image = ScreeningImage(
            farm_id=farm_id,
            s3_bucket="retained-photos",
            s3_key="raw/archive.jpg",
            normalized_key="normalized/archive.jpg",
            status="HEALTHY",
        )
        db.add(image)
        await db.flush()
        crop = ScreeningCrop(
            farm_id=farm_id,
            image_id=image.id,
            crop_index=0,
            box_x=0,
            box_y=0,
            box_w=100,
            box_h=100,
            status="HEALTHY",
            normalized_key="normalized/archive-crop.jpg",
        )
        db.add(crop)
        image_id = image.id
        await db.commit()
    response = await client.get(f"/api/screening/images/{image_id}", headers=owner)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "HEALTHY" and body["image_url"] is None
    assert len(body["crops"]) == 1 and body["crops"][0]["image_url"] is None


@pytest.mark.parametrize(
    "key",
    [0, MAX_IMAGE_KEY, MAX_IMAGE_KEY + 1, -1],
    ids=["retained-zero", "last-int8", "overflow", "negative"],
)
async def test_image_detail_reserves_zero_and_admits_real_int8_ceiling(
    client: httpx.AsyncClient,
    key: int,
) -> None:
    owner = await owner_with_farm(client)
    if 0 <= key <= MAX_IMAGE_KEY:
        async with get_sessionmaker()() as db:
            db.add(
                ScreeningImage(
                    id=key,
                    farm_id=int(owner["X-Farm-Id"]),
                    s3_bucket="retained-photos",
                    s3_key=f"raw/retained-key-{key}.jpg",
                    status="PENDING",
                )
            )
            await db.commit()
        listing = await client.get("/api/screening/images", headers=owner)
        assert listing.status_code == 200, listing.text
        assert listing.json()["total"] == 1 and listing.json()["images"][0]["id"] == key
    response = await client.get(f"/api/screening/images/{key}", headers=owner)
    assert response.status_code == (200 if key == MAX_IMAGE_KEY else 404), response.text
    if key == MAX_IMAGE_KEY:
        assert response.json()["id"] == key and response.json()["status"] == "PENDING"
    else:
        assert response.json()["detail"] == "Screening image not found"


@pytest.mark.parametrize(("days", "status"), [(365, 200), (366, 422)])
async def test_provider_scoreboard_retains_the_one_year_window(
    client: httpx.AsyncClient,
    days: int,
    status: int,
) -> None:
    owner = await owner_with_farm(client)
    response = await client.get(f"/api/screening/stats?days={days}", headers=owner)
    assert response.status_code == status, response.text
    if status == 200:
        assert response.json()["window_days"] == days and response.json()["providers"] == []


async def test_provider_scoreboard_correlates_the_single_live_photo_with_its_run(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        image = ScreeningImage(
            farm_id=farm_id, s3_bucket="retained-photos", s3_key="raw/score.jpg", status="HEALTHY"
        )
        db.add(image)
        await db.flush()
        db.add(_run(farm_id, image.id))
        await db.commit()
    response = await client.get("/api/screening/stats", headers=owner)
    assert response.status_code == 200, response.text
    providers = response.json()["providers"]
    assert len(providers) == 1
    assert providers[0]["provider"] == "retained-camera-provider"
    assert providers[0]["gate_runs"] == 1


async def test_image_read_coexists_with_an_independent_native_shared_retention_lease(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    async with get_sessionmaker()() as db:
        image = ScreeningImage(
            farm_id=int(owner["X-Farm-Id"]),
            s3_bucket="retained-photos",
            s3_key="raw/shared-lease.jpg",
            status="PENDING",
        )
        db.add(image)
        await db.commit()
        image_id = image.id
    async with get_sessionmaker()() as reader, get_sessionmaker()() as observer:
        row = await reader.scalar(
            select(ScreeningImage).where(ScreeningImage.id == image_id).with_for_update(read=True)
        )
        assert row is not None
        pid = await reader.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(pid, int)
        request = asyncio.create_task(
            client.get(f"/api/screening/images/{image_id}", headers=owner)
        )
        blocked: list[int] = []
        try:
            deadline = asyncio.get_running_loop().time() + 30
            while not request.done():
                blocked = list(
                    (
                        await observer.execute(
                            text(
                                "SELECT pid FROM pg_stat_activity "
                                "WHERE datname = current_database() "
                                "AND wait_event_type = 'Lock' "
                                "AND :holder = ANY(pg_blocking_pids(pid))"
                            ),
                            {"holder": pid},
                        )
                    ).scalars()
                )
                if blocked:
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError(
                        "Photo read returned neither response nor shared-lock witness"
                    )
                await asyncio.sleep(0.02)
            await reader.rollback()
            response = await asyncio.wait_for(request, timeout=30)
        finally:
            await reader.rollback()
            if not request.done():
                request.cancel()
            await asyncio.gather(request, return_exceptions=True)
    assert response.status_code == 200, response.text
    assert response.json()["id"] == image_id
    assert not blocked, (
        f"An independent compatible shared image lease blocked this reader: {blocked}"
    )


async def test_intake_uses_the_established_cross_runtime_farm_mutex(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    settings = _cycle_settings()
    monkeypatch.setattr(screening_api, "get_settings", lambda: settings)
    async with get_sessionmaker()() as lease, get_sessionmaker()() as observer:
        # Namespace 4716 is the established intake inter-runtime protocol.
        # This independent native lease represents an in-flight older runtime,
        # not another request using the same changed constant.
        await lease.execute(select(func.pg_advisory_xact_lock(4716, farm_id)))
        pid = await lease.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(pid, int)
        request = asyncio.create_task(client.post("/api/screening/batches", headers=owner))
        blocked: list[int] = []
        try:
            deadline = asyncio.get_running_loop().time() + 30
            while not request.done():
                blocked = list(
                    (
                        await observer.execute(
                            text(
                                "SELECT pid FROM pg_stat_activity "
                                "WHERE datname = current_database() "
                                "AND wait_event_type = 'Lock' "
                                "AND :holder = ANY(pg_blocking_pids(pid))"
                            ),
                            {"holder": pid},
                        )
                    ).scalars()
                )
                if blocked:
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError(
                        "Intake returned neither response nor a real farm-mutex witness"
                    )
                await asyncio.sleep(0.02)
            await lease.rollback()
            response = await asyncio.wait_for(request, timeout=30)
        finally:
            await lease.rollback()
            if not request.done():
                request.cancel()
            await asyncio.gather(request, return_exceptions=True)
    assert response.status_code == 201, response.text
    async with get_sessionmaker()() as db:
        batch = await db.get(ScreeningBatch, int(response.json()["id"]))
        assert batch is not None and batch.farm_id == farm_id and batch.created_by_id is not None
    assert blocked, "New intake escaped the older runtime's genuine same-farm intake lease"
