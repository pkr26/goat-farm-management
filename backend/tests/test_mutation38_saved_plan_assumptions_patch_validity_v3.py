"""An admitted assumptions-only saved-plan update remains a valid public plan."""

import httpx

from .conftest import owner_with_farm
from .type_helpers import json_object


async def test_assumptions_only_patch_preserves_valid_plan_and_authoritative_anchor(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    defaults = await client.get("/api/simulation/defaults", headers=headers)
    assert defaults.status_code == 200, defaults.text
    assumptions = json_object(defaults.json())
    assumptions["meta"] = {"horizon_months": 12, "start_year_month": "2026-01"}
    assumptions["risk"]["monte_carlo_runs"] = 2
    assumptions["optimization"]["max_candidates"] = 1
    created = await client.post(
        "/api/planner/plans",
        json={
            "name": "Reviewed assumptions update",
            "start_year_month": "2026-01",
            "assumptions": assumptions,
            "targets": [{"year_month": "2027-01", "animal_class": "male_grower", "count": 1.0}],
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    row = json_object(created.json())
    changed = json_object(row["assumptions"])
    changed["meta"]["start_year_month"] = "2027-05"
    changed["herd"]["does"] = 49
    response = await client.patch(
        f"/api/planner/plans/{row['id']}",
        json={"expected_revision": 1, "assumptions": changed},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    saved = json_object(response.json())
    assert saved["valid"] is True, saved
    assert saved["assumptions"] is not None, saved
    retained = json_object(saved["assumptions"])
    assert saved["revision"] == 2
    assert saved["start_year_month"] == "2026-01"
    assert retained["meta"]["start_year_month"] == "2026-01"
    assert retained["herd"]["does"] == 49
