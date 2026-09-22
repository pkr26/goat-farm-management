"""Exhaustive RBAC coverage.

`test_rbac.py` samples 3 preset roles × 4-ish endpoints. This module inspects
the mounted FastAPI app and asserts two complementary things about EVERY route
it serves:

1. every route that declares a `require_perm(...)` dependency answers 403 to a
   worker whose role holds no permissions at all, and
2. every route that declares none is listed in `UNGUARDED_BY_DESIGN`, the
   explicit, reasoned allowlist of public/self-service endpoints.

Only (2) makes the promise this module has always made — a new endpoint
shipped without a permission guard shows up as a test failure, not as a silent
security regression hiding behind sampled coverage. (1) alone can never see an
unguarded route, because an unguarded route has nothing for it to look at.

We introspect FastAPI's resolved dependency tree rather than parsing the source.
The previous ast extractor matched only a literal `Depends(require_perm("..."))`
in a route signature, so it silently skipped the 49 routes whose guard arrives
through a module-level alias (`TEAM_PERM`, `SimView`, `FeedingManage`, …) or
through a prepared sub-dependency (`_prepare_worker_create`): it audited 16 of
65 guarded routes and still reported success. The dependency tree resolves all
of those, and `MIN_GUARDED_ROUTES` below makes a repeat collapse a failure.
"""

import inspect
import re
from collections.abc import Iterator
from functools import cache

import httpx
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from app.deps import current_farm
from app.main import create_app

from .conftest import login_and_rotate, owner_with_farm

# Every route that carries no `require_perm` dependency, with the reason it
# sits outside the farm RBAC matrix. Adding an entry must be a deliberate act:
# an unlisted unguarded route fails the suite.
UNGUARDED_BY_DESIGN: dict[tuple[str, str], str] = {
    ("GET", "/healthz"): "liveness probe — unauthenticated by design",
    ("GET", "/readyz"): "readiness probe — unauthenticated by design",
    ("GET", "/metrics"): (
        "Prometheus exposition for internal scrapers — unauthenticated by design; "
        "the compose edge routes only /api/ to the backend, so it is never public "
        "(see README observability section; GOATFARM_METRICS_ENABLED=false removes it)"
    ),
    ("POST", "/api/auth/register"): "creates the account permissions are evaluated against",
    ("POST", "/api/auth/login"): "issues the token permissions are evaluated against",
    ("POST", "/api/auth/refresh"): "rotates the caller's own session, authorized by the token",
    ("POST", "/api/auth/logout"): "revokes the caller's own session",
    ("GET", "/api/auth/me"): "the caller's own identity",
    ("GET", "/api/auth/farms"): "the caller's own farm list — the picker that fills X-Farm-Id",
    ("POST", "/api/auth/farms"): "creating a farm makes the caller its owner",
    ("GET", "/api/auth/permissions"): "the caller's own effective permission set",
    ("POST", "/api/auth/change-password"): "self-service, gated on the current password",
    ("DELETE", "/api/auth/account"): "self-service account deletion",
    ("GET", "/api/auth/account/export"): "the caller's own data export",
    ("POST", "/api/auth/totp/enroll"): (
        "self-service second-factor enrollment, gated on the current password"
    ),
    ("POST", "/api/auth/totp/confirm"): (
        "activates the caller's own pending enrollment, gated on the new secret"
    ),
    ("POST", "/api/auth/totp/disable"): (
        "removes the caller's own second factor, gated on password + code"
    ),
    ("POST", "/api/auth/totp/challenge"): (
        "exchanges the login-issued single-use mfa token, gated on the code"
    ),
    ("GET", "/api/auth/worker-roster"): (
        "tablet first screen: names of PIN-enabled workers only, hard "
        "per-IP throttled (documented first-name-leak tradeoff)"
    ),
    ("POST", "/api/auth/worker-login"): (
        "issues the token permissions are evaluated against, gated on the "
        "membership PIN with login-grade throttling"
    ),
    ("POST", "/api/auth/totp/recovery/regenerate"): (
        "re-mints the caller's own recovery codes, gated on password + code"
    ),
    ("GET", "/api/owner/overview"): (
        "cross-farm view over OWNED farms; ownership (Farm.owner_id) is the "
        "permission, evaluated per farm inside the endpoint — not farm RBAC"
    ),
    ("GET", "/api/owner/benchmarks"): (
        "cross-farm view over OWNED farms; ownership (Farm.owner_id) is the "
        "permission, evaluated per farm inside the endpoint — not farm RBAC"
    ),
    ("GET", "/api/simulation/defaults"): "global breed/system reference data, no farm scope",
    ("GET", "/api/simulation/defaults/breeds"): "global breed list, no farm scope",
}

# Domain routes that deliberately resolve no `X-Farm-Id`. README's tenancy
# section names exactly these two; keep the two in step.
FARMLESS_BY_DESIGN: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/api/simulation/defaults"),
        ("GET", "/api/simulation/defaults/breeds"),
        ("GET", "/api/owner/overview"),
        ("GET", "/api/owner/benchmarks"),
    }
)

# The old extractor collapsed from 65 routes to 16 without anyone noticing.
# A floor turns "the walker went blind" into a failure of its own.
MIN_GUARDED_ROUTES = 60

_PATH_PARAM = re.compile(r"\{[^{}]+\}")


def _api_routes(router: object) -> Iterator[APIRoute]:
    """Flatten the app's route table. `include_router` stores each included
    router inside a private container route, so recurse through it."""
    for route in getattr(router, "routes", []):
        if isinstance(route, APIRoute):
            yield route
            continue
        included = getattr(route, "original_router", None)
        if included is not None:
            yield from _api_routes(included)


def _required_perms(dependant: Dependant) -> list[str]:
    """Every permission code enforced anywhere in a route's resolved dependency
    tree. `require_perm(code)` returns a closure, so the code is read back from
    the closure rather than from the (aliased) annotation in the signature."""
    codes: list[str] = []
    for sub in dependant.dependencies:
        if getattr(sub.call, "__qualname__", "").startswith("require_perm."):
            codes.append(str(inspect.getclosurevars(sub.call).nonlocals["code"]))
        codes.extend(_required_perms(sub))
    return codes


def _uses_farm_context(dependant: Dependant) -> bool:
    """True when `X-Farm-Id` is resolved anywhere in the dependency tree."""
    return any(
        sub.call is current_farm or _uses_farm_context(sub) for sub in dependant.dependencies
    )


@cache
def _app_routes() -> tuple[tuple[str, str, str, bool], ...]:
    """(method, path, permission_code | "", uses_farm_context) for every route
    the app serves. Cached: building the app is the expensive part."""
    routes: list[tuple[str, str, str, bool]] = []
    for route in _api_routes(create_app().router):
        perms = sorted(set(_required_perms(route.dependant)))
        farm_scoped = _uses_farm_context(route.dependant)
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            routes.extend((method, route.path, perm, farm_scoped) for perm in perms or [""])
    return tuple(routes)


def _guarded_routes() -> list[tuple[str, str, str]]:
    return [(method, path, perm) for method, path, perm, _ in _app_routes() if perm]


def _placeholders(path: str, farm_id: str) -> str:
    """Replace `{...}` path params with harmless placeholders that the
    endpoint will 4xx on. We only care that we hit the perm check first
    (403), not that the underlying operation succeeds."""
    return _PATH_PARAM.sub(lambda match: farm_id if match.group() == "{farm_id}" else "99999", path)


async def _make_zero_perm_worker(client: httpx.AsyncClient, farm_headers: dict) -> dict:
    """A worker with a role that holds NONE of the declared permissions —
    every require_perm route must return 403 for this user."""
    role_resp = await client.post(
        "/api/team/roles",
        json={"name": "Zero", "description": "no perms", "permissions": []},
        headers=farm_headers,
    )
    assert role_resp.status_code == 201, role_resp.text
    role_id = role_resp.json()["id"]

    await client.post(
        "/api/team/workers",
        json={
            "email": "zero@farm.in",
            "name": "Zero",
            "role_id": role_id,
            "password": "workerpass123",
        },
        headers=farm_headers,
    )
    # Rotate through the forced password change: a provisioned worker that
    # never rotated is fenced off by the must-change-password 403 before any
    # require_perm logic runs, so every route would answer 403 regardless of
    # the role — the matrix would pass even with every guard deleted. The
    # rotated credential makes the permission check the thing under test.
    worker_headers = await login_and_rotate(client, "zero@farm.in", "workerpass123")
    return worker_headers | {"X-Farm-Id": farm_headers["X-Farm-Id"]}


async def test_every_require_perm_route_403s_for_a_zero_permission_worker(
    client: httpx.AsyncClient,
) -> None:
    """The declarative RBAC matrix. Adds/removals of require_perm dependencies
    show up here at the (route, method) level."""
    farm_headers = await owner_with_farm(client)
    worker_headers = await _make_zero_perm_worker(client, farm_headers)
    farm_id = farm_headers["X-Farm-Id"]

    routes = _guarded_routes()
    assert len(routes) >= MIN_GUARDED_ROUTES, (
        f"only {len(routes)} permission-guarded routes discovered, expected at least "
        f"{MIN_GUARDED_ROUTES} — the walker has gone blind, so this test is auditing "
        "far less than it claims"
    )
    # Routes guarded by ALTERNATIVE permissions (herd-snapshot, calibration)
    # appear once per declared code; the denial may name any one of them.
    declared_codes: dict[tuple[str, str], set[str]] = {}
    for method, path, perm in routes:
        declared_codes.setdefault((method, path), set()).add(perm)

    failures: list[str] = []
    for method, path, perm in routes:
        url = _placeholders(path, farm_id)
        request_method = client.request
        # GET/DELETE/others need no body; POST/PUT/PATCH send empty {}.
        kwargs = {"headers": worker_headers}
        if method in {"POST", "PUT", "PATCH"}:
            kwargs["json"] = {}
        resp = await request_method(method, url, **kwargs)
        # 403 is the correct answer, and it must be the PERMISSION check's
        # 403 — the denial detail names a missing code the route actually
        # declares. Any other 403 (e.g. the must-change-password fence)
        # means the guard itself was never reached, which is exactly the
        # regression this matrix exists to catch. 422 is acceptable ONLY
        # when the dependency chain rejects the body before hitting
        # require_perm, which would be a classification; we treat it as a
        # failure here to catch it.
        detail = str(resp.json().get("detail", "")) if resp.status_code == 403 else ""
        denied = re.fullmatch(r"Missing permission: (\S+)", detail)
        if (
            resp.status_code != 403
            or denied is None
            or denied.group(1) not in declared_codes[(method, path)]
        ):
            failures.append(
                f"{method} {path} (perm={perm}) → {resp.status_code}"
                f" (expected 403 'Missing permission: <declared code>'; body={resp.text[:120]!r})"
            )

    assert not failures, "RBAC coverage failures:\n" + "\n".join(failures)


async def test_guards_declared_through_aliases_and_sub_dependencies_are_seen() -> None:
    """Regression for the blind spot this module used to have: the ast walker
    matched only an inline `Depends(require_perm("..."))` in the route
    signature, so every router that declares its guard as a module-level
    `Annotated` alias — or behind a prepared sub-dependency — was dropped,
    leaving 16 of 65 routes audited while the suite stayed green."""
    guarded = set(_guarded_routes())
    aliased = {
        ("GET", "/api/team", "team.manage"),
        ("POST", "/api/tasks/{task_id}/verify", "tasks.verify"),
        ("GET", "/api/health/events", "health.view"),
        ("GET", "/api/finance", "finance.view"),
        ("POST", "/api/feeding/mix", "feeding.manage"),
        ("GET", "/api/purchases", "purchases.view"),
        ("GET", "/api/dashboard", "dashboard.view"),
        ("DELETE", "/api/simulation/scenarios/{scenario_id}", "simulation.manage"),
    }
    # team.py reaches require_perm only via _prepare_worker_create /
    # _prepare_password_reset, so these need the tree walked recursively.
    nested = {
        ("POST", "/api/team/workers", "team.manage"),
        ("POST", "/api/team/workers/{membership_id}/reset-password", "team.manage"),
    }
    assert (aliased | nested) <= guarded, f"missed: {sorted((aliased | nested) - guarded)}"


async def test_no_route_ships_without_a_permission_guard() -> None:
    """The other half of the contract: a route with no `require_perm` anywhere
    in its dependency tree must be an acknowledged public/self-service one."""
    unguarded = {(method, path) for method, path, perm, _ in _app_routes() if not perm}

    undeclared = sorted(unguarded - UNGUARDED_BY_DESIGN.keys())
    assert not undeclared, (
        "route(s) shipped with no permission guard:\n"
        + "\n".join(f"  {method} {path}" for method, path in undeclared)
        + "\nAdd the require_perm dependency, or record the reason it is public "
        "in UNGUARDED_BY_DESIGN."
    )

    stale = sorted(UNGUARDED_BY_DESIGN.keys() - unguarded)
    assert not stale, (
        "UNGUARDED_BY_DESIGN lists route(s) that are now guarded or gone:\n"
        + "\n".join(f"  {method} {path}" for method, path in stale)
    )


async def test_every_domain_route_resolves_farm_context() -> None:
    """README's tenancy section promises `X-Farm-Id` on the domain API. The two
    global simulation reference endpoints are the documented exceptions; any
    third one must be a deliberate, documented decision."""
    farmless = {
        (method, path)
        for method, path, _, farm_scoped in _app_routes()
        if not farm_scoped and path.startswith("/api/") and not path.startswith("/api/auth/")
    }
    assert farmless == FARMLESS_BY_DESIGN, (
        "domain routes without X-Farm-Id changed; update README's tenancy "
        f"section and FARMLESS_BY_DESIGN together. Found: {sorted(farmless)}"
    )
