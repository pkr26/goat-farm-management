# Audit Remediation Log — 2026-09-01

Fixes applied in response to `00_MASTER_SYNTHESIS.md`. Every change is covered
by the existing suites plus a new behavioral file (`backend/tests/test_audit_remediation.py`).
Severity references map to the synthesis finding IDs.

## P0 (ship-stoppers)

| ID | Fix |
|---|---|
| D-1 BLOCKER | `services/tasks.py`: the fresh-pen survivorship re-check is gated on `species_profile(...).young_stay_with_dam`, mirroring `_guard_generated_movement_task`; dairy dams now exit RECOVERY via the +10d duty with a live calf (behavioral test `test_dairy_fresh_pen_exit_with_live_calf`). |
| D-2 | Migration `d1e2f3a4b5c6` widens `ck_breeding_loss_within_max_gestation` and `ck_breeding_records_result_within_max_gestation` from +200 to +350 days (global species max); per-species windows remain enforced in `services.breeding`. Day-250 buffalo abortions and day-210 PD results no longer 500. |
| S-1 | `simulation_calibration.py`: `kid_pre_weaning` calibration writes the observed whole-phase fraction directly (`died/n`) instead of annualizing via `1-(1-x)^4` (3.4× inflation removed). |

## P1 (financial integrity)

| ID | Fix |
|---|---|
| D-3 | `api/milk.py`: lactation-context fence — rejects yields for calf-shed (FEMALE_KIDS/MALE_KIDS), QUARANTINE and DELIVERY cohorts; never-calved animals rejected unless imported adult purchases (source=PURCHASED at breeding age). |
| D-4 | Milk chronology via `require_animal_event_chronology` (readings cannot predate birth/acquisition). MILK income requires litres + price provenance (schema), and `_guard_milk_sold_within_production` reconciles cumulative sold litres against recorded parlour production (±10%) on create and correct. |
| D-5 | `SpeciesProfile.voluntary_waiting_days` (goat 14, buffalo 60) enforced in `create_breeding_record` — services dated inside the postpartum VWP are refused with the earliest legal date. |
| D-6 | `SpeciesProfile.failed_services_before_cull` (goat 2, buffalo 3) replaces the species-blind constant; `cull_candidate` females additionally refuse further services unless the farm owner records them. |
| G-1 | MEAT_SALE window enforced on the write path (male MALE_KIDS below 8 months cannot be SOLD, 422 with the window quoted; cull remains the early exit) and single-sourced by the dashboard market rule; the simulator's default sale age is now 9 months on a retargeted stall-fed finish curve (male ≈ 24.4 kg at 9 mo, inside the 24–28 kg window). |
| G-3 | `services/feeding.py`: dependent kids in RECOVERY (dam in the same bucket) are planned on the CREEP line at 0.3 kg/head instead of the doe's full lactating TMR; `recipe_for_animal` accepts `is_dependent_kid`. |

## P2 (correctness & trust)

- **G-2** Dashboard suggestions are species-profile driven (breeding floors, gestation-day gates at 2/3 and 9/10 of the species gestation); the meat-market rule is goat-only and uses dated weight.
- **S-2** `min_dscr`/`avg_dscr` span principal-repaying years only (moratorium excluded, balloon exclusion preserved); optimizer feasibility gates on NABARD's average DSCR. The strong synthetic (₹1,200/kg, ₹5k labour, 40% loan) now yields 109/109 feasible candidates with a recommendation; default presets honestly report their thin margins instead of structural rejection.
- **S-3** Labour scales on adult breeding females (TNAU/NABARD per-doe norm): the 50+2 flagship hires 1 worker, not 3.
- **S-4** Dairy preset: milk growth 6% matches feed 6%; ₹/kg-fat default moved to the verified ₹850 procurement basis (fallback ₹58/L now consistent at 6.8% fat).
- **S-5** Dairy-mode sire sizing no longer double-counts the lactation overlay.
- **D-7** The day-90 dairy milk-weaning duty now moves surviving heifers FEMALE_KIDS→FOUNDATION (transition context `weaning`, species-gated); goat path unchanged.
- **D-8** `replan_dam_after_last_kid_death` realigns dairy calf deaths (calf-shed cohorts), cancels a childless litter's weaning duty, and leaves the dam (already in the milking string) untouched.
- **D-9** Litter caps enforced per species (goat ≤4, buffalo ≤2) with 422.
- **D-12** Per-animal daily milk sanity band (buffalo 40 L/day) covers corrections as well as new readings.
- **Dry-off alignment** The dairy DELIVERY move rides the dry-off point (EDD−60), matching the therapy task and README; the delivery-duty due-date check follows.
- **X-1** `GET /api/simulation/herd-snapshot` now requires `animals.view` in addition to `simulation.view`.
- **F-1** Animals list validates `?bucket/sex/status` against the generated enums (fallback ALL, never a guaranteed-422 request) and the error branch offers Clear filters.
- **Contract (C-1..C-4)** Router-level `COMMON_ERROR_RESPONSES` (400/401/403/404/409/429 + declared 500 on the calibration corrupt path); ~50 Out fields re-typed as enum Literals (TS unions tightened); `AnimalCreateIn.tag_number` uses `MAX_ANIMAL_TAG_LENGTH`; parity tests now cover 17 vocabularies, all caps, goat-profile aliases and species policy knobs.
- **ET+TT booster** Second pre-kidding dose generated at EKD−25 (the seeded template's "two doses 15 days apart" is now real).

## P3 (hardening)

- Quarantine sale fence (no SELL out of the 45-day protocol; cull stays open); quarantine ration 1.1 kg (≈3% BW DM).
- Buck mating policy enforced: a sire is refused his 21st open service.
- Manual ledger rows cannot use system categories (ANIMAL_SALE/ANIMAL_PURCHASE); MILK-price bounds tightened (fat 3–12, ₹/L ≤500, ₹/kg-fat ≤5,000).
- ORF dropped from the default vaccine calendar; PPR repeat 12 months (field convention); heifer floor unified at 24 months (AFC ≈ 35 mo).
- Forced credential rotation: `users.must_change_password` (migration `e3a5b7c9d1f2`) set on worker create/reset, cleared only by a self-service change; flagged users are blocked from domain routes (auth routes exempt) and the UI carries a banner.
- Register duplicate-email probes charged on a dedicated per-email limiter (separate bookkeeping map); Compose requires a real `POSTGRES_PASSWORD`; multi-worker deployments log a loud warning (in-process budgets); dev JWT keys default outside the repo tree (existing repo keys still honored).
- Milk inputs strict (`FiniteFloat`); finance fat bound aligned (3–12).
- Frontend: strict page-number grammar; `/animals/new` preserves its query; Sparkline x-scale by filtered length; friendly 422 sentence with specifics; loss-cause labels (incl. ANIMAL_STATUS_CHANGE); DEAD/SKIPPED badge tones; litres copy fixed; `lib/backend-caps.ts` + contract-parity vitest against `shared/openapi.json`.
- Simulation API test-order robustness (in-process run-budget/lock reset per test); milk planner sizes on the steady tail year.

## Test status at log time
- Backend: `tests/test_audit_remediation.py` (9 behavioral tests) green; full-suite result recorded below.
- Frontend: `vitest run` 155 files / 3,264 tests green; `tsc --noEmit` clean.
- Contract regenerated (`shared/openapi.json` 77 paths; orval 8.23.0 re-run).

## Implementation footprint
- Backend: 2 Alembic migrations (`d1e2f3a4b5c6` gestation CHECKs, `e3a5b7c9d1f2` rotation flag);
  `models/species.py` (+4 policy fields), `models/constants.py` (aliased to the profile),
  services (breeding, kidding, tasks, animals, milk, feeding, dashboard, simulation_calibration),
  APIs (animals, milk, finance, simulation, auth, team — every router declares error responses),
  schemas (enum-typed Out fields, provenance rules, strict floats), simulation package
  (engine DSCR/labour/sire/calf-milk, defaults alignment, milk-planner tail sizing), security
  (register probe limiter, workers-1 warning, dev-key location, rotation enforcement in deps).
- Frontend: animals URL-enum validation, tasks hint, shims, Sparkline, api-client 422 copy,
  enum-labels (lossCause), status tones, backend-caps module + contract parity test,
  rotation banner, regenerated client.
- Compose: `POSTGRES_PASSWORD` now required (`:?`).

