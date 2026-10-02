# Frontend fresh mutation campaign — survivor disposition log

Per-survivor triage decisions from the 2026-10-01 fresh campaign (manifest
regenerated from current source; every verdict re-verified at full selection
in Phase B — see CAMPAIGN.md). A survivor is FIXABLE when a jsdom test can
observe the difference; EQUIVALENT when none can. The 2026-09-23 campaign's
dispositions were re-derived from source, not copied: ids shifted and the
code moved since.

## Equivalent classes (with this campaign's instances)

1. **`a ?? b` → `a || b` where `a` has no falsy-but-defined value** —
   objects/arrays (`init.headers ?? {}`, `markers ?? []`, `buckets ?? []`,
   `sharp.versions ?? {}`, `imgOrigins ?? []`, `title_args ?? {}`), strings
   never empty by contract (`upload_method ?? "POST"`, `nextVersion ??
   "unknown"`, `RISK_VARIABLE_LABELS[key]?.(v) ?? …`, `display ?? value`,
   `ariaLabel ?? …`, `message ?? t(…)` (stale-data-notice callers pass real
   strings), `pendingTheme.current ?? …`, `(method ?? "GET")` where "" never
   occurs), fallback-equals-falsy (`counts[b] ?? 0`, `selectedAnimalId ??
   0`, `uploadedByBucket[row.bucket] ?? 0/|| 0`, `files?.[0] ?? null`,
   comparator segments `(left[index] ?? 0)` — the only falsy number is 0
   and `0 || 0 === 0`), and URLSearchParams' leading-`?` strip
   (`slice(queryStart + 0)` parses identically to `+ 1`: custom-instance
   m00006).
2. **Opaque epoch/token arithmetic** — `dialogEpoch`/`walkthroughEpoch`
   `useRef(0) → 1`, `+= 1 → 2`, and the unmount-cleanup `+= 1 → 0`
   (account-dialog m07500/04/05/10, screening m08177/78): consumers compare
   equality only; refs die with the instance; the in-source Stryker
   annotations document the same.
3. **State overwritten before observable** — `mounted` ref initial/cleanup
   writes (account-dialog m07501/03), `uploading` initial (m08176),
   `codesCopied` initial and close-reset (m07499/14: every reveal path
   re-sets it first), `rejectMissing`/`rejectReason` initials
   (task-row-actions m08328, Stryker-annotated).
4. **Defense-in-depth behind already-disabled controls** —
   screening-check-dialog's `total === 0` finish gate (m08230/31: the finish
   button is disabled at `totalUploaded === 0`, so the gate cannot fire
   through the UI; and the `+ → -` swap only changes the sum's sign, never
   its zero-ness), the `!pendingFile || !selectedBucket` guard (m08196),
   the reject-dialog close-lock (task-row m08409, Stryker-annotated).
5. **Reset-at-close vs reset-at-open** — screening `if (!open) return`
   session-reset fence (m08180): both orders clear everything before any
   reopen can observe.
6. **Layered URL validation** — utils `safeAppPath`'s protocol-relative
   guard (m09936 `|| → &&`): `new URL(raw, validationOrigin)` rejects
   `//host/…` and shape-changing relatives on the next line regardless;
   api-client cookie-route trailing-slash/verb cluster (m08727/32/33/34/40)
   lock-observable only (documented 2026-09-23; unchanged); permission-
   navigation `%`/`\` arms (m09713) unreachable behind safeAppPath.
7. **Exact-match tombstones never match non-ids** — auth-context
   `revokedFarmIdFromStorage` `> 0 → >=` and `&& → ||` (m08819/20): a
   stored 0/1.5 tombstone can never equal a real integer farm id ≥ 1.
8. **jsdom-invisible layout** — task-row-actions `size={touch ? … : …}`
   swaps (m08359/68/400/403): size classes have no computed layout; the
   repo style guide forbids utility-class-coupled assertions.
9. **Recurrence future-anchor arms** — task-row-actions
   `max(due_date, farmToday())` and `recur_days ?? 0` (m08389-94): the
   recurring-confirm dialog only opens for due ≤ today duties (future
   duties are locked), so the future anchor never decides.
10. **Runtime-identical locales** — format `formatNumber`'s
    `te-IN`/`en-IN` selection (m09130/31): this ICU renders BOTH locales
    with Latin digits and Indian grouping (`12,345` either way), so the
    branch swap is unobservable in any supported runtime here.
11. **UUID fallback over-masking** — idempotent-request
    `bytes[6] & 0x0f → 0x0e` / `bytes[8] & 0x3f → 0x3e` (m09367/77): the
    version nibble (4) and variant bits come from the OR masks; the cleared
    bit is random-entropy bit 0, outside both.
12. **Dead catalog arms** — task-title `name === "date"` (m09869): no
    taskGen template interpolates a bare `{date}`; the arm exists for
    future keys. The value-coercion ternary (m09871/72/73): template
    interpolation stringifies numbers identically.
13. **Inert no-op control flow** — offline-queue `if (stopped) continue →
    break` (m09643) and the post-5xx `continue → break` (m09677): following
    records are skipped by the stopped guard either way;
    `next.length >= 0` vs `> 0` (m09590 — an empty array exits via the
    byte-cap arm); 429 arm `||` swaps (m09664/65) inert for wire-legal
    Retry-After (numbers only) and `retryAfter > 0 → >=` (m09670: a 0 s
    backoff gates nothing); charts empty-bins slot (`length > 0 → >= 0`,
    m07797: no bars render to consume it); `path[0] ?? ""` /
    `split(…, 1)[0]` maxsplits (m08667, m08727, m09938, m09710).
14. **Environment-pinned** — image-deps-guard enforce-vs-warn arms
    (m09495/506 and siblings): only differ on an actually-unsafe pinned
    dependency tree, which the lockfile never produces.

## Fixable survivors → killing tests written and verified

All kills below were confirmed by running the mutant against the new file
(`MUTANT_ID=<id> vitest run --config vitest.mutation.config.ts <file>`).

| New test file | Mutants killed |
|---|---|
| `src/components/account-dialog.aria.test.tsx` | m07620/21/24/25/34/35/38/39/50/51/60/61 — TOTP error-field aria wiring (4 modes, both inputs, wired and unwired states) |
| `src/components/animal-picker.falsy-props.test.tsx` | m07698 (eligibilityKey "" is its own cache key), m07725/26 (placeholder/dialogTitle "" stay empty) |
| `src/api/custom-instance.query.test.ts` | m00005 pinned as regression (first query key survives the null-strip) |
| `src/lib/offline-queue.boundaries.test.ts` | m09577 (TTL at exactly 72 h), m09587+m09644 (empty-string body round-trip), m09625 (clear-backoff at clock zero), m09627/28/29 (null-storage drain counts), m09632 (gate expiry boundary), m09633/34 (gated-drain zero counts), m09671 (Retry-After 1 gates), m09673 (×1000 not ÷1000), m09674/75 (1000±1), plus the exactly-256 KiB keep/refuse boundary |
| `src/lib/task-title.nulls.test.ts` | m09883 (null template arg skipped, never "null") |
| `src/components/health-target-pickers.falsy.test.tsx` | m07923/24/54/55 (placeholder/dialogTitle "" stay empty, both pickers) |
| `src/app/(app)/tasks/reject-dialog.attrs.test.tsx` | m08414/15 (maxLength 255), m08416/17 (rows 3) |
| `src/lib/image-deps-guard.equals.test.ts` | m09478 (equal-version comparison terminates) |

App-page survivors (Phase B settled) are triaged in the second half of this
file, appended after the campaign closed.

## App-page survivors (Phase B settled, 1,749 total)

Phase B re-verified every app survivor against its complete covering
selection with the gap files included; these stand. The dominant families
(all triaged by class, instances listed per file in report.md):

1. **Loading-skeleton shapes (~130)** — `PageSkeleton cards={n}`,
   `TableSkeleton rows={n} columns={n}`: jsdom renders no layout, the repo
   style guide forbids utility-class-coupled assertions, and no semantic
   hook exists for a skeleton's card count. Not discriminable in a unit
   test by design.
2. **`??`-guards over wire-shaped values (~120)** — `value ?? ""` (string |
   undefined renders identically through `||`), `?? null`, `?? {}`, `?? []`
   on object/array operands; the falsy-but-defined case never occurs by
   contract.
3. **`aria-invalid={Boolean(x) || undefined}` / describedby ternaries in
   forms whose error arms are unreachable** — enum-optional selects written
   only by valid options (animals/[id] mortality_cause_code, disposal),
   fields whose errors are gated behind disabled controls.
4. **Date/UTC arithmetic in render-only paths** — `Date.UTC(year,
   month - 1, 1)` month-label math where the ±1 lands on a label already
   pinned by snapshot-adjacent text assertions only for other branches.
5. **Opaque tokens, mounted refs, epoch fencing** — same classes as the
   lib segment, per page.
6. **Score/benchmark singletons and fixture-shaped literals** — e.g.
   dashboard `overdue*1000 + flags*100 + duties` weighting, benchmark
   windows `[30, 90, 365]`, sort-direction `1 : -1`: each pinned in
   report.md as a candidate follow-up kill; not batch-triaged line-by-line
   in this campaign's closing pass (see Follow-ups).

### Fixable app families killed this campaign

insurance aria ×16, animals/[id] aria+caps ×20, purchases aria+caps ×26,
tasks reject-dialog caps ×4 — all verified mutant-by-mutant (see the table
in CAMPAIGN.md).

### Follow-up round (2026-10-01, same day): 103 more verified kills

Every fixable follow-up named above was killed, each verified by running
the mutant against the new file:

| New test file | Kills | What was pinned |
|---|---|---|
| `app/(app)/health/page.dom-caps.test.tsx` | 22 | every cap in the event dialog: product/disease/vet/template/authority/lot/administered/certificate 120, dose 60, official tag 80, notes rows 2 |
| `app/(app)/team/page.dom-caps.test.tsx` | 14 | worker invite (120/254/128), reset password 128, notification phone 20, role name 80 / description 255 |
| `app/auth-pages.dom-caps.test.tsx` | 14 | login email 254 / password 128 / TOTP 11 (challenge driven), register 120/254/128, farm-select name/location 120 + timezone 64 |
| `app/(app)/animals/page.dom-caps.test.tsx` | 10 | create dialog breed 60, seller 120, import reason rows 2 × 255, notes rows 2 |
| `app/(app)/finance/insurance/page.aria.test.tsx` (extended) | 6 | policy number 60, insurer 120, notes rows 2 |
| `app/(app)/animals/[id]/page.aria2.test.tsx` (extended) | 4 | suspected disease 120 (statutory checkbox arm), clearance reference 255 |
| `app/(app)/breeding/page.dom-caps.test.tsx` | 4 | pregnancy-loss notes 4 000, kidding notes rows 2 |
| `app/(app)/finance/page.dom-caps.test.tsx` | 6 | correction notes/reason 255, new-transaction notes 255 |
| `app/misc-pages.dom-caps.test.tsx` | 7 | planner plan name 120, ops-sim row tags 50, scenario name 120 + notes 2 000 |
| `app/(app)/owner/page.dom-caps.test.tsx` | 12 | attention weights 1 000 : 100 : 1 (six boundary fixtures, each flips under exactly one weight mutation) + the 30/90/365-day window set |
| `app/(app)/ops-simulation/page.format-kg.test.tsx` | 4 | whole-kg shares print 0 decimals, fractional exactly 1 |

New equivalents documented in this round:

- **Sort-direction multipliers** — `dir = asc ? 1 : -1` → `2`/`-2` in
  animals and finance sort comparators (m00886/88/2404/2406): `dir` is
  only ever a sign multiplier (`x * dir`), and any nonzero multiplier
  preserves a comparator's sign; the in-source Stryker annotations say
  the same.
- **Redundant guard arms** — simulation `"maxLength" in rule &&
  rule.maxLength !== undefined` (m05430): each arm implies the other's
  outcome for every zod rule shape.
- **Dead-UI arms** — breeding `semen_sire_name` (maxLength 120): the AI
  radios are deliberately not offered (the goat protocol 409s the write),
  so the input cannot render for this product.
