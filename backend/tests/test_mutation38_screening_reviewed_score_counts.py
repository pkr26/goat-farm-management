"""Actual adjudication removes pending work without inventing another queue cohort."""

import httpx
import pytest
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import ScreeningFinding, ScreeningRun

from .conftest import owner_with_farm
from .test_mutation38_screening_scoreboard_export_contracts import _screen_photo
from .type_helpers import Headers


async def _review_image_finding(
    client: httpx.AsyncClient, owner: Headers, image_id: int, status: str
) -> None:
    async with get_sessionmaker()() as db:
        findings = list(
            (
                await db.execute(
                    select(ScreeningFinding)
                    .join(ScreeningRun, ScreeningRun.id == ScreeningFinding.run_id)
                    .where(ScreeningRun.image_id == image_id)
                )
            ).scalars()
        )
        assert len(findings) == 1
        finding_id = findings[0].id
    response = await client.post(
        f"/api/screening/findings/{finding_id}/review",
        headers=owner,
        json={"status": status, "expected_status": "PENDING_REVIEW", "expected_revision": 0},
    )
    assert response.status_code == 200, response.text


async def test_confirmed_actual_positive_has_no_pending_positive_or_healthy_controls(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    image_id = await _screen_photo(owner, name="confirmed-positive", verdict="FLAGGED")
    await _review_image_finding(client, owner, image_id, "CONFIRMED")
    response = await client.get("/api/screening/stats", headers=owner)
    assert response.status_code == 200, response.text
    rows = response.json()["providers"]
    assert len(rows) == 1
    row = rows[0]
    assert (row["findings_confirmed"], row["findings_rejected"], row["findings_pending"]) == (
        1,
        0,
        0,
    )
    assert (
        row["healthy_controls_confirmed"],
        row["healthy_controls_rejected"],
        row["healthy_controls_pending"],
        row["healthy_controls_reviewed"],
    ) == (0, 0, 0, 0)
    assert row["positive_precision"] == "1.000"
    assert row["healthy_false_negative_rate"] is None


@pytest.mark.parametrize("status", ["CONFIRMED", "REJECTED"])
async def test_real_sampled_healthy_adjudication_never_becomes_a_positive_finding(
    client: httpx.AsyncClient, status: str
) -> None:
    owner = await owner_with_farm(client)
    assert int(owner["X-Farm-Id"]) == 1
    image_id = await _screen_photo(owner, name="reviewed-hash-control", height=164)
    await _review_image_finding(client, owner, image_id, status)
    response = await client.get("/api/screening/stats", headers=owner)
    assert response.status_code == 200, response.text
    rows = response.json()["providers"]
    assert len(rows) == 1
    row = rows[0]
    assert (row["findings_confirmed"], row["findings_rejected"], row["findings_pending"]) == (
        0,
        0,
        0,
    )
    assert row["positive_precision"] is None
    assert (
        row["healthy_controls_confirmed"],
        row["healthy_controls_rejected"],
        row["healthy_controls_pending"],
        row["healthy_controls_reviewed"],
    ) == (int(status == "CONFIRMED"), int(status == "REJECTED"), 0, 1)
    assert row["healthy_false_negative_rate"] == ("0.000" if status == "CONFIRMED" else "1.000")
