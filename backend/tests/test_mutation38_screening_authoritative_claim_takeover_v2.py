"""A real takeover failure retains identity through the production retry boundary.

Both producer attempts use the public screening cycle. The due retry uses
the production claim helper's explicit ``now`` and its committed row in the
production per-image helper; no wall-clock advance or row/cache patch is used.
"""

import datetime as dt
from dataclasses import dataclass, field

import httpx
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import ScreeningContentClaim, ScreeningImage
from app.services.screening.images import normalize_image
from app.services.screening.pipeline import (
    ERROR_RETRY_AFTER,
    CycleSummary,
    _claim_retry_rows,
    _process_image,
    pending_upload_abandoned_after,
    run_screening_cycle,
)
from app.services.screening.rotation import ProviderRotation
from app.services.screening.s3 import ScreeningStorageError
from app.utils import today

from .conftest import owner_with_farm
from .test_screening import CountingProvider, FakeStorage, _cycle_settings, _jpeg_bytes


@dataclass
class FailingDerivativeStorage(FakeStorage):
    """Two actual upload attempts fail with the declared storage error."""

    upload_failures_remaining: int = 2
    failed_upload_keys: list[str] = field(default_factory=list)

    def upload(self, key: str, data: bytes, content_type: str) -> None:
        if self.upload_failures_remaining:
            self.upload_failures_remaining -= 1
            self.failed_upload_keys.append(key)
            raise ScreeningStorageError("declared derivative upload failure")
        super().upload(key, data, content_type)


async def test_failed_stalled_owner_takeover_retains_claim_on_due_changed_raw_retry(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="claim-takeover-retry-v2@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    settings = _cycle_settings()
    original_raw = _jpeg_bytes(160, 80)
    replacement_raw = _jpeg_bytes(80, 160)
    prior = normalize_image(original_raw, settings.screening_image_max_edge_px, None)
    replacement = normalize_image(replacement_raw, settings.screening_image_max_edge_px, None)
    assert replacement.sha256 != prior.sha256
    old_key = f"raw/{farm_id}/stalled-owner-v2.jpg"
    takeover_key = f"raw/{farm_id}/takeover-v2.jpg"
    storage = FailingDerivativeStorage(objects={old_key: original_raw})
    provider = CountingProvider(name="takeover-identity-must-not-be-billed")

    # Start with ordinary trusted intake only: no digest, derivative or claim.
    async with get_sessionmaker()() as intake:
        old = ScreeningImage(
            farm_id=farm_id,
            s3_bucket=storage.bucket,
            s3_key=old_key,
            captured_date=today(),
            status="PENDING",
        )
        intake.add(old)
        await intake.commit()
        old_id = old.id
    async with get_sessionmaker()() as worker:
        initial = await run_screening_cycle(worker, settings, storage, ProviderRotation([provider]))
    assert (initial.claimed, initial.errors, initial.skipped, initial.healthy) == (1, 1, 0, 0)
    assert provider.calls == 0
    async with get_sessionmaker()() as observer:
        initial_error = await observer.get(ScreeningImage, old_id)
        assert initial_error is not None and initial_error.status == "ERROR"
        assert initial_error.sha256 == prior.sha256 and initial_error.normalized_key is None
        claim = (await observer.execute(select(ScreeningContentClaim))).scalar_one()
        assert (claim.farm_id, claim.image_id, claim.sha256) == (farm_id, old_id, prior.sha256)
        claim_id = claim.id

    # A fresh upload of the same bytes takes over the recent ERROR owner's
    # real claim. Its derivative also fails before its image SHA is written.
    storage.objects[takeover_key] = original_raw
    async with get_sessionmaker()() as intake:
        takeover = ScreeningImage(
            farm_id=farm_id,
            s3_bucket=storage.bucket,
            s3_key=takeover_key,
            captured_date=today(),
            status="PENDING",
        )
        intake.add(takeover)
        await intake.commit()
        takeover_id = takeover.id
    async with get_sessionmaker()() as worker:
        failed = await run_screening_cycle(worker, settings, storage, ProviderRotation([provider]))
    assert (failed.claimed, failed.errors, failed.skipped, failed.healthy) == (1, 1, 0, 0)
    assert provider.calls == 0
    assert len(storage.failed_upload_keys) == 2 and not storage.uploaded
    async with get_sessionmaker()() as observer:
        superseded = await observer.get(ScreeningImage, old_id)
        takeover_error = await observer.get(ScreeningImage, takeover_id)
        assert superseded is not None and superseded.status == "SKIPPED"
        assert superseded.error == "superseded by a newer upload of identical bytes"
        assert takeover_error is not None and takeover_error.status == "ERROR"
        assert takeover_error.sha256 is None and takeover_error.normalized_key is None
        claim = (await observer.execute(select(ScreeningContentClaim))).scalar_one()
        assert (claim.id, claim.farm_id, claim.image_id, claim.sha256) == (
            claim_id,
            farm_id,
            takeover_id,
            prior.sha256,
        )
        retry_boundary = takeover_error.updated_at + ERROR_RETRY_AFTER

    storage.objects[takeover_key] = replacement_raw
    # The helper accepts business time explicitly. At the actual persisted
    # ERROR timestamp + horizon no row is due; one microsecond later it is.
    # No database timestamp is rewritten and utcnow is never replaced.
    abandoned_after = pending_upload_abandoned_after(settings)
    stale_after = dt.timedelta(seconds=settings.screening_stale_processing_after_seconds)
    async with get_sessionmaker()() as worker:
        not_due = await _claim_retry_rows(worker, 10, retry_boundary, abandoned_after, stale_after)
        assert not_due == ([], 0, 0)
        claimed, retried_errors, retried_flagged = await _claim_retry_rows(
            worker,
            10,
            retry_boundary + dt.timedelta(microseconds=1),
            abandoned_after,
            stale_after,
        )
        assert [image.id for image in claimed] == [takeover_id]
        assert (retried_errors, retried_flagged) == (1, 0)
        summary = CycleSummary(claimed=1, retried_errors=retried_errors)
        await _process_image(
            worker,
            settings,
            storage,
            ProviderRotation([provider]),
            claimed[0],
            summary,
            today(),
        )
        await worker.commit()

    assert (summary.claimed, summary.skipped, summary.healthy, summary.errors) == (1, 1, 0, 0)
    assert provider.calls == 0
    assert len(storage.failed_upload_keys) == 2 and not storage.uploaded
    assert storage.objects[takeover_key] == replacement_raw
    async with get_sessionmaker()() as observer:
        rejected = await observer.get(ScreeningImage, takeover_id)
        assert rejected is not None and rejected.status == "SKIPPED"
        assert rejected.error == "object bytes changed after normalized content was claimed"
        assert rejected.sha256 is None and rejected.normalized_key is None
        claim = (await observer.execute(select(ScreeningContentClaim))).scalar_one()
        assert (claim.id, claim.farm_id, claim.image_id, claim.sha256) == (
            claim_id,
            farm_id,
            takeover_id,
            prior.sha256,
        )
