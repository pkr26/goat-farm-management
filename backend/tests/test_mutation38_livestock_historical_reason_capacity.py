"""A valid import narrative fills, but does not exceed, the initial-move audit field."""

from datetime import timedelta

import httpx
import pytest

from app.utils import today

from .conftest import owner_with_farm


@pytest.mark.parametrize("reason_length", [236, 237, 255])
async def test_historical_import_retains_the_full_255_character_initial_move_audit(
    client: httpx.AsyncClient, reason_length: int
) -> None:
    owner = await owner_with_farm(client)
    acquired_on = today() - timedelta(days=7)
    narrative = "Verified paper register: " + "arrival-history-" * 20
    reason = narrative[: reason_length - 1] + "!"
    assert len(reason) == reason_length
    response = await client.post(
        "/api/animals",
        headers=owner,
        json={
            "tag_number": "HISTORICAL-REASON-CAPACITY",
            "sex": "F",
            "source": "PURCHASED",
            "current_bucket": "FOUNDATION",
            "purchase_date": acquired_on.isoformat(),
            "historical_import_reason": reason,
        },
    )
    assert response.status_code == 201, response.text
    profile = await client.get(f"/api/animals/{response.json()['id']}", headers=owner)
    assert profile.status_code == 200, profile.text
    assert profile.json()["moves_total"] == 1
    move = profile.json()["moves"][0]
    assert move["from_bucket"] is None
    assert move["to_bucket"] == "FOUNDATION"
    assert move["effective_date"] == acquired_on.isoformat()
    # The marker occupies 19 characters of the public 255-character audit
    # field. Preserve all 236 remaining narrative characters, including the
    # last one, for every accepted input length.
    assert len(move["reason"]) == 255
    assert move["reason"] == "Historical import: " + reason[:236]
