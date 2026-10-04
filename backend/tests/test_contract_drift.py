"""Current-checkout OpenAPI freshness and semantic contract assertions.

`shared/openapi.json` is the Orval input for the frontend's generated
client; before CI existed nothing failed when it went stale.

- Byte-identical guard — the file matches the live schema. If it fails,
  run: `backend/.venv/bin/python backend/scripts/export_openapi.py`
  followed by `cd frontend && pnpm orval`.

Backward compatibility is a separate CI gate: ``scripts/openapi_compat.py``
compares this candidate with ``shared/openapi.json`` read from the immutable
pull-request base Git object. Regenerating candidate artifacts therefore
cannot rewrite its comparison baseline.
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest

from app.main import create_app

from .type_helpers import json_object

OPENAPI_JSON = Path(__file__).resolve().parent.parent.parent / "shared" / "openapi.json"


def _committed_schema() -> dict[str, Any]:
    return json_object(json.loads(OPENAPI_JSON.read_text()))


def test_committed_openapi_json_matches_the_live_schema() -> None:
    live = json.dumps(create_app().openapi(), indent=2) + "\n"
    committed = OPENAPI_JSON.read_text()
    assert committed == live, (
        "shared/openapi.json is stale — regenerate it with "
        "scripts/export_openapi.py and re-run `pnpm orval` in frontend/"
    )


def test_openapi_numeric_bounds_use_standard_json_schema_keywords() -> None:
    """Runtime numeric caps must be visible to standards-based consumers.

    A ``Field(le=...)`` appended outside an ``AfterValidator`` is still
    enforced by Pydantic but used to leak the internal key ``le`` into the
    schema. OpenAPI understands ``maximum``, not Pydantic's constraint name.
    """
    schema = create_app().openapi()
    forbidden = {"le", "lt", "ge", "gt"}

    def invalid_constraint_paths(value: object, path: str = "$") -> list[str]:
        if isinstance(value, dict):
            found = [f"{path}.{key}" for key in value if key in forbidden]
            for key, child in value.items():
                found.extend(invalid_constraint_paths(child, f"{path}.{key}"))
            return found
        if isinstance(value, list):
            return [
                match
                for index, child in enumerate(value)
                for match in invalid_constraint_paths(child, f"{path}[{index}]")
            ]
        return []

    assert invalid_constraint_paths(schema) == []
    components = schema["components"]["schemas"]
    assert components["WeightIn"]["properties"]["weight_kg"]["maximum"] == 1000
    assert components["TransactionIn"]["properties"]["amount"]["maximum"] == 1_000_000_000


def test_openapi_declares_bearer_security_on_protected_operations() -> None:
    schema = create_app().openapi()
    bearer = schema["components"]["securitySchemes"]["HTTPBearer"]
    assert bearer == {"type": "http", "scheme": "bearer"}

    for path, method in [
        ("/api/auth/me", "get"),
        ("/api/auth/farms", "get"),
        ("/api/team", "get"),
    ]:
        operation = schema["paths"][path][method]
        assert operation["security"] == [{"HTTPBearer": []}]
        assert all(
            parameter["name"].lower() != "authorization"
            for parameter in operation.get("parameters", [])
        )

    assert "security" not in schema["paths"]["/api/auth/login"]["post"]
    assert schema["paths"]["/api/auth/me"]["get"]["responses"]["401"]["headers"] == {
        "WWW-Authenticate": {
            "description": "Bearer authentication challenge",
            "schema": {"type": "string", "example": "Bearer"},
        }
    }
    assert "headers" not in schema["paths"]["/api/auth/login"]["post"]["responses"]["401"]


def test_openapi_error_responses_match_runtime_shapes_and_have_no_dangling_schema_refs() -> None:
    """Business-rule 422s are strings; malformed requests carry safe issues.

    The router-wide response declaration is intentionally richer than
    FastAPI's default 422. Pin both union branches and the global middleware
    errors, then walk all schema refs so a manually-written OpenAPI ref cannot
    silently point at a component FastAPI no longer emits.
    """
    schema = create_app().openapi()
    components = schema["components"]["schemas"]
    responses = schema["paths"]["/api/finance/insurance/{policy_id}/claim"]["post"]["responses"]

    assert responses["422"]["content"]["application/json"]["schema"] == {
        "oneOf": [
            {"$ref": "#/components/schemas/ErrorOut"},
            {"$ref": "#/components/schemas/RequestValidationErrorOut"},
        ]
    }
    assert components["RequestValidationErrorOut"]["required"] == ["detail"]
    assert components["RequestValidationIssueOut"]["required"] == ["type", "loc", "msg"]
    for status_code in ("413", "414", "500", "503"):
        assert responses[status_code]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorOut"
        }
    assert "415" not in schema["paths"]["/api/animals"]["get"]["responses"]
    for path in (
        "/api/auth/register",
        "/api/auth/login",
        "/api/auth/worker-login",
        "/api/auth/totp/challenge",
    ):
        assert schema["paths"][path]["post"]["responses"]["415"]["content"]["application/json"][
            "schema"
        ] == {"$ref": "#/components/schemas/ErrorOut"}

    def schema_references(value: object) -> set[str]:
        if isinstance(value, dict):
            direct = {value["$ref"]} if isinstance(value.get("$ref"), str) else set()
            return direct | set().union(*(schema_references(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(schema_references(item) for item in value))
        return set()

    schema_refs = {
        ref for ref in schema_references(schema) if ref.startswith("#/components/schemas/")
    }
    assert all(ref.removeprefix("#/components/schemas/") in components for ref in schema_refs)


def test_farm_response_contract_requires_every_emitted_key() -> None:
    farm_schema = create_app().openapi()["components"]["schemas"]["FarmOut"]

    # ``role`` remains nullable (None means owner), but neither it nor the
    # resolved farm timezone is ever omitted from a backend response.
    assert set(farm_schema["required"]) == {
        "id",
        "name",
        "location",
        "timezone",
        "role",
    }


# --- Idempotency-Key required-header drift tripwire (2026-10-01 audit, 04-4) ---

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
IDEMPOTENT_REQUEST_TS = REPO_ROOT / "frontend" / "src" / "lib" / "idempotent-request.ts"


def _spec_routes_requiring_idempotency_key(schema: dict[str, Any]) -> set[str]:
    """POST paths whose ``Idempotency-Key`` header parameter is required.

    Orval 8 emits no typed parameter for header arguments on body routes, so
    the ONLY thing standing between these routes and a bare 422 for SDK
    consumers is the frontend transport's route registry (documented gap in
    frontend/src/api/custom-instance.ts). These are the routes that must
    never fall out of that registry.
    """
    required: set[str] = set()
    for path, methods in schema.get("paths", {}).items():
        operation = methods.get("post")
        if not isinstance(operation, dict):
            continue
        for parameter in operation.get("parameters", []):
            if (
                parameter.get("name") == "Idempotency-Key"
                and parameter.get("in") == "header"
                and parameter.get("required") is True
            ):
                required.add(path)
    return required


def _frontend_registry_matchers() -> tuple[set[str], list[re.Pattern[str]]]:
    """Exact paths and regex matchers from isIdempotencyProtectedMutation.

    The registry mixes ``path === "/api/..."`` literals with anchored
    ``/^\\/api\\/...$/ `` regex tests; both are extracted so the tripwire
    sees every matcher the runtime transport uses.
    """
    source = IDEMPOTENT_REQUEST_TS.read_text()
    exact = set(re.findall(r'path === "(/api/[^"]+)"', source))
    patterns = [
        re.compile(literal.replace("\\/", "/")) for literal in re.findall(r"/\^(.+?)\$/", source)
    ]
    return exact, patterns


def test_required_idempotency_key_routes_stay_covered_by_the_frontend_registry() -> None:
    """Every spec-required Idempotency-Key route must be in the transport's
    allowlist: the generated SDK carries no typed header parameter for it
    (orval 8 gap), so registry drift would surface as a bare 422 for any
    consumer of the generated client without the custom instance."""
    if not IDEMPOTENT_REQUEST_TS.exists():  # backend-only checkout
        pytest.skip("frontend/src/lib/idempotent-request.ts not present")
    required = _spec_routes_requiring_idempotency_key(_committed_schema())
    assert required, "extraction found no required Idempotency-Key routes — parser drift"
    exact, patterns = _frontend_registry_matchers()
    assert exact or patterns, "frontend registry extraction found nothing — parser drift"

    def covered(spec_path: str) -> bool:
        # Instantiate path params with a concrete id (every idempotency-keyed
        # route parameter is an integer resource id) and ask the registry.
        concrete = re.sub(r"\{[^}]+\}", "1", spec_path)
        return concrete in exact or any(p.fullmatch(concrete) for p in patterns)

    uncovered = sorted(path for path in required if not covered(path))
    assert not uncovered, (
        "routes marked Idempotency-Key: required in shared/openapi.json but missing "
        f"from frontend isIdempotencyProtectedMutation: {uncovered}"
    )


def test_conftest_auto_key_hook_matches_the_spec_required_header_set() -> None:
    """The test client's auto-key injection mirrors the same spec set (with
    its documented inventory-add path normalization), so backend tests keep
    exercising the business logic on exactly the routes the spec protects."""
    from .conftest import IDEMPOTENCY_REQUIRED_PATHS

    def conftest_style(spec_path: str) -> str:
        return re.sub(
            r"^/api/feeding/inventory/\{item_id\}/add$",
            "/api/feeding/inventory-add",
            spec_path,
        )

    spec_required = _spec_routes_requiring_idempotency_key(_committed_schema())
    normalized = {conftest_style(path) for path in spec_required}
    assert normalized == set(IDEMPOTENCY_REQUIRED_PATHS)
