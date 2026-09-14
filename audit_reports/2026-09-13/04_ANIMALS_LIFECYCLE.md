# Red Team Audit — Part C: Animal Lifecycle Domain (units C1–C7)

- **Date:** 2026-09-13
- **Target:** `backend/app/` (FastAPI + async SQLAlchemy + Postgres), Herdly goat-farm SaaS monorepo
- **Scope:** units C1–C7 per `audit_reports/2026-09-13/00_RED_TEAM_AUDIT_SCOPE.md` Part C
- **Primary files audited end-to-end:**
  `api/animals.py`, `api/buckets.py`, `api/_shared.py`, `services/animals.py`,
  `services/purchases.py`, `services/chronology.py`, `services/breeding.py` (cascade helpers),
  `services/kidding.py` (`replan_dam_after_last_kid_death`), `services/health.py`
  (`place_movement_restriction`), `services/idempotency.py` (`execute_idempotent`),
  `models/animals.py`, `models/lifecycle.py`, `models/constants.py`, `models/species.py`,
  `models/helpers.py`, `schemas/animals.py`, `schemas/common.py`, `schemas/summaries.py`,
  `permissions.py`, `api/team.py` (`_clean_permissions`), `api/purchases.py` (perm parity).
- **Intent corpus:** `tests/test_animals_bugs.py`, `test_animals_extended.py` (179 tests),
  `test_animal_profile_pagination.py`, `test_input_bounds.py`, `test_domain_chronology.py`,
  `test_e2e_lifecycle_audit.py`, `test_domain_check_constraints.py`, `test_audit_remediation.py`.

## Methodology

Each unit was attacked as an adversary would: horizontal (cross-farm) and vertical
(permission) authorization bypass, input abuse (types, bounds, control characters,
non-finite values, enum parity vs DB CHECKs), business-logic/state-machine abuse
(transition matrix completeness, gate reachability), cascade abuse (money/task writes
triggered from lower-privilege endpoints), replay/idempotency abuse, race/concurrency
(lock ordering, FOR UPDATE coverage, check-then-act windows), enumeration oracles
(uniform 404s), and resource exhaustion (pagination caps, unbounded sweeps). Every
hypothesis below was verified against implementation code (not tests alone); tests were
read to separate intended behavior from gaps. Only code-evidenced findings are reported;
unconfirmed items are marked explicitly.

Cross-cutting sweeps performed:

- **Raw SQL interpolation:** `grep` for `text(f"...")` / f-string SQL across `services/`
  and the audited routers — none found (only the static `text("CURRENT_DATE")` server
  default in `models/animals.py:495`).
- **Decimal vs float:** all money columns are `Numeric(14,2)` and every write passes
  through `utils.money()` (`Decimal(str(v)).quantize(0.01, ROUND_HALF_UP)`,
  `utils.py:13-18`); inputs are strict finite floats bounded `<= 1e9` with a
  round-to-at-least-₹0.01 guard (`schemas/common.py:83-88,138-147`). Weights are
  `float`/`Real` with finite+positive+`<=1000` schema bounds and a species cap of 150 kg
  enforced in the API layer (`models/species.py:87`). No precision or overflow hole found.
- **Pydantic ↔ DB CHECK parity:** every CHECK on `animals`/`weight_records`/
  `bucket_moves` (`models/animals.py:70-163,429-447,465-481`) is mirrored or tightened in
  `schemas/animals.py` + `schemas/common.py` (PostgresText rejects NUL/C0/surrogates;
  strict enums via Literals; dates double-checked farm-local). No path was found where
  pydantic accepts a value Postgres CHECKs reject (i.e. no input-driven 500).

## Findings summary

| ID | Severity | Unit | Title |
|----|----------|------|-------|
| RT-C-1 | **High** | C2 | `animals.create` alone triggers the full purchase cascade — PurchaseBatch + ANIMAL_PURCHASE expense (up to ₹1e9) + 8 quarantine tasks — bypassing `purchases.manage` and `finance.manage` |
| RT-C-2 | Medium | C6 | Meat-sale minimum-age fence is bucket-scoped: an unweaned male kid in RECOVERY can be SOLD at any age |
| RT-C-3 | Low | C4 | Owner `history_override` bypasses the guarded batch-task QUARANTINE→FOUNDATION release without the pristine-protocol check |
| RT-C-4 | Low | C2/C5 | Money-booking endpoints (`POST /api/animals` managed purchase, `/weight`) accept an *optional* Idempotency-Key; double-submit duplicates animals + expenses |
| RT-C-5 | Low | C4 | Pregnancy guard only protects `PREGNANCY_LATE`/`DELIVERY` targets; a pregnant doe can be manually parked in RESTING (post-override state) |
| RT-C-6 | Low | C2 | `tag_number` (and `name`) accept embedded tab/LF/CR — identifier hygiene |
| RT-C-7 | Info | C6 | `animals.status` holder places movement restrictions (no health perms) on suspected-disease death — documented as intended |
| RT-C-8 | Info | C7 | Per-farm feed overrides (`daily_kg_per_head`) exposed to every `buckets.view` holder |
| RT-C-9 | Info | C1 | `q` LIKE-escaping is correct but has no regression test |
| RT-C-10 | Info | C6 | Per-animal pending-task sweep capped at 500 rows/request; convergence relies on background sweeper (documented design) |

Counts: **0 Critical, 1 High, 1 Medium, 4 Low, 4 Info.**

---

## Detailed findings

### RT-C-1 (High, C2) — `animals.create` triggers finance/procurement/task writes without `purchases.manage` / `finance.manage`

**Evidence:**

- Gate: `backend/app/api/animals.py:293` — `require_perm("animals.create")` is the only
  permission on `POST /api/animals`.
- Cascade: `api/animals.py:361` (`managed_purchase = source == "PURCHASED" and not
  historical_import_reason`), `api/animals.py:459-473` calls
  `create_purchase_batch(...)`, `api/animals.py:534-535` calls
  `schedule_quarantine_tasks(...)` (8 tasks), and
  `services/purchases.py:205-222` unconditionally books the expense
  `Transaction(type=EXPENSE, category=ANIMAL_PURCHASE, amount=purchase_price or ₹0)`.
- The same writes through the dedicated endpoint require `purchases.manage`:
  `api/purchases.py:34,104-114` (`POST /api/purchases/new` → `PurchasesManage`), and
  manual ledger writes require `finance.manage` (`permissions.py:39`,
  `api/finance.py:586`).
- No RBAC coupling exists: `PERMISSION_DEPENDENCIES`
  (`permissions.py:52-64`) only forces `animals.create → animals.view`;
  `_clean_permissions` (`api/team.py:709-726`) enforces no ceiling that would pair
  `animals.create` with `purchases.manage`/`finance.manage`. Presets pair them
  (MANAGER `permissions.py:113-138`, BUYER `permissions.py:186-197`), but the owner can
  grant any subset via custom roles.
- Tests confirm the gap is unpinned: `test_animals_extended.py:488-540` provisions
  exactly this minimal custom role `{animals.view, animals.create}` and asserts only the
  owner-only historical-import gates — never what that worker can do with
  `source="PURCHASED"`.

**Exploit sketch:** Owner grants a "registrar" custom role `{animals.view,
animals.create}` (e.g. a data-entry worker who should only be able to add animals).
That worker loops `POST /api/animals {"source":"PURCHASED","purchase_price":999999999,
"seller_name":"x"}`: each request creates a PurchaseBatch row (visible to
`purchases.view` holders as real procurement), books an ANIMAL_PURCHASE expense of up to
₹1e9 into the ledger (P&L, 12-month summaries, dashboard), and spawns 8 quarantine
protocol tasks. The worker never needs `finance.manage` (the permission that exists
precisely to gate ledger writes) or `purchases.manage`.

**Impact:** Vertical privilege escalation across three permission boundaries within a
tenant: unauthorised finance writes (arbitrary-amount expense rows), procurement-record
forgery, and task/work-list pollution. No cross-tenant effect; requires an
owner-granted narrow custom role (or a manager-level grant scoped by `_guard_role_scope`
that includes `animals.create` — those ceilings also do not force the pairing).

**Fix:** either (a) gate the cascade: require `purchases.manage` when
`managed_purchase` is true (and let plain BORN/historical paths stay on
`animals.create`), or (b) book the expense/defer batch creation until a
`purchases.manage` holder confirms, or (c) document `animals.create` as
finance-writing and add a dependency rule `animals.create → purchases.manage` +
ledger-write ceiling. Add a regression test asserting a `{animals.view, animals.create}`
worker cannot create purchase batches/expenses.

### RT-C-2 (Medium, C6) — Meat-sale minimum-age fence is bucket-scoped

**Evidence:** `api/animals.py:1030-1045` — the SOLD meat-sale gate fires only when
`animal.current_bucket == Bucket.MALE_KIDS.value`. Male kids live in RECOVERY with the
dam until weaning (`services/kidding.py:219-220`, WEANING at +60d,
`models/species.py:78`). A male kid that is SOLD while still in RECOVERY is never
age-checked. The documented escape via `CULLED` (with or without price) is explicitly
endorsed — the 422 message itself says "record a cull instead"
(`api/animals.py:1040-1044`) and `test_audit_remediation.py:114-117` asserts cull stays
open.

**Exploit sketch:** Worker with `animals.status` records a kidding, then immediately
sells a 3-week-old male kid straight out of RECOVERY with `{"new_status":"SOLD",
"sale_price":5000}` — the exact "3-month/12 kg kid sold for ₹5,000" scenario the gate
was built for (`test_audit_remediation.py:85-88`) — but from the pre-weaning bucket it
succeeds and books ANIMAL_SALE income.

**Impact:** The SPEC's minimum meat-sale age (8–9 months,
`models/constants.py:42-43`) is enforceable only after weaning; the same worker who
cannot sell a 7-month MALE_KIDS buckling can sell a 1-month RECOVERY kid. Business-rule
inconsistency, not state corruption (the sale itself is otherwise valid and the income
books correctly).

**Fix:** key the gate on sex + age (+ optionally weight) rather than current bucket,
e.g. `animal.sex == "M" and animal.effective_dob is not None and age < MEAT_SALE_AGE_MONTHS[0]`
for SOLD regardless of bucket, or additionally cover `RECOVERY` for males under the
weaning age. Note in the report: selling pre-weaned breeding stock to another farm is a
legitimate practice — if intended, document it; otherwise close it.

### RT-C-3 (Low, C4) — Owner `history_override` skips the guarded quarantine-release fence

**Evidence:** `api/animals.py:774-783` — the 409 "Purchased quarantine animals must be
released through the guarded batch task" is explicitly conditioned on
`not payload.history_override`. With `history_override=true` (owner-only, verified at
`api/animals.py:769-770`), `bucket_transition_error` short-circuits at
`services/animals.py:68-69`, so a mid-protocol batch animal can be moved
QUARANTINE→FOUNDATION with no pristine check. The pristine-protocol guard
(`api/animals.py:147-227`) is only invoked for *re-entry* into QUARANTINE
(`api/animals.py:865-874`). Contrast the SOLD-in-quarantine biosecurity fence
(`api/animals.py:1017-1024`) which has no override at all.

**Exploit sketch:** Owner moves a day-10 batch animal to FOUNDATION via override; the
batch's 8 protocol duties remain pending (completable, and the day-45 release task now
releases only the remaining batch animals).

**Impact:** Owner-only (owner implies all permissions and the override is the
documented correction hatch — `test_e2e_lifecycle_audit.py:837-868` treats it as "the
sole escape"). Biosecurity sequencing can be subverted by the trusted principal; the
protocol tasks are not corrupted. Defense-in-depth.

**Fix:** require the pristine-protocol check for owner overrides out of QUARANTINE too,
or auto-skip/annotate the batch protocol tasks when an override releases a batch animal.

### RT-C-4 (Low, C2/C5) — Optional Idempotency-Key on money-booking writes

**Evidence:** `api/animals.py:295` and `api/animals.py:898` declare
`idempotency_key: IdempotencyKey = None` (optional) while the other money-booking
writes *require* it: `api/finance.py:586` and `api/feeding.py:144`
(`RequiredIdempotencyKey`). A `POST /api/animals` with `source=PURCHASED,
purchase_price=…` books an expense (`services/purchases.py:210-222`); a double-submit
with an auto-generated tag (blank `tag_number`) creates two animals, two batches, two
expenses. With an explicit tag the second submit 400s on the duplicate-tag check
(`api/animals.py:445-456`). `/weight` double-submit appends a duplicate WeightRecord
(same value; no gate impact).

**Impact:** Accidental duplicate financial rows from client retries; correctable via
finance correction (I3). Self-inflicted, within-permission.

**Fix:** make the Idempotency-Key required for `POST /api/animals` when the request
books money (managed purchase or priced historical import), mirroring finance/feeding.

### RT-C-5 (Low, C4) — Pregnancy guard does not cover the manual BREEDING→RESTING edge

**Evidence:** `api/animals.py:854-859` blocks moves into `PREGNANCY_LATE`/`DELIVERY`
without a live pregnancy, but the legal manual edge `(BREEDING, RESTING)` includes
`"manual"` (`models/lifecycle.py:55`), and the open-breeding check
(`api/animals.py:843-853`) only blocks does with `PENDING`/un-kidded
`CONFIRMED_PREGNANT` services — wait, it *does* catch the confirmed case for
BREEDING→RESTING (`doe_has_open_breeding`, `services/breeding.py:277-296`). The
residual gap is restricted does: `bucket_transition_error` blocks any move of a
restricted animal (`services/animals.py:46-49`), so the remaining reachable path is
post-`history_override` states where the owner returned a pregnant doe to BREEDING
(documented as legitimate, `models/lifecycle.py:53-55`) and then manually moves her on;
the pregnancy check at 854 does not run for RESTING. If reached, her later
`record_kidding` fails cleanly with 422 ("Illegal lifecycle transition RESTING →
RECOVERY" wrapped at `api/kidding.py:354-355`).

**Impact:** Requires deliberate owner override + owner manual move; worst case is a
stuck-but-clean 422 at kidding time and a lingering KIDDING_DUE task until the loss is
recorded. Defense-in-depth only.

**Fix:** extend the pregnancy check to any target outside
{PREGNANCY_EARLY, PREGNANCY_LATE, DELIVERY, RECOVERY} when
`computed.is_currently_pregnant`.

### RT-C-6 (Low, C2) — Tag/name accept embedded tab/LF/CR

**Evidence:** `schemas/common.py:99-101` whitelists `\t\n\r` inside `PostgresText`;
`AnimalCreateIn.tag_number` (`schemas/animals.py:40-42`) uses it with only a
strip() at the edges (`api/animals.py:435`). Tags like `"AB\tCD"` or `"AB\nCD"` are
stored verbatim (DB `String(50)` accepts them). Tags are the farm's identifier
namespace used in task titles, pickers and CSV-like surfaces.

**Impact:** Identifier hygiene only — visually confusable tags, broken line-oriented
exports/logs; uniqueness still enforced. (Unicode/emoji tags are explicitly tested as
supported, `test_animals_extended.py:780`.)

**Fix:** restrict `tag_number` (and generated-tag inputs) to a printable, no-control
charset (`\S` plus spaces excluded), keeping `\t\n\r` tolerance for narrative fields
only.

### RT-C-7 (Info, C6) — Death with suspected scheduled disease places a movement restriction with only `animals.status`

**Evidence:** `api/animals.py:1052-1061` calls `place_movement_restriction`
(`services/health.py:184-214`) — incrementing `restriction_version`, setting the holds
and writing a `MovementRestrictionAction` — inside the `animals.status` endpoint with no
health permission check. README (`README.md:777-780`) documents death-with-suspicion as
the restriction path, and the scope brief flags it as intended. Noted for the record;
the audit found no bypass beyond the documented design (clearing still requires the
health flow).

### RT-C-8 (Info, C7) — Feed overrides visible on the buckets board

**Evidence:** `api/buckets.py:122-136` merges `BucketFeedSetting` overrides into
`daily_kg_per_head` for every `buckets.view` caller. Default rates are public reference
data; the override is farm-specific cost configuration normally written under
`feeding.manage`. Minor information disclosure to e.g. MOVER/CLEANER-adjacent roles
holding only `buckets.view`. (MOVER holds `buckets.view` without feeding perms —
`permissions.py:141-152`.)

### RT-C-9 (Info, C1) — `q` LIKE-escaping implemented correctly, untested

**Evidence:** `api/animals.py:254-264` escapes `\`, `%`, `_` in order and passes
`escape="\\"` to `ilike` — correct. The only wildcard-escape regression tests cover the
breeding pickers (`tests/test_scoped_picker_lookups.py:171-182`), not
`GET /api/animals?q=`. Test-coverage gap, not a vulnerability.

### RT-C-10 (Info, C6) — Bounded task sweep + background convergence

**Evidence:** `skip_pending_tasks_for_animal` defaults to `batch_size=500`
(`api/animals.py:1198`, `services/animals.py:216-244`); an animal holding more pending
tasks converges via `skip_inactive_animal_tasks_batch` (`services/animals.py:321-343`)
and read paths hide stale duties. Documented design bounding request work; the
batch-duty sweep (`services/animals.py:247-318`) is bounded at 8 protocol rows in
practice. No unbounded request path found.

---

## Per-unit attacked-and-held notes

### C1 — Herd register (`GET /api/animals`)

- **Farm scoping:** every branch filters `Animal.farm_id == farm.id`
  (`api/animals.py:243`); cross-farm tag search impossible; test-pinned
  (`test_animals_extended.py:302-332`). **Held.**
- **LIKE escaping:** backslash-first, then `%`/`_`, explicit `ESCAPE '\'`
  (`api/animals.py:257-263`). **Held** (test gap noted, RT-C-9).
- **NUL/control chars in `q`:** `PostgresText` rejects C0 controls and unpaired
  surrogates pre-asyncpg (`schemas/common.py:91-113`); max 60 chars. **Held.**
- **Limit/offset:** `limit` 1–200, `offset` 0–1,000,000, `ge/le` → 422
  (`api/animals.py:240-241`, `MAX_PAGE_OFFSET` `schemas/common.py:23`). No 0/-1/10^9
  path. **Held.**
- **Enum validation:** `bucket`/`sex`/`status` are Literals → 422, never a DB error
  (`schemas/animals.py:21-36`). **Held.**
- **`include_all_statuses` vs `status`:** explicit `status` wins; default ACTIVE-only is
  opt-in (`api/animals.py:248-253`). **Held.**
- **Ordering determinism:** `(current_bucket, tag_number)`; tags unique per farm
  (`uq_animal_tag_per_farm`, `models/animals.py:53`) → stable pagination. **Held.**
- **Case sensitivity:** `ilike` — intended case-insensitive tag match (test-pinned
  `test_animals_extended.py:1371`). **Held (intended).**

### C2 — Animal creation (`POST /api/animals`)

- **Three sources:** BORN requires `historical_import_reason` (schema,
  `schemas/animals.py:87-90`); any historical import is owner-only server-side
  (`api/animals.py:356-360`, 403) — a `{animals.view, animals.create}` worker cannot
  forge BORN/workflow-state rows (test `test_animals_extended.py:488-540`). Managed
  PURCHASED is forced into a one-head quarantine batch ignoring the client's bucket
  (`api/animals.py:361-362`, test :627-648). **Held except RT-C-1/RT-C-4.**
- **Purchase cascade permission asymmetry:** RT-C-1 (High).
- **Weight caps:** birth weight 0.5–8 kg and entry weight ≤150 kg enforced server-side
  with clean 422s (`api/animals.py:413-434`); schema bounds (≤1000, finite, positive)
  are tighter than DB CHECKs — no 500 path (`tests/test_input_bounds.py:480-514`). **Held.**
- **Types:** strict floats/ints/dates (`StrictInputModel`, `FiniteFloat`,
  `PastOrTodayDate` + farm-local `require_farm_not_future` re-check
  `api/animals.py:299-306`); NaN/1e309/negative all 422 at the schema
  (`tests/test_animals_extended.py:2197-2218` analog). **Held.**
- **Duplicate tag race:** pre-check inside a savepoint + `IntegrityError` on
  `uq_animal_tag_per_farm`/`uq_stillborn_tag_farm_namespace` → one bounded retry
  (auto-tag) or 400 (explicit tag) (`api/animals.py:437-576`); farm-scoped advisory
  trigger namespace handled. Two concurrent identical explicit tags → one 201, one 400.
  **Held.**
- **Dam/sire FKs:** `AnimalCreateIn` has no dam/sire fields at all — no cross-farm
  parent reference is possible from this endpoint. **Held.**
- **Historical-import bucket restrictions:** PREGNANCY_*/DELIVERY/RECOVERY rejected;
  BREEDING requires species age+weight minimums (`api/animals.py:363-407`). **Held.**
- **Chronology:** purchase/weight dates cannot predate DOB or each other, cannot be
  farm-future; priced imports require a purchase date (`api/animals.py:307-342`). **Held.**
- **Sex-bucket CHECK mirror:** schema validator = `ck_animals_bucket_sex`
  (`schemas/animals.py:93-103` vs `models/animals.py:116-121`) → 422, not 500. **Held.**
- **Length caps:** tag 50, name 80, breed 60<80, seller 120, notes 4000(Text) — all
  ≤ column limits. **Held.**

### C3 — Animal profile (`GET /api/animals/{id}`)

- **Uniform 404:** `_get_animal` farm-scopes and int4-guards ids
  (`api/animals.py:97-122`); cross-farm/huge/zero ids → 404 (tests :302, :1643). **Held.**
- **Pagination caps:** `history_limit` 1–100; five per-section offsets ≤1,000,000 → 422
  beyond (`api/animals.py:599-606`). **Held.**
- **Server-side withholding (not just flags):** health events and breeding ids are only
  queried under `health.view`/`breeding.view`; their totals degrade to `literal(0)`
  (`api/animals.py:630-694`); weight notes and move reasons are nulled without
  `health.view` (`api/animals.py:711-715,736`); `animal_out` redacts
  prices/supplier/restriction/notes per permission family
  (`api/_shared.py:211-249`; test `test_animals_extended.py:2903-3159`). **Held.**
- **Kids list exposure:** kid identity/lifecycle fields only — a subset of what
  `animals.view` already exposes via the register; ids are not secrets. **Held.**

### C4 — Bucket moves (`POST /{id}/move`)

- **Matrix completeness:** enumerated `LEGAL_BUCKET_TRANSITIONS`
  (`models/lifecycle.py:34-63`) — no edge into QUARANTINE except override (re-entry
  guarded by the pristine-protocol check), no DELIVERY→BREEDING, no
  MALE_KIDS→QUARANTINE, no backwards PREGNANCY_LATE→PREGNANCY_EARLY, male kids exit
  only to BREEDING (documented buck-rotation consequence, e2e test :837-868). The whole
  matrix is API-pinned by `test_e2e_lifecycle_audit.py:784-837`. No nonsense edge found.
  **Held.**
- **Enum/case/whitespace:** `to_bucket` is a Literal — exact match or 422. **Held.**
- **`history_override`:** worker → 403 (`api/animals.py:769-770`); reserved
  `[HISTORY OVERRIDE]` reason prefix cannot be forged by clients
  (`schemas/animals.py:182-200`). **Held** (RT-C-3 owner-scope note).
- **Cross-farm animal:** 404 via `_get_animal(for_update=True)`. **Held.**
- **Terminal animals:** first factual check in `bucket_transition_error`
  (`services/animals.py:44-45`) → 409 for SOLD/DEAD/CULLED. **Held.**
- **QUARANTINE→FOUNDATION guard:** enforced for batch animals; manual release exists
  only for batch-less imports (`api/animals.py:774-783`; rationale
  `models/lifecycle.py:37-41`). Override bypass = RT-C-3 (Low).
- **Restricted/pregnant animals:** any hold blocks all manual moves
  (`services/animals.py:46-49`); pregnant animals can only legally sit in
  PREGNANCY_*/DELIVERY workflow buckets, and entering PREGNANCY_LATE/DELIVERY requires
  a live pregnancy (`api/animals.py:854-859`); leaving BREEDING for RESTING checks open
  breedings (`api/animals.py:843-853`). Residual: RT-C-5 (Low).
- **Move-date backdating:** the endpoint accepts no date — `effective_date` is always
  farm-today (`api/animals.py:875-884`); backdated moves exist only inside domain
  workflows which carry their own chronology. **Held.**
- **Concurrency:** animal row locked `FOR UPDATE` before state checks; conflicting
  concurrent moves serialize to one winner (test
  `test_e2e_lifecycle_audit.py:1059-1073`). `move_animal` re-validates
  deterministically (same inputs, unchanged state) — no divergent second raise. **Held.**
- **Same-bucket move:** validated no-op, writes nothing (`services/animals.py:163-164`).
  **Held.**
- **BREEDING gates re-checked on move:** yes, with SQL-derived facts (latest dated
  weight, live pregnancy) under the row lock (`api/animals.py:765-768,830-842`;
  `services/animals.py:77-117`). Months-vs-days confusion not present (age in whole
  months, weights in kg with 150-kg cap blocking gram-scale nonsense). **Held.**

### C5 — Weight recording (`POST /{id}/weight`)

- **ACTIVE-only:** 400 for non-ACTIVE (`api/animals.py:902-906`). **Held.**
- **Bounds:** positive, finite, ≤150 kg species cap → 422 (`schemas/common.py:153`,
  `api/animals.py:911-919`); BCS strict int 1–5 (fractional 422) matching the DB CHECK
  (`models/animals.py:439-442`). **Held.**
- **Chronology:** not farm-future, not before birth/purchase
  (`api/animals.py:920-925`, `services/chronology.py:20-33`); duplicate dates allowed
  by design (append-only measurements; latest-by-(date,id) semantics). **Held.**
- **Cross-farm animal:** 404. **Held.**
- **Idempotency:** key optional — RT-C-4 (Low). Row locked `FOR UPDATE` before the
  ACTIVE check, so no status race.

### C6 — Status-change cascade (`POST /{id}/status`)

- **Terminal replay / double-booking (critical hypothesis):** ACTIVE-only transition
  under `FOR UPDATE` (`api/animals.py:962-970`) — replayed/duplicated SOLD returns 400
  and cannot book a second ANIMAL_SALE (test `test_animals_extended.py:2509-2535`).
  Concurrent status+move serialize on the same animal lock. **Held.**
- **Movement restriction AND withdrawal, on current data:** both checks run under the
  animal row lock (`api/animals.py:971-980, 997-1011`); health-event writers take the
  same lock before inserting events (`api/health.py:338`), so a concurrent withdrawal
  placement cannot slip between check and commit. **Held.**
- **SOLD in QUARANTINE:** blocked (`api/animals.py:1012-1024`); CULLED intentionally
  open (documented disease response; test `test_audit_remediation.py:122+`). **Held.**
- **Meat-sale minimum age:** males in MALE_KIDS gated; RECOVERY gap = RT-C-2 (Medium);
  female kids and culls intentionally ungated. **Partially held.**
- **DEAD + suspected scheduled disease:** restriction placed, version incremented,
  episode audited; requires only `animals.status` — RT-C-7 (Info, documented). Disease
  whitespace smuggling is schema-blocked (`str_strip_whitespace` +
  coherence validator, `schemas/animals.py:229,260-261`; test :2439-2471). **Held.**
- **Open breedings resolved:** PENDING→UNASSESSED (with task skips) and
  CONFIRMED_PREGNANT→ABORTED with `allow_late_administrative_close`, locked breeding
  rows, synthetic same-bucket BucketMove for the audit trail
  (`api/animals.py:1085-1151`; `services/breeding.py:748-895`). KIDDING_DUE/ULTRASOUND/
  move-to-DELIVERY duties are all `breeding_record_id`-linked and skipped inside
  `mark_aborted`/`mark_unassessed`; WEANING tasks only exist post-kidding, so none can
  survive. Chronology violations roll back to a clean 422 (`api/animals.py:1129-1134`).
  **Held.**
- **Orphan kids:** RECOVERY kids of the retiring dam (never genuinely weaned — override
  moves excluded) are early-weaned by sex with `context="weaning"` under kid row locks;
  restricted kids are silently left for the post-clearance `orphan_weaning` path
  (`api/animals.py:1159-1196`; provenance fence `api/animals.py:784-829`; e2e tests
  :566, :754, :3404). **Held.**
- **Task sweep:** per-animal (bounded 500, RT-C-10) + empty-batch protocol sweep after
  an explicit flush, batch-row-serialized (`api/animals.py:1198-1206`;
  `services/animals.py:247-318`). Recurring successors spawn on completion, not on
  skip (`services/tasks.py:574`, :644+), so no future-spawned duty survives. **Held.**
- **SALE_CAPABLE booking:** ₹0 floor with plain-text flag, negative prices 422
  (NonNegativeMoneyFloat), CULLED-with-price books monetized-cull income
  (`api/animals.py:1208-1233`; tests :2320-2425). `amount` CHECK-bounded 0–1e9
  (`models/finance.py:32-35`). **Held.**
- **Backdating:** status date ≤ farm today and ≥ every recorded fact (weight, health,
  breeding boundaries, kidding, real bucket moves) via
  `require_status_after_recorded_facts` (`services/chronology.py:36-119`); mortality and
  authority-notification sub-dates individually fenced
  (`api/animals.py:986-994`). **Held.**
- **Cross-farm:** 404. Lock order dam/kidding mutex races handled (`55P03` → retryable
  409, `api/animals.py:1062-1071`). **Held.**

### C7 — Buckets board (`GET /api/buckets`)

- **Farm scoping:** occupancy window and feed overrides both `farm_id`-filtered
  (`api/buckets.py:60,123`). Cross-farm isolation test-pinned (:2807). **Held.**
- **Preview:** ≤100 per bucket by `(tag_number, id)`; exact per-bucket totals via
  window count; only preview rows probe weight/move history (lateral joins) — no N+1,
  one statement for animals + one for settings (`api/buckets.py:43-102`). **Held.**
- **`animals_page_path`:** enum-constrained code interpolated into a fixed template
  (`api/buckets.py:143`; `ck_bucket_definitions_code` `models/animals.py:512-515`) —
  no injection. **Held.**
- **Feed overrides exposure:** RT-C-8 (Info).

## Unconfirmed / not reproducible from code alone

- **Deadlock potential between the status-change orphan sweep (locks dam → breedings →
  kid rows) and concurrent kid-side writes** — lock orders appear canonical
  (animal → breeding → task) and `test_lock_order_harding.py` exists, but full
  interleaving coverage was not exhaustively re-proven in this audit; flagged for the
  concurrency-focused pass rather than reported as a finding.
- **`generate_unique_tag` exhaustion** (10 attempts over a 31^5 space) raising
  `RuntimeError` → 500: practically unreachable; noted as dead-code hygiene only.

## Severity rationale recap

High (RT-C-1) matches the campaign definition "permission asymmetry enabling
finance/task writes". No cross-tenant access, authz bypass outside the permission
model, or double-money-booking path was found — the terminal-status replay guard and
row-lock serialization hold. Medium (RT-C-2) is a business-rule fence gap reachable by
an ordinary permission holder, not state corruption.
