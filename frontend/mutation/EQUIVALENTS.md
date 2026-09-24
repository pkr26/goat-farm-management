# Frontend mutation campaign — survivor disposition log

Per-survivor triage decisions from the 2026-09-23 deep-mutation campaign.
Updated as files complete; the final report (report.md) summarises counts.

## Fix policy

A survivor is FIXABLE when a test can observe the behavioural difference in
jsdom. It is EQUIVALENT when no test can ever see a difference. Classes seen
so far, with the canonical example of each:

## Equivalent-mutant classes (no action possible)

1. **`a ?? b` → `a || b` where `a`'s type has no falsy-but-defined value**
   (operand is a non-nullable number/boolean, an array/object, or a string
   that is never `""` by contract). Examples:
   - `account-dialog` m07495-501 `name ?? email` (name is `""`-free by
     contract — see Phase C note), m07496/m07500/m07501.
   - `screening-check-dialog` m08124 `(counts[b] ?? 0)` (fallback equals the
     only falsy value), m08150 `buckets ?? []`, m08151 `counts[b] ?? 0`,
     m08154 `files?.[0] ?? null`.
   - `charts` m07730 `markers ?? []`, m07744 `display ?? value` (display is
     never `""` from the formatter), m07745 `ariaLabel ?? …` (callers pass
     real strings; `""` never occurs).
   - `animals/[id]` m00019 `BUCKET_REQUIRED_SEX[bucket] ?? sex` (values are
     non-empty "female"/"male").
   - `animal-picker` m07607 `selectedAnimalId ?? 0` (number|null; the
     fallback equals every falsy value's replacement).
2. **Opaque counter/token mutations** — values compared only for equality or
   used as React keys; any distinct value behaves identically:
   - `account-dialog` m07414/18/19/24 (dialogEpoch), m07483-analogues.
   - `screening-check-dialog` m08083/84 (walkthroughEpoch).
   - `simulation/page` m05509 (editorVersion), m06157 (`tabIndex={-1}` → -2
     is equally out of the tab order).
3. **State overwritten before observable** — initial values a mount effect
   immediately replaces:
   - `account-dialog` m07415/17 (mounted ref), m07413 (codesCopied initial —
     every reveal path re-sets it first).
   - `screening-check-dialog` m08082 (uploading initial — the open effect
     resets it).
4. **Reset-at-close vs reset-at-open** — `screening-check-dialog` m08086
   (`if (!open) return` fence): the mutant resets session state while the
   dialog is closed instead of when it reopens; both clear everything before
   any reopen can observe.
5. **Defense-in-depth guards unreachable through the UI** (button already
   disabled / flow already gated):
   - `account-dialog` m07428 (codesCopied reset in close()).
   - `screening-check-dialog` m08102 (`!pendingFile || !selectedBucket` guard
     behind a disabled button), m08136/37 (zero-photo finish branch behind a
     disabled button), m08118 (`upload_method ?? "POST"` — `""` would poison
     the original too; the backend always sends "POST").
   - `task-row-actions` m08289-94: the recurring-confirm dialog only opens
     for due ≤ today duties (future duties are locked), so the future-anchor
     arm of `max(due, today)` and its `recur_days ?? 0` fallback never decide
     anything.
6. **Dead arms for wire-legal inputs**:
   - `animals/[id]` m00021 `v === "" || v === null || v === undefined`
     (second `||`): react-hook-form always delivers `""` for empty text
     inputs, never null/undefined, so the null arm never runs.
7. **Documented Stryker equivalents** — the tree carries `// Stryker disable`
   annotations from a previous campaign marking the same sites
   (e.g. `account-dialog` mounted-ref/epoch cluster, `app-layout-client`
   m00973 loading guard, `task-row-actions` m08229/259/268/300).
8. **jsdom-invisible layout differences** — `task-row-actions` m08259/268/300
   (`size={touch ? "default" : "sm"}` swaps): button size classes have no
   computed layout in jsdom, and the repo style guide forbids new
   utility-class-coupled assertions (semantic hooks preferred; none exists
   for button size).

## Fixable survivors → tests written (killed on re-verification)

- `account-dialog.tsx` — 61 of 66 killed (zod min/max boundaries at 1/12/128,
  DOM maxLength caps incl. delete-account + TOTP inputs, TOTP epoch fences on
  both fresh and stale paths, busy labels/wiring for enroll/activate/disable/
  regen, ApiError-vs-network error text, recovery-copy label cycle, alert
  absence per mode). Remaining: classes 1/3/7 + the Phase C name??email bug.
- `screening-check-dialog.tsx` — 53 of 57 killed (exact 25 MiB ceiling and
  1-byte floor, per-bucket/total counts, .png/.jpg file names, default POST
  + exact 60 s abort budget, uploads-leg 409 vs other, stale fences,
  finish flow 200-gate/toast/close, busy lockout, reopen reset, grid-card
  zero counts). Remaining: classes 2/4/5.
- `charts.tsx` — 15 of 17 killed (Donut total>0 gate at exactly 1, default
  size 148, maxCount floor 1, all-zero NaN guard, 2px bar floor, marker
  stroke styling/titles, bar opacity). Remaining: classes 1 + m07700
  (zero-bin slot unused).
- `breeding-candidate-picker.tsx` — 3 of 3 killed (cull suffix does-only,
  60-char search cap).
- `health-target-pickers.tsx` — 2 of 2 killed (60-char cap on the health
  animal search).
- `task-row-actions.tsx` — 2 of 16 killed (255-char skip reason). Remainder
  are classes 5/7/8.
- `custom-instance.ts` — m00006 is a capped-sample false survivor (the
  dedicated test pins the stripped URL); Phase B re-verification settles it.
  m00002/03 documented equivalents (Stryker comment).
- `breeding/page.tsx` m01297 — loading-skeleton card count: cosmetic,
  attempted, reverted (the fallback DOM in the gated test contains other
  busy regions; not discriminable without class-coupled assertions).

## Phase C (source fixes surfaced by mutation testing)

1. `account-dialog.tsx` `name ?? email` (4 mutants): with an empty-string
   name the trigger/avatar render blank instead of falling back to email.
   Decide `name || email` (behavioral fix, kills the class) — needs a
   manifest regen for that file afterwards.
2. `account-dialog.tsx` TOTP `finally` epoch fence: a stale settle after
   close leaves `totpBusy` stuck true — every TOTP button in the reopened
   dialog stays disabled until remount. Clearing busy unconditionally in
   `finally` (only result/error writes need the fence) fixes the lock-up;
   the fence mutants then disappear with the code.

## Round 2 dispositions (as later files completed)

- `api-client.ts` — killed 66 more via `src/lib/api-client.boundary.test.ts`
  (payload guard arms incl. numeric/empty/whitespace tokens, actor-scope
  preservation arms, auth-failure registration stack incl. `.at(-1)` and
  double-unregister, 499/500 transient boundary, single-flight dedup +
  release, exact 10 s refresh abort, 60 s / 300 s request budgets, root-path
  allowlist incl. traversal, ApiError.code extraction, 62 s cookie-lock
  wait). Remaining equivalents: epoch inits, `??`-class, split maxsplit,
  m08480 (a `length = 1` reset leaves only a sparse-undefined slot that every
  consumer's optional chaining absorbs), m08571 (redundant `&&` arm — the
  pathname-equality check catches every path it would), m08537-adjacent
  extractErrorCode arms now killed, cookie-route trailing-slash/verb-default
  routing (m08602-633 cluster) documented as lock-observable-only.
- `auth-context.tsx` — killed 7 (bootstrap retry budget exactly 3 incl.
  recovery on the third attempt, stored-farm-id strict parse incl. the
  zero/single-farm auto-select interplay, tombstone `revoked:0` no-op).
  Remaining are Stryker-documented generation refs / mounted flags; m08711/12
  equivalent because a non-positive tombstone id can never equal a real farm
  id (the revocation only acts on an exact match).
- `csp.ts` — killed 36 of 39 (port window 1–65535 exact, credentials).
  m08901 equivalent (WHATWG URL rejects >65535 ports before the check);
  m08908/09 `??`-class.
- `offline-queue.ts` — killed 111 of 135 (per-field wellFormed battery,
  rewrite-cleans-corruption, exact 256 KiB read AND write ceiling, blocked
  storage, failure classification 399/400/499/500 edges, drain drop/keep
  edges, foreign-skip and 409/4xx multi-record continue paths). Remaining:
  `??`-class, NULL_STORAGE.length, no-op extra loop iteration.
- `image-deps-guard.ts` — killed the comparator boundaries (first segment
  decides, missing segments zero, non-numeric zero). m09391/92 are
  environment-pinned: enforce-vs-warn only differs on an actually-unsafe
  dependency tree, which the pinned deps never produce.
- `task-title.ts` — killed 54 of 60 (all twelve English months incl. Telugu
  renderings, the 1–12 range gate at both edges, *_date formatting vs raw,
  null-arg skip with the null FIRST so a `break` mutant swallows the tag).
- `permission-navigation.ts` — all 6 documented equivalents (Stryker
  comments; the % and \\ arms are unreachable behind safeAppPath).
- `task-row-actions.tsx` recurrence cluster — equivalent: the confirm dialog
  only opens for due ≤ today duties (future duties are locked), so the
  future anchor arm never decides.
- `animals/[id]/page.tsx` m00021 — equivalent: react-hook-form always
  delivers "" for empty inputs, never null/undefined, so the null arm of
  optNum's coercion never runs.
- `breeding/page.tsx` m01297 — cosmetic loading-skeleton card count, not
  discriminable without utility-class-coupled assertions; left unpinned.
