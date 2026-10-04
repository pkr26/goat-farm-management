# Simulation Engine & Concurrency Audit (2026-10-01)

Auditor 08 — numerical correctness of `backend/app/simulation/**` + concurrency/reliability of background work and async patterns across the backend. Every in-scope file was read line-by-line; numeric claims below were re-verified by executing the engine with the repo venv (read-only; no source modified, no servers started).

## Executive summary

The simulation package is in unusually good shape for a financial engine. The core cohort accounting satisfies its mass-balance identity to float noise (worst |error| = 5.7e-14 over a 120-month default run, verified by execution), runs are bit-reproducible per seed, the Monte Carlo uses one per-run `random.Random(seed)` with a fixed draw order (no shared/global RNG, no cross-run correlation), percentile interpolation matches numpy's `linear` method exactly, and the EMI/annuity math is written with `expm1/log1p` so it stays exact down to the r→0 limit (verified against the textbook formula and at a 1e-17 rate). The finance layer's Decimal-isolation IRR solver, the phase-vs-annual mortality converters (a documented 10% phase rate removes exactly 10% over 3 months, verified), and the amortization balloon handling are all correct.

Concurrency-wise, the API admission-control layer (`app/api/_run_limits.py`) is carefully engineered: user→farm→global lock ordering is consistent, the busy-check-then-acquire sequence is atomic under single-threaded asyncio (uncontended asyncio primitives do not suspend), leases transfer to native-completion callbacks so a cancelled HTTP request cannot make a running engine pass invisible to admission control, and idle keyed locks are dropped to prevent cardinality leaks. The screening worker's heartbeat lease renews during long cycles, SIGTERM cancels the active cycle with rollback, and the loop exits nonzero after a bounded number of consecutive failures instead of crash-looping silently. Background loops in `app/main.py` re-raise `CancelledError` everywhere, use per-batch sessions with commits, and log (never swallow) iteration failures.

No Critical findings. The confirmed issues are: a misleading Monte Carlo risk statistic (`prob_dscr_below_one` reads 0.0 precisely when coverage is unmeasurable — verified with a 12-month moratorium under a 12-month horizon where year-1 EBITDA of −₹366,850 cannot cover ₹182,997 of interest), admission leases held across request-scoped DB reads in scenario compare, two cross-model mortality/pricing inconsistencies between the monthly engine and the daily-ops engine, a mid-class pricing fallback for boundary grower stock in degenerate `afb/sale_age == 6` configurations, and a mixed 30.44-day/calendar-month convention inside calibration exposure math. One business-level observation deserves escalation even though the arithmetic is internally consistent: the untouched default preset (50+2 Osmanabadi, the docstring's "NABARD bankable unit") produces a deterministic NPV of −₹8.56 lakh with an MC mean of −₹13.6 lakh.

Severity counts: 0 Critical, 2 High, 5 Medium, 7 Low, plus Info/positive observations.

## Findings

### [High] `prob_dscr_below_one` reports 0.0 exactly when debt coverage is unmeasurable — misleading risk figure shown to users
Location: `backend/app/simulation/montecarlo.py:438` (`weak_dscr_runs += int(core.min_dscr is not None and core.min_dscr < 1.0)`), denominator `:479`.
Evidence (verified by execution, 12-month horizon + 12-month moratorium, default otherwise):
```
deterministic min_dscr: None | yr1 interest: 182,997 | yr1 ebitda: -366,850
prob_dscr_below_one: 0.0 (all runs unmeasurable)
```
Every run whose horizon contains no principal-repaying year contributes 0 to the numerator but stays in the denominator. The deterministic metric is honestly `None` and `explain.py` narrates "no DSCR computed", but the MC probability is surfaced unqualified (`explain.py:1101` puts it in the risk figures) and reads as "0% chance of a coverage breach" in the financing shapes (long moratorium, short horizon, or `loan_fraction=0`) where the interest is in fact uncovered.
Impact: A lender-facing risk statistic understates risk to zero in exactly the fragile configurations; the same shape affects `plan_probabilities`-adjacent comparisons.
Fix: Track a third counter for unmeasurable runs and either report `prob_dscr_below_one` as `None` when any (or a majority) of runs are unmeasurable, or footnote the share of unmeasurable runs in `MonteCarloResult` and in the narrative.

### [High] Admission leases (one of two process-wide simulation slots) held across request-scoped DB reads in scenario compare
Location: `backend/app/api/simulation.py:608-624` (`run_compare` inside `_with_run_limits`), `backend/app/api/_run_limits.py:339-405`.
Evidence: `compare_scenarios` passes `run_compare` to `_with_run_limits`, which acquires the user lock, farm lock and one of two global semaphore slots *before* `run_compare` performs `await _get_scenario(db, …)` ×5, `_load_assumptions` ×5 (JSON parse + full pydantic validation of large assumption documents) and only then `await db.rollback()`. The sibling run paths (`run_adhoc:461`, `run_scenario:715`, planner `:227`, ops-sim `:83`) all deliberately snapshot and roll back *before* entering the limits region, with a comment explaining that the auth transaction must not span CPU work.
Impact: Under DB latency (or a slow `pgbouncer` hop), one of only two process-wide simulation slots is occupied by zero-CPU database/parse work; two such compares can starve all simulation admission deployment-wide while the 429 body claims "capacity is busy" during a CPU-idle period. Confirmed by control flow; conditional on real DB latency to matter.
Fix: Load and validate the scenarios before `_with_run_limits` (as `run_scenario` does), and pass only the detached `loaded` list into the limited region.

### [Medium] Daily-ops engine applies adult annual mortality to weaned kids/growers — inconsistent with the monthly engine's class rates
Location: `backend/app/simulation/daily_ops.py:1387-1389` (`hazard = self.kid_hazard if animal.dependent_kid else self.adult_hazard`).
Evidence: The monthly engine models `kid_post_weaning` (5% per 3-month phase) and `grower` (4%/yr) as distinct classes (`assumptions.py:279-280`, applied at `engine.py:719-726`); the daily-ops engine jumps straight from the pre-weaning hazard to the adult hazard the day a kid is weaned (or orphan-weaned). With defaults, a weaned 2-month weaner faces 5%/yr instead of the ~5%/3-month post-weaning phase rate.
Impact: The two projections of the same farm disagree on young-stock survival — the operational view is systematically more optimistic about weaner loss than the financial view the bank sees. Numerically wrong under realistic inputs at the margin, not a crash.
Fix: Derive a weaner/grower daily hazard from `kid_post_weaning` (phase) and `grower` (annual) equivalents in `DailyOpsParams`, mirroring `_daily_hazard`'s existing kid window.

### [Medium] Boundary grower stock priced at mid-class fallback age when the grower chain is empty (afb/sale_age == 6)
Location: `backend/app/simulation/engine.py:363-393` (`_pool_avg_weight` fallback), `:851-865` (event-sale draws).
Evidence: With `age_at_first_breeding_months == 6` (or `sale_age_months == 6`), `f_grower`/`m_grower` is an empty list and starting/event stock sits in `f_boundary_grower`. A sale event then calls `_pool_avg_weight(f_grower, 6, g, doe_w, f_grower_mid_age)` where `f_grower_mid_age = 6 + (afb-6)//2 = 6`, and prices the draw at `weight_at_age(6)` although the boundary animals are at graduation age (afb / sale_age, valued elsewhere at `weight_at_age(afb)`, e.g. `:777`).
Impact: Schema-valid but degenerate configuration under-prices event sales of boundary stock (the same animals the purchase path values at the heavier graduation weight). Edge case; small herds with early-maturing breeds (Black Bengal afb=9 unaffected; only explicit afb=6 inputs hit it).
Fix: Use the graduation age (afb / sale_age) as the fallback for the empty-chain boundary pools, matching `:777` and `:790`.

### [Medium] Calibration mixes a 30.44-day month convention with calendar `add_months` boundaries in mortality exposure
Location: `backend/app/services/simulation_calibration.py:79-84` (`_months_between` uses `/30.44`) vs `:724-742` (class windows via `add_months(dob, from_age)`).
Evidence: Exposure lengths per class are measured in 30.44-day "months" (`overlap = _months_between(...)`), while the class boundaries themselves are calendar months (`add_months`). A February-born animal's "3-month" boundary lands 2-3 days off the 30.44-day count, so `deaths / (animal_months/12)` can be attributed to a class with a slightly mismatched denominator.
Impact: Per-class annualized mortality biased by up to ~±2% in edge months; bounded by the 12-animal-month / 10-animal evidence gates and the 0.9 clamp. Not a crash; small systematic error in calibrated rates.
Fix: Compute overlaps in days and convert once at the end (`overlap_days / 30.44`), keeping the boundaries in a single convention.

### [Medium] Ops-sim result growth from in-sim births is bounded only by a priced heuristic, not a hard output cap
Location: `backend/app/api/ops_simulation.py:52-66` (`MAX_RESULT_HEAD_DAYS`, `_BIRTH_AMPLIFICATION_FACTOR = 10`), `backend/app/simulation/daily_ops.py:1896-1898`.
Evidence: Input head-days are capped at 50,000, but the result `days`/`journeys`/`feeding` lists scale with *standing* head, which births multiply — the code's own measurement shows 500 starters growing to 4,300 animals. The cap admits ~50k input head-days ⇒ up to ~10× in `DayRecord`/`FeedLine` rows; the code comments acknowledge "tens of MB" per maximal response and price it ~11× in the CPU budget.
Impact: A single maximal admissible request materializes a multi-ten-MB Python object graph and serializes it (in the worker thread, at least). Bounded and priced, but the *memory* bound is the heuristic, not an enforced ceiling — the resource trap is amortized, not removed.
Fix: Enforce an output-side cap (e.g. truncate `journeys`/`feeding` beyond N head-days and note it in `notes`), or downsize `MAX_RESULT_HEAD_DAYS`.

### [Medium] Default preset is deeply loss-making while its documentation frames it as the bankable unit
Location: `backend/app/simulation/assumptions.py:1-6` (docstring), `defaults.py:95-107`; verified by execution.
Evidence: `SimulationAssumptions()` (50+2 Osmanabadi, 120 months, all defaults) → deterministic NPV **−₹856,292**, `minimum_cash_balance` −₹1,576,247, `avg_dscr` 0.37; `run_monte_carlo` mean NPV **−₹1,363,459** (p5 −₹1,925,483). The mass-balance identity holds to 5.7e-14 and the debt schedule reconciles by hand (project ≈ ₹2.0M, 85% loan at 11%), so this is not an arithmetic bug — the leveraged 50+2 unit at 2025-26 Telangana cost defaults simply does not clear 12%.
Impact: Every "default" run a user first sees says NOT VIABLE. That is a business-calibration decision (conservative realism vs. aspirational preset), but it contradicts the in-code framing ("a complete, valid … NABARD-style run", "the flagship 50+2 unit") and deserves an explicit owner's decision.
Fix: Either re-baseline the preset's price/cost calibration to a bankable-plausible unit, or rename/documented-flag the default as a stress-conservative scenario.

### [Low] Empty-herd run still books ₹6,000/month of cultivation cost (and a ₹746k negative NPV) for zero animals
Location: `backend/app/simulation/engine.py:1376-1387` (home-grown fodder charged on what is grown), verified by execution.
Evidence: `HerdAssumptions(does=0, bucks=0, max_breeding_does=0)` → `feed_cost` ₹6,000/month (3 acres × 6 t DM/acre/yr ÷ 0.25 DM ÷ 12 × ₹1/kg), NPV −₹746,331 with zero head.
Impact: Degenerate-input edge; the acreage-is-a-decision rule is documented, but an empty farm defaulting to 3 cultivated acres produces a nonsense-looking "cost with no animals" projection. No division-by-zero anywhere in the empty-cohort path (verified: `land_acres`, DSCR, operating margin all degrade gracefully).
Fix: Skip cultivation cost when `feed_total.green_dm_kg == 0` (nothing is grown for nothing), or surface a warning.

### [Low] Planner's marginal-doe mirror lets serviced dams skip the service-month attrition
Location: `backend/app/simulation/planner.py:347-375`.
Evidence: `ready.pop(month_index)` executes before the end-of-month `_attrition` sweep, so a dam served in month m survives that month risk-free while her post-conception mass attracts attrition from month m onward; the remaining (failed) does are re-added to `ready[m+1]` and *do* receive month-m attrition.
Impact: Small systematic optimism in the purchase seed for `close_gaps` (documented as ~2% accurate, conservative on far windows; the forward re-run converges regardless). Zero-progress rollback catches phantom yield when it matters.
Fix: Apply attrition to the popped dam mass before splitting into `kidding`/`ready[m+1]`.

### [Low] Monte Carlo retains three full trajectory matrices for band construction
Location: `backend/app/simulation/montecarlo.py:431-433, 443-451`.
Evidence: `herd_paths`/`cash_paths`/`liquidity_paths` hold `runs × horizon` Python floats each — at schema max (2000 × 240) that is ~1.4M floats per matrix (~100+ MB as CPython lists) before the percentile bands collapse them.
Impact: Transient memory spike bounded by the schema and the CPU budget; a 2000-run MC is also priced near the 650k-unit window so it cannot be looped. Resource-amortized, not dangerous.
Fix: Build per-month columns online (accumulate then discard) or use `array('d')`.

### [Low] Break-even bisection assumes monotone NPV in meat price with no tolerance/verification
Location: `backend/app/simulation/engine.py:2056-2084`.
Evidence: 50 bisection steps on `[0, MAX_MONEY]` with only endpoint sign checks. Higher meat price also raises young-stock insurance value (`stock_value` uses the deseasonalized base price), so NPV is *practically* monotone but not proven; a non-monotone profile could return a non-minimal crossing.
Impact: Theoretical; every executed configuration was monotone (default break-even correctly `None` — beyond the schema ceiling). No tolerance check on the returned bracket width (50 halvings of 1e9 ⇒ ~₹1e-6/kg precision, ample).
Fix: Optional mid-course sanity: assert `npv_at(mid)` sign consistency or document the monotonicity assumption next to the loop.

### [Low] Worker cycle holds one DB session (and its connection) for the entire screening cycle
Location: `backend/app/worker/__init__.py:193-196`, pool `core/config.py:1452-1455` (size 2 + overflow 2).
Evidence: `async with sessionmaker() as db:` wraps `_await_cycle_with_heartbeats(run_screening_cycle(db, …))` — a cycle can legitimately run many minutes (up to 50 images × provider timeouts). The worker's dedicated pool is sized for exactly this, and the pipeline commits per stage and snapshots ORM state before mid-cycle rollbacks (`pipeline.py:641-655`), so this is a deliberate design, not a leak.
Impact: With the worker's own 2+2 pool and single-flight loop, connection starvation cannot occur in-process; only a co-deployed second consumer of the same database would notice. Conditional.
Fix: None required; worth a comment on the sessionmaker call site stating the whole-cycle lifetime is intentional.

### [Low] `_run_cost` pricing trusts `run_simulation`'s pass counts staying in sync with engine internals
Location: `backend/app/api/simulation.py:255-276`; planner `app/api/planner.py:265-266`.
Evidence: `_BREAK_EVEN_PASSES = 52`, `_SENSITIVITY_PASSES = 19` (1 + 9×2), `max_candidates` for optimization, `monte_carlo_runs` for MC — all currently exact (verified against `run_optimization`'s baseline+grid and `run_sensitivity`'s nine cases; the planner's `2 + (2+CLOSE_GAPS_MAX_ITERATIONS) + risk_runs` matches `build_backward_plan`'s worst case including the zero-progress rollback re-evaluation). The module docstring itself warns this "is only honest while one pass is linear in the horizon".
Impact: Any future engine change (e.g. break-even iterations tuned, a new analysis block) silently under-prices admission. Maintenance trap, not a current bug.
Fix: Derive the constants from the engine (export pass-count functions next to the loops, as `CLOSE_GAPS_MAX_ITERATIONS` already does).

### [Low] `daily_ops` weaned-grower hazard asymmetry also applies to creep-band grouping using the first member's age
Location: `backend/app/simulation/daily_ops.py:749-757`.
Evidence: `kg_per_head = creep_daily_kg(members[0].age_days(day))` prices the whole band at the first (tag-sorted) member's age. Band labels group ages, but a band straddling a ramp step prices every member at one representative age.
Impact: Gram-level per-head variance inside creep bands; totals shift by at most one ramp step × band size. Cosmetic-to-minor.
Fix: Use the band's mean age or per-head `creep_daily_kg` accumulation.

### [Info] Screening worker lifecycle is robust
`app/worker/__init__.py` — heartbeat written at every state transition via atomic `mkstemp`+`replace` (`heartbeat.py:32-48`); `working` lease renewed at `min(60, health_max_age/3)` while the cycle makes loop progress; SIGTERM cancels the active cycle (session rolled back by context manager; stale PROCESSING claims recovered by the pipeline's own reaper); consecutive-failure counter resets on success and exits nonzero at the configured limit (no silent busy-loop: inter-cycle sleep is the full poll interval ≥ 30 s; disabled deployments idle on a half-health-age sleep and keep publishing `disabled`). The only swallow is `OSError` on heartbeat writes, which is deliberately logged-and-continue so the probe, not the worker, decides.

### [Info] Concurrency sweep elsewhere in the backend came back clean
`rg` for `asyncio.gather/create_task/TaskGroup/run_in_executor/to_thread/threading./Lock/Semaphore` across `backend/app`: hits are `app/main.py` background loops (all re-raise `CancelledError`, per-batch `async with get_sessionmaker()` + `commit`, generic `Exception` logged — none swallowed), `app/security.py` password hashing on a dedicated executor with bounded slots, `app/api/team.py` awaited `create_task(hash_password_async)` with an idempotency gate, screening `to_thread` offloads for blocking S3/image work, and `_run_limits`. No un-awaited coroutines, no `gather(return_exceptions=True)` masking failures, no `time.sleep`/blocking `requests` on the loop, no DB sessions held across CPU offloads in the run paths (the one exception is the High finding above). `backend/pins/` is dependency pinning for the pip-audit/uv toolchain (hash-locked `uv==0.12.1` bootstrap and a generated `pip-audit.txt`), not simulation code. No simulation job/queue tables exist in alembic — runs are synchronous in-request, persisted only as `simulation_scenarios`/`planner_plans` rows (revision-checked with `FOR UPDATE`; quota races closed by `pg_advisory_xact_lock` namespaces 4713/4715).

## Coverage manifest

| File | Status |
| --- | --- |
| `backend/app/simulation/__init__.py` (171) | Read fully |
| `backend/app/simulation/engine.py` (2208) | Read fully, both chunks |
| `backend/app/simulation/montecarlo.py` (696) | Read fully |
| `backend/app/simulation/optimization.py` (318) | Read fully |
| `backend/app/simulation/planner.py` (854) | Read fully |
| `backend/app/simulation/backward_planner.py` (618) | Read fully |
| `backend/app/simulation/daily_ops.py` (1898) | Read fully, both chunks |
| `backend/app/simulation/assumptions.py` (1180) | Read fully |
| `backend/app/simulation/defaults.py` (275) | Read fully |
| `backend/app/simulation/results.py` (353) | Read fully |
| `backend/app/simulation/explain.py` (1194) | Read fully, both chunks |
| `backend/app/simulation/finance.py` (559) | Read fully |
| `backend/app/simulation/market.py` (163) | Read fully |
| `backend/app/simulation/feed.py` (91) | Read fully |
| `backend/app/simulation/shocks.py` (37) | Read fully |
| `backend/app/simulation/snapshot.py` (43) | Read fully |
| `backend/app/simulation/vocabulary.py` (57) | Read fully |
| `backend/app/worker/__init__.py` (275) | Read fully |
| `backend/app/worker/__main__.py` (7) | Read fully |
| `backend/app/worker/heartbeat.py` (51) | Read fully |
| `backend/app/services/simulation_calibration.py` (1164) | Read fully, both chunks |
| `backend/pins/` (pip-audit.in, pip-audit.txt, uv.txt) | Inspected — toolchain pinning only |
| `backend/app/api/simulation.py` (724) | Read fully (concurrency/lifecycle lens) |
| `backend/app/api/ops_simulation.py` (142) | Read fully |
| `backend/app/api/_run_limits.py` (406) | Read fully (shared admission machinery) |
| `backend/app/api/planner.py` (run-limit call sites: 1-330, 480-530) | Read (concurrency lens) |
| `backend/app/core/config.py` (worker/screening settings) | Read targeted sections (433-510, 802-915, 1261, 1434-1577) |
| `backend/app/main.py` (background loops, lifespan) | Read targeted sections (180-270, 380-700) |
| `backend/app/services/notifications/*`, `idempotency.py`, `screening/pipeline.py` | Sweep + targeted reads (commit/lock/claim paths) |
| Alembic versions | Searched for simulation job tables (none; `simulation_scenarios` = `a1b2c3d4e5f6`, `planner_plans` = `a1b2c3d4e5f6` family) |

Coverage of the mandated per-file scope: 100% (all 17 simulation modules, 3 worker files, calibration, pins, both API files plus the shared `_run_limits` they delegate to).

## Positive observations

1. **Mass balance and determinism verified by execution.** The engine's head-count identity (`total_herd = prev + births + purchases − deaths − sales − culls`) holds to 5.7e-14 over the default 120-month run; identical inputs produce identical NPVs; MC results reproduce exactly per seed (`montecarlo.mean` equal across re-runs).
2. **RNG discipline.** Per-run `random.Random(a.risk.seed)`, fixed `_DRAW_ORDER`, draws consumed even for disabled variables (common random numbers preserved when toggling risks), bootstrap stream sub-seeded with a salt, `daily_ops` uses a single stream over tag-sorted animals in fixed phase order. No global/shared RNG anywhere in scope.
3. **Numerically careful finance.** `monthly_emi` via `expm1/log1p` (verified exact against the textbook form at 11%/60m and non-singular at a 1e-17 rate), final-instalment clamp so no balance walks off the schedule, multi-root IRR isolation with a Decimal branch gated on integral exponents and a bounded scan fallback, gross-series BCR (not sign-split), `None` instead of inf for undefined ratios.
4. **Correct rate conversions.** `phase_monthly_mortality_rate` makes a documented 10% phase rate remove exactly 10% over 3 months (verified 0.10 → 0.10); annual→monthly compounds exactly (verified 20% → 20%); triangular inverse-CDF reproduces its mean (verified 1.0000); `_litter_probabilities` ladder sums to 1 with the exact target expectation.
5. **Admission control engineering.** Check-then-acquire is race-free under single-threaded asyncio; lock order user→farm→global is uniform across both routers; cancelled requests transfer leases to native-completion callbacks (a running engine pass can never become invisible to the 429 path); the sliding cost window has O(1) tiered LRU eviction with a cardinality ceiling; multi-process deployment is detected and loudly warned.
6. **Planner CPU pricing is exact.** The `plan_sales` cost formula `(2 + (2 + CLOSE_GAPS_MAX_ITERATIONS) + risk_runs) × horizon` matches the true worst-case pass count of `build_backward_plan`, including the zero-progress rollback re-evaluation; `_purchases_from` counts its event budget by arithmetic before materializing anything (the RT-L8-1 OOM vector is closed at two independent layers).
7. **Snapshot isolation of assumptions.** Every mutating driver deep-copies (`model_copy(deep=True)`) and revalidates before running; `run_simulation` revalidates the whole document once per public run; MC `_apply_draws` clamps to the same schema bounds the public API enforces; `BREED_PRESETS` module-level instances are never handed out mutably (`get_preset` builds fresh; no caller mutates the dict entries — verified by `rg`).
8. **Worker reliability patterns worth copying.** Atomic heartbeat files (mkstemp + fsync + replace, 0600), lease renewal keyed to the health-staleness budget, bounded consecutive-failure exit, provider-transport cleanup on shutdown, and per-stage ORM snapshots that survive mid-cycle rollbacks.
9. **Non-finite defense at the boundary.** `_finite_payload` walks every dumped result and turns NaN/inf into a clean 422 on all four run surfaces (run, scenario run, compare, planner, ops-sim, DPR).
10. **Guarded degenerate inputs throughout.** Empty cohorts, zero bucks, zero loan, `sex_ratio` 0/1, `conception_rate` 0, denormal marginals, and event-count overflow each fail loudly as 422 or degrade gracefully — verified by executing the empty-herd and 12-month-horizon runs.
