"""Use the unchanged CI coverage gate against local uncommitted changes.

Only git diff discovery changes: include the working tree and new source files.
Coverage loading, executable-line intersection and thresholds remain CI's code.
"""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
spec = spec_from_file_location("coverage_gate", ROOT / ".github/scripts/check_changed_coverage.py")
module = module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
run_git = module._run_git
original_lines = module.changed_lines


def worktree_git(*args):
    return run_git(*(arg.removesuffix("...HEAD") if arg.endswith("...HEAD") else arg for arg in args))


def files(base, roots):
    tracked = worktree_git("diff", "--name-only", "--diff-filter=ACMR", base, "--", *roots)
    new = run_git("ls-files", "--others", "--exclude-standard", "--", *roots)
    return sorted(set(tracked.splitlines()) | set(new.splitlines()))


def lines(base, filename):
    if filename in run_git("ls-files", "--others", "--exclude-standard", "--", filename).splitlines():
        return set(range(1, len((ROOT / filename).read_text().splitlines()) + 1))
    return original_lines(base, filename)


module._run_git = worktree_git
module.changed_files = files
module.changed_lines = lines
raise SystemExit(module.main())
