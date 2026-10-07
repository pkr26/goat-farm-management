"""Screening preserves inclusive upload limits and retained-content ownership."""

import hashlib

import httpx
import pytest
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import ScreeningContentClaim, ScreeningImage
from app.services.screening.images import normalize_image
from app.services.screening.pipeline import MAX_DOWNLOAD_BYTES, run_screening_cycle
from app.services.screening.rotation import ProviderRotation
from app.utils import today

from .conftest import owner_with_farm
from .test_screening import CountingProvider, FakeStorage, _cycle_settings, _jpeg_bytes


async def test_exact_upload_byte_allowance_is_screened_normally(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    key = f"raw/{farm_id}/inclusive-allowance.jpg"
    raster = _jpeg_bytes(80, 160)
    data = raster + b"\x00" * (MAX_DOWNLOAD_BYTES - len(raster))
    assert len(data) == MAX_DOWNLOAD_BYTES
    storage = FakeStorage()
    storage.objects[key] = data
    provider = CountingProvider(name="inclusive-allowance")
    async with get_sessionmaker()() as seed:
        seed.add(
            ScreeningImage(
                farm_id=farm_id,
                s3_bucket=storage.bucket,
                s3_key=key,
                captured_date=today(),
                status="PENDING",
            )
        )
        await seed.commit()
    async with get_sessionmaker()() as worker:
        summary = await run_screening_cycle(
            worker, _cycle_settings(), storage, ProviderRotation([provider])
        )
        image = (await worker.execute(select(ScreeningImage))).scalar_one()
        assert image.status == "HEALTHY" and image.error is None
        assert (summary.claimed, summary.healthy, summary.skipped, summary.errors) == (
            1,
            1,
            0,
            0,
        )
        assert provider.calls == 1
        assert image.normalized_key is not None and image.sha256 is not None


async def test_retained_raw_content_identity_rejects_a_changed_upload(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    settings = _cycle_settings()
    prior = normalize_image(_jpeg_bytes(160, 80), settings.screening_image_max_edge_px, None)
    key = f"raw/{farm_id}/retained-raw-content.jpg"
    storage = FakeStorage()
    storage.objects[key] = _jpeg_bytes(80, 160)
    replacement = normalize_image(storage.objects[key], settings.screening_image_max_edge_px, None)
    assert replacement.sha256 != prior.sha256
    async with get_sessionmaker()() as seed:
        seed.add(
            ScreeningImage(
                farm_id=farm_id,
                s3_bucket=storage.bucket,
                s3_key=key,
                captured_date=today(),
                status="PENDING",
                sha256=prior.sha256,
            )
        )
        await seed.commit()
    provider = CountingProvider(name="retained-raw-must-not-be-billed")
    async with get_sessionmaker()() as worker:
        summary = await run_screening_cycle(worker, settings, storage, ProviderRotation([provider]))
        image = (await worker.execute(select(ScreeningImage))).scalar_one()
        assert image.status == "SKIPPED"
        assert image.error == "object bytes changed after normalized content was claimed"
        assert image.sha256 == prior.sha256 and image.normalized_key is None
        assert (summary.claimed, summary.healthy, summary.skipped, summary.errors) == (
            1,
            0,
            1,
            0,
        )
        assert provider.calls == 0
        assert storage.objects[key] == _jpeg_bytes(80, 160)
        assert not (await worker.execute(select(ScreeningContentClaim))).scalars().all()


@pytest.mark.parametrize("same_farm", [True, False], ids=["same-farm-legacy", "other-farm-legacy"])
async def test_constraint_valid_legacy_terminal_results_keep_tenant_deduplication(
    client: httpx.AsyncClient, same_farm: bool
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    prior_owner = owner if same_farm else await owner_with_farm(client, email="prior-owner@farm.in")
    prior_farm = int(prior_owner["X-Farm-Id"])
    settings = _cycle_settings()
    data = _jpeg_bytes(80, 160)
    normalized = normalize_image(data, settings.screening_image_max_edge_px, None)
    assert hashlib.sha256(normalized.data).hexdigest() == normalized.sha256
    storage = FakeStorage()
    key = f"raw/{farm_id}/new-legacy-result.jpg"
    prior_key = f"screening/{prior_farm}/legacy-terminal.jpg"
    storage.objects[key] = data
    storage.objects[prior_key] = normalized.data
    async with get_sessionmaker()() as seed:
        seed.add(
            ScreeningImage(
                farm_id=prior_farm,
                s3_bucket=storage.bucket,
                s3_key=f"raw/{prior_farm}/legacy-original.jpg",
                captured_date=today(),
                status="HEALTHY",
                sha256=normalized.sha256,
                normalized_key=prior_key,
                width=normalized.width,
                height=normalized.height,
            )
        )
        seed.add(
            ScreeningImage(
                farm_id=farm_id,
                s3_bucket=storage.bucket,
                s3_key=key,
                captured_date=today(),
                status="PENDING",
            )
        )
        await seed.commit()
        assert not (await seed.execute(select(ScreeningContentClaim))).scalars().all()
    provider = CountingProvider(name="tenant-deduplication")
    async with get_sessionmaker()() as worker:
        summary = await run_screening_cycle(worker, settings, storage, ProviderRotation([provider]))
        image = (
            await worker.execute(select(ScreeningImage).where(ScreeningImage.s3_key == key))
        ).scalar_one()
        assert summary.claimed == 1 and summary.errors == 0
        if same_farm:
            assert image.status == "SKIPPED"
            assert image.error == "duplicate: identical bytes already screened for this farm"
            assert image.sha256 == normalized.sha256 and image.normalized_key is None
            assert (summary.skipped, summary.healthy, provider.calls) == (1, 0, 0)
            assert not (await worker.execute(select(ScreeningContentClaim))).scalars().all()
        else:
            assert image.status == "HEALTHY" and image.error is None
            assert (summary.skipped, summary.healthy, provider.calls) == (0, 1, 1)
            assert image.normalized_key is not None
            claim = (await worker.execute(select(ScreeningContentClaim))).scalar_one()
            assert (claim.farm_id, claim.image_id, claim.sha256) == (
                farm_id,
                image.id,
                normalized.sha256,
            )
