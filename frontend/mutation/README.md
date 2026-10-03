Frontend mutation reports checked in before the 2026-10-03 harness repair are historical, untrusted measurements. No new full campaign score is claimed by this repair.

From `frontend`, prepare current inputs in this order:

```sh
node mutation/mutate_gen.mjs
node mutation/mutate_cover.mjs
node mutation/mutate_run.mjs --full --dry-run
node mutation/mutate_run.mjs --smoke
node mutation/mutate_run.mjs --full
node mutation/mutate_report.mjs --dry-run
```

Generation records the original text of each edit. The runner and Vite transform both reject stale source fingerprints and edit spans; the runner validates syntax and jump/await/yield context in the complete changed file. Tests always run against an in-memory edit, never a rewritten source file. Keep source, tests, harness/configuration, dependency lock and patches stable during collection/execution; changes invalidate results.

Every exact test selection must first pass a clean baseline under the same configuration and timeout. Structured Vitest receipts distinguish assertion kills from invalid mutants, hook/collection/runtime infrastructure failures, and inconclusive timeouts. Resume folds the latest compatible record by campaign and mutant fingerprint, retrying errors, timeouts and uncovered results. A result records the exact selection digest, baseline, policy, provenance and unique run/attempt IDs. `--full` runs every coverer. Normal rounds sample 1/8/25 files, explicitly mark sampled outcomes, and exclude them from the complete-selection score. Explicit `--files` selections are reported separately.

Coverage stores contributions per test with source/test/harness hashes. `--only pattern` replaces those tests' old contributions, reconciles deleted tests and invalidates shifted source positions. All requested coverage runs must pass before an atomic publish; failure leaves the old map byte-identical. Intentionally partial maps have `complete: false` and cannot run a campaign until refreshed completely. Concurrent mutation runs use independent process-local transforms; coverage collection should have one writer at a time.

`mutate_reverify.mjs --write` uses the shared guarded baseline runner and every coverer, including modules with no dedicated tests. Without `--write`, or with `--dry-run`, it only prints a plan. Reports fold the latest compatible campaign, exclude sampled/explicit/error/timeout/invalid results from the full score, and publish `measurement-current.json`; they preserve historical reports. Use `--campaign-id` to select a current compatible campaign explicitly.

```sh
node --test mutation/mutate_harness.test.mjs
```

These contracts launch real installed Vitest/V8 against temporary fixture projects, proving concurrent transform isolation, classification, clean baselines, freshness/resume, partial coverage replacement and failure preservation, full selection, reverification and smoke exit behavior. They do not run a shared-checkout campaign or certify historical results.
