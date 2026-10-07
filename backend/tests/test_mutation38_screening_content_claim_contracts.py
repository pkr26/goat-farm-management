"""Native content reservation conserves immutable ownership and retention evidence.

All rows and tombstone manifests are constraint-enabled. Retained sparse image
bindings are initially restored records, never edits to known clinical facts.
Only the finalization case substitutes the external permanent-object-delete
adapter, matching the native retention suite; database planning, deletion and
claim resolution all execute their real implementations.
"""

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db import get_sessionmaker
from app.models import ScreeningContentClaim, ScreeningImage, ScreeningRetentionDeletion
from app.services.retention import run_retention_sweep
from app.services.screening import pipeline
from app.services.screening.s3 import ScreeningStorage
from app.utils import utcnow

from .conftest import owner_with_farm

DIGEST = "a" * 64
OTHER_DIGEST = "b" * 64
DUPLICATE_REASON = "duplicate: identical bytes already screened for this farm"


def _image(
    farm_id: int,
    key: str,
    *,
    status: str = "PROCESSING",
    old: bool = False,
    sha: str | None = None,
    tombstoned: bool = False,
) -> ScreeningImage:
    return ScreeningImage(
        farm_id=farm_id,
        s3_bucket="test-bucket",
        s3_key=f"raw/{farm_id}/{key}.jpg",
        status=status,
        sha256=sha,
        retention_tombstoned_at=utcnow() if tombstoned else None,
        created_at=utcnow() - timedelta(days=200 if old else 0),
    )


def _manifest(image: ScreeningImage) -> ScreeningRetentionDeletion:
    return ScreeningRetentionDeletion(
        farm_id=image.farm_id,
        image_id=image.id,
        s3_bucket=image.s3_bucket,
        object_keys=[image.s3_key],
        preserved_keys=[],
    )


async def _stored(db: AsyncSession, image_id: int) -> ScreeningImage:
    row = await db.get(ScreeningImage, image_id)
    assert row is not None
    return row


async def _reserve(db: AsyncSession, image: ScreeningImage, digest: str) -> int | None:
    try:
        return await pipeline._reserve_normalized_content(db, image, digest)
    except SQLAlchemyError as exc:
        pytest.fail(
            f"Valid content admission must return its documented owner/rejection decision: {exc}"
        )


async def _resolve(db: AsyncSession, image: ScreeningImage, owner_id: int) -> str | None:
    try:
        return await pipeline._resolve_content_claim_conflict(db, image, DIGEST, owner_id)
    except SQLAlchemyError as exc:
        pytest.fail(
            f"Valid retained/raced ownership must return its documented duplicate decision: {exc}"
        )


async def test_reobserved_canonical_digest_repairs_an_initially_missing_image_marker(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        image = _image(farm_id, "restored-marker")
        db.add(image)
        await db.flush()
        db.add(ScreeningContentClaim(farm_id=farm_id, image_id=image.id, sha256=DIGEST))
        await db.commit()
        image_id = image.id
    async with get_sessionmaker()() as db:
        image = await _stored(db, image_id)
        assert image is not None and image.sha256 is None
        result = await _reserve(db, image, DIGEST)
        assert result == image_id
    async with get_sessionmaker()() as db:
        image = await _stored(db, image_id)
        claim = await db.scalar(
            select(ScreeningContentClaim).where(ScreeningContentClaim.image_id == image_id)
        )
        assert image is not None and image.sha256 == DIGEST
        assert claim is not None and claim.sha256 == DIGEST and claim.image_id == image_id


async def test_duplicate_candidate_does_not_gain_an_ownership_marker_or_commit_pending_work(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        canonical = _image(farm_id, "canonical", status="HEALTHY", sha=DIGEST)
        candidate = _image(farm_id, "duplicate")
        db.add_all([canonical, candidate])
        await db.flush()
        db.add(ScreeningContentClaim(farm_id=farm_id, image_id=canonical.id, sha256=DIGEST))
        await db.commit()
        canonical_id, candidate_id = canonical.id, candidate.id
    async with get_sessionmaker()() as db:
        candidate = await _stored(db, candidate_id)
        assert candidate is not None
        # A native caller may retain a pending capture-date correction in this
        # transaction. Merely finding somebody else's owner must not commit it.
        candidate.bucket = "RESTING"
        result = await _reserve(db, candidate, DIGEST)
        assert result == canonical_id
        assert candidate.sha256 is None
        await db.rollback()
    async with get_sessionmaker()() as db:
        candidate = await _stored(db, candidate_id)
        assert candidate is not None and candidate.sha256 is None and candidate.bucket is None


async def test_tombstone_conflict_transfers_without_rewriting_the_fenced_prior_result(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        prior = _image(farm_id, "fenced-prior", status="HEALTHY", sha=DIGEST, tombstoned=True)
        fresh = _image(farm_id, "fenced-reupload")
        db.add_all([prior, fresh])
        await db.flush()
        db.add_all(
            [
                ScreeningContentClaim(farm_id=farm_id, image_id=prior.id, sha256=DIGEST),
                _manifest(prior),
            ]
        )
        await db.commit()
        prior_id, fresh_id = prior.id, fresh.id
        prior_fence = prior.retention_tombstoned_at
    async with get_sessionmaker()() as db:
        fresh = await _stored(db, fresh_id)
        assert fresh is not None
        decision = await _resolve(db, fresh, prior_id)
        assert decision is None
        await db.commit()
    async with get_sessionmaker()() as db:
        prior = await _stored(db, prior_id)
        claim = await db.scalar(
            select(ScreeningContentClaim).where(ScreeningContentClaim.sha256 == DIGEST)
        )
        intent = await db.scalar(
            select(ScreeningRetentionDeletion).where(
                ScreeningRetentionDeletion.image_id == prior_id
            )
        )
        assert claim is not None and claim.image_id == fresh_id
        assert prior is not None and prior.status == "HEALTHY" and prior.error is None
        assert prior.sha256 == DIGEST and prior.retention_tombstoned_at == prior_fence
        assert (
            intent is not None
            and intent.status == "PENDING"
            and intent.object_keys == [prior.s3_key]
        )


async def test_fresh_tombstone_reservation_ignores_an_unrelated_same_farm_claim(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        prior = _image(farm_id, "transfer-prior", status="HEALTHY", sha=DIGEST, tombstoned=True)
        fresh = _image(farm_id, "transfer-fresh")
        unrelated = _image(farm_id, "unrelated", status="HEALTHY", sha=OTHER_DIGEST)
        db.add_all([prior, fresh, unrelated])
        await db.flush()
        db.add_all(
            [
                ScreeningContentClaim(farm_id=farm_id, image_id=prior.id, sha256=DIGEST),
                ScreeningContentClaim(farm_id=farm_id, image_id=unrelated.id, sha256=OTHER_DIGEST),
                _manifest(prior),
            ]
        )
        await db.commit()
        prior_id, fresh_id, unrelated_id = prior.id, fresh.id, unrelated.id
    async with get_sessionmaker()() as db:
        fresh = await _stored(db, fresh_id)
        assert fresh is not None
        assert await _reserve(db, fresh, DIGEST) == fresh_id
    async with get_sessionmaker()() as db:
        claims = list(
            (
                await db.execute(
                    select(ScreeningContentClaim).where(ScreeningContentClaim.farm_id == farm_id)
                )
            ).scalars()
        )
        assert {c.sha256: c.image_id for c in claims} == {
            DIGEST: fresh_id,
            OTHER_DIGEST: unrelated_id,
        }
        fresh = await _stored(db, fresh_id)
        prior = await _stored(db, prior_id)
        assert fresh is not None and fresh.sha256 == DIGEST
        assert (
            prior is not None
            and prior.retention_tombstoned_at is not None
            and prior.status == "HEALTHY"
        )


@pytest.mark.parametrize("fenced_digest_owner", [False, True], ids=["new-digest", "fenced-digest"])
async def test_one_image_never_rebinds_its_existing_claim_to_changed_normalized_bytes(
    client: httpx.AsyncClient,
    fenced_digest_owner: bool,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        image = _image(farm_id, "immutable-candidate", sha=OTHER_DIGEST)
        db.add(image)
        await db.flush()
        db.add(ScreeningContentClaim(farm_id=farm_id, image_id=image.id, sha256=OTHER_DIGEST))
        if fenced_digest_owner:
            prior = _image(
                farm_id, "immutable-fenced", status="HEALTHY", sha=DIGEST, tombstoned=True
            )
            db.add(prior)
            await db.flush()
            db.add_all(
                [
                    ScreeningContentClaim(farm_id=farm_id, image_id=prior.id, sha256=DIGEST),
                    _manifest(prior),
                ]
            )
        await db.commit()
        image_id = image.id
    async with get_sessionmaker()() as db:
        image = await _stored(db, image_id)
        assert image is not None
        assert await _reserve(db, image, DIGEST) is None
        # A rejected savepoint must leave the enclosing transaction usable.
        assert (
            await db.scalar(select(ScreeningImage.id).where(ScreeningImage.id == image_id))
            == image_id
        )
        await db.rollback()
    async with get_sessionmaker()() as db:
        image = await _stored(db, image_id)
        claim = await db.scalar(
            select(ScreeningContentClaim).where(ScreeningContentClaim.image_id == image_id)
        )
        assert image is not None and image.sha256 == OTHER_DIGEST
        assert claim is not None and claim.sha256 == OTHER_DIGEST


@pytest.mark.parametrize("winner_is_candidate", [False, True], ids=["other-winner", "own-winner"])
async def test_stale_owner_argument_reloads_the_actual_retention_takeover_winner(
    client: httpx.AsyncClient,
    winner_is_candidate: bool,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        prior = _image(farm_id, "race-prior", status="HEALTHY", sha=DIGEST, tombstoned=True)
        candidate = _image(farm_id, "race-candidate")
        other = _image(farm_id, "race-other")
        db.add_all([prior, candidate, other])
        await db.flush()
        db.add_all(
            [
                ScreeningContentClaim(farm_id=farm_id, image_id=prior.id, sha256=DIGEST),
                _manifest(prior),
            ]
        )
        await db.commit()
        prior_id, candidate_id, other_id = prior.id, candidate.id, other.id
    winner_id = candidate_id if winner_is_candidate else other_id
    async with get_sessionmaker()() as db:
        winner = await _stored(db, winner_id)
        assert winner is not None and await _reserve(db, winner, DIGEST) == winner_id
    async with get_sessionmaker()() as db:
        candidate = await _stored(db, candidate_id)
        assert candidate is not None
        decision = await _resolve(db, candidate, prior_id)
        assert decision == (None if winner_is_candidate else DUPLICATE_REASON)
        await db.commit()
    async with get_sessionmaker()() as db:
        claim = await db.scalar(
            select(ScreeningContentClaim).where(ScreeningContentClaim.sha256 == DIGEST)
        )
        assert claim is not None and claim.image_id == winner_id
        prior = await _stored(db, prior_id)
        assert prior is not None and prior.status == "HEALTHY" and prior.error is None


async def test_cascade_deleted_owner_is_replaced_by_a_new_durable_claim_after_actual_retention(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        prior = _image(farm_id, "deleted-prior", status="HEALTHY", sha=DIGEST, old=True)
        candidate = _image(farm_id, "deleted-reupload")
        db.add_all([prior, candidate])
        await db.flush()
        db.add(ScreeningContentClaim(farm_id=farm_id, image_id=prior.id, sha256=DIGEST))
        await db.commit()
        prior_id, candidate_id, prior_key = prior.id, candidate.id, prior.s3_key
    deleted: list[list[str]] = []

    def external_object_delete(_storage: ScreeningStorage, keys: list[str]) -> None:
        deleted.append(list(keys))

    monkeypatch.setattr(ScreeningStorage, "delete_permanently", external_object_delete)
    async with get_sessionmaker()() as db:
        summary = await run_retention_sweep(
            db, Settings(environment="development", s3_bucket="test-bucket")
        )
    assert summary.screening_images == 1 and deleted == [[prior_key]]
    async with get_sessionmaker()() as db:
        assert await db.get(ScreeningImage, prior_id) is None
        candidate = await _stored(db, candidate_id)
        assert candidate is not None
        assert await _resolve(db, candidate, prior_id) is None
    async with get_sessionmaker()() as db:
        claim = await db.scalar(
            select(ScreeningContentClaim).where(ScreeningContentClaim.sha256 == DIGEST)
        )
        candidate = await _stored(db, candidate_id)
        assert claim is not None and claim.image_id == candidate_id
        assert candidate is not None and candidate.sha256 == DIGEST
