"""Inventory the final source and evidence without changing the original audit."""
from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[3]
REPORT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(name: str, content: object) -> None:
    (REPORT / "root" / name).write_text(json.dumps(content, indent=2) + "\n")


def main() -> None:
    original = ROOT / "audit_reports/independent-26-track-2026-10-04"
    inventory_path = original / "evidence/artifact-sha256.json"
    inventory = json.loads(inventory_path.read_text())["sha256"]
    changed = [name for name, expected in inventory.items()
               if not (original / name).is_file() or digest(original / name) != expected]
    write("original-audit-integrity.json", {
        "checked": len(inventory), "changed_or_missing": changed,
        "original_manifest_sha256": digest(inventory_path),
    })
    if changed:
        raise SystemExit("Original audit integrity failed")

    master = (REPORT / "REMEDIATION_29_FINDINGS.md").read_text()
    ids = re.findall(r"^\| ((?:D|F|O|S)26-\d{2}) \|", master, flags=re.M)
    expected = [f"{scope}26-{index:02d}" for scope, count in (("D", 9), ("F", 9), ("O", 9), ("S", 2))
                for index in range(1, count + 1)]
    if sorted(ids) != sorted(expected):
        raise SystemExit("Master finding coverage is incomplete or duplicated")
    generated = {(REPORT / "root" / name).resolve() for name in (
        "source-manifest.json", "artifact-sha256.json", "report-integrity.json"
    )}
    broken: list[dict[str, str]] = []
    for markdown in sorted(REPORT.rglob("*.md")):
        for link in re.findall(r"\]\(([^)]+)\)", markdown.read_text()):
            target = link.split("#", 1)[0]
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            target_path = (markdown.parent / unquote(target)).resolve()
            # These three referenced inventories are produced later in this
            # same invocation and asserted present before successful exit.
            if not target_path.exists() and target_path not in generated:
                broken.append({"file": str(markdown.relative_to(REPORT)), "target": target})
    write("report-integrity.json", {"finding_ids": ids, "broken_local_links": broken})
    if broken:
        raise SystemExit("Broken report links")

    paths = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=ROOT
    ).decode().split("\0")
    source = {name: digest(ROOT / name) for name in sorted(set(paths))
              if name and not name.startswith("audit_reports/") and (ROOT / name).is_file()}
    write("source-manifest.json", {
        "at_utc": datetime.now(UTC).isoformat(),
        "baseline_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip(),
        "scope": "Tracked and nonignored source outside audit_reports; local worktree, not a commit.",
        "files": len(source), "sha256": source,
    })
    artifacts = {str(path.relative_to(REPORT)): digest(path)
                 for path in sorted(REPORT.rglob("*"))
                 if path.is_file() and "__pycache__" not in path.parts
                 and path != REPORT / "root/artifact-sha256.json"}
    write("artifact-sha256.json", {
        "at_utc": datetime.now(UTC).isoformat(),
        "scope": "All remediation artifacts except this manifest and bytecode caches. Checksum inventory, not a signature.",
        "files": len(artifacts), "sha256": artifacts,
    })
    if not all(path.is_file() for path in generated):
        raise SystemExit("Generated inventory missing")
    print(json.dumps({"original_artifacts_verified": len(inventory), "findings": len(ids),
                      "source_files": len(source), "remediation_artifacts": len(artifacts)}))


if __name__ == "__main__":
    main()
