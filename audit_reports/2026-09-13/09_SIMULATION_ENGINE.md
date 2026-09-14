# RT-L8 — Simulation Engine Internals (Red Team Audit)

**Target:** Herdly goat-farm SaaS, Unit L8 — the 18 `backend/app/simulation/` modules plus `backend/app/services/simulation_calibration.py`.
**Date:** 2026-09-13 · **Auditor:** red-team subagent (numerical focus) · **Scope doc:** `audit_reports/2026-09-13/00_RED_TEAM_AUDIT_SCOPE.md` (L8).
**Out of scope (sibling units):** `api/simulation.py`, `api/planner.py`, `api/ops_simulation.py` boundary behavior, `api/_run_limits.py` admission control (its pricing formulas are referenced here only to grade amplification).

## Methodology

1. Full read of all 18 engine modules (11,351 lines total) and the 1,116-line calibration service; targeted read of `api/_run_limits.py` (budget unit = one engine pass × one simulated month, window 650,000 units / 5 min), the planner/ops-sim cost formulas, and API exception wrapping.
2. Attack hypotheses executed per the scope brief: input-bound completeness → unbounded memory/CPU; zero/negative division on validated input; non-finite propagation (inf/NaN through percentiles); determinism/seed threading; compounding overflow; lactation-curve domains; recursion/iteration caps; budget-evasion via population growth; shared-preset mutation; module-level caches; calibration divide-by-zero on sparse data.
3. Dynamic probes on the live code (Python 3, repo venv defaults): backward-planner division-by-zero (3 variants), `close_gaps` purchase-event materialization (bounded + unbounded runs with memory profiling), daily-ops worst-case fertility amplification (priced-vs-honest cost), float-overflow semantics (`/`, `*` → inf, no exception), maximal-growth engine finiteness (all cohorts at `MAX_HEAD`, 240 months), Monte-Carlo determinism (byte-identical repeat), worst-case MC wall time (2000×240), and a 293-case randomized daily-ops state-machine fuzz for illegal-move `AssertionError`s.
4. Static greps: `lru_cache|@cache` (only one per-run `cached_property`), `global`/`DEFAULTS[` (none), `BREED_PRESETS` external mutation (none), `while` loops (all bounded except the finding), unseeded `random.*` module-level draws (none — every draw goes through `random.Random(seed)`).

Severity rubric per task brief: Critical = cross-tenant/authz; High = unbounded compute/memory within priced budget, engine exception → 500 on validated input, cross-request contamination; Medium = silent wrong results, non-determinism, division-by-zero 500s, bounded budget evasion; Low = hardening.

---

## Findings

| ID | Severity | Title |
|----|----------|-------|
| RT-L8-1 | **High** | `close_gaps` materializes an unbounded number of purchase events before validation → OOM/CPU exhaustion within priced budget |
| RT-L8-2 | **Medium** | Backward-planner requirement chain: `ZeroDivisionError` → opaque 500 on schema-valid assumptions |
| RT-L8-3 | **Medium** | `daily_ops` births amplify compute ≈ 8.5× beyond the initial-head-priced budget (bounded by biology) |
| RT-L8-4 | **Low** | Near-zero `fodder_yield_t_dm_per_acre_year` (bound `gt=0.0` only) produces absurd/overflowing land requirement |
| RT-L8-5 | **Info** | `explain.py` family-labour imputation uses raw `math.ceil`, not the engine's ULP-snapping `_ceil_head_ratio` |
| RT-L8-6 | **Info** | Calibration can legitimately emit `conception_rate=0.0` / `sex_ratio_female∈{0,1}` — the exact values that arm RT-L8-2 |

### RT-L8-1 — High: `close_gaps` purchase-event materialization bomb

**Evidence (all `backend/app/simulation/planner.py`):**
- `planner.py:471` — `needed = math.ceil(fill.shortfall / marginal)`; `marginal = _marginal_kids_per_doe(...)` (planner.py:248) scales linearly with `reproduction.conception_rate`, which the schema bounds only as `ge=0.0` (assumptions.py:187-189). `shortfall` can be up to `MAX_HEAD = 100,000`.
- `planner.py:491-508` — `_purchases_from` chunks `needed` head into `HerdEventAssumptions` objects of ≤ `MAX_HEAD` each in an unguarded `while remaining > 0.0` loop. The 500-event schema cap (assumptions.py:884) is only enforced **afterwards**, when `_plan_assumptions` (planner.py:167-172) validates the finished list — the guard the docstring relies on ("fails validation loudly — by design") fires only after the entire list exists in memory.

**Exploit sketch (measured):** `POST /api/planner/plan` with assumptions `{"meta":{"horizon_months":240}, "reproduction":{"conception_rate":1e-9}, "herd":{"does":0,"bucks":0,"auto_purchase_bucks":false}}` and one target `male_grower ×100,000` at month 200 (all schema-valid; the endpoint's own horizon pre-check extends to the target).
- Measured `marginal ≈ 1.645 × conception_rate`; `needed = 6.08e13` head → **608,013,590 pydantic event objects** built before the first validation rejection.
- Bounded calibration run (`conception_rate=6.07e-7` → 1.0M events): **1.8 s wall, 835 MB peak RSS** (incl. the `model_dump` of the event list). The 608M-event run was still executing after **6 minutes** when killed; linear extrapolation ≈ 18 min CPU and ~500 GB RSS → the single-process uvicorn worker (`_run_limits.py` documents exactly one worker) is OOM-killed → **whole-backend, cross-tenant outage**.
- Budget evasion: the endpoint prices this request at `(2 + 9 + 0) × 240 = 2,640` units — 0.4% of the 650k window — so the request is admitted trivially and can be repeated; the chunking loop is invisible to the passes×months pricing model.

**Impact:** authenticated user with planner permission exhausts worker memory/CPU → denial of service for every tenant; no data exposure.

**Fix:** bound the work *before* materializing: (a) floor `marginal` (e.g. skip/raise when `needed > MAX_HEAD × (500 - existing events)` or when `marginal` implies fewer than, say, 0.01 head/doe); (b) count chunks inside `_purchases_from` and raise `ValueError` (→ existing 422 path at `api/planner.py:246-256`) the moment the 500-event cap would be exceeded; (c) optionally cap `needed` at a maximum purchasable head per iteration and let the 8-iteration loop report "cannot close within limits".

### RT-L8-2 — Medium: backward-planner `ZeroDivisionError` → 500 on validated input

**Evidence (`backend/app/simulation/backward_planner.py`):**
- `backward_planner.py:268-270` — `per_birth_of_sex = r.litter_size × (1 - stillbirth) × sex_share`; `total_kids_needed = math.ceil(kids_of_sex_needed / per_birth_of_sex)`. `sex_share` is `sex_ratio_female` (female target) or `1 - sex_ratio_female` (male target); the schema allows `sex_ratio_female = 0.0` and `1.0` (assumptions.py:215), so `per_birth_of_sex` is exactly `0.0` for the matching target sex.
- `backward_planner.py:273-274` — `does_bred = math.ceil(does_kidded / conception)`; `_effective_conception` (backward_planner.py:230-244) returns `1 - (1-rate)^cap`, which is exactly `0.0` when `conception_rate = 0.0` (schema-valid, `ge=0.0`).

**Exploit sketch (executed, all three raise `ZeroDivisionError: float division by zero`):**
1. `conception_rate=0.0`, 10 does + buck, target `male_grower ×5` at 2027-06.
2. `sex_ratio_female=1.0`, same herd, `male_grower` target.
3. `sex_ratio_female=0.0`, `female_weaner` target.

**Impact:** `POST /api/planner/plan` crashes: `api/planner.py` (lines 246-263) maps `ValidationError` and `ValueError` to 422 but **not** `ZeroDivisionError` → generic opaque 500. Severity Medium per rubric (division-by-zero 500); note values 0/1 for the sex ratio are exactly what an all-female sexed-semen strategy or a small all-male-born season produces, and the calibration service can emit them (RT-L8-6).

**Fix:** in `_requirement_chain`, guard both denominators and degrade to an "unachievable under these assumptions" chain (mirroring the existing missed-deadline branch at backward_planner.py:512-534), or add cross-validators (`sex_ratio_female` strictly inside (0,1) when planner targets of both sexes exist; `conception_rate > 0` required by the planner path).

### RT-L8-3 — Medium: `daily_ops` compute amplification ≈ 8.5× the priced budget

**Evidence:**
- Pricing (reference): `api/ops_simulation.py:96` — `cost = payload.horizon_days * (1 + len(payload.animals) // 10)` charges **initial** head only.
- Engine: `daily_ops.py` iterates every active animal through ~13 per-day phases (`_DailyOpsRun.run`, daily_ops.py:1419-1438; `_breeding`, `_mortality`, `_feed_lines`, etc. each do `sorted(self._active())`), and in-sim **births multiply the head count** with no total-head cap (only `MAX_START_HEAD = 500` on the *input*, daily_ops.py:103).

**Measured:** 475 does (starters in DELIVERY at bred day 149) + 25 bucks at `MAX_START_HEAD`, params maxed (`conception_rate=1.0, litter_size_mean=4.0, female_fraction_at_birth=1.0, stillbirth/abortion/mortality=0.0, failed_services_before_cull=6, buck_doe_ratio=100`), horizon 365 → **3,800 births, 4,300 total animals, 4.4 s CPU**; honest cost ≈ `365 × (1 + 4300//10)` = 157,315 units vs 18,615 priced → **8.5× amplification**. The amplification is *bounded*: ≤ 2 kiddings/doe/year × litter ≤ 4, and daughters cannot reach the 10-month breeding gate within the 365-day horizon ceiling (verified: no second-generation kiddings). So worst case is a ~4-5 s run, repeatable ~35× per budget window instead of the honest ~4× — budget evasion of a bounded ratio, not unbounded DoS; concurrency (2 global slots, 1/farm) further caps blast radius.

**Fix:** price with a head-growth factor (e.g. `horizon_days * (1 + 9 * head // 10)`) or cap total-born head at, say, 10× start head with a note.

### RT-L8-4 — Low: near-zero fodder yield overflows the land requirement

**Evidence:** `engine.py:2111` — `land_acres = avg_annual_green_dm / (feed.fodder_yield_t_dm_per_acre_year * 1000.0)`; the bound is `gt=0.0` only (assumptions.py:579). Measured: `fodder_yield_t_dm_per_acre_year = 1e-300` (schema-valid, 50-doe herd) → `land_requirement_acres = 1.53e301` (finite but absurd); with a larger green-DM demand the division overflows to `inf`. Probe confirmed Python float `/` returns `inf` (no exception), so the API's `_finite_payload` guard turns it into a clean 422 — defense holds (pinned by `tests/test_simulation_fuzz.py::test_derived_overflow_is_a_clean_422_never_500`). The Monte-Carlo driver already acknowledges this hazard with its `5e-324` clamp (montecarlo.py:240-246); the deterministic path has none.

**Fix:** raise the schema floor to something agronomically sane (e.g. `ge=0.01`).

### RT-L8-5 — Info: narrative labour imputation drifts from the engine's rounding

`explain.py:765` computes the family-labour opportunity cost with `max(0.5, math.ceil(2.0 * does / threshold) / 2.0)` while the engine (engine.py:375-388, 1606-1609) uses `_ceil_head_ratio` (ULP-snapping before `ceil`). At a does/threshold ratio that lands within 8 ULPs of an integer the two can differ by half a labour unit — a purely narrative/cash-figure inconsistency. Cosmetic.

### RT-L8-6 — Info: calibration outputs can arm RT-L8-2

`simulation_calibration.py:642` writes `sex_ratio_female = female_alive / len(alive_rows)` — legitimately `0.0` or `1.0` for a small all-one-sex season; `:483` writes `conception_rate = conceived / len(assessed)` — legitimately `0.0`. Both stay schema-valid and pass the final `model_validate` (simulation_calibration.py:1108), but they are exactly the values that crash the backward planner (RT-L8-2). Advisory: same fix as RT-L8-2 covers it.

---

## Per-module attacked-&-held notes

- **assumptions.py** — Every numeric field carries min/max incl. magnitude caps (`MAX_MONEY`, `MAX_WEIGHT_KG`, `MAX_HEAD`, `MAX_LACTATION_LITRES`, `MAX_FODDER_*`, seasonal ≤ 10, risk ≤ 100); NaN/inf rejected at 422 via `FiniteFloat(allow_inf_nan=False)`; lists length-capped (events ≤ 500, seasonality exactly 12, parity ≤ 12); coherent cross-validators (loan+subsidy ≤ 1, moratorium < term, afb ≤ max_doe_age, adult weight ≥ yearling, age-window order, event/festival months within horizon, sexed-policy coherence). Only gaps found: the two 0/1-valued fields feeding RT-L8-2 and the un-floored fodder yield (RT-L8-4).
- **engine.py** — Compute is O(horizon × fixed pool lengths), **head-count independent** (probe: 100,000 does × 240 months uncapped growth = 0.02 s; the cohort-float design has no per-animal loop). Maximal schema-valid growth (all 8 cohorts at `MAX_HEAD`, litter 4, retention 1, sex ratio 1, zero mortality, gestation 1/lactation 1/open 0, 240 months) reaches final herd 6.35e36 / NPV 3.7e51 — still finite (litter hard-capped at 4.0/2.0, kidding cycle ≥ 2 months → < 2^240 head; products stay ≪ 1e308). Full public run incl. 52-pass break-even + IRR on that body: 0.04 s, all-finite. Divisions guarded: `_draw`/`_pool_avg_weight` (>0 checks), `avg_monthly_opex` (horizon ≥ 12), `weight_at_age` (`adult_age ≥ 13`), `_ceil_head_ratio` (divisor ≥ 1, finite heads → `round()` cannot see inf), `preg_female_fraction/conceived` (>0), dairy mixed-foundation `k_months = max(1, round(1/max(conception,1e-9)))` (conception=0 safe, no 1e9-length allocation), `_add_purchased_does` empty-range defense. `held_males` dict ≤ `festival_hold_months ≤ 12` keys; `breeding_vintages` ≤ events + 2·240; per-vintage depreciation loop ≤ ~240k ops. `run_simulation` revalidates the whole document (engine.py:2230), defeating programmatic-mutation bypasses. Break-even bisection capped at 50 iterations.
- **montecarlo.py** — One `random.Random(seed)` stream threaded through correlated draws, event shocks and the AR(1) annual process; disabled variables still consume draws (common random numbers); bootstrap sub-seeded `seed ^ 0xB0057EED`. Probe: two identical runs → byte-identical results. Overflow paths bounded: `_triangular_log_sd` ≤ ~23.3 (low ≤ 1 ≤ high ≤ 100) → `exp(scale·z)` needs |z| ≈ 43 to overflow vs Gaussian-tail ≈ 5-6 across 480k draws; histogram zero-width division guarded incl. the `|lo| ≥ 2^53` case; `_apply_draws` clamps every perturbation back inside schema bounds and revalidates (incl. the `5e-324` fodder-yield floor and two-sided litter clamp). Percentile/`sorted()` NaN corruption is unreachable given finite flows (verified worst case finite). Worst legitimate request (2000 runs × 240 months) measured 35.3 s ≈ priced 480k/650k units — honest.
- **finance.py** — EMI guarded at `r == 0` and zero denominator via `expm1/log1p` (no cancellation ZeroDivision at tiny rates); final instalment clamped; `npv`/`bcr`/`mirr` denominators structurally positive (rates ≥ 0, `pv_costs == 0 → None`); Decimal IRR recursion bounded to ≤ 24 terms with integral exponents, fractional/monthly series gated to the 256-sample float scan (the horizon-23 hot-path inversion is documented and tested); `_bisect_power_sum` 100 steps; rank/dedup tolerances relative.
- **lactation.py** — Wood curve: `peak_day ∈ [1, 365]`, `exp(-c·t)` with `c·t ≤ 0.6` — no overflow; `total <= 0` raises a loud `ValueError` instead of dividing by zero; monthly weights = ≤ 12 × 30 sampled pow calls. `tests/test_lactation_curve.py` pins exact-total normalization, peak placement, both shapes, degenerate inputs — and does not pin anything the schema forbids.
- **market.py** — Compounding `pow(1+rate, (m-1)/12)` with rate ∈ (-1, 1] → base ∈ (0, 2], exponent ≤ 19.92 → ≤ 2^20; Bakrid table static through 2050 with an explicit coverage warning surfaced by the engine.
- **feed.py** — All three DM percentages `gt=0`; divisions by them safe; `land_requirement_acres` divisor is the (unfloored) yield — see RT-L8-4.
- **optimization.py** — Grid thinned to `max_candidates - 1` (≤ 299) deduped engine passes, each with one cached IRR solve; `_sample_grid`'s `while product > limit` provably terminates (a count ≥ 2 decreases each step; capped by total axis cardinality ≤ ~1,500); every candidate revalidated before running; rank keys finite floats. Priced via `max_candidates × horizon` — consistent with measured pass cost.
- **planner.py** (besides RT-L8-1) — `close_gaps` iteration cap 8; `_marginal_kids_per_doe` monthly mirror loop bounded by target month (≤ 240) with tiny pools; `plan_probabilities` seeded from `risk.seed`, `risk_runs ≤ 500` priced into the endpoint; `build_plan_report` revalidates; `_match_target_fills`' `popleft` relies on the engine's invariant of exactly one `EventFill` per sale event (holds — engine appends a fill unconditionally in the sale branch).
- **backward_planner.py** (besides RT-L8-2) — No recursion; calendar math bounded (years 1900-2200, offsets ≤ 240 enforced before budget charge by the endpoint); `ValueError` rejections (empty targets, target before start, > 240 months) all mapped to 422 by the API; stage-plan rows bounded by horizon.
- **daily_ops.py** (besides RT-L8-3) — Strict input validation (unique tags, bucket/sex coherence, gestation-day windows, quarantine day cap, male-in-RECOVERY rejected as strandable); single seeded RNG with tag-sorted fixed-phase draw order — deterministic; 293-case randomized fuzz (random buckets/ages/params incl. pregnancy buckets, orphans, culls) → **zero** illegal-move `AssertionError`s; per-day task volume scales with buildings (≤ 10) not head; result/ledger growth bounded; `MAX_HORIZON_DAYS=365`, `MAX_START_HEAD=500`.
- **results.py / explain.py** — Plain Pydantic models and template text; `_share`/`_pct`/`_inr` guarded for zero totals; no divisions on user-controlled denominators (RT-L8-5 aside); user-echoed content only ever lands inside JSON strings.
- **snapshot.py** — Pure counting; no crash paths from `(sex, age)` tuples.
- **vocabulary.py / defaults.py** — `BREED_PRESETS` module-level instances are never mutated anywhere in `app/` (grep-verified); `get_preset` builds fresh models via factories; `apply_system` deep-copies before mutating. No `global`, no `DEFAULTS[...]` writes.
- **simulation_calibration.py** (besides RT-L8-6) — Every ratio guarded (sample floors 3/5/10/≥5 kiddings, exposure ≥ 12 animal-months and ≥ 10 animals before mortality calibration, `quantity > 0`, `cost_months ≥ 1`, `current_head > 0`, `measured > 0`); outputs clamped to schema ceilings (stillbirth ≤ 0.5 with a warning, litter ∈ [0.5, 4], money ≤ `MAX_MONEY`, weight curve ≤ 1000 kg with the 0.25-4.0 rescale band and isotonic PAVA fit, gestation ∈ [1, 7] months); adult-weight reconciliation before the final `model_validate` prevents the young-herd 400; queries capped at 20,000 rows with truncation warnings; `Decimal→float` conversions only via `float()`.
- **Cross-run contamination (#11/#13)** — No module-level mutable state, no `lru_cache`/`@cache` anywhere in the package (the single `cached_property` lives on a per-run `_CoreResult` dataclass); shocks/plans/draw paths are freshly allocated per run; all RNG state is instance-local. Held.

## Unconfirmed / not reproduced

- None material. (The scope's hypothesis that the monthly engine's per-month cost scales with head count — enabling budget evasion via herd growth — was tested and **refuted**: cohort arithmetic is float-mass-based; the only head-scaled engine is `daily_ops`, graded RT-L8-3.)
