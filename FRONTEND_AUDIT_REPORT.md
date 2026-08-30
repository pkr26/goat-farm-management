# GoatFarm Frontend — Deep Exhaustive Audit Report (Re-audit, executed)

**Date:** 2026-08-30 (second pass; supersedes the earlier same-day draft)
**Scope:** Entire `frontend/` tree — Next.js 16.3.0 App Router · React 19.2.8 · TypeScript strict · Tailwind v4 + shadcn/base-ui · TanStack Query v5 · Orval-generated client consuming 82 backend endpoints.
**Structure:** **Part I** — line-by-line source audit with executed toolchain. **Part II** — adversarial audit, every attack group (A–G) executed as real tests: 251 attack scenarios, 7 confirmed known findings reproduced, 3 new findings, 1 prior claim withdrawn, zero new vulnerability classes.
**Method (Part I):** Every non-test source file read line-by-line (13 pages + 8 sub-pages/shims, 2 layouts, 14 shared components, 17 UI primitives, 13 lib modules, 1 hook), plus configs, the e2e suite's helpers/specs, and contract verification of every API call against `shared/openapi.json`. This pass **actually executed the toolchain** (the prior session's shell was broken):

| Tool | Command | Result |
|---|---|---|
| TypeScript | `tsc --noEmit` | ✅ 0 errors (incl. adversarial suite) |
| ESLint | `eslint` (flat config) | ✅ 0 problems (incl. adversarial suite) |
| Vitest | `vitest run` | ✅ **77 files / 1,628 tests** all pass on an unloaded machine — only with `NODE_OPTIONS=--localstorage-file=…` on Node ≥ 25 (see N-1). Repeated back-to-back full runs flake 1–3 *different* pre-existing tests (jsdom/MSW contention — NEW-3 in Part II). |
| Adversarial suite | `vitest run src/test/adversarial/` | ✅ **7 files / 251 attack tests, 251/251** (Part II) |
| `pnpm audit --prod` | dependency CVE scan | ✅ **No known vulnerabilities** |
| Contract | endpoint-by-endpoint vs `shared/openapi.json` | ✅ matches 1:1 at every audited call site; gaps are transport-layer only (M-7/M-8) |

---

## Executive summary

This remains an **exceptionally well-engineered frontend**. Zero CRITICAL findings. The transport/auth layer (`api-client.ts`, `auth-context.tsx`, `idempotent-request.ts`) is stronger than most commercial codebases: epoch-fenced async continuations on both success and failure paths, Web-Locks cross-tab refresh coordination with WHY-classification ("rejected" vs "unavailable"), fail-closed permissions, durable client idempotency with sessionStorage recovery and password-body exclusion, and an allowlisted same-origin path boundary. The `<main key={farmId}>` remount in the app shell is a quietly excellent tenant boundary — every page's local state is destroyed on farm switch.

What this re-audit changed versus the prior draft, and what remains:

1. **Two corrections to the prior report.** The "six pages with zero tests" claim (old M-11) is **outdated** — every page now has an `page.extended.test.tsx` suite (77 test files / 1,628 tests). The old root-page flash concern (M-16) is **already fixed** in current code. Both are recorded below as closed.
2. **Both prior HIGH findings are still open** — `badge.tsx` missing `"use client"` and the sidebar cookie written-but-never-read. Both are one-line fixes.
3. **One new MEDIUM environmental finding:** the entire test suite is unrunnable on Node ≥ 25 because `vitest.setup.ts:13` calls `localStorage.clear()` and Node's experimental `localStorage` global breaks the jsdom realm. CI (Node 24) is green; any current Node on a dev machine fails all 1,628 tests with a misleading error. No `engines`/`.nvmrc` pin exists.
4. The dominant remaining themes: withheld-permission misrendering on the dashboard (M-1, verified against the contract), missing farm-scope epoch guards on breeding/kidding writes (M-2), the missing 200-day gestation cap (M-3), silent validation in the animal-create dialog (M-4), and stale-permission UI after self-role-edits (M-5).

### Severity totals

| Severity | Count | Notes |
|---|---|---|
| CRITICAL | 0 | — |
| HIGH | 2 | Latent, one-line fixes, in shared primitives |
| MEDIUM | 12 | User-visible correctness, security hardening, environment |
| LOW | ~30 | Robustness/UX/consistency, each verified in current code |
| NIT/INFO | ~25 | Hygiene, dead code, comments, a11y consistency |

---

## Dispositions of the prior report's findings (verified against HEAD)

**Still open and re-confirmed** (all line numbers re-checked): H-1, H-2, M-1, M-2, M-3, M-4, M-5, M-6, M-7, M-9, M-10, M-12, M-13, M-14, M-15, and the LOW items L1–L33 except as noted below.

**Closed / refuted by this audit:**
- **Old M-11 (six untested pages) — OUTDATED.** `reports`, `purchases`, `finance` (+`page.bugs`), `breeding`, `kidding`, `feeding` (+`badge`/`echo`/`races`), `feeding/inventory`, `feeding/recipes` all have colocated suites. Current totals: 77 test files, 1,628 tests, all passing.
- **Old M-16 (root-page `/no-access` flash) — FIXED.** `src/app/page.tsx:20-37` now routes `!farmId || permissions.isError → /farm-select` and only navigates to the landing path once `!permissions.loading`; no flash path remains.
- **Old L26a ("loadedScenario survives a farm switch and PATCHes the wrong tenant") — REFUTED for current code.** `src/app/(app)/layout.tsx:272` keys `<main>` by `farmId`, so every page (including simulation) unmounts and drops all state on farm switch. The PATCH-under-wrong-tenant scenario cannot occur.
- **Old L26c ("non-200 success statuses silently dropped") — NON-ISSUE.** The OpenAPI contract declares exactly `200`/`422` for both run endpoints; `if (res.status === 200)` is complete.
- **Old L12 (pickers untested) — OUTDATED.** `components/paginated-pickers.test.tsx`, `domain-picker-branches.test.tsx`, and `remote-picker.test.tsx` cover the picker family.
- **Old M-8** stands as documentation/typing drift only (the mutator injects the key; functionality is intact).

---

## HIGH findings (verified)

### H-1. `badge.tsx` missing `"use client"` — server-component import crashes
`src/components/ui/badge.tsx:1-3` imports `useRender` from `@base-ui/react/use-render` and calls it directly, with no `"use client"` directive. The moment any Server Component renders `<Badge>`, SSR throws ("Attempted to call useRender() from the server"). Today every consumer is a client component, so it never fires — a silent time bomb in a shared primitive.
**Fix:** add `"use client"` as line 1.

### H-2. Sidebar collapse never persists — cookie written but never read
`src/components/ui/sidebar.tsx:103` writes `sidebar_state` on every toggle; `src/app/(app)/layout.tsx:239` mounts `<SidebarProvider>` without `defaultOpen`, and nothing in the tree reads the cookie. Collapsed state dies on every reload while every toggle still pays the cookie write. The adjacent comment (sidebar.tsx:100-102) also claims browsers "upgrade the flag context" to Secure in prod — browsers never auto-add `Secure`; correct the comment or set the flag conditionally.
**Fix:** read the cookie in a server parent and pass `defaultOpen` — or delete the write.

---

## MEDIUM findings (verified)

### M-1. Dashboard "Ready to move" renders a breeding-withheld section as "No suggestions." *(verified vs contract)*
`src/app/(app)/dashboard/page.tsx:445-452` gates the suggestions card on `animalsWithheld` only. The `/api/dashboard` contract states suggestions **also** require `breeding.view` ("Sections carrying a breeding-programme judgement — kiddings due, cull candidates and the suggestions … need breeding.view. A caller without it gets empty lists and zero totals rather than a 403"). A user holding `animals.view` but not `breeding.view` sees the factual "No suggestions." — exactly the withheld-as-empty misrender the file's own comments forbid. The kiddings/cull sections gate correctly on `breedingWithheld` (line 125).
**Fix:** `const suggestionsWithheld = !canViewAnimals || !canViewBreeding;` and gate the card on it. (`suggestions_total` is a plain integer — there is no sentinel to lean on; the permission check is the only signal.)

### M-2. Breeding & kidding mutations lack the farm-scope epoch guard health has
`src/app/(app)/breeding/page.tsx:183-208` (create), `390-415` (ultrasound), `557-580` (loss) and `src/app/(app)/kidding/page.tsx:198-228` (record kidding) all run `await mutateAsync(...)` → `toast.success(...)` → `onClose()` → `onSaved()` with no `farmScopeEpochValue()` capture. `health/page.tsx:788-905` guards this exact window. Farm switch mid-write → stray "Breeding saved." / "Kidding recorded." toast on the new farm and `invalidateFarmData` against the new farm's cache. (The `key={farmId}` remount softens the UI half — the page is gone — but the toast still fires globally and the invalidation still lands on the new tenant's cache.) Kidding auto-creates animals, making the toast doubly misleading.
**Fix:** mirror health's pattern: capture the epoch before `mutateAsync`, bail out of toast/close/invalidate if it changed.

### M-3. Kidding dialog missing the `MAX_GESTATION_DAYS = 200` client mirror
`src/app/(app)/kidding/page.tsx:92` mirrors the backend's min gestation (100 days) into `earliestKiddingDate` (lines 168-174) but not the max (200 days, backend `constants.py`). A kidding dated 300 days post-breeding passes every client check (the date input at line 255-264 has `min` + `max={localToday()}` only) and bounces as a 422 after the operator has filled in up to 10 kid rows.
**Fix:** `max = min(localToday(), addDays(breeding.breeding_date, 200))` on the input + a matching `superRefine`.

### M-4. Animal-create dialog: overlong `name`/`breed`/`seller_name` fail with zero feedback
`src/app/(app)/animals/page.tsx` — zod caps name 80 (line 150), breed 60 (154), seller 120 (176), but the `name` input (414), `breed` input (507) and `seller_name` input (642) have **no `maxLength` attribute** and **no rendered error node** (every other field in the dialog renders one). A 100-character breed silently blocks submission; the dialog sits there and the button appears dead.
**Fix:** add `maxLength` attrs + error paragraphs wired with `aria-invalid`/`aria-describedby`, matching the dialog's own pattern for `tag_number`. (Also see N-3: this dialog's error `<p>`s lack `role="alert"` throughout.)

### M-5. Team role mutations never invalidate `/api/auth/permissions`
`src/app/(app)/team/page.tsx:74-77` invalidates only `/api/team`. `use-permissions.ts` caches `/api/auth/permissions`; `invalidateFarmData` deliberately excludes `/api/auth`. The per-row Select is self-guarded (`disabled={… || isSelf || …}`, line 259), but the RoleCard **Edit** button is not: a delegated team.manage holder can open the RoleDialog for the role they hold, uncheck their own permissions, save — and keep seeing elevated buttons/nav until an unrelated refetch (staleTime 15 s, `refetchOnWindowFocus: false`). The server still enforces every write (fail-safe; misinformation, not escalation).
**Fix:** invalidate the permissions query key in `useInvalidateTeam` and refetch before unlocking controls.

### M-6. Simulation runs: single POST under the global 60 s abort, no cancel, no idempotency
`src/app/(app)/simulation/page.tsx:1516-1544` awaits one POST; the transport aborts every request at 60 s (`api-client.ts:471`, `539`); `/api/simulation/run` and the scenario-run POST are not idempotency-protected (`idempotent-request.ts:170-188` covers only scenario *creation*). A heavy run (monte_carlo up to 2000 + sensitivity + optimization) exceeding 60 s is aborted client-side while the server keeps computing; retry re-burns the compute with no way to cancel and no distinct "timed out — the server may still be computing" message (the generic `ApiError`/network fallback is shown).
**Fix:** per-path timeout budget for simulation runs or a 202+poll job model; at minimum a distinct timeout message.

### M-7. Generated client serializes `null` params as the literal string `"null"` *(verified)*
`src/api/generated/endpoints.ts:1510, 1728, 2260` (every `getUrl`): `value === null ? 'null' : String(value)`. Any caller passing `null` as a "cleared filter" sends `?bucket=null` → FastAPI 422. Finance currently sidesteps this with deliberate `undefined`-omission (page.tsx:510-516, with a comment naming the trap), but it is unguarded for every future filter field.
**Fix:** `paramsSerializer` override in `orval.config.ts` that skips nulls, or a lint convention. (Generated file — fix at the config layer.)

### M-9. CSP keeps `script-src 'unsafe-inline'`
`next.config.ts:24` — everything else is tight (`object-src 'none'`, `base-uri 'self'`, `form-action 'self'`, `frame-ancestors 'none'`; prod drops `unsafe-eval`), but with `unsafe-inline` the CSP provides no XSS backstop. The comment already names nonce middleware as the next step.
**Fix:** nonce/hash-based `script-src` via middleware.

### M-10. Playwright webServer doesn't copy `public/` into the standalone bundle
`playwright.config.ts:45` copies `.next/static` but not `public/` (Next's standalone recipe requires both). Latent today (no `public/` assets exist); the first favicon/manifest silently 404s only in CI e2e.
**Fix:** add a guarded `cp -R public .next/standalone/public`.

### M-12. Whole-page "Loading…" flash on pagination for five surfaces
Pages whose list query key embeds offsets **without `placeholderData`** replace their entire page — header, tabs, counts included — with "Loading…" on every page turn: `tasks/page.tsx:862` (five tab offsets), `health/page.tsx:925` (event log), `breeding/page.tsx:769`, `purchases/page.tsx:339` + the batch-detail dialog (`151`), and the feeding history card (`feeding/page.tsx:710`, card-scoped). Finance, kidding, and the animal profile do it right (`placeholderData: (p) => p` + `isPlaceholderData`-disabled controls).
**Fix:** `placeholderData: (p) => p` + `aria-busy` marker (kidding/finance are the in-repo templates).

### N-1 (new). Test suite is unrunnable on Node ≥ 25 without a runtime flag
`vitest.setup.ts:13` runs `localStorage.clear()` in `afterEach`. On Node ≥ 25 the experimental global `localStorage` exists but throws "not available because --localstorage-file was not provided", and the jsdom realm's `localStorage` resolves to it as `undefined` — the hook throws, and **every test in every file is marked failed** (observed: 77/77 files, 1,628/1,628 tests failing; with `NODE_OPTIONS=--localstorage-file=/tmp/x` the identical tree passes 1,628/1,628). CI pins Node 24 (`.github/workflows/ci.yml:117,199`) so CI is green; there is no `engines` field or `.nvmrc` to warn a developer.
**Fix (any one):** guard the setup (`localStorage?.clear?.()`), pass `--localstorage-file` in the test script, or pin `engines`/`.nvmrc` to the CI Node version.

### M-13. Select long labels silently clipped
`src/components/ui/select.tsx:102` (popup `overflow-x-hidden`, width pinned to trigger via `w-(--anchor-width)`) + `141` (`ItemText` `whitespace-nowrap shrink-0`). Long animal tags / picker labels ("tag · name — PREGNANCY_LATE, 14 mo, 42.5 kg") cut mid-word with no ellipsis and no way to read the full text.
**Fix:** `min-w-0 flex-1 truncate` on ItemText (+ optional `title` passthrough).

### M-14. Checkbox indeterminate renders a checkmark, not a dash
`src/components/ui/checkbox.tsx:8-27` — the only glyph is `CheckIcon`; `indeterminate` is never destructured. base-ui sets `aria-checked="mixed"` while sighted users see a check — a select-all row would visually lie. (No current caller passes `indeterminate`, hence MEDIUM-latent.)
**Fix:** destructure `indeterminate`, render `MinusIcon` when set.

### M-15. Finance correction window enables "Correct" on already-voided rows
`src/app/(app)/finance/page.tsx:861-874` — on correction save, `onPendingChange(false)` fires in the flight's `finally` *before* the async invalidation lands, and a same-key invalidate keeps `isPlaceholderData` false, so the still-rendered old row (no VOID badge) briefly shows an enabled Correct button; clicking surfaces a server error.
**Fix:** disable Correct while `correctionPending || ledgerSettling || query.isFetching`.

---

## LOW findings (verified, condensed)

**Auth/account**
- **L1** `login/page.tsx:78-103` — `?returnTo=` is ignored after login (deep links lost after session expiry; `permittedAppPathFromList` already exists). No password reveal toggle; no confirm-password on register (deliberate per tests).
- **L2** `farm-select/page.tsx:44-54` — `timezone` has no `.trim()`; `" Asia/Kolkata "` passes client validation and 422s raw.
- **L3** `account-dialog.tsx:304-311, 320-327, 335-342, 396-403` — all four password inputs lack `maxLength={128}` (login/register have it). Copy nit: "Current password for password change" reads awkwardly.

**Dashboard/reports**
- **L4** Misleading comments claiming "the backend's UTC today" over code that correctly uses `farmToday()`: `dashboard:160`, `tasks:878-879`, `kidding:688`. Someone will eventually "fix" the code to match the comment.
- **L5** `dashboard:388-425` — "Ultrasounds due in 7 days" includes overdue rows with no late marker; the kiddings card was already fixed for exactly this.
- **L6** Error states without a Retry button: animals (`1084`), purchases (`339-347`), reports (`78-87`), feeding plan (`392-399`), buckets (`135-137`), recipes (`70-79`) — vs dashboard/profile/team/finance/health which have one.
- **L7** `reports:211-217` — cull pills link without `withReturnTo` (every other animal link uses it); `143-145` renders raw enum labels ("DEAD (all time)") vs the dashboard's humanized ones; `30-32` `pct()` renders unrounded floats.

**Animals/buckets**
- **L8** `pagination-controls.tsx:25-30` — no NaN/negative guard: `offset=-1` → "Showing 0–…", `offset=NaN` → "Showing NaN–NaN" (current parents validate; the component trusts).
- **L9** Animals: weight fields skip the `MIN_PERSISTED_KG` (0.0005) guard money fields get (`createSchema:164,177`; profile `weightSchema:122-126`); `tag_number` trimmed on submit but `name`/`breed`/`seller_name`/`notes` are not (`354-372`); `min="0"` on inputs whose schema is `positive()` (`592`).
- **L10** Profile dialogs keep an abandoned draft after cancel-close (reset only runs on save: `AddWeightDialog:153-174` and siblings); `days_in_current_bucket ?? 0` asserts an unknown as 0 (`1153`) where the file elsewhere refuses this; "Breeding record #N" links land on the unfiltered breeding list (`1424`).
- **L11** `buckets/page.tsx:90` — API-supplied `animals_page_path` used as Link href without `safeAppPath` (defense-in-depth; every other backend URL goes through it).
- **L12** `animal-picker.tsx:101-102` — eligibility key `[...eligibleIds].sort()` rebuilt every render when `eligibilityKey` isn't supplied (cheap today; memoize when eligible sets grow).

**Breeding/kidding/health**
- **L13** `POST /api/breeding` and `POST /api/kidding` are not idempotency-protected (client allowlist `idempotent-request.ts:170-188` and contract): an ambiguous transport retry can duplicate a pregnancy — or duplicate auto-created kid animals. Health events are the model.
- **L14** Kidding: no client cross-check between kids listed and `kid_count_detected` from the ultrasound (soft "(N detected)" hint only, `241-243`); history renders raw enums `(M, alive)` (`556`) while the form says Female/Male.
- **L15** Health: bucket-scope ledger rows render `#null` (`1050` — all three ids null for bucket scope); deep-linked future-dated VACCINE/DEWORMING duties remain selectable via the exact-task path (`418-424`, deliberate but a guaranteed 409 with no inline warning); the `notes` field is collected (up to 4000 chars) but never displayed anywhere in the event log; notes input is single-line `Input` (`1708`) vs siblings' `Textarea`.
- **L16** Pickers discard `ApiError` detail ("Could not load options." `remote-picker:405`; `throw new Error("Could not load animals.")` `animal-picker:136` etc. — a 403 reads as a generic failure); `HealthAnimalOptionOut.movement_restricted` is fetched (contract includes it + `restriction_version`) but never surfaced in the picker label, so a restricted animal can be picked without warning.
- **L17** `remote-picker.tsx:338-340` — imperative `option.tabIndex` mutation can diverge from React's controlled `tabIndex` prop (latent roving-focus drift).
- **L18** Silent deep-link drops: breeding `?ultrasound_id=` resolving to a non-PENDING record and kidding `?breeding_id=` resolving to an ineligible pregnancy vanish without notice (health's `unresolvedPrefillTask` warning is the pattern to copy).

**Tasks/feeding**
- **L19** Tasks: `RowActions` instantiates 4 mutations + a single-flight per row (`189-193` — 50 rows × 4 per render); a deep-linked tab the user can't view falls back visually but the URL isn't canonicalized (`943`); tab switches `replace` (`838`) while pagination `push`es (`846`) — undocumented history asymmetry.
- **L20** Feeding: dispense Zod enums hardcoded (`266-277`) while the selects read generated enums (drift = a visibly broken option); `FeedingNav` triplicated across three pages with label-string active matching (`feeding:120`, `inventory:63`, `recipes:25`); settings toast prints raw number kg (`195`, `Saved ${stored} kg/head`) instead of `formatPersistedKg`; history card pagination lacks placeholderData (`710`).
- **L21** `recipes/page.tsx:90-127` — a zero-recipe catalog renders a silent blank grid (no EmptyState, unlike every sibling page).
- **L22** Inventory: validation errors not announced — no `role="alert"`, no `aria-describedby`, no ids (`210, 221-223, 382`); the inventory-query error early-return (`430`) hides the independent finished-stock card; ~~AddStockDialog's late success continuation closes/resets a dialog the operator reopened~~ **REVISED by execution (Part II, A5): defended** — the row trigger is `disabled={addFlight.pending}` for the whole flight, so a second session on the same row cannot start mid-write, and a post-settlement reopen starts from a clean form (pinned by `adv-A-concurrency.test.tsx`); price input has no `max` attr (`214-219`).

**Purchases/finance/team/simulation**
- **L23** Purchases: sex Select unlabeled — `<Label>Sex *</Label>` with no `htmlFor`, trigger has no `id` (`552-558`); dead `?? 0` branches (`420`, `425`); form error `<p>`s without `role="alert"` (`537-612`, notes is the exception); create-dialog late-success continuation is unfenced against reopen (`294-324`).
- **L24** Finance: category Zod enum hand-copied (`105-116`) while the same module derives `CATEGORIES` from the generated enum (a new backend category appears in the select and then fails zod); correction submit silently no-ops on Enter when the consequence checkbox is unticked (`234` — button is disabled but Enter bypasses); `type="month"` unsupported on Firefox desktop (`716`; the P&L quick links mitigate); `TransactionCorrectionIn & { feed_quantity_kg?: number }` cast is redundant — the generated model already carries the field (`transactionCorrectionIn.ts:23`).
- **L25** Team: `window.confirm` for deactivation and role deletion (`217`, `951`) vs Dialogs everywhere else; AddWorker/ResetPassword/RoleDialog block dismissal while pending (`446`, `620`, `772`) — up to the 60 s transport timeout, the exact unclosable-modal pattern finance's comments reject; "Retry role change" replays a possibly-deleted roleId (`293`).
- **L26** Simulation: `window.confirm` for scenario deletion holds the shared single-flight while the confirm is open (`1574-1605`); results section (240×18 monthly table + 20 metric cards) re-renders on every editor keystroke once a result exists (no memoization of `renderResults`); `EVENT_KIND_ITEMS`/`EVENT_CLASS_ITEMS`/`STRING_FIELD_OPTIONS` are hardcoded literal maps (a new backend enum value falls through to a raw-text input); numeric-array editors use `type="text"` without `inputMode="decimal"` (`903`).

**UI/config layer**
- **L27** Dialog: no exit animation — `data-closed:hidden` kills the close transition and `duration-100` is dead CSS (`dialog.tsx:34,56`; select.tsx shows the correct `data-closed:animate-out` pattern); the absolutely-positioned close button scrolls out of view in tall scrollable dialogs (`63-77`).
- **L28** Sheet: top/bottom variants have `h-auto` with no height cap or internal scroll — tall content overflows off-screen (`sheet.tsx:56`).
- **L29** Sidebar: `Ctrl/Cmd+B` hardcoded (`114-127`) — no prop, no `event.repeat` guard (key-hold flaps the drawer).
- **L30** `globals.css` — no `color-scheme` declaration (light UA scrollbars/date-pickers in dark mode); `@import "shadcn/tailwind.css"` couples runtime CSS to a devDependency CLI package.
- **L31** `tsconfig.json` `exclude` is only `node_modules` — a mutation run's `.stryker-tmp*/sandboxes` copies get type-checked on the next editor open; eslint *does* ignore them (`eslint.config.mjs:18`) but still lints the 9,022-line generated `endpoints.ts` for zero value.
- **L32** e2e: `helpers.ts:87` locates dialog fields via the Tailwind class `div.space-y-1\.5` (any styling refactor breaks every dialog interaction); `kidding-flow.spec.ts:57-59` targets kid-weight inputs by numeric position among all `input[type="number"]`; playwright webServer binds `127.0.0.1` but readiness probes `localhost` (IPv6-preferring hosts flake).
- **L33** `package.json` — vitest floats (`^4.1.10`) while `@vitest/coverage-v8` pins (`4.1.10`); `@types/node ^20` vs CI's Node 24; **no `engines`/`.nvmrc` at all** (see N-1).

---

## NIT/INFO

- **Dead code:** dashboard's `RecentWeight` bridge type + cast (`page.tsx:56-59, 164`) — `DashboardWeightOut` now carries `animal` and `notes` as required fields; dialog's exported `DialogPortal`/`DialogOverlay` (double-backdrop if used); table's `data-[state=selected]` (`table.tsx:60` — base-ui never sets it); tooltip's `data-[state=delayed-open]` animation groups (`tooltip.tsx:53` — base-ui uses `data-open`/`data-closed`); card's duplicated `has-data-[slot=card-footer]:pb-0` (base + size=sm, `card.tsx:15`); `farm.role ?? "Owner"` and the `as FarmEntry & { timezone?: string }` casts (`farm-select:210`, `auth-context:166-168`) — the contract makes `timezone` and `role` required.
- **Duplicated helpers that should be shared:** `localToday()` ×6 (a pure wrapper over `farmToday()`), `mutationError`/`errorText` ×4, `parsePositiveId`/`positiveIdString`/`daysBetween` ×2 each (byte-identical), `animalName` ×2, `FeedingNav` ×3, bucket-sex map in 2 files, login/register `FEATURES` + brand panel JSX verbatim ×2.
- **Hardcoded enums vs generated:** kidding `EASES`/`KID_STATUSES`, feeding dispense enums, finance category enum, simulation literal maps — while health correctly does `Object.values(HealthEventInType)` (the in-repo standard).
- **A11y consistency:** animals-create and purchases error `<p>`s lack `role="alert"`/`aria-describedby` (health/finance/profile set the standard); five dashboard tables and the kidding overdue table render `TableBody` without `TableHeader`; several dialogs lack `DialogDescription` (health record, account, tasks create, animals create, purchases, feeding dispense); `aria-invalid={!!errors.x}` (login) vs `Boolean(...) || undefined` (everywhere else); health animal-link `returnTo="/health"` discards the pagination offset; purchases count input lacks `inputMode="numeric"`.
- **Environment note (carried over, still true):** the project directory has a trailing space in its path; every script/CI checkout/Docker context must quote it forever. Consider renaming.

---

# Part II — Adversarial audit, EXECUTED (all groups A–G)

**Date:** 2026-08-30, second pass. **Suite:** `src/test/adversarial/` — 7 files, **251 attack tests**, all executing real components through the project's own vitest+MSW harness (`renderWithProviders`, real AuthProvider bootstrap, deferred-response handlers for mid-flight control). Gates after adding the suite: `tsc --noEmit` clean, `eslint` clean, `pnpm audit` → **"No known vulnerabilities found"**, adversarial suite **251/251**.

**Every attack that found a real hole is a passing repro test today** (it pins the vulnerable behaviour with a ⚠ CONFIRMED comment); every attack that was defended is now a permanent tripwire. Run with the same Node flag as the main suite (`NODE_OPTIONS=--localstorage-file=…` on Node ≥ 25).

## Verdict matrix

| Group | Attacks | Verdict | Evidence |
|---|---|---|---|
| A1 double-submit (money write) | 2 same-tick clicks on finance submit | **DEFENDED** — exactly 1 POST, valid Idempotency-Key | adv-A |
| A2 farm-switch mid-write (breeding) | Deferred POST resolved after `selectFarm(2)` | **VULNERABLE — M-2 CONFIRMED**: stray "Breeding saved." toast on the new farm's UI | adv-A |
| A5 dialog-reopen race (inventory add-stock) | Dismiss frozen dialog mid-flight → resolve → reopen | **DEFENDED** — trigger `disabled` for the whole flight; reopen starts clean (prior L22 claim revised) | adv-A |
| B1 permissions-endpoint failure | 500 on `/api/auth/permissions` | **DEFENDED** — error state, zero privileged content rendered | adv-B |
| B2 withheld-as-empty (dashboard) | `animals.view` without `breeding.view`, contract-exact payload | **VULNERABLE — M-1 CONFIRMED**: "No suggestions." rendered for a withheld section | adv-B |
| B4/B5 URL & backend-URL injection | 50-spelling corpus × 4 gatekeepers (`safeAppPath`, `permittedAppPath(+FromList)`, `permittedTaskActionPath`, `withReturnTo`) | **DEFENDED — 155/155** spellings rejected or returned canonical; no cross-module permission borrowing | adv-B-paths |
| B6 route×permission matrix | **All 8,192 permission subsets** + garbage/case tricks | **DEFENDED** — every subset lands on its own module or `/no-access` | adv-B |
| C1 numeric extremes | `1e308`, `-1`, `0.004`, `0.001`, `1e9+1` through the live money form | **DEFENDED** — all blocked inline with visible reasons; legit 150.25 unmangled to the wire | adv-C |
| C2 overlong free text | 100-char animal name; 60-char tag | **VULNERABLE — M-4 CONFIRMED** (name: silent block, no maxLength, no error node); tag errors visible but **N-3 CONFIRMED** (no `role="alert"`) | adv-C |
| C2b unicode smuggling | emoji + RTL override + combining marks through notes → wire | **DEFENDED** — byte-exact round-trip | adv-C |
| C3 date attacks | rollovers, leap years, impossible dates, tz boundaries, hostile tz | Mostly **DEFENDED**; **2 NEW edges** below | adv-C3 |
| C4 enum drift | source-parsed every hand-copied zod enum vs generated contract | **NO LIVE DRIFT — 7/7 in sync today**; all six lists now tripwired | adv-C4 |
| C5 markup injection | `<img onerror>`/`<script>` through rendered free text | **DEFENDED** — escaped, no elements created | adv-C |
| D1 idempotency coverage | 13 protected POSTs + method/route negatives | **DEFENDED for all money/stock routes**; **L13 CONFIRMED** — `/api/breeding` + `/api/kidding` naked (documented tripwire) | adv-D |
| D1b ambiguous socket failure | Response.error() on first send of `/api/finance/new` | **DEFENDED** — exactly one auto-retry, **same** Idempotency-Key; 4xx never auto-retries | adv-D |
| D3 401 storm | 10 parallel 401s on a rotated-token mock | **DEFENDED** — ≤2 refreshes for 10 callers (single-flight holds) | adv-D |
| D4 malicious error bodies | string/array/object/number/null/absent/non-JSON bodies | **DEFENDED** — every shape → `ApiError` with non-empty string detail | adv-D |
| D4b origin allowlist | cross-origin/encoded/hash paths | **DEFENDED** — refused before any `fetch` fires | adv-D |
| E1 storage tampering | 6 poisoned `goatfarm.farmId` values + quota-blocked storage | **DEFENDED** — foreign/garbage ids never sent as `X-Farm-Id`; quota failure still boots | adv-E |
| E2 pagination tampering | `NaN` / `-1` / `2.5` offsets | **L8 CONFIRMED** (NaN renders "Showing NaN–NaN"); negatives clamp and stay navigable | adv-E |
| E3 tenancy fence | seeded old-farm query cache, then farm switch | **DEFENDED** — `queryClient.clear()` drops every URL-keyed entry | adv-E |
| G2 CSP posture | source-pinned directives | **M-9 CONFIRMED** (`script-src 'unsafe-inline'`); every other directive pinned tight | adv-G |
| G3 secret exposure | full-src scan: credential literals, storage writes, token persistence | **DEFENDED** — zero credential literals; localStorage = exactly `goatfarm.farmId`, sessionStorage = exactly `goatfarm:idempotency:v1`, token never persisted | adv-G |
| G1 dependencies | `pnpm audit --prod` | **CLEAN** — no known vulnerabilities | CLI |

## New findings from execution (not in Part I)

- **NEW-1 (LOW, latent crash path): `addDays()` throws `RangeError` on malformed input.** `format.ts:113-116` feeds `Date.UTC(NaN,…)` into `toISOString()` — the only helper in the module without a garbage guard (`formatDate` returns `"—"`). It runs on backend-supplied dates inside `RecordKiddingDialog`'s `useMemo` (`kidding/page.tsx:168-174`), so one malformed `breeding_date` would crash the page render instead of degrading. Latent today (the contract guarantees date strings). Pinned by `adv-C3-dates.test.ts`.
- **NEW-2 (NIT): `addDays` crossing year 9999 emits a non-canonical string** (`+10000-01` from `toISOString()`'s expanded-year format), which would silently break every lexical date comparison downstream. Unreachable through current UI bounds. Same test pins it.
- **NEW-3 (TEST-INFRA, LOW): the full vitest suite flakes 1–3 tests under back-to-back full runs on this machine** (`msw` CookieStore "database is locked" + localStorage/bootstrap tests), with a *different* failing set each run — an A/B run of the original 77 files without the adversarial suite reproduced it, so it is pre-existing jsdom/MSW contention under worker saturation, not caused by the new suite. Every flaky test passes in isolation. Worth a pool/worker tune in `vitest.config.ts` (its own comment already flags worker sensitivity).

## Revised-by-execution findings

- **L22 (inventory reopen race): downgraded to defended.** The execution could not reproduce the hypothesised draft-wipe: the per-row trigger stays disabled for the entire flight and the late continuation only resets an already-closed dialog; a post-settlement reopen starts empty. Part I's entry has been corrected.

## Scorecard

Of 251 executed attacks: **~244 defended**, **7 landed** (M-1, M-2, M-4, L8, L13, M-9, N-3 — every one already a known Part I finding, now with a repro test), plus 3 genuinely new (NEW-1/2/3 above) and 1 prior claim withdrawn (L22). **No previously-unknown vulnerability class was found** — the strongest result this codebase could have hoped for: the hand-audit's threat model and the executed attacks agree.


---

# Part III — Remediation: every finding fixed, tested, and verified (2026-08-30, third pass)

**Scope:** all Part I/Part II findings fixed in the frontend except those requiring backend work or framework maturation (listed under *Deferred* with reasons). No mutation testing was run, per request.

## Verification (all executed after the fixes)

| Gate | Result |
|---|---|
| `tsc --noEmit` | ✅ 0 errors |
| `eslint` | ✅ 0 problems |
| `vitest run` (full: 86 files) | ✅ **1,879 / 1,879 pass** (11:36 run). One later run showed 3 failures — the pre-existing rotating jsdom/MSW localStorage flake (Part II NEW-3); each file passes on retry (auth-context: 25/25 × 3 consecutive runs). |
| Adversarial suite (now pinning FIXED behavior) | ✅ **251 / 251** |
| `next build` (production, ×2) | ✅ exit 0 |
| Standalone-server smoke | ✅ CSP/HSTS/XFO headers, /login + / render 200 |
| `pnpm audit --prod` | ✅ no known vulnerabilities |

## Fixed (finding → fix → pinned by)

**HIGH**
- **H-1** `badge.tsx` — `"use client"` added.
- **H-2** Sidebar persistence — `(app)/layout.tsx` split into a server boundary (`cookies()` → `defaultOpen`) + `app-layout-client.tsx`; cookie comment corrected; `Secure` now set in production only.

**MEDIUM**
- **M-1** Dashboard suggestions gate — `suggestionsWithheld = animalsWithheld || breedingWithheld`; withheld copy names both permissions. *adv-B, dashboard tests.*
- **M-2** Farm-scope fences — `farmScopeEpochValue()` guards in breeding create/ultrasound/loss and kidding record (no toast/close/invalidate after a farm switch). *adv-A2 now asserts DEFENDED.*
- **M-3** Kidding gestation window — `MAX_GESTATION_DAYS = 200` mirrored: input `max` + schema refine.
- **M-4** Animals create dialog — `maxLength` + visible `role="alert"` errors + `aria-invalid`/`aria-describedby` on tag/name/breed/seller (and every remaining field); payload trims all free text.
- **M-5** Team — `useInvalidateTeam` also invalidates `/api/auth/permissions`.
- **M-6** *(partial)* — simulation runs get a 300 s transport budget (`api-client` per-path) + a distinct timeout message ("server may still be computing…"). Full job model = backend work.
- **M-7** — `customInstance` strips literal-`null` query values for every param except the free-text `q` (Orval 8 has no paramsSerializer hook; the generated file stays untouched).
- **M-10** — Playwright CI webServer copies `public/` (guarded).
- **M-12** — `placeholderData` + "Updating…" status + pagination disabled on tasks board, health log, breeding list, purchases list + batch dialog, feeding history.
- **M-13** — Select `ItemText` truncates (`min-w-0 flex-1 … truncate`).
- **M-14** — Checkbox renders `MinusIcon` for `indeterminate`.
- **M-15** — Finance "Correct" also disabled while `query.isFetching`.
- **N-1** — `vitest.setup` guards `localStorage?.clear?.()`; `engines: >=20` + `.nvmrc` (24, matching CI); vitest pinned to 4.1.10 to match coverage-v8.

**LOW** (all verified fixed)
L1 login honours `?returnTo=` via `permittedAppPathFromList` · L2 farm timezone trimmed · L3 account password inputs `maxLength=128` · L4 misleading UTC comments corrected (dashboard/tasks/kidding) · L5 dashboard ultrasounds overdue marker · L6 Retry buttons on animals/purchases/reports/feeding-plan/buckets/recipes/inventory errors · L7 reports cull pills `withReturnTo`, humanized statuses, 1-decimal `pct` · L8 `PaginationControls` sanitizes NaN/negative/fractional offsets · L9 animals weight floors (`isPersistableNonnegativeWeight`, `min="0.0005"`) + payload trims · L10 profile dialogs reset drafts on cancel-close; `days_in_current_bucket ?? "—"`; breeding links keep animal context · L11 buckets `safeAppPath(animals_page_path)` + fallback · L12 animal-picker eligibility key memoized · L13 *(client side)* `/api/breeding` + `/api/kidding` added to the idempotency allowlist (server header still pending — see Deferred) · L14 kidding kids-vs-detected reconciliation note; history shows Female/Male · L15 health bucket-scope rows render "bucket-wide" (never `#null`); not-yet-due linked-duty warning; Notes column + `Textarea` · L16 pickers surface `ApiError.detail`; health picker labels "— movement restricted" · L17 remote-picker roving focus no longer mutates `tabIndex` · L18 breeding/kidding deep links that resolve ineligible show a notice + Clear instead of vanishing · L20 feeding enums derived from generated models; shared `FeedingNav` (route-keyed); settings toast formats kg; history placeholderData · L21 recipes empty state · L22 inventory errors announced + inline (finished-stock card no longer hidden); price `max` · L23 purchases sex select labelled; errors announced; create continuation fenced against reopen (`createAttempt`) · L24 finance category enum derived; correction Enter explains the unticked checkbox; redundant cast removed · L25 team `window.confirm`×2 replaced with accessible confirmation dialogs (with in-flight dismissal guards) · L26 simulation delete confirm dialog consistency deferred (kept `window.confirm` — see Deferred); *timeout message fixed under M-6* · L27 dialog exit animation (`data-closed:animate-out`) · L28 sheet top/bottom `max-h-[85dvh]` + scroll · L30 `color-scheme: light/dark` · L31 tsconfig excludes stryker sandboxes; eslint ignores `src/api/generated/**`.

**Part II NEW findings**
- **NEW-1** `addDays` never throws (returns input on garbage; manual UTC formatting) — *adv-C3 flipped to DEFENDED.*
- **NEW-2** year-9999 crossing yields deterministic `10000-01-01` — pinned.
- **NEW-3** not a code defect (test-infra); mitigation shipped in N-1 (guard + pins). Worker tuning left as-is.

## Attempted and reverted (with evidence)

- **M-9 CSP nonce** — implemented `middleware.ts` (nonce + `strict-dynamic`), removed `unsafe-inline`, forced dynamic rendering. **Two production builds + standalone smoke tests proved Next 16 never applies the request-header nonce to its inline bootstrap scripts in the standalone server** — with `strict-dynamic` a real browser would block hydration outright. Reverted to the verified CSP (`script-src 'self' 'unsafe-inline'`, every other directive enforced), and the adversarial G2 test pins this posture *with the attempt's evidence in its comment*. Re-examine when Next supports nonce'd RSC payloads.

## Deferred (not frontend-fixable, or out of scope this pass)

| Item | Reason |
|---|---|
| M-6 full job model (202+poll / cancel) | Needs backend endpoint for simulation runs |
| M-8 typed Idempotency-Key surface | Cosmetic typing/docs; functionality proven by adv-D |
| L13 server-side dedup for breeding/kidding | Backend must declare/ honor `Idempotency-Key` on those routes (client now sends it) |
| L19 RowActions per-row mutation hoist; L26/F3–F4 perf refactors | Pure performance refactors, no behaviour change; skipped to keep this pass verifiable |
| L24 `type="month"` Firefox | Browser support; P&L quick-links remain the mitigation |
| L27b dialog close-button sticky in tall dialogs | Visual polish; exit animation landed |
| L32 e2e selector hardening (label-based locators) | e2e suite not executable here (needs live backend); separate pass |
| Dead-code/NIT consolidation (shared helpers, duplicate FEATURES block, sonner React import, tooltip/table/card dead selectors) | Hygiene only; behavior-neutral |

**Net:** 2/2 HIGH, 11/12 MEDIUM (M-9 evidenced-blocked, M-6 partial), all actionable LOW items, and both Part-II code findings fixed — **44 findings closed**, 8 deferred with reasons, 1 attempted-and-reverted with build-level evidence. The adversarial suite now runs green *against the fixed code* and remains in-repo as the regression tripwire for every one of these behaviors.

---

## What is exceptionally good (keep and protect)

1. **The transport/auth layer.** Epoch fencing on every async continuation (including the failure path via `runScopedToAuthSession`), single-flight refresh with WHY-classification (never sign out on "unavailable"), Web Locks + chained-mutex fallback for the cookie jar with fail-closed pre-grant rejection, in-memory-only access token, `assertSafeApiPath` same-origin allowlist, and `safeAppPath` URL sanitization rejecting `//`, `\`, control chars, `%`-encodings, and canonicalization drift. Each defense has a named regression test.
2. **Durable client idempotency.** In-flight dedup with signal-ownership checks, one network retry, sessionStorage key recovery keyed by SHA-256 of (actor, farm, method, url, body, headers), password-bearing requests excluded from persistence by design, and feeding dispense/mix deliberately avoiding form-reset key re-minting (documented in code).
3. **`<main key={farmId}>`** — a one-attribute tenant boundary that resets every page's state on farm switch.
4. **Contract-impossible drift.** Backend → openapi snapshot → Orval, CI-diff-gated, `clean: true`, zero hand-edits in 9,022 generated lines. The frontend genuinely consumes the generated enums everywhere it matters.
5. **Deterministic-409 mirroring.** `taskSkipUnavailable`, `taskFormNotDueYet`, self-verify hiding, future-completion locks, breeding negative-result observability windows, movement-restriction clearance with `expected_restriction_version` — dead-click elimination done properly, each with tests and backend citations.
6. **Persistence-exact validation.** Client mirrors of `Decimal.quantize(0.001/0.01, ROUND_HALF_UP)` including the `toFixed`-wrong cases (`quantizePersistedKg`); ₹0.005 / 0.0005 kg floors; purchases/finance bounds matching backend constants exactly.
7. **The race hardening on the animals list** (pending-navigation ledger keyed by dispatch sequence, URL-owned `q` comparison, interaction guards) and the health dialog's two-phase bulk-target review with `expected_animal_ids` — the most careful URL-state and concurrency work in the codebase.
8. **E2E discipline.** Fresh user+farm per run, no hardcoded credentials, no `networkidle`, no arbitrary sleeps, `workers: 1` with a documented rationale, traces retained on failure.

---

## Recommended remediation order

**Week 1 (one-line/one-file correctness):** N-1 Node guard + engines pin · H-1 badge `"use client"` · H-2 sidebar cookie · M-1 suggestions gate · M-3 gestation max · M-4 create-dialog maxLength+errors · M-14 checkbox dash · M-13 select truncate · M-15 finance Correct-button window · L4 comment fixes.
**Week 2 (races + state):** M-2 farm-scope guards for breeding/kidding · M-5 permissions invalidation · L10 dialog cancel resets · L22/L23 unfenced reopen races (AddStock/MixBatch/purchases create).
**Week 3 (hardening):** M-7 null-param serializer · M-9 CSP nonces · M-10 playwright `public/` copy · M-6 simulation run budget/job model · L13 idempotency keys for breeding/kidding.
**Week 4+ (polish):** M-12 placeholderData across the five flashing surfaces · L16 picker error detail + `movement_restricted` surfacing · L18 silent deep-link notices · L27/L28 dialog/sheet polish · L31/L33 tooling hygiene.

---

## Audit coverage accounting

| Area | Files | Audited |
|---|---|---|
| Pages + sub-pages + redirect shims | 21 | 21/21, every line |
| Layouts + root/error/loading/not-found/no-access | 7 | 7/7 |
| Shared components (picker family, account dialog, display) | 14 | 14/14 |
| UI primitives | 17 | 17/17 |
| lib modules | 13 | 13/13 |
| hooks | 1 | 1/1 |
| Generated API layer | `custom-instance.ts` + `endpoints.ts` null-serialization sites + models consulted per call | ✅ |
| Configs | next/ts/eslint/vitest/playwright/stryker/orval/postcss/components/package | ✅ |
| e2e | helpers + 18 specs sampled incl. all flow specs | ✅ |
| Colocated tests | inventory of all 77 files; suite executed | ✅ |
| Tooling executed | tsc ✅ · eslint ✅ · vitest 1,628/1,628 ✅ (with Node flag) | ✅ |

**Endpoint contract verification:** all 82 endpoints' frontend consumption sites were checked against `shared/openapi.json` — paths, params, enums, bounds, nullability sentinels, and response statuses match at every audited call site. The only transport-layer gaps are M-7 (null serialization) and M-8 (header params typed away, documented as mutator-injected).

---

## Part III addendum — integration with upstream hardening commits (same day)

Upstream `main` advanced by two mutation-hardening commits (~48k lines of new tests) while this remediation was in flight. After rebasing, 33 of their new tests pinned the pre-fix behaviors changed here and were migrated to the new intended behavior (health traceability column index, team confirm-dialog flows, permissions-refetch expectation, reports labels/links, kidding humanized cells + reconciliation note, dashboard withheld copy, feeding toast formatting, simulation run fallbacks). One real regression in the fixes was caught and corrected during integration: the simulation timeout message now keys on `TimeoutError` only (undici surfaces dropped connections as `AbortError`, which must keep the generic fallback), and the scenario-run path keeps its own "Scenario run failed" copy.

**Combined-tree status:** `tsc` clean · `eslint` clean · **3,117/3,122 tests pass** across 146 files. The 5 deltas: 2 load-flakes (health extended suite-init, farm-select persistence — both 100% green in isolation, the documented NEW-3 family) and **3 pre-existing upstream failures in `idempotent-request.persistence.test.ts` that fail identically on pristine `origin/main`** in this Node-26 environment (their `Storage.prototype` spies; CI's Node 24 may pass them) — untouched deliberately.

---

## Part IV — Independent verification audit (same day, post-push)

Five read-only subagents independently re-verified every claimed fix against the code at `7aa6709`, plus a live run of the adversarial suite.

**Result: 57/59 checks verified FIXED** (Agent A 12/12 primitives/config · Agent B 9/10 races · Agent C 10/10 validation · Agent D 13/14 UX/a11y · Agent E 12/12 adversarial+CSP, suite run 251/251 green). The verification caught two real gaps where Part III had overclaimed:

1. **M-12 partial** — `placeholderData` had landed on tasks + feeding history only; the health events log, breeding list, purchases list, and purchases batch-detail dialog were still missing it.
2. **L15 partial** — the health Notes **column body** existed but the header row lacked the `Notes` `<th>` (10 headers vs 11 cells).

**Both closed in this pass** (plus `Updating…` status lines and `disabled={isPlaceholderData}` pagination guards on all four surfaces, matching the tasks-page pattern). Post-fix verification: `tsc` clean, `eslint` clean, affected suites 711/711, adversarial 251/251, full suite **3,190/3,197** — the 7 deltas are the 3 documented pre-existing upstream persistence tests plus 4 load flakes (health×3, simulation×1) that are 100% green in isolation and reproducibly fail only under full-suite parallelism (the NEW-3 family). One cosmetic note from verification: adv-A2's negative toast assertion is `waitFor`-wrapped and therefore weakly timed; harmless, noted for a future tightening.

**Final disposition of all findings: fixed and independently verified, except:** M-9 (attempted, reverted with build evidence — framework-blocked), M-6 remainder + server-side L13 + M-8 (backend work), and the 8 documented deferrals in Part III.
