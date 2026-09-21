# Independent Verification of the 2026-09-20 Fix Campaign — 2026-09-20

Method: every P1/P2 finding from `MASTER_SYNTHESIS.md` was re-verified against the current
(uncommitted) working tree by 7 independent auditors working from the finding text only — old line
numbers were not trusted, the original claims themselves were re-checked (all were real; agents
reproduced the pre-fix behavior against HEAD), and fix quality was judged on whether *every*
described trigger path is closed. Empirical repros ran on the project venv (SQLAlchemy 2.0.51,
pydantic 2.13.4): the screening parsers/validators/cascades were executed directly, the simulation
engine was run in-memory for P1-4/P2-5/P2-6/P2-7/P1-5/P2-9/P2-8, and the SQLAlchemy
failed-commit/expiry semantics behind the P1-1 residual were reproduced. 18 P3 items (a
representative sample of the ~60 grouped) were spot-checked.

## Verdict summary

| Severity | Fixed | Partially fixed | Not fixed | Total |
|---|---|---|---|---|
| P1 | 10 | 1 (P1-1) | 0 | 11 |
| P2 | 16 | 3 (P2-8, P2-14, P2-15) | 1 (P2-13) | 20 |
| P3 (sampled) | 18 | 0 | 0 | 18 |

## P1 verdicts

- **P1-1 — PARTIALLY FIXED.** Both audit-named paths are closed: attributes are captured before the
  per-image rollback (`pipeline.py:554`), the farm-id dict is snapshotted pre-loop (:521), and an
  awaited `db.refresh(image)` at loop top (:528) heals next-iteration reads after a prior rollback.
  **Residual (empirically confirmed twice by the agent and re-confirmed independently):**
  `pipeline.py:574` reads `image.id` *after* the failed commit (:567) and *before* the rollback
  (:575). A flush-level commit failure — including one caused by a *different* pending object (an
  unflushed findings INSERT) — provably raises `PendingRollbackError` on that read on SQLAlchemy
  2.0.51, the exception escapes `run_screening_cycle`, and the "log, roll back, keep screening"
  handler never reaches its own rollback for its primary documented case (constraint/connection
  failure). Rows strand as PROCESSING (now bounded by the P1-3 sweep) and the failure counts toward
  the 3-strike worker exit. **One-line fix:** move `await db.rollback()` above the log line, or log
  a pre-captured id.
- **P1-2 — FIXED.** Both parsers route through a shared `answer_list()` shape gate; all malformed
  shapes (`null`/object/number/missing) were executed and produce either a graceful empty result or
  a controlled parse error that the cascade catches; Anthropic adapter except-tuple now matches
  OpenAI. Minor softness, not a defect: a structured-but-wrong value like `{"goats": {}}` degrades
  to "no goats" instead of trying the next provider.
- **P1-3 — FIXED.** New `_terminate_budget_exhausted_processing` sweep runs each cycle before
  claiming: (PROCESSING ∧ attempts≥MAX ∧ stale) → terminal ERROR with operator message,
  `FOR UPDATE SKIP LOCKED`, durably committed even on empty cycles, never re-claimed, counted in
  `CycleSummary.terminated_processing`.
- **P1-4 — FIXED.** `doe.milestone_floor = -1` on every service assignment (daily_ops.py ~:1185-1197).
  Empirical 365-day run: second pregnancy now fires PREGNANCY_LATE move, both pre-kidding vaccines,
  and DELIVERY transition; pre-fix HEAD reproduced the audit's zero-milestone behavior.
- **P1-5 — FIXED.** New `min_feasible_sale_age()` clamp in both the sensitivity mutator and the
  optimizer grid; `_run_validated` additionally maps `ValueError`/`ValidationError` → 422 on both
  run endpoints. Empirical: the boundary scenario (grower purchase age 8, sale_age 9) still fails
  raw (proving the original claim) but sensitivity and optimization now complete.
- **P1-6 — FIXED.** `bd201c1cdc1b` now deletes `feed_finished_stock` and `feed_recipe_lines` before
  `feed_recipes`; both FKs verified to exist at that chain position with no cascade. Child-first
  ordering holds across every wiped table. Already-applied DBs need no manual pre-check (transactional
  alembic env).
- **P1-7 — FIXED.** `estimated_dob` date input renders exactly for (SOLD ∧ sex=M ∧ no DOB on file),
  wired into the payload, validated (required/future-date/non-SOLD), unregistered when leaving SOLD;
  consistent with the backend's stamping/rejection rules.
- **P1-8 — FIXED.** Herd-level duty links survive scope switches; deep link now defaults to bucket
  scope; animal/batch radios disabled with a localized hint mirroring the backend preview/write
  contract. Tests added naming the finding.
- **P1-9 — FIXED.** Only NATURAL offered in the dialog; backend guard unchanged (legacy AI records
  still render). Mutation test added; the mock-POST-succeeds-for-AI tests are gone.
- **P1-10 — FIXED.** Back handler clears the stale TOTP draft
  (`setValue("totp", undefined, {shouldValidate:false})`); RHF semantics traced — partial/empty
  drafts no longer dead-end the password submit. No regression test pins the actual repro.
- **P1-11 — FIXED.** New weekly `frontend-mutation.yml` (schedule+dispatch, 4-shard matrix, score
  extraction, trend job appending to `audit_reports/mutation-trend.json`) mirroring the backend
  mutmut posture; lib shard glob now `*.{ts,tsx}` including auth-context/i18n; `test:mutation` runs
  the shards. Posture caveats: deliberately non-gating; shard thresholds left at `low: 80` despite
  the measured 77.1% lib score; README still documents only the backend campaign.

## P2 verdicts

- **P2-1 — FIXED.** `books_money` guard requires Idempotency-Key for the historical-import-with-price
  branch too; matches the booking site exactly (₹0 price still guarded — correct).
- **P2-2 — FIXED.** Hold-clearing probe is now episode-scoped (PLACED row of the current
  `restriction_version`); mortality-placed holds (no health event) keep the statutory date. The
  existing test would pass under the old code too — the exact reported interleave is untested.
- **P2-3 — FIXED.** `sale_price IS NOT NULL` added; one WHERE constrains both sums.
- **P2-4 — FIXED.** Calibration now calls the engine's own `labour_units_for()` (half-attendants,
  0 for zero-does/family labour); zero-unit case keeps the wage with an evidence record. The
  existing calibration test still asserts whole-attendant math and passes only by numeric
  coincidence.
- **P2-5 — FIXED.** `sum(lact)` included in the doe-age ledger denominator. Empirical invariant:
  ledger-vs-census deficit was −1.07% (m12) / −4.43% (m120) pre-fix — reproducing the audit's
  numbers — and 0.0000% at every probe month post-fix.
- **P2-6 — FIXED.** Sex filter removed from the weaning loop; female guards moved into the
  dam/postpartum branches; age fallback weans males RECOVERY→MALE_KIDS. Empirical: male starter kid
  now weans day 31, gets weaned rations, sells ~month 8; pre-fix he was stranded with no feed line
  for life. No male-path regression test (existing test uses female kids only).
- **P2-7 — FIXED.** `apply_system("semi_intensive")` now sets the growth regime *and* swaps the
  weight table to the CIRG field curve anchored on the breed's birth weight (the explicit swap is
  required because the table derives only at construction). Empirical curve check confirms.
- **P2-8 — PARTIALLY FIXED.** The reported 120→24→120 round-trip now restores all Bakrid months
  (re-anchor branch + backward-planner re-validation). Two reproduced residuals: (a) a materialized
  *empty* festival list never re-anchors (2039+ horizons auto-fill `[]` and lose the decade's
  festivals permanently); (b) a user-customized list that happens to equal a Bakrid prefix is
  silently expanded to the full calendar.
- **P2-9 — FIXED.** Zero-progress guard with rollback and an explanatory note; all three degenerate
  configs now return 0 purchases with `gaps_closed=False`; healthy-path planner/backward-planner
  tests pass (41 targeted tests). No dedicated regression test for the rollback.
- **P2-10 — FIXED.** `_touch_processing_lease` now flushes at all three call sites (row lock pinned);
  new boot-time cross-validator rejects `stale < (2N+6)×timeout` in both Settings and worker
  settings — empirically rejects the audit's pathological config at the exact boundary.
- **P2-11 — FIXED.** Blank→None coercion at the sole `ScreeningFinding` construction site and in
  `ScreeningFindingReviewIn`; Python strip is stricter than the DB CHECKs; both trigger paths
  empirically closed.
- **P2-12 — FIXED.** `_https_or_loopback_url` now reads `parsed.port` inside try/except
  (config.py:262-270, comment cites P2-12); malformed ports fail at boot.
- **P2-13 — NOT FIXED (fix attempt defeats itself).** The added
  `UPDATE health_events SET schedule_template_id = NULL` is blocked by
  `trg_health_event_schedule_template_id_immutable` (BEFORE UPDATE OF schedule_template_id, raises
  23514 on any change), created by `d7e8f9a0b1c2` and dropped/re-created by `e3f4a5b6c7d9` — both
  verified ancestors of `b5d7f9a1c3e5` by walking the down_revision chain. Deployed databases with
  ORF-linked events now abort the upgrade with 23514 instead of 23503 — still broken, different
  error; fresh/CI databases still can't see it (zero ORF-linked events). The fix must
  drop/disable/recreate the trigger around the UPDATE, exactly as `e3f4a5b6c7d9` itself does.
- **P2-14 — PARTIALLY FIXED.** Both recommended guardrails landed (CI `migration-immutability` job
  + README detection query with runbook caveat); the current revision refuses instead of rewriting
  (the destructive original is preserved in git history with a HISTORY CAVEAT docstring). The
  historical data loss itself is unfixable, as the audit expected. **Internal contradiction:** the
  new CI gate rejects Modified/Deleted/Renamed files under `backend/alembic/versions/` — which is
  precisely what this fix campaign did to `bd201c1cdc1b` (P1-6) and `b5d7f9a1c3e5` (P2-13). As
  written, committing this working tree as a PR trips the gate. Resolve deliberately (allowlist
  these two edits once, or restate the fixes as new revisions) before committing.
- **P2-15 — PARTIALLY FIXED.** The fleet-wide single-bucket aggregation is gone (per-client
  `map` key), documented, and test-pinned. Two residuals: (a) **the leftmost-XFF key is
  spoofable** under the repo's own documented appending-terminator topology
  (`docker-compose.production.yml:99-104`): a client injecting `X-Forwarded-For:` rotates unlimited
  fresh buckets (bypassing the flood zone) or pins a victim's IP for targeted 429s; the
  spoof-resistant choice is the rightmost XFF entry or `set_real_ip_from` — the template comment
  claiming equivalence with the backend's right-walking limiter is inaccurate; (b) rates remain
  hardcoded (5 r/s, burst 20), not env-tunable.
- **P2-16 — FIXED.** Mirror constant is 10_000 in both places, drifted copy deleted, read-time clamp
  + out-of-range self-heal added, and a new `backend-constants-parity.test.ts` reads the backend
  source at runtime to pin the mirror (and the breeding age) — either side drifting now fails the
  suite.
- **P2-17 — FIXED.** 12 everywhere (gate copy, breedingEntry, facts, field help); client zod gate
  reads the vocabulary dynamically so copy and gate cannot drift; pinned by the parity test and
  vocabulary tests.
- **P2-18 — FIXED.** Renewal cell branches to "(Nd overdue)" in destructive color, mirroring the
  adjacent late patterns. No overdue-string test.
- **P2-19 — FIXED.** 404 distinguished (`deepLinkNotFound`), friendly stale-link explainer with
  Clear-link dismissal, Retry suppressed for 404; tests added for the stale-link lifecycle.
- **P2-20 — FIXED.** Selection re-validated against current rows (label, gate, and compare payload),
  removable chips including off-page ids, honest count; tests added.

## P3 spot checks (18 sampled, all fixed)

WATER enum labels (en+te); `utils.today/business_date` catch ValueError; `InsurancePolicyIn.notes`
max_length; whitespace suspected_disease 422 (the audit's 500 claim was already stale at HEAD, but
the current tree is safe and test-pinned); dead vocabularies dropped by the new head migration
`cad1e2f3a4b5` (fail-closed preflights, NOT VALID+VALIDATE, symmetric downgrade, enum/model/schema
parity pinned); Dockerfile `--no-cache`/`--locked`; CI `uv lock --check`; `.dockerignore` coverage +
audit_reports; `eslint --max-warnings 0`; TrustedHostMiddleware outermost; `cull_candidate` in
`BreedingCandidateOut` + picker flag; status-badge ERROR/CONFIRMED/PLACED/CLEARED; recurring-complete
next-occurrence anchor `max(due, today)`; process-wide S3 client (lru_cache + identity-keyed
settings cache); rotation `cross_checker_for()` respects who served/failed; `_SENSITIVITY_PASSES`
comment matches the 19 code paths; `prob_npv_negative_se` None contract + gated liquidation-payback
sentence; `throttle_summary_task` awaited on shutdown.

## Cross-cutting gaps in the fix campaign

1. **Regression-test coverage is the systemic weakness.** Most backend fixes (P1-1..P1-6, P2-1..P2-3,
   P2-5, P2-6, P2-8..P2-11, P2-13) shipped with no test that pins the fixed behavior; several
   pre-existing tests pass coincidentally under old and new code (P2-2, P2-4). Frontend fared
   better (P1-8, P1-9, P2-16/17/19/20 got tests; P1-7 and P1-10 did not).
2. **Two fixes are themselves defective:** P2-13 (trigger abort — must be redone) and P2-15
   (spoofable rate-limit key — should switch to the rightmost XFF entry before the next prod deploy).
3. **One internal contradiction:** the new migration-immutability CI gate will fail this very
   working tree's in-place edits of `bd201c1cdc1b`/`b5d7f9a1c3e5` if committed as a PR.

## Recommended actions before committing/deploying

1. Fix `pipeline.py:574` (P1-1 residual) — one line.
2. Redo the P2-13 fix with drop-trigger/UPDATE/recreate (mirror `e3f4a5b6c7d9`), and add a migration
   test that actually seeds an ORF-linked health event.
3. Re-key the prod auth rate-limit zone on the rightmost XFF entry (or real_ip) and consider making
   the rates env-tunable.
4. Resolve the migration-immutability gate contradiction (allowlist the two sanctioned edits or
   restate them as new revisions).
5. Close the P2-8 empty-festival-list edge (re-anchor on `[]` when the horizon contains festivals).
6. Backfill the highest-value regression tests: mid-cycle rollback/commit-failure (P1-1),
  PROCESSING-at-budget sweep (P1-3), second-pregnancy milestones (P1-4), priced+unpriced mixed
  sales (P2-3), planner zero-progress rollback (P2-9), rejected pathological screening config
  (P2-10).

---

## Addendum — remediation of the verification findings (2026-09-20, same day)

All six recommended actions above were implemented and verified in the working tree:

1. **P1-1 residual closed.** `run_screening_cycle` now iterates a pre-loop
   `claimed_snapshot` of `(image, image_id, attempts)` tuples — captured while the session is clean
   after the claim commit — so no post-rollback/post-failed-commit path reads an ORM attribute:
   both log lines use the snapshot id, the attempt count comes from the snapshot (stable: the claim
   commits the budget before the cycle), and the commit-failure handler rolls back BEFORE logging.
   Pinned by `test_one_images_unexpected_failure_does_not_kill_the_cycle` (mid-cascade crash after a
   rollback) and `test_one_images_commit_failure_rolls_back_and_spares_the_cycle` (real constraint
   violation at the per-image commit); both were verified to fail with `MissingGreenlet` /
   `PendingRollbackError` against the pre-fix loop.
2. **P2-13 redone.** `b5d7f9a1c3e5` now DROPs `trg_health_event_schedule_template_id_immutable`,
   runs the detach UPDATE, re-creates the trigger (the pattern `e3f4a5b6c7d9` itself uses), then
   deletes the ORF template. Pinned end-to-end by
   `test_orf_linked_health_events_survive_the_reference_data_migration`: a throwaway DB upgraded to
   `e3a5b7c9d1f2` with a seeded ORF-linked `health_events` row (which first proves the trigger
   really raises 23514 on the detach), then `alembic upgrade head` succeeds, the link is NULL, the
   template is gone, and the trigger is present and still enforcing immutability afterwards.
3. **P2-15 re-keyed + tunable.** The prod zone map now captures the RIGHTMOST X-Forwarded-For entry
   (`~^(?:.*,)?[ \t]*(?<client_addr>[^, \t]+)[ \t]*$`) — the address the trusted appending
   terminator controls — with the header-comment rationale corrected (the first entry is
   client-controllable; the backend's right-walk is the equivalent, not the first-entry read).
   Rate/burst are now operator knobs (`GOATFARM_EDGE_AUTH_RATE`, `GOATFARM_EDGE_AUTH_BURST`)
   grammar-validated by `edge-entrypoint.sh` and wired through both compose files and `.env.example`.
   Deployment tests pin the map shape, default and custom rendering, and rejection of malformed
   values (exit 2).
4. **P2-14 contradiction resolved.** The CI `migration-immutability` job now carries an explicit,
   documented, per-file allowlist for exactly the two sanctioned in-place corrections
   (`bd201c1cdc1b` — P1-6, `b5d7f9a1c3e5` — P2-13), with the reasoning recorded in the job
   comment (a defect inside an applied revision's own DDL cannot be pre-empted by a later
   revision; both DB populations stay safe because alembic revisions run transactionally and the
   added statements are idempotent no-ops on DBs that already passed). The gate script was
   executed against the real working tree: passes, and still fails on any unsanctioned file.
5. **P2-8 empty-list edge closed.** Auto-fill no longer materializes an empty Bakrid calendar:
   when the horizon contains no festival month, `festival_sale_months` stays `None` (the auto-fill
   stays live), so extending the horizon re-derives the festivals instead of freezing `[]` into the
   decade. An explicitly empty list remains the user's "no festival months" and is respected
   verbatim (an earlier attempt that treated `[]` as a re-anchorable zero-length prefix broke four
   engine tests that legitimately disable festivals with `[]`; the None-preserving design fixes the
   2039 horizon-12→120 repro without touching explicit lists). Behavior-neutral for defaults
   (`eid_month=0` means the legacy fallback never fires). Pinned by the four festival tests in
   `test_simulation_planner.py`.
6. **Regression tests backfilled (53 new/updated across backend and frontend),** covering every
   gap named in this report: screening P1-1a/P1-1b/P1-2/P1-3/P2-10/P2-11 (31 instances,
   mutation-validated against the pre-fix code), P2-13/P2-2/P2-3, P1-4/P2-6/P2-4 (updated to
   real half-attendant arithmetic)/P1-5/P2-9/P2-8, frontend P1-7 (6 tests) and P1-10 (2 tests).
   Also: misleading parser comments corrected to match the shipped null→empty semantics, and the
   README now documents the Tuesday frontend mutation campaign.

Gates after remediation: `ruff check`, `ruff format --check`, and `mypy --strict` clean; full
backend suite green (4,659 passed, 4 skipped); full frontend suite green except one pre-existing,
environment-specific failure (`screening-check-dialog.test.tsx` "creates the batch lazily…" fails
deterministically under the local Node v24.8.0 — including with HEAD's own unmodified test and
component — and passes under both local Node v22 installs; it predates both the fix campaign and
this remediation, and looks like a Node-24.8.0 undici/happy-dom multipart regression rather than a
repository defect; CI pins `node-version: 24` and should be watched for it).
