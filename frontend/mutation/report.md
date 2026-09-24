# Frontend Mutation Testing Report — 2026-09-23

Manifest: 9763 mutants over 125 files
(compare ==/!=/</<=/>/>=; boolop &&/||/??; not-drop; binop +-*/&|; boolconst true/false; intconst +-1; ifexp swap; loopjump break/continue).

## Headline

| Metric | Value |
|---|---|
| Mutants executed | 3200 |
| Killed by tests | 2780 |
| Killed by timeout | 2 |
| **Survived** | **418** |
| On lines no test covers | 40 |
| Runner errors | 0 |
| Not yet run | 6523 |
| **Mutation score (covered code)** | **86.9%** |

Per-operator scores: compare 93.7%, intconst 83.6%, binop 79.7%, boolconst 87.2%, not 96.1%, ifexp 95.6%, loopjump 50.0%

## Per-file scores (covered mutants only)

| File | Mutants | Killed | Survived | NoCov | Score |
|---|---|---|---|---|---|
| src/app/(app)/app-layout-client.tsx | 43 | 0 | 1 | 0 | 0.0% |
| src/app/(app)/screening/page.tsx | 194 | 0 | 1 | 0 | 0.0% |
| src/app/(app)/breeding/page.tsx | 298 | 1 | 1 | 0 | 50.0% |
| src/app/(app)/feeding/page.tsx | 261 | 1 | 1 | 0 | 50.0% |
| src/app/(app)/kidding/page.tsx | 315 | 1 | 1 | 0 | 50.0% |
| src/app/(app)/purchases/page.tsx | 263 | 1 | 1 | 0 | 50.0% |
| src/app/(app)/team/page.tsx | 425 | 2 | 1 | 0 | 66.7% |
| src/api/custom-instance.ts | 13 | 10 | 3 | 0 | 76.9% |
| src/app/(app)/simulation/page.tsx | 1410 | 7 | 2 | 0 | 77.8% |
| src/app/(app)/animals/[id]/page.tsx | 518 | 438 | 80 | 0 | 84.6% |
| src/app/(app)/animals/page.tsx | 421 | 266 | 34 | 1 | 88.7% |
| src/app/(app)/animals/new/page.tsx | 3 | 3 | 0 | 0 | 100.0% |
| src/app/(app)/breeding/[id]/ultrasound/page.tsx | 5 | 0 | 0 | 0 | — |
| src/app/(app)/buckets/page.tsx | 37 | 0 | 0 | 0 | — |
| src/app/(app)/dashboard/page.tsx | 293 | 2 | 0 | 0 | 100.0% |
| src/app/(app)/feeding/inventory/page.tsx | 148 | 0 | 0 | 0 | — |
| src/app/(app)/feeding/recipes/page.tsx | 17 | 0 | 0 | 0 | — |
| src/app/(app)/finance/insurance/page.tsx | 187 | 1 | 0 | 0 | 100.0% |
| src/app/(app)/finance/page.tsx | 269 | 2 | 0 | 0 | 100.0% |
| src/app/(app)/health/new/page.tsx | 2 | 0 | 0 | 0 | — |
| src/app/(app)/health/page.tsx | 614 | 4 | 0 | 0 | 100.0% |
| src/app/(app)/health/schedule/[animalId]/page.tsx | 35 | 0 | 0 | 0 | — |
| src/app/(app)/health/task-prefill.ts | 9 | 0 | 0 | 0 | — |
| src/app/(app)/kidding/new/page.tsx | 2 | 0 | 0 | 0 | — |
| src/app/(app)/layout.tsx | 1 | 0 | 0 | 0 | — |
| src/app/(app)/loading.tsx | 4 | 0 | 0 | 0 | — |
| src/app/(app)/not-found.tsx | 2 | 0 | 0 | 0 | — |
| src/app/(app)/reports/page.tsx | 88 | 0 | 1 | 0 | 0.0% |
| src/app/(app)/ops-simulation/page.tsx | 285 | 1 | 1 | 0 | 50.0% |
| src/app/(app)/owner/page.tsx | 76 | 0 | 0 | 0 | — |
| src/app/(app)/planner/page.tsx | 330 | 1 | 1 | 0 | 50.0% |
| src/app/(app)/simulation/components/editor-widgets.tsx | 1 | 0 | 0 | 0 | — |
| src/app/(app)/simulation/components/format-helpers.ts | 26 | 0 | 0 | 0 | — |
| src/app/(app)/simulation/components/number-inputs.tsx | 98 | 0 | 0 | 0 | — |
| src/app/(app)/simulation/components/results-visuals.tsx | 67 | 0 | 0 | 0 | — |
| src/components/data-table-card.tsx | 16 | 12 | 4 | 0 | 75.0% |
| src/components/empty-state.tsx | 8 | 6 | 2 | 0 | 75.0% |
| src/components/animal-picker.tsx | 46 | 44 | 2 | 0 | 95.7% |
| src/components/charts.tsx | 117 | 113 | 4 | 0 | 96.6% |
| src/app/(app)/tasks/page.tsx | 277 | 1 | 0 | 0 | 100.0% |
| src/app/farm-select/page.tsx | 72 | 0 | 0 | 0 | — |
| src/app/healthz/route.ts | 2 | 0 | 0 | 0 | — |
| src/app/layout.tsx | 1 | 0 | 0 | 0 | — |
| src/app/login/page.tsx | 70 | 0 | 1 | 0 | 0.0% |
| src/app/page.tsx | 10 | 0 | 0 | 0 | — |
| src/app/register/page.tsx | 36 | 0 | 0 | 0 | — |
| src/app/worker/layout.tsx | 35 | 0 | 0 | 0 | — |
| src/app/worker/login/page.tsx | 73 | 0 | 0 | 0 | — |
| src/app/worker/page.tsx | 60 | 0 | 0 | 0 | — |
| src/components/ui/checkbox.tsx | 1 | 0 | 1 | 0 | 0.0% |
| src/components/ui/select.tsx | 5 | 0 | 1 | 4 | 0.0% |
| src/components/status-badge.tsx | 6 | 3 | 3 | 0 | 50.0% |
| src/lib/auth-context.tsx | 138 | 72 | 66 | 0 | 52.2% |
| src/components/permission-gate.tsx | 12 | 5 | 2 | 5 | 71.4% |
| src/components/page-header.tsx | 8 | 6 | 2 | 0 | 75.0% |
| src/lib/image-deps-guard.ts | 42 | 32 | 10 | 0 | 76.2% |
| src/lib/i18n/index.tsx | 9 | 7 | 2 | 0 | 77.8% |
| src/lib/permission-navigation.ts | 28 | 22 | 6 | 0 | 78.6% |
| src/lib/server-error-phrases.ts | 14 | 11 | 3 | 0 | 78.6% |
| src/components/pagination-controls.tsx | 30 | 23 | 6 | 1 | 79.3% |
| src/lib/simulation-field-help.ts | 10 | 8 | 2 | 0 | 80.0% |
| src/lib/utils.ts | 11 | 9 | 2 | 0 | 81.8% |
| src/lib/offline-queue.ts | 135 | 111 | 24 | 0 | 82.2% |
| src/lib/use-permissions.ts | 12 | 10 | 2 | 0 | 83.3% |
| src/components/task-row-actions.tsx | 99 | 82 | 16 | 1 | 83.7% |
| src/lib/enum-labels.ts | 22 | 19 | 3 | 0 | 86.4% |
| src/components/screening-check-dialog.tsx | 94 | 82 | 12 | 0 | 87.2% |
| src/lib/format.ts | 139 | 122 | 17 | 0 | 87.8% |
| src/lib/api-client.ts | 279 | 249 | 30 | 0 | 89.2% |
| src/components/remote-picker.tsx | 146 | 128 | 15 | 3 | 89.5% |
| src/lib/idempotent-request.ts | 240 | 216 | 24 | 0 | 90.0% |
| src/lib/task-title.ts | 60 | 54 | 6 | 0 | 90.0% |
| src/components/stat-card.tsx | 11 | 10 | 1 | 0 | 90.9% |
| src/components/theme-toggle.tsx | 11 | 10 | 1 | 0 | 90.9% |
| src/lib/csp.ts | 39 | 36 | 3 | 0 | 92.3% |
| src/components/account-dialog.tsx | 190 | 176 | 12 | 2 | 93.6% |
| src/components/skeletons.tsx | 45 | 34 | 2 | 9 | 94.4% |
| src/components/breeding-candidate-picker.tsx | 22 | 22 | 0 | 0 | 100.0% |
| src/components/feeding-nav.tsx | 4 | 4 | 0 | 0 | 100.0% |
| src/components/finance-nav.tsx | 4 | 4 | 0 | 0 | 100.0% |
| src/components/health-target-pickers.tsx | 61 | 61 | 0 | 0 | 100.0% |
| src/components/language-toggle.tsx | 2 | 2 | 0 | 0 | 100.0% |
| src/components/logo.tsx | 2 | 1 | 0 | 1 | 100.0% |
| src/components/providers.tsx | 5 | 5 | 0 | 0 | 100.0% |
| src/components/ui/dialog.tsx | 4 | 2 | 0 | 2 | 100.0% |
| src/components/ui/sheet.tsx | 2 | 1 | 0 | 1 | 100.0% |
| src/components/ui/sidebar.tsx | 54 | 49 | 0 | 5 | 100.0% |
| src/components/ui/table.tsx | 8 | 8 | 0 | 0 | 100.0% |
| src/components/ui/tooltip.tsx | 4 | 0 | 0 | 4 | — |
| src/lib/use-single-flight.ts | 11 | 10 | 1 | 0 | 90.9% |
| src/lib/use-url-state.ts | 23 | 22 | 1 | 0 | 95.7% |
| src/hooks/use-mobile.ts | 6 | 6 | 0 | 0 | 100.0% |
| src/lib/backend-caps.ts | 16 | 16 | 0 | 0 | 100.0% |
| src/lib/backend-rewrites.ts | 21 | 21 | 0 | 0 | 100.0% |
| src/lib/farm-scope-guard.ts | 1 | 1 | 0 | 0 | 100.0% |
| src/lib/farm-vocabulary.ts | 27 | 27 | 0 | 0 | 100.0% |
| src/lib/mutations.ts | 1 | 1 | 0 | 0 | 100.0% |
| src/lib/permission-envelope.ts | 3 | 3 | 0 | 0 | 100.0% |
| src/lib/persisted-numbers.ts | 19 | 19 | 0 | 0 | 100.0% |
| src/lib/query-invalidation.ts | 7 | 7 | 0 | 0 | 100.0% |
| src/lib/task-action-access.ts | 29 | 29 | 0 | 0 | 100.0% |
| src/lib/task-optimistic.ts | 7 | 7 | 0 | 0 | 100.0% |
| src/proxy.ts | 1 | 0 | 0 | 1 | — |

## Survivors (418)

### src/api/custom-instance.ts (3)

- `m00002` L34: `if (queryStart === -1) return url;` → `if (queryStart === -2) return url;` (intconst: 1 -> 2) ⚠capped-sample
- `m00003` L34: `if (queryStart === -1) return url;` → `if (queryStart === -0) return url;` (intconst: 1 -> 0) ⚠capped-sample
- `m00006` L35: `const params = new URLSearchParams(url.slice(queryStart + 1));` → `const params = new URLSearchParams(url.slice(queryStart + 0));` (intconst: 1 -> 0) ⚠capped-sample

### src/app/(app)/animals/[id]/page.tsx (80)

- `m00019` L127: `(BUCKET_REQUIRED_SEX[bucket] ?? sex) === sex;` → `(BUCKET_REQUIRED_SEX[bucket] || sex) === sex;` (binop: ?? -> ||)
- `m00021` L131: `(v) => (v === "" || v === null || v === undefined ? undefined : Number(v)),` → `(v) => (v === "" || v === null && v === undefined ? undefined : Number(v)),` (binop: || -> &&)
- `m00035` L167: `notes: z.string().max(255, "Notes cannot exceed 255 characters").optional(),` → `notes: z.string().max(254, "Notes cannot exceed 255 characters").optional(),` (intconst: 255 -> 254)
- `m00056` L292: `rows={2}` → `rows={3}` (intconst: 2 -> 3)
- `m00057` L292: `rows={2}` → `rows={1}` (intconst: 2 -> 1)
- `m00063` L307: `disabled={isSubmitting || actionFlight.pending || profileSettling}` → `disabled={isSubmitting || actionFlight.pending && profileSettling}` (binop: || -> &&)
- `m00068` L340: `const [coatColor, setCoatColor] = useState<string>(animal.coat_color ?? "");` → `const [coatColor, setCoatColor] = useState<string>(animal.coat_color || "");` (binop: ?? -> ||)
- `m00075` L360: `setCoatColor(animal.coat_color ?? "");` → `setCoatColor(animal.coat_color || "");` (binop: ?? -> ||)
- `m00097` L464: `reason: z.string().max(255, "Reason cannot exceed 255 characters").optional(),` → `reason: z.string().max(254, "Reason cannot exceed 255 characters").optional(),` (intconst: 255 -> 254)
- `m00108` L550: `value={field.value ?? ""}` → `value={field.value || ""}` (binop: ?? -> ||)
- `m00114` L586: `rows={2}` → `rows={3}` (intconst: 2 -> 3)
- `m00115` L586: `rows={2}` → `rows={1}` (intconst: 2 -> 1)
- `m00121` L601: `disabled={isSubmitting || actionFlight.pending || profileSettling}` → `disabled={isSubmitting || actionFlight.pending && profileSettling}` (binop: || -> &&)
- `m00140` L660: `.max(120, "Suspected disease cannot exceed 120 characters")` → `.max(119, "Suspected disease cannot exceed 120 characters")` (intconst: 120 -> 119)
- `m00148` L686: `values.estimated_dob !== ""` → `values.estimated_dob === ""` (compare: !== -> ===)
- `m00174` L822: `? (values.sale_price ?? null)` → `? (values.sale_price || null)` (binop: ?? -> ||)
- `m00177` L827: `? (values.sale_weight_kg ?? null)` → `? (values.sale_weight_kg || null)` (binop: ?? -> ||)
- `m00180` L831: `? (values.sale_price_per_kg ?? null)` → `? (values.sale_price_per_kg || null)` (binop: ?? -> ||)
- `m00182` L836: `values.new_status === StatusChangeInNewStatus.SOLD && needsEstimatedDob` → `values.new_status === StatusChangeInNewStatus.SOLD || needsEstimatedDob` (binop: && -> ||)
- `m00189` L849: `? ((values.mortality_cause_code ?? null) as StatusChangeInMortalityCauseCode)` → `? ((values.mortality_cause_code || null) as StatusChangeInMortalityCauseCode)` (binop: ?? -> ||)
- `m00192` L853: `? ((values.disposal_method ?? null) as StatusChangeInDisposalMethod)` → `? ((values.disposal_method || null) as StatusChangeInDisposalMethod)` (binop: ?? -> ||)
- `m00198` L862: `values.new_status === StatusChangeInNewStatus.DEAD && values.necropsy_done` → `values.new_status === StatusChangeInNewStatus.DEAD || values.necropsy_done` (binop: && -> ||)
- `m00203` L869: `values.new_status === StatusChangeInNewStatus.DEAD &&` → `values.new_status === StatusChangeInNewStatus.DEAD ||` (binop: && -> ||)
- `m00206` L874: `values.new_status === StatusChangeInNewStatus.DEAD &&` → `values.new_status === StatusChangeInNewStatus.DEAD ||` (binop: && -> ||)
- `m00218` L934: `if (nextStatus !== StatusChangeInNewStatus.SOLD) {` → `if (nextStatus === StatusChangeInNewStatus.SOLD) {` (compare: !== -> ===)
- `m00221` L939: `setValue("necropsy_done", false);` → `setValue("necropsy_done", true);` (boolconst: -> true)
- `m00228` L986: `aria-invalid={Boolean(errors.sale_weight_kg) || undefined}` → `aria-invalid={Boolean(errors.sale_weight_kg) && undefined}` (binop: || -> &&)
- `m00229` L987: `aria-describedby={errors.sale_weight_kg ? "status-sale-weight-error" : undefined}` → `aria-describedby={errors.sale_weight_kg ? undefined : "status-sale-weight-error"}` (ifexp: swap ternary branches)
- `m00238` L1024: `disabled={!saleWeight || Number(saleWeight) <= 0}` → `disabled={!saleWeight || Number(saleWeight) < 0}` (compare: <= -> <)
- `m00239` L1024: `disabled={!saleWeight || Number(saleWeight) <= 0}` → `disabled={!saleWeight || Number(saleWeight) <= 1}` (intconst: 0 -> 1)
- `m00240` L1025: `aria-invalid={Boolean(errors.sale_price_per_kg) || undefined}` → `aria-invalid={Boolean(errors.sale_price_per_kg) && undefined}` (binop: || -> &&)
- `m00241` L1027: `errors.sale_price_per_kg ? "status-price-per-kg-error" : "status-price-per-kg-hint"` → `errors.sale_price_per_kg ? "status-price-per-kg-hint" : "status-price-per-kg-error"` (ifexp: swap ternary branches)
- `m00243` L1046: `maxLength={120}` → `maxLength={121}` (intconst: 120 -> 121)
- `m00244` L1046: `maxLength={120}` → `maxLength={119}` (intconst: 120 -> 119)
- `m00256` L1090: `maxLength={120}` → `maxLength={121}` (intconst: 120 -> 121)
- `m00257` L1090: `maxLength={120}` → `maxLength={119}` (intconst: 120 -> 119)
- `m00261` L1115: `value={field.value ?? ""}` → `value={field.value || ""}` (binop: ?? -> ||)
- `m00264` L1124: `aria-invalid={Boolean(errors.mortality_cause_code) || undefined}` → `aria-invalid={Boolean(errors.mortality_cause_code) && undefined}` (binop: || -> &&)
- `m00265` L1151: `value={field.value ?? ""}` → `value={field.value || ""}` (binop: ?? -> ||)
- `m00268` L1160: `aria-invalid={Boolean(errors.disposal_method) || undefined}` → `aria-invalid={Boolean(errors.disposal_method) && undefined}` (binop: || -> &&)
- `m00269` L1162: `errors.disposal_method ? "disposal-method-error" : undefined` → `errors.disposal_method ? undefined : "disposal-method-error"` (ifexp: swap ternary branches)
- `m00276` L1210: `if (!selected) {` → `if (selected) {` (not: drop !)
- `m00278` L1227: `rows={3}` → `rows={4}` (intconst: 3 -> 4)
- `m00279` L1227: `rows={3}` → `rows={2}` (intconst: 3 -> 2)
- `m00280` L1228: `maxLength={4_000}` → `maxLength={4001}` (intconst: 4000 -> 4001)
- `m00281` L1228: `maxLength={4_000}` → `maxLength={3999}` (intconst: 4000 -> 3999)
- `m00282` L1229: `aria-invalid={Boolean(errors.necropsy_findings) || undefined}` → `aria-invalid={Boolean(errors.necropsy_findings) && undefined}` (binop: || -> &&)
- `m00283` L1231: `errors.necropsy_findings ? "necropsy-findings-error" : undefined` → `errors.necropsy_findings ? undefined : "necropsy-findings-error"` (ifexp: swap ternary branches)
- `m00289` L1274: `maxLength={120}` → `maxLength={121}` (intconst: 120 -> 121)
- `m00290` L1274: `maxLength={120}` → `maxLength={119}` (intconst: 120 -> 119)
- `m00297` L1309: `rows={2}` → `rows={3}` (intconst: 2 -> 3)
- `m00298` L1309: `rows={2}` → `rows={1}` (intconst: 2 -> 1)
- `m00299` L1310: `maxLength={255}` → `maxLength={256}` (intconst: 255 -> 256)
- `m00300` L1310: `maxLength={255}` → `maxLength={254}` (intconst: 255 -> 254)
- `m00304` L1325: `disabled={isSubmitting || actionFlight.pending || profileSettling}` → `disabled={isSubmitting || actionFlight.pending && profileSettling}` (binop: || -> &&)
- `m00310` L1358: `if (actionFlight.pending || profileSettling || awaitingEpisodeRefresh) return;` → `if (actionFlight.pending || profileSettling && awaitingEpisodeRefresh) return;` (binop: || -> &&)
- `m00311` L1358: `if (actionFlight.pending || profileSettling || awaitingEpisodeRefresh) return;` → `if (actionFlight.pending && profileSettling || awaitingEpisodeRefresh) return;` (binop: || -> &&)
- `m00327` L1437: `maxLength={255}` → `maxLength={256}` (intconst: 255 -> 256)
- `m00328` L1437: `maxLength={255}` → `maxLength={254}` (intconst: 255 -> 254)
- `m00347` L1518: `? query.error.detail` → `? "Could not load the lifetime P&L."query.error.detail` (ifexp: swap ternary branches)
- `m00349` L1558: `pnl.net < 0 ? "text-destructive" : "text-success",` → `pnl.net < 0 ? "text-success" : "text-destructive",` (ifexp: swap ternary branches)
- `m00350` L1558: `pnl.net < 0 ? "text-destructive" : "text-success",` → `pnl.net <= 0 ? "text-destructive" : "text-success",` (compare: < -> <=)
- `m00351` L1558: `pnl.net < 0 ? "text-destructive" : "text-success",` → `pnl.net < 1 ? "text-destructive" : "text-success",` (intconst: 0 -> 1)
- `m00372` L1704: `{a.restriction_reason ?? "A health hold is active for this animal."}` → `{a.restriction_reason || "A health hold is active for this animal."}` (binop: ?? -> ||)
- `m00381` L1734: `(restrictionHistory?.total ?? 0) > 0) && (` → `(restrictionHistory?.total || 0) > 0) && (` (binop: ?? -> ||)
- `m00384` L1736: `title={`Movement restriction audit (${restrictionHistory?.total ?? 0})`}` → `title={`Movement restriction audit (${restrictionHistory?.total || 0})`}` (binop: ?? -> ||)
- `m00389` L1780: `<TableCell>{action.disease_target ?? "—"}</TableCell>` → `<TableCell>{action.disease_target || "—"}</TableCell>` (binop: ?? -> ||)
- `m00390` L1786: `total={restrictionHistory?.total ?? 0}` → `total={restrictionHistory?.total || 0}` (binop: ?? -> ||)
- `m00391` L1786: `total={restrictionHistory?.total ?? 0}` → `total={restrictionHistory?.total ?? 1}` (intconst: 0 -> 1)
- `m00392` L1787: `limit={restrictionHistory?.limit ?? RESTRICTION_HISTORY_LIMIT}` → `limit={restrictionHistory?.limit || RESTRICTION_HISTORY_LIMIT}` (binop: ?? -> ||)
- `m00393` L1788: `offset={restrictionHistory?.offset ?? restrictionOffset}` → `offset={restrictionHistory?.offset || restrictionOffset}` (binop: ?? -> ||)
- `m00401` L1819: `<Detail label="Days in bucket">{a.days_in_current_bucket ?? "—"}</Detail>` → `<Detail label="Days in bucket">{a.days_in_current_bucket || "—"}</Detail>` (binop: ?? -> ||)
- `m00415` L1840: `<Detail label="Seller">{a.seller_name ?? "—"}</Detail>` → `<Detail label="Seller">{a.seller_name || "—"}</Detail>` (binop: ?? -> ||)
- `m00421` L1854: `<Detail label="Buyer">{a.buyer_name ?? "—"}</Detail>` → `<Detail label="Buyer">{a.buyer_name || "—"}</Detail>` (binop: ?? -> ||)
- `m00424` L1859: `<Detail label="Mortality cause">{a.mortality_cause ?? "—"}</Detail>` → `<Detail label="Mortality cause">{a.mortality_cause || "—"}</Detail>` (binop: ?? -> ||)
- `m00437` L1901: `{a.restriction_clearance_reference ?? "—"}` → `{a.restriction_clearance_reference || "—"}` (binop: ?? -> ||)
- `m00449` L1954: `<TableCell className="text-right">{w.bcs ?? "—"}</TableCell>` → `<TableCell className="text-right">{w.bcs || "—"}</TableCell>` (binop: ?? -> ||)
- `m00450` L1955: `<TableCell>{w.notes ?? ""}</TableCell>` → `<TableCell>{w.notes || ""}</TableCell>` (binop: ?? -> ||)
- `m00455` L1997: `<TableCell>{m.reason ?? ""}</TableCell>` → `<TableCell>{m.reason || ""}</TableCell>` (binop: ?? -> ||)
- `m00504` L2223: `const backHref = permittedAppPath(searchParams.get("returnTo"), can) ?? "/animals";` → `const backHref = permittedAppPath(searchParams.get("returnTo"), can) || "/animals";` (binop: ?? -> ||)

### src/app/(app)/animals/page.tsx (34)

- `m00546` L120: `(BUCKET_REQUIRED_SEX[bucket] ?? sex) === sex;` → `(BUCKET_REQUIRED_SEX[bucket] || sex) === sex;` (binop: ?? -> ||)
- `m00584` L231: `.max(1_000_000_000, "Purchase price cannot exceed ₹1,000,000,000")` → `.max(999999999, "Purchase price cannot exceed ₹1,000,000,000")` (intconst: 1000000000 -> 999999999)
- `m00586` L234: `seller_name: z.string().max(120).optional(),` → `seller_name: z.string().max(119).optional(),` (intconst: 120 -> 119)
- `m00590` L253: `historical_import_reason: z.string().max(255).optional().default(""),` → `historical_import_reason: z.string().max(256).optional().default(""),` (intconst: 255 -> 256)
- `m00591` L253: `historical_import_reason: z.string().max(255).optional().default(""),` → `historical_import_reason: z.string().max(254).optional().default(""),` (intconst: 255 -> 254)
- `m00613` L329: `const raw = searchParams.get("page") ?? "";` → `const raw = searchParams.get("page") || "";` (binop: ?? -> ||)
- `m00618` L335: `return parsed >= 1 ? Math.min(parsed, MAX_PAGE) : 1;` → `return parsed > 1 ? Math.min(parsed, MAX_PAGE) : 1;` (compare: >= -> >)
- `m00619` L335: `return parsed >= 1 ? Math.min(parsed, MAX_PAGE) : 1;` → `return parsed >= 2 ? Math.min(parsed, MAX_PAGE) : 1;` (intconst: 1 -> 2)
- `m00644` L466: `shouldValidate: true,` → `shouldValidate: false,` (boolconst: -> false)
- `m00647` L474: `if (!isOwner && source === AnimalCreateInSource.BORN) {` → `if (!isOwner && source !== AnimalCreateInSource.BORN) {` (compare: === -> !==)
- `m00648` L475: `setValue("source", AnimalCreateInSource.PURCHASED, { shouldValidate: true });` → `setValue("source", AnimalCreateInSource.PURCHASED, { shouldValidate: false });` (boolconst: -> false)
- `m00660` L505: `? { horned: false }` → `? { horned: true }` (boolconst: -> true)
- `m00662` L510: `birth_weight: values.birth_weight ?? null,` → `birth_weight: values.birth_weight || null,` (binop: ?? -> ||)
- `m00663` L512: `purchase_price: values.purchase_price ?? null,` → `purchase_price: values.purchase_price || null,` (binop: ?? -> ||)
- `m00665` L514: `weight_kg: values.weight_kg ?? null,` → `weight_kg: values.weight_kg || null,` (binop: ?? -> ||)
- `m00677` L546: `const cancelBusy = isSubmitting || createFlight.pending;` → `const cancelBusy = isSubmitting && createFlight.pending;` (binop: || -> &&)
- `m00702` L698: `? vocabulary.breedingEntry.male.minMonths` → `? vocabulary.breedingEntry.female.minMonthsvocabulary.breedingEntry.male.minMonths` (ifexp: swap ternary branches)
- `m00703` L697: `{sex === AnimalCreateInSex.M` → `{sex !== AnimalCreateInSex.M` (compare: === -> !==)
- `m00706` L713: `maxLength={60}` → `maxLength={61}` (intconst: 60 -> 61)
- `m00707` L713: `maxLength={60}` → `maxLength={59}` (intconst: 60 -> 59)
- `m00754` L1009: `const navigationSeq = useRef(0);` → `const navigationSeq = useRef(1);` (intconst: 0 -> 1)
- `m00755` L1025: `const pageNavigationPending = useRef(false);` → `const pageNavigationPending = useRef(true);` (boolconst: -> true)
- `m00756` L1046: `urlQ: (new URLSearchParams(key).get("q") ?? "").trim(),` → `urlQ: (new URLSearchParams(key).get("q") || "").trim(),` (binop: ?? -> ||)
- `m00773` L1117: `const hasUnmatchedPending = pending.size > 0;` → `const hasUnmatchedPending = pending.size >= 0;` (compare: > -> >=)
- `m00777` L1125: `if (seq > matchedSeq) break;` → `if (seq > matchedSeq) continue;` (loopjump: break -> continue)
- `m00778` L1140: `const nextQ = clampSearch(params.get("q") ?? "");` → `const nextQ = clampSearch(params.get("q") || "");` (binop: ?? -> ||)
- `m00789` L1184: `const urlQ = (new URLSearchParams(paramsKey).get("q") ?? "").trim();` → `const urlQ = (new URLSearchParams(paramsKey).get("q") || "").trim();` (binop: ?? -> ||)
- `m00793` L1197: `setSearchNavigationPending(true);` → `setSearchNavigationPending(false);` (boolconst: -> false)
- `m00797` L1206: `page: 1,` → `page: 0,` (intconst: 1 -> 0)
- `m00798` L1209: `}, 300);` → `}, 301);` (intconst: 300 -> 301)
- `m00799` L1209: `}, 300);` → `}, 299);` (intconst: 300 -> 299)
- `m00821` L1226: `const total = Math.max(0, payload?.total ?? 0);` → `const total = Math.max(0, payload?.total || 0);` (binop: ?? -> ||)
- `m00822` L1226: `const total = Math.max(0, payload?.total ?? 0);` → `const total = Math.max(0, payload?.total ?? 1);` (intconst: 0 -> 1)
- `m00846` L1303: `setPage(1);` → `setPage(2);` (intconst: 1 -> 2)

### src/app/(app)/app-layout-client.tsx (1)

- `m00973` L347: `if (loading || !user || !farmId) {` → `if (loading && !user || !farmId) {` (binop: || -> &&)

### src/app/(app)/breeding/page.tsx (1)

- `m01297` L1305: `<PageSkeleton stats={3} cards={1} />` → `<PageSkeleton stats={3} cards={0} />` (intconst: 1 -> 0)

### src/app/(app)/feeding/page.tsx (1)

- `m01945` L644: `const recorded = dispensedByBucket.get(bucketName) ?? 0;` → `const recorded = dispensedByBucket.get(bucketName) || 0;` (binop: ?? -> ||)

### src/app/(app)/kidding/page.tsx (1)

- `m03403` L1006: `payload.total === 0 ? 0 : Math.floor((payload.total - 1) / payload.limit) * payload.limit;` → `payload.total === 0 ? 1 : Math.floor((payload.total - 1) / payload.limit) * payload.limit;` (intconst: 0 -> 1)

### src/app/(app)/ops-simulation/page.tsx (1)

- `m03727` L785: `{rowErrors.length > 0 && (` → `{rowErrors.length > 0 || (` (binop: && -> ||)

### src/app/(app)/planner/page.tsx (1)

- `m04051` L708: `const evaluation = report ? (report.plan.after ?? report.plan.before) : null;` → `const evaluation = report ? (report.plan.after || report.plan.before) : null;` (binop: ?? -> ||)

### src/app/(app)/purchases/page.tsx (1)

- `m04213` L127: `origin_market: z.string().max(120, "At most 120 characters").optional(),` → `origin_market: z.string().max(119, "At most 120 characters").optional(),` (intconst: 120 -> 119)

### src/app/(app)/reports/page.tsx (1)

- `m04537` L361: `<TableCell colSpan={2} className="text-muted-foreground">` → `<TableCell colSpan={1} className="text-muted-foreground">` (intconst: 2 -> 1)

### src/app/(app)/screening/page.tsx (1)

- `m04699` L487: `{agrees !== null && run.run_status === "OK" ? (` → `{agrees === null && run.run_status === "OK" ? (` (compare: !== -> ===)

### src/app/(app)/simulation/page.tsx (2)

- `m05509` L1313: `setEditorVersion((version) => version + 1);` → `setEditorVersion((version) => version + 2);` (intconst: 1 -> 2)
- `m06157` L3327: `tabIndex={-1}` → `tabIndex={-2}` (intconst: 1 -> 2)

### src/app/(app)/team/page.tsx (1)

- `m06805` L1054: `screening_flags: prefs?.screening_flags ?? false,` → `screening_flags: prefs?.screening_flags || false,` (binop: ?? -> ||)

### src/app/login/page.tsx (1)

- `m07129` L124: `router.push(requested ?? firstPermittedPathFromList(permissions.permissions));` → `router.push(requested || firstPermittedPathFromList(permissions.permissions));` (binop: ?? -> ||)

### src/components/account-dialog.tsx (12)

- `m07413` L69: `const [codesCopied, setCodesCopied] = useState(false);` → `const [codesCopied, setCodesCopied] = useState(true);` (boolconst: -> true)
- `m07414` L74: `const dialogEpoch = useRef(0);` → `const dialogEpoch = useRef(1);` (intconst: 0 -> 1)
- `m07415` L76: `const mounted = useRef(true);` → `const mounted = useRef(false);` (boolconst: -> false)
- `m07417` L95: `mounted.current = false;` → `mounted.current = true;` (boolconst: -> true)
- `m07418` L99: `dialogEpoch.current += 1;` → `dialogEpoch.current += 2;` (intconst: 1 -> 2)
- `m07419` L99: `dialogEpoch.current += 1;` → `dialogEpoch.current += 0;` (intconst: 1 -> 0)
- `m07424` L127: `dialogEpoch.current += 1;` → `dialogEpoch.current += 2;` (intconst: 1 -> 2)
- `m07428` L140: `setCodesCopied(false);` → `setCodesCopied(true);` (boolconst: -> true)
- `m07495` L419: `const release = () => finishAction("password");` → `const release = () => fin||hAction("password");` (binop: ?? -> ||)
- `m07496` L427: `void submission.then(release, release);` → `void s||mission.then(release, release);` (binop: ?? -> ||)
- `m07500` L429: `<Dialog open={open} onOpenChange={(next) => (next ? setOpen(true) : close())}>` → `<Dialog open={open} onOpenChange={(next) => (next ? setOpen(true) : close||)}>` (binop: ?? -> ||)
- `m07501` L437: `/>` → `||    />` (binop: ?? -> ||)

### src/components/animal-picker.tsx (2)

- `m07603` L103: `eligibilityKey ??` → `eligibilityKey ||` (binop: ?? -> ||)
- `m07607` L109: `selectedAnimalId ?? 0,` → `selectedAnimalId || 0,` (binop: ?? -> ||)

### src/components/charts.tsx (4)

- `m07700` L180: `const slot = finiteBins.length > 0 ? (width - pad * 2) / finiteBins.length : width;` → `const slot = finiteBins.length >= 0 ? (width - pad * 2) / finiteBins.length : width;` (compare: > -> >=)
- `m07730` L188: `const markerLines = (markers ?? []).flatMap((marker, i) => {` → `const markerLines = (markers || []).flatMap((marker, i) => {` (binop: ?? -> ||)
- `m07744` L203: `<title>{`${marker.label}: ${marker.display ?? marker.value}`}</title>` → `<title>{`${marker.label}: ${marker.display || marker.value}`}</title>` (binop: ?? -> ||)
- `m07745` L215: `ariaLabel ??` → `ariaLabel ||` (binop: ?? -> ||)

### src/components/data-table-card.tsx (4)

- `m07773` L41: `const hasTitle = Boolean(title) || title === 0;` → `const hasTitle = Boolean(title) || title !== 0;` (compare: === -> !==) ⚠capped-sample
- `m07774` L41: `const hasTitle = Boolean(title) || title === 0;` → `const hasTitle = Boolean(title) || title === 1;` (intconst: 0 -> 1) ⚠capped-sample
- `m07779` L43: `const hasActions = Boolean(actions) || actions === 0;` → `const hasActions = Boolean(actions) || actions !== 0;` (compare: === -> !==) ⚠capped-sample
- `m07780` L43: `const hasActions = Boolean(actions) || actions === 0;` → `const hasActions = Boolean(actions) || actions === 1;` (intconst: 0 -> 1) ⚠capped-sample

### src/components/empty-state.tsx (2)

- `m07792` L21: `const hasChildren = Boolean(children) || children === 0;` → `const hasChildren = Boolean(children) || children !== 0;` (compare: === -> !==) ⚠capped-sample
- `m07793` L21: `const hasChildren = Boolean(children) || children === 0;` → `const hasChildren = Boolean(children) || children === 1;` (intconst: 0 -> 1) ⚠capped-sample

### src/components/page-header.tsx (2)

- `m07873` L17: `const hasActions = Boolean(actions) || actions === 0;` → `const hasActions = Boolean(actions) || actions !== 0;` (compare: === -> !==) ⚠capped-sample
- `m07874` L17: `const hasActions = Boolean(actions) || actions === 0;` → `const hasActions = Boolean(actions) || actions === 1;` (intconst: 0 -> 1) ⚠capped-sample

### src/components/pagination-controls.tsx (6)

- `m07884` L30: `if (!Number.isFinite(total) || total <= 0) return null;` → `if (!Number.isFinite(total) || total <= 1) return null;` (intconst: 0 -> 1) ⚠capped-sample
- `m07886` L36: `Number.isFinite(limit) && limit > 0 ? Math.max(1, Math.trunc(limit)) : 1;` → `Number.isFinite(limit) || limit > 0 ? Math.max(1, Math.trunc(limit)) : 1;` (binop: && -> ||) ⚠capped-sample
- `m07887` L36: `Number.isFinite(limit) && limit > 0 ? Math.max(1, Math.trunc(limit)) : 1;` → `Number.isFinite(limit) && limit >= 0 ? Math.max(1, Math.trunc(limit)) : 1;` (compare: > -> >=) ⚠capped-sample
- `m07888` L36: `Number.isFinite(limit) && limit > 0 ? Math.max(1, Math.trunc(limit)) : 1;` → `Number.isFinite(limit) && limit > 1 ? Math.max(1, Math.trunc(limit)) : 1;` (intconst: 0 -> 1) ⚠capped-sample
- `m07889` L36: `Number.isFinite(limit) && limit > 0 ? Math.max(1, Math.trunc(limit)) : 1;` → `Number.isFinite(limit) && limit > 0 ? Math.max(2, Math.trunc(limit)) : 1;` (intconst: 1 -> 2) ⚠capped-sample
- `m07890` L36: `Number.isFinite(limit) && limit > 0 ? Math.max(1, Math.trunc(limit)) : 1;` → `Number.isFinite(limit) && limit > 0 ? Math.max(0, Math.trunc(limit)) : 1;` (intconst: 1 -> 0) ⚠capped-sample

### src/components/permission-gate.tsx (2)

- `m07917` L105: `title={noAccessTitle ?? "You don't have access to this page."}` → `title={noAccessTitle || "You don't have access to this page."}` (binop: ?? -> ||) ⚠capped-sample
- `m07918` L112: `{noAccessMessage ?? "You don't have access to this page."}` → `{noAccessMessage || "You don't have access to this page."}` (binop: ?? -> ||) ⚠capped-sample

### src/components/remote-picker.tsx (15)

- `m07928` L72: `const SEARCH_DEBOUNCE_MS = 300;` → `const SEARCH_DEBOUNCE_MS = 301;` (intconst: 300 -> 301) ⚠capped-sample
- `m07929` L72: `const SEARCH_DEBOUNCE_MS = 300;` → `const SEARCH_DEBOUNCE_MS = 299;` (intconst: 300 -> 299) ⚠capped-sample
- `m07939` L119: `}, 0);` → `}, 1);` (intconst: 0 -> 1) ⚠capped-sample
- `m07941` L124: `(chosenOption?.value === value ? chosenOption : null) ??` → `(chosenOption?.value === value ? chosenOption : null) ||` (binop: ?? -> ||) ⚠capped-sample
- `m07951` L136: `if (!disabled || !nextOpen) setOpen(nextOpen);` → `if (!disabled || nextOpen) setOpen(nextOpen);` (not: drop !) ⚠capped-sample
- `m07958` L160: `{currentOption?.label ?? (value ? `Selected item ${value}` : placeholder)}` → `{currentOption?.label || (value ? `Selected item ${value}` : placeholder)}` (binop: ?? -> ||) ⚠capped-sample
- `m07963` L249: `if (debounceTimer.current === handle) debounceTimer.current = null;` → `if (debounceTimer.current !== handle) debounceTimer.current = null;` (compare: === -> !==) ⚠capped-sample
- `m07970` L278: `for (const page of results.data?.pages ?? []) {` → `for (const page of results.data?.pages || []) {` (binop: ?? -> ||) ⚠capped-sample
- `m07977` L303: `const checkedCount = lastPage?.nextOffset ?? 0;` → `const checkedCount = lastPage?.nextOffset || 0;` (binop: ?? -> ||) ⚠capped-sample
- `m07978` L303: `const checkedCount = lastPage?.nextOffset ?? 0;` → `const checkedCount = lastPage?.nextOffset ?? 1;` (intconst: 0 -> 1) ⚠capped-sample
- `m07979` L304: `const total = lastPage?.total ?? 0;` → `const total = lastPage?.total || 0;` (binop: ?? -> ||) ⚠capped-sample
- `m07980` L304: `const total = lastPage?.total ?? 0;` → `const total = lastPage?.total ?? 1;` (intconst: 0 -> 1) ⚠capped-sample
- `m07985` L316: `const tabbableOptionIndex = selectedDisplayedIndex >= 0 ? selectedDisplayedIndex : 0;` → `const tabbableOptionIndex = selectedDisplayedIndex > 0 ? selectedDisplayedIndex : 0;` (compare: >= -> >) ⚠capped-sample
- `m07986` L316: `const tabbableOptionIndex = selectedDisplayedIndex >= 0 ? selectedDisplayedIndex : 0;` → `const tabbableOptionIndex = selectedDisplayedIndex >= 1 ? selectedDisplayedIndex : 0;` (intconst: 0 -> 1) ⚠capped-sample
- `m08020` L361: `{dialogDescription ?? "Search the farm records, then choose one option."}` → `{dialogDescription || "Search the farm records, then choose one option."}` (binop: ?? -> ||) ⚠capped-sample

### src/components/screening-check-dialog.tsx (12)

- `m08082` L66: `const [uploading, setUploading] = useState(false);` → `const [uploading, setUploading] = useState(true);` (boolconst: -> true)
- `m08083` L75: `const walkthroughEpoch = useRef(0);` → `const walkthroughEpoch = useRef(1);` (intconst: 0 -> 1)
- `m08084` L85: `walkthroughEpoch.current += 1;` → `walkthroughEpoch.current += 2;` (intconst: 1 -> 2)
- `m08086` L86: `if (!open) return;` → `if (open) return;` (not: drop !)
- `m08102` L139: `if (!pendingFile || !selectedBucket) return;` → `if (!pendingFile && !selectedBucket) return;` (binop: || -> &&)
- `m08118` L173: `method: result.data.upload_method ?? "POST",` → `method: result.data.upload_method || "POST",` (binop: ?? -> ||)
- `m08124` L190: `[selectedBucket]: (counts[selectedBucket] ?? 0) + 1,` → `[selectedBucket]: (counts[selectedBucket] || 0) + 1,` (binop: ?? -> ||)
- `m08136` L214: `const total = Object.values(uploadedByBucket).reduce((sum, count) => sum + count, 0);` → `const total = Object.values(uploadedByBucket).reduce((sum, count) => sum - count, 0);` (binop: + -> -)
- `m08137` L214: `const total = Object.values(uploadedByBucket).reduce((sum, count) => sum + count, 0);` → `const total = Object.values(uploadedByBucket).reduce((sum, count) => sum + count, 1);` (intconst: 0 -> 1)
- `m08150` L255: `{(buckets ?? []).map((row) => (` → `{(buckets || []).map((row) => (` (binop: ?? -> ||)
- `m08151` L267: `count: uploadedByBucket[row.bucket] ?? 0,` → `count: uploadedByBucket[row.bucket] || 0,` (binop: ?? -> ||)
- `m08154` L306: `onFileChosen(event.target.files?.[0] ?? null);` → `onFileChosen(event.target.files?.[0] || null);` (binop: ?? -> ||)

### src/components/skeletons.tsx (2)

- `m08180` L95: `style={{ width: c === 0 ? "22%" : `${Math.max(8, 18 - c * 2)}%` }}` → `style={{ width: c === 0 ? "22%" : `${Math.max(7, 18 - c * 2)}%` }}` (intconst: 8 -> 7) ⚠capped-sample
- `m08208` L159: `{children ?? t("common.loading")}` → `{children || t("common.loading")}` (binop: ?? -> ||) ⚠capped-sample

### src/components/stat-card.tsx (1)

- `m08219` L83: `trendToneClasses[trend.tone ?? "neutral"],` → `trendToneClasses[trend.tone || "neutral"],` (binop: ?? -> ||) ⚠capped-sample

### src/components/status-badge.tsx (3)

- `m08220` L81: `return STATUS_TONES[normalize(status)] ?? null;` → `return STATUS_TONES[normalize(status)] || null;` (binop: ?? -> ||) ⚠capped-sample
- `m08224` L108: `variant={tone ?? "secondary"}` → `variant={tone || "secondary"}` (binop: ?? -> ||) ⚠capped-sample
- `m08225` L116: `{children ?? humanize(status)}` → `{children || humanize(status)}` (binop: ?? -> ||) ⚠capped-sample

### src/components/task-row-actions.tsx (16)

- `m08229` L82: `const [rejectMissing, setRejectMissing] = useState(false);` → `const [rejectMissing, setRejectMissing] = useState(true);` (boolconst: -> true)
- `m08259` L262: `size={touch ? "default" : "sm"}` → `size={touch ? "sm" : "default"}` (ifexp: swap ternary branches)
- `m08268` L281: `size={touch ? "default" : "sm"}` → `size={touch ? "sm" : "default"}` (ifexp: swap ternary branches)
- `m08289` L362: `days: task.recur_days ?? 0,` → `days: task.recur_days || 0,` (binop: ?? -> ||)
- `m08290` L362: `days: task.recur_days ?? 0,` → `days: task.recur_days ?? 1,` (intconst: 0 -> 1)
- `m08291` L370: `task.due_date > farmToday() ? task.due_date : farmToday(),` → `task.due_date > farmToday() ? farmToday() : task.due_date,` (ifexp: swap ternary branches)
- `m08292` L370: `task.due_date > farmToday() ? task.due_date : farmToday(),` → `task.due_date >= farmToday() ? task.due_date : farmToday(),` (compare: > -> >=)
- `m08293` L371: `task.recur_days ?? 0,` → `task.recur_days || 0,` (binop: ?? -> ||)
- `m08294` L371: `task.recur_days ?? 0,` → `task.recur_days ?? 1,` (intconst: 0 -> 1)
- `m08300` L411: `size={touch ? "default" : "sm"}` → `size={touch ? "sm" : "default"}` (ifexp: swap ternary branches)
- `m08303` L424: `size={touch ? "default" : "sm"}` → `size={touch ? "sm" : "default"}` (ifexp: swap ternary branches)
- `m08309` L442: `if (!nextOpen && actionFlight.pending) return;` → `if (nextOpen && actionFlight.pending) return;` (not: drop !)
- `m08314` L463: `maxLength={255}` → `maxLength={256}` (intconst: 255 -> 256)
- `m08315` L463: `maxLength={255}` → `maxLength={254}` (intconst: 255 -> 254)
- `m08316` L464: `rows={3}` → `rows={4}` (intconst: 3 -> 4)
- `m08317` L464: `rows={3}` → `rows={2}` (intconst: 3 -> 2)

### src/components/theme-toggle.tsx (1)

- `m08330` L36: `const current = pendingTheme.current ?? (isDark ? "dark" : "light");` → `const current = pendingTheme.current || (isDark ? "dark" : "light");` (binop: ?? -> ||)

### src/components/ui/checkbox.tsx (1)

- `m08336` L26: `{indeterminate ? <MinusIcon /> : <CheckIcon />}` → `{indeterminate ? <CheckIcon /> : <MinusIcon />}` (ifexp: swap ternary branches) ⚠capped-sample

### src/components/ui/select.tsx (1)

- `m08341` L22: `onValueChange={(value, eventDetails) => onValueChange?.(value ?? "", eventDetails)}` → `onValueChange={(value, eventDetails) => onValueChange?.(value || "", eventDetails)}` (binop: ?? -> ||) ⚠capped-sample

### src/lib/api-client.ts (30)

- `m08420` L17: `let authSessionEpoch = 0;` → `let authSessionEpoch = 1;` (intconst: 0 -> 1)
- `m08421` L19: `let farmScopeEpoch = 0;` → `let farmScopeEpoch = 1;` (intconst: 0 -> 1)
- `m08480` L108: `authFailureRegistrations.length = 0;` → `authFailureRegistrations.length = 1;` (intconst: 0 -> 1)
- `m08486` L122: `onAuthFailure = authFailureRegistrations.at(-1)?.handler ?? null;` → `onAuthFailure = authFailureRegistrations.at(-1)?.handler || null;` (binop: ?? -> ||)
- `m08511` L290: `const contentType = (resp.headers.get("content-type") ?? "").toLowerCase();` → `const contentType = (resp.headers.get("content-type") || "").toLowerCase();` (binop: ?? -> ||)
- `m08520` L311: `const refreshedActorScope = tokenScope ?? userScope;` → `const refreshedActorScope = tokenScope || userScope;` (binop: ?? -> ||)
- `m08528` L373: `}, 0);` → `}, 1);` (intconst: 0 -> 1)
- `m08537` L437: `if (typeof code === "string" && code.length > 0) return code;` → `if (typeof code === "string" && code.length > 1) return code;` (intconst: 0 -> 1)
- `m08560` L503: `const root = path[0] ?? "";` → `const root = path[0] || "";` (binop: ?? -> ||)
- `m08566` L533: `const rawPathname = path.split(/[?#]/, 1)[0];` → `const rawPathname = path.split(/[?#]/, 2)[0];` (intconst: 1 -> 2)
- `m08571` L539: `(path.startsWith("/api/") && parsed.pathname.startsWith("/api/")) ||` → `(path.startsWith("/api/") || parsed.pathname.startsWith("/api/")) ||` (binop: && -> ||)
- `m08602` L645: `const requestPath = path.split(/[?#]/, 1)[0];` → `const requestPath = path.split(/[?#]/, 2)[0];` (intconst: 1 -> 2)
- `m08607` L647: `requestPath.length > 1 && requestPath.endsWith("/")` → `requestPath.length >= 1 && requestPath.endsWith("/")` (compare: > -> >=)
- `m08608` L647: `requestPath.length > 1 && requestPath.endsWith("/")` → `requestPath.length > 2 && requestPath.endsWith("/")` (intconst: 1 -> 2)
- `m08609` L647: `requestPath.length > 1 && requestPath.endsWith("/")` → `requestPath.length > 0 && requestPath.endsWith("/")` (intconst: 1 -> 0)
- `m08613` L650: `const verb = (method ?? "GET").toUpperCase();` → `const verb = (method || "GET").toUpperCase();` (binop: ?? -> ||)
- `m08620` L658: `const requestPath = path.split(/[?#]/, 1)[0];` → `const requestPath = path.split(/[?#]/, 2)[0];` (intconst: 1 -> 2)
- `m08625` L660: `const route = requestPath.length > 1 && requestPath.endsWith("/") ? requestPath.slice(0, -1) : requestPath;` → `const route = requestPath.length >= 1 && requestPath.endsWith("/") ? requestPath.slice(0, -1) : requestPath;` (compare: > -> >=)
- `m08626` L660: `const route = requestPath.length > 1 && requestPath.endsWith("/") ? requestPath.slice(0, -1) : requestPath;` → `const route = requestPath.length > 2 && requestPath.endsWith("/") ? requestPath.slice(0, -1) : requestPath;` (intconst: 1 -> 2)
- `m08627` L660: `const route = requestPath.length > 1 && requestPath.endsWith("/") ? requestPath.slice(0, -1) : requestPath;` → `const route = requestPath.length > 0 && requestPath.endsWith("/") ? requestPath.slice(0, -1) : requestPath;` (intconst: 1 -> 0)
- `m08633` L663: `return (method ?? "GET").toUpperCase() === "POST" && route === "/api/auth/logout";` → `return (method || "GET").toUpperCase() === "POST" && route === "/api/auth/logout";` (binop: ?? -> ||)
- `m08638` L688: `if (cookieMutation && authSessionEpoch !== sessionScope) {` → `if (cookieMutation || authSessionEpoch !== sessionScope) {` (binop: && -> ||)
- `m08650` L704: `const requestPathname = path.split(/[?#]/, 1)[0];` → `const requestPathname = path.split(/[?#]/, 2)[0];` (intconst: 1 -> 2)
- `m08661` L796: `const requestPath = path.split("?", 1)[0];` → `const requestPath = path.split("?", 2)[0];` (intconst: 1 -> 2)
- `m08666` L798: `requestPath.length > 1 && requestPath.endsWith("/")` → `requestPath.length >= 1 && requestPath.endsWith("/")` (compare: > -> >=)
- `m08667` L798: `requestPath.length > 1 && requestPath.endsWith("/")` → `requestPath.length > 2 && requestPath.endsWith("/")` (intconst: 1 -> 2)
- `m08668` L798: `requestPath.length > 1 && requestPath.endsWith("/")` → `requestPath.length > 0 && requestPath.endsWith("/")` (intconst: 1 -> 0)
- `m08692` L864: `path.split("?", 1)[0] === "/api/auth/farms" ? null : currentFarmId;` → `path.split("?", 2)[0] === "/api/auth/farms" ? null : currentFarmId;` (intconst: 1 -> 2)
- `m08693` L864: `path.split("?", 1)[0] === "/api/auth/farms" ? null : currentFarmId;` → `path.split("?", 0)[0] === "/api/auth/farms" ? null : currentFarmId;` (intconst: 1 -> 0)
- `m08694` L864: `path.split("?", 1)[0] === "/api/auth/farms" ? null : currentFarmId;` → `path.split("?", 1)[1] === "/api/auth/farms" ? null : currentFarmId;` (intconst: 0 -> 1)

### src/lib/auth-context.tsx (66)

- `m08707` L127: `if (value === null || !value.startsWith(FARM_STORAGE_REVOKED_PREFIX)) {` → `if (value === null && !value.startsWith(FARM_STORAGE_REVOKED_PREFIX)) {` (binop: || -> &&) ⚠capped-sample
- `m08711` L131: `return Number.isSafeInteger(stored) && stored > 0 ? stored : null;` → `return Number.isSafeInteger(stored) || stored > 0 ? stored : null;` (binop: && -> ||)
- `m08712` L131: `return Number.isSafeInteger(stored) && stored > 0 ? stored : null;` → `return Number.isSafeInteger(stored) && stored >= 0 ? stored : null;` (compare: > -> >=)
- `m08724` L187: `const mounted = useRef(true);` → `const mounted = useRef(false);` (boolconst: -> false) ⚠capped-sample
- `m08725` L190: `const farmRefreshGeneration = useRef(0);` → `const farmRefreshGeneration = useRef(1);` (intconst: 0 -> 1) ⚠capped-sample
- `m08726` L194: `const sessionEstablishmentGeneration = useRef(0);` → `const sessionEstablishmentGeneration = useRef(1);` (intconst: 0 -> 1) ⚠capped-sample
- `m08727` L199: `const appliedFarmGeneration = useRef(0);` → `const appliedFarmGeneration = useRef(1);` (intconst: 0 -> 1) ⚠capped-sample
- `m08730` L217: `farmRefreshGeneration.current += 1;` → `farmRefreshGeneration.current += 2;` (intconst: 1 -> 2) ⚠capped-sample
- `m08731` L217: `farmRefreshGeneration.current += 1;` → `farmRefreshGeneration.current += 0;` (intconst: 1 -> 0) ⚠capped-sample
- `m08732` L219: `sessionEstablishmentGeneration.current += 1;` → `sessionEstablishmentGeneration.current += 2;` (intconst: 1 -> 2) ⚠capped-sample
- `m08733` L219: `sessionEstablishmentGeneration.current += 1;` → `sessionEstablishmentGeneration.current += 0;` (intconst: 1 -> 0) ⚠capped-sample
- `m08735` L238: `const selected = farmsRef.current.find((farm) => farm.id === id);` → `const selected = farmsRef.current.find((farm) => farm.id !== id);` (compare: === -> !==) ⚠capped-sample
- `m08736` L239: `setActiveFarmTimezone(timezone ?? selected?.timezone);` → `setActiveFarmTimezone(timezone || selected?.timezone);` (binop: ?? -> ||) ⚠capped-sample
- `m08737` L249: `farmRefreshGeneration.current += 1;` → `farmRefreshGeneration.current += 2;` (intconst: 1 -> 2) ⚠capped-sample
- `m08738` L249: `farmRefreshGeneration.current += 1;` → `farmRefreshGeneration.current += 0;` (intconst: 1 -> 0) ⚠capped-sample
- `m08739` L251: `sessionEstablishmentGeneration.current += 1;` → `sessionEstablishmentGeneration.current += 2;` (intconst: 1 -> 2) ⚠capped-sample
- `m08740` L251: `sessionEstablishmentGeneration.current += 1;` → `sessionEstablishmentGeneration.current += 0;` (intconst: 1 -> 0) ⚠capped-sample
- `m08743` L278: `existingFlight.teardownEpoch === authSessionEpochValue()` → `existingFlight.teardownEpoch !== authSessionEpochValue()` (compare: === -> !==) ⚠capped-sample
- `m08744` L284: `forcedLogout.current = true;` → `forcedLogout.current = false;` (boolconst: -> false) ⚠capped-sample
- `m08745` L299: `if (signOutFlight.current === flight) signOutFlight.current = null;` → `if (signOutFlight.current !== flight) signOutFlight.current = null;` (compare: === -> !==) ⚠capped-sample
- `m08747` L309: `const preferred = farmIdRef.current ?? readStoredFarmId();` → `const preferred = farmIdRef.current || readStoredFarmId();` (binop: ?? -> ||) ⚠capped-sample
- `m08758` L366: `if (!mounted.current || generation !== farmRefreshGeneration.current) return;` → `if (!mounted.current && generation !== farmRefreshGeneration.current) return;` (binop: || -> &&) ⚠capped-sample
- `m08759` L366: `if (!mounted.current || generation !== farmRefreshGeneration.current) return;` → `if (mounted.current || generation !== farmRefreshGeneration.current) return;` (not: drop !) ⚠capped-sample
- `m08760` L366: `if (!mounted.current || generation !== farmRefreshGeneration.current) return;` → `if (!mounted.current || generation === farmRefreshGeneration.current) return;` (compare: !== -> ===) ⚠capped-sample
- `m08761` L369: `if (!mounted.current || generation !== farmRefreshGeneration.current) return;` → `if (!mounted.current && generation !== farmRefreshGeneration.current) return;` (binop: || -> &&) ⚠capped-sample
- `m08765` L403: `isAuthSessionChangedError(retryable) ||` → `isAuthSessionChangedError(retryable) &&` (binop: || -> &&) ⚠capped-sample
- `m08766` L402: `establishmentGeneration !== sessionEstablishmentGeneration.current ||` → `establishmentGeneration !== sessionEstablishmentGeneration.current &&` (binop: || -> &&) ⚠capped-sample
- `m08767` L401: `!mounted.current ||` → `!mounted.current &&` (binop: || -> &&) ⚠capped-sample
- `m08768` L401: `!mounted.current ||` → `mounted.current ||` (not: drop !) ⚠capped-sample
- `m08769` L402: `establishmentGeneration !== sessionEstablishmentGeneration.current ||` → `establishmentGeneration === sessionEstablishmentGeneration.current ||` (compare: !== -> ===) ⚠capped-sample
- `m08770` L404: `authSessionEpochValue() !== ownedEpoch` → `authSessionEpochValue() === ownedEpoch` (compare: !== -> ===) ⚠capped-sample
- `m08771` L408: `await new Promise((resolve) => setTimeout(resolve, 750));` → `await new Promise((resolve) => setTimeout(resolve, 751));` (intconst: 750 -> 751) ⚠capped-sample
- `m08772` L408: `await new Promise((resolve) => setTimeout(resolve, 750));` → `await new Promise((resolve) => setTimeout(resolve, 749));` (intconst: 750 -> 749) ⚠capped-sample
- `m08776` L420: `farmGeneration !== farmRefreshGeneration.current &&` → `farmGeneration === farmRefreshGeneration.current &&` (compare: !== -> ===) ⚠capped-sample
- `m08777` L422: `appliedFarmGeneration.current > farmGeneration` → `appliedFarmGeneration.current >= farmGeneration` (compare: > -> >=) ⚠capped-sample
- `m08778` L430: `isAuthSessionChangedError(error) ||` → `isAuthSessionChangedError(error) &&` (binop: || -> &&) ⚠capped-sample
- `m08779` L429: `establishmentGeneration !== sessionEstablishmentGeneration.current ||` → `establishmentGeneration !== sessionEstablishmentGeneration.current &&` (binop: || -> &&) ⚠capped-sample
- `m08780` L428: `!mounted.current ||` → `!mounted.current &&` (binop: || -> &&) ⚠capped-sample
- `m08787` L485: `forcedLogout.current = true;` → `forcedLogout.current = false;` (boolconst: -> false) ⚠capped-sample
- `m08791` L505: `forcedLogout.current = true;` → `forcedLogout.current = false;` (boolconst: -> false) ⚠capped-sample
- `m08796` L536: `stored !== farmIdRef.current &&` → `stored !== farmIdRef.current ||` (binop: && -> ||) ⚠capped-sample
- `m08797` L535: `stored > 0 &&` → `stored > 0 ||` (binop: && -> ||) ⚠capped-sample
- `m08798` L534: `Number.isSafeInteger(stored) &&` → `Number.isSafeInteger(stored) ||` (binop: && -> ||) ⚠capped-sample
- `m08799` L535: `stored > 0 &&` → `stored >= 0 &&` (compare: > -> >=) ⚠capped-sample
- `m08800` L535: `stored > 0 &&` → `stored > 1 &&` (intconst: 0 -> 1) ⚠capped-sample
- `m08802` L537: `farmsRef.current.some((farm) => farm.id === stored)` → `farmsRef.current.some((farm) => farm.id !== stored)` (compare: === -> !==) ⚠capped-sample
- `m08803` L548: `initialRefreshStarted.current = true;` → `initialRefreshStarted.current = false;` (boolconst: -> false) ⚠capped-sample
- `m08804` L558: `for (let attempt = 0; attempt < BOOTSTRAP_REFRESH_ATTEMPTS; attempt += 1) {` → `for (let attempt = 1; attempt < BOOTSTRAP_REFRESH_ATTEMPTS; attempt += 1) {` (intconst: 0 -> 1) ⚠capped-sample
- `m08805` L558: `for (let attempt = 0; attempt < BOOTSTRAP_REFRESH_ATTEMPTS; attempt += 1) {` → `for (let attempt = 0; attempt <= BOOTSTRAP_REFRESH_ATTEMPTS; attempt += 1) {` (compare: < -> <=) ⚠capped-sample
- `m08806` L558: `for (let attempt = 0; attempt < BOOTSTRAP_REFRESH_ATTEMPTS; attempt += 1) {` → `for (let attempt = 0; attempt < BOOTSTRAP_REFRESH_ATTEMPTS; attempt += 2) {` (intconst: 1 -> 2) ⚠capped-sample
- `m08807` L558: `for (let attempt = 0; attempt < BOOTSTRAP_REFRESH_ATTEMPTS; attempt += 1) {` → `for (let attempt = 0; attempt < BOOTSTRAP_REFRESH_ATTEMPTS; attempt += 0) {` (intconst: 1 -> 0) ⚠capped-sample
- `m08809` L562: `break;` → `continue;` (loopjump: break -> continue) ⚠capped-sample
- `m08811` L564: `if (outcome.kind === "rejected") break;` → `if (outcome.kind === "rejected") continue;` (loopjump: break -> continue) ⚠capped-sample
- `m08812` L565: `if (attempt < BOOTSTRAP_REFRESH_ATTEMPTS - 1) {` → `if (attempt <= BOOTSTRAP_REFRESH_ATTEMPTS - 1) {` (compare: < -> <=) ⚠capped-sample
- `m08813` L565: `if (attempt < BOOTSTRAP_REFRESH_ATTEMPTS - 1) {` → `if (attempt < BOOTSTRAP_REFRESH_ATTEMPTS + 1) {` (binop: - -> +) ⚠capped-sample
- `m08814` L565: `if (attempt < BOOTSTRAP_REFRESH_ATTEMPTS - 1) {` → `if (attempt < BOOTSTRAP_REFRESH_ATTEMPTS - 2) {` (intconst: 1 -> 2) ⚠capped-sample
- `m08815` L565: `if (attempt < BOOTSTRAP_REFRESH_ATTEMPTS - 1) {` → `if (attempt < BOOTSTRAP_REFRESH_ATTEMPTS - 0) {` (intconst: 1 -> 0) ⚠capped-sample
- `m08816` L566: `await new Promise((resolve) => window.setTimeout(resolve, 750));` → `await new Promise((resolve) => window.setTimeout(resolve, 751));` (intconst: 750 -> 751) ⚠capped-sample
- `m08817` L566: `await new Promise((resolve) => window.setTimeout(resolve, 750));` → `await new Promise((resolve) => window.setTimeout(resolve, 749));` (intconst: 750 -> 749) ⚠capped-sample
- `m08818` L570: `if (body && mounted.current) {` → `if (body || mounted.current) {` (binop: && -> ||) ⚠capped-sample
- `m08829` L599: `if (!loading && !user && forcedLogout.current && PUBLIC_PATHS.includes(pathname)) {` → `if (!loading && !user && forcedLogout.current || PUBLIC_PATHS.includes(pathname)) {` (binop: && -> ||) ⚠capped-sample
- `m08830` L599: `if (!loading && !user && forcedLogout.current && PUBLIC_PATHS.includes(pathname)) {` → `if (!loading && !user || forcedLogout.current && PUBLIC_PATHS.includes(pathname)) {` (binop: && -> ||) ⚠capped-sample
- `m08831` L599: `if (!loading && !user && forcedLogout.current && PUBLIC_PATHS.includes(pathname)) {` → `if (!loading || !user && forcedLogout.current && PUBLIC_PATHS.includes(pathname)) {` (binop: && -> ||) ⚠capped-sample
- `m08832` L599: `if (!loading && !user && forcedLogout.current && PUBLIC_PATHS.includes(pathname)) {` → `if (loading && !user && forcedLogout.current && PUBLIC_PATHS.includes(pathname)) {` (not: drop !) ⚠capped-sample
- `m08833` L599: `if (!loading && !user && forcedLogout.current && PUBLIC_PATHS.includes(pathname)) {` → `if (!loading && user && forcedLogout.current && PUBLIC_PATHS.includes(pathname)) {` (not: drop !) ⚠capped-sample
- `m08834` L600: `forcedLogout.current = false;` → `forcedLogout.current = true;` (boolconst: -> true) ⚠capped-sample

### src/lib/csp.ts (3)

- `m08901` L49: `if (!Number.isInteger(port) || port <= 0 || port > 65535) return null;` → `if (!Number.isInteger(port) || port <= 0 || port > 65536) return null;` (intconst: 65535 -> 65536)
- `m08908` L104: `const imgOrigins = options.imgOrigins ?? [];` → `const imgOrigins = options.imgOrigins || [];` (binop: ?? -> ||)
- `m08909` L105: `const connectOrigins = options.connectOrigins ?? [];` → `const connectOrigins = options.connectOrigins || [];` (binop: ?? -> ||)

### src/lib/enum-labels.ts (3)

- `m08923` L335: `const language = lang ?? getActiveLanguage();` → `const language = lang || getActiveLanguage();` (binop: ?? -> ||) ⚠capped-sample
- `m08925` L337: `const telugu = TE_LABELS[kind]?.[value] ?? TE_LABELS[kind]?.[value.toUpperCase()];` → `const telugu = TE_LABELS[kind]?.[value] || TE_LABELS[kind]?.[value.toUpperCase()];` (binop: ?? -> ||) ⚠capped-sample
- `m08927` L341: `return BUCKET_LABELS[value] ?? titleCase(value);` → `return BUCKET_LABELS[value] || titleCase(value);` (binop: ?? -> ||) ⚠capped-sample

### src/lib/format.ts (17)

- `m08964` L7: `if (value === null || value === undefined || !Number.isFinite(value)) return "—";` → `if (value === null && value === undefined || !Number.isFinite(value)) return "—";` (binop: || -> &&)
- `m08969` L12: `if (Math.abs(value) >= 1e21) {` → `if (Math.abs(value) >= 1e+21) {` (intconst: 1e+21 -> 1e+21) ⚠capped-sample
- `m08970` L12: `if (Math.abs(value) >= 1e21) {` → `if (Math.abs(value) >= 1e+21) {` (intconst: 1e+21 -> 1e+21) ⚠capped-sample
- `m08971` L14: `maximumFractionDigits: 0,` → `maximumFractionDigits: 1,` (intconst: 0 -> 1)
- `m08974` L17: `return `${value < 0 ? "-" : ""}₹${grouped}`;` → `return `${value <= 0 ? "-" : ""}₹${grouped}`;` (compare: < -> <=)
- `m08975` L17: `return `${value < 0 ? "-" : ""}₹${grouped}`;` → `return `${value < 1 ? "-" : ""}₹${grouped}`;` (intconst: 0 -> 1)
- `m08979` L22: `const negative = value < 0 && (intPart !== "0" || fracPart !== "00");` → `const negative = value <= 0 && (intPart !== "0" || fracPart !== "00");` (compare: < -> <=)
- `m09030` L112: `!Number.isInteger(month) ||` → `!Number.isInteger(month) &&` (binop: || -> &&)
- `m09031` L111: `!Number.isInteger(year) ||` → `!Number.isInteger(year) &&` (binop: || -> &&)
- `m09038` L121: `const date = new Date(0);` → `const date = new Date(1);` (intconst: 0 -> 1)
- `m09042` L126: `date.getUTCMonth() !== month - 1 ||` → `date.getUTCMonth() !== month - 1 &&` (binop: || -> &&)
- `m09043` L125: `date.getUTCFullYear() !== year ||` → `date.getUTCFullYear() !== year &&` (binop: || -> &&)
- `m09044` L124: `Number.isNaN(date.getTime()) ||` → `Number.isNaN(date.getTime()) &&` (binop: || -> &&)
- `m09080` L203: `return Math.round((toDate.getTime() - fromDate.getTime()) / 86400000);` → `return Math.round((toDate.getTime() - fromDate.getTime()) / 86400001);` (intconst: 86400000 -> 86400001)
- `m09081` L203: `return Math.round((toDate.getTime() - fromDate.getTime()) / 86400000);` → `return Math.round((toDate.getTime() - fromDate.getTime()) / 86399999);` (intconst: 86400000 -> 86399999)
- `m09086` L224: `const [y, m, d] = match.slice(1, 4).map(Number);` → `const [y, m, d] = match.slice(1, 5).map(Number);` (intconst: 4 -> 5)
- `m09098` L232: `if ((lang ?? getActiveLanguage()) === "te") {` → `if ((lang || getActiveLanguage()) === "te") {` (binop: ?? -> ||) ⚠capped-sample

### src/lib/i18n/index.tsx (2)

- `m09106` L52: `const template = language === "te" ? (te[key] ?? en[key]) : en[key];` → `const template = language === "te" ? (te[key] || en[key]) : en[key];` (binop: ?? -> ||) ⚠capped-sample
- `m09107` L53: `return interpolate(template ?? key, vars);` → `return interpolate(template || key, vars);` (binop: ?? -> ||) ⚠capped-sample

### src/lib/idempotent-request.ts (24)

- `m09138` L99: `if (typeof candidate !== "object" || candidate === null) continue;` → `if (typeof candidate !== "object" || candidate === null) break;` (loopjump: continue -> break)
- `m09142` L108: `typeof record.expiresAt !== "number" ||` → `typeof record.expiresAt !== "number" &&` (binop: || -> &&)
- `m09163` L134: `return readPersistedRecords(storage, now).find((record) => record.digest === digest)?.key ?? null;` → `return readPersistedRecords(storage, now).find((record) => record.digest === digest)?.key || null;` (binop: ?? -> ||)
- `m09171` L161: `(count, record) => count + (liveDigests.has(record.digest) ? 1 : 0),` → `(count, record) => count + (liveDigests.has(record.digest) ? 2 : 0),` (intconst: 1 -> 2)
- `m09181` L167: `.sort((left, right) => right.expiresAt - left.expiresAt)` → `.sort((left, right) => right.expiresAt + left.expiresAt)` (binop: - -> +)
- `m09186` L185: `...bounded.slice(0, Math.max(MAX_LOGICAL_REQUESTS - 1, 0)),` → `...bounded.slice(0, Math.max(MAX_LOGICAL_REQUESTS + 1, 0)),` (binop: - -> +)
- `m09188` L185: `...bounded.slice(0, Math.max(MAX_LOGICAL_REQUESTS - 1, 0)),` → `...bounded.slice(0, Math.max(MAX_LOGICAL_REQUESTS - 0, 0)),` (intconst: 1 -> 0)
- `m09189` L185: `...bounded.slice(0, Math.max(MAX_LOGICAL_REQUESTS - 1, 0)),` → `...bounded.slice(0, Math.max(MAX_LOGICAL_REQUESTS - 1, 1)),` (intconst: 0 -> 1)
- `m09193` L207: `if (!subtle || typeof TextEncoder === "undefined") return null;` → `if (!subtle && typeof TextEncoder === "undefined") return null;` (binop: || -> &&)
- `m09200` L225: `return url.split(/[?#]/, 1)[0];` → `return url.split(/[?#]/, 2)[0];` (intconst: 1 -> 2)
- `m09201` L225: `return url.split(/[?#]/, 1)[0];` → `return url.split(/[?#]/, 0)[0];` (intconst: 1 -> 0)
- `m09202` L225: `return url.split(/[?#]/, 1)[0];` → `return url.split(/[?#]/, 1)[1];` (intconst: 0 -> 1)
- `m09204` L233: `if ((method ?? "GET").toUpperCase() !== "POST") return false;` → `if ((method || "GET").toUpperCase() !== "POST") return false;` (binop: ?? -> ||)
- `m09247` L289: `bytes[6] = (bytes[6] & 0x0f) | 0x40;` → `bytes[6] = (bytes[6] & 14) | 0x40;` (intconst: 15 -> 14)
- `m09257` L290: `bytes[8] = (bytes[8] & 0x3f) | 0x80;` → `bytes[8] = (bytes[8] & 62) | 0x80;` (intconst: 63 -> 62)
- `m09286` L327: `(init.method ?? "GET").toUpperCase(),` → `(init.method || "GET").toUpperCase(),` (binop: ?? -> ||)
- `m09287` L329: `farmScope ?? "",` → `farmScope || "",` (binop: ?? -> ||)
- `m09288` L334: `callerKey ?? "",` → `callerKey || "",` (binop: ?? -> ||)
- `m09289` L353: `(init.method ?? "GET").toUpperCase(),` → `(init.method || "GET").toUpperCase(),` (binop: ?? -> ||)
- `m09290` L356: `farmScope ?? "",` → `farmScope || "",` (binop: ?? -> ||)
- `m09334` L461: `const signal = init.signal ?? null;` → `const signal = init.signal || null;` (binop: ?? -> ||)
- `m09343` L497: `(callerKey ?? "")` → `(callerKey || "")` (binop: ?? -> ||)
- `m09344` L498: `: (persistedDigest ? loadPersistedKey(persistedDigest, now) : null) ??` → `: (persistedDigest ? loadPersistedKey(persistedDigest, now) : null) ||` (binop: ?? -> ||)
- `m09346` L501: `expiresAt: now + RETRY_KEY_TTL_MS,` → `expiresAt: now - RETRY_KEY_TTL_MS,` (binop: + -> -)

### src/lib/image-deps-guard.ts (10)

- `m09356` L49: `for (let index = 0; index < length; index += 1) {` → `for (let index = 0; index <= length; index += 1) {` (compare: < -> <=)
- `m09360` L50: `const delta = (left[index] ?? 0) - (right[index] ?? 0);` → `const delta = (left[index] || 0) - (right[index] ?? 0);` (binop: ?? -> ||)
- `m09362` L50: `const delta = (left[index] ?? 0) - (right[index] ?? 0);` → `const delta = (left[index] ?? 0) - (right[index] || 0);` (binop: ?? -> ||)
- `m09373` L73: ``(next ${nextVersion ?? "unknown"} does not disable HEIF decoding itself).`,` → ``(next ${nextVersion || "unknown"} does not disable HEIF decoding itself).`,` (binop: ?? -> ||)
- `m09384` L88: ``next ${nextVersion ?? "unknown"} decodes HEIF/AVIF input again — upgrading ` +` → ``next ${nextVersion || "unknown"} decodes HEIF/AVIF input again — upgrading ` +` (binop: ?? -> ||)
- `m09385` L102: `return sharp.versions ?? {};` → `return sharp.versions || {};` (binop: ?? -> ||)
- `m09386` L120: `const sharpProbe = deps.sharpProbe ?? defaultSharpProbe();` → `const sharpProbe = deps.sharpProbe || defaultSharpProbe();` (binop: ?? -> ||)
- `m09388` L123: `const manifest = join(deps.rootDir ?? process.cwd(), "node_modules", "next", "package.json");` → `const manifest = join(deps.rootDir || process.cwd(), "node_modules", "next", "package.json");` (binop: ?? -> ||)
- `m09391` L161: `process.env.NEXT_PHASE === "phase-production-build" ? "enforce" : "warn";` → `process.env.NEXT_PHASE === "phase-production-build" ? "warn" : "enforce";` (ifexp: swap ternary branches)
- `m09392` L161: `process.env.NEXT_PHASE === "phase-production-build" ? "enforce" : "warn";` → `process.env.NEXT_PHASE !== "phase-production-build" ? "enforce" : "warn";` (compare: === -> !==)

### src/lib/offline-queue.ts (24)

- `m09437` L80: `length: 0,` → `length: 1,` (intconst: 0 -> 1)
- `m09438` L88: `export function readOfflineQueue(storage: Storage = availableLocalStorage() ?? NULL_STORAGE): QueuedMutation[] {` → `export function readOfflineQueue(storage: Storage = availableLocalStorage() || NULL_STORAGE): QueuedMutation[] {` (binop: ?? -> ||)
- `m09448` L140: `body: init.body ?? null,` → `body: init.body || null,` (binop: ?? -> ||)
- `m09449` L141: `headers: { ...(init.headers ?? {}) },` → `headers: { ...(init.headers || {}) },` (binop: ?? -> ||)
- `m09451` L150: `while (next.length > 0 && JSON.stringify(next).length > MAX_STORAGE_BYTES) {` → `while (next.length >= 0 && JSON.stringify(next).length > MAX_STORAGE_BYTES) {` (compare: > -> >=)
- `m09459` L161: `if ((method ?? "GET").toUpperCase() !== "POST") return false;` → `if ((method || "GET").toUpperCase() !== "POST") return false;` (binop: ?? -> ||)
- `m09471` L174: `if (typeof navigator !== "undefined" && navigator.onLine === false) return true;` → `if (typeof navigator !== "undefined" || navigator.onLine === false) return true;` (binop: && -> ||)
- `m09476` L178: `"name" in error &&` → `"name" in error ||` (binop: && -> ||)
- `m09477` L177: `error !== null &&` → `error !== null ||` (binop: && -> ||)
- `m09478` L176: `typeof error === "object" &&` → `typeof error === "object" ||` (binop: && -> ||)
- `m09486` L213: `if (storage === null) return { replayed: 0, remaining: 0 };` → `if (storage === null) return { replayed: 1, remaining: 0 };` (intconst: 0 -> 1)
- `m09487` L213: `if (storage === null) return { replayed: 0, remaining: 0 };` → `if (storage === null) return { replayed: 0, remaining: 1 };` (intconst: 0 -> 1)
- `m09496` L230: `if (stopped) continue;` → `if (stopped) break;` (loopjump: continue -> break)
- `m09497` L234: `body: record.body ?? undefined,` → `body: record.body || undefined,` (binop: ?? -> ||)
- `m09519` L270: `let workersRunning = false;` → `let workersRunning = true;` (boolconst: -> true)
- `m09520` L276: `workersRunning = true;` → `workersRunning = false;` (boolconst: -> false)
- `m09521` L279: `if (scopes === null) return;` → `if (scopes !== null) return;` (compare: === -> !==)
- `m09522` L280: `if (typeof navigator !== "undefined" && navigator.onLine === false) return;` → `if (typeof navigator !== "undefined" || navigator.onLine === false) return;` (binop: && -> ||)
- `m09523` L280: `if (typeof navigator !== "undefined" && navigator.onLine === false) return;` → `if (typeof navigator === "undefined" && navigator.onLine === false) return;` (compare: !== -> ===)
- `m09524` L280: `if (typeof navigator !== "undefined" && navigator.onLine === false) return;` → `if (typeof navigator !== "undefined" && navigator.onLine !== false) return;` (compare: === -> !==)
- `m09525` L280: `if (typeof navigator !== "undefined" && navigator.onLine === false) return;` → `if (typeof navigator !== "undefined" && navigator.onLine === true) return;` (boolconst: -> true)
- `m09526` L285: `const timer = window.setInterval(drainIfScoped, 30_000);` → `const timer = window.setInterval(drainIfScoped, 30001);` (intconst: 30000 -> 30001)
- `m09527` L285: `const timer = window.setInterval(drainIfScoped, 30_000);` → `const timer = window.setInterval(drainIfScoped, 29999);` (intconst: 30000 -> 29999)
- `m09528` L287: `workersRunning = false;` → `workersRunning = true;` (boolconst: -> true)

### src/lib/permission-navigation.ts (6)

- `m09532` L29: `PERMISSION_LANDING_ROUTES.find(({ permission }) => can(permission))?.href ?? "/no-access"` → `PERMISSION_LANDING_ROUTES.find(({ permission }) => can(permission))?.href || "/no-access"` (binop: ?? -> ||)
- `m09534` L105: `const rawPath = safe.split(/[?#]/, 1)[0];` → `const rawPath = safe.split(/[?#]/, 2)[0];` (intconst: 1 -> 2)
- `m09537` L107: `if (rawPath.includes("%") || rawPath.includes("\\")) return null;` → `if (rawPath.includes("%") && rawPath.includes("\\")) return null;` (binop: || -> &&)
- `m09552` L162: `resolved.path.length > 1 && resolved.path.endsWith("/")` → `resolved.path.length >= 1 && resolved.path.endsWith("/")` (compare: > -> >=)
- `m09553` L162: `resolved.path.length > 1 && resolved.path.endsWith("/")` → `resolved.path.length > 2 && resolved.path.endsWith("/")` (intconst: 1 -> 2)
- `m09554` L162: `resolved.path.length > 1 && resolved.path.endsWith("/")` → `resolved.path.length > 0 && resolved.path.endsWith("/")` (intconst: 1 -> 0)

### src/lib/server-error-phrases.ts (3)

- `m09588` L51: `{ status: 403, test: /owner|role|permission|not allow/i, key: "serverErrors.permissionDenied" },` → `{ status: 404, test: /owner|role|permission|not allow/i, key: "serverErrors.permissionDenied" },` (intconst: 403 -> 404) ⚠capped-sample
- `m09589` L51: `{ status: 403, test: /owner|role|permission|not allow/i, key: "serverErrors.permissionDenied" },` → `{ status: 402, test: /owner|role|permission|not allow/i, key: "serverErrors.permissionDenied" },` (intconst: 403 -> 402) ⚠capped-sample
- `m09592` L62: `if (code !== undefined && code !== null) {` → `if (code !== undefined || code !== null) {` (binop: && -> ||) ⚠capped-sample

### src/lib/simulation-field-help.ts (2)

- `m09603` L582: `const variableLabel = RISK_VARIABLE_LABELS[key]?.(v) ?? key.replace(/_/g, " ");` → `const variableLabel = RISK_VARIABLE_LABELS[key]?.(v) || key.replace(/_/g, " ");` (binop: ?? -> ||)
- `m09608` L591: `const variableLabel = RISK_VARIABLE_LABELS[key]?.(v) ?? key.replace(/_/g, " ");` → `const variableLabel = RISK_VARIABLE_LABELS[key]?.(v) || key.replace(/_/g, " ");` (binop: ?? -> ||)

### src/lib/task-title.ts (6)

- `m09688` L70: `if ((name === "date" || name.endsWith("_date")) && typeof value === "string" && ISO_DATE.test(value)) {` → `if ((name !== "date" || name.endsWith("_date")) && typeof value === "string" && ISO_DATE.test(value)) {` (compare: === -> !==)
- `m09690` L73: `return typeof value === "number" && Number.isFinite(value) ? value : String(value);` → `return typeof value === "number" && Number.isFinite(value) ? String(value) : value;` (ifexp: swap ternary branches)
- `m09691` L73: `return typeof value === "number" && Number.isFinite(value) ? value : String(value);` → `return typeof value === "number" || Number.isFinite(value) ? value : String(value);` (binop: && -> ||)
- `m09692` L73: `return typeof value === "number" && Number.isFinite(value) ? value : String(value);` → `return typeof value !== "number" && Number.isFinite(value) ? value : String(value);` (compare: === -> !==)
- `m09701` L85: `for (const [name, value] of Object.entries(task.title_args ?? {})) {` → `for (const [name, value] of Object.entries(task.title_args || {})) {` (binop: ?? -> ||)
- `m09702` L86: `if (value === null || value === undefined) continue;` → `if (value === null && value === undefined) continue;` (binop: || -> &&)

### src/lib/use-permissions.ts (2)

- `m09713` L40: `const perms = new Set(payload?.permissions ?? []);` → `const perms = new Set(payload?.permissions || []);` (binop: ?? -> ||) ⚠capped-sample
- `m09716` L50: `isOwner: payload?.is_owner ?? false,` → `isOwner: payload?.is_owner || false,` (binop: ?? -> ||) ⚠capped-sample

### src/lib/use-single-flight.ts (1)

- `m09719` L13: `const mounted = useRef(true);` → `const mounted = useRef(false);` (boolconst: -> false) ⚠capped-sample

### src/lib/use-url-state.ts (1)

- `m09731` L47: `(key: string, fallback: string | null = null) => searchParams.get(key) ?? fallback,` → `(key: string, fallback: string | null = null) => searchParams.get(key) || fallback,` (binop: ?? -> ||) ⚠capped-sample

### src/lib/utils.ts (2)

- `m09755` L17: `if (!raw.startsWith("/") || raw.startsWith("//")) return null` → `if (!raw.startsWith("/") && raw.startsWith("//")) return null` (binop: || -> &&)
- `m09757` L26: `const rawPathname = raw.split(/[?#]/, 1)[0]` → `const rawPathname = raw.split(/[?#]/, 2)[0]` (intconst: 1 -> 2)
