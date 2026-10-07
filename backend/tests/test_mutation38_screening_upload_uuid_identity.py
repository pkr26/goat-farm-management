"""Distinct native upload identities preserve two public photo registrations."""

import base64
import json
import uuid

import httpx
import pytest
from sqlalchemy import select

from app.api import screening as screening_api
from app.db import get_sessionmaker
from app.models import ScreeningImage
from app.services.screening.pipeline import parse_raw_key

from .conftest import owner_with_farm
from .test_screening import _cycle_settings


class _DistinctNativeIds:
    def __init__(self) -> None:
        self.values = [
            uuid.UUID("12345678-1234-4abc-8def-123456789abc"),
            uuid.UUID("12345678-1234-4def-9abc-abcdef012345"),
        ]
        self.calls = 0

    def uuid4(self) -> uuid.UUID:
        result = self.values[self.calls]
        self.calls += 1
        return result


async def test_distinct_uuid4_identity_keeps_two_walkthrough_photos_and_bound_forms(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await owner_with_farm(client, email="screening-uuid-identity@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    settings = _cycle_settings(crop_detection=False)
    monkeypatch.setattr(screening_api, "get_settings", lambda: settings)
    created = await client.post("/api/screening/batches", headers=owner)
    assert created.status_code == 201, created.text
    batch_id = int(created.json()["id"])
    rng = _DistinctNativeIds()
    assert all(value.version == 4 and value.variant == uuid.RFC_4122 for value in rng.values)
    assert rng.values[0] != rng.values[1]
    # Distinct real UUID4 values can share this prefix. The next nibble is
    # the fixed UUID version, so a thirteen-character prefix also collides.
    assert rng.values[0].hex[:13] == rng.values[1].hex[:13]
    monkeypatch.setattr(screening_api, "uuid", rng)
    forms = []
    for ordinal in (1, 2):
        response = await client.post(
            "/api/screening/uploads",
            headers={**owner, "Idempotency-Key": f"uuid-photo-{ordinal}"},
            json={
                "batch_id": batch_id,
                "bucket": "RESTING",
                "file_name": "camera.jpg",
                "content_type": "image/jpeg",
                "file_size": 1_024,
            },
        )
        assert response.status_code == 201, response.text
        forms.append(response.json())
    assert rng.calls == 2
    assert forms[0]["image_id"] != forms[1]["image_id"]
    assert forms[0]["s3_key"] != forms[1]["s3_key"]
    for form in forms:
        parsed = parse_raw_key(form["s3_key"], settings.screening_s3_prefix)
        assert parsed is not None and parsed.farm_id == farm_id and parsed.bucket == "RESTING"
        assert len(form["s3_key"].encode("utf-8")) <= 1_024
        assert form["upload_method"] == "POST"
        assert form["upload_fields"]["key"] == form["s3_key"]
        policy = json.loads(base64.b64decode(form["upload_fields"]["policy"]))
        assert {"key": form["s3_key"]} in policy["conditions"]
        assert {"Content-Type": "image/jpeg"} in policy["conditions"]
    async with get_sessionmaker()() as db:
        rows = list(
            (
                await db.execute(
                    select(ScreeningImage)
                    .where(ScreeningImage.farm_id == farm_id, ScreeningImage.batch_id == batch_id)
                    .order_by(ScreeningImage.id)
                )
            ).scalars()
        )
        assert len(rows) == 2
        assert [row.id for row in rows] == [form["image_id"] for form in forms]
        assert [row.s3_key for row in rows] == [form["s3_key"] for form in forms]
        assert all(row.status == "PENDING" for row in rows)
        assert len({row.upload_token for row in rows}) == 2
        for row, form in zip(rows, forms, strict=True):
            assert row.upload_token == form["upload_fields"]["x-amz-meta-screening-token"]
            assert row.upload_content_type == "image/jpeg"
