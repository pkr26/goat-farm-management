"""Independent ledger, chronology, exit-weight and real lock regressions."""

import asyncio
import hashlib
import json
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import event, select, text

from app.db import get_engine, get_sessionmaker
from app.models import Animal, Farm, InsurancePolicy, Transaction, WeightRecord
from app.services.finance import lapse_policies_for_animal, renew_insurance_policy
from app.utils import today, utcnow

from .conftest import owner_with_farm
from .test_finance_extended import make_animal
from .test_finance_insurance import add_policy


async def test_stored_run_returns_actual_snapshot_revision_and_fingerprint(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    defaults = await client.get("/api/simulation/defaults", headers=owner)
    document = defaults.json()
    document["meta"]["horizon_months"] = 12
    created = await client.post(
        "/api/simulation/scenarios",
        headers=owner,
        json={"name": "Executed evidence", "assumptions": document},
    )
    assert created.status_code == 201, created.text
    cached = created.json()
    # Another operator changes the stored basis after this cached row was read.
    document["herd"]["does"] = 73
    changed = await client.patch(
        f"/api/simulation/scenarios/{cached['id']}",
        headers=owner,
        json={"assumptions": document, "expected_revision": cached["revision"]},
    )
    assert changed.status_code == 200, changed.text
    response = await client.post(f"/api/simulation/scenarios/{cached['id']}/run", headers=owner)
    assert response.status_code == 200, response.text
    result = response.json()
    actual = result["executed_assumptions"]
    assert actual["herd"]["does"] == 73
    assert result["executed_scenario_revision"] == changed.json()["revision"]
    assert result["executed_scenario_revision"] != cached["revision"]
    canonical = json.dumps(actual, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert result["assumptions_fingerprint"] == hashlib.sha256(canonical).hexdigest()


async def test_owner_uses_active_ledger_and_chronological_endpoints(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    growing = await make_animal(client, owner, tag="CHRONO-GROW")
    shrinking = await make_animal(client, owner, tag="CHRONO-LOSS")
    same_day = await make_animal(client, owner, tag="CHRONO-ZERO")
    async with get_sessionmaker()() as db:
        for animal, observations in (
            (growing, [(-10, 20), (-5, 40), (0, 25)]),
            (shrinking, [(-10, 30), (0, 28)]),
            (same_day, [(0, 10), (0, 100)]),
        ):
            for delta, weight in observations:
                db.add(
                    WeightRecord(
                        farm_id=farm_id,
                        animal_id=animal["id"],
                        date=today() + timedelta(days=delta),
                        weight_kg=weight,
                    )
                )
        for kind, amount, voided in (
            ("EXPENSE", "300.00", False),
            ("EXPENSE", "900.00", True),
            ("INCOME", "500.00", False),
            ("INCOME", "700.00", True),
        ):
            db.add(
                Transaction(
                    farm_id=farm_id,
                    date=today(),
                    type=kind,
                    category="FEED",
                    amount=Decimal(amount),
                    voided_at=utcnow() if voided else None,
                    void_reason="Corrected record" if voided else None,
                )
            )
        await db.commit()
    overview = await client.get("/api/owner/overview", headers=owner)
    assert overview.status_code == 200, overview.text
    row = overview.json()["farms"][0]
    assert Decimal(row["month_income"]) == Decimal("500.00")
    assert Decimal(row["month_expense"]) == Decimal("300.00")
    assert Decimal(row["month_net"]) == Decimal("200.00")
    response = await client.get("/api/owner/benchmarks", headers=owner)
    assert response.status_code == 200, response.text
    row = response.json()["farms"][0]
    # Endpoints +5 and -2 over ten days; the middle spike/same-day pair
    # contribute neither fake growth nor a zero-day divisor.
    assert row["avg_daily_gain_kg"] == pytest.approx((0.5 - 0.2) / 2)
    assert row["feed_cost_per_kg_gain"] == pytest.approx(300 / (5 - 2))


async def test_sale_calibration_prefers_exit_weight_and_excludes_old_measurements(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        sale_day = today(farm.timezone)
        for index in range(7):
            animal = Animal(
                farm_id=farm_id,
                tag_number=f"EXIT-KG-{index}",
                sex="M",
                source="PURCHASED",
                current_bucket="BREEDING",
                status="SOLD",
                date_of_birth=sale_day - timedelta(days=400),
                purchase_date=sale_day - timedelta(days=300),
                status_date=sale_day,
                sale_price=Decimal("8000"),
                sale_weight_kg=40 if index < 5 else None,
            )
            db.add(animal)
            await db.flush()
            db.add(
                WeightRecord(
                    farm_id=farm_id,
                    animal_id=animal.id,
                    weight_kg=20,
                    date=sale_day - timedelta(days=10 if index == 5 else 60),
                )
            )
            # A post-exit weighing must never leak back into sale denominators.
            db.add(
                WeightRecord(
                    farm_id=farm_id,
                    animal_id=animal.id,
                    weight_kg=80,
                    date=sale_day + timedelta(days=1),
                )
            )
        await db.commit()
    response = await client.get("/api/simulation/calibration", headers=owner)
    assert response.status_code == 200, response.text
    data = response.json()
    evidence = {item["path"]: item for item in data["evidence"]}
    assert evidence["sales.meat_price_per_kg"]["sample_size"] == 6
    # Five actual 200/kg and one disclosed recent estimate 400/kg: median200.
    expected = 200.0
    from app.services.simulation_calibration import _is_bakrid_month

    if _is_bakrid_month(today()):
        expected /= 1 + data["assumptions"]["sales"]["festival_price_uplift"]
    assert data["assumptions"]["sales"]["meat_price_per_kg"] == pytest.approx(expected)
    assert any(
        "1 sale-price observations used an estimated exit weight" in w for w in data["warnings"]
    )
    assert any("1 sale-price observations were excluded" in w for w in data["warnings"])


@pytest.mark.parametrize("exit_first", [True, False])
async def test_renewal_and_exit_are_serial_with_fresh_animal_status(
    client: httpx.AsyncClient,
    exit_first: bool,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    animal_data = await make_animal(client, owner, tag="LOCK-RENEW-EXIT")
    created = await add_policy(client, owner, animal_id=animal_data["id"])
    assert created.status_code == 201, created.text
    policy_id = created.json()["id"]
    first_locked = asyncio.Event()
    waiting_lock = asyncio.Event()
    release = asyncio.Event()
    loop = asyncio.get_running_loop()

    def observe(
        _conn: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if "FROM animals" in statement and "FOR UPDATE" in statement and first_locked.is_set():
            loop.call_soon_threadsafe(waiting_lock.set)

    async def exit_animal(first: bool) -> None:
        async with get_sessionmaker()() as db:
            await db.execute(text("SET LOCAL lock_timeout = '3s'"))
            farm = await db.get(Farm, farm_id)
            animal = (
                await db.execute(
                    select(Animal).where(Animal.id == animal_data["id"]).with_for_update()
                )
            ).scalar_one()
            assert farm is not None
            if first:
                first_locked.set()
                await release.wait()
            animal.status = "CULLED"
            animal.status_date = today(farm.timezone)
            await lapse_policies_for_animal(db, farm, animal)
            await db.commit()

    async def renew(first: bool) -> None:
        async with get_sessionmaker()() as db:
            await db.execute(text("SET LOCAL lock_timeout = '3s'"))
            farm = await db.get(Farm, farm_id)
            # Deliberately populate the identity map BEFORE waiting on exit.
            stale_animal = await db.get(Animal, animal_data["id"])
            policy = await db.get(InsurancePolicy, policy_id)
            assert farm is not None and policy is not None and stale_animal is not None
            if exit_first:
                assert stale_animal.status == "ACTIVE"
                with pytest.raises(ValueError, match="left the herd"):
                    await renew_insurance_policy(
                        db,
                        farm,
                        policy,
                        renewal_date=policy.renewal_date + timedelta(days=365),
                        premium=Decimal("500"),
                    )
                await db.rollback()
            else:
                await renew_insurance_policy(
                    db,
                    farm,
                    policy,
                    renewal_date=policy.renewal_date + timedelta(days=365),
                    premium=Decimal("500"),
                )
                if first:
                    first_locked.set()
                    await release.wait()
                await db.commit()

    first = asyncio.create_task(exit_animal(True) if exit_first else renew(True))
    await asyncio.wait_for(first_locked.wait(), 5)
    engine = get_engine().sync_engine
    event.listen(engine, "before_cursor_execute", observe)
    second = asyncio.create_task(renew(False) if exit_first else exit_animal(False))
    try:
        await asyncio.wait_for(waiting_lock.wait(), 5)
        release.set()
        await asyncio.wait_for(asyncio.gather(first, second), 5)
    finally:
        release.set()
        event.remove(engine, "before_cursor_execute", observe)
        await asyncio.gather(first, second, return_exceptions=True)
    async with get_sessionmaker()() as db:
        animal = await db.get(Animal, animal_data["id"])
        policy = await db.get(InsurancePolicy, policy_id)
        assert animal is not None and animal.status == "CULLED"
        assert policy is not None and policy.status == "lapsed"
        assert policy.premium == Decimal("450.00" if exit_first else "500.00")
