"""A native cycle resumes real persisted derivatives from a preloaded session.

This is the exported AsyncSession interface. The deployed worker currently
creates a fresh session for each cycle, so its ordinary loop does not preload
a photo before another worker's completed cycle.
"""

import datetime as dt
from typing import cast

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db import get_sessionmaker
from app.models import ScreeningImage
from app.services.screening import pipeline
from app.services.screening.rotation import ProviderRotation
from app.services.screening.s3 import (
    ScreeningObjectInfo,
    ScreeningStorage,
    ScreeningStorageError,
)
from app.utils import today, utcnow

from .conftest import owner_with_farm
from .test_screening import (
    CountingProvider,
    FakeStorage,
    _cycle_settings,
    _jpeg_bytes,
    _register_fake_objects,
)


class OneTransientStorageFault(FakeStorage):
    failed_head = False

    def object_info(self, key: str) -> ScreeningObjectInfo | None:
        if not self.failed_head:
            self.failed_head = True
            raise ScreeningStorageError("external object store temporarily unavailable")
        return super().object_info(key)


@pytest.mark.parametrize(
    "initial_state",
    ["PENDING", "ERROR"],
    ids=["before-first-failure", "before-next-failure"],
)
async def test_native_preloaded_cycle_resumes_current_derivative_and_conserves_real_attempts(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, initial_state: str
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    raw_key = f"raw/{farm_id}/{today().isoformat()}/cached-resume.jpg"
    storage = OneTransientStorageFault()
    storage.objects[raw_key] = _jpeg_bytes(100, 200)
    async with get_sessionmaker()() as setup:
        await _register_fake_objects(setup, farm_id, storage)
    first_clock = utcnow()
    monkeypatch.setattr(pipeline, "utcnow", lambda: first_clock)
    if initial_state == "ERROR":
        async with get_sessionmaker()() as first_attempt:
            failed = await pipeline.run_screening_cycle(
                first_attempt,
                _cycle_settings(),
                cast(ScreeningStorage, storage),
                ProviderRotation([CountingProvider(name="camera")]),
            )
            real_failure = (await first_attempt.execute(select(ScreeningImage))).scalar_one()
            assert failed.errors == 1 and real_failure.status == "ERROR"
            assert real_failure.normalized_key is None and real_failure.error is not None
        second_clock = utcnow() + dt.timedelta(hours=2)
        monkeypatch.setattr(pipeline, "utcnow", lambda: second_clock)
    else:
        storage.failed_head = True
    async with get_sessionmaker()() as cached:
        initial = (await cached.execute(select(ScreeningImage))).scalar_one()
        image_id = initial.id
        assert initial.status == initial_state and initial.normalized_key is None
        assert (initial.error is not None) == (initial_state == "ERROR")
        async with get_sessionmaker()() as previous:
            first = await pipeline.run_screening_cycle(
                previous,
                _cycle_settings(),
                cast(ScreeningStorage, storage),
                ProviderRotation([CountingProvider(name="temporarily-down", fail=True)]),
            )
            real = await previous.get(ScreeningImage, image_id)
            assert first.errors == 1 and real is not None and real.status == "ERROR"
            assert real.normalized_key is not None and real.normalized_key in storage.objects
            derivative = real.normalized_key
            actual_sha = real.sha256
        # Real pipeline privacy deletion removed raw bytes after making its
        # derivative durable. No clinical row or stored provenance is edited.
        assert raw_key not in storage.objects
        assert initial.normalized_key is None  # genuine earlier identity map
        third_clock = utcnow() + dt.timedelta(hours=4)
        monkeypatch.setattr(pipeline, "utcnow", lambda: third_clock)
        provider = CountingProvider(name="camera-recovered")
        try:
            result = await pipeline.run_screening_cycle(
                cached,
                _cycle_settings(),
                cast(ScreeningStorage, storage),
                ProviderRotation([provider]),
            )
        except IntegrityError as exc:
            await cached.rollback()
            pytest.fail(f"A valid native cached retry must preserve current row constraints: {exc}")
        await cached.refresh(initial)
        assert result.claimed == 1 and result.healthy == 1 and result.errors == 0
        assert provider.calls == 1 and initial.status == "HEALTHY" and initial.error is None
        assert initial.normalized_key == derivative and initial.sha256 == actual_sha

        assert initial.screening_attempts == (3 if initial_state == "ERROR" else 2)
