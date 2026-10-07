"""Historical BREEDING entry counts completed calendar months, not year labels."""

from datetime import date

import httpx
import pytest

from app.api import animals as animals_api

from .conftest import owner_with_farm


def _reference_date(_timezone: str = "Asia/Kolkata") -> date:
    return date(2026, 1, 15)


@pytest.mark.parametrize("sex", ["F", "M"])
@pytest.mark.parametrize(
    ("birth_date", "expected_status"),
    [("2025-02-15", 422), ("2025-01-15", 201)],
    ids=["eleven-calendar-months", "twelfth-month-anniversary"],
)
async def test_historical_breeding_entry_respects_calendar_months_across_year_boundary(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    sex: str,
    birth_date: str,
    expected_status: int,
) -> None:
    owner = await owner_with_farm(client)
    monkeypatch.setattr(animals_api, "today", _reference_date)
    # A 30kg entry exceeds both normal doe and sire minimum weights. Normal
    # JSON, owner authorization, and coherent BORN provenance leave age as
    # the sole difference between the rejected and admitted calendar cases.
    response = await client.post(
        "/api/animals",
        headers=owner,
        json={
            "tag_number": f"HIST-AGE-{sex}",
            "sex": sex,
            "source": "BORN",
            "current_bucket": "BREEDING",
            "historical_import_reason": "Reconcile the signed annual herd register",
            "date_of_birth": birth_date,
            "weight_kg": 30,
            "weight_date": "2026-01-15",
        },
    )
    assert response.status_code == expected_status, response.text
    if expected_status == 422:
        assert (
            "Historical BREEDING entry requires age at least 12 months" in response.json()["detail"]
        )
    else:
        animal = response.json()
        assert animal["date_of_birth"] == birth_date
        assert animal["age_months"] == 12
        assert animal["latest_weight_kg"] == 30
        assert animal["current_bucket"] == "BREEDING"
        assert animal["status"] == "ACTIVE"
