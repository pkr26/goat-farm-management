"""One claimed photo contributes exactly one factual cycle outcome."""

import hashlib
import json
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import ScreeningImage
from app.services.screening.pipeline import run_screening_cycle
from app.services.screening.providers import ProviderAnswer
from app.services.screening.rotation import ProviderRotation
from app.services.screening.s3 import (
    ScreeningObjectInfo,
    ScreeningObjectTooLargeError,
    ScreeningStorageError,
)
from app.utils import today

from .conftest import owner_with_farm
from .test_screening import CountingProvider, FakeStorage, _cycle_settings, _jpeg_bytes


@pytest.mark.parametrize("crop_detection", [False, True], ids=["whole-frame", "detector-empty"])
@pytest.mark.parametrize("verdict", ["HEALTHY", "FLAGGED", "UNASSESSABLE", "ERROR"])
async def test_single_real_photo_has_exact_clinical_and_cycle_totals(
    client: httpx.AsyncClient,
    crop_detection: bool,
    verdict: str,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    captured = today() - timedelta(days=2)
    key = f"raw/{farm_id}/{captured.isoformat()}/single-photo.jpg"
    storage = FakeStorage()
    storage.objects[key] = _jpeg_bytes(160, 80) if verdict == "FLAGGED" else _jpeg_bytes(80, 160)

    class ClinicalProvider(CountingProvider):
        async def complete(self, image_jpeg: bytes, system_prompt: str) -> ProviderAnswer:
            if verdict == "UNASSESSABLE" and "Find every goat" not in system_prompt:
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
            return await super().complete(image_jpeg, system_prompt)

    provider = ClinicalProvider(name="actual-verdict", fail=verdict == "ERROR", detect_boxes=[])
    async with get_sessionmaker()() as db:
        db.add(
            ScreeningImage(
                farm_id=farm_id,
                s3_bucket=storage.bucket,
                s3_key=key,
                captured_date=captured,
                status="PENDING",
            )
        )
        await db.commit()
        summary = await run_screening_cycle(
            db,
            _cycle_settings(crop_detection=crop_detection),
            storage,
            ProviderRotation([provider]),
        )
        image = (await db.execute(select(ScreeningImage))).scalar_one()
        assert image.status == verdict
        assert provider.calls == {"HEALTHY": 1, "FLAGGED": 2, "UNASSESSABLE": 1, "ERROR": 1}[
            verdict
        ] + int(crop_detection)
        assert summary.claimed == 1
        assert (
            summary.healthy,
            summary.flagged,
            summary.unassessable,
            summary.errors,
            summary.skipped,
        ) == {
            "HEALTHY": (1, 0, 0, 0, 0),
            "FLAGGED": (0, 1, 0, 0, 0),
            "UNASSESSABLE": (0, 0, 1, 0, 0),
            "ERROR": (0, 0, 0, 1, 0),
        }[verdict]
        assert image.normalized_key is not None and image.sha256 is not None
        assert (
            image.normalized_key
            == f"screening/{farm_id}/{captured.isoformat()}/v2/images/{image.id}/{image.sha256}.jpg"
        )
        assert summary.budget_deferred == summary.retried_errors == summary.retried_flagged == 0


@pytest.mark.parametrize(
    ("fault", "status", "reason"),
    [
        ("metadata", "ERROR", "DOWNLOAD_FAILED"),
        ("download", "ERROR", "DOWNLOAD_FAILED"),
        ("upload", "ERROR", "DOWNLOAD_FAILED"),
        ("raw-delete", "ERROR", "DOWNLOAD_FAILED"),
        ("invalid-raster", "SKIPPED", "INVALID_IMAGE"),
        ("head-oversize", "SKIPPED", "OBJECT_TOO_LARGE"),
        ("stream-oversize", "SKIPPED", "OBJECT_TOO_LARGE"),
        ("wrong-token", "SKIPPED", "object upload token does not match its pre-registration"),
        ("wrong-type", "SKIPPED", "object content type does not match its pre-registration"),
        (
            "changed-derivative",
            "ERROR",
            "normalized screening derivative failed its integrity check",
        ),
    ],
)
async def test_storage_frontier_contributes_one_truthful_failure_and_no_clinical_calls(
    client: httpx.AsyncClient,
    fault: str,
    status: str,
    reason: str,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    captured = today()
    key = f"raw/{farm_id}/{captured.isoformat()}/frontier.jpg"

    class FrontierStorage(FakeStorage):
        def object_info(self, source_key: str) -> ScreeningObjectInfo | None:
            if fault == "metadata":
                raise ScreeningStorageError("actual object store metadata refusal")
            return super().object_info(source_key)

        def download(
            self,
            source_key: str,
            *,
            max_bytes: int,
            etag: str | None = None,
            version_id: str | None = None,
        ) -> bytes:
            if fault == "download":
                raise ScreeningStorageError("actual object store transfer refusal")
            if fault == "stream-oversize":
                raise ScreeningObjectTooLargeError(
                    "actual streamed object crossed its size allowance"
                )
            return super().download(
                source_key, max_bytes=max_bytes, etag=etag, version_id=version_id
            )

        def upload(self, destination: str, data: bytes, content_type: str) -> None:
            if fault == "upload":
                raise ScreeningStorageError("actual derivative upload refusal")
            super().upload(destination, data, content_type)

    storage = FrontierStorage()
    storage.objects[key] = (
        b"unusable JPEG bytes" if fault == "invalid-raster" else _jpeg_bytes(80, 160)
    )
    if fault == "head-oversize":
        storage.sizes[key] = 26_279_937
    if fault == "raw-delete":
        storage.delete_failures_remaining = 1
    registered = fault in {"wrong-token", "wrong-type"}
    if registered:
        storage.metadata[key] = {
            "screening-token": "incorrect" if fault == "wrong-token" else "t" * 32
        }
        storage.content_types[key] = "image/png" if fault == "wrong-type" else "image/jpeg"
    derivative = f"screening/{farm_id}/{captured.isoformat()}/retained-derivative.jpg"
    if fault == "changed-derivative":
        storage.objects[derivative] = _jpeg_bytes(80, 160)
    provider = CountingProvider(name="must-not-be-billed")
    async with get_sessionmaker()() as db:
        image = ScreeningImage(
            farm_id=farm_id,
            s3_bucket=storage.bucket,
            s3_key=key,
            captured_date=captured,
            status="PENDING",
            upload_token="t" * 32 if registered else None,
            upload_content_type="image/jpeg" if registered else None,
            normalized_key=derivative if fault == "changed-derivative" else None,
            sha256=hashlib.sha256(b"prior immutable derivative bytes").hexdigest()
            if fault == "changed-derivative"
            else None,
            width=80 if fault == "changed-derivative" else None,
            height=160 if fault == "changed-derivative" else None,
        )
        db.add(image)
        await db.commit()
        summary = await run_screening_cycle(
            db, _cycle_settings(), storage, ProviderRotation([provider])
        )
        assert image.status == status
        assert image.error is not None and reason in image.error
        assert summary.claimed == 1
        assert (
            summary.healthy,
            summary.flagged,
            summary.unassessable,
            summary.errors,
            summary.skipped,
        ) == ((0, 0, 0, 1, 0) if status == "ERROR" else (0, 0, 0, 0, 1))
        assert provider.calls == 0


async def test_slow_live_upload_refunds_only_its_current_probe_attempt(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    storage = FakeStorage()
    async with get_sessionmaker()() as db:
        image = ScreeningImage(
            farm_id=farm_id,
            s3_bucket=storage.bucket,
            s3_key=f"raw/{farm_id}/{today().isoformat()}/still-uploading.jpg",
            captured_date=today(),
            status="PENDING",
            upload_token="t" * 32,
            upload_content_type="image/jpeg",
            screening_attempts=2,
        )
        db.add(image)
        await db.commit()
        provider = CountingProvider(name="must-not-be-billed")
        summary = await run_screening_cycle(
            db, _cycle_settings(), storage, ProviderRotation([provider])
        )
        assert image.status == "PENDING" and image.error is None
        assert image.screening_attempts == 2
        assert image.next_attempt_at is not None
        assert summary.claimed == 1
        assert (
            summary.healthy,
            summary.flagged,
            summary.unassessable,
            summary.errors,
            summary.skipped,
        ) == (0, 0, 0, 0, 0)
        assert provider.calls == 0
