"""Clinical urgency remains visible when another goat's screen is unfinished."""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import ScreeningCrop, ScreeningImage, ScreeningRun
from app.services.screening.pipeline import _aggregate_crop_statuses, run_screening_cycle
from app.services.screening.rotation import ProviderRotation
from app.utils import today, utcnow

from .conftest import owner_with_farm
from .test_screening import CountingProvider, FakeStorage, _cycle_settings, _jpeg_bytes


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([], "ERROR"),
        (["HEALTHY"], "HEALTHY"),
        (["FLAGGED", "ERROR"], "FLAGGED"),
        (["ERROR", "FLAGGED"], "FLAGGED"),
        (["HEALTHY", "UNASSESSABLE"], "UNASSESSABLE"),
        (["UNASSESSABLE", "ERROR"], "ERROR"),
        (["ERROR", "UNASSESSABLE", "FLAGGED"], "FLAGGED"),
    ],
)
def test_parent_preserves_urgent_flags_and_reports_incomplete_healthy_coverage(
    statuses: list[str], expected: str
) -> None:
    assert _aggregate_crop_statuses(statuses) == expected


async def test_retry_keeps_a_clinical_flag_visible_when_another_crop_still_fails(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="crop-urgent-failed@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    capture_date = today()
    key = f"raw/{farm_id}/{capture_date.isoformat()}/BREEDING/urgent-failed.jpg"
    storage = FakeStorage(objects={key: _jpeg_bytes(2000, 1000)})
    aged = utcnow() - timedelta(hours=1, minutes=1)
    async with get_sessionmaker()() as db:
        image = ScreeningImage(
            farm_id=farm_id,
            bucket="BREEDING",
            s3_bucket=storage.bucket,
            s3_key=key,
            captured_date=capture_date,
            status="FLAGGED",
            created_at=aged,
            updated_at=aged,
        )
        db.add(image)
        await db.flush()
        image_id = image.id
        db.add_all(
            [
                ScreeningCrop(
                    farm_id=farm_id,
                    image_id=image_id,
                    crop_index=0,
                    box_x=100,
                    box_y=100,
                    box_w=600,
                    box_h=300,
                    status="FLAGGED",
                ),
                ScreeningCrop(
                    farm_id=farm_id,
                    image_id=image_id,
                    crop_index=1,
                    box_x=700,
                    box_y=100,
                    box_w=250,
                    box_h=600,
                    status="ERROR",
                    error="Original provider outage",
                ),
                # A crop-only retry reuses truthful, already-completed whole-frame
                # coverage. Its provider outage must leave both crop outcomes visible.
                ScreeningRun(
                    farm_id=farm_id,
                    image_id=image_id,
                    stage="GATE",
                    run_status="OK",
                    verdict="healthy",
                    provider="previous",
                    model="previous-test",
                    prompt_version="fixture-v1",
                    latency_ms=1,
                    detail={"coverage_safety_pass": True},
                    created_at=aged,
                ),
            ]
        )
        await db.commit()
    failing = CountingProvider(name="outage", fail=True)
    async with get_sessionmaker()() as db:
        summary = await run_screening_cycle(
            db, _cycle_settings(crop_detection=True), storage, ProviderRotation([failing])
        )
        parent = await db.get(ScreeningImage, image_id)
        assert parent is not None
        crops = (
            (await db.execute(select(ScreeningCrop).order_by(ScreeningCrop.crop_index)))
            .scalars()
            .all()
        )
        assert parent.status == "FLAGGED" and parent.error is None
        assert [crop.status for crop in crops] == ["FLAGGED", "ERROR"]
        assert crops[1].error is not None
    assert summary.claimed == summary.retried_flagged == summary.flagged == 1
    assert summary.retried_errors == 0
    assert failing.calls == 1
