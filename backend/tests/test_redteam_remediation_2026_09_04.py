"""Red-team remediation proofs (audit round 2026-09-04).

Every test here replays one attack from audit_reports/2026-09-04/ against the
fixed code and asserts the attack now FAILS with the intended status and
message, plus a negative control showing legitimate traffic still passes.
Identifiers (RED-H1, RED-M3, …) match the remediation log.
"""

import logging
import re
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest

from app.core.config import get_settings

from . import conftest
from .conftest import owner_with_farm

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GOAT_GESTATION = 150


def iso(d: date) -> str:
    return d.isoformat()


def today() -> date:
    return date.today()


# ---------------------------------------------------------------------------
# Shared API helpers (local copies so this file stays self-contained)
# ---------------------------------------------------------------------------


async def _make_animal(
    client: httpx.AsyncClient, headers: dict, tag: str, *, sex: str, bucket: str, **overrides
) -> dict:
    payload: dict = {
        "tag_number": tag,
        "sex": sex,
        "source": "PURCHASED",
        "current_bucket": bucket,
        "historical_import_reason": "Red-team fixture",
    } | overrides
    resp = await client.post("/api/animals", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _adult(
    client: httpx.AsyncClient,
    headers: dict,
    tag: str,
    *,
    sex: str,
    bucket: str,
    age_days: int = 800,
    weight_kg: float = 30.0,
) -> dict:
    dob = today() - timedelta(days=age_days)
    return await _make_animal(
        client,
        headers,
        tag,
        sex=sex,
        bucket=bucket,
        date_of_birth=iso(dob),
        weight_kg=weight_kg,
        weight_date=iso(dob),
    )


async def _breed(
    client: httpx.AsyncClient,
    headers: dict,
    doe_id: int,
    breeding_date: date,
    *,
    method: str = "NATURAL",
    buck_id: int | None = None,
    semen_sire: str | None = None,
    idempotency_key: str | None = None,
) -> httpx.Response:
    payload: dict = {
        "doe_id": doe_id,
        "breeding_date": iso(breeding_date),
        "method": method,
    }
    if buck_id is not None:
        payload["buck_id"] = buck_id
    if method != "NATURAL":
        payload["semen_sire_name"] = semen_sire or "Test Sire 001"
    request_headers = dict(headers)
    if idempotency_key is not None:
        request_headers["Idempotency-Key"] = idempotency_key
    return await client.post("/api/breeding", json=payload, headers=request_headers)


async def _confirm_pregnant(
    client: httpx.AsyncClient, headers: dict, breeding_record: dict, scan_offset_days: int
) -> dict:
    scan_date = date.fromisoformat(breeding_record["breeding_date"]) + timedelta(
        days=scan_offset_days
    )
    resp = await client.post(
        f"/api/breeding/{breeding_record['id']}/ultrasound",
        json={"pregnant": True, "kid_count": 1, "date": iso(scan_date)},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _kidding(
    client: httpx.AsyncClient,
    headers: dict,
    breeding_record: dict,
    kidding_date: date,
    kids: list[dict],
    *,
    idempotency_key: str | None = None,
) -> httpx.Response:
    request_headers = dict(headers)
    if idempotency_key is not None:
        request_headers["Idempotency-Key"] = idempotency_key
    return await client.post(
        "/api/kidding",
        json={
            "breeding_record_id": breeding_record["id"],
            "date": iso(kidding_date),
            "ease": "NORMAL",
            "kids": kids,
        },
        headers=request_headers,
    )


async def _goat_doe_buck_and_kids(
    client: httpx.AsyncClient, headers: dict, *, tag_stem: str
) -> tuple[dict, dict, dict, dict]:
    """Doe × buck kidded ~410 days ago; dam then DEAD so both kids early-wean
    into sex buckets. Returns (father_buck, daughter, son, kidding_record).

    The daughter and son are now breeding-age (13+ months) with dated weight
    records, and their sire_id/dam_id point at the parents — exactly the
    lineage facts the inbreeding fence must read.
    """
    # Parents must clear the species' breeding-age floor AT the backdated
    # service date (today-560): 1000-day-old animals are ~14.6 months then.
    doe = await _adult(
        client,
        headers,
        f"{tag_stem}-DOE",
        sex="F",
        bucket="FOUNDATION",
        age_days=1_000,
        weight_kg=28.0,
    )
    buck = await _adult(
        client,
        headers,
        f"{tag_stem}-BUCK",
        sex="M",
        bucket="BREEDING",
        age_days=1_000,
        weight_kg=34.0,
    )
    breeding_date = today() - timedelta(days=GOAT_GESTATION + 410)
    bred = (await _breed(client, headers, doe["id"], breeding_date, buck_id=buck["id"])).json()
    confirmed = await _confirm_pregnant(client, headers, bred, 35)
    kidding_date = date.fromisoformat(confirmed["breeding_date"]) + timedelta(days=GOAT_GESTATION)
    kidded = await _kidding(
        client,
        headers,
        confirmed,
        kidding_date,
        [
            {"sex": "F", "status": "ALIVE", "birth_weight": 3.0},
            {"sex": "M", "status": "ALIVE", "birth_weight": 3.2},
        ],
    )
    assert kidded.status_code == 201, kidded.text

    # Dam leaves the herd → kids early-wean into MALE_KIDS/FEMALE_KIDS.
    dead = await client.post(
        f"/api/animals/{doe['id']}/status",
        json={
            "new_status": "DEAD",
            "date": iso(kidding_date + timedelta(days=5)),
            "mortality_cause": "Fixture clearing",
        },
        headers=headers,
    )
    assert dead.status_code in (200, 201), dead.text

    animals = (await client.get("/api/animals?limit=100", headers=headers)).json()
    by_tag = {a["tag_number"]: a for a in animals["animals"]}
    daughter = next(a for a in by_tag.values() if a["sex"] == "F" and a["source"] == "BORN")
    son = next(a for a in by_tag.values() if a["sex"] == "M" and a["source"] == "BORN")
    for kid, weight in ((daughter, 26.0), (son, 30.0)):
        resp = await client.post(
            f"/api/animals/{kid['id']}/weight",
            json={"date": iso(today() - timedelta(days=30)), "weight_kg": weight},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
    # The son must sit in BREEDING to be a serviceable sire.
    if son["current_bucket"] != "BREEDING":
        moved = await client.post(
            f"/api/animals/{son['id']}/move",
            json={"to_bucket": "BREEDING", "reason": "Grown herd sire"},
            headers=headers,
        )
        assert moved.status_code in (200, 201), moved.text
    return buck, daughter, son, kidded.json()


async def _finance_rows(client: httpx.AsyncClient, headers: dict) -> list[dict]:
    resp = await client.get("/api/finance?limit=200", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["transactions"]


# ---------------------------------------------------------------------------
# RED-H1 — inbreeding fence (buck ↔ daughter, full siblings)
# ---------------------------------------------------------------------------


async def test_red_h1_sire_cannot_be_bred_to_his_daughter(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client, email="red-h1@farm.in", farm_name="Lineage Farm")
    sire, daughter, _son, _record = await _goat_doe_buck_and_kids(client, headers, tag_stem="H1")

    resp = await _breed(
        client, headers, daughter["id"], today() - timedelta(days=5), buck_id=sire["id"]
    )
    assert resp.status_code == 409, resp.text
    assert "close kin" in resp.json()["detail"]


async def test_red_h1_full_siblings_rejected(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client, email="red-h1b@farm.in", farm_name="Lineage Farm B")
    _sire, daughter, son, _record = await _goat_doe_buck_and_kids(client, headers, tag_stem="H1B")

    resp = await _breed(
        client, headers, daughter["id"], today() - timedelta(days=5), buck_id=son["id"]
    )
    assert resp.status_code == 409, resp.text
    assert "close kin" in resp.json()["detail"]


async def test_red_h1_unrelated_sire_still_accepted(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client, email="red-h1c@farm.in", farm_name="Lineage Farm C")
    _sire, daughter, _son, _record = await _goat_doe_buck_and_kids(client, headers, tag_stem="H1C")
    unrelated = await _adult(
        client, headers, "H1C-OUTSIDER", sex="M", bucket="BREEDING", weight_kg=34.0
    )

    resp = await _breed(
        client, headers, daughter["id"], today() - timedelta(days=5), buck_id=unrelated["id"]
    )
    assert resp.status_code == 201, resp.text


# ---------------------------------------------------------------------------
# RED-M2 — species gate: AI claims on a goat farm
# ---------------------------------------------------------------------------


async def test_red_m2_goat_farm_rejects_ai_service(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client, email="red-m2@farm.in", farm_name="Species Gate")
    doe = await _adult(client, headers, "M2-DOE", sex="F", bucket="FOUNDATION", weight_kg=28.0)

    resp = await _breed(client, headers, doe["id"], today() - timedelta(days=5), method="AI_SEXED")
    assert resp.status_code == 409, resp.text
    assert "goat protocol" in resp.json()["detail"]

# RED-H3 — off-ledger sales/purchases
# ---------------------------------------------------------------------------


async def test_red_h3_sold_without_price_books_flagged_zero_row(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client, email="red-h3@farm.in", farm_name="Ledger Farm")
    animal = await _adult(client, headers, "H3-GOAT", sex="M", bucket="FOUNDATION")

    resp = await client.post(
        f"/api/animals/{animal['id']}/status",
        json={"new_status": "SOLD"},
        headers=headers,
    )
    assert resp.status_code in (200, 201), resp.text

    rows = await _finance_rows(client, headers)
    sale_rows = [t for t in rows if t["category"] == "ANIMAL_SALE"]
    assert len(sale_rows) == 1
    assert float(sale_rows[0]["amount"]) == 0.0
    assert "no price recorded" in sale_rows[0]["notes"]

    # Negative control: an explicit price still books the real amount.
    priced = await _adult(client, headers, "H3-GOAT-2", sex="M", bucket="FOUNDATION")
    resp = await client.post(
        f"/api/animals/{priced['id']}/status",
        json={"new_status": "SOLD", "sale_price": 4500.0},
        headers=headers,
    )
    assert resp.status_code in (200, 201), resp.text
    rows = await _finance_rows(client, headers)
    priced_rows = [t for t in rows if t["category"] == "ANIMAL_SALE" and t["amount"] == 4500.0]
    assert len(priced_rows) == 1


async def test_red_h3_purchase_without_total_books_flagged_zero_row(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client, email="red-h3b@farm.in", farm_name="Ledger Farm B")
    resp = await client.post(
        "/api/purchases/new",
        json={"date": iso(today()), "count": 2, "create_animals": True},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text

    rows = await _finance_rows(client, headers)
    purchase_rows = [t for t in rows if t["category"] == "ANIMAL_PURCHASE"]
    assert len(purchase_rows) == 1
    assert float(purchase_rows[0]["amount"]) == 0.0
    assert "no price recorded" in purchase_rows[0]["notes"]


# ---------------------------------------------------------------------------
# RED-M3 — Idempotency-Key required on keyless money/stock mutations
# ---------------------------------------------------------------------------


async def test_red_m3_finance_new_without_key_rejected(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(conftest, "IDEMPOTENCY_REQUIRED_PATHS", set())
    headers = await owner_with_farm(client, email="red-m3@farm.in", farm_name="Key Farm")
    resp = await client.post(
        "/api/finance/new",
        json={"type": "EXPENSE", "category": "OTHER", "date": iso(today()), "amount": 100.0},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text
    assert "Idempotency-Key header is required" in resp.json()["detail"]


async def test_red_m3_dispense_without_key_rejected(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(conftest, "IDEMPOTENCY_REQUIRED_PATHS", set())
    headers = await owner_with_farm(client, email="red-m3b@farm.in", farm_name="Key Farm B")
    resp = await client.post(
        "/api/feeding/dispense",
        json={
            "bucket": "BREEDING",
            "shift": "MORNING",
            "recipe_code": "Dry jowar stover",
            "qty_kg": 5.0,
        },
        headers=headers,
    )
    assert resp.status_code == 422, resp.text
    assert "Idempotency-Key header is required" in resp.json()["detail"]


async def test_red_m3_key_replay_books_exactly_one_row(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client, email="red-m3c@farm.in", farm_name="Key Farm C")
    payload = {
        "type": "EXPENSE",
        "category": "OTHER",
        "date": iso(today()),
        "amount": 100.0,
    }
    key = {"Idempotency-Key": "red-m3-replay-1"}
    first = await client.post("/api/finance/new", json=payload, headers={**headers, **key})
    assert first.status_code == 201, first.text
    replay = await client.post("/api/finance/new", json=payload, headers={**headers, **key})
    assert replay.status_code == 201, replay.text
    assert replay.headers.get("Idempotency-Replayed") == "true"

    rows = [
        t
        for t in await _finance_rows(client, headers)
        if t["category"] == "OTHER" and t["amount"] == 100.0
    ]
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# RED-M4 — breeding/kidding replay returns the committed response, not a 409
# ---------------------------------------------------------------------------


async def test_red_m4_breeding_replay_returns_committed_record(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client, email="red-m4@farm.in", farm_name="Replay Farm")
    doe = await _adult(client, headers, "M4-DOE", sex="F", bucket="FOUNDATION", weight_kg=28.0)
    buck = await _adult(client, headers, "M4-BUCK", sex="M", bucket="BREEDING", weight_kg=34.0)

    first = await _breed(
        client,
        headers,
        doe["id"],
        today() - timedelta(days=5),
        buck_id=buck["id"],
        idempotency_key="red-m4-breeding-1",
    )
    assert first.status_code == 201, first.text
    replay = await _breed(
        client,
        headers,
        doe["id"],
        today() - timedelta(days=5),
        buck_id=buck["id"],
        idempotency_key="red-m4-breeding-1",
    )
    assert replay.status_code == 201, replay.text
    assert replay.headers.get("Idempotency-Replayed") == "true"
    assert replay.json()["id"] == first.json()["id"]

    listing = (await client.get("/api/breeding?limit=100", headers=headers)).json()
    assert listing["total"] == 1
    assert len(listing["records"]) == 1


async def test_red_m4_kidding_replay_returns_committed_record(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client, email="red-m4b@farm.in", farm_name="Replay Farm B")
    doe = await _adult(client, headers, "M4B-DOE", sex="F", bucket="FOUNDATION", weight_kg=28.0)
    buck = await _adult(client, headers, "M4B-BUCK", sex="M", bucket="BREEDING", weight_kg=34.0)
    breeding_date = today() - timedelta(days=GOAT_GESTATION + 5)
    bred = (await _breed(client, headers, doe["id"], breeding_date, buck_id=buck["id"])).json()
    confirmed = await _confirm_pregnant(client, headers, bred, 35)
    kidding_date = date.fromisoformat(confirmed["breeding_date"]) + timedelta(days=GOAT_GESTATION)
    kids = [{"sex": "F", "status": "ALIVE", "birth_weight": 3.0}]

    first = await _kidding(
        client,
        headers,
        confirmed,
        kidding_date,
        kids,
        idempotency_key="red-m4-kidding-1",
    )
    assert first.status_code == 201, first.text
    replay = await _kidding(
        client,
        headers,
        confirmed,
        kidding_date,
        kids,
        idempotency_key="red-m4-kidding-1",
    )
    assert replay.status_code == 201, replay.text
    assert replay.headers.get("Idempotency-Replayed") == "true"
    assert replay.json()["id"] == first.json()["id"]

    history = (await client.get("/api/kidding?limit=100", headers=headers)).json()
    assert len(history["records"]) == 1


# ---------------------------------------------------------------------------
# RED-M5 — species-banded weights
# ---------------------------------------------------------------------------


async def _confirmed_goat_pregnancy(
    client: httpx.AsyncClient, headers: dict, tag_stem: str
) -> tuple[dict, dict]:
    doe = await _adult(
        client, headers, f"{tag_stem}-DOE", sex="F", bucket="FOUNDATION", weight_kg=28.0
    )
    buck = await _adult(
        client, headers, f"{tag_stem}-BUCK", sex="M", bucket="BREEDING", weight_kg=34.0
    )
    breeding_date = today() - timedelta(days=GOAT_GESTATION + 5)
    bred = (await _breed(client, headers, doe["id"], breeding_date, buck_id=buck["id"])).json()
    confirmed = await _confirm_pregnant(client, headers, bred, 35)
    return confirmed, date.fromisoformat(confirmed["breeding_date"]) + timedelta(
        days=GOAT_GESTATION
    )


async def test_red_m5_goat_birth_weight_band(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client, email="red-m5@farm.in", farm_name="Band Farm")
    confirmed, kidding_date = await _confirmed_goat_pregnancy(client, headers, "M5")

    absurd = await _kidding(
        client,
        headers,
        confirmed,
        kidding_date,
        [{"sex": "F", "status": "ALIVE", "birth_weight": 950.0}],
    )
    assert absurd.status_code == 422, absurd.text
    assert "not a credible newborn weight" in absurd.json()["detail"]

    plausible = await _kidding(
        client,
        headers,
        confirmed,
        kidding_date,
        [{"sex": "F", "status": "ALIVE", "birth_weight": 3.0}],
    )
    assert plausible.status_code == 201, plausible.text


async def test_red_m5_adult_weight_cap_is_species_scaled(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client, email="red-m5b@farm.in", farm_name="Band Farm B")
    animal = await _adult(client, headers, "M5B-GOAT", sex="F", bucket="FOUNDATION")

    absurd = await client.post(
        f"/api/animals/{animal['id']}/weight",
        json={"date": iso(today()), "weight_kg": 500.0},
        headers=headers,
    )
    assert absurd.status_code == 422, absurd.text
    assert "credible adult scale" in absurd.json()["detail"]

    normal = await client.post(
        f"/api/animals/{animal['id']}/weight",
        json={"date": iso(today()), "weight_kg": 42.0},
        headers=headers,
    )
    assert normal.status_code == 201, normal.text


async def test_red_m5_animal_create_weight_band(client: httpx.AsyncClient) -> None:
    """Independent-verifier regression: the create form is the fourth weight
    entry point and must respect the same species bands — a 950-kg entry
    weight or birth weight here coalesces into "latest weight" downstream."""
    headers = await owner_with_farm(client, email="red-m5d@farm.in", farm_name="Band Farm D")

    huge_entry = await client.post(
        "/api/animals",
        json={
            "tag_number": "M5D-HEAVY",
            "sex": "F",
            "source": "PURCHASED",
            "current_bucket": "FOUNDATION",
            "historical_import_reason": "fixture",
            "weight_kg": 950.0,
        },
        headers=headers,
    )
    assert huge_entry.status_code == 422, huge_entry.text
    assert "credible adult scale" in huge_entry.json()["detail"]

    huge_birth = await client.post(
        "/api/animals",
        json={
            "tag_number": "M5D-BORN",
            "sex": "F",
            "source": "BORN",
            "current_bucket": "FOUNDATION",
            "historical_import_reason": "fixture",
            "birth_weight": 950.0,
        },
        headers=headers,
    )
    assert huge_birth.status_code == 422, huge_birth.text
    assert "not a credible newborn weight" in huge_birth.json()["detail"]


async def test_red_m5_purchase_avg_weight_cap_is_species_scaled(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client, email="red-m5c@farm.in", farm_name="Band Farm C")
    resp = await client.post(
        "/api/purchases/new",
        json={"date": iso(today()), "count": 2, "avg_weight_kg": 500.0},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text
    assert "credible adult scale" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# RED-M6 — owner cull-rule override is attributed in the service log
# ---------------------------------------------------------------------------


async def test_red_m6_owner_cull_override_is_logged(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    headers = await owner_with_farm(client, email="red-m6@farm.in", farm_name="Cull Farm")
    doe = await _adult(client, headers, "M6-DOE", sex="F", bucket="FOUNDATION", weight_kg=28.0)
    buck = await _adult(client, headers, "M6-BUCK", sex="M", bucket="BREEDING", weight_kg=34.0)

    # Two consecutive failed cycles flag the doe (goat limit: 2).
    for _cycle, offset in enumerate((-120, -60), start=1):
        bred = (
            await _breed(
                client, headers, doe["id"], today() + timedelta(days=offset), buck_id=buck["id"]
            )
        ).json()
        scan = await client.post(
            f"/api/breeding/{bred['id']}/ultrasound",
            json={"pregnant": False, "kid_count": None, "date": bred["ultrasound_date"]},
            headers=headers,
        )
        assert scan.status_code == 200, scan.text
    flagged = (await client.get(f"/api/animals/{doe['id']}", headers=headers)).json()["animal"]
    assert flagged["cull_candidate"] is True

    with caplog.at_level(logging.WARNING, logger="app.services.breeding"):
        override = await _breed(
            client, headers, doe["id"], today() - timedelta(days=5), buck_id=buck["id"]
        )
    assert override.status_code == 201, override.text
    assert any("Cull-rule override" in record.getMessage() for record in caplog.records)


# ---------------------------------------------------------------------------
# RED-L4 — idempotency open-record quota
# ---------------------------------------------------------------------------


async def test_red_l4_open_record_quota_blocks_new_claims(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "idempotency_max_open_records_per_actor", 1)
    headers = await owner_with_farm(client, email="red-l4@farm.in", farm_name="Quota Farm")

    first = await client.post(
        "/api/finance/new",
        json={"type": "EXPENSE", "category": "OTHER", "date": iso(today()), "amount": 10.0},
        headers={**headers, "Idempotency-Key": "red-l4-a"},
    )
    assert first.status_code == 201, first.text

    second = await client.post(
        "/api/finance/new",
        json={"type": "EXPENSE", "category": "OTHER", "date": iso(today()), "amount": 20.0},
        headers={**headers, "Idempotency-Key": "red-l4-b"},
    )
    assert second.status_code == 429, second.text
    assert "retention window" in second.json()["detail"]

    # Independent-verifier regression: at the cap, a same-key REPLAY must
    # still answer — it is read-only and is the client's only way to retrieve
    # a committed response it never saw.
    replay = await client.post(
        "/api/finance/new",
        json={"type": "EXPENSE", "category": "OTHER", "date": iso(today()), "amount": 10.0},
        headers={**headers, "Idempotency-Key": "red-l4-a"},
    )
    assert replay.status_code == 201, replay.text
    assert replay.headers.get("Idempotency-Replayed") == "true"


# ---------------------------------------------------------------------------
# RED-L5 — task due-date sanity band
# ---------------------------------------------------------------------------


async def test_red_l5_task_due_date_band(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client, email="red-l5@farm.in", farm_name="Duty Farm")
    for bad in ("0001-01-01", "2101-01-01"):
        resp = await client.post(
            "/api/tasks",
            json={"title": "Backdated fiction", "due_date": bad},
            headers=headers,
        )
        assert resp.status_code == 422, (bad, resp.text)

    fine = await client.post(
        "/api/tasks",
        json={"title": "Real duty", "due_date": iso(today() + timedelta(days=7))},
        headers=headers,
    )
    assert fine.status_code == 201, fine.text


# ---------------------------------------------------------------------------
# RED-L14/L15 — compose edge guard + no known-secret fallback (pinning tests)
# ---------------------------------------------------------------------------


def test_red_l14_edge_warns_when_publicly_bound_in_development() -> None:
    compose = (REPO_ROOT / "docker-compose.yml").read_text()
    assert "WARNING: the edge is bound to" in compose
    assert "127.0.0.1|localhost|::1) ;;" in compose
    # Independent-verifier regression: the guard reads the in-container
    # environment, so compose must pass the bind host through — otherwise the
    # warning can never fire (the guard would always see the loopback default).
    assert "GOATFARM_EDGE_BIND_HOST: ${GOATFARM_EDGE_BIND_HOST:-127.0.0.1}" in compose


def test_red_l15_compose_has_no_known_hmac_secret_fallback() -> None:
    compose = (REPO_ROOT / "docker-compose.yml").read_text()
    # The documented dev value may appear only in .env.example, never as a
    # compose-level fallback default.
    assert not re.search(r"GOATFARM_IDEMPOTENCY_REQUEST_HMAC_SECRET:\s*\$\{[^}]*:-", compose)
    assert "development-only-idempotency-hmac-secret-change-me" not in compose
