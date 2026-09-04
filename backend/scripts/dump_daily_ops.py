"""Dump Buckets & Tasks daily-operations ledgers for manual verification.

Runs canned goat-herd scenarios through the pure daily_ops engine and writes
one Markdown audit ledger per scenario, byte-identical to what the API's
``include_ledger`` flag returns for the same input.

Run from the repo root:

    backend/.venv/bin/python backend/scripts/dump_daily_ops.py [out-dir]

Default out-dir: audit_reports/2026-09-03/daily_ops — pinned to the commit
date (not <today>) so regeneration is byte-stable across days; pass an
explicit out-dir to write elsewhere.
"""

import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.simulation.daily_ops import (  # noqa: E402
    AnimalStartSpec,
    DailyOpsInput,
    DailyOpsParams,
    build_daily_ledger,
    run_daily_ops,
)

SCENARIOS: dict[str, DailyOpsInput] = {
    # The engine tests' toy herd, with the stochastic knobs forced so every
    # date matches the hand-derived golden trace (scan d33, kidding d151,
    # weaning d211, flush + re-breed d241).
    "toy_herd_full_cycle": DailyOpsInput(
        start_date=date(2026, 9, 3),
        horizon_days=280,
        seed=1,
        animals=[
            AnimalStartSpec(tag="D1", sex="F", bucket="BREEDING", age_months=18),
            AnimalStartSpec(tag="B1", sex="M", bucket="BREEDING", age_months=24),
        ],
        params=DailyOpsParams(
            conception_rate=1.0,
            litter_size_mean=1.0,
            female_fraction_at_birth=1.0,
            stillbirth_rate=0.0,
            abortion_rate=0.0,
            kid_pre_weaning_mortality=0.0,
            adult_annual_mortality=0.0,
        ),
    ),
    # A realistic standing herd under default rates: open does, a late-
    # pregnancy doe, a mid-quarantine arrival and growing kids.
    "standing_herd_default_rates": DailyOpsInput(
        start_date=date(2026, 9, 3),
        horizon_days=180,
        seed=7,
        animals=[
            *[
                AnimalStartSpec(tag=f"D{i}", sex="F", bucket="BREEDING", age_months=13 + i)
                for i in range(1, 11)
            ],
            AnimalStartSpec(tag="B1", sex="M", bucket="BREEDING", age_months=24),
            AnimalStartSpec(
                tag="P1", sex="F", bucket="PREGNANCY_LATE", age_months=30, bred_days_ago=120
            ),
            AnimalStartSpec(
                tag="Q1", sex="F", bucket="QUARANTINE", age_months=14, days_in_bucket=10
            ),
            AnimalStartSpec(tag="MK1", sex="M", bucket="MALE_KIDS", age_months=7),
            AnimalStartSpec(tag="MK2", sex="M", bucket="MALE_KIDS", age_months=6),
            AnimalStartSpec(tag="FK1", sex="F", bucket="FEMALE_KIDS", age_months=9),
            AnimalStartSpec(
                tag="R1",
                sex="F",
                bucket="RECOVERY",
                age_months=28,
                days_in_bucket=20,
                dependent_kid=True,
            ),
        ],
    ),
    # Fresh arrivals only: the 45-day quarantine protocol from day 1.
    "quarantine_arrival": DailyOpsInput(
        start_date=date(2026, 9, 3),
        horizon_days=60,
        seed=3,
        animals=[
            AnimalStartSpec(
                tag=f"Q{i}", sex="F", bucket="QUARANTINE", age_months=13 + i, days_in_bucket=0
            )
            for i in range(1, 6)
        ],
        params=DailyOpsParams(adult_annual_mortality=0.0),
    ),
}


def main() -> None:
    default_out = REPO_ROOT / "audit_reports" / "2026-09-03" / "daily_ops"
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else default_out
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in SCENARIOS.items():
        result = run_daily_ops(payload)
        ledger = build_daily_ledger(result)
        path = out_dir / f"{name}.md"
        path.write_text(ledger, encoding="utf-8")
        print(
            f"{name}: {result.horizon_days} days, {result.head_start} head start, "
            f"{result.totals.moves} moves, {result.totals.tasks_by_category.get('FEED', 0)} "
            f"feed duties -> {path}"
        )


if __name__ == "__main__":
    main()
