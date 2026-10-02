# Offline-First & Client Data Layer Audit (2026-10-01)

Auditor 07 of 10 — angle: the offline-first client data layer as a distributed system (sync, idempotency, conflict, cache correctness, token lifecycle).

Scope: `frontend/src/lib/` (all 34 non-test source files incl. `i18n/`), `frontend/public/sw.js`, `frontend/src/app/manifest.ts`, `frontend/src/proxy.ts`, `frontend/next.config.ts`, service-worker registration, `frontend/src/hooks/`, plus a usage scan of `src/app/` and `src/components/`. Backend consulted for contract cross-checks only.

## Executive summary

This is an unusually disciplined client data layer. The token lifecycle (memory-only access token, httpOnly refresh cookie, Web-Locks-serialized cross-tab refresh, single-flight per realm, epoch fencing on every await, "rejected vs unavailable" refresh taxonomy), the idempotency registry (per-operation keys, same-key automatic retry, key retention on ambiguous failure, cross-reload recovery keyed by actor+farm digest with credential-body carve-outs), and the offline queue (versioned fail-closed persistence, actor+farm scoping, merge-safe drain, idempotency-key carry-through) each show multiple generations of audited hardening inline. The i18n catalogs are in full key parity (2787/2787) with verified placeholder parity. I found **no Critical defects and no duplicate-write path**: every replay path I traced reuses the same Idempotency-Key, and the server-side task-state 409 backstop covers the rest.

The one High finding is a policy contradiction between two components: the offline queue's own drain logic deliberately keeps records on 401 ("a momentarily dead session never destroys field writes"), but a *definitively* rejected refresh triggers `onAuthFailure → clearSession → wipeOfflineQueue()`, silently destroying every queued field completion the moment the drain that was trying to save them discovers the dead session — with no end-shift-style warning. The remaining findings are robustness/consistency edges: 5xx answers are treated as retryable by the drain but as non-queueable at the immediate attempt; `AbortSignal.any`/`AbortSignal.timeout` have no fallback (unlike the Web Locks path); queue records are trusted on read without re-validating the replay allowlist or scrubbing persisted headers.

Severity counts: **0 Critical, 1 High, 3 Medium, 5 Low, 4 Info** (+ positive observations below).

## Findings

### [High] Forced logout silently wipes the offline queue mid-drain, destroying queued field writes

- Location: `frontend/src/lib/auth-context.tsx:258-280` (clearSession), `:493-506` (handleAuthFailure), `frontend/src/lib/api-client.ts:835-843` (rejected-refresh path), `frontend/src/lib/offline-queue.ts:311-322` (drain 401 branch), `:340-343` (write-back).
- Evidence (traced lifecycle, confirmed):
  1. Worker offline → completions enqueued (actor+farm scoped, keys attached).
  2. Connectivity returns → drain replays record #1 via `apiFetch` → 401 (access token expired).
  3. `apiResponseOnce` → `refreshSessionOutcome()` → cookie dead (expired family, or revoked by an owner password reset — the account dialog documents that password change "signs out every other device") → `{kind:"rejected"}` → `setAccessToken(null)` + `onAuthFailure?.()` → `handleAuthFailure` → `clearSession()` → `wipeOfflineQueue()`.
  4. `ApiError(401)` propagates to the drain's catch: `status === 401` → record KEPT, `stopped = true` (the queue's explicit design: "a later drain after re-login… can still deliver it").
  5. Drain write-back: `readOfflineQueue(storage)` now returns `[]` (wiped) → `writeQueue(storage, [])` → `{replayed:0, remaining:0, rejected:0}` — `onRejected` never fires.
  6. Worker is redirected to `/worker/login`; after re-PIN the queue is empty. No toast, no dialog (the end-shift confirm dialog covers only the explicit path).
- Impact: silent, total loss of queued offline duty completions exactly when the queue's promise ("must survive connectivity loss exactly once") matters — after a long offline stretch (refresh cookie > 14 days) or an owner-side password reset that revokes the tablet's family while records sit queued. Borderline Critical under the "lost offline writes" rubric; rated High because it requires the refresh family to be dead (not the common path). Note the drain itself already refuses to replay foreign actors, so keeping the records would have been safe.
- Fix: on forced logout, do not wipe actor-scoped queue records (they cannot leak to the next worker — the drain skips non-matching `actorScope`); or, if the wipe policy stays, count the destroyed records and surface them the way `rejected` is surfaced (`worker.offlineRejected_*`), and mention the loss on the PIN pad.

### [Medium] 5xx answers: retryable during drain, non-queueable at the immediate attempt

- Location: `frontend/src/lib/offline-queue.ts:196-207` (`isOfflineQueueableFailure`), `:333-334` (drain 5xx branch), `frontend/src/app/worker/page.tsx:199-215`.
- Evidence: `isOfflineQueueableFailure` returns false for any numeric status in `[400,500)`; a 5xx `ApiError` falls through to the name check (`"TypeError" | "TimeoutError"`) — but `ApiError` never sets `.name` (constructor at `api-client.ts:433-447` leaves it `"Error"`), so a 502/503/504 answers **false** → immediate completions during a server/proxy outage are rolled back with an error toast, never enqueued. The drain, in contrast, treats the same 5xx as "back off — keep the record, stop here".
- Impact: asymmetric classification. A completion attempted while the backend (or a rural carrier proxy returning 502/504) is failing is lost unless the worker re-taps; the identical failure one drain later preserves the record. No corruption (task stays PENDING server-side and reappears), but the offline-first guarantee is inconsistent across the two halves of the same subsystem.
- Fix: treat `status >= 500` as queueable in `isOfflineQueueableFailure` (they are "server could not answer", matching the refresh path's `isTransientRefreshStatus` philosophy), or document the deliberate distinction.

### [Medium] `AbortSignal.any` / `AbortSignal.timeout` hard dependency with no fallback

- Location: `frontend/src/lib/api-client.ts:733-734` (rawFetch).
- Evidence:
  ```ts
  const timeoutSignal = AbortSignal.timeout(timeoutMs);
  const signal = init.signal ? AbortSignal.any([init.signal, timeoutSignal]) : timeoutSignal;
  ```
  No feature detection or fallback (verified: no `typeof AbortSignal.any` guard in the file). `AbortSignal.any` requires Chrome 116+ / Safari 17.4+ / Firefox 124+. Contrast: the Web Locks path at `api-client.ts:225-235` *does* degrade for pre-Web-Lock browsers.
- Impact: on iOS/Safari < 17.4 (early-2024 iPads are common shared tablets), `AbortSignal.any is not a function` throws synchronously inside `rawFetch` for **every** API request. Worse on the worker surface: that `TypeError` is exactly what `isOfflineQueueableFailure` classifies as queueable — each completion shows "Saved — will send when online" (`worker.queuedToast`) for a request that never left the device. The board itself cannot load, so the breakage is quickly visible, but the false "saved" affordance misleads.
- Fix: feature-detect and fall back to the caller signal alone (bounded lifetime lost, but functional), or to a manual `setTimeout(() => controller.abort())` composition; alternatively pin the support matrix explicitly and refuse to register the worker shell below it.

### [Medium] Offline-queue records are trusted on read: no allowlist re-validation, headers persisted verbatim

- Location: `frontend/src/lib/offline-queue.ts:60-82` (`wellFormed`), `:154-164` (enqueue), `:283-296` (drain replay).
- Evidence: `enqueueOfflineMutation` fail-closes on `isOfflineQueueableMutation` (only `POST /api/tasks/{id}/(complete|skip)`), but `readOfflineQueue`/`wellFormed` validate only shape (`v`, string fields, `path.startsWith("/api/")`), and `drainOfflineQueue` replays any well-formed record matching scopes without re-checking the allowlist. `record.headers` persists `init.headers` verbatim — today only `Idempotency-Key`, but nothing strips `Authorization`/`Cookie` if a future caller passes them, and `wellFormed` would happily accept them into `localStorage`.
- Impact: a record written by an older deploy with a wider allowlist, or a future caller regression, replays through the drain without a server-safe-write re-check; a future caller leaking credential headers into `init.headers` would persist them to disk. No current exploit path (today's only enqueue site passes a single header), hence Medium not High.
- Fix: re-check `isOfflineQueueableMutation(record.path, record.method)` in the drain loop (drop-and-count otherwise), and strip/validate header names at enqueue (allowlist: `Idempotency-Key`, `Content-Type`).

### [Low] Concurrent duty mutations: one failure's rollback clobbers the other's optimistic strike-through

- Location: `frontend/src/app/worker/page.tsx:179` (`setBusyId(task.id)` disables only that duty's buttons), `frontend/src/lib/task-optimistic.ts:44-48` (rollback restores whole-board snapshots).
- Evidence: `busy={busyId === task.id}` — duties A and B can both be in flight. Each `applyOptimisticTaskPatch` snapshots all board caches; A's failure rollback restores A's pre-patch snapshot, which does not contain B's already-applied patch (B still queued/in flight).
- Impact: cosmetic-only optimistic-UI inconsistency (B's row un-strikes though its write is queued/will land; refetch reconciles). No data impact.
- Fix: per-task snapshot (patch only `task.id`'s row on rollback) or serialize mutations like the manager surfaces do (`useSingleFlight`).

### [Low] Worker duty success path is not fenced by `captureFarmScope`

- Location: `frontend/src/app/worker/page.tsx:186-188`.
- Evidence: the catch path checks `if (!farmScope())` before rollback/toasts; the success path runs `toast.success(...)` and `await query.refetch()` unconditionally — a farm-scope change (end-shift/farm switch) mid-flight still toasts "Marked done." and triggers a board refetch under the new scope.
- Impact: harmless today (refetch after `queryClient.clear()` just repopulates the current scope; the write itself carried the captured `X-Farm-Id`), but it violates the fence contract every other write surface follows.
- Fix: `if (farmScope()) { toast; await query.refetch(); }`.

### [Low] Queue badge and end-shift confirm count include other actors'/farms' records

- Location: `frontend/src/app/worker/layout.tsx:145-147, 232` (`offlineQueueDepth()` / `depth`).
- Evidence: `offlineQueueDepth()` counts ALL records; the drain only processes records matching the current `actorScope`+`farmScope`. Residue is unlikely (teardown wipes) but a pinned-farm change or a crash-before-teardown leaves foreign records inflating the badge and the "N saved duties… deletes them permanently" confirm copy (which also overstates: those foreign records belong to no one on this tablet).
- Fix: count with the same scope filter the drain uses.

### [Low] `formatPersistedKg` hardcodes `en-IN` grouping

- Location: `frontend/src/lib/persisted-numbers.ts:13`; contrast `frontend/src/lib/format.ts:52-57` (`formatNumber` is language-aware and its comment records the 2026-09-28 audit that fixed exactly this class).
- Evidence: stock-ledger quantities always render with English digit grouping in Telugu sessions while sibling surfaces group `te-IN`.
- Impact: minor locale inconsistency on the stock ledger.
- Fix: route through `formatNumber(value, { minimumFractionDigits: 1, maximumFractionDigits: 3 })`.

### [Low] `formatDate` with a datetime input renders the string's wall date, not the farm-timezone date

- Location: `frontend/src/lib/format.ts:225-245`.
- Evidence: the regex accepts full ISO datetimes; display uses the `y-m-d` from the string as-is, so `2026-08-05T23:30:00Z` renders "5 Aug 2026" though the farm day (IST) is already 6 Aug. Overdue logic elsewhere correctly uses `farmToday()`.
- Impact: only reachable if a caller passes a datetime where a farm-local calendar date is expected; current callers pass date-only `due_date` values. Consistency hazard, not an observed bug.
- Fix: either narrow the accepted grammar to date-only, or convert datetimes through the farm timezone like `formatFarmDateTime`.

### [Info] Service-worker cache never evicts stale hashed assets (constant cache name)

- Location: `frontend/public/sw.js:14` (`CACHE = "herdly-worker-v2"`), `:32-39`.
- Evidence: the deploy-freshness fix (network-first shell + `registration.update()` on every shell mount, `worker/layout.tsx:95-111`) works without bumping the cache name, so `activate`'s cleanup deletes nothing on ordinary deploys and every deploy's `/_next/static` chunks accumulate until browser storage pressure evicts the whole cache (losing the offline shell until the next online visit).
- Fix (optional): prune entries not referenced by the current shell, or bump the cache name per release (precache requires connectivity anyway).

### [Info] Cross-reload idempotency recovery is dropped on a backward clock jump

- Location: `frontend/src/lib/idempotent-request.ts:101-102` (`record.expiresAt > now + RETRY_KEY_TTL_MS` pruned as junk).
- Evidence: a >2-minute backward clock jump within the 2-minute recovery window makes a legitimate record look future-dated and prunes it; the retry then mints a fresh key. Extreme edge (deliberate anti-poisoning bound); the server-side task-state/idempotency backstop still prevents duplicates on the routes that matter.

### [Info] Ambiguous send + page reload is an inherent gap for the worker surface

- Location: `frontend/src/app/worker/page.tsx:165-215` (enqueue happens only in the observed-failure catch).
- Evidence: the registry's sessionStorage recovery covers orval-auto-key routes; the worker page's caller-provided key is deliberately non-persistent (`idempotent-request.ts:508` — `!callerProvidedKey`), so a completion whose response is lost AND whose page is killed before the catch runs is never enqueued. Accepted trade-off (the caller key is the right design for long-lived queue entries); noted for completeness.

### [Info] Reload-mid-drain and cross-tab drains are safe

- Location: `frontend/src/lib/offline-queue.ts:245-348`.
- Evidence (positive confirmation of a suspected risk): the `drainInFlight` realm guard plus idempotency keys make two tabs' concurrent drains of the same record a same-key double-send that the server collapses; the merge-safe write-back re-reads live storage. No queue inconsistency found.

## Coverage manifest

Every in-scope file below was read in full with `Read` (chunked where >2000 lines) unless noted. `*.test.*` files were used only as intent documentation where cited.

| File | Status | Notes |
|---|---|---|
| `src/lib/api-client.ts` (925) | Read line-by-line | Token lifecycle, refresh, locks, timeouts, path guard |
| `src/lib/idempotent-request.ts` (613) | Read line-by-line | Registry, persistence, allowlists, carve-outs |
| `src/lib/offline-queue.ts` (380) | Read line-by-line | Enqueue/drain/worker lifecycle traced |
| `src/lib/auth-context.tsx` (660) | Read line-by-line | Session establish/teardown, cross-tab, farm tombstones |
| `src/lib/format.ts` (246) | Read line-by-line | UTC/tz handling, money/date math |
| `src/lib/i18n/en.ts` (3245) | Read in full | Catalog |
| `src/lib/i18n/te.ts` (3212) | Read (full pass + programmatic verification) | Key parity 2787/2787, placeholder parity, no untranslated-critical or dangerous values; 23 identical values are legitimate (₹, NPV/IRR/BCS/`{noun}` templates) |
| `src/lib/i18n/index.tsx` (143) | Read line-by-line | Provider, interpolate, fallback |
| `src/lib/backend-rewrites.ts` | Read line-by-line | BACKEND_URL host policy |
| `src/lib/backend-caps.ts` | Read line-by-line | Parity-pinned caps |
| `src/lib/csp.ts` | Read line-by-line | Origin parsing, nonce policy |
| `src/lib/active-language.ts` | Read line-by-line | Module store mirror |
| `src/lib/farm-scope-guard.ts` | Read line-by-line | Epoch fence |
| `src/lib/farm-vocabulary.ts` | Read line-by-line | Species mirror |
| `src/lib/mutations.ts` | Read line-by-line | Shared error rendering hook |
| `src/lib/query-invalidation.ts` | Read line-by-line | Farm-data predicate |
| `src/lib/task-optimistic.ts` | Read line-by-line | Optimistic patch/rollback |
| `src/lib/task-action-access.ts` | Read line-by-line | 409-gate mirrors, action-path perms |
| `src/lib/task-title.ts` | Read line-by-line | title_key resolution |
| `src/lib/enum-labels.ts` | Read line-by-line | Enum maps, te fallback |
| `src/lib/persisted-numbers.ts` | Read line-by-line | Storage-precision floors |
| `src/lib/server-error-phrases.ts` | Read line-by-line | Code/phrase/status mapping |
| `src/lib/safe-storage.ts` | Read line-by-line | Guarded storage accessor |
| `src/lib/brand.ts` | Read line-by-line | Storage-key namespace policy |
| `src/lib/bucket-sex.ts` | Read line-by-line | CHECK mirror |
| `src/lib/use-permissions.ts` | Read line-by-line | Fail-closed permissions |
| `src/lib/use-single-flight.ts` | Read line-by-line | Double-click guard |
| `src/lib/use-url-state.ts` | Read line-by-line | URL state, offset ceiling |
| `src/lib/utils.ts` | Read line-by-line | `cn`, `safeAppPath` |
| `src/lib/permission-envelope.ts` | Read line-by-line | Shared permissions fetch |
| `src/lib/permission-navigation.ts` | Read line-by-line | Landing routes, farm-switch path rules |
| `src/lib/image-deps-guard.ts` | Read line-by-line | Build-time sharp/libheif gate |
| `src/lib/simulation-field-help.ts` | Read line-by-line | Static help copy |
| `src/api/custom-instance.ts` (adjacent) | Read | Orval mutator wiring every generated call into apiFetchEnvelope |
| `public/sw.js` (92) | Read line-by-line | Cache strategy, install/activate/fetch |
| `src/app/manifest.ts` | Read line-by-line | PWA manifest; icons verified present in `public/` |
| `src/proxy.ts` | Read line-by-line | Nonce CSP, matcher exclusions |
| `next.config.ts` | Read line-by-line | Rewrites, headers, sw/manifest no-cache |
| `src/hooks/use-mobile.ts` | Read line-by-line | `useSyncExternalStore` |
| SW registration | Located + read | `src/app/worker/layout.tsx:95-111` (only registration site; `rg "serviceWorker|workbox"` — no workbox) |
| Backend cross-check | `rg Idempotency backend/app/api/tasks.py` | `IdempotencyKey` accepted on complete/skip/verify/reject — matches the client allowlist |

Usage-scan method for `src/app/` + `src/components/` (not line-by-line, per brief): `rg` for `apiFetch(`, `Idempotency-Key`, `mutateAsync`, `invalidateFarmData`, `queryClient.clear()/cancelQueries()`, `captureFarmScope`, `signOut`, `selectFarm`, `startOfflineQueueWorkers`, `fetch(` (raw). Findings: the only raw `fetch` outside api-client is the S3 presigned upload in `src/components/screening-check-dialog.tsx:172` (correct — same-origin guard must not apply); the only direct `apiFetch` mutation sites are the worker board, worker login/setup, login, and farm-select create — every other mutation flows through orval hooks → `customInstance` → `apiFetchEnvelope` → idempotency registry (key auto-injected). Farm switch (`selectFarm`) cancels+clears the query cache before swapping `X-Farm-Id` and bumps `farmScopeEpoch`; logout/forced-logout funnel through `clearSession` (queue wipe — see High). Manager write surfaces (tasks, feeding, finance/insurance, planner, simulation) consistently fence continuations with `captureFarmScope` and invalidate via `invalidateFarmData`.

## Positive observations

1. **Token lifecycle is exemplary.** Access token in memory only (never localStorage); httpOnly refresh cookie; single-flight refresh per realm keyed by session epoch; Web Locks (`goatfarm-auth-refresh`) serialize cookie-bearing responses across tabs with a bounded wait and a fail-closed fallback story; the `rejected` vs `unavailable` refresh taxonomy (with content-type sniffing to ignore captive-portal HTML as an answer) prevents both transient-logout and infinite retry; the intentional-logout-teardown carve-out (`api-client.ts:707-719`) correctly permits exactly the signOut token transition through the cookie lock.
2. **Idempotency engineering is deep and correct.** Per-operation keys minted once and reused across the automatic network retry, retained (2-min TTL) for explicit retry after ambiguous failures, persisted cross-reload via actor+farm-scoped SHA-256 digests, with deliberate carve-outs: PIN/password bodies excluded from persistence (offline-verifier risk), void-POST batch creation excluded from both in-flight sharing and recovery, and cancellation-signal ownership conflicts rejected instead of silently ignored. The worker surface's caller-provided key stored in the queue record is exactly the right way to keep a 72-hour replay under the original key.
3. **The offline queue's persistence hygiene is strong for its size**: version field, fail-closed wholesale discard of malformed/oversized stores, count and byte bounds with oldest-first eviction, 72h TTL with future-dated stamps kept (clock-skew tolerance), scope-mismatch records skipped-not-dropped, merge-safe write-back against live storage, in-flight drain guard, per-session 429 `Retry-After` backoff, and a surfaced `rejected` count so refused writes are not lost silently.
4. **Farm-scope correctness is handled as a first-class concern**: `X-Farm-Id` captured before the first await (so refresh retries and mid-flight switches cannot retarget a tenant), `farmScopeEpoch` fencing for UI continuations, `captureFarmScope` adopted across write surfaces, `queryClient.cancelQueries()+clear()` before scope swap (URL-only cache keys!), the `/api/auth/farms` actor-scoped carve-out, and the cross-tab revoke-tombstone protocol distinguishing "farm revoked" from "session dead".
5. **The service worker gets the offline-first contract right for a multi-tenant app**: `/api` is strictly network-only (mutations never intercepted, non-GET returns early), no authenticated API response is ever cached, hashed assets cache-first, shell network-first with a bounded 4s wait and cache fallback, `sw.js`/`manifest.webmanifest` served no-cache, precache failure fails install, and a forced `registration.update()` on every shell mount closes the 24h revalidation gap.
6. **i18n integrity is mechanically verifiable and clean**: full key parity, placeholder parity, English fallback chain, raw key rendered only as a deliberate last resort, plural forms handled via `_one`/`_many` key pairs, and server errors mapped through stable machine codes first.
7. **Defense-in-depth at the transport boundary**: `assertSafeApiPath` (same-origin, no `%`, no hash, no normalization drift), `assertSafeBackendUrl` (loopback/single-label-only for both schemes), `safeAppPath` for API-supplied URLs, CSP nonce pipeline with a strict origin parser, and `safeStorage` as the single guarded storage accessor.
