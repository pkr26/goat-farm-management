"""Fail when changed production code is missing executable-line coverage.

The ordinary backend/frontend thresholds remain the broad ratchet. This gate
adds two properties aggregates cannot provide: coverage of lines changed in
this revision and a modest whole-file floor for every production file whose
executable code changed.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
DEFAULT_EXCLUDES = (
    "/api/generated/",
    "/test/",
    "/tests/",
    ".test.ts",
    ".test.tsx",
    ".d.ts",
)


@dataclass(frozen=True)
class FileCoverage:
    executable: frozenset[int]
    covered: frozenset[int]


def _run_git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args], check=True, text=True, capture_output=True
    )
    return completed.stdout


def _repo_path(filename: str, source_root: str) -> str:
    normalized = filename.replace("\\", "/")
    root = source_root.strip("/")
    marker = f"/{root}/"
    if marker in normalized:
        normalized = f"{root}/{normalized.split(marker, 1)[1]}"
    elif normalized.startswith(f"{root}/"):
        pass
    else:
        normalized = f"{root}/{normalized.lstrip('/')}"
    return PurePosixPath(normalized).as_posix()


def load_cobertura(path: Path, source_root: str) -> dict[str, FileCoverage]:
    by_file: dict[str, dict[int, int]] = defaultdict(dict)
    for class_node in ET.parse(path).getroot().findall(".//class"):
        filename = class_node.attrib.get("filename")
        if not filename:
            continue
        target = by_file[_repo_path(filename, source_root)]
        for line in class_node.findall("./lines/line"):
            number = int(line.attrib["number"])
            target[number] = max(
                target.get(number, 0), int(line.attrib.get("hits", "0"))
            )
    return {
        filename: FileCoverage(
            frozenset(lines), frozenset(n for n, hits in lines.items() if hits > 0)
        )
        for filename, lines in by_file.items()
    }


def load_istanbul(path: Path, source_root: str) -> dict[str, FileCoverage]:
    document: Any = json.loads(path.read_text())
    if not isinstance(document, dict):
        raise TypeError("Istanbul coverage must be a JSON object")
    result: dict[str, FileCoverage] = {}
    for filename, entry in document.items():
        if not isinstance(filename, str) or not isinstance(entry, dict):
            continue
        statement_map = entry.get("statementMap", {})
        statement_hits = entry.get("s", {})
        if not isinstance(statement_map, dict) or not isinstance(statement_hits, dict):
            continue
        hits_by_line: dict[int, list[int]] = defaultdict(list)
        for statement_id, location in statement_map.items():
            if not isinstance(location, dict):
                continue
            start = location.get("start")
            if not isinstance(start, dict) or not isinstance(start.get("line"), int):
                continue
            raw_hits = statement_hits.get(statement_id, 0)
            hits_by_line[start["line"]].append(
                raw_hits if isinstance(raw_hits, int) else 0
            )
        result[_repo_path(filename, source_root)] = FileCoverage(
            frozenset(hits_by_line),
            frozenset(
                line
                for line, hits in hits_by_line.items()
                if hits and all(value > 0 for value in hits)
            ),
        )
    return result


def changed_files(base: str, roots: list[str]) -> list[str]:
    output = _run_git(
        "diff", "--name-only", "--diff-filter=ACMR", f"{base}...HEAD", "--", *roots
    )
    return [line for line in output.splitlines() if line]


def changed_lines(base: str, filename: str) -> set[int]:
    diff = _run_git(
        "diff", "--unified=0", "--diff-filter=ACMR", f"{base}...HEAD", "--", filename
    )
    lines: set[int] = set()
    for row in diff.splitlines():
        match = HUNK.match(row)
        if not match:
            continue
        start = int(match.group(1))
        count = int(match.group(2) or "1")
        lines.update(range(start, start + count))
    return lines


def _percentage(covered: int, total: int) -> float:
    return 100.0 if total == 0 else covered * 100.0 / total


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True, help="immutable base commit/ref")
    parser.add_argument("--coverage", type=Path, required=True)
    parser.add_argument("--format", choices=("cobertura", "istanbul"), required=True)
    parser.add_argument(
        "--source-root",
        required=True,
        help="repo directory coverage paths are relative to",
    )
    parser.add_argument("--root", action="append", required=True, dest="roots")
    parser.add_argument("--changed-lines-min", type=float, default=90.0)
    parser.add_argument("--changed-file-min", type=float, default=60.0)
    parser.add_argument("--exclude", action="append", default=[])
    parser.add_argument(
        "--include-suffix",
        action="append",
        default=[],
        help="only inspect changed files ending in this suffix (repeatable)",
    )
    args = parser.parse_args()

    coverage = (
        load_cobertura(args.coverage, args.source_root)
        if args.format == "cobertura"
        else load_istanbul(args.coverage, args.source_root)
    )
    excludes = (*DEFAULT_EXCLUDES, *args.exclude)
    candidates = [
        name
        for name in changed_files(args.base, args.roots)
        if not any(token in f"/{name}" for token in excludes)
        and (
            not args.include_suffix
            or any(name.endswith(suffix) for suffix in args.include_suffix)
        )
    ]

    errors: list[str] = []
    summaries: list[dict[str, object]] = []
    changed_total = 0
    changed_covered = 0
    for filename in candidates:
        measured = coverage.get(filename)
        if measured is None:
            errors.append(
                f"{filename}: production file is absent from the coverage report"
            )
            continue
        whole_rate = _percentage(len(measured.covered), len(measured.executable))
        if whole_rate + 1e-9 < args.changed_file_min:
            errors.append(
                f"{filename}: whole-file executable-line coverage {whole_rate:.2f}% "
                f"is below {args.changed_file_min:.2f}%"
            )
        touched = changed_lines(args.base, filename) & set(measured.executable)
        if not touched:
            continue
        touched_covered = touched & set(measured.covered)
        changed_total += len(touched)
        changed_covered += len(touched_covered)
        changed_rate = _percentage(len(touched_covered), len(touched))
        summaries.append(
            {
                "file": filename,
                "changed_executable": len(touched),
                "changed_covered": len(touched_covered),
                "changed_rate": round(changed_rate, 2),
                "file_rate": round(whole_rate, 2),
            }
        )

    aggregate = _percentage(changed_covered, changed_total)
    if changed_total and aggregate + 1e-9 < args.changed_lines_min:
        errors.append(
            f"changed executable-line coverage {aggregate:.2f}% is below "
            f"{args.changed_lines_min:.2f}% ({changed_covered}/{changed_total})"
        )

    print(
        json.dumps(
            {
                "base": args.base,
                "changed_rate": round(aggregate, 2),
                "changed_covered": changed_covered,
                "changed_executable": changed_total,
                "files": summaries,
            },
            indent=2,
        )
    )
    for error in errors:
        print(f"::error::{error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
