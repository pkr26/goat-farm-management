"""Re-verify sampled survivors: confirms verdict stability (no flaky kills
hiding as survivors, no corruption-window artifacts).

Usage:
    .venv/bin/python mutation/mutate_verify.py [--sample N] [--seed S]

``--sample N`` derives a reproducible survivor sample from the generated
manifest and the latest result per mutant. Without it, an existing
``verify_sample.json`` is required. Both files are local campaign artifacts.
"""

from __future__ import annotations

import argparse
import collections
import contextlib
import json
import random
import sys
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND / "mutation"))
from mutate_identity import read_artifact  # noqa: E402
from mutate_run import Runner  # noqa: E402

DEFAULT_SEED = 20260928


def derive_sample(n: int, seed: int) -> list[dict[str, Any]]:
    """N SURVIVED mutants from results.jsonl, deterministically shuffled."""
    last_status: dict[str, str] = {}
    for line in (
        read_artifact(
            BACKEND / "mutation" / "results.jsonl", instruction="run a mutation campaign first"
        )
        .decode()
        .splitlines()
    ):
        with contextlib.suppress(Exception):
            rec = json.loads(line)
            last_status[rec["id"]] = rec["status"]
    survivors = {mid for mid, status in last_status.items() if status == "SURVIVED"}
    manifest = json.loads(
        read_artifact(
            BACKEND / "mutation" / "manifest.json",
            instruction="run python mutation/mutate_gen.py first",
        )
    )
    pool = sorted((m for m in manifest if m["id"] in survivors), key=lambda m: m["id"])
    random.Random(seed).shuffle(pool)
    return pool[:n]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--sample",
        type=int,
        default=None,
        help="derive verify_sample.json from results.jsonl first (N SURVIVED mutants)",
    )
    ap.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help="shuffle seed for --sample (fixed default: reproducible)",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    sample_path = BACKEND / "mutation" / "verify_sample.json"
    if args.sample is not None:
        derived = derive_sample(args.sample, args.seed)
        if args.dry_run:
            print(json.dumps({"targets": [m["id"] for m in derived], "writes": False}))
            return
        sample_path.write_text(json.dumps(derived, indent=1) + "\n")
        print(f"wrote {sample_path.name}: {len(derived)} survivors (seed {args.seed})")
    sample = json.loads(
        read_artifact(
            sample_path, instruction="run python mutation/mutate_verify.py --sample N first"
        ).decode()
    )
    if args.dry_run:
        print(json.dumps({"targets": [m["id"] for m in sample], "writes": False}))
        return
    runner = Runner(workers=2, max_seconds=None)
    runner.results_path = BACKEND / "mutation" / "verify_results.jsonl"
    verdicts: collections.Counter[str] = collections.Counter()
    for m in sample:
        rec = runner.execute(m, worker=9)
        verdicts[rec["status"]] += 1
        with runner.results_path.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
        print(rec["status"], m["file"], m["line"], m["kind"], flush=True)
    runner.close()
    print(dict(verdicts))


if __name__ == "__main__":
    main()
