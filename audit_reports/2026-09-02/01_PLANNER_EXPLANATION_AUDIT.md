# Planner Explanation Layer — Independent Audit & Remediation (2026-09-02)

Scope: the changes answering "give me clear explanations of the recommendations,
and for the dairy verify the backward planning honours Wood's lactation curve —
milk falls in late pregnancy and the animal goes dry". Three independent
read-only auditors examined (1) the dairy biology and the new explanation
narrative, (2) the planner backend (backward planner + API + persistence),
and (3) the frontend and contract chain.

## Verdicts

| Audit | Scope | Verdict | Critical | Moderate | Minor |
|---|---|---|---|---|---|
| 01 Dairy biology & explanations | lactation.py, milk_planner.py, new `explanations` layer | FAIL → fixed | 0 | 2 | 6 |
| 02 Planner backend | backward_planner.py, api/planner.py, schemas, model/migration, _run_limits | FAIL → fixed | 0 | 4 | 4 |
| 03 Frontend & contract | planner page, generated client, nav/RBAC, tests | PASS (fixes applied anyway) | 0 | 2 | 9 |

## What was independently verified as sound

- **Wood's curve**: `lactation.py` implements Y(t)=a·t^b·e^(−ct), b=0.6 (published
  river-buffalo range 0.465–0.677), peak exactly at the configured day (65 for
  Murrah), monthly buckets summing to exactly `lactation_milk_litres`.
- **The dry period is real in the engine**: a new deterministic single-doe test
  (`test_single_doe_stops_milking_at_dry_off_in_late_pregnancy`) proves calvings
  at months 6/12/18, milk only in the 3 lactation months after each calving
  summing to exactly 300.0 L, and **exactly zero milk while pregnant** in the
  dry months 9–11 and 15–17. The milk planner's `_simulate` shares the same
  overlay conventions (`curve_from_assumptions` is the single construction point).
- **The steady-state identity**: freshenings/month × calving interval sizes the
  herd; milking share ≈ lactation length / calving interval (verified within 5%).
- Chain math, calendar round-trips, caller-object isolation, fills/echo/chains
  index alignment under duplicate targets, CPU pricing pass count (exactly
  2+9+risk_runs engine passes), 422 exception ordering, model↔migration parity,
  farm scoping and optimistic locking on all five `/plans` routes.
- Frontend: contract chain intact in both directions, month math matches the
  backend's `month_offset ≥ 2 and ≤ 240` rules, curve table math (÷30.44, share
  normalization, 1-based peak month), species wording, removal cleanliness.

## Findings and remediation

### Audit 01 — dairy biology & explanations

1. **MODERATE — Explanation 3's arithmetic didn't close** (calf-milk allowance
   silently absorbed: "30,440 ÷ 2,100 needs 16.2 calvings" vs a calculator's
   14.5). **Fixed**: the line now shows the seed division (~14.5), states the
   plan's measured figure separately (~15.4), and names the gap (calf milk +
   attrition). A new 3a line discloses the calf-milk allowance explicitly.
2. **MODERATE — impossible percentages under heavy culling** ("142% milking,
   −42% dry") because the narrative used the raw share while the field clamps.
   **Fixed**: narrative clamps like the field, and the finishing-pen case gets
   an explanatory sentence.
3. MINOR — "× 30 days = 30,440" false arithmetic → now prints 30.44.
4. MINOR — explanation 5's derivation off by ~2% → rephrased to "calvings ×
   cycle ≈ N animals in the cycle at any moment; the steady pool measures M"
   with no false causal claim.
5. MINOR — explanation 6 omitted the replacement bridge → now quotes bridge
   and total buying when a bridge exists.
6. MINOR — geometric-curve wording claimed a "rising" phase → shape-aware.
7. MINOR — "tank stays flat" without the seasonality caveat → caveat added
   whenever yield multipliers vary.
8. MINOR — partially tautological test assertions → kept as contract pins;
   the independent identities (3–5 month dry band, share ≈ L/CI within 5%,
   seed-vs-measured disclosure) carry the load.

### Audit 02 — planner backend

1. **MODERATE — advisory-lock namespace 4714 collided with finance's milk
   ledger** (cross-feature serialization). **Fixed**: planner quota moved to
   4715; regression test asserts uniqueness across all five farm lock
   namespaces.
2. **MODERATE — PATCH could 500** on stored assumptions that no longer
   validate (unguarded re-validation). **Fixed**: guarded, answers 422 with
   the same message as the read paths; `plan.name` snapshotted before the
   rollback (a lazy-refresh MissingGreenlet the test caught). Regression test
   corrupts a row via SQL and asserts the 422.
3. **MODERATE — storable dead-on-arrival plans** (schema accepted years
   0000–9999 while the engine runs 1900–2200). **Fixed**: all three calendar
   fields bounded to `^(19\d{2}|20\d{2}|21[0-1]\d|2200)-(0[1-9]|1[0-2])$`;
   test rejects 1899/2201/0000/9999 on every field.
4. **MODERATE — CPU budget charged for requests the 240-month ceiling then
   rejected** (up to ~19% of the sliding window for zero work). **Fixed**: the
   ceiling check moved ahead of the charge in the endpoint; regression test
   monkeypatches the charger and asserts it never fires for a doomed request.
5. MINOR — `_effective_conception` mislabelled unlimited-retry herds (default
   `max_services_before_cull=0`) as one-service, overstating bred does ~18% →
   unlimited retries now return certainty with an honest label; sexed-semen
   caveat added to chains when configured.
6. MINOR — purchase-action lead time used a male/foundation formula for every
   class → now the tightest of the target classes' windows
   (`_purchase_month_for`'s own formula).
7. MINOR — stale `_run_limits` docstring → corrected.
8. MINOR — test gaps aligned with the real bugs → duplicate-target API test
   added (three identical targets → three aligned fills/echo/chains).

### Audit 03 — frontend & contract

1. **MODERATE — rename silently discarded on update** (PATCH never sent
   `name`). **Fixed**: name is sent and required; regression test asserts the
   PATCH body carries the edited name and revision.
2. **MODERATE — breed/system dropdowns desynced after opening a saved plan**
   (herd bucketing/calibration could run against the wrong species silently).
   **Fixed**: opening a plan re-anchors the dropdowns to the farm species
   default, visibly.
3. MINOR — 409 recovery loop → the open plan's fresh revision is fetched and
   adopted so a retry succeeds.
4. MINOR — "Use my herd" before defaults land claimed false success → guarded.
5. MINOR — milk report flagged stale by sale-target edits → staleness key no
   longer includes targets.
6. MINOR — missed-deadline tint and milk labels used the live start month →
   anchored on the run's own start.
7. MINOR — dry-row month range could render inverted → clamped.
8. MINOR — plans-list failure rendered as "No saved plans"; >50 plans silent
   → error state + truncation note.
9. MINOR — weak peak-cell test assertion → asserts the exact "3 (peak)" cell;
   Update button disabled at zero targets like Save.

## Post-fix verification

- Backend (targeted): `tests/test_simulation_milk_planner.py` 26 passed;
  `tests/test_backward_planner.py` 13 passed; `tests/test_planner_api.py` 15
  passed; `tests/test_simulation_planner.py` + `tests/test_simulation_api.py`
  + `tests/test_lactation_curve.py` green; ruff clean; mypy unchanged (only
  the repo-wide pre-existing `responses=COMMON_ERROR_RESPONSES` pattern).
- Frontend (full suite): **3,280 passed / 0 failed**; `tsc --noEmit` clean;
  eslint clean.
- Full backend suite re-run cleanly after an earlier run was invalidated by
  concurrent interactive test runs sharing the test database (register-email
  collisions cascading into fixture errors — a harness mistake, not a code
  defect); the clean result is recorded below.

### Clean full-suite results

- Backend: **3,896 passed, 4 skipped, 0 failed** (exit 0, 21m25s).
- Frontend: **3,280 passed, 0 failed** (exit 0).
- `tsc --noEmit`, eslint, ruff clean; mypy shows only the repo-wide
  pre-existing `responses=COMMON_ERROR_RESPONSES` pattern (unchanged by this
  work; present on every router at HEAD).

