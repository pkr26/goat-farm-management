"""Collect a passing isolated baseline and atomically publish coverage provenance."""

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from mutate_identity import atomic_json, input_identity, sha_bytes
from mutate_run import BACKEND, VENV_PY, snapshot_ignore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.dry_run:
        print(json.dumps({"selection": "all tests", "isolated": True, "writes": False}))
        return
    inputs = input_identity(BACKEND)
    with tempfile.TemporaryDirectory(prefix="herdly-coverage-") as temporary:
        root = Path(temporary) / "repo"
        shutil.copytree(BACKEND.parent, root, ignore=snapshot_ignore)
        workspace = root / BACKEND.name
        if input_identity(workspace) != inputs:
            raise SystemExit("source/tests changed during snapshot; no coverage published")
        env = os.environ.copy()
        env.update(
            {
                "GOATFARM_TEST_DB": f"herdly_cov_{uuid.uuid4().hex[:12]}_test",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": str(workspace),
                "COVERAGE_FILE": str(workspace / ".coverage-mut"),
            }
        )
        result = subprocess.run(
            [
                str(VENV_PY),
                "-m",
                "pytest",
                "tests",
                "--maxfail=1",
                "--cov=app",
                "--cov-context=test",
                "--cov-report=",
            ],
            cwd=workspace,
            env=env,
            check=False,
        )
        if result.returncode != 0 or not (workspace / ".coverage-mut").is_file():
            raise SystemExit("clean coverage baseline failed; no coverage published")
        if input_identity(BACKEND) != inputs:
            raise SystemExit("source/tests changed during baseline; no coverage published")
        coverage_bytes = (workspace / ".coverage-mut").read_bytes()
        temporary_data = BACKEND / ".coverage-mut.tmp"
        temporary_data.write_bytes(coverage_bytes)
        temporary_data.replace(BACKEND / ".coverage-mut")
        atomic_json(
            BACKEND / ".coverage-mut.provenance.json",
            {
                "schema": 1,
                "source_root": str(workspace),
                "inputs": inputs,
                "coverage_sha256": sha_bytes(coverage_bytes),
                "baseline_exit_code": 0,
                "selection": "all tests",
            },
        )


if __name__ == "__main__":
    main()
