# Frontend remediation: F26-01 through F26-09

All nine independently audited frontend findings have application fixes and maintained regression coverage. The original audit artifacts were preserved. No application servers, databases, real object stores or paid providers were started or used by this frontend task. This frontend task owns the full frontend coverage receipts; coordinating root/ops runs own production images and real-browser receipts.

| Finding | Implemented behavior | Maintained regression evidence |
|---|---|---|
| F26-01 | HTML includes a per-build marker from Next's compiled config. The service worker retains lazy runtime assets across same-build document refreshes, rotates only changed build identities, and serializes cache membership updates. Initial static imports that finished before worker control are explicitly warmed into CacheStorage. Failed locale loads show bilingual retry/English recovery controls and retry upon reconnection. | `src/test/adversarial/sw-update.test.ts` executes the real service worker, including parallel lazy chunk/font caching, repeated refreshes, offline reads, previous-build retention, third-build pruning, and URL restrictions on initial warmup. `src/lib/i18n/provider-cold-start.test.tsx` resets the shared setup's preloaded catalogs and verifies actual lazy-load failure/retry/recovery. `src/app/worker/layout.test.tsx` checks initial warmup selection. `e2e/worker-offline-regressions.spec.ts` preserves the original prerequisite: clear only Chromium's HTTP cache, retain CacheStorage, then reload offline. |
| F26-02 | The root provider resolves the worker route's Telugu default before storing a fallback or mounting the worker shell. A deliberate stored English preference or server-selected English cookie remains English. | Cold provider regression covers a fresh `/worker/login`, local preference, and cookie preference. The real-browser regression checks a fresh context with no injected preference. |
| F26-03 | `formatDate` interprets offsetless backend datetime timestamps as UTC before converting them to the farm calendar date; date-only and offset-bearing inputs retain their existing semantics. | `src/lib/format.utc.test.ts` checks the concrete 5 Aug result and equivalence with explicit `Z` timestamps (including fractional seconds), executed with `TZ=America/Phoenix`; existing format suites also pass. |
| F26-04 | Finish is blocked until the selected photo is uploaded or explicitly discarded. Synchronous per-walkthrough refs protect selected-file, upload, and finish races; upload controls cannot start new work while submitting. Closing/remounting retains the existing abort/epoch behavior. | Real dialog with MSW-controlled object-store response: one uploaded photo, second selected photo, explicit discard, another slow second upload, attempted Finish while pending (no submit, signal not aborted), successful completion, then exactly one submit. Existing timeout, same-tick duplicate, close/reopen, unmount, and failure tests remain green. |
| F26-05 | Farm switches retain an explicit set of portable module filters/pagination keys and strip record IDs, nested return destinations, free-text record search, and record anchors. Same-farm navigation preserves its full state. | `permission-navigation.test.ts` covers screening image, health task/animal/purchase batch, breeding ultrasound, kidding breeding ID, repeated IDs, preserved filters and same-farm controls; route mapping/path validation/farm-select suites also pass. Previous tests that expected old-farm IDs/anchors to survive were updated to the corrected contract. |
| F26-06 | The owning root language provider commits the pure-helper language default before notifying context consumers, within the same guarded transition. It does not mutate that default during rendering. Async completions retain version/cleanup guards. | Cold provider test records context language, helper default, rendered date, and enum text on the first visible Telugu render and both subsequent switch directions, without requiring an unrelated rerender. |
| F26-07 | The worker shell owns the main landmark; the offline page renders a normal content container. | Offline component under an enclosing main asserts exactly one main. The real-browser test runs all three originally failing axe landmark rules and retains the result attachment. |
| F26-08 | Health viewers do not see intake or retake controls; the management dialog is not mounted without `health.manage`. If permissions are revoked after opening, create/upload/submit 403 responses explain missing role access. | Screening page test verifies view-only quality guidance without intake/retake/export. Parameterized dialog tests exercise a 403 at each of the three mutation steps. Existing manager workflows remain green. |
| F26-09 | Statistics failures show an actionable unavailable message and Retry. Prior successful statistics remain visible with an explicit stale-data warning when refreshing fails; only successful empty history uses empty-history copy. | Actual page with MSW responses covers initial stats outage with working images, successful local retry, stale-data refresh failure, and the existing successful empty result. |

## Verified local commands

From `frontend`, using the unchanged configured Vitest/ESLint/TypeScript settings:

```sh
TZ=America/Phoenix pnpm exec vitest run src/lib/i18n src/lib/format src/lib/permission-navigation src/test/adversarial/sw-update.test.ts src/test/adversarial/config.test.ts src/components/screening-check-dialog.test.tsx 'src/app/(app)/screening/page.test.tsx' src/app/worker src/app/farm-select
pnpm lint
pnpm typecheck
git diff --check
```

- **PASS:** 27 files, 389 tests; 13.60 seconds. [Final affected-suite receipt](affected-suites-verified.log).
- **PASS:** ESLint, zero warnings. [Receipt](lint-final.log).
- **PASS:** TypeScript, exit 0. [Receipt](typecheck-final.log).
- **PASS:** `git diff --check`.

Earlier iteration logs are retained. They include obsolete expectations for retained record anchors/queries, a new test's missing import, and incorrect object-store/request-shape test doubles corrected before the verified run. They are not silently presented as successful runs, nor counted as additional product findings. The 145-test and 91-test intermediate successes overlap the final 389 tests and must not be added to the final count.

The new real-browser tests are prepared under the maintained `frontend/e2e/` suite, but **their execution result belongs to the coordinating browser report**. The service-worker VM tests do not substitute for browser CacheStorage, HTTP cache, Next build metadata or real hydration behavior.

## Boundaries

- No live S3 integration or real camera was used. Upload tests prove frontend request/abort/submit ordering; they do not establish whether an already-transmitted real object-store upload persists after a device disconnect.
- Physical tablet installation/update, OS storage eviction, RF conditions and suspension remain device validation. The Chromium test deliberately models the audited missing-HTTP-cache prerequisite.
- New recovery, discard and statistics copy has matching English/Telugu keys. A native Telugu language review remains outside automated validation.
- Cache retention is bounded by current and previous build memberships, including runtime assets, with compatibility handling for previously cached shells lacking the new marker. Distinct production deployments should serve one consistent built artifact per version, as Next's deployment contract already requires.

## Completed full frontend coverage and coordinating Chromium verification

The final complete run includes the coordinating agent's regenerated API types, simulation disposal-cost P&L display integration, and the later installed-sharp probe isolation described below. Application changes were finished before this run. Thresholds, exclusions and worker count were unchanged. The earlier passing 327-file/5,272-test baseline is preserved in [its original log](full-coverage-before-probe-isolation.log) and [coverage reports](coverage-before-probe-isolation/index.html); a fresh full run was required because the probe implementation changed afterward.

```sh
pnpm exec vitest run --coverage --coverage.reportsDirectory=../audit_reports/remediation-29-findings-2026-10-04/frontend/coverage --coverage.reporter=text --coverage.reporter=json --coverage.reporter=json-summary --coverage.reporter=html
```

**PASS, exit 0: 328 files, 5,293 tests, 381.52 seconds.** All global, layer and pinned entrypoint coverage floors passed. The existing jsdom scroll/navigation warnings remained; no failed assertion or threshold required a retry.

| Metric | Covered / total | Coverage |
|---|---:|---:|
| Statements | 9,377 / 9,912 | 94.60% |
| Branches | 9,896 / 10,766 | 91.91% |
| Functions | 2,492 / 2,634 | 94.60% |
| Lines | 8,503 / 8,782 | 96.82% |

Worker layout—the previously narrowest statement margin—passes at 81.52% statements against its unchanged 80% floor, with 74.20% branches, 90.78% functions and 88.62% lines.

- [Full coverage console receipt](full-coverage.log)
- [Machine-readable coverage summary](coverage/coverage-summary.json)
- [Raw coverage](coverage/coverage-final.json)
- [HTML coverage report](coverage/index.html)
- [Integrated lint receipt](integrated-lint.log): PASS, no warnings.
- [Integrated TypeScript receipt](integrated-typecheck.log): PASS.
- [Final coordinating changed-line coverage gate](../root/frontend-changed-coverage.json): PASS, 78/83 lines (93.98%), unchanged thresholds.

The coordinating production Chromium baseline reports **107 passing tests**, including both new worker regressions: fresh Telugu/default and axe landmark checks; and pre-control cache warmup, three same-build refreshes, independent HTTP-cache removal and successful offline Telugu reload. [Coordinating browser receipt](../root/browser-chromium.log). These browser executions provide the production proof that the earlier component/VM-only work deliberately did not claim. Browser/device coverage beyond this run is recorded by the coordinating report.

The focused 389-test run is a subset of the complete run; counts are not additive. The separate 22 node mutation-harness tests and 94 Python gate/harness checks are described in [peer-review-root.md](peer-review-root.md).

## Build verification follow-up

The AMD64 image build initially failed after route generation. A controlled reduction isolated native sharp loading during Next configuration as a required interaction; an observational syscall probe identified an invalid epoll descriptor (EBADF), not a filesystem collision. The installed native version probe now runs in a bounded child process while retaining the production image-decoder safety policy. The exact internal descriptor-closing component is not claimed. [Diagnosis, controls, fix and receipts](amd64-build-diagnosis.md).

The focused real-child regressions pass **37 tests** and are included in the 5,293-test total. The guard has 100% statement/line/function coverage and 96.61% branch coverage. Final [focused lint](isolated-probe-lint-final.log) and [integrated TypeScript check](isolated-probe-typecheck-final.log) pass.

Both final ARM64 and AMD64 images [build successfully](../ops/frontend-final-builds.json), pass standalone `/healthz` and installed native sharp [runtime smokes](../ops/frontend-final-runtime.json), and pass both raw and unchanged-policy vulnerability [scans](../ops/frontend-final-scan-manifest.json), with zero reported vulnerability records in either image. AMD64 builds/smokes used local QEMU; a native AMD64 host was not exercised. No production build exit handling, dependency safety threshold or vulnerability suppression was relaxed.
