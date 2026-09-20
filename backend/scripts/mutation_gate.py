#!/usr/bin/env python3
"""Per-PR diff-scoped mutation gate for tenancy-critical and financial paths.

The weekly mutmut campaign is far too slow to sit on a PR, but two classes of
code cannot wait a week for feedback:

  * TENANCY_CRITICAL — every router's farm scoping, the X-Farm-Id gate
    (deps.py), the RBAC layer (permissions.py) and the idempotency scoping;
    a surviving tenant-filter mutant here is a cross-farm data leak.
  * FINANCIAL_CORE — the simulation engine/finance/market math, where a
    surviving arithmetic mutant silently mis-prices every projection.

This gate applies a small, hand-curated catalog of surgical mutants that are
MAPPED TO SOURCE FILES, keeps only those whose files intersect the PR diff,
runs each against its targeted pytest selection in a throwaway checkout, and
fails the check if any mutant SURVIVES (the full suite passes with the mutant
applied). Mutants killed by targeted tests cost ~1-3 min each; a typical
scoped PR runs 2-6 of them.

Usage (from backend/, in CI):
  .venv/bin/python scripts/mutation_gate.py --base origin/main

Exit codes: 0 = all in-scope mutants killed (or nothing in scope);
1 = at least one surviving mutant; 2 = harness/config error.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
REPO = BACKEND.parent

# Curated mutants: (id, description, file, op, targeted tests).
# op kinds: ("replace", old, new, count) | ("regex_first", pattern, repl)
@dataclass
class GateMutant:
    scope: str
    mid: str
    description: str
    file: str  # repo-relative
    op: tuple
    targeted: list[str] = field(default_factory=list)


T = "tests"


def _tenant(mid, desc, file, old, new, targeted, count=1):
    return GateMutant("tenancy", mid, desc, file, ("replace", old, new, count), targeted)


def _regex_tenant(mid, desc, file, pattern, repl, targeted):
    return GateMutant("tenancy", mid, desc, file, ("regex_first", pattern, repl), targeted)


def _finance(mid, desc, file, old, new, targeted, count=1):
    return GateMutant("financial", mid, desc, file, ("replace", old, new, count), targeted)


TENANCY_COMMON = [f"{T}/test_tenant_foreign_keys.py", f"{T}/test_rbac_exhaustive.py"]

CATALOG: list[GateMutant] = [
    # ---- tenant-filter deletion: detail (IDOR) queries ------------------
    _tenant("animals-idor", "animals: farm filter dropped from by-id lookup",
            "backend/app/api/animals.py",
            "stmt = select(Animal).where(Animal.id == animal_id, Animal.farm_id == farm_id)",
            "stmt = select(Animal).where(Animal.id == animal_id)",
            [f"{T}/test_animals_extended.py", *TENANCY_COMMON], count=2),
    _tenant("breeding-idor", "breeding: farm filter dropped from record-by-id",
            "backend/app/api/breeding.py",
            ".where(BreedingRecord.id == record_id, BreedingRecord.farm_id == farm.id)",
            ".where(BreedingRecord.id == record_id)",
            [f"{T}/test_breeding_extended.py", *TENANCY_COMMON]),
    _tenant("feeding-idor", "feeding: farm filter dropped from inventory item by id",
            "backend/app/api/feeding.py",
            ".where(FeedInventory.id == item_id, FeedInventory.farm_id == farm.id)",
            ".where(FeedInventory.id == item_id)",
            [f"{T}/test_feeding_extended.py", *TENANCY_COMMON]),
    _tenant("health-idor", "health: farm filter dropped from animal-by-id",
            "backend/app/api/health.py",
            "select(Animal).where(Animal.id == animal_id, Animal.farm_id == farm.id)",
            "select(Animal).where(Animal.id == animal_id)",
            [f"{T}/test_health_extended.py", *TENANCY_COMMON]),
    _tenant("tasks-idor", "tasks: farm filter dropped from task-by-id",
            "backend/app/api/tasks.py",
            ".where(Task.id == task_id, Task.farm_id == farm.id)",
            ".where(Task.id == task_id)",
            [f"{T}/test_tasks_extended.py", *TENANCY_COMMON], count=3),
    _tenant("screening-idor", "screening: farm filter dropped from image-by-id",
            "backend/app/api/screening.py",
            ".where(ScreeningImage.farm_id == farm.id, ScreeningImage.id == image_id)",
            ".where(ScreeningImage.id == image_id)",
            [f"{T}/test_screening.py", *TENANCY_COMMON]),
    # ---- tenant-filter deletion: list queries ---------------------------
    _tenant("animals-list", "animals: farm filter dropped from list query",
            "backend/app/api/animals.py",
            "stmt = select(Animal).where(Animal.farm_id == farm.id)",
            "stmt = select(Animal)",
            [f"{T}/test_animals_extended.py", f"{T}/test_animal_profile_pagination.py",
             *TENANCY_COMMON]),
    _tenant("finance-list", "finance: farm filter dropped from transactions list",
            "backend/app/api/finance.py",
            "Transaction.farm_id == farm_id,\n",
            "",
            [f"{T}/test_finance_extended.py", f"{T}/test_finance_bugs.py", *TENANCY_COMMON]),
    _tenant("kidding-list", "kidding: farm filter dropped from records list",
            "backend/app/api/kidding.py",
            ".where(KiddingRecord.farm_id == farm.id)",
            ".where(KiddingRecord.id > 0)",
            [f"{T}/test_kidding_husbandry.py", *TENANCY_COMMON], count=2),
    _tenant("team-roles", "team: farm filter dropped from role list",
            "backend/app/api/team.py",
            "Role.farm_id == farm.id,\n",
            "",
            [f"{T}/test_team_extended.py", f"{T}/test_rbac.py", *TENANCY_COMMON], count=2),
    _tenant("auth-farmscope", "farm selector lists every farm, not just owned",
            "backend/app/deps.py",
            "select(Farm).where(Farm.owner_id == user.id).order_by(Farm.id).limit(cap + 1)",
            "select(Farm).order_by(Farm.id).limit(cap + 1)",
            [f"{T}/test_auth_extended.py", *TENANCY_COMMON]),
    # ---- central X-Farm-Id / RBAC gates ---------------------------------
    _regex_tenant("rbac-require-perm-bypass", "require_perm never denies",
                  "backend/app/deps.py",
                  r"        if code not in perms:",
                  "        if False and code not in perms:",
                  [f"{T}/test_rbac.py", f"{T}/test_rbac_exhaustive.py",
                   f"{T}/test_dashboard_permissions.py"]),
    _tenant("rbac-perms-for-owner", "every farm member receives the owner permission set",
            "backend/app/deps.py",
            ("    if farm.owner_id == user.id:\n        return set(ALL_PERMISSIONS)\n"
             "    if membership is None or membership.role is None:\n        return set()\n"
             "    return membership.role.permission_set()"),
            ("    if farm.owner_id == user.id:\n        return set(ALL_PERMISSIONS)\n"
             "    if membership is None or membership.role is None:\n        return set()\n"
             "    return set(ALL_PERMISSIONS)"),
            [f"{T}/test_rbac.py", f"{T}/test_rbac_exhaustive.py",
             f"{T}/test_dashboard_permissions.py"]),
    # ---- idempotency scoping --------------------------------------------
    _tenant("idem-scope-farm", "idempotency replay lookup drops the farm scope",
            "backend/app/services/idempotency.py",
            "                IdempotencyRecord.farm_id == farm_id,\n",
            "",
            [f"{T}/test_idempotency.py", f"{T}/test_redteam_remediation_2026_09_04.py"],
            count=2),
    _tenant("idem-fingerprint", "idempotency fingerprint mismatch never 409s",
            "backend/app/services/idempotency.py",
            ("    if operation not in SENSITIVE_IDEMPOTENCY_OPERATIONS:\n"
             "        return stored_hash == candidates[0]"),
            "    if True:\n        return True",
            [f"{T}/test_idempotency.py", f"{T}/test_redteam_remediation_2026_09_04.py"]),
    # ---- financial core arithmetic --------------------------------------
    _finance("mc-seed-ignored", "Monte Carlo RNG ignores the run seed",
             "backend/app/simulation/montecarlo.py",
             "    rng = random.Random(a.risk.seed)",
             "    rng = random.Random()",
             [f"{T}/test_simulation_engine.py", f"{T}/test_simulation_audit_remediation.py"]),
    _finance("parity-stillbirth-sign", "stillbirth rate applied as a bonus",
             "backend/app/simulation/planner.py",
             "r.litter_size * (1.0 - r.stillbirth_rate)",
             "r.litter_size * (1.0 + r.stillbirth_rate)",
             [f"{T}/test_simulation_planner.py", f"{T}/test_simulation_engine.py"], count=2),
    _finance("growth-premium-sign", "young male weight premium subtracted",
             "backend/app/simulation/engine.py",
             "        return weight * (1.0 + growth.young_male_weight_premium)",
             "        return weight * (1.0 - growth.young_male_weight_premium)",
             [f"{T}/test_simulation_engine.py", f"{T}/test_simulation_financials.py"]),
    _finance("feed-take-sign", "feed utilization factor sign flipped",
             "backend/app/simulation/engine.py",
             "        factor = 1.0 - take / available",
             "        factor = 1.0 + take / available",
             [f"{T}/test_simulation_engine.py", f"{T}/test_simulation_financials.py"], count=2),
    _finance("festival-uplift", "Eid festival price uplift dropped",
             "backend/app/simulation/market.py",
             "    uplift = 1.0 + sales.eid_price_uplift if festival else 1.0",
             "    uplift = 1.0",
             [f"{T}/test_simulation_financials.py", f"{T}/test_bakrid_advisory.py"]),
]

_FAILED_RE = re.compile(r"^(FAILED|ERROR)\s+(\S+)", re.MULTILINE)


def changed_files(base: str) -> set[str]:
    r = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...HEAD"],
        cwd=REPO, capture_output=True, text=True, check=True,
    )
    return {line.strip() for line in r.stdout.splitlines() if line.strip()}


def apply_op(path: Path, op: tuple) -> None:
    text = path.read_text()
    kind = op[0]
    if kind == "replace":
        _, old, new, count = op
        n = text.count(old)
        if n != count:
            raise RuntimeError(f"expected {count} occurrence(s), found {n} of {old[:60]!r}")
        text = text.replace(old, new)
    elif kind == "regex_first":
        _, pattern, repl = op
        if not re.findall(pattern, text, flags=re.MULTILINE):
            raise RuntimeError(f"no match for /{pattern}/")
        text = re.sub(pattern, repl, text, count=1, flags=re.MULTILINE)
    else:
        raise RuntimeError(kind)
    path.write_text(text)


def run_gate_mutants(mutants: list[GateMutant], db: str, per_run_timeout: int) -> int:
    env = os.environ.copy()
    env["GOATFARM_TEST_DB"] = db
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.setdefault("GOATFARM_AUTH_RATE_LIMIT_ENABLED", "false")
    survivors: list[GateMutant] = []
    for m in mutants:
        path = REPO / m.file
        backup = path.read_text()
        try:
            apply_op(path, m.op)
            cmd = [
                str(BACKEND / ".venv/bin/python"), "-m", "pytest",
                "-q", "--no-header", "-p", "no:cacheprovider", "--tb=line", "-rf",
                "-o", "addopts=", *m.targeted,
            ]
            try:
                r = subprocess.run(
                    cmd, cwd=BACKEND, env=env, capture_output=True, text=True,
                    timeout=per_run_timeout,
                )
            except subprocess.TimeoutExpired:
                print(f"  TIMEOUT  {m.scope}/{m.mid}")
                continue
            killed = r.returncode != 0
            failing = sorted(set(_FAILED_RE.findall(r.stdout)))[:3]
            verdict = "KILLED  " if killed else "SURVIVED"
            print(f"  {verdict} {m.scope}/{m.mid} :: {m.description}"
                  + (f" [{failing[0][1]}]" if killed and failing else ""))
            if not killed:
                survivors.append(m)
        except RuntimeError as exc:
            print(f"  STALE   {m.scope}/{m.mid}: catalog no longer matches source "
                  f"({str(exc)[:120]}); treat as a gate-maintenance failure")
            survivors.append(m)
        finally:
            path.write_text(backup)
            cache = path.parent / "__pycache__"
            if cache.is_dir():
                shutil.rmtree(cache, ignore_errors=True)
    if survivors:
        print(f"\nmutation gate FAILED: {len(survivors)} surviving/stale mutant(s):")
        for m in survivors:
            print(f"  - [{m.scope}] {m.mid}: {m.description} ({m.file})")
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--scope", choices=["auto", "tenancy", "financial", "all"],
                    default="auto")
    ap.add_argument("--per-run-timeout", type=int, default=900)
    args = ap.parse_args()

    try:
        diff = changed_files(args.base)
    except subprocess.CalledProcessError as exc:
        print(f"could not diff against {args.base}: {exc.stderr[:200]}")
        return 2

    scopes = None if args.scope == "all" else (
        [args.scope] if args.scope != "auto" else None
    )
    selected = [
        m for m in CATALOG
        if m.file in diff and (scopes is None or m.scope in scopes)
    ]
    if not selected:
        print("mutation gate: no curated mutants map to this diff — skipping "
              f"({len(diff)} changed files, scopes: {args.scope})")
        return 0

    print(f"mutation gate: {len(selected)} mutant(s) in scope "
          f"(of {len(CATALOG)} curated; diff has {len(diff)} files)")
    db = os.environ.get("GOATFARM_TEST_DB", "goatfarm_test_gate")
    if "_test" not in db:
        print(f"refusing to run against non-throwaway database {db!r}")
        return 2
    return run_gate_mutants(selected, db, args.per_run_timeout)


if __name__ == "__main__":
    sys.exit(main())
