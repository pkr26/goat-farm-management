"""Check the independent audit's final evidence and its current source binding.

Run from any directory with Python 3.13. This verifies preserved measurements;
it does not rerun tests or infer production/field readiness from local gates.
"""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
AUDIT = HERE.parent
ROOT = HERE.parents[2]


def read(path: Path) -> dict:
    return json.loads(path.read_text())


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_files(base: Path, files: dict[str, str]) -> None:
    for relative, expected in files.items():
        target = (base / relative).resolve()
        assert target.is_relative_to(base.resolve()), relative
        assert target.is_file(), f"Missing file: {relative}"
        assert digest(target) == expected, f"Changed file: {relative}"


def main() -> None:
    manifest = read(AUDIT / "evidence-manifest.json")
    check_files(AUDIT, manifest["evidence_files"])
    check_files(ROOT, manifest["final_source_files"])
    check_files(ROOT, manifest["documentation_updated_after_verification"])
    check_files(ROOT, manifest["original_claim_ledger"])
    for relative, expected in manifest["source_inventories"].items():
        actual = sorted(
            p.relative_to(ROOT).as_posix()
            for p in (ROOT / relative).rglob("*")
            if p.is_file() and "__pycache__" not in p.parts
            and p.suffix not in {".pyc", ".pyo"}
        )
        assert actual == expected, f"Source inventory changed: {relative}"

    claims = read(HERE / "original-claim-map.json")
    assert claims["original_tracked_claims"] == 83
    claim_ids = {item["finding"] for item in claims["claims"]}
    ledger_ids = set(re.findall(
        r"^\|\s*((?:FE|DP|D)\d+|(?:DB|PL|FM|BM|ADD)-\d+)\s*\|",
        (ROOT / next(iter(manifest["original_claim_ledger"]))).read_text(),
        re.MULTILINE,
    ))
    assert claim_ids == ledger_ids and len(claim_ids) == 83

    original = read(HERE / "original-backend-summary.json")
    assert original["counts"] == {"passed": 5124, "skipped": 4, "failed": 0, "errors": 0}
    assert original["unique_cases"] == 5128
    assert original["combined_line_branch_coverage"]["percent_covered"] >= 92

    backend = read(HERE / "final-backend-summary.json")
    assert backend["counts"] == {"passed": 5166, "skipped": 4, "failed": 0, "errors": 0}
    assert backend["unique_cases"] == 5170
    assert backend["complete_suite_snapshot_counts"] == {
        "passed": 5165, "skipped": 4, "failed": 0, "errors": 0,
    }
    assert backend["full_plus_latest_migration_checks_match_current_collection"]
    assert backend["combined_line_branch_coverage"]["percent_covered"] >= 92
    assert backend["combined_floor_exit_code"] == 0
    cases = {}
    for relative in [
        "verification/final-backend-part-1.xml",
        "verification/final-backend-part-2.xml",
        "domain/migration-contracts-final.xml",
    ]:
        for case in ET.parse(AUDIT / relative).findall(".//testcase"):
            assert case.find("failure") is None and case.find("error") is None
            key = (case.attrib["classname"], case.attrib["name"])
            cases[key] = "skipped" if case.find("skipped") is not None else "passed"
    assert len(cases) == 5170
    assert list(cases.values()).count("passed") == 5166
    assert list(cases.values()).count("skipped") == 4
    fingerprint = hashlib.sha256(
        "\n".join(sorted("::".join(key) for key in cases)).encode()
    ).hexdigest()
    assert fingerprint == backend["case_fingerprint"]
    delta = read(HERE / "post-freeze-source-delta.json")
    assert set(delta["only_source_deltas"]) == {
        "backend/alembic/versions/f9a3b7c1d5e2_preserve_legacy_screening_reviews.py",
        "backend/tests/test_independent_review_history_audit.py",
    }
    assert delta["latest_affected_sweep_passed"] == 212
    assert delta["latest_affected_sweep_failures"] == 0
    assert delta["backend_app_source_unchanged"] and delta["frontend_source_unchanged"]
    snapshot = read(HERE / "final-source-sha256.json")
    for relative, change in delta["only_source_deltas"].items():
        assert snapshot[relative] == change["snapshot_sha256"]
        assert manifest["final_source_files"][relative] == change["current_sha256"]

    frontend = read(HERE / "final-frontend-summary.json")
    assert frontend["passed"] == 5281 and frontend["failed"] == 0
    assert frontend["exit_code"] == frontend["production_build_exit_code"] == 0
    assert frontend["global_and_scoped_thresholds_unchanged"]
    binding = read(HERE / "browser-source-binding.json")
    assert binding["mismatches"] == []
    for relative, expected in binding["shipping_hashes"].items():
        if relative in delta["only_source_deltas"]:
            assert expected == delta["only_source_deltas"][relative]["snapshot_sha256"]
        else:
            check_files(ROOT, {relative: expected})
    for browser, expected in [("chromium", 74), ("webkit", 71)]:
        stats = read(HERE / f"final-{browser}-summary.json")["stats"]
        assert stats["expected"] == expected
        assert stats["unexpected"] == stats["flaky"] == stats["skipped"] == 0

    migrations = read(HERE / "migration-history.json")
    assert migrations["historical_modules"] == 101
    assert migrations["historical_changes"] == []
    assert migrations["new_head"] == "f9a3b7c1d5e2"

    cleanup = read(HERE / "final-resource-cleanup.json")
    assert cleanup["owned_suite_databases_remaining"] == []
    assert cleanup["owned_browser_resources_removed"]
    print(json.dumps({
        "evidence_files_verified": len(manifest["evidence_files"]),
        "source_files_verified": len(manifest["final_source_files"]),
        "original_claims": 83,
        "repaired_findings": 22,
        "backend_passed": 5166,
        "backend_skipped": 4,
        "frontend_passed": 5281,
        "fresh_browser_passed": 145,
        "limits": "Local source/evidence verification; no fresh Firefox, production or field validation.",
    }, indent=2))


if __name__ == "__main__":
    main()
