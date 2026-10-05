"""Check final report completeness and local evidence links without rerunning tests."""
from pathlib import Path
import collections
import hashlib
import json
import re
import subprocess

BASE = Path(__file__).resolve().parents[2]
ROOT = BASE.parent.parent
report = (BASE / "INDEPENDENT_26_TRACK_AUDIT.md").read_text()
findings = json.loads((BASE / "evidence/findings.json").read_text())
validation = json.loads((BASE / "evidence/validation-summary.json").read_text())
manifest = json.loads((BASE / "evidence/source-manifest.json").read_text())

track_table = report.split("## All 26 audit tracks\n", 1)[1].split("## Fresh validation\n", 1)[0]
tracks = [int(x) for x in re.findall(r"^\| (\d+) \|", track_table, re.M)]
assert tracks == list(range(1, 27)), tracks
details = re.findall(r"^#{2,5} ([DFOS]26-\d+) —", report, re.M)
expected = [i["id"] for i in findings["findings"]]
assert collections.Counter(details) == collections.Counter(expected)
anchors = re.findall(r'<a id="([dfos]26-\d+)"></a>', report)
assert collections.Counter(anchors) == collections.Counter(i.lower() for i in expected)
assert findings["total"] == len(details) == len(set(details))
assert findings["counts"] == dict(collections.Counter(i["severity"] for i in findings["findings"]))
for row in validation["checks"]:
    assert f"| {row['check']} | {row['outcome']} | [{row['label']}]({row['evidence']}) |" in report
assert "RUNNING — final receipt pending" not in report
assert "FINAL INTEGRITY CHECK PENDING" not in report

missing_links = []
local_links = 0
for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", report):
    if target.startswith(("http://", "https://", "mailto:", "#")):
        continue
    target = target.split("#", 1)[0].strip("<>")
    local_links += 1
    if not (BASE / target).exists():
        missing_links.append(target)
assert not missing_links, missing_links

changed_files = []
for name, expected_sha in manifest["sha256"].items():
    file = ROOT / name
    actual = hashlib.sha256(file.read_bytes()).hexdigest() if file.is_file() else None
    if actual != expected_sha:
        changed_files.append(name)
assert not changed_files, changed_files
revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
assert revision == manifest["commit"]
tracked_status = subprocess.check_output(
    ["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT, text=True
).strip()
assert not tracked_status, tracked_status

result = {
    "status": "PASS",
    "tracks": len(tracks),
    "distinct_findings": len(details),
    "severity_counts": findings["counts"],
    "detail_anchors": len(anchors),
    "validation_rows": len(validation["checks"]),
    "existing_local_links": local_links,
    "verified_current_source_hashes": len(manifest["sha256"]),
    "tracked_source_changes": changed_files,
    "revision": revision,
    "scope": "Report structure, retained evidence paths and source integrity; not a replacement for audit judgment or runtime validation.",
}
(BASE / "evidence/root/report-quality.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result))
