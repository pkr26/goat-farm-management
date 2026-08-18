"""Cull dispositions provide distinct doe and buck calibration prices."""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest

from app.services.simulation_calibration import _curve_from_observations
from app.simulation.assumptions import MAX_WEIGHT_KG, GrowthAssumptions
from app.simulation.defaults import get_preset
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


def test_weight_curve_extrapolation_survives_one_mis_keyed_weight() -> None:
    """A single decimal-point slip must not 500 every later calibration.

    Ages past the last observation keep the preset's shape rescaled to meet
    the final fitted point. That ratio multiplied the whole tail unbounded, so
    one "250" typed for 25.0 kg drove derived points past the assumption
    schema's 1000 kg ceiling — and ``GET /api/simulation/calibration`` turns
    the resulting ValidationError into a hard 500, permanently, because the
    bad weight record stays in the ledger.
    """
    preset = list(get_preset("osmanabadi", "stall_fed").growth.weight_by_age_months)
    curve = _curve_from_observations({0: 2.55, 1: 4.45, 2: 250.0}, preset)

    assert len(curve) == len(preset)
    assert max(curve) <= MAX_WEIGHT_KG
    assert curve == sorted(curve), "curve must stay nondecreasing"
    # The observation itself is still honoured exactly...
    assert curve[2] == pytest.approx(250.0)
    # ...but it no longer multiplies the whole preset tail by that outlier:
    # the tail flattens onto the observation instead of climbing past the
    # ceiling (unclamped this produced ~1019 kg at 12 months).
    assert curve[12] == pytest.approx(250.0)
    assert curve[12] <= max(250.0, 4.0 * preset[12])

    # The schema the endpoint validates against now accepts the result.
    GrowthAssumptions(birth_weight_kg=curve[0], weight_by_age_months=curve)


def test_weight_curve_still_follows_an_ordinary_farm_exactly() -> None:
    """The clamp must not disturb a normal, in-band flock."""
    preset = list(get_preset("osmanabadi", "stall_fed").growth.weight_by_age_months)
    observed = {0: 2.5, 3: 9.0, 6: 15.0}
    curve = _curve_from_observations(observed, preset)

    for age, weight in observed.items():
        assert curve[age] == pytest.approx(weight), "observations are honoured exactly"
    assert curve == sorted(curve)
    assert max(curve) <= MAX_WEIGHT_KG
