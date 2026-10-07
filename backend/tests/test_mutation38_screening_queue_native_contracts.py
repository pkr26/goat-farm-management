"""Native queue boundaries preserve constrained retained upload/clinical facts.

The scheduler helper accepts an explicit UTC clock and arbitrary positive
native limit. Its2000 candidate fence is tested through that declared helper;
ordinary typed application runtimes separately cap per-cycle limits at1000.
"""

import asyncio
import datetime as dt
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_sessionmaker
from app.models import ScreeningFinding, ScreeningImage, ScreeningRun
from app.models.screening import ScreeningCrop
from app.services.screening import pipeline

from .conftest import owner_with_farm

NOW = dt.datetime(2026, 1, 13, 12)
HOUR = dt.timedelta(hours=1)
STALE = dt.timedelta(minutes=30)


async def _image(
    db: AsyncSession,
    farm_id: int,
    name: str,
    *,
    status: str = "PENDING",
    created: dt.datetime = NOW,
    updated: dt.datetime = NOW,
    token: bool = False,
    attempts: int = 0,
    next_at: dt.datetime | None = None,
) -> ScreeningImage:
    image = ScreeningImage(
        farm_id=farm_id,
        s3_bucket="native-retained-photos",
        s3_key=f"raw/{farm_id}/2026-01-13/BREEDING/{name}.jpg",
        status=status,
        error="retained object unavailable" if status == "ERROR" else None,
        created_at=min(created, updated),
        updated_at=updated,
        upload_token=uuid4().hex if token else None,
        upload_content_type="image/jpeg" if token else None,
        screening_attempts=attempts,
        next_attempt_at=next_at,
    )
    db.add(image)
    await db.flush()
    return image


async def _whole_frame_flag_with_crop(
    db: AsyncSession,
    image: ScreeningImage,
    crop_status: str,
) -> None:
    """A real retained whole-frame concern remains visible with a crop retry.

    Current detection participates in aggregation with the whole-frame safety
    verdict. Hence a flagged parent need not have a flagged individual crop.
    """
    run = ScreeningRun(
        farm_id=image.farm_id,
        image_id=image.id,
        stage="GATE",
        run_status="OK",
        verdict="flagged",
        provider="retained-camera",
        model="retained-vet-model",
        prompt_version="retained",
        latency_ms=7,
        detail={"coverage_safety_pass": True},
        created_at=image.updated_at,
    )
    db.add(run)
    await db.flush()
    db.add(
        ScreeningFinding(
            farm_id=image.farm_id,
            run_id=run.id,
            label="Visible whole-frame lesion",
            status="PENDING_REVIEW",
            created_at=image.updated_at,
        )
    )
    db.add(
        ScreeningCrop(
            farm_id=image.farm_id,
            image_id=image.id,
            crop_index=0,
            box_x=10,
            box_y=10,
            box_w=500,
            box_h=500,
            status=crop_status,
            error="retained crop provider failure" if crop_status == "ERROR" else None,
            created_at=image.updated_at,
        )
    )
    await db.flush()


def test_new_native_cycle_summary_has_no_phantom_claims() -> None:
    assert pipeline.CycleSummary().claimed == 0


@pytest.mark.parametrize("bucket", [None, "BREEDING"], ids=["whole-farm", "herd-bucket"])
def test_retained_raw_key_diagnostic_preserves_its_real_capture_date(bucket: str | None) -> None:
    segment = "" if bucket is None else f"{bucket}/"
    try:
        parsed = pipeline.parse_raw_key(f"raw/123/2026-01-13/{segment}photo.jpg", "raw")
    except (TypeError, ValueError) as exc:
        pytest.fail(f"A valid retained diagnostic raw key must parse: {exc}")
    assert parsed is not None
    assert parsed.farm_id == 123 and parsed.captured_date == dt.date(2026, 1, 13)
    assert parsed.bucket == bucket


async def test_a_missing_live_upload_refunds_the_claim_and_reprobes_in_five_minutes(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    monkeypatch.setattr(pipeline, "utcnow", lambda: NOW)
    async with get_sessionmaker()() as db:
        image = await _image(db, farm_id, "slow-phone", status="PROCESSING", token=True, attempts=1)
        await db.commit()
        summary = pipeline.CycleSummary()
        pipeline._note_object_absent(image, summary)
        await db.commit()
        await db.refresh(image)
        assert image.status == "PENDING" and image.screening_attempts == 0
        assert image.error is None and image.next_attempt_at == NOW + dt.timedelta(minutes=5)
        assert summary.errors == 0 and len(summary.notes) == 1


@pytest.mark.parametrize(
    "case, expected",
    [
        ("pending-horizon", True),
        ("pending-next-at", True),
        ("processing-equality", False),
        ("flagged-equality", False),
        ("flagged-recent", False),
        ("flagged-old-healthy-crop", False),
        ("flagged-old-error-crop", True),
        ("error-recent", False),
    ],
    ids=[
        "pending-grace",
        "due-reprobe",
        "live-processing",
        "crop-backoff-equality",
        "crop-backoff-live",
        "no-crop-failure",
        "real-crop-retry",
        "error-backoff-live",
    ],
)
async def test_native_queue_respects_upload_lease_and_crop_retry_boundaries(
    client: httpx.AsyncClient,
    case: str,
    expected: bool,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        if case == "pending-horizon":
            image = await _image(db, farm_id, case, created=NOW - HOUR, token=True)
        elif case == "pending-next-at":
            image = await _image(db, farm_id, case, created=NOW - STALE, token=True, next_at=NOW)
        elif case == "processing-equality":
            image = await _image(
                db, farm_id, case, status="PROCESSING", updated=NOW - STALE, attempts=1
            )
        elif case == "error-recent":
            image = await _image(
                db,
                farm_id,
                case,
                status="ERROR",
                updated=NOW - HOUR + dt.timedelta(seconds=1),
                attempts=1,
            )
        else:
            updated = NOW - HOUR
            if case == "flagged-recent":
                updated += dt.timedelta(seconds=1)
            elif case.startswith("flagged-old"):
                updated -= dt.timedelta(seconds=1)
            image = await _image(db, farm_id, case, status="FLAGGED", updated=updated, attempts=1)
            await _whole_frame_flag_with_crop(
                db, image, "HEALTHY" if case == "flagged-old-healthy-crop" else "ERROR"
            )
        image_id, old_status, old_attempts = image.id, image.status, image.screening_attempts
        await db.commit()
        try:
            rows, errors, flags = await pipeline._claim_retry_rows(db, 10, NOW, HOUR, STALE, 0)
        except (AttributeError, TypeError, ValueError) as exc:
            pytest.fail(f"A valid constrained queue cohort must return its claims: {exc}")
        assert [row.id for row in rows] == ([image_id] if expected else [])
        assert errors == 0
        assert flags == (1 if expected and old_status == "FLAGGED" else 0)
        await db.refresh(image)
        assert image.status == ("PROCESSING" if expected else old_status)
        assert image.screening_attempts == old_attempts + (1 if expected else 0)
        if case.startswith("flagged"):
            assert await db.scalar(select(func.count()).select_from(ScreeningFinding)) == 1


@pytest.mark.parametrize("kind", ["upload", "processing"], ids=["upload-grace", "processing-lease"])
async def test_maintenance_keeps_rows_exactly_at_their_deadline(
    client: httpx.AsyncClient,
    kind: str,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        if kind == "upload":
            image = await _image(db, farm_id, "expiry-equality", created=NOW - HOUR, token=True)
            await db.commit()
            count = await pipeline._expire_abandoned_uploads(db, NOW, HOUR)
            await db.commit()
            await db.refresh(image)
            assert image.status == "PENDING" and image.error is None
        else:
            image = await _image(
                db,
                farm_id,
                "processing-equality",
                status="PROCESSING",
                updated=NOW - STALE,
                attempts=5,
            )
            await db.commit()
            count = await pipeline._terminate_budget_exhausted_processing(db, NOW, STALE)
            await db.commit()
            await db.refresh(image)
            assert image.status == "PROCESSING" and image.error is None
        assert count == 0


@pytest.mark.parametrize("kind", ["upload", "processing"], ids=["upload-sweep", "processing-sweep"])
async def test_maintenance_skips_an_owned_row_lease_and_retires_the_free_peer(
    client: httpx.AsyncClient,
    kind: str,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        status = "PENDING" if kind == "upload" else "PROCESSING"
        first = await _image(
            db,
            farm_id,
            "owned",
            status=status,
            token=kind == "upload",
            attempts=0 if kind == "upload" else 5,
            created=NOW - 2 * HOUR,
            updated=NOW - 2 * HOUR,
        )
        second = await _image(
            db,
            farm_id,
            "free",
            status=status,
            token=kind == "upload",
            attempts=0 if kind == "upload" else 5,
            created=NOW - HOUR - dt.timedelta(seconds=1),
            updated=NOW - HOUR - dt.timedelta(seconds=1),
        )
        first_id, second_id = first.id, second.id
        await db.commit()
    async with get_sessionmaker()() as holder, get_sessionmaker()() as observer:
        assert (
            await holder.scalar(
                select(ScreeningImage).where(ScreeningImage.id == first_id).with_for_update()
            )
            is not None
        )
        pid = await holder.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(pid, int)

        async def sweep() -> int:
            async with get_sessionmaker()() as db:
                count = await (
                    pipeline._expire_abandoned_uploads(db, NOW, HOUR)
                    if kind == "upload"
                    else pipeline._terminate_budget_exhausted_processing(db, NOW, STALE)
                )
                await db.commit()
                return count

        request = asyncio.create_task(sweep())
        blocked: list[int] = []
        try:
            deadline = asyncio.get_running_loop().time() + 30
            while not request.done():
                blocked = list(
                    (
                        await observer.execute(
                            text(
                                "SELECT pid FROM pg_stat_activity WHERE datname=current_database() "
                                "AND wait_event_type='Lock' AND :holder=ANY(pg_blocking_pids(pid))"
                            ),
                            {"holder": pid},
                        )
                    ).scalars()
                )
                if blocked:
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError(
                        "Maintenance gave neither completion nor a real owned-row lock witness"
                    )
                await asyncio.sleep(0.02)
            await holder.rollback()
            count = await asyncio.wait_for(request, timeout=30)
        finally:
            await holder.rollback()
            if not request.done():
                request.cancel()
            await asyncio.gather(request, return_exceptions=True)
    assert count == 1 and not blocked
    async with get_sessionmaker()() as db:
        protected = await db.get(ScreeningImage, first_id)
        retired = await db.get(ScreeningImage, second_id)
        assert protected is not None and protected.status == status
        assert retired is not None and retired.status == (
            "SKIPPED" if kind == "upload" else "ERROR"
        )


async def test_native_claim_priority_serves_an_unvisited_farm_before_a_recently_served_farm(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    peer = await owner_with_farm(client, email="recently-served@farm.in")
    first_farm, peer_farm = int(owner["X-Farm-Id"]), int(peer["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        unvisited = await _image(
            db,
            first_farm,
            "unvisited",
            created=NOW - dt.timedelta(days=10),
            updated=NOW - dt.timedelta(minutes=1),
        )
        await _image(
            db,
            peer_farm,
            "completed",
            status="HEALTHY",
            attempts=1,
            created=NOW - dt.timedelta(days=2),
            updated=NOW - 2 * HOUR,
        )
        pending_peer = await _image(
            db, peer_farm, "next", created=NOW - dt.timedelta(days=1), updated=NOW - 2 * HOUR
        )
        first_id, peer_id = unvisited.id, pending_peer.id
        await db.commit()
        rows, errors, flags = await pipeline._claim_retry_rows(db, 1, NOW, HOUR, STALE, 0)
        assert [row.id for row in rows] == [first_id]
        assert errors == flags == 0
        await db.refresh(pending_peer)
        assert pending_peer.id == peer_id and pending_peer.status == "PENDING"


async def test_native_deep_backlog_keeps_the_two_thousand_candidate_fence(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        db.add_all(
            [
                ScreeningImage(
                    farm_id=farm_id,
                    s3_bucket="native-retained-photos",
                    s3_key=f"raw/{farm_id}/2026-01-13/BREEDING/backlog-{index}.jpg",
                    status="PENDING",
                    created_at=NOW,
                    updated_at=NOW,
                )
                for index in range(2001)
            ]
        )
        await db.commit()
        rows, errors, flags = await pipeline._claim_retry_rows(db, 2001, NOW, HOUR, STALE, 0)
        assert len(rows) == 2000 and errors == flags == 0
        assert (
            await db.scalar(
                select(func.count())
                .select_from(ScreeningImage)
                .where(ScreeningImage.status == "PROCESSING")
            )
            == 2000
        )
        assert (
            await db.scalar(
                select(func.count())
                .select_from(ScreeningImage)
                .where(ScreeningImage.status == "PENDING")
            )
            == 1
        )
