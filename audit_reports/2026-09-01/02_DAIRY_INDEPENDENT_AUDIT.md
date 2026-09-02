# 02 — DAIRY (Murrah Buffalo) Independent Full-Stack Audit

- **Auditor:** A2 (independent, from-scratch; no prior audit knowledge assumed)
- **Date:** 2026-09-01
- **Repo:** `/Users/pavankumarreddyreddem/Desktop/goat_saas`
- **Scope:** BUFFALO_DAIRY domain, full stack — `app/models/{species,milk,breeding,helpers,constants,enums}.py`, `app/services/{milk,breeding,kidding,feeding,health,tasks,simulation_calibration}.py`, `app/seed.py`, `app/api/{milk,breeding}.py`, `app/simulation/{lactation,milk_planner,market,montecarlo,defaults,assumptions,engine,shocks}.py` (dairy paths), frontend `src/app/(app)/milk/page.tsx`, `src/lib/{milk-series,farm-vocabulary}.ts`, `src/test/dairy.test.tsx`, dashboard dairy vocabulary.
- **Method:** read every dairy constant/function; traced the lifecycle end-to-end; ran the backend dairy test set and frontend dairy tests; reproduced suspected defects in-process against a throwaway Postgres DB (ASGI/httpx, no server); verified constants against published sources (ICAR/CIRB/NDRI, TNAU, NDDB Dairy Knowledge Portal, NABARD model projects, 2022-2024 sexed-semen field trials, 2025-26 Telangana procurement rates).

## Executive summary

The dairy domain is unusually well-engineered: the species profile correctly parameterizes gestation 310 d, PD+60 d, 90-d milk weaning, 10-d fresh pen, 24-h calf separation, and dry-off at EDD−60; the milk ledger (per-shift upsert, frozen originals, litre-weighted fat, ₹/kg-fat income with paise-exact provenance) is correct and well tested; the Wood lactation curve and the murrah_dairy simulation preset match published biology and 2025-26 Telangana economics closely. All 74 backend dairy-scoped tests and the 6 frontend dairy tests pass (on an isolated DB).

However, four real defects exist:

1. **Two hard 500s on legitimate dairy data entry** — DB CHECK constraints written for goat biology (`breeding_date + 200`) reject buffalo PD results and abortions recorded after gestation day 200, even though the service layer explicitly accepts them to day 350 (`BUFFALO_DAIRY_PROFILE.max_gestation_days`). Empirically reproduced.
2. **The README/seed/UI-promised "3-service cull rule" is not implemented in the operational app** — the global `MAX_FAILED_CYCLES_BEFORE_CULL = 2` flags a dairy buffalo as a cull candidate after only 2 failed services.
3. **The 60-day voluntary waiting period is not enforced (or even checked) anywhere in the operational breeding path.**
4. **The auto-generated dairy weaning duty ("Wean calves … → FOUNDATION") is a silent no-op** — dairy calves are never in RECOVERY, so completing the duty moves nothing, and there is no legal automated FEMALE_KIDS→FOUNDATION transition.

Plus several medium/low modeling and consistency gaps (calf-milk not deducted, KidEntry mortality never realigned for dairy calves, dry-period inconsistency between ops protocol and simulation).

---

## FINDINGS

### HIGH-1 — DB CHECK `+200 days` aborts legitimate buffalo late-gestation records with HTTP 500

- **File:** `backend/app/models/breeding.py:59-63` (`ck_breeding_loss_within_max_gestation`) vs `backend/app/services/breeding.py:736-744` (`mark_aborted`)
- **Issue:** The CHECK constraint requires `loss_date <= breeding_date + 200` for every user-entered cause (`ANIMAL_STATUS_CHANGE` is the only exemption). For a dairy farm the service validates losses against `profile.max_gestation_days = 350` (buffalo gestation 310 d), so any user-recorded abortion between gestation day 201 and 350 passes the API validation and then explodes at flush time with an unhandled `IntegrityError` → HTTP 500, rolling back nothing user-visible.
- **Evidence (reproduced in-process on a throwaway DB, real app code):**
  - Registered dairy farm → buffalo AI bred 250 days ago → PD positive at day 65 (`200 OK`) → `POST /api/breeding/{id}/abort {"loss_date": <today>, "cause": "DISEASE"}` →
    `asyncpg.exceptions.CheckViolationError: new row for relation "breeding_records" violates check constraint "ck_breeding_loss_within_max_gestation"` → **HTTP 500**.
  - `api/breeding.py:391-423` catches only `ValueError`; `IntegrityError` is unhandled.
- **Real-world:** buffalo late-gestation abortions (brucellosis, campylobacter, trichomoniasis, nutritional) routinely occur in the last trimester (day 200-310). An app that 500s on a day-250 abortion cannot record the most consequential dairy pregnancy losses.
- **Fix:** make the constraint species-aware — either (a) drop the magic 200 from the CHECK and enforce the species window purely in the service (the service already validates against the profile), or (b) widen the CHECK to the global maximum (`breeding_date + 350`) and keep the tight species-specific windows in `mark_aborted`, or (c) farm-type-conditional constraint not being possible in a shared table — so (a)/(b). Also map `IntegrityError` on this endpoint to a 4xx, not 500.

### HIGH-2 — Same `+200` CHECK rejects buffalo PD results recorded after day 201 → HTTP 500

- **File:** `backend/app/models/breeding.py:109-112` (`ck_breeding_records_result_within_max_gestation`) vs `backend/app/services/breeding.py:492-510` (`record_ultrasound_result`)
- **Issue:** `record_ultrasound_result` deliberately accepts a late-entered result up to `breeding_date + max_gestation` (350 d for buffalo) — the code comment explicitly invites "a delayed record using its true historical observation date inside this window". The DB constraint caps `ultrasound_result_date` at `breeding_date + 200`. A buffalo PD result (either direction) dated day 201-350 after service passes the service check and then hits `CheckViolationError` → 500.
- **Evidence (reproduced):** `POST /api/breeding/{id}/ultrasound {"pregnant": true, "date": <bred+210d>}` →
  `CheckViolationError … 'ck_breeding_records_result_within_max_gestation'` → **HTTP 500**.
- **Fix:** same as HIGH-1 (align the CHECK with the species profile maximum; goat profile max is exactly 200 so goat behavior is unchanged by a 350-day shared cap only if the service keeps enforcing per-species windows — it does).

### HIGH-3 — The promised 3-service cull rule is not implemented for dairy (cull flag fires after 2 failed services)

- **File:** `backend/app/models/constants.py:38` (`MAX_FAILED_CYCLES_BEFORE_CULL = 2`), `backend/app/services/breeding.py:689-704` (`_update_cull_candidate` — species-blind), `backend/app/services/breeding.py:641` (called on every FAILED result)
- **Issue:** The goat SPEC rule (2 consecutive FAILED cycles → cull candidate) is applied to every farm type. The dairy contract says 3:
  - `README.md:14` — "60-day voluntary waiting period, **3-service cull rule**"
  - `backend/app/seed.py:151` — dairy BREEDING bucket: "first AI at 60 days post-calving, **max 3 services**"
  - `frontend/src/lib/farm-vocabulary.ts:118` — "max 3 services before cull review"
  - `backend/app/simulation/defaults.py:265` — the simulation honors `max_services_before_cull=3`.
- **Consequence:** a dairy buffalo failing her 2nd AI is flagged `cull_candidate=True` one service early. With ~45% per-service conception, P(2 consecutive fails) ≈ 30% of breeding attempts — the flag fires on roughly a third of healthy buffalo, poisoning the cull-review league (milk page + dashboard cull preview) with false candidates. Repeat-breeder culling at 2 services would also remove ~30%/yr of the milking herd instead of the modeled ~17%/yr (0.55³), wrecking herd-life economics (the sim's 4.8-lactuation herd-life assumption).
- **Real-world:** 3 services before repeat-breeder disposal is the standard Murrah farm discipline (and the farm's own site plan).
- **Fix:** add `failed_services_before_cull: int` to `SpeciesProfile` (2 for GOAT, 3 for BUFFALO_DAIRY) and use it in `_update_cull_candidate` instead of the module constant.

### HIGH-4 — 60-day voluntary waiting period is not enforced anywhere in the operational breeding path

- **File:** `backend/app/services/breeding.py:379-458` (`create_breeding_record`) and `backend/app/api/breeding.py:213-334` (`create_breeding`)
- **Issue:** The only post-calving protection is `breeding_date > latest reproductive boundary` (`services/breeding.py:408-413`), i.e. AI is recordable **the day after calving**. Nothing checks "≥60 days since last calving" for dairy. The bucket graph gives soft protection (the dam sits in RECOVERY for ~10 days), but RECOVERY→RESTING is operator-driven and RESTING is in `BREEDING_READY_BUCKETS` (`models/constants.py:72`), so a buffalo can legally be bred on day 11 post-calving. The 60-day VWP is promised in `README.md:13-14`, `seed.py:151` ("first AI at 60 days post-calving"), `seed.py:186`, `farm-vocabulary.ts:118`, and is implemented in the simulation (`defaults.py:255`, `months_open_before_breeding=2`) — but not in the operational workflow that records actual AI services.
- **Real-world:** breeding buffalo before uterine involution (~40-60 d) sharply lowers conception and raises early embryonic loss; 45-60 d VWP is standard Murrah practice.
- **Fix:** in `create_breeding_record`, for `farm_type != GOAT`, reject `breeding_date < latest_calving_date + 60` (species-profile field, e.g. `voluntary_waiting_days`) with a 4xx explaining the VWP.

### MEDIUM-1 — Dairy weaning duty ("Wean calves … → FOUNDATION") completes as a silent no-op

- **File:** `backend/app/services/kidding.py:319-329` (duty creation), `backend/app/services/tasks.py:429-436` and `tasks.py:546-572` (completion side effects), `backend/app/services/animals.py:50` (transition table)
- **Issue:** The duty title promises "Wean calves of {tag} off milk; → FOUNDATION". But `complete_task` builds `weaning_kids` filtered to `current_bucket == RECOVERY` (`tasks.py:435`), and dairy calves are born directly into `MALE_KIDS`/`FEMALE_KIDS` (`kidding.py:209-213`, `young_stay_with_dam=False`), so `weaning_kids` is always empty on a dairy. The dam is already in RESTING (moved at day 10), so the dam move also no-ops (`tasks.py:560-563` requires DELIVERY/RECOVERY). Net effect of completing the day-90 duty: the task goes green; **no animal moves**. The (FEMALE_KIDS → FOUNDATION) transition only allows context `manual` (`animals.py:50`), so the promised move cannot even be triggered by the duty's own workflow; calves remain in FEMALE_KIDS until someone manually re-buckets them.
- **Impact:** the dairy bucket board's "Weaned (~3 mo) → FOUNDATION / Growing Heifers (Building C)" lifecycle never happens through the workflow; FOUNDATION stays empty; code-vs-task-title inconsistency misleads operators. (Breeding is not blocked — FEMALE_KIDS is in `BREEDING_READY_BUCKETS` — which softens the damage.)
- **Fix:** for `young_stay_with_dam=False`, completing the WEANING duty should move each surviving female calf FEMALE_KIDS→FOUNDATION (add `weaning` to that transition's contexts, guarded to dairy), and the title's "→ FOUNDATION" then becomes true. Also fix the goat-specific `"Weaned (day 60)"` reason string (`tasks.py:555`) to use `profile.weaning_days`.

### MEDIUM-2 — KidEntry never realigned when a dairy calf dies pre-weaning; weaning duty not cancelled; kids-per-calving stats overstate

- **File:** `backend/app/services/kidding.py:362-414` (`replan_dam_after_last_kid_death` — early return `child.current_bucket != RECOVERY`), `backend/app/api/dashboard.py:505-522` (reports use `KidEntry.status == ALIVE`)
- **Issue:** The KidEntry→DIED realignment, the weaning-duty cancellation, and the postpartum replan all live behind "the child is in RECOVERY" — true for goat kids, never true for dairy calves (they are in FEMALE_KIDS/MALE_KIDS from birth). Consequences on a dairy farm:
  1. A calf that dies at day 30 keeps `KidEntry.status = ALIVE` forever (its Animal row is DEAD, but the birth entry is never realigned) → `kids_per_kidding` / `total_alive` / twin-rate reporting counts dead calves as alive.
  2. If all calves of a calving die before day 90, the dam's "Wean calves" duty is never skipped/cancelled — it lingers as overdue noise (and per MEDIUM-1 completing it does nothing anyway).
- **Fix:** extend the "dependent young" test in `replan_dam_after_last_kid_death` to the dairy calf buckets (never left FEMALE_KIDS/MALE_KIDS since birth), or add a dairy branch in the status-change path that realigns the KidEntry and skips a childless litter's weaning duty.

### MEDIUM-3 — Milk fed to calves is not deducted from saleable milk anywhere (ops or simulation)

- **File:** `backend/app/simulation/engine.py:1444-1452` (100% of the lactation curve is revenue milk), `backend/app/simulation/milk_planner.py` (same), `backend/app/seed.py:200` (dairy board: heifer calves "whole-milk fed, wean off milk by day ~90")
- **Issue:** Retained heifer calves are whole-milk fed to ~90 days (the farm's own protocol), typically 2-3 L/day falling to ~1 L — roughly 200-270 L per replacement heifer. At the modelled replacement rate (~25 heifer calves retained/yr for a 60-head unit) that is ~5,000-6,700 L/yr, i.e. **~5-7% of milk revenue overstated** in the simulation (and no calf-milk line exists in the operational parlour ledger either — every recorded litre is implicitly saleable).
- **Real-world:** NABARD/NDRI calf-rearing budgets explicitly carry whole-milk/calf-milk consumption as a cost or a milk deduction.
- **Fix:** add a `calf_milk_litres_per_day`-style assumption applied over `weaning_days` for retained female calves, netting it out of saleable litres (and documenting it in the milk planner).

### MEDIUM-4 — Dry-period modeling is internally inconsistent (ops: 60 d protocol vs 21 d dry TMR vs sim: ~128 d)

- **Files:**
  - `backend/app/services/breeding.py:581-603` — dry-off therapy task at **EDD−60** (correct per README) but the DELIVERY bucket move (the only way onto the dry TMR `D_DRY_CLOSEUP`, `services/feeding.py:235-236`) is at **EDD−21**. Between EDD−60 and EDD−21 a dry buffalo sits in PREGNANCY_LATE on `D_LACTATION_MED` (a milking ration).
  - `backend/app/simulation/defaults.py:253-255` + engine: sim gestation 10 months, lactation 10 months, VWP 2 months, ~2.2 months-to-conceive → calving interval ≈ 14.2 months → implicit dry period ≈ 4.2 months (~128 d), not 60 d.
- **Real-world:** published Murrah dry periods average ~101 d (NDRI herds) to 150 d (NABARD model), so the *simulation* is defensible; the *operational* flow is the odd one — the animal receives dry-cow therapy at −60 d but only reaches the dry TMR/close-up pen 3 weeks before calving, and the README's "dry-off 60 days before calving" is only half-realized (a task exists; the ration/bucket does not follow).
- **Fix:** either move the DELIVERY duty to EDD−60 for dairy (and relabel the calving-pen move), or add a second pre-calving duty at −60 for the dry-group move, and align the sim's lactation length or dry-off rule with the same 60-day figure if that is the farm's stated protocol.

### LOW-1 — Goat-stale docstrings/comments on the shared calving path

- `backend/app/services/kidding.py:62-63` — "Doe → RECOVERY; WEANING task at **+60d**" (dairy is +90 via the profile); `kidding.py:63` "bucket=RECOVERY" (dairy calves go to calf buckets). Comment-only drift; behavior is profile-driven and correct.
- `backend/app/services/tasks.py:552` — "WEANING → kids to MALE_KIDS/FEMALE_KIDS … dam to RESTING" and `tasks.py:555` `"Weaned (day 60)"` — goat wording on the shared path.

### LOW-2 — Finance milk-fat bound (0-12) looser than the milk-record bound (3-12)

- **File:** `backend/app/schemas/finance.py:39` (`MilkFatPctFloat = Field(ge=0, le=12)`) vs `backend/app/models/milk.py:50-54` (`BETWEEN 3 AND 12`)
- A milk **sale** can be booked with 0.5% fat while a milk **reading** of the same fat is rejected. Murrah true range is 6.5-8.3% (NDDB). Harmless to arithmetic (provenance math is exact) but inconsistent validation; consider 3-12 or 5-12 on the finance field.

### LOW-3 — Preset's fallback ₹/L disagrees with its own ₹/kg-fat figure

- **File:** `backend/app/simulation/defaults.py:311` — `milk_price_per_litre=58.0  # fallback ≈ procurement ₹850/kg fat @ 6.8%` while `milk_price_per_kg_fat=900.0` (line 327). 900 × 6.8/100 = ₹61.2/L, not 58. The engine prefers the fat basis (`engine.py:1434-1437`) so this only bites a user who zeroes the kg-fat field — then revenue drops ~5% silently. Align the fallback to the fat basis or fix the comment.

### LOW-4 — Sim breeds heifers at 24 mo, operational profile at 22 mo; both below published AFC norms

- **Files:** `backend/app/models/species.py:87` (22 mo / 340 kg) vs `backend/app/simulation/defaults.py:270` (`age_at_first_breeding_months=24`). 22 mo + 310 d ⇒ AFC ≈ 33 mo, below the well-managed published range 36-40 mo (TNAU cites 40-50 mo; ICAR-CIRB field AFC ~43 mo; the preset's own comment admits this). The 22-24/340-345 kg gate is defensible for intensively reared heifers and is documented as intentional, but the two modules disagree with each other (22 vs 24), and an AFC of 33 mo is optimistic for anything but the best heifer rearing. Unify on one profile-driven number and note the AFC implication in the UI copy.

### LOW-5 — Frontend litres error message includes a value it rejects

- **File:** `frontend/src/app/(app)/milk/page.tsx:150-152` — "Litres must be between 0 and 100" but `parsedLitres <= 0` is rejected; 0 is not a legal entry. Cosmetic.

### INFO-1 — No milk KPI on the dairy dashboard
`frontend/src/app/(app)/dashboard/page.tsx` renders only parturition vocabulary ("Calvings due"); today's litres / fat % / income live only on `/milk`. A dairy operator's landing page has no parlour KPI. Product gap, not a bug.

### INFO-2 — Male-calf sale at birth has no operational flow
README promises "male-calf sales at birth". Operationally it is a manual status change + ANIMAL_SALE finance row (seed board says "sell within a week", `seed.py:193`); only the simulation automates it (`defaults.py:362`, `male_calf_sell_at_birth_fraction=0.9` at ₹1,600 — within the ₹1,200-1,800 week-old bull-calf range). Acceptable, but the README oversells.

### INFO-3 — Fresh-pen buffalo auto-allocated the high-yielder TMR from day 1
`backend/app/services/feeding.py:237-238` — RECOVERY ⇒ `D_LACTATION_HIGH` ("10+ L/day; 3x milking"). Fresh animals are usually transitioned (moderate energy, ramped concentrate) for ~10-14 d to avoid rumen acidosis/mastitis. The fresh-pen assignment should arguably be a transition ration.

### INFO-4 — Gestation modeled at whole-month resolution
`defaults.py:253` — `gestation_months=10` = 304.4 d vs actual 310; implied calving interval ~432 d still inside the published 400-500 d Murrah range. Monthly-granularity artifact, documented here for completeness.

### INFO-5 — Shared test-DB race (environmental, observed during audit)
Running the dairy suite while another session used the default `goatfarm_test` produced 20 spurious failures ("Account no longer exists" mid-test). On an isolated DB (`GOATFARM_TEST_DB=goatfarm_test_dairy2`) everything passes. Not a product defect; conftest already supports suffixed DBs — parallel auditors should use them.

---

## VERIFIED CORRECT (constants and logic checked against published sources)

### Species profile — `backend/app/models/species.py:76-94` (BUFFALO_DAIRY_PROFILE)

| Constant | App value | Published | Verdict |
|---|---|---|---|
| Gestation | 310 d; window (300, 325); sanity band 270-350 | 310 d avg, ~305-320 range | OK |
| PD after service | 60 d | standard (PD ~60 d post-AI) | OK |
| First breeding (heifer) | 22 mo / 340 kg | NABARD ~275-325 kg; NDRI 325-350 kg — aggressive but documented (see LOW-4) | OK* |
| Sire | 24 mo / 350 kg | young-sire norms | OK |
| Milk weaning | 90 d | calf rearing 2-3 mo, whole milk to ~90 d | OK |
| Fresh pen | 10 d | fresh-cow practice 7-14 d | OK |
| 24-h calf separation | `young_stay_with_dam=False` → calves born straight into sexed calf buckets (`services/kidding.py:209-213`) | standard dairy calf-shed practice | OK |

### Simulation preset — `backend/app/simulation/defaults.py:220-438` (murrah_dairy)

| Constant | App value | Published | Verdict |
|---|---|---|---|
| 305-d lactation yield | 2,100 L | breed avg 1,752 (NDDB range 1,003-2,057); first-lactation 1,751 (IJAR); review 2,148; elite 2,277+ | OK for purchased proven 2nd-lactation animals (justified in-code); slightly optimistic vs breed average |
| Lactation length | 10 mo = 304 d | ~297-308 d | OK |
| Fat % | 6.8 (bound 3-12 DB; benchmark 6.5-7.5 UI) | 6.9-8.3, avg 7.3 (NDDB) | OK (low end; conservative) |
| Conception / AI | 45% | field 40-50% | OK |
| Sexed female fraction | 0.90 | 90.2-91.2% (UP programme, KVK Guntur) | OK |
| Sexed conception ×0.85 ⇒ 38.3% | — | 38.63% sexed vs 45% conv. (Ingawale 2022); 40 vs 50 (Sawant 2022); 55.2 vs 63.6 (Sharma 2024) | OK |
| Services before cull (sim) | 3 | farm's site plan; standard | OK (ops app does NOT match — HIGH-3) |
| VWP (sim) | 2 mo = 61 d | 45-60 d | OK (ops app does not enforce — HIGH-4) |
| Stillbirth | 3% | 2-5% | OK |
| Calf (pre-wean) mortality | 10% | NABARD 5-10%; organized ~8% | OK |
| Adult mortality | 2.5%/yr | organized herds 2-5% | OK |
| Birth weight | 31 kg | CIRB recorded M 31.7 / F 30 kg | OK |
| Adult weight | doe 520 / bull 600 kg | published females ~450-650 kg | OK |
| Growth | 215 kg yearling; ~345 kg @24 mo; mature @40 mo | NDRI growth studies | OK |
| Calving interval (implied) | ~14.2 mo = 432 d | Murrah 400-500 d | OK |
| Wood curve | peak day 65, b=0.6, c=b/peak | river-buffalo Wood fits peak day 57-73, b 0.465-0.677 | OK |
| Persistency | 0.93/mo (geometric); Wood peak-to-average 1.475 | recorded Murrah persistency ~89-93% (Bulgarian Murrah 89.2%) | OK |
| Heat-stress yield seasonality | trough ×0.86 (Jun), winter ×1.08 | Telangana summer 10-20% decline | OK |
| Lean-season price premium | max ×1.07 (Jun) | flush/lean procurement swing | OK |
| ₹/kg fat | procurement 840-865 cited; default 900 blended | Sangam ₹840/kg fat; Vijaya ₹85/L @10% fat = ₹850/kg fat (Apr 2026) | OK |
| Male calf at birth | 90% sold @ ₹1,600 | week-old bull calf ₹1,200-1,800 | OK |
| Concentrate | ₹26/kg; ~6 kg DM/d for 10-12 L | DairyKnowledge benchmark ration | OK |
| Effective ₹/L precedence | `₹/kg fat × fat% / 100` wins over flat ₹/L (`engine.py:1434-1437`, `milk_planner.py:653-656`) | how Telangana coops pay | OK |

### Lactation curve implementation — `backend/app/simulation/lactation.py`
Verified numerically with the app's own code: `monthly_milk_curve(2100, 10, wood, peak=65)` = [191, 300, 310, 287, 252, 215, 180, 148, 120, 97] L; sum exactly 2100; avg 6.9 L/d; peak month 3 at 10.18 L/d; first month 6.28 L/d; peak-to-average 1.475. Matches the code's documented claims and published Murrah shape (peak month ~2-3, ~10 L/d peak for proven animals; review avg peak 8.87 kg/d first lactation). Normalization identity (curve sums to lactation total) holds.

### Operational dairy flow — verified end-to-end
- AI (conventional/sexed) records with semen-sire identity, DB CHECK enforces sire-method coherence (`models/breeding.py:73-83`); heat-cycle derived server-side; PD task at +60; positive PD ⇒ CONFIRMED + dry-off therapy at EDD−60 + DELIVERY move at EDD−21 + Calving-due at EDD (all species-labeled via profile).
- Calving: gestation band 270-350 enforced (`kidding.py:81-87`); calves auto-created in sexed calf buckets (24-h separation modeled); dam → RECOVERY (fresh pen); fresh-exit duty at +10 d; calf weaning duty at +90 d assigned to the Calf-shed Attendant (tested).
- Milk: (animal, date, shift) unique upsert; corrections require a reason and freeze first-submitted values (`models/milk.py:82-95`, `services/milk.py:79-96`); fat entry gated behind `milk.quality` with carry-forward under the animal lock (`api/milk.py:156-219`); litre-weighted herd fat; endpoints 422 on goat farms (tested).
- ₹/kg fat income: provenance all-or-nothing; fat basis wins; amount must reproduce `litres × fat% / 100 × ₹/kg fat` within a paisa (`schemas/finance.py:43-105`; tested).
- Dairy TMRs (`seed.py:289-368`): all five recipes sum to exactly 100 kg; 50:50 concentrate (as-fed) for 10+ L/d, 60:40 for 6-10 L/d, 75:25 dry, calf starter 100% concentrate from day 15 — consistent with ICAR/TNAU buffalo rationing; bucket→recipe mapping (`services/feeding.py:222-244`) matches the seeded board.
- Dairy vaccine calendar (`seed.py:413-453`): FMD 4 mo + 6-monthly cycle (NADCP-free), HS/BQ annual pre-monsoon, brucellosis 4-8 mo heifers once (never pregnant), LSD annual homologous, IBR marker for AI herds, dry buffalo therapy at dry-off — standard Telangana practice.
- Simulation dairy engine: VWP pools, sexed-service buckets (2 sexed then conventional, 3-service repeat-breeder cull to the finishing pen that milks until dry-off), male-calf at-birth sales, stage-indexed lactation overlay, species-aware gestation calibration (280-345 d window, `simulation_calibration.py:547-562`) — all traced and mass-balance tested in the suite.

### Frontend
- `farm-vocabulary.ts` mirrors the species profile exactly (asserted by `dairy.test.tsx:49-74`); milk page fat/litre bounds (3-12, ≤100) mirror the backend CHECKs; permission-gated fat field with stale-value drop; parlour hidden for meat farms; `milk-series.ts` zero-fill/UTC arithmetic correct (4/4 tests).

### Test results (this audit)
- Backend (isolated DB): `pytest -k "milk or dairy or calv or lactation"` → **74 passed**; `tests/test_dairy.py` → **28 passed**; `tests/test_lactation_curve.py` + `tests/test_simulation_milk_planner.py` → **33 passed**.
- Frontend: `vitest run src/test/dairy.test.tsx` → **6/6 passed**; `src/lib/milk-series.test.ts` → **4/4 passed**.
- Empirical defect reproductions: see HIGH-1/HIGH-2 (both produced CheckViolation → HTTP 500 through the real ASGI app).

---

## Sources

- TNAU Agritech Portal — Buffalo breeds (Murrah): https://agritech.tnau.ac.in/animal_husbandry/animhus_buffalo%20breeds.html
- NDDB Dairy Knowledge Portal — Murrah (yield 1,003-2,057 kg, avg 1,752; fat 6.9-8.3%, avg 7.3%): https://www.dairyknowledge.in/dkp/article/murrah
- Indian Journal of Animal Research — first lactation 305-d yield 1,750.91 kg: https://arccjournals.com/journal/indian-journal-of-animal-research/ARCC472
- ICAR-CIRB/NDRI Murrah performance review (2,147.6 kg 305-d, peak 8.87 kg/d): https://www.academia.edu/43567799/Productive_and_reproductive_performances_of_Murrah_buffalo_cows_A_review
- CIRB Hisar first-calving study: https://pmc.ncbi.nlm.nih.gov/articles/PMC9262729/
- Sawant et al. 2022 — sexed vs conventional pregnancy rate (40% vs 50%, 100% female): https://pdfs.semanticscholar.org/f59d/5038f86c330434069394fbc47f5c85261da8.pdf
- Sharma et al. 2024 — sexed vs conventional first-AI conception (55.24% vs 63.63%): https://pmc.ncbi.nlm.nih.gov/articles/PMC11188877/
- Vijaya/Sangam procurement (Sangam ₹840/kg fat): https://dairynews.today/news/vijaya-dairy-and-sangam-dairy-raise-milk-prices-amid-cost-pressures.html
- Vijaya Dairy April-2026 revision (₹85/L @10% fat): https://west.dairyindustryexpo.com/vijaya-dairy-to-increase-milk-prices-from-april-4-dairydimension/
- Luna-Palomera et al. 2021 — Wood model best fit for Murrah lactation: https://www.scielo.cl/scielo.php?script=sci_arttext&pid=S0719-38902021000300200
- Aziz 2006 — lactation-curve shapes (Wood incomplete gamma): https://www.lrrd.cipav.org.co/lrrd18/5/aziz18059.htm
- Bulgarian Murrah persistency 89.22%: https://www.lifescienceglobal.com/media/zj_fileseller/files/JBSV2N3A3-Peeva.pdf
- NABARD dairy model bankable project (Graded Murrah, lactation/dry days, economics): https://agridots.com/courses/exam-ibps-afo/agriculture/agricultural-economics/modal-bankable-projects/02-01-dairy-farming and https://www.pashudhanpraharee.com/nabard-model-dairy-farming-project-report/
- Murrah gestation 310 d / dry period: https://www.dairyfarmguide.com/murrah-buffalo-0114.html
