"""Scenario-gap audit: every small path the first lifecycle pass (and the
existing suite) left unproven at the API level.

Companion to ``test_e2e_lifecycle_audit.py`` (whose helpers are reused):
inbreeding fence, AI refusal, buck 1:20 capacity, mixed/partial litters,
gestation window, DIED-at-entry kids, sold-dam orphan weaning, cull ledger,
not-yet-due guard, wrong-bucket breeding, tag uniqueness across farms,
withdrawal cap, bulk bucket health events with cost split, next-due schedule
status, and auto tags for blank-tag kids.
"""

from datetime import date, timedelta

import httpx

from app.models import MAX_WITHDRAWAL_DAYS
from app.utils import today

from .conftest import OWNER_PW, create_farm, owner_with_farm, register
from .test_e2e_lifecycle_audit import (
    breed,
    change_status,
    confirm_pregnancy,
    find_task,
    get_animal,
    health_event,
    make_animal,
    record_kidding,
    set_weight,
)

PW = OWNER_PW


async def _kidding_of_two(
    client: httpx.AsyncClient, headers: dict, bred_days_ago: int
) -> tuple[dict, dict]:
    """Confirmed pregnancy delivered today-ish with one live kid; returns
    (kidding_response, breeding_detail)."""
    doe = await make_animal(client, headers, f"GF-F-{bred_days_ago}")
    buck = await make_animal(client, headers, f"GF-M-{bred_days_ago}", sex="M", weight_kg=32.0)
    br = await breed(
        client, headers, doe["id"], buck["id"], today() - timedelta(days=bred_days_ago)
    )
    detail = await confirm_pregnancy(client, headers, br["id"], kid_count=2)
    return br, detail


# ---------------------------------------------------------------------------
# Genetics fences (lineage only exists through real kiddings, so grow litters)
# ---------------------------------------------------------------------------


async def _grown_litter(
    client: httpx.AsyncClient,
    headers: dict,
    dam_tag: str,
    sire_id: int,
    kids: list[dict],
) -> list[dict]:
    """Kidd a litter 400 days ago, wean it, weight the kids: farm-born adults
    whose dam/sire lineage is real. Returns the grown kid animals."""
    dam = await make_animal(client, headers, dam_tag, dob_days=1200)
    br = await breed(client, headers, dam["id"], sire_id, today() - timedelta(days=550))
    detail = await confirm_pregnancy(client, headers, br["id"], kid_count=len(kids))
    delivery = await find_task(client, headers, category="BUCKET_MOVE", breeding_record_id=br["id"])
    assert (
        await client.post(f"/api/tasks/{delivery['id']}/complete", headers=headers)
    ).status_code == 200
    kidding_date = date.fromisoformat(detail["expected_kidding_date"])
    await record_kidding(client, headers, br["id"], kidding_date, kids)
    weaning = await find_task(client, headers, category="WEANING", animal_id=dam["id"])
    assert (
        await client.post(f"/api/tasks/{weaning['id']}/complete", headers=headers)
    ).status_code == 200
    animals = (await client.get("/api/animals", headers=headers)).json()
    grown = []
    for row in animals["animals"]:
        if row.get("dam_id") == dam["id"] and row["status"] == "ACTIVE":
            grown.append(row)
    for row in grown:
        await set_weight(client, headers, row["id"], 26.0 if row["sex"] == "F" else 30.0)
    return grown


async def test_inbreeding_fence_end_to_end(client: httpx.AsyncClient) -> None:
    """Sire×daughter and full-sibling services are refused; half-siblings
    (shared sire, different dams) remain permitted practice."""
    headers = await owner_with_farm(client)
    sire = await make_animal(client, headers, "IB-M-S", sex="M", weight_kg=32.0, dob_days=1200)

    litter_a = await _grown_litter(
        client,
        headers,
        "IB-F-A",
        sire["id"],
        [
            {"tag": "IB-K-A1", "sex": "F", "birth_weight": 2.4, "status": "ALIVE"},
            {"tag": "IB-K-A2", "sex": "M", "birth_weight": 2.6, "status": "ALIVE"},
        ],
    )
    litter_b = await _grown_litter(
        client,
        headers,
        "IB-F-B",
        sire["id"],
        [{"tag": "IB-K-B1", "sex": "M", "birth_weight": 2.6, "status": "ALIVE"}],
    )
    daughter = next(a for a in litter_a if a["sex"] == "F")
    full_brother = next(a for a in litter_a if a["sex"] == "M")
    half_brother = next(a for a in litter_b if a["sex"] == "M")
    # sons live in MALE_KIDS; promote the ones we need as sires
    for brother in (full_brother, half_brother):
        promoted = await client.post(
            f"/api/animals/{brother['id']}/move",
            json={"to_bucket": "BREEDING", "reason": "retained as sire"},
            headers=headers,
        )
        assert promoted.status_code == 200, promoted.text

    def _breed(doe_id: int, buck_id: int) -> httpx.Response:
        return client.post(
            "/api/breeding",
            json={"doe_id": doe_id, "buck_id": buck_id, "breeding_date": today().isoformat()},
            headers=headers,
        )

    sire_x_daughter = await _breed(daughter["id"], sire["id"])
    assert sire_x_daughter.status_code in {400, 409, 422}, sire_x_daughter.text
    assert "inbreeding" in sire_x_daughter.json()["detail"].lower()

    full_siblings = await _breed(daughter["id"], full_brother["id"])
    assert full_siblings.status_code in {400, 409, 422}, full_siblings.text
    assert "inbreeding" in full_siblings.json()["detail"].lower()

    half_siblings = await _breed(daughter["id"], half_brother["id"])
    assert half_siblings.status_code == 201, half_siblings.text


async def test_ai_methods_refused_for_goats(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_animal(client, headers, "AI-F-1")
    for method in ("AI", "AI_SEXED"):
        resp = await client.post(
            "/api/breeding",
            json={
                "doe_id": doe["id"],
                "semen_sire_name": "Imported semen",
                "method": method,
                "breeding_date": today().isoformat(),
            },
            headers=headers,
        )
        assert resp.status_code in {400, 409, 422}, (method, resp.text)


async def test_buck_covers_at_most_twenty_open_services(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    buck = await make_animal(client, headers, "CAP-M-1", sex="M", weight_kg=32.0)
    doe_ids = []
    for i in range(20):
        doe = await make_animal(client, headers, f"CAP-F-{i}")
        doe_ids.append(doe["id"])
    for doe_id in doe_ids:
        assert (await breed(client, headers, doe_id, buck["id"], today()))["id"]
    twenty_first = await make_animal(client, headers, "CAP-F-X")
    refused = await client.post(
        "/api/breeding",
        json={
            "doe_id": twenty_first["id"],
            "buck_id": buck["id"],
            "breeding_date": today().isoformat(),
        },
        headers=headers,
    )
    assert refused.status_code in {400, 409}, refused.text
    assert "20" in refused.json()["detail"] or "ratio" in refused.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Kidding micro-scenarios
# ---------------------------------------------------------------------------


async def test_mixed_litter_weaning_moves_survivor_and_dam(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_animal(client, headers, "MX-F-1")
    buck = await make_animal(client, headers, "MX-M-1", sex="M", weight_kg=32.0)
    br = await breed(client, headers, doe["id"], buck["id"], today() - timedelta(days=260))
    detail = await confirm_pregnancy(client, headers, br["id"], kid_count=2)
    delivery = await find_task(client, headers, category="BUCKET_MOVE", breeding_record_id=br["id"])
    assert (
        await client.post(f"/api/tasks/{delivery['id']}/complete", headers=headers)
    ).status_code == 200
    kidding = await record_kidding(
        client,
        headers,
        br["id"],
        date.fromisoformat(detail["expected_kidding_date"]),
        [
            {"tag": "MX-K-1", "sex": "F", "birth_weight": 2.4, "status": "ALIVE"},
            {"tag": "", "sex": "M", "birth_weight": None, "status": "STILLBORN"},
        ],
    )
    survivor_id = kidding["kids"][0]["animal_id"]
    assert kidding["kids"][1].get("animal_id") is None
    # One survivor → weaning duty (NOT the 14-day postpartum), and the
    # stillborn never becomes an animal.
    weaning = await find_task(client, headers, category="WEANING", animal_id=doe["id"])
    assert (
        await client.post(f"/api/tasks/{weaning['id']}/complete", headers=headers)
    ).status_code == 200
    assert (await get_animal(client, headers, survivor_id))["current_bucket"] == "FEMALE_KIDS"
    assert (await get_animal(client, headers, doe["id"]))["current_bucket"] == "RESTING"


async def test_partial_litter_one_death_weaning_still_completes(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_animal(client, headers, "PT-F-1")
    buck = await make_animal(client, headers, "PT-M-1", sex="M", weight_kg=32.0)
    br = await breed(client, headers, doe["id"], buck["id"], today() - timedelta(days=260))
    detail = await confirm_pregnancy(client, headers, br["id"], kid_count=2)
    delivery = await find_task(client, headers, category="BUCKET_MOVE", breeding_record_id=br["id"])
    await client.post(f"/api/tasks/{delivery['id']}/complete", headers=headers)
    kidding_date = date.fromisoformat(detail["expected_kidding_date"])
    kidding = await record_kidding(
        client,
        headers,
        br["id"],
        kidding_date,
        [
            {"tag": "PT-K-1", "sex": "F", "birth_weight": 2.4, "status": "ALIVE"},
            {"tag": "PT-K-2", "sex": "F", "birth_weight": 2.3, "status": "ALIVE"},
        ],
    )
    kid_ids = [k["animal_id"] for k in kidding["kids"]]
    died = await change_status(
        client,
        headers,
        kid_ids[0],
        "DEAD",
        mortality_cause="Weakness",
        mortality_reported_at=(kidding_date + timedelta(days=10)).isoformat(),
        date=(kidding_date + timedelta(days=10)).isoformat(),
    )
    assert died.status_code == 200, died.text
    # One survivor remains: weaning completes normally (no postpartum replan).
    weaning = await find_task(client, headers, category="WEANING", animal_id=doe["id"])
    assert (
        await client.post(f"/api/tasks/{weaning['id']}/complete", headers=headers)
    ).status_code == 200
    assert (await get_animal(client, headers, kid_ids[1]))["current_bucket"] == "FEMALE_KIDS"
    assert (await get_animal(client, headers, doe["id"]))["current_bucket"] == "RESTING"


async def test_kidding_gestation_window_enforced(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_animal(client, headers, "GW-F-1")
    buck = await make_animal(client, headers, "GW-M-1", sex="M", weight_kg=32.0)
    # bred 101 days ago: a delivery today-2 implies 99 days (below the 100-day
    # floor); today-1 lands exactly on the 100-day boundary.
    br = await breed(client, headers, doe["id"], buck["id"], today() - timedelta(days=101))
    await confirm_pregnancy(client, headers, br["id"], kid_count=1)
    too_early = await client.post(
        "/api/kidding",
        json={
            "breeding_record_id": br["id"],
            "date": (today() - timedelta(days=2)).isoformat(),
            "ease": "NORMAL",
            "notes": "",
            "kids": [{"tag": "GW-K-1", "sex": "F", "birth_weight": 2.5, "status": "ALIVE"}],
        },
        headers=headers,
    )
    assert too_early.status_code in {400, 409, 422}, too_early.text
    assert "gestation" in too_early.json()["detail"].lower()
    ok = await client.post(
        "/api/kidding",
        json={
            "breeding_record_id": br["id"],
            "date": (today() - timedelta(days=1)).isoformat(),
            "ease": "NORMAL",
            "notes": "",
            "kids": [{"tag": "GW-K-2", "sex": "F", "birth_weight": 2.5, "status": "ALIVE"}],
        },
        headers=headers,
    )
    assert ok.status_code == 201, ok.text


async def test_kid_died_at_entry_follows_postpartum_path(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_animal(client, headers, "DE-F-1")
    buck = await make_animal(client, headers, "DE-M-1", sex="M", weight_kg=32.0)
    br = await breed(client, headers, doe["id"], buck["id"], today() - timedelta(days=260))
    detail = await confirm_pregnancy(client, headers, br["id"], kid_count=1)
    delivery = await find_task(client, headers, category="BUCKET_MOVE", breeding_record_id=br["id"])
    await client.post(f"/api/tasks/{delivery['id']}/complete", headers=headers)
    kidding_date = date.fromisoformat(detail["expected_kidding_date"])
    kidding = await record_kidding(
        client,
        headers,
        br["id"],
        kidding_date,
        [
            {
                "tag": "DE-K-1",
                "sex": "M",
                "birth_weight": 2.2,
                "status": "DIED",
                "mortality_reported_at": kidding_date.isoformat(),
            }
        ],
    )
    assert kidding["kids"][0].get("animal_id") is not None
    # DIED-at-entry keeps a DEAD animal row for lineage — never in the herd.
    kid = await get_animal(client, headers, kidding["kids"][0]["animal_id"])
    assert kid["status"] == "DEAD"
    # no live kid → postpartum duty at kidding+14, weaning never created
    postpartum = await find_task(client, headers, category="BUCKET_MOVE", animal_id=doe["id"])
    assert date.fromisoformat(postpartum["due_date"]) == kidding_date + timedelta(days=14)
    assert (
        await client.post(f"/api/tasks/{postpartum['id']}/complete", headers=headers)
    ).status_code == 200
    assert (await get_animal(client, headers, doe["id"]))["current_bucket"] == "RESTING"


async def test_blank_tag_live_kid_gets_auto_tag(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_animal(client, headers, "AT-F-1")
    buck = await make_animal(client, headers, "AT-M-1", sex="M", weight_kg=32.0)
    br = await breed(client, headers, doe["id"], buck["id"], today() - timedelta(days=260))
    detail = await confirm_pregnancy(client, headers, br["id"], kid_count=1)
    delivery = await find_task(client, headers, category="BUCKET_MOVE", breeding_record_id=br["id"])
    await client.post(f"/api/tasks/{delivery['id']}/complete", headers=headers)
    kidding = await record_kidding(
        client,
        headers,
        br["id"],
        date.fromisoformat(detail["expected_kidding_date"]),
        [{"tag": "", "sex": "M", "birth_weight": 2.6, "status": "ALIVE"}],
    )
    kid_id = kidding["kids"][0]["animal_id"]
    kid = await get_animal(client, headers, kid_id)
    assert kid["tag_number"] and kid["tag_number"].strip()


async def test_doe_sold_from_recovery_early_weans_kid(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_animal(client, headers, "SO-F-1")
    buck = await make_animal(client, headers, "SO-M-1", sex="M", weight_kg=32.0)
    br = await breed(client, headers, doe["id"], buck["id"], today() - timedelta(days=260))
    detail = await confirm_pregnancy(client, headers, br["id"], kid_count=1)
    delivery = await find_task(client, headers, category="BUCKET_MOVE", breeding_record_id=br["id"])
    await client.post(f"/api/tasks/{delivery['id']}/complete", headers=headers)
    kidding = await record_kidding(
        client,
        headers,
        br["id"],
        date.fromisoformat(detail["expected_kidding_date"]),
        [{"tag": "SO-K-1", "sex": "F", "birth_weight": 2.5, "status": "ALIVE"}],
    )
    kid_id = kidding["kids"][0]["animal_id"]
    sold = await change_status(
        client, headers, doe["id"], "SOLD", sale_price=5500, buyer_name="Buyer"
    )
    assert sold.status_code == 200, sold.text
    kid = await get_animal(client, headers, kid_id)
    assert kid["current_bucket"] == "FEMALE_KIDS", kid["current_bucket"]


# ---------------------------------------------------------------------------
# Task timing and assignment micro-guards
# ---------------------------------------------------------------------------


async def test_auto_duty_cannot_complete_before_due(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_animal(client, headers, "ND-F-1")
    buck = await make_animal(client, headers, "ND-M-1", sex="M", weight_kg=32.0)
    # bred 40 days ago: the scan (breeding+32) is recordable in the past, but
    # the generated pre-kidding move is due EKD-15, far in the future.
    br = await breed(client, headers, doe["id"], buck["id"], today() - timedelta(days=40))
    await confirm_pregnancy(client, headers, br["id"], kid_count=1)
    delivery = await find_task(client, headers, category="BUCKET_MOVE", breeding_record_id=br["id"])
    resp = await client.post(f"/api/tasks/{delivery['id']}/complete", headers=headers)
    assert resp.status_code == 409, resp.text
    assert "not due" in resp.json()["detail"].lower()


async def test_breeding_refused_while_doe_in_delivery_bucket(client: httpx.AsyncClient) -> None:
    from .test_e2e_lifecycle_audit import pregnant_doe_in

    headers = await owner_with_farm(client)
    doe, _br = await pregnant_doe_in(client, headers, "PREGNANCY_LATE", bred_days_ago=120)
    moved = await client.post(
        f"/api/animals/{doe['id']}/move",
        json={"to_bucket": "DELIVERY", "reason": "pre-kidding"},
        headers=headers,
    )
    assert moved.status_code == 200, moved.text
    buck2 = await make_animal(client, headers, "WB-M-2", sex="M", weight_kg=32.0)
    refused = await client.post(
        "/api/breeding",
        json={"doe_id": doe["id"], "buck_id": buck2["id"], "breeding_date": today().isoformat()},
        headers=headers,
    )
    assert refused.status_code in {400, 409, 422}, refused.text


# ---------------------------------------------------------------------------
# Identity, health and money micro-scenarios
# ---------------------------------------------------------------------------


async def test_duplicate_tag_within_farm_refused_but_other_farm_ok(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "DUP-1")
    dup = await client.post(
        "/api/animals",
        json={
            "tag_number": "DUP-1",
            "sex": "F",
            "source": "PURCHASED",
            "current_bucket": "FOUNDATION",
            "historical_import_reason": "dup",
        },
        headers=headers,
    )
    assert dup.status_code in {400, 409, 422}, dup.text
    # A different farm may reuse the tag: tags are unique per farm only.
    other_owner = await register(client, "other@farm.in", PW)
    other = await create_farm(client, other_owner, "Beta Farm")
    same_tag = await client.post(
        "/api/animals",
        json={
            "tag_number": "DUP-1",
            "sex": "F",
            "source": "PURCHASED",
            "current_bucket": "FOUNDATION",
            "historical_import_reason": "other farm",
        },
        headers=other,
    )
    assert same_tag.status_code == 201, same_tag.text


async def test_withdrawal_longer_than_two_years_refused(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_animal(client, headers, "WD2-F-1")
    resp = await health_event(
        client,
        headers,
        scope="animal",
        animal_id=doe["id"],
        type="TREATMENT",
        product_name="X",
        date=today().isoformat(),
        withdrawal_until=(today() + timedelta(days=MAX_WITHDRAWAL_DAYS + 1)).isoformat(),
    )
    assert resp.status_code == 422, resp.text


async def test_bucket_scope_health_event_splits_cost_per_animal(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    for i in range(3):
        await make_animal(client, headers, f"BLK-F-{i}", bucket="FOUNDATION")
    preview = await client.post(
        "/api/health/events/preview",
        json={"scope": "bucket", "bucket": "FOUNDATION"},
        headers=headers,
    )
    assert preview.status_code == 200, preview.text
    targets = preview.json()["target_animal_ids"]
    assert len(targets) == 3
    created = await health_event(
        client,
        headers,
        scope="bucket",
        bucket="FOUNDATION",
        type="VACCINE",
        product_name="PPR",
        cost=300.0,
        expected_animal_ids=targets,
    )
    assert created.status_code == 201, created.text
    events = created.json()
    assert len(events) == 3
    assert all(abs(float(e["cost"]) - 100.0) < 0.01 for e in events)
    finance = (await client.get("/api/finance", headers=headers)).json()
    rows = finance["transactions"] if isinstance(finance, dict) else finance
    medicine = [r for r in rows if r["category"] == "MEDICINE"]
    assert len(medicine) == 1
    assert abs(float(medicine[0]["amount"]) - 300.0) < 0.01


async def test_next_due_date_marks_schedule_upcoming_then_overdue(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    doe = await make_animal(client, headers, "ND2-F-1")
    recorded = await health_event(
        client,
        headers,
        scope="animal",
        animal_id=doe["id"],
        type="DEWORMING",
        product_name="Albendazole",
        date=(today() - timedelta(days=40)).isoformat(),
        next_due_date=(today() + timedelta(days=5)).isoformat(),
        schedule_template_name="Deworming",
        next_due_authority="Farm veterinarian record",
    )
    assert recorded.status_code == 201, recorded.text
    schedule = (await client.get(f"/api/health/schedule/{doe['id']}", headers=headers)).json()
    row = next(r for r in schedule["rows"] if r["template_name"] == "Deworming")
    assert row["status"] in {"UPCOMING", "DONE"}, row
    # A past next-due flips the row to OVERDUE.
    overdue_doe = await make_animal(client, headers, "ND3-F-1")
    await health_event(
        client,
        headers,
        scope="animal",
        animal_id=overdue_doe["id"],
        type="DEWORMING",
        product_name="Albendazole",
        date=(today() - timedelta(days=100)).isoformat(),
        next_due_date=(today() - timedelta(days=5)).isoformat(),
        schedule_template_name="Deworming",
        next_due_authority="Farm veterinarian record",
    )
    schedule2 = (
        await client.get(f"/api/health/schedule/{overdue_doe['id']}", headers=headers)
    ).json()
    row2 = next(r for r in schedule2["rows"] if r["template_name"] == "Deworming")
    assert row2["status"] == "OVERDUE", row2


async def test_cull_with_price_books_income_row(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    doe = await make_animal(client, headers, "CU-F-1", bucket="FEMALE_KIDS")
    culled = await change_status(
        client, headers, doe["id"], "CULLED", sale_price=1500.0, buyer_name="Meat trader"
    )
    assert culled.status_code == 200, culled.text
    assert (await get_animal(client, headers, doe["id"]))["status"] == "CULLED"
    finance = (await client.get("/api/finance", headers=headers)).json()
    rows = finance["transactions"] if isinstance(finance, dict) else finance
    income = [r for r in rows if r["category"] == "ANIMAL_SALE"]
    assert len(income) == 1
    assert float(income[0]["amount"]) == 1500.0
