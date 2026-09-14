# Red Team Audit 08 — Aggregates & Simulation API Layer (Parts K + L, units K1–K2, L1–L7)

- **Date:** 2026-09-13
- **Target:** Herdly goat-farm SaaS monorepo, backend (`backend/app/`)
- **Scope:** K1 dashboard aggregate, K2 reports aggregate, L1 defaults/breeds/calibration, L2 herd
  snapshot, L3 run admission control (`api/_run_limits.py`), L4 ad-hoc run, L5 scenario
  CRUD/compare/run, L6 planner run + plans CRUD, L7 ops-sim run.
- **Out of scope:** L8 engine internals (numerical math audited separately; this audit only
  verifies the API layer around the engine — pricing formula inputs, pass-count parity,
  validation boundaries, result-transport guards).
- **Method:** read every implementation end-to-end (`api/dashboard.py`, `api/simulation.py`,
  `api/planner.py`, `api/ops_simulation.py`, `api/_run_limits.py`, `services/dashboard.py`,
  `services/simulation_calibration.py`, `simulation/{assumptions,snapshot,defaults,planner,
  backward_planner,daily_ops}.py`, `schemas/{dashboard,summaries,simulation,ops_simulation,planner}.py`,
  `models/{simulation,planner}.py`, `permissions.py`, `deps.py`, `metrics.py`, `db.py`) and attack
  each hypothesis from the campaign brief; verify enforcement in code; cross-check against the
  regression suites (`test_dashboard_permissions.py`, `test_simulation_api.py`,
  `test_daily_ops_api.py`, `test_planner_api.py`, `test_backward_planner.py`,
  `test_e2e_lifecycle_audit.py`, `test_finance_extended.py` dashboard sections, `test_adversarial.py`).
  Severity per campaign rubric: Critical = cross-tenant/authz bypass/permission-gated data leak;
  High = admission-control bypass/leak or aggregate leak across permission boundaries; Medium =
  defense-in-depth, DoS vectors, info disclosure; Low = hardening; Info = observation.

This surface has been hardened by several prior campaigns and it shows: the classic issues
(cross-farm ids, NaN payloads, quota races, optimistic locking, rollback-before-CPU, lease leaks
on cancellation) are all closed and test-pinned. What remains are boundary-consistency gaps in
the two aggregate endpoints and one transport-size asymmetry in ops-sim. No Critical, no High.

---

## Findings summary

| ID | Severity | Unit | Title |
|----|----------|------|-------|
| RT-KL-1 | **Medium** | K1/K2 | Per-bucket occupancy (incl. pregnancy buckets) and per-bucket average weights disclosed without `buckets.view`/`animals.view` — the aggregate endpoints bypass the permission that gates the same data on its dedicated pages |
| RT-KL-2 | **Medium** | L7 | Ops-sim result payload (`days` + `journeys`) is not covered by the 50k head-day ledger cap; multi-MB responses serialize on the event loop |
| RT-KL-3 | Low | K1 | `ready_to_move_suggestions` uses a `count().over()` window aggregate — the exact anti-pattern `dashboard._exact_total` was written to eliminate |
| RT-KL-4 | Low | K1 | Withheld task/kidding/suggestion totals render as factual `0` (int), not the `None` withheld sentinel the same payload uses elsewhere |
| RT-KL-5 | Low | L3 | `capacity_busy` 429 carries no `Retry-After` (the `cpu_budget` 429 does) |
| RT-KL-6 | Info | K2 | Reports raw counts (`breeding.total_records`, `breeding.kiddings`, `mortality.total_kids_born`) ungated for reports-only callers — deliberate and test-pinned; residual volume disclosure noted |
| RT-KL-7 | Info | L6 | Planner priced at `2 + 9 + risk_runs` passes; worst-case engine work is `12 + risk_runs` — one horizon pass (≤240 units, ~0.04% of budget) underpriced |
| RT-KL-8 | Info | L5/L6 | Scenario/plan name collisions map to 400, quota exhaustion to 409 — status-code split within the same resource is inconsistent (no security impact) |
| RT-KL-9 | Info | L3 | Admission "no queueing" guarantee rests on asyncio's non-suspending uncontended `acquire()` fast path (fragility note, currently airtight); admission state is single-process by design |

---

## RT-KL-1 — Medium — K1/K2: bucket occupancy and bucket average weights bypass `buckets.view` / `animals.view` via the aggregates

**Evidence**

- `backend/app/api/dashboard.py:234-249, 397-402` — the dashboard's `buckets` list
  (`BucketCountOut(code, name, count)`), `total_active` and `sex_counts` are computed and
  returned gated only by `dashboard.view` (`api/dashboard.py:59, 212-214`). The 10 bucket codes
  include the breeding-programme states `BREEDING`, `PREGNANCY_EARLY`, `PREGNANCY_LATE`,
  `DELIVERY`, `RESTING` (`backend/app/models/enums.py:27-35`).
- `backend/app/api/dashboard.py:483-508, 645` — reports' `bucket_rows` return per-bucket
  counts **and** `avg_weight` (mean live weight per bucket via correlated latest-weight
  subquery) gated only by `reports.view`.
- The same data is permission-gated at its dedicated surfaces: the bucket board requires
  `buckets.view` (`backend/app/api/buckets.py:26-31`), and per-animal weight data requires
  `animals.view` everywhere else — the dashboard itself withholds `recent_weights` behind
  `animals.view` (`api/dashboard.py:305-327`) with the rationale "an aggregate view is not a
  side door around field-level authorization" (`api/dashboard.py:6-8`).
- Roles that make this live: `CLEANER` / `CLEANER_MANAGER` hold `dashboard.view` with **no**
  `animals.view`, `buckets.view`, or `breeding.view` (`backend/app/permissions.py:212-227`);
  `ACCOUNTANT` holds `reports.view` without `breeding.view`/`kidding.view`
  (`permissions.py:229-241`); an owner can mint a custom role with only
  `dashboard.view`/`reports.view` (B11 custom-role CRUD).

**Exploit sketch**

1. Cleaner authenticates, `GET /api/dashboard` with the farm's `X-Farm-Id`.
2. Response contains `buckets: [{code: "PREGNANCY_LATE", count: 14}, {code: "BREEDING", count: 9}, …]`
   — the scale and phase of the breeding programme, data `/api/buckets` (403) and
   `/api/animals` (403) both refuse this caller, and which `kiddings_due` (breeding.view-gated,
   `api/dashboard.py:282-291`) intentionally withholds.
3. Same caller class on `GET /api/dashboard/reports`: `bucket_rows[].avg_weight` discloses
  per-bucket mean live weights (animals.view-derived aggregate) to a reports-only custom role.

**Impact** — insider-only, counts/averages only, same farm. But it crosses two permission
boundaries the product itself defines (`buckets.view` for occupancy; `animals.view` for weight
data), hands breeding-programme occupancy to presets with no breeding visibility, and directly
contradicts the module's own stated doctrine. Unlike the reports raw counts (RT-KL-6) there is
**no test pinning this as intended**; the adjacent hardening passes (weights, DEAD/CULLED
status totals, breeding rates) all chose to gate.

**Fix** — gate `buckets`/`total_active`/`sex_counts` behind `buckets.view` (or at minimum
collapse pregnancy/delivery/breeding buckets behind `breeding.view`), with the None/empty
withheld convention; gate `bucket_rows` (or at least `avg_weight`) behind
`buckets.view`/`animals.view` respectively. Add regression tests in the style of
`test_dashboard_permissions.py`.

---

## RT-KL-2 — Medium — L7: ops-sim result size unbounded by the head-day cap; event-loop serialization of multi-MB payloads

**Evidence**

- `backend/app/api/ops_simulation.py:81-91` — the `MAX_LEDGER_HEAD_DAYS = 50_000` cap
  (`schemas/ops_simulation.py:33`) is enforced **only when `include_ledger=true`**. Without the
  ledger flag, a maximal run — 500 head (`daily_ops.py:249`), 365 days (`daily_ops.py:246,
  schemas/ops_simulation.py:41`) = 182,500 head-days — is admitted for a budget cost of
  `365 × (1 + 500//10) = 18,615` units (`ops_simulation.py:96`), ~2.9% of the 650k window.
- The result itself is large: `DailyOpsResult.days` carries one `DayRecord` per simulated day,
  each with `tasks`, `moves`, `births`, `exits`, `feeding` (per building), `occupancy` (per
  building) (`backend/app/simulation/daily_ops.py:399-409`), plus `journeys` per animal
  (starters and every birth) with per-hop history (`daily_ops.py:412-436, 467-481`). The
  router's own comment concedes "model_dump/ledger of a maximal herd is seconds of CPU and tens
  of MB" (`api/ops_simulation.py:100-103`).
- `_finite_payload(result.model_dump())` runs inside `_offload` (worker thread — good,
  `api/ops_simulation.py:98-107`), but the response `DailyOpsRunOut` is then serialized by
  FastAPI's `jsonable_encoder` + `JSONResponse.render` **on the event loop**, stalling every
  tenant for the serialization duration.

**Exploit sketch** — an identity with `simulation.view` alternates `POST /api/ops-sim/run`
with 500-head/365-day herds (each admitted; ~34 per 300s budget window; per-user and per-farm
1-in-flight + 2 global slots cap concurrency at 2). Each response stalls the loop for the
multi-MB encode and burns seconds of worker CPU. Two cooperating identities (owner + a
provisioned worker both holding `simulation.view`) keep both global slots saturated.

**Impact** — bounded (slots + budget + 500-head/365-day schema caps) but a real cross-tenant
latency/stall vector, and an inconsistency: the Markdown *derivative* of the run is capped at
50k head-days while the JSON *primary* result is not.

**Fix** — apply the head-day ceiling to the result itself (or a `MAX_RESULT_HEAD_DAYS` of
similar magnitude) regardless of `include_ledger`, and/or move response serialization into the
offloaded worker (build the JSON payload there). Optionally price ops-sim units against
measured serialization cost, not just engine passes.

---

## RT-KL-3 — Low — K1: `count().over()` window aggregate in move suggestions

**Evidence** — `backend/app/services/dashboard.py:206-211`:

```python
candidates = select(context, func.count().over().label("suggestions_total")).where(qualifies)
```

`api/dashboard.py:81-91` (`_exact_total`) documents precisely why this shape was removed
elsewhere: an unpartitioned window aggregate must drain its whole input before the LIMIT emits,
and the `dashboard_animal_context` subquery carries **five correlated scalar subqueries per
row** (two latest-weight lookups, latest bucket move, open-pregnancy, withdrawal EXISTS —
`services/dashboard.py:61-136`). Every dashboard load for an `animals.view` holder evaluates
those for every ACTIVE animal (qualifying or being filtered), not just the 100-row preview.

**Impact** — on a many-thousand-head farm each dashboard load costs seconds of DB CPU with
temp-spill risk; the 30s statement timeout (`core/config.py:115`) bounds the worst case, and
the attacker is primarily their own tenant. Perf/DoS-resilience hardening.

**Fix** — reuse `_exact_total` (uncorrelated scalar subquery over the filtered base) for
`suggestions_total`, keeping the preview bounded.

---

## RT-KL-4 — Low — K1: withheld totals rendered as factual zero for task/kidding/suggestion sections

**Evidence** — `api/dashboard.py:252-257` (`todays_tasks_total`/`overdue_tasks_total`/
`ultrasounds_due_total` default `0` when `tasks.view` missing), `:283-284`
(`kiddings_due_total = 0` without `breeding.view`), `:298-299` (`suggestions_total = 0`
without `animals.view`); schema types are plain `int` (`backend/app/schemas/dashboard.py:46-58`)
while the sibling sections use the documented `None` withheld sentinel
(`schemas/dashboard.py:54-67`, `api/dashboard.py:286-288`: "rendering a permission gate as a
factual zero would let herd decisions ride on truncated data"). An `ACCOUNTANT`
(dashboard.view + animals.view, no tasks.view) receives `todays_tasks_total: 0` —
indistinguishable on the wire from "no duties today".

**Impact** — the exact confusion the None convention was introduced to prevent, but on
sections whose data is operational rather than clinical. The suggestion gate is test-pinned as
0 (`test_dashboard_permissions.py:224`), so this is partly deliberate; the asymmetry is the
finding.

**Fix** — either widen the `None` sentinel to these totals (schema + P7 fail-closed rendering),
or document the split contract in the schema the way `cull_candidates_total` is documented.

---

## RT-KL-5 — Low — L3: `capacity_busy` 429 has no `Retry-After`

**Evidence** — `backend/app/api/_run_limits.py:370-374` raises the busy 429 with no headers;
the budget 429 sets `Retry-After: 300` (`_run_limits.py:297-301`, asserted in
`test_simulation_api.py` budget tests). A client backing off on `capacity_busy` has no server
hint; runs can take ~25s worst case, so naive immediate retries just re-429.

**Fix** — add a conservative `Retry-After` (e.g. 5-30s) or document the busy 429 as
retry-immediately-safe in the OpenAPI description.

---

## RT-KL-6 — Info — K2: breeding/kidding raw counts stay visible to reports-only callers (deliberate)

**Evidence** — `api/dashboard.py:590-604` (`total_records`, `kiddings` ungated),
`:636-642` (`total_kids_born` ungated while `total_deaths`/`stillborn` are health.view-gated).
`test_dashboard_permissions.py:151-161` pins this explicitly: "The raw counts behind the rates
aren't breeding-identity data; they stay." An `ACCOUNTANT` (no breeding.view/kidding.view)
therefore learns the farm's service volume, kidding count and kids-born total. Recorded as an
observation: the counts do disclose reproductive-programme volume across those permission
boundaries; the project's tested position is that only the derived rates are gated. No action
required unless the product reclassifies volume data.

---

## RT-KL-7 — Info — L6: planner pricing under-counts one engine pass

**Evidence** — priced `(2 + 9 + risk_runs) × horizon` (`api/planner.py:229-231`). Actual
worst case: initial `_evaluate` (1) + `close_gaps` initial `_evaluate` (1) + up to 8 iteration
re-evaluations (`simulation/planner.py:381-482`, `max_iterations: int = 8`) + stage-plan
`_run_core` (`simulation/backward_planner.py:477`) + `risk_runs` = `12 + risk_runs` passes.
Under-priced by ≤1 horizon pass = ≤240 units (~0.04% of the 650k window; a maximal planner
request 511×240 = 122,640 stays far under budget). Direction and magnitude are immaterial; fix
by pricing `3 + 9 + risk_runs` if the constant is ever touched.

---

## RT-KL-8 — Info — L5/L6: name-collision status code inconsistency

**Evidence** — duplicate scenario name → 400 both on the pre-check and on the
`IntegrityError` fallback (`api/simulation.py:179, 486-489, 631-637`); planner identical
(`api/planner.py:174, 312-315, 422-426`); quota exhaustion → 409
(`api/simulation.py:473-474`). The campaign brief expected 409 semantics for uniqueness;
implemented as 400 consistently within each router, so this is API-surface consistency only
(no authz or integrity impact). Harmonize to 409 if the contract allows a breaking change, or
pin 400 in the OpenAPI description.

---

## RT-KL-9 — Info — L3: fragility note on the check-then-acquire admission fast path

**Evidence** — `_run_limits.py:369-379`: the 429 busy check reads `locked()` on the user lock,
farm lock and global semaphore, then acquires them in order. This is safe today **only**
because CPython's uncontended `asyncio.Lock.acquire()`/`BoundedSemaphore.acquire()` set state
and return without ever yielding to the loop, and `release_capacity()` (release + dict pop) is
fully synchronous — so no other coroutine can interleave between a passing check and the three
acquisitions (a contended lock at check time produces the 429 instead). If a future asyncio
change (or a debugging/coverage hook) makes the uncontended acquire yield, the guarantee
degrades silently from "429, never queue" to "queue behind the current run". The single-process
nature of all admission state (documented at `_run_limits.py:5-13, 106-108`) is enforced by the
one-worker Dockerfile (R1) and is the other load-bearing assumption. Suggestion: a
meta-test asserting `_with_run_limits` raises 429 (not awaits) under two same-principal
concurrent entries already exists (`test_run_limiter_429_while_run_in_flight`); consider also
pinning the no-yield property of the acquire sequence.

---

## Per-unit attacked-and-held notes

### K1 — Dashboard aggregate (`GET /api/dashboard`, `api/dashboard.py:212-445`)

Field-by-field permission audit of `DashboardOut`:

| Field(s) | Gate | Verdict |
|---|---|---|
| `buckets`, `total_active`, `sex_counts` | `dashboard.view` only | **RT-KL-1** (buckets.view/breeding.view bypass) |
| `status_totals` | DEAD/CULLED stripped without `health.view` (`:66, 404-416`) | Held — matches reports convention |
| `todays_tasks*`, `overdue_tasks*`, `ultrasounds_due*` | `tasks.view` (`:258-280`); queries not executed at all without it (totals stay 0 — see RT-KL-4); rows via `task_scope` (farm-scoped, owner-all/worker role+personal visibility, `services/tasks.py:823-895`) + `actionable_pending_task_predicate` | Held — identical visibility to the task board; ULTRASOUND task exposure matches the board's own contract |
| `kiddings_due*` | `breeding.view` (`:282-291`); query not executed otherwise | Held (total-zero convention → RT-KL-4) |
| `cull_candidates*` | `breeding.view`; total `None` when withheld | Held — mirrors `animal_out` cull gating (`api/_shared.py:220-223`) |
| `suggestions*` | `animals.view`; SQL-level `include_breeding` filter reduces rules to the market rule (male kid age/weight exit) for non-breeding viewers (`services/dashboard.py:198-205`) | Held — no breeding state leaks; the suggestion reasons embed tag+weight, correctly gated |
| `restricted_animals*` | list behind `animals.view`; `reason` (clinical narrative) behind `health.view` (`:347-395`) | Held — mirrors `_shared.animal_out`'s effective-hold disclosure (`_shared.py:224-249`); test-pinned (`test_e2e_lifecycle_audit.py:1277-1310`) |
| `recent_weights*` | `animals.view`; free-text `notes` additionally needs `health.view` (`:431-441`) | Held — test-pinned in `test_dashboard_permissions.py:44-92` |

Cross-farm: every query filters `farm_id == farm.id` (bucket counts `:241-243`, status `:331`,
cull `:115-119`, kiddings `:160`, weights `:313-317`, restrictions `:350-357` and the
`MovementRestrictionAction` lookup `:369-381`); `BucketDefinition` is global reference data.
The kidding-due preview's overdue/upcoming split with per-side totals is correctness-only.
Perf: preview lists bounded at 100 with exact totals via `_exact_total` scalar subqueries;
`ready_to_move_suggestions` is the one remaining window aggregate (RT-KL-3).

### K2 — Reports aggregate (`GET /api/dashboard/reports`, `api/dashboard.py:448-663`)

- Field gating: breeding rates (`conception_rate`, `first_cycle_rate`, `kids_per_kidding`,
  `twin_rate`) and cull preview behind `breeding.view`; mortality block
  (`total_deaths`, `deaths_by_month`, `stillborn`, `stillborn_rate`) behind `health.view`;
  `status_counts` strips DEAD/CULLED without `health.view` (`:636-660`). All test-pinned
  (`test_dashboard_permissions.py:105-189`).
- Raw counts `total_records`/`kiddings`/`total_kids_born` ungated — deliberate (RT-KL-6).
- `bucket_rows` incl. `avg_weight` ungated — RT-KL-1.
- SQL correctness spot-checks: conception/first-cycle SQL mirrors
  `models/helpers.ASSESSED_OUTCOMES`/`CONCEIVED_OUTCOMES` exactly (`helpers.py:34-42`,
  parity comment at `:48-50`); `_rate` returns `None` on zero denominators (`:555-556`) — no
  NaN/ZeroDivision; `kids_per_kidding` guarded by `kiddings_count` truthiness (`:595-599`);
  per-kidding alive counts use an outer join so zero-kid kiddings stay in the denominator
  (`:559-577`); the latest-weight scalar subquery is `LIMIT 1` (no cardinality 500,
  `:460-469`) and projected once before aggregation (`:475-495`). Deterministic row ordering
  pinned on both GROUP BYs.

### L1 — Defaults / breeds / calibration (`api/simulation.py:265-402`)

- `/defaults/breeds` and `/defaults` are `CurrentUser`-only global reference data; nothing
  farm-specific is read (verified — `get_preset` is pure, `simulation/defaults.py:238-243`);
  unknown breed → 400.
- `/calibration` enforces **all six** permissions as independent dependencies
  (`SimView`, `AnimalsView`, `BreedingView`, `KiddingView`, `FeedingView`, `FinanceView`,
  `api/simulation.py:366-371`). Data-path audit of `services/simulation_calibration.py`:
  reads Animal + WeightRecord (animals.view ✓), BreedingRecord (breeding.view ✓),
  KiddingRecord + KidEntry (kidding.view ✓), Transaction (finance.view ✓), FeedInventory
  (feeding.view ✓). **No health or task reads** — no permission gap found.
- `lookback_months` bounded `[6, 60]` by `Query(ge=6, le=60)` (`:374`).
- GET is advisory-only: `calibrate_farm_assumptions` performs SELECTs exclusively — no
  `add`/`flush`/`commit` anywhere in the service (verified end-to-end).
- Empty-farm division safety: every ratio guarded (`_annual_fraction_from_exposure` zero
  guards `:98-101`, conception needs ≥5 assessed `:472`, litter ≥5 kiddings `:535`, sex ratio
  needs `alive_rows` `:640`, kid mortality needs `weaned_rows` `:656`, seasonality needs
  `sale_calibrated > 0` `:872`, vet needs `current_head > 0` `:1037`, labour divides by
  `cost_months ≥ 1` and `labour_headcount ≥ 1` `:1006-1018`, curve rescale clamped
  `:128-133`, output clamped to schema ceilings `:185`, adult-weight reconciliation before the
  final validate `:1077-1090`). A calibrated set the schema still rejects → clean 500 with a
  fixed message (`:391-399`); only `get_preset` ValueErrors map to 400.
- Farm scoping: every query filters `farm.id`.

### L2 — Herd snapshot (`GET /api/simulation/herd-snapshot`, `api/simulation.py:285-354`)

- Dual gate verified: `SimView` + `animal_perms: AnimalsView` (`:289-295`) — the consultant
  with only `simulation.view` gets 403, matching the in-code rationale.
- Cohorts are computed from sex + age only (`age_months` over coalesced DOB; unknown age →
  adult); **no bucket, pregnancy or breeding state is consulted**, so the response cannot
  leak pregnancy occupancy beyond what `animals.view` itself implies. The age math (year/month
  extraction + day-of-month decrement, `:311-316`) matches `Animal.age_months_on`.
- `breed` drives `age_at_first_breeding_months`; unknown breed → 400 (`:302-305`,
  test-pinned `test_simulation_api.py:717`). Query is farm-scoped + ACTIVE-only.

### L3 — Run admission control (`api/_run_limits.py`) — deep audit

- **Lease lifecycle:** `_with_run_limits` acquires user → farm → global in a fixed order;
  `release_capacity` is idempotent (`released` flag) and runs in `finally` either directly or
  via per-native-run `add_done_callback` when a cancelled request abandoned a live worker
  thread (`:334-399`). Raw-cancellation, queued-cancellation and busy-fast-path lock cleanup
  are all unit-tested (`test_simulation_api.py:1234-1351, 1214-1232`). No path leaves a farm
  permanently 429: even interpreter-edge cases route through the completion callback with a
  `RuntimeError` guard at shutdown (`_run_limits.py:79-85`).
- **No queueing:** proven airtight for the current asyncio (see RT-KL-9 reasoning); the
  busy check itself is inside the `try` precisely so 429s don't leak keyed-lock dict entries.
- **Budget:** priced `passes × horizon_months`; single maximal request = (1+52+2000+17+300) ×
  240 = 568,800 < 650,000 by construction. Checked via `would_exceed_any` (prospective,
  pure probe never allocates), charged **before** the run but **after** admission (a 429-busy
  request never spends budget, `api/simulation.py:255-260`); both `("user", id)` and
  `("farm", id)` principals charged — switching farms cannot evade the per-user budget and
  multiple identities on one farm share the farm budget. `check`+`charge` are synchronous
  with no await between them → atomic on the loop. Sliding window uses `time.monotonic`
  (`:148`), lazy per-bucket pruning, 50k-key cardinality ceiling with tiered LRU eviction that
  can never turn into a global admission outage (pure probes don't allocate; eviction tests at
  `test_simulation_api.py:1370-1520`). Budget 429 carries `Retry-After: 300`
  (busy 429 doesn't — RT-KL-5).
- **Pricing vs engine parity (API layer):** horizon capped 12–240 and `monte_carlo_runs ≤
  2000`, `max_candidates ≤ 300`, sensitivity fixed 17 (`assumptions.py:72, 754, 810`); engine
  confirmed to run exactly `monte_carlo_runs` passes (`montecarlo.py:382-406`), candidates
  bounded by `max_candidates` (`optimization.py:210`), break-even always computed
  (`engine.py:2211, 2245`). Events list ≤500 (`assumptions.py:884`) is per-month bookkeeping,
  not extra passes. Planner parity in RT-KL-7 (one pass under, never over). Ops-sim priced
  `days × (1 + head//10)`; head ≤500 and days ≤365 enforced at **both** schema layers
  (`schemas/ops_simulation.py:41-43`, `daily_ops.py:246-249`) — RT-KL-2 covers the residual
  transport asymmetry.
- **Budget evasion attempts, all failed:** compare prices `Σ(53 × horizon)` and forces
  `monte_carlo=sensitivity=optimization=False` in both pricing and execution
  (`api/simulation.py:579, 588`) — five minimal scenarios cost ≥ 5×53×12, no bypass;
  `run_scenario` prices its query flags (`:675-683` → `_run_for_farm` with the same flags);
  planner rejects `max(offsets) > 240` **before** `_with_run_limits` and any charge
  (`api/planner.py:217-227`; test `test_backward_plan_ceiling_rejection_is_not_charged`).
- **Connection handling:** all three routers snapshot + `db.rollback()` before entering
  `_with_run_limits` (ad-hoc `api/simulation.py:426`, scenario run `:674`, planner
  `api/planner.py:197`, ops-sim `api/ops_simulation.py:60`; compare rolls back after scenario
  load, before offloads `:582`). Test-pinned
  (`test_simulation_cpu_phase_releases_database_checkout`). Pool 5+10 with 30s checkout
  timeout and 30s statement timeout — two concurrent off-transaction runs leave the pool
  intact.
- **Global 2-slot DoS:** two ~25s maximal runs do block all simulation-family runs
  process-wide, but per-user=1 forces two identities, the budget (650k/570k) allows only one
  maximal run per identity per window, and this trade-off is documented
  (`_run_limits.py:120-123`). Metrics counters exist for both rejection reasons
  (`metrics.py:98`; recorded at `_run_limits.py:296, 370`).

### L4 — Ad-hoc run (`POST /api/simulation/run`, `api/simulation.py:405-435`)

- Input validation: `SimulationAssumptions` is `strict=True`, `extra=forbid`, and every float
  is `FiniteFloat = Annotated[float, Field(strict=True, allow_inf_nan=False)]`
  (`simulation/assumptions.py:17, 65-66`) — JSON `NaN`/`Infinity` literals parse (stdlib json
  accepts them) but are rejected 422 at the model boundary. Magnitude caps
  (`MAX_MONEY`/`MAX_WEIGHT_KG`/…) prevent finite-but-huge overflow (`assumptions.py:33-46`).
- Output guard: `_finite_payload(result.model_dump())` → 422 (`api/simulation.py:198-201`),
  inside the offloaded worker. Planner and ops-sim carry the identical guard
  (`api/planner.py:262-263`, `api/ops_simulation.py:104-105`).
- No persistence: the only mutation before CPU is `db.rollback()`; the engine is pure;
  verified no rows are written on this path.

### L5 — Scenario CRUD / compare / run (`api/simulation.py:438-683`)

- Quota: `pg_advisory_xact_lock(4713, farm.id)` taken before `execute_idempotent`, serializing
  count+insert per farm (`:461-463`); overflow → 409 (`:473-474`); concurrency-safe test
  exists (`test_saved_scenario_limit_is_concurrency_safe`). The advisory-not-FOR-UPDATE
  rationale (FK key-share deadlock avoidance) is documented at `:452-460`.
- Name uniqueness: pre-check + `IntegrityError` fallback (`:164-179, 485-489, 631-637`) with a
  per-farm unique constraint (`models/simulation.py:29`); 400 not 409 (RT-KL-8).
- Revision optimistic lock: PATCH takes `with_for_update` and rejects stale
  `expected_revision` with 409 (`:601-614`); concurrent-update test exists. Create/list need
  `simulation.view`; create/patch/delete need `simulation.manage` (owner-implies-all via
  `perms_for`, `deps.py:626-631`).
- Stored-JSON revalidation: `_load_assumptions` 422s invalid rows; list marks `valid=False`
  with `assumptions=None` instead of 500 (`:92-142`); after-commit PATCH responses use
  `allow_invalid=True` (`:641`); stale-row tests exist (`test_stale_scenario_row_never_500s`).
- Compare: `ids` ≤128 chars; dedup via `dict.fromkeys`; non-int → 400; out-of-int32 → 400;
  >5 distinct → 400; unknown/cross-farm id → uniform 404 from farm-scoped `_get_scenario`
  inside the lease (released correctly) (`:540-591`). Budget charged once from the loaded
  assumptions before any engine work (`:579-585`). GET-with-CPU is auth-header protected, so
  no CSRF/prefetch exposure.
- Run stored scenario: 404 cross-farm, 422 invalid assumptions, priced with the caller's
  MC/sensitivity/optimization query flags (`:654-683`).
- Listing bounded `limit ≤ 50`, `offset ≤ MAX_PAGE_OFFSET`, deterministic `id` order (`:507-537`).

### L6 — Planner (`api/planner.py`)

- `/plan`: malformed/at-start targets → 422 pre-admission; `max(offsets) > 240` → 422
  **before** locks and budget (`:210-227`); `horizon = min(240, max(...))` bounded; cost
  `(2+9+risk_runs) × horizon` with `risk_runs ≤ 500` (`schemas/planner.py:58`) → max 122,640
  units; one-pass underpricing noted (RT-KL-7). Engine `ValidationError` → 422 with bounded
  error list; `ValueError` → 422; non-finite report → 422 (`:246-263`).
- Plans CRUD: quota under advisory namespace **4715** (namespace-collision test exists),
  revision optimistic lock, name uniqueness, farm-scoped 404 uniformity — all mirroring L5 and
  test-covered (`test_planner_api.py`).
- Anchor re-bake: create bakes `start_year_month` into the stored assumptions
  (`:297-308`); PATCH with a new anchor re-validates the stored document (422+rollback on
  stale rows, not a mid-write 500 — `:399-417`) and re-bakes even when only the anchor moved
  or assumptions were replaced in the same request. Targets are re-validated by the `/plan`
  run path, which rejects targets at/before the new anchor.

### L7 — Ops-sim (`POST /api/ops-sim/run`, `api/ops_simulation.py`)

- Permission: `simulation.view` only (a toy-herd editor input; reads no farm data — the herd
  is fully client-supplied), verified no farm-table reads on this path.
- Herd coherence: `DailyOpsInput` strict re-validation with per-animal and cross-animal
  validators (sex/bucket mismatches, gestation windows per bucket, overstayed quarantine,
  duplicate tags, `daily_ops.py:181-307`); rejections surface as 422 with at most three
  sanitized error objects (input stripped to `type/loc/msg` — `api/ops_simulation.py:70-79`).
- Ledger cap → 422 (not truncation) when `horizon × head > 50_000` (`:81-91`,
  test `test_run_ledger_rejects_oversized_runs`). Result-payload asymmetry → RT-KL-2.
- Seed: user-controlled, bounded ±2^62 (echo-bound rationale documented,
  `schemas/ops_simulation.py:31-33`), deterministic, nothing persisted — no cache-poisoning
  surface. Budget/lease semantics shared with L3 (429 tests exist in `test_daily_ops_api.py`).

### Cross-cutting checks

- Every farm-scoped endpoint takes `CurrentFarm` (X-Farm-Id validated upstream, B1); defaults
  endpoints deliberately farm-free and contain only global reference data.
- No raw SQL in any in-scope router (advisory locks use `func.pg_advisory_xact_lock` with
  bound literals).
- Metrics: `record_simulation_admission_rejection` used by all three routers with fixed-label
  reasons (`cpu_budget`, `capacity_busy`) — no cardinality risk.
- Simulation result responses are bounded: `MonteCarloResult` carries percentile bands
  (3 × horizon floats), not per-run series (`simulation/results.py:233-268`); `months ≤ 240`
  rows (`results.py:328-331`). Ops-sim is the only large-payload surface (RT-KL-2).

## Unconfirmed / not reproducible from code alone

- RT-KL-9's asyncio fragility argument is a proof-from-implementation, not an executed attack;
  it depends on CPython's current `Lock.acquire` fast path never yielding. No exploit exists
  today; flagged as a fragility so a future runtime change cannot silently weaken the 429
  contract.
- Response-size estimates for maximal ops-sim runs (RT-KL-2) are derived from the model shape
  and the code's own "tens of MB" comment; not measured against a live 500×365 run in this
  audit.
