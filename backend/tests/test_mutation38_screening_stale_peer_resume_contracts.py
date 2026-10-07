"""A resumed native consumer preserves a peer's completed derivative.

The UTC seam represents elapsed time while the first consumer is suspended
at real external provider I/O. A second normal constrained worker reclaims
the stale queue and completes both photos. No claimed/model rows are edited
by the test; the original consumer then resumes with its real identity map.
"""

import asyncio
import datetime as dt

import httpx
import pytest
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import ScreeningImage, ScreeningRun
from app.services.screening import pipeline
from app.services.screening.providers import ProviderAnswer
from app.services.screening.rotation import ProviderRotation
from app.utils import today, utcnow

from .conftest import owner_with_farm
from .test_screening import (
    CountingProvider,
    FakeStorage,
    _cycle_settings,
    _jpeg_bytes,
    _register_fake_objects,
)


class SuspendedExternalProvider:
    name = "suspended-consumer"
    model = "native-peer-resume"

    def __init__(self) -> None:
        self.reached = asyncio.Event()
        self.release = asyncio.Event()
        self.delegate = CountingProvider(name=self.name, model=self.model)

    async def complete(self, image_jpeg: bytes, system_prompt: str) -> ProviderAnswer:
        if not self.reached.is_set():
            self.reached.set()
            await self.release.wait()
        return await self.delegate.complete(image_jpeg, system_prompt)


async def test_resumed_native_consumer_preserves_later_photos_peer_committed_success(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    day = today().isoformat()
    first_key = f"raw/{farm_id}/{day}/BREEDING/first-lease.jpg"
    later_key = f"raw/{farm_id}/{day}/BREEDING/later-lease.jpg"
    storage = FakeStorage(
        objects={first_key: _jpeg_bytes(900, 1400), later_key: _jpeg_bytes(1000, 1500)}
    )
    async with get_sessionmaker()() as setup:
        await _register_fake_objects(setup, farm_id, storage, keys=[first_key, later_key])
        later_id = await setup.scalar(
            select(ScreeningImage.id).where(ScreeningImage.s3_key == later_key)
        )
        assert isinstance(later_id, int)
    initial_clock = utcnow()
    clock = initial_clock
    monkeypatch.setattr(pipeline, "utcnow", lambda: clock)
    suspended = SuspendedExternalProvider()
    settings = _cycle_settings(max_images_per_cycle=2)
    async with get_sessionmaker()() as original:
        task = asyncio.create_task(
            pipeline.run_screening_cycle(original, settings, storage, ProviderRotation([suspended]))
        )
        try:
            await asyncio.wait_for(suspended.reached.wait(), timeout=30)
            # Native snapshot provenance really is stale after the later peer
            # normalizes/deletes raw bytes; do not clear or alter this cache.
            old_later = await original.get(ScreeningImage, later_id)
            assert old_later is not None and old_later.status == "PROCESSING"
            assert old_later.normalized_key is None and old_later.screening_attempts == 1
            clock = initial_clock + dt.timedelta(
                seconds=settings.screening_stale_processing_after_seconds + 60
            )
            async with get_sessionmaker()() as peer:
                finished = await pipeline.run_screening_cycle(
                    peer,
                    settings,
                    storage,
                    ProviderRotation([CountingProvider(name="recovering-peer")]),
                )
                real_later = await peer.get(ScreeningImage, later_id)
                assert finished.claimed == 2 and finished.errors == 0
                assert real_later is not None and real_later.status == "HEALTHY"
                assert real_later.screening_attempts == 2 and real_later.error is None
                derivative, actual_sha = real_later.normalized_key, real_later.sha256
                assert derivative is not None and derivative in storage.objects
                peer_run_id = await peer.scalar(
                    select(ScreeningRun.id).where(
                        ScreeningRun.image_id == later_id,
                        ScreeningRun.stage == "GATE",
                        ScreeningRun.provider == "recovering-peer",
                    )
                )
                assert isinstance(peer_run_id, int)
            assert later_key not in storage.objects
            assert old_later.normalized_key is None and old_later.screening_attempts == 1
            suspended.release.set()
            await asyncio.wait_for(task, timeout=30)
        finally:
            suspended.release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await original.rollback()
    async with get_sessionmaker()() as check:
        completed = await check.get(ScreeningImage, later_id)
        peer_run = await check.get(ScreeningRun, peer_run_id)
        assert completed is not None and peer_run is not None
        assert completed.status == "HEALTHY" and completed.error is None, (
            "A resumed earlier consumer overwrote the peer's actual completed photo "
            f"with {completed.status}: {completed.error}"
        )
        assert completed.normalized_key == derivative and completed.sha256 == actual_sha
        assert completed.screening_attempts == 2
        assert peer_run.run_status == "OK" and peer_run.verdict == "healthy"
