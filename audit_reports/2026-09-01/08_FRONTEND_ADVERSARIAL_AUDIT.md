# Frontend Adversarial Audit (A8) — Next.js SPA Security & Reliability

**Date:** 2026-09-01
**Scope:** `frontend/src` — state corruption, permission bypass, token theft paths, cross-farm data leaks, crash/dead-end states reachable by a hostile or clumsy user.
**Method:** full read of `src/lib/*` (api-client, auth-context, idempotent-request, use-single-flight, use-url-state, persisted-numbers, use-permissions, permission-navigation, task-action-access, query-invalidation, format, enum-labels, farm-vocabulary, milk-series, utils, custom-instance), guard/error paths of every `page.tsx` under `app/(app)`, all pickers (animal, breeding-candidate, remote, health-target), charts, pagination, account dialog, login/register/farm-select, next.config, backend cookie/logout cross-checks; greps for XSS/dangerous patterns; executed the existing adversarial suite plus targeted race/token suites; one **new** out-of-tree probe test (evidence cited below).
**Source tree:** untouched (READ-ONLY). Probe lives at `/tmp/a8probe/animals-filter.test.tsx` (scratch only).

**Test evidence (executed today):**
- `src/test/adversarial/` — **9 files, 251 tests, all pass** (A1/A2/A5 concurrency, B1/B2/B6 authorization, B4/B5 path-injection fuzz, C1/C2/C2b/C5 input, C3 dates, C4 enum drift, D1/D1b/D3/D4/D4b transport, E1/E2/E3 state, G2/G3 config/secrets).
- `api-client.races`, `api-client.refresh-concurrency`, `api-client.token-hazards`, `use-single-flight`, `idempotent-request.persistence` — **5 files, 89 tests, all pass**.
- New probe `A8-1` (below) — **fails-safe confirmation of a real gap** (test passes, demonstrating the dead end).

Severity scale: CRITICAL (pwn/session theft), HIGH (cross-tenant leak / durable state corruption), MEDIUM (user-reachable dead-end or integrity nuisance with real operational impact), LOW (hardening / UX-reliability), INFO (observation).

---

## FINDINGS

### MEDIUM-1 — Animals list: hostile/mistyped URL filter values are cast blind into generated enum params → guaranteed-422 dead end with a looping "Retry"
**Files:**
- `frontend/src/app/(app)/animals/page.tsx:807-810` — `useState(searchParams.get("bucket") ?? ALL)` / `sex` / `status`: the raw query param seeds filter state **without any enum validation**.
- `frontend/src/app/(app)/animals/page.tsx:1005-1010` — `bucket: bucket as ListAnimalsApiAnimalsGetBucket` (same for `sex`, `status`): blind cast into the Orval-typed request.
- Error branch `frontend/src/app/(app)/animals/page.tsx:1271-1280` — shows the raw backend detail plus a "Retry animals" button only.

**Issue/exploit scenario:** Any shared, bookmarked, or hostile link of the form `/animals?bucket=EVIL_BUCKET` (or `sex=BANANA`, `status=ZOMBIE`) is forwarded verbatim to `GET /api/animals?bucket=EVIL_BUCKET`, which FastAPI rejects with 422. The page then renders the raw pydantic message ("Input should be 'QUARANTINE', …") and its **only** affordance is "Retry animals", which re-sends the identical invalid filter — an infinite 422 loop. The "Clear filters" escape exists only on the *empty* branch (`animals.length === 0`), which is unreachable because the query errored. The filter `Select`s above the error remain on the garbage value, so recovery requires the victim to notice and manually re-pick a filter or edit the URL.

**Probe evidence (new, `/tmp/a8probe/animals-filter.test.tsx`, passing):** renders `AnimalsPage` with `?bucket=EVIL_BUCKET&sex=BANANA&status=ZOMBIE&page=2` against an MSW handler mirroring FastAPI's enum 422; asserts (a) the raw `Input should be …` detail is rendered, (b) clicking "Retry animals" fires a second identical request and re-renders the same error, (c) no "Clear filters" button exists in the error branch.

**Impact:** No data exposure (the API is the guard) — but a hostile link wedges the primary herd register for a victim into a confusing raw-schema error with a self-looping retry; the URL is also *kept* (not normalized), so reload/share reproduces it. Same-page contrast: the finance ledger validates every URL filter before use (`finance/page.tsx:108-125` — `monthFromParams`/`typeFromParams`/`categoryFromParams` with regex/`includes` checks, malformed → filter off). The animals page is the one list that skipped this.

**Fix:** validate each param against the generated enum (`Object.values(ListAnimalsApiAnimalsGetBucket/Sex/Status).includes(raw)`, else `ALL`) both at seed time and in the paramsKey re-sync effect (`animals/page.tsx:941-944`), and strip the invalid param from the URL exactly as `?new=1` is stripped. Optionally add "Clear filters" to the `query.isError` branch.

---

### LOW-2 — Tasks board: future-locked auto-generated/recurring duties render a completely empty action cell (no control, no explanation)
**File:** `frontend/src/app/(app)/tasks/page.tsx:299-355` (`RowActions`).

**Issue:** For a `PENDING` duty with `due_date > today`:
- Complete is hidden when `lockedFutureCompletion` (auto-generated or recurring);
- Skip is hidden when `lockedFutureRecurrence` (recurring) or `taskSkipUnavailable` (auto WEANING/BUCKET_MOVE/quarantine-gate);
- the "Not due yet" hint only renders when a **permitted linked form** exists.

An auto-generated future duty with `action_url = null` (e.g. a WEANING duty whose operator lacks `animals.move`, or any generated duty without a linked form) therefore renders *no* action affordance and *no* hint — the action column is blank. Reached by deep links from the dashboard "View" or by paging the Upcoming tab.

**Impact:** Operator dead end ("why is this row inert?"); no security impact — the locks correctly mirror guaranteed server 409s.

**Fix:** render the existing muted `Not due yet — …` span whenever `lockedFutureCompletion || lockedFutureRecurrence` and no permitted action link exists.

---

### LOW-3 — `/animals/new` redirect shim drops the whole query string (incl. `returnTo`)
**File:** `frontend/src/app/(app)/animals/new/page.tsx:16` — `router.replace("/animals?new=1")` discards `search`.

**Issue/exploit:** `permittedAppPath` explicitly authorizes `/animals/new` for `animals.create` holders and preserves `?returnTo=…`; a permission-aware link or task action URL of the form `/animals/new?returnTo=/tasks?tab=today` loses its return destination on the hop. The sibling shims (`health/new/page.tsx`, `kidding/new/page.tsx`, `breeding/[id]/ultrasound/page.tsx`) all preserve `search`.

**Impact:** After creating the animal, the operator lands on the bare list instead of returning to the duty/animal they came from. Cosmetic navigation-state loss only.

**Fix:** mirror the sibling shims: `router.replace(with keened "/animals" + search + new=1)` (set `new=1` into the existing params).

---

### LOW-4 — `pageFromSearchParams` accepts JS-number exotics (`0x10`, `1e2`, whitespace) as page numbers
**File:** `frontend/src/app/(app)/animals/page.tsx:291-294`.

**Issue:** `Number("0x10")` → 16, `Number("1e2")` → 100, `Number(" 5 ")` → 5 — so `/animals?page=0x10` jumps 16 pages into the register instead of being treated as malformed. The tasks board already uses the strict `^(0|[1-9]\d*)$` grammar (`tasks/page.tsx:126`) and `useUrlState.getNumber`'s `Number`-based parse has the same exotic acceptance (harmless there because callers clamp).

**Impact:** None corrupting (clamped by `MAX_PAGE`, `Number.isSafeInteger`); inconsistent canonicalization means two spellings of the "same" page produce different URLs and an empty-looking page deep in the herd. Hostile-link-grade annoyance only.

**Fix:** adopt the tasks grammar (`/^(0|[1-9]\d*)$/`) in `pageFromSearchParams`; optionally in `useUrlState.getNumber`.

---

### LOW-5 — `Sparkline` x-scale divides by the *unfiltered* length while plotting *filtered* values
**File:** `frontend/src/components/charts.tsx:28,52-55` — `values = data.filter(Number.isFinite)` but `x = (i / (data.length - 1)) * width`.

**Issue:** One non-finite point (contract breach or a NaN from a future aggregator) makes the curve stop short of the right edge instead of rescaling — silently undermining the very guard the filter was added for. The last-point dot likewise sits mid-chart. Not reachable with today's validated backend payloads.

**Impact:** Cosmetic chart distortion under a backend contract breach; no crash (crash-paths are properly fenced — see defenses).

**Fix:** `const x = (i / (values.length - 1)) * width;`.

---

### LOW-6 — Raw FastAPI validation internals surfaced verbatim to operators
**Files:** `frontend/src/lib/api-client.ts:445-462` (`extractDetail` joins pydantic `detail[]` msgs), consumed by every page error box.

**Issue:** Any 422 (including MEDIUM-1, but also any future client/server schema drift) renders the pydantic message chain ("Input should be 'QUARANTINE', ..." / "loc: query.bucket") straight into the UI. Not exploitable (no markup sinks — React escapes), but it turns schema drift into operator-facing jargon and gives a hostile-link author a lever to display arbitrary-length error text from the backend's own vocabulary.

**Impact:** Usability/leak-of-internals; combine with MEDIUM-1 for the worst case. Keep as defense-in-depth: map 422 to a friendly "The server rejected these values" with the detail behind a disclosure.

---

### INFO-7 — Logout durability depends on a fire-and-forget revocation (documented, backend-defended)
**Files:** `frontend/src/lib/auth-context.tsx:214-216` (logout POST never awaited), `frontend/src/lib/api-client.ts:588-593` (`/api/auth/logout` in `NO_REFRESH_PATHS`).

**Analysis:** I initially suspected an expired in-memory bearer could 401 the logout and strand the refresh cookie. Verified against the backend: `POST /api/auth/logout` accepts **either** the refresh cookie **or** the bearer as proof (`backend/app/api/auth.py:966-1116`, "two proofs, not a priority list"), and the cookie is sent (`credentials: "include"`). So the expired-bearer case still revokes. The residual case is a *transport* failure at the instant of logout: the cookie survives and the next full page reload silently re-establishes the session (bootstrap `refreshSession()`). This is explicitly documented in-code as a deliberate trade-off, and local state (query cache, farm selection, idempotency store, `goatfarm.farmId`) is fully cleared regardless.

**Optional hardening:** on the next app boot after an apparent logout, no signal exists to distinguish "user chose logout" from "crash" — a one-time `sessionStorage` "logged-out" marker consulted by the bootstrap could make logout durable without breaking crash recovery.

---

### INFO-8 — Tenancy fence for the query cache is procedural, not structural
**Files:** `frontend/src/lib/auth-context.tsx:154-173` (`selectFarm`: `cancelQueries()` + `clear()`), all query keys are URL-only (`src/lib/query-invalidation.ts:8-10` documents this).

**Analysis:** Cross-farm safety rests on every farm transition going through `selectFarm`/`clearSession` (both clear the cache — verified by `auth-context.teardown.test.tsx` and `adv-E3`). No query key embeds `farmId`. Today this holds (and is tested, including mid-flight aborts and epoch fencing via `farmScopeEpochValue`), but any *future* code path that changes the farm header without calling `selectFarm` (e.g., an invite-acceptance flow calling `setCurrentFarmId` directly) would silently drop the fence and reintroduce cross-farm cache bleed with zero compile-time signal.

**Optional hardening:** inject the farm id into query keys via the QueryClient `queryClient.setQueryDefaults`/meta or a default `queryKey` factory, making the fence structural. Low urgency given current test coverage.

---

## DEFENSES THAT HELD (verified by read + executed tests)

1. **Token storage & theft paths** — access token is module-memory only; never localStorage/sessionStorage/URL (grep + `adv-G3` scan pass; `G3` pins the invariant). Refresh cookie: httpOnly, `SameSite=Lax`, Secure, Path=/ (`backend/app/api/auth.py:395-407`). No `dangerouslySetInnerHTML`, `javascript:` hrefs, `eval`, `window.open`, or token-shaped literals anywhere in shipped source.
2. **Refresh concurrency** — single-flight per realm **plus** cross-tab Web Locks (`goatfarm-auth-refresh`) with bounded waits, fail-closed fallback, intentional-logout-teardown exception; 401 storm → ≤2 refreshes (`adv-D3`, `api-client.refresh-concurrency` pass). "rejected vs unavailable" split prevents one dropped request from destroying a valid 14-day session; actor-scope mismatch (another tab swapped accounts) is an authoritative rejection with full teardown.
3. **Logout/teardown completeness** — `clearSession` clears query cache, idempotency store (memory + sessionStorage), token, farm id, farm timezone, and `goatfarm.farmId`; forced-logout path is idempotent (`forcedLogout` latch); account deletion routes through `signOut` after success.
4. **Path/URL injection** — `safeAppPath` / `permittedAppPath(FromList)` / `permittedTaskActionPath` / `withReturnTo` survive the full 47-entry attack corpus incl. `%2e%2e`, backslash-canonicalization, protocol-relative, control chars, dot-segment normalization, and trailing-slash record-id demotion across farm switches (`adv-B-path-injection`, 155 cases). `assertSafeApiPath` blocks cross-origin/`%`-encoded/fragment API paths pre-fetch (`adv-D4b`).
5. **Cross-farm leakage** — `selectFarm` cancels in-flight queries *before* clearing URL-only keys (`adv-E3`, teardown tests); `<main key={farmId}>` remounts pages on switch; mid-write continuations fenced by `farmScopeEpochValue` (`adv-A2` breeding POST); farm-switch return destinations demote foreign record ids to module roots; a poisoned `goatfarm.farmId` never selects a foreign tenant (E1: garbage/fractional/negative/float-precision all collapse to a real membership).
6. **Permission enforcement (UI layer)** — every page fails closed: `permsLoading` → skeleton, `permsError` → retry card (never data), `!allowed` → notice; mutations/actions gated per-permission (`tasks.complete`/`verify`, `animals.weight/move/status`, `health.manage`, `milk.manage/quality`, `simulation.manage`, `team.manage`); **self-verify blocked** for non-owners (`canVerify && (isOwner || t.completed_by_id !== currentUserId)`); worker-role controls null out; role editor cannot grant beyond the editor's own set (ceiling + `canGrant`), and worker creation offers only ceiling-filtered roles; permission-check failure ≠ empty grant (`use-permissions` fails closed on `isError`, ignoring stale cached grants).
7. **Double-submit & idempotency** — money/stock-creating POSTs are allowlisted for `Idempotency-Key`; ambiguous socket death replays the *same* key exactly once (`adv-D1b`); 4xx verdicts never auto-retry; two same-tick clicks → one network request (`adv-A1`); every submit button disabled-while-pending via `useSingleFlight` + `isSubmitting` (+ fieldset disable); session-epoch/signature discipline prevents a stale retry key surviving logout or a different actor claiming it (persistence tests); password-bearing worker create deliberately excluded from persisted recovery (digest-as-verifier risk).
8. **Dialog/state races** — dialog-reopen after mid-flight completion cannot corrupt the new session (`adv-A5`); per-attempt epochs (finance `addAttempt`, health `submissionEpoch`, account-dialog `dialogEpoch`) fence late continuations; dismissal-while-pending is either blocked (tasks create, skip) or deliberately allowed with session fencing (health record).
9. **URL state deserialization** — `useUrlState.getNumber` fallback/truncates/clamps; tasks offsets use a strict integer grammar with canonicalization + beyond-last-page re-homing; feeding/health/finance/kidding/simulation all re-home stale offsets after writes (no "page 7 of 2" dead ends); invalid `tab` deep links fall back and rewrite the URL; `PaginationControls` sanitizes NaN/negative/fractional offsets (`adv-E2`).
10. **Dates/timezones** — all business dates are farm-timezone `YYYY-MM-DD` (`farmToday` via `Intl` parts, hostile tz falls back to default, `adv-C3`); `addDays`/`formatDate` degrade instead of throwing on malformed backend dates; `formatMoney` handles 1e21+ and `-₹0` edge cases.
11. **Enum drift & rendering** — unknown enum codes render Title Case, never crash (`adv-C4` tripwires keep hand-copied zod lists in sync with the contract); user strings (tags, notes, duty titles, hostile unicode/RTL/markup) render as text everywhere (`adv-C2b/C5`).
12. **Numeric inputs** — money/weight floors (₹0.005 / 0.0005 kg), caps (₹1e9, 1,000,000 kg, 100 L, 3–12 % fat), `Infinity`/`NaN`/negative rejected inline before the wire (`adv-C1`); `maxLength` + visible `role="alert"` + `aria-invalid` wiring on the previously-silent animal name field (`adv-C2` now DEFENDED); `MAX_ANIMAL_TAG_LENGTH=50` enforced visibly.
13. **Resilience** — error boundary + not-found under `(app)`; charts fence <2-point/non-finite series; every list keeps headers mounted with skeleton/loading states; `placeholderData` prevents page-turn blanking; `enum-labels` fallback; persisted-numbers guard non-finite; localStorage blocked/quota/corrupt all degrade (`adv-E1`).
14. **CSP/hardening headers** — full baseline set + prod CSP/HSTS; `unsafe-inline` for scripts is the one documented gap (M-9, nonce attempt reverted due to Next 16 bootstrap constraints — pinned by `adv-G2` so any future loosening fails CI).

---

## Summary

| Severity | Count | Items |
|---|---|---|
| CRITICAL | 0 | — |
| HIGH | 0 | — |
| MEDIUM | 1 | M-1 animals URL filters → 422 dead-end loop (probe-confirmed) |
| LOW | 5 | L-2 tasks future-duty empty action cell; L-3 `/animals/new` drops query; L-4 page-number exotics; L-5 Sparkline scale; L-6 raw 422 internals surfaced |
| INFO | 2 | I-7 logout durability (backend-defended); I-8 procedural tenancy fence |

The SPA's auth/tenancy/idempotency core is exceptionally hardened and heavily adversarially tested; the residual risk concentrates in per-page URL-param hygiene, of which the animals list is the one real outlier.

**Probe reproduction:** `/tmp/a8probe/` (self-contained vitest config + setup, symlinked to the project's node_modules; run `frontend/node_modules/.bin/vitest run --config /tmp/a8probe/vitest.config.ts`). No source files were modified.
