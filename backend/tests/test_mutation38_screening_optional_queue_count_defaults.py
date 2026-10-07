"""Optional public review counts agree with the real empty SQL review queue."""

import httpx
from sqlalchemy import func, select

from app.db import get_sessionmaker
from app.models import ScreeningFinding, ScreeningImage, ScreeningRun
from app.models.screening import HEALTHY_CONTROL_LABEL
from app.schemas.screening import ScreeningImageRowOut
from app.utils import today

from .conftest import owner_with_farm
from .test_screening import FakeStorage


async def test_optional_public_row_counts_describe_the_real_empty_review_queue(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="optional-review-counts@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    storage = FakeStorage()
    captured = today()
    async with get_sessionmaker()() as intake:
        image = ScreeningImage(
            farm_id=farm_id,
            s3_bucket=storage.bucket,
            s3_key=f"raw/{farm_id}/{captured.isoformat()}/awaiting-photo.jpg",
            captured_date=captured,
            status="PENDING",
        )
        intake.add(image)
        await intake.commit()
        image_id = image.id
    async with get_sessionmaker()() as observer:
        counts = (
            await observer.execute(
                select(
                    func.count(ScreeningFinding.id),
                    func.count(ScreeningFinding.id).filter(
                        ScreeningFinding.label == HEALTHY_CONTROL_LABEL
                    ),
                )
                .join(ScreeningRun, ScreeningRun.id == ScreeningFinding.run_id)
                .where(ScreeningRun.farm_id == farm_id, ScreeningRun.image_id == image_id)
            )
        ).one()
        empty_findings, empty_controls = map(int, counts)
    response = await client.get("/api/screening/images", headers=owner)
    assert response.status_code == 200, response.text
    rows = response.json()["images"]
    assert len(rows) == 1 and rows[0]["id"] == image_id
    assert rows[0]["pending_findings"] == empty_findings
    assert rows[0]["pending_healthy_controls"] == empty_controls

    published = await client.get("/openapi.json")
    assert published.status_code == 200, published.text
    schema = published.json()["components"]["schemas"]["ScreeningImageRowOut"]
    for name in ("pending_findings", "pending_healthy_controls"):
        assert name not in schema["required"]
    # A consumer may omit these declared optional fields. Their model defaults
    # must not manufacture work in this actual empty review queue. The current
    # HTTP producer supplies both counts explicitly, which is checked above.
    optional_payload = dict(rows[0])
    del optional_payload["pending_findings"]
    del optional_payload["pending_healthy_controls"]
    decoded = ScreeningImageRowOut.model_validate(optional_payload)
    assert decoded.pending_findings == empty_findings
    assert decoded.pending_healthy_controls == empty_controls
    assert schema["properties"]["pending_findings"]["default"] == empty_findings
    assert schema["properties"]["pending_healthy_controls"]["default"] == empty_controls
