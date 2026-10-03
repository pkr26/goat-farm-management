# Independent frontend audit — current working tree, 2026-10-03

Workspace: `/Users/pradeepreddy/Desktop/goat-farm-management-main`. No application changes. Temporary component reproduction tests were removed, and `git status --short` was empty afterward. Existing audit prose/comments were treated as navigation, not evidence.

## Assessment

The app has unusually broad operational coverage and a coherent design system: shared UI primitives, dark tokens, permission-gated navigation, Telugu catalogs, mobile operational cards, farm-calendar formatting, typed generated contracts and extensively guarded transport/auth code. The largest release risks are now ownership and durability across asynchronous boundaries, plus clinical truthfulness. A green completion/result must correspond to durable, correctly scoped evidence; several current paths violate that expectation.

**Frontend score: 68/100**, a judgment against reliable production use on a shared rural tablet, not a percentage of tests passing. Suggested category scores: UI/workflow UX 78; accessibility 82; localization 86; frontend engineering 73; state/concurrency 60; offline reliability 35; maintainability 62; performance 72. Accessibility/visual/performance figures are provisional source-based assessments: this agent did not conduct a complete live-device, screen-reader, production-load or Web Vitals campaign. Root-owned suites/build/visual checks should refine them.

## Verified findings

### FE1 — High: An old-farm offline drain sends later records with the new farm header and deletes rejected work

**Primary:** `/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/src/app/worker/layout.tsx:129` (scope callback), `:164` (in-flight drain), `:165` (cleanup). **Replay edge:** `/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/src/lib/offline-queue.ts:401`, `:447`, `:484`.

The supposed live `scopes()` reader closes over the original effect's user/farm values. Cleanup stops the workers/interval, but neither invalidates that callback nor stops its already running drain. `sessionStillCurrent()` therefore keeps reporting the original farm while `apiFetch` uses its module's current farm header for each new request. Record headers intentionally contain no farm ID. After farm switching during the first awaited replay, the next old-farm task is sent to the new farm. Backend task IDs are globally unique and scoped by farm, so it receives a 404; the queue's definitive-4xx branch then drops it permanently. This is verified data loss, not a claim of cross-tenant database access.

**Observed isolated integration probe using actual unmodified api-client/offline-queue modules:** replay calls were `task1/complete, X-Farm-Id=10`, then `task2/complete, X-Farm-Id=20`, despite both queued for farm 10. Outcome `{replayed:1,remaining:0,rejected:1}`. Actor remained the same. Auth session epochs do protect several actor-switch paths, so broader cross-actor corruption is not claimed.

**Fix:** maintain a live scope ref plus a drain generation/mount fence; invalidate on effect cleanup; recheck actor/farm before every send, including immediately before the transport chooses headers. A switched-farm drain must retain unprocessed records. Add an integration test that rerenders the shell while a replay is awaiting a response.

### FE2 — High: A failed queue write is acknowledged as saved and leaves the duty optimistically complete

**Primary:** `/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/src/lib/offline-queue.ts:79`, `:258`. **User effect:** `/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/src/app/worker/page.tsx:225`.

`writeQueue()` swallows quota/storage-denial exceptions and returns void. `enqueueOfflineMutation()` unconditionally returns true afterward. The board keeps its DONE/SKIPPED optimistic state and says the action was saved for delivery, although no record exists. A failing localStorage.setItem reproduction returned `{saved:true,persistedCount:0}`.

**Fix:** propagate durable storage success/failure, roll back the optimistic patch when persistence fails, and display a storage-specific recovery message. Consider IndexedDB transactions and a durable outbox. Do not silently shift old records to satisfy the byte cap (`offline-queue.ts:252`); report capacity without deleting unsent work.

### FE3 — High: Immediate End shift skips the unsent-work confirmation and erases newly queued duties

**Primary:** `/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/src/app/worker/layout.tsx:154`, `:243`. **Queue creation:** `/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/src/app/worker/page.tsx:216`.

The End shift decision uses the badge's depth state, refreshed every 1.5 seconds. Enqueuing from the board does not synchronously update that shell state. Complete offline, then immediately press End shift before the next tick: depth is still zero, so the handler directly wipes the queue and signs out without the intended confirmation.

**Observed current-component probe:** after rendering with depth 0, change the mocked live queue depth to 1 and immediately click End shift. Actual WorkerShell calls wipe and signOut, with no alertdialog. One targeted Vitest test passed in 707 ms; the retained probe is `/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/evidence/herdly-worker-handover-probe.test.tsx`. This test establishes the stale shell decision; the production enqueue-to-tick gap is independently evident in the source.

**Fix:** read scoped queue depth at the moment of clicking; use queue change subscriptions for the badge. Recheck pending records at final handover confirmation too, since new records can arrive while that dialog is open.

### FE4 — High: Cancel/navigation during pending tablet manager login can leave a late manager session on the shared tablet

**Primary:** `/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/src/app/worker/login/page.tsx:156`, `:169`, `:230`; cleanup at `:68`; TOTP equivalent at `:192`.

The page sets `setupRef.current=true` only after the authentication request resolves. Cancel and unmount cleanup sign out only when that ref is already true. Cancelling/navigating during a delayed password or TOTP request sees false and does nothing; its late response then calls global signIn with the manager credentials. No setup attempt/mount fence suppresses it. The Cancel controls remain available during these steps.

**Observed AST-extracted actual-functions probe:** Cancel produces `setSetupStep(null)`; resolving the pending login then produces `signIn(manager)` and `setSetupStep('choose-farm')`, without signOut. `/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/evidence/herdly-worker-setup-probe.json`. The unmount case follows the same ref gap, source-verified.

**Fix:** own every setup attempt with a generation/mounted flag and abortable request; coordinate late cookie/session establishment so abandonment revokes the abandoned manager session. Test cancellation/unmount before password and TOTP responses, not only after farm selection.

### FE5 — Medium: First-class PIN tablet users cannot be created or reset in the Team UI

**Primary:** `/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/src/app/(app)/team/page.tsx:665`, `:680`, `:780`. **Contract:** `/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/src/api/generated/models/workerCreateIn.ts:19`.

Team AddWorkerDialog requires/submits password and has no PIN option. The entire Team production source has zero PIN references/reset-PIN affordances, despite WorkerCreateIn supporting optional pin and the backend exposing PIN lifecycle endpoints. The tablet roster admits only PIN workers. Existing tablet e2e fixtures provision them through the API, bypassing this missing operator flow.

**Fix:** expose explicit Password/PIN credential modes, safe PIN generation/reset and shared-device onboarding. Exercise creating a PIN worker through the actual Team UI and signing in on the actual tablet roster in one e2e journey.

### FE6 — Medium: A password worker's required first-password rotation is inaccessible from its automatic worker landing

**Primary:** `/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/src/lib/permission-navigation.ts:12`; `/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/src/app/worker/layout.tsx:211`.

Password-provisioned task-only roles without dashboard.view land on /worker. Backend requires these users to rotate their initial password before domain writes. The worker shell has no AccountDialog/change-password or must_change_password affordance, whereas the main app shell has one. These workers reach a duty board whose completion writes are refused and have no UI path to clear the requirement. Manually typing a main-shell route is a workaround, not usable onboarding. PIN-only workers are not affected.

**Fix:** gate required rotation before role-based landing, or expose a dedicated password rotation route in both shells. Test owner-created password worker -> first sign-in -> successful rotation -> first duty completion.

### FE7 — Medium: Planner conflict recovery advances revision without loading the new contents, then prompts the user to overwrite them

**Primary:** `/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/src/app/(app)/planner/page.tsx:629`, `:633`. **Copy:** `/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/src/lib/i18n/en.ts:2393`.

A 409 fetches the latest plan and sets only openPlan. Local name/targets/assumptions remain the stale draft. The toast claims the latest revision was loaded and tells the user to press Update again. That second PATCH uses the latest revision with old local contents, bypassing the very conflict guard intended to protect another operator's edits. Unlike simulation's analogous conflict flow, the current planner does not replace/reconcile the editor or show a deliberate overwrite decision.

**Observed AST-extracted actual onUpdatePlan probe:** server revision 2 contains 99-doe targets/assumptions; retry submits expected_revision 2 with the old 5-doe values. `/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/evidence/herdly-planner-conflict-probe.json`. This establishes the request payload mechanism; it was not a live PostgreSQL overwrite probe.

**Fix:** preserve the local draft separately, reload/reconcile the full authoritative document and show explicit comparison/merge/overwrite choices. Preserve the expected revision until the operator makes that decision.

### FE8 — Medium: Simulation's field and section explanations remain English in Telugu mode

**Primary:** `/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/src/app/(app)/simulation/page.tsx:1998`, `:2009`, `:4210`; section equivalent `:3469`. **Help corpus:** `/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/src/lib/simulation-field-help.ts:31`, `:59`, `:571`.

The help title/facts localize, but the entire explanatory body is taken from English-only simulationFieldHelp/SIMULATION_SECTION_HELP and rendered verbatim. A Telugu farmer gets translated inputs and validation but English-only explanations of units, cash/debt, disease risk and assumptions. Catalog key parity alone does not cover these strings.

**Fix:** move explanatory bodies into language-bound catalogs, translate with domain review, and test the rendered help in Telugu. Metric narrative explanations returned by the backend also need a locale-aware contract or translated keyed text, rather than server-written English.

### FE9 — Medium, source-verified: A stored-scenario result can be bound to stale cached assumptions

**Primary:** `/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/src/app/(app)/simulation/page.tsx:1797`, `:1804`, `:1570`. **Backend contract:** `/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/api/simulation.py:726`.

Run stored scenario loads its current database assumptions without expected_revision. Frontend binds that result to a fingerprint computed from the cached list-row assumptions, ignoring the response's actual assumptions_fingerprint. If another operator changed that scenario after the list loaded, the server computes the new revision while the browser claims the result describes the old assumptions; its stale banner sees the same stale cached basis and remains false. This is a source-verified cross-layer mechanism, not an end-to-end repro.

**Fix:** return executed scenario revision and canonical assumptions fingerprint/snapshot; bind results to that execution provenance, and compare with current editor/list basis. Require expected_revision on stored-scenario execution or explicitly show that the current saved revision was used.

### FE10 — Medium, product gap: Older saved plans are inaccessible past the first 50

**Primary:** `/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/src/app/(app)/planner/page.tsx:525`, `:1622`.

The query always requests limit 50/offset 0; the UI renders a showing-first notice but no next-page/search route. Unlike saved simulation scenarios, a farmer cannot open/delete/download a plan beyond that fixed slice using the app. Weekly use reaches this threshold within a year. Invalid legacy plans are also filtered out of the UI (`:544`) instead of shown for recovery/removal.

**Fix:** add URL-based pagination/search and visible invalid-document recovery. An honestly labelled cap does not make old saved work accessible.

### FE11 — Medium, product limitation: Offline duties survive only while the current authenticated page remains alive

**Primary:** `/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/public/sw.js:15`, `:47`; `/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/src/lib/auth-context.tsx:596`.

The service worker caches shell HTML/static assets but no authenticated read snapshot. A reload while offline starts AuthProvider with no user, performs network-only refresh/farm discovery, exhausts retries and redirects to a network-only PIN login. Even if the shell's hashed resources are cached, the worker cannot reopen the duty board and continue. Existing offline e2e journeys keep the page alive while disconnecting; they do not establish reload/restart resilience. Installation also does not explicitly precache the shell's dependent hashed JS/CSS.

**Fix:** define secure shared-tablet offline unlock/resume policy, persist a minimal actor/farm scoped duty snapshot and outbox transactionally, reconcile with the server on reconnect, and test first install, reload, device restart, update, forced logout and shift handover. Present offline capability precisely until these flows exist.

### FE12 — Low: Owner benchmarks conflate fetch failure with no data and can silently retain stale figures

**Primary:** `/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/src/app/(app)/owner/page.tsx:89`, `:203`.

Benchmarks !payload renders the empty message even after a query error, without retry; cached overview/benchmark payloads remain rendered on a background error without the StaleDataNotice used by most domain pages. Owner operational prioritization can therefore look current or absent when it failed to refresh.

**Fix:** use explicit initial-loading, unavailable, empty, stale and fresh states; show updated-at timestamps and retry. Apply the same contract to screening stats/list failure states.

### FE13 — Low, conditional compatibility risk: Photo upload bypasses the API timeout compatibility fallback

**Primary:** `/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/src/components/screening-check-dialog.tsx:175`.

Direct S3 upload calls AbortSignal.timeout unconditionally, while api-client feature-detects timeout/any with a manual fallback. On a browser/webview without timeout, API calls work but upload throws before fetch. No specific deployed browser version was reproduced by this agent.

**Fix:** share the compatible timeout helper or define/enforce the minimum supported browser. Verify the oldest supported tablet webview.

## Additional independently reproduced domain findings

See `/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/04-frontend-domain-pages.md` for full evidence:

1. **Medium:** pending Insurance Add/Renew/Claim can be dismissed and remounted, then the old success closes/discards the new form (`/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/src/app/(app)/finance/insurance/page.tsx:204`, parent close callbacks `:1047`). Actual Add draft-loss probe passed; Renew/Claim share the source mechanism.
2. **Medium:** animal phenotype selects stay editable during a pending PATCH; the screen accepts newer values, but the old captured values are saved and the dialog closes (`/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/src/app/(app)/animals/[id]/page.tsx:423`, `:438`). Actual current-component probe passed.
3. **Medium/Low:** same-route Finance URL filter changes retain page offset 50 and display an empty page when two matching records exist (`/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/src/app/(app)/finance/page.tsx:626`). Actual current-component probe passed; Previous provides manual recovery.
4. **Low:** animal health-history Add event (`/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/src/app/(app)/animals/[id]/page.tsx:2097`) and empty health-log Record health event (`/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/src/app/(app)/health/page.tsx:1280`) navigate through `/health/new` without recognized create intent. The shim redirects to `/health`; its hydration returns unless task_id/animal_id/purchase_batch_id exists (`/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/src/app/(app)/health/page.tsx:874`). Neither link opens the form, and the animal link loses its animal context. Complete source trace only; no runtime probe for this fourth finding.

Three targeted reproduction tests passed in 1.48 s. Copy retained at `/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/evidence/herdly-frontend-domain-pages-probe.test.tsx`; temporary test removed.

## Clinical finding to combine with backend report

Backend's ten-photo PostgreSQL probe confirmed quality_problem=true + flagged=false becomes HEALTHY. Frontend `/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/src/app/(app)/screening/page.tsx:364` renders that healthy status green; the run detail loop `:476` reads detail only for CROSS_CHECK.agrees and never surfaces quality_problem/reupload need. Searching current app/components/catalogs found no quality_problem use. Thus the drilldown reinforces the false healthy claim, rather than allowing the farmer to discover the unusable evidence. Fix backend outcome semantics and frontend quality/uncertainty states together. Do not count this twice in the global audit.

## Product improvements, in order

1. **Establish operational trust.** Durable actor/farm scoped outbox, no silent data loss, true clinical/AI uncertainty states, complete treatment-round evidence, safe handover and conflict resolution. Success must mean durable evidence. Keep sync failures/rejections in a reviewable ledger rather than deleting the record after a toast.
2. **Complete the five real journeys.** Manager creates a worker through the UI; PIN worker joins the tablet; password worker completes first rotation; disconnected worker captures/resumes duties; manager reconciles pending work and clinical alerts at shift handover. Measure completion time, abandoned tasks, duplicate/missing writes and recovery rate on real farms.
3. **Make field work fast.** Unified Today view for risk/duties, barcode/QR tag scan, camera-first animal lookup, batch health/feed work with preview/coverage, optional Telugu voice/photo notes, clear confirmations for high-impact actions, and undo/correction with an audit trail. Add an in-shell language switch on worker surfaces; prefer 44px hit areas even for the simulation's 16px help buttons (`/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/src/app/(app)/simulation/editor-widgets.tsx:40`, `:78`).
4. **Make planning understandable.** Guided basic assumptions then advanced settings, fully translated explanations, comparable scenarios with provenance/version, obvious uncertainty and constraints, difference review for conflicts, searchable saved plans and mobile recommendation cards. Avoid relying on 14-column financial tables as the primary mobile decision interface (`/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/src/app/(app)/simulation/results-visuals.tsx:230`).
5. **Control complexity.** Decompose route-level orchestration from domain dialogs/selectors/results. Simulation currently has 4,251 lines, 32 useState calls and 253 function/callback nodes. Standardize query/error/freshness states and asynchronous dialog ownership instead of copying farm/attempt/lock rules into every page. Derived state/selectors and explicit state machines are valuable where transitions truly need them.
6. **Set measurable quality budgets.** Production mobile bundle budgets, slow-network bootstrap timing, Web Vitals, accessible keyboard/screen-reader flows, translations reviewed by field operators, telemetry for dropped/rejected writes and abandoned onboarding, and a real-photo AI evaluation set. Split/lazy-load locale/help/domain bundles if measurements justify it; the two raw locale source files total 489,689 bytes, and both are statically imported by the app-wide provider. This byte count is not the actual compressed production bundle size.

## Review coverage and limits

`/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/evidence/herdly-audit-frontend-files.txt` and `/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/evidence/herdly-audit-frontend-ledger.json` list every inventoried path and method. The main mechanical inventory covers 556 non-test production/model/config/helper files; full-source AST inspection covers 550 JS/TS files with zero parse errors, recorded in `/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/evidence/herdly-audit-frontend-ast.json`. Generated files, locale corpora and remaining large domain pages have explicitly different review depth. This is **not** a claim that all 81,469 inventoried lines received line-by-line semantic review.

The combined ledger now records 135 complete-source manual reads by the frontend worker and 17 additional complete-source delegated reads. All inventoried application routes, components, hooks, state/transport libraries, scripts, styles and configs received complete visible reads, including the 4,251-line Simulation page and all 13 assigned domain pages (17,383 lines). The English/Telugu catalogs retain the separately stated parity/sampled-semantic classification. Remaining different-depth entries are generated APIs/models (403 AST/structural entries plus one manually reviewed model), the two locale corpora (complete key/placeholder parity, sampled translation semantics), three sampled test fixtures, one icon visual review, three raster metadata reviews, and one transient generated e2e state artifact reviewed by metadata only. Supporting route and instruction reads are also included. Full source exposure is not dynamic branch coverage, and generated/locale structural checks are not claimed as exhaustive semantic review.

Mechanical generated-model parity inspected 231 interfaces against shared/openapi.json properties and required flags: zero mismatches. This validates those structural fields, not every enum/type/constraint or live backend contract. Catalog parity examined 2,797 English and 2,797 Telugu keys: zero missing keys, zero empty translations, zero interpolation-variable differences. This validates catalog completeness, not every Telugu translation's correctness or English literals outside those catalogs.

Root owns lint/typecheck/full Vitest/e2e/build. This worker ran only targeted probes above. No complete production load/security/device accessibility validation was performed. Reproduction artifacts include `/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/evidence/herdly-offline-probe.cjs`, `/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/evidence/herdly-worker-setup-probe.json`, `/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/evidence/herdly-planner-conflict-probe.json`, `/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/evidence/herdly-worker-handover-probe.test.tsx` and the delegated domain probe. The application working tree is unchanged.
