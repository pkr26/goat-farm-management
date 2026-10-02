# Frontend Livestock Journeys Audit (2026-10-01)

Auditor 05 of 10 — FRONTEND CORRECTNESS of the livestock user journeys (animals, breeding, kidding, health, screening) and the app/auth/worker shells.
Scope: source files only (`*.test.*` exempt). Method: every in-scope file read completely; data flows traced through `lib/api-client.ts`, `lib/auth-context.tsx`, `lib/format.ts`, `lib/enum-labels.ts`, `lib/use-permissions.ts`, `lib/use-single-flight.ts`, `lib/farm-scope-guard.ts`, `lib/offline-queue.ts`, `lib/idempotent-request.ts`, and `components/permission-gate.tsx` before reporting. Read-only; no tests run, no source modified.

## Executive summary

This is an unusually well-hardened frontend. The livestock journeys (animals list/create/profile, breeding + ultrasound + loss, kidding record, health event log + record dialog, screening review) and all three shells (root, `(app)`, worker PWA) show systematic, deliberate defense against the classic failure classes this audit hunted:

- **Double-submit / duplicate records**: every mutation is wrapped in `useSingleFlight` + `fieldset disabled` + server-side idempotency keys (`lib/idempotent-request.ts`), including an offline queue that replays the *same* `Idempotency-Key`. No duplicate-create path found.
- **Races**: auth/session epoch fencing (`authSessionEpochValue`), farm-scope fencing (`captureFarmScope`) on every write continuation, latest-wins generation counters in the auth provider, a per-dialog `submissionEpoch` in the health dialog, and a genuinely rigorous pending-navigation ledger in the animals list URL state machine.
- **Loading/error states**: every query has skeleton/loading, mapped server-error, and retry states; background-refetch failure keeps last-good data with a stale-notice banner (breeding/kidding/health/screening) and freezes lifecycle writes via `profileSettling` (animal profile).
- **Form validation parity**: species gates (breeding-entry age/weight, litter caps, gestation windows, birth-weight bands, withdrawal caps) mirror backend constants; 422 field issues are mapped inline via `applyApiValidationToForm`, and errors hidden in the collapsed "Advanced" section force it open.
- **RBAC**: `PermissionGate` fails closed on permission errors; the worker surface has its own gates; owner-only actions check `isOwner`/`ownsAnyFarm`.

**No Critical findings.** What remains is one conditional Medium (worker duty mutation dies silently on insecure-origin deployments because `crypto.randomUUID` is called outside the error handler), one Medium i18n/UX gap (raw administration-route enum codes rendered to users), one consolidated Medium i18n finding (hardcoded English fragments in rendered output), and a handful of Low/Info items. Severity counts: **0 Critical, 3 Medium, 2 Low, 4 Info** (plus positives).

## Findings

### [Medium] Worker duty Complete/Skip silently no-ops on insecure origins (`crypto.randomUUID` called outside the error path)
Location: `frontend/src/app/worker/page.tsx:169` (with `frontend/src/lib/offline-queue.ts:155` sharing the pattern).
Evidence:
```ts
const idempotencyKey = crypto.randomUUID();   // line 169 — BEFORE the try block
const rollback = applyOptimisticTaskPatch(...);
setBusyId(task.id);
try {
  await apiFetch(path, { method: "POST", body, headers: { "Idempotency-Key": idempotencyKey } });
```
`crypto.randomUUID` exists only in secure contexts (HTTPS or localhost). The production edge (`docker/edge-proxy.production.conf.template`) listens on plain :3000 behind an *optional* outer TLS terminator, and the worker shell itself acknowledges `http:// IP` deployments (`worker/layout.tsx:108`). On such an origin `crypto.randomUUID` is `undefined` → a `TypeError` is thrown at line 169, *before* `applyOptimisticTaskPatch` and before the `try` — so the tap produces no optimistic state, no toast, no rollback, and an unhandled promise rejection. The codebase's own `randomIdempotencyKey` (`lib/idempotent-request.ts:322-337`) implements a `getRandomValues` fallback for exactly this environment; this call site bypasses it.
Impact: on an `http://` tablet deployment, the worker board's only two actions (Complete, Skip) silently do nothing — the primary worker journey is broken with zero feedback.
Conditional on deployment over a non-secure origin; confirmed as a code path (the throw site precedes all error handling).
Fix: use `randomIdempotencyKey()` (export it from `lib/idempotent-request.ts`) or wrap the whole mutation — key generation included — in the `try` with a surfaced error.

### [Medium] Administration-route enum codes rendered raw (no label map, no Telugu)
Location: `frontend/src/app/(app)/health/page.tsx:125-128` (`ROUTE_ITEMS`), `:1732-1737` (select options `{r}`), `:1360` (log table `{e.route ?? "—"}`).
Evidence:
```ts
export const HealthEventInRoute = { SC: 'SC', IM: 'IM', IV: 'IV', ORAL: 'ORAL', TOPICAL: 'TOPICAL', INTRANASAL: 'INTRANASAL' };
...
const ROUTE_ITEMS: Record<string, string> = { [NONE]: "—", ...Object.fromEntries(ROUTES.map((r) => [r, r])) };
...
{ROUTES.map((r) => (<SelectItem key={r} value={r}>{r}</SelectItem>))}
```
`lib/enum-labels.ts`'s contract is "Raw codes … must never reach a screen", and every sibling enum in the same dialog resolves through `enumLabel(kind, value, language)`. `route` has no `EnumKind` entry, so `INTRANASAL`/`TOPICAL` render verbatim in the closed trigger, the option list, and the event-log table — in English SCREAMING_SNAKE for a Telugu-first audience.
Impact: vet-facing select and history show untranslated machine codes; inconsistent with the app's own vocabulary rule (correctness-of-output/i18n, not data corruption).
Fix: add a `route` kind to `enum-labels.ts` (labels + `TE_LABELS`) and resolve through it in both the dialog and the log cell.

### [Medium] Hardcoded English literals in rendered output (i18n) — consolidated
Location & evidence (all in scope, all visible to Telugu users):
- `frontend/src/app/(app)/health/page.tsx:176` — `return <>batch #{event.purchase_batch_id}</>;`
- `frontend/src/app/(app)/health/page.tsx:180` — `return <span className="text-muted-foreground">bucket-wide</span>;`
- `frontend/src/app/(app)/health/page.tsx:590` — `` `${resolveTaskTitle(t, language)} (due ${formatDate(t.due_date)})` `` (also rendered again at `:1836`)
- `frontend/src/app/(app)/health/page.tsx:1618` — `` {age != null ? ` · ${age} mo` : ""} `` (bulk-preview review list)
- `frontend/src/app/(app)/animals/[id]/page.tsx:1897,1900,2020` and `frontend/src/app/(app)/animals/page.tsx:1617,1674` — `kg` unit suffixes hardcoded (`${a.latest_weight_kg.toFixed(1)} kg`)
- `frontend/src/app/(app)/animals/[id]/page.tsx:2012` — `<TableHead>BCS</TableHead>` (clinical acronym; borderline)
- `frontend/src/app/(app)/app-layout-client.tsx:377-378` — brand-link fallback label `"access status"` (used as an `aria-label` interpolation)
- `frontend/src/app/farm-select/page.tsx:263` / `frontend/src/lib/farm-vocabulary.ts:80,116` — `farmTypeLabel` ("Goat farm") is English-only by construction and renders on every farm card/switcher chip
- `frontend/src/app/worker/login/page.tsx:566` — `← {t("worker.login.title")}` (a literal glyph, contradicting the page's own "No glyph decorations in UI copy" note at line 129-130)
Impact: untranslated fragments on primary Telugu-facing surfaces; "bucket-wide"/"batch #N"/"N mo" are full sentences, not symbols. Severity per rubric: Medium (i18n correctness).
Fix: route each through catalog keys (e.g. `health.log.batchTarget`, `health.log.bucketWide`, shared `common.kg`-style unit tokens, `shell.brandLinkFallback`).

### [Low] Worker board `busyId` is a single slot — concurrent cards re-enable each other mid-flight
Location: `frontend/src/app/worker/page.tsx:130,277,292` (`busy={busyId === task.id}` for every card).
Evidence: `const [busyId, setBusyId] = useState<number | null>(null);` … `setBusyId(task.id)` at mutation start, `setBusyId(null)` in `finally`. Starting card B's mutation overwrites `busyId`, re-enabling card A's Complete/Skip while A's POST is still in flight.
Impact: tapping A again issues a second POST with a *fresh* idempotency key; the server's pending-state check turns it into a 409, which surfaces as a spurious error toast right after a success toast. No data corruption (server-guarded), but confusing double-fire feedback on the tablet surface.
Fix: `busyId: Set<number>` (or block the whole board with one pending flag while any mutation flies).

### [Low] Animals list tag sort uses host-locale `localeCompare`
Location: `frontend/src/app/(app)/animals/page.tsx:1439` — `a.tag_number.localeCompare(b.tag_number) * dir`.
Evidence: no explicit locale argument; the collation (digit handling, case) follows the device locale, so a Telugu-locale tablet and an English desktop sort the same tag set differently, and neither matches the server's recency default deterministically.
Impact: sort order is environment-dependent; minor and cosmetic, but a correctness-of-output variance on a primary list.
Fix: `localeCompare(b, "en")` (tags are `G-XXXXX` scheme) or a plain numeric-aware comparator.

### [Info] Health bulk-preview swallows non-200 success envelopes
Location: `frontend/src/app/(app)/health/page.tsx:980-981` — `const response = await previewMutation.mutateAsync(...); if (response.status !== 200) return;`.
A 201/204 (contract drift) would silently dead-end the first "Review targets" click with no feedback. Current contract is 200, so unreachable today; noting for drift-resistance.

### [Info] Worker login types the worker-login response inline
Location: `frontend/src/app/worker/login/page.tsx:106` — `apiFetch<{ access_token: string; user: {...} }>` rather than the generated model, unlike `login/page.tsx` which deliberately anchors to `LoginOut`/`TokenOut` so backend renames break `tsc`. Contract-drift risk only.

### [Info] Kidding date-window memo can go stale across midnight
Location: `frontend/src/app/(app)/kidding/page.tsx:339-352` — `latestKiddingDate` uses `farmToday()` inside a `useMemo` whose deps omit it. A dialog held open across midnight keeps the old ceiling in schema + `min`/`max` until a re-render recomputes via dep change. Practically invisible; the sibling `NewBreedingDialog` handles this explicitly (`resetField` effect) — that pattern could be reused.

### [Info] Dead condition in animals sort
Location: `frontend/src/app/(app)/animals/page.tsx:1434-1435` — `listedAnimals && sort ? ...` — `listedAnimals` (`payload?.animals ?? []`) is always a truthy array; the guard never decides anything. Cosmetic.

## Coverage manifest

| File | Status |
|---|---|
| `frontend/src/app/layout.tsx` | full |
| `frontend/src/app/page.tsx` | full |
| `frontend/src/app/error.tsx` | full |
| `frontend/src/app/global-error.tsx` | full |
| `frontend/src/app/manifest.ts` | full |
| `frontend/src/app/globals.css` | partial (skim for correctness-relevant tokens, per instructions) — status ramp, `html:lang(te)` font swap, reduced-motion all coherent |
| `frontend/src/app/healthz/route.ts` | full |
| `frontend/src/app/login/page.tsx` | full |
| `frontend/src/app/register/page.tsx` | full |
| `frontend/src/app/farm-select/page.tsx` | full |
| `frontend/src/app/(app)/layout.tsx` | full |
| `frontend/src/app/(app)/app-layout-client.tsx` | full |
| `frontend/src/app/(app)/error.tsx` | full |
| `frontend/src/app/(app)/loading.tsx` | full |
| `frontend/src/app/(app)/not-found.tsx` | full |
| `frontend/src/app/(app)/no-access/page.tsx` | full |
| `frontend/src/app/(app)/animals/page.tsx` | full (1725 lines) |
| `frontend/src/app/(app)/animals/new/page.tsx` | full |
| `frontend/src/app/(app)/animals/[id]/page.tsx` | full (2413 lines, 2 passes) |
| `frontend/src/app/(app)/breeding/page.tsx` | full (1397 lines) |
| `frontend/src/app/(app)/breeding/[id]/ultrasound/page.tsx` | full |
| `frontend/src/app/(app)/kidding/page.tsx` | full (1550 lines) |
| `frontend/src/app/(app)/kidding/new/page.tsx` | full |
| `frontend/src/app/(app)/health/page.tsx` | full (2153 lines, 2 passes) |
| `frontend/src/app/(app)/health/new/page.tsx` | full |
| `frontend/src/app/(app)/health/schedule/[animalId]/page.tsx` | full |
| `frontend/src/app/(app)/health/task-prefill.ts` | full |
| `frontend/src/app/(app)/screening/page.tsx` | full |
| `frontend/src/app/worker/layout.tsx` | full |
| `frontend/src/app/worker/login/page.tsx` | full (570 lines) |
| `frontend/src/app/worker/page.tsx` | full |

Context-only reads (not in owned scope, for data-flow verification): `lib/auth-context.tsx`, `lib/api-client.ts`, `lib/format.ts` (partial), `lib/enum-labels.ts` (partial), `lib/farm-vocabulary.ts`, `lib/use-permissions.ts` (partial), `lib/use-single-flight.ts`, `lib/farm-scope-guard.ts`, `lib/offline-queue.ts` (partial), `lib/idempotent-request.ts` (partial), `components/permission-gate.tsx`, generated models `healthEventInRoute.ts`.

Coverage: 30/31 files full, 1 partial by instruction (globals.css skim) ≈ **100% of owned scope**.

## Positive observations

1. **The animals-list URL state machine is exceptional** (`animals/page.tsx:1067-1269`): per-dispatch pending-navigation ledger keyed by sequence with URL-own `q` capture, same-URL replace suppression, newest-match consumption for A→B→C→B, out-of-range page self-healing, and a 300ms debounced search that never fights the committed URL. I tried to construct a stale-row or lost-keystroke race and could not.
2. **Write-continuation fencing everywhere**: `captureFarmScope()` + `submissionEpoch`/`useSingleFlight` on every dialog write means farm switches, dialog re-opens, and unmounts can neither toast, close, reset, nor invalidate into the wrong session — including the health dialog's deliberately-dismissible-during-write design, which still commits the toast+invalidation for a landed write but leaves the new session untouched.
3. **422 mapping is first-class**: `applyApiValidationToForm` maps FastAPI `loc` tails onto RHF fields with unmapped-issue fallthrough, and the health dialog force-opens the collapsed Advanced `<details>` when a hidden field fails (client- or server-side) — a silent-stuck-submit class of bug proactively eliminated.
4. **Deep-link lifecycle on /breeding and /kidding** is genuinely thorough: off-page records fetched through the exact endpoint, "screen busy" deferral so a resolving deep link never stacks modals over a half-filled form, stale-link explainers (not-pending / already-kidded / 404) with one-intent-scoped dismissal latches, and shims (`/kidding/new`, `/breeding/[id]/ultrasound`, `/health/new`) that preserve `returnTo` and re-dispatch on query-only navigation.
5. **Auth bootstrap resilience** (`auth-context.tsx`): transient-vs-authoritative refresh outcomes, Web-Locks cookie coordination with bounded waits, cross-tab tombstoning that distinguishes "farm revoked" from "session dead", and StrictMode-safe one-shot fencing.
6. **A11y as correctness**: `role="alert"` on every inline error, `aria-live`/`role="status"` on every async surface (including route-level loading and error boundaries resolving language without the provider), `aria-pressed` filter groups, table semantics kept on the screening rows (button-in-cell instead of `role="button"` on `<tr>`), and 44px touch floors on the worker/below-md surfaces.
7. **Pluralization is done right**: `_one`/`_many` splits with `{count}` interpolation across animals/kidding/health/worker/screening toasts (e.g. `animals.list.description_one`), and count-bearing card titles use the same pattern.
8. **Mobile parity is systematic**: every wide table has a below-md card layout with the same data and label sources, so phone users are not second-class on any audited journey.
9. **The worker shell's offline story** (queue badge, drain-on-online, rejected-drain toast, end-shift confirm that refuses to silently discard queued writes, session-scoped queue wipe on sign-out) closes the shared-tablet hygiene loop end to end.
10. **Farm-switch hygiene**: `key={farmId}` on the app shell's `<main>` remounts pages on switch, `selectFarm` cancels+clears in-flight queries before swapping `X-Farm-Id`, and URL-only cache keys are purged on revoke — no stale-cross-tenant render path found.
