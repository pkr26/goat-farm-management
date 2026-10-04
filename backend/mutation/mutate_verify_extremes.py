"""Append current-run full-selection verification; timeouts remain inconclusive."""

import argparse
import collections
import json
import os
import sys
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parent.parent
MUTDIR = BACKEND / "mutation"
sys.path.insert(0, str(MUTDIR))
from mutate_final_pass import GAP_FILES  # noqa: E402
from mutate_identity import read_artifact, read_results  # noqa: E402
from mutate_run import Runner  # noqa: E402


def build_runner(phase_timeout: float) -> type[Runner]:
    class VerifyRunner(Runner):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(phase_timeout=phase_timeout, **kwargs)

        def select_tests(self, mutant: dict[str, Any]) -> tuple[list[str], bool]:
            selection, module_level = super().select_tests(mutant)
            return sorted(set(selection) | set(GAP_FILES)), module_level

    return VerifyRunner


def current_attempts(
    records: list[dict[str, Any]], *, run_id: str, target_ids: set[str], campaign_id: str
) -> dict[str, dict[str, Any]]:
    """Never reinterpret another pass's timeout under the current policy."""
    latest = {}
    for record in records:
        if (
            record.get("run_id") == run_id
            and record.get("id") in target_ids
            and record.get("campaign_id") == campaign_id
        ):
            latest[record["id"]] = record
    return latest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--status", choices=["TIMEOUT", "INCONCLUSIVE_TIMEOUT", "SURVIVED"], required=True
    )
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=1200)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    manifest = {
        m["id"]: m
        for m in json.loads(
            read_artifact(
                MUTDIR / "manifest.json", instruction="run python mutation/mutate_gen.py first"
            )
        )
    }
    latest = {r["id"]: r for r in read_results(MUTDIR / "results.jsonl")}
    target_ids = {
        mid for mid, r in latest.items() if r.get("status") == args.status and mid in manifest
    }
    if args.dry_run:
        print(json.dumps({"targets": sorted(target_ids), "full": True, "writes": False}))
        return
    os.environ["MUTATE_FULL_PHASE"] = "1"
    runner = build_runner(args.timeout)(workers=args.workers, max_seconds=None)
    runner.results_path = MUTDIR / "verify_extremes.jsonl"
    try:
        runner.run([manifest[mid] for mid in sorted(target_ids)])
        current = current_attempts(
            read_results(runner.results_path),
            run_id=runner.run_id,
            target_ids=target_ids,
            campaign_id=runner.campaign_id,
        )
        with (MUTDIR / "results.jsonl").open("a") as handle:
            for record in current.values():
                handle.write(json.dumps(record) + "\n")
        print(dict(collections.Counter(r["status"] for r in current.values())))
    finally:
        runner.close()


if __name__ == "__main__":
    main()
