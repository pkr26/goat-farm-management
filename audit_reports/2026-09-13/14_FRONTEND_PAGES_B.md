# Red Team Audit — Frontend Domain Pages, Part B (units P12–P22)

**Date:** 2026-09-13
**Scope:** Part P units P12–P22 per `00_RED_TEAM_AUDIT_SCOPE.md` — the second half of
domain pages plus boundary files:
`frontend/src/app/(app)/{health,kidding,ops-simulation,planner,purchases,reports,simulation,tasks,team,no-access}`,
health/kidding shims, `error.tsx`, `global-error.tsx`, `loading.tsx`, `not-found.tsx`,
`app/healthz/route.ts`, plus the supporting libs named in the brief
(`use-url-state`, `persisted-numbers`, `use-single-flight`, `farm-scope-guard`,
`idempotent-request`, `task-action-access`, `permission-navigation`, `use-permissions`,
`query-invalidation`, `health-target-pickers`, `remote-picker`, `permission-gate`,
`empty-state`, simulation `components/*`).

**Verdict up front:** no Critical, High or Medium findings. The prior hardening passes
hold up under adversarial re-reading: every URL-borne write path re-validates server-side,
every money/stock mutation the pages issue is single-flighted and (where the contract
allows) idempotency-keyed, every `returnTo`/`action_url` goes through
`safeAppPath` + permission checks, and worker passwords never touch sessionStorage.
Six Low/Info findings are documented below with exact evidence; none is exploitable
beyond hardening/consistency gaps.

## Methodology

1. Full read of every in-scope file (simulation/page.tsx 3601L read in four chunks),
   not grep-only, to trace state machines (dialog open/submit cycles, epoch/fence
   counters, single-flight guards) end to end.
2. Per-unit adversarial attacks as briefed: deep-link hydration → auto-submit?
   bulk-preview snapshot forgery/staleness? two-step confirm bypass by URL state?
   wrong-record writes on query-only re-navigation? password persistence/log paths?
   withheld-sentinel fail-open? awaiting-tab probing? encoding tricks on
   backend-supplied action URLs?
3. Cross-cutting greps over `app/ lib/ components/ hooks/`:
   `dangerouslySetInnerHTML`, `window.confirm`, `alert(`, `target=_blank`,
   `document.cookie`, `location.href =`, `location.replace/assign`, `window.open`,
   `URL.createObjectURL`, `download=`, `form action=`, `router.push/replace` with
   interpolated values. Results in §"Cross-cutting sweep".
4. Colocated tests consulted selectively to confirm intent (e.g. team page Esc-key
   tests at `page.extended.test.tsx:452+` never pair Escape with a typed password —
   the gap behind finding RT-P2-1; simulation line 937 comment confirms the
   window.confirm→dialog migration).

## Findings

| ID | Severity | Unit | Title |
|----|----------|------|-------|
| RT-P2-1 | Low | P21 | Typed worker password survives Esc/backdrop dismissal of the Add-worker dialog (only the Cancel button clears it) |
| RT-P2-2 | Low | P18 | `kids_per_kidding` omits the API sentinel from its withheld check — fails toward "—" instead of "Requires breeding access" in the stale-permission window |
| RT-P2-3 | Info | P17 | `?batch=` deep-link id parsed with permissive `Number()` (accepts `1e2`, `0x64`) unlike the strict `/^\d+$/` grammar used elsewhere |
| RT-P2-4 | Info | P20 | `tasks.create` holders without `team.manage` cannot assign duties in the UI (role/worker directory only exists behind `team.manage`) — deliberate, leak-free, but a delegated-creator feature gap |
| RT-P2-5 | Info | P19 | `onDeleteScenario` error path toasts without the farm-scope fence every sibling handler applies |
| RT-P2-6 | Info | P16 | Planner "Update" button enabled with an empty plan name (guarded only at submit); plan-basis card uses a raw `<a href="/simulation">` instead of a Next link |

---

### RT-P2-1 — Low — P21 team — password survives Esc dismissal

**Evidence:** `frontend/src/app/(app)/team/page.tsx`
- Dialog dismissal handler, lines 499–505:
  ```tsx
  onOpenChange={(nextOpen) => {
    if (!nextOpen && (isSubmitting || createFlight.pending)) return;
    if (!nextOpen) setFormError(null);
    onOpenChange(nextOpen);
  }}
  ```
- Only the Cancel button resets the form, lines 610–620, with the intent comment:
  `// Cancel must also clear the form: the dialog stays mounted
  // for owners, and a half-typed password should not survive
  // into the next open.` → `reset(); onOpenChange(false);`
- The dialog stays mounted for owners (`{isOwner && <AddWorkerDialog open={workerOpen} …/>}`,
  lines 1429–1436), so RHF form state persists across close/reopen.

**Exploit sketch:** owner types a worker password (min 12 chars), presses Esc (or clicks
the backdrop) instead of Cancel, then walks away; the next "Add worker" click reopens the
dialog with the previous password still populated in the (masked) field — viewable via
browser password-reveal extensions or devtools, and present in JS memory indefinitely.
The page's own documented contract ("should not survive into the next open") is only
enforced on the Cancel path.

**Impact:** sensitive-data handling gap on the exact surface the brief flags. In-memory
only: never written to sessionStorage (`lib/idempotent-request.ts:235-237` —
`allowsPersistedRecovery` excludes `/api/team/workers`, verified), never logged, never
rendered in clear. Requires the owner's own authenticated session; no cross-user exposure.

**Fix:** call `reset()` in the `!nextOpen` branch of the dialog's `onOpenChange` (after
the in-flight guard), or key the dialog by an open-counter so each open remounts a blank
form. Add a regression test pairing Escape with a typed password (the existing
`page.extended.test.tsx` Escape tests never do).

---

### RT-P2-2 — Low — P18 reports — incomplete withheld-OR on one row

**Evidence:** `frontend/src/app/(app)/reports/page.tsx`
- Correct fail-closed pattern, lines 134–135:
  ```tsx
  const healthWithheld = mortality.total_deaths === null || !canViewHealth;
  const breedingWithheld = breeding.cull_candidates_total === null || !canViewBreeding;
  ```
- The inconsistent row, lines 230–237:
  ```tsx
  <SummaryRow
    label={`Alive ${vocabulary.youngPlural} per ${vocabulary.parturition}`}
    value={
      canViewBreeding
        ? (breeding.kids_per_kidding ?? "—")
        : <Withheld permission="breeding" />
    }
  />
  ```

**Exploit sketch:** user's breeding.view is revoked server-side; the reports payload
refetches and nulls `kids_per_kidding`, but `/api/auth/permissions` (a different query
that `invalidateFarmData` deliberately excludes — `lib/query-invalidation.ts:14-26`) is
still cached with `breeding.view` present for its staleTime window. `canViewBreeding`
is true, so the row renders "—" — the glyph the page defines as "not enough data"
(`pct()`, lines 36–40) — for a value that is actually withheld.

**Impact:** display-semantics only. No number leaks (null renders as "—", never 0 and
never the withheld value); the mislabelled reason persists only for the permission
query's staleTime. Every value-bearing sibling (conception/first-cycle/twin rates,
cull counts, deaths, stillborn, monthly table) uses the correct `sentinel || !can` OR.

**Fix:** change the ternary to `!breedingWithheld ? (breeding.kids_per_kidding ?? "—") : <Withheld …/>`
(or add a dedicated `kids_per_kidding === null` arm OR-ed with `!canViewBreeding`).

---

### RT-P2-3 — Info — P17 purchases — permissive `?batch=` id parsing

**Evidence:** `frontend/src/app/(app)/purchases/page.tsx:310-315` (mount seed) and
`:336-339` (adoption effect):
```tsx
const raw = getUrl("batch");
const parsed = raw === null ? Number.NaN : Number(raw);
return Number.isInteger(parsed) && parsed >= 1 ? parsed : null;
```
`Number("1e2")`, `Number("0x64")` and `Number(" 100 ")` all resolve to 100, so
`/purchases?batch=1e2` opens batch #100. Contrast the health page's strict
`positiveIdString` (`/^\d+$/` + safe-integer, health/page.tsx:133-137) and the tasks
page's offset grammar (`^(0|[1-9]\d*)$`, tasks/page.tsx:147).

**Impact:** none beyond URL-grammar looseness — the id is only used for a farm-scoped
`GET /api/purchases/{id}` whose authz is server-side, and any resolved id identifies a
batch the farm can already see. The offset param in the same page uses the clamped
`getNumber` (use-url-state.ts:50-64), so no 422 path exists either.

**Fix (hardening):** reuse a strict `parsePositiveId` helper for `batch`.

---

### RT-P2-4 — Info — P20 tasks — delegated duty-creators cannot assign

**Evidence:** `frontend/src/app/(app)/tasks/page.tsx`
- `canSeeTeam = can("team.manage")` (line 1007) gates both `/api/team` consumers:
  the create-dialog directory (line 1128–1130, `enabled: canCreate && canSeeTeam && open`)
  and the completed-tab name map (line 1138–1140).
- Without `team.manage` the create dialog renders the literal note
  `tasks.form.noTeamAccess` (lines 1619–1623) and offers no role/worker selects; both
  assignment fields default to `NONE` → submitted as `null` (lines 1196–1197, 1221–1222).

**Answer to the brief's question (no 403, no leak, no workaround):** the page never
fires `/api/team` for a `tasks.create`-without-`team.manage` holder — the query is
disabled at source, so there is no 403 handling path and no fallback scrape. The
design comment (lines 1200–1204) documents that blocking submit on a failing team
fetch would be worse. The completed tab degrades to timestamp-only, never a raw user id
(`resolveMemberName` gated at line 1403; the map lookup itself returns `?? null`,
line 1150–1151). Server-side G3 still accepts assignment payloads from such a user via
the API — the UI simply offers no way to build one.

**Impact:** functional limitation, not a vulnerability: role/worker assignment from the
duty form is effectively restricted to `team.manage` holders even though
`tasks.create` is an independently grantable action. Worth a product decision (a
least-privilege "assignable directory" endpoint, or auto-assignment to the creator's
role).

---

### RT-P2-5 — Info — P19 simulation — unfenced error toast on delete

**Evidence:** `frontend/src/app/(app)/simulation/page.tsx:1448-1450`:
```tsx
} catch (err) {
  toast.error(errorMessage(err, "Could not delete the scenario."));
}
```
Every sibling continuation checks `farmScope()` in its catch (e.g. `onRun` lines
1377–1379, `onSaveScenario` 1508–1510, `onUpdateScenario` 1539–1540, planner/team
equivalents). A farm switch during a failing DELETE surfaces the error toast in the new
farm's UI. The success path is fenced (line 1422), and the write itself carries its
captured `X-Farm-Id`. Toast-only impact.

**Fix:** add `if (!farmScope()) return;` to the catch.

---

### RT-P2-6 — Info — P16 planner — two nits

**Evidence:** `frontend/src/app/(app)/planner/page.tsx`
- Update button disabled-condition (lines 683–688) omits `!planName.trim()` while the
  Save button includes it (line 672). Submitting with an empty name is caught at
  `onUpdatePlan` lines 540–544 with a toast — no silent write, just an enabled-then-
  rejected button.
- Line 906: `<a className="underline" href="/simulation">Simulation</a>` — a raw anchor
  (full document reload) inside an App-Router page; same-origin fixed literal path, so
  purely a consistency/performance nit, not a navigation-surface risk.

**Fix:** mirror the Save disabled-condition; switch to `next/link`.

---

## Cross-cutting sweep (all P12–P22 files + shared libs)

- `dangerouslySetInnerHTML`: **zero occurrences** in the app tree.
- `window.confirm` / `alert(`: **zero live occurrences** — only a comment at
  simulation/page.tsx:937 documenting its replacement by the staged pending-delete
  dialog (which is the sole caller of `deleteMutation`, line 3537 — no bypass path).
- `target="_blank"`: **zero occurrences**.
- `document.cookie`: only `components/ui/sidebar.tsx:103` — non-sensitive sidebar
  open/close UI state, SameSite=Lax, path-scoped.
- `location.href =` / `location.replace` / `location.assign` / `window.open`: **zero
  occurrences**; all navigation goes through `next/navigation`, and every URL-borne
  destination is validated (`permittedAppPath` → `safeAppPath` → canonicalization
  re-check, permission-navigation.ts:85-113 + utils.ts — rejects `//`, schemes,
  backslashes, control chars, `%`-encoded pathnames, dot-segment drift).
- `URL.createObjectURL`: two uses, both safe — ops-simulation ledger
  (ops-simulation/page.tsx:445-456, revoked immediately after click; filename built
  only from API-supplied `start_date`/`seed`, content is API markdown rendered in a
  `<pre>` at line 1062) and `components/account-dialog.tsx:130-139` (revoked; out of
  Part-P scope, re-verified safe).
- `download=` attribute / `form action=`: **zero occurrences**.
- `router.push/replace` with interpolations: health returnTo (789, 1028 — validated),
  `/health/schedule/${scheduleAnimalId}` (1169 — strict-parsed digits via
  `positiveIdString`), tasks URLs built by `taskListUrl` from validated tab/offset
  grammar. No open redirect anywhere.
- Password surfaces: only P21 — both dialogs use `type="password"`,
  `autoComplete="new-password"`, `maxLength 128`, zod min 12 (team/page.tsx:90-107,
  567-576, 710-720). `/api/team/workers` excluded from sessionStorage idempotency
  persistence (idempotent-request.ts:227-237); the reset-password route is not in the
  idempotency allowlist at all (memory-only). No password reaches a toast, an error
  banner, a log call, or component state outside RHF.

## Per-unit attacked-&-held notes

### P12 — `/health` (page.tsx, 2063L)
- **Deep links hydrate, never auto-submit.** The hydration effect (796–832) only runs
  `resetEventForm(); setOpen(true); setValue(...)` — `submitEvent` is reachable solely
  from the form's submit handler. Signature latch `deepLinkHydratedRef` (611, 806–812)
  keys on the `task_id|animal_id|purchase_batch_id` string: a changed link re-hydrates
  (new signature), a permission-query rerender cannot (same signature), and clearing
  all params resets the latch to null (801–803).
- **Bulk preview fail-closed two-step.** First submit with non-animal scope and no
  matching preview runs `POST /api/health/events/preview` and returns without writing
  (887–951). The match key (894–900) is scope + task_id + bucket/batch; every retarget
  path clears `bulkPreview` first (changeScope 763, bucket select 1459, both pickers
  via `clearLinkedTaskPrefill` 740, task apply 694). The write sends
  `expected_animal_ids` from the reviewed snapshot (952, 997) and the server rejects
  drift (F4 409) — a stale snapshot cannot submit silently; the animals that join
  later are deliberately excluded (reviewed-set semantics, stated in the UI copy
  1516–1521). Preview continuation is triple-fenced (epoch + farm-scope + live
  field re-comparison, 921–938).
- **In-flight dismissal.** The dialog is deliberately closable mid-write; the success
  path toasts + invalidates BEFORE the epoch check (1009–1017), so the write is always
  surfaced, while a reopened dialog is never closed/reset by the late continuation
  (1022–1029). `submissionEpoch` bumps on unmount (632), session reset (665) and each
  submit (916, 1001).
- **Task linking.** Local select offers only duties from the tab response (object-level
  scoped) plus the exact deep-link id resolved through `GET /api/tasks/{id}` (uniform
  404); the deep-linked duty is kept only while PENDING + VACCINE/DEWORMING (527–540),
  not-due duties warn inline (1719–1730), and the server (F5) enforces
  scope/type/target matching anyway.
- **Advanced `<details>` error reveal** is pure UI state (685–689, 1054–1060); offset
  self-heal re-homes past-the-end offsets (482–498); `positiveIdString` blocks `1e2`
  style ids everywhere; purchase-batch picker resolves `#id` via the exact-id
  anti-probe endpoint (health-target-pickers.tsx:150-171).

### P13 — shims + schedule + task-prefill
- `health/new` and `kidding/new` redirect to fixed `/health` / `/kidding` with the raw
  query re-appended (new/page.tsx:19, 26) — no host/path component can enter, so no
  open redirect; the kidding shim keys its effect on the query string so a second
  deep link before commit is honored (comment 15–21).
- Schedule page guards invalid ids before any fetch (`/^\d+$/` + safe integer, 76–79);
  its Add-event CTA layers `returnTo` (schedule → /health/new → /health), and every hop
  re-validates via `permittedAppPath` when read — an injected `returnTo` to an
  unpermitted or non-canonical path degrades to null → default back-reference (85–87).
- `taskPrefill` regexes only ever prefill two text fields from a server-owned title;
  parse failure yields `{}` (task-prefill.ts:20-26). Harmless.

### P14 — `/kidding` + `/kidding/new`
- Litter array: min 1 enforced by schema (146–149) and remove-button disable (582);
  species cap mirrored via `vocabulary.facts.maxLitterSize` in schema (153–157) and
  add-button disable (457). Per-kid weight band from the farm vocabulary (109–119);
  mortality date required only for DIED, cleared when status flips (552–559) and
  nulled in the payload for non-DIED kids (334–335).
- Date window `kiddingSchemaFor(earliest, latest)` (200–220) — earliest = max(gestation
  floor, ultrasound date), latest = min(gestation ceiling, farm today) (275–288);
  a server/client mismatch maps to the banner via `errorText` (443–447), no field-level
  422 mapping (acceptable: client schema is the stricter mirror).
- `?breeding_id`: per-intent dismiss latch (698, 739–745, 758–767); stale
  non-confirmed/already-kidded link renders a notice + "Clear link" (857–873); the
  dialog is keyed `key={activeRecord.id}` (1089) so a query-only re-navigation to a
  different breeding remounts a clean form (wrong-doe guard); dismissal of a
  hand-opened row does not retire the deep link (1091–1099).
- Write path: single-flight, close-locked while submitting (355), kid rows frozen
  mid-submit (444–448, comment documents the dropped-row hazard), farm-scope fenced,
  `POST /api/kidding` idempotency-protected (idempotent-request.ts:215-216).

### P15 — `/ops-simulation`
- Row validation is complete client-side before any request (370–394): tag required,
  duplicate tags, ≥1 row, ≤500 head, age/days/bred bounds; the run re-checks horizon
  bounds (407–410). Unbounded UI row-adding is memory-only — the run is blocked at 500
  and the server re-validates herd coherence (L7).
- Ledger download (445–456): Blob → objectURL → click → immediate `revokeObjectURL`;
  filename from API `start_date`/`seed` only; content rendered in a `<pre>` (React
  escaping). Seed/horizon commit only finite numbers (NumberField 314–334).

### P16 — `/planner`
- Target month must be strictly after the start month and within the 240-month ceiling
  (396–410); count 1–100,000; ≤50 targets (706). Opening a saved plan never re-runs it
  (612–635 — toast says "press Plan"); update 409 reloads the fresh revision and
  instructs a re-press, no silent overwrite (567–576); delete is a staged confirm
  dialog (1212–1254); the anchor re-bake stamps `meta.start_year_month` on every
  run/save/update (422–429); "Use my herd" gated on `animals.view`, calibration on the
  5-permission conjunction (233–240, 874–897). See RT-P2-6 for the two nits.

### P17 — `/purchases`
- Two-step consequence confirm is pure component state: `pendingBatch` is settable only
  by a schema-valid form submit (`onReview` 404–406) and consumed only by the Confirm
  button (694–700); no URL input can open the create dialog (`open` set only in
  `openNewBatch`, 481–493, which refuses while a flight is pending). The
  irreversibility warning is `role="alert"` copy (682–684).
- Open/submit cycle fence: `createAttempt` bumped on dismissal (640) and on each
  attempt (412); the success path toasts + invalidates BEFORE the attempt check, so a
  late success after dismissal is still surfaced but cannot close/reset a reopened
  dialog (429–444). `POST /api/purchases/new` idempotency-protected (allowlist 204).
- `?batch=` detail state: write-through with `lastWrittenParamsRef` adoption fence
  (321–352) — external URL changes adopt, own writes don't re-adopt; the batch id is
  only ever a farm-scoped GET key. See RT-P2-3 for the parsing nit.

### P18 — `/reports`
- Withheld semantics audited row by row: deaths/stillborn/rates/monthly-table all OR
  the null sentinel with the permission (134–135, 301–334); clinical outcome rows are
  re-added as withheld when absent under `healthWithheld` (191–200); cull total null →
  "Requires breeding access", never 0 (246–249). The single inconsistency is RT-P2-2.

### P19 — `/simulation` (3601L)
- **No auto-run effect anywhere**: runs execute only inside click handlers (`onRun`
  1354–1384, `onRunScenario` 1386–1414); every `useEffect` touches editor/pager/params
  state only (defaults adoption 852–866, offset adoption 822–828, pager self-heal
  906–919). No URL param can trigger a run — the only URL state is the `scenarios`
  pagination offset (807–836); `compare` ids are pure client state (no `?compare=`
  reader exists).
- Scenario CRUD: name ≤120, notes ≤2000 (3468–3485); delete goes exclusively through
  the staged `pendingDelete` dialog (937–939, 3501–3544 — confirm button is the only
  `onDeleteScenario` caller); pager self-heals after delete both reactively (906–919)
  and inline (1438–1446).
- Compare: ≤5 cap enforced in the checkbox handler (3327–3345), in `selectedUsableIds`
  (901–904) and at the button (2513–2517); stale comparisons cleared on any selection
  change, page turn and `invalidateScenarios` (948, 955, 3332); server re-checks tenant
  scope/validity (L5).
- Run-option gating: `runOptionsFingerprint` participates in `resultIsStale`
  (1150, 1167–1170) so toggling MC/sensitivity/optimization dims and banners the old
  figures; scenario runs are measured against the saved scenario's own fingerprint
  (`liveFingerprint` 1158–1165), so a dirty editor cannot produce a permanent staleness
  banner nor silently mislabel a result.
- Editor integrity: `NumberInput`/`NumberArrayInput` commit only finite, in-bounds
  numbers and feed a `invalidFields` set that hard-disables Run/Save/Update
  (number-inputs.tsx:91-115, page 1146–1147, 2532, 2541–2546, 2557); cross-field rules
  (loan+subsidy ≤1, moratorium<term, fodder stock ≤ capacity, weight-curve monotone,
  adult ≥ yearling, risk brackets, festival months ≤ horizon, 500-event cap) all
  inline-block (1033–1145); events month-beyond-horizon blocked by `validateEvents`
  (555–578). Loader epochs (`editorEpochRef`/`editorContentEpochRef`) prevent stale
  defaults/calibration/snapshot/409-recovery completions from clobbering newer editor
  intent (753–765, 1266–1275, 1324–1332, 1550–1559). API data flows only into text/
  tables/`<a href="#fragment">` with a hardcoded fragment list (2583–2598). See
  RT-P2-5 for the delete-catch nit.

### P20 — `/tasks` (1681L)
- URL grammar: offsets `^(0|[1-9]\d*)$` ≤1,000,000 (145–150); malformed offsets and
  shrunk-bucket offsets are canonicalized through a single `router.replace` with a
  dedupe ref + navigation override bridge (1066–1113); a deep-linked `?tab=awaiting`
  without `tasks.verify` falls back visually to "today" AND rewrites the URL
  (1118–1126, 1345) — no verifier-queue probing.
- Actions: one shared single-flight per row covers complete/verify/skip/reject
  (243, per-action `run` wrappers); every mutation is farm-scope fenced and invalidates
  farm data. The recurring-complete flow requires the confirm dialog
  (`needsRecurringConfirm` 250, 400–402) whose confirm button is disabled while the
  flight is pending and whose close is blocked mid-flight (485–488) — double-confirm
  cannot double-complete (single-flight ref fires first, 266).
- Gating mirrors: `taskFormNotDueYet` replaces the Open-form link with a "not due"
  note (364–367); `taskSkipUnavailable` removes Skip for quarantine/WEANING/
  animal-linked BUCKET_MOVE duties (412); `permittedTaskActionPath` rejects
  percent-encoded traversal (`%(2e|2f|5c)` — task-action-access.ts:61) and gates each
  module by its manage permission, failing closed on unknown paths; self-verification
  is pre-empted for non-owners (775, 905). Team pickers: see RT-P2-4.

### P21 — `/team` (1487L)
- Passwords: see RT-P2-1 for the one gap; everything else held — masked inputs,
  `new-password` autocomplete, min 12/max 128 client + server, reset dialog unmounts
  on close (`{resetTarget && …}` 1437–1453) so its form state dies, cancel clears via
  unmount, and no password is echoed into errors/toasts/logs/storage.
- Permission matrix: `PERMISSION_DEPENDENCIES` auto-adds grantable view deps on check
  (799–811) and repairs action-only roles on open (777–789); unchecking a view
  permission still required by a selected action is disabled (929–932);
  `roleWithinCeiling` (139–148) blocks non-owners from offering team.manage-bearing or
  above-own-ceiling roles, mirrored in worker reassignment (`assignableRoles` 196),
  AddWorker roles (1247–1249), protected-target rows (1257–1261) and role-card actions
  (1032–1041) — server B8/B11 remain the enforcement; a mirror mismatch yields
  client-side dead clicks, never an escalation.
- Row ownership: one `actionLock` ref per row spanning role/status/reset with a
  painted `rowBusy` union (179–195); the reset flow claims the row and hands the
  release callback to the parent (341–375, 1363–1368, 1437–1452).
- Authority TOCTOU: `authority.canStart()` reads the query-cache state synchronously
  (1213–1216) on top of the painted `blocked` flag, so an invalidate-started refetch
  freezes actions within the same tick.
- Own-permission refresh: `useInvalidateTeam` invalidates `/api/auth/permissions`
  explicitly (76–88), closing the `invalidateFarmData` exclusion. Role 409 →
  invalidation + dialog remount keyed by fresh `id:revision` (838–851, 1454–1468) — no
  stale-revision retry loop, no silent overwrite of a concurrent admin.
- Deactivate is confirm-gated for active workers (229–234, 378–413); preset/occupied
  roles cannot reach the delete mutation (1037–1044 + staged confirm 1127–1157).

### P22 — boundaries
- `error.tsx` / `global-error.tsx`: both render fixed copy only — no `error.message`,
  no stack, no digest reaches the UI (error.tsx:28-30, global-error.tsx:34-36); each
  logs once via `useEffect([error])` to console only; global-error owns
  `<html>/<body>` per the Next contract (global-error.tsx:28-42). `reset` offered in
  both. No rethrow (correct: these ARE the terminal handlers).
- `app/healthz/route.ts`: static `"ok"`, `force-dynamic` + `Cache-Control: no-store`,
  zero backend/auth dependency (13–20) — a liveness probe that cannot lie about a dead
  process and shadows the proxied backend `/healthz` deliberately.
- `not-found.tsx` resolves a permitted landing (`firstPermittedPath`, falling back to
  `/farm-select` while loading/erroring — 12–15); `no-access/page.tsx` is a static
  zero-permission landing with a single farm-select CTA; `(app)/loading.tsx` is a
  skeleton.

## Unconfirmed / not pursued
- None of the brief's hypothesized High-risk shapes (deep-link auto-submit, silent
  stale bulk snapshot, URL-state confirm bypass, tasks-team 403 breakage,
  `?compare=` URL injection, auto-run effect) exists in the current code — each was
  specifically traced and disproven at the cited lines rather than assumed.
- P18's `Withheld` wording ("Requires health/breeding access") uses short module names
  rather than full permission codes; cosmetic only, not pursued further.
