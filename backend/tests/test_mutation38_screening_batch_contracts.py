"""Walkthrough progress and upload metadata preserve independently stated photo facts."""

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.api import screening as screening_api
from app.db import get_sessionmaker
from app.models import Farm, ScreeningBatch, ScreeningImage
from app.schemas.screening import (
    ScreeningBatchBucketProgressOut,
    ScreeningBatchOut,
    ScreeningUploadIn,
)

from .conftest import owner_with_farm
from .test_screening import _cycle_settings


@pytest.mark.parametrize(
    ("filename", "accepted"),
    [("a.jpg", True), ("f" * 251 + ".jpg", True), ("f" * 252 + ".jpg", False), ("  a.jpg  ", True)],
    ids=["shortest", "maximum", "overlong", "trim-device-name"],
)
def test_upload_filename_adapter_preserves_five_to_255_character_device_names(
    filename: str,
    accepted: bool,
) -> None:
    body = {
        "batch_id": 1,
        "bucket": "RESTING",
        "file_name": filename,
        "content_type": "image/jpeg",
        "file_size": 1,
    }
    if not accepted:
        with pytest.raises(ValidationError):
            ScreeningUploadIn.model_validate(body)
        return
    try:
        result = ScreeningUploadIn.model_validate(body)
    except ValidationError as exc:
        pytest.fail(f"A supported direct-upload device filename was rejected: {exc}")
    assert result.file_name == filename.strip()


@pytest.mark.parametrize(
    ("size", "accepted"),
    [(0, False), (1, True), (26_214_400, True), (26_214_401, False)],
    ids=["empty", "first-byte", "25-mib", "over-25-mib"],
)
def test_upload_registration_metadata_keeps_the_literal_one_byte_to_25_mib_band(
    size: int,
    accepted: bool,
) -> None:
    body = {
        "batch_id": 1,
        "bucket": "RESTING",
        "file_name": "a.jpg",
        "content_type": "image/jpeg",
        "file_size": size,
    }
    if not accepted:
        with pytest.raises(ValidationError):
            ScreeningUploadIn.model_validate(body)
        return
    try:
        result = ScreeningUploadIn.model_validate(body)
    except ValidationError as exc:
        pytest.fail(f"Supported positive upload size metadata was rejected: {exc}")
    # This is pre-registration metadata, before the separate image decoder.
    assert result.file_size == size


async def test_walkthrough_progress_conserves_actual_mixed_and_legacy_image_states(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await owner_with_farm(client, email="batch-progress-facts@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    monkeypatch.setattr(
        screening_api, "get_settings", lambda: _cycle_settings(crop_detection=False)
    )
    created = await client.post("/api/screening/batches", headers=owner)
    assert created.status_code == 201, created.text
    empty = created.json()
    assert [
        empty[k]
        for k in ("images_uploaded", "images_screened", "images_flagged", "images_unassessable")
    ] == [0, 0, 0, 0]
    assert empty["buckets"] == []
    batch_id = int(empty["id"])
    facts = [
        ("RESTING", "HEALTHY"),
        ("RESTING", "FLAGGED"),
        ("BREEDING", "UNASSESSABLE"),
        ("BREEDING", "UNASSESSABLE"),
        ("BREEDING", "ERROR"),
        ("BREEDING", "PENDING"),
        ("QUARANTINE", "SKIPPED"),
        (None, "HEALTHY"),
    ]
    async with get_sessionmaker()() as db:
        batch = await db.get(ScreeningBatch, batch_id)
        assert batch is not None
        # A real committed row exercises the explicitly declared native ORM
        # adapter; progress defaults reflect an empty retained row projection.
        try:
            native = ScreeningBatchOut.model_validate(batch)
        except ValidationError as exc:
            pytest.fail(
                f"A committed walkthrough must support its declared ORM wire adapter: {exc}"
            )
        assert native.id == batch_id and native.submitted_at is None
        assert native.model_dump()["images_unassessable"] == 0
        db.add_all(
            [
                ScreeningImage(
                    farm_id=farm_id,
                    batch_id=batch_id,
                    bucket=bucket,
                    s3_bucket="goat-photos",
                    s3_key=f"raw/{farm_id}/2026-01-01/retained-{i}.jpg",
                    status=status,
                    error="Unusable legacy evidence" if status == "ERROR" else None,
                )
                for i, (bucket, status) in enumerate(facts)
            ]
        )
        await db.commit()
        # The native optional-count adapter consumes genuine projected photo
        # facts for this pen. This cohort has no unassessable photographs.
        projection = (
            (
                await db.execute(
                    select(ScreeningImage.bucket.label("bucket"), func.count().label("uploaded"))
                    .where(ScreeningImage.batch_id == batch_id, ScreeningImage.bucket == "RESTING")
                    .group_by(ScreeningImage.bucket)
                )
            )
            .mappings()
            .one()
        )
        projected = ScreeningBatchBucketProgressOut.model_validate(
            dict(projection) | {"screened": 2, "flagged": 1}
        )
        assert projected.unassessable == 0
    listed = await client.get("/api/screening/batches", headers=owner)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert (body["total"], body["limit"], body["offset"]) == (1, 10, 0)
    assert len(body["batches"]) == 1, body
    row = body["batches"][0]
    assert row["id"] == batch_id
    assert [
        row[k]
        for k in ("images_uploaded", "images_screened", "images_flagged", "images_unassessable")
    ] == [8, 3, 1, 2]
    assert {
        item["bucket"]: {k: v for k, v in item.items() if k != "bucket"} for item in row["buckets"]
    } == {
        "RESTING": {"uploaded": 2, "screened": 2, "flagged": 1, "unassessable": 0},
        "BREEDING": {"uploaded": 4, "screened": 0, "flagged": 0, "unassessable": 2},
        "QUARANTINE": {"uploaded": 1, "screened": 0, "flagged": 0, "unassessable": 0},
    }


@pytest.mark.parametrize(
    "key", [0, 2_147_483_647, 2_147_483_648], ids=["retained-zero", "last", "overflow"]
)
async def test_real_walkthrough_submission_preserves_the_positive_int4_identifier_contract(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    key: int,
) -> None:
    owner = await owner_with_farm(client, email="batch-submission-key@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    monkeypatch.setattr(
        screening_api, "get_settings", lambda: _cycle_settings(crop_detection=False)
    )
    if key <= 2_147_483_647:
        async with get_sessionmaker()() as db:
            farm = await db.get(Farm, farm_id)
            assert farm is not None
            db.add(ScreeningBatch(id=key, farm_id=farm_id, created_by_id=farm.owner_id))
            await db.flush()
            db.add(
                ScreeningImage(
                    farm_id=farm_id,
                    batch_id=key,
                    bucket="RESTING",
                    s3_bucket="goat-photos",
                    s3_key=f"raw/{farm_id}/2026-01-01/retained.jpg",
                    status="PENDING",
                )
            )
            await db.commit()
    submitted = await client.post(f"/api/screening/batches/{key}/submit", headers=owner)
    assert submitted.status_code == (200 if key == 2_147_483_647 else 404), submitted.text
    if key == 2_147_483_647:
        result = submitted.json()
        assert result["id"] == key and result["submitted_at"] is not None
        assert result["images_uploaded"] == 1 and result["images_screened"] == 0
    else:
        assert submitted.json()["detail"] == "Screening batch not found"
