# AUDIT A4 — SIMULATION & FINANCE ENGINES

**Date:** 2026-09-01 · **Auditor:** A4 (independent) · **Repo:** `/Users/pavankumarreddyreddem/Desktop/goat_saas`
**Scope:** `backend/app/simulation/` (all 17 files), `backend/app/services/{finance,simulation_calibration}.py`, `backend/app/api/simulation.py`, `backend/app/schemas/simulation.py`, `frontend/src/app/(app)/simulation/page.tsx`, `frontend/src/lib/persisted-numbers.ts`, README claims.
**Method:** full source read; formulas re-derived and cross-checked against literature and market data; every engine executed in `/tmp` scratch scripts (`/tmp/audit_a4/t1..t11*.py`); targeted pytest suites run (`goatfarm_test` DB created for the purpose). Source tree untouched (READ-ONLY).

---

## EXECUTIVE SUMMARY

The simulation package is unusually well-engineered: monthly mass-balance holds to 5.7e-14 over 120 months with scheduled events, the financial math (EMI, amortization with moratorium + balloon, monthly-timed NPV discounting, multi-root IRR isolation, MIRR, tax with loss carry-forward, straight-line depreciation with residual caps) is textbook-correct and numerically hardened, Monte Carlo is bit-for-bit deterministic under a seed and its percentile routine matches numpy's `linear` method, and the Wood lactation curve sums exactly to the configured lactation total with a day-65 peak that reproduces published Murrah shape statistics. Defaults are mostly inside verified 2025-26 Telangana market bands.

Four findings deserve action: **(1) HIGH** — the calibration service converts an observed 3-month kid-mortality phase loss into an "annual" rate that the engine then re-applies as a whole-phase rate, inflating calibrated kid mortality 3.3–3.7×; **(2) MEDIUM** — the optimizer's DSCR floor makes *every* candidate infeasible on both flagship presets (year-1 DSCR is negative by construction during the interest-only moratorium), so `recommended` is always `None` and Monte-Carlo `prob_dscr_below_one` is degenerate (1.00); **(3) MEDIUM** — the default Osmanabadi preset ships deeply loss-making (NPV −₹37.5 L) chiefly because the labour threshold counts standing head (kids+growers) against a norm its own comment states per breeding doe; **(4) MEDIUM** — the dairy preset's feed inflation (6%/yr) outruns milk price growth (5%/yr) for a decade with no procurement pass-through, flipping EBITDA negative from year 3 (NPV −₹92 L), which contradicts observed 2025 Telangana procurement revisions.

---

## FINDINGS

Severity: BLOCKER / HIGH / MEDIUM / LOW / INFO. All file paths relative to repo root.

---

### [HIGH-1] Calibration inflates kid pre-weaning mortality 3.3–3.7× (phase/annual rate confusion)

**File:** `backend/app/services/simulation_calibration.py:84-88` (`_annualized_fraction`) and `:666-681` (call site); contradicts `backend/app/simulation/engine.py:123-133, 676-681` and `backend/app/simulation/assumptions.py:200-219`.

**Issue:** The engine's `kid_pre_weaning` is a **whole-phase** rate — `phase_monthly_mortality_rate(x, 3)` removes exactly `x` of the crop over the 3-month kid class (docstring in `assumptions.py:200-211` is explicit, and engine.py:676-681 applies it that way). But the calibration service writes

```python
calibrated_kid_mortality = _annualized_fraction(died, len(weaned_rows), 3)
# = 1.0 - (1.0 - died/n) ** (12/3)  =  1 - (1-observed_phase_loss)**4
```

with the comment "The engine expects an annualized class rate" (`simulation_calibration.py:668-670`) — stale: the engine has expected a phase rate since the phase converter was introduced. The engine then treats that value as the whole-phase fraction.

**Evidence (executed):**

| Farm's observed 3-month loss | `_annualized_fraction` writes | Engine removes per crop | Inflation |
|---|---|---|---|
| 5%  | 0.185 | 18.6% | 3.7× |
| 10% | 0.344 | 34.4% | 3.4× |
| 12% | 0.400 | 40.0% | 3.3× |
| 15% | 0.518 | 51.8% | 3.5× |
| 30% | 0.802 | 80.3% | 2.7× |

A farm with an *average* 10–12% pre-weaning loss (NABARD models assume 15%; Osmanabadi field data 10.9–20.4%) gets a model in which a third to a half of every kid crop dies — depressing every calibrated projection, milk-planner pipeline estimate and lender presentation built from "calibrate from my records".

**Fix:** write the observed phase fraction directly: `calibrated_kid_mortality = min(0.9, died / len(weaned_rows))`, and update the method string. (The same helper is correct where it is *not* used for kid mortality — check no other call sites rely on annual semantics; there are none.)

---

### [MEDIUM-2] DSCR floor makes the optimizer infeasible on its own defaults; `prob_dscr_below_one` is degenerate

**Files:** `backend/app/simulation/optimization.py:47-49` (min DSCR → infeasible), `backend/app/simulation/engine.py:1671-1687, 1868-1884` (DSCR per 12-month block), `backend/app/simulation/assumptions.py:595-596` (moratorium 12), `backend/app/simulation/montecarlo.py:304`.

**Issue:** Year 1 of a goat run is interest-only (moratorium) with structurally near-zero sales (first male sales land ~month 10-11 after settling + gestation + growth), so year-1 EBITDA is negative by construction → year-1 DSCR = (EBITDA − tax)/interest < 0 → `min_dscr` < the default 1.20 feasibility floor for **every** candidate.

**Evidence (executed):**
- Default Osmanabadi: `dscr_per_year = [-3.05, -0.57, -0.53, -0.60, -0.73, -0.73, 0, 0, 0, 0]`; `run_optimization` → `evaluated=37, feasible=0, recommended=None`.
- Default Murrah: `min_dscr=-0.22`; 135/135 candidates infeasible (also at loan fraction 0.40).
- Even a synthetic goat run with meat ₹1,200/kg, labour ₹5,000/mo, loan 40% (NPV +₹33.7 L) still has `min_dscr=−3.45` → 0 feasible candidates.
- Monte Carlo: `prob_dscr_below_one = 1.00` on the default goat run (and 1.00 on dairy at min_dscr −0.22) — the risk metric carries no information.

The engine *does* compute a correct average DSCR (NABARD's actual criterion), but the optimizer and MC risk summary gate on the minimum, which the moratorium-year structure guarantees to fail.

**Fix options:** exclude moratorium/interest-only years from `min_dscr` (mirroring the existing balloon exclusion precedent at engine.py:1862-1874), or gate on `avg_dscr` as NABARD bankable schemes do, or floor DSCR measurement at the first year with principal repayment. Rank key unchanged.

---

### [MEDIUM-3] Default Osmanabadi preset is deeply loss-making as shipped; labour threshold unit mismatch is the dominant driver

**Files:** `backend/app/simulation/assumptions.py:546-557` (`labour_per_month=14000`, `labour_per_head_threshold=60`), `backend/app/simulation/engine.py:1467-1474` (`ceil(total_herd / threshold) * labour`).

**Issue:** The threshold comment cites "TNAU/NABARD stall-fed budgets staff roughly one worker per 50 goats" — a **per-breeding-doe** norm — but the engine applies it to `total_herd`, the standing head *including* kids, weaners and growers (≈2.6× the doe count at defaults). The model therefore hires 3 labourers (₹5.6–8.0 L/yr, escalating 5%/yr) for a 50+2 unit that NABARD budgets at ~1 worker per 50 does with progeny.

**Evidence (executed, default run):** project ₹22.5 L; NPV **−₹37.5 L**; BCR 0.58; payback never; `min_cash −₹57.5 L`. Year 5: revenue ₹7.86 L vs labour ₹6.27 L + feed ₹3.78 L (EBITDA −₹3.6 L). Break-even meat price ₹781/kg — 2.1× the market — confirms no parameter tweak of prices alone rescues it.

**Fix:** either express the threshold in doe-equivalents (e.g. count adults 1.0, kids/weaners 0.3-0.5), raise the default to ≈150 standing head (1 per ~55 does with progeny), or expose family-labour (partially imputed wage) in the preset. At minimum, the flagship default should not be a guaranteed-rejection bankable model.

---

### [MEDIUM-4] Dairy preset: feed inflation outruns milk price growth for a decade with no pass-through; ₹900/kg-fat default is above verified procurement

**Files:** `backend/app/simulation/defaults.py:361` (`annual_milk_price_growth_rate=0.05`), `:388` (`annual_feed_price_growth_rate=0.06`), `:327` (`milk_price_per_kg_fat=900.0`), `:419-421` (finance).

**Issue:** With milk revenue ≈ 55-60% of total and feed the largest cost, a persistent 1 pp/yr real squeeze compounds for 10 years with no mechanism linking procurement price revisions to feed cost. Result (executed): Y1 EBITDA +₹13.6 L declines monotonically to negative from Y3 (−₹3.9 L) and stays negative; NPV **−₹92.3 L**; min DSCR −0.22; min cash −₹12.9 L. Telangana reality shows the opposite coupling: Vijaya raised buffalo procurement ₹82→₹85/L (10% fat ≈ ₹850/kg fat) in April 2025 alone, and ₹48→₹59.5/L within the year (cited by the repo itself at `assumptions.py:673-675`).

The default fat price ₹900/kg fat (₹61.2/L at 6.8% fat) is +5.9% over the verified ₹850/kg-fat procurement rate; the docstring justifies it as a blend with direct sales at ₹80-110/L — defensible as a *level*, but the growth-rate wedge is the structural problem.

**Fix:** couple the two growth rates in the dairy preset (milk growth ≥ feed growth, or an explicit real-squeeze toggle the user must opt into), and/or set the milk growth default to 6% to match feed. Re-run: at 6%/6% the Y10 EBITDA gap narrows by ~₹4-5 L (revenue is a smaller base than feed, so exact parity of rates still bleeds ~0.3%/yr margin; a true fix links them multiplicatively).

---

### [LOW-5] Dairy-mode sire sizing double-counts the lactation overlay

**File:** `backend/app/simulation/engine.py:991` — `does_now = sum(svc) + sum(open_waiting) + sum(preg) + sum(lact)`; in dairy mode `lact` is an *overlay* of the same does (plus the finishing pen), not a distinct pool (see engine.py:366-369, 1107-1120). The parallel computation at step 6 (engine.py:1153-1161) correctly omits `lact` in dairy mode.

**Evidence (executed):** Sirohi dairy-goat run (110 L lactation, 25 doe:buck): true breeding pool at m25 = 48.9; engine computes `does_now = 73.6` (includes 24.7 overlay) → 3 auto-purchased bucks instead of 2 (+50% sire carrying cost).

**Impact:** inert on the flagship Murrah preset (AI, `bucks=0`), but over-purchases sires ~1 per 25 milking does for any dairy-goat run with `auto_purchase_bucks=True`. **Fix:** use the step-6 form `(0.0 if dairy_mode else sum(lact))` at line 991.

---

### [LOW-6] Milk planner converges on a transient-flattered average; its own trend check then rejects the plan

**File:** `backend/app/simulation/milk_planner.py:576-593` (multiplicative rescale to the window average), `:591-593` (`achievable` gate), `:461-475` (`_measure`).

**Evidence (executed, target 500 L/d, Murrah defaults):** the converged plan (91.5 breeding) delivers a 24-month window average of 499.3 L/d — but the deseasonalized *trend* falls from 556 L/d (m24) to 439 L/d (m36) as the bought-in in-milk placement transient washes out, so `achievable=False` and `steady_from=None` on the very plan the loop just declared converged, and `herd.milking_does=75.2` (window average) overstates the sustainable ~64 milking. The failure is surfaced honestly (good), but the planner never grows the herd to make the *steady state* meet the target, so the user gets "not achievable" instead of a correct larger herd.

**Fix:** iterate on the tail-trend projection (e.g. fit the last 12 deseasonalized months' slope, extrapolate to steady state, size on that), or extend `projection_months` internally before measuring.

---

### [LOW-7] `tests/test_simulation_api.py` fails as a file, passes test-by-test (process-state leakage)

**Evidence:** `pytest tests/test_simulation_api.py` → 31 failed / 6 errors; the same tests sampled individually (`test_run_limiter_is_per_farm`, `test_run_can_return_bounded_optimization`, ...) pass. Engine-level suites are clean in bulk: `test_lactation_curve.py` + `test_simulation_engine.py` + `test_simulation_financials.py` = 248 passed; advanced/milk_planner/planner/explain/fuzz/finance_mutation = 155 passed (403 total). The failures stem from in-process shared state (`_run_budget` window, in-flight locks, rate limiter) not reset between tests. Not an engine-correctness issue; CI presumably shards or orders differently — worth fixing so the suite is order-robust.

---

### [LOW-8] Kid-mortality default 10% is below the NABARD bankable convention (15%)

**File:** `backend/app/simulation/assumptions.py:214-216`. NABARD's model bankable scheme assumes **15%** kid mortality; Osmanabadi field studies report 10.9–20.4% (Barbind et al.: ~20.4% of 1,463 kids over 15 years, averaging 10.9%/yr); organized farms achieve 2.6-5%. A lender-facing default at the *best decile* of field data flatters projections by ~5-8% of offtake. (Contrast: the engine's phase-rate *math* is exactly right — verified `1-(1-m)^3 = 0.10` for the default.) Suggest 12-15% for the default, or a `management_level` toggle.

---

### [INFO-9] Float64 in all simulation money paths

The simulation package uses binary floats throughout (₹ crores are far from float64 precision limits, and every verified identity holds to ≤2.3e-10 absolute on values up to ₹1.6 Cr). `Decimal` is correctly used where money is *stored*: `Transaction.amount Numeric(14,2)`, `FeedInventory` prices, animal purchase/sale prices, and `services/finance.py`'s ledger aggregation (banker's rounding via `round(Decimal, 2)` — consistent with the 2-dp column constraint; `frontend/src/lib/persisted-numbers.ts` minimums ₹0.005 / 0.0005 kg match those precisions exactly). Acceptable engineering boundary; no paise/₹ unit mixing found anywhere (all engine outputs are whole ₹; the only unit boundary — as-fed kg vs kg DM — is handled via explicit `_dm_pct` conversions, spot-verified).

### [INFO-10] `DAYS_PER_MONTH = 30.44` vs the cited 365.25/12 = 30.4375

`backend/app/simulation/feed.py:15` — comment claims the FAO/GBADs convention; the constant is +0.008% high. Immaterial (2100 L/305 d = 6.895 vs 6.90 L/d) but the comment should either match the constant or vice versa.

### [INFO-11] Monte Carlo design: level-shift risk, no intra-run autocorrelation

`backend/app/simulation/montecarlo.py:161-189, 196-243`. Run-level draws are scalar multipliers on base prices/rates (a level shift for the whole run) plus non-overlapping monthly disease/drought/crash episodes. This deliberately avoids the classic bug of applying independent draws to cumulative quantities with wrong autocorrelation (verified: draws enter only level parameters; the month-loop shock vectors are episode-blocked, not white noise). Consequence: NPV variance reflects between-year *level* uncertainty, not multi-year commodity cycles; for a 10-year appraisal this understates tail risk (P5-P95 = ±13% of NPV on a 24-month run). Seeded and bit-identical across repeat runs (verified `model_dump()` equality); percentile function matches numpy `linear` exactly (p5 of 1..100 = 5.95); P5/P95 moved <2% from 100→400 runs, so 500 default runs are converged for the reported statistics; band monotonicity p5≤p50≤p95 verified with 0 violations across 24 months.

### [INFO-12] BCR convention: equity cash-flow BCR, not the NABARD economic BCR

`backend/app/simulation/engine.py:1846-1860`, `finance.py:526-546`. Cost flows = equity (month 0) + opex + **debt service (interest and principal)** + tax; benefits = gross revenue + terminal value. NABARD bankable-scheme BCR discounts *project* costs (capital + operating, financing excluded). The implemented version is internally consistent (it is the promoter's cash-flow ratio) but the module is billed "NABARD-style viability metrics"; magnitudes will not match a bank's appraisal. The code's own docstring acknowledges the gross-split rationale.

### [INFO-13] Sensitivity: +20% conception slightly *reduces* dairy NPV at short horizons

`backend/app/simulation/montecarlo.py:475-481` (executed, 36-month Murrah: +conception → ΔNPV −₹5.2k; −20% → −₹3.2 L). Internally consistent: extra calves eat grower rations while their first lactation lands after the horizon. Worth a narrative caveat so a reader does not read "conception doesn't matter".

### [INFO-14] Calibration has no look-ahead bias

All queries bounded `date <= reference_date` with `reference_date = today(farm.timezone)` (`simulation_calibration.py:202-215` and every `between`/`<=` filter); kid-mortality eligibility restricted to kiddings old enough to have completed the 3-month exposure (`:657-664`); Bakrid-month sale prices deflated by the uplift before deriving the base price so the engine's festival premium is not double-counted, and *only* when festival pricing will actually re-apply (`:834-858`); recurring costs divided by the months of ledger history that exist, not the requested window (`:977-997`); labour bill split across the labourer count the engine's own helper implies (`:1003-1029`). Only nit: labour splitting uses *today's* head count for a historical average (negligible).

---

## VERIFIED-CORRECT (formulas, defaults, behavior)

### Finance math (`finance.py`)
- **EMI** `P·r/(1−(1+r)^−n)` via `expm1/log1p` — algebraically the textbook `P·r(1+r)^n/((1+r)^n−1)`; matches to 1.8e-10 on P=₹10 L, 11%, 60 mo; stable to the r→0 limit (`principal/n`).
- **Amortization with interest-only moratorium**: EMI computed on `term − moratorium`; final instalment clamps the balance to exactly 0.00 (verified principal repaid = ₹10,00,000.00); months' debt-service total equals the schedule total (₹27,04,766.50 both).
- **NPV** discounted at actual monthly times `t = month/12` (not year-end lumping) — recomputed from the monthly rows, matches engine to the paisa.
- **IRR**: generalized-polynomial root isolation with exact Decimal path for ≤24-term integral-exponent series, sampled scan beyond; returns `None` on genuinely multiple roots (documented example re-derived: roots −89.2/−26.4/+16.4% for the cited series). Monthly MIRR provided as the timing-accurate alternative; MIRR formula (FV positives at reinvestment rate / PV negatives at finance rate)^(1/horizon) − 1 is standard.
- **Payback** skips the month-0 equity row (a fully-financed project has no instant payback).
- **DSCR** `(EBITDA − tax)/(interest + principal)`, moratorium-year and balloon handling as documented (balloon excluded from DSCR only).
- **Straight-line depreciation** with residual fractions and accumulated-depreciation caps; horizon-capped rows verified; tax assessed per 12-month block with loss carry-forward — recomputed and consistent with annual P&L rows.

### Engine identities (executed, default runs, 120 months)
- **Mass balance** `total_herd = prev + births + purchases − deaths − sales − culls`: worst error 5.7e-14 (goat, with events; dairy same order).
- **Annual P&L = Σ months** (revenue/opex/net cash, error ≤2.3e-10); project cost = shed + equipment + stock + working capital; equity = project − loan − subsidy; terminal breakdown sums; cash balance evolution from the working-capital seed reproduces month rows exactly.
- **Mortality conversion**: annual→monthly `1−(1−a)^(1/12)` compounds back to exactly `a` over 12 months (0.05 → 0.050000); phase rates `1−(1−p)^(1/3)` realize exactly `p` over the 3-month class — the phase/annual distinction the model documents is the *correct* one (see HIGH-1 for where the calibration service disagrees with it).
- **Cull-rate compounding** uses the same converter (a documented 20%/yr removes 20%, not 18.3%).
- **Herd stability**: doe pool holds 48.6–49.8 under the 50-cap for 120 months (no explosion/imploding); Murrah grows 62→125 via 0.75 heifer retention as documented.
- **Integer policy**: all heads are expected-value floats by design; whole-unit decisions (labourers, bucks) use `_ceil_head_ratio` with ULP snapping — no fractional-animal purchases, no phantom 3rd buck from float dust.
- **Bakrid calendar**: 2026-05-28 … 2032-03-22 match the published Indian dates; `bakrid_festival_months('2026-08', 24) = [10, 22]` (May-2027 → month 10, May-2028 → month 22) — verified against manual calendar arithmetic; preset auto-fill yields 10 festivals in 120 months.
- **Festival uplift**: month-10 price ₹519.6 vs prev ₹383.6 = ×1.354 (uplift 1.35 + seasonality) — matches config; held-for-festival males sold in the festival month at the festival price (11.5 head, ₹540/kg observed).
- **Seasonality alignment**: default start 2026-08 → realized milk revenue peaks Nov-Feb (₹6.0 L/mo) and troughs Jun-Aug (₹5.3 L/mo), matching the flush Oct-Feb / lean Mar-Jun pattern with the yield trough in Apr-Jul (multipliers 0.86-0.90) and the price premium in Mar-Jul (1.02-1.07); meat multipliers normalized to mean exactly 1.0.
- **Weight curve**: table 0-12 mo then linear to adult at `adult_weight_age_months` (m18 = 20.5 + 0.5×(33−20.5) = 26.75 ✓); young-male premium ×1.10 applied below adult age only.
- **Feed**: DM = head×BW×dmi%×30.44 exactly; grazed fraction free; 2:1 green:dry roughage split; as-fed conversions via DM%; lactating buffalo ration verified: 520 kg × 3.0% = 15.6 kg DM/d, 38% concentrate = 6.6 kg/d (matches the cited DairyKnowledge benchmark), ₹6,556/mo.
- **Fodder balance**: storage loss monthly, home-grown consumed first, only the true shortfall purchased at market price, home green charged on what is *grown* (documented policy), seasonal yield multipliers normalized preserving the annual tonnage.
- **Insurance**: adults at purchase price, young stock at market value incl. festival uplift; 4%/yr.
- **Break-even bisection**: reported ₹781.06/kg gives NPV = −0.00 exactly; search clamped to the schema ceiling and reports `None` beyond it.

### Lactation (`lactation.py`)
- **Wood's incomplete gamma** `Y = a·t^b·e^(−ct)` with b = 0.6 (published river-buffalo Wood fits b = 0.465-0.677), c = b/peak_day, peak day 65 default (t* = b/c = 65 d; published river-buffalo peaks span ~day 46-113; Murrah-specific fits report peak yields 10.9-11.5 kg/d — the model's 10.2 kg/d peak-month average at 2,100 L is consistent). Curve **sums to exactly 2100.0 L** over 10 months; peak month = 3 (days 61-91 contains day 65); peak/avg = 1.476 (docstring ~1.5 ✓); first month 6.28 L/d (docstring ~6.3 ✓); late-lactation monthly decline 0.81-0.93 (persistency ~0.93 ✓, recorded Murrah 89-93%); Wood model is the best fit for Murrah in the literature (Luna-Palomera 2021).
- Geometric shape: first-month peak 285 L then ×0.93, normalized to the same total.
- Dairy-mode lactation overlay (milking status independent of pregnancy state) mass-consistent: finishing-pen culls keep milking until dry-off and the cull books at dry-off — verified the reported `lactating_does` never exceeds state pools + finishing.

### Monte Carlo / sensitivity / planner / optimizer
- Determinism: two runs of `run_monte_carlo` produce identical `model_dump()`; different seeds differ; fixed draw order with disabled variables still consuming RNG (common random numbers).
- Gaussian copula: factor-loadings construction is PSD by construction; correlation 0.60 default within bounds; triangular inverse-CDF verified empirically (mean 1.0000, support [0.85, 1.15]).
- Event shocks: episode start probabilities `1−(1−p)^(1/12)`; measured long-run frequencies 0.112/0.158/0.113 per year for configured 0.10/0.15/0.10 (within 1σ of 2,000 run-years).
- Percentiles: numpy-`linear`-equivalent order statistics (P5 of 1..100 = 5.95); bands monotone.
- Sale planner (`planner.py`): `_match_target_fills` queueing, purchase-backed class policy, earliest-supply-month impossibility rule, chunked events within schema caps; `close_gaps` converged (80 male growers at m30 → 121 does bought at m14, all targets met, mass balance intact).
- Optimizer mechanics (on a constructed feasible scenario): axis-thinning keeps all dimensions; `_rank_key` for all three objectives re-derived; the infeasibility at defaults is covered in MEDIUM-2, not a ranking bug.
- Milk planner: `_service_ladder` expected services/conception = 1.733 for rates [.3825, .3825, .45] (hand-verified), repeat-breeder fraction 0.210 (matches defaults.py's "~17-21%/yr disposal" claim); monthly revenue = litres × effective ₹61.2/L exactly.

### Numeric defaults vs published/market data (Telangana 2025-26)

| Default (file) | Value | Verified market/literature | Verdict |
|---|---|---|---|
| `meat_price_per_kg` (assumptions.py:327) | ₹370 | Hyderabad Osmanabadi listings ₹350-450/kg; farm-gate/bulk ₹230-350; Bakrid +40% | OK |
| `eid_price_uplift` (:356) | 0.35 | Bakrid premiums "up to 40%" in major mandis | OK |
| `cull_doe_price_per_kg` (:330) | ₹220 | female listings ₹220-350, spent animals near floor | OK |
| `concentrate_price_per_kg` (:479 / murrah ₹26) | ₹25-26 | compound cattle feed ₹22.4-27.6/kg (branded 50-kg bags ₹1,120-1,380) | OK |
| `dry_price_per_kg` (:476) | ₹5.0 | paddy straw bales ₹5-6/kg Hyderabad (IndiaMART/JustDial) | OK |
| `green_price_per_kg` home/purchased (:474-475, murrah 0.8/2.5) | ₹0.8-1.0 / ₹2.5 | farm-gate ₹0.80-3.50/kg (napier contracts ₹0.80; retail higher) | OK |
| `labour_per_month` (:546 / murrah ₹16,000) | ₹14,000-16,000 | Telangana notified unskilled monthly ₹12,750-16,000 (Apr-2025 G.O.); 2026 revision higher | OK (upper band) |
| `vet_per_animal_per_year` (:541 / murrah ₹2,000) | ₹450 / ₹2,000 | ₹400-600/head/yr goats; dairy retainers higher | OK |
| `milk_price_per_kg_fat` (defaults.py:327) | ₹900 (₹61.2/L @ 6.8%) | Vijaya buffalo procurement ₹85/L @ 10% fat = ₹850/kg fat (Apr 2025) | +5.9% over procurement; documented blend — see MEDIUM-4 |
| `milk_fat_pct` (:328) | 6.8% | Murrah 6.0-7.5% | OK |
| `lactation_milk_litres` (murrah) | 2,100 L/305 d | CIRB breed standard 2,000 kg; NDRI 1,750-1,850 first lactation; purchased proven 2nd-lactation mid-range | OK |
| `conception_rate` (murrah 0.45/service AI) | 0.45 | field AI 40-50%/service | OK |
| sexed semen (0.90 female, ×0.85 conception) | — | field trials 88-91% female, 8-15 pp penalty | OK |
| `gestation_months` (murrah 10 ≈ 310 d) | 10 | Murrah ~310 d | OK |
| `doe_purchase_price` murrah ₹1,10,000 | — | lactating 2nd-parity Murrah ₹75k-1.5 L (Karnal/Bengaluru listings) | OK |
| buffalo cull ₹160/kg live (meat_price murrah) | — | export plants ₹300-320/kg carcass ÷ ~52-55% dressing ≈ ₹155-165/kg live | OK (conversion verified) |
| `male_calf_price_per_head` ₹1,600 | — | week-old bull calf ₹1,200-1,800 | OK |
| `kid_pre_weaning` 0.10 | — | NABARD 15%; Osmanabadi field 10.9-20.4% | LOW-8 (optimistic) |
| buffalo calf mortality 0.10 | — | organized Murrah farms ~8% | OK |
| Murrah weights (31 kg birth / 215 kg yearling / 345 kg @24 mo / 520 kg adult @40 mo) | — | CIRB calf weights 30-31.7 kg; NDRI growth studies | OK |
| `insurance_pct` 4%/yr | — | livestock cover 4-6% | OK |
| discount 12%, agri loan 9-11%, 85:15 margin (goat), 20-yr shed/7-10-yr equipment lives | — | NABARD conventions | OK |
| Osmanabadi weights (12.1 kg @3 mo, 17.0 @6, 20.5 yearling, doe 33/buck 42 kg) | — | ICAR-AICRP/NARI field weights; NBAGR descriptors | OK |
| kidding interval implied (5 gest + 1 open + ~1 to conceive ≈ 7-9 mo) | — | published Osmanabadi intervals 232-297 d | OK |

### README claims
"dairy lactation-curve simulation (peak yield, persistency, summer heat-stress trough, lean-season price premium) with Monte-Carlo milk-price risk" — **all five elements verified present and numerically correct** (Wood peak/persistency; yield multipliers 0.86-0.90 Apr-Jul; price premium 1.02-1.07 Mar-Jul; milk-price triangular risk in MC + market-crash episodes). "310-day gestation, 60-day VWP, 3-service cull, male-calf sales at birth, fat-based procurement" — all present (defaults.py:251-266, 362-363, 311-327). No inflated claims found beyond the preset-viability concerns in MEDIUM-3/4 (which are about outcomes, not features).

### Frontend
`page.tsx` is a display/editor layer: numbers round-trip through the typed Orval client unchanged; the only numeric processing is formatting (`toFixed`, `toLocaleString`); `persisted-numbers.ts` thresholds (₹0.005, 0.0005 kg) mirror the DB `Numeric(14,2)`/`Numeric(15,3)` column precisions exactly; the editor rejects non-finite input before it reaches the assumptions object. No projection math is duplicated client-side.

---

## TEST EXECUTION SUMMARY

- `pytest tests/test_lactation_curve.py tests/test_simulation_engine.py tests/test_simulation_financials.py` → **248 passed**.
- `pytest tests/test_simulation_{advanced,milk_planner,planner,explain,fuzz,finance_mutation}.py` → **155 passed**.
- `pytest tests/test_simulation_api.py` (file) → 31 failed / 6 errors — all sampled tests pass individually (LOW-7). `tests/test_dairy.py` + `test_cull_price_calibration.py` share the same leakage pattern.
- Scratch verification scripts: `/tmp/audit_a4/t1_consistency.py`, `t2_drivers.py`, `t3_lactation.py`, `t4_mc.py`, `t5_calibration_optimizer.py`, `t6/t7_optimizer*.py`, `t8_dscr_planner.py`, `t9_plan_trend.py`, `t10_festival.py`, `t11_final.py` (run with `backend/.venv/bin/python`).

## Sources consulted (market data)

- Vijaya Dairy procurement revision (₹85/L buffalo @10% fat, Apr 2025): [DairyDimension](https://west.dairyindustryexpo.com/vijaya-dairy-to-increase-milk-prices-from-april-4-dairydimension/), [Dairy News Today](https://dairynews.today/milkypedia/organization/vijaya_dairy_8476601/)
- Karimnagar Dairy fat-rate history: [The Hindu](https://www.thehindu.com/news/national/telangana/karimnagar-dairy-increases-milk-price-diversification-on-the-anvil/article30560926.ece)
- Goat live-weight, Hyderabad/Osmanabadi: [TradeIndia Hyderabad](https://www.tradeindia.com/hyderabad/live-goat-city-196467.html) and IndiaMART/Justdial listings (₹350-450/kg retail, ₹230-350 farm-gate, Bakrid +40%)
- Compound cattle feed / ingredients: Brinda Foods market guide (₹1,120-1,380 per 50 kg), IndiaMART/TradeIndia listings (maize ~₹25/kg, soybean meal ₹24-55/kg, wheat bran ₹15-24/kg)
- Paddy straw Hyderabad: [IndiaMART ₹6/kg bales](https://www.indiamart.com/proddetail/paddy-straw-bales-2855340099655.html), [JustDial Hyderabad suppliers](https://www.justdial.com/jdmart/Hyderabad/Paddy-Straw/jdm-1324560-ent-2-17728702)
- Murrah prices: Pashushala Bengaluru (₹1.1 L, 2nd parity), Karnal dairy listings (₹75k-1.5 L)
- Cull buffalo economics: [Indian Express — India's $5 bn buffalo meat trade](https://indianexpress.com/article/india/india-buffalo-meat-exports-5-billion-10839359/) (₹300-320/kg carcass)
- Telangana minimum wages: [Telangana Labour Dept](https://labour.telangana.gov.in/MinRatesWages.do), [SGCMS Apr-2025 notification summary](https://www.sgcms.com/regulatory-updates/minimum-rate-of-wages-telangana-april-2025/)
- Kid mortality: [NABARD model bankable scheme](https://www.slideshare.net/slideshow/model-bankable-scheme-on-goat-nabard-10-animals/122765575) (15%), [Barbind et al., Osmanabadi pre-weaning mortality](http://arccarticles.s3.amazonaws.com/webArticle/articles/381021.pdf) (10.9-20.4%)
- Wood lactation curve: [Luna-Palomera 2021 (Murrah, Wood best fit)](https://www.scielo.cl/scielo.php?script=sci_arttext&pid=S0719-38902021000300200), [Sahoo et al., Murrah models, peak 10.9-11.5 kg](https://pdfs.semanticscholar.org/90f2/367ed2c0de4242ff084d696db2604ee2ab85.pdf), [Omar et al. 2024, MDPI Animals](https://www.mdpi.com/2076-2615/14/8/1248), [Egyptian buffalo peak week 4.5](https://www.researchgate.net/figure/The-estimated-lactation-curve-of-daily-milk-yield-of-the-Egyptian-buffalo_fig1_267651571)

---

**Findings by severity: BLOCKER 0 · HIGH 1 · MEDIUM 3 · LOW 4 · INFO 6.**
