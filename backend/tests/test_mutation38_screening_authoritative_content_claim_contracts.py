"""An authoritative retained claim protects content despite a missing advisory digest."""

import httpx
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import ScreeningContentClaim, ScreeningImage
from app.services.screening.images import normalize_image
from app.services.screening.pipeline import run_screening_cycle
from app.services.screening.rotation import ProviderRotation
from app.utils import today

from .conftest import owner_with_farm
from .test_screening import CountingProvider, FakeStorage, _cycle_settings, _jpeg_bytes


async def test_durable_content_claim_keeps_its_identity_and_counts_one_rejection(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    settings = _cycle_settings()
    prior = normalize_image(_jpeg_bytes(160, 80), settings.screening_image_max_edge_px, None)
    key = f"raw/{farm_id}/retained-authoritative-claim.jpg"
    storage = FakeStorage()
    storage.objects[key] = _jpeg_bytes(80, 160)
    replacement = normalize_image(storage.objects[key], settings.screening_image_max_edge_px, None)
    assert replacement.sha256 != prior.sha256
    async with get_sessionmaker()() as seed:
        image = ScreeningImage(
            farm_id=farm_id,
            s3_bucket=storage.bucket,
            s3_key=key,
            captured_date=today(),
            status="PENDING",
        )
        seed.add(image)
        await seed.flush()
        image_id = image.id
        seed.add(ScreeningContentClaim(farm_id=farm_id, image_id=image_id, sha256=prior.sha256))
        await seed.commit()
    provider = CountingProvider(name="authoritative-claim-must-not-be-billed")
    async with get_sessionmaker()() as worker:
        summary = await run_screening_cycle(worker, settings, storage, ProviderRotation([provider]))
        rejected = await worker.get(ScreeningImage, image_id)
        assert rejected is not None
        assert rejected.status == "SKIPPED"
        assert rejected.error == "object bytes changed after normalized content was claimed"
        assert rejected.sha256 is None and rejected.normalized_key is None
        assert (summary.claimed, summary.skipped, summary.healthy, summary.errors) == (1, 1, 0, 0)
        assert provider.calls == 0
        claim = (await worker.execute(select(ScreeningContentClaim))).scalar_one()
        assert (claim.farm_id, claim.image_id, claim.sha256) == (farm_id, image_id, prior.sha256)
        assert storage.objects[key] == _jpeg_bytes(80, 160)
