"""Veterinary decisions retain real author/history and supported bigint identities.

Explicit-key and pre-existing revision-zero fixtures are initially restored,
constraint-valid clinical records. The latter matches the forward migration's
published legacy baseline shape. No known decision is deleted or overwritten.
"""

import asyncio
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select, text

from app.db import get_sessionmaker
from app.models import Farm, ScreeningFinding, ScreeningImage, ScreeningRun, User
from app.models.screening import ScreeningFindingReview
from app.utils import utcnow

from .conftest import owner_with_farm
from .test_screening import _role_worker_headers

MAX_KEY = 9_223_372_036_854_775_807


async def _seed_pending(farm_id: int, finding_id: int) -> tuple[int, int]:
    async with get_sessionmaker()() as db:
        image = ScreeningImage(
            farm_id=farm_id,
            s3_bucket="retained-photos",
            s3_key=f"raw/{farm_id}/clinical-{finding_id}.jpg",
            status="FLAGGED",
        )
        db.add(image)
        await db.flush()
        run = ScreeningRun(
            farm_id=farm_id,
            image_id=image.id,
            stage="GATE",
            run_status="OK",
            verdict="flagged",
            confidence=Decimal("0.900"),
            provider="retained-vet-camera",
            model="retained-vet-model",
            prompt_version="retained",
            latency_ms=7,
            detail={"quality_problem": False},
        )
        db.add(run)
        await db.flush()
        db.add(
            ScreeningFinding(
                id=finding_id,
                farm_id=farm_id,
                run_id=run.id,
                label="Visible mouth lesion",
                status="PENDING_REVIEW",
            )
        )
        await db.commit()
        return image.id, run.id


@pytest.mark.parametrize(
    "key", [1, 0, MAX_KEY, MAX_KEY + 1], ids=["ordinary", "retained-zero", "last-int8", "overflow"]
)
async def test_vet_review_and_history_admit_positive_int8_ceiling_and_reserve_zero(
    client: httpx.AsyncClient,
    key: int,
) -> None:
    owner = await owner_with_farm(client)
    vet = await _role_worker_headers(client, owner, "VET", "boundary-vet@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    if key <= MAX_KEY:
        image_id, _run_id = await _seed_pending(farm_id, key)
        detail = await client.get(f"/api/screening/images/{image_id}", headers=owner)
        assert detail.status_code == 200, detail.text
        assert [f["id"] for f in detail.json()["findings"]] == [key]
    response = await client.post(
        f"/api/screening/findings/{key}/review",
        headers=vet,
        json={
            "status": "CONFIRMED",
            "expected_status": "PENDING_REVIEW",
            "expected_revision": 0,
            "review_note": "Veterinary physical examination",
        },
    )
    expected = 200 if 1 <= key <= MAX_KEY else 404
    assert response.status_code == expected, response.text
    history = await client.get(f"/api/screening/findings/{key}/reviews", headers=owner)
    assert history.status_code == expected, history.text
    if expected == 200:
        changed = response.json()
        assert changed["id"] == key and changed["status"] == "CONFIRMED"
        assert (
            changed["review_revision"] == 1
            and changed["review_note"] == "Veterinary physical examination"
        )
        async with get_sessionmaker()() as db:
            actor = await db.scalar(select(User.id).where(User.email == "boundary-vet@farm.in"))
            finding = await db.get(ScreeningFinding, key)
            review = await db.get(ScreeningFindingReview, (key, 1))
            assert actor is not None and changed["reviewed_by_id"] == actor
            assert (
                finding is not None and finding.farm_id == farm_id and finding.review_revision == 1
            )
            assert finding.status == "CONFIRMED" and finding.reviewed_by_id == actor
            assert (
                review is not None and review.farm_id == farm_id and review.reviewed_by_id == actor
            )
            assert review.previous_status == "PENDING_REVIEW" and review.status == "CONFIRMED"
        body = history.json()
        assert body["finding_id"] == key and body["review_revision"] == 1
        assert body["legacy_review"] is False and body["total"] == 1
        assert [r["revision"] for r in body["reviews"]] == [1]
    else:
        assert (
            response.json()["detail"] == history.json()["detail"] == "Screening finding not found"
        )
        if key == 0:
            async with get_sessionmaker()() as db:
                finding = await db.get(ScreeningFinding, key)
                assert finding is not None and finding.status == "PENDING_REVIEW"
                assert finding.review_revision == 0 and finding.reviewed_by_id is None
                assert await db.get(ScreeningFindingReview, (key, 1)) is None


async def test_existing_migrated_legacy_snapshot_is_preserved_during_a_real_vet_correction(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    vet = await _role_worker_headers(client, owner, "VET", "second-opinion-vet@farm.in")
    viewer = await _role_worker_headers(client, owner, "VIEWER", "history-viewer@farm.in")
    foreign_owner = await owner_with_farm(client, email="foreign-clinical-owner@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        original_author = farm.owner_id
        original_at = utcnow() - timedelta(days=7)
        image = ScreeningImage(
            farm_id=farm_id,
            s3_bucket="retained-photos",
            s3_key=f"raw/{farm_id}/legacy-baseline.jpg",
            status="FLAGGED",
        )
        db.add(image)
        await db.flush()
        run = ScreeningRun(
            farm_id=farm_id,
            image_id=image.id,
            stage="GATE",
            run_status="OK",
            verdict="flagged",
            confidence=Decimal("0.900"),
            provider="retained-vet-camera",
            model="retained-vet-model",
            prompt_version="retained",
            latency_ms=7,
        )
        db.add(run)
        await db.flush()
        finding = ScreeningFinding(
            farm_id=farm_id,
            run_id=run.id,
            label="Visible hoof lesion",
            status="CONFIRMED",
            reviewed_at=original_at,
            reviewed_by_id=original_author,
            review_revision=0,
            review_note="Known original clinical examination",
        )
        db.add(finding)
        await db.flush()
        db.add(
            ScreeningFindingReview(
                farm_id=farm_id,
                finding_id=finding.id,
                revision=0,
                previous_status="CONFIRMED",
                status="CONFIRMED",
                reviewed_at=original_at,
                reviewed_by_id=original_author,
                review_note=finding.review_note,
            )
        )
        finding_id = finding.id
        await db.commit()
    path = f"/api/screening/findings/{finding_id}"
    original = await client.get(f"{path}/reviews", headers=viewer)
    assert original.status_code == 200, original.text
    assert original.json()["total"] == 1 and original.json()["legacy_review"] is True
    baseline = original.json()["reviews"][0]
    assert baseline["revision"] == 0 and baseline["reviewed_by_id"] == original_author
    assert baseline["reviewed_at"] == original_at.isoformat()
    correction = {
        "status": "REJECTED",
        "expected_status": "CONFIRMED",
        "expected_revision": 0,
        "review_note": "Independent second veterinary opinion",
    }
    denied = await client.post(f"{path}/review", headers=viewer, json=correction)
    assert denied.status_code == 403, denied.text
    foreign = await client.get(f"{path}/reviews", headers=foreign_owner)
    assert foreign.status_code == 404, foreign.text
    response = await client.post(f"{path}/review", headers=vet, json=correction)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "REJECTED" and response.json()["review_revision"] == 1
    history = await client.get(f"{path}/reviews", headers=viewer)
    assert history.status_code == 200, history.text
    body = history.json()
    assert body["finding_id"] == finding_id and body["review_revision"] == 1
    assert body["legacy_review"] is True and body["total"] == 2
    assert [r["revision"] for r in body["reviews"]] == [1, 0]
    assert body["reviews"][1] == baseline
    latest = body["reviews"][0]
    assert latest["previous_status"] == "CONFIRMED" and latest["status"] == "REJECTED"
    assert latest["review_note"] == correction["review_note"]
    async with get_sessionmaker()() as db:
        actor = await db.scalar(select(User.id).where(User.email == "second-opinion-vet@farm.in"))
        assert actor is not None and latest["reviewed_by_id"] == actor
        snapshot = await db.get(ScreeningFindingReview, (finding_id, 0))
        assert snapshot is not None and snapshot.reviewed_by_id == original_author
        assert (
            snapshot.reviewed_at == original_at and snapshot.review_note == baseline["review_note"]
        )
    stale = await client.post(f"{path}/review", headers=vet, json=correction)
    assert stale.status_code == 409, stale.text
    unchanged = await client.get(f"{path}/reviews", headers=viewer)
    assert unchanged.status_code == 200, unchanged.text
    assert unchanged.json() == body


async def test_vet_adjudication_coexists_with_an_independent_shared_photo_reader(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    vet = await _role_worker_headers(client, owner, "VET", "shared-photo-vet@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    image_id, _run_id = await _seed_pending(farm_id, 1)
    async with get_sessionmaker()() as reader, get_sessionmaker()() as observer:
        image = await reader.scalar(
            select(ScreeningImage).where(ScreeningImage.id == image_id).with_for_update(read=True)
        )
        assert image is not None
        pid = await reader.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(pid, int)
        request = asyncio.create_task(
            client.post(
                "/api/screening/findings/1/review",
                headers=vet,
                json={
                    "status": "CONFIRMED",
                    "expected_revision": 0,
                    "expected_status": "PENDING_REVIEW",
                },
            )
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
                                "WHERE datname = current_database() AND wait_event_type = 'Lock' "
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
                        "Vet review gave neither response nor an image-reader lock witness"
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
    assert response.json()["status"] == "CONFIRMED" and response.json()["review_revision"] == 1
    assert not blocked, (
        f"An independent compatible shared image reader blocked adjudication: {blocked}"
    )
    async with get_sessionmaker()() as db:
        actor = await db.scalar(select(User.id).where(User.email == "shared-photo-vet@farm.in"))
        review = await db.get(ScreeningFindingReview, (1, 1))
        assert actor is not None and review is not None and review.reviewed_by_id == actor
        assert review.status == "CONFIRMED" and review.farm_id == farm_id
