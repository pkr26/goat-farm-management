"""Read-only reconciliation and bounded credential-pattern checks.

Run with the backend virtualenv interpreter. This script prints JSON, never
secret values, and does not write reports or change application/database data.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import ast
import asyncio
import hashlib
import json
import os
import re
import subprocess
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
from cryptography.hazmat.primitives.serialization import load_pem_private_key

ROOT = Path(__file__).resolve().parents[3]
REPORT_DIRECTORY = Path(__file__).resolve().parent
BASE_REVISION = "b285645"
EXPECTED_HEAD = "f8e2f6a0c5d3"
EXPECTED_FORWARDS = {
    "f5b9d3e7a2c0_scope_pin_refresh_sessions.py",
    "f6c0e4f8b3d1_clinical_evidence_and_budget.py",
    "f7d1e5f9b4c2_transactional_notification_outbox.py",
    "f8e2f6a0c5d3_durable_maintenance_progress.py",
}


def command(argv: list[str], *, cwd: Path = ROOT) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        argv,
        cwd=cwd,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        timeout=30,
        check=False,
    )


def git(*arguments: str) -> bytes:
    result = command(["git", *arguments])
    if result.returncode != 0:
        # Avoid printing arbitrary command output: a future failing tool
        # invocation must not expose credential-bearing source or artifacts.
        raise RuntimeError("Read-only Git command failed")
    return result.stdout


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_migrations() -> dict[str, object]:
    directory = "backend/alembic/versions"
    historical = git("ls-tree", "-r", "--name-only", BASE_REVISION, "--", directory)
    paths = historical.decode().splitlines()
    missing, modified = [], []
    for name in paths:
        path = ROOT / name
        if not path.is_file():
            missing.append(name)
        elif path.read_bytes() != git("show", f"{BASE_REVISION}:{name}"):
            modified.append(name)
    current = {path.name for path in (ROOT / directory).glob("*.py")}
    forwards = current - {Path(name).name for name in paths}
    heads_result = command(
        [sys.executable, "-B", "-m", "alembic", "heads"], cwd=ROOT / "backend"
    )
    heads = [
        line.split()[0]
        for line in heads_result.stdout.decode().splitlines()
        if line.strip()
    ]
    return {
        "base_revision": BASE_REVISION,
        "historical_modules": len(paths),
        "missing_historical": missing,
        "modified_historical": modified,
        "forward_modules": sorted(forwards),
        "heads": heads,
        "alembic_heads_exit_code": heads_result.returncode,
        "passed": len(paths) == 97
        and not missing
        and not modified
        and forwards == EXPECTED_FORWARDS
        and heads_result.returncode == 0
        and heads == [EXPECTED_HEAD],
    }


def verify_baseline() -> dict[str, object]:
    directory = ROOT / "audit_reports/2026-10-03"
    manifest = json.loads((directory / "evidence/manifest.json").read_text())
    failures = []
    for item in manifest["files"]:
        path = directory / item["path"]
        if not path.is_file():
            failures.append({"path": item["path"], "reason": "missing"})
        elif path.stat().st_size != item["bytes"] or digest(path) != item["sha256"]:
            failures.append(
                {"path": item["path"], "reason": "byte-size or digest mismatch"}
            )
    return {
        "manifest": "audit_reports/2026-10-03/evidence/manifest.json",
        "source_commit": manifest["source_commit"],
        "entries": len(manifest["files"]),
        "exact_matches": len(manifest["files"]) - len(failures),
        "failures": failures,
        "limitation": "Comparison uses the preserved manifest; it excludes itself and is not an independently signed archive.",
        "passed": len(manifest["files"]) == 63 and not failures,
    }


async def verify_backend() -> dict[str, object]:
    directory = ROOT / "audit_reports/implementation-2026-10-03/final-backend"
    manifest = json.loads((directory / "manifest.json").read_text())
    hashes = manifest["source_sha256"]
    missing = [name for name in hashes if not (ROOT / name).is_file()]
    mismatched = [
        name
        for name, expected in hashes.items()
        if (ROOT / name).is_file() and digest(ROOT / name) != expected
    ]
    nodeids = manifest["all_collected_nodeids"]
    all_files = {nodeid.split("::", 1)[0] for nodeid in nodeids}
    parts = manifest["parts"]
    file_sets = [set(part["files"]) for part in parts]
    partition_counts = [
        sum(nodeid.split("::", 1)[0] in files for nodeid in nodeids)
        for files in file_sets
    ]
    disjoint = len(file_sets) == 2 and not file_sets[0].intersection(file_sets[1])
    complete = set.union(*file_sets) == all_files
    totals = {key: 0 for key in ("tests", "failures", "errors", "skipped")}
    part_receipts = []
    for index in (1, 2):
        element = ET.parse(directory / f"part-{index}.xml").getroot()
        suites = list(element) if element.tag == "testsuites" else [element]
        counts = {
            key: sum(int(suite.get(key, "0")) for suite in suites) for key in totals
        }
        for key, value in counts.items():
            totals[key] += value
        receipt = json.loads((directory / f"receipt-{index}.json").read_text())
        part_receipts.append(
            {"part": index, "exit_code": receipt["exit_code"], **counts}
        )
    coverage = json.loads((directory / "coverage.json").read_text())["totals"]
    databases = [part["database"] for part in parts]
    # Only inspect these two explicitly named throwaway databases. Never
    # enumerate unrelated database names or read any business/auth data.
    if not all(
        re.fullmatch(r"herdly_final_[a-z0-9]+_test", name) for name in databases
    ):
        raise RuntimeError("Unexpected final partition database name")
    connection = await asyncpg.connect(
        "postgresql://localhost:5432/postgres", timeout=10
    )
    try:
        remaining = await connection.fetchval(
            "SELECT count(*) FROM pg_database WHERE datname = ANY($1::text[])",
            databases,
        )
    finally:
        await connection.close()
    return {
        "source_hashes": len(hashes),
        "missing_sources": missing,
        "mismatched_sources": mismatched,
        "collection_count": len(nodeids),
        "unique_nodeids": len(set(nodeids)),
        "collected_test_files": len(all_files),
        "partition_files_disjoint": disjoint,
        "partitions_cover_collection": complete,
        "partition_nodeid_counts": partition_counts,
        "junit_parts": part_receipts,
        "junit_total": totals,
        "passed_cases": totals["tests"]
        - totals["failures"]
        - totals["errors"]
        - totals["skipped"],
        "combined_coverage_percent": coverage["percent_covered"],
        "line_coverage_percent": coverage["percent_statements_covered"],
        "branch_coverage_percent": coverage["percent_branches_covered"],
        "existing_combined_floor": manifest["required_aggregate_coverage"],
        "actual_owned_partition_databases_remaining": remaining,
        "passed": len(hashes) == 410
        and not missing
        and not mismatched
        and len(nodeids) == len(set(nodeids)) == manifest["collected_count"] == 5128
        and disjoint
        and complete
        and partition_counts == [part["collected"] for part in parts] == [2564, 2564]
        and all(part["exit_code"] == 0 for part in part_receipts)
        and totals == {"tests": 5128, "failures": 0, "errors": 0, "skipped": 4}
        and coverage["percent_covered"] >= manifest["required_aggregate_coverage"] == 92
        and remaining == 0,
    }


def verify_hygiene() -> dict[str, object]:
    paths = [
        ROOT / name
        for name in git("ls-files", "--cached", "--others", "--exclude-standard", "-z")
        .decode()
        .split("\0")
        if name
        and (ROOT / name).is_file()
        and not (ROOT / name).is_relative_to(REPORT_DIRECTORY)
    ]
    credential_patterns = {
        "jwt_literals": re.compile(
            rb"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
        ),
        "private_key_headers": re.compile(
            rb"-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"
        ),
        "provider_key_shapes": re.compile(
            rb"(?:AKIA[0-9A-Z]{16}|sk-(?:proj-)?[A-Za-z0-9_-]{30,}|gh[pousr]_[A-Za-z0-9]{30,})"
        ),
    }
    artifact_patterns = {
        "credential_assignment": re.compile(
            rb"""(?i)["'](?:access_token|refresh_token|auth_token|authorization|password|api_key|secret_access_key)["']\s*[:=]\s*["'][^"'\n]{10,}["']"""
        ),
        "credential_url": re.compile(
            rb"""(?i)(?:postgres(?:ql)?|mysql|redis)://[^\s/@:"']+:[^\s/@"']+@"""
        ),
        "refresh_cookie_value": re.compile(
            rb"(?i)goatfarm_refresh\s*=\s*[A-Za-z0-9_-]{20,}"
        ),
    }
    candidates, raw_auth_files, structured_fields = [], [], []
    artifact_json_count = 0
    credential_keys = {
        "access_token",
        "refresh_token",
        "auth_token",
        "authorization",
        "password",
        "pin",
        "api_key",
        "secret_access_key",
        "sessionstorage",
        "storage_state",
        "storagestate",
        "localstorage",
    }

    def inspect_fields(value: object, location: str, name: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                normalized = str(key).lower().replace("-", "_")
                if normalized in credential_keys and child not in (None, "", [], {}):
                    structured_fields.append(
                        {
                            "file": name,
                            "location": location + "." + str(key),
                            "value_type": type(child).__name__,
                        }
                    )
                if (
                    normalized == "cookies"
                    and isinstance(child, list)
                    and any(
                        isinstance(cookie, dict) and cookie.get("value")
                        for cookie in child
                    )
                ):
                    structured_fields.append(
                        {
                            "file": name,
                            "location": location + ".cookies",
                            "value_type": "nonempty-cookie-values",
                        }
                    )
                inspect_fields(child, location + "." + str(key), name)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                inspect_fields(child, f"{location}[{index}]", name)

    for path in paths:
        name = str(path.relative_to(ROOT))
        content = path.read_bytes()
        patterns = (
            {**credential_patterns, **artifact_patterns}
            if name.startswith("audit_reports/")
            else credential_patterns
        )
        for label, pattern in patterns.items():
            matches = list(pattern.finditer(content))
            if matches:
                candidates.append({"file": name, "kind": label, "count": len(matches)})
        if re.search(
            r"(^|/)(?:test-results|playwright-report|\.auth|traces?)(/|$)|(?:trace|storage[-_]?state).*\.(?:json|zip|har)$|\.har$",
            name,
            re.IGNORECASE,
        ):
            raw_auth_files.append(name)
        if name.startswith("audit_reports/") and path.suffix == ".json":
            try:
                parsed = json.loads(content)
            except (UnicodeError, json.JSONDecodeError):
                continue
            artifact_json_count += 1
            inspect_fields(parsed, "$", name)

    # The sole initial candidate is an authored negative-test string. Check
    # its content remains an invalid key, without displaying that content.
    fixture = ROOT / "backend/tests/test_security_hardening.py"
    fixture_literals = []
    for node in ast.walk(ast.parse(fixture.read_text())):
        if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
            content = node.value.encode() if isinstance(node.value, str) else node.value
            if b"-----BEGIN" in content and b"PRIVATE KEY-----" in content:
                try:
                    load_pem_private_key(content, password=None)
                    valid = True
                except (ValueError, TypeError):
                    valid = False
                fixture_literals.append(
                    {
                        "line": node.lineno,
                        "literal_bytes": len(content),
                        "parseable_private_key": valid,
                    }
                )
    expected_candidate = {
        "file": "backend/tests/test_security_hardening.py",
        "kind": "private_key_headers",
        "count": 1,
    }
    return {
        "initial_independent_audit_included_files": 1671,
        "current_included_files_scanned": len(paths),
        "scope": "Git tracked and nonignored untracked files; verifier/README/summary in this receipt directory are excluded to keep reruns noncircular. New receipts added since the initial audit increase current scope.",
        "credential_pattern_candidates": candidates,
        "invalid_key_fixture_literals": fixture_literals,
        "included_raw_auth_trace_filename_candidates": raw_auth_files,
        "artifact_json_files_scanned": artifact_json_count,
        "nonempty_credential_or_browser_auth_fields": structured_fields,
        "limitation": "Known key/token/credential URL and browser-auth patterns are heuristic checks, not proof that every possible secret is absent. Ignored files and temporary traces outside the repository inclusion set are not inspected or certified.",
        "passed": candidates == [expected_candidate]
        and len(fixture_literals) == 1
        and not fixture_literals[0]["parseable_private_key"]
        and not raw_auth_files
        and not structured_fields,
    }


def verify_source_markers() -> dict[str, object]:
    directories = ("backend/app", "backend/scripts", "frontend/src", "frontend/public")
    changed = (
        git("diff", "--name-only", BASE_REVISION, "--", *directories)
        .decode()
        .splitlines()
    )
    added = (
        git("ls-files", "--others", "--exclude-standard", "--", *directories)
        .decode()
        .splitlines()
    )
    markers = []
    for name in sorted(set(changed + added)):
        path = ROOT / name
        if not path.is_file() or path.suffix not in {
            ".py",
            ".ts",
            ".tsx",
            ".js",
            ".mjs",
            ".sh",
        }:
            continue
        for number, line in enumerate(path.read_text().splitlines(), 1):
            labels = re.findall(r"\b(?:TODO|FIXME|XXX|HACK)\b", line)
            if labels:
                markers.append({"file": name, "line": number, "labels": labels})
    diff = command(["git", "diff", "--check"])
    return {
        "changed_authored_source_markers": markers,
        "git_diff_check_exit_code": diff.returncode,
        "passed": not markers and diff.returncode == 0,
    }


async def main() -> None:
    result = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "command": "backend/.venv/bin/python audit_reports/implementation-2026-10-03/final-integrity/verify.py",
        "verifier_sha256": digest(Path(__file__)),
        "read_only": True,
        "suite_reruns": False,
        "checks": {
            "migration_history": verify_migrations(),
            "baseline_bundle": verify_baseline(),
            "final_backend": await verify_backend(),
            "included_artifact_hygiene": verify_hygiene(),
            "changed_source_markers_and_diff": verify_source_markers(),
        },
    }
    result["all_passed"] = all(check["passed"] for check in result["checks"].values())
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["all_passed"] else 1)


if __name__ == "__main__":
    asyncio.run(main())
