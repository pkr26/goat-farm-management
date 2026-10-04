# Frontend mutation testing

Generate campaign inputs from the current checkout. Manifests, coverage maps,
attempt logs and measurements are local outputs and CI artifacts; they are
not committed. [CAMPAIGN.md](../../docs/archive/frontend-mutation/CAMPAIGN.md) and [EQUIVALENTS.md](../../docs/archive/frontend-mutation/EQUIVALENTS.md)
retain historical investigation notes, whose measurements are untrusted.
No current full campaign score is claimed.

`.github/workflows/mutation.yml` now exercises real application mutants on
relevant pull requests and weekly. It rebuilds the complete per-test coverage
map, selects up to 25 sites across changed production files (or a bounded
repository sample when only tests/harness inputs changed), runs every coverer
for each selected site, rejects incomplete/infrastructure outcomes, and
requires an 80% assertion-kill score. Its plan, coverage provenance,
manifest, raw attempts, and report remain available as 30-day artifacts; the
bounded result is deliberately not described as a whole-manifest campaign.

From `frontend`, prepare current inputs in this order:

```sh
node mutation/mutate_gen.mjs
node mutation/mutate_cover.mjs
node mutation/mutate_run.mjs --full --dry-run
node mutation/mutate_run.mjs --smoke
node mutation/mutate_run.mjs --full
node mutation/mutate_report.mjs --dry-run
node mutation/mutate_triage.mjs                  # current complete-selection survivors
```

Generation records the original text of each edit. The runner and Vite transform both reject stale source fingerprints and edit spans; the runner validates syntax and jump/await/yield context in the complete changed file. Tests always run against an in-memory edit, never a rewritten source file. Keep source, tests, harness/configuration, dependency lock and patches stable during collection/execution; changes invalidate results.

Campaign creation and reports hash the same manifest/coverage bytes they parse. Active attempts check those artifact hashes and all execution inputs before a baseline, before applying a mutation, and before accepting its outcome. A manifest or coverage replacement during a run is an infrastructure result, never a credited kill or completed resume entry. The child transform also verifies the pinned manifest hash so replacing an edit between the parent guard and child startup cannot execute another edit under the original identity.

Every exact test selection must first pass a clean baseline under the same configuration and timeout. Provenance includes public assets, scripts, Next configuration, and the backend/contract/deployment files read by parity tests. Structured Vitest receipts distinguish assertion kills from invalid mutants, hook/collection/runtime infrastructure failures, and inconclusive timeouts. Resume folds the latest compatible record by campaign and mutant fingerprint, retrying errors, timeouts and uncovered results. A result records the exact selection digest, baseline, policy, provenance and unique run/attempt IDs. `--full` runs every coverer. Normal rounds sample 1/8/25 files, explicitly mark sampled outcomes, and exclude them from the complete-selection score. Explicit `--files` selections are reported separately.

Coverage stores contributions per test with source/test/harness hashes. `--only pattern` replaces those tests' old contributions, reconciles deleted tests and invalidates shifted source positions. All requested coverage runs must pass before an atomic publish; failure leaves the old map byte-identical. Intentionally partial maps have `complete: false` and cannot run a campaign until refreshed completely. Concurrent mutation runs use independent process-local transforms; coverage collection should have one writer at a time.

`mutate_reverify.mjs --write` uses the shared guarded baseline runner and every coverer, including modules with no dedicated tests. Without `--write`, or with `--dry-run`, it only prints a plan. Reports fold the latest compatible campaign, exclude sampled/explicit/error/timeout/invalid results from the full score, and publish `measurement-current.json`; they preserve historical reports. Use `--campaign-id` to select a current compatible campaign explicitly.

```sh
node --test mutation/mutate_harness.test.mjs
```

These contracts launch real installed Vitest/V8 against temporary fixture projects, proving concurrent transform isolation, classification, clean baselines, freshness/resume, partial coverage replacement and failure preservation, full selection, reverification and smoke exit behavior. They do not run a shared-checkout campaign or certify historical results.
