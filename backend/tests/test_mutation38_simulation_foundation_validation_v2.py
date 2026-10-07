"""An impossible foundation age identifies the field the operator must fix."""

import httpx

from .conftest import owner_with_farm


async def test_public_impossible_foundation_minimum_has_an_actionable_field_error(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    response = await client.post(
        "/api/simulation/run",
        json={
            "assumptions": {
                "herd": {
                    "foundation_doe_age_min_months": 181,
                    "foundation_doe_age_max_months": 180,
                }
            }
        },
        headers=headers,
    )
    assert response.status_code == 422, response.text
    errors = response.json()["detail"]
    assert any(
        error["loc"] == ["body", "assumptions", "herd", "foundation_doe_age_min_months"]
        for error in errors
    ), response.text
