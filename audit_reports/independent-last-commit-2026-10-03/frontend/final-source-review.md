# Final frontend source peer review and FM claim map

The final peer review rechecked the five application production diffs, their caller/callee session and editor ownership, committed-outbox handling, and the expanded mutation input manifest. No further application defect was confirmed after the nine application corrections documented in `summary.md`. No application, public asset, E2E or frontend unit-test edit occurred during this follow-up. The root agent reported the final 5,281-case frontend coverage run, production build and 74 Chromium cases passing; this subagent did not repeat those full application gates.

The mutation review confirmed one additional correctness defect: an active campaign could consume a replacement manifest edit while assigning its outcome to the previously captured mutant identity. A real isolated Vitest fixture captured a no-op `1 -> 1` in its campaign, replaced the live same-ID edit with `1 -> 2`, and returned `KILLED` with a clean baseline while retaining the original no-op digest. `active-campaign-before.json` retains this exact observed failure without credentials. Live coverage-map changes were also absent from active-attempt freshness checks.

The correction now checks manifest, coverage and execution input hashes at campaign entry, after the clean baseline before applying a mutation, and before accepting its result. Drift is an infrastructure outcome and remains retryable. The requested parent mutant must match the edit read from the manifest whose hash is pinned to the campaign. The runner passes the expected manifest hash into the child process, and the transform checks that hash against the exact bytes it then parses. This also covers a replacement and restoration between parent checks: consuming the temporary replacement fails its child hash check. Campaign creation and reporting hash the same bytes they parse, removing a separate read/parse/hash race. Selection comes from the coverage bytes captured with the campaign fingerprint, rather than rereading a replacement coverage map for execution.

## FM claim coverage

| Claim | Independent final assessment | Source / meaningful evidence |
| --- | --- | --- |
| FM-01 | Verified clean exact-selection baselines and structured assertion/invalid/infrastructure/timeout classification. A failure during setup, collection, unhandled execution or a missing receipt cannot become an assertion kill. | `mutate_run.mjs`, `vitest_receipt.mjs`; real Vitest fixtures cover assertion failures, thrown errors, failing setup, collection failures, failed baseline, timeout and empty/nonexistent selections. |
| FM-02 | Verified source/test/config/lock/patch/edit/campaign bindings, compatible latest-attempt resume and retryable infrastructure outcomes after correcting the active manifest/coverage race. Support provenance captures service worker/assets, Next config, scripts and actual backend/shared/deployment parity inputs while excluding transient bytecode. | `mutate_identity.mjs`, `mutate_run.mjs`, `mutate_transform.mjs`, `mutate_reverify.mjs`, `mutate_report.mjs`; new artifact-drift tests, parent/guard/child pinning checks, real concurrent transform isolation, source drift, reused IDs, resume retry and 41-coverer reverification. `mutation-input-check.json` confirms all fourteen inspected external support paths. |
| FM-03 | Verified contributions are replaced per test, deleted paths removed, shifted source/test/harness changes invalidated, and a requested failed refresh preserves the existing map bytes. Successful publication uses atomic rename. | `mutate_cover.mjs`; real V8 publish/failure fixture plus contribution replacement/deletion/source-position contracts. Concurrent coverage publishers still require the documented single writer. |
| FM-04 | Verified full mode passes every coverer without the historical cap; sampled/explicit outcomes are labeled and excluded from the complete score. Smoke requires a successful exact baseline and repeated actual assertion kills and exits nonzero when a required check fails. | `mutate_run.mjs`, `mutate_report.mjs`; the 1,001-coverer contract, complete-score exclusion, real failed smoke and real successful CLI smoke. The larger selection contract uses an injected runner to observe all arguments; 41-coverer reverification executes real Vitest. |

## Final tool changes and verification

This subagent corrected `frontend/mutation/mutate_run.mjs`, `mutate_transform.mjs`, `mutate_report.mjs`, added four contracts to `mutate_harness.test.mjs`, and documented artifact pinning in `mutation/README.md`. The root agent's input expansion and bytecode exclusion in `mutate_identity.mjs` remain intact.

From `frontend`:

```sh
node --test mutation/mutate_harness.test.mjs
pnpm exec eslint mutation/mutate_identity.mjs mutation/mutate_run.mjs mutation/mutate_transform.mjs mutation/mutate_report.mjs mutation/mutate_harness.test.mjs --max-warnings 0
git diff --check
```

All 22 harness tests passed in 17.0776s, zero failures/cancellations/skips; scoped ESLint and diff checks passed. `final-mutation-harness.log` preserves the complete final isolated harness output, and `final-mutation-receipt.json` binds the receipt to six final mutation source/document hashes. The printed negative smoke result is an intentional passing fixture, not a suite failure. `active-campaign-after.log` is the earlier four-case focused receipt and precedes the final added parent-binding assertion; the complete final log contains that assertion.

These fixes invalidate prior mutation campaign identities because their harness input hashes changed. No fresh complete application mutation campaign or score is claimed. Execution still requires stable source/test/dependency inputs as documented; the harness is not a sandbox against an external process temporarily rewriting and restoring unrelated test/source files during a child run. The specific confirmed manifest replacement race is closed by checking the bytes actually consumed by the transform. Installed dependency contents are represented by package/lock/patch identity and assume installation matches those files. Physical-device, provider, human/domain and deployment limits from `summary.md` remain.
