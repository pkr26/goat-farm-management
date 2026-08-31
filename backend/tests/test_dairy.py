"""Buffalo-dairy (Murrah) farm type: creation, seeding, operations, milk and
the dairy simulation preset.

The ten lifecycle bucket codes are shared with goats; a dairy farm must see
dairy definitions, recipes, vaccines and biology everywhere the species
diverges.
"""

from datetime import date, timedelta

import pytest

from .conftest import create_farm, owner_with_farm, register

MILKING_BUFFALO = {
    "tag_number": "BUF-001",
    "sex": "F",
    "breed": "Murrah",
    "date_of_birth": "2021-01-15",  # ~5.5 years old
    "source": "PURCHASED",
    "purchase_date": "2025-09-01",
    "purchase_price": 110000,
    "current_bucket": "BREEDING",
    "historical_import_reason": "Foundation herd import",
    "weight_kg": 520,
}
# Weight records must predate a backdated service for the eligibility gate.
WEIGHT_DATE = "2025-09-02"


async def _dairy_owner(client, email="dairy@farm.in"):
    headers = await register(client, email)
    return await create_farm(client, headers, "Navipet Dairy", farm_type="BUFFALO_DAIRY")


async def _create_animal(client, headers, payload_overrides=None):
    """Create a dairy animal with a backdated entry-weight record.

    The entry weight rides on the create payload (the historical-import gate
    reads it); a dated weight record anchored to WEIGHT_DATE keeps the
    breeding eligibility's as-of weight query correct for backdated services.
    """
    payload = {**MILKING_BUFFALO, **(payload_overrides or {})}
    weight = payload.pop("weight_kg", None)
    payload["weight_kg"] = weight
    payload["weight_date"] = WEIGHT_DATE
    resp = await client.post("/api/animals", json=payload, headers=headers)
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


# --- farm type -----------------------------------------------------------------


async def test_create_and_list_dairy_farm(client):
    headers = await _dairy_owner(client)
    resp = await client.get("/api/auth/farms", headers={"Authorization": headers["Authorization"]})
    assert resp.status_code == 200
    farms = resp.json()
    dairy = [f for f in farms if f["farm_type"] == "BUFFALO_DAIRY"]
    assert dairy and dairy[0]["name"] == "Navipet Dairy"


async def test_default_farm_type_is_goat(client):
    headers = await owner_with_farm(client, email="goat-default@farm.in")
    resp = await client.get("/api/auth/farms", headers=headers)
    assert resp.status_code == 200
    assert all(f["farm_type"] == "GOAT" for f in resp.json())


async def test_rejects_unknown_farm_type(client):
    headers = await register(client, "bad-type@farm.in")
    resp = await client.post(
        "/api/auth/farms", json={"name": "X", "farm_type": "HORSE"}, headers=headers
    )
    assert resp.status_code == 422


# --- species-scoped reference data ---------------------------------------------


async def test_dairy_bucket_board_is_dairy(client):
    headers = await _dairy_owner(client)
    resp = await client.get("/api/buckets", headers=headers)
    assert resp.status_code == 200
    board = resp.json()
    assert len(board) == 10
    by_code = {row["bucket"]: row for row in board}
    assert "Milking" in by_code["BREEDING"]["name"]
    assert "Building B" in by_code["DELIVERY"]["name"]
    # Dairy per-head feed rates are an order of magnitude above goat rates.
    assert by_code["BREEDING"]["daily_kg_per_head"] > 15.0
    assert by_code["FEMALE_KIDS"]["name"].startswith("Heifer Calves")


async def test_goat_bucket_board_unchanged(client):
    headers = await owner_with_farm(client, email="goat-buckets@farm.in")
    resp = await client.get("/api/buckets", headers=headers)
    board = {row["bucket"]: row for row in resp.json()}
    assert board["BREEDING"]["name"] == "Breeding Bucket"
    assert board["BREEDING"]["daily_kg_per_head"] == 1.2


async def test_dairy_recipes_and_vaccines_scoped(client):
    headers = await _dairy_owner(client)
    recipes = (await client.get("/api/feeding/recipes", headers=headers)).json()
    codes = {r["code"] for r in recipes["recipes"]}
    assert "D_LACTATION_HIGH" in codes
    assert "FATTENING_50_50" not in codes
    templates = (await client.get("/api/health/schedule-templates", headers=headers)).json()
    names = {t["name"] for t in templates["templates"]}
    assert "Lumpy Skin Disease (LSD)" in names
    assert "Goat Pox" not in names
    # Goat farm still sees its own list.
    goat_headers = await owner_with_farm(client, email="goat-templates@farm.in")
    goat_templates = (
        await client.get("/api/health/schedule-templates", headers=goat_headers)
    ).json()
    goat_names = {t["name"] for t in goat_templates["templates"]}
    assert "Goat Pox" in goat_names
    assert "Lumpy Skin Disease (LSD)" not in goat_names


async def test_dairy_feed_inventory_ingredients(client):
    headers = await _dairy_owner(client)
    inv = (await client.get("/api/feeding/inventory", headers=headers)).json()
    ingredients = {i["ingredient"] for i in inv}
    assert "Paddy straw" in ingredients
    assert "Cottonseed cake" in ingredients


async def test_dairy_breed_default_is_murrah(client):
    headers = await _dairy_owner(client)
    resp = await client.post(
        "/api/animals",
        json={k: v for k, v in MILKING_BUFFALO.items() if k != "weight_kg"}
        | {"breed": "", "weight_kg": 520, "weight_date": WEIGHT_DATE},
        headers=headers,
    )
    assert resp.status_code in (200, 201), resp.text
    body = resp.json()
    animal = body if "tag_number" in body else body.get("animal", body)
    assert animal["breed"] == "Murrah"


# --- species biology: AI breeding, 310-day gestation, calving ------------------


async def _breed_and_confirm(client, headers, doe_id, *, method="AI", buck_id=None):
    """Create an AI service ~320 days ago, confirm pregnant, calve on the
    expected date (breeding + 310 days, already in the past)."""
    breeding_date = date.today() - timedelta(days=320)
    payload = {
        "doe_id": doe_id,
        "breeding_date": breeding_date.isoformat(),
        "method": method,
    }
    if buck_id is not None:
        payload["buck_id"] = buck_id
    if method != "NATURAL":
        payload["semen_sire_name"] = "Karanvir 999"
    resp = await client.post("/api/breeding", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    record = resp.json()
    # PD is planned ~60 days after service for buffalo.
    assert date.fromisoformat(record["ultrasound_date"]) == breeding_date + timedelta(days=60)
    assert record["method"] == method
    if method != "NATURAL":
        assert record["buck_id"] is None
        assert record["semen_sire_name"] == "Karanvir 999"
    confirm = await client.post(
        f"/api/breeding/{record['id']}/ultrasound",
        json={"pregnant": True, "date": (breeding_date + timedelta(days=65)).isoformat()},
        headers=headers,
    )
    assert confirm.status_code == 200, confirm.text
    confirmed = confirm.json()
    expected_due = breeding_date + timedelta(days=310)
    assert date.fromisoformat(confirmed["expected_kidding_date"]) == expected_due
    assert record["buck_id"] is None
    return confirmed


async def test_ai_breeding_and_buffalo_gestation(client):
    headers = await _dairy_owner(client)
    animal = await _create_animal(client, headers)
    confirmed = await _breed_and_confirm(client, headers, animal["id"])
    # Calve on the expected date (310-day gestation, 10 days in the past).
    resp = await client.post(
        "/api/kidding",
        json={
            "breeding_record_id": confirmed["id"],
            "date": confirmed["expected_kidding_date"],
            "ease": "NORMAL",
            "kids": [
                {"sex": "F", "status": "ALIVE", "birth_weight": 34.0},
            ],
        },
        headers=headers,
    )
    assert resp.status_code in (200, 201), resp.text
    kid = resp.json()["kids"][0]
    # Dairy calves are placed straight into the calf shed, not with the dam.
    calf = (await client.get(f"/api/animals/{kid['animal_id']}", headers=headers)).json()
    animal_body = calf.get("animal", calf)
    assert animal_body["current_bucket"] == "FEMALE_KIDS"


async def test_natural_service_still_requires_buck(client):
    headers = await _dairy_owner(client)
    animal = await _create_animal(client, headers)
    resp = await client.post(
        "/api/breeding",
        json={
            "doe_id": animal["id"],
            "breeding_date": date.today().isoformat(),
            "method": "NATURAL",
        },
        headers=headers,
    )
    assert resp.status_code == 422


async def test_buffalo_breeding_gates(client):
    """A 23-month 300 kg heifer fails the 340 kg floor; 400 kg passes."""
    headers = await _dairy_owner(client)
    young = await _create_animal(
        client,
        headers,
        {
            "tag_number": "BUF-HEIFER",
            "date_of_birth": (date.today() - timedelta(days=23 * 30)).isoformat(),
            "current_bucket": "FOUNDATION",
            "weight_kg": 300,
        },
    )
    doe = await _create_animal(
        client,
        headers,
        {
            "tag_number": "BUF-ADULT",
            "date_of_birth": "2021-01-15",
            "current_bucket": "BREEDING",
            "weight_kg": 520,
        },
    )
    candidates = (await client.get("/api/breeding/candidates?kind=doe", headers=headers)).json()
    tags = {c["tag_number"] for c in candidates["candidates"]}
    assert doe["tag_number"] in tags
    assert young["tag_number"] not in tags


# --- milk records ---------------------------------------------------------------


async def test_milk_record_upsert_and_summary(client):
    headers = await _dairy_owner(client)
    animal = await _create_animal(client, headers)
    today = date.today().isoformat()
    for litres in (8.5, 9.0):
        resp = await client.post(
            "/api/milk/new",
            json={
                "animal_id": animal["id"],
                "date": today,
                "shift": "MORNING",
                "litres": litres,
                "fat_pct": 6.9,
            },
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
    listing = (await client.get("/api/milk?date_from=" + today, headers=headers)).json()
    assert listing["total"] == 1  # upsert replaced, not duplicated
    assert listing["total_litres"] == 9.0
    summary = (await client.get("/api/milk/summary?days=7", headers=headers)).json()
    assert summary["total_litres"] == 9.0
    assert summary["animals"][0]["animal_tag"] == animal["tag_number"]


async def test_milk_requires_female_active_animal(client):
    headers = await _dairy_owner(client)
    male = await _create_animal(
        client, headers, {"tag_number": "BULL-1", "sex": "M", "current_bucket": "MALE_KIDS"}
    )
    resp = await client.post(
        "/api/milk/new",
        json={
            "animal_id": male["id"],
            "date": date.today().isoformat(),
            "shift": "MORNING",
            "litres": 5.0,
        },
        headers=headers,
    )
    assert resp.status_code == 422


async def test_milk_sale_transaction_provenance(client):
    headers = await _dairy_owner(client)
    resp = await client.post(
        "/api/finance/new",
        json={
            "date": date.today().isoformat(),
            "type": "INCOME",
            "category": "MILK",
            "amount": 3920.0,
            "notes": "Vijaya pickup",
            "milk_litres": 70.0,
            "milk_unit_price_per_litre": 56.0,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["milk_litres"] == 70.0
    assert body["milk_unit_price_per_litre"] == 56.0
    # Provenance is refused on non-MILK income.
    resp2 = await client.post(
        "/api/finance/new",
        json={
            "date": date.today().isoformat(),
            "type": "INCOME",
            "category": "MANURE",
            "amount": 1000.0,
            "milk_litres": 10.0,
            "milk_unit_price_per_litre": 50.0,
        },
        headers=headers,
    )
    assert resp2.status_code == 422


# --- dairy simulation preset ----------------------------------------------------


def test_murrah_preset_defaults():
    from app.simulation.defaults import get_preset

    a = get_preset("murrah_dairy")
    assert a.reproduction.gestation_months == 10
    assert a.reproduction.lactation_months == 10
    assert a.reproduction.litter_size == 1.0
    assert a.reproduction.conception_rate == pytest.approx(0.45)
    assert a.sales.lactation_milk_litres == pytest.approx(2400.0)
    assert a.sales.milk_price_per_kg_fat == pytest.approx(950.0)
    assert a.sales.milk_fat_pct == pytest.approx(6.8)
    assert a.sales.male_calf_sell_at_birth_fraction == pytest.approx(0.9)
    assert a.growth.birth_weight_kg == pytest.approx(34.0)
    assert a.growth.weight_by_age_months[0] == pytest.approx(34.0)


def test_murrah_simulation_is_mass_balanced_and_profitable_shape():
    from app.simulation.defaults import get_preset
    from app.simulation.engine import run_simulation

    result = run_simulation(get_preset("murrah_dairy"), with_break_even=False)
    # Milk is the dominant revenue line from year 1 (Phase A procurement).
    y1 = result.annual_pl[0]
    assert y1.milk_revenue > 0
    assert y1.milk_revenue > 4 * max(y1.meat_revenue, 1.0)
    # The herd grows toward the 200-milking plan, not to zero.
    assert result.months[-1].total_herd > 60.0
    # Mass balance: no month reports a negative herd.
    assert all(m.total_herd >= 0 for m in result.months)


def test_murrah_monte_carlo_milk_price_risk():
    from app.simulation.defaults import get_preset
    from app.simulation.engine import run_simulation

    a = get_preset("murrah_dairy")
    a.risk.monte_carlo_runs = 25
    result = run_simulation(a, with_break_even=False, with_monte_carlo=True)
    mc = result.monte_carlo
    assert mc.npv_p5 <= mc.npv_p50 <= mc.npv_p95
    assert mc.npv_p95 > mc.npv_p5
