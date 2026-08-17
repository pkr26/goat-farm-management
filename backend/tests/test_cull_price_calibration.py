"""Cull dispositions provide distinct doe and buck calibration prices."""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest

from app.utils import today

from .conftest import owner_with_farm


async def _record_priced_exit(
    client: httpx.AsyncClient,
    headers: dict,
    *,
    tag: str,
    sex: str,
    status: str,
    sale_price: float,
    weight_kg: float = 30.0,
) -> None:
    reference_date = today()
    created = await client.post(
        "/api/animals",
        json={
            "tag_number": tag,
            "sex": sex,
            "source": "PURCHASED",
            "current_bucket": "BREEDING",
            "date_of_birth": (reference_date - timedelta(days=800)).isoformat(),
            "purchase_date": (reference_date - timedelta(days=400)).isoformat(),
            "weight_kg": weight_kg,
            "weight_date": reference_date.isoformat(),
            "historical_import_reason": "Cull-price calibration fixture",
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    exited = await client.post(
        f"/api/animals/{created.json()['id']}/status",
        json={"new_status": status, "sale_price": sale_price},
        headers=headers,
    )
    assert exited.status_code == 200, exited.text


async def test_calibration_separates_cull_doe_buck_and_ordinary_sale_prices(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    for index in range(3):
        await _record_priced_exit(
            client,
            headers,
            tag=f"CULL-DOE-{index}",
            sex="F",
            status="CULLED",
            sale_price=6000.0,
        )
        await _record_priced_exit(
            client,
            headers,
            tag=f"CULL-BUCK-{index}",
            sex="M",
            status="CULLED",
            sale_price=9000.0,
        )
    for index in range(5):
        await _record_priced_exit(
            client,
            headers,
            tag=f"MEAT-SALE-{index}",
            sex="M",
            status="SOLD",
            sale_price=12_000.0,
        )

    response = await client.get(
        "/api/simulation/calibration",
        params={"lookback_months": 24},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    sales = response.json()["assumptions"]["sales"]
    assert sales["cull_doe_price_per_kg"] == pytest.approx(200.0)
    assert sales["cull_buck_price_per_kg"] == pytest.approx(300.0)
    assert sales["meat_price_per_kg"] == pytest.approx(400.0)

    evidence = {item["path"]: item for item in response.json()["evidence"]}
    assert evidence["sales.cull_doe_price_per_kg"]["sample_size"] == 3
    assert evidence["sales.cull_buck_price_per_kg"]["sample_size"] == 3
    # Cull proceeds must not contaminate the ordinary live-meat sale sample.
    assert evidence["sales.meat_price_per_kg"]["sample_size"] == 5
