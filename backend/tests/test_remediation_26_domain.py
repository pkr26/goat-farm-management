"""Acceptance regressions for independently audited domain findings D26-01..09."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import cast

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.db import get_sessionmaker
from app.models import Animal, Farm, FeedInventory, ScreeningFinding, ScreeningImage, Transaction
from app.models.feed_rules import DRY_ROUGHAGE, DRY_ROUGHAGE_INGREDIENT
from app.services.screening.pipeline import run_screening_cycle
from app.services.screening.providers import ProviderAnswer
from app.services.screening.rotation import ProviderRotation
from app.services.screening.s3 import ScreeningStorage
from app.utils import today
from tests.conftest import owner_with_farm
from tests.test_screening import FakeStorage, _cycle_settings, _jpeg_bytes, _register_fake_objects


@pytest.mark.parametrize("birth_offset, accepted", [(365, False), (730, True), (900, True)])
async def test_sale_estimate_respects_acquisition_and_failed_sale_is_atomic(
    client: httpx.AsyncClient, birth_offset: int, accepted: bool
) -> None:
    owner = await owner_with_farm(client)
    created = await client.post(
        "/api/animals",
        headers=owner,
        json={
            "tag_number": "UNKNOWN-AGE",
            "sex": "M",
            "source": "PURCHASED",
            "current_bucket": "FOUNDATION",
            "purchase_date": (today() - timedelta(days=730)).isoformat(),
            "historical_import_reason": "Import previously acquired goat with unknown age",
        },
    )
    assert created.status_code == 201, created.text
    animal_id = created.json()["id"]
    response = await client.post(
        f"/api/animals/{animal_id}/status",
        headers=owner,
        json={
            "new_status": "SOLD",
            "estimated_dob": (today() - timedelta(days=birth_offset)).isoformat(),
            "sale_price": 1000,
        },
    )
    assert response.status_code == (200 if accepted else 422), response.text
    async with get_sessionmaker()() as db:
        animal = await db.get(Animal, animal_id)
        assert animal is not None
        assert animal.status == ("SOLD" if accepted else "ACTIVE")
        assert (animal.estimated_dob is not None) == accepted
        sales = (
            (
                await db.execute(
                    select(Transaction).where(
                        Transaction.source_type == "ANIMAL_SALE", Transaction.source_id == animal_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(sales) == int(accepted)


async def test_sale_estimate_cannot_postdate_a_recorded_weight_without_acquisition(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    response = await client.post(
        "/api/animals",
        headers=owner,
        json={
            "tag_number": "UNKNOWN-AGE-WEIGHED",
            "sex": "M",
            "source": "PURCHASED",
            "current_bucket": "FOUNDATION",
            "historical_import_reason": "Existing unknown-age goat",
        },
    )
    assert response.status_code == 201
    animal_id = response.json()["id"]
    weighed = await client.post(
        f"/api/animals/{animal_id}/weight",
        headers=owner,
        json={
            "weight_kg": 20,
            "date": (today() - timedelta(days=600)).isoformat(),
        },
    )
    assert weighed.status_code == 201, weighed.text
    sold = await client.post(
        f"/api/animals/{animal_id}/status",
        headers=owner,
        json={
            "new_status": "SOLD",
            "estimated_dob": (today() - timedelta(days=365)).isoformat(),
            "sale_price": 1000,
        },
    )
    assert sold.status_code == 422
    assert "earliest recorded lifecycle event" in sold.text


@pytest.mark.parametrize("birth_field", ["date_of_birth", "estimated_dob"])
async def test_database_rejects_purchase_before_effective_birth(
    client: httpx.AsyncClient, birth_field: str
) -> None:
    owner = await owner_with_farm(client)
    async with get_sessionmaker()() as db:
        animal = Animal(
            farm_id=int(owner["X-Farm-Id"]),
            tag_number="DB-BIRTH",
            sex="M",
            source="PURCHASED",
            current_bucket="FOUNDATION",
            purchase_date=today(),
        )
        db.add(animal)
        await db.commit()
        with pytest.raises(IntegrityError, match="ck_animals_purchase_after_birth"):
            await db.execute(
                text(f"UPDATE animals SET {birth_field} = :born WHERE id = :id"),
                {"born": today() + timedelta(days=1), "id": animal.id},
            )
        await db.rollback()


async def test_full_ledger_aggregate_excludes_income_voids_outside_window_and_other_farms(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    other = await owner_with_farm(client, "other-ledger@example.test")
    farm_id = int(owner["X-Farm-Id"])
    reference = today()
    async with get_sessionmaker()() as db:
        db.add_all(
            [
                Transaction(
                    farm_id=farm_id,
                    date=reference - timedelta(days=180),
                    type="EXPENSE",
                    category="OTHER",
                    amount=Decimal("60000"),
                ),
                Transaction(
                    farm_id=farm_id,
                    date=reference,
                    type="EXPENSE",
                    category="OTHER",
                    amount=Decimal("1000"),
                ),
                Transaction(
                    farm_id=farm_id,
                    date=reference - timedelta(days=800),
                    type="EXPENSE",
                    category="OTHER",
                    amount=Decimal("900000"),
                ),
                Transaction(
                    farm_id=farm_id,
                    date=reference,
                    type="EXPENSE",
                    category="OTHER",
                    amount=Decimal("900000"),
                    voided_at=datetime.combine(reference, datetime.min.time()),
                    void_reason="Incorrect duplicate charge",
                ),
                Transaction(
                    farm_id=int(other["X-Farm-Id"]),
                    date=reference,
                    type="EXPENSE",
                    category="OTHER",
                    amount=Decimal("900000"),
                ),
            ]
        )
        await db.execute(
            text(
                "INSERT INTO transactions (farm_id, date, type, category, amount) "
                "SELECT :farm, :date, 'INCOME', 'OTHER', 1 FROM generate_series(1,20001)"
            ),
            {"farm": farm_id, "date": reference},
        )
        await db.commit()
    response = await client.get(
        "/api/simulation/calibration", headers=owner, params={"lookback_months": 12}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    evidence = next(
        item for item in body["evidence"] if item["path"] == "costs.misc_overhead_per_month"
    )
    assert body["assumptions"]["costs"]["misc_overhead_per_month"] == pytest.approx(61000 / 6)
    assert evidence["sample_size"] == 2
    assert evidence["confidence"] == "low"
    assert not any("recent ledger rows" in warning for warning in body["warnings"])


@pytest.mark.parametrize(
    "timezone, created_hour, local_shift",
    [
        ("America/Phoenix", 0, -1),
        ("Asia/Kolkata", 23, 1),
        ("UTC", 12, 0),
    ],
)
async def test_first_business_day_feeding_accepts_creation_day_and_rejects_prior_day(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    timezone: str,
    created_hour: int,
    local_shift: int,
) -> None:
    import app.api.feeding as feeding
    import app.services.chronology as chronology

    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    utc_day = today() - timedelta(days=3)
    local_day = utc_day + timedelta(days=local_shift)
    monkeypatch.setattr(feeding, "today", lambda *_args: local_day)
    monkeypatch.setattr(chronology, "today", lambda *_args: local_day)
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        farm.timezone = timezone
        farm.created_at = datetime.combine(utc_day, datetime.min.time()) + timedelta(
            hours=created_hour, minutes=30
        )
        stock = (
            await db.execute(
                select(FeedInventory).where(
                    FeedInventory.farm_id == farm_id,
                    FeedInventory.ingredient == DRY_ROUGHAGE_INGREDIENT,
                )
            )
        ).scalar_one()
        stock.qty_on_hand = 100.0
        await db.commit()
    payload = {"bucket": "QUARANTINE", "shift": "NIGHT", "recipe_code": DRY_ROUGHAGE, "qty_kg": 1.0}
    denied = await client.post(
        "/api/feeding/dispense",
        headers=owner,
        json={**payload, "date": (local_day - timedelta(days=1)).isoformat()},
    )
    assert denied.status_code == 422
    accepted = await client.post(
        "/api/feeding/dispense", headers=owner, json={**payload, "date": local_day.isoformat()}
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
        assert stock.qty_on_hand == Decimal("99")


class _ScreeningProvider:
    name = "contract-regression"
    model = "synthetic"

    def __init__(self, *, malformed_eye: bool) -> None:
        self.malformed_eye = malformed_eye

    async def complete(self, image_jpeg: bytes, system_prompt: str) -> ProviderAnswer:
        response: dict[str, object]
        if "veterinary specialist" not in system_prompt:
            response = {
                "flagged": True,
                "confidence": 0.9,
                "observations": (
                    [
                        {"region": "mouth", "label": "mouth lesion", "confidence": 0.9},
                        {"region": "eye", "label": "eye concern", "confidence": 0.8},
                    ]
                    if self.malformed_eye
                    else []
                ),
            }
        elif not self.malformed_eye:
            response = {"conditions": []}
        elif "skin, lips" in system_prompt:
            response = {
                "conditions": [{"disease": "ORF", "confidence": 0.8, "severity": "moderate"}]
            }
        else:
            response = {
                "conditions": [
                    {"disease": "EYE_TRAUMA", "confidence": "high", "severity": "severe"}
                ]
            }
        return ProviderAnswer(
            text=json.dumps(response), provider=self.name, model=self.model, latency_ms=1
        )


@pytest.mark.parametrize("malformed_eye", [False, True])
async def test_flagged_cascade_keeps_a_reviewable_concern_and_every_failed_region(
    client: httpx.AsyncClient,
    malformed_eye: bool,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    storage = FakeStorage()
    storage.objects[f"raw/{farm_id}/{today().isoformat()}/contract.jpg"] = _jpeg_bytes(500, 300)
    async with get_sessionmaker()() as db:
        await _register_fake_objects(db, farm_id, storage)
        summary = await run_screening_cycle(
            db,
            _cycle_settings(),
            cast(ScreeningStorage, storage),
            ProviderRotation([_ScreeningProvider(malformed_eye=malformed_eye)]),
        )
        image = (await db.execute(select(ScreeningImage))).scalar_one()
        findings = (await db.execute(select(ScreeningFinding))).scalars().all()
        assert image.status == "FLAGGED" and summary.flagged == 1
        if malformed_eye:
            assert {(f.region, f.label) for f in findings} == {
                ("mouth", "ORF"),
                ("eye", "eye concern"),
            }
        else:
            assert len(findings) == 1
            assert findings[0].region == "general"
            assert "no specific condition" in (findings[0].note or "")
        assert all(f.status == "PENDING_REVIEW" for f in findings)


@pytest.mark.parametrize(
    "prices", [(100, 100, 100, 400), (100, 100, 100, 25), (100, 100, 100, 1000)]
)
async def test_seasonal_fit_preserves_clamped_monthly_levels_and_evidence(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    prices: tuple[int, ...],
) -> None:
    import app.services.simulation_calibration as calibration

    monkeypatch.setattr(calibration, "today", lambda *_args: date(2026, 10, 4))
    owner = await owner_with_farm(client)
    async with get_sessionmaker()() as db:
        for month, price in enumerate(prices, 1):
            for index in range(3):
                db.add(
                    Animal(
                        farm_id=int(owner["X-Farm-Id"]),
                        tag_number=f"SEASON-{month}-{index}",
                        sex="M",
                        source="PURCHASED",
                        current_bucket="FOUNDATION",
                        date_of_birth=date(2024, 1, 1),
                        purchase_date=date(2024, 6, 1),
                        status="SOLD",
                        status_date=date(2026, month, 15),
                        sale_weight_kg=30,
                        sale_price=Decimal(price * 30),
                    )
                )
        await db.commit()
    response = await client.get("/api/simulation/calibration", headers=owner)
    assert response.status_code == 200, response.text
    body = response.json()
    sales = body["assumptions"]["sales"]
    curve = sales["monthly_meat_price_multipliers"]
    implied = [sales["meat_price_per_kg"] * factor for factor in curve]
    assert sum(curve) / 12 == pytest.approx(1)
    assert implied[:4] == pytest.approx([max(25, min(400, price)) for price in prices])
    assert implied[4:] == pytest.approx([100] * 8)
    evidence = {item["path"]: item for item in body["evidence"]}
    assert evidence["sales.meat_price_per_kg"]["calibrated_value"] == sales["meat_price_per_kg"]
    assert evidence["sales.monthly_meat_price_multipliers"]["calibrated_value"] == curve


@pytest.mark.parametrize(
    "exit_day, status, expected_rate, denominator",
    [
        (70, "DEAD", 0.1, 10),
        (90, "DEAD", 0.1, 10),
        (91, "DEAD", 0, 10),
        (120, "DEAD", 0, 10),
        (70, "CULLED", 0, 9),
    ],
)
async def test_mortality_uses_actual_death_with_phase_boundary_and_early_exit_censoring(
    client: httpx.AsyncClient,
    exit_day: int,
    status: str,
    expected_rate: float,
    denominator: int,
) -> None:
    from app.models import BreedingRecord, BucketMove, KiddingRecord, KidEntry

    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    born = today() - timedelta(days=180)
    ids: list[int] = []
    async with get_sessionmaker()() as db:
        buck = Animal(
            farm_id=farm_id,
            tag_number="MORT-SIRE",
            sex="M",
            source="BORN",
            current_bucket="FOUNDATION",
            date_of_birth=born - timedelta(days=900),
        )
        db.add(buck)
        await db.flush()
        for index in range(5):
            doe = Animal(
                farm_id=farm_id,
                tag_number=f"MORT-DAM-{index}",
                sex="F",
                source="BORN",
                current_bucket="RESTING",
                date_of_birth=born - timedelta(days=900),
            )
            db.add(doe)
            await db.flush()
            bred = born - timedelta(days=150)
            breeding = BreedingRecord(
                farm_id=farm_id,
                doe_id=doe.id,
                buck_id=buck.id,
                breeding_date=bred,
                ultrasound_date=bred + timedelta(days=35),
                ultrasound_result_date=bred + timedelta(days=35),
                ultrasound_done=True,
                pregnant=True,
                expected_kidding_date=born,
                outcome="CONFIRMED_PREGNANT",
            )
            db.add(breeding)
            await db.flush()
            litter = KiddingRecord(
                farm_id=farm_id, doe_id=doe.id, date=born, breeding_record_id=breeding.id
            )
            db.add(litter)
            await db.flush()
            for j in range(2):
                kid = Animal(
                    farm_id=farm_id,
                    tag_number=f"MORT-KID-{index}-{j}",
                    sex="F",
                    source="BORN",
                    current_bucket="FEMALE_KIDS",
                    date_of_birth=born,
                    dam_id=doe.id,
                    sire_id=buck.id,
                    birth_type="TWIN",
                    birth_weight=3.0,
                )
                db.add(kid)
                await db.flush()
                ids.append(kid.id)
                db.add(
                    KidEntry(
                        farm_id=farm_id,
                        kidding_record_id=litter.id,
                        tag=kid.tag_number,
                        sex="F",
                        birth_weight=3,
                        status="ALIVE",
                        animal_id=kid.id,
                    )
                )
                db.add_all(
                    [
                        BucketMove(
                            animal_id=kid.id,
                            from_bucket=None,
                            to_bucket="RECOVERY",
                            effective_date=born,
                            reason="Born",
                        ),
                        BucketMove(
                            animal_id=kid.id,
                            from_bucket="RECOVERY",
                            to_bucket="FEMALE_KIDS",
                            effective_date=born + timedelta(days=60),
                            reason="Weaned",
                        ),
                    ]
                )
        await db.commit()
    payload = {"new_status": status, "date": (born + timedelta(days=exit_day)).isoformat()}
    if status == "DEAD":
        # Delayed reporting must not move a death into the wrong age phase.
        payload.update(
            {
                "mortality_cause_code": "PNEUMONIA",
                "mortality_reported_at": (born + timedelta(days=150)).isoformat(),
            }
        )
    response = await client.post(f"/api/animals/{ids[0]}/status", headers=owner, json=payload)
    assert response.status_code == 200, response.text
    response = await client.get("/api/simulation/calibration", headers=owner)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["assumptions"]["mortality"]["kid_pre_weaning"] == pytest.approx(expected_rate)
    evidence = next(
        item for item in body["evidence"] if item["path"] == "mortality.kid_pre_weaning"
    )
    assert evidence["sample_size"] == denominator
    async with get_sessionmaker()() as db:
        entry = (
            await db.execute(select(KidEntry).where(KidEntry.animal_id == ids[0]))
        ).scalar_one()
        assert entry.status == "ALIVE", (
            "Operational weaning must not rewrite the live-birth outcome"
        )
