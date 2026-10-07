"""An ordinary multi-goat photo persists actual native crop evidence."""

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.db import get_sessionmaker
from app.models import ScreeningCrop, ScreeningImage, ScreeningRun
from app.services.screening.pipeline import run_screening_cycle
from app.services.screening.rotation import ProviderRotation

from .conftest import owner_with_farm
from .test_mutation38_screening_crop_control_completion import _intake, _sampled_jpeg
from .test_screening import CountingProvider, FakeStorage, _cycle_settings


async def test_native_detected_goats_commit_crop_identities_and_their_own_evidence(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="native-crop-persistence@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    storage = FakeStorage()
    image_id = await _intake(
        farm_id,
        storage,
        "native-crop-persistence",
        _sampled_jpeg(farm_id, False, 600, 600),
    )
    provider = CountingProvider(
        name="declared-native-crop-provider", detect_boxes=[[60, 60, 200, 500], [330, 60, 200, 500]]
    )
    async with get_sessionmaker()() as worker:
        try:
            summary = await run_screening_cycle(
                worker,
                _cycle_settings(crop_detection=True),
                storage,
                ProviderRotation([provider]),
            )
        except (SQLAlchemyError, AttributeError, TypeError) as exc:
            pytest.fail(f"Valid detected-goat intake must persist its native crop evidence: {exc}")
    assert (summary.claimed, summary.healthy, summary.errors, summary.flagged) == (1, 1, 0, 0)
    async with get_sessionmaker()() as observer:
        image = await observer.get(ScreeningImage, image_id)
        assert image is not None and image.status == "HEALTHY"
        crops = list(
            (
                await observer.execute(
                    select(ScreeningCrop)
                    .where(ScreeningCrop.image_id == image_id)
                    .order_by(ScreeningCrop.crop_index)
                )
            ).scalars()
        )
        assert len(crops) == 2, "both actual detector proposals must have persisted crop identities"
        assert [crop.crop_index for crop in crops] == [0, 1]
        assert all(crop.farm_id == farm_id and crop.status == "HEALTHY" for crop in crops)
        assert len({crop.id for crop in crops}) == 2
        runs = list(
            (
                await observer.execute(
                    select(ScreeningRun).where(ScreeningRun.image_id == image_id)
                )
            ).scalars()
        )
        for crop in crops:
            evidence = [run for run in runs if run.crop_id == crop.id]
            assert evidence and all(run.farm_id == farm_id for run in evidence)
            assert any(run.stage == "GATE" and run.run_status == "OK" for run in evidence)
            assert crop.normalized_key is not None and crop.normalized_key in storage.objects
