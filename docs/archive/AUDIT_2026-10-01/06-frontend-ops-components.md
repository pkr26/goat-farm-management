# Frontend Ops Journeys & Components Audit (2026-10-01)

Auditor 06 of 10 — FRONTEND CORRECTNESS of the OPERATIONS/FINANCE journeys plus the shared component library.
Scope: `frontend/src/app/(app)/{feeding,finance,purchases,tasks,planner,buckets,dashboard,reports,team,owner,simulation,ops-simulation}/`, the `(app)`-level `error.tsx`/`loading.tsx`, all 41 files of `frontend/src/components/`, and `frontend/src/hooks/`. Read-only line-by-line review of every non-test source file; co-located tests were consulted only where intent was ambiguous. Supporting libs (`lib/format.ts`, `lib/api-client.ts`, `lib/task-action-access.ts`, `lib/task-optimistic.ts`, `lib/enum-labels.ts`, generated models) were read for tracing.

## Executive summary

This slice of the frontend is in **unusually good shape**. The codebase shows clear evidence of multiple prior remediation passes, and the classic bug classes this audit hunted for are systematically fenced:

- **Money display**: every rupee figure routes through `formatMoney`/`formatMoneyDecimal` (Indian grouping, paise-safe for Decimal-as-string, non-finite → "—", no "-₹0"). No client-side summation over rounded strings was found; all totals come from the server.
- **Dates**: `farmToday()` (farm-timezone, not browser), UTC-safe `addDays`/`daysBetween`, `formatFarmDateTime` rendering backend UTC datetimes in the farm zone and active locale. The IST 00:00–05:30 off-by-one window is explicitly handled on every due-date comparison surface I checked.
- **Double-submit**: every money/record write (ledger add/correct, insurance register/renew/claim, purchase create, feed dispense, add-stock, mix, worker/role mutations) is fenced by `useSingleFlight` + disabled controls + ref-backed synchronous locks; the API layer additionally idempotency-protects mutations. No duplicate-write path found.
- **Races**: continuation fencing is pervasive and mostly correct — `captureFarmScope()` on every mutation, epoch refs for whole-editor loads (simulation/planner defaults vs herd vs calibration vs scenario), attempt counters on never-unmounting dialogs, render-phase state reconciliation instead of effect loops. Optimistic task completion has exact-snapshot rollback with a farm-switch guard before rollback.
- **Pagination**: `PaginationControls` sanitizes offset/limit, clamps "Showing X–Y" ranges, and every list re-homes an over-long offset to the real last page (feeding history, scenarios, tasks tabs) so a shrunken list can't dead-end on a false empty state.

What remains are **label/i18n defects in primary surfaces** (2 Medium), a handful of **consistency and edge-case nits** (Low/Info), and no Critical/High findings. Zero crashes, zero lost-money paths, zero missed-terminal-state paths identified in this scope.

Severity counts: **Critical 0 · High 0 · Medium 2 · Low 5 · Info 4** (plus positive observations).

## Findings

### [Medium] Task status chips render raw SCREAMING_SNAKE enum codes (no label family exists)
Location: `frontend/src/app/(app)/tasks/page.tsx:288`, `frontend/src/app/(app)/tasks/page.tsx:457`, `frontend/src/app/(app)/purchases/page.tsx:369`
Evidence:
```tsx
// tasks/page.tsx:288 (mobile card) and :457 (desktop table), Completed tab
{completedTab && (
  <StatusBadge status={task.status}>{task.status}</StatusBadge>
)}
// purchases/page.tsx:369 (batch detail, open quarantine tasks)
<StatusBadge status={t.status}>{t.status}</StatusBadge>
```
`StatusBadge`'s own `humanize()` fallback never runs because `children` are supplied — and the children are the raw wire value. The Completed tab (a primary worker surface, shown to every role with `tasks.view`) therefore renders literal `SKIPPED`, `VERIFIED`, `DONE`; the purchase detail renders literal `PENDING`. `frontend/src/lib/enum-labels.ts` has no family covering these codes (`status` covers only ACTIVE/SOLD/DEAD/CULLED; the breeding `outcome` family covers only its own PENDING), and the i18n catalogs contain no task-status keys (verified by scan).
Impact: raw enum codes shown as truth in a primary list in both languages; Telugu workers get zero localization on the exact column that tells them what happened to a duty.
Fix: add a `taskStatus` label family (en + te: PENDING/DONE/SKIPPED/VERIFIED) to `enum-labels.ts` and pass `enumLabel("taskStatus", task.status, language)` as the StatusBadge children (or drop children and extend the badge's label resolution); same for the purchases detail's PENDING.
Confirmed (code + missing label family verified).

### [Medium] StatusBadge is English-only at several in-scope call sites; hardcoded English string in a shared picker
Location: `frontend/src/components/status-badge.tsx:88-117` (humanize fallback); call sites `frontend/src/app/(app)/finance/page.tsx:1088,1184`; `frontend/src/app/(app)/team/page.tsx:579,606`; `frontend/src/components/breeding-candidate-picker.tsx:46`
Evidence:
```tsx
// finance ledger type badge (mobile + desktop) — no children passed:
<StatusBadge status={txn.type} />            // renders humanize("INCOME") → "Income"
// team membership status:
<StatusBadge status={m.is_active ? "ACTIVE" : "INACTIVE"} />  // "Active"/"Inactive"
// breeding-candidate-picker.tsx:46 — hardcoded literal inside the option label:
const cull = kind === "doe" && candidate.cull_candidate ? " — cull candidate (owner only)" : "";
```
A localized label already exists and is used ten lines away in the same file for the type *filters* (`enumLabel("txType", ...)`, te: ఆదాయం/ఖర్చు) — the table badge is simply never passed it. The team page badges and the picker's cull string have no catalog entries at all. Contrast with in-scope call sites that do it right (insurance: `enumLabel("insuranceStatus", ...)`, purchases animals: `enumLabel("status", ...)`).
Impact: mixed-language UI in the finance ledger and team register under Telugu; the cull-candidate warning — a safety-relevant "owner only" hint — is English-only for Telugu managers.
Fix: pass localized children at the finance/team call sites (one-line changes, families exist for `txType`; add `ACTIVE/INACTIVE` where needed); move the cull suffix into the i18n catalog with a `te` entry.
Confirmed.

### [Low] Pending-write dialogs block dismissal — inconsistent with the app's own "never block" rule
Location: `frontend/src/app/(app)/tasks/page.tsx:1020`; `frontend/src/components/task-row-actions.tsx:304,356,446`
Evidence:
```tsx
// tasks create dialog:
if (!nextOpen && (isSubmitting || createFlight.pending)) return;
// task-row-actions skip / recurring-confirm / reject dialogs:
if (!nextOpen && actionFlight.pending) return;
```
The finance pages removed exactly this pattern (their comments cite the 2026-09-20 P3: a modal dialog whose backdrop also blocks the page became "unclosable … with only a reload to escape") in favor of attempt counters/epoch fences that let dismissal win. The tasks surfaces keep the block deliberately (comment explains a late continuation could wipe a reopened session), but the effect on a slow rural connection is that Escape/backdrop/the X do nothing for up to the 60 s mutation timeout.
Impact: worst case a ~60 s unclosable modal; no data risk (single-flight prevents duplicates either way).
Fix: adopt the finance pattern (open/submit cycle counter) so dismissal is always honored while the continuation stays fenced.
Confirmed (deliberate, documented trade-off — rated Low for that reason).

### [Low] Owner console converts Decimal-as-string money through `Number()`
Location: `frontend/src/app/(app)/owner/page.tsx:155`
Evidence:
```tsx
<td className="table-numeric text-right">{formatMoney(Number(farm.month_net))}</td>
```
`OwnerFarmOverviewOut.month_net` is a Decimal-pattern string on the wire; the codebase's own rule (see `formatMoneyDecimal`'s doc, used for `mortality.estimated_loss`) is to never route Decimal strings through a float. At realistic magnitudes the display is correct, but paise beyond 2 dp round silently and the invariant is broken on the one cross-farm money figure. (`month_income`/`month_expense` are not rendered at all.)
Impact: display-only rounding at the third decimal; inconsistency with the established money-display rule.
Fix: `formatMoneyDecimal(farm.month_net)`.
Confirmed.

### [Low] Tasks board highlights "due soon" differently on mobile vs desktop
Location: `frontend/src/app/(app)/tasks/page.tsx:264-265` vs `:378-380`
Evidence:
```tsx
// mobile card: warning badge only when due today
const dueToday = task.status === "PENDING" && task.due_date === today;
// desktop row: warning badge within 2 days
const dueSoon = t2.status === "PENDING" && !overdue && daysBetween(today, t2.due_date) <= 2;
```
Impact: the same duty is highlighted on desktop but not on the phone (or vice versa across the md breakpoint) — cosmetic divergence of the same data in the two renderings of one list.
Fix: share one `dueSoon` predicate for both render paths.
Confirmed.

### [Low] Tasks pager pushes a history entry per page turn; every other pager replaces
Location: `frontend/src/app/(app)/tasks/page.tsx:848` (vs `frontend/src/app/(app)/purchases/page.tsx:456-459`, feeding, simulation, insurance)
Evidence:
```tsx
function changeOffset(taskTab: TaskTab, nextOffset: number) {
  ...
  router.push(taskListUrl({ ... }));   // tasks: push
}
// purchases documents the opposite convention:
// "The URL is only ever replaced (never pushed) — Back returns to the page,
//  not to a previous page number."
```
Impact: after paging the duty board, browser Back walks through every visited page offset before leaving the route; tab changes (which do `replace`) behave differently from page turns within a tab.
Fix: use `router.replace` in `changeOffset` for parity, or document the divergence as intentional.
Confirmed.

### [Low→Info] Insurance pager uses the hardcoded page limit instead of the echoed one
Location: `frontend/src/app/(app)/finance/insurance/page.tsx:1032-1038`
Evidence: `PaginationControls limit={INSURANCE_PAGE_LIMIT}` while finance/purchases/tasks/feeding pass `payload.limit`. Backend `GET /api/finance/insurance` has `limit: le=200` with no lower clamp and echoes the request value (verified in `backend/app/api/finance.py`), so the math is correct today; the hardcode would only diverge if the server ever clamped below 50. Rated Low severity, Info likelihood.
Fix: `limit={payload.limit}` for uniformity.

### [Info] Planner does not surface the evaluation's working-capital figures
Location: `frontend/src/app/(app)/planner/page.tsx:1119-1138`
`PlanEvaluation` carries `minimum_cash_balance`, `minimum_cash_month`, and `additional_working_capital_required`; the planner prints only NPV/shortfall/purchases. The simulation page displays all three for its runs. For a "what must I buy and when" planner aimed at loan-taking farmers, the minimum-cash month is arguably the most decision-relevant figure the payload already contains. Product decision, not a bug.

### [Info] MonteCarloHistogram tolerates an empty counts array
Location: `frontend/src/app/(app)/simulation/components/results-visuals.tsx:45-47`
`Math.max(...counts, 1)` guards division, but `peakBin = counts.indexOf(max)` becomes −1 when `counts` is empty, so the aria-label's start/end degrade to `"—"`. Harmless (backend always sends bins with `monte_carlo`), gracefully degraded — no action required.

### [Info] Hardcoded English unit suffix "mo" in shared picker labels
Location: `frontend/src/components/animal-picker.tsx:57,70`; `frontend/src/components/breeding-candidate-picker.tsx:38`
Labels like `D1 · Name — 24 mo, 43.2 kg` embed the English abbreviation. Cosmetic (kg is universal; "mo" is not, but the audience reads it). Candidate for a shared unit-token if the i18n pass continues.

### [Info] Co-located test files document intent that matches implementation
Used only to confirm intended behavior at four suspicion points (finance add-dialog attempt fencing, tasks optimistic rollback ordering, sidebar open-state batching, NumberInput bounds recovery). All four implementations match the tested intent; no divergence found.

## Coverage manifest

Every in-scope non-test source file was read completely (`Read`, whole file). Status legend: OK = audited, no finding beyond those listed above.

**App tree (21 files)**
| File | Status |
|---|---|
| `(app)/buckets/page.tsx` | OK |
| `(app)/dashboard/page.tsx` | OK |
| `(app)/error.tsx` | OK |
| `(app)/loading.tsx` | OK |
| `(app)/feeding/page.tsx` | OK |
| `(app)/feeding/inventory/page.tsx` | OK |
| `(app)/feeding/recipes/page.tsx` | OK |
| `(app)/finance/page.tsx` | M2 call sites (txType badge) |
| `(app)/finance/insurance/page.tsx` | Low→Info (pager limit) |
| `(app)/ops-simulation/page.tsx` | OK |
| `(app)/owner/page.tsx` | Low (month_net via Number) |
| `(app)/planner/page.tsx` | Info (working capital not surfaced) |
| `(app)/purchases/page.tsx` | M1 call site (raw PENDING) |
| `(app)/reports/page.tsx` | OK |
| `(app)/simulation/page.tsx` | OK (4245 lines, read in 4 chunks) |
| `(app)/simulation/components/editor-widgets.tsx` | OK |
| `(app)/simulation/components/format-helpers.ts` | OK |
| `(app)/simulation/components/number-inputs.tsx` | OK |
| `(app)/simulation/components/results-visuals.tsx` | Info (empty-counts edge) |
| `(app)/tasks/page.tsx` | M1, Low ×3 (dialog block, due-soon split, push-vs-replace) |
| `(app)/team/page.tsx` | M2 call sites (Active/Inactive) |

**Components (41 files)**
| File | Status |
|---|---|
| `account-dialog.tsx` | OK |
| `animal-picker.tsx` | Info ("mo" suffix) |
| `auth-layout.tsx` | OK |
| `breeding-candidate-picker.tsx` | M2 (hardcoded cull string) |
| `charts.tsx` | OK |
| `data-table-card.tsx` | OK |
| `empty-state.tsx` | OK |
| `health-target-pickers.tsx` | OK |
| `language-toggle.tsx` | OK |
| `logo.tsx` | OK |
| `page-header.tsx` | OK |
| `pagination-controls.tsx` | OK |
| `permission-gate.tsx` | OK |
| `permissions-error.tsx` | OK |
| `providers.tsx` | OK |
| `remote-picker.tsx` | OK |
| `screening-check-dialog.tsx` | OK |
| `section-nav.tsx` | OK |
| `skeletons.tsx` | OK |
| `stale-data-notice.tsx` | OK |
| `stat-card.tsx` | OK |
| `status-badge.tsx` | M2 root (English humanize) |
| `task-row-actions.tsx` | Low (dialog block ×3) |
| `theme-toggle.tsx` | OK |
| `ui/badge.tsx` | OK |
| `ui/button.tsx` | OK |
| `ui/card.tsx` | OK |
| `ui/checkbox.tsx` | OK |
| `ui/dialog.tsx` | OK |
| `ui/input.tsx` | OK |
| `ui/label.tsx` | OK |
| `ui/select.tsx` | OK |
| `ui/separator.tsx` | OK |
| `ui/sheet.tsx` | OK |
| `ui/sidebar.tsx` | OK |
| `ui/skeleton.tsx` | OK |
| `ui/sonner.tsx` | OK |
| `ui/table.tsx` (incl. SortableTableHead) | OK |
| `ui/tabs.tsx` | OK |
| `ui/textarea.tsx` | OK |
| `ui/tooltip.tsx` | OK |

**Hooks (1 file)**
| File | Status |
|---|---|
| `hooks/use-mobile.ts` | OK |

Supporting (read for tracing, other auditors' scope): `lib/format.ts`, `lib/utils.ts`, `lib/api-client.ts` (925 lines), `lib/task-action-access.ts`, `lib/task-optimistic.ts`, `lib/enum-labels.ts`, plus ~20 generated models (`transactionOut`, `financeOut`, `insurance*`, `planReport`, `planEvaluation`, `targetRisk`, `plannerMonthRow`, `ownerFarm*`, `dashboardOut`, `dailyOps*`).

**Coverage: 63/63 in-scope source files read in full — 100%.**

## Positive observations

1. **Money/date display discipline is exemplary.** `formatMoney`/`formatMoneyDecimal` handle Indian grouping, exponential-notation magnitudes, "-₹0" suppression and non-finite sentinels; `farmToday`/`addDays`/`daysBetween`/`formatFarmDateTime` make the farm-timezone invariant impossible to get wrong at call sites. The feeding page even mirrors the server's Decimal HALF_UP quantization (`quantizePersistedKg`) so its confirmation toast never reports a value that was not stored.
2. **Withheld-vs-empty semantics are enforced everywhere.** Dashboard and reports combine the API's null sentinels with the (separately cached) permission state via fail-closed ORs, so a revoked permission or a stale cache can never render a privileged figure as a factual zero — including clinical outcome rows kept visibly "withheld" in the reports summary.
3. **Every mutation continuation is fenced.** `captureFarmScope()` + single-flight + epoch/attempt counters appear on all ~30 write paths in scope; notable hard-won details include the tasks optimistic rollback that checks farm scope *before* restoring snapshots (avoiding resurrecting the old farm's cache), and the simulation editor's three-tier epoch system (loader intent vs content edits vs dialog drafts) that prevents a late defaults/calibration/scenario response from clobbering user edits.
4. **Pagination never dead-ends.** Shrunk lists re-home over-long offsets to the real last page on the feeding history, scenario list and all five task tabs (with URL canonicalization of malformed offsets), and `PaginationControls` treats its inputs as untrusted (NaN/negative/fractional sanitized, ranges clamped).
5. **Component library is safe by construction.** No prop defaults mutate shared objects; `Donut` filters non-finite slices before totaling (one ±Infinity can't poison the ring); `Histogram` clamps marker positions to the plot; `safeAppPath` closes XSS-style smuggles on every backend-supplied link; RemotePicker implements the APG dialog-picker pattern with a roving-focus listbox, server-side search with debounce + abort, and offset advancement that survives eligibility-filtered empty pages.
6. **Hardcoded-English scan is nearly clean.** An rg sweep for JSX literals across the whole scope found only `worker@example.com` (a format example), `kg` (universal unit) and the bilingual language-toggle aria-label — evidence the english-literal gate test is doing its job; the residual i18n gaps are exactly the two Medium findings above.
