# Backend mutation measurements

Historical `manifest.json`, `results.jsonl` and `report.md` remain historical
artifacts. Their percentages are untrusted: they lack passing-selection
baselines, isolated execution and compatible campaign receipts. The corrected
report's dry run reports the current checkout as unmeasured until a fresh
campaign exists.

For a new campaign on a stable checkout:

1. Generate a manifest with `python mutation/mutate_gen.py`. Each edit carries
   the original source SHA-256 and exact statement bytes. Validation compiles
   the complete edited file, preserving return/await/yield and loop context.
2. Collect a passing isolated coverage baseline with
   `python mutation/mutate_cover.py`. Collection snapshots the checkout, uses
   a unique disposable test database and publishes coverage plus source/test/
   harness/lock provenance only after the clean suite passes. Changed inputs
   or an interrupted publication invalidate the map. This is a full suite;
   allow the usual integration-suite runtime.
3. Preview with `python mutation/mutate_run.py --dry-run`, then execute with
   `python mutation/mutate_run.py --full --workers 2`. Full mode runs every
   selected coverer. Default mode samples and records that limit explicitly.
4. Preview compatible results with `python mutation/mutate_report.py --dry-run`.
   Generate a report only when ready to record the fresh campaign.

Each attempt gets a private source snapshot, process session and database
namespace. The parent reaps that exact database after process exit, including
timeouts where pytest cannot run fixture teardown. Cleanup allows PostgreSQL
45 seconds to finish an in-progress checkpoint; a cleanup failure remains an
infrastructure result and cannot count as a kill. Neither the runner nor
verification helpers rewrite live app files.
The exact selection must pass unmutated under identical resource limits before
its mutant runs. Structured pytest receipts distinguish assertion failures
from setup, collection, usage, internal, no-test and transport errors. Only
assertion failures become `KILLED`; other failures are `INFRA_ERROR`, invalid
edits are `INVALID`, and every timeout is `INCONCLUSIVE_TIMEOUT`.

A campaign receipt binds manifest, source, tests, dependency locks, coverage,
coverage provenance and harness configuration, plus executed migration/scripts
and repository contract/deployment inputs read by the tests. The runner captures
manifest, coverage and coverage-provenance bytes once, fingerprints those bytes,
and parses the captured coverage. Replacing and restoring live artifacts during
execution cannot change its test selection. Every mutation must exactly match
the captured manifest; the snapshot uses that manifest and the execution mode
is fixed when the campaign starts. Coverage publication hashes the bytes from
its passing baseline, so a concurrent publication cannot receive that baseline's
provenance. Reports parse and fingerprint the same captured manifest bytes.
Resume accepts compatible terminal results only; errors, uncovered results and
timeouts are retried.
Reports fold the latest compatible attempt per mutant, show incomplete states,
and exclude sampled/module-fallback selections and inconclusive states from
the complete-selection score. Review equivalence separately with evidence.

Verification receipts append as history. Extremes verification folds only its
own run and target IDs; it never reinterprets another run's timeout. All
execution drivers support a preview that starts no runner, process or write.
The final-pass preview is covered by a byte-identical artifact regression.
