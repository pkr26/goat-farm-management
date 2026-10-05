"""Independent adversarial controls against audit-remediation commit ad2f616."""

from __future__ import annotations

import json
from typing import cast

import httpx
import pytest
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import ScreeningFinding, ScreeningImage, ScreeningRun
from app.services.screening.pipeline import run_screening_cycle
from app.services.screening.providers import ProviderAnswer
from app.services.screening.rotation import ProviderRotation
from app.services.screening.s3 import ScreeningStorage
from app.services.screening.specialists import (
    SpecialistKind,
    SpecialistParseError,
    parse_specialist_response,
)
from app.utils import today
from tests.conftest import owner_with_farm
from tests.test_screening import FakeStorage, _cycle_settings, _jpeg_bytes, _register_fake_objects

BAD_NEGATIVE_ANSWERS = [
    {},
    {"conditions": None},
    {"conditions": False},
    {"conditions": 0},
    {"conditions": ""},
    {"conditions": {}},
]


@pytest.mark.parametrize("answer", BAD_NEGATIVE_ANSWERS)
def test_specialist_rejects_unstructured_negative_answers(answer: dict[str, object]) -> None:
    with pytest.raises(SpecialistParseError):
        parse_specialist_response(json.dumps(answer), SpecialistKind.EYE)


class _RegionProvider:
    name = "independent-region-contract"
    model = "deterministic-audit-fixture"

    def __init__(self, eye_answer: dict[str, object], *, unspecified: bool = False) -> None:
        self.eye_answer = eye_answer
        self.unspecified = unspecified

    async def complete(self, image_jpeg: bytes, system_prompt: str) -> ProviderAnswer:
        if "veterinary specialist" not in system_prompt:
            answer: dict[str, object] = {
                "flagged": True,
                "confidence": 0.9,
                "observations": []
                if self.unspecified
                else [
                    {"region": "mouth", "label": "visible mouth concern", "confidence": 0.85},
                    {"region": "eye", "label": "visible eye concern", "confidence": 0.8},
                ],
            }
        elif "skin, lips" in system_prompt:
            answer = {
                "conditions": [
                    {"disease": "ORF", "confidence": 0.8, "severity": "moderate"},
                ]
            }
        else:
            answer = self.eye_answer
        return ProviderAnswer(
            text=json.dumps(answer),
            provider=self.name,
            model=self.model,
            latency_ms=1,
        )


@pytest.mark.parametrize("answer", BAD_NEGATIVE_ANSWERS)
async def test_malformed_negative_keeps_its_region_when_another_specialist_succeeds(
    client: httpx.AsyncClient,
    answer: dict[str, object],
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    storage = FakeStorage()
    storage.objects[f"raw/{farm_id}/{today().isoformat()}/independent.jpg"] = _jpeg_bytes(700, 400)
    async with get_sessionmaker()() as db:
        await _register_fake_objects(db, farm_id, storage)
        summary = await run_screening_cycle(
            db,
            _cycle_settings(),
            cast(ScreeningStorage, storage),
            ProviderRotation([_RegionProvider(answer)]),
        )
        image = (await db.execute(select(ScreeningImage))).scalar_one()
        findings = (await db.execute(select(ScreeningFinding))).scalars().all()
        assert image.status == "FLAGGED" and summary.flagged == 1
        assert {(finding.region, finding.label) for finding in findings} == {
            ("mouth", "ORF"),
            ("eye", "visible eye concern"),
        }
        runs = (await db.execute(select(ScreeningRun))).scalars().all()
        assert any(run.run_status == "ERROR" for run in runs)
        assert all(finding.status == "PENDING_REVIEW" for finding in findings)
        pending = await client.get(f"/api/screening/images/{image.id}", headers=owner)
        assert pending.status_code == 200, pending.text
        assert len(pending.json()["findings"]) == 2


@pytest.mark.parametrize("answer", [{"conditions": []}, {"conditions": False}, {}])
async def test_unspecified_flag_survives_valid_negative_or_malformed_specialist(
    client: httpx.AsyncClient,
    answer: dict[str, object],
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    storage = FakeStorage()
    storage.objects[f"raw/{farm_id}/{today().isoformat()}/unspecified.jpg"] = _jpeg_bytes(700, 400)
    async with get_sessionmaker()() as db:
        await _register_fake_objects(db, farm_id, storage)
        await run_screening_cycle(
            db,
            _cycle_settings(),
            cast(ScreeningStorage, storage),
            ProviderRotation([_RegionProvider(answer, unspecified=True)]),
        )
        findings = (await db.execute(select(ScreeningFinding))).scalars().all()
        assert len(findings) == 1
        assert findings[0].region == "general"
        assert findings[0].status == "PENDING_REVIEW"
        assert "no specific condition" in (findings[0].note or "")


@pytest.mark.parametrize("estimate_after_event", [False, True])
async def test_sale_birth_estimate_respects_health_fact_and_is_atomic(
    client: httpx.AsyncClient,
    estimate_after_event: bool,
) -> None:
    from datetime import timedelta

    from app.models import Animal, Transaction

    owner = await owner_with_farm(client)
    created = await client.post(
        "/api/animals",
        headers=owner,
        json={
            "tag_number": "INDEPENDENT-HEALTH-AGE",
            "sex": "M",
            "source": "PURCHASED",
            "current_bucket": "FOUNDATION",
            "historical_import_reason": "Unknown-age existing buck",
        },
    )
    assert created.status_code == 201, created.text
    animal_id = created.json()["id"]
    event_day = today() - timedelta(days=700)
    health = await client.post(
        "/api/health/events",
        headers=owner,
        json={
            "animal_id": animal_id,
            "type": "TREATMENT",
            "date": event_day.isoformat(),
            "product_name": "Recorded treatment",
            "cost": 0,
        },
    )
    assert health.status_code == 201, health.text
    estimate = event_day + timedelta(days=int(estimate_after_event))
    sold = await client.post(
        f"/api/animals/{animal_id}/status",
        headers=owner,
        json={
            "new_status": "SOLD",
            "sale_price": 2000,
            "estimated_dob": estimate.isoformat(),
        },
    )
    assert sold.status_code == (422 if estimate_after_event else 200), sold.text
    async with get_sessionmaker()() as db:
        animal = await db.get(Animal, animal_id)
        assert animal is not None
        assert animal.status == ("ACTIVE" if estimate_after_event else "SOLD")
        assert animal.estimated_dob == (None if estimate_after_event else estimate)
        sales = (
            (
                await db.execute(
                    select(Transaction).where(
                        Transaction.source_type == "ANIMAL_SALE",
                        Transaction.source_id == animal_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(sales) == int(not estimate_after_event)


async def test_large_expense_ledger_preserves_structured_feed_and_category_confidence(
    client: httpx.AsyncClient,
) -> None:
    from datetime import timedelta
    from decimal import Decimal

    from sqlalchemy import text

    from app.models import Animal, FeedInventory, Transaction
    from app.models.feed_rules import DRY_ROUGHAGE_INGREDIENT

    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    reference = today()
    async with get_sessionmaker()() as db:
        inventory = (
            await db.execute(
                select(FeedInventory).where(
                    FeedInventory.farm_id == farm_id,
                    FeedInventory.ingredient == DRY_ROUGHAGE_INGREDIENT,
                )
            )
        ).scalar_one()
        db.add(
            Animal(
                farm_id=farm_id,
                tag_number="LEDGER-ACTIVE",
                source="BORN",
                sex="F",
                current_bucket="RESTING",
                date_of_birth=reference - timedelta(days=800),
            )
        )
        db.add(
            Transaction(
                farm_id=farm_id,
                date=reference - timedelta(days=180),
                type="EXPENSE",
                category="FEED",
                amount=Decimal("800"),
                source_type="FEED_PURCHASE",
                source_id=20002,
                feed_inventory_id=inventory.id,
                feed_quantity_kg=Decimal("10"),
                feed_unit_price_per_kg=Decimal("80"),
            )
        )
        for category in ["LABOUR", "VET", "MEDICINE", "OTHER"]:
            db.add(
                Transaction(
                    farm_id=farm_id,
                    date=reference,
                    type="EXPENSE",
                    category=category,
                    amount=Decimal("600"),
                )
            )
            await db.execute(
                text(
                    "INSERT INTO transactions (farm_id, date, type, category, amount) "
                    "SELECT :farm, :date, 'INCOME', :category, 100 FROM generate_series(1,35)"
                ),
                {"farm": farm_id, "date": reference, "category": category},
            )
        await db.execute(
            text(
                "INSERT INTO transactions (farm_id, date, type, category, amount, source_type, "
                "source_id, feed_inventory_id, feed_quantity_kg, feed_unit_price_per_kg) "
                "SELECT :farm, :date, 'EXPENSE', 'FEED', 2, 'FEED_PURCHASE', seq, :stock, 1, 2 "
                "FROM generate_series(1,20001) seq"
            ),
            {"farm": farm_id, "date": reference, "stock": inventory.id},
        )
        await db.commit()
    response = await client.get("/api/simulation/calibration", headers=owner)
    assert response.status_code == 200, response.text
    body = response.json()
    evidence = {item["path"]: item for item in body["evidence"]}
    assert body["assumptions"]["feed"]["dry_price_per_kg"] == pytest.approx(40802 / 20011)
    assert evidence["feed.dry_price_per_kg"]["sample_size"] == 20002
    assert body["assumptions"]["costs"]["misc_overhead_per_month"] == 100
    assert body["assumptions"]["costs"]["vet_per_animal_per_year"] == 2400
    for path, count in [
        ("costs.labour_per_month", 1),
        ("costs.vet_per_animal_per_year", 2),
        ("costs.misc_overhead_per_month", 1),
    ]:
        assert evidence[path]["sample_size"] == count
        assert evidence[path]["confidence"] == "low"


@pytest.mark.parametrize("seed", range(12))
def test_randomized_breeding_cost_conservation_through_all_exit_paths(seed: int) -> None:
    import random

    from app.simulation.assumptions import SimulationAssumptions
    from app.simulation.engine import _run_core
    from tests.test_remediation_26_disposal import _assumptions, _event

    rng = random.Random(seed)
    assumptions = _assumptions(60)
    assumptions.costs.breeding_stock_useful_life_months = rng.choice([1, 7, 19, 60, 96])
    assumptions.culling.max_doe_age_months = rng.choice([36, 48, 60, 120])
    assumptions.culling.doe_cull_rate_annual = rng.choice([0, 0.2, 0.7, 1])
    assumptions.culling.buck_rotation_years = rng.choice([1, 2, 3])
    assumptions.herd.auto_purchase_bucks = bool(seed % 2)
    assumptions.mortality.adult = rng.choice([0, 0.2, 0.8])
    assumptions.reproduction.conception_rate = rng.choice([0, 0.5, 0.9])
    assumptions.reproduction.max_services_before_cull = rng.choice([0, 1, 3])
    events = []
    for month in range(1, 61):
        if rng.random() < 0.3:
            events.append(
                _event(
                    month,
                    "purchase",
                    rng.choice(["doe", "buck"]),
                    rng.uniform(0.5, 5),
                    rng.uniform(1000, 30000),
                )
            )
        if rng.random() < 0.3:
            events.append(
                _event(
                    month,
                    "sale",
                    rng.choice(["doe", "buck"]),
                    rng.uniform(0.5, 5),
                    rng.uniform(1000, 30000),
                )
            )
    assumptions.events = events
    result = _run_core(SimulationAssumptions.model_validate(assumptions.model_dump()))
    basis = sum(month.breeding_stock_capex for month in result.months)
    released = sum(
        month.depreciation + month.breeding_stock_disposal_cost for month in result.months
    )
    retained = result.terminal_value_breakdown.breeding_stock
    assert released + retained == pytest.approx(basis, abs=1e-6)
    assert all(
        month.depreciation >= 0 and month.breeding_stock_disposal_cost >= 0
        for month in result.months
    )
    if result.months[-1].total_herd == 0:
        assert retained == pytest.approx(0, abs=1e-6)


async def test_creation_day_feed_backfill_survives_daylight_saving_change(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import date, datetime
    from decimal import Decimal

    import app.api.feeding as feeding
    import app.services.chronology as chronology
    from app.models import Farm, FeedInventory
    from app.models.feed_rules import DRY_ROUGHAGE, DRY_ROUGHAGE_INGREDIENT

    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    # UTC November 2 is local November 1, before New York's 2025 DST rollback.
    # Backfill occurs November 3 after the offset changed from UTC-4 to UTC-5.
    monkeypatch.setattr(feeding, "today", lambda *_args: date(2025, 11, 3))
    monkeypatch.setattr(chronology, "today", lambda *_args: date(2025, 11, 3))
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        farm.timezone = "America/New_York"
        farm.created_at = datetime(2025, 11, 2, 3, 30)
        stock = (
            await db.execute(
                select(FeedInventory).where(
                    FeedInventory.farm_id == farm_id,
                    FeedInventory.ingredient == DRY_ROUGHAGE_INGREDIENT,
                )
            )
        ).scalar_one()
        stock.qty_on_hand = 10
        await db.commit()
    payload = {"bucket": "QUARANTINE", "shift": "NIGHT", "recipe_code": DRY_ROUGHAGE, "qty_kg": 1}
    rejected = await client.post(
        "/api/feeding/dispense",
        headers=owner,
        json={
            **payload,
            "date": "2025-10-31",
        },
    )
    assert rejected.status_code == 422, rejected.text
    accepted = await client.post(
        "/api/feeding/dispense",
        headers=owner,
        json={
            **payload,
            "date": "2025-11-01",
        },
    )
    assert accepted.status_code == 201, accepted.text
    async with get_sessionmaker()() as db:
        stock = (
            await db.execute(
                select(FeedInventory).where(
                    FeedInventory.farm_id == farm_id,
                    FeedInventory.ingredient == DRY_ROUGHAGE_INGREDIENT,
                )
            )
        ).scalar_one()
        assert stock.qty_on_hand == Decimal("9")


async def test_unbalanced_sale_counts_preserve_month_prices_through_market_function(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import date
    from decimal import Decimal

    import app.services.simulation_calibration as calibration
    from app.models import Animal
    from app.simulation.assumptions import SimulationAssumptions
    from app.simulation.market import meat_price_for_month

    monkeypatch.setattr(calibration, "today", lambda *_args: date(2026, 10, 4))
    owner = await owner_with_farm(client)
    async with get_sessionmaker()() as db:
        for month, price, count in [(1, 100, 3), (2, 200, 3), (3, 300, 15), (4, 400, 3)]:
            for index in range(count):
                db.add(
                    Animal(
                        farm_id=int(owner["X-Farm-Id"]),
                        tag_number=f"IMBALANCED-{month}-{index}",
                        sex="M",
                        source="BORN",
                        current_bucket="MALE_KIDS",
                        date_of_birth=date(2024, 1, 1),
                        status="SOLD",
                        status_date=date(2026, month, 15),
                        sale_weight_kg=20,
                        sale_price=Decimal(20 * price),
                    )
                )
        await db.commit()
    response = await client.get("/api/simulation/calibration", headers=owner)
    assert response.status_code == 200, response.text
    result = SimulationAssumptions.model_validate(response.json()["assumptions"])
    result.sales.festival_sale_months = []
    result.sales.annual_livestock_price_growth_rate = 0
    fitted = [
        meat_price_for_month(result.sales, simulation_month=month, calendar_month=month)
        for month in range(1, 13)
    ]
    assert fitted == pytest.approx([100, 200, 300, 400, *([300] * 8)])
    assert result.sales.meat_price_per_kg == pytest.approx(sum(fitted) / 12)


async def test_mortality_counts_linked_neonatal_postweaning_and_legacy_deaths_once(
    client: httpx.AsyncClient,
) -> None:
    from datetime import timedelta

    from app.models import Animal, BreedingRecord, BucketMove, KiddingRecord, KidEntry

    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    born = today() - timedelta(days=180)
    ids: list[int | None] = []
    async with get_sessionmaker()() as db:
        sire = Animal(
            farm_id=farm_id,
            tag_number="MIX-SIRE",
            sex="M",
            source="BORN",
            current_bucket="FOUNDATION",
            date_of_birth=born - timedelta(days=900),
        )
        db.add(sire)
        await db.flush()
        for litter_number in range(4):
            dam = Animal(
                farm_id=farm_id,
                tag_number=f"MIX-DAM-{litter_number}",
                sex="F",
                source="BORN",
                current_bucket="RESTING",
                date_of_birth=born - timedelta(days=900),
            )
            db.add(dam)
            await db.flush()
            bred = born - timedelta(days=150)
            breeding = BreedingRecord(
                farm_id=farm_id,
                doe_id=dam.id,
                buck_id=sire.id,
                breeding_date=bred,
                ultrasound_date=bred + timedelta(days=35),
                ultrasound_done=True,
                ultrasound_result_date=bred + timedelta(days=35),
                pregnant=True,
                expected_kidding_date=born,
                outcome="CONFIRMED_PREGNANT",
            )
            db.add(breeding)
            await db.flush()
            litter = KiddingRecord(
                farm_id=farm_id, doe_id=dam.id, date=born, breeding_record_id=breeding.id
            )
            db.add(litter)
            await db.flush()
            for ordinal in range(3):
                index = litter_number * 3 + ordinal
                tag = f"MIX-KID-{index}"
                animal_id = None
                if index != 4:  # Older ledger can retain a DIED birth entry without an animal link.
                    kid = Animal(
                        farm_id=farm_id,
                        tag_number=tag,
                        sex="F",
                        source="BORN",
                        current_bucket="RECOVERY" if index == 0 else "FEMALE_KIDS",
                        date_of_birth=born,
                        dam_id=dam.id,
                        sire_id=sire.id,
                        birth_type="TRIPLET",
                        birth_weight=3,
                    )
                    db.add(kid)
                    await db.flush()
                    animal_id = kid.id
                    db.add(
                        BucketMove(
                            farm_id=farm_id,
                            animal_id=kid.id,
                            from_bucket=None,
                            to_bucket="RECOVERY",
                            effective_date=born,
                            reason="Born",
                        )
                    )
                    if index != 0:
                        db.add(
                            BucketMove(
                                farm_id=farm_id,
                                animal_id=kid.id,
                                from_bucket="RECOVERY",
                                to_bucket="FEMALE_KIDS",
                                effective_date=born + timedelta(days=60),
                                reason="Weaned",
                            )
                        )
                ids.append(animal_id)
                db.add(
                    KidEntry(
                        farm_id=farm_id,
                        kidding_record_id=litter.id,
                        tag=tag,
                        sex="F",
                        birth_weight=3,
                        status="DIED" if index == 4 else "ALIVE",
                        animal_id=animal_id,
                        mortality_reported_at=born + timedelta(days=50) if index == 4 else None,
                    )
                )
        await db.commit()
    for index, day, status in [(0, 30, "DEAD"), (1, 70, "DEAD"), (2, 91, "DEAD"), (3, 70, "SOLD")]:
        payload = {"new_status": status, "date": (born + timedelta(days=day)).isoformat()}
        if status == "DEAD":
            payload["mortality_cause_code"] = "PNEUMONIA"
            payload["mortality_reported_at"] = (born + timedelta(days=150)).isoformat()
        response = await client.post(
            f"/api/animals/{ids[index]}/status", headers=owner, json=payload
        )
        assert response.status_code == 200, response.text
    response = await client.get("/api/simulation/calibration", headers=owner)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["assumptions"]["mortality"]["kid_pre_weaning"] == pytest.approx(3 / 11)
    evidence = next(row for row in body["evidence"] if row["path"] == "mortality.kid_pre_weaning")
    assert evidence["sample_size"] == 11
    async with get_sessionmaker()() as db:
        neonatal = (
            await db.execute(select(KidEntry).where(KidEntry.animal_id == ids[0]))
        ).scalar_one()
        weaned = (
            await db.execute(select(KidEntry).where(KidEntry.animal_id == ids[1]))
        ).scalar_one()
        assert neonatal.status == "DIED"
        assert weaned.status == "ALIVE"
