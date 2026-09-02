# Independent Audit A1 — GOAT (meat) Farm Domain, Full Stack

- Date: 2026-09-01
- Auditor: A1 (independent, from-scratch; no prior audit output consulted)
- Repo: `/Users/pavankumarreddyreddem/Desktop/goat_saas` (branch working tree, read-only on source)
- Scope: Osmanabadi goat (meat, Telangana) domain — backend models/services/API/seed/simulation goat paths, frontend goat pages/libs, and real-world verification against ICAR/CIRG/NBAGR/TNAU/NABARD/Vikaspedia/DAHD-NADCP sources.

**Executive summary.** The goat lifecycle state machine (breed → PD-32 → kidding → RECOVERY → wean-60 → RESTING → rebreed; quarantine-45 → FOUNDATION) is implemented with unusual care: legal-transition contexts, lock ordering, date arithmetic, and idempotence guards all check out end-to-end, and 485 targeted backend tests plus the frontend lib suites pass. The dominant problems are (a) a family of SPEC goat constants that are **dead code** — most importantly the meat-sale window (8–9 mo / 24–28 kg), which has no task, no enforcement, and only stray duplicated literals in the dashboard; (b) the **dashboard suggestion engine bypasses the species profile** and would emit goat thresholds on buffalo farms; (c) the daily **feeding plan counts nursing kids as full adult ration heads** while the seeded CREEP recipe is unreachable; and (d) the **financial simulation and the operational module disagree** on sale age/weight, weaning age, buck:doe ratio, DMI and concentrate share, so the lender-facing projection models a different farm than the one the app runs.

---

## 1. FINDINGS

Severity counts: **0 BLOCKER, 3 HIGH, 6 MEDIUM, 9 LOW, 5 INFO.**

---

### HIGH

#### H1. The meat-sale policy (MEAT_SALE_AGE_MONTHS / MEAT_SALE_WEIGHT_KG) is dead code — the terminal money event of a meat farm has no system support
- **Where:** `backend/app/models/constants.py:36-37`; re-exported `backend/app/models/__init__.py:26-27`; only references outside constants are `backend/tests/test_unit_extended.py:424-425`, which assert the constants against their own values. `BUCK_DOE_RATIO` (:35) and `BUCK_ROTATION_DAYS` (:34) are equally dead (no service, task, or guard uses them).
- **What's wrong:** `grep -rn "MEAT_SALE"` over `app/` returns only `constants.py` and the `__init__` re-export. No task category, no readiness task, no sale guard, no report metric consumes the 8–9-month / 24–28-kg window. It exists as prose in the seeded bucket text (`seed.py:118-120`, MALE_KIDS "Sold at 8–9 months, 24–28 kg") and as duplicated magic numbers in the dashboard (`services/dashboard.py:135-136`: `add_months(reference_date, -8)` and `>= 24.0`). Every earlier lifecycle stage gets a generated duty (PD at +32 d, pre-kidding ET+TT at −40 d, DELIVERY move at −15 d, kidding due, weaning at +60 d); the male kid's market exit — the farm's primary revenue event — generates nothing.
- **Evidence (code):**
  ```python
  # constants.py:36-38
  MEAT_SALE_AGE_MONTHS = (8, 9)
  MEAT_SALE_WEIGHT_KG = (24.0, 28.0)
  MAX_FAILED_CYCLES_BEFORE_CULL = 2   # ← this one IS used (breeding.py:698)
  ```
- **Real-world check:** 8–9 mo at 24–28 kg is at the optimistic top of published Osmanabadi performance: 12-month field/grazing weights 15–17 kg; stall-fed comparisons 13.65 kg @3 mo / 21.55 kg @6 mo; commercial 6–12-month male listings 20–30 kg ([ICAR IJAnS improved-management study](https://epubs.icar.org.in/index.php/IJAnS/article/view/11545), [breed status/vet-extension summaries](https://www.facebook.com/Drmssaravananvet/posts/osmanabadi-goat-breeda-important-national-genetic-resource-the-osmanabadi-goat-i/1637236111739418/)). A system that claims this window should at least surface a market-ready duty with the target weight, and the constants should be the single source for the dashboard literals.
- **Fix:** wire `MEAT_SALE_*` into (a) the dashboard `market_rule` instead of `-8`/`24.0` literals, and (b) a generated "market ready" reminder on the male kid at the age/weight threshold (mirroring the WEANING task pattern), or delete the constants and admit the SPEC item is unimplemented.

#### H2. Dashboard "ready to move" suggestions bypass `species_profile` — goat thresholds would be shown on buffalo-dairy farms, and the goat numbers are unanchored duplicates
- **Where:** `backend/app/services/dashboard.py:138` (`add_months(reference_date, -MIN_BREEDING_AGE_MONTHS)` → 10 mo), `:157`/`:166` (`>= MIN_BREEDING_WEIGHT_KG` → 22 kg), `:135` (`-8` months), `:158` (`>= 24.0` kg), `:169-176` (gestation day `100`/`135` and RESTING `30`-day literals).
- **What's wrong:** every write path and list path in `services/breeding.py` / `services/animals.py` resolves biology through `species_profile(farm.farm_type)`; `ready_to_move_suggestions` takes `farm` but never reads `farm.farm_type`. On a `BUFFALO_DAIRY` farm the widget would suggest moving a 10-month/22-kg heifer calf to BREEDING (real gate: 22 months / 340 kg, `species.py:87-88`) and selling male buffalo calves at 8 months/24 kg (dairy preset grows them to 14 months, `simulation/defaults.py:302`). The suggestion also uses `latest_weight` (undated) for the market rule while breeding rules correctly use `latest_weight_as_of`.
- **Evidence (code):**
  ```python
  # dashboard.py — no farm_type anywhere in the module
  age_cutoff = add_months(reference_date, -MIN_BREEDING_AGE_MONTHS)   # goat constant, all species
  male_sale_age_cutoff = add_months(reference_date, -8)               # magic number
  ...
  context.c.latest_weight >= 24.0,                                    # magic number
  ```
- **Fix:** derive doe rules from `species_profile(farm.farm_type)` and gate the male-market rule to `farm_type == GOAT` (or species-parameterise it).

#### H3. Daily feeding plan bills every kid in RECOVERY a full adult doe ration (1.5 kg/head of lactating TMR); the seeded CREEP recipe is unreachable
- **Where:** `backend/app/seed.py:102-106` (RECOVERY "Doe + kids together… 1.5" kg/head), `backend/app/services/feeding.py:393-412` (`feeding_plan` groups **all** ACTIVE animals by bucket; heads × `daily_kg_per_head`), `backend/app/seed.py:273-283` (CREEP recipe, "Kids weeks 2–8").
- **What's wrong:** a doe with twin live kids in RECOVERY produces a plan line of 3 × 1.5 = 4.5 kg/day of LACTATING_60_40. A 4–8-week kid eats ≈3% of an 8–12 kg body weight ≈ 0.3–0.4 kg; 1.5 kg of a 60:40 as-fed mix per kid is ~4–5× intake and ~54% concentrate on a DM basis. Creep feeding from 2–3 weeks is the standard stall-fed meat-goat practice the SPEC itself encodes — but `grep CREEP` finds only `seed.py` and `RECIPE_DISPLAY` (`feeding.py:48`); no bucket, age rule, plan path, or frontend string ever assigns it. The repo's own test fixture dodges the effect by using a STILLBORN kid "without adding a second RECOVERY animal to this ration test" (`tests/test_feeding_extended.py:264-267`), confirming live kids inflate the plan.
- **Real-world:** creep + kid starter for pre-weaner kids and ~3% BW DMI are standard (ICAR/TNAU goat feeding; MSD Vet Manual caps goat DMI at ~6.5% BW, meat-goat profitable zone 2.5–3%).
- **Fix:** exclude dependent kids from the doe's ration line and add a kid/creep line (or make RECOVERY per-head explicitly "per doe" with a documented kid allowance), and expose CREEP in the plan/recipes UI or delete it.

---

### MEDIUM

#### M1. Simulation's default sale age (10 mo) and weight curve contradict the operational SPEC window (8–9 mo, 24–28 kg)
- **Where:** `backend/app/simulation/assumptions.py:292` (`sale_age_months=10`, "Navipet practice markets Osmanabadi males at 8-10 months"), `:256-270` (weight curve 12.1 kg @3 mo … 20.5 kg @12 mo), vs `backend/app/models/constants.py:36-37`.
- **What's wrong:** the engine sells males at 10 months at `weight_at_age(10)×1.1 ≈ 22.0 kg` (`engine.py` step 1, `male_weight_at_age`), i.e. below the operational 24–28-kg floor even a month later than the SPEC window; on its curve 24 kg is only reached around 12–14 months. Either the ops target is optimistic or the sim curve is pessimistic — published data spans both extremes (grazing 12-mo 15–17 kg vs well-managed stall-fed males ~30 kg at 12 mo), but the two modules of the same product must agree, otherwise the 10-year projection prices a different animal than the dashboard tells the farmer to sell.
- **Fix:** single source the sale window (age + weight) shared by `constants.py`/dashboard and the sim defaults, or parameterise the sim preset from the farm's operational policy.

#### M2. Operational lactating ration vs simulation feed model: ~3.0% BW DMI at ~54% concentrate-of-DM vs 4.5% BW DMI at 20% concentrate-of-DM
- **Where:** `backend/app/seed.py:229-241` (LACTATING_60_40: 36 green + 24 dry + 40 conc as-fed) + `seed.py:104-106` (1.5 kg/head); `backend/app/simulation/assumptions.py:452-453` (`dmi_doe_lactating=0.045`, `concentrate_share_doe_lactating=0.20`), `:467-469` (DM% 0.25/0.88/0.90).
- **What's wrong:** 1.5 kg of the 60:40 mix = ~0.99 kg DM ≈ 3.0% of a 33-kg doe, with concentrate ≈54% of DM (0.40×1.5×0.90 = 0.54 kg). The simulation feeds the same doe 1.49 kg DM (4.5% BW) with only 0.30 kg concentrate. The daily plan under-feeds total DM relative to the sim's assumption while feeding nearly double the concentrate share — and the sim's feed cost (a lender-facing number) therefore does not model what the feeding module dispenses. The ops module also never states whether `daily_kg_per_head` is as-fed or DM.
- **Fix:** declare the basis of `daily_kg_per_head`, and reconcile per-class DMI and concentrate shares (ICAR/TNAU zone: lactating meat-goat DMI ≈3.5–4.5% BW; concentrate typically ≤1% BW for meat does).

#### M3. Simulation weaning at 3 months vs operational weaning at day 60
- **Where:** `backend/app/simulation/engine.py:37-39` ("Weaning is modelled at month 3 … versus the operational system's day-60 wean task — a documented monthly-resolution approximation"); `assumptions.py:214-217` (kid 0–2 mo class, whole-phase mortality over 3 monthly slots).
- **What's wrong:** documented, but systematic: the 10% pre-weaning mortality default is spread over three monthly slots while the real pre-wean period is two months, and kid feed is charged for an extra month. Minor skew on every meat-scenario P&L. (The sim's own docstring admits it.)
- **Fix:** shift the kid class to 0–1 months (two slots) or note the bias in the explain output.

#### M4. Dashboard/seed text promises a DELIVERY-ward stay "day ~5–10 post-kidding" and a 7-day buck rotation that the code never implements
- **Where:** `backend/app/seed.py:95-99` (DELIVERY: "Last ~2 weeks of pregnancy through ~5 days post-kidding … Day ~5–10 post-kidding → RECOVERY") and `:76` ("1 buck per 20 does (rotate 7d on/off)"), vs `backend/app/services/kidding.py:255-266` (doe moves DELIVERY → RECOVERY **on the kidding date**) and `constants.py:34-35` (BUCK_ROTATION_DAYS/BUCK_DOE_RATIO unused anywhere).
- **What's wrong:** the bucket definition describes a post-kidding DELIVERY phase that does not exist in the state machine (the only post-kidding bucket is RECOVERY, exited at weaning or +14 d with no survivors), and the "rotate 7d on/off" mating management exists only as prose. Code-vs-own-documentation drift on the primary lifecycle view (the buckets page renders this text).
- **Fix:** align the seeded exit rules with the implemented transitions, or implement the rotation duty; delete the unused constants (see L1).

#### M5. Quarantine per-head feed (0.8 kg) is below maintenance for a 30-kg adult goat for 42 of 45 days
- **Where:** `backend/app/seed.py:62-65` (QUARANTINE 0.8 kg/head), `backend/app/services/feeding.py:257-258` (days 1–3 dry roughage, then MAINTENANCE_75_25 — same 0.8 kg/head rate).
- **What's wrong:** 0.8 kg of the 75:25 mix ≈ 0.60 kg DM ≈ 2.0% BW for a 30-kg doe vs the ~3% maintenance DMI the simulation itself assumes (`assumptions.py:451`). Under-feeding newly transported animals during a 45-day quarantine compromises the immunity the quarantine exists to protect. (Farm-level override exists via `BucketFeedSetting`, but the default is biologically off.)
- **Fix:** raise the quarantine default to ~1.1–1.2 kg (or species-scale it) and/or differentiate the day-1–3 roughage-only rate from the post-transition maintenance rate.

#### M6. Pre-kidding ET+TT: template promises "two doses 15 days apart", the workflow generates one task
- **Where:** `backend/app/seed.py:388-393` ("ET + TT pre-kidding … two doses 15 days apart; each pregnancy") vs `backend/app/services/breeding.py:604-614` (single `PRE_KIDDING_VACCINE_TITLE` task at `ekd - 40 days`) and `backend/app/services/health.py:40`.
- **What's wrong:** the generated duty series never schedules the second dose; a farm following the app will under-vaccinate dams against the SPEC/seed claim. (Real-world: the common Indian practice for clostridial/TT in pregnant does is a primary + 3–4-week booster pre-kidding — Vikaspedia lists ET booster intervals; TNAU notes first-dose-plus-booster for clostridials.)
- **Fix:** generate the booster task 15 days after the first, or correct the template's timing note.

---

### LOW

#### L1. Eight goat constants in `constants.py` are dead or duplicated code (drift hazard)
- `KIDDING_WINDOW_DAYS`, `MIN/MAX_GESTATION_DAYS`, `ULTRASOUND_AFTER_BREEDING_DAYS`, `MIN_BUCK_*`, `WEANING_DAYS`, `POSTPARTUM_RECOVERY_DAYS` are all shadowed by `species.py GOAT_PROFILE` (services read the profile); `BUCK_ROTATION_DAYS`, `BUCK_DOE_RATIO`, `MEAT_SALE_*` are referenced nowhere (H1). Values currently agree with the profile (verified line-by-line), but nothing enforces it — one edit to `species.py` silently strands `constants.py`. `MIN_BREEDING_AGE_MONTHS`/`MIN_BREEDING_WEIGHT_KG` remain live only via the species-blind dashboard (H2).
- **Fix:** delete or alias to the profile; add a parity test asserting `GOAT_PROFILE` fields equal the legacy constants (the pattern `tests/test_schema_parity.py` already uses elsewhere).

#### L2. `MIN_BREEDING_AGE_MONTHS = 10` is earlier than published Osmanabadi puberty (~11.5 months)
- `constants.py:14` / `species.py:67`. Field studies: age at first kidding 494–506 d (~16.3 mo) ⇒ first service ~345–355 d; age at puberty ~350 d at ~17.45 kg ([Osmanabadi reproductive-performance study](https://www.researchgate.net/publication/354384449_The_Reproductive_Performance_of_Native_Osmanabadi_Goat_of_India), [breed-tract characterization](https://www.researchgate.net/publication/326369967_MORPHOLOGICAL_CHARACTERIZATION_OF_OSMANABADI_GOAT_IN_ITS_BREEDING_TRACT)). The 22-kg weight gate (above the ~17.5-kg puberty weight) makes the 10-month floor defensible for well-fed doelings and matches the SPEC's "10–12 mo" text — but the floor alone permits a precocious 10-month service if a weight is mis-scaled. Acceptable with the caveat documented.

#### L3. `mark_aborted` can 422 on a legitimate loss after an owner history correction
- `services/animals.py:54` allows BREEDING→RESTING only via `"manual"`. A doe history-overridden from PREGNANCY_* back to BREEDING and then suffering a recorded loss hits `ValueError("Illegal lifecycle transition BREEDING → RESTING")` (`services/breeding.py:757-769`) — the loss is unrecordable until the owner manually re-stages the buckets. Narrow, owner-triggered only; the API maps it to 422 not 500.

#### L4. ORF vaccine template is not standard Indian practice
- `seed.py:386` ("ORF", 4 mo, repeat 6-monthly). TNAU's goat disease page states "We do not vaccinate for Sore Mouth" ([TNAU Agritech](https://agritech.tnau.ac.in/animal_husbandry/ani_goat_d%20mgt%20&%20v%20schedule.html)); ORF control in India is outbreak-driven (autogenous/live vaccines under vet direction). A default 6-monthly ORF duty on every animal is over-prescriptive and will spam the schedule. Suggest flagging as optional/region-specific like Anthrax's note.

#### L5. PPR repeat set to 36 months vs the field convention of annual revaccination
- `seed.py:373` ("PPR", 3 mo, repeat 36). Immunity of ≥3 years is supported by research ([PMC PPR vaccination review](https://pmc.ncbi.nlm.nih.gov/articles/PMC4663708/)), but Telangana/Vikaspedia field schedules revaccinate yearly ([Vikaspedia goat vaccination schedule](https://en.vikaspedia.in/viewcontent/agriculture/livestock/sheep-and-goat-farming/vaccination-schedule-for-goats?lgn=en)). Defensible, but should be surfaced as a farm-policy choice because department camps will flag the herd as overdue.

#### L6. Buck:doe ratio drift between ops (20) and simulation (25)
- `constants.py:35` (dead, but quoted in seed text "1 buck per 20 does") vs `simulation/assumptions.py:233` (`buck_doe_ratio=25`). Both inside the published 1:20–30 range ([KSU](https://www.kysu.edu/academics/college-ahnr/school-of-anr/co-op/publications-goat-management-breeding-season-pdf.php), NABARD-style project reports use 50+2 … 500+25), so both are "right" — but the product states two different herd policies.

#### L7. Simulation `age_at_first_breeding_months=12` vs operational floor of 10 months
- `assumptions.py:174` vs `constants.py:14`. The sim is closer to published puberty; the ops floor is the aggressive one (see L2). Same reconciliation as M1.

#### L8. Dashboard market rule uses undated `latest_weight` (latest ever) while breeding rules use `latest_weight_as_of`
- `services/dashboard.py:104-110`, `:158`. A future-dated weight record typo cannot inflate breeding eligibility but can inflate a "market ready" suggestion. Cosmetic-to-minor; the sale itself is unguarded anyway (H1).

#### L9. FMD first dose at 3 months matches TNAU but not Vikaspedia (4 months)
- `seed.py:372` ("FMD", 3 mo). TNAU: "first vaccination at 3rd month, once in 4–6 months" ([TNAU](https://agritech.tnau.ac.in/animal_husbandry/ani_goat_d%20mgt%20&%20v%20schedule.html)); Vikaspedia/NADCP materials commonly say 4 months for kids. Sources genuinely conflict; the app's 6-monthly repeat (Sep/Mar, matching NADCP rounds — [DAHD NADCP](https://www.dahd.gov.in/en/schemes/programmes/nadcp)) is correct and the important half.

---

### INFO (observations, no action required)

1. **State machine verified legal end-to-end.** `LEGAL_BUCKET_TRANSITIONS` (`services/animals.py:41-68`) covers every context actually emitted: breeding (`BREEDING` from FOUNDATION/FEMALE_KIDS/RESTING), ultrasound (BREEDING→PREGNANCY_EARLY), delivery (`PREGNANCY_{EARLY,LATE}`/DELIVERY→DELIVERY via task), kidding (→RECOVERY), weaning (RECOVERY→MALE/FEMALE_KIDS + doe→RESTING), orphan weaning on dam exit (`api/animals.py:1116-1136`, context `"weaning"`), postpartum no-survivor (+14 d), abortion (→RESTING with `allow_restricted_reclassification`). `history_override` bypass is deliberate and its reason-prefix convention is consistently honoured in kid-dependency tests (`kidding.py:401-414`, `tasks.py:445-474`).
2. **Date arithmetic verified.** `expected_kidding_date = breeding + 150 d`; `planned_ultrasound_date = +32 d`; PD task on breeding date; pre-kidding vaccine at EKD−40 d; DELIVERY move at EKD−15 d; weaning at kidding+60 d; no-survivor recovery at anchor+14 d (anchor = max(kidding, mortalities)); all three guard functions re-derive exactly these offsets (`tasks.py:283-296`, `:314-327`). `add_months` month-end clamping is correct and consistent with `age_months_on`.
3. **Ledger integrity verified.** Sale books `ANIMAL_SALE` income keyed `source_type="ANIMAL_SALE", source_id=animal.id` (`api/animals.py:1167-1180`); health events book MEDICINE/VET with exact paise-split per animal (`services/health.py:292-388`); feed restocks book FEED with quantity/unit-price provenance (`services/feeding.py:584-618`). Withdrawal blocks sale/cull via `withdrawal_until >= status_date` (`api/animals.py:964-971`) and is bounded by `MAX_WITHDRAWAL_DAYS=730` (`api/health.py:794-802`).
4. **Tests:** `pytest tests -k "goat or kidding or breeding or weaning"` → **485 passed** (on an isolated `goatfarm_test_goataudit` DB; a parallel audit run owned the default DB — the initial shared-DB run deadlocked on the seed advisory lock, an environment collision, not a code defect; throwaway DB dropped after the run). Frontend: `vitest run src/lib/task-action-access.test.ts src/components/display-components.test.tsx` → 45 passed.
5. **Frontend parity verified.** `farm-vocabulary.ts` GOAT entry matches `GOAT_PROFILE` exactly (10/22 doe, 12/25 buck, 100–200 gestation band, PD 32 d, wean 60 d, youngStayWithDam). Kidding page validates the same min/max gestation band as the backend error strings; breeding page quotes the same gates; dashboard/reports/feeding are species-vocabulary driven; `₹` Indian digit grouping in `format.ts`. No goat/dairy copy leakage found in the goat pages.

---

## 2. VERIFIED CORRECT (checked against sources)

| Constant / rule | Code | Published reference | Verdict |
|---|---|---|---|
| Gestation 150 d; window 145–155 | `constants.py:5-6`, `species.py:62-63` | Merck/MSD Vet Manual: 145–155 d, avg 150 | ✔ |
| PD/ultrasound at +32 d | `constants.py:13`, `species.py:66` | Transrectal US reliable from day 30–35 | ✔ |
| Recording sanity band 100–200 d | `constants.py:11-12` | Generous but documented as data-entry sanity, not biology | ✔ (by design) |
| Doe breeding floor 10 mo + ≥22 kg | `constants.py:14-15` | Puberty ~350 d / ~17.5 kg; first kidding ~16 mo | ✔ with caveat (L2) |
| Buck floor 12 mo + ≥25 kg | `constants.py:21-22` | Conservative vs adult buck 36–45 kg | ✔ |
| Weaning 60 d | `constants.py:23` | 8–12-week weaning with creep feed is standard | ✔ |
| Postpartum recovery 14 d (no-survivor path) | `constants.py:33` | Osmanabadi PPA ~67 d; rebreeding here follows weaning+30 d resting, not the fresh pen | ✔ |
| Failed-cycle cull at 2 | `constants.py:38`, `breeding.py:689-704` | Common station discipline (2–3 failed services) | ✔ |
| SHIFT_SPLIT 40/20/40 (6:30/13:30/19:30) | `constants.py:75-79`, `feeding.py:92-96` | Sum=1.0; gram-exact largest-remainder split verified | ✔ (asked-for check: it is 40/20/40, not 40/20/20) |
| MAX_WITHDRAWAL_DAYS 730 | `constants.py:50`, enforced `api/health.py:794` | Longest real veterinary withdrawals are weeks; bound is fail-safe | ✔ |
| 45-day quarantine protocol (deworm d4, PPR d10, ET+TT d20, goat pox d30, FMD d40, release d45) | `models/helpers.py:62-75` | Matches NABARD-style purchased-animal protocols and the vaccine ordering used in Indian schedules | ✔ |
| FMD 3 mo + 6-monthly (Sep/Mar) | `seed.py:372` | TNAU 3 mo/4–6-monthly; NADCP Sep–Oct & Mar–Apr rounds | ✔ (L9 footnote) |
| ET 4 mo, annual pre-monsoon | `seed.py:375-380` | TNAU "once in a year before onset of monsoon"; Vikaspedia 4 mo | ✔ |
| Goat pox 3 mo, annual | `seed.py:382` | Vikaspedia/TNAU 3 mo, yearly | ✔ |
| PPR 3 mo | `seed.py:373` | Vikaspedia 3–4 mo | ✔ (repeat interval — L5) |
| Deworming 6-monthly (kids 3-monthly note) | `seed.py:396-401` | Standard Indian practice | ✔ |
| Anti-coccidial 1–3 mo (Amprolium) | `seed.py:403-408` | Coccidiosis peaks 1–6 mo in kids | ✔ |
| Recipe lines each total exactly 100 kg/100 kg; 50:50, 60:40, 75:25, 70:30 splits as named | `seed.py:212-284` (sums re-computed by audit and by `mix_feed_batch`'s own total check) | Roughage:concentrate ratios appropriate for the stated classes | ✔ |
| Buck replacement 3 y (sim) | `assumptions.py:232` | Replace bucks every 2–3 y to avoid inbreeding | ✔ |
| Sim Osmanabadi weights: birth 2.5, doe 33, buck 42, yearling 20.5 | `assumptions.py:249-270` | NBAGR/TNAU: doe 27–36 kg, buck 36–45 kg; stall-fed yearling 19.6–21.5 | ✔ |
| Sim litter size 1.6 | `assumptions.py:169` | Osmanabadi ~1.7 (1.75 prolificacy; ~50% twins) — slightly conservative | ✔ |
| Sim conception 0.85/service | `assumptions.py:155` | Upper end of ICAR herd-model range (0.7–0.85 natural service) | ✔ |
| Sim kidding-interval logic (5 mo gestation + 1 mo open) | `assumptions.py:160-168` | Published intervals 232–297 d ⇒ ~7.7–9.8 mo cycle | ✔ |
| Sim mortality 10% pre-wean / 5% post-wean / 4% grower / 5% adult | `assumptions.py:214-219` | Indian stall-fed pre-weaning 5–15% (NABARD models 15%) | ✔ |
| Sim meat price ₹370/kg live, cull doe ₹220, Bakrid uplift 35% | `assumptions.py:327-356` | Telangana 2025-26: ₹250–450/kg live; festival premium 20–40% | ✔ |
| Sim DMI 3–4.5% BW by class; green:dry 2:1 | `assumptions.py:448-456`, `feed.py:17-19` | Goat DMI 3–5% BW (meat 2.5–3%, lactating 3–4.5%) | ✔ (but see M2 on ops divergence) |
| Sim costs: vet ₹450/head/yr, labour ₹14k/mo, shed ₹6k/place, feed conc ₹25/kg | `assumptions.py:541-569` | 2025-26 Telangana ranges as commented | ✔ |
| Bakrid calendar 2026 = May 28, drifting ~10.5 d/yr | `market.py:98-133` | Consistent with published Eid al-Adha dates | ✔ |
| `monthly_mortality_rate` / `phase_monthly_mortality_rate` compounding | `engine.py:116-133` | Math verified (12 monthly slots remove exactly the annual fraction; 3 slots remove the whole-phase fraction) | ✔ |
| EMI/amortization/IRR/MIRR/NPV | `finance.py` | expm1/log1p annuity form verified; moratorium + final-balance clamp correct | ✔ |
| `age_months_on` / `add_months` consistency (SQL cutoff vs Python months) | `breeding.py:92-102`, `animals.py:252-260`, `utils.py:77-87` | Month-end clamping consistent across both paths | ✔ |

**Sources used for real-world verification**
- MSD/Merck Veterinary Manual (goat gestation 145–155 d; ultrasound day 30+); Veterian Key (transrectal 25–30 d)
- ICAR ePublishings — [Improved management of Osmanabadi goats](https://epubs.icar.org.in/index.php/IJAnS/article/view/115455)
- [Reproductive performance of native Osmanabadi goats](https://www.researchgate.net/publication/354384449_The_Reproductive_Performance_of_Native_Osmanabadi_Goat_of_India) (AFK 494 d, KI 232.6 d, gestation 152.2 d, PPA 67 d)
- [Morphological characterization of Osmanabadi in its breeding tract](https://www.researchgate.net/publication/326369967_MORPHOLOGICAL_CHARACTERIZATION_OF_OSMANABADI_GOAT_IN_ITS_BREEDING_TRACT) + [NBAGR goat breeds](https://nbagr.res.in/goat-breed) (INDIA_GOAT_1100_OSMANABADI_06017)
- [TNAU Agritech — goat breeds](https://agritech.tnau.ac.in/animal_husbandry/ani_goat_breeds%2520of%2520goat.html) (buck 36–45 kg, doe 27–36 kg) and [TNAU vaccination/disease management](https://agritech.tnau.ac.in/animal_husbandry/ani_goat_d%20mgt%20&%20v%20schedule.html)
- [ICAR-CCARI goat page](https://ccari.res.in/dss/goat.html) (~35/30 kg adults)
- [Vikaspedia — goat vaccination schedule](https://en.vikaspedia.in/viewcontent/agriculture/livestock/sheep-and-goat-farming/vaccination-schedule-for-goats?lgn=en)
- [DAHD — NADCP](https://www.dahd.gov.in/en/schemes/programmes/nadcp) (6-monthly FMD mass vaccination; small ruminants included since 2024); [PIB NADCP release](https://www.pib.gov.in/PressReleasePage.aspx?PRID=2302242)
- [PMC — PPR vaccine and vaccination in India](https://pmc.ncbi.nlm.nih.gov/articles/PMC4663708/) (immunity duration)
- Haldar et al. 2014 ([PMC4093192](https://pmc.ncbi.nlm.nih.gov/articles/PMC4093192/)) — prolificacy 175% in Indian native goats
- NABARD model bankable goat projects ([Pashudhan Praharee NABARD report](https://www.pashudhanpraharee.com/commercial-goat-farming-project-report-as-per-nabard/), 50+2 / 500+25 buck ratios)
- KSU goat management (buck:doe 20–30 mature); Snyman 2010 / West Bengal / Sirohi pre-weaning mortality 6–16%
- Live-price context 2025-26: IndiaMART/Hyderabad mandi listings ₹250–450/kg (e.g. [Osmanabadi listing](https://www.indiamart.com/proddetail/osmanabadi-live-goat-17036621948.html), [Hyderabad mandi rates](https://dir.indiamart.com/hyderabad/pet-goat.html))

---

## 3. Method notes / coverage

- Read in full: `constants.py`, `species.py`, `enums.py`, `animals.py` (model+service), `breeding.py`, `kidding.py`, `feeding.py`, `health.py`, `tasks.py`, `chronology.py`, `helpers.py`, `seed.py`, `dashboard.py` (service+api), `api/{breeding,kidding,animals}.py` (sale/status/weight/move paths), `simulation/{assumptions,defaults,feed,market,shocks,finance,planner}.py` and the goat-relevant halves of `engine.py`; frontend `farm-vocabulary.ts`, `enum-labels.ts`, `task-action-access.ts`, `format.ts`, `use-farm-type.ts`, dashboard/kidding/breeding/feeding/reports pages (grep-verified species parity), `kidding/new` redirect.
- Every seed recipe was arithmetically re-summed; SHIFT_SPLIT and its gram allocation were recomputed; the goat transition table was walked against every emitting call site.
- Tests run read-only against a throwaway DB; no servers started; no source files modified.
