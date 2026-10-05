"""Registry operations use local doubles; no test publishes or deletes real versions."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".github/scripts/cleanup_release_versions.py"


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    pass


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    pass


@pytest.mark.parametrize("scenario", ["preflight", "owned", "foreign_tag", "wrong_digest"])
def test_release_cleanup_only_deletes_exact_exclusively_owned_versions(
    tmp_path: Path, scenario: str
) -> None:
    digest = "sha256:" + "a" * 64
    tag = "v1-run-123-2-publish-amd64"
    receipt = {"image": "ghcr.io/example/goatfarm-backend", "tag": tag, "digest": digest}
    log = tmp_path / "gh-calls.jsonl"
    fake = tmp_path / "gh"
    fake.write_text("""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
from typing import Any
with Path(os.environ['GH_TEST_LOG']).open('a') as out:
    out.write(json.dumps(sys.argv[1:])+'\\n')
if '--slurp' in sys.argv:
    print(os.environ['GH_TEST_VERSIONS'])
""")
    fake.chmod(0o700)
    version: dict[str, Any] = {"id": 31, "name": digest, "metadata": {"container": {"tags": [tag]}}}
    if scenario == "foreign_tag":
        version["metadata"]["container"]["tags"].append("previous-stable")
    if scenario == "wrong_digest":
        version["name"] = "sha256:" + "b" * 64
    env = os.environ | {
        "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
        "RELEASE_OWNER": "Example",
        "GITHUB_RUN_ID": "123",
        "GITHUB_RUN_ATTEMPT": "2",
        "RUNNER_TEMP": str(tmp_path),
        "OWNED_PUBLISHES": json.dumps([] if scenario == "preflight" else [receipt]),
        "GH_TEST_LOG": str(log),
        "GH_TEST_VERSIONS": json.dumps([[version]]),
    }
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    deletes = [call for call in calls if "DELETE" in call]
    assert len(deletes) == (1 if scenario == "owned" else 0)
    if scenario == "preflight":
        assert calls == []
    if deletes:
        assert deletes[0][-1].endswith("/goatfarm-backend/versions/31")
