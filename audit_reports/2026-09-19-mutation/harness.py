#!/usr/bin/env python3
"""Surgical mutation-testing harness for the goat-farm backend (v2, worktrees).

Each worker owns a private `git worktree` plus its own venv and throwaway
PostgreSQL database, so mutants can never contaminate another worker's
baseline or test run (v1 shared one working tree and cross-mutant
contamination produced false baseline failures and unreliable verdicts).

Stages per mutant:
  1. preflight   — each unique targeted-test selection runs once per worktree
                   on the clean tree; a failing selection marks the mutant
                   INCONCLUSIVE_BASELINE (rerunning the campaign retries it).
  2. targeted    — apply patch, run the mutant's targeted test files.
                   Failure => KILLED_TARGETED.
  3. full        — survivors run the whole suite in the same worktree.
                   Failure => KILLED_FULL; success => SURVIVED.

Files are restored with `git restore` inside the worktree and the module's
__pycache__ is purged after every restore (a mutated same-size .pyc whose
recorded mtime matches the restored source would otherwise execute as a
phantom mutant — this bit v0 of the harness).

Usage:
  .venv/bin/python harness.py --campaign c1 --workers 4
  .venv/bin/python harness.py --campaign c1 --dry        # patch validation only
  kill -USR1 <pid>                                        # dump all thread stacks
"""

from __future__ import annotations

import argparse
import ast
import faulthandler
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BACKEND = REPO / "backend"
PRIMARY_PY = BACKEND / ".venv/bin/python"
RESULTS = HERE / "results"
WT_ROOT = REPO / ".mutation-wt"
RESULTS.mkdir(exist_ok=True)

TARGETED_TIMEOUT = 1200
FULL_TIMEOUT = 3000
SETUP_TIMEOUT = 600

faulthandler.register(signal.SIGUSR1, all_threads=True)

# RLock: main's result loop writes the record while holding the lock and
# then logs through log(), which re-enters — a plain Lock self-deadlocks
# there and froze every campaign launched before this fix.
_lock = threading.RLock()


def log(msg: str) -> None:
    with _lock:
        print(msg, flush=True)


@dataclass
class Mutant:
    campaign: str
    mid: str
    description: str
    patches: dict[str, list[dict]] = field(default_factory=dict)
    targeted: list[str] = field(default_factory=list)
    full_check: bool = True

    @property
    def uid(self) -> str:
        return f"{self.campaign}:{self.mid}"


# ---------------------------------------------------------------- patches --

def apply_patch(op: dict, content: str) -> str:
    if op["op"] == "replace":
        old, new = op["old"], op["new"]
        n = content.count(old)
        expect = op.get("count", 1)
        if n != expect:
            raise RuntimeError(f"expected {expect} occurrence(s), found {n} of {old!r}")
        return content.replace(old, new)
    if op["op"] == "regex":
        pattern, repl = op["pattern"], op["repl"]
        matches = re.findall(pattern, content, flags=re.MULTILINE)
        mode = op.get("mode", "first")
        if mode == "first":
            if len(matches) < op.get("at_least", 1):
                raise RuntimeError(
                    f"expected >= {op.get('at_least', 1)} matches of /{pattern}/, "
                    f"found {len(matches)}"
                )
            return re.sub(pattern, repl, content, count=1, flags=re.MULTILINE)
        if mode == "all":
            expect = op.get("count")
            if expect is not None and len(matches) != expect:
                raise RuntimeError(
                    f"expected exactly {expect} matches of /{pattern}/, found {len(matches)}"
                )
            return re.sub(pattern, repl, content, flags=re.MULTILINE)
        raise RuntimeError(f"unknown regex mode {mode}")
    raise RuntimeError(f"unknown op kind {op['op']}")


# --------------------------------------------------------------- worktree --

class Worktree:
    """A worker's isolated checkout: repo files, venv, and database."""

    def __init__(self, index: int) -> None:
        self.index = index
        self.path = WT_ROOT / f"w{index}"
        self.backend = self.path / "backend"
        self.py = self.backend / ".venv/bin/python"
        self.db = f"goatfarm_test_wt{index}"

    def setup(self) -> None:
        if (REPO / ".git" / "worktrees").exists() or True:
            subprocess.run(
                ["git", "worktree", "prune"], cwd=REPO, capture_output=True
            )
        if self.path.exists():
            self.teardown()
        r = subprocess.run(
            ["git", "worktree", "add", "--detach", str(self.path), "HEAD"],
            cwd=REPO, capture_output=True, text=True, timeout=SETUP_TIMEOUT,
        )
        if r.returncode != 0:
            raise RuntimeError(f"worktree add failed: {r.stderr[:300]}")
        venv = subprocess.run(
            ["uv", "venv", str(self.backend / ".venv")],
            cwd=self.backend, capture_output=True, text=True, timeout=SETUP_TIMEOUT,
        )
        if venv.returncode != 0:
            raise RuntimeError(f"uv venv failed: {venv.stderr[:300]}")
        sync = subprocess.run(
            ["uv", "pip", "install", "--python", str(self.py), "-e", ".[dev]"],
            cwd=self.backend, capture_output=True, text=True, timeout=SETUP_TIMEOUT,
        )
        if sync.returncode != 0:
            raise RuntimeError(f"uv pip install failed: {sync.stderr[-400:]}")

    def teardown(self) -> None:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(self.path)],
            cwd=REPO, capture_output=True, timeout=SETUP_TIMEOUT,
        )

    # -- mutant lifecycle ---------------------------------------------------

    def apply(self, m: Mutant) -> None:
        for rel, ops in m.patches.items():
            path = self.path / rel
            text = path.read_text()
            for op in ops:
                text = apply_patch(op, text)
            path.write_text(text)

    def restore(self, m: Mutant) -> None:
        for rel in m.patches:
            subprocess.run(
                ["git", "restore", "--", rel],
                cwd=self.path, check=True, capture_output=True,
            )
            src = self.path / rel
            cache = src.parent / "__pycache__"
            if cache.is_dir():
                shutil.rmtree(cache, ignore_errors=True)

    def compile_check(self, m: Mutant) -> None:
        for rel in m.patches:
            try:
                ast.parse((self.path / rel).read_text())
            except SyntaxError as exc:
                raise RuntimeError(f"mutant does not parse ({rel}): {exc}") from exc

    def run_pytest(self, test_files: list[str], timeout: int) -> dict:
        env = os.environ.copy()
        env["GOATFARM_TEST_DB"] = self.db
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env.setdefault("GOATFARM_AUTH_RATE_LIMIT_ENABLED", "false")
        rel = [t.removeprefix("backend/") for t in test_files]
        cmd = [
            str(self.py), "-m", "pytest",
            "-q", "--no-header", "-p", "no:cacheprovider", "--tb=line", "-rf",
            "-o", "addopts=",
            *rel,
        ]
        started = time.monotonic()
        try:
            r = subprocess.run(
                cmd, cwd=self.backend, env=env, capture_output=True, text=True,
                timeout=timeout, start_new_session=True,
            )
        except subprocess.TimeoutExpired as exc:
            try:
                os.killpg(os.getpgid(exc.pid or 0), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, TypeError):
                pass
            return {"outcome": "timeout", "duration": timeout, "failing": [], "tail": ""}
        duration = round(time.monotonic() - started, 1)
        out = r.stdout + r.stderr
        failing = sorted({f"{k} {n}" for k, n in _FAILED_RE.findall(out)})[:15]
        return {
            "outcome": "failed" if r.returncode != 0 else "passed",
            "duration": duration,
            "failing": failing,
            "tail": out[-1200:],
            "output": out[-200000:],
        }


_FAILED_RE = re.compile(r"^(FAILED|ERROR)\s+(\S+)", re.MULTILINE)
_INFRA_RE = re.compile(
    r"too many clients|could not connect|connection refused|OperationalError|"
    r"ConnectionDoesNotExistError|sorry, too many|database .* is being accessed|"
    r"InvalidRequestError: Could not refresh|could not serialize access|"
    r"deadlock detected|InternalError|asyncpg.exceptions",
    re.IGNORECASE,
)


def run_pytest_retry(wt: Worktree, test_files: list[str], timeout: int) -> dict:
    """Retry once when the failure smells like DB-infra contention rather
    than an assertion; a genuine kill repeats on the retry."""
    res = wt.run_pytest(test_files, timeout)
    if res["outcome"] == "failed" and _INFRA_RE.search(res.get("output", "")):
        time.sleep(15)
        res2 = wt.run_pytest(test_files, timeout)
        res2["retried_infra"] = True
        return res2
    return res


# ----------------------------------------------------------- mutant runner --

_baselines: dict[tuple[str, ...], bool] = {}
_baseline_lock = threading.Lock()


def baseline_ok(wt: Worktree, test_files: list[str]) -> bool:
    key = tuple(test_files)
    with _baseline_lock:
        if key in _baselines:
            return _baselines[key]
    if not test_files:
        return True
    log(f"  .. w{wt.index} preflight baseline: {Path(test_files[0]).name} (+{len(test_files)-1})")
    res = run_pytest_retry(wt, test_files, TARGETED_TIMEOUT)
    ok = res["outcome"] == "passed"
    if not ok:
        # Heavy selections occasionally fail their preflight only under the
        # 3-worker load (isolated DBs; reproduced passing serially in seconds).
        # Retry once after a pause: a genuine baseline failure repeats, a
        # load flake clears, and the mutant still gets a trustworthy verdict.
        time.sleep(30)
        log(f"  .. w{wt.index} baseline retry after flake: {Path(test_files[0]).name}")
        res = run_pytest_retry(wt, test_files, TARGETED_TIMEOUT)
        ok = res["outcome"] == "passed"
    with _baseline_lock:
        _baselines[key] = ok
    if not ok:
        log(f"  .. w{wt.index} BASELINE FAILS {Path(test_files[0]).name}: {res['failing'][:2]}")
        dump = RESULTS / f"baseline_fail_w{wt.index}_{Path(test_files[0]).stem}.log"
        dump.write_text(res.get("output", "")[-400000:])
    return ok


def run_mutant(m: Mutant, wt: Worktree) -> dict:
    record: dict = {
        "campaign": m.campaign, "id": m.mid, "description": m.description,
        "files": sorted(m.patches), "targeted": m.targeted,
    }
    # Patch validation first (applies then restores immediately on failure).
    try:
        wt.apply(m)
        wt.compile_check(m)
    except RuntimeError as exc:
        wt.restore(m)
        record["verdict"] = "PATCH_ERROR"
        record["error"] = str(exc)[:500]
        return record
    try:
        wt.restore(m)  # preflight the selection WITHOUT the mutant applied
        if not baseline_ok(wt, m.targeted):
            record["verdict"] = "INCONCLUSIVE_BASELINE"
            return record
        wt.apply(m)
        if m.targeted:
            log(f"  .. w{wt.index} {m.uid}: targeted stage ({len(m.targeted)} files)")
            res1 = run_pytest_retry(wt, m.targeted, TARGETED_TIMEOUT)
            record["targeted_result"] = {k: res1[k] for k in ("outcome", "duration", "failing")}
            record["targeted_infra_retry"] = bool(res1.get("retried_infra"))
            if res1["outcome"] == "timeout":
                record["verdict"] = "TIMEOUT_TARGETED"
                return record
            if res1["outcome"] != "passed":
                record["verdict"] = "KILLED_TARGETED"
                record["failing_tests"] = res1["failing"]
                return record
        if m.full_check:
            log(f"  .. w{wt.index} {m.uid}: FULL suite stage")
            res2 = run_pytest_retry(wt, ["backend/tests/"], FULL_TIMEOUT)
            record["full_result"] = {k: res2[k] for k in ("outcome", "duration", "failing")}
            record["full_infra_retry"] = bool(res2.get("retried_infra"))
            if res2["outcome"] == "timeout":
                record["verdict"] = "TIMEOUT_FULL"
                return record
            if res2["outcome"] != "passed":
                record["verdict"] = "KILLED_FULL"
                record["failing_tests"] = res2["failing"]
                return record
            record["verdict"] = "SURVIVED"
            return record
        record["verdict"] = "SURVIVED_TARGETED_ONLY"
        return record
    finally:
        wt.restore(m)


# -------------------------------------------------------------------- main --

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--campaign", required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--only", default=None)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    sys.path.insert(0, str(HERE))
    import campaigns as campaigns_mod

    mutants = campaigns_mod.campaign_mutants(args.campaign)
    if args.only:
        wanted = set(args.only.split(","))
        mutants = [m for m in mutants if m.mid in wanted]
    if args.list:
        for m in mutants:
            print(f"{m.uid:60s} {m.description}")
        return

    if args.dry:
        for m in mutants:
            try:
                for rel, ops in m.patches.items():
                    path = REPO / rel
                    text = path.read_text()
                    for op in ops:
                        text = apply_patch(op, text)
                    path.write_text(text)
                    try:
                        ast.parse(path.read_text())
                    finally:
                        subprocess.run(
                            ["git", "restore", "--", rel], cwd=REPO, capture_output=True
                        )
                        cache = path.parent / "__pycache__"
                        if cache.is_dir():
                            shutil.rmtree(cache, ignore_errors=True)
                print(f"OK    {m.uid}")
            except RuntimeError as exc:
                print(f"BAD   {m.uid}: {str(exc)[:200]}")
        return

    out_path = RESULTS / "all.jsonl"
    done: set[str] = set()
    if out_path.exists():
        for line in out_path.read_text().splitlines():
            try:
                rec = json.loads(line)
                if rec.get("verdict") not in ("PATCH_ERROR", "INCONCLUSIVE_BASELINE"):
                    done.add(f"{rec['campaign']}:{rec['id']}")
            except json.JSONDecodeError:
                continue
    todo = [m for m in mutants if m.uid not in done]
    log(f"campaign={args.campaign} mutants={len(mutants)} todo={len(todo)} workers={args.workers}")

    # Stale .pyc from an earlier mutated compile would execute as a phantom
    # mutant; start from a guaranteed-clean bytecode state.
    for cache in BACKEND.glob("**/__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)

    # One harness instance owns .mutation-wt exclusively: two concurrent
    # campaigns would reuse the same worktree indexes (same directory AND the
    # same throwaway database) and contaminate each other's runs.
    campaign_lock = RESULTS / "campaign.lock"
    if campaign_lock.exists():
        try:
            other = int(campaign_lock.read_text().strip())
            os.kill(other, 0)
            raise SystemExit(
                f"another harness (pid {other}) owns the worktrees; "
                "wait for it to finish or remove results/campaign.lock"
            )
        except (ProcessLookupError, ValueError):
            pass  # stale lock from a dead run
    campaign_lock.write_text(str(os.getpid()))

    worktrees: list[Worktree] = []
    n = min(args.workers, max(1, len(todo)))
    try:
        for i in range(n):
            wt = Worktree(i)
            log(f"setting up worktree w{i} (venv + editable install)...")
            wt.setup()
            worktrees.append(wt)

        started = time.monotonic()
        counter = 0
        with ThreadPoolExecutor(max_workers=n) as pool:
            futures: dict = {}
            for i, m in enumerate(todo):
                futures[pool.submit(run_mutant, m, worktrees[i % n])] = m
            for fut in futures:
                m = futures[fut]
                try:
                    rec = fut.result()
                except Exception as exc:  # noqa: BLE001
                    rec = {"campaign": m.campaign, "id": m.mid,
                           "verdict": "HARNESS_ERROR", "error": repr(exc)[:500]}
                    for wt in worktrees:
                        subprocess.run(
                            ["git", "-C", str(wt.path), "restore", "--", "."],
                            capture_output=True,
                        )
                with _lock:
                    with out_path.open("a") as fh:
                        fh.write(json.dumps(rec) + "\n")
                    counter += 1
                    v = rec.get("verdict", "?")
                    extra = ""
                    if rec.get("failing_tests"):
                        extra = " | " + ", ".join(rec["failing_tests"][:2])
                    log(f"[{counter}/{len(todo)}] {m.uid}: {v}{extra}")
        log(f"campaign {args.campaign} finished in {round(time.monotonic() - started, 1)}s")
    finally:
        for wt in worktrees:
            try:
                wt.teardown()
            except Exception:  # noqa: BLE001
                pass
        try:
            campaign_lock.unlink()
        except (OSError, NameError, UnboundLocalError):
            pass
    summarize(args.campaign)


def summarize(campaign: str) -> None:
    path = RESULTS / "all.jsonl"
    rows = []
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("campaign") == campaign:
                rows.append(rec)
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    print(f"\n=== {campaign} summary ===")
    for v, c in sorted(counts.items()):
        print(f"  {v}: {c}")
    print("  survivors / timeouts:")
    for r in rows:
        if r["verdict"].startswith("SURVIVED") or r["verdict"].startswith("TIMEOUT"):
            print(f"    {r['id']}: {r['description']}")


if __name__ == "__main__":
    main()
