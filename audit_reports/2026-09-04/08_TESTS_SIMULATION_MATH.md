# Red Team Audit #8 — "The Verification Skeptic" — Test-Suite Integrity & Simulation Math

**Date:** 2026-09-04 · **Method:** read-only audit — all 21 files in `backend/app/simulation/`, `api/simulation.py`, `api/ops_simulation.py`, `api/_run_limits.py`, schema/defaults, 76 backend test files (AST-scanned all 3,026 test functions), 219 frontend test files (AST-scanned), all 24 Stryker report JSONs, `backend/mutants/mutmut-stats.json`, git history of the two quality-brag commits. Nothing executed or modified.

## TOP FINDINGS (ranked by false confidence created)

### 1. "Backend mutation kill rate 90.5%" is scoped to ~15% of the backend — HIGH (claim integrity)
`backend/pyproject.toml:70-75`

`only_mutate = ["app/simulation/*.py", "app/schemas/simulation.py"]` — the 10,345 mutants never touched `api/`, `services/` (17 files incl. 49k-line `simulation_calibration.py`), `security.py`, `deps.py`, `permissions.py`, `ratelimit.py`. Worse, mutation mode runs only 10 hand-picked DB-free test files (`pytest_add_cli_args_test_selection`, pyproject.toml:80-94): the API-level suites that would kill mutants in shared code (`test_simulation_api.py`, `test_adversarial.py`, `test_rbac.py`, …) never execute against a mutant. The commit message admits "over app/simulation", but the headline creates repo-wide confidence the artifact does not support.

**Fix:** state the scope in the commit/README headline, or extend `only_mutate` + selection.

### 2. Frontend mutation state: last full-repo artifact shows 64.1% with 4,459 survivors; simulation pages at 62% AFTER remediation — HIGH (claim integrity)
`frontend/reports/mutation/full-force-baseline.json`; `simulation-logic-after-remediation.json`

The only full-repo snapshot (`full-force-baseline.json`, Aug 21 23:14 — before the final targeted campaigns): 14,770 mutants, 9,466 killed, **4,459 survived**, 844 NoCoverage → **64.1%**. The "886 tests killing 1,900+ survivors" commit (03d6a32) is a true *delta*, but no final full re-run exists, and the post-remediation targeted reports still carry hundreds of live survivors on the most business-critical code: `simulation-logic-after-remediation.json` 1,639 mutants / **568 survived + 55 NoCoverage = 62.0%**, `domain-pages-after-remediation.json` 569 survived (75.2%), `highrisk-pages-after-remediation.json` 776 survived (73.3%), `core-behavior-verification.json` 163 survived (64.7%), `simulation-timeout-isolation` 8/8 survived (0%).

**Fix:** run one authoritative full-repo Stryker pass and publish that score.

### 3. `test_murrah_monte_carlo_milk_price_risk` verifies nothing about milk-price risk — MEDIUM (test theater)
`backend/tests/test_dairy.py:883-892`

```python
assert mc.npv_p5 <= mc.npv_p50 <= mc.npv_p95
assert mc.npv_p95 > mc.npv_p5
```

Percentile ordering over a sorted list is monotone in p *by construction* (`montecarlo.py:63-73`), and `p95 > p5` is guaranteed by the other 8 enabled risk variables — if the `milk_price` draw were silently dropped from `_correlated_draws`/`_apply_draws`, this test still passes green. Mitigated by `test_simulation_deep_mutation.py:774` (`test_apply_draws_scales_both_milk_price_bases`) which does pin the mechanism — but this named test is pure theater.

**Fix:** disable the other 8 RiskVariables and assert NPV spread direction vs. the deterministic run.

### 4. Feed-price risk treats home-grown fodder inconsistently between the two shock channels — MEDIUM (math)
`backend/app/simulation/montecarlo.py:88-90` vs `backend/app/simulation/market.py:63-72`

`_apply_draws` scales `green_price_per_kg` by the MC feed-price draw, but `feed_prices_for_month` omits `shock_multiplier` from the home-green price only (`feed.green_price_per_kg * green_factor * growth`, market.py:63) while purchased green/dry/concentrate all get it (lines 64-72). Concrete: Murrah preset grows 25 acres × 10 t DM ≈ 250,000 kg DM/yr charged at ₹0.8/kg ≈ ₹200k/yr — a drought month (default multiplier 1.30) leaves the dominant share of green cost unshocked, while an equivalent MC draw (low 0.90/high 1.25) does shock it. Drought downside feed cost is understated relative to MC (or MC overstated relative to drought — one convention must go).

**Fix:** apply (or omit) the feed-price shock to `green_price_per_kg` in both paths.

### 5. Calf-milk charge contradicts its own comment; retained male calves drink nothing — LOW-MEDIUM (math/doc)
`backend/app/simulation/engine.py:1471-1479`

Comment: "every calf in it drinks the daily allowance" — code subtracts only `sum(f_kid) * calf_milk_litres_per_day_per_calf * DAYS_PER_MONTH`. Under the preset (`male_calf_sell_at_birth_fraction=0.9`), the ~10% of male calves retained also nurse in reality; saleable milk is overstated by their share (order ~50-60 L/month on the flagship preset).

**Fix:** include `sum(m_kid)` or fix the comment and document the heifer-only protocol.

### 6. 30 backend tests with zero assertions — LOW (shallow)
`backend/tests/test_unit_extended.py:1266` (`test_farm_create_valid`), :1352, :1443 … :2199 (21 tests), plus 9 delegation-cases

`test_farm_create_valid` body is just `FarmCreateIn(**{...})` — passes unless Pydantic raises. It does catch over-tightening, but each is a smoke, not a verifier. The other 9 flagged (e.g. `test_mass_balance_default_run`, `test_simulation_financials.py:303`) delegate to asserting helpers — real.

**Fix:** none urgent; these are honest "valid input validates" smokes.

### 7. "Lactation sums to *exactly* lactation_milk_litres" is broken ~1.1% by the heat-stress preset — LOW (doc/math)
`backend/app/simulation/lactation.py:23-25` vs `backend/app/simulation/defaults.py:344-357`

The curve identity holds pre-seasonality, but `monthly_milk_yield_multipliers` (`engine.py:1468`) are not mean-normalized — unlike meat-price defaults which go through `_normalized_seasonality` (`assumptions.py:351`). The Murrah preset's yield curve averages 0.989, so a "2,100 L" lactation sells ≈2,077 L/yr. Heat-stress loss is intended; the undocumented coupling with the "exactly" claim is not.

**Fix:** normalize the preset's yield multipliers to mean 1.0 or document the realized total.

### 8. Client-chosen seed makes MC risk numbers cherry-pickable — LOW (by design, worth stating)
`backend/app/simulation/assumptions.py:689` + `montecarlo.py:268`

`seed: int = Field(default=42, ge=0, le=2**31-1)` feeds `random.Random(a.risk.seed)`; a user can farm seeds for a favorable `npv_p95`/`prob_npv_negative` before saving a scenario shown to a lender. Results are advisory and any user can equally choose their own seed, so this is a product property (reproducibility, pinned by `test_monte_carlo_same_seed_reproducible`, `test_simulation_engine.py:437`) — but a saved scenario's MC band is not an unbiased risk estimate. *(Independently re-found by Audit #4 — cross-confirmed.)*

**Fix:** server-side re-seed per run, or label saved-scenario MC as "user-seeded".

### 9. Frontend coverage of simulation pages is broad but mutation-thin (see #2) — LOW
`src/app/(app)/simulation/` (17 test files, 16k lines)

17 component test files exist and are genuine (MSW-mocked network, real DOM assertions — e.g. `page.mutation.test.tsx:1072-1074` asserts the full posted assumptions object; `page.metric-thresholds.test.tsx:260-261` asserts rendered tint classes). The gap is mutation-detection strength (62%), not test count.

## ATTACKS THAT FAILED (verified correct)

- **No negative yields/prices:** persistency bounded [0.5, 1.0] (`assumptions.py:410`), Wood weights strictly positive, normalization exact to 1e-9 (`test_lactation_curve.py:40-43`); all price channels multiplicative positive multipliers; `RiskVariable` requires `low <= 1.0 <= high` (`assumptions.py:676`).
- **No percentile off-by-one:** `k = (len-1)*p` linear interpolation = numpy method (`montecarlo.py:63-73`); `pstdev`/`mean` safe at n=1; `monte_carlo_runs ge=1`.
- **No NaN path:** `FiniteFloat(allow_inf_nan=False)` schema-wide, MC clamps (`min(0.9, …)`, `max(5e-324, …)` `montecarlo.py:101-133`), `_finite_payload` 422 backstop (`api/simulation.py:200`); verified by `test_simulation_fuzz.py:227`.
- **No heat-stress double-dip:** curve normalization excludes seasonality; `monthly_milk_yield_multipliers` applied exactly once (`engine.py:1468`); disease `milk_yield` shock is a separate channel.
- **No DST/timezone bugs:** monthly engine is pure integer-month arithmetic; daily_ops uses `date` + `timedelta(days=day-1)` consistently 1-based (`daily_ops.py:616-622`); Bakrid calendar embedded with tentative years flagged (`market.py:98-114`).
- **No state leakage / DB mutation:** engine is pure, zero mutable module state in the simulation package; run endpoints `rollback()` before CPU work and persist nothing (`api/simulation.py:430, 680`); scenarios store assumptions only, never results; per-farm/per-user locks + budget in `_run_limits.py` prevent cross-farm interference (only the documented 2 process-wide slots are shared).
- **Money math:** EMI uses expm1/log1p (exact as r→0, `finance.py:41-46`), final payment clamps balance to exactly 0, IRR is multi-root-aware returning None when ambiguous, DSCR/balloon exclusions documented and tested.
- **Claim-verified strong tests (named):** `test_dairy.py:874-880` (exact herd mass-balance identity at abs=1e-6 for the preset), `test_simulation_deep_mutation.py:1955-1992` (dairy overlay anchoring re-derived independently at rel=1e-9 across 3 parametric shapes), `test_simulation_api.py:542-567` (cross-farm scenario 404 on all four verbs + list non-leak), `test_simulation_engine.py:471-488` (feed hand-check from raw arithmetic), `e2e/simulation.spec.ts:66+` (real sign-in, real backend run — unmocked).
- **Theater greps came back clean:** no `assert True`, no `.skip`/`.todo`/`.only` in frontend, no snapshot tests, 0 frontend tests without an `expect`, only 2 conditional `pytest.skip`s (justified precondition changes).

## VERDICT

**Real test-suite strength vs the claimed 90.5%:** the simulation package itself earns something close to its number — the deep-mutation suite (`test_simulation_deep_mutation.py`, ~90 tests) uses independently derived golden mirrors at rel=1e-9, and the mass-balance/identity tests would genuinely fail on real breakage. But "backend kill rate" as a repo claim covers only `app/simulation` + one schema file with a curated 10-file mutant-killing subset; for the other ~85% of the backend the claim says nothing (integration coverage there is nonetheless unusually thorough: 3,983 tests over real PostgreSQL). Frontend: the credible aggregate mutation score is ~70-80%, not implied-90+, with the simulation UI specifically at 62% carrying 568 live survivors.

**Simulation correctness:** the engine's math survived a line-by-line attack — no negative yields, no negative prices, no off-by-one percentiles, no NaN propagation, no heat-stress double-dip, no calendar hazards, exact mass balance. The genuine defects are modeling-level, not arithmetic: the home-green feed-price shock inconsistency (#4), the calf-milk sex asymmetry (#5), and the ~1.1% seasonality-coupled shortfall vs the "exactly lactation_milk_litres" identity (#7). No way was found to make the engine produce a wrong number silently; the two risk-channel inconsistencies are the only places where two code paths disagree with each other.
