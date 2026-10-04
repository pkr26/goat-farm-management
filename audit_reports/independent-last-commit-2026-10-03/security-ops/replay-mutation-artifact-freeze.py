"""Replay captured pre-fix harness code inside a disposable pytest process.

Invoke with backend/.venv/bin/python from any directory. Expected result:
five failed focused regressions. This never replaces workspace source files.
"""

import importlib
import os
import sys
from pathlib import Path

evidence = Path(__file__).resolve().parent
repository = evidence.parents[2]
backend = repository / "backend"
sys.path.insert(0, str(backend / "mutation"))
os.chdir(backend)
for name in ("mutate_run", "mutate_cover", "mutate_report"):
    module = importlib.import_module(name)
    snapshot = evidence / f"{name}-before-artifact-freeze.py.txt"
    exec(compile(snapshot.read_text(), str(snapshot), "exec"), module.__dict__)

import pytest  # noqa: E402

raise SystemExit(
    pytest.main(
        [
            "tests/test_mutation_harness_20261003.py",
            "-k",
            "captured_coverage or captured_manifest or publication_hashes or report_never_scores",
            "-q",
            "--tb=short",
        ]
    )
)
