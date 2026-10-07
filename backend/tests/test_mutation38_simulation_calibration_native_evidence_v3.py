"""Public calibration output reconciles actual native herd and structured ledger evidence."""

from datetime import date, timedelta
from decimal import Decimal
from itertools import pairwise

import httpx
import pytest

from app.db import get_sessionmaker
from app.models import Animal, Farm, FeedInventory, Transaction
from app.services.simulation_calibration import (
    _curve_from_observations,
    _isotonic_fit,
    _months_between,
)
from app.simulation.assumptions import MAX_WEIGHT_KG, SimulationAssumptions
from app.simulation.defaults import get_preset
from app.simulation.engine import labour_units_for
from app.utils import add_months, today

from .conftest import owner_with_farm
from .type_helpers import json_object


async def test_empty_native_farm_calibration_returns_zero_opening_cohorts_with_auditable_evidence(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    response = await client.get("/api/simulation/calibration", headers=owner)
    assert response.status_code == 200, response.text
    body = json_object(response.json())
    assert body["lookback_months"] == 24
    assumptions = SimulationAssumptions.model_validate(body["assumptions"])
    cohorts = [
        "does",
        "bucks",
        "female_kids",
        "female_weaners",
        "female_growers",
        "male_kids",
        "male_weaners",
        "male_growers",
    ]
    assert all(getattr(assumptions.herd, name) == 0 for name in cohorts)
    evidence = {row["path"]: row for row in body["evidence"]}
    assert set(evidence) == {"herd." + name for name in cohorts}
    for name in cohorts:
        row = evidence["herd." + name]
        assert row["calibrated_value"] == 0 and row["sample_size"] == 0
        assert row["confidence"] == "low"
        assert row["period_end"] == body["reference_date"]
    assert body["coverage_score"] == pytest.approx(1 / 7)
    assert any("No active animals" in warning for warning in body["warnings"])
    assert any("low confidence" in warning for warning in body["warnings"])


async def test_native_cohort_and_feed_cost_evidence_matches_complete_actual_current_ledger(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        reference = today(farm.timezone)
        for sex in ("F", "M"):
            for age in (None, 0, 3, 6, 12, 24):
                dob = add_months(reference, -age) if age is not None else None
                purchased = reference - timedelta(days=60)
                if dob is not None:
                    purchased = max(purchased, dob)
                db.add(
                    Animal(
                        farm_id=farm_id,
                        tag_number=f"CAL-{sex}-{age}",
                        sex=sex,
                        source="PURCHASED",
                        current_bucket="FOUNDATION",
                        date_of_birth=dob,
                        purchase_date=purchased,
                        purchase_price=Decimal("9000"),
                        status="ACTIVE",
                    )
                )
        for category, label in [
            ("ROUGHAGE_WET", "Green"),
            ("ROUGHAGE_DRY", "Dry"),
            ("CONCENTRATE", "Concentrate"),
        ]:
            inventory = FeedInventory(
                farm_id=farm_id, ingredient=label, category=category, unit="kg", qty_on_hand=300.0
            )
            db.add(inventory)
            await db.flush()
            for quantity, price in [(Decimal("100"), Decimal("3")), (Decimal("200"), Decimal("4"))]:
                db.add(
                    Transaction(
                        farm_id=farm_id,
                        date=reference,
                        type="EXPENSE",
                        category="FEED",
                        amount=quantity * price,
                        feed_inventory_id=inventory.id,
                        feed_quantity_kg=quantity,
                        feed_unit_price_per_kg=price,
                    )
                )
        for category, amount in [
            ("LABOUR", "3000"),
            ("VET", "60"),
            ("MEDICINE", "60"),
            ("OTHER", "300"),
        ]:
            db.add(
                Transaction(
                    farm_id=farm_id,
                    date=reference,
                    type="EXPENSE",
                    category=category,
                    amount=Decimal(amount),
                )
            )
        # Income and future expenses lie outside the requested complete expense window.
        db.add(
            Transaction(
                farm_id=farm_id,
                date=reference,
                type="INCOME",
                category="OTHER",
                amount=Decimal("9000"),
            )
        )
        db.add(
            Transaction(
                farm_id=farm_id,
                date=reference + timedelta(days=1),
                type="EXPENSE",
                category="OTHER",
                amount=Decimal("9000"),
            )
        )
        await db.commit()
    response = await client.get(
        "/api/simulation/calibration", params={"lookback_months": 24}, headers=owner
    )
    assert response.status_code == 200, response.text
    body = json_object(response.json())
    assert date.fromisoformat(body["reference_date"]) == reference
    assumptions = SimulationAssumptions.model_validate(body["assumptions"])
    counts = {
        "does": 3,
        "bucks": 3,
        "female_kids": 1,
        "female_weaners": 1,
        "female_growers": 1,
        "male_kids": 1,
        "male_weaners": 1,
        "male_growers": 1,
    }
    assert {name: getattr(assumptions.herd, name) for name in counts} == counts
    evidence = {row["path"]: row for row in body["evidence"]}
    for name, count in counts.items():
        row = evidence["herd." + name]
        assert row["calibrated_value"] == count and row["sample_size"] == sum(counts.values())
        assert row["confidence"] == "high"
    for name in ("purchased_green_price_per_kg", "dry_price_per_kg", "concentrate_price_per_kg"):
        assert getattr(assumptions.feed, name) == pytest.approx(11 / 3)
        row = evidence["feed." + name]
        assert row["sample_size"] == 2 and row["confidence"] == "low"
        assert row["calibrated_value"] == pytest.approx(11 / 3)
    attendants = labour_units_for(
        counts["does"], assumptions.costs.family_labour, assumptions.costs.labour_per_head_threshold
    )
    assert attendants > 0
    assert assumptions.costs.labour_per_month * attendants == pytest.approx(3000)
    assert assumptions.costs.vet_per_animal_per_year * sum(counts.values()) == pytest.approx(
        120 * 12
    )
    assert assumptions.costs.misc_overhead_per_month == pytest.approx(300)
    assert evidence["costs.labour_per_month"]["sample_size"] == 1
    assert evidence["costs.vet_per_animal_per_year"]["sample_size"] == 2
    assert evidence["costs.misc_overhead_per_month"]["sample_size"] == 1
    assert any("1 month(s)" in warning and "24 month(s)" in warning for warning in body["warnings"])
    groups = {row["path"].split(".")[0] for row in body["evidence"]}
    assert body["coverage_score"] == pytest.approx(
        len(groups & {"herd", "growth", "reproduction", "mortality", "sales", "feed", "costs"}) / 7
    )


@pytest.mark.parametrize(
    "observed,expected",
    [
        ([(0, 10.0), (1, 8.0), (2, 4.0)], [(0, 22 / 3), (1, 22 / 3), (2, 22 / 3)]),
        ([(0, 10.0), (1, 4.0), (2, 8.0)], [(0, 7.0), (1, 7.0), (2, 8.0)]),
        ([(0, 10.0), (1, 8.0), (2, 6.0), (3, 20.0)], [(0, 8.0), (1, 8.0), (2, 8.0), (3, 20.0)]),
        ([(0, 2.0), (1, 4.0), (2, 8.0)], [(0, 2.0), (1, 4.0), (2, 8.0)]),
    ],
)
def test_actual_isotonic_growth_fit_conserves_equal_observation_weight(
    observed: list[tuple[int, float]], expected: list[tuple[int, float]]
) -> None:
    try:
        fitted = _isotonic_fit(observed)
    except (ArithmeticError, IndexError, KeyError, TypeError, ValueError) as exc:
        pytest.fail(f"Valid age-weight observations must produce their native isotonic fit: {exc}")
    assert [age for age, _ in fitted] == [age for age, _ in expected]
    assert [weight for _, weight in fitted] == pytest.approx([weight for _, weight in expected])


def test_legal_low_weight_measurements_produce_a_nondecreasing_native_curve() -> None:
    preset = list(get_preset("osmanabadi", "stall_fed").growth.weight_by_age_months)
    original = list(preset)
    curve = _curve_from_observations({1: 0.1, 5: 2.0, 10: 4.0}, preset)
    assert len(curve) == len(preset)
    assert preset == original
    assert all(0 < weight <= MAX_WEIGHT_KG for weight in curve)
    assert all(a <= b for a, b in pairwise(curve))


def test_native_ledger_month_fraction_matches_actual_elapsed_engine_days() -> None:
    start = date(2026, 1, 1)
    end = date(2026, 1, 31)
    try:
        fraction = _months_between(start, end)
    except (ArithmeticError, TypeError, ValueError) as exc:
        pytest.fail(f"An actual ordered ledger date window must produce elapsed months: {exc}")
    assert fraction == pytest.approx((end - start).days / 30.44)
    assert _months_between(start, start) == 0.0
    assert _months_between(end, start) == 0.0
