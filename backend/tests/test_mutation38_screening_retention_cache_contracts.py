"""Native content helpers must observe retention fences beyond an ORM cache."""

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.db import get_sessionmaker
from app.models import ScreeningContentClaim, ScreeningImage, ScreeningRetentionDeletion
from app.services.retention import RetentionSummary, _sweep_farm
from app.utils import utcnow

from .conftest import owner_with_farm
from .test_mutation38_screening_content_claim_contracts import (
    DIGEST,
    _image,
    _reserve,
    _resolve,
    _stored,
)


@pytest.mark.parametrize("operation", ["reserve", "resolve"])
async def test_native_cached_prior_owner_observes_the_real_committed_retention_fence(
    client: httpx.AsyncClient,
    operation: str,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as setup:
        prior = _image(farm_id, "cached-prior", status="HEALTHY", old=True, sha=DIGEST)
        fresh = _image(farm_id, "cached-fresh")
        setup.add_all([prior, fresh])
        await setup.flush()
        setup.add(ScreeningContentClaim(farm_id=farm_id, image_id=prior.id, sha256=DIGEST))
        await setup.commit()
        prior_id, fresh_id = prior.id, fresh.id
    async with get_sessionmaker()() as worker:
        cached_prior = await _stored(worker, prior_id)
        fresh = await _stored(worker, fresh_id)
        assert cached_prior.retention_tombstoned_at is None
        # Execute the actual distinct-session retention planner, including
        # root-row ownership, durable exact-key manifest and same-row Core
        # tombstone update. No clinical fields or provenance are cleared.
        now = utcnow()
        summary = RetentionSummary()
        async with get_sessionmaker()() as planner:
            await _sweep_farm(
                planner,
                farm_id=farm_id,
                screening_cutoff=now - timedelta(days=180),
                screening_batch_cutoff=now - timedelta(days=180),
                screening_budget_cutoff=now.date() - timedelta(days=400),
                notification_cutoff=now - timedelta(days=400),
                task_cutoff=now - timedelta(days=400),
                batch_size=10,
                settings=Settings(environment="development", s3_bucket="test-bucket"),
                summary=summary,
            )
            await planner.commit()
        assert summary.screening_deletion_intents_staged == 1
        assert cached_prior.retention_tombstoned_at is None
        fence = await worker.scalar(
            select(ScreeningImage.retention_tombstoned_at).where(ScreeningImage.id == prior_id)
        )
        assert fence is not None
        if operation == "reserve":
            assert await _reserve(worker, fresh, DIGEST) == fresh_id
        else:
            assert await _resolve(worker, fresh, prior_id) is None
            await worker.commit()
    async with get_sessionmaker()() as inspect:
        claim = await inspect.scalar(
            select(ScreeningContentClaim).where(ScreeningContentClaim.sha256 == DIGEST)
        )
        prior = await _stored(inspect, prior_id)
        intent = await inspect.scalar(
            select(ScreeningRetentionDeletion).where(
                ScreeningRetentionDeletion.image_id == prior_id
            )
        )
        assert claim is not None and claim.image_id == fresh_id
        assert (
            prior.retention_tombstoned_at == fence
            and prior.status == "HEALTHY"
            and prior.error is None
        )
        assert (
            intent is not None
            and intent.status == "PENDING"
            and intent.object_keys == [prior.s3_key]
        )
