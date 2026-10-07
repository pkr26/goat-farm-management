"""Public snapshot conservation at whole-month and sex/cohort boundaries."""

from datetime import date, timedelta

import httpx
import pytest

import app.api.simulation as simulation_api

from .conftest import owner_with_farm


def _months_before(months: int) -> date:
    # January makes the annual term material even for the young cohorts.
    absolute = 2026 * 12 - months
    return date(absolute // 12, absolute % 12 + 1, 15)


@pytest.mark.parametrize("breed,doe_adult_months", [("osmanabadi", 12), ("jamunapari", 15)])
async def test_public_herd_snapshot_conserves_each_calendar_boundary_and_unknown_age(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    breed: str,
    doe_adult_months: int,
) -> None:
    reference = date(2026, 1, 15)
    monkeypatch.setattr(simulation_api, "today", lambda timezone: reference)
    headers = await owner_with_farm(client)
    expected = {
        "does": 0,
        "bucks": 0,
        "f_kids": 0,
        "f_weaners": 0,
        "f_growers": 0,
        "m_kids": 0,
        "m_weaners": 0,
        "m_growers": 0,
        "total_head": 0,
    }
    # Each observation is checked immediately, so erroneous classifications
    # cannot cancel when later animals cross a different boundary.
    cases: list[tuple[str, date | None, str]] = [
        ("F", _months_before(2), "f_kids"),
        ("M", _months_before(2), "m_kids"),
        ("F", _months_before(3), "f_weaners"),
        ("M", _months_before(3), "m_weaners"),
        ("F", _months_before(3) + timedelta(days=1), "f_kids"),
        ("M", _months_before(3) + timedelta(days=1), "m_kids"),
        ("F", _months_before(5), "f_weaners"),
        ("M", _months_before(5), "m_weaners"),
        ("F", _months_before(6), "f_growers"),
        ("M", _months_before(6), "m_growers"),
        ("F", _months_before(6) + timedelta(days=1), "f_weaners"),
        ("M", _months_before(6) + timedelta(days=1), "m_weaners"),
        ("F", _months_before(doe_adult_months - 1), "f_growers"),
        ("M", _months_before(11), "m_growers"),
        ("F", _months_before(doe_adult_months), "does"),
        ("M", _months_before(12), "bucks"),
        ("F", _months_before(doe_adult_months) + timedelta(days=1), "f_growers"),
        ("M", _months_before(12) + timedelta(days=1), "m_growers"),
        ("F", None, "does"),
        ("M", None, "bucks"),
    ]
    for index, (sex, birth, cohort) in enumerate(cases):
        payload: dict[str, str] = {
            "tag_number": f"BOUNDARY-{index}",
            "sex": sex,
            "source": "PURCHASED",
            "current_bucket": "QUARANTINE",
        }
        if birth is not None:
            # Both authoritative dates and genuinely estimated dates are
            # legitimate inputs to the snapshot's effective-birth rule.
            payload["estimated_dob" if index % 2 else "date_of_birth"] = birth.isoformat()
        created = await client.post("/api/animals", json=payload, headers=headers)
        assert created.status_code == 201, created.text
        expected[cohort] += 1
        expected["total_head"] += 1
        response = await client.get(
            "/api/simulation/herd-snapshot", params={"breed": breed}, headers=headers
        )
        assert response.status_code == 200, response.text
        assert response.json() == expected, (sex, birth, cohort, response.text)
        assert (
            sum(expected[key] for key in expected if key != "total_head") == expected["total_head"]
        )
