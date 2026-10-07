"""Independent semantic oracles for campaigns 32, 36 and 38.

Staged outside the backend until the initial campaign baseline finishes.
"""

import math
import random
from datetime import date, timedelta
from decimal import Decimal

import httpx
import pytest

import app.services.simulation_calibration as calibration
from app.db import get_sessionmaker
from app.models import Animal, WeightRecord
from app.simulation.assumptions import RiskVariable, SimulationAssumptions
from app.simulation.engine import run_simulation
from app.simulation.montecarlo import _apply_annual_price_variation, _correlated_draws
from app.simulation.planner import SaleTarget, _match_target_fills
from app.simulation.results import EventFill
from app.simulation.shocks import MonthlyShockPath
from app.utils import today
from tests.conftest import owner_with_farm
from tests.test_finance_extended import get_dashboard


class _FixedFactorRandom(random.Random):
    """One isolated market/climate/disease state, without residual noise."""

    def __init__(self, factors: tuple[float, float, float]) -> None:
        super().__init__(0)
        self.values = iter([*factors, *([0.0] * 9)])

    def normalvariate(self, mu: float = 0.0, sigma: float = 1.0) -> float:
        return next(self.values)


@pytest.mark.parametrize(
    "eligible_cost, estimate", [(400_000.0, 200_000.0), (3_000_000.0, 1_000_000.0)]
)
def test_nlm_estimate_is_half_eligible_cost_then_limited_by_unit_cap(
    eligible_cost: float,
    estimate: float,
) -> None:
    assumptions = SimulationAssumptions()
    assumptions.meta.horizon_months = 12
    assumptions.finance.nlm_subsidy = True
    assumptions.finance.nlm_unit_females = 100
    assumptions.finance.nlm_unit_males = 5
    assumptions.finance.nlm_eligible_capital_cost = eligible_cost
    result = run_simulation(assumptions, with_break_even=False)
    assert result.metrics.subsidy_estimate_amount == estimate
    assert result.metrics.subsidy_status == "estimate_only"
    assert result.metrics.subsidy_amount == 0.0
    assert all(row.subsidy_receipt == 0.0 for row in result.months)
    assert result.metrics.equity == pytest.approx(
        result.metrics.project_cost - result.metrics.loan_amount
    )


@pytest.mark.parametrize("factor", ["climate", "disease"])
def test_adverse_latent_factors_lower_conception(factor: str) -> None:
    variables = {
        name: RiskVariable(low=0.5, high=1.5)
        for name in (
            "meat_price",
            "feed_price",
            "adult_mortality",
            "kid_mortality",
            "litter_size",
            "conception_rate",
            "fodder_yield",
            "operating_cost",
            "milk_price",
        )
    }
    adverse = (0.0, 1.0, 0.0) if factor == "climate" else (0.0, 0.0, 1.0)
    benign = (-adverse[0], -adverse[1], -adverse[2])
    worse = _correlated_draws(_FixedFactorRandom(adverse), variables, 0.9)
    better = _correlated_draws(_FixedFactorRandom(benign), variables, 0.9)

    assert worse["adult_mortality"] > better["adult_mortality"]
    assert worse["kid_mortality"] > better["kid_mortality"]
    assert worse["conception_rate"] < 1.0 < better["conception_rate"]
    if factor == "climate":
        assert worse["feed_price"] > better["feed_price"]
        assert worse["fodder_yield"] < better["fodder_yield"]
    else:
        assert worse["litter_size"] < better["litter_size"]


def test_annual_price_risk_splits_variance_equally() -> None:
    assumptions = SimulationAssumptions()
    assumptions.meta.horizon_months = 12
    assumptions.risk.meat_price = RiskVariable(low=0.5, high=1.5)
    assumptions.risk.feed_price = RiskVariable(low=0.5, high=1.5)
    path = MonthlyShockPath.neutral(12)
    # Zero innovations leave only the explicitly documented mean correction.
    effective = _apply_annual_price_variation(
        path,
        {"meat_price": 1.2, "feed_price": 0.8},
        assumptions,
        _FixedFactorRandom((0.0, 0.0, 0.0)),
    )
    assert effective["meat_price"] == pytest.approx(1.2 ** math.sqrt(0.5))
    assert effective["feed_price"] == pytest.approx(0.8 ** math.sqrt(0.5))
    # Triangular(.5, 1, 1.5) has variance 1/24. Half is assigned to
    # annual log shocks, whose lognormal mean correction is exp(-1/96).
    assert path.meat_price == pytest.approx([math.exp(-1.0 / 96.0)] * 12)
    assert path.feed_price == pytest.approx([math.exp(-1.0 / 96.0)] * 12)


def test_duplicate_sale_targets_keep_their_own_ordered_fills() -> None:
    targets = [
        SaleTarget(month=2, animal_class="male_kid", count=7.0),
        SaleTarget(month=1, animal_class="doe", count=3.0),
        SaleTarget(month=2, animal_class="male_kid", count=4.0),
    ]
    fills = [
        EventFill(
            month=1,
            kind="sale",
            animal_class="doe",
            requested=3.0,
            filled=2.0,
            shortfall=1.0,
            price_per_head=10.0,
            revenue=20.0,
        ),
        EventFill(
            month=2,
            kind="purchase",
            animal_class="male_kid",
            requested=10.0,
            filled=10.0,
            shortfall=0.0,
            price_per_head=8.0,
            revenue=80.0,
        ),
        EventFill(
            month=2,
            kind="sale",
            animal_class="male_kid",
            requested=7.0,
            filled=7.0,
            shortfall=0.0,
            price_per_head=20.0,
            revenue=140.0,
        ),
        EventFill(
            month=2,
            kind="sale",
            animal_class="male_kid",
            requested=4.0,
            filled=1.0,
            shortfall=3.0,
            price_per_head=22.0,
            revenue=22.0,
        ),
    ]
    matched = _match_target_fills(fills, targets)
    assert [item.filled for item in matched] == [7.0, 2.0, 1.0]
    assert [item.shortfall for item in matched] == [0.0, 1.0, 3.0]
    assert [item.revenue for item in matched] == [140.0, 20.0, 22.0]
    assert [item.met for item in matched] == [True, False, False]


async def test_dashboard_readiness_uses_weights_observed_by_today(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    reference = today()
    async with get_sessionmaker()() as db:
        for sex, bucket, tag, current_weight, future_weight in (
            ("F", "FOUNDATION", "F-FUTURE-HEAVY", 20, 30),
            ("M", "MALE_KIDS", "M-FUTURE-HEAVY", 20, 30),
            ("F", "FOUNDATION", "F-READY-NOW", 23, 15),
            ("M", "MALE_KIDS", "M-READY-NOW", 25, 15),
        ):
            animal = Animal(
                farm_id=farm_id,
                tag_number=tag,
                sex=sex,
                source="BORN",
                birth_type="SINGLE",
                birth_weight=Decimal("3.0"),
                date_of_birth=reference - timedelta(days=500),
                current_bucket=bucket,
            )
            db.add(animal)
            await db.flush()
            # Legacy/imported history can contain a future row even though
            # current write paths reject it. A present readiness decision
            # must use the current measurement in either direction.
            db.add_all(
                [
                    WeightRecord(
                        farm_id=farm_id,
                        animal_id=animal.id,
                        date=reference,
                        weight_kg=Decimal(current_weight),
                    ),
                    WeightRecord(
                        farm_id=farm_id,
                        animal_id=animal.id,
                        date=reference + timedelta(days=30),
                        weight_kg=Decimal(future_weight),
                    ),
                ]
            )
        await db.commit()

    dashboard = await get_dashboard(client, owner)
    actual = {row["animal"]["tag_number"]: row["to"] for row in dashboard["suggestions"]}
    assert actual == {"F-READY-NOW": "BREEDING", "M-READY-NOW": "SELL"}
    assert dashboard["suggestions_total"] == 2
    male = next(row for row in dashboard["suggestions"] if row["to"] == "SELL")
    assert "25.0 kg" in male["reason"]


async def test_calibration_counts_exact_class_exposure_until_exit(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    reference = date(2026, 10, 1)
    monkeypatch.setattr(calibration, "today", lambda timezone: reference)
    birth = date(2026, 2, 1)
    async with get_sessionmaker()() as db:
        for index in range(12):
            status, departed = "ACTIVE", None
            if index == 0:
                status, departed = "DEAD", date(2026, 6, 15)
            elif index == 1:
                status, departed = "DEAD", date(2026, 8, 4)
            elif index == 2:
                status, departed = "SOLD", date(2026, 8, 20)
            purchased = (
                date(2026, 6, 20) if index == 10 else date(2026, 8, 10) if index == 11 else None
            )
            db.add(
                Animal(
                    farm_id=farm_id,
                    tag_number=f"EXPOSURE-YOUNG-{index}",
                    sex="M",
                    source="PURCHASED" if purchased else "BORN",
                    birth_type="SINGLE" if not purchased else None,
                    date_of_birth=birth,
                    purchase_date=purchased,
                    current_bucket="MALE_KIDS",
                    status=status,
                    status_date=departed,
                    sale_price=Decimal("1000") if status == "SOLD" else None,
                    sale_weight_kg=Decimal("25") if status == "SOLD" else None,
                )
            )
        for index in range(10):
            status, departed = "ACTIVE", None
            if index == 0:
                status, departed = "DEAD", date(2026, 9, 15)
            elif index == 1:
                status, departed = "SOLD", date(2026, 8, 31)
            db.add(
                Animal(
                    farm_id=farm_id,
                    tag_number=f"EXPOSURE-UNKNOWN-{index}",
                    sex="F",
                    source="PURCHASED",
                    purchase_date=date(2026, 8, 1) if index == 9 else date(2026, 5, 1),
                    current_bucket="FOUNDATION",
                    status=status,
                    status_date=departed,
                    sale_price=Decimal("1000") if status == "SOLD" else None,
                    sale_weight_kg=Decimal("25") if status == "SOLD" else None,
                )
            )
        await db.commit()

    response = await client.get(
        "/api/simulation/calibration", params={"lookback_months": 24}, headers=owner
    )
    assert response.status_code == 200, response.text
    body = response.json()
    evidence = {item["path"]: item for item in body["evidence"]}
    # The 30.44-day boundaries are May 3 and August 3, respectively.
    # Hand-count each partial purchase/exit span, including the living
    # animals that have since aged into the following class.
    expected_days = {
        "kid_post_weaning": 43 + 9 * 92 + 44,
        "grower": 1 + 17 + 7 * 59 + 59 + 52,
        "adult": 137 + 122 + 7 * 153 + 61,
    }
    expected_samples = {"kid_post_weaning": 11, "grower": 11, "adult": 10}
    expected_deaths = {"kid_post_weaning": 1, "grower": 1, "adult": 1}
    for group, days in expected_days.items():
        period = 3.0 if group == "kid_post_weaning" else 12.0
        expected = 1.0 - math.exp(-expected_deaths[group] * period * 30.44 / days)
        item = evidence[f"mortality.{group}"]
        assert item["sample_size"] == expected_samples[group]
        assert item["calibrated_value"] == pytest.approx(expected)
        assert body["assumptions"]["mortality"][group] == pytest.approx(expected)
