# Test Integrity & Quality-Claims Audit (2026-10-01)

Auditor 10 of 10 — angle: TEST INTEGRITY. Do the tests actually test the
product, and are the quality claims in `MUTATION_TESTING_REPORT.md` true?
Every quantitative claim below was recomputed from the raw artifacts
(`backend/mutation/results.jsonl`, `frontend/mutation/{manifest,results.jsonl,
survivors.json,coverage-map.json}`, `backend/coverage.xml`, a live
`pytest --collect-only`), not taken from the reports.

## Executive summary

**Verdict: the quality claims are materially TRUE.** Every headline number in
the mutation reports reproduces exactly from the raw result files, the harness
is honest (zero no-op mutants, byte-exact/in-memory transforms, timeouts
verified separately from kills), the backend suite is 4,908 real
integration tests against real PostgreSQL via the real ASGI app (almost no
mocking), the frontend vitest suite has zero skips/onlys/snapshots and ~3.3
assertions per test, and CI gates every suite with coverage ratchets and no
`continue-on-error`. The 10 "equivalent" survivors I sampled from
`EQUIVALENTS.md` were verified against the source: 10 of 11 are sound (one is
contract-scoped, i.e. equivalent only under a documented input contract).

Findings are therefore Low/Info in severity: a stale umbrella report (root
`MUTATION_TESTING_REPORT.md` still cites the superseded 79.8% frontend
campaign), Playwright CI retries=2 that can absorb real flakes, a small set
of deliberately loose `status_code in (200, 422)` "never-a-500" pins, and
informational observations about operator-scope and CI placement of the
mutation harnesses. No Critical, no High.

Severity counts: **0 Critical, 0 High, 4 Low, 6 Info.**

## Findings

### [Low] Umbrella mutation report is one commit stale on the frontend numbers

Location: `MUTATION_TESTING_REPORT.md:13-23` vs
`frontend/mutation/CAMPAIGN.md:11-21`, commit `b1abd42`.

Evidence:

```
# MUTATION_TESTING_REPORT.md ("Frontend — 2026-10-01 (latest)")
| Killed                | 7,869 + 2 confirmed hangs |
| Survived (Phase B…)   | 1,995 (of which app pages 1,749) |
| Mutation score        | 79.8% |
Closing suite: 5,000 tests / 304 files.

# recomputed from frontend/mutation/results.jsonl (last-write-wins by id):
KILLED 7,972 + TIMEOUT 2 + SURVIVED 1,892 + NO_COVERAGE 78 = 9,944
score = 7,974 / 9,866 = 80.8%   (matches CAMPAIGN.md exactly)
# commit b1abd42: "1,995 -> 1,892 survivors, 79.8% -> 80.8%"; suite 313 files / 5,026 tests
```

`git log -- MUTATION_TESTING_REPORT.md` shows its last touch was `d634e5c`;
the follow-up round `b1abd42` updated `CAMPAIGN.md`, `results.jsonl`,
`survivors.json`, `report.md` — but not the root doc that labels itself
"latest".

Impact: the repo's single umbrella quality document understates the current
frontend score and cites superseded artifact numbers; anyone auditing from
the root doc alone sees numbers that no longer match the committed raw files
(a claim-vs-artifact mismatch, though in the conservative direction).

Fix: regenerate the frontend section of `MUTATION_TESTING_REPORT.md` from
`mutate_report.mjs` output as part of the campaign closing checklist (same
step that updates CAMPAIGN.md).

### [Low] Playwright CI retries (2) can convert real flakes into green runs

Location: `frontend/playwright.config.ts:50`
(`retries: process.env.CI ? 2 : 0`).

Evidence:

```ts
timeout: 60_000,
workers: 1,
forbidOnly: !!process.env.CI,
retries: process.env.CI ? 2 : 0,
```

Impact: a test that fails twice and passes on the third attempt reports as
green in CI with only a reporter annotation; a genuinely racy product
behavior (the suite is full of timing-sensitive hydration/WebKit waits) can
therefore land on main as "passing". This is a disclosed, conventional
choice — `workers: 1`, `forbidOnly`, `trace: retain-on-failure` and the
failure-only HTML artifact upload are all present — but nothing forces anyone
to read the flaky-vs-failed split.

Fix: add `--retries` reporting to CI (e.g. fail the job on `flaky` status
via the JSON reporter, or surface a "flaky tests" count as a PR annotation),
or ratchet retries down as the suite stabilizes.

### [Low] A small cluster of deliberately loose status pins ("never a 500" only)

Location: `backend/tests/test_animals_bugs.py:121,136,151`;
`backend/tests/test_auth_gaps.py:64`; `backend/tests/test_feeding_gaps.py:84`
and ~6 more `status_code in (200, 201/422)` sites across the suite.

Evidence (`test_animals_bugs.py:112-121`):

```python
# POST /api/animals/{id}/weight with notes longer than 255 chars …
# Expected: 422 (schema bound) or the note stored (Text column) — never a 500.
resp = await client.post(..., json={"weight_kg": 20, "notes": "n" * 300}, ...)
assert resp.status_code in (201, 422)
```

Impact: each such test under-determines the contract — a change from 422 to
silent truncation-at-201 (or vice versa) passes unnoticed. The looseness is
documented inline in every case (the fixed bug was the 500), and the
surrounding files are otherwise exact-value, so this is a residual weakness,
not fake coverage.

Fix: where the product has since settled on one arm (schema max_length now
exists), tighten to the single expected status and keep the comment.

### [Low] One of the sampled "equivalent" survivors is contract-scoped, not mathematically equivalent

Location: `frontend/mutation/EQUIVALENTS.md:68-71` (class 12,
`m09869`), `src/lib/task-title.ts:70`.

Evidence:

```ts
if ((name === "date" || name.endsWith("_date")) && typeof value === "string" && ISO_DATE.test(value)) {
  return formatDate(value, language);
}
// mutant: `name === "date"` → `name !== "date"`
```

The flipped arm routes ANY non-`date`, non-`_date` template arg whose value
matches `ISO_DATE` through `formatDate`. EQUIVALENTS.md declares this dead
because "no taskGen template interpolates a bare `{date}`" — true of today's
backend catalog, but the equivalence depends on a cross-repo data contract
(future backend templates), unlike the other 10 classes I verified, which
are runtime-mathematical (`sign-only multipliers`, `UUID entropy bits`,
`0.5 never equals an integer farm id`, `URLSearchParams strips one leading
'?'`, RFC-shape token masking).

Impact: low — the behavior is benign formatting and the dependency is
documented; but the class reads stronger than it is.

Fix: mark class 12 "contract-scoped (backend taskGen catalog)" in
EQUIVALENTS.md, or pin the catalog statically (assert the shipped template
keys) to promote it to a real equivalence.

### [Info] Mutation scores are over the executable-statement operator set (disclosed)

`backend/mutation/mutate_gen.py:15-21` excludes annotations, decorators,
string constants, `models/` DDL and `__init__` re-exports from the manifest;
`MUTATION_TESTING_REPORT.md:206-208` discloses "the score is a property of
this operator set". These exclusions are methodologically correct (annotation
mutants are unkillable by any test), but the 98.8%/80.8% numbers are not
directly comparable to a naive "mutate everything" Stryker run. The report
also correctly separates TIMEOUT (hang) verdicts and re-verifies them at a
1,200 s budget before counting 5 as kills.

### [Info] Mutation campaigns are offline artifacts, not CI-enforced

`.github/workflows/ci.yml` never invokes `mutation/` on either side; the
committed `results.jsonl`/`manifest.json` are point-in-time (backend source
unchanged since campaign commit `c7b6a4d` — verified via
`git diff --stat c7b6a4d HEAD -- backend/app` = empty; frontend manifest
shas: 126/126 files match the current tree). The scores can silently decay
as code changes; nothing flags when `manifest.json` no longer matches
`app/`/`src/`. (The frontend runner refuses to *run* on drift —
`mutate_run.mjs:319-324` — which is good, but nothing in CI checks it.)

### [Info] The only 4 backend skips are conditional and compensated

Suite-wide there are zero `pytest.mark.skip/xfail` decorators. The 4 skipped
tests (matching the report's "4,904 passed / 4 skipped", and
`pytest --collect-only` = 4,908) are parametrize branches at
`backend/tests/test_finance_extended.py:318,345` for the system-generated
finance categories, each covered by a dedicated rejection-pinning test
(`test_system_categories_reject_manual_rows`, same file, asserting the 422
message). Frontend: zero `test.skip/only/todo`, zero `describe.skip`, zero
`expect.soft`, zero snapshots across all 313 files.

### [Info] conftest's password-rotation hook can rewrite a 401 into a 200 — opt-in, probed

`backend/tests/conftest.py:106-199` (`_rotate_provisioned_password`): when
the `auto_rotate_worker_logins` fixture is attached, a worker-login 401 is
retried with the derived password and the response patched to 200 if that
succeeds. Masking risk is contained: the hook is opt-in (used in exactly one
test, `test_auth_extended.py:3807`), the default client never rewrites
(documented in-fixture), and the retry only fires for emails already rotated
in the same test, after probing the derived password against a fresh app.

### [Info] CI gating is complete; one suite-placement note on the mobile e2e spec

`.github/workflows/ci.yml` runs on every PR/push: migration-immutability,
backend (ruff+mypy strict incl. the mutation harness, OpenAPI snapshot,
alembic up/down/up, pytest with `fail_under = 92` coverage floor, pip-audit),
frontend (eslint, tsc, orval drift, `vitest run --coverage` with per-glob
thresholds, production build, pnpm audit), e2e (3-browser matrix, real
FastAPI+Postgres+production Next build), docker build+smoke. No
`continue-on-error` anywhere; the two `if: always()` uses are coverage
artifact uploads. The mobile/worker-tablet journeys run only in the Chromium
job (`playwright.config.ts` `testIgnore`/`testMatch`) — disclosed and
justified in-config (device emulation of the same engine). Branch-protection
required checks cannot be inferred from the repo (GitHub settings); all jobs
appear intended to gate.

## Coverage manifest

### Backend — all 126 test files (+ conftest) read

`conftest.py` — deep-read. Real PostgreSQL (`goatfarm_test`), schema via
`alembic upgrade head` subprocess (migration under test), per-test
`TRUNCATE … RESTART IDENTITY CASCADE` + reference reseed with a 5 s
lock-timeout bound, ASGI httpx client, DB-name safety refusal for non-`_test`
targets, opt-in auto-rotation hook. **Strong — this is the test world, and it
is the production schema, not a fixture schema.**

Read-depth key: deep-read = full/sectional read of file; body-read = test
bodies inspected; header+names = docstring + all test names + assertion
fingerprint (tests/asserts/raises counted programmatically). Every file was
opened; totals: 3,716 test functions, 13,809 `assert`s (3.7/test), 311
`pytest.raises`, 729 monkeypatch uses, unittest.mock in exactly 1 file
(`test_tasks_bugs.py:47`, a justified guard probe asserting `db.execute` is
never awaited).

| File | tests | asserts | raises | read depth | verdict |
|---|---|---|---|---|---|
| `test_account_tombstones.py` | 5 | 88 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_adversarial.py` | 44 | 216 | 0 | deep-read | strong (integration, real DB / documented unit scope) |
| `test_animal_profile_pagination.py` | 4 | 44 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_animals_bugs.py` | 16 | 52 | 0 | body-read | strong (integration, real DB / documented unit scope) |
| `test_animals_extended.py` | 184 | 657 | 2 | header+names | strong (integration, real DB / documented unit scope) |
| `test_animals_status_husbandry.py` | 23 | 72 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_audit_followups.py` | 8 | 52 | 2 | header+names | strong (integration, real DB / documented unit scope) |
| `test_audit_remediation.py` | 5 | 22 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_auth_bugs.py` | 15 | 42 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_auth_extended.py` | 197 | 627 | 2 | deep-read | strong (integration, real DB / documented unit scope) |
| `test_auth_gaps.py` | 2 | 9 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_auth_token_version_races.py` | 5 | 27 | 1 | header+names | strong (integration, real DB / documented unit scope) |
| `test_backward_planner.py` | 12 | 45 | 4 | header+names | strong (integration, real DB / documented unit scope) |
| `test_bakrid_advisory.py` | 4 | 16 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_breeding_bugs.py` | 18 | 80 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_breeding_extended.py` | 253 | 641 | 5 | header+names | strong (integration, real DB / documented unit scope) |
| `test_breeding_gaps.py` | 2 | 6 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_cadence.py` | 22 | 75 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_calibration_curve_math.py` | 8 | 30 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_chronology_mutation_gaps.py` | 3 | 0 | 3 | deep-read | strong (integration, real DB / documented unit scope) |
| `test_concurrency.py` | 25 | 106 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_concurrency_gaps.py` | 2 | 13 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_contract_drift.py` | 5 | 14 | 0 | body-read | strong (integration, real DB / documented unit scope) |
| `test_cull_price_calibration.py` | 3 | 18 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_daily_ops.py` | 55 | 201 | 17 | header+names | strong (integration, real DB / documented unit scope) |
| `test_daily_ops_api.py` | 14 | 44 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_dashboard_age_math.py` | 5 | 18 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_dashboard_permissions.py` | 5 | 44 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_dashboard_redteam.py` | 4 | 35 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_deployment_artifacts.py` | 91 | 637 | 10 | deep-read | strong (integration, real DB / documented unit scope) |
| `test_domain_audit_fixes.py` | 32 | 139 | 7 | header+names | strong (integration, real DB / documented unit scope) |
| `test_domain_audit_migration.py` | 5 | 41 | 2 | header+names | strong (integration, real DB / documented unit scope) |
| `test_domain_check_constraints.py` | 5 | 7 | 1 | body-read | strong (integration, real DB / documented unit scope) |
| `test_domain_chronology.py` | 7 | 26 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_e2e_lifecycle_audit.py` | 22 | 164 | 0 | deep-read | strong (integration, real DB / documented unit scope) |
| `test_e2e_scenario_gaps.py` | 16 | 54 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_engine_gaps.py` | 6 | 22 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_feed_quantity_numeric.py` | 3 | 31 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_feeding_extended.py` | 175 | 582 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_feeding_gaps.py` | 2 | 10 | 1 | body-read | strong (integration, real DB / documented unit scope) |
| `test_feeding_split_gaps.py` | 4 | 4 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_finance_bugs.py` | 16 | 72 | 3 | header+names | strong (integration, real DB / documented unit scope) |
| `test_finance_extended.py` | 162 | 549 | 0 | deep-read | strong (integration, real DB / documented unit scope) |
| `test_finance_gaps.py` | 5 | 18 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_finance_insurance.py` | 26 | 237 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_health_bugs.py` | 21 | 46 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_health_extended.py` | 191 | 600 | 1 | header+names | strong (integration, real DB / documented unit scope) |
| `test_health_gaps.py` | 3 | 16 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_health_note_gaps.py` | 3 | 8 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_health_safety.py` | 42 | 257 | 6 | header+names | strong (integration, real DB / documented unit scope) |
| `test_idempotency.py` | 49 | 341 | 8 | deep-read | strong (integration, real DB / documented unit scope) |
| `test_idempotency_gaps.py` | 1 | 9 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_inactive_animal_task_cleanup.py` | 6 | 25 | 1 | header+names | strong (integration, real DB / documented unit scope) |
| `test_index_drop_parity.py` | 4 | 6 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_input_bounds.py` | 25 | 89 | 1 | body-read | strong (integration, real DB / documented unit scope) |
| `test_jsonb_columns.py` | 6 | 19 | 2 | header+names | strong (integration, real DB / documented unit scope) |
| `test_jwt_rotation.py` | 6 | 12 | 3 | header+names | strong (integration, real DB / documented unit scope) |
| `test_kidding_due_pagination.py` | 3 | 27 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_kidding_gaps.py` | 3 | 16 | 0 | body-read | strong (integration, real DB / documented unit scope) |
| `test_kidding_husbandry.py` | 10 | 56 | 1 | header+names | strong (integration, real DB / documented unit scope) |
| `test_kidding_suffix_gaps.py` | 1 | 2 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_lifecycle_attribution_gaps.py` | 3 | 11 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_lifecycle_gaps.py` | 7 | 46 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_lock_order_hardening.py` | 30 | 153 | 0 | deep-read | strong (integration, real DB / documented unit scope) |
| `test_logic.py` | 22 | 148 | 0 | deep-read | strong (integration, real DB / documented unit scope) |
| `test_metrics.py` | 9 | 32 | 1 | deep-read | strong (integration, real DB / documented unit scope) |
| `test_monetized_cull_migration.py` | 1 | 9 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_notifications.py` | 30 | 92 | 4 | deep-read | strong (integration, real DB / documented unit scope) |
| `test_notifications_mutation_gaps.py` | 8 | 28 | 1 | header+names | strong (integration, real DB / documented unit scope) |
| `test_online_index_recovery.py` | 1 | 9 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_ops.py` | 98 | 236 | 38 | header+names | strong (integration, real DB / documented unit scope) |
| `test_ops_migration_integrity.py` | 7 | 64 | 5 | header+names | strong (integration, real DB / documented unit scope) |
| `test_owner_overview.py` | 6 | 49 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_password_capacity.py` | 10 | 42 | 2 | header+names | strong (integration, real DB / documented unit scope) |
| `test_planner_api.py` | 20 | 96 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_postgres_text_regressions.py` | 6 | 8 | 3 | header+names | strong (integration, real DB / documented unit scope) |
| `test_prefarm_idempotency_migration.py` | 5 | 46 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_purchase_batch_bulk.py` | 14 | 110 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_query_performance.py` | 12 | 126 | 0 | deep-read | strong (integration, real DB / documented unit scope) |
| `test_ratelimit_sweep.py` | 7 | 24 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_rbac.py` | 21 | 112 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_rbac_exhaustive.py` | 4 | 7 | 0 | body-read | strong (integration, real DB / documented unit scope) |
| `test_redteam_domain_fixes.py` | 21 | 81 | 2 | header+names | strong (integration, real DB / documented unit scope) |
| `test_redteam_remediation_2026_09_04.py` | 20 | 75 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_redteam_spine_fixes.py` | 18 | 51 | 3 | header+names | strong (integration, real DB / documented unit scope) |
| `test_retention.py` | 8 | 27 | 3 | header+names | strong (integration, real DB / documented unit scope) |
| `test_retention_mutation_gaps.py` | 2 | 7 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_schema_parity.py` | 16 | 69 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_scoped_picker_lookups.py` | 2 | 62 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_screening.py` | 115 | 468 | 24 | deep-read | strong (integration, real DB / documented unit scope) |
| `test_screening_provider_gaps.py` | 6 | 9 | 2 | body-read | strong (integration, real DB / documented unit scope) |
| `test_security_events.py` | 5 | 21 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_security_hardening.py` | 77 | 271 | 16 | header+names | strong (integration, real DB / documented unit scope) |
| `test_security_module_gaps.py` | 3 | 15 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_security_mutation_gaps.py` | 5 | 15 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_security_workflow.py` | 3 | 15 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_settings_defaults.py` | 5 | 17 | 0 | deep-read | strong (integration, real DB / documented unit scope) |
| `test_simulation_advanced.py` | 50 | 232 | 4 | header+names | strong (integration, real DB / documented unit scope) |
| `test_simulation_api.py` | 53 | 271 | 5 | header+names | strong (integration, real DB / documented unit scope) |
| `test_simulation_audit_remediation.py` | 22 | 79 | 3 | header+names | strong (integration, real DB / documented unit scope) |
| `test_simulation_deep_mutation.py` | 71 | 223 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_simulation_engine.py` | 144 | 537 | 20 | deep-read | strong (integration, real DB / documented unit scope) |
| `test_simulation_explain.py` | 32 | 207 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_simulation_finance_mutation.py` | 18 | 58 | 2 | header+names | strong (integration, real DB / documented unit scope) |
| `test_simulation_financials.py` | 81 | 233 | 4 | header+names | strong (integration, real DB / documented unit scope) |
| `test_simulation_fuzz.py` | 10 | 47 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_simulation_goat_domain.py` | 25 | 67 | 2 | header+names | strong (integration, real DB / documented unit scope) |
| `test_simulation_planner.py` | 23 | 74 | 2 | header+names | strong (integration, real DB / documented unit scope) |
| `test_simulation_redteam.py` | 11 | 15 | 8 | header+names | strong (integration, real DB / documented unit scope) |
| `test_simulation_vocabulary.py` | 3 | 8 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_smoke.py` | 2 | 3 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_strict_inputs.py` | 11 | 8 | 14 | body-read | strong (integration, real DB / documented unit scope) |
| `test_task_visibility_parity.py` | 6 | 3 | 0 | deep-read | strong (integration, real DB / documented unit scope) |
| `test_tasks_bugs.py` | 10 | 48 | 0 | deep-read | strong (integration, real DB / documented unit scope) |
| `test_tasks_extended.py` | 205 | 575 | 0 | body-read | strong (integration, real DB / documented unit scope) |
| `test_tasks_gaps.py` | 2 | 11 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_team_bugs.py` | 18 | 69 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_team_extended.py` | 104 | 432 | 1 | header+names | strong (integration, real DB / documented unit scope) |
| `test_tenancy_matrix.py` | 8 | 31 | 0 | deep-read | strong (integration, real DB / documented unit scope) |
| `test_tenant_foreign_keys.py` | 5 | 12 | 3 | header+names | strong (integration, real DB / documented unit scope) |
| `test_tenant_history_pagination_indexes.py` | 2 | 5 | 0 | header+names | strong (integration, real DB / documented unit scope) |
| `test_totp.py` | 46 | 181 | 1 | deep-read | strong (integration, real DB / documented unit scope) |
| `test_totp_rfc_gaps.py` | 5 | 9 | 0 | deep-read | strong (integration, real DB / documented unit scope) |
| `test_unit_bugs.py` | 9 | 9 | 2 | header+names | strong (integration, real DB / documented unit scope) |
| `test_unit_extended.py` | 192 | 201 | 52 | deep-read | strong (integration, real DB / documented unit scope) |
| `test_worker_pin_auth.py` | 22 | 94 | 1 | header+names | strong (integration, real DB / documented unit scope) |

Notes on standout files: `test_deployment_artifacts.py` (91 tests) executes
the real backup/restore shell scripts against mock executables (ordering,
cleanup, failure behavior — not grep-the-script); `test_tenancy_matrix.py`
walks every mounted route and proves the tenant fence both directions, with
an anti-blindness `assert len(routes) >= 80`; `test_rbac_exhaustive.py`
asserts no route ships without a permission guard;
`test_task_visibility_parity.py` cross-checks two independent visibility
engines over every DB-reachable combination;
`test_query_performance.py` compares SQL aggregates to an independently
written Python aggregation over ORM rows (golden, not tautological);
`test_simulation_engine.py` derives golden values from hand math documented
in the module docstring (`8.5 * s**5 * 1.6 * 0.98`), never by calling the
engine; `test_settings_defaults.py` freezes defaults as literal tables
(expected values hard-coded, not re-derived from the settings object — no
tautology); `test_totp_rfc_gaps.py` pins RFC 4226 Appendix-D vectors.

### Frontend vitest — 313 files scanned, 31 sampled in depth

Programmatic scan of all 313 `*.test.ts(x)` files: 4,364 `it/test` cases,
14,312 `expect()` calls (3.3/test), **zero** files with fewer expects than
tests, zero empty-body tests, zero `skip/only/todo`, zero snapshots, zero
`expect.soft`. 348 `vi.mock` uses across 225 files target infrastructure
(`next/navigation` 203, `sonner` 85, auth-context 14, api-client 11) — API
responses go through MSW (`vitest.setup.ts` boots the worker with
`onUnhandledRequest: "error"`, so an unmocked endpoint fails the test, and
`cleanup()` + storage clears run after every test).

Sampled in depth (13 full/sectional + 18 header+density):

| File | lines | expects | note |
|---|---|---|---|
| `src/lib/api-client.test.ts` | 431 | 55 | stubs global fetch directly to count calls/headers precisely |
| `src/lib/auth-context.races.test.tsx` | 1,193 | 96 | regression pair w/ documented bug mechanics (epoch races) |
| `src/lib/auth-context.test.tsx` | 999 | 123 | drives real provider; localStorage + X-Farm-Id propagation |
| `src/lib/idempotent-request.test.ts` | 1,260 | 182 | `catchError` helper then asserts error shape (no swallow) |
| `src/lib/offline-queue.boundaries.test.ts` | 208 | 31 | exact 72 h TTL, 256 KiB cap, Retry-After arithmetic |
| `src/lib/format.test.ts` / `utils.test.ts` | 97/159 | 36/32 | exact expected strings (`₹12,34,567.50`) |
| `src/lib/image-deps-guard.equals.test.ts` | 20 | 4 | kills the infinite-loop `<`→`<=` mutant |
| `src/lib/task-title.nulls.test.ts` | 38 | 4 | null template arg never renders "null" |
| `src/components/screening-check-dialog.test.tsx` | 1,027 | 94 | batch minting invariants; presigned POST end-to-end |
| `src/components/account-dialog.totp.test.tsx` | ~400 | 40+ | TOTP lifecycle (redundant-but-harmless `toBeTruthy` after `getByText`) |
| `src/components/account-dialog.aria.test.tsx` | 260 | 16 | aria-invalid/describedby wiring, mutation-campaign gap file |
| `src/components/animal-picker.falsy-props.test.tsx` | 165 | 8 | `""` is a distinct cache key (falsy-prop semantics) |
| `src/hooks/use-mobile.test.tsx` | — | — | RawProbe keeps `false` distinguishable from `undefined` |
| `src/test/adversarial/adv-sw-update.test.ts` | — | — | EXECUTES public/sw.js against stubbed browser APIs |
| `src/test/adversarial/adv-B-authorization.test.tsx` | 137 | 11 | fail-closed permissions rendering |
| `src/app/(app)/simulation/page.events.test.tsx` | 2,355 | 246 | events editor + NaN-never-reaches-assumptions guards |
| `src/app/(app)/tasks/page.campaign.test.tsx` | 1,511 | 165 | mutation-campaign kills; MSW handler records create payloads |
| `src/app/(app)/tasks/page.board-copy.test.tsx` | 1,098 | 119 | toast/alert clearing on retry; `catch {}` only inside MSW handler for bodyless actions |
| `src/app/(app)/owner/page.dom-caps.test.tsx` | 135 | — | 1000:100:1 ranking weights; boundary fixtures each flip under exactly one weight mutation |
| `src/app/(app)/planner/page.mutation2.test.tsx` | 1,589 | 246 | documents provably-equivalent mutants in the header |
| `src/app/(app)/animals/[id]/page.test.tsx` | 2,061 | 324 | profile page: history cards, RBAC gating |
| `src/app/(app)/team/page.extended.test.tsx` | 2,097 | 220 | dialogs, validation, payload mapping |
| `src/app/(app)/health/page.extended.test.tsx` | 2,003 | 217 | per-scope zod validation |
| `src/app/(app)/breeding/page.extended.test.tsx` | 1,210 | 160 | ultrasound flow |
| `src/app/(app)/kidding/page.extended.test.tsx` | 1,177 | 125 | days-late math |
| `src/app/(app)/finance/page.extended.test.tsx` | 1,182 | 151 | filters wired to query params |
| `src/app/(app)/feeding/page.extended.test.tsx` | 1,075 | 137 | 40/20/40 split table |
| `src/app/(app)/dashboard/page.test.tsx` | 871 | 112 | mocked GET, empty farm state |
| `src/app/(app)/health/page.dialog-state-ownership.test.tsx` | 1,012 | 69 | stale-dialog-state ownership |
| `src/app/(app)/ops-simulation/page.format-kg.test.tsx` | 134 | 2 | whole-kg 0 decimals / fractional exactly 1 |
| `src/app/auth-pages.dom-caps.test.tsx` | 67 | 9 | schema-limit DOM caps incl. driven TOTP field |

### E2E (Playwright) — all 24 specs + helpers + global-setup + config read

49 tests, 423 `expect()` (8.6/test), zero skips/fixme/only. Real stack
(production Next build + uvicorn + PostgreSQL in CI; webServer blocks with
120 s timeouts). `global-setup.ts` provisions a fresh user+farm via the real
API per run (no hardcoded accounts). `helpers.ts` interacts by role/label
with strict-mode-safe regexes and asserts visibility/URLs. Two specs stub at
the network layer, both documented and legitimate:
`screening.spec.ts:131-157` (S3 + vision provider don't exist in e2e; the
stub asserts derived UI figures like "30%"/"1.5s" from fixture data — mock
shapes mirror the OpenAPI models, e.g. `confidence: "0.87"` as string) and
`ops-simulation.spec.ts:73` (`route.abort()` used to PROVE the client-side
guard prevents the request: `expect(runRequests).toEqual([])`).
`api-contract-*.spec.ts` hit the real backend through the Next proxy with
orval-generated types.

### Configs

- `vitest.config.ts` — include `src/**/*.test.{ts,tsx}` (nothing excluded
  but tests/generated/test-utils); coverage thresholds global
  (90/87/90/90) + per-glob floors incl. single-file floors for former 0%
  files; `maxWorkers: 2`; CI timeout headroom documented.
- `vitest.setup.ts` — MSW `onUnhandledRequest: "error"`, cleanup, storage
  clear (both realms), idempotency state reset. Strong.
- `vitest.libcore/components.config.ts` — harness-only, deliberately scoped
  to dedicated tests (STRICTER, disclosed: "a mutant that some page test
  happens to kill incidentally is now reported as survived"). Not used by
  `pnpm test`.
- `vitest.mutation.config.ts` — harness-only, in-memory transform plugin,
  per-process cache dir, coverage disabled. Not a release gate (correct).
- `playwright.config.ts` — see Low finding on retries; otherwise strict.
- `backend/pyproject.toml` — `--strict-markers --strict-config`, session
  loop scope, `fail_under = 92` with greenlet-aware tracing documented.
- `.github/workflows/ci.yml` — all suites gate (see Info finding).

## Mutation-claims verification (recomputed vs claimed)

### Backend (`MUTATION_TESTING_REPORT.md` vs `backend/mutation/results.jsonl`)

| Metric | Claimed | Recomputed | Match |
|---|---|---|---|
| Mutants (manifest) | 6,565 | 6,565 (manifest.json; 6,565 unique ids in results) | YES |
| Killed | 6,043 | 6,043 KILLED | YES |
| Confirmed hangs | 5 | 5 TIMEOUT (verify_extremes.jsonl: 75 records, 74 SURVIVED re-verified + 1 killed) | YES |
| Survived | 74 | 74 SURVIVED | YES |
| Not covered | 443 | 443 NOT_COVERED | YES |
| Score (covered) | 98.8% (98.79%) | (6043+5)/6122 = **98.79%** | YES |
| Suite | 4,904 passed / 4 skipped | `pytest --collect-only` = **4,908** collected (= 4,904+4); skips located at `test_finance_extended.py:318,345` | YES |
| Coverage | 92.52% | committed `coverage.xml` recomputes to lines 94.30%, branches 85.99%, **combined 92.54%**; pyproject floor 92 | YES |

Harness honesty checks performed:

- **No-op mutants: 0.** All 6,565 manifest entries have
  `mut_stmt != orig_stmt`; 300 sampled `mut_stmt`s parse as valid Python.
  Manifest statement spans align with current source (differences are
  `ast.unparse` normalization: dropped comments/parens/underscored ints —
  and `backend/app` is byte-unchanged since the campaign commit `c7b6a4d`).
- **Timeout ≠ killed by default.** `mutate_run.py` records TIMEOUT
  separately; `mutate_verify_extremes.py` re-ran every TIMEOUT at 1,200 s
  (9 slow-but-finite → KILLED, 5 true hangs), and every final SURVIVED
  against its full cap-400 selection (74 stood, 1 more killed — the
  appended record is in `verify_extremes.jsonl`).
- **Restore integrity.** `mutate_run.execute` restores original bytes and
  re-hashes (raises on mismatch); `PYTHONDONTWRITEBYTECODE=1` documented as
  a live-observed `.pyc` contamination fix; per-file locks; ad-hoc
  `Runner.execute` cross-contamination incident documented and the run
  discarded.
- **Report math.** `mutate_report.py` computes the score from
  `results.jsonl` joined to the manifest; results file is deduped by id
  (last-write-wins), 6,565 records = 6,565 ids (no superseded leftovers).

### Frontend (`CAMPAIGN.md` vs `frontend/mutation/results.jsonl`)

| Metric | Claimed (CAMPAIGN.md) | Recomputed | Match |
|---|---|---|---|
| Mutants | 9,944 over 126 files | 9,944 unique ids / 126 fileMeta entries | YES |
| Killed by tests | 7,972 | 7,972 KILLED (last-write-wins) | YES |
| Killed by timeout | 2 | 2 TIMEOUT | YES |
| Survived | 1,892 | 1,892 SURVIVED | YES |
| No coverage | 78 | 78 NO_COVERAGE | YES |
| Score (covered) | 80.8% | 7,974/9,866 = **80.8%** | YES |
| Root doc instead says | 7,869 killed / 1,995 / 79.8% | stale (pre-`b1abd42`) | **NO — see Low finding** |

12,356 raw records → 9,944 final by id (re-verification rounds append;
`mutate_reverify.mjs`/runner append then dedupe). Manifest shas match the
current tree 126/126 (zero drift). 0 no-op edits (every `edits[].text`
differs from the span it replaces; reapplying the edits reproduces a
different file). Survivor distribution: app pages 1,646 / lib 166 /
components 77; by op: intconst 806, binop 701, compare 167 — consistent
with CAMPAIGN's operator table and the "app pages dominate" narrative.

### EQUIVALENTS.md sample-verification (11 survivors, code read)

| Survivor | Site | Claim | Verdict |
|---|---|---|---|
| m00006 | `custom-instance.ts:35` `slice(queryStart + 1)` → `+0` | URLSearchParams strips one leading `?` | **Sound** (spec-verified; in-source annotated; contract: urls start `/api`, no `??`) |
| m00886 | `animals/page.tsx:1437` `dir = asc ? 1 : -1` → `2` | dir is sign-only multiplier | **Sound** (all 3 uses are `x * dir` comparators) |
| m07500/m07504 | `account-dialog.tsx:77/106` `dialogEpoch` 0→1 / +=1→2 | opaque equality-only epoch | **Sound** (refs die with instance; Stryker-annotated) |
| m08230 | `screening-check-dialog.tsx:217` `+→-` in `sum + count` | sign swap preserves `total === 0` | **Sound** (counts are non-negative; gate behind disabled button) |
| m08819 | `auth-context.tsx:142` `&&→||` on `isSafeInteger && >0` | garbage never equals an integer farm id | **Sound** (consumer is equality vs integer ids) |
| m09130 | `format.ts:56` `te-IN`/`en-IN` swap | CLDR: te defaults to latn digits + Indian grouping | **Sound** (CLDR default numbering for `te` is latn) |
| m09367/77 | `idempotent-request.ts:330-331` mask `0x0f→0x0e`, `0x3f→0x3e` | version/variant from OR masks; cleared bit is entropy | **Sound** (v4 `| 0x40`, variant `| 0x80` intact) |
| m09643/77 | `offline-queue.ts:288/322` `continue→break` | stopped-guard skips the rest anyway | **Sound** (`if (stopped) continue` precedes) |
| m09869 | `task-title.ts:70` `===→!==` on `name === "date"` | dead arm: no template interpolates `{date}` | **Contract-scoped** (see Low finding) |

## Positive observations

1. **The claims survive adversarial recomputation.** Every number in both
   mutation reports — including the awkward ones (443 not-covered, 5 hangs,
   1,892 survivors) — reproduces exactly from the committed raw files. The
   umbrella doc's only error is being one commit stale, in the conservative
   direction.
2. **The backend suite is the product's second implementation of the
   schema.** conftest runs `alembic upgrade head` as a subprocess (the
   migration chain is itself under test), truncates per test, and refuses
   non-`_test` DB names. Integration through the real ASGI app with real
   PostgreSQL is the default; `unittest.mock` appears in exactly one file
   for one justified guard probe.
3. **Golden tests are derived independently, not round-tripped.** The
   simulation engine's expected values come from hand math in the docstring;
   `test_query_performance` compares SQL aggregates against an independently
   written Python aggregation; `test_settings_defaults` freezes literals.
   No expected-value-computed-by-the-code-under-test pattern was found.
4. **Exhaustive matrices with anti-blindness guards.** `test_tenancy_matrix`
   walks every mounted route both directions and asserts the walker found
   ≥80 routes ("the walker has gone blind, so this matrix is auditing far
   less than it claims"); `test_rbac_exhaustive` proves no unguarded route;
   `test_task_visibility_parity` cross-checks twin engines over every
   DB-reachable combination; `test_contract_drift` pins the OpenAPI snapshot
   (also CI-enforced).
5. **Concurrency is tested deterministically, not by luck.**
   `test_lock_order_hardening` parks transactions at lock boundaries and
   waits for `pg_locks` to report the waiter; `test_idempotency` uses two
   ASGI clients (two sessions) with a test-delayed claim transaction;
   `test_concurrency`/`test_concurrency_gaps` pin exact final states.
6. **Mutation-driven gap tests are mutation-aware by design.** The gap files
   state which mutant they kill and why the fixture flips under exactly one
   operator (e.g. owner ranking 1000:100:1 boundary fixtures, the
   `<`→`<=` infinite-loop comparator kill, RFC 4226 vectors, behavioral
   chronology scoping replacing a SQL-text grep).
7. **Frontend test hygiene is enforced at the setup level.** MSW
   `onUnhandledRequest: "error"` turns any unmocked network call into a
   failure; per-test cleanup/storage clearing; `forbidOnly` in CI on the
   e2e side; coverage floors per glob so no page can ship at 0%.
8. **Harness self-skepticism.** Both runners include self-checks (smoke
   mode verifies the transform applies and is deterministic; backend
   runner refuses to run on manifest drift) and the docs record discarded
   runs (the 2026-09-30 cross-contamination incident) rather than hiding
   them.
