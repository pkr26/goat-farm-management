"""Real daily runs preserve head-day admission, opt-in ledgers and charged CPU work."""

import httpx
import pytest

from app.api._run_limits import _run_budget

from .conftest import owner_with_farm


def _document(heads: int, days: int, *, ledger: bool = False) -> dict[str, object]:
    # These older males sell on the first simulated day. The horizon remains
    # materialized, while the boundary test stays small after its real sale.
    return {
        "start_date": "2026-01-01",
        "horizon_days": days,
        "seed": 1,
        "animals": [
            {"tag": f"M{index}", "sex": "M", "bucket": "MALE_KIDS", "age_months": 24}
            for index in range(heads)
        ],
        "params": {
            "adult_annual_mortality": 0.0,
            "grower_annual_mortality": 0.0,
            "kid_pre_weaning_mortality": 0.0,
            "kid_post_weaning_mortality": 0.0,
        },
        "include_ledger": ledger,
    }


async def test_head_day_ceiling_admits_exactly_fifty_thousand(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    response = await client.post("/api/ops-sim/run", json=_document(250, 200), headers=headers)
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["head_start"] == 250
    assert sum(row["heads"] for row in result["days"][-1]["occupancy"]) == 0
    assert result["totals"]["sales"] == 250
    assert len(result["days"]) == 200
    assert len(result["journeys"]) == 250


async def test_above_head_day_ceiling_rejects_before_charging_budget(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    farm_id = int(headers["X-Farm-Id"])
    before = _run_budget._spent("farm", farm_id)
    response = await client.post("/api/ops-sim/run", json=_document(250, 201), headers=headers)
    assert response.status_code == 422, response.text
    assert "head-days" in response.json()["detail"]
    assert _run_budget._spent("farm", farm_id) == before


@pytest.mark.parametrize("heads", [1, 9, 10, 11])
async def test_completed_daily_run_books_its_documented_amplified_work(
    client: httpx.AsyncClient, heads: int
) -> None:
    headers = await owner_with_farm(client)
    farm_id = int(headers["X-Farm-Id"])
    before = _run_budget._spent("farm", farm_id)
    response = await client.post("/api/ops-sim/run", json=_document(heads, 7), headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["result"]["totals"]["sales"] == heads
    # Published admission policy reserves the initial pass plus ten potential
    # birth-expanded passes, priced in ten-head groups for every simulated day.
    assert _run_budget._spent("farm", farm_id) - before == 7 * (1 + heads // 10) * 11


@pytest.mark.parametrize("ledger", [False, True])
async def test_valid_daily_run_returns_only_the_requested_audit_ledger(
    client: httpx.AsyncClient, ledger: bool
) -> None:
    headers = await owner_with_farm(client)
    response = await client.post(
        "/api/ops-sim/run", json=_document(1, 7, ledger=ledger), headers=headers
    )
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert len(result["days"]) == 7
    assert result["totals"]["sales"] == 1
    if ledger:
        assert response.json()["ledger"].startswith("# Buckets & Tasks — daily operations ledger")
        assert "## Day 7 — 2026-01-07" in response.json()["ledger"]
    else:
        assert response.json()["ledger"] is None


async def test_real_births_that_outgrow_result_ceiling_receive_input_rejection(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    document = _document(225, 100)
    document["animals"] = [
        {
            "tag": f"D{index}",
            "sex": "F",
            "bucket": "DELIVERY",
            "age_months": 24,
            "bred_days_ago": 149,
        }
        for index in range(225)
    ]
    document["params"] = {
        "litter_size_mean": 4.0,
        "female_fraction_at_birth": 1.0,
        "stillbirth_rate": 0.0,
        "abortion_rate": 0.0,
        "adult_annual_mortality": 0.0,
        "kid_pre_weaning_mortality": 0.0,
    }
    response = await client.post("/api/ops-sim/run", json=document, headers=headers)
    assert response.status_code == 422, response.text
    assert "standing herd" in response.json()["detail"]
    assert "head-days" in response.json()["detail"]
