# V3 — SIMULATION / FINANCE REMEDIATION VERIFICATION

**Date:** 2026-09-01 · **Verifier:** V3 (independent; did not author the fixes)
**Repo:** `/Users/pavankumarreddyreddem/Desktop/goat_saas/backend` (READ-ONLY; scratch scripts in `/tmp/v3_verify/`, throwaway DBs only)
**Method:** source read + execution of the engines (`run_simulation`, `run_optimization`, `run_monte_carlo`, `build_milk_plan`, `calibrate_farm_assumptions` against a synthetic `kid_entries` history in a throwaway Postgres DB), plus the six targeted pytest suites.
**Baseline:** `04_SIMULATION_FINANCE_AUDIT.md` (HIGH-1, MEDIUM-2/3/4, LOW-5/6/7/8); claims per `00B_REMEDIATION_LOG.md`.

---

## VERDICTS AT A GLANCE

| Item | Finding | Verdict |
|---|---|---|
| S-1 (HIGH-1) | Calibration writes the raw phase fraction | **PASS** |
| S-2 (MEDIUM-2) | DSCR spans principal-repaying years; optimizer/MC undegenerated | **PASS** (one wording nuance, see S-2a) |
| S-3 (MEDIUM-3) | Labour scales on adult breeding females | **PASS** |
| S-4 (MEDIUM-4) | Dairy preset parity (6%/6%, ₹850/kg fat) + calf-milk netting | **PASS** |
| S-5 (LOW-5) | Dairy-mode sire sizing excludes lactation overlay | **PASS** |
| LOW-6 | Milk planner 500 L/day achievable on the steady tail | **PASS** |
| LOW-8 | Goat defaults (15% kid mortality, 20:1, AFC 10, sale 9 mo, ≈24.4 kg) | **PASS** |
| Identities | P&L revenue split, amortization 0.00, MC determinism | **PASS** |
| Test suites | 6 files | **PASS — 392 passed, 0 failed** |

**Overall: 9/9 PASS. No FAILED or PARTIAL items.** (S-2a has a documentation-level nuance that does not affect behavior; details below.)

---

## S-1 — Calibration kid-mortality inflation (HIGH-1) — PASS

**Code** (`app/services/simulation_calibration.py:666-683`): writes `min(0.9, died / len(weaned_rows))` directly, with a comment explaining the whole-phase semantics and the old 3.4× inflation. `_annualized_fraction` still exists (line 84) but has no kid-mortality call site.

**Executed** (`/tmp/v3_verify/t_s1.py`, throwaway DB `v3_verify_s1_test`, alembic-migrated, reference date 2026-09-02 farm-local):
synthetic history = 10 kiddings ~5 months back (beyond the 3-month weaning cutoff), 20 live-born kid entries, 2 DIED with `mortality_reported_at` inside the 3-month window (10% observed phase loss).

- Calibrated `mortality.kid_pre_weaning` = **0.100000** (not 0.3439).
- Old helper on the same data: `_annualized_fraction(2, 20, 3)` = 0.343900 — the fix bypasses it.
- Evidence row: `previous=0.15 → calibrated=0.1, n=20`, method string now states the whole-phase basis.
- Eligibility filters intact: only kiddings old enough to have completed the window enter the denominator; deaths counted only when reported within 3 months of birth.

## S-2 — DSCR span, optimizer feasibility, MC risk metric (MEDIUM-2) — PASS

**Code** (`engine.py:1898-1928`): `dscr_per_year` unchanged (0 for debt-free years); `min_dscr`/`avg_dscr` computed over `repaying_dscr` = years with `debt_service > 0 AND principal > 0` (moratorium excluded; the pre-existing balloon exclusion from the DSCR *denominator* preserved at `:1892-1896`). Optimizer gate (`optimization.py:48-53`) now uses `avg_dscr` with a `None` guard. MC `weak_dscr_runs` (`montecarlo.py:304`) counts `min_dscr is not None and min_dscr < 1.0` — `None` (no repaying year) no longer counts as weak.

**Executed:**

- **Span on the default osmanabadi run:** `dscr_per_year = [-2.022, 0.442, 0.484, 0.409, 0.271, 0.357, 0, 0, 0, 0]`; year-1 principal = 0.00 (interest-only moratorium). `min_dscr = 0.2709 = min(years 2-6)` and `avg_dscr = 0.3925 = mean(years 2-6)` — the moratorium year (−2.022) is excluded from both. Recomputed from the annual rows; exact match.
- **S-2a None case:** a no-debt run (`loan_fraction = 0`) yields `min_dscr = avg_dscr = None`. *Nuance:* the instruction's "only debt year is interest-only → None" is **unreachable with a live loan**: when the loan outlives the horizon the closing balance is booked as principal in the final year (`engine.py:1796-1797`), so that year counts as repaying. I scanned horizon ∈ {12,18,24,36} × moratorium ∈ {0,6,12,18,24,36} × term ∈ {13..72}: **no loan-carrying run with debt years produced None**; the 12-month/12-moratorium/60-term toy yields −2.077 for both (which IS the interest-only ratio, balloon excluded from the denominator). The in-code comment's toy example overstates the branch's reach; behavior is correct and conservative.
- **S-2b strong synthetic** (osmanabadi preset, meat ₹1,200/kg, labour ₹5,000/mo, loan fraction 0.40): `run_optimization` → **evaluated 109, feasible 109, recommended NOT None** (recommended NPV ₹1,05,41,220 at loan 0.55, `min_dscr` 5.73). Deterministic single run: `min_dscr 8.05 / avg_dscr 8.90`, NPV ₹82.9 L, year-1 DSCR 0.114 (moratorium) correctly out of span. (Audit baseline: 37 evaluated / 0 feasible / recommended None.)
- **S-2c Monte Carlo on the strong synthetic** (200 runs, default seed): `prob_dscr_below_one = 0.00 < 1`. NPV P5/P50/P95 = ₹51.2 L / ₹70.7 L / ₹93.8 L. The *default* goat preset still reports 1.00 — legitimately: its repaying-year DSCRs (0.27–0.48) are genuinely below 1; the metric now measures thin margins rather than the moratorium artifact (matches the remediation log's framing).

## S-3 — Labour scaling (MEDIUM-3) — PASS

**Code** (`engine.py:1470, 1482-1494`): `labourers = ceil(all_does_now / labour_per_head_threshold)` where `all_does_now = does_now + finishing_total` — adult breeding females plus the finishing pen, not standing head. (Kids/weaners/growers excluded; bucks excluded, consistent with the per-doe norm.)

**Executed** (default osmanabadi run; adult does recomputed from the monthly rows as `open_does + pregnant_does + lactating_does`):

| month | adult does | total herd | engine labour ₹ | expected `ceil(adults/60)·14000·1.05^((m−1)/12)` | diff |
|---|---|---|---|---|---|
| 12 | 49.787 | 118.504 | 14,640.3534 | 14,640.3534 (1 labourer) | **0.000000** |
| 1 | 49.79 | 110.8 | 14,000.00 | 14,000.00 | 0.000000 |
| 6 | 48.73 | — | 14,287.52 | 14,287.52 | 0.000000 |
| 24 | 48.87 | — | 15,372.37 | 15,372.37 | 0.000000 |
| 60 | 48.87 | — | 17,795.44 | 17,795.44 | 0.000000 |

Month-12 identity holds **exactly**; implied labourers = 1 (audit found 3 on a 118-head standing herd). Downstream effect: default-preset NPV moved from −₹37.5 L (audit) to **−₹6.94 L**, BCR 0.885, payback month 120, break-even meat ₹440.8/kg (was ₹781) — the preset is no longer a structural rejection.

## S-4 — Dairy preset parity and calf-milk netting (MEDIUM-4) — PASS

**Preset values** (`defaults.py`): `milk_price_per_kg_fat = 850.0` (:339), `annual_milk_price_growth_rate = 0.06` (:377), `annual_feed_price_growth_rate = 0.06` (:404), `calf_milk_litres_per_day_per_calf = 2.5` (:322); fallback `milk_price_per_litre = 58.0` = 850 × 6.8% (consistent).

**Calf-milk netting executed** (`engine.py:1461-1466`): two full 120-month Murrah runs differing only in the flag (0 vs 2.5 L/calf/day). In every month with `f_kids > 0`, `milk_revenue(0) − milk_revenue(2.5)` equals `f_kids × 2.5 × 30.44 × effective_price` (effective price = 850×6.8% × 1.06^((m−1)/12) × monthly price multiplier). **120 months checked, worst relative error 3.7e-15.** Example month 1: f_kids 2.161 → revenue gap ₹9,791 ≈ 2.161×2.5×30.44×₹59.5.

## S-5 — Dairy-mode sire sizing (LOW-5) — PASS

**Code** (`engine.py:995-997`): pre-service purchase check now computes
`does_now = sum(svc) + sum(open_waiting) + sum(preg) + (0.0 if dairy_mode else sum(lact))` — the lactation overlay is excluded in dairy mode, matching the step-6 form at `:1191-1197`.

**Executed** (Sirohi dairy-goat, `auto_purchase_bucks=True`, 36 months):

- ratio 20: month 25 — open 10.176 + preg 38.693 = breeding pool 48.869 (overlay lact 24.667 correctly NOT counted); `ceil(48.869/20) = 3` → **bucks = 3.0**. Same agreement at m12/m24/m26/m36. The pre-fix form (pool+overlay = 73.5) would have bought 4.
- Audit's original scenario (ratio 25): m25 pool 48.869 → `ceil(48.869/25) = 2` → **bucks = 2.0** (audit observed the buggy 3).
- Non-dairy contrast (lactation 0): pool counts lact (5.44+27.38+16.05 = 48.87) → 3 bucks — overlay still counted where it IS a distinct pool.

## LOW-6 — Milk planner steady-tail sizing — PASS

**Executed**: `build_milk_plan(murrah defaults, 500 L/day)` → **`achievable = True`**, `steady_from_month = 104`, `steady_average_daily_litres = 497.89`; final-12-month average of the projection = **500.00 L/day = 100.0% of target (≥ 98%)**. Design herd 113.4 breeding does. The loop now measures on the final 12 months only (`milk_planner.py:589-607`), so the bought-in placement transient no longer flatters the sizing; the plan's own trend check accepts it. (Audit: converged at 499.3 window-average but `achievable=False`, trend 556→439.)

## LOW-8 — Goat defaults — PASS

All read from `SimulationAssumptions()` defaults (osmanabadi = base preset) and recomputed:

| Default | Value | Check |
|---|---|---|
| `mortality.kid_pre_weaning` | **0.15** (assumptions.py:218-220, NABARD convention) | ✓ (was 0.10) |
| `culling.buck_doe_ratio` | **20** (assumptions.py:240) | ✓ |
| `reproduction.age_at_first_breeding_months` | **10** (assumptions.py:178) | ✓ |
| `growth.sale_age_months` | **9** (assumptions.py:303) | ✓ |
| male weight at 9 mo | `weight_by_age_months[9] = 22.2` × 1.10 premium = **24.42 kg** (engine `male_weight_at_age(9)` = 24.42) | ✓ inside the 24–28 kg SPEC window |

## Identities — PASS

Default osmanabadi run (120 months, with break-even):

- **Annual P&L revenue split:** worst `|meat+cull+milk+manure − total_revenue|` across all 10 years = **0.0**.
- **Amortization:** 72 rows (72-month term), final `closing_balance = 0.00`; Σ principal = **₹16,50,065.77 = metrics.loan_amount** exactly.
- **Monte Carlo determinism:** two consecutive `run_monte_carlo` (50 runs, default seed) produce identical `model_dump()` → **True**.

## Test suites — PASS

```
GOATFARM_TEST_DB=v3_verify_test ./.venv/bin/python -m pytest \
  tests/test_simulation_engine.py tests/test_simulation_financials.py \
  tests/test_simulation_advanced.py tests/test_simulation_milk_planner.py \
  tests/test_simulation_api.py tests/test_simulation_explain.py -q
→ 392 passed in 136.35s
```

Notably `test_simulation_api.py` now passes **as a file** (it was 31F/6E in the audit — LOW-7, fixed by the per-test run-budget/lock reset noted in the log).

---

## Notes (non-blocking)

1. **S-2a wording nuance** (see above): with a live loan, the horizon-end refinancing balance is booked as annual `principal` (engine.py:1796-97), so a "debt year that is only interest-only" cannot exist inside the DSCR span; `None` occurs exactly when there is no principal-repaying year — in practice the no-loan run. The engine comment at :1920-1924 gives a toy example that this accounting makes unreachable. Behavior is correct; only the comment is loose.
2. Default goat Monte Carlo `prob_dscr_below_one = 1.00` is now a true statement about repaying-year coverage (0.27–0.48), not the moratorium artifact.
3. Scratch scripts: `/tmp/v3_verify/t_engine.py`, `t_dscr.py`, `t_s1.py`, `t_extra.py`. Throwaway DBs dropped; source tree untouched.
