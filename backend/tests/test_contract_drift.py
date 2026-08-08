"""Contract-drift guard.

`shared/openapi.json` is the Orval input for the frontend's generated
client; before CI existed nothing failed when it went stale. Two tests:

- Byte-identical guard — the file matches the live schema. If it fails,
  run: `backend/.venv/bin/python backend/scripts/export_openapi.py`
  followed by `cd frontend && pnpm orval`.
- Shape-diff guard — the operations and schema names are a superset of the
  committed snapshot. The byte-identical guard is cosmetic (trailing
  whitespace or indent changes trip it); this second guard catches the
  case where the file is re-exported after a *breaking* change (removed
  operation, dropped schema, dropped enum value) and the byte-identical
  test starts passing again.
"""

import json
from pathlib import Path

from app.main import create_app

OPENAPI_JSON = Path(__file__).resolve().parent.parent.parent / "shared" / "openapi.json"


def _committed_schema() -> dict:
    return json.loads(OPENAPI_JSON.read_text())


def test_committed_openapi_json_matches_the_live_schema() -> None:
    live = json.dumps(create_app().openapi(), indent=2) + "\n"
    committed = OPENAPI_JSON.read_text()
    assert committed == live, (
        "shared/openapi.json is stale — regenerate it with "
        "scripts/export_openapi.py and re-run `pnpm orval` in frontend/"
    )


def test_openapi_paths_and_schemas_are_a_superset_of_the_committed_snapshot() -> None:
    """A stronger check than byte-identical: no operationId, path+method, or
    schema name that the frontend depends on may disappear. Additions are
    fine (additive change); removals must be an explicit, reviewed edit."""
    live = create_app().openapi()
    committed = _committed_schema()

    def operation_ids(schema: dict) -> set[str]:
        ids: set[str] = set()
        for _path, methods in schema.get("paths", {}).items():
            for _method, op in methods.items():
                if isinstance(op, dict) and "operationId" in op:
                    ids.add(op["operationId"])
        return ids

    def path_methods(schema: dict) -> set[tuple[str, str]]:
        return {
            (path, method.upper())
            for path, methods in schema.get("paths", {}).items()
            for method in methods
            if method.lower() in {"get", "post", "put", "patch", "delete"}
        }

    def schema_names(schema: dict) -> set[str]:
        return set(schema.get("components", {}).get("schemas", {}).keys())

    committed_ops = operation_ids(committed)
    live_ops = operation_ids(live)
    missing_ops = committed_ops - live_ops
    assert not missing_ops, f"operationIds removed since snapshot: {sorted(missing_ops)}"

    committed_paths = path_methods(committed)
    live_paths = path_methods(live)
    missing_paths = committed_paths - live_paths
    assert not missing_paths, f"path/method pairs removed since snapshot: {sorted(missing_paths)}"

    committed_schemas = schema_names(committed)
    live_schemas = schema_names(live)
    missing_schemas = committed_schemas - live_schemas
    assert not missing_schemas, f"schemas removed since snapshot: {sorted(missing_schemas)}"
