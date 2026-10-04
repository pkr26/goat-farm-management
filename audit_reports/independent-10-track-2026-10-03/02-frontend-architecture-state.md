# Track 02 — Frontend Architecture and State Audit

**Commit:** `1ca78768`  
**Audit date:** 2026-10-03  
**Scope:** Auth/session and farm state, React Query ownership, async continuation safety, form/editor concurrency, idempotent request ownership, permission state, error handling, and representative page architecture under `frontend/src`. Generated endpoints were used only to follow current call paths.  
**Independence:** This pass did not read prior audit reports, improvement/status plans, or mutation-campaign reports. Historical labels embedded in source comments were not treated as evidence.

## Result

I found **5 current issues: 0 critical, 0 high, 4 medium, and 1 low**. None was claimed as a runtime reproduction. They are source-inferred interleavings/control gaps with high-confidence current code paths; the focused test suite passed and does not exercise the missing interleavings.

| ID | Severity | Confidence | Finding |
| --- | --- | --- | --- |
| FE-01 | Medium | High | Cross-tab sign-out has no signal when the shared farm key is absent or storage is unavailable |
| FE-02 | Medium | High | Simulation calibration and herd imports can overwrite edits made after the request starts |
| FE-03 | Medium | High | Screening exports and walkthrough continuations can complete in the UI after farm ownership changes |
| FE-04 | Medium | High | A failed worker-board refresh is hidden behind cached duties while actions stay enabled |
| FE-05 | Low | High | The worker board creates a permission query observer for every rendered duty |

## Findings

### FE-01 — Cross-tab sign-out has no signal when the shared farm key is absent or storage is unavailable

**Severity:** Medium  
**Confidence:** High  
**Status:** Source-inferred; not reproduced in a real multi-tab browser

The only cross-tab session-death signal is removal of `goatfarm.farmId`. `clearSession` clears in-memory identity and then calls `clearStoredFarmId()` (`frontend/src/lib/auth-context.tsx:265-285`); that helper only invokes `localStorage.removeItem` and silently tolerates missing/unavailable storage (`frontend/src/lib/auth-context.tsx:93-124`). The receiving tab listens only for that key and interprets `newValue === null` as logout (`frontend/src/lib/auth-context.tsx:534-552`).

Removing an absent key does not change storage and therefore does not emit a cross-document `storage` event. This is a normal state: a signed-in user with no farms has no stored key (`frontend/src/lib/auth-context.test.tsx:195-203`), and the production helper deliberately permits storage to be blocked. The current test fabricates a `StorageEvent` directly (`frontend/src/lib/auth-context.test.tsx:611-622`, `677-690`), so it proves receiver behavior but not that a sender can produce the event in the absent-key case.

**Impact and preconditions:** With two same-origin tabs open, signing out in one tab while the farm key is absent (farmless account, storage cleared, or storage blocked) leaves the other tab's in-memory user, bearer token, and query cache intact. That tab is not immediately redirected or locally torn down. How long API access remains usable depends on server token revocation and was outside this frontend-only audit, but stale authenticated UI state is certain until another local auth failure, refresh, or reload.

**Recommendation:** Broadcast auth transitions independently of farm selection. A `BroadcastChannel` message or a dedicated local-storage auth-event key containing a fresh nonce/timestamp should be written on every logout, with a storage-unavailable fallback (for example, visibility-triggered session validation). Keep the farm-selection key for farm changes only. Add a real multi-page browser test for a farmless account and for an absent key.

### FE-02 — Simulation calibration and herd imports can overwrite edits made after the request starts

**Severity:** Medium  
**Confidence:** High  
**Status:** Source-inferred; existing race tests cover loader-vs-loader ordering, not loader-vs-edit ordering

The simulation editor intentionally has two epochs: loader intent and content edits. Its comment says field edits do **not** advance the loader epoch (`frontend/src/app/(app)/simulation/page.tsx:1082-1096`), while both ordinary and nested edits advance only `editorContentEpochRef` (`frontend/src/app/(app)/simulation/page.tsx:1395-1401`, `1427-1438`).

Both affected loaders ignore the content epoch:

- “Use current herd” captures only `editorEpochRef`, then merges returned head counts over the latest editor state (`frontend/src/app/(app)/simulation/page.tsx:1683-1724`). A head-count edit made during the fetch is therefore replaced.
- Calibration captures the loader epoch and calibration-parameter generation, then replaces the complete assumptions object and event list (`frontend/src/app/(app)/simulation/page.tsx:1738-1770`). Any editor change made during the fetch is therefore replaced.

The page remains editable during both requests. Their buttons disable only themselves (`frontend/src/app/(app)/simulation/page.tsx:3320-3329`, `3361-3370`); the editor fieldset is disabled only for an explicitly requested defaults load (`frontend/src/app/(app)/simulation/page.tsx:3465-3473`).

**Impact and preconditions:** If an operator starts calibration or herd import and edits an affected value before the response returns, a successful response silently discards the later edit. Calibration can discard changes anywhere in the assumptions editor; herd import discards edits to the imported herd counts. Long-running farm-data operations and mobile latency make the window plausible.

**Recommendation:** Capture `editorContentEpochRef.current` at request start and refuse automatic application if it changed, with an explicit “results ready—replace my edits” choice. Alternatively, disable the entire portion that will be replaced for the request duration. Model whole-editor replacement as an explicit reducer/state-machine transition so all loaders share the same ownership rule. Add deferred-response tests that edit a field between request start and resolution for both actions.

### FE-03 — Screening exports and walkthrough continuations can complete in the UI after farm ownership changes

**Severity:** Medium  
**Confidence:** High  
**Status:** Source-inferred; not exercised with a farm switch during a deferred request

The screening export awaits a generated request and then creates a download and global success/error toast without capturing or rechecking farm scope (`frontend/src/app/(app)/screening/page.tsx:145-173`). Generated calls flow through `customInstance` without a required request scope (`frontend/src/api/custom-instance.ts:55-60`); `apiFetchEnvelope` fences only the authentication epoch, not the farm epoch (`frontend/src/lib/api-client.ts:1031-1043`). The transport correctly preserves the old farm header for the request, but that does not stop its page-level continuation.

The walkthrough dialog has a similar ownership token that advances only when `open` changes (`frontend/src/components/screening-check-dialog.tsx:68-97`). Upload and finish continuations compare that token (`frontend/src/components/screening-check-dialog.tsx:121-143`, `159-212`, `215-237`), but unmounting does not invalidate it. A farm change intentionally remounts the application-page subtree via `key={farmId}` (`frontend/src/app/(app)/app-layout-client.tsx:440-448`), so an outstanding old component can still pass its private epoch check and fire a toast, download, or `onFinished` callback after it is detached. `onFinished` starts a list refetch (`frontend/src/app/(app)/screening/page.tsx:253-259`).

**Impact and preconditions:** Switch farms (including via another tab) while an export, batch creation/upload, or batch submission is pending. The server request remains scoped to the farm from which it began, but its late UI side effects occur after the operator is in the new farm: the old farm's dataset can download without warning, and old walkthrough completion/error toasts or refresh callbacks can appear in the new context.

**Recommendation:** Capture `currentRequestScope()`/a shared `captureFarmScope()` guard for every imperative screening operation, check it before every external side effect, and invalidate the local operation epoch in an unmount cleanup. Prefer an abort controller owned by the page/dialog in addition to the scope guard. Add deferred export and submit tests that switch farm or unmount before resolution.

### FE-04 — A failed worker-board refresh is hidden behind cached duties while actions stay enabled

**Severity:** Medium  
**Confidence:** High  
**Status:** Source-inferred from the current TanStack Query state branches

The worker board derives `payload` from any retained successful data regardless of `query.isError` (`frontend/src/app/worker/page.tsx:133-149`). It recognizes the error only when deciding whether to persist a fresh offline snapshot (`frontend/src/app/worker/page.tsx:150-163`). In rendering, it shows an error only when there is no payload; with cached data it renders the normal duty list (`frontend/src/app/worker/page.tsx:261-341`). Complete and Skip remain available (`frontend/src/app/worker/page.tsx:85-108`), and `runDutyMutation` has no stale-query/error gate (`frontend/src/app/worker/page.tsx:178-225`).

The codebase already documents the relevant Query behavior in the permission hook: retained successful data remains present when a background refetch fails, so permissions explicitly fail closed on `query.isError` (`frontend/src/lib/use-permissions.ts:28-51`). The worker board does not make the equivalent stale-data state visible.

**Impact and preconditions:** After one successful task load, a focus-triggered/background refresh fails while the server-side duty set or status has changed. The worker sees the previous list as current and can act on it. Online writes may be rejected by server conflict checks; intermittent/offline writes can be queued and later require review. Either path creates avoidable confusion on a field-facing workflow.

**Recommendation:** Render a prominent stale/offline banner whenever `query.isError && payload`, including last-success time and Retry. Decide explicitly whether actions remain available: either disable them until refresh, or label them as offline/queued actions and preserve the current reconciliation behavior. Add a test with initial success followed by a failed refetch and assert the chosen UX and action policy.

### FE-05 — The worker board creates a permission query observer for every rendered duty

**Severity:** Low  
**Confidence:** High  
**Status:** Source-inferred architecture/performance issue

`WorkerBoardContent` already receives the shared `PermissionsState` (`frontend/src/app/worker/page.tsx:125-149`), but each `DutyCard` calls `usePermissions()` again solely to validate its action URL (`frontend/src/app/worker/page.tsx:45-56`). The board requests up to 200 active tasks and maps every visible task to a card (`frontend/src/app/worker/page.tsx:133-146`, `294-329`). React Query deduplicates the underlying request, so this is not a claim of 200 network calls; it is up to 200 extra query observers, permission-set allocations, and subscriptions/renders. The hook's own type comment says page content should share one observer's result (`frontend/src/lib/use-permissions.ts:16-18`).

**Impact and preconditions:** Large worker boards amplify render and notification work on a low-power tablet, particularly when permissions refetch or auth/farm state changes. It also weakens the otherwise clear single-owner permission architecture.

**Recommendation:** Pass `perms.can` (or the already-computed `actionPath`) into `DutyCard`. Keep one permission observer at the worker-layout/page boundary.

## Strengths observed

- The API transport centralizes same-origin path validation, in-memory bearer tokens, bounded requests, 401 refresh, response parsing, and authentication-epoch assertions. Protected mutations preserve a captured farm header across retries (`frontend/src/lib/api-client.ts:866-893`, `1001-1025`).
- Farm selection synchronously cancels queries, clears URL-only cache keys, updates the transport scope, and keys the main application subtree by farm (`frontend/src/lib/auth-context.tsx:244-263`; `frontend/src/app/(app)/app-layout-client.tsx:440-448`).
- Permission state deliberately fails closed after background errors instead of authorizing from stale cached grants (`frontend/src/lib/use-permissions.ts:28-51`).
- The simulation editor has explicit loader generations, conflict refreshes, farm-scope checks, and substantial deferred-response coverage. FE-02 is a narrow missing ownership edge rather than an absence of concurrency design.
- Worker mutations capture request/farm scope, persist their idempotency key before sending, fence old-scope continuations, and separate definitive rejection from queued retry (`frontend/src/app/worker/page.tsx:178-258`).

## Verification performed

- `pnpm typecheck` — passed.
- `pnpm lint` — passed with zero warnings.
- Focused Vitest run over auth context, API races, idempotency, screening dialog, and worker board — **7 files, 239 tests passed**.
- Simulation deferred-response suites (`page.events` and `page.editor-projection`) — **2 files, 89 tests passed**.

## Limits

- This was a source/static and jsdom-focused audit. I did not run a real multi-tab browser, throttled mobile network, or production build against a live backend/object store.
- Server token-revocation duration, server idempotency behavior, and authorization enforcement were not evaluated; impacts above distinguish known frontend state from backend-dependent consequences.
- Offline/PWA internals, visual accessibility, dependency security, backend authorization, and deployment controls belong to other audit tracks and were not exhaustively assessed here.
- No application code was modified.
