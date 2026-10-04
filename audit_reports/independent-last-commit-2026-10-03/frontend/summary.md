# Independent frontend audit of fee30d5

Audit date: 3 October 2026. Scope: FE1–FE13, DP1–DP4 and the eleven frontend ADD findings in the implementation ledger. ADD-07 belongs to the backend audit. The audit compared the last-commit changes with actual runtime code, inspected caller/callee ownership and durable state, and introduced adversarial regressions for uncovered cases. It did not treat the previous report or its test totals as proof.

Nine residual behaviors were corrected in five production files. Twelve regression executions were added across four existing test files; one other existing test expectation was updated for the intentional sign-in API option. No commit was created. Production files and tests were frozen before the root agent's final full frontend verification.

The subsequent final source peer review and FM-01–04 map are in `final-source-review.md`. That follow-up corrected a confirmed active mutation-campaign artifact race and passed 22 isolated harness contracts while leaving the application freeze unchanged.

## Residual findings and corrections

| Independent finding | Related claims | Before | Correction and regression |
| --- | --- | --- | --- |
| FEA-01 | FE4, ADD-02/03/05 | Cancelling or unmounting a manager stage that another login superseded could leave its captured grant alive. | Capture the exact manager token and epoch. Revoke the captured grant without clearing the replacement session. Password and MFA cancellation cases preserve the replacement worker. |
| FEA-02 | FE4, ADD-05 | An obsolete farm picker could pin its old farm and sign out a newer worker. | Check ownership of the staged session before pinning. A real AuthProvider/WorkerShell race verifies that the replacement actor and tablet selection survive. |
| FEA-03 | FE4, ADD-03 | Farm discovery failure during a temporary manager sign-in revoked through ordinary cookie-bearing logout. | Explicit temporary-session sign-in mode uses cookie-free exact-session revocation on establishment failure, for password and MFA flows. |
| FEA-04 | FE4 | A request's own failed farm discovery cleared the session and changed its epoch; its original epoch guard then suppressed the error and release of busy controls. | Associate the original thrown error object with its own teardown epoch using a WeakMap. Only that intentional teardown permits the still-mounted request to display the error and finish; replacement-session guards remain. Password/MFA and PIN recovery regressions exercise this. |
| FEA-05 | FE7 | An earlier successful update could rebind the editor after another saved plan was opened. | Capture the editor generation at submission and apply returned plan metadata only to that generation. Deferred real transport response verifies the other plan remains open. |
| FEA-06 | FE7 | A 409 refresh from an older editor could appear after the same saved plan was reopened, because checking the plan ID alone accepted it. | Fence conflict adoption by editor generation as well as plan ID. The same-ID reopen regression suppresses the obsolete conflict. |
| FEA-07 | FE7 | A delayed delete of the formerly open plan could clear a different plan opened while deletion was pending. | Inspect the current open-plan ref when deletion succeeds. The new deferred deletion regression preserves the newly opened plan. |
| FEA-08 | FE7 | Opening another saved NLM plan reset parent validation but preserved the previous NumberInput's invalid local draft when its basis key matched. The visible capital budget remained -1 instead of the newly adopted 3,000,000. | Increment an NLM adoption version when complete defaults/calibration/saved assumptions are adopted and remount the NLM editor on that version. Regression checks the displayed value, validity and update action. |
| FEA-09 | FE6, ADD-06 | The board suppressed task queries before mandatory password rotation, but the shell still automatically replayed pending duties. Definitive restricted-session 403s could become review receipts before rotation. | Supply a replay capability callback to the outbox poller. Durable receipts remain visible while replay is gated. Shell test verifies the gate follows password rotation; an IndexedDB/transport test verifies zero sends while locked and the original retry key after unlocking. |

## Changed files

Production:

- `frontend/src/app/worker/login/page.tsx`
- `frontend/src/lib/auth-context.tsx`
- `frontend/src/app/(app)/planner/page.tsx`
- `frontend/src/app/worker/layout.tsx`
- `frontend/src/lib/worker-outbox.ts`

Tests:

- `frontend/src/app/worker/login/page.races.test.tsx` — six new executions, including password/MFA parameter cases.
- `frontend/src/app/(app)/planner/page.mutation2.test.tsx` — four new cases.
- `frontend/src/app/worker/layout.test.tsx` — one new case.
- `frontend/src/lib/worker-outbox.test.ts` — one new case.
- `frontend/src/app/worker/page.test.tsx` — updated an existing exact-call expectation for the temporary-session option.

At frontend freeze: ten changed files, 295 insertions and 32 deletions. No frontend public asset needed a production correction.

## Claim coverage

“Verified” below means independent source review plus the mapped local unit subset, within the stated limits. It does not claim deployment, human fluency or a new full browser run. The mapped subset initially passed 441 of 442 cases; its sole failure was an existing exact-arity mock expectation after the intentional API extension, subsequently corrected and passed in the 207-case changed-surface run. Counts across runs overlap.

| Claim | Independent assessment | Evidence / limits |
| --- | --- | --- |
| FE1 | Verified actor/farm capture, session/farm epoch revalidation before sending, original retry keys, interruption retention. | `worker-outbox` tests and real transport/IndexedDB paths inspected. FEA-09 closes the separate mandatory-password prerequisite. |
| FE2 | Verified acknowledgement follows committed IndexedDB completion; failed persistence/capacity retains existing queue and reverses optimistic UI. | Outbox and worker board tests. Actual device quota behavior is outside these deterministic storage cases. |
| FE3 | Verified End shift reads committed scoped outbox and cannot discard unresolved work based on stale badges. | WorkerShell tests for pending operations, storage failure, cancellation and actor replacement. |
| FE4 | Original request/mount fencing is present; residuals FEA-01–04 corrected. | Real AuthProvider/WorkerShell password, MFA and PIN races plus auth lifecycle suites. |
| FE5 | Verified permission-correct Team PIN setup/reset, password/PIN member modes and secure random PIN generation. | Team extended suite/source. Backend authority is covered by the backend audit. |
| FE6 | Verified task-only password worker Account action and PIN restriction; residual FEA-09 corrected. | Shell tests and committed-receipt replay test. |
| FE7 | Original local-draft preservation and deliberate complete conflict adoption are present; residuals FEA-05–08 corrected. | Planner campaign/mutation suites, new deferred response and NLM regressions. |
| FE8 | Verified locale-reactive help and known evidence/calibration/narrative templates preserve quantities and conditional text; unknown templates disclose fallback. | Help/evidence/narrative localization tests. Fluent Telugu review remains outside automated proof. |
| FE9 | Verified decision results use executed assumptions, fingerprint and revision, including calibration; stale comparisons use that evidence. | Planner campaign and decision-audit tests/source. Backend numerical/domain accuracy is separately audited. |
| FE10 | Verified URL-backed saved-plan pagination beyond 50; invalid legacy rows remain visible and cannot run or produce a DPR. | Planner tests/source. Search is explicitly outside the original claim. |
| FE11 | Verified minimal credential-free scoped current-shift snapshots, 12-hour bound, current-tab marker, pending-action retention, SW dependencies and reauthentication reconciliation. | Offline shift/outbox/page/shell and service-worker tests. Physical restart, marker retention and oldest-tablet execution are not established. |
| FE12 | Verified empty datasets are distinct from unavailable data; background failure and stale timestamps remain visible. | Owner page tests/source. |
| FE13 | Verified photo upload uses shared composed timeout/cancellation compatibility helper. | Signal-composition and upload source/tests. Oldest supported physical webview remains an operational check. |
| DP1 | Verified parent mutation single-flight, dialog generation protection, captured intent and animal pagination. | Insurance page tests/source. |
| DP2 | Verified phenotype controls freeze while their captured save is pending. | Animal phenotype tests/source. |
| DP3 | Verified external finance filter adoption resets/adopts the associated offset. | Finance campaign suite/source. |
| DP4 | Verified health create intent and animal/duty context, identical-intent dedupe and protection of an already open draft. | Health async ownership suite/source. |
| ADD-01 | Verified shell HTML publication follows successful asset precache, with immediately consumed clones and refresh lifetime retention. | Executed `adv-sw-update` tests; ADD-09 is the related body-stream fix. |
| ADD-02 | Verified PIN attempt abort/invalidation; manager-related abandonment residual FEA-01 corrected. | Login races, exact-session revocation suites. |
| ADD-03 | Exact revocation omits cookies/refresh coordination and retains its bearer; failed transient-establishment residual FEA-03 corrected. | Transport session-logout and auth/login tests. |
| ADD-04 | Verified unresolved-capacity accounting excludes accepted history; explicit captured accepted-ID cleanup is epoch scoped; legacy delivered tombstones remain. | Real IndexedDB tests and shell cleanup tests. |
| ADD-05 | Verified real WorkerShell keeps the login route mounted during setup; stale picker/manager stage residuals FEA-01/02 corrected. | Real shell/provider manager password and MFA setup tests. |
| ADD-06 | Verified guidance and board query gate before required rotation; automatic replay residual FEA-09 corrected. | Shell and IndexedDB/transport gate tests. |
| ADD-08 | Verified help triggers outside interactive details/summary, accessible while collapsed, with focus restoration. | Simulation result-behaviour/field-help tests and source. |
| ADD-09 | Verified each shell body is consumed immediately after its response, prior to aggregate completion; assets warm before HTML publication. | Execution of service-worker tests and source. No fresh physical cold-install browser run by this subagent. |
| ADD-10 | Verified every generated section key uses EN/TE catalog heading/help labels, with explicit unknown fallback. | Section-label and simulation i18n tests. Human fluency remains separate. |
| ADD-11 | Verified real Base UI recipient selector width/prompt, EN/TE keyboard/focus and reopen reset. | Ownership-transfer-dialog tests/source. New screenshot/browser verification is separate. |
| ADD-12 | Verified the two E2E files await the committed URL condition before subsequent hard navigation and retain their business assertions. | Independent source inspection. No fresh complete browser matrix run by this subagent. |

## Verification receipts

Sanitized before failures and after summaries are retained in `focused-results.log`. DOM dumps, bearer fixtures, password/email fixtures and irrelevant warning bodies were excluded.

Commands below ran from `frontend`:

```sh
pnpm exec vitest run src/app/worker/page.test.tsx src/app/worker/login/page.races.test.tsx src/lib/auth-context.test.tsx src/lib/auth-context.races.test.tsx src/lib/auth-context.teardown.test.tsx src/lib/auth-context.latches.test.tsx src/lib/auth-context.session-states.test.tsx 'src/app/(app)/planner/page.mutation2.test.tsx' --reporter=dot
```

Eight files / 207 passed in 17.01s. This preceded the final NLM/delete and replay-gate additions.

```sh
pnpm exec vitest run 'src/app/(app)/planner/page.mutation2.test.tsx' 'src/app/(app)/planner/page.campaign.test.tsx' 'src/app/(app)/simulation/components/decision-audit.test.tsx' --reporter=dot
```

Three files / 104 passed in 12.01s, including the NLM regression. The subsequently added late-delete case passed separately (one selected / 71 skipped); its original tool receipt was observed but no raw file was retained.

```sh
pnpm exec vitest run src/app/worker/layout.test.tsx src/lib/worker-outbox.test.ts --reporter=dot
```

Two files / 52 passed in 3.79s. Following a test-only fetch mock typing correction, both added replay-gate cases passed again in a focused selection; their tool receipt was observed but no raw file was retained.

`pnpm exec tsc --noEmit` passed after all frontend edits. Scoped ESLint of the ten changed files with `--max-warnings 0` passed before the final test-only typing correction. Final `git diff --check` passed. Several established suites emit React act or intentionally unmatched permission mock warnings; passing tests do not imply warning-free output. The root agent owns the final complete coverage, build, lint and typecheck receipts.

## Before evidence limits

The saved NLM failure is a direct UI assertion: expected 3,000,000, received -1. The saved shell rotation failure shows the missing replay-gate callback; independent caller/source review established that the old poller always drained whenever online. The own-establishment failure log initially asserted an enabled PIN submit immediately after the cleared PIN: the final regression was refined to assert visible failure and reenter the PIN before checking enabled controls. This fixture refinement is disclosed rather than representing its initial assertion as complete causal proof. The manager and first planner race failures were observed in tool output before fixes but their raw initial outputs were not retained as files.

## Limits

This subagent did not run a new complete browser matrix, physical tablet restart/cold-install test, oldest supported webview, manual screen-reader/touch review, fluent Telugu assessment, real paid-provider call, veterinarian evaluation or production deployment. Unit coverage and mutation contracts cannot establish those outcomes or a new product-quality score. The preserved implementation reports remain historical evidence; this independent summary does not rewrite them.
