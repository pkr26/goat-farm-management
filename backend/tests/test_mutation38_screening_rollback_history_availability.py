"""Clinical history remains readable during a later image's remote metadata I/O.

The first image encounters an actual external fault or a real PostgreSQL
constraint failure at its final commit. The latter deliberately adds an
uncommittable pending row through a native session event, never a stored
clinical fact. The following retained whole-frame concern/crop-retry graph
keeps its existing review provenance throughout the genuine worker retry.
"""

import asyncio
import datetime as dt
import threading

import httpx
import pytest
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from app.db import get_sessionmaker
from app.models import ScreeningFinding, ScreeningImage, ScreeningRun
from app.services.screening import pipeline
from app.services.screening.rotation import ProviderRotation
from app.services.screening.s3 import ScreeningObjectInfo

from .conftest import owner_with_farm
from .test_mutation38_screening_queue_native_contracts import (
    NOW,
    _image,
    _whole_frame_flag_with_crop,
)
from .test_screening import CountingProvider, FakeStorage, _cycle_settings, _jpeg_bytes


class PausedLaterMetadata(FakeStorage):
    """Only the external object-store adapter controls the I/O seam."""

    def __init__(self, first_key: str, second_key: str, failure: str) -> None:
        super().__init__(
            objects={first_key: _jpeg_bytes(900, 1400), second_key: _jpeg_bytes(1000, 1500)}
        )
        self.first_key = first_key
        self.second_key = second_key
        self.failure = failure
        self.reached = threading.Event()
        self.release = threading.Event()

    def object_info(self, key: str) -> ScreeningObjectInfo | None:
        if key == self.first_key and self.failure == "external":
            raise RuntimeError("unexpected object-store metadata transport failure")
        if key == self.second_key:
            self.reached.set()
            if not self.release.wait(timeout=60):
                raise TimeoutError("The owned object metadata barrier was not released")
        return super().object_info(key)


@pytest.mark.parametrize("failure", ["external", "commit"], ids=["rollback", "commit-rollback"])
async def test_prior_rollback_does_not_lease_a_later_photos_history_during_remote_head(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    monkeypatch.setattr(pipeline, "utcnow", lambda: NOW)
    async with get_sessionmaker()() as setup:
        first = await _image(
            setup,
            farm_id,
            "earlier-transport",
            created=NOW - dt.timedelta(hours=3),
            updated=NOW - dt.timedelta(hours=3),
        )
        second = await _image(
            setup,
            farm_id,
            "retained-later-concern",
            status="FLAGGED",
            created=NOW - dt.timedelta(hours=2),
            updated=NOW - dt.timedelta(hours=2),
        )
        await _whole_frame_flag_with_crop(setup, second, "ERROR")
        first_id, second_id = first.id, second.id
        first_key, second_key = first.s3_key, second.s3_key
        finding_id = await setup.scalar(
            select(ScreeningFinding.id)
            .join(ScreeningRun, ScreeningRun.id == ScreeningFinding.run_id)
            .where(ScreeningRun.image_id == second_id)
        )
        assert isinstance(finding_id, int)
        await setup.commit()
    storage = PausedLaterMetadata(first_key, second_key, failure)
    poisoned = False

    def fail_the_first_completed_image_commit(session: Session) -> None:
        nonlocal poisoned
        if failure != "commit" or poisoned:
            return
        for value in session.identity_map.values():
            # Passive state avoids any ORM refresh inside this session event.
            if (
                isinstance(value, ScreeningImage)
                and value.__dict__.get("id") == first_id
                and value.__dict__.get("status") == "HEALTHY"
            ):
                poisoned = True
                session.add(
                    ScreeningRun(
                        farm_id=None,
                        image_id=first_id,
                        stage="GATE",
                        run_status="ERROR",
                        provider="uncommittable-fault",
                        model="fault-boundary",
                        prompt_version="fault-boundary",
                        error="Uncommittable pending row tests the real commit rollback boundary",
                    )
                )
                return

    request: asyncio.Task[httpx.Response] | None = None
    blocked: list[int] = []
    async with get_sessionmaker()() as worker, get_sessionmaker()() as observer:
        event.listen(worker.sync_session, "before_commit", fail_the_first_completed_image_commit)
        cycle = asyncio.create_task(
            pipeline.run_screening_cycle(
                worker,
                _cycle_settings(crop_detection=True),
                storage,
                ProviderRotation([CountingProvider(name="history-availability")]),
            )
        )
        try:
            deadline = asyncio.get_running_loop().time() + 30
            while not storage.reached.is_set():
                if cycle.done():
                    await cycle
                    pytest.fail("The genuine later image did not reach its external HEAD request")
                if asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError("The genuine worker did not reach the owned metadata seam")
                await asyncio.sleep(0.02)
            # No session operation is in flight while the worker awaits external
            # metadata. Observe the actual lease owner without rewriting queries.
            worker_pid = await worker.scalar(text("SELECT pg_backend_pid()"))
            assert isinstance(worker_pid, int)
            request = asyncio.create_task(
                client.get(f"/api/screening/findings/{finding_id}/reviews", headers=owner)
            )
            deadline = asyncio.get_running_loop().time() + 30
            while not request.done():
                blocked = list(
                    (
                        await observer.execute(
                            text(
                                "SELECT pid FROM pg_stat_activity "
                                "WHERE datname = current_database() AND wait_event_type = 'Lock' "
                                "AND :holder = ANY(pg_blocking_pids(pid))"
                            ),
                            {"holder": worker_pid},
                        )
                    ).scalars()
                )
                if blocked:
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError("History gave neither a response nor a worker lock witness")
                await asyncio.sleep(0.02)
            storage.release.set()
            response = await asyncio.wait_for(request, timeout=30)
            summary = await asyncio.wait_for(cycle, timeout=30)
        finally:
            storage.release.set()
            if request is not None and not request.done():
                request.cancel()
            if not cycle.done():
                cycle.cancel()
            await asyncio.gather(
                cycle, *([] if request is None else [request]), return_exceptions=True
            )
            event.remove(
                worker.sync_session, "before_commit", fail_the_first_completed_image_commit
            )
            await worker.rollback()
    assert response.status_code == 200, response.text
    assert response.json()["finding_id"] == finding_id
    assert response.json()["legacy_review"] is False and response.json()["reviews"] == []
    assert summary.claimed == 2
    assert not blocked, (
        "The rollback recovery leased a later photo during external metadata I/O "
        f"and blocked its existing clinical history reader: {blocked}"
    )
    assert poisoned is (failure == "commit")
    async with get_sessionmaker()() as check:
        prior = await check.get(ScreeningImage, first_id)
        later = await check.get(ScreeningImage, second_id)
        finding = await check.get(ScreeningFinding, finding_id)
        assert prior is not None and later is not None and finding is not None
        assert prior.status == ("ERROR" if failure == "external" else "PROCESSING")
        assert later.status != "PROCESSING" and later.screening_attempts == 1
        assert finding.status == "PENDING_REVIEW" and finding.review_revision == 0
        assert finding.reviewed_at is None
