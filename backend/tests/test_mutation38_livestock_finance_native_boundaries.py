"""Native insurance and accounting boundaries preserve their declared contracts."""

from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import Farm, InsurancePolicy, InsurancePremium, Task
from app.services.finance import _require_linked_animal_active, create_insurance_policy
from app.utils import allocate_money, today

from .conftest import owner_with_farm
from .test_finance_extended import change_status, make_animal
from .test_finance_insurance import add_policy


def test_native_zero_share_count_reports_the_declared_value_error() -> None:
    try:
        allocate_money("1.00", 0)
    except ValueError as exc:
        assert str(exc) == "parts must be positive"
    except Exception as exc:
        pytest.fail(f"Invalid share count needs its domain ValueError: {exc!r}")
    else:
        pytest.fail("A zero-part money allocation was accepted")
    assert allocate_money("0.02", 4) == [
        Decimal("0.01"),
        Decimal("0.01"),
        Decimal("0.00"),
        Decimal("0.00"),
    ]


async def test_native_covered_animal_gate_accepts_active_and_rejects_real_sold_and_foreign_cover(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    other = await owner_with_farm(client, "foreign-native-insurance@example.test")
    active = await make_animal(client, owner, "NATIVE-ACTIVE-COVER")
    sold = await make_animal(client, owner, "NATIVE-SOLD-COVER")
    foreign = await make_animal(client, other, "NATIVE-FOREIGN-COVER")
    policies = []
    for headers, animal_id, number in [
        (owner, active["id"], "NATIVE-ACTIVE"),
        (owner, sold["id"], "NATIVE-SOLD"),
        (other, foreign["id"], "NATIVE-FOREIGN"),
    ]:
        created = await add_policy(client, headers, policy_number=number, animal_id=animal_id)
        assert created.status_code == 201, created.text
        policies.append(created.json()["id"])
    await change_status(client, owner, sold["id"], "SOLD", date=today().isoformat(), sale_price=0)
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, int(owner["X-Farm-Id"]))
        assert farm is not None
        active_policy = await db.get(InsurancePolicy, policies[0])
        sold_policy = await db.get(InsurancePolicy, policies[1])
        foreign_policy = await db.get(InsurancePolicy, policies[2])
        assert active_policy is not None and sold_policy is not None and foreign_policy is not None
        try:
            await _require_linked_animal_active(db, farm, active_policy, "renew this policy")
        except Exception as exc:
            pytest.fail(f"A genuine active covered animal is eligible: {exc!r}")
        for policy, expected in [
            (sold_policy, "has left the herd (sold)"),
            (foreign_policy, "covered animal is not on this farm"),
        ]:
            # Every policy and animal here is committed through its real public
            # writer with all FKs intact. This invokes the declared native guard
            # with a foreign policy, rather than inventing a broken linkage.
            try:
                await _require_linked_animal_active(db, farm, policy, "renew this policy")
            except ValueError as exc:
                assert expected in str(exc)
            except Exception as exc:
                pytest.fail(f"Ineligible native cover needs its domain ValueError: {exc!r}")
            else:
                pytest.fail(f"Ineligible covered animal was accepted: {policy.policy_number}")


@pytest.mark.parametrize("span,accepted", [(1830, True), (1831, False)])
async def test_native_registration_literal_five_year_backstop_before_any_register_effect(
    client: httpx.AsyncClient,
    span: int,
    accepted: bool,
) -> None:
    owner = await owner_with_farm(client)
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, int(owner["X-Farm-Id"]))
        assert farm is not None
        start = today(farm.timezone)
        try:
            try:
                policy = await create_insurance_policy(
                    db,
                    farm,
                    policy_number="NATIVE-FIVE-YEAR-BOUNDARY",
                    insurer="Native audited insurer",
                    sum_insured=Decimal("10000.00"),
                    premium=Decimal("100.00"),
                    start_date=start,
                    renewal_date=start + timedelta(days=span),
                    animal_id=None,
                    notes="Explicit native domain backstop",
                    created_by_id=farm.owner_id,
                )
            except ValueError as exc:
                assert not accepted, f"A literal1830-day coverage span is admitted: {exc}"
                assert "at most five years" in str(exc)
                assert not list((await db.execute(select(InsurancePolicy))).scalars())
                assert not list((await db.execute(select(InsurancePremium))).scalars())
                assert not list(
                    (await db.execute(select(Task).where(Task.category == "INSURANCE"))).scalars()
                )
            except Exception as exc:
                pytest.fail(f"Native registration requires a valid result/domain refusal: {exc!r}")
            else:
                assert accepted, (
                    "A single1831-day premium span must be refused before register effects"
                )
                await db.flush()
                assert policy.renewal_date == start + timedelta(days=1830)
                premiums = list((await db.execute(select(InsurancePremium))).scalars())
                assert len(premiums) == 1 and premiums[0].premium == Decimal("100.00")
                duties = list(
                    (await db.execute(select(Task).where(Task.category == "INSURANCE"))).scalars()
                )
                assert len(duties) == 1
                assert duties[0].due_date == policy.renewal_date - timedelta(days=30)
                assert duties[0].title_args["due_date"] == duties[0].due_date.isoformat()
        finally:
            await db.rollback()
    async with get_sessionmaker()() as db:
        assert not list((await db.execute(select(InsurancePolicy))).scalars())
        assert not list((await db.execute(select(InsurancePremium))).scalars())
