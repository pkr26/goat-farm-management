# Independent frontend audit — 2026-10-04

Commit: `7245368fa91333fb39387df789fa4ef9a58dbea3`. No application source edits or fixes. Findings were derived from current source and new probes, without consulting historical audit conclusions. `frontend/AGENTS.md` and installed Next 16.3.8 layout/PWA guidance were read. All new artifacts are in this report directory. No development database or pre-existing application service was used; browser probes used the coordinating auditor's isolated production stack at localhost:3000, in independent Chromium processes and fresh browser contexts, with no account or farm mutations.

## Coverage and boundaries

| Track | Independent work and result |
|---|---|
| 1 Architecture / frontend maintainability | Reviewed Next server/client layout boundaries, provider composition, generated API transport, shared permission/navigation/formatting/picker components, query invalidation and farm remounting. Substantive risks are reflected in concrete findings below. Large page modules are a maintenance concern, not proof of a behavior defect: simulation 4,330 lines; animal profile 2,421; health 2,144; team 2,108. No separate size-only defect was manufactured. |
| 2 Functional journeys | Reviewed login/farm selection, worker handover and task completion, screening intake/review, health round and deep-link entry, breeding/kidding entry, animal filtering, finance/report rendering. Existing 5,248-test baseline passed. New component probes reproduced interrupted screening uploads, stale farm-switch query references, read-only screening action mismatch, and misleading outage copy. Coordinating report owns full real-stack journey coverage. |
| 14 Frontend API boundary | Reviewed real-status response envelopes, null parameter serialization, approved same-origin paths, timeout/session/farm epochs, generated hooks, backend contracts for the specific reproduced findings. Permission mismatch in F26-08 is a UI defect; server enforcement remains in place. No tenant isolation bypass was established. |
| 15 State / races | Reviewed auth and farm epochs, query cancellation/clear, keyed app remount, optimistic duty rollback, URL-state composition, dialog attempt ownership, and worker outbox transaction/lease semantics. New probes focus on provider-effect ordering, live upload/finish interaction, and tenant-specific query parameters. |
| 16 Offline / PWA | Read complete service worker, offline snapshot/outbox and worker page/shell. Executed actual service-worker source in isolated VM; reproduced cache deletion and conditional blank offline reload in production Chromium. Reviewed actor/farm scoping, persistent write-before-send, replay keys, review receipts, end-shift preservation, TTL/clock validation and import quarantine. No new outbox data-loss defect established. |
| 17 Usability / browsers / devices | Fresh Chromium context at 390×844; worker startup, Telugu selection, repeated reload, offline recovery. Reviewed responsive components, mobile cards, touch controls and retry states. Physical Android/iPad camera, installation, browser storage eviction behavior, battery suspension, poor RF connectivity and assistive hardware were not exercised. |
| 18 Accessibility | Reviewed dialog/table/picker primitives and worker semantics; real axe scan of /worker/offline found three landmark rules stemming from one nested-main defect. Keyboard/screen reader and manual contrast/zoom coverage is not exhaustive. |
| 19 Localization / time | Reviewed catalog loading/persistence, language toggle, formatting module and farm timezone propagation. Independently reproduced initial-worker English, stale language-sensitive formatting, and UTC-naive timestamp day drift. Translation quality/terminology still needs native Telugu review; key parity is not a fluency review. |

## Confirmed findings

### F26-01 — Medium — Same-build shell refreshes evict live lazy assets; an offline Telugu reload can become a blank screen

- **Location:** `frontend/public/sw.js:64–78,82–101`; `frontend/src/lib/i18n/index.tsx:151–163,202–203`.
- **Trigger:** Load the worker online, choose Telugu, perform two successful document reloads of the same deployed worker shell. Then cold-reload offline after the ordinary browser HTTP cache no longer holds the lazy Telugu chunk.
- **Expected / actual:** The current build's required locale and runtime assets remain durably available for the offline shell. Instead `publishAssetGeneration` calls each HTML refresh a generation, replaces `current` with HTML-discovered dependencies, shifts the prior `current` to `previous`, and deletes runtime-only chunks on the second refresh. The Telugu catalog is a dynamic import absent from shell HTML. Initial catalog rejection is swallowed while every child, including language/retry controls, stays hidden behind an empty `aria-busy` div.
- **Evidence:** `sw-runtime-asset-probe.mjs/.log` executes actual sw.js and observes cached=true → true → false. `browser-worker-probe.json` confirms actual production chunk `/_next/static/chunks/1ske97k1fzh5d.js` disappears after navigation 2. With only the independent browser's HTTP cache cleared (SW CacheStorage retained), offline reload produced empty body text, one busy container and zero buttons. `worker-offline-locale-cache-evicted.png` visually confirms blank page. `locale-failure.probe.test.tsx` independently confirms rejection has no visible recovery and an `online` event does not recover it.
- **Important limit:** This is **not** an unconditional failure immediately after two reloads. The first real-browser run remained usable because ordinary HTTP cache rescued the missing chunk; that successful control is preserved in `browser-worker-http-cache-intact.json/.log`. Cache clearing models an absent/evicted HTTP entry; it does not claim a measured eviction frequency on physical tablets. Runtime font assets were also absent in the cache-cleared run.
- **Impact:** Offline worker access depends on a secondary cache despite a previously successful online warmup; the entire Telugu surface can be unavailable.
- **Fix direction:** Track actual build generations and retain their runtime dependencies; durably precache/retain the selected locale. Provide visible retry/language recovery outside the gated content if a catalog fails.
- **Confidence:** High (source + VM + real production Chromium, with explicit HTTP-cache condition).

### F26-02 — Medium — First-time worker setup defaults to English instead of Telugu

- **Location:** `frontend/src/lib/i18n/index.tsx:151–157`; `frontend/src/app/worker/layout.tsx:116–125`; `frontend/src/app/layout.tsx:53–63`.
- **Trigger:** A fresh tablet context visits `/worker/login` without a locale cookie or localStorage preference.
- **Expected / actual:** The explicit worker contract in `frontend/AGENTS.md` and WorkerShell calls for Telugu when the preference is unset. Root layout passes `initialLanguage=null`; LanguageProvider hides its children, selects and persists English, then mounts WorkerShell. Its null-only Telugu default sees the newly written English and cannot run.
- **Evidence:** Actual-provider/actual-shell `worker-default-language.probe.test.tsx`; fresh-context production browser output: `htmlLang=en`, `herdly.language=en`, English setup instructions.
- **Impact:** The intended Telugu-first shared-tablet onboarding starts in English, obstructing workers who rely on the default.
- **Fix direction:** Decide the route-specific initial locale before persisting a fallback/mounting the shell, while preserving deliberate existing preferences.
- **Confidence:** High (component + browser).

### F26-03 — Low — UTC-naive health snapshot timestamps display a browser-dependent farm date

- **Location:** `frontend/src/lib/format.ts:237–245`; actual caller `frontend/src/components/health-round-progress.tsx:87`; source contract `backend/app/models/health.py:329–331`, `backend/app/schemas/health.py:127`.
- **Trigger:** View a health round with UTC-naive `snapshot_at="2026-08-05T16:00:00"` for an Asia/Kolkata farm in an America/Phoenix browser.
- **Expected / actual:** Snapshot farm date is 5 Aug 2026. `formatDate` parses the offsetless timestamp in the browser timezone, then converts it to farm time, displaying **6 Aug 2026**. The companion `formatFarmDateTime` correctly gives 5 Aug, 9:30 pm for the same input. The model's UTC-naive timestamp and unmodified datetime schema make this a real caller path, not only hypothetical helper input.
- **Evidence:** `naive-timestamp.probe.test.tsx`, run with `TZ=America/Phoenix`.
- **Impact:** Snapshot/audit chronology is a day wrong for some timezone/time combinations. Date-only values are unaffected; no data mutation was shown.
- **Fix direction:** Normalize offsetless backend datetimes to UTC consistently before converting to the selected farm timezone (or emit explicit UTC offsets throughout the wire contract).
- **Confidence:** High.

### F26-04 — Medium — Finishing a walkthrough can abort an actively uploading photo while reporting success

- **Location:** `frontend/src/components/screening-check-dialog.tsx:97–99,244–267,376–378`; backend submit semantics `backend/app/api/screening.py:1209–1258`.
- **Trigger:** Successfully upload one image, start a second slow upload, then select “Finish & process” while the second upload is pending.
- **Expected / actual:** Finish waits for the pending upload or explicitly asks to discard it. The button is disabled only by submit state/zero prior uploads, so it remains enabled. Submission succeeds, closes the dialog, and the open-state cleanup aborts the current object-store request. The walkthrough reports success although the chosen second photo was interrupted.
- **Evidence:** `screening-upload.probe.test.tsx` uses the actual dialog with deterministic API/object-store doubles. The second upload's signal starts un-aborted; Finish is enabled; submit receives batch 99; onFinished fires; that signal becomes aborted. Source review confirms submit accepts registered image rows without waiting for their upload requests.
- **Impact:** A worker can finish a batch with an omitted/interrupted photo and lose their pending capture without a discard warning.
- **Fix direction:** Serialize finish with active uploads and pending selected files, with an explicit discard path if needed; guard the handler as well as button state.
- **Confidence:** High for UI cancellation; no live S3 service was used, so object-store persistence after abort is not asserted.

### F26-05 — Low — Switching farms preserves tenant-specific record IDs in root-route query state

- **Location:** `frontend/src/lib/permission-navigation.ts:161–165`; actual navigation `frontend/src/app/farm-select/page.tsx:124–128`; consumers `frontend/src/app/(app)/screening/page.tsx:119,200–205,374–414`, `health/page.tsx:850–881`, `breeding/page.tsx:895`, `kidding/page.tsx:977`.
- **Trigger:** Switch from a farm while viewing `/screening?image_id=71` (also health `task_id`/`animal_id`/`purchase_batch_id`, breeding `ultrasound_id`, kidding `breeding_id`).
- **Expected / actual:** The new farm opens its corresponding module without old-farm record context, just as the helper already drops `/animals/7` style path IDs. Root routes retain the entire query string. Screening requests the prior image ID under the new farm and opens an error/detail panel; health can reopen an old-record prefilled form.
- **Evidence:** `farm-switch-query.probe.test.tsx` verifies all four concrete preserved destinations and contrasts path-ID stripping. `screening-boundaries.probe.test.tsx` feeds the actual helper output into the actual page: image ID 71 is requested and its simulated inaccessible-record response displays the detail error and “Back to list”. Backend get-image filters both image and farm, so 404 is the expected real cross-farm response.
- **Impact:** Confusing stale-record errors and unusable form context after a valid farm switch. **No cross-tenant data access or mutation bypass was established.** Component test simulates the 404 rather than provisioning two farms.
- **Fix direction:** Preserve only farm-agnostic filter/pagination keys across farm changes; strip record IDs and nested return destinations tied to the old farm.
- **Confidence:** High for destination/consumer behavior; medium-high for complete end-to-end symptom (source-supported, not a new real-DB two-farm probe).

### F26-06 — Low — Language-sensitive formatting lags a locale switch until another render

- **Location:** `frontend/src/lib/i18n/index.tsx:169–173,175–188`; `frontend/src/lib/format.ts:226–258` and other helpers consulting `getActiveLanguage()`.
- **Trigger:** A mounted page calls `formatDate` without an explicit language and the user switches English to Telugu.
- **Expected / actual:** Text and formatted dates switch together. The context change rerenders children before the provider's effect updates the module-level active language. The visible date remains English (`5 Aug 2026`) even after context and module state are Telugu. Calling the helper afterward yields Telugu, but no render is scheduled to replace the visible stale result.
- **Evidence:** `locale-format-render.probe.test.tsx` uses actual LanguageProvider, locale loader and formatter; verifies context=te, active module=te, DOM still English, direct formatter result Telugu.
- **Impact:** Mixed-language date/number/enum presentation until unrelated state changes; startup with a loaded non-default catalog has the same ordering hazard. The probe demonstrates date formatting specifically.
- **Fix direction:** Pass the current context locale into rendering helpers or expose the language through a render-consistent subscribed state rather than an after-render side effect.
- **Confidence:** High.

### F26-07 — Low — Offline worker page nests duplicate main landmarks

- **Location:** `frontend/src/app/worker/layout.tsx:209`; `frontend/src/app/worker/offline/page.tsx:73`.
- **Trigger:** Open `/worker/offline` once loaded.
- **Expected / actual:** One top-level main landmark. Both shell and page render `<main>`, producing `main > main` with no distinguishing label.
- **Evidence:** Production Chromium measured two mains, one nested. `browser-worker-axe.json` independently reports `landmark-main-is-top-level`, `landmark-no-duplicate-main`, and `landmark-unique` (axe moderate impact); 32 checks passed and no incomplete checks on this tested state.
- **Impact:** Screen-reader landmark navigation exposes ambiguous/nested primary regions. This is one root defect, not three separately counted issues or a claim of complete WCAG conformance testing.
- **Fix direction:** Assign main ownership to the shell or page and use a non-landmark container for the other.
- **Confidence:** High.

### F26-08 — Low — Health viewers are offered a screening workflow they cannot submit

- **Location:** `frontend/src/app/(app)/screening/page.tsx:267–269,282–287` and retake action `:421–425`; backend create/upload/submit gates `backend/app/api/screening.py:1104,1213,1270`.
- **Trigger:** A custom role has `health.view` but lacks `health.manage` and opens Screening.
- **Expected / actual:** Management-only intake/retake controls are hidden or explain missing access. “Disease check” is always rendered, although export is correctly manage-gated. It opens the capture workflow, then backend batch creation/upload/submit is denied. The dialog converts creation failure to a generic “Could not start the walkthrough — try again.”
- **Evidence:** `screening-boundaries.probe.test.tsx` renders the actual page with view-only permissions and confirms the Disease check button while export is absent; backend signatures establish enforcement. No forbidden server write is possible in this finding.
- **Impact:** Misleading dead-end action and futile retries for valid read-only users.
- **Fix direction:** Apply `health.manage` to intake and retake controls and retain appropriate permission error feedback.
- **Confidence:** High.

### F26-09 — Low — Screening statistics outages are presented as an empty history

- **Location:** `frontend/src/app/(app)/screening/page.tsx:294–299`; wording `frontend/src/lib/i18n/en.ts:1351`.
- **Trigger:** `/api/screening/stats` fails before returning data while the images list is available.
- **Expected / actual:** An actionable unavailable/retry state, distinguishable from a successful empty result. `!stats` shares the empty branch and displays “No model runs recorded yet.” There is no statistics error notice or dedicated retry control.
- **Evidence:** `screening-boundaries.probe.test.tsx` passes an errored stats query with a successful list into the actual page and observes the empty-history copy with no alert.
- **Impact:** Operators mistake an unavailable metrics service for an absence of screening history and have no local recovery action. No incorrect model accuracy calculation was alleged.
- **Fix direction:** Handle stats error separately, and show stale-data status if prior stats exist.
- **Confidence:** High.

## Reproducible validation receipts

Commands run from repository root unless a directory is stated. Probe tests intentionally assert the observed defective behavior; passing them confirms reproduction, not correctness of the application.

| Command | Result / evidence |
|---|---|
| `cd frontend && pnpm test` | PASS: 326 files, 5,248 tests, 310.05s. `evidence/frontend/vitest-baseline.log`. Existing jsdom “scrollTo/navigation not implemented” warnings; no failures. |
| `cd frontend && pnpm lint` | PASS, exit 0. `evidence/frontend/lint.log`. |
| `cd frontend && pnpm typecheck` | PASS, exit 0. `evidence/frontend/typecheck.log`. |
| `TZ=America/Phoenix frontend/node_modules/.bin/vitest run --config audit_reports/independent-26-track-2026-10-04/evidence/frontend/vitest-audit.config.mjs` | PASS: 7 new files, 9 independent reproduction test cases, 3.89s. `evidence/frontend/targeted-probes.log`. Config explicitly includes only evidence-directory probes and bypasses baseline setup that preloads Telugu. |
| `node audit_reports/independent-26-track-2026-10-04/evidence/frontend/sw-runtime-asset-probe.mjs` | PASS (reproduced cache loss); `.log` saved. No network/database. |
| `node audit_reports/independent-26-track-2026-10-04/evidence/frontend/browser-worker-probe.mjs` | Completed against coordinating auditor's isolated production stack. Actual cache retention/deletion, blank offline state and landmarks saved in `.json/.log` plus screenshot. Successful HTTP-cache control also retained separately. |
| `node audit_reports/independent-26-track-2026-10-04/evidence/frontend/browser-worker-axe.mjs` | Completed: three axe findings from F26-07, 32 passing checks, no incomplete checks. `.json/.log` saved. |

Harness corrections are preserved rather than mislabeled as product failures: the first component harness did not resolve Next's mocked navigation module (`targeted-probes-harness-error.log`); adding an explicit Next alias fixed it. Initial browser navigation used `networkidle` and timed out at 90s (`browser-worker-probe-incomplete.log`); the successful probe uses DOMContentLoaded plus semantic readiness. Initial axe runner used `browser.newPage`, which axe rejected; switching the probe to an explicit new browser context fixed it (`browser-worker-axe-harness-error.log`).

## Remaining test gaps, not additional defects

- Real camera/S3 upload and interruption semantics need a disposable object-store integration test; F26-04 proves the frontend abort, not whether an already-transmitted S3 request finishes after cancellation.
- Durable offline behavior under OS storage pressure, physical device sleep, process kill, installation/update and actual hardware clock changes was not exercised. Controlled HTTP-cache removal establishes F26-01's causal condition but not field frequency.
- This worker-focused browser pass is Chromium only; the coordinating report must identify any additional Firefox/WebKit evidence. No blanket compatibility claim is made.
- Full native Telugu linguistic review, manual screen reader usability, low-vision/zoom, touch accuracy and actual farm usability remain human/device work.
- Baseline tests preload the Telugu catalog, which conceals production lazy-loader ordering/failure conditions unless separate production-style provider tests are used. All baseline green results coexist with the independent confirmed findings above.


## Fresh configured coverage gate — track 26 addendum

A separate fresh coverage run completed successfully after the plain test baseline; the earlier baseline alone did not enforce coverage floors. This was one gate execution, with no retry, changed threshold, or application source edit. Only report destination/reporters were overridden; all includes, exclusions, worker count and thresholds came from the committed `frontend/vitest.config.ts`.

Exact command, from `frontend`:

```sh
pnpm exec vitest run --coverage --coverage.reportsDirectory=../audit_reports/independent-26-track-2026-10-04/evidence/frontend/coverage --coverage.reporter=text --coverage.reporter=json --coverage.reporter=json-summary --coverage.reporter=html
```

**PASS, exit 0:** 326 test files and 5,248 tests; 466.13 seconds. V8 coverage includes 138 instrumented report entries. Global totals: statements **9,311/9,855 (94.47%)**, branches **9,808/10,678 (91.85%)**, functions **2,475/2,620 (94.46%)**, lines **8,452/8,738 (96.72%)**. All configured global, layer and entrypoint floors passed. Only the existing jsdom scroll/navigation warnings appeared; no test or threshold failure required investigation or retry.

| Configured scope | Files | Statements actual / floor | Branches actual / floor | Functions actual / floor | Lines actual / floor | Result |
|---|---:|---:|---:|---:|---:|---|
| `Global` | 138 | 94.47% / 90% | 91.85% / 87% | 94.46% / 90% | 96.72% / 90% | PASS |
| `src/app/[(]app[)]/**` | 40 | 94.75% / 80% | 92.16% / 60% | 95.29% / 80% | 96.86% / 80% | PASS |
| `src/app/**` | 53 | 93.88% / 55% | 91.34% / 50% | 94.46% / 55% | 96.46% / 55% | PASS |
| `src/components/**` | 44 | 96.37% / 75% | 93.92% / 70% | 97.12% / 80% | 98.11% / 75% | PASS |
| `src/lib/**` | 38 | 95.12% / 85% | 92.89% / 85% | 92.95% / 85% | 96.70% / 85% | PASS |
| `src/proxy.ts` | 1 | 100.00% / 80% | 100.00% / 80% | 100.00% / 80% | 100.00% / 80% | PASS |
| `src/app/error.tsx` | 1 | 100.00% / 80% | 100.00% / 80% | 100.00% / 80% | 100.00% / 80% | PASS |
| `src/app/manifest.ts` | 1 | 100.00% / 80% | 100.00% / 80% | 100.00% / 80% | 100.00% / 80% | PASS |
| `src/app/healthz/route.ts` | 1 | 100.00% / 80% | 100.00% / 80% | 100.00% / 80% | 100.00% / 80% | PASS |
| `src/app/[(]app[)]/layout.tsx` | 1 | 100.00% / 80% | 100.00% / 80% | 100.00% / 80% | 100.00% / 80% | PASS |
| `src/app/worker/layout.tsx` | 1 | 81.11% / 80% | 74.19% / 70% | 91.66% / 70% | 88.29% / 80% | PASS |

Evidence:

- Full console log: `evidence/frontend/coverage-gate.log`.
- Machine-readable aggregate/per-file coverage: `evidence/frontend/coverage/coverage-summary.json`.
- Raw Istanbul-compatible coverage: `evidence/frontend/coverage/coverage-final.json`.
- Navigable HTML coverage: `evidence/frontend/coverage/index.html`.
- All configured threshold comparisons with numerator/denominator: `evidence/frontend/coverage-threshold-results.json`; deterministic summary script `evidence/frontend/summarize-coverage.py`.
- Config snapshot: `evidence/frontend/coverage-config-excerpt.txt`.

The per-scope table is independently summed from the new JSON summary using the exact configured prefix/file scopes, with Istanbul-style truncation to two percentage decimals. Installed Vitest's threshold implementation was checked: global coverage includes all files, overlapping glob scopes are independently checked, and these thresholds are aggregate per scope (`perFile` is unset). This independently corroborates the gate's exit status. The worker layout statement floor is the narrowest margin (81.11% actual versus 80% minimum); it still passed. Coverage success does not invalidate the separately reproduced product defects above.


## Coordinating reviewer supplement: build budgets and mutation-harness contracts

Both checks ran once against the unchanged current source and existing fresh production build:

- `pnpm check:route-js-budget`: **PASS**, exit 0. `/login` initial JavaScript is 446,517 gzip bytes against 465,920; `/simulation` is 529,323 against 547,840. The script's lazy-Telugu checks also passed. These are bundle-size gates, not measured device latency or production capacity. [Receipt](evidence/frontend/route-js-budget.log).
- `node --test mutation/mutate_harness.test.mjs`: **PASS**, exit 0, 22 tests, no skips/failures, 17.31 seconds. The `smoke: failed` line is output from an intentional negative control; its enclosing assertion passed. This validates the harness contracts, not a new full-project mutation campaign. [Receipt](evidence/frontend/mutation-harness-contracts.log).


## Retained simulation screenshot review (track 17 supplement)

Visually inspected all eight retained Chromium/WebKit simulation screenshots: results and advanced analysis at desktop 1440×1000 and mobile 390×844 CSS viewports. Metric cards, text, and run options visibly reflow within the mobile width. No additional persistent layout defect was established. A calibration toast is visibly clipped during the immediate desktop-to-mobile resize in three captures; it is fully visible in the later WebKit capture, consistent with the installed 400ms toaster transform transition. This observation is documented with its timing limit, not counted as a new stable layout finding.

[The screenshot review and all eight image links](evidence/frontend/screenshot-review.md) record the examined views, source-supported transition explanation, and limits. This was inspection of existing images only: no browser/server/test was rerun. The captures do not establish physical-device, touch/keyboard, zoom, screen-reader, dark-theme, Telugu, or Firefox behavior, and do not change the failed full WebKit baseline.
