# V1 Backend Domain Verification — 2026-09-01 remediation

**Verifier:** V1 (independent; did not author the fixes)
**Date:** 2026-09-01/02 · **Repo:** `goat_saas/backend` @ working tree (read-only)
**Method:** source reading + live in-process probes (httpx `ASGITransport`, real Postgres) in
`/tmp/v1_probes/test_v1_probes.py`, driven through the same fixtures as the repo suite
(`GOATFARM_TEST_DB=v1_verify_test`, recreated + migrated by conftest). A second scratch DB
`v1_alembic_test` was used for the from-scratch Alembic check and then dropped; `v1_verify_test`
is dropped automatically at session teardown. No source files were modified and no servers were run.

## Verdict summary

| # | Claim | Verdict |
|---|---|---|
| 1 | D-1 dairy fresh-pen exit with live calf; goat still refuses | **VERIFIED** |
| 2 | D-2 buffalo day-250 abortion / day-210 PD not 500; migration `d1e2f3a4b5c6` | **VERIFIED** |
| 3 | D-5/D-6 VWP 60-day; cull flag on 3rd failure; flagged dam refuses non-owner services | **VERIFIED** |
| 4 | G-1/G-4 meat-sale window; buck 21st open service | **VERIFIED** |
| 5 | D-3/D-4/D-12 milk context / chronology / provenance / sold-vs-produced / 40 L day cap | **VERIFIED** |
| 6 | G-3 CREEP line at 0.3 kg/head for RECOVERY kids with RECOVERY dam | **VERIFIED** |
| 7 | D-7/D-8/D-9 weaning graduation, calf-death realignment, litter caps | **PARTIAL — D-7 FAILED** (duty completes 200 but the heifer is NOT moved; D-8 and D-9 verified) |
| 8 | Prescribed pytest suite | **VERIFIED — 313 passed, 0 failed** (144 s) |

**Bottom line:** 7/8 claims verified. One shipped fix does not work end-to-end: the day-90 dairy
weaning duty is still a silent no-op through the API (D-7), because the task-completion route never
loads the dairy calves the service-layer fix tries to move.

---

## 1. D-1 (BLOCKER) — dairy fresh-pen exit with a live calf → VERIFIED

**Code.** The survivorship re-check is species-gated in both places:
- `app/services/tasks.py:269-286` (`_guard_generated_movement_task`): raises only when
  `movement_profile.young_stay_with_dam` (goat `True`, dairy `False` — `app/models/species.py:86/112`)
  and `_litter_has_surviving_kid(...)`.
- `app/services/tasks.py:398-410` (`complete_task` postpartum branch): identical
  `recovery_profile.young_stay_with_dam` gate before "invalid while a kid survives".

**Probe (dairy).** Purchased Murrah dam → AI 380 d ago → PD+65 pregnant → calved on EKD
(2026-06-23) with one live heifer calf (calf auto-placed `FEMALE_KIDS`, dam → `RECOVERY`):

```
PROBE| D1 complete +10d fresh duty -> 200   (task "Move V1-D1-DAM to RESTING after the fresh period", due 2026-07-03)
PROBE| D1 dam bucket=RESTING calf status=ACTIVE   (calf still FEMALE_KIDS)
```

**Probe (goat refusal).** A goat postpartum `BUCKET_MOVE` duty was synthesized with exact workflow
provenance (auto-generated, breeding link, due = kidding+14) while twins were alive in RECOVERY
(note: the goat workflow itself never emits this duty while a kid survives, so the duty had to be
shaped by hand to reach the guard). Completion through the real API:

```
PROBE| D1 goat postpartum complete (expect refusal) -> 409
       {"detail": "The postpartum movement duty has no eligible kidding record"}
```

409 raised from `tasks.py:275-278`; the doe stayed in `RECOVERY`. The goat path still refuses.

## 2. D-2 — late buffalo records no longer 500 → VERIFIED

**Code.** `alembic/versions/d1e2f3a4b5c6_widen_gestation_check_constraints.py` (revises `f8a2c4e6b1d9`)
widens both CHECKs to `+350`; ORM mirrored at `app/models/breeding.py` (`ck_breeding_loss_within_max_gestation`,
`ck_breeding_records_result_within_max_gestation`, both `+ 350`). Per-species windows stay in
`services/breeding.py` (`max_gestation_days`: goat 200 / buffalo 350).

**Fresh-DB migration.** `CREATE DATABASE v1_alembic_test` → `GOATFARM_DATABASE_URL=…v1_alembic_test
python -m alembic upgrade head` → exit 0, head = `e3a5b7c9d1f2`, chain includes
`f8a2c4e6b1d9 -> d1e2f3a4b5c6 -> e3a5b7c9d1f2`. Constraint defs in the migrated DB:

```
ck_breeding_loss_within_max_gestation           … OR (loss_date <= (breeding_date + 350))
ck_breeding_records_result_within_max_gestation … OR (ultrasound_result_date <= (breeding_date + 350))
```

**Probes.** Bred 260 d ago, PD+65 → `POST /api/breeding/{id}/abort` at day 250 → **200**
(`outcome=ABORTED`, `loss_date=2026-08-22`, `loss_cause=DISEASE`). PD results dated day 210:
positive → **200** (`CONFIRMED_PREGNANT`, EKD intact); negative → **200** (`FAILED`). No 500s.

## 3. D-5/D-6 — VWP, species-aware cull flag, owner-only override → VERIFIED

**Code.** VWP: `app/services/breeding.py:469-478` (`voluntary_waiting_days`, `app/models/species.py:113` = 60).
Cull: `breeding.py:776-795` `_update_cull_candidate` with `failed_services_before_cull`
(`species.py:114` = 3, goat 2), called at `breeding.py:728`; flagged-dam service refusal with
owner override at `breeding.py:450-457` (`actor_is_owner`).

**Probes.**
```
D5 AI at calving+11 -> 409 "V1-D5-DAM is inside the 60-day voluntary waiting period after her
   calving on 2026-06-23 — earliest service date is 2026-08-22"
D5 AI at calving+61 -> 201
D6 after failed service 1: cull_candidate=False
D6 after failed service 2: cull_candidate=False        # does NOT fire on the 2nd (audit's complaint)
D6 after failed service 3: cull_candidate=True
D6 worker serves flagged dam -> 409 "… flagged as a cull candidate after 3 failed services —
   only the farm owner can record another service for her"
D6 owner serves flagged dam -> 201
```
The worker held `breeding.view/manage + animals.view` and had completed forced password rotation.

## 4. G-1/G-4 — meat-sale window + buck mating policy → VERIFIED

**Code.** G-1 write-path gate: `app/api/animals.py:1015-1037` (goat-only, male, `MALE_KIDS`,
`age_months < MEAT_SALE_AGE_MONTHS[0]` → 422 quoting the window; cull untouched). G-4:
`app/services/breeding.py:312-338` `_buck_open_service_count` + `479-486` refusal
(`BUCK_DOE_RATIO = 20`, `models/constants.py:38`).

**Probes.**
```
G1 3-month male kid SOLD -> 422 "V1-G1-KID is 2 months old — the meat-sale window opens at
   8 months and 24 kg (record a cull instead …)"   [age reported in completed months]
G1 3-month male kid CULLED -> 200
G4 20 services on one buck -> 201 ×20; 21st -> 409 "V1-G4-BUCK already covers 20 open services —
   the mating policy caps a buck at 20 does (1:20 buck:doe ratio); use another sire"
```

## 5. D-3/D-4/D-12 — milk fences → VERIFIED

**Code.** Context: `app/api/milk.py:43-50` `NON_MILKING_BUCKETS` (FEMALE_KIDS/MALE_KIDS/QUARANTINE/
DELIVERY) + guard `213-220`; lactation context (calved or imported adult purchase) `221-246`;
chronology `247-253` via `services/chronology.py:26-33`. Provenance schema:
`app/schemas/finance.py:75-105` (`_validate_milk_provenance`). Sold-vs-produced:
`app/api/finance.py:593-627` `_guard_milk_sold_within_production` (±10%), applied on create
(`643-646`) and correction (`751-755`). Day cap: `app/services/milk.py:77-96`
(`max_daily_milk_litres` = 40, `species.py:116`).

**Probes (all dairy farm, one test).**
```
D3  milk for FEMALE_KIDS calf -> 422 "… is in the female kids cohort — milk is recorded for the
    milking string only"
D4  milk dated before birth    -> 422 "Milk record cannot predate V1-MILK-DAM's recorded birth date"
D4  milk dated before purchase -> 422 "… cannot predate … recorded purchase date"
D12 third shift 18+15+20 L    -> 422 "… total yield … would exceed the 40 L/day sanity band …"
D12 correction re-key 7→30 L then +20 L afternoon -> 422 sanity band (correction path covered)
D4  MILK income, no provenance -> 422 "Milk income requires provenance: milk_litres plus a price …"
D4  MILK income 100 L vs 63 L produced -> 422 "… against 63.0 L ever recorded in the parlour …"
D4  MILK income 10 L within band (control) -> 201
```

## 6. G-3 — CREEP line for dependent kids → VERIFIED

**Code.** `app/services/feeding.py:37` `CREEP_KG_PER_HEAD = 0.3`; `is_dependent_kid` SQL predicate
`360-371` (kid with `dam_id`, dam ACTIVE in RECOVERY in the same plan); CREEP case in the plan
`393-402`; per-head override `470-477` (CREEP is per-kid, not a bucket rate);
`recipe_for_animal(..., is_dependent_kid=)` `197-228` / `274-277`.

**Probe.** Goat farm, natural service 175 d ago → kidding on EKD with live twins (kids born into
RECOVERY, dam in RECOVERY). `GET /api/feeding/plan`:

```
bucket=RECOVERY recipe=CREEP           heads=2 kg_per_head=0.3 daily_kg=0.6
bucket=RECOVERY recipe=LACTATING_60_40 heads=1 kg_per_head=1.5 daily_kg=1.5
```

Kids are billed 0.3 kg/head on the CREEP line; the doe keeps her 1.5 kg lactating ration —
the 4-5× kid over-billing is gone.

## 7. D-7/D-8/D-9 — PARTIAL (D-8, D-9 verified; **D-7 FAILED**)

### D-7 — day-90 dairy weaning duty does NOT move heifers to FOUNDATION (FAILED)

**Probe.** Dairy dam bred 410 d ago → PD+65 → calved on EKD (2026-05-24) with a live heifer
(auto `FEMALE_KIDS`). Weaning duty `Wean calves of V1-D7-DAM off milk; → FOUNDATION`, due
2026-08-22 (= calving+90), PENDING. Completed through the API:

```
PROBE| D7 complete day-90 weaning duty -> 200   (task DONE)
PROBE| D7 heifer bucket after weaning=FEMALE_KIDS      <-- no move happened
PROBE| D7 control: manual FEMALE_KIDS->FOUNDATION move -> 200   (FOUNDATION)
```

The duty completes "successfully" while the heifer never leaves FEMALE_KIDS — the same silent
no-op the audit flagged (original D-7). The manual move control succeeds, so the transition
(`FEMALE_KIDS → FOUNDATION`, context `weaning`, `app/services/animals.py:52`) is legal; the duty
path simply never executes it.

**Root cause (code).** The service-layer fix exists — `app/services/tasks.py:452-465` builds
`weaning_kids` for dairy from calf-shed buckets, `_weaning_target` `505-511` returns FOUNDATION for
heifers, and the move loop `585-605` targets FOUNDATION. But all of it iterates `affected_animals`
(= `locked_animals` passed by the route), and the route's lock predicate only ever loads the dam's
offspring that are in RECOVERY — goat biology:

```python
# app/api/tasks.py:161-169  (_lock_completion_animals)
elif target.category == TaskCategory.WEANING.value and target.animal_id is not None:
    animal_filter = or_(
        Animal.id == target.animal_id,
        and_(
            Animal.dam_id == target.animal_id,
            Animal.status == AnimalStatus.ACTIVE.value,
            Animal.current_bucket == Bucket.RECOVERY.value,   # <-- dairy calves are in FEMALE_KIDS/MALE_KIDS
        ),
    )
```

Dairy calves (born into `FEMALE_KIDS`/`MALE_KIDS`, `app/services/kidding.py:210-218`) never match,
so `weaning_kids` is empty and the completion is a no-op that still returns 200/DONE. `/complete`
(`app/api/tasks.py:570-600`) is the only completion path for a WEANING duty (the health form path
`api/health.py:885` is for health-linked duties; `api/animals.py:1148+` orphan path is RECOVERY-only
goat kids). No test in the repo asserts the heifer actually graduates (the dairy weaning tests cover
duty assignment/routing only: `tests/test_dairy.py:1034`, `tests/test_ops.py:1600`), which is why the
prescribed suite stays green.

**Fix needed:** include calf-shed offspring (species-aware bucket set) in the WEANING lock filter,
e.g. gate the bucket on the farm's species profile like `complete_task` does.

### D-8 — pre-weaning calf death realigns KidEntry and cancels the weaning duty (VERIFIED)

**Code.** `app/services/kidding.py:367-613` `replan_dam_after_last_kid_death`: dairy birth cohorts
are the calf-shed buckets (`400-407`), KidEntry realigned to DIED (`443-444`), and the dairy branch
(`481-504`) cancels the litter's WEANING duty and leaves the dam untouched.

**Probe.** Calving 110 d ago with one heifer → calf died 5 d ago (`POST /status DEAD` → 200):

```
D8 KidEntry status=DIED mortality=2026-08-27
D8 weaning duty after calf death: status=SKIPPED skip_reason="Final surviving calf died; milk weaning no longer applies"
D8 dam bucket after calf death=RECOVERY   (untouched — her exit is the +10d fresh duty)
```

### D-9 — species-aware litter caps (VERIFIED)

**Code.** `app/services/kidding.py:94-98` (`profile.max_litter_size`: goat 4 / buffalo 2,
`app/models/species.py:89/115`).

**Probes.**
```
D9 goat 5-kid litter    -> 422 "A goat kidding cannot deliver more than 4 kids (recorded 5)"
D9 buffalo 3-calf litter -> 422 "A buffalo_dairy calving cannot deliver more than 2 calves (recorded 3)"
```

## 8. Prescribed pytest suite → VERIFIED

```
$ GOATFARM_TEST_DB=v1_verify_test ./.venv/bin/python -m pytest \
    tests/test_audit_remediation.py tests/test_dairy.py tests/test_breeding_extended.py \
    tests/test_kidding* tests/test_logic.py -q
313 passed in 143.98s (0:02:23)
```

313 passed, 0 failed, 0 errors.

---

## Notes and caveats

- Probe file: `/tmp/v1_probes/test_v1_probes.py` (12 tests; 11 pass, the D-7 test fails by design —
  it asserts the claimed behavior and captures the defect's evidence).
- The goat-path D-1 probe required a hand-shaped postpartum duty (see §1) because the goat workflow
  cannot produce that state; provenance fields matched the workflow's exactly, so the guard's
  refusal demonstrates the species gate, not a forged-task artifact.
- G-1's message reports the kid's age in completed months ("2 months old" for a 90-day-old kid);
  the window and the 422 are correct.
- Scratch DBs: `v1_verify_test` (auto-dropped at session end) and `v1_alembic_test` (dropped after
  the migration check). No source or repo files were modified.
