"""Positive read contracts fail explicitly before consuming optional wire values."""

import httpx
import pytest
from sqlalchemy import select

from app.api import screening as screening_api
from app.db import get_sessionmaker
from app.models import ScreeningCrop, ScreeningImage, ScreeningRun

from .conftest import owner_with_farm
from .test_mutation38_screening_read_contracts import _run
from .test_screening import _cycle_settings


async def test_committed_gate_is_present_in_its_own_photo_latest_evidence(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        image = ScreeningImage(
            farm_id=farm_id,
            s3_bucket="retained-photos",
            s3_key="raw/positive-latest.jpg",
            status="HEALTHY",
        )
        db.add(image)
        await db.flush()
        run = _run(farm_id, image.id)
        db.add(run)
        await db.commit()
        image_id, run_id = image.id, run.id
    response = await client.get("/api/screening/images", headers=owner)
    assert response.status_code == 200, response.text
    rows = response.json()["images"]
    assert response.json()["total"] == len(rows) == 1
    assert rows[0]["id"] == image_id
    latest = rows[0]["latest_run"]
    assert latest is not None, (
        "A committed model run disappeared from its own farm/photo review row"
    )
    assert latest["id"] == run_id and latest["image_id"] == image_id
    async with get_sessionmaker()() as db:
        assert (await db.scalar(select(ScreeningRun.id).where(ScreeningRun.id == run_id))) == run_id


async def test_configured_review_returns_the_actual_normalized_crop_url(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    settings = _cycle_settings(crop_detection=True)
    monkeypatch.setattr(screening_api, "get_settings", lambda: settings)
    async with get_sessionmaker()() as db:
        image = ScreeningImage(
            farm_id=farm_id,
            s3_bucket="goat-photos",
            s3_key="raw/positive-url.jpg",
            status="HEALTHY",
        )
        db.add(image)
        await db.flush()
        crop = ScreeningCrop(
            farm_id=farm_id,
            image_id=image.id,
            crop_index=0,
            box_x=0,
            box_y=0,
            box_w=100,
            box_h=100,
            status="HEALTHY",
            normalized_key="normalized/positive-crop.jpg",
        )
        db.add(crop)
        await db.commit()
        image_id, crop_id = image.id, crop.id
    response = await client.get(f"/api/screening/images/{image_id}", headers=owner)
    assert response.status_code == 200, response.text
    rows = response.json()["crops"]
    assert len(rows) == 1 and rows[0]["id"] == crop_id
    url = rows[0]["image_url"]
    assert isinstance(url, str), "A real immutable crop derivative lost its configured review URL"
    assert "/normalized/positive-crop.jpg?" in url and "X-Amz-Signature=" in url
    assert response.json()["image_url"] is None
