"""Clinical history reads distinguish pending evidence and compatible photo leases."""

import asyncio

import httpx
from sqlalchemy import select, text

from app.db import get_sessionmaker
from app.models import ScreeningFinding, ScreeningImage

from .conftest import owner_with_farm
from .test_mutation38_screening_vet_adjudication_contracts import _seed_pending


async def test_pending_finding_has_no_legacy_decision_and_missing_history_is_private(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    await _seed_pending(farm_id, 1)
    response = await client.get("/api/screening/findings/1/reviews", headers=owner)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["finding_id"] == 1 and body["review_revision"] == 0
    assert body["legacy_review"] is False
    assert body["total"] == 0 and body["reviews"] == []
    missing = await client.get("/api/screening/findings/2/reviews", headers=owner)
    assert missing.status_code == 404, missing.text
    assert missing.json()["detail"] == "Screening finding not found"
    async with get_sessionmaker()() as db:
        finding = await db.get(ScreeningFinding, 1)
        assert finding is not None and finding.status == "PENDING_REVIEW"
        assert finding.review_revision == 0 and finding.reviewed_at is None


async def test_review_history_coexists_with_an_independent_shared_photo_reader(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    image_id, _run_id = await _seed_pending(farm_id, 1)
    async with get_sessionmaker()() as reader, get_sessionmaker()() as observer:
        image = await reader.scalar(
            select(ScreeningImage).where(ScreeningImage.id == image_id).with_for_update(read=True)
        )
        assert image is not None
        pid = await reader.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(pid, int)
        request = asyncio.create_task(
            client.get("/api/screening/findings/1/reviews", headers=owner)
        )
        blocked: list[int] = []
        try:
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
                            {"holder": pid},
                        )
                    ).scalars()
                )
                if blocked:
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError(
                        "Review history gave neither response nor an image-reader lock witness"
                    )
                await asyncio.sleep(0.02)
            await reader.rollback()
            response = await asyncio.wait_for(request, timeout=30)
        finally:
            await reader.rollback()
            if not request.done():
                request.cancel()
            await asyncio.gather(request, return_exceptions=True)
    assert response.status_code == 200, response.text
    assert response.json()["finding_id"] == 1
    assert response.json()["legacy_review"] is False and response.json()["reviews"] == []
    assert not blocked, (
        f"An independent compatible photo reader blocked clinical history: {blocked}"
    )
