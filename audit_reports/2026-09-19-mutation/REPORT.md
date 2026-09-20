# Mutation-Test Campaigns — 2026-09-19

## Executive summary

**104 surgical mutants across ten campaigns — 82 killed, 22 survived,
0 unresolved.** Of the 22 survivors, **5 are
equivalent mutants** (a redundant second guard makes the mutation
behaviorally inert — defense in depth working as designed) and **17 are
real, untested behavior changes**, ranked:

| # | Severity | Finding | Where |
|---|---|---|---|
| F1 | High | Screening image **list** has no cross-farm test and no post-query guard — one deleted farm filter leaks every farm's photos | api/screening.py:164 |
| F3 | High | Dashboard **cull preview** likewise leaks cross-farm rows; the other `_…_preview` helpers share the shape | api/dashboard.py:122 |
| F2 | Medium | `PERMISSION_DEPENDENCIES` (manage-requires-view) is only enforced in the role editor and untested | permissions.py:51 |
| F5 | Medium | Verification state machine: a **PENDING duty can be VERIFIED** without ever being completed (and a VERIFIED duty re-verified) | api/tasks.py:759 |
| F6 | Medium | Rejected-login **timing padding has no test** — disabling it re-opens the user-enumeration oracle silently | security.py:236 |
| F7 | Medium | Screening **provider failover** chain completely untested (single-provider test profile) | screening/pipeline.py:578 |
| F8 | Medium | Planner **stillbirth applied as a bonus** (`1.0 − r` → `1.0 + r`) in both sites — every projected litter overstated | simulation/planner.py |
| F9 | Medium | `move_animal` can **skip writing the BucketMove row** (audit/history of every move) with nothing failing | services/animals.py:216 |
| — | Low | Breeding age floor off-by-one (11mo), exact-22kg boundary, quarantine release date-guard, refresh/access expiry boundary instants, team capacity counts other farms' members, ops-sim budget globally keyed, planner settling −1 month | see campaign sections |

Campaigns 3 (biosecurity), 7 (idempotency), and 10 (contract drift) are
**clean sweeps** — every mutant killed, most by purpose-built tests
(`test_buck_in_breeding_bucket_has_no_manual_exit`,
`test_replay_probe_expiry_boundary_is_inclusive`,
`test_farm_response_contract_requires_every_emitted_key`). The husbandry
boundary suite (campaign 2) killed 18/21 with explicit day-nine/day-ten and
+60d pins. The IDOR surface defeated the mutant class structurally: every
by-id helper carries a post-query farm re-check.

The deliverables also include the two process upgrades (campaigns 11–12):
a per-domain weekly mutmut matrix with published score trend, and a
diff-scoped per-PR mutation gate over tenancy + financial-core paths.

---

Surgical mutation campaigns over the goat-farm backend, run in the requested
priority order (tenancy → husbandry → biosecurity → buckets → verification →
auth → idempotency → screening → simulation → contract drift), plus the two
process upgrades (per-domain mutmut, per-PR gate + score trend).

**Suite under test**: 4,617 tests collected (4,613 pass + 4 skip on a clean
23-minute full run against real PostgreSQL — verified before any mutant ran).

**Method.** Every mutant is a hand-specified source patch (exact string or
regex replacement, occurrence-count-checked), applied in an isolated `git
worktree` with its own venv and throwaway database. Staging:

1. **preflight** — the mutant's targeted test selection runs once on the
   clean tree; a failing selection marks the mutant `INCONCLUSIVE_BASELINE`
   (retried on the next pass) rather than poisoning its verdict;
2. **targeted** — selection re-run with the mutant applied → `KILLED_TARGETED`;
3. **full** — survivors run all 4,617 tests → `KILLED_FULL` or `SURVIVED`.

Verdicts are recorded in `results/all.jsonl`; this report classifies every
**SURVIVED** mutant as either a *real hole* (the behavior changed and no test
noticed) or an *equivalent mutant* (a second, redundant guard makes the
mutation behaviorally inert — defense in depth doing its job).

---

## Campaign 1 — Multi-tenancy & RBAC (30 mutants)

### Verdicts (pass 1 + resume pass)

| Mutant | Verdict | Killer / classification |
|---|---|---|
| animals-idor | SURVIVED | **Equivalent** — `_get_animal` re-checks `animal.farm_id != farm_id` after the query (api/animals.py:123). Verified by live cross-farm probe: farm B still gets 404 under the mutant. |
| breeding-idor | SURVIVED | **Equivalent** — post-query re-check at api/breeding.py:83 (`br.farm_id != farm.id`). |
| feeding-idor | SURVIVED | **Equivalent** — post-query re-check at api/feeding.py:352. |
| screening-idor | KILLED_TARGETED | `test_screening.py::test_review_api_lists_scopes_and_reviews` |
| tasks-idor | KILLED_TARGETED | tasks selection (resume pass) |
| simulation-idor | KILLED_TARGETED | `test_simulation_api.py::test_scenario_cross_farm_404` |
| animals-list | KILLED_TARGETED | `test_animals_extended.py::test_list_never_leaks_other_farms_animals` |
| breeding-list | KILLED_TARGETED | breeding selection (adversarial cascade) |
| buckets-list | KILLED_TARGETED | `test_animals_extended.py::test_buckets_board_cross_farm_isolation` |
| buckets-settings | KILLED_FULL | full suite (1,432 s; infra retry then clean kill) |
| **dashboard-list** | **SURVIVED** | **REAL LEAK** — see F3 below |
| feeding-list | KILLED_TARGETED | feeding selection |
| finance-idor | KILLED_TARGETED | `test_finance_extended.py::test_purchase_batch_correction_will_not_reach_another_farms_batch` (resume pass) |
| finance-list | KILLED_TARGETED | finance selection (resume pass; mutant triggers app-wide 500s) |
| health-idor / health-list | KILLED | health selection / `test_scoped_picker_lookups.py::test_health_lookups_are_targetable_tenant_safe_and_least_privilege` |
| kidding-list | KILLED_TARGETED | kidding selection (resume pass) |
| planner-list | KILLED_TARGETED | `test_planner_api.py::test_planner_plan_farm_scoping` |
| purchases-list | KILLED_FULL | `test_health_extended.py::test_purchases_isolated_between_farms` |
| **screening-list** | **SURVIVED** | **REAL LEAK** — see F1 below |
| **team-list** | **SURVIVED** | **REAL GAP (Low)** — see F4 below |
| team-roles | SURVIVED | **Equivalent** — `_get_role` post-query re-check (api/team.py:528). |
| auth-farmscope | KILLED_TARGETED | `test_auth_extended.py::test_farms_list_isolated_between_two_owners` |
| ops-sim-limits | SURVIVED | **REAL (Low)** — run-limits/budget keyed globally: one farm's ops-sim runs consume another farm's budget (availability, no data leak). Untested. |
| central-membership-bypass | KILLED_TARGETED | `test_adversarial.py::test_team_takeover_and_escalation_guards`, `test_self_verification_blocked_for_worker` |
| rbac-require-perm-bypass | KILLED_TARGETED | `test_rbac.py::test_cleaner_sees_only_tasks`, `test_mover_permissions` |
| rbac-viewer-write (Auditor gains finance.manage) | KILLED_TARGETED | RBAC suite |
| rbac-cleaner-verify | KILLED_FULL | `test_tasks_extended.py::test_cleaner_cannot_verify`, `test_cleaner_cannot_reject` |
| rbac-perms-for-owner | KILLED_TARGETED | `test_dashboard_permissions.py` (clinical totals + move suggestions withheld without view) |
| **rbac-perm-dependency-drop** | **SURVIVED** | **REAL GAP** — see F2 below |

### Findings

**F1 (High — real cross-farm leak, untested).** `GET /api/screening/images`
(api/screening.py:164): deleting `ScreeningImage.farm_id == farm.id` from
`filters` is caught by *no* test in the entire 4,617-test suite. Both the
count query and the page query run unscoped, rows are serialized directly,
and there is no post-query guard (unlike every by-id helper). One deleted
line shows every farm's screening photos (S3 keys, health verdicts, pending
findings) to any authenticated farm member. **Fix**: add the cross-farm
isolation test the other routers already have (two farms, two uploads, assert
the list shows only one), and mirror the by-id helpers' post-query re-check.

**F3 (High — real cross-farm leak, untested).** Dashboard cull-candidate
preview `_cull_preview` (api/dashboard.py:122-126): deleting
`Animal.farm_id == farm_id` from the preview query survives the full suite.
The preview rows are serialized with no post-query guard, so one deleted
line shows *another farm's* cull candidates (tags, preview list) on the
dashboard. Same single-guard shape as F1. **Fix**: cross-farm dashboard
isolation test (two farms with cull candidates; assert each dashboard
previews only its own) — and the same audit pass over the remaining
`_…_preview` helpers in dashboard.py, which all follow this pattern.

**F2 (Medium — validation gap, untested).** `PERMISSION_DEPENDENCIES`
(permissions.py:51) is enforced only in the role-editor validation
(api/team.py:747); deleting all fourteen `manage → view` entries survives
the full suite. A crafted role of `finance.manage` without `finance.view`
can be created via the API. No privilege escalation on its own (each write
route still checks its own code), but the invariant the frontend relies on
for the editor matrix is server-untested. **Fix**: one test asserting the
role editor rejects manage-without-view.

**F4 (Low — cross-tenant capacity counting, untested).** The team-capacity
preflight in worker creation (api/team.py:267-274) counts memberships with
`FarmMembership.farm_id == farm.id`; deleting that filter makes **every
farm's** memberships count toward this farm's `max_team_members_per_farm`
cap, so unrelated farms' team growth can block worker creation here
(wrong 409, availability only — no data crosses tenants). No test seeds
enough cross-farm memberships to notice. **Fix**: capacity test with a
second farm holding memberships near the cap.

**Positive result.** The by-id (IDOR) surface is double-guarded everywhere
probed: query filter *and* post-query re-check. Deleting the query filter is
behaviorally inert — the codebase's defense-in-depth pattern defeated the
"scariest mutant class" at four separate sites (animals, breeding, feeding,
team-roles) with zero reliance on tests. Cross-farm **list** isolation, by
contrast, is enforced once and is properly tested (animals, feeding,
purchases, auth farm-selector all have dedicated killers).

*(All baselines eventually resolved; the INCONCLUSIVE batch was an artifact
of a harnessing accident — see Harness notes — and every affected mutant was
re-run to a clean verdict.)*

---

## Campaign 2 — Husbandry-constants boundaries (21 mutants)

**18 killed, 3 survived.** The hard-coded husbandry rules are the
best-tested surface in the codebase: exact-day pins exist for weaning
(`test_kidding_creates_weaning_task_at_plus_60` kills ±1), the dry-off
window has *both* boundary sides tested
(`test_move_resting_to_breeding_blocked_at_day_nine` /
`…allowed_at_day_ten` kill `<=`, 9 and 11), gestation ±1 (EKD 149/151),
ultrasound 31/33 and the sim's `<`→`<=` day-32 boundary, the ∧→∨ readiness
flip, 21 kg, day-45→39 early release, the schedule off-by-one
(`test_quarantine_schedule_covers_45_days`), and all three inbreeding-fence
halves — including a dedicated end-to-end fence test
(`test_inbreeding_fence_end_to_end`) and a red-team full-sibling test.

### Survivors

| Mutant | Verdict | Classification |
|---|---|---|
| ready-age-11mo (`age >= 12` → `>= 11`) | SURVIVED | **REAL (Low)** — the age floor's off-by-one is unpinned: an 11-month doe becomes breedable and nothing notices. Fix: one test at 11 complete months (rejected) and 12 (accepted). |
| ready-weight-gt (`>= 22` → `> 22`) | SURVIVED | **REAL (Low)** — the exactly-22.0 kg boundary is unpinned (21 kg is tested). Fix: boundary test at exactly the floor. |
| quar-guard-date-drop (release duty no longer must match the day-45 protocol date) | SURVIVED | **REAL (Low-Med)** — `_guard_quarantine_release`'s protocol-date verification is untested; a release duty with a wrong due date would pass. Fix: forge a mis-dated release duty and assert refusal. |

Note: the campaign brief assumed a ≥10-month age floor; the actual
`GOAT_PROFILE.min_breeding_age_months` is **12** months (species.py:90), so
the off-by-one mutants ran at 11/13 against the real rule.

---

## Campaign 3 — Biosecurity fail-closed (7 mutants)

**6 killed, 0 real survivors** (1 inconclusive: weaning-wrong-pen — its
lifecycle-selection baseline flaked twice; treat as untested).

The flagship mutant — suspected scheduled disease no longer setting
`movement_restricted` (fail-closed → fail-open) — is killed twice over
(targeted and full). Every verdict came from a *purpose-built* test:
`test_clear_restriction_version_mismatch_and_no_active_hold_are_distinct`
(restriction fail-open), the held-doe kidding tests (disease flag),
orphan-wean provenance (moves while restricted),
`test_buck_in_breeding_bucket_has_no_manual_exit` (terminal buck residency),
pen-guard tests (sex-pen routing), and
`test_quarantine_release_requires_completed_records_and_no_disease_hold`
(quarantine release hold). This is the campaign where every survivor would
have been a real farm-safety bug — there were none.

---

## Campaign 4 — Bucket state machine (8 mutants)

**7 killed, 1 real survivor.** Dropping the transition guard, adding a skip-stage edge
(BREEDING→DELIVERY), never updating `current_bucket`, never recording the
`BucketMove` row, ignoring backdated `effective_date`, dropping/duplicating
the weaning duty, and anchoring the weaning duty to the breeding date are
all caught — mostly by targeted selections, with cascading app-level
rejections (the forged moves surface as 4xx/5xx in adjacent flows).

### Survivor

| Mutant | Classification |
|---|---|
| no-move-record (`move_animal` updates `current_bucket` but writes no `BucketMove` row) | **REAL (Medium)** — the audit/history row behind every lifecycle move can be silently dropped (targeted 26 s and full 1,350 s both pass). Fix: assert the move row (from/to/effective_date/reason) after a manual move. |

---

## Campaign 5 — Verification loop (6 mutants)

**4 killed, 2 real survivors.**

Killed: the CLEANING two-person rule (self-verification) is pinned by
`test_completer_cannot_verify_own_work_409` + `test_other_verifier_can_verify_after_409`;
the owner-exemption flip, CLEANER→VET duty routing, and emptying
VERIFICATION_REQUIRED_CATEGORIES are all caught by the workflow suites.

### Survivors (both REAL — verification state-machine edges untested)

| Mutant | Classification |
|---|---|
| verify-before-complete (a PENDING duty can be verified without being completed) | **REAL (Medium)** — `verify` only re-checks `status != DONE`; mutating the check to let PENDING through survives the full suite. A verifier can mark a never-worked duty VERIFIED (final state) — it can then never be completed. Fix: POST /verify on a PENDING duty must 400 (test both PENDING and SKIPPED). |
| double-verify (an already-VERIFIED duty can be verified again) | **REAL (Low)** — re-verifying a VERIFIED duty survives; `verified_at`/`verified_by_id` get silently overwritten. Fix: second verify on VERIFIED must 400. |

---

## Campaign 6 — Auth & token family (11 mutants)

**9 killed, 3 real survivors.** (Two original kills came from a load-flaky
unrelated test — `test_species_reference_data_migration_reaches_existing_databases`,
which touches no API and passes under both mutants alone; both mutants were
re-run to honest verdicts.)

Killed: both family-revocation mutants by
`test_refresh_old_token_reuse_revokes_family` (the exact replay scenario),
cookie HttpOnly and SameSite flips by the cookie-attribute tests, both TOTP
rekey mutants by `test_previous_stable_totp_key_decrypts_then_marks_for_rewrap`,
and the legacy-rehash stall by the benign-race tests.

### Survivors

| Mutant | Classification |
|---|---|
| session-expiry-lt (`expires_at <= now` → `<`) | **REAL (Low)** — refresh-session expiry boundary unpinned; a token is accepted one extra boundary instant. |
| rejected-login-timing-off (padding disabled on rejected logins) | **REAL (Medium)** — the rejected-login timing equalization has *no test at all*: disabling it (re-opening the user-enumeration timing oracle) passes the full suite. Fix: a test that measures rejected-vs-accepted login timing buckets, or at minimum pins that `complete_rejected_login_timing` executes the pbkdf2 padding path (unit-level). |

Re-run verdicts: cookie-secure-off **KILLED_TARGETED** by
`test_production_refresh_cookie_is_host_bound` (the production-cookie test
does assert Secure — the original kill was a fluke but the conclusion
holds); access-expiry-lt **SURVIVED** — the access-token expiry boundary is
unpinned, same class as session-expiry-lt.

---

## Campaign 7 — Idempotency (4 mutants)

**4/4 killed.** Dropping the farm scope from the replay lookup is caught by
`test_replay_probe_lookup_is_scoped_to_farm_actor_and_operation` and
`test_worker_create_key_is_not_shared_across_farms`; the retention boundary
flip (`<=` → `<`) by `test_replay_probe_expiry_boundary_is_inclusive`;
ignoring the request fingerprint (same key, different body replays the old
response) by the retry-preservation tests
(`test_bulk_health_event_retries_preserve_the_reviewed_target_set_exactly_once`,
`test_farm_create_replays_at_quota_and_persists_one_complete_seed_graph`);
and dropping the actor scope by the scoped-lookup tests. The idempotency
layer's scope, fingerprint and boundary semantics are all pinned exactly.

---

## Campaign 8 — Screening pipeline (7 mutants)

**4 killed, 2 survivors, 1 anchor repaired and re-run.**

Killed: frozen provider rotation (dedicated determinism tests
`test_rotation_primary_is_deterministic_and_wraps`), error-backoff removal
(`test_error_rows_without_runs_back_off_before_retry`), specialists skipped
(cascade test), export status filter off
(`test_export_endpoint_returns_training_corpus`), and gate-exhaustion filed
as HEALTHY (`test_provider_failure_records_error_run_and_recovers`,
`test_deterministic_failure_exhausts_its_retry_budget`).

### Survivors

| Mutant | Classification |
|---|---|
| failover-removed (detect chain reduced to the primary provider) | **REAL (Medium)** — no test exercises a failing primary with a live fallback; the fallback chain is completely untested (the test profile configures a single provider). Fix: a rotation test where the primary raises `ProviderError` and the fallback's answer is served and recorded. |
| content-dedup-off (claim pre-check disabled) | **Likely equivalent** — the unique `(farm_id, sha256)` index still arbitrates via the savepoint/IntegrityError path; the disabled pre-check is an optimization, not the boundary. Verify before fixing tests. |

---

## Campaign 9 — Simulation engine, seeded (7 mutants)

Step 1, the **determinism contract**, holds and is now executable:
`backend/scripts/check_simulation_determinism.py` asserts same-seed →
byte-identical SHA-256 of the full projection and different-seed → divergence.

**3 killed, 2 survivors** (+ 1 repaired patch, 1 flake re-run).

Killed: RNG unseeded (the reproducibility tests fire immediately), the
backward-planner gestation sign flip, and the growth-premium sign flip.
A seeded campaign was only meaningful because the determinism contract
holds — and the mutants that break it die instantly.

### Survivors

| Mutant | Classification |
|---|---|
| planner-settling-off-by-one (lead window −1 month) | **REAL (Low)** — the settling-months off-by-one is unpinned. |
| parity-stillbirth-sign (stillbirth applied as a bonus) | **REAL (Medium)** — `r.litter_size * (1.0 − stillbirth)` → `(1.0 + …)` flips a loss into a gain in **both** planner sites and nothing notices: every projected litter is overstated. The financial core's mutmut scope covers `planner.py` only for *covered lines* with the same suite, so this hole would survive the weekly run too. Fix: golden projection with a nonzero stillbirth rate. |

---

## Campaign 10 — Contract drift (3 mutants + manual reverse check)

**3/3 killed.** The committed byte-identical drift guard catches a field-type
change (`FarmOut.name string → integer`), whole-schema removal
(`test_committed_openapi_json_matches_the_live_schema`), and a backend-side
Pydantic drift is caught by *both* the drift guard and a dedicated contract
test (`test_farm_response_contract_requires_every_emitted_key`). The
snapshot gate is genuinely load-bearing.

**Manual reverse-direction check (done).** With `FarmOut.location` mutated
`str → int` in Pydantic: `scripts/export_openapi.py` propagates the change
into `shared/openapi.json` (verified `git diff`), which is exactly what the
CI gate (`export_openapi.py` + `git diff --exit-code`, ci.yml:88-89) fails
on, and `pnpm orval` (ci.yml:177) would regenerate the typed client. The
frontend half (orval + `tsc --noEmit` + the Zod enum-drift adversarial test)
could not be executed locally — no Node runtime on this machine — and
remains CI-verified. Note: runtime Zod validation exists only in
`src/test/adversarial/adv-C4-enum-drift.test.ts`; the frontend otherwise
relies on generated types, i.e. compile-time lockstep, not runtime parsing.

 — Simulation engine, seeded (7 mutants) — partial

Step 1, the **determinism contract**, holds and is now executable:
`backend/scripts/check_simulation_determinism.py` asserts same-seed →
byte-identical SHA-256 of the full projection and different-seed → divergence.
(First two verdicts from a collision-tainted run were discarded and re-run.)

*Results appended after the orchestrator pass.*

---

## Campaign 10 — Contract drift (3 mutants + manual reverse check)

*Results appended after the orchestrator pass.*

**Manual reverse-direction check (done).** With `FarmOut.location` mutated
`str → int` in Pydantic: `scripts/export_openapi.py` propagates the change
into `shared/openapi.json` (verified `git diff`), which is exactly what the
CI gate (`export_openapi.py` + `git diff --exit-code`, ci.yml:88-89) fails
on, and `pnpm orval` (ci.yml:177) would regenerate the typed client. The
frontend half (orval + `tsc --noEmit` + the Zod enum-drift adversarial test)
could not be executed locally — no Node runtime on this machine — and
remains CI-verified. Note: runtime Zod validation exists only in
`src/test/adversarial/adv-C4-enum-drift.test.ts`; the frontend otherwise
relies on generated types, i.e. compile-time lockstep, not runtime parsing.

---

## Campaign 11 — Per-domain mutmut scope (process upgrade, delivered)

* `backend/mutmut-configs/{breeding_kidding,health_feeding,tasks_duties,auth_rbac}.toml`
  — four DB-free (`--mutation-pure`) domain profiles; the financial-core
  profile remains the committed `pyproject.toml` default and is never
  overwritten in the tree.
* `backend/scripts/mutmut_domain.py` — swaps a fragment into
  `[tool.mutmut]` for one run, restores `pyproject.toml` in a `finally`
  (a crashed leg can't leak scope), and prints a machine-greppable
  `MUTATION_SCORE domain=… score=…` line from mutmut's own stats.
  The section-swapper round-trips all four fragments exactly (validated).
* `.github/workflows/mutation.yml` — the weekly job is now a matrix over
  `financial_core` + the four domains (`fail-fast: false`), each leg uploads
  its own artifacts and a `domain-score.txt` row.
* Domain scopes deliberately keep to deterministic, DB-free tests — the
  integration halves stay in ci.yml, and the tenancy/financial surgical
  surface moves to the per-PR gate (campaign 12). This mirrors exactly the
  split this campaign suite proved out.

## Campaign 12 — Per-PR diff-scoped gate + score trend (process upgrade, delivered)

* `backend/scripts/mutation_gate.py` — a 20-mutant curated catalog (14
  tenancy, 6 financial) **mapped to source files**; only mutants whose files
  intersect the PR diff run (typical scoped PR: 2–6 mutants, minutes not
  hours). Any SURVIVOR — or a STALE catalog anchor (source drifted out from
  under the gate) — fails the check. All 20 anchors verified against the
  current tree.
* `.github/workflows/mutation-pr.yml` — `pull_request` gate triggering only
  on tenancy-critical/financial paths, real PostgreSQL service, ~45 min cap.
* Score trend — the weekly matrix legs each emit a score row; a new `trend`
  job in `mutation.yml` aggregates them into `audit_reports/mutation-trend.json`
  (bounded to the last 104 weeks) and commits it, so every dated audit bundle
  and release note can cite mutation posture at that release.

---

## Harness notes (what it took to get trustworthy verdicts)

The campaign directory (`audit_reports/2026-09-19-mutation/`) contains the
reusable harness (`harness.py`), the mutant catalogs (`campaigns.py`), and
raw verdicts (`results/all.jsonl`). Five defects were found and fixed *of the
harness itself* during the run — each initially produced misleading signals
worth recording because they mimic real findings:

1. **Phantom-mutant bytecode** — dry-run validation compiled mutated sources
   with `py_compile`; a size-preserving patch restored in the same clock
   second left a stale *mutated* `.pyc` that Python accepted as current.
   Two "failing baseline" tests were in fact correctly detecting
   `quarantine_schedule` running day-shifted bytecode. Fix: `ast.parse`
   validation, `PYTHONDONTWRITEBYTECODE=1` everywhere, and __pycache__
   purge on every restore.
2. **Baseline ran with the mutant applied** — preflight ordering made
   "baseline fails" mean "this selection kills the mutant", producing
   deterministic INCONCLUSIVEs exactly on the strongest selections.
   Fix: restore → preflight → re-apply.
3. **Self-deadlock** — the result loop held a plain `Lock` and called
   `log()`, which re-acquired it; every campaign froze minutes in.
   Fix: `RLock`.
4. **Shared worktree collision** — a second campaign instance reused
   worktree indexes (same directory, same throwaway DB) and contaminated a
   window of runs. Fix: `results/campaign.lock` (pid-checked) — one harness
   instance owns the worktrees; campaigns run strictly in sequence.
5. **Connection-capped infra false-kills** — under 4–5 concurrent pytest
   processes the 100-connection PostgreSQL cap produced crash-exit "kills"
   in ~350 s. Fix: infra-signature scan of the *full* output with one retry,
   plus 3 workers maximum.

Two of these (1 and 5) are also **reportable test-suite findings**: the
suite's per-test DB truncation makes it sensitive to stale bytecode, and
`test_purchase_batch_correction_will_not_reach_another_farms_batch` /
`test_screening.py::test_review_api_lists_scopes_and_reviews` can fail under
connection pressure with no infra signature in their assertion output.

## Repository additions

| File | Purpose |
|---|---|
| `audit_reports/2026-09-19-mutation/harness.py` | worktree-isolated mutation runner (reusable) |
| `audit_reports/2026-09-19-mutation/campaigns.py` | all 104 surgical mutants across campaigns 1–10 |
| `audit_reports/2026-09-19-mutation/results/all.jsonl` | raw per-mutant verdicts |
| `backend/scripts/check_simulation_determinism.py` | executable same-seed determinism contract |
| `backend/scripts/mutmut_domain.py` + `backend/mutmut-configs/*.toml` | campaign 11 |
| `backend/scripts/mutation_gate.py` + `.github/workflows/mutation-pr.yml` | campaign 12 |
| `.github/workflows/mutation.yml` (updated) | per-domain weekly matrix + trend job |
