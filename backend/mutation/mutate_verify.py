"""Re-verify sampled survivors: confirms verdict stability (no flaky kills
hiding as survivors, no corruption-window artifacts)."""

from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND / "mutation"))
from mutate_run import Runner  # noqa: E402


def main() -> None:
    sample = json.loads((BACKEND / "mutation" / "verify_sample.json").read_text())
    runner = Runner(workers=8, max_seconds=None)
    runner.results_path = BACKEND / "mutation" / "verify_results.jsonl"
    verdicts = collections.Counter()
    for m in sample:
        rec = runner.execute(m, worker=9)
        verdicts[rec["status"]] += 1
        with open(runner.results_path, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
        print(rec["status"], m["file"], m["line"], m["kind"], flush=True)
    print(dict(verdicts))


if __name__ == "__main__":
    main()
