"""Contract-drift guard (AUDIT 10-M3).

`shared/openapi.json` is the Orval input for the frontend's generated
client; before CI existed nothing failed when it went stale. This test
rebuilds the schema from the live app and compares it byte-for-byte with
the committed export — if it fails, run:

    backend/.venv/bin/python backend/scripts/export_openapi.py
    cd frontend && pnpm orval
"""

import json
from pathlib import Path

from app.main import create_app

OPENAPI_JSON = Path(__file__).resolve().parent.parent.parent / "shared" / "openapi.json"


def test_committed_openapi_json_matches_the_live_schema() -> None:
    live = json.dumps(create_app().openapi(), indent=2) + "\n"
    committed = OPENAPI_JSON.read_text()
    assert committed == live, (
        "shared/openapi.json is stale — regenerate it with "
        "scripts/export_openapi.py and re-run `pnpm orval` in frontend/"
    )
