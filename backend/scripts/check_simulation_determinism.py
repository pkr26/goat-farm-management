#!/usr/bin/env python3
"""Simulation determinism contract (campaign 9, step 1).

Monte Carlo and the daily-ops engine are only mutation-testable under fixed
seeds. This script is the executable form of that contract:

  * same seed  -> byte-identical JSON projection (two independent runs)
  * new seed   -> a different projection (the seed actually drives the draws)

It runs against the live simulation package (DB-free, like the --mutation-pure
profile). Exit 0 = contract holds; exit 1 = the engine is nondeterministic and
every seeded mutant verdict recorded after that point is untrustworthy.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.simulation.assumptions import (
    HerdAssumptions,
    MetaAssumptions,
    SimulationAssumptions,
)
from app.simulation.engine import run_simulation


def _digest(seed: int) -> str:
    a = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=24),
        herd=HerdAssumptions(
            does=20, bucks=2, auto_purchase_bucks=False, foundation_flock_state="open"
        ),
    )
    a.risk.seed = seed
    result = run_simulation(a, with_break_even=False)
    payload = json.dumps(
        result.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def main() -> int:
    digest_a1 = _digest(20260919)
    digest_a2 = _digest(20260919)
    digest_b = _digest(987654321)

    ok = True
    if digest_a1 != digest_a2:
        print(
            f"FAIL determinism: same seed produced different projections\n"
            f"  run1={digest_a1}\n  run2={digest_a2}"
        )
        ok = False
    else:
        print(f"OK same-seed reproducible: {digest_a1[:16]}…")

    if digest_a1 == digest_b:
        print(
            "FAIL seed sensitivity: a different seed produced the identical "
            "projection — the seed is not driving the draws"
        )
        ok = False
    else:
        print(f"OK different seed diverges: {digest_b[:16]}…")

    if not ok:
        print(
            "\nThe determinism contract is BROKEN — no seeded mutant verdict "
            "can be trusted until this is fixed."
        )
        return 1
    print("\nDeterminism contract holds — seeded mutation verdicts are meaningful.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
