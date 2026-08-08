"""Exhaustive RBAC coverage.

`test_rbac.py` samples 3 preset roles × 4-ish endpoints. This test walks
EVERY (path, method) that uses `require_perm(...)` and confirms a
zero-permission worker gets 403. A new endpoint shipped without a permission
guard now shows up as a test failure at the (route, method) level, not as a
silent security regression that hides behind sampled coverage.
"""

import ast
from pathlib import Path

import httpx

from .conftest import login, owner_with_farm

API_DIR = Path(__file__).resolve().parent.parent / "app" / "api"


def _routes_with_require_perm() -> list[tuple[str, str, str]]:
    """Walk backend/app/api/*.py and return (method, path, permission_code)
    for every FastAPI decorator whose function calls `require_perm(...)`.

    We use ast so a runtime shift (module import order) can't influence
    what we inspect; the result is a static contract.
    """
    routes: list[tuple[str, str, str]] = []
    for path in sorted(API_DIR.glob("*.py")):
        if path.name in {"__init__.py", "_shared.py"}:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        prefix = _router_prefix(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            method, route_path = _endpoint_from_decorators(node)
            if method is None or route_path is None:
                continue
            perm = _first_require_perm(node)
            if perm is None:
                continue
            routes.append((method, f"{prefix}{route_path}", perm))
    return routes


def _router_prefix(tree: ast.Module) -> str:
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "APIRouter"
        ):
            for kw in node.value.keywords:
                if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                    return str(kw.value.value)
    return ""


def _endpoint_from_decorators(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[str | None, str | None]:
    for dec in node.decorator_list:
        # @router.get("...") / @router.post("...") ...
        if (
            isinstance(dec, ast.Call)
            and isinstance(dec.func, ast.Attribute)
            and isinstance(dec.func.value, ast.Name)
            and dec.func.value.id == "router"
            and dec.func.attr in {"get", "post", "put", "patch", "delete"}
            and dec.args
            and isinstance(dec.args[0], ast.Constant)
        ):
            return dec.func.attr.upper(), str(dec.args[0].value)
    return None, None


def _first_require_perm(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """Extract the string literal from a require_perm("...") anywhere in the
    function's argument defaults (Depends(require_perm(...)))."""
    for sub in ast.walk(node):
        if (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Name)
            and sub.func.id == "require_perm"
            and sub.args
            and isinstance(sub.args[0], ast.Constant)
        ):
            return str(sub.args[0].value)
    return None


def _placeholders(path: str, farm_id: str) -> str:
    """Replace `{...}` path params with harmless placeholders that the
    endpoint will 4xx on. We only care that we hit the perm check first
    (403), not that the underlying operation succeeds."""
    return (
        path.replace("{farm_id}", farm_id)
        .replace("{animal_id}", "99999")
        .replace("{breeding_record_id}", "99999")
        .replace("{task_id}", "99999")
        .replace("{membership_id}", "99999")
        .replace("{role_id}", "99999")
        .replace("{batch_id}", "99999")
        .replace("{scenario_id}", "99999")
        .replace("{id}", "99999")
    )


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
    worker_headers = await login(client, "zero@farm.in", "workerpass123")
    return worker_headers | {"X-Farm-Id": farm_headers["X-Farm-Id"]}


async def test_every_require_perm_route_403s_for_a_zero_permission_worker(
    client: httpx.AsyncClient,
) -> None:
    """The declarative RBAC matrix. Adds/removals of require_perm decorators
    show up here at the (route, method) level."""
    farm_headers = await owner_with_farm(client)
    worker_headers = await _make_zero_perm_worker(client, farm_headers)
    farm_id = farm_headers["X-Farm-Id"]

    routes = _routes_with_require_perm()
    assert routes, "expected at least one require_perm route to audit"

    failures: list[str] = []
    for method, path, perm in routes:
        url = _placeholders(path, farm_id)
        request_method = client.request
        # GET/DELETE/others need no body; POST/PUT/PATCH send empty {}.
        kwargs = {"headers": worker_headers}
        if method in {"POST", "PUT", "PATCH"}:
            kwargs["json"] = {}
        resp = await request_method(method, url, **kwargs)
        # 403 is the correct answer. 422 is acceptable ONLY when the
        # dependency chain rejects the body before hitting require_perm,
        # which would be a classification; we treat it as a failure here to
        # catch it.
        if resp.status_code != 403:
            failures.append(
                f"{method} {path} (perm={perm}) → {resp.status_code}"
                f" (expected 403; body={resp.text[:120]!r})"
            )

    assert not failures, "RBAC coverage failures:\n" + "\n".join(failures)
