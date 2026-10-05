"""Record completed pytest/coverage receipts; pass the actual process exit code."""
from pathlib import Path
import json
import sys
import tomllib
import xml.etree.ElementTree as ET

BASE = Path(__file__).resolve().parents[2]
ROOT = BASE.parent.parent
exit_code = int(sys.argv[1])
suite = ET.parse(BASE / "evidence/backend-full.xml").getroot().find("testsuite")
assert suite is not None
stats = {key: int(suite.attrib[key]) for key in ("tests", "failures", "errors", "skipped")}
stats["passed"] = stats["tests"] - stats["failures"] - stats["errors"] - stats["skipped"]
duration = float(suite.attrib["time"])
skips = [
    {"class": case.attrib.get("classname"), "test": case.attrib["name"], "reason": case.find("skipped").attrib.get("message")}
    for case in suite.findall("testcase") if case.find("skipped") is not None
]
coverage = ET.parse(BASE / "evidence/backend-coverage.xml").getroot().attrib
counts = {key: int(coverage[key]) for key in ("lines-valid", "lines-covered", "branches-valid", "branches-covered")}
combined = 100 * (counts["lines-covered"] + counts["branches-covered"]) / (counts["lines-valid"] + counts["branches-valid"])
line_percent = 100 * counts["lines-covered"] / counts["lines-valid"]
branch_percent = 100 * counts["branches-covered"] / counts["branches-valid"]
config = tomllib.loads((ROOT / "backend/pyproject.toml").read_text())
floor = config["tool"]["coverage"]["report"]["fail_under"]
result = {
    "process_exit_code": exit_code,
    "junit": stats,
    "duration_seconds": duration,
    "skip_details": skips,
    "coverage": {**counts, "line_percent": line_percent, "branch_percent": branch_percent, "combined_percent": combined, "combined_floor": floor},
    "receipt_paths": ["evidence/backend-full.log", "evidence/backend-full.xml", "evidence/backend-coverage.xml"],
}
(BASE / "evidence/root/backend-validation.json").write_text(json.dumps(result, indent=2) + "\n")
validation_path = BASE / "evidence/validation-summary.json"
validation = json.loads(validation_path.read_text())
passed = exit_code == 0 and stats["failures"] == 0 and stats["errors"] == 0
status = "PASS" if passed else "FAILED"
for row in validation["checks"]:
    if row["check"] == "Backend complete suite and branch coverage":
        row["outcome"] = (
            f"{status}: {stats['passed']:,} passed, {stats['skipped']} skipped, {stats['failures']} failed, {stats['errors']} errors; "
            f"combined line/branch coverage {combined:.2f}% against {floor}% floor "
            f"(lines {line_percent:.2f}%, branches {branch_percent:.2f}%); {duration:.2f} seconds; exit {exit_code}"
        )
        row["label"] = "Final JUnit/coverage summary"
        row["evidence"] = "evidence/root/backend-validation.json"
        break
else:
    raise AssertionError("Backend validation row missing")
validation_path.write_text(json.dumps(validation, indent=2) + "\n")
print(json.dumps(result))
