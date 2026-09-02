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


async def test_ultrasound_kid_count_is_species_capped(client):
    """Detected-fetus counts follow the species litter cap: goat scans may
    report 4 (SpeciesProfile.max_litter_size), a buffalo PD stops at 2."""
    headers = await _dairy_owner(client, email="litter-cap@farm.in")
    animal = await _create_animal(client, headers)
    bred = await client.post(
        "/api/breeding",
        json={
            "doe_id": animal["id"],
            "breeding_date": (date.today() - timedelta(days=320)).isoformat(),
            "method": "AI",
            "semen_sire_name": "Karanvir 999",
        },
        headers=headers,
    )
    assert bred.status_code == 201, bred.text
    too_many = await client.post(
        f"/api/breeding/{bred.json()['id']}/ultrasound",
        json={
            "pregnant": True,
            "kid_count": 3,
            "date": (date.today() - timedelta(days=255)).isoformat(),
        },
        headers=headers,
    )
    assert too_many.status_code == 422, too_many.text
    assert "cannot carry more than 2 calves" in too_many.json()["detail"]

    # Goat side of the cap: a quadruplet scan is a legal goat observation.
    goat_headers = await register(client, email="litter-cap-goat@farm.in")
    goat_headers = await create_farm(client, goat_headers, "Goat Litter Farm")
    doe = await client.post(
        "/api/animals",
        json={
            "tag_number": "DOE-QUAD",
            "sex": "F",
            "breed": "Osmanabadi",
            "date_of_birth": (date.today() - timedelta(days=400)).isoformat(),
            "source": "PURCHASED",
            "current_bucket": "BREEDING",
            "historical_import_reason": "litter-cap fixture",
            "weight_kg": 30.0,
            # Dated before the backdated service so the eligibility gate's
            # as-of weight query sees it.
            "weight_date": (date.today() - timedelta(days=390)).isoformat(),
        },
        headers=goat_headers,
    )
    assert doe.status_code in (200, 201), doe.text
    gb = await client.post(
        "/api/breeding",
        json={
            "doe_id": doe.json()["id"],
            "breeding_date": (date.today() - timedelta(days=40)).isoformat(),
            "method": "AI",
            "semen_sire_name": "Semen sire 7",
        },
        headers=goat_headers,
    )
    assert gb.status_code == 201, gb.text
    quad = await client.post(
        f"/api/breeding/{gb.json()['id']}/ultrasound",
        json={
            "pregnant": True,
            "kid_count": 4,
            "date": (date.today() - timedelta(days=8)).isoformat(),
        },
        headers=goat_headers,
    )
    assert quad.status_code == 200, quad.text
    assert quad.json()["kid_count_detected"] == 4


# --- milk records ---------------------------------------------------------------


async def test_milk_record_upsert_and_summary(client):
    headers = await _dairy_owner(client)
    animal = await _create_animal(client, headers)
    today = date.today().isoformat()
    for litres in (8.5, 9.0):
        payload = {
            "animal_id": animal["id"],
            "date": today,
            "shift": "MORNING",
            "litres": litres,
            "fat_pct": 6.9,
        }
        if litres != 8.5:
            # Re-submitting the same milking is a correction and must say why.
            payload["correction_reason"] = "Mis-keyed yield"
        resp = await client.post("/api/milk/new", json=payload, headers=headers)
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


async def test_milk_endpoints_reject_goat_farm(client):
    """Milk is a dairy operation: a goat (meat) farm gets a 422, not an empty
    parlour, from every milk route."""
    headers = await owner_with_farm(client, email="goat-milk@farm.in")
    # A schema-valid submission: the refusal must come from the farm-type
    # gate, not from body validation.
    valid = {
        "animal_id": 1,
        "date": date.today().isoformat(),
        "shift": "MORNING",
        "litres": 5.0,
    }
    for method, path, kwargs in (
        ("GET", "/api/milk", {}),
        ("GET", "/api/milk/summary", {}),
        ("POST", "/api/milk/new", {"json": valid}),
    ):
        resp = await client.request(method, path, headers=headers, **kwargs)
        assert resp.status_code == 422, (method, path, resp.text)
        assert resp.json()["detail"] == "Milk is recorded on buffalo dairy farms only"
    # The dairy twin of the same farm gets through (and has all along above).
    dairy = await _dairy_owner(client, email="dairy-milk-ok@farm.in")
    resp = await client.get("/api/milk", headers=dairy)
    assert resp.status_code == 200, resp.text
    resp = await client.get("/api/milk/summary", headers=dairy)
    assert resp.status_code == 200, resp.text
    assert resp.json()["animals_total"] == 0
    assert resp.json()["animals"] == []


async def test_milk_correction_freezes_original_and_requires_reason(client):
    """Correcting a milking is an audited edit, never a silent overwrite."""
    headers = await _dairy_owner(client)
    animal = await _create_animal(client, headers, {"tag_number": "BUF-AUDIT"})
    today = date.today().isoformat()

    def submit(**overrides):
        return {
            "animal_id": animal["id"],
            "date": today,
            "shift": "MORNING",
            "litres": 8.5,
            "fat_pct": 6.9,
            "notes": "first reading",
        } | overrides

    first = (await client.post("/api/milk/new", json=submit(), headers=headers)).json()
    assert first["original_litres"] is None
    assert first["original_fat_pct"] is None
    assert first["original_notes"] is None
    assert first["original_recorded_by_id"] is None
    assert first["corrected_at"] is None
    assert first["correction_reason"] is None

    # A correction without a stated reason is refused and changes nothing.
    refused = await client.post(
        "/api/milk/new", json=submit(litres=9.2), headers=headers
    )
    assert refused.status_code == 422, refused.text
    assert "correction_reason" in refused.json()["detail"]

    corrected = (
        await client.post(
            "/api/milk/new",
            json=submit(litres=9.2, fat_pct=7.1, correction_reason="Mis-keyed yield"),
            headers=headers,
        )
    ).json()
    assert corrected["litres"] == 9.2
    assert corrected["fat_pct"] == 7.1
    assert corrected["original_litres"] == 8.5  # the FIRST reading is frozen
    assert corrected["original_fat_pct"] == 6.9
    assert corrected["original_notes"] == "first reading"
    assert corrected["original_recorded_by_id"] is not None
    assert corrected["corrected_at"] is not None
    assert corrected["correction_reason"] == "Mis-keyed yield"

    # A second correction moves the current values but never the originals.
    again = (
        await client.post(
            "/api/milk/new",
            json=submit(litres=9.5, fat_pct=7.2, correction_reason="Fat test re-run"),
            headers=headers,
        )
    ).json()
    assert again["litres"] == 9.5
    assert again["original_litres"] == 8.5
    assert again["original_fat_pct"] == 6.9
    assert again["original_notes"] == "first reading"
    assert again["correction_reason"] == "Fat test re-run"
    assert again["corrected_at"] >= corrected["corrected_at"]

    # The audit trail is visible on the read path too, still one row.
    listing = (await client.get("/api/milk?date_from=" + today, headers=headers)).json()
    assert listing["total"] == 1
    row = listing["records"][0]
    assert row["original_litres"] == 8.5
    assert row["correction_reason"] == "Fat test re-run"


async def test_milk_summary_weighted_fat_and_animals_total(client):
    """Fat averages are litre-weighted and the animal list reports its true
    window total alongside the capped page."""
    headers = await _dairy_owner(client)
    animal_a = await _create_animal(client, headers, {"tag_number": "BUF-W-A"})
    animal_b = await _create_animal(client, headers, {"tag_number": "BUF-W-B"})
    animal_c = await _create_animal(client, headers, {"tag_number": "BUF-W-C"})
    today = date.today().isoformat()

    async def record(animal_id, shift, litres, fat_pct=None):
        payload = {
            "animal_id": animal_id,
            "date": today,
            "shift": shift,
            "litres": litres,
        }
        if fat_pct is not None:
            payload["fat_pct"] = fat_pct
        resp = await client.post("/api/milk/new", json=payload, headers=headers)
        assert resp.status_code == 201, resp.text

    await record(animal_a["id"], "MORNING", 10.0, 6.0)
    await record(animal_a["id"], "NIGHT", 2.0, 7.0)
    await record(animal_b["id"], "MORNING", 6.0, 5.0)
    # An untested shipment counts its litres, but weighs on no fat average.
    await record(animal_c["id"], "MORNING", 4.0)

    summary = (await client.get("/api/milk/summary?days=7", headers=headers)).json()
    assert summary["animals_total"] == 3
    assert len(summary["animals"]) == 3
    # Herd: (10x6.0 + 2x7.0 + 6x5.0) / 18 = 5.777... — an unweighted record
    # mean would report 6.0, and animal A alone would report 6.5.
    assert summary["total_litres"] == 22.0
    assert summary["avg_fat_pct"] == 5.78
    by_tag = {row["animal_tag"]: row for row in summary["animals"]}
    assert by_tag["BUF-W-A"]["avg_fat_pct"] == 6.17
    assert by_tag["BUF-W-B"]["avg_fat_pct"] == 5.0
    assert by_tag["BUF-W-C"]["avg_fat_pct"] is None
    assert summary["daily"][0]["avg_fat_pct"] == 5.78
    assert summary["daily"][0]["recorded_animals"] == 3


async def _record_parlour_milk(client, headers, litres_by_day):
    """Record parlour yields on a purchased adult buffalo so milk-income
    sales reconcile against real production."""
    animal = await _create_animal(client, headers)
    for day, litres in litres_by_day:
        resp = await client.post(
            "/api/milk/new",
            json={
                "animal_id": animal["id"],
                "date": day,
                "shift": "MORNING",
                "litres": litres,
                "fat_pct": 6.9,
            },
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
    return animal


async def test_milk_sale_transaction_provenance(client):
    headers = await _dairy_owner(client)
    today = date.today()
    await _record_parlour_milk(
        client,
        headers,
        [(today.isoformat(), 35.0), ((today - timedelta(days=1)).isoformat(), 35.0)],
    )
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


async def test_milk_income_rejected_on_goat_farm(client):
    """MILK is a dairy-ledger category in both the create and correct paths."""
    headers = await owner_with_farm(client, email="goat-ledger@farm.in")
    payload = {
        "date": date.today().isoformat(),
        "type": "INCOME",
        "category": "MILK",
        "amount": 2800.0,
        # Complete provenance: the dairy-farm gate is what must refuse this
        # row on a goat farm (a provenance-less row is refused earlier by the
        # schema's mandatory-provenance rule).
        "milk_litres": 50.0,
        "milk_unit_price_per_litre": 56.0,
    }
    resp = await client.post("/api/finance/new", json=payload, headers=headers)
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == "Milk income is booked on buffalo dairy farms only"
    # The category is refused for expenses too, and the ledger stays empty.
    expense = await client.post(
        "/api/finance/new", json=payload | {"type": "EXPENSE"}, headers=headers
    )
    assert expense.status_code == 422, expense.text
    listing = (await client.get("/api/finance", headers=headers)).json()
    assert listing["transactions"] == []
    # A goat farm cannot correct its way into a MILK row either.
    other = (
        await client.post(
            "/api/finance/new",
            json=payload
            | {
                "category": "OTHER",
                "type": "EXPENSE",
                "milk_litres": None,
                "milk_unit_price_per_litre": None,
            },
            headers=headers,
        )
    ).json()
    rebooked = await client.post(
        f"/api/finance/transactions/{other['id']}/correct",
        json=payload | {"reason": "Recategorise as milk"},
        headers=headers,
    )
    assert rebooked.status_code == 422, rebooked.text
    assert rebooked.json()["detail"] == "Milk income is booked on buffalo dairy farms only"


async def test_milk_fat_priced_sale_provenance(client):
    """Fat-based procurement: litres x fat% x ₹/kg-fat must price the amount."""
    headers = await _dairy_owner(client, email="dairy-fat-price@farm.in")
    today = date.today()
    # Ten days of parlour yield: the test books several cumulative sales
    # (70 + 70 + 100 + 100 L), all of which must reconcile against production.
    await _record_parlour_milk(
        client,
        headers,
        [((today - timedelta(days=n)).isoformat(), 35.0) for n in range(10)],
    )
    base = {
        "date": date.today().isoformat(),
        "type": "INCOME",
        "category": "MILK",
        "notes": "Sangam pickup",
    }
    # 70 L at 6.9% fat and ₹950/kg fat prices to ₹4,588.50.
    booked = (
        await client.post(
            "/api/finance/new",
            json=base
            | {
                "amount": 4588.50,
                "milk_litres": 70.0,
                "milk_fat_pct": 6.9,
                "milk_price_per_kg_fat": 950.0,
            },
            headers=headers,
        )
    ).json()
    assert booked["milk_litres"] == 70.0
    assert booked["milk_fat_pct"] == 6.9
    assert booked["milk_price_per_kg_fat"] == 950.0
    assert booked["milk_unit_price_per_litre"] is None
    assert booked["amount"] == 4588.50

    # A mistyped amount that disagrees with the provenance is refused. The
    # refusal is a schema-level 422, so the message rides the validation
    # error list rather than a plain string detail.
    mismatch = await client.post(
        "/api/finance/new",
        json=base
        | {
            "amount": 4588.00,
            "milk_litres": 70.0,
            "milk_fat_pct": 6.9,
            "milk_price_per_kg_fat": 950.0,
        },
        headers=headers,
    )
    assert mismatch.status_code == 422, mismatch.text
    assert "prices to ₹4588.50" in mismatch.text

    # One paisa of rounding is tolerated; two is not.
    assert (
        await client.post(
            "/api/finance/new",
            json=base
            | {
                "amount": 4588.51,
                "milk_litres": 70.0,
                "milk_fat_pct": 6.9,
                "milk_price_per_kg_fat": 950.0,
            },
            headers=headers,
        )
    ).status_code == 201
    assert (
        await client.post(
            "/api/finance/new",
            json=base
            | {
                "amount": 4588.52,
                "milk_litres": 70.0,
                "milk_fat_pct": 6.9,
                "milk_price_per_kg_fat": 950.0,
            },
            headers=headers,
        )
    ).status_code == 422

    # The fat pair is all-or-nothing and cannot stand in for litres.
    for incomplete in (
        {"milk_litres": 70.0, "milk_fat_pct": 6.9},
        {"milk_litres": 70.0, "milk_price_per_kg_fat": 950.0},
        {"milk_fat_pct": 6.9, "milk_price_per_kg_fat": 950.0},
        {"milk_unit_price_per_litre": 56.0},
    ):
        resp = await client.post(
            "/api/finance/new", json=base | {"amount": 3920.0} | incomplete, headers=headers
        )
        assert resp.status_code == 422, incomplete

    # When both bases are given, fat pricing is the price of record:
    # 100 L x 6.0% x ₹800 = ₹4,800, not 100 L x ₹50 = ₹5,000.
    both = await client.post(
        "/api/finance/new",
        json=base
        | {
            "amount": 4800.0,
            "milk_litres": 100.0,
            "milk_fat_pct": 6.0,
            "milk_price_per_kg_fat": 800.0,
            "milk_unit_price_per_litre": 50.0,
        },
        headers=headers,
    )
    assert both.status_code == 201, both.text
    flat = await client.post(
        "/api/finance/new",
        json=base
        | {
            "amount": 5000.0,
            "milk_litres": 100.0,
            "milk_fat_pct": 6.0,
            "milk_price_per_kg_fat": 800.0,
            "milk_unit_price_per_litre": 50.0,
        },
        headers=headers,
    )
    assert flat.status_code == 422, flat.text

    # The flat ₹/litre basis keeps working exactly as before.
    per_litre = (
        await client.post(
            "/api/finance/new",
            json=base | {"amount": 3920.0, "milk_litres": 70.0, "milk_unit_price_per_litre": 56.0},
            headers=headers,
        )
    ).json()
    assert per_litre["amount"] == 3920.0
    assert per_litre["milk_fat_pct"] is None
    assert per_litre["milk_price_per_kg_fat"] is None

    # Corrections restate fat-based provenance under the same pricing rule
    # (69.75 L x 6.9% x ₹950 prices to ₹4,572.11).
    corrected = (
        await client.post(
            f"/api/finance/transactions/{booked['id']}/correct",
            json=base
            | {
                "amount": 4572.11,
                "milk_litres": 69.75,
                "milk_fat_pct": 6.9,
                "milk_price_per_kg_fat": 950.0,
                "reason": "Tare weight on the tanker slip",
            },
            headers=headers,
        )
    ).json()
    assert corrected["milk_litres"] == 69.75
    assert corrected["milk_fat_pct"] == 6.9
    assert corrected["correction_of_id"] == booked["id"]
    bad_correction = await client.post(
        f"/api/finance/transactions/{corrected['id']}/correct",
        json=base
        | {
            "amount": 4000.0,
            "milk_litres": 69.75,
            "milk_fat_pct": 6.9,
            "milk_price_per_kg_fat": 950.0,
            "reason": "Wrong amount",
        },
        headers=headers,
    )
    assert bad_correction.status_code == 422, bad_correction.text


# --- dairy simulation preset ----------------------------------------------------


def test_murrah_preset_defaults():
    from app.simulation.defaults import get_preset

    a = get_preset("murrah_dairy")
    assert a.reproduction.gestation_months == 10
    assert a.reproduction.lactation_months == 10
    assert a.reproduction.litter_size == 1.0
    assert a.reproduction.conception_rate == pytest.approx(0.45)
    # Calibrated to the 2025-26 CIRB/NDRI lactation and Telangana procurement
    # figures (see the preset docstring): 2,100 L over a 305-day lactation,
    # Verified procurement basis Rs 850/kg fat (Vijaya/Sangam 2025-26)
    # sales (Rs 58/L flat fallback).
    assert a.sales.lactation_milk_litres == pytest.approx(2100.0)
    assert a.sales.milk_price_per_kg_fat == pytest.approx(850.0)
    assert a.sales.milk_price_per_litre == pytest.approx(58.0)
    assert a.sales.milk_fat_pct == pytest.approx(6.8)
    assert a.sales.male_calf_sell_at_birth_fraction == pytest.approx(0.9)
    assert a.sales.male_calf_price_per_head == pytest.approx(1600.0)
    # Cull buffaloes price near the Rs 145-170/kg live floor.
    assert a.sales.cull_doe_price_per_kg == pytest.approx(160.0)
    assert a.sales.cull_buck_price_per_kg == pytest.approx(170.0)
    # CIRB recorded calf weights: males 31.7 kg, females 30 kg; the curve is
    # 31 kg + 15.3 kg/month to a ~215 kg yearling.
    assert a.growth.birth_weight_kg == pytest.approx(31.0)
    assert a.growth.weight_by_age_months[0] == pytest.approx(31.0)
    assert a.growth.weight_by_age_months == pytest.approx(
        [31.0 + 15.3 * month for month in range(13)]
    )
    # Heifers are bred at ~345 kg / 24 months and mature to the adult weight
    # at 40 months (NDRI growth studies), not the goat-default 24.
    assert a.reproduction.age_at_first_breeding_months == 24
    assert a.growth.adult_weight_age_months == 40
    assert a.growth.young_male_weight_premium == pytest.approx(0.05)
    # AI-first breeding policy: sexed semen for the first two services (90%
    # female births at a 15% conception penalty), a 3-service repeat-breeder
    # cull, and a flat 50:50 ratio on conventional services.
    assert a.reproduction.sex_ratio_female == pytest.approx(0.5)
    assert a.reproduction.sexed_semen_services == 2
    assert a.reproduction.sexed_female_fraction == pytest.approx(0.90)
    assert a.reproduction.sexed_conception_multiplier == pytest.approx(0.85)
    assert a.reproduction.max_services_before_cull == 3
    # Voluntary/age culls only: the repeat-breeder rule above removes ~17%/yr,
    # so the residual rate cull is 5% (total disposal ~21%/yr, ~4.8 lactations).
    assert a.culling.doe_cull_rate_annual == pytest.approx(0.05)
    # A 25-acre multi-cut fodder plot at 10 t DM/acre/yr covers ~80% of the
    # green-DM need at home cost.
    assert a.feed.cultivated_fodder_acres == pytest.approx(25.0)
    assert a.feed.fodder_yield_t_dm_per_acre_year == pytest.approx(10.0)
    # Dairy is a milk business, not a sacrificial-market one: the preset
    # explicitly disables the (auto-filled) Bakrid festival pricing.
    assert a.sales.festival_sale_months == []


def test_murrah_simulation_is_mass_balanced_and_profitable_shape():
    from app.simulation.defaults import get_preset
    from app.simulation.engine import run_simulation

    preset = get_preset("murrah_dairy")
    result = run_simulation(preset, with_break_even=False)
    # Milk is the dominant revenue line from year 1 (Phase A procurement).
    y1 = result.annual_pl[0]
    assert y1.milk_revenue > 0
    assert y1.milk_revenue > 4 * max(y1.meat_revenue, 1.0)
    # The herd grows toward the 200-milking plan, not to zero.
    assert result.months[-1].total_herd > 60.0
    # Mass balance holds EXACTLY, including the dairy finishing pen (culled
    # does leave the breeding pools immediately but keep milking/eating until
    # their lactation overlay dries off):
    # total_herd == prev + births + purchases - deaths - sales - culls.
    h = preset.herd
    previous = float(
        h.does
        + h.bucks
        + h.female_kids
        + h.male_kids
        + h.female_weaners
        + h.male_weaners
        + h.female_growers
        + h.male_growers
    )
    for m in result.months:
        expected = (
            previous + m.births + m.purchases_head - m.deaths - m.sales_head - m.culls_head
        )
        assert m.total_herd == pytest.approx(expected, abs=1e-6), m.month
        assert m.total_herd >= 0
        previous = m.total_herd


def test_murrah_monte_carlo_milk_price_risk():
    from app.simulation.defaults import get_preset
    from app.simulation.engine import run_simulation

    a = get_preset("murrah_dairy")
    a.risk.monte_carlo_runs = 25
    result = run_simulation(a, with_break_even=False, with_monte_carlo=True)
    mc = result.monte_carlo
    assert mc.npv_p5 <= mc.npv_p50 <= mc.npv_p95
    assert mc.npv_p95 > mc.npv_p5


def test_dairy_culls_book_at_dry_off_and_keep_milking_until_then():
    """A real dairy culls at dry-off, not mid-lactation: culled does leave the
    breeding pools immediately (no further service) but keep milking and
    eating in the finishing pen until their lactation overlay ends — cull
    head and revenue book THAT month. Meat mode books the cull immediately."""
    from app.simulation import (
        CullingAssumptions,
        HerdAssumptions,
        MetaAssumptions,
        MortalityAssumptions,
        ReproductionAssumptions,
        SalesAssumptions,
        SimulationAssumptions,
        run_simulation,
    )

    def run(milk_litres: float):
        # Open foundation, certain conception, single kids, no mortality: the
        # six does calve in month 12, so the month-13 rate cull catches them
        # all in milk (a 100% annual rate compounds to everything in month 13).
        assumptions = SimulationAssumptions(
            meta=MetaAssumptions(horizon_months=18),
            herd=HerdAssumptions(
                does=6, bucks=0, auto_purchase_bucks=False, foundation_flock_state="open"
            ),
            reproduction=ReproductionAssumptions(
                conception_rate=1.0,
                gestation_months=5,
                lactation_months=3,
                months_open_before_breeding=1,
                litter_size=1.0,
                stillbirth_rate=0.0,
            ),
            mortality=MortalityAssumptions(
                kid_pre_weaning=0.0, kid_post_weaning=0.0, grower=0.0, adult=0.0
            ),
            culling=CullingAssumptions(doe_cull_rate_annual=1.0, max_doe_age_months=180),
            sales=SalesAssumptions(
                lactation_milk_litres=milk_litres,
                milk_price_per_litre=30.0,
                annual_milk_price_growth_rate=0.0,
                annual_livestock_price_growth_rate=0.0,
                monthly_milk_yield_multipliers=[1.0] * 12,
                monthly_milk_price_multipliers=[1.0] * 12,
                monthly_meat_price_multipliers=[1.0] * 12,
                festival_sale_months=[],
            ),
        )
        return run_simulation(assumptions, with_break_even=False)

    dairy = run(100.0)
    meat = run(0.0)

    # Meat mode: the cull decision and the booking are the same month.
    assert meat.months[12].culls_head == pytest.approx(6.0)
    assert meat.months[12].cull_revenue > 0.0
    # Dairy mode: month 13 books nothing — every culled doe is still milking.
    assert dairy.months[12].culls_head == pytest.approx(0.0)
    assert dairy.months[12].cull_revenue == 0.0
    assert dairy.months[12].milk_revenue > 0.0
    assert dairy.months[13].milk_revenue > 0.0  # the finishing pen keeps milking
    # Their 3-month lactation (fresh in month 12) dries off in month 15: that
    # is when the head and the cull revenue book.
    assert dairy.months[13].culls_head == pytest.approx(0.0)
    assert dairy.months[14].culls_head == pytest.approx(6.0)
    assert dairy.months[14].cull_revenue > 0.0
    assert dairy.months[14].milk_revenue == 0.0


# --- dairy RBAC: parlour roles and farm-type-scoped presets ---------------------


async def _worker_with_role(client, owner, role_code: str, email: str) -> dict:
    """Add a worker holding a seeded preset role and return login headers."""
    from .conftest import login

    resp = await client.get("/api/team", headers=owner)
    assert resp.status_code == 200, resp.text
    role_id = next(r["id"] for r in resp.json()["roles"] if r["code"] == role_code)
    resp = await client.post(
        "/api/team/workers",
        json={"name": "Worker", "email": email, "password": "workerpass123", "role_id": role_id},
        headers=owner,
    )
    assert resp.status_code == 201, resp.text
    worker = await login(client, email, "workerpass123")
    return worker | {"X-Farm-Id": owner["X-Farm-Id"]}


async def test_dairy_presets_are_scoped_by_farm_type(client):
    """Dairy parlour presets seed only on dairy farms; the universal
    management/office presets seed everywhere."""
    from app.permissions import preset_codes_for_farm_type

    goat_owner = await owner_with_farm(client, email="scope-goat@farm.in")
    dairy_owner = await _dairy_owner(client, email="scope-dairy@farm.in")

    goat_resp = await client.get("/api/team", headers=goat_owner)
    assert goat_resp.status_code == 200, goat_resp.text
    dairy_resp = await client.get("/api/team", headers=dairy_owner)
    assert dairy_resp.status_code == 200, dairy_resp.text
    goat_codes = {r["code"] for r in goat_resp.json()["roles"]}
    dairy_codes = {r["code"] for r in dairy_resp.json()["roles"]}
    assert goat_codes == preset_codes_for_farm_type("GOAT")
    assert dairy_codes == preset_codes_for_farm_type("BUFFALO_DAIRY")
    assert {"MILKER", "MILK_QC", "CALF_ATTENDANT"} <= dairy_codes
    assert not {"MILKER", "MILK_QC", "CALF_ATTENDANT"} & goat_codes
    # The new universal presets land on both farm types.
    assert {"MANAGER", "BUYER", "ACCOUNTANT", "VIEWER"} <= goat_codes & dairy_codes


async def test_milker_records_yield_but_only_quality_roles_set_fat(client):
    """Separation of duties on the fat number: the parlour recorder keys
    litres, the ₹/kg-fat input is owned by the quality/manager roles, and a
    recorder's litre correction cannot erase a tested fat."""
    owner = await _dairy_owner(client, email="dairy-rbac@farm.in")
    animal = await _create_animal(client, owner)
    milker = await _worker_with_role(client, owner, "MILKER", "milker@farm.in")
    qc = await _worker_with_role(client, owner, "MILK_QC", "qc@farm.in")
    today = date.today().isoformat()

    def payload(litres, fat=None, reason=None):
        body = {
            "animal_id": animal["id"],
            "date": today,
            "shift": "MORNING",
            "litres": litres,
        }
        if fat is not None:
            body["fat_pct"] = fat
        if reason is not None:
            body["correction_reason"] = reason
        return body

    # The recorder keys the yield without a fat test.
    resp = await client.post("/api/milk/new", json=payload(8.5), headers=milker)
    assert resp.status_code == 201, resp.text
    assert resp.json()["fat_pct"] is None

    # The recorder cannot set the fat number that pricing pays on.
    resp = await client.post(
        "/api/milk/new", json=payload(8.5, fat=7.1, reason="retry"), headers=milker
    )
    assert resp.status_code == 403
    assert "fat test" in resp.json()["detail"]

    # The quality supervisor enters the fat test for the same milking.
    resp = await client.post(
        "/api/milk/new", json=payload(8.6, fat=6.8, reason="Add fat test"), headers=qc
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["fat_pct"] == 6.8

    # The recorder corrects a mis-keyed yield: litres change, fat survives.
    resp = await client.post(
        "/api/milk/new", json=payload(8.9, reason="Mis-keyed litres"), headers=milker
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["litres"] == 8.9
    assert resp.json()["fat_pct"] == 6.8


# --- frozen dairy preset bundles (contract mirror, goat side lives in
# --- test_team_extended.PRESET_PERMS) -------------------------------------------


DAIRY_PRESET_PERMS: dict[str, set[str]] = {
    "MILKER": {
        "dashboard.view",
        "animals.view",
        "buckets.view",
        "milk.view",
        "milk.manage",
        "tasks.view",
        "tasks.complete",
    },
    # The quality supervisor owns the ₹/kg-fat input; the recorder never does.
    "MILK_QC": {
        "dashboard.view",
        "animals.view",
        "buckets.view",
        "milk.view",
        "milk.manage",
        "milk.quality",
        "tasks.view",
        "tasks.complete",
    },
    "CALF_ATTENDANT": {
        "dashboard.view",
        "animals.view",
        "animals.move",
        "buckets.view",
        "kidding.view",
        "milk.view",
        "tasks.view",
        "tasks.complete",
    },
}

DAIRY_PRESET_NAMES = {
    "MILKER": "Milking Attendant",
    "MILK_QC": "Milk Quality Supervisor",
    "CALF_ATTENDANT": "Calf-shed Attendant",
}


async def test_dairy_preset_bundles_are_frozen(client):
    """The dairy-only presets' permission bundles are frozen here as the
    contract under test: editing a seeded dairy preset in app/permissions.py
    must be a deliberate act that updates this mirror."""
    owner = await _dairy_owner(client, email="dairy-frozen@farm.in")
    resp = await client.get("/api/team", headers=owner)
    assert resp.status_code == 200, resp.text
    roles = {r["code"]: r for r in resp.json()["roles"]}
    for code, perms in DAIRY_PRESET_PERMS.items():
        assert code in roles, f"{code} missing from the dairy farm's presets"
        assert set(roles[code]["permissions"]) == perms
        assert roles[code]["name"] == DAIRY_PRESET_NAMES[code]
        assert roles[code]["description"]


async def test_dairy_weaning_duty_lands_on_calf_attendant(client):
    """End to end: a real calving on a dairy farm spawns the day-~90 milk
    weaning duty on the Calf-shed Attendant, not the mover (goat routing)."""
    owner = await _dairy_owner(client, email="dairy-weaning@farm.in")
    animal = await _create_animal(client, headers=owner)
    confirmed = await _breed_and_confirm(client, owner, animal["id"])
    resp = await client.post(
        "/api/kidding",
        json={
            "breeding_record_id": confirmed["id"],
            "date": confirmed["expected_kidding_date"],
            "ease": "NORMAL",
            "kids": [{"sex": "F", "status": "ALIVE", "birth_weight": 34.0}],
        },
        headers=owner,
    )
    assert resp.status_code in (200, 201), resp.text

    tabs = (await client.get("/api/tasks", headers=owner)).json()
    duties = [t for tab in tabs.values() if isinstance(tab, list) for t in tab]
    weaning = [t for t in duties if t["category"] == "WEANING" and "Wean calves" in t["title"]]
    assert weaning, f"no dairy weaning duty spawned; duties: {[t['title'] for t in duties]}"
    assert weaning[0]["assigned_role_name"] == "Calf-shed Attendant"
    assert weaning[0]["assigned_role_id"] is not None


async def test_feeder_cannot_record_milk_on_a_dairy(client):
    """FEEDER lost milk.manage: a feed error must not be correctable by
    editing the milk ledger, at the API level and not just in the bundle."""
    owner = await _dairy_owner(client, email="dairy-feeder@farm.in")
    animal = await _create_animal(client, headers=owner)
    feeder = await _worker_with_role(client, owner, "FEEDER", "feeder-dairy@farm.in")
    resp = await client.post(
        "/api/milk/new",
        json={
            "animal_id": animal["id"],
            "date": date.today().isoformat(),
            "shift": "MORNING",
            "litres": 8.0,
        },
        headers=feeder,
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == "Missing permission: milk.manage"
