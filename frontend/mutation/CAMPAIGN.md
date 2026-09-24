# Frontend Mutation Testing Campaign — 2026-09-23

Deep mutation testing of the Next.js frontend (`frontend/src`), mirroring the
backend campaign's method (see ../MUTATION_TESTING_REPORT.md).

## Method

1. **Baseline**: full vitest suite green (4,699 tests, 271 files) after
   fixing one pre-existing broken test (`image-deps-guard.test.tsx` asserted
   a toast spy AFTER `mockRestore()`, which wipes the call history).
2. **Mutant generation** (`mutate_gen.mjs`): TypeScript-compiler-API AST
   walk, one mutation per site, same operator set as the backend (compare
   swaps, `&&`↔`||`, `??`→`||`, `!`-drop, arithmetic swaps, `true`↔`false`,
   numeric ±1, ternary swap, `break`↔`continue`). String literals, type
   positions, generated API code and test scaffolding excluded.
   **9,763 mutants over 125 source files.**
3. **Coverage map** (`mutate_cover.mjs`): vitest 4 has no per-test coverage,
   so each of the 271 test files ran once with coverage; the map records
   which source LINES each test file executes. 128 source files touched,
   36,546 covered lines, **only 104 mutants sit on lines no test covers**.
4. **Execution** (`mutate_run.mjs`): each mutant is applied IN-MEMORY by a
   vite transform plugin (`mutate_transform.mjs`, env-selected by
   `MUTANT_ID`) — no file on disk is ever modified, so N vitest processes
   run mutants in parallel on one tree. Test selection escalates
   dedicated-file → 8 → 25 (spread, dedicated first), mirroring the backend
   15→60 scheme. Wall-clock kill at 240 s + 20 s/file. Resumable
   (results.jsonl); smoke-checked (transform applies, deterministic, no
   cross-run cache).
5. **Fix loop**: every survivor of a completed file was triaged; genuine gaps
   got killing tests (re-verified mutant-by-mutant with `--redo --files`),
   documented equivalents got logged in EQUIVALENTS.md.

## Headline (campaign in progress — regenerate with `node mutation/mutate_report.mjs`)

See `report.md` for the live numbers and the full per-file table /
survivor list. At the time of writing: ~4,400 of 9,763 mutants executed
(3,349 killed, ~960 survivors incl. capped samples pending Phase B, 57 on
uncovered lines), **mutation score ≈ 87%** on covered code, zero runner
errors. The campaign continues in the background (resumable); re-run
`node mutation/mutate_report.mjs` at any time for current numbers.

## What the campaign found and fixed

**One real product bug (source-fixed + regression-tested):**
- `account-dialog.tsx` — a TOTP request settling after the dialog closed
  left `totpBusy` stuck true: every TOTP control in the reopened dialog
  stayed disabled until remount. The epoch fence now guards only the
  result/error writes; busy always clears.
- `account-dialog.tsx` — an empty-string display name rendered blank
  trigger/avatar instead of falling back to the email (`??` → `||`).

**One pre-existing broken test fixed** (see baseline above).

**~500 survivors killed with new tests** across 20 modules, concentrated in:

| Module | What was unpinned |
|---|---|
| `lib/api-client.ts` | refresh-payload guard arms; actor-scope preservation; auth-failure registration stack; 499/500 transient boundary; single-flight dedup; exact 10 s / 60 s / 300 s / 62 s budgets; root-path allowlist; ApiError.code |
| `lib/auth-context.tsx` | bootstrap retry budget of exactly 3; stored-farm-id strict parse; tombstone `revoked:0` |
| `lib/offline-queue.ts` | per-field wellFormed battery; exact 256 KiB read+write ceiling; failure classification edges; multi-record drain paths |
| `lib/idempotent-request.ts` | UUID v4 fallback generator; 128-record retention bound |
| `lib/format.ts` | money sign rule on rounded parts; addDays month/year edges; daysBetween exactness; timestamped formatDate |
| `components/screening-check-dialog.tsx` | exact 25 MiB ceiling; 1-byte floor; per-bucket counts; .png/.jpg names; 409 split; stale fences; finish flow |
| `components/account-dialog.tsx` | zod 1/12/128 bounds; DOM maxLength caps; TOTP epoch fences both sides; busy wiring; recovery-copy cycle |
| `components/charts.tsx` | Donut total>0 gate; 148 px default; maxCount floor; 2 px bar floor; marker styling |
| `components/*pickers` | cull-candidate suffix (does only); 60-char search caps |
| app pages (animals/[id], insurance, simulation, tasks) | sale money/weight schema caps; mortality/necropsy caps; aria-describedby wiring; skip-reason 255; event-count 100,000; recurring-anchor future leg (equivalent — locked UI) |

## Equivalent-mutant classes (EQUIVALENTS.md has every decision)

The dominant classes: `a ?? b` → `a || b` where `a`'s type has no
falsy-but-defined value; opaque epoch/version tokens; initial state a mount
effect immediately overwrites; defense-in-depth guards behind already
disabled buttons; dead arms for wire-legal inputs; jsdom-invisible layout
(button size classes); documented `// Stryker disable` annotations from the
tree's earlier hardening era.

## Reproduce / resume

```bash
cd frontend
node mutation/mutate_gen.mjs                  # manifest (9,763 mutants)
node mutation/mutate_cover.mjs                # line → test-file map (~7 min)
MUT_WORKERS=10 node mutation/mutate_run.mjs   # campaign (resumable)
node mutation/mutate_report.mjs               # report.md + survivors.json
node mutation/mutate_triage.mjs [filter]      # survivor triage view
# single-mutant debugging:
MUTANT_ID=m09754 ./node_modules/.bin/vitest run --config \
  vitest.mutation.config.ts src/lib/utils.test.ts
```

Phase B (after the campaign drains): regenerate the manifest + coverage map
(picks up the new tests and the account-dialog source fixes), then re-run
every remaining SURVIVED and TIMEOUT mutant with `--full` for final
verdicts; `mutate_report.mjs` then prints the closing numbers.

## Caveats

- Covering sets > 25 test files are judged by a 25-file spread (dedicated
  files always included); capped survivors are flagged in survivors.json and
  settle in Phase B.
- TIMEOUT counts as killed (provisional; re-verified in Phase B).
- The full suite stays green with every added test (4,806 at last run); one
  planner test flakes only under the campaign's 12-worker CPU saturation
  (passes solo with its normal budget).
