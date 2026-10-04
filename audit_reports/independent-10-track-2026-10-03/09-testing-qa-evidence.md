# Track 9 — Testing, QA, and evidence integrity

Audit date: 3 October 2026 (America/Phoenix)  
Audited commit: `1ca78768ed227182b1b84663bcc97c5ea9bee41b`

## Verdict

The repository has an unusually substantial automated test system: the backend uses real PostgreSQL and real migrations, the frontend has enforced branch/line/function thresholds, the browser gate drives a live Next.js/FastAPI/PostgreSQL stack in three desktop engines, retries are not allowed to turn a flaky E2E into a green job, and releases require a successful push-CI run for the exact tagged commit.

That breadth is not yet equivalent to reliable behavioral evidence. A fresh Chromium run reproduced React duplicate-key errors in the feeding workflow while the affected tests continued to pass. The same review found a contract test that cannot enforce the backward-compatibility claim in its own documentation, no current application mutation score or mutation threshold in CI, and an important disease-screening boundary that remains mocked at every automated layer. Aggregate coverage floors and the mobile matrix leave additional escape windows.

Counted findings: **0 Critical, 0 High, 5 Medium, 3 Low**.

## What was independently exercised

- `backend/.venv/bin/python -m pytest --collect-only -q` completed with **5,170 tests collected**. The full backend and frontend suites were intentionally left to the campaign-wide runners rather than duplicated here.
- `backend/.venv/bin/python -m pytest -q tests/test_mutation_harness_20261003.py -k 'not timeout_cleanup'` completed with **45 passed**. There is no currently named `timeout_cleanup` case, so the expression excluded nothing.
- `node --test mutation/mutate_harness.test.mjs` completed with **22 passed, 0 failed**.
- A clean-checkout `CI=1 E2E_BROWSER=chromium pnpm exec playwright test --list` failed before enumeration with the missing `.e2e-state.json` error and reported no tests. This is finding 09-7, not a test-suite result.
- The campaign's fresh real-stack Chromium run executed **74 tests: 73 passed and 1 failed**. The failure was the offline worker journey discussed under “Cross-track runtime corroboration.” The run repeatedly emitted the feeding duplicate-key warning discussed in 09-1.
- The failed worker trace and error context were inspected. No application source was edited, and no prior audit files or mutation result/report artifacts were read.

## Findings

### 09-1 — Browser console errors are not a gate; a real feeding identity collision passes tests

- **Severity:** Medium
- **Confidence:** High
- **Evidence status:** Runtime reproduced and source-proven. Incorrect React reconciliation and allocation-completion display are plausible source-derived impacts; the campaign did not separately demonstrate a wrong row on screen.
- **Impact:** Framework errors and real rendering defects can ship under a green E2E result. In this instance, two distinct feeding-plan lines share both React identity and progress identity. React may reuse the wrong row, and the same dispensed quantity can be compared independently with each sex-split line rather than with an unambiguous allocation. That can make a breeding ration appear complete without the sum of both planned lines having been dispensed.
- **Preconditions:** A farm has both a doe and a buck in `BREEDING`; both receive `MAINTENANCE_75_25`. This is a normal domain state, not hostile input.

The real-stack Chromium run repeatedly logged a duplicate child key, `BREEDING:MAINTENANCE_75_25`, while the feeding flow continued to pass. This matches the code exactly:

- The backend intentionally splits `BREEDING` by sex (`backend/app/services/feeding.py:341-379`) and applies the buck supplement (`backend/app/services/feeding.py:467-473`), but the response line contains no line ID or sex discriminator (`backend/app/services/feeding.py:480-501`). A backend integration test expressly proves that the response contains two breeding lines with the same bucket and recipe (`backend/tests/test_feeding_extended.py:993-1014`).
- Both the mobile cards and desktop table key those distinct lines only as `${bucket}:${recipe_code}` (`frontend/src/app/(app)/feeding/page.tsx:686-702`, `frontend/src/app/(app)/feeding/page.tsx:760-777`).
- Progress is also indexed only by bucket, recipe, and shift (`frontend/src/app/(app)/feeding/page.tsx:90-92`, `frontend/src/app/(app)/feeding/page.tsx:529-559`). Thus the warning exposes a deeper identity ambiguity rather than harmless console chatter.
- The feeding E2E asserts visible headings, a quarantine dispense, toast, and ledger row (`frontend/e2e/feeding-finance.spec.ts:6-75`), but does not seed/assert the two-line breeding case.
- There is no suite-level `page.on("console")`, `pageerror`, or `requestfailed` policy in `frontend/e2e/`, and `frontend/playwright.config.ts:24-51` configures tracing/reporters but no console gate. `frontend/vitest.setup.ts:73-90` likewise has no unhandled-console policy.

**Recommendation:** Give every plan line a stable API identity that includes the sex/segment dimension, define whether dispense progress is line-specific or aggregated before it reaches the UI, and add a component plus real-stack case containing both sex-split lines. Fail E2E on unallowlisted `console.error`/`pageerror`; narrowly allow expected offline transport errors during the explicit offline phase.

### 09-2 — The OpenAPI “breaking change” test compares two versions from the same checkout

- **Severity:** Medium
- **Confidence:** High
- **Evidence status:** Source-proven false-negative design; the downstream break is inferred.
- **Impact:** Removing an operation, path, schema, required field, or enum member can pass the advertised compatibility guard when the OpenAPI snapshot/client is regenerated in the same change. An external consumer, or a currently unused frontend path, can then break even though the “contract drift” tests and freshness jobs are green.
- **Preconditions:** The breaking schema edit is accompanied by the normal regeneration step, and no currently compiled frontend call site or behavioral test happens to rely on the removed shape.

`test_committed_openapi_json_matches_the_live_schema` correctly checks freshness (`backend/tests/test_contract_drift.py:35-41`). The purported compatibility test then builds both sets from the current live app and the current checkout's `shared/openapi.json` (`backend/tests/test_contract_drift.py:44-83`). Once a PR regenerates that file, both sides contain the same removal. It also claims to catch a dropped enum value (`backend/tests/test_contract_drift.py:9-14`) but its implementation compares only operation IDs, path/method pairs, and schema names; enum values, required properties, types, and response shapes are never compared.

CI's export-and-diff step (`.github/workflows/ci.yml:170-175`) and Orval generation check (`.github/workflows/ci.yml:269-273`) are valuable freshness controls, but they do not provide a previous-version compatibility baseline.

**Recommendation:** Run an OpenAPI compatibility tool against the PR base or last released schema, preserving that baseline outside files regenerated by the same change. Gate removals and narrowing changes, including enum values, required fields, types, security requirements, parameters, and responses; retain the existing same-checkout freshness checks separately.

### 09-3 — CI validates the mutation harness, not the application's mutation resistance

- **Severity:** Medium
- **Confidence:** High
- **Evidence status:** Source-proven; both focused harness contract suites were reproduced passing.
- **Impact:** A change can weaken or delete assertions while retaining line and branch execution and still satisfy every required CI job. The repository currently has no release-gated measurement of whether application mutants are killed.
- **Preconditions:** The regression preserves enough ordinary coverage and does not fail an existing assertion—precisely the class mutation testing is meant to expose.

CI runs only the backend harness contracts (`.github/workflows/ci.yml:152-162`) and frontend harness contracts (`.github/workflows/ci.yml:275-277`), followed by ordinary coverage. It never invokes either application's generator, coverage mapper, runner, report, or a mutation-score threshold. The current harness READMEs are appropriately candid: the backend describes the checkout as unmeasured until a fresh campaign (`backend/mutation/README.md:1-24`), and the frontend says no new full campaign score is claimed (`frontend/mutation/README.md:1-22`).

The harness mechanics themselves have substantive controls. Backend input identity covers source, tests, migrations, scripts, locks, contracts, and deployment inputs (`backend/mutation/mutate_identity.py:31-80`); its runner uses a private snapshot and exact clean baselines (`backend/mutation/mutate_run.py:193-260`, `backend/mutation/mutate_run.py:425-463`). The frontend binds manifest/coverage/input hashes, rejects stale edits, accepts only assertion failures as kills, and requires an exact clean-selection baseline (`frontend/mutation/mutate_run.mjs:11-49`, `frontend/mutation/mutate_run.mjs:88-124`). The reproduced 45/45 and 22/22 harness tests support those mechanics, but cannot substitute for mutating the application under audit.

**Recommendation:** Add a bounded changed-file mutation gate to pull requests and a scheduled/full campaign with a provenance-bound threshold. Publish the campaign identity, complete-vs-sampled status, denominator, survivor list, and exact commit; do not convert infrastructure errors/timeouts into kills or exclude them silently.

### 09-4 — Disease-screening automation never crosses the real storage/provider boundary

- **Severity:** Medium
- **Confidence:** High
- **Evidence status:** Source-proven validation gap; failure against a real provider or S3-compatible deployment is inferred.
- **Impact:** Credential/header drift, real multipart upload policy/CORS behavior, S3 endpoint semantics, provider request/response changes, quotas, and real timeout behavior can first fail in deployment. This affects the product's disease-screening workflow, not only an ancillary test helper.
- **Preconditions:** Screening is enabled with a real object store and vision provider.

The browser journey explicitly routes every `/api/screening/**` request to in-memory fixtures (`frontend/e2e/screening.spec.ts:5-13`, `frontend/e2e/screening.spec.ts:127-157`). It proves UI rendering and review/conflict handling, but neither the real screening API nor upload/pipeline behavior.

Backend coverage is strong inside the seam, but still mocked at that seam: the suite describes provider adapters as mocked transport and the cascade as real PostgreSQL (`backend/tests/test_screening.py:1-7`); provider contract tests use `httpx.MockTransport` (`backend/tests/test_screening.py:480-560`); S3 client behavior is replaced with local fakes (`backend/tests/test_screening.py:754-811`); and the full cycle uses `FakeStorage` plus a fake provider (`backend/tests/test_screening.py:1153-1168`).

**Recommendation:** Add a secret-safe scheduled sandbox contract test that uploads a small fixture through the real presigned policy, reads it back through the configured storage adapter, and calls each supported provider with a bounded deterministic contract assertion. Keep it separate from hermetic PR tests, fail closed on schema/auth drift, and record provider/model/endpoint versions without storing secrets or sensitive images.

### 09-5 — Aggregate coverage floors still permit a new production file at 0%

- **Severity:** Medium
- **Confidence:** High
- **Evidence status:** Source-proven gate property; no claim is made that a current production file is wholly uncovered.
- **Impact:** A small new authorization, offline, finance, or animal-care module can receive no tests and still pass if its zeroes fit under the aggregate headroom. High aggregate coverage therefore does not ensure coverage of the changed risk surface.
- **Preconditions:** The new/changed file is small enough not to lower its global/glob aggregate below the configured floor and is not manually added to the single-file list.

Backend coverage is a single combined application floor of 92 (`backend/pyproject.toml:134-167`) with no per-file or changed-line threshold. Frontend global floors are 90/87/90/90, but route aggregates fall as low as 55/50/55/55 (`frontend/vitest.config.ts:30-55`). A manual list pins six historically important files (`frontend/vitest.config.ts:56-78`), while the configuration itself acknowledges that a new untested file is caught only through the aggregate (`frontend/vitest.config.ts:70-72`).

**Recommendation:** Add diff/changed-line coverage and a modest per-file floor for non-generated production code, with explicit reviewed exclusions. Keep the strong global floors as a second ratchet rather than replacing them.

### 09-6 — The “tablet” and offline journeys run only as a Pixel 7 in Chromium

- **Severity:** Low
- **Confidence:** High
- **Evidence status:** Source-proven matrix gap; device-specific failure is inferred.
- **Impact:** Tablet breakpoints/orientation, touch layout, mobile Safari/WebKit behavior, and engine-specific service-worker/IndexedDB behavior in the critical worker/offline journey can escape.
- **Preconditions:** The defect depends on tablet geometry, orientation, or a non-Chromium mobile engine.

CI does run the ordinary desktop project independently in Chromium, Firefox, and WebKit (`.github/workflows/ci.yml:308-316`; `frontend/playwright.config.ts:3-7`, `frontend/playwright.config.ts:62-76`). However, both worker device journeys are excluded from all desktop projects and run only in a Chromium `Pixel 7` project (`frontend/playwright.config.ts:56-76`). The so-called tablet spec documents that it is a Pixel 7 and Mobile Chrome only (`frontend/e2e/worker-tablet-journey.spec.ts:7-15`). There is no tablet-sized or landscape device project.

**Recommendation:** Add at least one actual tablet breakpoint/orientation and one mobile-WebKit worker/offline smoke. Keep the full desktop three-engine matrix and avoid multiplying every long scenario unnecessarily.

### 09-7 — Playwright cannot enumerate tests on a clean checkout

- **Severity:** Low
- **Confidence:** High
- **Evidence status:** Reproduced.
- **Impact:** Inventory, sharding, audit, and selective-test tooling can falsely report zero E2E tests or fail before collection. This weakens reproducibility even though the normal `playwright test` path can succeed.
- **Preconditions:** `.e2e-state.json` does not already exist, as on a clean checkout.

`frontend/e2e/helpers.ts` reads the generated credentials synchronously at module import (`frontend/e2e/helpers.ts:13-28`). The file is created only by global setup (`frontend/e2e/global-setup.ts:25-57`), but Playwright's list/discovery path imports specs without running that stateful setup first. The clean-checkout command above exited 1 with repeated “`.e2e-state.json missing`” errors and enumerated zero tests.

**Recommendation:** Keep test discovery side-effect-free: load credentials lazily from a fixture after global setup, or inject them as project metadata/environment for execution. Add a clean-checkout `playwright test --list` smoke to CI.

### 09-8 — E2E verdict evidence is lossy even though the flake gate fails correctly

- **Severity:** Low
- **Confidence:** High
- **Evidence status:** Source-proven.
- **Impact:** A later reviewer cannot recover the exact per-test record from a successful E2E run, and a flaky failure's diagnostic step does not name the flaky tests. The job still fails correctly; this is evidence/triage loss, not a false-green gate.
- **Preconditions:** Someone needs to audit a previous green run, or a CI test passes only after a retry.

The workflow correctly reads `.stats.flaky` and fails when it is nonzero (`.github/workflows/ci.yml:396-419`). But its title query searches each attempt result for `status == "flaky"` (`.github/workflows/ci.yml:413-416`). In the pinned Playwright JSON schema, flakiness is the test-level status; individual results hold attempt statuses such as passed/failed/timedOut/skipped (`frontend/node_modules/.pnpm/playwright@1.62.1/node_modules/playwright/types/testReporter.d.ts:314-333`). The query also skips the report's `specs[].tests[]` nesting, so it prints no titles.

Finally, the JSON/HTML report is uploaded only when the job is already failing and retained for seven days (`.github/workflows/ci.yml:421-429`). Successful-run machine-readable outcomes are not retained, whereas coverage artifacts are retained for 30 days (`.github/workflows/ci.yml:204-213`, `.github/workflows/ci.yml:290-298`).

**Recommendation:** Query `.suites[] | recurse(.suites[]?) | .specs[]? | select(any(.tests[]?; .status == "flaky")) | .title`, upload the compact JSON report on every run, and retain it with commit/run metadata for the same evidence window as coverage.

## Cross-track runtime corroboration — not counted as a Track 9 finding

The fresh Chromium run's only failing test was `worker-tablet-journey.spec.ts:55`: after proving the service worker was ready, the page was controlled, and the offline snapshot existed, the test switched the context offline and navigated to `/worker/offline`; the expected duty button never appeared and the captured DOM remained `main > status: Loading…` for 15 seconds (`frontend/e2e/worker-tablet-journey.spec.ts:166-181`).

The assertion is meaningful, not a loose/flaky proxy: it checks the user action that offline mode promises. The source has a single instantaneous escape based on `navigator.onLine === false`; otherwise bootstrap can enter three refresh attempts before releasing `loading` (`frontend/src/lib/auth-context.tsx:46-48`, `frontend/src/lib/auth-context.tsx:590-632`). Each refresh has a 10-second request bound (`frontend/src/lib/api-client.ts:126-135`). Both the worker layout and offline page hide content behind that auth loading flag (`frontend/src/app/worker/layout.tsx:163-183`, `frontend/src/app/worker/offline/page.tsx:18-21`, `frontend/src/app/worker/offline/page.tsx:71-92`).

This is runtime corroboration of an offline/auth product race and should be deduplicated with the offline/PWA track. From a QA perspective, it is a strength: the real-stack test caught a genuine inaccessible workflow and failed the run. The trace also contains development-HMR network failures after going offline, so production-mode CI reproduction remains important before attributing every trace symptom to the same cause.

## Controls that materially reduce risk

- **Real database and migration isolation:** Backend setup refuses unsafe database names, recreates a test database, runs Alembic, and truncates/reseeds after every test (`backend/tests/conftest.py:21-30`, `backend/tests/conftest.py:57-98`). The default HTTP client uses the real ASGI app (`backend/tests/conftest.py:253-263`).
- **Fixture masking is documented and bounded:** The convenience idempotency hook is explicit and dedicated keyless tests disable it (`backend/tests/conftest.py:201-250`); password auto-rotation is opt-in rather than default (`backend/tests/conftest.py:105-114`, `backend/tests/conftest.py:266-277`).
- **Strict frontend network behavior:** MSW fails unhandled unit-test requests and resets handlers after each test (`frontend/vitest.setup.ts:73-90`).
- **Behavioral assertions are generally specific:** Real-stack API contracts assert proxy origin, exact status/content type/body, generated model shapes, persistence, and cross-request invariants rather than only truthiness (`frontend/e2e/api-contract-domain.spec.ts:79-115`, `frontend/e2e/api-contract-domain.spec.ts:700-779`; `frontend/e2e/api-contract-auth-team.spec.ts:53-106`).
- **Flakes cannot become green through retry:** CI uses two retries but separately fails any nonzero Playwright flaky count (`frontend/playwright.config.ts:29-47`; `.github/workflows/ci.yml:396-419`). Local retries remain zero.
- **Three real desktop engines:** Chromium, Firefox, and WebKit each receive an isolated PostgreSQL-backed live-stack job (`.github/workflows/ci.yml:308-394`).
- **Contract freshness and migration drift:** OpenAPI export and generated-client freshness are CI gates, and the full migration chain is upgraded, downgraded to base, and upgraded again (`.github/workflows/ci.yml:170-188`, `.github/workflows/ci.yml:269-273`). Finding 09-2 concerns backward compatibility, not these freshness controls.
- **Release-to-test provenance:** Release checks that the tagged SHA is reachable from main and has a successful push-CI run for that exact SHA (`.github/workflows/release.yml:49-65`).
- **Skip/selection hygiene:** Static screening found no JavaScript `skip`/`only`, no Python `xfail`, and only three `pytest.skip` source sites: an absent-frontend guard and two parameterized system-category cases (`backend/tests/test_contract_drift.py:209-210`; `backend/tests/test_finance_extended.py:333-367`). CI also uses `forbidOnly` (`frontend/playwright.config.ts:29-31`).
- **Accessibility breadth:** Axe runs 13 routes in English and Telugu and fails critical/serious findings (`frontend/e2e/a11y-gate.spec.ts:6-33`, `frontend/e2e/a11y-gate.spec.ts:73-87`). Moderate/minor violations are an explicit residual limit, not represented as a zero-violation claim here.

## Limits and residual risks

- Full backend/frontend suites and coverage were not rerun by this track; campaign-wide runners own those expensive executions. Collection and focused harness results do not imply full-suite success.
- The Chromium E2E result was a local development-server run; CI builds the Next.js production standalone server. The duplicate React key is source-deterministic, while the precise offline trace should also be reproduced in production mode.
- No real S3/provider credentials were used, no production CI settings/branch protection were inspected, and source review cannot establish field-device, veterinary, or provider behavior.
- Frontend global cleanup clears local storage and MSW state but not session storage, fake timers, mocks, or stubbed globals (`frontend/vitest.setup.ts:75-90`). A focused shuffled run of `worker-offline-shift.test.ts` passed 6/6, so no isolation defect is counted; the residual depends on each file restoring its own state.
- E2E data is fresh per run but shared across all specs with `workers: 1` (`frontend/playwright.config.ts:20-28`). Unique fixtures and serial execution reduce collisions, but order-dependent accumulated domain state remains possible; the feeding warning surfaced only because the full farm state exercised a combination absent from the dedicated feeding fixture.
- Mutation harness contract passes establish runner mechanics, not a current mutation score, operator completeness, or semantic equivalence decisions.

## Finding count

| Severity | Count |
|---|---:|
| Critical | 0 |
| High | 0 |
| Medium | 5 |
| Low | 3 |
| **Total** | **8** |
