"""Post-presign raw-object cleanup is durable and status-independent."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import subprocess
import sys
import threading
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path
from typing import cast

import asyncpg
import httpx
import pytest
from sqlalchemy import select, text, update

import app.api.screening as screening_api
import app.main as main_module
from app.db import get_engine, get_sessionmaker
from app.models import ScreeningImage
from app.services.screening import raw_cleanup
from app.services.screening.pipeline import (
    MAX_DOWNLOAD_BYTES,
    MAX_SCREENING_ATTEMPTS,
    CycleSummary,
    run_screening_cycle,
)
from app.services.screening.raw_cleanup import (
    RAW_CLEANUP_PROGRESS_RETRY,
    RAW_CLEANUP_RETRY_BASE,
    RAW_UPLOAD_CLEANUP_SLACK,
    RawCleanupSummary,
    claim_raw_cleanup_batch,
    raw_cleanup_deadline,
    run_raw_cleanup_batch,
)
from app.services.screening.rotation import ProviderRotation
from app.services.screening.s3 import (
    ScreeningStorage,
    ScreeningStorageDeleteInProgress,
    ScreeningStorageError,
)
from app.utils import utcnow

from .conftest import BACKEND_DIR, TEST_DB, owner_with_farm
from .test_screening import CountingProvider, FakeStorage, _cycle_settings, _jpeg_bytes


class ReplayableCleanupStorage:
    """Small idempotent storage double that permits presign-style replay."""

    bucket = "goat-photos"

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.purge_calls: list[str] = []

    def delete_permanently(self, keys: Sequence[str]) -> None:
        for key in keys:
            self.purge_calls.append(key)
            self.objects.pop(key, None)


class FailingCleanupStorage(ReplayableCleanupStorage):
    def delete_permanently(self, keys: Sequence[str]) -> None:
        raise ScreeningStorageError(
            f"provider https://s3.internal.invalid?credential=top-secret failed for {keys!r}"
        )


class MultiPageCleanupStorage(ReplayableCleanupStorage):
    def __init__(self, progress_pages: int) -> None:
        super().__init__()
        self.progress_pages = progress_pages

    def delete_permanently(self, keys: Sequence[str]) -> None:
        for key in keys:
            self.purge_calls.append(key)
        if self.progress_pages:
            self.progress_pages -= 1
            raise ScreeningStorageDeleteInProgress("one bounded version page removed")
        for key in keys:
            self.objects.pop(key, None)


async def _alembic_current_database(*args: str) -> subprocess.CompletedProcess[str]:
    migration_env = os.environ.copy()
    migration_env["GOATFARM_MIGRATION_WRITES_QUIESCED"] = "true"
    return await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env=migration_env,
        check=False,
        capture_output=True,
        text=True,
    )


async def _insert_due_image(
    *,
    farm_id: int,
    key: str,
    due_at: dt.datetime,
    status: str,
    error: str | None = None,
    attempts: int = 0,
    direct_registration: bool = True,
) -> int:
    async with get_sessionmaker()() as db:
        image = ScreeningImage(
            farm_id=farm_id,
            bucket="BREEDING",
            s3_bucket="goat-photos",
            s3_key=key,
            captured_date=due_at.date(),
            status=status,
            error=error,
            screening_attempts=attempts,
            upload_content_type="image/jpeg" if direct_registration else None,
            upload_token="u" * 43 if direct_registration else None,
            raw_cleanup_after=due_at,
            raw_cleanup_next_attempt_at=due_at,
        )
        db.add(image)
        await db.commit()
        return image.id


def test_raw_cleanup_deadline_uses_exact_expiry_and_fixed_slack() -> None:
    issued_at = dt.datetime(2026, 10, 4, 12, 30, 45, 123456)
    assert raw_cleanup_deadline(issued_at, 900) == (
        issued_at + dt.timedelta(seconds=900) + RAW_UPLOAD_CLEANUP_SLACK
    )
    with pytest.raises(ValueError, match="between"):
        raw_cleanup_deadline(issued_at, 0)
    with pytest.raises(ValueError, match="between"):
        raw_cleanup_deadline(issued_at, 86_401)


async def test_direct_upload_persists_exact_post_expiry_cleanup_deadline(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = await owner_with_farm(client, email="raw-deadline@farm.in")
    settings = _cycle_settings()
    issued_at = utcnow()
    monkeypatch.setattr(screening_api, "get_settings", lambda: settings)
    monkeypatch.setattr(screening_api, "utcnow", lambda: issued_at)

    batch = await client.post("/api/screening/batches", headers=headers)
    assert batch.status_code == 201, batch.text
    upload = await client.post(
        "/api/screening/uploads",
        json={
            "batch_id": batch.json()["id"],
            "bucket": "BREEDING",
            "file_name": "deadline.jpg",
            "content_type": "image/jpeg",
            "file_size": 1024,
        },
        headers=headers,
    )
    assert upload.status_code == 201, upload.text

    async with get_sessionmaker()() as db:
        image = await db.get(ScreeningImage, upload.json()["image_id"])
    assert image is not None
    expected = raw_cleanup_deadline(issued_at, settings.screening_presign_expiry_seconds)
    assert image.raw_cleanup_after == expected
    assert image.raw_cleanup_next_attempt_at == expected
    assert image.raw_cleanup_completed_at is None
    assert image.raw_cleanup_attempts == 0


async def test_cleanup_covers_every_terminal_shape_and_ignores_model_attempt_cap(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client, email="raw-terminal-shapes@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    due_at = utcnow() - dt.timedelta(minutes=1)
    shapes = [
        ("HEALTHY", None, 0, False),
        ("SKIPPED", "presigned upload never arrived (URL expired)", 0, True),
        ("SKIPPED", "photo bytes could not be processed (INVALID_IMAGE)", 1, True),
        ("SKIPPED", "photo exceeds the size limit (OBJECT_TOO_LARGE)", 1, True),
        ("SKIPPED", "duplicate: identical bytes already screened for this farm", 1, True),
        (
            "ERROR",
            "photo storage could not be reached (DOWNLOAD_FAILED)",
            MAX_SCREENING_ATTEMPTS,
            True,
        ),
    ]
    storage = ReplayableCleanupStorage()
    image_ids: list[int] = []
    for index, (status, error, attempts, direct) in enumerate(shapes):
        key = f"raw/{farm_id}/2026-10-04/BREEDING/shape-{index}.jpg"
        image_ids.append(
            await _insert_due_image(
                farm_id=farm_id,
                key=key,
                due_at=due_at,
                status=status,
                error=error,
                attempts=attempts,
                direct_registration=direct,
            )
        )
        storage.objects[key] = b"sensitive raw bytes"

    # One call is deliberately smaller than the backlog: work and memory are
    # bounded, with the remaining rows discoverable on the next page.
    first = await run_raw_cleanup_batch(
        get_sessionmaker(),
        cast(ScreeningStorage, storage),
        batch_size=3,
        now=utcnow(),
    )
    assert (first.claimed, first.completed, first.failed) == (3, 3, 0)
    assert len(storage.objects) == 3
    second = await run_raw_cleanup_batch(
        get_sessionmaker(),
        cast(ScreeningStorage, storage),
        batch_size=3,
        now=utcnow(),
    )
    assert (second.claimed, second.completed, second.failed) == (3, 3, 0)
    assert storage.objects == {}

    async with get_sessionmaker()() as db:
        images = list(
            (
                await db.execute(
                    select(ScreeningImage)
                    .where(ScreeningImage.id.in_(image_ids))
                    .order_by(ScreeningImage.id)
                )
            ).scalars()
        )
    assert len(images) == len(shapes)
    assert all(image.raw_cleanup_completed_at is not None for image in images)
    assert all(image.raw_cleanup_next_attempt_at is None for image in images)
    assert images[-1].screening_attempts == MAX_SCREENING_ATTEMPTS


async def test_pipeline_terminal_paths_keep_cleanup_obligation_until_final_purge(
    client: httpx.AsyncClient,
) -> None:
    """Real pipeline outcomes all converge on the independent cleanup saga."""
    headers = await owner_with_farm(client, email="raw-pipeline-paths@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    capture_day = dt.date.today()
    due_at = utcnow() - dt.timedelta(minutes=1)
    recent = utcnow()
    abandoned_at = recent - dt.timedelta(hours=3)
    healthy_bytes = _jpeg_bytes(1000, 2000)
    keys = {
        name: f"raw/{farm_id}/{capture_day.isoformat()}/BREEDING/{name}.jpg"
        for name in ("healthy", "invalid", "oversized", "duplicate", "abandoned")
    }
    tokens = {name: name[0] * 43 for name in keys}
    storage = FakeStorage(
        objects={
            keys["healthy"]: healthy_bytes,
            keys["invalid"]: b"not an image",
            keys["oversized"]: _jpeg_bytes(100, 100),
            keys["duplicate"]: healthy_bytes,
        },
        content_types=dict.fromkeys(keys.values(), "image/jpeg"),
        metadata={key: {"screening-token": tokens[name]} for name, key in keys.items()},
        sizes={keys["oversized"]: MAX_DOWNLOAD_BYTES + 1},
    )
    async with get_sessionmaker()() as db:
        for name in ("healthy", "invalid", "oversized", "duplicate", "abandoned"):
            created_at = abandoned_at if name == "abandoned" else recent
            db.add(
                ScreeningImage(
                    farm_id=farm_id,
                    bucket="BREEDING",
                    s3_bucket=storage.bucket,
                    s3_key=keys[name],
                    upload_content_type="image/jpeg",
                    upload_token=tokens[name],
                    captured_date=capture_day,
                    status="PENDING",
                    raw_cleanup_after=due_at,
                    raw_cleanup_next_attempt_at=due_at,
                    created_at=created_at,
                    updated_at=created_at,
                )
            )
        await db.commit()
        provider = CountingProvider(name="fake")
        screened = await run_screening_cycle(
            db,
            _cycle_settings(),
            storage,
            ProviderRotation([provider]),
        )

    assert (screened.expired_uploads, screened.claimed) == (1, 4)
    assert (screened.healthy, screened.skipped) == (1, 3)
    assert provider.calls == 1
    async with get_sessionmaker()() as db:
        before_cleanup = list(
            (
                await db.execute(select(ScreeningImage).where(ScreeningImage.farm_id == farm_id))
            ).scalars()
        )
    by_name = {
        image.s3_key.rsplit("/", 1)[-1].removesuffix(".jpg"): image for image in before_cleanup
    }
    assert by_name["healthy"].status == "HEALTHY"
    assert by_name["invalid"].error == "photo bytes could not be processed (INVALID_IMAGE)"
    assert by_name["oversized"].error == "photo exceeds the size limit (OBJECT_TOO_LARGE)"
    assert by_name["duplicate"].error == (
        "duplicate: identical bytes already screened for this farm"
    )
    assert "never arrived" in (by_name["abandoned"].error or "")
    assert all(image.raw_cleanup_completed_at is None for image in before_cleanup)
    pipeline_timestamps = {image.id: image.updated_at for image in before_cleanup}

    # The healthy path already performed its eager deletion, but a still-live
    # form may replay it. Invalid, oversized and duplicate raw bytes were not
    # normalized and remain until this post-window purge.
    assert keys["healthy"] not in storage.objects
    storage.objects[keys["healthy"]] = b"late presigned replay"
    assert {
        keys["healthy"],
        keys["invalid"],
        keys["oversized"],
        keys["duplicate"],
    }.issubset(storage.objects)

    cleaned = await run_raw_cleanup_batch(
        get_sessionmaker(),
        storage,
        batch_size=5,
        now=utcnow(),
    )
    assert (cleaned.claimed, cleaned.completed, cleaned.failed) == (5, 5, 0)
    assert not any(key.startswith(f"raw/{farm_id}/") for key in storage.objects)
    async with get_sessionmaker()() as db:
        after_cleanup = list(
            (
                await db.execute(select(ScreeningImage).where(ScreeningImage.farm_id == farm_id))
            ).scalars()
        )
    assert all(image.raw_cleanup_completed_at is not None for image in after_cleanup)
    assert all(image.updated_at == pipeline_timestamps[image.id] for image in after_cleanup)


async def test_replayed_raw_is_purged_and_db_ack_failure_retries_safely(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S3 success followed by DB failure must repeat the idempotent purge."""
    headers = await owner_with_farm(client, email="raw-replay-saga@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    cleanup_after = utcnow() + dt.timedelta(minutes=20)
    captured_date = dt.date.today()
    key = f"raw/{farm_id}/{captured_date.isoformat()}/BREEDING/replay.jpg"
    upload_token = "r" * 43
    storage = FakeStorage(
        objects={key: _jpeg_bytes(1000, 2000)},
        content_types={key: "image/jpeg"},
        metadata={key: {"screening-token": upload_token}},
    )
    async with get_sessionmaker()() as db:
        image = ScreeningImage(
            farm_id=farm_id,
            bucket="BREEDING",
            s3_bucket=storage.bucket,
            s3_key=key,
            upload_content_type="image/jpeg",
            upload_token=upload_token,
            captured_date=captured_date,
            status="PENDING",
            raw_cleanup_after=cleanup_after,
            raw_cleanup_next_attempt_at=cleanup_after,
        )
        db.add(image)
        await db.commit()
        image_id = image.id
        screened = await run_screening_cycle(
            db,
            _cycle_settings(),
            storage,
            ProviderRotation([CountingProvider(name="fake")]),
        )
    assert (screened.claimed, screened.healthy) == (1, 1)

    # The real normalization path durably stores its sanitized derivative and
    # eagerly removes the raw object while the form is still live. That eager
    # deletion cannot complete the independent post-expiry obligation.
    assert key not in storage.objects
    assert storage.deleted_permanently == [key]
    async with get_sessionmaker()() as db:
        before_replay = await db.get(ScreeningImage, image_id)
    assert before_replay is not None
    assert before_replay.status == "HEALTHY"
    assert before_replay.normalized_key in storage.objects
    assert before_replay.raw_cleanup_completed_at is None
    screening_retry_marker = before_replay.updated_at

    # The same still-valid form writes the raw key again.
    storage.objects[key] = b"presigned replay after eager deletion"
    assert key in storage.objects

    real_acknowledge = raw_cleanup.acknowledge_raw_cleanup
    fail_once = True

    async def fail_first_ack(*args: object, **kwargs: object) -> bool:
        nonlocal fail_once
        if fail_once:
            fail_once = False
            raise RuntimeError("simulated commit-path outage after object success")
        return await real_acknowledge(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(raw_cleanup, "acknowledge_raw_cleanup", fail_first_ack)
    first_time = cleanup_after + dt.timedelta(seconds=1)
    first = await run_raw_cleanup_batch(
        get_sessionmaker(),
        cast(ScreeningStorage, storage),
        batch_size=1,
        now=first_time,
    )
    assert (first.claimed, first.completed, first.acknowledgement_failed) == (1, 0, 1)
    assert key not in storage.objects
    assert storage.deleted_permanently == [key, key]  # eager delete, then final purge

    async with get_sessionmaker()() as db:
        unacknowledged = await db.get(ScreeningImage, image_id)
    assert unacknowledged is not None
    assert unacknowledged.raw_cleanup_completed_at is None
    assert unacknowledged.raw_cleanup_next_attempt_at == first_time + RAW_CLEANUP_RETRY_BASE

    # After the committed lease/backoff, an empty-key purge is still a valid
    # verified idempotent success and the database acknowledgement completes.
    second = await run_raw_cleanup_batch(
        get_sessionmaker(),
        cast(ScreeningStorage, storage),
        batch_size=1,
        now=first_time + RAW_CLEANUP_RETRY_BASE + dt.timedelta(seconds=1),
    )
    assert (second.claimed, second.completed, second.acknowledgement_failed) == (1, 1, 0)
    assert storage.deleted_permanently == [key, key, key]
    async with get_sessionmaker()() as db:
        completed = await db.get(ScreeningImage, image_id)
    assert completed is not None
    assert completed.raw_cleanup_completed_at is not None
    assert completed.raw_cleanup_next_attempt_at is None
    assert completed.raw_cleanup_attempts == 2
    assert completed.updated_at == screening_retry_marker


async def test_zero_row_ack_is_not_reported_complete_and_retries(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = await owner_with_farm(client, email="raw-zero-row-ack@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    due_at = utcnow() - dt.timedelta(minutes=1)
    key = f"raw/{farm_id}/2026-10-04/BREEDING/zero-row-ack.jpg"
    image_id = await _insert_due_image(
        farm_id=farm_id,
        key=key,
        due_at=due_at,
        status="HEALTHY",
        direct_registration=False,
    )
    storage = ReplayableCleanupStorage()
    storage.objects[key] = b"raw bytes"
    real_acknowledge = raw_cleanup.acknowledge_raw_cleanup

    async def acknowledge_nothing(*_args: object, **_kwargs: object) -> bool:
        return False

    monkeypatch.setattr(raw_cleanup, "acknowledge_raw_cleanup", acknowledge_nothing)
    first_time = utcnow()
    first = await run_raw_cleanup_batch(
        get_sessionmaker(),
        cast(ScreeningStorage, storage),
        batch_size=1,
        now=first_time,
    )
    assert (first.claimed, first.completed, first.acknowledgement_failed) == (1, 0, 1)
    async with get_sessionmaker()() as db:
        still_pending = await db.get(ScreeningImage, image_id)
    assert still_pending is not None
    assert still_pending.raw_cleanup_completed_at is None
    assert still_pending.raw_cleanup_next_attempt_at == first_time + RAW_CLEANUP_RETRY_BASE

    monkeypatch.setattr(raw_cleanup, "acknowledge_raw_cleanup", real_acknowledge)
    second = await run_raw_cleanup_batch(
        get_sessionmaker(),
        cast(ScreeningStorage, storage),
        batch_size=1,
        now=first_time + RAW_CLEANUP_RETRY_BASE + dt.timedelta(seconds=1),
    )
    assert (second.claimed, second.completed, second.acknowledgement_failed) == (1, 1, 0)
    assert storage.purge_calls == [key, key]


async def test_zero_row_progress_checkpoint_uses_committed_claim_lease(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = await owner_with_farm(client, email="raw-zero-row-progress@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    due_at = utcnow() - dt.timedelta(minutes=1)
    key = f"raw/{farm_id}/2026-10-04/BREEDING/zero-row-progress.jpg"
    image_id = await _insert_due_image(
        farm_id=farm_id,
        key=key,
        due_at=due_at,
        status="HEALTHY",
        direct_registration=False,
    )
    storage = MultiPageCleanupStorage(progress_pages=1)
    storage.objects[key] = b"raw bytes"
    real_record_progress = raw_cleanup.record_raw_cleanup_progress

    async def record_no_progress(*_args: object, **_kwargs: object) -> bool:
        return False

    monkeypatch.setattr(raw_cleanup, "record_raw_cleanup_progress", record_no_progress)
    first_time = utcnow()
    first = await run_raw_cleanup_batch(
        get_sessionmaker(),
        cast(ScreeningStorage, storage),
        batch_size=1,
        now=first_time,
    )
    assert (
        first.claimed,
        first.completed,
        first.in_progress,
        first.acknowledgement_failed,
    ) == (1, 0, 1, 1)
    async with get_sessionmaker()() as db:
        still_leased = await db.get(ScreeningImage, image_id)
    assert still_leased is not None
    assert still_leased.raw_cleanup_attempts == 1
    assert still_leased.raw_cleanup_completed_at is None
    assert still_leased.raw_cleanup_next_attempt_at == first_time + RAW_CLEANUP_RETRY_BASE

    monkeypatch.setattr(raw_cleanup, "record_raw_cleanup_progress", real_record_progress)
    second = await run_raw_cleanup_batch(
        get_sessionmaker(),
        cast(ScreeningStorage, storage),
        batch_size=1,
        now=first_time + RAW_CLEANUP_RETRY_BASE + dt.timedelta(seconds=1),
    )
    assert (second.claimed, second.completed, second.acknowledgement_failed) == (1, 1, 0)
    assert storage.purge_calls == [key, key]


async def test_zero_row_failure_checkpoint_uses_committed_claim_lease(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = await owner_with_farm(client, email="raw-zero-row-failure@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    due_at = utcnow() - dt.timedelta(minutes=1)
    key = f"raw/{farm_id}/2026-10-04/BREEDING/zero-row-failure.jpg"
    image_id = await _insert_due_image(
        farm_id=farm_id,
        key=key,
        due_at=due_at,
        status="HEALTHY",
        direct_registration=False,
    )
    storage = FailingCleanupStorage()
    real_record_failure = raw_cleanup.record_raw_cleanup_failure

    async def record_no_failure(*_args: object, **_kwargs: object) -> bool:
        return False

    monkeypatch.setattr(raw_cleanup, "record_raw_cleanup_failure", record_no_failure)
    first_time = utcnow()
    first = await run_raw_cleanup_batch(
        get_sessionmaker(),
        cast(ScreeningStorage, storage),
        batch_size=1,
        now=first_time,
    )
    assert (first.claimed, first.failed, first.acknowledgement_failed) == (1, 1, 1)
    async with get_sessionmaker()() as db:
        still_leased = await db.get(ScreeningImage, image_id)
    assert still_leased is not None
    assert still_leased.raw_cleanup_attempts == 1
    assert still_leased.raw_cleanup_last_error is None
    assert still_leased.raw_cleanup_next_attempt_at == first_time + RAW_CLEANUP_RETRY_BASE

    monkeypatch.setattr(raw_cleanup, "record_raw_cleanup_failure", real_record_failure)
    second = await run_raw_cleanup_batch(
        get_sessionmaker(),
        cast(ScreeningStorage, storage),
        batch_size=1,
        now=first_time + RAW_CLEANUP_RETRY_BASE + dt.timedelta(seconds=1),
    )
    assert (second.claimed, second.failed, second.acknowledgement_failed) == (1, 1, 0)
    async with get_sessionmaker()() as db:
        recorded = await db.get(ScreeningImage, image_id)
    assert recorded is not None
    assert recorded.raw_cleanup_attempts == 2
    assert recorded.raw_cleanup_last_error == "raw object permanent purge failed"


async def test_due_claims_skip_rows_locked_by_another_dispatcher(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client, email="raw-cleanup-locks@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    due_at = utcnow() - dt.timedelta(minutes=1)
    first_id = await _insert_due_image(
        farm_id=farm_id,
        key=f"raw/{farm_id}/2026-10-04/BREEDING/locked.jpg",
        due_at=due_at,
        status="SKIPPED",
        error="presigned upload never arrived (URL expired)",
    )
    second_id = await _insert_due_image(
        farm_id=farm_id,
        key=f"raw/{farm_id}/2026-10-04/BREEDING/free.jpg",
        due_at=due_at,
        status="SKIPPED",
        error="presigned upload never arrived (URL expired)",
    )

    session_factory = get_sessionmaker()
    async with session_factory() as lock_owner, session_factory() as contender:
        held = await claim_raw_cleanup_batch(lock_owner, batch_size=1, now=utcnow())
        other = await claim_raw_cleanup_batch(contender, batch_size=1, now=utcnow())
        assert [held[0].image_id, other[0].image_id] == [first_id, second_id]
        await contender.rollback()
        await lock_owner.rollback()


async def test_purge_failure_log_redacts_object_key_and_provider_detail(
    client: httpx.AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    headers = await owner_with_farm(client, email="raw-cleanup-log@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    key = f"raw/{farm_id}/2026-10-04/BREEDING/private-location.jpg"
    image_id = await _insert_due_image(
        farm_id=farm_id,
        key=key,
        due_at=utcnow() - dt.timedelta(minutes=1),
        status="SKIPPED",
        error="photo bytes could not be processed (INVALID_IMAGE)",
    )
    screening_retry_marker = utcnow() - dt.timedelta(hours=2)
    async with get_sessionmaker()() as db:
        await db.execute(
            update(ScreeningImage)
            .where(ScreeningImage.id == image_id)
            .values(updated_at=screening_retry_marker)
        )
        await db.commit()
    caplog.set_level(logging.WARNING, logger="app.services.screening.raw_cleanup")
    summary = await run_raw_cleanup_batch(
        get_sessionmaker(),
        cast(ScreeningStorage, FailingCleanupStorage()),
        batch_size=1,
        now=utcnow(),
    )
    assert (summary.claimed, summary.failed) == (1, 1)
    assert "reason=PURGE_FAILED" in caplog.text
    assert key not in caplog.text
    assert "s3.internal.invalid" not in caplog.text
    assert "top-secret" not in caplog.text
    async with get_sessionmaker()() as db:
        image = await db.get(ScreeningImage, image_id)
    assert image is not None
    assert image.raw_cleanup_last_error == "raw object permanent purge failed"
    assert image.updated_at == screening_retry_marker


async def test_multi_page_purge_uses_short_nonfailure_continuations(
    client: httpx.AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    headers = await owner_with_farm(client, email="raw-cleanup-pages@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    key = f"raw/{farm_id}/2026-10-04/BREEDING/many-versions.jpg"
    image_id = await _insert_due_image(
        farm_id=farm_id,
        key=key,
        due_at=utcnow() - dt.timedelta(minutes=1),
        status="HEALTHY",
        direct_registration=False,
    )
    storage = MultiPageCleanupStorage(progress_pages=2)
    storage.objects[key] = b"newest of many versions"
    caplog.set_level(logging.WARNING, logger="app.services.screening.raw_cleanup")

    attempt_time = utcnow()
    for expected_calls in (1, 2):
        summary = await run_raw_cleanup_batch(
            get_sessionmaker(),
            cast(ScreeningStorage, storage),
            batch_size=1,
            now=attempt_time,
        )
        assert (summary.claimed, summary.in_progress, summary.failed) == (1, 1, 0)
        assert len(storage.purge_calls) == expected_calls
        async with get_sessionmaker()() as db:
            continuing = await db.get(ScreeningImage, image_id)
        assert continuing is not None
        assert continuing.raw_cleanup_attempts == 0
        assert continuing.raw_cleanup_last_error is None
        assert continuing.raw_cleanup_completed_at is None
        assert continuing.raw_cleanup_next_attempt_at is not None
        assert continuing.raw_cleanup_next_attempt_at >= attempt_time + RAW_CLEANUP_PROGRESS_RETRY
        attempt_time = continuing.raw_cleanup_next_attempt_at + dt.timedelta(seconds=1)

    completed_summary = await run_raw_cleanup_batch(
        get_sessionmaker(),
        cast(ScreeningStorage, storage),
        batch_size=1,
        now=attempt_time,
    )
    assert (
        completed_summary.claimed,
        completed_summary.completed,
        completed_summary.in_progress,
        completed_summary.failed,
    ) == (1, 1, 0, 0)
    assert storage.purge_calls == [key, key, key]
    assert key not in storage.objects
    assert "PURGE_FAILED" not in caplog.text
    async with get_sessionmaker()() as db:
        completed = await db.get(ScreeningImage, image_id)
    assert completed is not None
    assert completed.raw_cleanup_attempts == 1
    assert completed.raw_cleanup_completed_at is not None


async def test_retention_tombstone_transfers_cleanup_ownership(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client, email="raw-cleanup-tombstone@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    key = f"raw/{farm_id}/2026-10-04/BREEDING/retention-owned.jpg"
    image_id = await _insert_due_image(
        farm_id=farm_id,
        key=key,
        due_at=utcnow() - dt.timedelta(minutes=1),
        status="HEALTHY",
        direct_registration=False,
    )
    async with get_sessionmaker()() as db:
        image = await db.get(ScreeningImage, image_id)
        assert image is not None
        image.retention_tombstoned_at = utcnow()
        await db.commit()
    storage = ReplayableCleanupStorage()
    storage.objects[key] = b"retention saga owns this manifest"
    summary = await run_raw_cleanup_batch(
        get_sessionmaker(),
        cast(ScreeningStorage, storage),
        batch_size=1,
        now=utcnow(),
    )
    assert summary.claimed == 0
    assert key in storage.objects


async def test_main_lifecycle_cleanup_loop_runs_immediately_and_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = asyncio.Event()
    sentinel_factory = object()
    sentinel_storage = cast(ScreeningStorage, ReplayableCleanupStorage())
    calls: list[tuple[object, ScreeningStorage, int]] = []

    async def fake_run(
        session_factory: object,
        storage: ScreeningStorage,
        *,
        batch_size: int,
    ) -> RawCleanupSummary:
        calls.append((session_factory, storage, batch_size))
        called.set()
        return RawCleanupSummary()

    monkeypatch.setattr(main_module, "get_settings", object)
    monkeypatch.setattr(main_module, "get_sessionmaker", lambda: sentinel_factory)
    monkeypatch.setattr(main_module, "storage_for_settings", lambda _settings: sentinel_storage)
    monkeypatch.setattr(main_module, "run_raw_cleanup_batch", fake_run)
    worker = asyncio.create_task(
        main_module._screening_raw_cleanup_loop(
            interval_seconds=3_600,
            batch_size=7,
            max_batches=2,
        )
    )
    try:
        await asyncio.wait_for(called.wait(), timeout=2)
        assert calls == [(sentinel_factory, sentinel_storage, 7)]
    finally:
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker


async def test_cancelling_active_cleanup_leaves_a_durable_retry_lease(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client, email="raw-cleanup-cancel@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    due_at = utcnow() - dt.timedelta(minutes=1)
    key = f"raw/{farm_id}/2026-10-04/BREEDING/cancelled.jpg"
    image_id = await _insert_due_image(
        farm_id=farm_id,
        key=key,
        due_at=due_at,
        status="HEALTHY",
        direct_registration=False,
    )
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    class BlockingCleanupStorage(ReplayableCleanupStorage):
        def delete_permanently(self, keys: Sequence[str]) -> None:
            for object_key in keys:
                self.purge_calls.append(object_key)
            entered.set()
            try:
                assert release.wait(timeout=2)
                for object_key in keys:
                    self.objects.pop(object_key, None)
            finally:
                finished.set()

    storage = BlockingCleanupStorage()
    storage.objects[key] = b"raw bytes"
    claim_time = utcnow()
    cleanup = asyncio.create_task(
        run_raw_cleanup_batch(
            get_sessionmaker(),
            cast(ScreeningStorage, storage),
            batch_size=1,
            now=claim_time,
        )
    )
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        cleanup.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cleanup
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 2)

    async with get_sessionmaker()() as db:
        retryable = await db.get(ScreeningImage, image_id)
    assert retryable is not None
    assert retryable.raw_cleanup_attempts == 1
    assert retryable.raw_cleanup_completed_at is None
    assert retryable.raw_cleanup_next_attempt_at == claim_time + RAW_CLEANUP_RETRY_BASE
    assert storage.purge_calls == [key]


async def test_cleanup_due_index_is_partial_and_ordered() -> None:
    async with get_sessionmaker()() as db:
        definition = (
            await db.execute(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE schemaname = current_schema() "
                    "AND indexname = 'ix_screening_images_raw_cleanup_due'"
                )
            )
        ).scalar_one()
    normalized = " ".join(str(definition).lower().split())
    assert "raw_cleanup_next_attempt_at" in normalized
    assert "raw_cleanup_completed_at is null" in normalized


def test_fd_migration_backfills_historical_rows_conservatively() -> None:
    migration = (
        screening_api.__file__  # anchor the installed checkout, not process CWD
    )
    assert migration is not None
    migration_path = (
        Path(migration).parents[2]
        / "alembic"
        / "versions"
        / "fd4e5f6a7b8c_screening_retention_deletion_saga.py"
    )
    source = migration_path.read_text()
    assert "created_at + interval '25 hours'" in source
    assert "status IN ('PENDING', 'PROCESSING', 'ERROR', 'FLAGGED')" in source
    assert "screening_attempts < 5" in source
    assert "WHERE raw_cleanup_after IS NULL" not in source
    assert (
        source.index('sa.Column("raw_cleanup_after"')
        < source.index("\n    _scope_screening_updated_at_trigger()\n")
        < source.index("UPDATE screening_images")
        < source.index('"raw_cleanup_after",\n        nullable=False')
    )
    assert '"ix_screening_images_raw_cleanup_due"' not in source
    assert 'sa.Column("retention_tombstoned_at"' in source


async def test_fd_downgrade_refuses_to_discard_outstanding_raw_cleanup(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client, email="raw-cleanup-downgrade@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    image_id = await _insert_due_image(
        farm_id=farm_id,
        key=f"raw/{farm_id}/2026-10-04/BREEDING/downgrade.jpg",
        due_at=utcnow() - dt.timedelta(minutes=1),
        status="HEALTHY",
        direct_registration=False,
    )

    await get_engine().dispose()
    try:
        refused = await _alembic_current_database("downgrade", "fc3d4e5f6a7b")
        assert refused.returncode != 0
        assert (
            "Cannot downgrade: raw screening cleanup obligations are outstanding" in refused.stderr
        )

        # Once the verified purge has an acknowledgement, the raw-cleanup
        # state no longer needs to survive a downgrade. (No retention intent
        # exists in this fixture.)
        async with get_sessionmaker()() as db:
            image = await db.get(ScreeningImage, image_id)
            assert image is not None
            image.raw_cleanup_attempts = 1
            image.raw_cleanup_completed_at = utcnow()
            image.raw_cleanup_next_attempt_at = None
            await db.commit()
        await get_engine().dispose()
        downgraded = await _alembic_current_database("downgrade", "fc3d4e5f6a7b")
        assert downgraded.returncode == 0, downgraded.stdout + downgraded.stderr
    finally:
        restored = await _alembic_current_database("upgrade", "head")
        assert restored.returncode == 0, restored.stdout + restored.stderr


async def test_fd_upgrade_backfills_form_capable_and_legacy_images_safely() -> None:
    created_at = dt.datetime(2020, 1, 2, 3, 4, 5)
    database_url = f"postgresql://localhost:5432/{TEST_DB}"
    await get_engine().dispose()
    try:
        downgraded = await _alembic_current_database("downgrade", "fc3d4e5f6a7b")
        assert downgraded.returncode == 0, downgraded.stdout + downgraded.stderr

        connection = await asyncpg.connect(database_url)
        try:
            user_id = await connection.fetchval(
                """
                INSERT INTO users (email, password_hash, created_at)
                VALUES ('raw-backfill@example.test', 'not-used', $1)
                RETURNING id
                """,
                created_at,
            )
            farm_id = await connection.fetchval(
                """
                INSERT INTO farms (name, owner_id, created_at, updated_at)
                VALUES ('Raw backfill farm', $1, $2, $2)
                RETURNING id
                """,
                user_id,
                created_at,
            )
            active_legacy_key = f"raw/{farm_id}/2020-01-02/BREEDING/active-legacy.jpg"
            form_capable_image_id = await connection.fetchval(
                """
                INSERT INTO screening_images (
                    farm_id, s3_bucket, s3_key, normalized_key, status,
                    screening_attempts, created_at, updated_at
                )
                VALUES ($1, 'goat-photos', $2, $3, 'HEALTHY', 0, $4, $4)
                RETURNING id
                """,
                farm_id,
                f"raw/{farm_id}/2020-01-02/BREEDING/form-capable.jpg",
                f"normalized/{farm_id}/2020-01-02/form-capable.jpg",
                created_at,
            )
            active_legacy_image_id = await connection.fetchval(
                """
                INSERT INTO screening_images (
                    farm_id, bucket, s3_bucket, s3_key, captured_date,
                    status, screening_attempts, created_at, updated_at
                )
                VALUES ($1, 'BREEDING', 'goat-photos', $2, DATE '2020-01-02',
                        'PENDING', 0, $3, $3)
                RETURNING id
                """,
                farm_id,
                active_legacy_key,
                created_at,
            )
            terminal_legacy_image_id = await connection.fetchval(
                """
                INSERT INTO screening_images (
                    farm_id, s3_bucket, s3_key, status, error,
                    screening_attempts, created_at, updated_at
                )
                VALUES ($1, 'goat-photos', $2, 'SKIPPED',
                        'legacy input was rejected', 5, $3, $3)
                RETURNING id
                """,
                farm_id,
                f"raw/{farm_id}/2020-01-02/BREEDING/terminal-legacy.jpg",
                created_at,
            )
        finally:
            await connection.close()

        upgrade_started = utcnow()
        upgraded = await _alembic_current_database("upgrade", "head")
        upgrade_finished = utcnow()
        assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
        connection = await asyncpg.connect(database_url)
        try:
            rows = await connection.fetch(
                """
                SELECT id, raw_cleanup_after, raw_cleanup_next_attempt_at,
                       raw_cleanup_attempts, raw_cleanup_completed_at,
                       raw_cleanup_last_error, retention_tombstoned_at,
                       updated_at
                FROM screening_images
                WHERE id = ANY($1::bigint[])
                ORDER BY id
                """,
                [
                    form_capable_image_id,
                    active_legacy_image_id,
                    terminal_legacy_image_id,
                ],
            )
        finally:
            await connection.close()
        assert len(rows) == 3
        by_id = {int(row["id"]): row for row in rows}
        form_capable = by_id[form_capable_image_id]
        active_legacy = by_id[active_legacy_image_id]
        terminal_legacy = by_id[terminal_legacy_image_id]
        expected_due = created_at + dt.timedelta(hours=25)
        assert form_capable["raw_cleanup_after"] == expected_due
        assert form_capable["raw_cleanup_next_attempt_at"] == expected_due
        # Active tokenless legacy intake still needs its raw-only input, so it
        # gets a bounded processing grace. A terminal legacy row has neither a
        # reusable form nor remaining work and is due immediately.
        assert (
            upgrade_started + dt.timedelta(hours=25)
            <= active_legacy["raw_cleanup_after"]
            <= upgrade_finished + dt.timedelta(hours=25)
        )
        assert active_legacy["raw_cleanup_next_attempt_at"] == active_legacy["raw_cleanup_after"]
        assert upgrade_started <= terminal_legacy["raw_cleanup_after"] <= upgrade_finished
        assert (
            terminal_legacy["raw_cleanup_next_attempt_at"] == terminal_legacy["raw_cleanup_after"]
        )
        for row in rows:
            assert row["raw_cleanup_attempts"] == 0
            assert row["raw_cleanup_completed_at"] is None
            assert row["raw_cleanup_last_error"] is None
            assert row["retention_tombstoned_at"] is None
            # Privacy bookkeeping must not make a six-year-old processing
            # lease appear freshly alive.
            assert row["updated_at"] == created_at

        # Lifecycle startup launches the cleanup and screening loops as
        # independent tasks. Exercise that ordering directly: even while the
        # cleanup task drains already-due legacy obligations, the worker must
        # retain and consume this active row's raw-only input.
        storage = FakeStorage(objects={active_legacy_key: _jpeg_bytes(1000, 2000)})

        async def screen_active_legacy() -> CycleSummary:
            async with get_sessionmaker()() as db:
                return await run_screening_cycle(
                    db,
                    _cycle_settings(),
                    storage,
                    ProviderRotation([CountingProvider(name="fake")]),
                )

        cleanup_summary, screening_summary = await asyncio.gather(
            run_raw_cleanup_batch(
                get_sessionmaker(),
                storage,
                batch_size=10,
                now=upgrade_finished,
            ),
            screen_active_legacy(),
        )
        assert cleanup_summary.claimed == 2
        assert cleanup_summary.completed == 2
        assert screening_summary.claimed == 1
        assert screening_summary.healthy == 1
        async with get_sessionmaker()() as db:
            active_image = await db.get(ScreeningImage, active_legacy_image_id)
            assert active_image is not None
            assert active_image.status == "HEALTHY"
            assert active_image.raw_cleanup_completed_at is None
            assert active_image.raw_cleanup_next_attempt_at == active_image.raw_cleanup_after
    finally:
        restored = await _alembic_current_database("upgrade", "head")
        assert restored.returncode == 0, restored.stdout + restored.stderr
