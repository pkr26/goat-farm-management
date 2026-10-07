"""Public screening intake conserves fixed farm capacity and upload lifetime."""

from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

from app.api import screening as screening_api
from app.db import get_sessionmaker
from app.models import ScreeningBatch, ScreeningImage
from app.services.screening.s3 import ScreeningStorageError
from app.utils import utcnow

from .conftest import owner_with_farm
from .test_screening import _cycle_settings


def _photo(batch_id: int) -> dict[str, Any]:
    return {
        "batch_id": batch_id,
        "bucket": "RESTING",
        "file_name": "camera.jpg",
        "content_type": "image/jpeg",
        "file_size": 1_024,
    }


async def test_farm_admits_exactly_five_live_walkthroughs(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client)
    monkeypatch.setattr(screening_api, "get_settings", lambda: _cycle_settings())
    for _ in range(5):
        response = await client.post("/api/screening/batches", headers=owner)
        assert response.status_code == 201, response.text
    rejected = await client.post("/api/screening/batches", headers=owner)
    assert rejected.status_code == 409, rejected.text
    assert "open screening walkthroughs" in rejected.json()["detail"]
    async with get_sessionmaker()() as db:
        count = await db.scalar(
            select(func.count())
            .select_from(ScreeningBatch)
            .where(ScreeningBatch.farm_id == int(owner["X-Farm-Id"]))
        )
        assert count == 5


@pytest.mark.parametrize("quota", ["walkthrough", "farm"])
async def test_last_available_registration_consumes_the_literal_photo_capacity(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, quota: str
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    monkeypatch.setattr(screening_api, "get_settings", lambda: _cycle_settings())
    created = await client.post("/api/screening/batches", headers=owner)
    assert created.status_code == 201, created.text
    batch_id = int(created.json()["id"])
    retained = 99 if quota == "walkthrough" else 249
    async with get_sessionmaker()() as db:
        # Genuine constrained pre-registrations retain pending photos from
        # before this request. Older farm rows may have no walkthrough id.
        db.add_all(
            [
                ScreeningImage(
                    farm_id=farm_id,
                    batch_id=batch_id if quota == "walkthrough" else None,
                    s3_bucket="goat-photos",
                    s3_key=f"raw/{farm_id}/2026-01-01/retained-capacity-{i}.jpg",
                    status="PENDING",
                )
                for i in range(retained)
            ]
        )
        await db.commit()
    admitted = await client.post("/api/screening/uploads", headers=owner, json=_photo(batch_id))
    assert admitted.status_code == 201, admitted.text
    refused = await client.post("/api/screening/uploads", headers=owner, json=_photo(batch_id))
    assert refused.status_code == 409, refused.text
    async with get_sessionmaker()() as db:
        total = await db.scalar(
            select(func.count())
            .select_from(ScreeningImage)
            .where(ScreeningImage.farm_id == farm_id)
        )
        assert total == retained + 1


@pytest.mark.parametrize(
    ("age", "accepted"),
    [
        (timedelta(hours=24, minutes=30), True),
        (timedelta(hours=25), True),
        (timedelta(hours=25, minutes=30), False),
    ],
    ids=["inside-grace", "last-instant", "expired"],
)
async def test_walkthrough_upload_window_includes_the_exact_25_hour_boundary(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, age: timedelta, accepted: bool
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    monkeypatch.setattr(screening_api, "get_settings", lambda: _cycle_settings())
    created = await client.post("/api/screening/batches", headers=owner)
    assert created.status_code == 201, created.text
    batch_id = int(created.json()["id"])
    instant = utcnow()
    async with get_sessionmaker()() as db:
        batch = await db.get(ScreeningBatch, batch_id)
        assert batch is not None
        batch.created_at = instant - age
        await db.commit()
    monkeypatch.setattr(screening_api, "utcnow", lambda: instant)
    response = await client.post("/api/screening/uploads", headers=owner, json=_photo(batch_id))
    assert response.status_code == (201 if accepted else 409), response.text
    async with get_sessionmaker()() as db:
        count = await db.scalar(
            select(func.count())
            .select_from(ScreeningImage)
            .where(ScreeningImage.farm_id == farm_id)
        )
        assert count == int(accepted)


async def test_exactly_25_hour_old_walkthroughs_still_consume_the_farm_open_quota(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    monkeypatch.setattr(screening_api, "get_settings", lambda: _cycle_settings())
    instant = utcnow()
    for _ in range(5):
        created = await client.post("/api/screening/batches", headers=owner)
        assert created.status_code == 201, created.text
    async with get_sessionmaker()() as db:
        for batch in (
            await db.execute(select(ScreeningBatch).where(ScreeningBatch.farm_id == farm_id))
        ).scalars():
            batch.created_at = instant - timedelta(hours=25)
        await db.commit()
    monkeypatch.setattr(screening_api, "utcnow", lambda: instant)
    refused = await client.post("/api/screening/batches", headers=owner)
    assert refused.status_code == 409, refused.text


async def test_absent_walkthrough_upload_returns_the_public_not_found_contract(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client)
    monkeypatch.setattr(screening_api, "get_settings", lambda: _cycle_settings())
    response = await client.post(
        "/api/screening/uploads", headers=owner, json=_photo(2_147_483_647)
    )
    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Screening batch not found"


async def test_typed_presign_refusal_returns_retryable_503_and_rolls_back_registration(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    monkeypatch.setattr(screening_api, "get_settings", lambda: _cycle_settings())
    created = await client.post("/api/screening/batches", headers=owner)
    assert created.status_code == 201, created.text
    batch_id = int(created.json()["id"])

    class RefusedStorage:
        def presign_post(
            self, key: str, *, content_type: str, upload_token: str, max_bytes: int
        ) -> None:
            raise ScreeningStorageError("the genuine signing service could not prepare this form")

    with monkeypatch.context() as failed_store:
        failed_store.setattr(
            screening_api, "storage_for_settings", lambda settings: RefusedStorage()
        )
        response = await client.post(
            "/api/screening/uploads",
            headers={**owner, "Idempotency-Key": "refused-form"},
            json=_photo(batch_id),
        )
        assert response.status_code == 503, response.text
        assert "try again later" in response.json()["detail"]
    async with get_sessionmaker()() as db:
        count = await db.scalar(
            select(func.count())
            .select_from(ScreeningImage)
            .where(ScreeningImage.farm_id == farm_id)
        )
        assert count == 0
    retry = await client.post(
        "/api/screening/uploads",
        headers={**owner, "Idempotency-Key": "refused-form"},
        json=_photo(batch_id),
    )
    assert retry.status_code == 201, retry.text


@pytest.mark.parametrize(
    ("path", "default", "maximum"),
    [
        ("/api/screening/images", 25, 200),
        ("/api/screening/batches", 10, 50),
        ("/api/screening/export", 5_000, 5_000),
    ],
    ids=["review-queue", "walkthrough-page", "training-export"],
)
async def test_published_page_contract_keeps_independent_default_and_ceiling(
    client: httpx.AsyncClient, path: str, default: int, maximum: int
) -> None:
    response = await client.get("/openapi.json")
    assert response.status_code == 200, response.text
    params = response.json()["paths"][path]["get"]["parameters"]
    schema = next(
        item["schema"] for item in params if item["name"] == "limit" and item["in"] == "query"
    )
    assert schema["default"] == default
    assert schema["minimum"] == 1
    assert schema["maximum"] == maximum
