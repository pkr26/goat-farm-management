"""Health gap tests (2026-09-23 verification plan, category 5).

The e2e audit already pins "withdrawal blocks sale until expired" and the
correction flow's inclusive last-day. These pin the exact day-29/day-31
relative boundaries of a 30-day meat withdrawal on a direct sale, the
overlapping-treatment rule (the latest expiry governs — the SQL takes
max(withdrawal_until) still covering the sale date), and record a plan-vs-
app discrepancy as an executable fact: FAMACHA scoring does NOT exist as a
husbandry duty — the monthly cadence round is weighing. If FAMACHA is ever
added, this test flips and forces a conscious decision.
"""

from datetime import date, timedelta

import httpx

from app.models.enums import TaskCategory
from app.utils import today

from .conftest import owner_with_farm
from .test_e2e_lifecycle_audit import change_status, health_event, make_animal


def iso(d: date) -> str:
    return d.isoformat()


async def _treat(
    client: httpx.AsyncClient,
    owner: dict,
    animal_id: int,
    on: date,
    withdrawal_until: date,
) -> httpx.Response:
    return await health_event(
        client,
        owner,
        scope="animal",
        animal_id=animal_id,
        type="TREATMENT",
        product_name="Enrofloxacin 10%",
        date=iso(on),
        withdrawal_until=iso(withdrawal_until),
    )


async def test_withdrawal_day29_blocked_day31_allowed(client: httpx.AsyncClient) -> None:
    """A 30-day meat withdrawal: on the 30th day (withdrawal_until == sale
    date) the sale is still blocked; on day 32 (withdrawal_until < sale
    date) it goes through."""
    owner = await owner_with_farm(client, email="wd@farm.in")

    # Doe A: treated 30 days ago with a 30-day withdrawal → today IS the
    # last withdrawal day (30 days of the window elapsed).
    boundary = await make_animal(client, owner, "WD-A", sex="M", bucket="MALE_KIDS", weight_kg=28.0)
    resp = await _treat(client, owner, boundary["id"], today() - timedelta(days=30), today())
    assert resp.status_code == 201, resp.text
    blocked = await change_status(
        client, owner, boundary["id"], "SOLD", sale_price=4000.0, buyer_name="butcher"
    )
    assert blocked.status_code == 409, blocked.text
    assert "withdrawal" in blocked.json()["detail"].lower()
    assert today().isoformat() in blocked.json()["detail"], (
        f"denial should name the expiry: {blocked.json()['detail']}"
    )

    # Doe B: treated 31 days ago with a 30-day withdrawal → the window
    # closed yesterday; the sale must go through.
    clear = await make_animal(client, owner, "WD-B", sex="M", bucket="MALE_KIDS", weight_kg=28.0)
    resp = await _treat(
        client,
        owner,
        clear["id"],
        today() - timedelta(days=31),
        today() - timedelta(days=1),
    )
    assert resp.status_code == 201, resp.text
    sold = await change_status(
        client, owner, clear["id"], "SOLD", sale_price=4000.0, buyer_name="butcher"
    )
    assert sold.status_code == 200, sold.text


async def test_overlapping_treatments_block_until_the_latest_expiry(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="wd2@farm.in")
    animal = await make_animal(client, owner, "WD2", sex="M", bucket="MALE_KIDS", weight_kg=28.0)

    # Two treatments: the first expires sooner, the second later.
    sooner = today() + timedelta(days=3)
    later = today() + timedelta(days=17)
    first = await _treat(client, owner, animal["id"], today() - timedelta(days=5), sooner)
    second = await _treat(client, owner, animal["id"], today() - timedelta(days=2), later)
    assert first.status_code == 201 and second.status_code == 201, (first.text, second.text)

    blocked = await change_status(
        client, owner, animal["id"], "SOLD", sale_price=4000.0, buyer_name="butcher"
    )
    assert blocked.status_code == 409, blocked.text
    # The denial must name the LATER expiry — max(withdrawal_until) governs.
    assert later.isoformat() in blocked.json()["detail"], (
        f"overlapping withdrawal should defer to {later}: {blocked.json()['detail']}"
    )


async def test_famacha_monthly_round_materializes_and_weighing_survives(
    client: httpx.AsyncClient,
) -> None:
    """FIXED (2026-09-23): the verification found FAMACHA existed only as a
    screening label — the monthly anemia-scoring round the husbandry plan
    expects was missing. It is now a cadence interval round: a dedicated
    FAMACHA category, generated (never manually creatable), assigned to the
    VET preset role, materializing alongside the monthly weighing round."""
    categories = {c.value for c in TaskCategory}
    assert "FAMACHA" in categories, "the FAMACHA task category disappeared"
    assert "WEIGHING" in categories, "the monthly weighing round disappeared"

    from sqlalchemy import select

    from app.db import get_sessionmaker
    from app.models import Farm, Role
    from app.services.cadence import ensure_cadence_tasks

    owner = await owner_with_farm(client, email="famacha@farm.in")
    await make_animal(client, owner, "FAM-1")
    async with get_sessionmaker()() as db:
        farm = (
            await db.execute(select(Farm).where(Farm.id == int(owner["X-Farm-Id"])))
        ).scalar_one()
        await ensure_cadence_tasks(db, farm)
        await db.commit()

    duties = await client.get("/api/tasks", headers=owner)
    rows = duties.json()["today"] + duties.json()["upcoming"] + duties.json()["overdue"]
    titles = [t["title"] for t in rows]
    assert any("weighing" in title.lower() for title in titles), (
        f"monthly weighing round missing from cadence output: {titles[:6]}"
    )
    famacha = [t for t in rows if t.get("category") == "FAMACHA"]
    assert famacha, f"FAMACHA round missing from cadence output: {titles[:6]}"
    assert "conjunctiva" in famacha[0]["title"].lower()

    # The round lands on the farm's seeded VET role.
    async with get_sessionmaker()() as db:
        vet_role = (
            await db.execute(
                select(Role).where(
                    Role.farm_id == int(owner["X-Farm-Id"]),
                    Role.code == "VET",
                )
            )
        ).scalar_one()
    assert famacha[0]["assigned_role_id"] == vet_role.id, (
        f"FAMACHA round assigned to role {famacha[0]['assigned_role_id']}, expected VET"
    )

    # FAMACHA stays workflow-owned: the manual-duty endpoint rejects it.
    manual = await client.post(
        "/api/tasks",
        json={"title": "fake famacha", "category": "FAMACHA", "due_date": rows[0]["due_date"]},
        headers=owner,
    )
    assert manual.status_code == 422, (
        f"manual FAMACHA duty accepted: {manual.status_code} {manual.text[:120]}"
    )
