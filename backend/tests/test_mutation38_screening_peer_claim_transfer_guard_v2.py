"""A real committed peer takeover must survive stale-claim admission.

The observer delays exactly one production stale-claim SELECT until another
configured native session finishes the actual reservation helper. Every SQL
statement/result and ORM identity remains native; no provider outcome, source,
clock, database timestamp or result row is replaced.
"""

from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.engine import Result
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql import Executable, Select

from app.core.config import Settings
from app.db import get_sessionmaker
from app.models import ScreeningContentClaim, ScreeningImage
from app.services.retention import RetentionSummary, _sweep_farm
from app.services.screening.pipeline import _reserve_normalized_content
from app.utils import utcnow

from .conftest import owner_with_farm

DIGEST = "a" * 64


def _image(
    farm_id: int,
    key: str,
    *,
    status: str = "PROCESSING",
    old: bool = False,
    sha: str | None = None,
) -> ScreeningImage:
    """Constraint-valid restored evidence, matching native claim contracts."""
    return ScreeningImage(
        farm_id=farm_id,
        s3_bucket="test-bucket",
        s3_key=f"raw/{farm_id}/{key}.jpg",
        status=status,
        sha256=sha,
        created_at=utcnow() - timedelta(days=200 if old else 0),
    )


async def test_stale_reservation_cannot_steal_a_peer_committed_live_claim(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await owner_with_farm(client, email="peer-transfer-guard@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as intake:
        old = _image(farm_id, "transfer-old", status="HEALTHY", old=True, sha=DIGEST)
        peer = _image(farm_id, "transfer-peer")
        candidate = _image(farm_id, "transfer-candidate")
        intake.add_all([old, peer, candidate])
        await intake.flush()
        intake.add(ScreeningContentClaim(farm_id=farm_id, image_id=old.id, sha256=DIGEST))
        await intake.commit()
        old_id, peer_id, candidate_id = old.id, peer.id, candidate.id
    now = utcnow()
    retention = RetentionSummary()
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
            summary=retention,
        )
        await planner.commit()
    assert retention.screening_deletion_intents_staged == 1
    observed_admissions = 0
    async with get_sessionmaker()() as worker:
        fresh = await worker.get(ScreeningImage, candidate_id)
        assert fresh is not None
        original_execute = worker.execute

        async def observe_stale_admission(
            statement: Executable, *args: Any, **kwargs: Any
        ) -> Result[Any]:
            nonlocal observed_admissions
            if (
                isinstance(statement, Select)
                and statement._for_update_arg is not None
                and any(
                    item.get("entity") is ScreeningContentClaim
                    for item in statement.column_descriptions
                )
            ):
                observed_admissions += 1
                assert observed_admissions == 1
                async with get_sessionmaker()() as peer_worker:
                    peer_image = await peer_worker.get(ScreeningImage, peer_id)
                    assert peer_image is not None
                    assert (
                        await _reserve_normalized_content(peer_worker, peer_image, DIGEST)
                        == peer_id
                    )
            return await original_execute(statement, *args, **kwargs)

        monkeypatch.setattr(worker, "execute", observe_stale_admission)
        try:
            await _reserve_normalized_content(worker, fresh, DIGEST)
        except IntegrityError:
            # Existing native behavior reports its original unique conflict.
            # The business assertion concerns ownership, not exception text.
            await worker.rollback()
        else:
            await worker.commit()
    assert observed_admissions == 1
    async with get_sessionmaker()() as inspector:
        claim = (await inspector.execute(select(ScreeningContentClaim))).scalar_one()
        peer_row = await inspector.get(ScreeningImage, peer_id)
        candidate_row = await inspector.get(ScreeningImage, candidate_id)
        prior = await inspector.get(ScreeningImage, old_id)
        assert prior is not None and prior.retention_tombstoned_at is not None
        assert peer_row is not None and peer_row.retention_tombstoned_at is None
        assert candidate_row is not None and candidate_row.retention_tombstoned_at is None
        assert claim.image_id == peer_id, (
            "a stale contender stole the live peer's committed reservation"
        )
        assert peer_row.sha256 == DIGEST and candidate_row.sha256 is None
