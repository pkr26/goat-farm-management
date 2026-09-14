# Red Team Audit — Part H (Feeding H1–H7), Part I (Finance I1–I3), Part J (Purchases J1–J3)

Date: 2026-09-13 · Auditor unit: 07 · Target: `backend/app/` (FastAPI + async SQLAlchemy + Postgres)

## Methodology

Every unit was attacked as specified: authorization bypass (horizontal/vertical), tenant
isolation, input abuse (negative/zero/huge/sub-precision quantities and money, NUL/control
bytes, non-finite floats), business-logic/cascade abuse (recipe/stock bypass, void-and-replace
integrity), race/concurrency (lock ordering, deadlock cycles, lost updates on stock/money
rows), replay/idempotency abuse, enumeration oracles, and resource abuse. All primary files
were read end-to-end:

- `backend/app/api/feeding.py`, `api/finance.py`, `api/purchases.py`
- `backend/app/services/feeding.py`, `services/finance.py`, `services/purchases.py`
- `backend/app/models/feeding.py`, `models/finance.py`, `models/purchases.py`, `models/helpers.py`,
  `models/feed_rules.py`, `models/constants.py`
- `backend/app/schemas/feeding.py`, `schemas/finance.py`, `schemas/purchases.py`, `schemas/common.py`
- Supporting: `services/idempotency.py`, `services/chronology.py`, `utils.py`,
  `permissions.py`, `api/_shared.py` (`visible_to`), `schemas/summaries.py`,
  `api/animals.py` status-change sale booking (lock-order analysis),
  `alembic/versions/f2c3d4e5f6a7_feed_quantity_numeric.py`
- Tests scanned for pinned regressions: `test_feeding_extended.py` (161),
  `test_finance_extended.py` (162), `test_finance_bugs.py` (14),
  `test_feed_quantity_numeric.py` (3), `test_cull_price_calibration.py` (3).

Sibling findings referenced, not re-derived: **RT-M-1** (optional-only Idempotency-Key on
`/feeding/mix`, `/feeding/inventory/add`, `/purchases/new`, `/auth/farms` —
`03_MIDDLEWARE_RATELIMIT_IDEMPOTENCY.md`) and **RT-C-1** (`animals.create` triggers the
purchase cascade without `purchases.manage`/`finance.manage` — `04_ANIMALS_LIFECYCLE.md`).

Severity scale: Critical = cross-tenant / authz bypass / negative-money / double-booking;
High = stock/money corruption, deadlock/lost-update, underflow, permission asymmetry
enabling cross-domain writes; Medium = defense-in-depth defeat, info disclosure; Low =
hardening; Info = observations.

## Findings table

| ID | Severity | Unit | Title |
|----|----------|------|-------|
| RT-HIJ-1 | **High** | J2 | `purchases.manage` cascade creates full Animal rows (up to 1,000/batch) + moves/weights/tasks + ₹1e9 expense without `animals.create` — exact mirror of RT-C-1 |
| RT-HIJ-2 | **Medium** | I3 | Correction path bypasses `SYSTEM_ONLY_CATEGORIES`: a manual ledger row can be re-booked as `ANIMAL_SALE`/`ANIMAL_PURCHASE` (type-flipped, animal-linked), defeating the declared manual-entry invariant |
| RT-HIJ-3 | **Low** | J2/G1 | Supplier free-text embedded in quarantine task titles, readable on the task board by workers without `purchases.view` |
| RT-HIJ-4 | **Low** | H3/H4 | Dispense records accept arbitrarily backdated dates (no lower bound / chronology check) — audit-trail pollution only, stock debited from current balance |
| RT-HIJ-5 | **Low** | H2 | Per-head ration setting capped only by the generic 1e6 kg quantity ceiling; plan totals × heads produce absurd (display-only) numbers |
| RT-HIJ-6 | **Low** | J2 | Catch-all `except IntegrityError` reports every integrity failure during batch creation as a "tag collision" 409 |
| RT-HIJ-7 | Info | I1 | Garbage `month` filter silently matches nothing (`WHERE false`) instead of 422 (deliberate v1 compatibility) |
| RT-HIJ-8 | Info | I1 | `monthly_pnl` rounds with Python `round()` (HALF_EVEN) vs `money()` ROUND_HALF_UP — no-op today (sums are exact 2dp), latent inconsistency |
| RT-HIJ-9 | Info | I3 | `/finance/transactions/{id}/correct` has an optional key but is replay-safe even keyless (void guard → 409) — benign contrast to RT-M-1's list |
| RT-HIJ-10 | Info | I3 | Correction chains unbounded (correcting corrections); fully audited (`correction_of_id` + inherited source pair), no depth cap |
| RT-HIJ-11 | Info | H7 | Restock without a price increases stock with zero ledger trace (documented "gifted feed" design); `feeding.manage` holder can inflate stock invisible to finance |
| RT-HIJ-12 | Info | H5/H6 | `FeedRecipeLine.kg_per_100kg` stored as double-precision float (reference proportions, not money); mix math re-derives exact grams via `Decimal(str())` + 1e-6 total tolerance |
| RT-HIJ-13 | Info | I1 | All-time totals + 12-month P&L recomputed on every ledger list request (two aggregate scans) — farm-bounded and indexed, noted for scale |

No Critical findings: all lookups are farm-scoped with uniform 404/400 (no cross-tenant path
found), negative money is impossible (positive/non-negative schema bounds + DB
`ck_transactions_amount_bounded`, `ck_purchase_batches_total_price`), and no new
double-booking beyond RT-M-1's optional-key endpoints (source-linked rows are protected by
`uq_transactions_active_source`; manual creation and dispense require the key).

---

## Detailed findings

### RT-HIJ-1 (High, J2) — `purchases.manage` cascade creates Animal rows without `animals.create` (mirror of RT-C-1)

**Evidence.**
- `backend/app/api/purchases.py:107-115` — `POST /api/purchases/new` is gated solely by
  `require_perm("purchases.manage")`.
- `backend/app/services/purchases.py:145-167` — with `create_animals` (default **true**,
  `backend/app/schemas/purchases.py:34`) the service inserts up to `MAX_BATCH_COUNT` (1000,
  `models/constants.py:52`) full `Animal` rows: `farm_id, tag_number, sex, source=PURCHASED,
  current_bucket=QUARANTINE, status=ACTIVE, purchase_date, purchase_price, seller_name,
  purchase_batch_id, estimated_dob`.
- `backend/app/services/purchases.py:174-199` — plus per-animal `BucketMove` (initial
  QUARANTINE placement) and `WeightRecord` rows.
- `backend/app/services/purchases.py:200-222` — plus 8 quarantine `Task` rows and an
  `ANIMAL_PURCHASE` expense `Transaction` (amount up to ₹1,000,000,000 via
  `NonNegativeMoneyFloat` cap).
- `backend/app/permissions.py:51-60` — `PERMISSION_DEPENDENCIES` couples
  `purchases.manage` only to `purchases.view`; nothing pairs it with `animals.create`
  (the direct creation endpoint requires it: `backend/app/api/animals.py:293`).

**Exploit sketch.** Owner grants a custom role `{purchases.view, purchases.manage}` (a
procurement clerk). The clerk POSTs `/api/purchases/new` with `count=1000`,
`total_price=1000000000`, `create_animals=true` repeatedly: each request mints 1,000 ACTIVE
QUARANTINE animals, 1,000 bucket moves, 1,000 weight records, 8 tasks and a ₹1e9 booked
expense. The worker never holds `animals.create`, `animals.view`, `finance.manage` or
`tasks.*` — yet creates records in all four domains. Herd counts, the feeding plan
(QUARANTINE ration lines), dashboards and reports are all polluted; the ledger expense is
booked without `finance.manage`.

**Impact.** Vertical permission-domain crossing identical in structure to RT-C-1 (rated
High): one manage permission silently performs writes owned by `animals.create`,
`animals.weight`/`animals.move` (system moves), `tasks` creation and `finance.manage`.
Custom-role ceilings (`_clean_permissions`) do not prevent the subset grant.

**Fix.** Either require `animals.create` on the stub cascade (reject `create_animals=true`
without it, mirroring what C2's fix should do in reverse), or introduce an explicit composed
permission (e.g. `purchases.create_animals`) documented in the catalog, and book the expense
under the same coupling RT-C-1's fix applies.

### RT-HIJ-2 (Medium, I3) — Correction recategorizes a manual row into a system-only category

**Evidence.**
- `backend/app/schemas/finance.py:33-35` — `SYSTEM_ONLY_CATEGORIES = {ANIMAL_SALE,
  ANIMAL_PURCHASE}` with the stated purpose: *"a manual row in these categories would be
  indistinguishable from system-generated revenue."*
- `backend/app/schemas/finance.py:47-54` — the guard exists only on `TransactionIn`
  (`_manual_row_policy`).
- `backend/app/schemas/finance.py:77-93` — `TransactionCorrectionIn` has **no** such policy;
  `category` is the full 9-value `TransactionCategoryStr` literal and `amount` may be ₹0+.
- `backend/app/api/finance.py:305-314` — `_reconcile_source_record` returns `None`
  immediately for a source-less (manual) row **before** the type/category freeze check, so
  nothing re-validates the payload category.
- `backend/app/api/finance.py:666-671, 686-701` — for manual rows the replacement takes
  `payload.type`, `payload.category`, `payload.related_animal_id` (any same-farm animal,
  `_resolve_related_animal` only checks farm membership) verbatim.

**Exploit sketch.** A `finance.manage` holder creates a legitimate manual row
(`EXPENSE`/`OTHER`, ₹10), then corrects it with `{type: "INCOME", category: "ANIMAL_SALE",
amount: 500000, related_animal_id: <any sold animal>, reason: "rebook"}`. The replacement
(row 2) is a hand-typed `ANIMAL_SALE` income optionally linked to a real animal — exactly
the row the creation guard forbids. `GET /api/finance?category=ANIMAL_SALE`, the P&L
category breakdown (`backend/app/services/finance.py:59-62`) and the ledger calibration
reader (`backend/app/services/simulation_calibration.py:911-927`, which aggregates every
active row by category) all present it as animal-sale revenue. Its only distinguishing
feature is `source_type: null` in the raw payload.

**Impact.** Defeats a declared financial-control invariant; enables miscategorized/phantom
"sale revenue" that category-grouped reporting and advisory calibration cannot distinguish
from real sales. Requires `finance.manage` (insider) — not cross-tenant, hence Medium.

**Fix.** Apply the system-only policy in `correct_transaction` for source-less targets:
reject `payload.category in SYSTEM_ONLY_CATEGORIES` when `txn.source_type is None` (and
likewise a type/category change that *moves into* a system-only category).

### RT-HIJ-3 (Low, J2/G1) — Supplier name disclosed via task titles to non-procurement workers

**Evidence.** `backend/app/models/helpers.py:84-103` — `quarantine_schedule()` builds every
task title as `[{supplier} #{batch.id}] {protocol}`; `backend/app/services/purchases.py:70-80`
persists them via `_add_task`, auto-assigned to the category's default role
(`backend/app/services/_common.py:71-93`). Quarantine tasks surface on the task board (G1)
to any worker whose role covers QUARANTINE/DEWORMING/VACCINE/BUCKET_MOVE categories — no
`purchases.view` required. J3 deliberately degrades batch-detail payloads to hide
procurement data from task/animal viewers (`backend/app/schemas/summaries.py:59-84`), but
the title side-channel was not covered.

**Impact.** Farm-scoped disclosure of supplier identity (business-sensitive procurement
data) to feeder/cleaner/vet workers. Low severity.

**Fix.** Drop the supplier from task titles (the `#batch_id` reference suffices) or replace
with a static label.

### RT-HIJ-4 (Low, H3/H4) — Unbounded backdating of dispensing records

**Evidence.** `backend/app/api/feeding.py:148-153` — only `require_farm_not_future` is
applied; `backend/app/schemas/feeding.py:56` (`PastOrTodayDate`, UTC+1day) — no lower
bound. `backend/app/services/feeding.py:517-527` accepts any past `dispense_date`. Records
can be dated before farm creation or before any animal/recipe existed; date-filtered
history and per-day aggregates (`api/feeding.py:82-121`) reflect the fabricated chronology.

**Impact.** Audit-trail/data-quality pollution only: the finished-stock/inventory debit is
taken from the *current* balance under lock, so no stock or money corruption, and idempotency
plus `_positive_kg` bounds hold. Low.

**Fix.** Reject dispense dates before the farm's creation date (or the earliest mix /
animal acquisition) as a hardening floor.

### RT-HIJ-5 (Low, H2) — Ration override ceiling is the generic 1e6 kg quantity bound

**Evidence.** `backend/app/schemas/feeding.py:71-73` — `daily_kg_per_head: QuantityKgFloat`
(positive, ≤ 1,000,000 kg, `backend/app/schemas/common.py:148-152`);
`backend/app/services/feeding.py:330-335` multiplies by headcount with no domain ceiling.
The suite explicitly pins acceptance of huge finite values
(`tests/test_feeding_extended.py:963` `test_settings_huge_finite_accepted`). Negative/zero
are correctly rejected; the plan is display-only (no stock movement), so this is
garbage-in-garbage-out, not corruption.

**Fix.** Domain cap (e.g. ≤ 50 kg/head/day) in `FeedSettingIn`.

### RT-HIJ-6 (Low, J2) — IntegrityError catch-all mislabels non-tag failures

**Evidence.** `backend/app/api/purchases.py:153-160` — any `IntegrityError` raised during
batch creation is reported as *"A generated animal tag already exists on this farm — please
retry."* A different constraint failure (e.g. a future CHECK added to the cascade) would be
misdiagnosed as a retryable tag collision, prompting operators to hammer retry.

**Fix.** Inspect `exc.orig.diag.constraint_name` before choosing the 409 message; re-raise
anything that is not the tag unique index.

### RT-HIJ-7 (Info, I1) — Garbage `month` filter matches nothing instead of 422

`backend/app/api/finance.py:516-528` — unparseable months and the unrepresentable
`9999-12` both compile to `WHERE false()`, returning an empty page with 200. Deliberate
v1 compatibility (comment cites v1's LIKE behavior); harmless but can mask client typos.

### RT-HIJ-8 (Info, I1) — Rounding-mode inconsistency in `monthly_pnl`

`backend/app/services/finance.py:64-68` uses Python `round()` (context default
ROUND_HALF_EVEN) while all money derives through `money()` (ROUND_HALF_UP). Sums of
`Numeric(14,2)` amounts are exactly 2dp, so the calls are currently no-ops; the latent
inconsistency is worth normalizing (use `money()`).

### RT-HIJ-9 (Info, I3) — Correction endpoint is replay-safe despite its optional key

Interaction note to RT-M-1: `POST /api/finance/transactions/{id}/correct` declares
`IdempotencyKey = None` (`backend/app/api/finance.py:636-637`), but a keyless double-submit
cannot double-book: the target row is locked `FOR UPDATE` and the second attempt observes
`voided_at` → 409 (`api/finance.py:648-658`). The void-guard is the natural key RT-M-1's
list lacks. No action needed beyond RT-M-1's overall key-policy decision.

### RT-HIJ-10 (Info, I3) — Unbounded correction chains

A replacement row is itself correctable (`api/finance.py:686-701`); chains continue with each
link carrying `correction_of_id` and the inherited, chain-stable `(source_type, source_id)`
pair under the partial-unique active index (`models/finance.py:85-94`). Integrity holds (one
active row per source event at all times); only audit-review length grows. Observation only.

### RT-HIJ-11 (Info, H7) — Price-less restock is ledger-invisible stock growth

`backend/app/services/feeding.py:466-479` — with `price_per_kg=None` the quantity increases
and no expense is booked (pinned by `test_add_stock_without_price_books_no_expense`). This
is documented design ("gifted/home-grown feed"), but means a `feeding.manage` holder can
grow stock without any finance trace. Observation; if unwanted, book a ₹0 flagged expense as
purchases do (`services/purchases.py:205-222`).

### RT-HIJ-12 (Info, H5/H6) — Recipe proportions stored as float64

`backend/app/models/feeding.py:33-57` — `kg_per_100kg` is `Mapped[float]` (float64 column
since the initial schema). Not money; `mix_feed_batch` re-derives exact whole grams via
`Decimal(str(...))` aggregation with a 1e-6 recipe-total tolerance
(`services/feeding.py:385-399`), and every debit/credit is exact at the 0.001 kg ledger
precision. All actual stock/money columns are `Numeric` (see cross-cutting note). Observation.

### RT-HIJ-13 (Info, I1) — Per-request aggregate cost of the ledger page

`backend/app/api/finance.py:548-564` + `backend/app/services/finance.py:25-46` run two
extra aggregate scans (all-time totals by type; 12-month P&L by month/type/category) on
every list call. Both are farm-equality-filtered and supported by
`ix_transactions_farm_date_id` (`models/finance.py:134-139`); cost is farm-bounded. Fine at
current scale; consider caching if ledgers grow six orders of magnitude.

---

## Per-unit attacked-and-held notes

### H1 — Today's plan
- Totals are truthful beyond the 200-record window: `records_total` is a full-day count and
  `dispensed_totals` a SQL aggregate over the entire day, not the bounded preview
  (`api/feeding.py:63-121`).
- Plan math is exact whole-gram arithmetic: daily kg quantized HALF_UP, shift split by
  largest-remainder over `total_units` (`services/feeding.py:114-136`); the missing-grams
  residual is provably ≤ 2 (< 3 shifts), so no index error. Multi-head gram conservation is
  pinned by `test_settings_multihead_gram_total_is_neither_rounded_up_nor_lost`.
- `today(farm.timezone)` with safe fallback (`utils.py:43-54`); dependent-kid creep logic and
  bucket-day boundaries computed SQL-side, farm-scoped. Held.

### H2 — Feed settings
- Bucket is a `Literal` of the ten real buckets (`schemas/animals.py:25-38`) — arbitrary
  bucket strings rejected at the schema; negative/zero kg rejected (`QuantityKgFloat`);
  concurrent first-save serialized inside `pg_insert ... ON CONFLICT`
  (`services/feeding.py:187-204`). Held except the ceiling (RT-HIJ-5).

### H3 — Dispense
- **Required** Idempotency-Key verified (`RequiredIdempotencyKey`, `api/feeding.py:144`;
  duplicate headers 422 at `services/idempotency.py:45-61`); replay with a different body →
  409 fingerprint mismatch (`services/idempotency.py:378-382`); state-dependent recipe
  validation correctly inside the claim (a replay does not re-evaluate a changed recipe).
- Quantity bounds: schema `QuantityKgFloat` + `_positive_kg` backstop — 0.0004 kg is
  rejected ("must be at least 0.001 kg after rounding"), so a zero-debit-but-recorded row is
  impossible; non-finite rejected.
- DRY_ROUGHAGE special case **cannot** bypass the stock debit: it locks and debits the
  seeded `Dry jowar stover` inventory row `FOR UPDATE`; a missing row or shortage → 400
  (`services/feeding.py:529-554`). No ex-nihilo feed.
- Finished-stock debit serialized by `FOR UPDATE` on the `(farm, recipe_code)` row
  (`services/feeding.py:556-577`); underflow → `InsufficientFeedError` → 400, backed by
  `ck_finished_feed_qty_nonneg`. Negative finished stock not reachable.
- Recipe existence checked for every non-dry-roughage code (400 for unknown); farm
  isolation on records; `feeding.manage` 403 enforced (pinned by role tests). Dispensing to
  an unoccupied bucket is allowed (stock still debited — waste, not bypass). Future dates
  422 vs farm timezone. Held except backdating (RT-HIJ-4).

### H4 — History
- `date_from > date_to` → 400; limit ≤ 200, offset ≤ 1e6; stable `(date, id)` ordering;
  farm-scoped. Held.

### H5 — Recipes + finished stock
- Read-only; recipes are global reference data (no tenant payload); finished stock is
  farm-scoped `Numeric(15,3)` rows mutated only by mix/dispense transactions. Float drift
  note RT-HIJ-12. Held.

### H6 — Mix
- Per-ingredient `FOR UPDATE` acquired in **canonical ingredient-name order**
  (`services/feeding.py:403-419`), so concurrent mixes of recipes sharing ingredients in
  different line orders cannot invert lock order (deadlock-safe); single-lock writers
  (dispense-dry-roughage, add-stock) cannot cycle against it.
- Shortage check is check-then-debit entirely under the locks and all-or-nothing (no writes
  before the shortage raise) — pinned by `test_mix_refusal_is_all_or_nothing`.
- No free finished stock: lineless recipes, non-positive lines, and totals ≠ 100 kg are
  rejected; Hamilton allocation guarantees every ingredient ≥ 1 g and
  Σ(grams) = batch grams exactly, so debit total == finished credit
  (`services/feeding.py:57-90, 397-399`); the finished-stock upsert is a single atomic
  `INSERT ... ON CONFLICT DO UPDATE ... round(3)` (`services/feeding.py:434-452`) —
  concurrent mixes add to the committed balance.
- Recipes are seeded-only (no creation API), so custom zero-line recipe abuse is not
  reachable. Mix books no finance transaction (pinned). Optional key → RT-M-1. Held.

### H7 — Inventory add-stock
- `FOR UPDATE` on the item with farm-scoped lookup → cross-farm/nonexistent/malformed ids
  all uniform 404 (`api/feeding.py:328-341`).
- All derived-value validation **precedeses** mutation (`services/feeding.py:93-111`):
  negative price/qty impossible (schema + `_restock_money`/`_positive_kg`); positive price
  must round ≥ ₹0.01/kg; a positive-price restock must total ≥ ₹0.01; total ≤ ₹1e9. Amount
  = `money(qty × exact_price)` — exact paise, never negative (no phantom income);
  ₹0/kg is a real price (books ₹0, zeroes last price); no price → no expense (RT-HIJ-11).
- `last_purchase_price_per_kg` is stored, not divided — no ÷0; expense dated `today(farm.tz)`
  (no backdating); provenance self-source id unique by construction with all-or-none CHECK
  (`services/feeding.py:506-513`, `models/finance.py:50-59`). Optional key → RT-M-1. Held.

### I1 — Ledger + P&L
- Filters typed as `Literal`s (NUL/enum injection impossible — pinned at
  `test_finance_extended.py:2094`); month parsing hardened for garbage and `9999-12`
  (RT-HIJ-7 is the residual by-design gap); pagination caps enforced.
- All-time totals exclude voided (`api/finance.py:548-555`) while the list retains voided
  rows as flagged audit trail — consistent, deliberate split (pinned
  `test_totals_ignore_*`, `test_correction_preserves_audit_trail_and_replaces_totals`).
- P&L: SQL aggregation, voided excluded, 12 calendar months pre-seeded from the farm-timezone
  `today`, boundaries on stored business dates; rounding note RT-HIJ-8. Held.

### I2 — Manual transaction
- **Required** key verified (`api/finance.py:586`); `MoneyFloat` is strictly positive,
  ≤ ₹1e9, paise-normalized (negative and sub-paisa-zero rejected; zero rejected by design —
  corrections may zero, creations may not).
- System-only categories blocked at creation (the gap is RT-HIJ-2 on the correction side);
  `related_animal_id` unknown vs cross-farm share one uniform 400 — no enumeration oracle
  (`api/finance.py:61-76`); future date rejected twice (UTC+1 schema, farm-tz service);
  notes control-character/surrogate-safe and bounded. Held.

### I3 — Correction (void-and-replace)
- **Atomicity:** void + source reconciliation + replacement insert all run inside the single
  transaction committed by `execute_idempotent` (`services/idempotency.py:307-315, 431-447`);
  the void is flushed before the replacement insert so the active-source partial unique
  index admits the new row (`api/finance.py:673-678`).
- **Concurrency:** target row locked `FOR UPDATE` farm-scoped (uniform 404; int32 guard);
  double-correction serializes → 409 on `voided_at`; correcting a voided row → 409; no
  optimistic-version needed given the row lock. Animal and inventory reconcilers take
  their locks *after* the transaction lock consistently.
- **Deadlock analysis (scope #11):** status-change books a sale as Animal(lock) →
  BreedingRecords/Task locks → Transaction **insert** (`api/animals.py:961-1234`); the
  correction path is Transaction(lock) → Animal(lock)/FeedInventory(lock). No cycle is
  reachable: the pair (status-change booking `ANIMAL_SALE` for animal X, correction of X's
  sale) is state-exclusive — the endpoint requires `status == ACTIVE` under the row lock
  (`api/animals.py:966-971`), so a sale txn exists only after the animal left ACTIVE, and
  vice versa; no path that holds an Animal/FeedInventory lock ever waits on an *existing*
  Transaction row (status-change/add-stock only INSERT new rows whose source pairs are
  unique-by-construction or animal-specific). `db.get(..., with_for_update=True)` is not
  defeated by the identity map here — no prior animal load exists in the correction request.
  Held.
- **ANIMAL_SALE rewrite:** animal locked; auto-abort anchor (BreedingRecord
  `ANIMAL_STATUS_CHANGE`) blocks date moves (409); birth/purchase chronology (422);
  `require_status_after_recorded_facts` (422); withdrawal re-checked on the **new** date
  (409) — and the check can only over-block (never under-block: the original sale implies no
  withdrawal covered the original date); orphan-early-wean anchor blocks date desync (409);
  `sale_price`/`status_date` updated atomically. All pinned by tests
  (`:977-:1406`). Held.
- **ANIMAL_PURCHASE rewrite:** birth lower bound + terminal-status upper bound (422),
  earliest-recorded-fact boundary via `require_purchase_before_recorded_facts` (422),
  initial `BucketMove` re-dated under its own lock, `purchase_price/date` updated. Purchase
  date cannot be moved before birth or after the first lifecycle fact. Held.
- **PURCHASE_BATCH rewrite:** year-2000 floor; batch locked `FOR UPDATE`; the guard counts
  animals allocated to the batch (`api/finance.py:445-461`) — count (not price-sum) is the
  right invariant because per-head prices are an exact `allocate_money` split of the total,
  so any allocation implies the total is fanned out → amount/date frozen (409);
  zero-allocation batches are freely correctable. Cross-farm batch probes → 409 after the
  farm-filtered lookup; dangling source pointers → 409. Held.
- **FEED_PURCHASE rewrite:** pre-provenance rows (all-NULL triple) take the pure-ledit path
  with qty-correction refused (422); quantity delta applies to the running balance with a
  negative-stock refusal (409) and `ck_feed_inventory_qty` backstop; unit price re-derived
  exactly (paise floor + ₹1e9 cap, 422); latest-purchase arbitration deterministic on
  `(date, source_id)` — chain-stable under corrections; legacy-row ambiguity → 409;
  notes-only corrections skip the locks safely. Extensively pinned
  (`test_finance_bugs.py:150-400`). Held.
- **Switch exhaustiveness:** unknown/future source types fall into the default branch which
  **denies** amount/date changes (409) — fail-closed, no permissive fallthrough
  (`api/finance.py:470-483`). Type/category frozen on every source-linked row (422) — the
  residual gap is manual rows (RT-HIJ-2).
- `related_animal_id` pinning: a source-linked row cannot be moved to a different animal
  (422); manual rows resolve via the uniform-400 resolver. Chains noted (RT-HIJ-10);
  optional key benign (RT-HIJ-9). Held except RT-HIJ-2.

### J1 — Batch list/search
- `q` is bounded (≤120) and PostgresText-safe; LIKE wildcards escaped including backslash
  (`api/purchases.py:84-92`); `#id` exact-match guards int32 before `int()` and rejects
  non-ASCII digits (`.isascii()`); farm-scoped; enrichment counts safe (batch ids are
  globally unique, so the unfiltered `in_(ids)` joins cannot cross tenants). Held.

### J2 — Batch creation
- Count 1..1000 triple-enforced (schema, service, `ck_purchase_batches_count`); avg weight
  species-capped at 150 kg (`GOAT_PROFILE.max_adult_weight_kg`, `api/purchases.py:127-135`)
  on top of the ≤1000 schema/CHECK band; `total_price` non-negative, paise-exact, ≤ ₹1e9 —
  negative totals impossible; omitted/₹0 price books a *flagged* ₹0 expense (never
  invisible inventory); per-head prices are an exact non-negative `allocate_money` split
  (`services/purchases.py:137-144`).
- Date ≥ year-2000 (schema) + not-future vs farm tz; quarantine schedule is 8 tasks dated
  day-1..45 from the batch date (`models/helpers.py:60-103`) — a backdated purchase makes
  tasks immediately overdue, which is the documented intent; supplier truncated into the
  200-char title budget. Stub sex `M|F` persisted on the batch regardless of
  `create_animals`; CSPRNG nonce tags with collision → rollback + 409 (RT-HIJ-6 notes the
  catch-all). WeightRecords only for a positive average.
- Findings: permission asymmetry RT-HIJ-1; supplier-in-title RT-HIJ-3; optional key →
  RT-M-1 (each keyless retry duplicates the entire batch graph).

### J3 — Batch detail
- Cross-farm/nonexistent batch → uniform 404 after int32 guard; animals are a bounded page
  with a truthful `animals_total`; tasks are 8 by construction.
- Purpose scoping verified: full `AnimalOut` only under `animals.view` (plus computed
  facts); otherwise `PurchaseQuarantineAnimalOut` (id/tag/sex/bucket/status only); full
  `TaskOut` only under `tasks.view` **and** per-task `visible_to`
  (`api/purchases.py:204-252`); otherwise `QuarantineScheduleTaskOut`
  (id/title/due/status/category — no assignee/buyer/worker data). A `purchases.view`-only
  worker sees schedule stubs, not task-module data; supplier is legitimate procurement data
  for them here. No leakage found in this unit (the title side-channel is RT-HIJ-3, J2/G1).

### Cross-cutting money integrity
- Every money column is exact: `Transaction.amount/feed_unit_price_per_kg`,
  `PurchaseBatch.total_price`, `FeedInventory.last_purchase_price_per_kg` are
  `Numeric(14,2)` `Decimal`; quantities are `Numeric(15,3)` (exact gram storage since
  migration `f2c3d4e5f6a7`, with non-finite/sub-gram/overflow preflight refusal). The only
  float64 domain column is recipe proportions (RT-HIJ-12).
- Rounding is `ROUND_HALF_UP` via `money()`/`_positive_kg`/`_restock_money` on every write
  (P&L note RT-HIJ-8); `allocate_money` keeps per-head splits exact and non-negative.
- Void semantics: voided rows retained (audit), excluded from totals/P&L/calibration;
  replacements inherit the chain-stable source pair under `uq_transactions_active_source`
  (one active row per source event — no double-booking via corrections); voids only through
  the correct endpoint (single writer of `voided_at`), self-correction blocked by CHECK,
  void state all-or-none by CHECK (`models/finance.py:60-67`).
- Negative money is unreachable in all three modules (schema minima + DB CHECKs).
- "Today" is farm-timezone-resolved everywhere (`utils.today`), with a safe
  `Asia/Kolkata` fallback for corrupt tz values.

## Regression tests to add

1. RT-HIJ-1: worker with exactly `{purchases.view, purchases.manage}` POSTs `/api/purchases/new`
   (`create_animals=true`) → assert 403 (after fix) / document current 201; assert no
   `Animal`/`BucketMove`/`WeightRecord` rows appear without `animals.create`.
2. RT-HIJ-2: manual row corrected to `category=ANIMAL_SALE`/`ANIMAL_PURCHASE` → assert 422;
   negative-control: correction to `FEED`/`OTHER` still 201.
3. RT-HIJ-3: worker with tasks.view but no purchases.view fetches the task board → titles
   contain no supplier string (after fix).
4. RT-HIJ-4: dispense dated before farm creation → 422 (after fix).
5. RT-HIJ-5: settings with `daily_kg_per_head=1_000_000` → 422 (after fix).
6. RT-HIJ-6: force a non-tag IntegrityError during batch creation (fixture) → not the
   tag-collision 409 copy.
