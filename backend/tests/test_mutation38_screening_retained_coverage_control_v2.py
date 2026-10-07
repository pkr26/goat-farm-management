"""A crop retry resolves one latest control from real retained gate history.

The initial ERROR is produced by the public cycle. A real locked service
cascade adds one retained safety pass, as supported by existing native history
tests. Retry uses the production helper's explicit time and committed row;
no ordinary single-worker history-uniqueness or elapsed-hour claim is made.
"""

import datetime as dt
from dataclasses import dataclass, field

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import MultipleResultsFound

from app.db import get_sessionmaker
from app.models import Farm, ScreeningCrop, ScreeningFinding, ScreeningImage, ScreeningRun
from app.models.screening import HEALTHY_CONTROL_LABEL
from app.services.screening.pipeline import (
    ERROR_RETRY_AFTER,
    CycleSummary,
    _claim_retry_rows,
    _process_image,
    _run_cascade,
    pending_upload_abandoned_after,
    run_screening_cycle,
)
from app.services.screening.rotation import ProviderRotation
from app.services.screening.s3 import ScreeningStorageError
from app.utils import today

from .conftest import owner_with_farm
from .test_mutation38_screening_crop_control_completion import _sampled_jpeg
from .test_screening import CountingProvider, FakeStorage, _cycle_settings


@dataclass
class _FirstCropUploadFailsOnce(FakeStorage):
    failed_keys: list[str] = field(default_factory=list)

    def upload(self, key: str, data: bytes, content_type: str) -> None:
        if key.endswith("-c0.jpg") and not self.failed_keys:
            self.failed_keys.append(key)
            raise ScreeningStorageError("declared transient first crop derivative outage")
        super().upload(key, data, content_type)


async def test_retained_native_coverage_history_keeps_latest_neutral_review_provenance(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="retained-coverage-control-v2@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    settings = _cycle_settings(crop_detection=True)
    raw = _sampled_jpeg(farm_id, True, 300, 600)
    key = f"raw/{farm_id}/{today().isoformat()}/retained-coverage-control-v2.jpg"
    storage = _FirstCropUploadFailsOnce(objects={key: raw})
    provider = CountingProvider(
        name="actual-initial-safety-provider",
        detect_boxes=[[100, 100, 300, 600], [500, 100, 300, 600]],
    )
    async with get_sessionmaker()() as intake:
        image = ScreeningImage(
            farm_id=farm_id,
            s3_bucket=storage.bucket,
            s3_key=key,
            captured_date=today(),
            status="PENDING",
        )
        intake.add(image)
        await intake.commit()
        image_id = image.id
    async with get_sessionmaker()() as worker:
        initial = await run_screening_cycle(worker, settings, storage, ProviderRotation([provider]))
    assert (initial.claimed, initial.errors, initial.healthy) == (1, 1, 0)
    assert len(storage.failed_keys) == 1

    # This retained-history producer is the real service cascade, with a
    # generated image under a native lock and an actual declared provider
    # reply. No ScreeningRun, clinical result or timestamp is manufactured.
    retained_provider = CountingProvider(name="actual-retained-latest-safety-provider")
    async with get_sessionmaker()() as history:
        retained_image = (
            await history.execute(
                select(ScreeningImage).where(ScreeningImage.id == image_id).with_for_update()
            )
        ).scalar_one()
        assert retained_image.status == "ERROR"
        assert retained_image.normalized_key is not None
        farm = await history.get(Farm, farm_id)
        assert farm is not None
        business_day = today(farm.timezone)
        actual_normalized = storage.uploaded[retained_image.normalized_key]
        native_history_summary = CycleSummary()
        status = await _run_cascade(
            history,
            settings,
            ProviderRotation([retained_provider]),
            retained_image,
            None,
            actual_normalized,
            native_history_summary,
            business_day,
            coverage_safety_pass=True,
            sample_healthy=False,
        )
        assert status == "HEALTHY" and retained_provider.calls == 1
        await history.commit()
    async with get_sessionmaker()() as observer:
        error_image = await observer.get(ScreeningImage, image_id)
        assert error_image is not None and error_image.status == "ERROR"
        retry_boundary = error_image.updated_at + ERROR_RETRY_AFTER
        crops = list(
            (
                await observer.execute(select(ScreeningCrop).order_by(ScreeningCrop.crop_index))
            ).scalars()
        )
        assert [crop.status for crop in crops] == ["ERROR", "HEALTHY"]
        runs = list(
            (await observer.execute(select(ScreeningRun).order_by(ScreeningRun.id))).scalars()
        )
        coverage = [
            run
            for run in runs
            if run.image_id == image_id
            and run.crop_id is None
            and run.stage == "GATE"
            and run.run_status == "OK"
            and run.detail is not None
            and run.detail.get("coverage_safety_pass") is True
        ]
        assert len(coverage) == 2
        latest = max(coverage, key=lambda run: (run.created_at, run.id))
        assert latest.provider == retained_provider.name and latest.verdict == "healthy"
        latest_id = latest.id
        assert not list((await observer.execute(select(ScreeningFinding))).scalars())

    # ERROR eligibility is measured at the genuine durable boundary using
    # the production explicit-time seam; no wall clock or row is rewritten.
    async with get_sessionmaker()() as worker:
        claimed, retried_errors, retried_flagged = await _claim_retry_rows(
            worker,
            10,
            retry_boundary + dt.timedelta(microseconds=1),
            pending_upload_abandoned_after(settings),
            dt.timedelta(seconds=settings.screening_stale_processing_after_seconds),
        )
        assert [row.id for row in claimed] == [image_id]
        assert (retried_errors, retried_flagged) == (1, 0)
        retry = CycleSummary(claimed=1, retried_errors=retried_errors)
        try:
            await _process_image(
                worker,
                settings,
                storage,
                ProviderRotation([provider]),
                claimed[0],
                retry,
                business_day,
            )
        except MultipleResultsFound as exc:
            pytest.fail(
                f"A retained crop retry must resolve one latest neutral-control gate: {exc}"
            )
        await worker.commit()
    assert (retry.claimed, retry.healthy, retry.errors, retry.flagged) == (1, 1, 0, 0)
    async with get_sessionmaker()() as observer:
        healthy_image = await observer.get(ScreeningImage, image_id)
        assert healthy_image is not None and healthy_image.status == "HEALTHY"
        assert healthy_image.error is None
        crops = list((await observer.execute(select(ScreeningCrop))).scalars())
        assert len(crops) == 2 and all(crop.status == "HEALTHY" for crop in crops)
        controls = list((await observer.execute(select(ScreeningFinding))).scalars())
        assert len(controls) == 1 and controls[0].label == HEALTHY_CONTROL_LABEL
        assert controls[0].farm_id == farm_id and controls[0].crop_id is None
        assert controls[0].run_id == latest_id
        gate = await observer.get(ScreeningRun, latest_id)
        assert gate is not None and gate.image_id == image_id
        assert gate.detail is not None and gate.detail.get("healthy_control_sample") is True
