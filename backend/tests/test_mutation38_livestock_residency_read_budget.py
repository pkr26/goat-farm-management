"""Only a resting-to-breeding admission needs the postpartum residency read."""

import re
from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import event

from app.db import get_engine
from app.utils import today

from .conftest import owner_with_farm


@pytest.mark.parametrize(("source_bucket", "residency_reads"), [("FOUNDATION", 0), ("RESTING", 1)])
async def test_breeding_admission_reads_residency_only_for_the_postpartum_cohort(
    client: httpx.AsyncClient, source_bucket: str, residency_reads: int
) -> None:
    owner = await owner_with_farm(client)
    reference_date = today()
    created = await client.post(
        "/api/animals",
        headers=owner,
        json={
            "tag_number": f"RESIDENCY-{source_bucket}",
            "sex": "F",
            "source": "PURCHASED",
            "historical_import_reason": "Audited existing-herd import",
            "current_bucket": source_bucket,
            "date_of_birth": (reference_date - timedelta(days=800)).isoformat(),
            "weight_kg": 26.0,
            "weight_date": reference_date.isoformat(),
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["current_bucket"] == source_bucket
    scans: list[str] = []

    def capture_statement(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        # Observe SQL executed by the real driver. This scoped read consults
        # the historical RESTING cohort rather than current maturity facts.
        if (
            re.match(r"\s*SELECT\b", statement, re.IGNORECASE)
            and re.search(r"\b(?:FROM|JOIN)\s+bucket_moves\b", statement, re.IGNORECASE)
            and re.search(
                r"\bbucket_moves(?:_[a-z0-9]+)?\.to_bucket\s*(?:=|IN\s*\()",
                statement,
                re.IGNORECASE,
            )
            and "RESTING" in parameters
        ):
            scans.append(statement)

    engine = get_engine().sync_engine
    event.listen(engine, "before_cursor_execute", capture_statement)
    try:
        moved = await client.post(
            f"/api/animals/{created.json()['id']}/move",
            headers=owner,
            json={"to_bucket": "BREEDING", "reason": "Recorded breeding admission"},
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture_statement)
    assert moved.status_code == 200, moved.text
    assert moved.json()["current_bucket"] == "BREEDING"
    # A foundation doe has no postpartum flush prerequisite. Her admission
    # must not add that historical read to the request's read budget.
    # The genuine resting admission proves the monitor observes the read.
    assert len(scans) == residency_reads
