"""A birth-weight validation reports the real inclusive credibility band."""

from datetime import timedelta

import httpx
import pytest

from app.utils import today

from .conftest import owner_with_farm


@pytest.mark.parametrize(
    ("birth_weight", "expected_status"),
    [(0.49, 422), (0.5, 201), (8.0, 201), (8.01, 422)],
)
async def test_historical_birth_weights_use_and_report_the_literal_half_to_eight_kg_band(
    client: httpx.AsyncClient, birth_weight: float, expected_status: int
) -> None:
    owner = await owner_with_farm(client)
    response = await client.post(
        "/api/animals",
        headers=owner,
        json={
            "tag_number": "LITERAL-BIRTH-BAND",
            "sex": "F",
            "source": "BORN",
            "current_bucket": "FEMALE_KIDS",
            "date_of_birth": (today() - timedelta(days=90)).isoformat(),
            "historical_import_reason": "Existing herd register",
            "birth_weight": birth_weight,
        },
    )
    assert response.status_code == expected_status, response.text
    if expected_status == 201:
        assert response.json()["birth_weight"] == birth_weight
    else:
        # The keeper needs both true endpoints to correct an implausible entry.
        assert "birth weight must be between 0.5 and 8 kg" in response.json()["detail"]
        assert f"{birth_weight:g} kg is not a credible newborn weight" in response.json()["detail"]
