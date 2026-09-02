# GOAT FARM ADVERSARIAL AUDIT — RED-TEAM AUDITOR A5

**Date:** 2026-09-01
**Repo:** `/Users/pavankumarreddyreddem/Desktop/goat_saas` (FastAPI `backend/app`, Next.js `frontend/src`)
**Method:** full read of the goat lifecycle services/APIs (animals, breeding, kidding, tasks, health, purchases, finance, idempotency), guard mapping, then live execution of 30+ exploit sequences against a throwaway Postgres DB (`goatfarm_audit5_test`) through the real ASGI app (probe: `/tmp/audit5_probe.py`, runs in `/tmp/audit5_run6.log`), plus code-path citation for everything not executable headless. Regression subset re-run clean: `pytest tests -k "breeding or kidding or bucket or purchase or sale" -q` → **823 passed**.

Severity scale: BLOCKER (direct money loss/tenant breach) / HIGH (integrity corruption reachable by a worker) / MEDIUM (spec business rule absent, operational/biosecurity risk) / LOW (hardening/quality).

---

## FINDINGS

### M-1 (MEDIUM) — Buck:doe ratio (1:20) and buck rotation (7d) are advertised but never enforced

* **Where:**
  * Constants defined and exported, never used by any guard: `backend/app/models/constants.py:34-35` (`BUCK_ROTATION_DAYS = 7`, `BUCK_DOE_RATIO = 20`); only reference outside `models/__init__.py` is a value assertion in `backend/tests/test_unit_extended.py:422-423`.
  * Missing enforcement point: `backend/app/services/breeding.py:379-458` (`create_breeding_record`) and `backend/app/api/breeding.py:213-334` (`create_breeding`) — the sire is validated for age/weight/health/bucket only (`is_buck_breeding_candidate`, `services/breeding.py:245-261`); no query ever counts the buck's open services.
  * The rule is advertised to users in-product: `backend/app/seed.py:76` — BREEDING bucket description *"Does ready to conceive; 1 buck per 20 does (rotate 7d on/off)"*.
* **Exploit (executed):** one mature buck + 21 eligible does; POST `/api/breeding` for the same `buck_id` 21 times on the same date. **All 21 accepted (201)**. Nothing stops 200 the same way; no warning, no cap, no rotation concept anywhere.
* **Impact:** genetic concentration/inbreeding, overworked sire, and spec-noncompliant records feeding the simulation/calibration and reports (`simulation/assumptions.py:232-233` models `buck_doe_ratio` — the planner trusts a constraint the operational write path does not have). No direct money path.
* **Reproduction:** `POST /api/breeding {"doe_id":N,"buck_id":B,"breeding_date":D,"method":"NATURAL"}` ×21 (needs `breeding.manage`, e.g. Farm Manager or Vet role).
* **Fix:** in `create_breeding_record`, count the buck's open breedings (`outcome = PENDING` or `CONFIRMED_PREGNANT` without kidding, `buck_id = B`, within a rotation window) and reject beyond `BUCK_DOE_RATIO`; expose current buck load in `GET /api/breeding/candidates?kind=buck` so the UI can warn. Consider `BUCK_ROTATION_DAYS` as a rest-day advisory on the same counter.

### M-2 (MEDIUM) — Meat-sale window (8–9 months / 24–28 kg) is advisory only on the sale write path

* **Where:**
  * Constants: `backend/app/models/constants.py:36-37` (`MEAT_SALE_AGE_MONTHS = (8, 9)`, `MEAT_SALE_WEIGHT_KG = (24.0, 28.0)`) — never referenced by a guard (only `models/__init__.py` exports).
  * Advisory display: `backend/app/services/dashboard.py:139,178-185` (`market_rule`: male, MALE_KIDS, ≥8 mo, ≥24 kg, no withdrawal → "market ready" *suggestion*).
  * Unchecked write path: `backend/app/api/animals.py:941-1184` (`change_status`) — the only gates are ACTIVE status, movement/disease hold, chronology, and withdrawal. No age/weight/bucket check for SOLD/CULLED.
* **Exploit (executed):** 3-month-old, 12 kg male kid sold for ₹5,000 → **200 OK**, income booked. Verified in probe run (`SOLD 3mo/12kg kid @ Rs.5000: 200`).
* **Impact:** any worker holding `animals.status` (Farm Manager preset, `permissions.py:132-160`) can liquidate a kid, foundation doe, or pregnant doe (auto-abort fires) at any price at any age — breeding-program loss and ledger entries the SPEC's window was meant to bound. Withdrawal/hold checks still apply, so this is a business-rule gap, not a safety bypass.
* **Fix:** at minimum return a warning/confirmation-required response for sales outside `MEAT_SALE_AGE_MONTHS`/`MEAT_SALE_WEIGHT_KG`; optionally block non-owner sales of animals under the minimum age (8 mo) outright, mirroring the breeding-floor style (`require_farm_not_future`-style dedicated error).

### L-1 (LOW) — Animals can be sold/culled straight out of the 45-day QUARANTINE protocol

* **Where:** `backend/app/api/animals.py:959-999` (sale/cull checks) has no bucket/quarantine gate; the quarantine fence exists only for bucket moves (`api/animals.py:773-782` refuses manual `QUARANTINE → FOUNDATION` for batch animals) and for protocol skips (`api/tasks.py:640-656`).
* **Exploit (executed):** created a managed purchase (`source=PURCHASED` → forced QUARANTINE + protocol tasks), sold it on day 0 of 45 → **200 OK**, income booked, batch duties swept by `skip_pending_tasks_for_empty_batch`.
* **Note on intent:** `backend/tests/test_animals_extended.py:1234-1237` implicitly blesses this (a filter-test fixture marks a QUARANTINE animal SOLD with price 5000 and asserts 200), so it appears accepted behavior (e.g. rejecting an animal back to a seller). Reporting as LOW because the biosecurity intent of quarantine ("don't move possibly-incubating stock") is not extended to herd exit, and no reason/owner-approval is required.
* **Fix:** decide explicitly — either block `SOLD/CULLED` while `current_bucket = QUARANTINE` and `purchase_batch_id` protocol tasks are still open (owner override via a reason), or require an owner-scoped reason for such sales.

### L-2 (LOW) — Manual ledger rows can impersonate system-generated `ANIMAL_SALE` (and other system) categories

* **Where:** `backend/app/api/finance.py:591-643` (`add_transaction`) + `backend/app/schemas/finance.py:114-118` — `TransactionIn.category` accepts the full `TransactionCategoryStr` vocabulary with no animal link required. Only `MILK` is fenced (dairy-only + provenance cross-check, `api/finance.py:54-66`).
* **Exploit (executed):** `POST /api/finance/new {"type":"INCOME","category":"ANIMAL_SALE","amount":99999.0}` (no `related_animal_id`) → **201 Created**.
* **Impact:** fabricated "animal-sale revenue" indistinguishable from genuine per-animal sale income in category-grouped P&L/reports (genuine rows carry `source_type="ANIMAL_SALE"`, `source_id=<animal>`; manual rows have neither). Requires `finance.manage` (Accountant/Owner), fully attributed — integrity/audit noise, not theft.
* **Fix:** restrict manual-row categories to user-domain categories (exclude at least `ANIMAL_SALE`, `ANIMAL_PURCHASE`, `MILK`), or tag manual rows of system categories as `OTHER`-style display.

### L-3 (LOW) — Coverage gaps: no adversarial tests exist for the unenforced SPEC rules

* `BUCK_ROTATION_DAYS`/`BUCK_DOE_RATIO`/`MEAT_SALE_*` have zero behavioral tests (only verbatim value assertions, `backend/tests/test_unit_extended.py:422-423`); nothing covers quarantine-exit sales as a *policy* (only as an incidental fixture). When M-1/M-2/L-1 are fixed, add route-level tests in `backend/tests/test_adversarial.py` alongside the existing cull/withdrawal cases.

### Notes — owner-scope behaviors examined and NOT counted as vulns

* **history_override** (`backend/app/api/animals.py:768-772`, `services/animals.py:112-113`): owner-only; lets the owner place an ACTIVE animal in any sex-compatible bucket **including PREGNANCY_LATE without a live pregnancy** (probe: 200) because the pregnancy-guard at `api/animals.py:850-855` is skipped for overrides. Audited via the mandatory `"[HISTORY OVERRIDE] "` prefix; spoofing that prefix through a plain move is rejected (probe: 422); movement/disease holds and non-ACTIVE status still block even the owner (`services/animals.py:88-93`, confirmed dead-animal override path 404/409). Resurrection round-trips are impossible: no endpoint ever returns an animal to ACTIVE.
* Selling a pregnant doe auto-closes the pregnancy (`ABORTED`/`UNASSESSED`, `api/animals.py:1039-1105`) with an immutable audit trail — by design.
* Ultrasound `kid_count_detected` (1–3) vs delivered litter (≤10) has no consistency check — scans legitimately under-count; data-quality only.

---

## DEFENSES THAT HELD (attacked and confirmed solid)

All of the following were actively attempted (probe runs 2–6 in `/tmp/audit5_run*.log`) or traced to a DB constraint + row lock + existing test:

1. **Terminal-state fencing:** DEAD/SOLD/CULLED animals cannot be re-sold (`400 already …`), moved buckets (`409`), weighed (`400`), bred (`400`), kidded (`409`), health-evented (`400`), or used by tasks (`409`), and their pending duties are swept.
2. **Cross-farm IDOR:** breeding participants (`api/breeding.py:230-249` — probe: 404 both directions), tasks (`api/tasks.py:106-124`), finance animal links (`api/finance.py:76-91`), kidding pregnancies (`api/kidding.py:156-191`), purchases, health targets — everything resolves through `farm_id`-scoped lookups; composite FKs (`fk_breeding_records_farm_doe/buck`, `fk_kidding_records_farm_*`, `fk_animals_farm_dam/sire`) make cross-tenant lineage impossible at the DB level.
3. **Double-spend races:** concurrent double sale → one income row (probe: `200/400`, 1 `ANIMAL_SALE` row) via `FOR UPDATE` + ACTIVE re-check; concurrent double breeding → one open record (`uq_breeding_open_pregnancy` + doe lock); double kidding on one pregnancy → 409 (`uq_kidding_breeding_record`); concurrent ultrasound/abort/kidding serialize on animal→breeding lock order (`_lock_doe_then_breeding_record`); covered by `tests/test_concurrency.py:225-1270`.
4. **Breeding eligibility as-of-the-service-date:** doe ≥10 mo/≥22 kg and buck ≥12 mo/≥25 kg evaluated with weight *as of `breeding_date`* (`_latest_weight_as_of`, `breeding_weights_as_of`); my forged-eligibility attempts (backdated 30 kg weight; sire under 12 mo at the backdated date) were correctly rejected — the floors use historical facts, not today's scale reading. Overlapping pregnancies impossible (`doe_has_open_breeding` + `_latest_doe_reproductive_boundary`); re-breeding a pregnant doe → 400.
5. **Kidding integrity:** requires ultrasound-confirmed pregnancy + ACTIVE doe; gestation band 100–200 d enforced (probe: 95-day kidding → 409); kidding cannot predate confirmation; litter capped at 10 (schema) with MULTIPLET ≥5; duplicate kid tags rejected across the shared animal/stillborn namespace with deadlock-safe advisory lock; stillborn/DIED accounting consistent (`ck_kid_entries_mortality_matches_status`).
6. **Withdrawal windows:** sale AND cull blocked while any `withdrawal_until ≥ status_date`; backdating the sale is fenced by `require_status_after_recorded_facts` (health-event dates are recorded facts — probe: backdated sale → 422); health events are immutable (no edit endpoint) and withdrawal is capped at 730 d; the finance sale-date correction path re-checks both chronology and withdrawal (`api/finance.py:359-377`).
7. **Money ledger:** amounts finite, non-negative, paise-exact, ≤ ₹1e9 (probe: negative → 422, 1e30 → 422); `allocate_money` sums exactly (no rounding drift); manual rows must be > 0 (`MoneyFloat`); corrections are atomic void+replacement that must keep source type/category, re-sync `animal.sale_price`/`purchase_price`/batch totals, refuse to move dates anchored to auto-aborts/orphan weans, and block amount/date changes on allocated `PURCHASE_BATCH` rows; mortality+sale double-credit impossible (status is one-way); purchase batches capped at 1000 head with per-head paise allocation.
8. **Task two-person rule & duty integrity:** self-verification blocked (`api/tasks.py:700-701`, owner exempt by design; covered by `test_adversarial.py:1158`); form-linked duties refuse bare completion (`task_action_url` + `api/tasks.py:585-586`); auto-generated duties cannot be completed before due date; batch protocol duties cannot be skipped while any batch animal is active; RECOVERY duties (weaning/postpartum) cannot be skipped — they are the only legal exit; manual duty creation rejects system workflow categories and cross-farm animals/workers/roles; quarantine release requires every prerequisite DONE/VERIFIED, no holds, and a pristine protocol series for re-entry.
9. **Weaning/RECOVERY closure:** kids leave RECOVERY only via the generated WEANING duty matched to the exact kidding milestone and litter, or the dam-terminal orphan path with KidEntry provenance; history overrides deliberately do not count as weaning in all four dependant predicates; final-kid-death replanning is lock-serialized; 14-day postpartum recovery deterministic.
10. **Idempotency binding:** keys are scoped `(farm_id, actor_id, operation, key_digest)` with a request-hash guard (`services/idempotency.py:211-233, 299-357`) — no replay across users, farms, endpoints, or mutated payloads (409 on mismatch).
11. **Tenant-safe aggregates:** dashboard (`services/dashboard.py:134`) and simulation herd snapshot (`api/simulation.py:701-704`) count only `status = ACTIVE`; sold/dead animals never inflate head counts or feed plans (feeding service likewise filters ACTIVE, `services/feeding.py:399`).
12. **Species fencing:** MILK income/records rejected on goat farms (`api/finance.py:54-66`, `api/milk.py:38`).
13. **Frontend farm-switch race:** `selectFarm` clears the entire react-query cache (`frontend/src/lib/auth-context.tsx:162`), so pickers (`breeding-candidate-picker.tsx`, `animal-picker.tsx` via `RemotePicker`) cannot surface another farm's animals after a switch; any stale in-flight submission is still rejected server-side (404, probe P5). Covered by `frontend/src/test/adversarial/adv-A-concurrency.test.tsx` (ADV A2) and `adv-E-state.test.tsx` (ADV E1/E3).
14. **Deeplinks:** `/kidding/new?breeding_id=…` and `/breeding/{id}/ultrasound` resolve through farm-scoped endpoints that 404 foreign/finished pregnancies; task action URLs route only to forms the assignee is permission-checked for.
15. **Cull policy:** `cull_candidate` set after 2 consecutive FAILED cycles (`_update_cull_candidate`), cleared on conception or herd exit — advisory per SPEC, consistently implemented.
16. **Input hardening:** `StrictInputModel(extra="forbid")`, strict bool/int/float, non-finite/NaN rejection, control-character and surrogate rejection, `MAX_INT32_ID` guards (404 not 500), bounded pagination/limits everywhere.

---

## Test-coverage notes

* Already covered (do not re-report): resell sold animal, ultrasound replay, kidding on non-confirmed pregnancy, duplicate kid tags, gestation window, self-verification, sale-price NaN/negative, death sweeps, concurrent double sale/breeding/ultrasound/abort/kidding, tag races, farm-switch cache fencing.
* Gaps: no tests for buck ratio/rotation enforcement (M-1), meat-sale window (M-2), quarantine-exit sale policy (L-1), manual system-category ledger rows (L-2).

## Audit-infra note

The first `pytest -k …` run here showed ~470 failures; that was **environmental** — a stale `goatfarm_test` database left by an interrupted session caused cross-session truncation interference (symptom: mid-test `401 Account no longer exists`). After dropping the stale DB the same subset runs green: **823 passed, 3018 deselected**. All my DB work used throwaway `goatfarm_audit5_*` databases; repo sources were only read.
