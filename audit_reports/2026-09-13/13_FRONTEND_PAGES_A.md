# Frontend Red-Team Audit — Part P1–P11 (domain pages, first half)

**Report ID:** 13_FRONTEND_PAGES_A · **Date:** 2026-09-13
**Scope:** Part P units P1–P11 from `audit_reports/2026-09-13/00_RED_TEAM_AUDIT_SCOPE.md`
**Auditor posture:** authorized red team; frontend trust-boundary and client-state-machine abuse only — the backend enforces all authz.

## Files audited (read in full)

| Unit | File | LoC |
|---|---|---|
| P1 | `frontend/src/app/(app)/animals/page.tsx` | 1534 |
| P2 | `frontend/src/app/(app)/animals/[id]/page.tsx` | 1678 |
| P3 | `frontend/src/app/(app)/animals/new/page.tsx` | 47 |
| P4 | `frontend/src/app/(app)/breeding/page.tsx` | 1224 |
| P5 | `frontend/src/app/(app)/breeding/[id]/ultrasound/page.tsx` | 40 |
| P6 | `frontend/src/app/(app)/buckets/page.tsx` | 182 |
| P7 | `frontend/src/app/(app)/dashboard/page.tsx` | 824 |
| P8 | `frontend/src/app/(app)/feeding/page.tsx` | 1092 |
| P9 | `frontend/src/app/(app)/feeding/inventory/page.tsx` | 613 |
| P10 | `frontend/src/app/(app)/feeding/recipes/page.tsx` | 160 |
| P11 | `frontend/src/app/(app)/finance/page.tsx` | 1287 |

Supporting libs verified: `lib/use-url-state.ts`, `lib/persisted-numbers.ts`, `lib/farm-scope-guard.ts`, `lib/use-single-flight.ts`, `lib/idempotent-request.ts`, `lib/permission-navigation.ts`, `lib/task-action-access.ts`, `lib/utils.ts` (`safeAppPath`), `lib/api-client.ts` (`applyApiValidationToForm`), `lib/query-invalidation.ts`, `lib/backend-caps.ts`, `lib/format.ts`, `lib/mutations.ts`, `lib/enum-labels.ts`, `components/permission-gate.tsx`, `components/animal-picker.tsx`, `components/breeding-candidate-picker.tsx`, `components/remote-picker.tsx`, `components/pagination-controls.tsx`.
Backend mirrors cross-checked: `api/dashboard.py`, `api/finance.py`, `api/breeding.py` (availability gating), `schemas/animals.py` (`StatusChangeIn`), `services/breeding.py` (ultrasound window), `services/dashboard.py`.

## Methodology

Per page: (1) reflection/XSS — every API-string render path, `href`/`src` construction, `dangerouslySetInnerHTML` grep (zero hits in scope); (2) URL-borne state — every `useUrlState`/`useSearchParams`-derived value traced into navigation targets, API query params and dialog open/prefill/submit paths; (3) money/write safety — idempotency-key lifecycle per mutation, single-flight coverage, close-with-in-flight semantics, negative/zero/sub-minimum guards vs `persisted-numbers` and `backend-caps`, backend cap mirrors; (4) permission gating — page `PermissionGate` perm, per-action `can()` checks, inline gated data sections, withheld sentinels (None vs 0) fail-closed; (5) state machines — shared action flight, write-freeze, 409 conflict loops, deep-link latches; (6) pagination/offset sanitization and self-heal loop proofs; (7) i18n/date drift; (8) cache invalidation coverage after every write; (9) error surfaces (fatal vs transient, last-good data retention, 422 field mapping).

## Findings summary

| ID | Severity | Unit | Title |
|---|---|---|---|
| RT-P7-1 | Medium | P7 | Dashboard renders withheld task/ultrasound sections as factual "0 / Nothing due" for users without `tasks.view` |
| RT-P11-1 | Low | P11 | Finance URL `month` param validated with a looser grammar than the input handler → misleading empty ledger from crafted/shared links |
| RT-P6-1 | Low | P6 | `animals_page_path` validated for origin/canonicalization only, not module permission (defense-in-depth gap) |
| RT-P2-1 | Info | P2 | `ProfileBody` mounts a second `usePermissions()` observer — the documented anti-pattern |

No Critical findings (no XSS sink, no open redirect, no credential handling in scope). No High findings (no duplicate-money-write path, no navigation to attacker-controlled URL, no gated-data exposure in a non-gated section survived attack).

---

## RT-P7-1 — Dashboard withheld task sections render as factual zero/empty states

**Severity:** Medium · **Unit:** P7 `/dashboard`
**Evidence:**
- `frontend/src/app/(app)/dashboard/page.tsx:181` — `const taskTotal = payload.todays_tasks_total + payload.overdue_tasks_total;` fed unconditionally into the "Tasks due + overdue" StatCard (lines 304–311).
- `dashboard/page.tsx:367-382` — "Today's tasks (N)" card: when `payload.todays_tasks.length === 0` it renders `<EmptyState title="Nothing due today." …/>` with no permission/sentinel check.
- `dashboard/page.tsx:503-514` — "Ultrasounds due in 7 days (N)" card: renders `<EmptyState title="No ultrasounds due." …/>` and always prints the numeric total in the title.
- Backend `backend/app/api/dashboard.py:252-280` — `todays_tasks`, `overdue_tasks`, `ultrasounds_due` (and their totals) are computed only `if "tasks.view" in perms:`; otherwise they default to `[]` / `0` (plain values, **not** the `None` sentinel used for `cull_candidates_total` at lines 285–291).
- Contrast the same page's own convention: `breedingWithheld`/`animalsWithheld`/`suggestionsWithheld` (dashboard/page.tsx:142-148, 177) OR the client permission with the server `None` sentinel precisely to avoid rendering a withheld section as a factual empty/zero — the M-1 class.

**Exploit sketch:** a farm owner assigns a monitor a custom role with `dashboard.view` but without `tasks.view` (valid per `PERMISSION_DEPENDENCIES`). The monitor's dashboard authoritatively claims "Tasks due + overdue 0", "Nothing due today." and "No ultrasounds due." while overdue vaccinations, quarantine-gate duties and pregnancy checks exist. The operator rationally skips mandated statutory work (withdrawal observation, scheduled-disease checks) because the dashboard asserts there is none. A crafted/shared deep link is not even needed — the deception is the default rendering.

**Impact:** misleading fail-open display of withheld operational data; wrong operational decisions on the single landing page. No data *exposure* (rows stay withheld), which caps this at Medium per the campaign's severity rubric ("permission-gating inconsistencies that mislead").

**Fix:** in `DashboardPageContent`, compute `const tasksWithheld = !can("tasks.view");` and (a) render the same "requires tasks access" EmptyState treatment used by the kiddings/suggestions/weights cards for the Today's-tasks, overdue (already hidden by total 0 — fine), and ultrasounds cards, (b) drop the parenthesized totals and the "Tasks due + overdue" StatCard value (or replace with a withheld marker) while `tasksWithheld`. Longer term, have the backend return `None` sentinels for the three task sections like it already does for `cull_candidates_total`, so the client can rely on the payload alone.

---

## RT-P11-1 — Finance URL `month` param uses a looser grammar than the input handler

**Severity:** Low · **Unit:** P11 `/finance`
**Evidence:**
- `frontend/src/app/(app)/finance/page.tsx:116-120` — `monthFromParams` accepts `/^\d{4}-\d{2}$/`, i.e. any two-digit month: `2026-99`, `2026-00`, `0000-13` all pass and are adopted as the month filter (and mirrored back into the URL by `replaceLedgerUrl`).
- `finance/page.tsx:862-869` — the typing handler (Firefox's text-degraded `type="month"`) enforces the strict `/^\d{4}-(0[1-9]|1[0-2])$/` before accepting a value, so keyboard input can never produce this state; only URL state can.
- Backend `backend/app/api/finance.py:516-521` — `datetime.strptime(month, "%Y-%m")` raises `ValueError` for those shapes → `query.where(false())` → the ledger returns zero rows and `transactions_total = 0`.

**Exploit sketch:** share/bookmark/send `/finance?month=2026-99`. The victim sees the month box containing `2026-99`, the P&L/totals still rendered from the *unfiltered* aggregates (they are filter-independent), and the transactions card showing "No transactions match." with a "Clear filters" affordance — presenting a filter that legitimately has no rows rather than an invalid month. No 422, no request loop (the query runs; the shape merely matches nothing), but the deception persists across refresh because `replaceLedgerUrl` preserves the bogus param.

**Impact:** misleading empty state from a crafted link; recoverable via "Clear filters". Not a crash or loop — Low.

**Fix:** apply the same strict month regex in `monthFromParams` (fall back to `""`), matching the input handler.

---

## RT-P6-1 — `animals_page_path` validated for origin/canonicalization only, not module permission

**Severity:** Low (hardening) · **Unit:** P6 `/buckets`
**Evidence:** `frontend/src/app/(app)/buckets/page.tsx:100-107` — the "View the full bucket register" link uses `safeAppPath(row.animals_page_path) ?? "/animals?bucket=…"`. `safeAppPath` (`frontend/src/lib/utils.ts:13-37`) rejects schemes, protocol-relative URLs, backslashes, control chars, encoded path bytes and canonicalization drift, but accepts **any** same-origin absolute path — e.g. a (hypothetical compromised/buggy) backend value of `/finance` or `/team` would render as the bucket-register link. `permittedTaskActionPath`/`permittedAppPath` demonstrate the stricter pattern (path must map to a module the `can()` check permits).

**Impact:** no data exposure (the destination server re-checks permissions and shows its denial page), and the backend generator is trusted today — defense-in-depth gap only.

**Fix:** wrap with a check that the validated path resolves to `/animals` (or run it through `permittedAppPath(path, can)` and fall back to the plain `/animals?bucket=` filter when it does not).

---

## RT-P2-1 — Second `usePermissions()` observer in the animal profile body

**Severity:** Info · **Unit:** P2 `/animals/[id]`
**Evidence:** `frontend/src/app/(app)/animals/[id]/page.tsx:1004` — `ProfileBody` calls `const { can } = usePermissions();` although `AnimalProfilePageContent` already holds `perms` (line 1533) and passes everything else down. `components/permission-gate.tsx:7-14` documents this exact anti-pattern: a second query observer next to the page's own refetches `/api/auth/permissions` on every mount, doubling round trips on profile navigation (dam/sire/kid hops).

**Impact:** performance/consistency only; both observers read the same fail-closed query. No security effect.

**Fix:** thread the existing `perms.can` into `ProfileBody` as a prop like every other page does.

---

## Per-unit attacked-and-held notes

### P1 `/animals` (list + create dialog) — held
- **Reflection:** tag/name/breed rendered as React text everywhere (mobile cards 1414-1434, table 1468-1490); all row links are `/animals/${a.id}` with numeric ids. Zero `dangerouslySetInnerHTML` in scope.
- **URL state:** `bucket`/`sex`/`status` validated against generated enums with `ALL` fallback (329-345) — a hostile `?bucket=GARBAGE` can never reach the API as a 422. `q` clamped to 60 chars (backend mirror, line 82-84). `page` uses the strict `^(0|[1-9]\d*)$` grammar and clamps to `MAX_PAGE = floor(1_000_000/50)+1 = 20001`, so the derived offset lands exactly at the backend `MAX_PAGE_OFFSET` cap (88-89, 315-324).
- **`?new=1` deep link:** latched once via `useState` initializer (1046), opens the create dialog only when `can("animals.create")` (1282-1289), stripped by a guarded replace (1048-1062). Auto-OPEN + prefill only — no auto-SUBMIT path anywhere in the page.
- **Out-of-range self-heal:** `pageOutOfRange` requires `payload !== undefined && page > totalPages`; `totalPages = max(1, …)` so an empty list heals page>1 → 1 exactly once (1113, 1125-1155). No replace loop on empty lists.
- **Create dialog money/kg guards:** birth/entry weight bands from species vocabulary + `isPersistableNonnegativeWeight`; `purchase_price` non-negative ≤ ₹1e9 with `isPersistableNonnegativeMoney`; all date inputs `max={farmToday()}` plus schema refines. BORN source is owner-only in the UI (583-586) with a defensive reset (441-446) — server independently enforces owner-only. `shouldUnregister` prevents cross-source field leakage (414-415). Single-flight + `POST /api/animals` idempotency-protected; farm-scope fence on the continuation (449-497).
- **Pagination double-click:** guarded by `pageNavigationPending` ref + `query.isFetching` (1203-1214), cleared only when the fetch settles (1120-1123). Not `useSingleFlight`, but functionally locked.

### P2 `/animals/[id]` (profile + lifecycle dialogs) — held
- **Shared action flight:** one `useSingleFlight` in `ProfileBody` (1014) shared by weight/move/status/clear-restriction dialogs — a dismissed slow dialog cannot overlap a second lifecycle write.
- **Write-freeze:** `profileSettling={query.isFetching || query.isError}` (1634). Submit handlers re-check `profileSettling` at submit time from the latest render closure (159, 313, 543, 855) — a dialog opened before a background refetch cannot ride a stale snapshot; buttons and fieldsets also disable. On transient refetch failure the last-good profile stays rendered with a warning banner and actions frozen (1614-1626) — no blank-page swallow.
- **Fatal vs transient:** 401/403/404 tear down to the error box (1581-1583, 1613); non-fatal errors keep last-good data + retry banner. `returnTo` passes through `permittedAppPath` with `/animals` fallback (1559) — no open redirect.
- **Restriction-clear 409:** `conflictedVersion` latches the failed `restrictionVersion`; `awaitingEpisodeRefresh` blocks resubmission until the refreshed profile yields a different version, with a manual "Refresh episode" button (848-971). Versions are monotonic on the server, so the latch cannot wedge into a stale loop; the 409 branch also invalidates so the row disappears when the other actor's clearance removed the hold.
- **Status dialog:** `superRefine` mirrors `schemas/animals.py::StatusChangeIn` exactly — checked against the backend: `authority_notified_at` is **optional** server-side even with `suspected_scheduled_disease=true` (only `suspected_disease` is required, `schemas/animals.py:260-261`); the client mirror matches. DEAD-not-before-reported, future-date, and cross-field (`mortality_*` only for DEAD, sale fields only for SOLD/CULLED) rules all mirrored; payload mapper strips non-applicable fields (547-580). Status POST is not idempotency-keyed client-side, but the server's ACTIVE-only precondition turns any replay into a 409 — no duplicate ANIMAL_SALE booking.
- **Move dialog:** bucket list sex-filtered and current-bucket-excluded (383-389); server re-validates `LEGAL_BUCKET_TRANSITIONS` regardless.
- **Gated inline data:** health-events card and restriction audit gated on `health.view` (1121, 1368); breeding history on `breeding.view` (1489); `suspected_scheduled_disease` Detail renders "—" without `health.view` even though the API fails closed to `false` (1233-1237). Kids/dam/sire links are animal-module data under the page's `animals.view` gate.
- **Page-keyed remount** by `params.id` (1674) prevents the previous animal's section offsets leaking into the next profile's query params.
- See RT-P2-1 (second permissions observer).

### P3 `/animals/new` shim — held
Fixed-path `router.replace("/animals?<preserved query>&new=1")` (25-29); query is re-encoded through `URLSearchParams`; no attacker-controlled path component. Strict-Mode double dispatch fenced by `redirectStarted` ref.

### P4 `/breeding` — held
- **Deep link:** `?ultrasound_id` parsed by `parsePositiveId` (digits + safe positive integer, 80-85); non-numeric ids are ignored, never forwarded. Auto-OPENS the ultrasound dialog (no prefill beyond "not pregnant unchecked / kid-count default 2-on-check" — a deliberate anti-confirmation-bias design, 486-490); no auto-submit.
- **Dismiss latch:** `dismissedPrefillId` compares against the *current* URL id — dismissing record A does not suppress a later `?ultrasound_id=B` (896-916); the latch releases when the param clears (918-928) so a second visit to the same task URL re-opens. A deep link resolving to a non-PENDING record surfaces a stale-link notice with "Clear link" instead of vanishing (907-916, 979-995).
- **Least privilege:** the off-list prefill record is fetched only for `breeding.manage` (873-884); `candidate_availability` is `null` without `breeding.manage` (backend `api/breeding.py:144-154`) and the create dialog fails closed on `null` (298-302).
- **Ultrasound window:** the "unobservable negative result" bound (day 1 → day 18) is a client mirror of a **server-enforced 409** (`services/breeding.py:641-646`) — info note only, no client-only trust. Kid-count select bounded by species `maxLitterSize`. `saveLock` ref + `saving` state prevent double-submit; closure-race on the pregnant checkbox documented and guarded by disabling inputs while saving (617-631).
- **422 mapping:** `applyApiValidationToForm` against the declared field list, unmapped issues degrade to banner+toast (259-274); the mapper's loc-tail/root matching in `api-client.ts:444-467` cannot silently drop a known field.
- **Money:** none on this page. Doe/buck animal links gated by `animals.view` (1052-1067).

### P5 `/breeding/[id]/ultrasound` shim — held
Fixed-path redirect to `/breeding?ultrasound_id=<URLSearchParams-encoded id>` (18-24); the unvalidated id is only ever a query value that P4's `parsePositiveId` then re-validates. No open redirect.

### P6 `/buckets` — held
Read-only board. `animals_page_path` — see RT-P6-1. Row links numeric-id templates, gated by `animals.view` (77-83, 99-113). Preview truncation labeled with server `animals_limit`.

### P7 `/dashboard` — one finding (RT-P7-1), otherwise held
- **Withheld sentinels where they exist:** `breedingWithheld = cull_candidates_total === null || !canViewBreeding`, `animalsWithheld = recent_weights_total === null || !canViewAnimals`, `suggestionsWithheld = animalsWithheld || breedingWithheld` (142-148, 177) — OR-combined fail-closed in both directions, exactly the pattern the scope asked to verify. Cull banner and restricted-animals card hidden when their totals are `null` (619, 642).
- **Task action links:** every `TaskLink` routes `task.action_url` through `permittedTaskActionPath` (backend URL → `safeAppPath` → encoded-separator rejection → module manage-permission check; unknown modules fail closed to a plain "View" gated on `tasks.view`, `components/task-action-access.ts:53-75`). `withReturnTo` only ever stamps fixed literal return paths. Bucket-donut links are `encodeURIComponent`-built `/animals` filters.
- **`/kidding/new?breeding_id=${r.id}`** gated by `kidding.manage` (476-483); numeric id interpolation.
- See RT-P7-1 for the tasks/ultrasounds cards.

### P8 `/feeding` (plan + dispense + settings) — held
- **Dispense idempotency:** `dispenseFlight` is a ref-backed single-flight *outside* RHF (437-448) — the documented guard so a dialog reopen `reset()` cannot clear the in-flight lock or re-seed a body that would mint a fresh Idempotency-Key. `POST /api/feeding/dispense` is server-key-required and client-coalesced (`idempotent-request.ts:221`). The logical signature covers method+farm+session+URL+body+headers, so a body changed mid-dialog is a *different* logical request with a new key — the 409 fingerprint-mismatch path is unreachable from this page by construction; ambiguous failures retain the key for a same-body retry (`shouldRetainForExplicitRetry`, incl. 401/408/409/429/5xx).
- **Close-with-in-flight:** the dispense dialog may close mid-flight (by design); the write completes → toast + `invalidateFarmData`; the trigger button is `disabled={dispenseFlight.pending}` so no reopen/resubmit until settle. No duplicate write.
- **History URL state:** `offset` clamped `[0, MAX_PAGE_OFFSET]` via `getNumber` (319-321); `date_from`/`date_to` accepted only in strict ISO shape (85-87); an inverted range disables the query entirely (`enabled: allowed && !invalidHistoryRange`) with an inline error — no 422 loop. Self-heal re-homes an out-of-range offset once (`historyOffset < history.total` guard, total=0 → offset 0, 406-415) — proven loop-free, including under `placeholderData` because filter changes always reset the offset first.
- **Bounds:** `qty_kg` and kg/head mirror `QuantityKgFloat` (≥ 0.0005, ≤ 1e6); dispense `date` future-blocked by schema (287); backdated entries allowed — that surface is the sibling backend finding RT-HIJ-4 and is not re-reported here. `meetsPlannedQuantity` tolerance ±0.0005 is display-only against server-computed dispensed totals.
- **Settings dialog:** kg/head schema mirrors the same caps; the 204 response is confirmed via the server's own `quantize` mirror (`quantizePersistedKg`, 106-113); stale-server-value adoption handled while drafting. Every write path calls `invalidateFarmData`.

### P9 `/feeding/inventory` (add stock, mix) — held
- **Add stock:** qty/price bounds mirror backend (`MIN_PERSISTED_KG`, price ≤ ₹1e9/kg) plus a derived-total `superRefine` that pre-catches sub-paise and over-cap FEED_PURCHASE expenses (95-119). `POST /api/feeding/inventory/{id}/add` idempotency-protected; single-flight; success toast reports the *server-returned* quantized balance (140-152). Trigger disabled while pending — no close/reopen double-submit.
- **Mix:** flight lifted to the page so both openers (header + empty-state CTA) disable on the same lock (436-438, 470-479, 570-579); the reset-on-open effect cannot clear the in-flight guard (287-291). Shortage comes back as a 400 whose detail renders inside the dialog and clears on the next attempt or close (306-313, 317-322) — recomputed per submit; batch count bounded 1–50 matching the server. `POST /api/feeding/mix` idempotency-protected.
- No URL state on this page; all reflection React-escaped (ingredient names, shortage detail strings).

### P10 `/feeding/recipes` — held
Strictly read-only; no mutation surface, no URL state, all rendering React-escaped text; stale-data notice + retry on background failure.

### P11 `/finance` (ledger, add, correction) — one finding (RT-P11-1), otherwise held
- **Add open/submit cycle fence:** the page-level dialog never unmounts, so `addAttempt` (641) fences one open/submit cycle — closing bumps the counter (1088-1093, 1230-1235) and a late-resolving success toasts + invalidates but refuses to close/reset a dialog the operator has reopened and retyped into (692-697). `POST /api/finance/new` is server-key-required and client-coalesced; single-flight plus disabled fieldset/submit; same-body retry after an ambiguous failure replays the retained key.
- **Correction race (single-flight scope):** `correctionFlight` is per-dialog, but the lifted `correctionPending` disables **every** row's Correct button from submit until `finally` (1042, 274, 309) — a second dialog for the same or another transaction cannot open while the first is unresolved, including after closing the dialog mid-flight (close is deliberately unblocked; the continuation still runs to toast + invalidate, 314-320). Residual window: after the first correction commits but before the invalidation refetch lands, the not-yet-voided row is clickable — the server's void-and-replace preconditions (I3) reject correcting a voided source with a user-visible error, so no duplicate money write is reachable. `POST /api/finance/transactions/{id}/correct` idempotency-protected.
- **Money bounds:** add amount (0.005 … ₹1e9), correction amount (0 … ₹1e9, zero meaningful), `feed_quantity_kg` (0.0005 … 1e6 kg) — all mirrored; `isPersistableNonnegativeMoney` at every money input. Consequence checkbox is UX-only; server enforces atomicity — matches scope expectation.
- **Month filter:** typed input enforces the strict month grammar (862-869); URL adoption does not — RT-P11-1. Type/category URL params validated against generated enums with `all` fallback (122-134); inactive filters omitted so Orval never serializes `"null"` into a real filter (617-626).
- **Permission gating:** related-animal link rendered only with `animals.view` (1000-1012); without it the correction/add dialogs show the preserved-link note and the picker is replaced by a read-only output (466-482, 1200-1208) — matches scope expectation. `PermissionGate` adds `alsoLoading={authLoading}` to avoid a denial flash during farm bootstrap (1281).
- **Pagination:** `offset` is component state (not URL) reset to 0 on every filter change; `PaginationControls` sanitizes; ledger rows cannot be deleted (voids retained), so an out-of-range offset is not reachable in practice; no self-heal needed and none present.
- **422/error surfaces:** `mutationError` renders server detail (React-escaped); the page-level ledger error keeps the retry affordance; `StaleDataNotice` for background refetch failures with last-good data retained.

## Explicitly verified non-findings (attacked, held)

1. Zero `dangerouslySetInnerHTML` in scope; every API string (tags, names, notes, supplier/shortage details, error `detail`) renders through React text nodes.
2. No `router.push`/`router.replace` target derives a path from URL state: all navigation strings are fixed templates (`/animals`, `/breeding`), validated (`permittedAppPath`, `safeAppPath`, `permittedTaskActionPath`), or template+numeric-id.
3. No auto-submit path exists behind any deep link (`?new=1`, `?ultrasound_id=`, shims) — deepest behavior is dialog-open with explicit operator confirmation.
4. `useSingleFlight`/equivalent ref locks cover every mutation button in P1–P11; pagination links are guarded by pending-refs/isFetching/disable flags.
5. Every successful write calls `invalidateFarmData` (P1 create, P2 all four lifecycle dialogs incl. the 409 branch, P4 create/ultrasound/loss, P8 settings/dispense, P9 add-stock/mix, P11 add/correction) — no missed invalidation that would leave misleading balances.
6. `sanitizeOffset`/`MAX_PAGE` grammars held on every URL-backed pager; self-heal effects proven single-shot.
7. Status-change DEAD validation is a faithful server mirror (authority date is optional server-side too).
8. The ultrasound unobservable-window and not-yet-due mirrors are client conveniences over server-enforced 409s.
9. Idempotency-key lifecycle: keys are minted once per logical (body,farm,session,URL) signature, persisted durably before fetch for cross-reload recovery (except password-bearing worker-create), retained only for ambiguous/retryable failures, and cleared on success — no path replays a retained key with a mutated body.

## Severity counts

Critical 0 · High 0 · Medium 1 (RT-P7-1) · Low 2 (RT-P11-1, RT-P6-1) · Info 1 (RT-P2-1)
