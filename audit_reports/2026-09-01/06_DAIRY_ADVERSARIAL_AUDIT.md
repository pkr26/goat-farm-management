# 06 — DAIRY (BUFFALO) ADVERSARIAL AUDIT

- **Auditor:** RED-TEAM A6 (dairy flows)
- **Date:** 2026-09-01
- **Scope:** milk recording & manipulation, breeding calendar abuse, calving/calf consistency, dry-off/fresh-pen, milk pricing & income, simulation coupling, concurrency, frontend parlour page
- **Method:** full read of `services/milk.py`, `api/milk.py`, `models/milk.py`, `schemas/milk.py`, `services/breeding.py`, `api/breeding.py`, `services/kidding.py`, `api/kidding.py`, `services/tasks.py`, `api/tasks.py`, `services/animals.py`, `api/animals.py`, `finance` schemas/API, `permissions.py`, `species.py`; every exploit below was **reproduced live** against a throwaway Postgres (`goatfarm_audit_a6_test`, alembic head, in-process ASGI client — script at `/tmp/a6_dairy_audit.py`, source tree untouched). Server `today` during the run resolved to 2026-09-02.
- **Test-coverage cross-check:** only issues *not* already covered by `backend/tests/test_dairy.py`, `test_breeding_*`, `test_domain_chronology.py`, `test_concurrency.py` are reported as vulnerabilities.

---

## Findings by severity

| # | Severity | Title |
|---|----------|-------|
| B1 | **BLOCKER** | Dairy dam cannot leave the fresh pen while her calf lives — the +10d duty is permanently uncompletable and unskippable |
| H1 | **HIGH** | Milk recording has zero lactation context — dry, pregnant and day-old calf animals accept yields |
| H2 | **HIGH** | Milk dates have no chronology bound — yields can be backdated before the animal's birth/purchase and shuffled across months |
| H3 | **HIGH** | MILK income is untethered from production — ₹1e9 with no provenance, 1,000,000 L sold vs 319.5 L produced |
| M1 | **MEDIUM** | 60-day voluntary waiting period is not enforced — AI accepted at calving + 1 day |
| M2 | **MEDIUM** | Cull rule is advisory-only and uses the goat threshold for buffalo (2, not 3); flagged animals keep being served |
| M3 | **MEDIUM** | No buffalo litter cap — a calving with 10 live calves is accepted, each becomes saleable inventory |
| M4 | **MEDIUM** | Per-animal daily yield is unbounded (3 × 100 L shifts) and any `milk.manage` holder can re-key any milking to the cap with a token reason |
| L1 | **LOW** | 90-day calf weaning bypassable same-day via the manual `FEMALE_KIDS → FOUNDATION` edge |
| L2 | **LOW** | Sexed-semen policy exists only in the simulator — `AI_SEXED` allowed on any service number |
| L3 | **LOW** | Milk inputs use plain (coercing) `float`, defeating the strict-wire-number design claim |

---

## B1 (BLOCKER) — Dairy dam stranded in the fresh pen while her calf lives

- **Files:** `backend/app/services/tasks.py:396-400` (bug), `backend/app/services/tasks.py:269-286` (`_guard_generated_movement_task`, species-aware and *correct*), `backend/app/services/kidding.py:299-318` (duty creation), `backend/app/api/tasks.py:657-673` (skip refusal)
- **Exploit / reproduction (live):**
  1. Buffalo B2: AI at `today-380`, PD positive at `today-315` (≥ service+60), calving at `today-105` (275-day gestation, in band) with **one live heifer calf** → dam → `RECOVERY` (fresh pen), calf → `FEMALE_KIDS` (calf shed).
  2. The auto-generated `BUCKET_MOVE` duty *"Move B2 to RESTING after the fresh period"* (due calving+10d) is overdue.
  3. `POST /api/tasks/{id}/complete` → **409** `"Postpartum recovery duty is invalid while a kid survives"`.
  4. `POST /api/tasks/{id}/skip` → **409** `"This duty is the only way out of postpartum recovery; complete it once the animals can be moved"`.
  5. The dam's only exit is the day-90 `WEANING` duty (confirmed live: completing it moved her `RECOVERY → RESTING` on day 105).
- **Root cause:** `complete_task`'s RECOVERY arm re-checks survivorship with `_litter_has_surviving_kid` **without** the species gate that `_guard_generated_movement_task` applies two lines earlier (`movement_profile.young_stay_with_dam`). Dairy calves are separated at birth by design — the record_kidding comment explicitly says *"the dam rejoins the milking string after the fresh pen regardless of calf survival"* — but this check treats the dairy dam like a goat dam nursing kids. The `# pragma: no coverage` marker on the branch shows the authors believed it unreachable.
- **Impact:** every dairy calving with a surviving calf (the normal case) leaves (a) the dam in the fresh-pen/`RECOVERY` cohort for 90 instead of 10 days — wrong ration, wrong milking-string cohort, wrong dashboard state; (b) one **permanently PENDING, overdue, unactionable duty per calving** (duty noise, "overdue" board lies); (c) a perverse incentive — the flow works only when the calf is dead or sold. This is herd-state corruption on the core dairy lifecycle, triggerable by any honest worker simply recording a normal calving.
- **Fix:** gate the survivorship re-check on `species_profile(farm.farm_type).young_stay_with_dam` in `complete_task` (mirror `_guard_generated_movement_task`), and add a dairy regression test that completes the +10d duty with a live calf.

## H1 (HIGH) — No lactation context on milk recording

- **Files:** `backend/app/api/milk.py:174-205` (the only animal gates are `status == ACTIVE` and `sex == 'F'`); `backend/app/services/milk.py:44-109` (no context checks)
- **Exploit (live, all 201):**
  - a **dry** buffalo sitting in `DELIVERY` (dry-off pen, confirmed pregnant, moved there via legal manual moves) — 11 L accepted;
  - a **105-day-old heifer calf** (`FEMALE_KIDS`, never bred, never calved) — 4 L accepted;
  - same for pregnant `PREGNANCY_EARLY` heifers (same gate set — no bucket/lactation predicate exists).
- **Impact:** a corrupt parlour recorder (`milk.manage`, e.g. the seeded MILKER role) can inflate herd totals without touching a single lactating animal — or hide theft by moving real yield onto dry/calf "slots" that no auditor would question. Corrupts the herd summary, the 30-day trend chart, and the per-animal "cull-review league" (the UI literally says *"Below 6 L/day at peak lactation is a cull candidate"*), driving wrong cull decisions. The frontend `AnimalPicker` on the milk page offers every animal, so the UI does not filter either.
- **Fix:** require a lactation context for dairy: animal must have a prior calving record (or be an imported in-milk foundation dam flagged at intake) and must not currently be in `DELIVERY`/pregnancy buckets; at minimum reject `current_bucket in (FEMALE_KIDS, MALE_KIDS, DELIVERY, PREGNANCY_*)` for yield recording.

## H2 (HIGH) — Milk dates have no chronology bound

- **Files:** `backend/app/api/milk.py:170-171` (only future dates are rejected); no `require_animal_event_chronology` call in the milk path (contrast `api/animals.py:911` for weights)
- **Exploit (live):** `POST /api/milk/new {date: "2019-06-01"}` against a buffalo with `date_of_birth = 2020-01-01` and `purchase_date = 2024-01-01` → **201**. The reading predates the animal's birth by 19 months and the farm's acquisition by 4.5 years.
- **Impact:** unlimited backdating = fabricated parlour history. Yields can be shifted across month boundaries ( inflate a month before an owner/investor review, deflate the current month to hide theft), and totals for any window are attacker-controlled. Weight, health, status and breeding writers all enforce animal chronology; the milk path is the one factual writer that forgot it.
- **Fix:** `require_animal_event_chronology(animal, payload.date, "Milk record")` inside the locked section of `add_milk_record`, plus a sane floor (e.g. ≥ animal's farm entry, and optionally a "no records older than X days without owner override" rule).

## H3 (HIGH) — MILK income untethered from production

- **Files:** `backend/app/api/finance.py:591-643` (`add_transaction` — provenance fully optional), `backend/app/schemas/finance.py` (`_validate_milk_provenance` only runs *if* fields are present; `milk_litres ≤ 1e6`, `amount ≤ ₹1e9`)
- **Exploit (live, both 201):**
  1. `{type: INCOME, category: MILK, amount: 1000000000}` — no litres, no rate — booked.
  2. `{amount: 50000000, milk_litres: 1000000, milk_unit_price_per_litre: 50}` — a farm that had produced **319.5 L** in its entire ledger "sold" 1,000,000 L for ₹5 crore in one row, with provenance that is internally coherent.
- **Impact:** any `finance.manage` holder (seeded ACCOUNTANT role; every manager+owner) can fabricate dairy revenue that no parlour record supports — and the ₹/kg-fat (or ₹/L) rate is set per transaction with no farm-level config or band, so procurement-price history is equally fictional. For a lender/investor-facing P&L (monthly_pnl, dashboard, VIEWER role) this is direct financial misstatement. There is **no reconciliation anywhere**: nothing compares `Σ milk_litres sold` to `Σ litres produced` (`MilkRecord` is consumed only by the milk module — the simulation/calibration never reads it, so poisoned parlour data cannot poison projections, but neither can honest data constrain the ledger).
- **Fix:** (a) require provenance on MILK income (litres + one price basis) or an explicit owner-acknowledged "unpriced sale" flag; (b) add a reconciliation guard/warning when cumulative sold litres exceed produced litres for a period; (c) consider a farm-level procurement-rate config with per-entry tolerance.

## M1 (MEDIUM) — Voluntary waiting period not enforced

- **Files:** `backend/app/services/breeding.py:301-344` (`_latest_doe_reproductive_boundary` — the only post-event gap is `breeding_date > boundary`), `backend/app/services/tasks.py` (auto duties can't fire early, so the practical floor is the +10d fresh-pen exit), `backend/app/simulation/milk_planner.py:14` (the 60-day VWP exists **only** in the simulator)
- **Exploit (live):** after the B1 calving, once the dam reached `RESTING`, `POST /api/breeding {breeding_date: <calving+1 day>, method: "AI_SEXED"}` → **201**. A 1-day postpartum service is biologically absurd for buffalo (VWP ~60d per the farm's own protocol notes in `models/species.py` and `seed.py`).
- **Impact:** corrupts calving-interval economics the simulation plans around (a 1-day VWP halves the calving interval the model assumes), inflates breeding/PD task counts, and lets a rival worker degrade reproductive stats. Note the species profile has no `voluntary_waiting_days` field at all — the concept never left the simulation layer.
- **Fix:** add `voluntary_waiting_days` to `SpeciesProfile` (60 for buffalo, ~10-20 for goats as policy dictates) and reject services dated before `latest_calving + VWP` in `create_breeding_record`.

## M2 (MEDIUM) — Cull rule advisory-only and goat-thresholded

- **Files:** `backend/app/models/constants.py:38` (`MAX_FAILED_CYCLES_BEFORE_CULL = 2`, applied to every species), `backend/app/services/breeding.py:689-704` (`_update_cull_candidate`), `backend/app/services/breeding.py:216-242` + `models/animals.py:358-375` (`is_breeding_candidate` — no `cull_candidate` predicate)
- **Exploit (live):** four consecutive AI services with negative PDs at +19d each — all four `POST /api/breeding` returned **201**; the animal's profile shows `cull_candidate = true` from failure #2 yet services #3 and #4 were accepted without friction.
- **Impact:** the stated buffalo protocol (cull after 3 services) is unenforced operationally (and the flag fires at 2, the goat number, for buffalo too). A worker can burn semen and PD budget on an animal management has decided to cull; the flag gates nothing (breeding, feeding, tasks all ignore it — only reports surface it). Defense that *did* hold: breeding records are immutable (no edit/delete endpoints), so the failure streak cannot be reset by tampering, and `derived_heat_cycle_number` is server-derived.
- **Fix:** make the threshold species-aware (`species_profile.max_services_before_cull`), and require an explicit owner override (reason + audit) to serve a `cull_candidate` female.

## M3 (MEDIUM) — No buffalo litter cap; calf fabrication + day-0 sale

- **Files:** `backend/app/schemas/kidding.py:48` (`kids: max_length=10` for every species), `backend/app/services/kidding.py:131-141` (BirthType MULTIPLET for ≥5), `backend/app/api/animals.py:1162-1180` (sale transaction from a free-text `sale_price`)
- **Exploit (live):** calving recorded with **10 live calves** (5M/5F) → 201; ten `Animal` rows created in `MALE_KIDS`/`FEMALE_KIDS`. One male calf sold the same day: `POST /api/animals/{id}/status {new_status: SOLD, sale_price: 60000}` → 200, booking ₹60k income — `BORN` inventory is the only animal source that costs nothing to fabricate (no purchase batch, no money trail).
- **Impact:** a `kidding.manage` + `animals.status` holder (VET preset has kidding.manage; MANAGER has both) can mint phantom calves and sell them — fabricated headcount, fabricated ANIMAL_SALE income, distorted twinning/calving stats and the litter-size calibration (`simulation_calibration.py:534-546` averages litter size from `kid_entries`, clamped to 4.0, but conception/gestation calibration also read these rows). Twins are plausible for buffalo; ten is not.
- **Fix:** species litter cap (`kid_count ≤ 2` for buffalo, ≤4 for goats) at the schema/service layer; flag litters above the detected `kid_count_detected` for verification; consider owner co-sign for day-0 calf sales above a price band.

## M4 (MEDIUM) — Unbounded daily yield + correction re-keying

- **Files:** `backend/app/schemas/milk.py:18` (per-shift cap only), `backend/app/services/milk.py:79-96` (correction path)
- **Exploit (live):** three shifts × 100 L accepted for one animal on one day (208.5 L total day observed); and an existing 7 L reading was corrected to **100 L** with `correction_reason: "typo"` — no reviewer, no bound on how far a correction may move the number, unlimited times per row.
- **Impact:** 300 L/buffalo/day of headroom is far outside Murrah biology (peak ~18-20 L/day); combined with H1 a single recorder can inflate the herd ledger ~15× with schema-valid data. The frozen `original_*` columns preserve the first reading for audit, but every aggregate (summary, trend, league) uses the latest value.
- **Fix:** per-animal daily total sanity band (e.g. ≤ 40 L/day for buffalo, species-aware), and/or a second-role approval for corrections that move a reading by more than a tolerance; cap correction chains and surface them on the parlour board.

## L1 (LOW) — 90-day weaning bypassable same-day

- **Files:** `backend/app/services/animals.py:52` (`(FEMALE_KIDS, FOUNDATION): {"manual"}`), `api/animals.py:753-885` (no age/dwell check on manual moves)
- **Exploit (live):** manual `FEMALE_KIDS → FOUNDATION` on a 105-day calf (and any day-old calf) → 200. The day-90 `WEANING` duty is the *intended* path (and is due-date gated), but the manual edge has no dwell time, so any `animals.move` holder (MOVER, CALF_ATTENDANT presets) can pull a calf out of the milk program at any age — distorting calf-ration costs and the weaning calendar.
- **Fix:** require a minimum dwell (species `weaning_days`) for `FEMALE_KIDS/MALE_KIDS → FOUNDATION/BREEDING` manual moves, or route them through the weaning duty.

## L2 (LOW) — Sexed-semen policy not operational

- **Files:** `backend/app/schemas/breeding.py:17-35` (`method` accepts `AI_SEXED` unconditionally), `backend/app/simulation/defaults.py:257-264` (the "sexed for the first two services" policy exists only in simulation assumptions)
- **Exploit (live):** `AI_SEXED` accepted on a repeat-breeder service (and on the postpartum-day-1 service of M1).
- **Impact:** farm semen policy (expensive sexed doses reserved for first services) is unenforced; a worker can burn sexed straws on the 5th service of a cull candidate. Data/model mismatch: the simulator assumes the policy holds.
- **Fix:** enforce the service-number rule (or a farm-configured policy) in `create_breeding_record` with an owner override.

## L3 (LOW) — Coercing floats in milk inputs

- **Files:** `backend/app/schemas/milk.py:18,21` (`litres: float`, `fat_pct: float` — plain, not `FiniteFloat`), contrast `schemas/common.py`'s strict-number rationale
- **Evidence (validated against `MilkRecordIn`):** `"litres": "8"` and `"fat_pct": "6.5"` (JSON strings) are **accepted** and coerced; `1e2` (=100) is accepted for litres (in bounds), `1e400` and `NaN` are rejected by the bounds. No security impact (all bounds still apply, DB CHECKs backstop), but the module violates the project's own strict-wire-number rule ("`\"12.5\"` must not look like a successfully recorded measurement").
- **Fix:** type the fields as `FiniteFloat`/`StrictFloat` variants like the finance module does.

---

## Defenses that held (verified live or by constraint)

1. **Duplicate/race milk entries:** `uq_milk_records_animal_day_shift` + the `FOR UPDATE` animal-row lock serialize concurrent same-milking submissions — live race produced `[201, 422]` and exactly **one** row; re-submit without a `correction_reason` is rejected.
2. **Milk bounds:** `0 < litres ≤ 100`, `fat_pct ∈ [3,12]` (plus NaN/±Infinity rejection) enforced at schema **and** DB CHECK; negative, 100.5 L, 12.01%, 2.99% all 422; `1e400` rejected.
3. **Future dates:** rejected against the *farm's* timezone (`api/milk.py:170`), stricter than the generic `PastOrTodayDate` headroom.
4. **Fat separation of duties:** setting/changing `fat_pct` requires `milk.quality`; a plain recorder's correction carries the tested fat forward *under the animal lock* so it cannot erase a quality role's test (`api/milk.py:163-205`). Seeded roles match (MILKER has no quality; MILK_QC/manager do; FEEDER is view-only).
5. **No edit/delete of milk rows:** no DELETE/PUT endpoints exist; the in-place correction freezes the first reading in `original_*` audit columns.
6. **Idempotency:** keys are scoped `(farm, actor, operation, key)`; replay returns the committed response with `Idempotency-Replayed: true`; same key + different body → 409; cross-farm key reuse is harmless by scoping.
7. **Milk provenance coherence (finance):** when a price basis is present, `amount` must equal `litres×rate` (or the winning `litres×fat%×₹/kg-fat` basis) within ₹0.01 — no double multiplication, no half-pairs (`schemas/finance.py`).
8. **Farm-type gates:** every milk endpoint (and MILK income) refuses goat farms (422, verified).
9. **Calving integrity:** requires a `CONFIRMED_PREGNANT` breeding (PD+ cannot predate service+60 for buffalo), one kidding per breeding (`uq_kidding_breeding_record` + locked pre-check), gestation band 270-350d enforced, kidding date ≥ PD date, dead/SOLD dams cannot deliver. Calving without PD → 400 (verified).
10. **Overlapping breedings blocked:** `has_open_breeding` (PENDING or confirmed-undelivered) + the partial unique index on PENDING + doe-row lock ordering; service to a pregnant/open doe → 400 (verified). Breeding records are immutable — the failure counter cannot be reset by edits/deletes.
11. **Backdated services bounded** by the latest reproductive boundary (kidding/PD/loss); PD result windows enforced (positive not before planned +60d, negative not in the unobservable 2-17d gap, nothing past max gestation).
12. **Auto-generated duties cannot be completed before their due date** (`api/tasks.py:591-594`) — the dry-off/DELIVERY move at EKD-21 cannot be jumped.
13. **Non-ACTIVE animals:** no milk (409 on SOLD, verified), no moves, no kidding; status changes enforce chronology against recorded facts.
14. **Frontend:** milk page mirrors server bounds (0-100 L, 3-12%), submits only farm-today, sends `fat_pct: null` for non-quality roles, and aggregates charts from server-grouped daily rows (`milk-series.ts` zero-fills by date — no double counting); queries are farm-scoped via `X-Farm-Id`.
15. **Simulation coupling:** `MilkRecord` is consumed by no other module — poisoned parlour data cannot corrupt the lactation snapshot or calibration (conversely, see H3: the ledger is equally unconstrained by the parlour).

## Not-vulnerable / by-design notes

- 500 kg/shift and 15%+ fat are impossible (bounds at three layers).
- "Calf dies AND is sold at birth": impossible — DIED calves are created `DEAD` and `change_status` only acts on `ACTIVE` animals.
- "Dead-calf + weaning task": weaning duty is only created when ≥1 calf is `ALIVE` at calving; the no-survivor path schedules the postpartum RESTING duty instead (existing coverage in `test_breeding_extended.py`).
- Rounding drift: litres are stored at 0.001 precision and summed in SQL float64 — no exploitable ledger drift.
- Two calvings for one breeding: unique constraint + lock-ordered pre-checks (covered by `test_concurrency.py` patterns).

## Reproduction

Script: `/tmp/a6_dairy_audit.py` (self-contained: creates/drops `goatfarm_audit_a6_test`, runs alembic, seeds, drives the app in-process). Final live output — 15 vuln confirmations, 17 held, including:

```
[m2-predob]          milk dated 18 months before DOB / 4.5y before purchase -> 201
[m9-correct-to-100]  corrected to 100 L; original_litres=7.0 litres=100.0
[f1-no-provenance]   Rs.1e9 MILK income, zero provenance -> 201
[f2-sold>produced]   1,000,000 L sold -> 201   (produced=319.5 L)
[fp-stranded]        dairy dam w/ live calf: complete=409 skip=409 bucket=RECOVERY
[fp-day90-exit]      only exit at day 90 weaning duty -> 200, bucket=RESTING
[fp-zombie-duty]     +10d duty still PENDING forever: True
[vwp-1day]           AI_SEXED at calving+1 -> 201
[dry-milked]         milk on dry DELIVERY buffalo -> 201
[calf-milked]        milk on 105-day heifer calf (never calved) -> 201
[early-wean]         manual FEMALE_KIDS->FOUNDATION day 105/90 -> 200
[litter-10]          buffalo calving, 10 live calves -> 201
[male-calf-sale]     day-0 male calf sold Rs.60k -> 200
[cull-not-enforced]  4 consecutive services [201,201,201,201], cull_candidate=True
```
