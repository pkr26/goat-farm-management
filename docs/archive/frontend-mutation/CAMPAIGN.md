# Frontend Mutation Testing Campaign — 2026-10-01

> Historical investigation, retained for context. Its measurements are
> untrusted under the current harness; see [the current workflow](../../../frontend/mutation/README.md) for the
> current workflow. File paths and commands below describe that checkout.

Deep mutation testing of the Next.js frontend (`frontend/src`), run
completely fresh: prior artifacts deleted, manifest regenerated from current
source, coverage map rebuilt file-by-file (293 test files, zero failures),
every one of the 9,944 mutants executed with escalating test selections, a
fix cycle that added 11 new test files (35 tests, 93 verified mutant kills),
and Phase B full-selection re-verification of every survivor. Mirrors the
backend campaign's method (see [the backend report](../MUTATION_TESTING_REPORT.md)).

## Headline

| Metric | Value |
|---|---|
| Mutants (fresh manifest) | 9,944 over 126 source files |
| Executed | 9,944 (100%; zero runner errors) |
| Killed by tests | 7,972 |
| Killed by timeout (confirmed hang) | 2 (api-client 10 s abort budget; image-deps comparator loop) |
| Survived (Phase B re-verified) | 1,892 |
| Not covered by any test | 78 |
| **Mutation score (covered code)** | **80.8%** |

A same-day follow-up round killed 103 more fixable survivors (DOM caps
across nine more page harnesses, the owner attention-weight boundaries,
the benchmark windows, the ops-sim kg formatting) — see EQUIVALENTS.md.

Operator scores: compare 91.2%, not 94.2%, ifexp 93.0%, boolconst 80.8%,
binop 74.4% (??-equivalents), intconst 68.6% (skeleton/card-count and DOM
caps), loopjump 31.3%.

Baseline suite: 4,965 tests in 293 files, green after fixing seven
pre-existing test bugs found by the fresh run (below); closing suite 5,026
tests in 313 files (20 new gap-test files across the campaign and the
follow-up round, all green together).

## Fresh-start incidents (kept for the record)

1. The first wipe deleted the old artifacts at the wrong directory level —
   the stale `results.jsonl` (10,940 verdicts against the OLD 9,763-mutant
   manifest) survived and made the first campaign skip 9,763 ids with
   meaningless verdicts. Caught by the "0 already done/skipped" invariant;
   results deleted, campaign restarted.
2. A `--only` merge feature added to `mutate_cover.mjs` during the map
   rebuild corrupted covering sets via `set.add(...array)` — JS `Set.add`
   takes ONE argument, so all-but-one coverer was silently dropped per line
   (e.g. account-dialog lines lost 5 of their 10 covering files). Detected
   when hardened zod-bound mutants "survived"; map rebuilt in one clean
   pass. The merge path now adds per-element.

## Method

1. **Baseline**: full vitest suite; seven date/budget test bugs fixed
   first (all test-side, no product change):
   - Planner tests computed "current month" in the BROWSER timezone while
     the page anchors to the farm timezone (Asia/Kolkata) — on the last day
     of a month the two disagree for hours (observed 2026-09-30, MST
     evening vs IST October). Five planner test files now derive the month
     via `farmToday()` like the page.
   - One planner test hardcoded `2026-10` as a valid 10–12 band month; it
     became the plan-start month itself on the IST boundary. Now uses next
     year's October (always strictly after the start).
   - The two heaviest simulation tests (501-row and 500-event jsdom
     renders) carried inline budgets sized for idle machines; under
     coverage instrumentation on a loaded machine they time out. Budgets
     raised to 240 s / 420 s (patience only — no assertion weakened).
2. **Manifest** (`mutate_gen.mjs`): TypeScript-compiler-API AST walk, one
   mutation per site, same operator set as the backend campaign (compare
   swaps, `&&`↔`||`, `??`→`||`, `!`-drop, arithmetic swaps, `true`↔`false`,
   numeric ±1, ternary swap, `break`↔`continue`); strings, type positions,
   generated API code and test scaffolding excluded. **9,944 mutants.**
3. **Coverage map** (`mutate_cover.mjs`): each of the 293 test files ran
   once with v8 coverage; the map records which source LINES each file
   executes (40,217 covered lines over 130 source files).
4. **Execution** (`mutate_run.mjs`): every mutant applied IN-MEMORY by a
   vite transform plugin (no file on disk is mutated, so N vitest
   processes share one tree). Selection escalates dedicated-file → 8-file
   spread → full/capped set; wall-clock kill budget 420 s + 25 s/file;
   resumable via `results.jsonl`; a watchdog restarted the runner across
   two OOM/load-spike kills. Zero runner errors at close.
5. **Fix cycle** (concurrent with the campaign): every FINAL survivor of
   the lib/components segment triaged against source (not copied from the
   2026-09-23 log — ids shifted, code moved). Genuine gaps got killing
   tests; each kill verified by running the mutant directly against the
   new file. 11 new test files, 35 tests, **93 verified kills**. The
   equivalent classes are documented in EQUIVALENTS.md with instances.
6. **Phase B** (`mutate_reverify.mjs`, new): every SURVIVED verdict re-run
   against its complete covering selection (dedicated files first) with
   the gap-test files force-included. For ubiquitously-imported modules
   (api-client is covered by 200+ test files) a literal full run costs
   hours per mutant, so the selection there is every DEDICATED file plus a
   25-file spread — stronger than the campaign's sampled rounds. TIMEOUT
   verdicts re-checked separately.

## What the fix cycle killed (93 mutants, all verified mutant-by-mutant)

| New test file | Kills | What was unpinned |
|---|---|---|
| `components/account-dialog.aria.test.tsx` | 12 | TOTP error-field wiring: aria-invalid + per-mode aria-describedby, wired and unwired, all four modes |
| `components/animal-picker.falsy-props.test.tsx` | 3 | `eligibilityKey: ""` is its own query-cache key; placeholder/dialogTitle `""` stay empty |
| `api/custom-instance.query.test.ts` | 1 (+1 pin) | the null-strip slice skips exactly the "?" — first query key survives |
| `lib/offline-queue.boundaries.test.ts` | 14 | exact 72 h TTL expiry; exactly-256 KiB keep/refuse; empty-string body round-trip; null-storage drain counts; Retry-After gate arithmetic (clear-to-zero at clock zero, expiry boundary, retryAfter=1, ×1000, ±1 ms) |
| `lib/task-title.nulls.test.ts` | 1 | a null template arg is skipped — never rendered as "null" |
| `components/health-target-pickers.falsy.test.tsx` | 4 | placeholder/dialogTitle `""` stay empty, both pickers |
| `app/(app)/tasks/reject-dialog.attrs.test.tsx` | 4 | reject-reason textarea maxLength 255 / rows 3 |
| `lib/image-deps-guard.equals.test.ts` | 1 | equal-version comparison terminates (the `<`→`<=` loop bound hangs on equal inputs) |
| `app/(app)/finance/insurance/page.aria.test.tsx` | 16 | create + renew dialog error wiring for all nine fields (incl. the 4 000-char notes cap) |
| `app/(app)/animals/[id]/page.aria2.test.tsx` | 20 | sale weight/price-per-kg error wiring (incl. the disabled-rate refine path), necropsy findings wiring + caps, dialog notes caps |
| `app/(app)/purchases/page.aria.test.tsx` | 26 | origin/transport/weights/history error wiring + every DOM cap in the new-batch dialog |

## Harness hardening (kept for future campaigns)

- `mutate_cover.mjs`: `--only <files>` re-runs selected test files and
  MERGES into an existing map (per-element set adds), with
  `--testTimeout=240000` and a 600 s wall kill so heavy files survive a
  loaded machine.
- `mutate_run.mjs`: budgets 420 s + 25 s/file; escalation is always
  dedicated → 8 → full (jumping straight to full for medium covering sets
  made the simulation tail spend 15–40 min per survivor); auto-run guarded
  so `mutate_reverify.mjs` can import `runVitest` without starting a
  campaign.
- `vitest.mutation.config.ts`: local test/hook timeout 60 s (15 s proved
  too tight for heavy jsdom renders on a loaded machine).
- New `mutate_reverify.mjs` (Phase B full-selection re-verification,
  prefix/extra-files filters).

## Reproduce

```bash
cd frontend
node mutation/mutate_gen.mjs                    # manifest (9,944 mutants)
node mutation/mutate_cover.mjs                  # line → test-file map
MUT_WORKERS=10 node mutation/mutate_run.mjs     # campaign (resumable)
node mutation/mutate_report.mjs                 # report.md + survivors.json
node mutation/mutate_reverify.mjs --status SURVIVED --write   # Phase B
node mutation/mutate_triage.mjs [filter]        # survivor triage view
# single-mutant debugging:
MUTANT_ID=m09754 ./node_modules/.bin/vitest run --config \
  vitest.mutation.config.ts src/lib/utils.test.ts
```

Artifacts: `mutation/{manifest.json, coverage-map.json, results.jsonl,
report.md, survivors.json}` were committed in the historical checkout;
these generated files are no longer tracked.

## Caveats

- Survivor verdicts for ubiquitously-imported modules use
  dedicated-plus-spread selections (see method step 6); everything else was
  re-verified against its complete covering set.
- The two heavy simulation tests' raised budgets change local-run patience
  only; CI budgets are unchanged.
- The machine shared CPU with an unrelated workload throughout; two runner
  kills (OOM/load spikes) were absorbed by the watchdog + resume design
  with zero lost verdicts.
