"""Neutral controls require a healthy parent; each detected goat gets a verdict.

Native PostgreSQL and Pillow exercise the public screening cycle with declared
in-memory provider/storage adapters. JPEG inputs naturally enter or miss the
documented farm-local ten-percent sample; no sampler, IDs or outputs are patched.
"""

import hashlib
import io
from dataclasses import dataclass, field

import httpx
import pytest
from PIL import Image
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import ScreeningCrop, ScreeningFinding, ScreeningImage, ScreeningRun
from app.models.screening import HEALTHY_CONTROL_LABEL
from app.services.screening.images import normalize_image
from app.services.screening.pipeline import _sample_healthy_control, run_screening_cycle
from app.services.screening.providers import ProviderAnswer, ProviderError
from app.services.screening.rotation import ProviderRotation
from app.services.screening.s3 import ScreeningStorageError
from app.utils import today

from .conftest import owner_with_farm
from .test_screening import CountingProvider, FakeStorage, _cycle_settings


def _sampled_jpeg(farm_id: int, sampled: bool, width: int, height: int) -> bytes:
    """Find ordinary pixels in the unchanged documented sampling partition."""
    for red in range(256):
        buffer = io.BytesIO()
        Image.new("RGB", (width, height), (red, 83, 127)).save(buffer, format="JPEG")
        raw = buffer.getvalue()
        normalized = normalize_image(raw, _cycle_settings().screening_image_max_edge_px)
        digest = hashlib.sha256(
            f"healthy-control-v1:{farm_id}:{normalized.sha256}".encode("ascii")
        ).digest()
        if (int.from_bytes(digest[:8], "big") % 10 == 0) is sampled:
            return raw
    raise AssertionError("the ordinary JPEG fixtures did not span the declared sample partition")


@dataclass
class _GateUnavailableProvider(CountingProvider):
    """Detection succeeds; each real subsequent provider invocation fails."""

    async def complete(self, image_jpeg: bytes, system_prompt: str) -> ProviderAnswer:
        if "Find every goat" in system_prompt:
            return await super().complete(image_jpeg, system_prompt)
        self.calls += 1
        raise ProviderError("declared whole-frame gate outage")


@dataclass
class _FirstCropUploadFails(FakeStorage):
    """The first actual crop derivative upload fails through its typed protocol."""

    failed_crop_keys: list[str] = field(default_factory=list)

    def upload(self, key: str, data: bytes, content_type: str) -> None:
        if key.endswith("-c0.jpg"):
            self.failed_crop_keys.append(key)
            raise ScreeningStorageError("declared first crop derivative outage")
        super().upload(key, data, content_type)


async def _intake(farm_id: int, storage: FakeStorage, name: str, raw: bytes) -> int:
    key = f"raw/{farm_id}/{today().isoformat()}/{name}.jpg"
    storage.objects[key] = raw
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
        return image.id


@pytest.mark.parametrize(
    ("sampled", "boxes", "expected_status", "expected_controls"),
    [
        (True, [[100, 100, 300, 600]], "HEALTHY", 1),
        (False, [[100, 100, 300, 600]], "HEALTHY", 0),
        (True, [[100, 100, 800, 100]], "FLAGGED", 0),
    ],
    ids=["sampled-healthy", "unsampled-healthy", "sampled-positive-crop"],
)
async def test_neutral_control_waits_for_parent_and_uses_its_whole_frame_evidence(
    client: httpx.AsyncClient,
    sampled: bool,
    boxes: list[list[int]],
    expected_status: str,
    expected_controls: int,
) -> None:
    owner = await owner_with_farm(client, email="crop-neutral-control@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    storage = FakeStorage()
    image_id = await _intake(
        farm_id,
        storage,
        "crop-neutral-control",
        _sampled_jpeg(farm_id, sampled, 300, 600),
    )
    provider = CountingProvider(name="declared-crop-control", detect_boxes=boxes)
    async with get_sessionmaker()() as worker:
        summary = await run_screening_cycle(
            worker,
            _cycle_settings(crop_detection=True),
            storage,
            ProviderRotation([provider]),
        )
    assert summary.claimed == 1 and summary.errors == 0
    assert (summary.healthy, summary.flagged) == (
        (1, 0) if expected_status == "HEALTHY" else (0, 1)
    )
    async with get_sessionmaker()() as observer:
        image = await observer.get(ScreeningImage, image_id)
        assert image is not None and image.status == expected_status
        assert _sample_healthy_control(image) is sampled
        crops = list((await observer.execute(select(ScreeningCrop))).scalars())
        assert [crop.status for crop in crops] == [expected_status]
        runs = list((await observer.execute(select(ScreeningRun))).scalars())
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
        assert len(coverage) == 1 and coverage[0].verdict == "healthy"
        findings = list((await observer.execute(select(ScreeningFinding))).scalars())
        controls = [finding for finding in findings if finding.label == HEALTHY_CONTROL_LABEL]
        assert len(controls) == expected_controls, (
            "a neutral control must be sampled only after the complete parent verdict is healthy"
        )
        if controls:
            assert controls[0].farm_id == farm_id and controls[0].run_id == coverage[0].id
            assert controls[0].crop_id is None
        elif expected_status == "FLAGGED":
            assert findings and all(finding.crop_id == crops[0].id for finding in findings)


async def test_whole_frame_gate_outage_counts_one_parent_error(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="crop-safety-outage@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    storage = FakeStorage()
    image_id = await _intake(
        farm_id, storage, "crop-safety-outage", _sampled_jpeg(farm_id, True, 300, 600)
    )
    provider = _GateUnavailableProvider(name="declared-coverage-gate-unavailable")
    async with get_sessionmaker()() as worker:
        summary = await run_screening_cycle(
            worker,
            _cycle_settings(crop_detection=True),
            storage,
            ProviderRotation([provider]),
        )
    assert (summary.claimed, summary.errors, summary.healthy, summary.flagged) == (
        1,
        1,
        0,
        0,
    )
    async with get_sessionmaker()() as observer:
        image = await observer.get(ScreeningImage, image_id)
        assert image is not None and image.status == "ERROR"
        assert not list((await observer.execute(select(ScreeningFinding))).scalars())
        assert not list((await observer.execute(select(ScreeningCrop))).scalars())
        runs = list((await observer.execute(select(ScreeningRun))).scalars())
        assert any(run.stage == "DETECT" and run.run_status == "OK" for run in runs)
        assert any(run.stage == "GATE" and run.run_status == "ERROR" for run in runs)


async def test_unusable_first_box_does_not_leave_later_goat_pending(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="crop-geometry-continuation@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    storage = FakeStorage()
    image_id = await _intake(
        farm_id,
        storage,
        "crop-geometry-continuation",
        _sampled_jpeg(farm_id, True, 100, 200),
    )
    # Both proposals pass the declared detector schema/size policy. Pixel
    # clipping makes the edge proposal unusable; the second goat is usable.
    provider = CountingProvider(
        name="declared-edge-proposal",
        detect_boxes=[[1000, 1000, 40, 40], [100, 100, 600, 800]],
    )
    async with get_sessionmaker()() as worker:
        summary = await run_screening_cycle(
            worker,
            _cycle_settings(crop_detection=True),
            storage,
            ProviderRotation([provider]),
        )
    assert (summary.claimed, summary.errors, summary.healthy) == (1, 1, 0)
    async with get_sessionmaker()() as observer:
        image = await observer.get(ScreeningImage, image_id)
        assert image is not None and image.status == "ERROR"
        crops = list(
            (
                await observer.execute(select(ScreeningCrop).order_by(ScreeningCrop.crop_index))
            ).scalars()
        )
        assert [crop.status for crop in crops] == ["ERROR", "HEALTHY"], (
            "one unusable detector proposal must not abandon a later detected goat"
        )
        assert crops[0].error and crops[0].normalized_key is None
        assert crops[1].normalized_key in storage.uploaded
        assert not list((await observer.execute(select(ScreeningFinding))).scalars())


async def test_failed_first_crop_upload_does_not_leave_later_goat_pending(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="crop-upload-continuation@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    storage = _FirstCropUploadFails()
    image_id = await _intake(
        farm_id,
        storage,
        "crop-upload-continuation",
        _sampled_jpeg(farm_id, True, 300, 600),
    )
    provider = CountingProvider(
        name="declared-crop-upload",
        detect_boxes=[[100, 100, 300, 600], [500, 100, 300, 600]],
    )
    async with get_sessionmaker()() as worker:
        summary = await run_screening_cycle(
            worker,
            _cycle_settings(crop_detection=True),
            storage,
            ProviderRotation([provider]),
        )
    assert (summary.claimed, summary.errors, summary.healthy) == (1, 1, 0)
    assert len(storage.failed_crop_keys) == 1
    async with get_sessionmaker()() as observer:
        image = await observer.get(ScreeningImage, image_id)
        assert image is not None and image.status == "ERROR"
        crops = list(
            (
                await observer.execute(select(ScreeningCrop).order_by(ScreeningCrop.crop_index))
            ).scalars()
        )
        assert [crop.status for crop in crops] == ["ERROR", "HEALTHY"], (
            "one typed derivative failure must not abandon a later detected goat"
        )
        assert crops[0].normalized_key is None and crops[0].error
        assert crops[1].normalized_key in storage.uploaded
        assert not list((await observer.execute(select(ScreeningFinding))).scalars())
