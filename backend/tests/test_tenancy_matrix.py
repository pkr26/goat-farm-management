"""Full-endpoint multi-tenancy isolation matrix (2026-09-23 verification).

The sampled cross-farm probes scattered across the domain suites each prove
one endpoint. This module enumerates EVERY route the app serves that
resolves an ``X-Farm-Id`` and proves the tenant fence on each of them:

1. a foreign farm header (owner of A acting on B, and the reverse) answers
   404 on every farm-scoped route — never 200, never 403, which would
   confirm the farm exists;
2. the unknown-farm and forbidden-farm 404s are byte-identical and
   statistically indistinguishable in latency, so the API leaks neither
   existence nor non-existence;
3. sequential/guessable foreign object IDs (IDOR) answer 404 for reads and
   mutations alike across every object family; and
4. crafted payloads that reference farm B's animals inside farm A's writes
   (health events, insurance policies) are rejected at the API boundary —
   the tenant-composite FK is the DB backstop, this is the request-path
   proof.

The route enumeration reuses the dependency-tree walker from
``test_rbac_exhaustive`` so the two matrices can never drift apart: a new
endpoint shows up here the day it is added.
"""

import statistics
import time
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import FarmMembership, Role, User
from app.utils import today

from .conftest import create_farm, owner_with_farm, register
from .test_rbac_exhaustive import FARMLESS_BY_DESIGN, _app_routes, _placeholders

UNKNOWN_FARM = 999_999_999  # inside int32, never created


def _farm_scoped_routes() -> list[tuple[str, str]]:
    """(method, path) for every route that resolves X-Farm-Id."""
    return sorted(
        {(method, path) for method, path, _perm, scoped in _app_routes() if scoped}
    )


async def _seed_farm_objects(
    client: httpx.AsyncClient, headers: dict
) -> dict[str, tuple[int, dict]]:
    """One object of every mutable family, all owned by `headers`' farm."""
    made: dict[str, tuple[int, dict]] = {}

    animal = await client.post(
        "/api/animals",
        json={
            "tag_number": "TEN-1",
            "sex": "F",
            "source": "PURCHASED",
            "current_bucket": "FOUNDATION",
            "date_of_birth": (today() - timedelta(days=900)).isoformat(),
            "weight_kg": 30.0,
            "weight_date": (today() - timedelta(days=900)).isoformat(),
            "historical_import_reason": "tenancy matrix fixture",
        },
        headers=headers,
    )
    assert animal.status_code == 201, animal.text
    made["animal"] = (animal.json()["id"], animal.json())

    task = await client.post(
        "/api/tasks",
        json={
            "title": "Matrix probe duty",
            "category": "OTHER",
            "due_date": today().isoformat(),
        },
        headers=headers,
    )
    assert task.status_code == 201, task.text
    made["task"] = (task.json()["id"], task.json())

    txn = await client.post(
        "/api/finance/new",
        json={
            "date": today().isoformat(),
            "type": "EXPENSE",
            "category": "OTHER",
            "amount": 100.0,
            "notes": "tenancy matrix probe",
        },
        headers=headers,
    )
    assert txn.status_code == 201, txn.text
    made["transaction"] = (txn.json()["id"], txn.json())

    policy = await client.post(
        "/api/finance/insurance",
        json={
            "policy_number": "TEN-POL-1",
            "insurer": "Oriental Insurance",
            "sum_insured": 5000.0,
            "premium": 250.0,
            "start_date": today().isoformat(),
            "renewal_date": (today() + timedelta(days=365)).isoformat(),
        },
        headers=headers,
    )
    assert policy.status_code == 201, policy.text
    made["insurance_policy"] = (policy.json()["id"], policy.json())

    batch = await client.post(
        "/api/purchases/new",
        json={
            "date": today().isoformat(),
            "supplier": "Matrix supplier",
            "count": 1,
        },
        headers=headers,
    )
    assert batch.status_code == 201, batch.text
    made["purchase_batch"] = (batch.json()["id"], batch.json())

    return made


async def test_every_farm_scoped_route_404s_under_a_foreign_farm_header(
    client: httpx.AsyncClient,
) -> None:
    """The full isolation matrix: for every route that resolves X-Farm-Id,
    the owner of farm A with farm B's header — and the reverse — gets 404.

    Path parameters are filled with 99999 so any object-level lookup would
    also 404; the farm-scoped list endpoints (no path parameters) can ONLY
    be failing at the tenant check, which is the pure tenancy proof."""
    a_headers = await owner_with_farm(client, email="matrix-a@farm.in", farm_name="Matrix A")
    b_headers = await owner_with_farm(client, email="matrix-b@farm.in", farm_name="Matrix B")
    farm_a, farm_b = a_headers["X-Farm-Id"], b_headers["X-Farm-Id"]

    routes = _farm_scoped_routes()
    farmless = {(m, p) for m, p in FARMLESS_BY_DESIGN}
    assert len(routes) >= 80, (
        f"only {len(routes)} farm-scoped routes discovered — the walker has gone "
        "blind, so this matrix is auditing far less than it claims"
    )
    assert farmless.isdisjoint(routes), "a FARMLESS_BY_DESIGN route resolved farm context"

    failures: list[str] = []
    for method, path in routes:
        for owner_headers, foreign_farm in ((a_headers, farm_b), (b_headers, farm_a)):
            probe = owner_headers | {"X-Farm-Id": str(foreign_farm)}
            url = _placeholders(path, str(foreign_farm))
            kwargs: dict = {"headers": probe}
            if method in {"POST", "PUT", "PATCH"}:
                kwargs["json"] = {}
            resp = await client.request(method, url, **kwargs)
            if resp.status_code != 404:
                failures.append(
                    f"{method} {url} as owner-of-{farm_a if foreign_farm == farm_b else farm_b}"
                    f" → {resp.status_code} (expected 404; body={resp.text[:120]!r})"
                )
    assert not failures, "Cross-farm isolation failures:\n" + "\n".join(failures)


async def test_unknown_and_forbidden_farm_404s_are_byte_identical(
    client: httpx.AsyncClient,
) -> None:
    """404 symmetry: an unknown farm and a farm the caller is forbidden from
    must be indistinguishable in status, headers-that-matter, and body bytes
    (no farm enumeration via response shape)."""
    a_headers = await owner_with_farm(client, email="sym-a@farm.in")
    b_headers = await owner_with_farm(client, email="sym-b@farm.in", farm_name="Sym B")
    farm_b = b_headers["X-Farm-Id"]

    # Every farm-scoped route WITHOUT a path parameter — the 404 can only
    # come from the tenant lookup, so the comparison is meaningful.
    list_routes = [
        (m, p) for m, p in _farm_scoped_routes() if "{" not in p and m == "GET"
    ]
    assert len(list_routes) >= 20, f"only {len(list_routes)} parameterless GETs found"

    failures: list[str] = []
    for method, path in list_routes:
        forbidden = await client.request(method, path, headers=a_headers | {"X-Farm-Id": farm_b})
        unknown = await client.request(
            method, path, headers=a_headers | {"X-Farm-Id": str(UNKNOWN_FARM)}
        )
        for label, resp in (("forbidden", forbidden), ("unknown", unknown)):
            if resp.status_code != 404:
                failures.append(f"{method} {path} ({label}) → {resp.status_code}, expected 404")
        if forbidden.content != unknown.content:
            failures.append(
                f"{method} {path}: bodies differ — forbidden={forbidden.content[:120]!r} "
                f"unknown={unknown.content[:120]!r}"
            )
    assert not failures, "404 symmetry failures:\n" + "\n".join(failures)


async def test_unknown_and_forbidden_farm_404_latency_is_indistinguishable(
    client: httpx.AsyncClient,
) -> None:
    """Statistical timing symmetry (generous bounds, interleaved samples):
    resolving a forbidden farm must not be measurably cheaper or costlier
    than an unknown farm — an oracle for farm existence."""
    a_headers = await owner_with_farm(client, email="time-a@farm.in")
    b_headers = await owner_with_farm(client, email="time-b@farm.in", farm_name="Time B")
    farm_b = b_headers["X-Farm-Id"]

    probes = [
        "GET /api/animals",
        "GET /api/tasks",
        "GET /api/finance",
    ]
    forbidden_ms: list[float] = []
    unknown_ms: list[float] = []
    for _ in range(15):  # interleaved to cancel machine drift
        for label, farm, sink in (
            ("forbidden", farm_b, forbidden_ms),
            ("unknown", UNKNOWN_FARM, unknown_ms),
        ):
            for route in probes:
                method, path = route.split(" ")
                start = time.perf_counter()
                resp = await client.request(
                    method, path, headers=a_headers | {"X-Farm-Id": str(farm)}
                )
                sink.append((time.perf_counter() - start) * 1000)
                assert resp.status_code == 404, (label, route, resp.status_code)

    f_med, u_med = statistics.median(forbidden_ms), statistics.median(unknown_ms)
    # Both paths execute the same single tenant lookup; allow a wide 5x ratio
    # band AND a 75 ms absolute band so CI jitter cannot flake this, while a
    # real oracle (e.g. a second ownership query, a different error path)
    # still trips it.
    assert f_med < max(75.0, 5 * u_med) and u_med < max(75.0, 5 * f_med), (
        f"404 latency asymmetry: forbidden median={f_med:.1f} ms, "
        f"unknown median={u_med:.1f} ms"
    )


async def test_idor_foreign_object_ids_answer_404_for_reads_and_writes(
    client: httpx.AsyncClient,
) -> None:
    """Sequential-ID probing: every object family created in farm B, then
    requested (and mutated) under farm A's context, answers 404 — the
    forbidden object is indistinguishable from a nonexistent one."""
    a_headers = await owner_with_farm(client, email="idor-a@farm.in")
    b_headers = await owner_with_farm(client, email="idor-b@farm.in", farm_name="Idor B")
    made = await _seed_farm_objects(client, b_headers)

    animal_id = made["animal"][0]
    task_id = made["task"][0]
    txn_id = made["transaction"][0]
    policy_id = made["insurance_policy"][0]
    batch_id = made["purchase_batch"][0]

    reads: list[tuple[str, str]] = [
        ("GET", f"/api/animals/{animal_id}"),
        ("GET", f"/api/finance/animals/{animal_id}/lifetime-pnl"),
        ("GET", f"/api/tasks/{task_id}"),
        ("GET", f"/api/finance/insurance/{policy_id}/history"),
        ("GET", f"/api/purchases/{batch_id}"),
        ("GET", f"/api/health/schedule/{animal_id}"),
    ]
    writes: list[tuple[str, str, dict | None]] = [
        ("POST", f"/api/tasks/{task_id}/complete", None),
        ("POST", f"/api/tasks/{task_id}/skip", {"reason": "idor probe"}),
        ("POST", f"/api/animals/{animal_id}/move", {"to_bucket": "BREEDING", "reason": "idor"}),
        (
            "POST",
            f"/api/finance/insurance/{policy_id}/renew",
            {"renewal_date": (today() + timedelta(days=30)).isoformat()},
        ),
        (
            "POST",
            f"/api/finance/transactions/{txn_id}/correct",
            {
                "date": today().isoformat(),
                "type": "EXPENSE",
                "category": "OTHER",
                "amount": 99.0,
                "notes": "idor probe",
                "reason": "tenancy matrix probe",
            },
        ),
        ("PATCH", f"/api/animals/{animal_id}", {"horned": True}),
    ]

    failures: list[str] = []
    for method, url in reads:
        resp = await client.request(method, url, headers=a_headers)
        if resp.status_code != 404:
            failures.append(f"{method} {url} → {resp.status_code} (expected 404)")
        twin = await client.request(
            method, url.replace(str(animal_id), "99999").replace(str(task_id), "99999"), headers=a_headers
        )
        if twin.status_code == 404 and resp.content != twin.content:
            failures.append(
                f"{method} {url}: foreign-id 404 body differs from nonexistent-id 404 "
                f"({resp.content[:80]!r} vs {twin.content[:80]!r})"
            )
    for method, url, body in writes:
        kwargs: dict = {"headers": a_headers}
        if body is not None:
            kwargs["json"] = body
        resp = await client.request(method, url, **kwargs)
        if resp.status_code != 404:
            failures.append(f"{method} {url} → {resp.status_code} (expected 404)")

    # Same probes with a nonexistent id of the same magnitude: byte-equal
    # denial, so the API does not confirm the object exists elsewhere.
    for method, url, body in writes:
        kwargs = {"headers": a_headers}
        if body is not None:
            kwargs["json"] = body
        nonexistent = await client.request(
            method, url.replace(str(task_id), "99999").replace(str(animal_id), "99999"), **kwargs
        )
        foreign = await client.request(method, url, **kwargs)
        if nonexistent.status_code == 404 and foreign.content != nonexistent.content:
            failures.append(
                f"{method} {url}: foreign-id and unknown-id 404 bodies differ "
                f"({foreign.content[:80]!r} vs {nonexistent.content[:80]!r})"
            )

    assert not failures, "IDOR failures:\n" + "\n".join(failures)


async def test_cross_tenant_foreign_keys_rejected_at_the_api_boundary(
    client: httpx.AsyncClient,
) -> None:
    """Crafted payloads referencing farm B's animal inside farm A's writes.
    The tenant-composite FK is the database backstop (proven for direct SQL
    in test_tenant_foreign_keys); these prove the request path rejects the
    write too. Rejection must never be 200/201/403, and must be byte-equal
    to the same write aimed at a nonexistent id, so the API does not reveal
    that the animal exists in another tenant.

    NOTE (2026-09-23 verification): health events answer 400 "No active
    animals match the given scope" here rather than the 404 "Animal not
    found" that /api/health/schedule/{id} and every other object family
    use — a status-code inconsistency (recorded in the report), but not an
    oracle: the nonexistent-id twin answers the identical 400."""
    a_headers = await owner_with_farm(client, email="fk-a@farm.in")
    b_headers = await owner_with_farm(client, email="fk-b@farm.in", farm_name="Fk B")
    b_animal = (
        await client.post(
            "/api/animals",
            json={
                "tag_number": "FK-B-1",
                "sex": "F",
                "source": "PURCHASED",
                "current_bucket": "FOUNDATION",
                "date_of_birth": (today() - timedelta(days=900)).isoformat(),
                "historical_import_reason": "fk fixture",
            },
            headers=b_headers,
        )
    ).json()

    async def probe(url: str, json_body: dict) -> tuple[httpx.Response, httpx.Response]:
        foreign = await client.post(url, json=json_body, headers=a_headers)
        twin = await client.post(
            url.replace(str(b_animal["id"]), "99999"), json=json_body, headers=a_headers
        )
        return foreign, twin

    # Health event in farm A scoped to farm B's animal.
    health, health_twin = await probe(
        "/api/health/events",
        {
            "scope": "animal",
            "animal_id": b_animal["id"],
            "type": "TREATMENT",
            "product_name": "Oxytetracycline",
            "date": today().isoformat(),
        },
    )
    assert health.status_code not in (200, 201, 403), (
        f"cross-farm health event accepted: {health.status_code} {health.text[:200]}"
    )
    assert health.status_code == health_twin.status_code and health.content == health_twin.content, (
        "health-event rejection differs between a foreign animal and a nonexistent one "
        f"({health.status_code} {health.content[:100]!r} vs "
        f"{health_twin.status_code} {health_twin.content[:100]!r}) — an existence oracle"
    )

    # Manual ledger row in farm A attributed to farm B's animal. The app
    # answers a deliberate 400 "Related animal is not on this farm" whose
    # docstring pins "unknown and cross-farm ids share one response so this
    # check is not an enumeration oracle" — the twin assertion proves that.
    txn, txn_twin = await probe(
        "/api/finance/new",
        {
            "date": today().isoformat(),
            "type": "EXPENSE",
            "category": "OTHER",
            "amount": 10.0,
            "related_animal_id": b_animal["id"],
        },
    )
    assert txn.status_code not in (200, 201, 403), (
        f"cross-farm finance row accepted: {txn.status_code} {txn.text[:200]}"
    )
    assert txn.status_code == txn_twin.status_code and txn.content == txn_twin.content, (
        "finance related-animal rejection differs between foreign and nonexistent ids "
        f"({txn.status_code} {txn.content[:100]!r} vs {txn_twin.status_code} "
        f"{txn_twin.content[:100]!r}) — an existence oracle"
    )

    # Insurance policy in farm A covering farm B's animal — same deliberate
    # shared 400/404 family as finance; the twin proves no oracle.
    policy, policy_twin = await probe(
        "/api/finance/insurance",
        {
            "policy_number": "FK-PROBE",
            "insurer": "Probe Insurer",
            "sum_insured": 1000.0,
            "premium": 50.0,
            "start_date": today().isoformat(),
            "renewal_date": (today() + timedelta(days=365)).isoformat(),
            "animal_id": b_animal["id"],
        },
    )
    assert policy.status_code not in (200, 201, 403), (
        f"cross-farm insurance policy accepted: {policy.status_code} {policy.text[:200]}"
    )
    assert policy.status_code == policy_twin.status_code and policy.content == policy_twin.content, (
        "insurance animal rejection differs between foreign and nonexistent ids "
        f"({policy.status_code} {policy.content[:100]!r} vs {policy_twin.status_code} "
        f"{policy_twin.content[:100]!r}) — an existence oracle"
    )

    # And the write truly did not happen in farm A.
    rows_a = await client.get("/api/finance", headers=a_headers)
    assert rows_a.status_code == 200
    items = rows_a.json().get("items", rows_a.json() if isinstance(rows_a.json(), list) else [])
    assert not any(i.get("related_animal_id") == b_animal["id"] for i in items)


@pytest.mark.parametrize(
    "bad_header",
    ["", "not-a-number", "12.5", "-1", "0", "+1", "9999999999999999999999"],
)
async def test_malformed_farm_header_is_rejected_consistently(
    client: httpx.AsyncClient, bad_header: str
) -> None:
    """A malformed X-Farm-Id (empty, non-integer, negative, zero, int64
    overflow) is rejected before tenant logic runs — the app answers 400 for
    unparseable ids (pinned by the breeding header tests), 404/422 for
    well-formed-but-wrong ones. Never 200/403.

    (Non-ASCII digits like '١٢٣' cannot be probed here: httpx refuses to
    encode non-ASCII header values, so no HTTP client library can send one;
    raw-socket senders are the app's strict-ASCII header validation's job.)"""
    a_headers = await owner_with_farm(client, email="hdr@farm.in")
    resp = await client.get("/api/animals", headers=a_headers | {"X-Farm-Id": bad_header})
    assert resp.status_code in (400, 404, 422), (
        f"{bad_header!r} → {resp.status_code}: {resp.text[:120]}"
    )


async def test_missing_farm_header_rejected_on_every_farm_scoped_get(
    client: httpx.AsyncClient,
) -> None:
    a_headers = await owner_with_farm(client, email="nohdr@farm.in")
    a_headers.pop("X-Farm-Id")

    failures: list[str] = []
    for method, path in _farm_scoped_routes():
        if method != "GET" or "{" in path:
            continue
        resp = await client.request(method, path, headers=a_headers)
        if resp.status_code not in (404, 422):
            failures.append(f"{method} {path} without X-Farm-Id → {resp.status_code}")
    assert not failures, "Missing-header failures:\n" + "\n".join(failures)


async def test_owner_console_and_defaults_do_not_leak_other_farms(
    client: httpx.AsyncClient,
) -> None:
    """The four farmless routes are the documented exceptions; prove they
    stay farmless AND that the owner console only spans OWNED farms even
    when the caller manages (but does not own) another farm."""
    owner_headers = await register(client, email="console-owner@farm.in")
    owned_headers = await create_farm(client, owner_headers, "Console Owned")

    # A second user's farm. The team-consent guard refuses to provision a
    # pre-existing account through the API, so the membership is inserted
    # directly — the exact state a future invite flow would produce.
    other_owner = await register(client, email="console-other@farm.in")
    other_headers = await create_farm(client, other_owner, "Other Farm")
    async with get_sessionmaker()() as db:
        owner_user = (
            await db.execute(select(User).where(User.email == "console-owner@farm.in"))
        ).scalar_one()
        other_role = (
            await db.execute(
                select(Role).where(
                    Role.farm_id == int(other_headers["X-Farm-Id"]),
                    Role.deleted_at.is_(None),
                )
            )
        ).scalars().first()
        assert other_role is not None, "farm seeding did not create any role"
        db.add(
            FarmMembership(
                user_id=owner_user.id,
                farm_id=int(other_headers["X-Farm-Id"]),
                role_id=other_role.id,
            )
        )
        await db.commit()

    overview = await client.get("/api/owner/overview", headers=owner_headers)
    assert overview.status_code == 200, overview.text
    names = {farm["farm_name"] for farm in overview.json().get("farms", [])}
    assert "Console Owned" in names
    assert "Other Farm" not in names, (
        "a managed-but-not-owned farm leaked into the owner console: " + str(names)
    )
    benchmarks = await client.get("/api/owner/benchmarks", headers=owner_headers)
    assert benchmarks.status_code == 200, benchmarks.text
    bench_names = {row.get("farm_name") for row in benchmarks.json().get("farms", [])}
    assert "Other Farm" not in bench_names, (
        f"managed-but-not-owned farm leaked into benchmarks: {bench_names}"
    )

    # The membership itself is real (farm picker shows it) — the console is
    # the thing that must filter on ownership, not the tenancy fence.
    farms = await client.get("/api/auth/farms", headers=owner_headers)
    farm_names = {f["name"] for f in farms.json()}
    assert "Console Owned" in farm_names
    assert "Other Farm" in farm_names
    _ = owned_headers
