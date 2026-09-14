# Frontend Session / Auth / API Layer — Red Team Audit (Part O, units O1–O8)

Date: 2026-09-13
Target: Herdly goat-farm SaaS monorepo, frontend SPA (`frontend/src`)
Scope units: O1 `lib/api-client.ts`, O2 `lib/auth-context.tsx`, O3 `api/custom-instance.ts`, O4 `/login`, O5 `/register`, O6 `/farm-select`, O7 `components/account-dialog.tsx`, O8 root dispatcher + `(app)` shell.

## Methodology

- Read every primary file end-to-end (line counts: api-client 818, auth-context 460, account-dialog 467, app-layout-client 457, farm-select 356, login 245, register 179, plus custom-instance, permission-navigation/envelope, utils/safeAppPath, format, idempotent-request, use-single-flight, use-permissions, backend-rewrites, next.config).
- Verified response-status assumptions against `shared/openapi.json` (change-password: 200-only success; DELETE account: 204; refresh: 200; farm create: 201).
- Cross-checked attacker hypotheses against the existing suites (`api-client.token-hazards.test.ts`, `api-client.refresh-outcomes.test.ts`, `api-client.races.test.ts`, `aa-auth-campaign-kills.test.tsx`, login/farm-select/register page suites) to distinguish new findings from already-pinned behavior.
- Cross-cutting greps: `dangerouslySetInnerHTML` (0 hits), `document.cookie` (1 hit: sidebar UI-state cookie, `SameSite=Lax`, non-sensitive), `window.open`/`postMessage`/`target=_blank` (0 hits), storage inventory (localStorage: `goatfarm.farmId`, language key; sessionStorage: `goatfarm:idempotency:v1`; **no tokens or passwords anywhere**), `console.*` (only `console.error(error)` in the three error boundaries — no credential/token logging), `access_token` consumers (login/register signIn, refresh parse, in-memory store only).
- Severity scale: Critical (XSS, token/cred exfiltration, open redirect to phishing) / High (session-security break, unsafe navigation) / Medium (defense-in-depth, state bugs with user impact) / Low (hardening) / Info (observations).

## Findings table

| ID | Severity | Unit | Title |
|----|----------|------|-------|
| RT-O-1 | Low | O1 | Internally-owned timeouts auto-retry protected mutations — `TimeoutError` not classified as an abort, contradicting the documented "non-retryable" invariant |
| RT-O-2 | Low | O1 | `performRefresh` maps a *local* epoch supersession to `{kind:"rejected"}` — contract footgun for destructive consumers of `refreshSessionDetailed` |
| RT-O-3 | Low | O2 | `applyFarmList` silently swaps the operator into `list[0]` when the persisted farm was revoked — unchosen-farm landing |
| RT-O-4 | Info | O1 | Queued logout revocation silently skipped when superseded by a new sign-in before the Web Lock grants |
| RT-O-5 | Info | O1 | Any authoritative-looking non-transient non-2xx on `/api/auth/refresh` (e.g. gateway 404/403 during a deploy) forces full local session teardown |
| RT-O-6 | Info | O8 | `must_change_password` banner is advisory in the client; navigation is not blocked client-side |
| RT-O-7 | Info | O4/O5 | Farmless post-signIn navigation lacks an epoch re-check (structurally safe today, hardening note) |
| RT-O-8 | Info | O7 | Export success toast is not dialog-epoch fenced (fires after the dialog was closed mid-download) |

Counts: **0 Critical, 0 High, 0 Medium, 3 Low, 5 Info.**

---

## Finding detail

### RT-O-1 (Low) — Timeout aborts auto-retry protected mutations

Evidence:
- `frontend/src/lib/api-client.ts:656` — `const signal = init.signal ?? AbortSignal.timeout(timeoutMs);` (60 s general / 300 s simulation, lines 574–581).
- `frontend/src/lib/idempotent-request.ts:332-339` — `isAbortError` matches only `error.name === "AbortError"`.
- `frontend/src/lib/idempotent-request.ts:372-382` — `executeWithOneNetworkRetry` retries when the rejection has neither an HTTP status nor an `AbortError` name.
- `frontend/src/lib/api-client.ts:626-632` — the design comment claims the opposite: "A timeout here aborts, which idempotent-request classifies as non-retryable while RETAINING the logical key".
- `frontend/src/app/(app)/simulation/page.tsx:615-618` — the codebase itself documents that `AbortSignal.timeout()` rejects with a `TimeoutError` DOMException, i.e. *not* an `AbortError`.

Exploit sketch / behavior: any idempotency-protected POST (finance/new, purchases/new, animals, breeding, kidding, tasks, team/workers, health/events, simulation/scenarios, feeding dispense/mix/inventory-add, farm creation) whose internally-owned timeout fires rejects with name `"TimeoutError"` → classified as a network failure → one automatic replay. Caller-owned aborts (TanStack unmount/farm-switch signals produce genuine `AbortError`) are correctly never retried.

Impact: no double-commit — the replay carries the *same* `Idempotency-Key` (`preparedInit` retains the header; `shouldRetainForExplicitRetry` keeps the key either way, idempotent-request.ts:341-353), so the backend dedupes and an ambiguous commit is recovered — but (a) the documented invariant is false, (b) a wedged proxy yields up to 2× the timeout budget of silent wait with the submitting dialog locked, and (c) a mutation is replayed without user consent on a class of failure the design explicitly said would not replay. This is a behavior/documentation divergence with availability impact, not an integrity break.

Fix: in `isAbortError` (or a sibling `isTransportAbort`), also match `error.name === "TimeoutError"` (and optionally `error instanceof DOMException && error.name === "TimeoutError"`), or amend both comments to declare timeouts deliberately retryable and add a pinning test.

### RT-O-2 (Low) — Epoch supersession reported as authoritative "rejected"

Evidence:
- `frontend/src/lib/api-client.ts:237-240` — outcome contract: `"rejected" is the server's authoritative answer … the only outcome that may destroy local session state`.
- `frontend/src/lib/api-client.ts:252` and `:271` — `performRefresh` returns `{kind:"rejected"}` when `authSessionEpoch !== expectedEpoch`, i.e. purely local supersession (new sign-in / clearSession landed mid-flight), with no server answer at all.
- Safe today because every consumer re-asserts the epoch before reacting destructively: `apiResponseOnce` asserts at `api-client.ts:727` (so the branch at 734-742 is unreachable for this case), and account-dialog checks at `frontend/src/components/account-dialog.tsx:219` before its "rejected" → `signOut()` path (line 220-226).

Impact: no live forced-logout misfire (verified against `api-client.refresh-outcomes.test.ts:137` "rejects a refresh whose body lands after a newer sign-in"). The hazard is contractual: `refreshSessionDetailed()` is exported, and a future caller that treats "rejected" as terminal without an epoch comparison — exactly what the docstring invites — would fire a spurious sign-out (revoking a live server session via `/api/auth/logout`) against a session a newer sign-in owns.

Fix: return a distinct outcome (e.g. `{kind:"superseded"}`) or `{kind:"unavailable"}` for epoch mismatches, keeping "rejected" exclusively for server answers; add a type-level exhaustiveness consumer test.

### RT-O-3 (Low) — Silent fallback to `list[0]` when the persisted farm is revoked

Evidence: `frontend/src/lib/auth-context.tsx:253-255` —
`const preferred = farmIdRef.current ?? readStoredFarmId(); const valid = list.find((f) => f.id === preferred) ?? list[0]; if (valid) selectFarm(valid.id, valid.timezone);`

Exploit sketch: an operator's membership in farm A (persisted in `goatfarm.farmId`) is revoked by the owner while they are away. On next reload/bootstrap, `establishSession` → `applyFarmList` cannot find A and silently installs whichever farm the server lists first. `X-Farm-Id`, the timezone store and the UI all switch with no operator action and no notice.

Impact: all reads/writes remain inside the operator's own valid memberships (backend enforces authz — no tenancy break), but the operator can land in an *unchosen* farm and act on the wrong herd's data (e.g., record a health event against farm B's animals believing they are in farm A). The farm switcher label does update, so this is recoverable/visible — hence Low, not Medium.

Fix: when `preferred` was set but no longer valid, route to `/farm-select` (or render an explicit "your last farm is no longer available" notice) instead of silently selecting `list[0]`.

### RT-O-4 (Info) — Queued logout can be silently dropped

Evidence: `frontend/src/lib/api-client.ts:636-648` (intentional-logout-teardown carve-out) + `frontend/src/lib/auth-context.tsx:232-234` (`apiFetch("/api/auth/logout").catch(() => {})`).

If `signOut`'s logout POST is queued behind another tab's Web Lock and, while waiting, the same tab completes a *new* sign-in (epoch +2, replacement token installed), the carve-out fails, `assertAuthSession` throws, and the swallow-all catch drops the revocation. The previous session's refresh cookie has been overwritten by the new login's `Set-Cookie`, so it is unusable from this browser; residual exposure requires the old cookie to have been exfiltrated separately. Fail-closed w.r.t. the shared cookie jar (the queued request never touches it under the wrong session). Observation only.

### RT-O-5 (Info) — Intermediary errors on refresh force local teardown

Evidence: `frontend/src/lib/api-client.ts:264-267` + `isTransientRefreshStatus` (244-246): only 5xx/408/429 are transient; any other non-2xx (404/403/502-with-custom-code from a misrouted gateway during a rolling deploy, a captive portal answering 200-with-JSON-garbage is separately mapped at line 269-270 to `rejected` via `parseRefreshSessionResult` null) is treated as authoritative → `setAccessToken(null)` + `onAuthFailure` → full `clearSession` and redirect to `/login`. The refresh cookie survives; the operator can sign back in. Availability-only, documented tradeoff at lines 227-236. Observation.

### RT-O-6 (Info) — must-change-password banner is advisory client-side

Evidence: `frontend/src/app/(app)/app-layout-client.tsx:409-418`: the banner renders `role="alert"` copy stating farm pages stay blocked, but the client does not prevent navigation into `(app)` pages while `user.must_change_password` is true. Backend 403s fence actual data (per audit 01); the client shows each page's error state. UX/degradation note only.

### RT-O-7 (Info) — Farmless navigation without epoch re-check

Evidence: `frontend/src/app/login/page.tsx:89-95` (comment argues no epoch change is possible between `signIn()` resolving and the synchronous continuation) and `frontend/src/app/register/page.tsx:75` (mounted check only). The argument holds structurally today — no authenticated query/mutation is mounted on those pages, so no 401→refresh continuation can interleave — but it is an implicit invariant. If a data-fetching widget is ever added to the auth pages, a forced logout landing in that microtask gap would navigate a dead session to `/farm-select` (harmless destination; farm-select re-checks auth). Hardening: mirror the `authSessionEpochValue() !== signedInEpoch` check used at login/page.tsx:104/118.

### RT-O-8 (Info) — Export toast not dialog-epoch fenced

Evidence: `frontend/src/components/account-dialog.tsx:119-141`: `downloadExport` checks `authSessionEpochValue() !== sessionEpoch` (line 126) before building the blob but does not compare `operationEpoch === dialogEpoch.current` for the success toast (line 141). Closing the dialog mid-download bumps `dialogEpoch` (close(), lines 104-117) yet the toast still fires on completion. The download itself was explicitly user-initiated and the data stays client-side; purely a cosmetic lifecycle nit. (The error path at 143-150 is correctly double-fenced.)

---

## Per-unit attacked-&-held notes

### O1 `lib/api-client.ts` — held, except RT-O-1/2/4/5
- **JWT decode via atob**: `tokenActorScope` (52-68) pads base64url, catches all exceptions, returns null on undecodable payloads; mojibake/unicode `sub` yields a garbage-but-string scope (no throw, no crash loop; worst case a persisted-idempotency digest mismatch → new key). `setAccessToken` never throws mid-session. Held.
- **Epoch fencing of fetch/parse edges**: `apiFetch` (676-690) and `apiFetchEnvelope` (805-818) assert before status read, wrap `resp.json()` in `runScopedToAuthSession` (524-534, re-asserts on the rejection path too), and assert again after. Pinned by token-hazards tests 410/433/454. Held.
- **Farm-scope contract**: `X-Farm-Id` frozen per request (default param at 617 re-frozen for the 401 retry at 729-732); `/api/auth/farms` deliberately actor-scoped (782-783). No in-file consumer applies farm-scoped response data un-fenced (refresh/teardown paths are actor-scoped). TanStack `cancelQueries()` + `clear()` at farm switch (auth-context 178-179) closes the cache window, and aborted signals are never auto-retried. Held as a documented caller contract (enforced in Q3 sibling scope).
- **Web Locks coordination**: `withAuthCookieLock` (192-225) fail-closed on pre-grant rejection (no realm-mutex bypass of another tab's lock — verified there is *no* fallback when the API exists, matching the claim); wait bounded at 62 s; local-mutex fallback (147-186) chains gates so a timed-out ticket releases only its own gate; lock-holder death auto-releases (browser semantics). Held.
- **Logout teardown carve-out** (636-648): requires logout route + captured token non-null + current token null + exactly `sessionScope+1`; a login-after-logout (or refresh — which writes the token *without* bumping the epoch, 294-296) cannot smuggle a request through. Pinned by token-hazards 298-351. Held (residual: RT-O-4).
- **Actor-scope checks on refresh** (272-293): token `sub` must equal `user.id`; a cross-account cookie swap tears down rather than replaying under the wrong actor. Held.
- **`NO_REFRESH_PATHS`** (697-703): login/register/refresh/logout exempt; change-password and DELETE account 401 → one refresh+retry — benign (serialized under the cookie lock; DELETE reuses the same init body). Held.
- **`assertSafeApiPath`** (474-505): canonicalization-drift check *is* implemented (`parsed.pathname !== rawPathname`); verified against backslash (`/api/\evil.com` → pathname drift → reject), protocol-relative (origin check), dot-segments (drift), `%` anywhere in the raw pathname (reject), `#` fragment (hash check), space (URL-encodes → drift). `/healthz`/`/readyz` exact-match only and match the actual rewrites in `backend-rewrites.ts`. Held.
- **Error normalization** (537-565): detail strings/422 specifics returned as plain strings rendered by React (escaped); `String(detail)` for exotic shapes. Held.
- **Success buffering** (771-773): `resp.clone().json()` before completion; double-buffer bounded by response size; registry owns the canonical response and clones per consumer (796-798). Held.

### O2 `lib/auth-context.tsx` — held, except RT-O-3
- Bootstrap single-refresh under Strict Mode via `initialRefreshStarted` ref (388-410) — ref survives the double-mount rehearsal. Held.
- `establishSession` generation fencing (293-355): newer establishment owns identity+membership; explicit refresh owns only the list (`appliedFarmGeneration` distinguishes started vs committed); failure path rethrows without teardown when superseded (epoch + generation + error-name triple check). Held.
- `revokeOnFailure` (338-351): fired only while the staged bearer is still installed, never awaited; login/register transactionality verified (O5). Held.
- `selectFarm` (171-190): `cancelQueries()` **before** `clear()` — URL-only cache keys cannot be repopulated by an old-farm response. Held.
- Stale `farmId` after revocation: `applyFarmList` clears selection when the list empties (256-267); revoked-single-farm falls back per RT-O-3; a stale farm pick surfaces as a permissions ApiError on `/farm-select` with retry — no redirect loop (app-layout redirects only on `!farmId`).
- `signOut` coalescing (213-247): flight keyed by `teardownEpoch`; a post-relogin sign-out is not swallowed by a slow older flight. Held.
- Forced logout (375-386): single execution via `forcedLogout` latch; fires only on server-authoritative refresh rejection or actor mismatch (see RT-O-5 for the intermediary caveat); network/5xx/408/429 during refresh map to "unavailable" (token kept, no teardown). Held.
- Redirect rules (412-426): `PUBLIC_PATHS = ["/login", "/register"]`; intent-deduped; no loop for signed-out `/farm-select` (redirects to `/login`). `router.replace` (history not polluted); form-data loss on forced logout is inherent UX. Held.
- `clearSession` (195-211): clears query cache, idempotency state (sessionStorage wipe prevents prior-account retry-key revival; in-flight settles are generation-checked in idempotent-request 473-491), token, farm, timezone, storage — ordering documented. Held.

### O3 `api/custom-instance.ts` — held
Envelope preservation via `apiFetchEnvelope` (real status/headers); `stripNullQueryValues` (22-35) strips only literal `"null"` values, exempts `q` (FREE_TEXT_QUERY_PARAMS); re-serialization touches the query only (URLSearchParams percent-encodes within the query — cannot alter the pathname or smuggle `#`; `assertSafeApiPath` runs downstream and its raw-pathname checks remain intact). `ErrorType` is compile-time only. Held.

### O4 `/login` — held, except RT-O-7
- Zod bounds (email ≤254, password ≤128, no client minimum beyond "required" — correct for login; backend policy applies to set/change, 422s mapped by `applyApiValidationToForm` where used). Held.
- Error copy (128-134): fixed translated message for **any** 401 (no detail oracle — rate-limit is 429 and its server detail is surfaced verbatim; React-escaped). Retry-After header not surfaced (cosmetic). Held.
- Post-login epoch fence: `signedInEpoch` captured after `signIn`; checked after the permissions read (104) and in the failure recovery (118). Pinned by login/page.campaign.test.tsx:84 ("stays put when a newer session lands…"). Held.
- returnTo: only `permittedAppPathFromList(searchParams.get("returnTo"), permissions.permissions)` (108-112) — every navigation input flows through it or `firstPermittedPathFromList`; no raw `router.push(returnTo)` anywhere in the O-scope (grep-verified). Farm-switch demotion of record ids handled in permission-navigation (136-158; traversal/encoding tests at permission-navigation.test.ts:66/155). Held.
- Password field `autocomplete="current-password"`; forgot-password dialog is informational only (no recovery endpoint — social-engineering surface is the honest copy itself). Held.

### O5 `/register` — held
Client min 12 (matches backend policy); duplicate-email 400 surfaced via server detail (enumeration is a backend-documented property; frontend merely displays); `signIn` continuation uses `revokeOnFailure=true` — a farms-fetch failure after a successful register fires logout and clears local state so a reload cannot silently resurrect the half-session (establishSession 338-351). Held.

### O6 `/farm-select` — held
- Farm creation durability: POST `/api/auth/farms` is in the idempotency allowlist (idempotent-request.ts:201); the key is persisted to sessionStorage **before** fetch (466) and re-derived from the body digest after reload (signature omits the session epoch, includes actor+farmScope, 298-321); a client-side 60 s timeout triggers at most one automatic replay with the *same* key (RT-O-1), and a manual retry of the unchanged draft also reuses the key (logical signature identity) — no duplicate farm. Form `reset()` runs only after the POST resolves (163), so a failed create keeps the draft for a same-key retry. Held.
- Transition latch: `selectingFarmId` set synchronously and deliberately never reset on success (115-121) + `farmTransition` single-flight ref closes the second-pick-mid-navigation race (buttons disabled via `farmTransition.pending || selectingFarmId !== null`, line 223). Held.
- returnTo validated identically to login (110-114). Sign-out button present and functional (191-201; campaign test pins it). Farm-card data (role label) is the operator's own membership list — no disclosure. Held.

### O7 `components/account-dialog.tsx` — held, except RT-O-8
- Export: blob → objectURL → anchor.click() → revoke in `finally` (127-140); data stays client-side; epoch-fenced. Held.
- Change password: three-way outcome (216-243) — "rejected" → sign-out with rotation-safe toast; "unavailable" → token kept, self-healing 401-retry, single toast, dialog closes (no loop — `refreshSessionDetailed` is called exactly once per submission and the action lock prevents resubmission while active); "session" → `updateUser` clears the must-change banner. Epoch checks at 204/219 fence replacement-login races; `response.status === 200` matches the contract (change-password documents no other success code). Held.
- Delete account: password-confirmed, epoch-fenced toast, `signOut()` after — local teardown runs even if the server response path degrades (logout POST failure is swallowed server-side; local state cleared unconditionally in `clearSession`). Held.
- Cross-action serialization: `activeActionRef` synchronous lock (86-102) + `dialogEpoch` invalidation on close/unmount (104-117, 72-84) — export/password/delete cannot race; late failures cannot repopulate a closed dialog's error regions. Held.

### O8 Root dispatcher + `(app)` shell — held, except RT-O-6
- Dispatcher (`app/page.tsx`): destination ladder (loading → wait; !user → /login; !farmId || perms error → /farm-select; else first permitted), intent-deduped, `replace` used. No loop: farm-select failure path is retry-in-place. Held.
- `app-layout-client.tsx`: farmless redirect deduped by `farmRedirectIntent` (317-327); loading gate renders before shell (329-341); nav groups filtered by live `can` with `permsLoading ? []` (fail-closed, 345-350); `firstPermittedPath` fallback `/no-access` — direct-URL access to hidden modules is possible client-side but backend-enforced (client gating is concealment, not authz — by design). `key={farmId}` on `<main>` purges stale page closures on farm switch (422). `useDocumentTitle` maps static strings only — no title injection from URL (167-174). Mobile sidebar state is UI-only. `farmSelectHref` builds `/farm-select?returnTo=<encodeURIComponent(pathname+search)>` (357-359) — consumed only through `permittedAppPathFromList`. `(app)/layout.tsx` reads only the `sidebar_state` cookie as a boolean — no injection. Held.

### Cross-cutting — held
- `dangerouslySetInnerHTML`: zero occurrences in `frontend/src`.
- Storage inventory: `goatfarm.farmId` (localStorage), i18n language (localStorage), `goatfarm:idempotency:v1` (sessionStorage, cleared on session teardown via `clearPersistedIdempotencyRequestState`; `/api/team/workers` excluded from persistence so no password-bearing digest is stored). Access token strictly in-memory module state; no token/password ever written to storage or logs.
- `document.cookie`: one UI-only write (`sidebar.tsx:103`, `SameSite=Lax`, boolean state).
- No `window.open`, no `postMessage`, no `target=_blank` without opener control (none at all).
- All `router.push/replace` targets in O-scope are static, derived from validated `permittedAppPathFromList` output, or internal fallbacks — no open redirect.
- `Authorization` header only on approved same-origin `/api/*` (+ exact `/healthz`, `/readyz`) paths guarded by `assertSafeApiPath`; the backend hop is scheme-gated by `assertSafeBackendUrl` (plaintext only to loopback/compose-internal names).

## Unconfirmed / not independently verified
- The claim that backend 403s fully gate `must_change_password` farm pages (RT-O-6 relies on audit 01's findings; not re-tested here).
- Cross-tab Web Locks behavior under real browser crash (relies on spec auto-release; jsdom tests only exercise the API surface).
