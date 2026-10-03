# Mutation measurement review — 2026-10-03

The mutation tooling supplies useful test-gap evidence, but its historical headline scores are not reliable enough to serve as release certification. The root audit independently recomputed the latest recorded verdicts as approximately 98.79% for backend covered mutants and 80.82% for frontend covered mutants. That corrects aggregation arithmetic; it does not establish isolation, baseline validity, source freshness, or the cause of every recorded kill.

This review covers complete semantic reading of seven frontend harness files (1,208 lines) by the evidence reviewer and nine backend harness files (1,548 lines) by the root reviewer. The [source ledger](evidence/mutation-source-ledger.tsv) identifies all 16 files. No mutation harness was executed, no fresh campaign was run, and no application source was changed during this review. Findings below are confirmed source behavior with inferred measurement consequences. The review does not quantify how many historical verdicts were affected.

## Frontend

The frontend's process-local Vite transform applies one mutant in memory and does not rewrite application files. That gives it a stronger isolation design than the backend harness's shared working tree. Its report also takes the latest result per mutant ID, avoiding the backend report's raw-row counting issue. The normal campaign checks current source hashes against its manifest before running. These controls are present in [mutate_transform.mjs](/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/mutation/mutate_transform.mjs:32), [mutate_report.mjs](/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/mutation/mutate_report.mjs:15), and [mutate_run.mjs](/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/mutation/mutate_run.mjs:318).

### FM-01 — Medium: unsuccessful runner exits can be credited as test kills

The runner detects a wall-clock timeout, explicit “No test files found” output, and process-spawn errors. Every other nonzero or signaled completion becomes `KILLED`, without requiring an assertion failure or a successful unmutated baseline for the exact selection. Collection, import, configuration, infrastructure, or invalid-mutant compilation failures can therefore receive the same verdict as a behavioral test kill. Timeouts are counted as kills in the report. See [runner classification](/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/mutation/mutate_run.mjs:112) and [score calculation](/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/mutation/mutate_report.mjs:34).

The generator also permits operators such as `break → continue`; syntax/context validity must be established separately rather than assuming every generated edit creates an executable behavioral mutant. This review did not execute such a mutation or count invalid current mutants.

**Improve:** run and record a clean baseline for each distinct test selection under the same mutation configuration and resource limits. Parse a structured test result. Use separate `INVALID`, `INFRA_ERROR`, and `INCONCLUSIVE_TIMEOUT` states, and require a reproducible mutant-specific hang against a passing baseline before treating a timeout as a behavioral kill.

### FM-02 — Medium: resumed and reverified verdicts have no complete campaign identity

Mutant IDs are sequential within each regenerated manifest. Resume treats any recorded ID as complete, including an earlier `ERROR` or `NO_COVERAGE`, and results contain no manifest/source/test/coverage digest that proves the old ID still represents the same experiment. Regenerating a manifest after source changes can reuse IDs. Changes to tests alone also do not invalidate earlier results. See [ID generation](/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/mutation/mutate_gen.mjs:95), [resume loading](/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/mutation/mutate_run.mjs:252), and [resume use](/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/mutation/mutate_run.mjs:341).

Reverification imports `runVitest` directly and does not execute the normal campaign's source-hash guard. The transform applies stored offsets without checking the input text's hash. An outdated manifest can therefore be used for reverification even when the normal campaign would refuse it. See [reverification invocation](/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/mutation/mutate_reverify.mjs:123) and [unguarded transform edits](/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/mutation/mutate_transform.mjs:36).

**Improve:** bind every attempt to a campaign ID covering source, tests, coverage, harness configuration, dependency lock, exact selection and mutant edit. Enforce the source guard inside the transform or shared execution entry point. Resume only compatible terminal results; retry inconclusive failures explicitly.

### FM-03 — Medium: partial coverage refresh retains obsolete coverers

The `--only` coverage path loads the old map, then only adds newly observed test-file coverage. It does not remove the selected test file's old coverage before refreshing it. Deleted tests, removed execution paths and changed source line positions can remain mapped. There are no source/test hashes or generated-at identity in the output map. See [old-map loading](/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/mutation/mutate_cover.mjs:65), [additive updates](/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/mutation/mutate_cover.mjs:133), and [output format](/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/mutation/mutate_cover.mjs:167).

Failed coverage runs are warned about and excluded, but the script still writes a map and completes successfully; a full refresh can thus publish incomplete coverage as a valid selection map. On a partial refresh, stale entries from the failed test remain. See [failed-run handling](/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/mutation/mutate_cover.mjs:121) and [final completion](/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/mutation/mutate_cover.mjs:167).

**Improve:** store coverage per test file with source and test hashes. Remove a test's prior contribution before replacement, reconcile deleted files, and invalidate changed source positions. Publish the map atomically only when required baseline coverage runs succeeded, with an explicit completeness flag for intentionally partial maps.

### FM-04 — Low: “full” verification and smoke checks overstate their guarantees

The initial campaign stops at 25 selected files; `--full` still samples at 60. Reverification switches to dedicated tests above 30 covering files and silently leaves the prior result when no dedicated file exists. Although individual results include a `capped` flag, the headline score combines these verdicts with fully selected verdicts. See [full selection cap](/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/mutation/mutate_run.mjs:160), [initial cap](/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/mutation/mutate_run.mjs:175), and [dedicated-only reverification](/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/mutation/mutate_reverify.mjs:118).

The smoke check prints failures without an aggregate failure exit, and labels an unmutated `ERROR` or `TIMEOUT` as passing because only `KILLED` is treated as failure. See [smoke baseline verdict](/Users/pradeepreddy/Desktop/goat-farm-management-main/frontend/mutation/mutate_run.mjs:267).

**Improve:** distinguish sampled, dedicated-only, and complete-selection verdicts in headline totals. Make a true full mode run every coverer; record an explicit unverified state when no practical selection is available. Make smoke fail with a nonzero exit unless all required checks pass, and require the baseline verdict to equal `SURVIVED`.

## Backend

The backend runner restores each edited file's original bytes in `finally`, and uses per-file locks. Those controls help restore the checkout after ordinary completion. They do not isolate tests from concurrent edits to other imported files. See [file-lock setup](/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/mutation/mutate_run.py:102) and [byte restoration](/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/mutation/mutate_run.py:256).

### BM-01 — Medium: parallel mutants share a mutable application checkout

Each worker writes its mutant into the shared `app` source tree, launches pytest, and restores that file. Locks are per file, while the default runner has six workers. A test evaluating mutant A can import concurrently mutated files B or C. A result is therefore not guaranteed to represent one mutation. See [in-place application](/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/mutation/mutate_run.py:198) and [concurrent workers](/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/mutation/mutate_run.py:315). Verification helpers use the same shared execution model.

**Improve:** give each mutant an exclusive worktree or immutable source snapshot, its own process namespace and isolated database. Alternatively execute one mutant at a time under a checkout-wide lock. Preserve byte-exact restoration, but do not use restoration as evidence of experiment isolation.

### BM-02 — Medium: all unsuccessful pytest exits and timeouts can improve the score

The runner maps nonzero pytest exits to `KILLED`, including collection, internal, no-tests and infrastructure failures. It does not prove that the exact unmutated selection passed first. The report credits `TIMEOUT` as killed. Longer verification budgets reduce some false timeouts but do not establish a counterfactual baseline. See [classification](/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/mutation/mutate_run.py:239) and [timeout scoring](/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/mutation/mutate_report.py:37).

**Improve:** use the same clean-selection baseline and structured verdict taxonomy proposed for the frontend. Record assertion failures separately from invalid mutants, setup errors, runner failures and resource exhaustion.

### BM-03 — Medium: source/test freshness is not enforced for resumed results or edits

The backend mutant ID material does not bind results to a complete source/test/coverage identity. Resume retains recorded IDs after changes, and applying a mutant does not check its original statement or source hash against the current file. See [ID material](/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/mutation/mutate_gen.py:386), [resume](/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/mutation/mutate_run.py:345), and [edit application](/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/mutation/mutate_run.py:198).

**Improve:** check source hashes and exact original edit text immediately before mutation, and bind result reuse to the versioned campaign identity. Invalidate old killed verdicts when relevant tests or source change, rather than only revisiting survivors.

### BM-04 — Medium: the final-pass dry-run flag still performs mutations

`mutate_final_pass.py` declares `--dry-run`, but does not branch on it before rewriting results and running in-place mutants. Someone asking for a preview can trigger execution. This source behavior was not executed during the audit. See [flag declaration](/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/mutation/mutate_final_pass.py:49) and [unconditional writes/execution](/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/mutation/mutate_final_pass.py:103).

**Improve:** make dry-run exit after displaying an immutable plan and before any write or process launch. Add a focused harness check that verifies dry-run leaves sources and result artifacts byte-identical and spawns no test processes.

### BM-05 — Medium: extremes verification folds earlier passes under current semantics

`mutate_verify_extremes.py` appends to its verification artifact, then reads and folds all historical records using the current `--status` semantics instead of restricting the fold to the current run's target IDs and attempts. A previous pass's timeout can be reinterpreted under another pass's semantics. See [append](/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/mutation/mutate_verify_extremes.py:128) and [historical folding](/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/mutation/mutate_verify_extremes.py:133).

**Improve:** assign each verification run a unique identity and store its status policy per attempt. Fold only compatible attempts for the current target set; retain historical artifacts as history rather than silently reclassifying them.

### BM-06 — Low: the generated backend report counts raw append rows

The report reads raw results filtered against the manifest without taking only the latest record per ID. Reverified mutants can appear more than once in totals. The root audit's independent latest-ID aggregation fixes this arithmetic for the audit report, while leaving the repository's tooling unchanged. See [raw-row loading](/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/mutation/mutate_report.py:21).

**Improve:** deduplicate compatible results by mutant and campaign identity before scoring. Show total, executed, uncovered, invalid, inconclusive, sampled and reviewed-equivalent counts alongside the covered-mutant score.

### BM-07 — Medium: standalone compilation silently excludes function-return mutants

Before compiling the complete mutated file, the generator compiles the replacement statement by itself in module context. A valid function `return` statement consequently raises `SyntaxError` and is dropped; direct `await` and `yield` statements have the same context problem. Only loop jumps receive a special exemption. See [standalone validation](/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/mutation/mutate_gen.py:359).

The current manifest has **zero original statements beginning with return among 6,565 mutants**. A read-only check confirmed that ordinary return/await/yield snippets fail standalone compilation. No generator or mutant was executed. This systematically omits direct return-expression mutations despite the documented exclusion policy naming annotations/decorators, rather than returns.

**Improve:** validate the replacement inside its original full-file context, using the existing full-file compile step; or preserve a correct enclosing function/async context in any preliminary check. Report excluded sites by reason so the score's actual mutation scope is visible.

## A trustworthy measurement gate

Before treating a mutation percentage as a release gate, establish isolated execution, a passing baseline for each selected test set, immutable campaign identity and explicit incomplete/error states. Rebuild coverage against the same source/tests, then run a fresh campaign and retain reproducible attempt receipts. Review survivors for missing behavioral assertions and genuinely equivalent edits; document exclusions instead of optimizing the percentage alone.

The existing full unit suites passing during the project audit remain valuable evidence. They do not retrospectively validate every historical mutation attempt, especially when mutation configuration, per-file test selection and concurrent resource conditions differ. No claimed historical percentage measures veterinary correctness, financial model accuracy, offline durability or real-world usability.
