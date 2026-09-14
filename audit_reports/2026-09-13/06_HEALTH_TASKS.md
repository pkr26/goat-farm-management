# Red Team Audit — Part F (Health) + Part G (Tasks) — 2026-09-13

Domain report for units F1–F6 and G1–G7 of `audit_reports/2026-09-13/00_RED_TEAM_AUDIT_SCOPE.md`.

## Methodology

Every unit was attacked as an adversary would, by reading implementations end-to-end
and verifying enforcement in code (not comments):

- **Primary code read fully**: `backend/app/api/health.py` (985 l), `backend/app/api/tasks.py`
  (808 l), `backend/app/services/health.py` (688 l), `backend/app/services/tasks.py` (903 l),
  `backend/app/models/health.py`, `backend/app/models/tasks.py`, `backend/app/schemas/health.py`,
  `backend/app/schemas/tasks.py`, `backend/app/api/_shared.py` (visibility twin + `task_action_url`).
- **Supporting code cross-checked**: `services/chronology.py` (farm/animal date fences),
  `services/idempotency.py::execute_idempotent` (failure semantics, claim rollback),
  `services/animals.py` (`bucket_transition_error`, `move_animal`, task sweeps),
  `services/_common.py` (`_clear_task_rejection`, `_add_task`), `models/constants.py`
  (`VERIFICATION_REQUIRED_CATEGORIES = (CLEANING,)`, `MAX_WITHDRAWAL_DAYS = 730`,
  `MAX_RECUR_DAYS = 3650`, `MAX_BATCH_COUNT = 1000`), `models/helpers.py::quarantine_schedule`,
  `models/lifecycle.py::LEGAL_BUCKET_TRANSITIONS`, `schemas/common.py` (Strict*/BoundedId/money
  validators), `utils.py` (`money`/`allocate_money`/`today(farm.tz)`), `api/animals.py`
  status-change path (mortality restriction placement, withdrawal sale-block), `api/finance.py`
  correction path (batch headcount immutability), `deps.py` (perm resolution), `main.py`
  (inactive-animal cleanup loop).
- **Tests consulted**: `test_health_bugs.py`, `test_health_extended.py`, `test_health_safety.py`,
  `test_tasks_bugs.py`, `test_tasks_extended.py`, `test_task_visibility_parity.py`,
  `test_inactive_animal_task_cleanup.py`.
- **Attack classes applied per unit**: horizontal/vertical authz, tenant isolation,
  business-logic/cascade abuse, form-link bypass, state-machine abuse, replay/idempotency abuse,
  concurrency/lock-order (deadlock + lost-update), enumeration oracles, resource exhaustion,
  input bounds/CHECK parity (500 hunting).

Only code-evidenced findings are reported. Reachability caveats are stated explicitly.
Severity: Critical = cross-tenant / authz bypass / reachable form-link bypass; High = task-state
corruption, mass freeze, scoping gaps; Medium = defense-in-depth in a safety-critical invariant;
Low = hardening/policy; Info = observations.

---

## Findings table

| ID | Severity | Unit | Title |
|----|----------|------|-------|
| RT-FG-1 | Medium | G4 (cross F5) | Form-linked-duty rejection is linkage-derived, not category-derived: a generated duty missing its linkage columns is bare-completable with zero data recorded |
| RT-FG-2 | Low | F2 | Any `health.manage` holder can unilaterally clear a statutory scheduled-disease hold with a self-supplied clearance reference (no dual control) |
| RT-FG-3 | Low | F2/F3 | Episode supersession leaves stale clearance attribution (`restriction_cleared_at/by/reference`) describing the previous, cleared episode on an animal under a new active hold |
| RT-FG-4 | Low | G5 | Skip reason (and skip body) is optional — unexplained skips leave no audit reason |
| RT-FG-5 | Low | G7 | Reject note is optional despite the documented "note required" contract |
| RT-FG-6 | Info | F3/F4 | Health-event `cost` books a farm EXPENSE `Transaction` — a `health.manage`-only worker writes ledger rows without any finance permission (by design; policy note) |
| RT-FG-7 | Info | F1 | `restriction_version` (episode counter) exposed to `health.view`-only roles in the health animal lookup |
| RT-FG-8 | Info | G6 | Farm owner is exempt from the two-person verify rule (documented policy, not a bug) |
| RT-FG-9 | Info | F4 | One bulk request can mass-freeze up to 250 (bucket) / batch-sized animals (`suspected_scheduled_disease` / `withdrawal_until`) — intended veterinary power, single-actor |
| RT-FG-10 | Info | G6/G7 | Verify is allowed on duties whose linked animal became inactive while reject is 409 — deliberate asymmetry, noted for awareness |

Counts: **Critical 0, High 0, Medium 1, Low 4, Info 5.**

---

## Detailed findings

### RT-FG-1 — Medium — G4 (cross F5): form-link rejection is linkage-derived, not category-derived

**Evidence**
- `backend/app/api/tasks.py:591-592` — the only form-link fence on the bare complete route:

```python
if task_action_url(task) is not None:
    raise HTTPException(status_code=409, detail="Use the linked form to complete this duty")
```

- `backend/app/api/_shared.py:252-268` — `task_action_url` returns `None` (i.e. "freely
  completable") for:
  - `ULTRASOUND` with `breeding_record_id is None`,
  - `KIDDING_DUE` with `breeding_record_id is None`,
  - `VACCINE`/`DEWORMING` with **both** `animal_id` and `purchase_batch_id` `None`.
- The health route *does* guard the equivalent shape: `backend/app/api/health.py:854-857`
  raises `422 "Linked health task has no supported target"` (regression test
  `test_health_safety.py::test_linked_duty_without_a_target_cannot_be_closed`). The tasks
  complete route has no category-based fallback.
- `backend/app/models/tasks.py:34-114` — the tasks table has CHECK constraints for status,
  category vocabulary, recurrence, and verification/skip/rejection state, but **none** enforcing
  `category IN ('ULTRASOUND','KIDDING_DUE') -> breeding_record_id NOT NULL` or
  `category IN ('VACCINE','DEWORMING') -> animal_id IS NOT NULL OR purchase_batch_id IS NOT NULL`.

**Exploit sketch** (requires a malformed row — see reachability)
`POST /api/tasks/{id}/complete` on a PENDING auto-generated `VACCINE` duty that carries neither
animal nor batch: `_lock_completion_animals` returns `[]` (no target columns,
`api/tasks.py:176-179`), `_require_locked_linked_animal_active` passes (`animal_id is None`,
`api/tasks.py:226-231`), `task_action_url` is `None` so no 409, and if the duty carries the
worker's role, `visible_to` passes -> `complete_task` marks DONE with no vaccination recorded
(`services/tasks.py:363-489` has no VACCINE branch). Same for a linkage-less `ULTRASOUND`
duty: DONE with no scan, breeding left PENDING. A linkage-less `KIDDING_DUE` duty closes with
no kidding record.

**Reachability (why Medium, not Critical/High)** — no API path creates such a row today:
manual creation is restricted to `FEED/CLEANING/OTHER` (`schemas/tasks.py:29`
`ManualTaskCategoryStr`, tested by `test_create_rejects_system_workflow_categories`), and every
generator populates the linkage (`services/_common.py::_add_task` callers; quarantine duties are
batch-linked, pre-kidding vaccine animal-linked, ultrasound/kidding-due breeding-linked). The
class is therefore reachable only via legacy rows, out-of-process writers, or a future generator
regression — precisely the situation the DB CHECK-parity philosophy elsewhere in this schema is
meant to fence (`ck_health_events_*` analogously hardens health rows against out-of-process
writers).

**Impact** — silent form-link bypass for corrupt data: a duty that exists to force data capture
(health/ultrasound/kidding facts) closes with none; compliance reporting understates
vaccinations; a skipped-scan pregnancy is never resolved.

**Fix** — make the fence data-invariant instead of linkage-derived:
1. Add CHECK constraints: `category IN ('ULTRASOUND','KIDDING_DUE') -> breeding_record_id IS NOT NULL`
   and `category IN ('VACCINE','DEWORMING') AND auto_generated -> (animal_id IS NOT NULL OR purchase_batch_id IS NOT NULL)`.
2. Belt-and-braces in the complete route: reject when
   `task.auto_generated and task.category in (VACCINE, DEWORMING, ULTRASOUND, KIDDING_DUE) and task_action_url(task) is None`
   (mirroring the health route's "no supported target" 422).

---

### RT-FG-2 — Low — F2: unilateral clearance of statutory disease holds

**Evidence** — `backend/app/api/health.py:316-400` (`POST /api/health/restrictions/{animal_id}/clear`):
permission gate is `health.manage` only; the payload is a self-supplied free-text
`clearance_reference` (`schemas/health.py:34`, 1-255 chars); there is no second-person rule, no
requirement that the clearer differ from the placer, and no restriction-specific permission
split between "operational" and "scheduled-disease" holds. The code's own framing ("Record a
factual authority/veterinary clearance") is enforced only by attribution, not by control.

**Exploit sketch** — a worker holding `health.manage` (e.g. a vet-role worker) records a
suspicion (or a colleague does), then clears it moments later with
`clearance_reference: "self OK"`; the animal is immediately sellable
(`api/animals.py` change_status blocks sale only while the hold flags are set).

**Impact** — single-actor defeat of the biosecurity hold the module otherwise treats as
statutory (scheduled-disease suspicion freezes movement, sale, breeding). The audit trail stays
intact (CLEARED action attributed), so this is an insider-abuse/policy gap, not a stealth one.

**Fix** — policy decision: require a distinct clearer for `suspected_scheduled_disease` holds
(mirror the two-person verify rule in G6 with an owner exemption), or require a structured
clearance (e.g. minimum reference shape / vet-role attribute) rather than free text.

---

### RT-FG-3 — Low — F2/F3: stale clearance attribution after episode supersession

**Evidence**
- `backend/app/api/health.py:367-369` — clear sets `restriction_cleared_at`,
  `restriction_cleared_by_id`, `restriction_clearance_reference` (documented there as describing
  "the CURRENT episode only").
- `backend/app/services/health.py:184-221` — `place_movement_restriction` opens a NEW episode
  (bumps `restriction_version`, sets hold flags and reason) but **does not reset the three
  clearance columns**. Contrast the deliberate, tested handling of `authority_notified_at`
  (`services/health.py:347-353`; `test_health_safety.py::test_a_new_suspicion_episode_restates_its_own_authority_notification`).
- Exposure: `schemas/animals.py:135-137` / `api/_shared.py:224-237` render these columns under
  `health.view`. No test covers the clear(v1) -> place(v2) sequence for these columns
  (`test_restriction_versions_history_and_explicit_supersession` places v1 then v2 without an
  intervening clear).

**Exploit sketch** — suspicion v1 placed -> cleared with reference "AHD-2026-117" -> new suspicion
v2 placed. The animal profile now shows an *active* hold (`movement_restricted=true`,
`restriction_reason` = new) **and** `restriction_cleared_at` / `restriction_clearance_reference`
from episode v1 — a stale "authorised clearance" attached to a live restriction.

**Impact** — misleading compliance display: an operator (or auditor reading the profile rather
than the episode history) can read an authoritative clearance as covering the current concern.
No enforcement consumer uses these columns (sale gating reads the hold flags only), so this is
data hygiene, not a control bypass.

**Fix** — null `restriction_cleared_at/by_id/clearance_reference` in
`place_movement_restriction` (the historical fact is preserved on the CLEARED action row).

---

### RT-FG-4 — Low — G5: skip reason optional

**Evidence** — `backend/app/api/tasks.py:624` (`payload: TaskSkipIn | None = None` — the whole
body may be omitted); `backend/app/schemas/tasks.py:98-99` (`reason` optional);
`backend/app/services/tasks.py:736` (`task.skip_reason = (reason or "").strip() or None`).
The scope contract for G5 says "reason capture".

**Impact** — a worker can skip any skippable duty with zero explanation; the skip trail carries
`skipped_by/skipped_at` but no why. Policy/audit-quality gap only (the un-skippable gates are
unaffected).

**Fix** — require `reason: min_length=1` in `TaskSkipIn` (or at minimum for
`auto_generated` duties).

---

### RT-FG-5 — Low — G7: reject note optional despite "note required"

**Evidence** — `backend/app/schemas/tasks.py:94-95` (`note: ... | None = Field(default=None,
max_length=255)`); `backend/app/api/tasks.py:806` and `backend/app/services/tasks.py:787`
accept an empty note (`verification_note = note or None`). The scope contract for G7 says
"note required"; the model CHECK (`ck_tasks_rejection_current_state`) permits a PENDING row
with NULL note.

**Impact** — a verifier can bounce a worker's completed duty back to PENDING with no
explanation; the worker sees a reopened duty with `verification_note = null`. Audit-quality
gap; state machine is unaffected.

**Fix** — `note: PostgresText = Field(min_length=1, max_length=255)`.

---

### RT-FG-6 — Info — F3/F4: health cost writes to the finance ledger

`backend/app/services/health.py:354-372`: every `record_health_event` call with a non-null cost
inserts one `EXPENSE` `Transaction` (`source_type="HEALTH_EVENT"`, category VET/MEDICINE mapped
from event type). A worker with `health.manage` but no finance permission therefore creates
ledger rows (including backdated ones, dated `event_date`). This is deliberate (P&L
correctness) and the amount is bounded/non-negative (`NonNegativeMoneyFloat` <= Rs 1e9;
`ck_health_events_cost_nonneg/bounded`), but it is a cross-domain write worth recording as a
policy fact. No bypass found: category/notes/amount are server-derived.

### RT-FG-7 — Info — F1: episode counter visible to `health.view`

`backend/app/api/health.py:178-182` returns `movement_restricted` (effective hold) and
`restriction_version` to any `health.view` holder. The version is the optimistic-concurrency
token for the clear flow (which needs `health.manage`), so view-level roles learn how many
restriction episodes an animal has had. This is consistent with the module's own leakage
reasoning (`api/_shared.py:239-243` zeroes the counter only for non-health roles) and
farm-scoped; recorded for completeness.

### RT-FG-8 — Info — G6: owner exempt from the two-person rule

`backend/app/api/tasks.py:737-739`: `if task.completed_by_id == user.id and farm.owner_id != user.id`.
The owner can complete and then verify their own CLEANING duty. Documented exemption; flagged
as policy, not defect.

### RT-FG-9 — Info — F4: single-actor mass effects in bulk events

One `POST /api/health/events` with `scope=bucket` (<=250 animals, `MAX_BULK_BUCKET_TARGETS`) or
`scope=batch` (<=1000, `MAX_BATCH_COUNT`) can set `suspected_scheduled_disease=true` (freezing
movement/sale/breeding for every target via `place_movement_restriction`) and/or
`withdrawal_until` on all targets atomically. Intended veterinary power, bounded and audited;
noted because the same request also books one aggregated ledger transaction (RT-FG-6).

### RT-FG-10 — Info — G6/G7: verify/reject asymmetry on inactive linked animals

Verify deliberately omits `_require_locked_linked_animal_active` and instead withholds the
recurring successor (`backend/app/api/tasks.py:740-761`), so a duty whose animal was sold after
completion can still be verified (series ends); reject is 409 in the same situation
(`api/tasks.py:792`; regression-tested in `test_tasks_bugs.py:400`). Reasoned and documented —
recorded so future refactors do not "fix" one side only.

---

## Per-unit attacked-and-held notes

### F1 Reference lookups — held
- `GET /api/health/animals` (`api/health.py:129-188`): farm filter (`Animal.farm_id == farm.id`)
  is applied **before** the `#id` exact branch and the numeric/tag/name OR-branch, so `#id`
  cross-farm probes return nothing — no tenant oracle. `#<non-numeric>` falls to the escaped
  LIKE branch (`_escaped_contains`, `api/health.py:88-91`, escape=`"\\"` — no wildcard
  injection); ids above `MAX_INT32_ID` collapse to `false()` (no asyncpg 500). ACTIVE-only;
  `limit <= 100`, `offset <= MAX_PAGE_OFFSET`. Held.
- `GET /api/health/purchase-batches` (`api/health.py:191-259`): requires `health.manage`;
  exact-id-only anti-probe (non-numeric `q` -> `false()`); tenant defense-in-depth via the
  `PurchaseBatch.farm_id` join on top of composite FKs; returns only opaque ids + live
  quarantine counts. `q=#1` cannot enumerate another farm's batches. Held.
- `GET /api/health/schedule-templates`: global seeded reference data behind `health.view` +
  farm dependency. Held.

### F2 Movement restrictions — held (findings RT-FG-2, RT-FG-3)
- History (`api/health.py:262-313`): farm-scoped 404 for foreign/missing animals; pagination
  bounded (limit <= 100); deterministic newest-first ordering; total count included.
- Optimistic concurrency (`api/health.py:333-352`): animal row taken `with_for_update()`
  **before** the `expected_restriction_version` compare, and both placement writers (health
  event via `_lock_event_targets`; mortality via `_get_animal(for_update=True)` in
  `api/animals.py` change_status) take the same row lock first — a stale clear cannot interleave
  with a new placement (regression-tested: `test_clear_racing_a_new_hold_cannot_clear_the_new_episode`).
  Clearing a non-existent restriction -> 409; another farm's animal -> 404; superseded version ->
  409. `ck_movement_restriction_action_episode` + the monotonic version keep PLACED/CLEARED
  episodes collision-free.
- Authority-notified preservation: nulling `authority_notified_at` is gated on a HealthEvent
  holding the fact (`api/health.py:387-399`), covering the mortality path that writes no event.
  Held.

### F3 Health events (single) — held
- SOLD/DEAD animals rejected: animal scope requires ACTIVE under `with_for_update`
  (`api/health.py:539-553`); bulk stability checks re-require ACTIVE per animal.
- Withdrawal: `withdrawal_until >= event_date` (route + `ck_health_events_withdrawal_after_event`)
  and <= event+730 enforced twice — schema (only when `date` supplied) and after farm-tz date
  resolution (`api/health.py:781-799`), plus DB CHECK parity. Negative cost impossible
  (`NonNegativeMoneyFloat` + `ck_health_events_cost_nonneg`); cost ceiling Rs 1e9 in schema, DB,
  and ledger.
- Scheduled-disease suspicion: restriction placement happens in the same transaction as the
  event insert (`services/health.py:325-353`, event id flushed first for the audit reference);
  duplicate-episode races serialize on the animal row lock. Compliance dates
  (`authority_notified_at`, `isolation_started_at`) require the suspicion flag (schema + DB CHECK).
- Chronology: future dates rejected against the farm's business timezone
  (`require_farm_not_future`); pre-birth/pre-acquisition rejected
  (`require_animal_event_chronology`); batch events cannot predate the batch.
- Template linkage: VACCINE/DEWORMING accept only exact seeded `VaccineTemplate.name`s
  (`validated_template`), type-coupled; targets validated word-wise against the template/task
  (`target_matches_template/task` — ET+TT combined-component rule); templates are global
  reference data (reference-only linkage, fine).
- Field caps all mirror DB column sizes; notes <= 4000; `StrictInputModel(extra="forbid")`.

### F4 Bulk events — held
- Caps: bucket 250 / batch 1000, enforced in preview (`limit+1` probe -> 409) and again at
  record (`len(expected) > target_limit` -> 409); duplicates and out-of-int4 ids -> 409.
- Snapshot exactness: record re-locks the exact expected ids in canonical id order and requires
  set equality plus per-animal ACTIVE + bucket/batch membership (`api/health.py:636-684`) —
  a SOLD-between-preview-and-record animal makes the snapshot stale (409), and an old snapshot
  replay is only accepted if the live scope is byte-for-byte unchanged (that is the documented
  idempotent-retry semantic; the durable Idempotency-Key covers true replays).
- Linked batch writes get the stronger whole-cohort rule: every ACTIVE batch animal is locked
  (not just QUARANTINE rows), and the QUARANTINE cohort derived under lock must equal the
  reviewed ids (`api/health.py:583-634`) — closes the re-entry phantom; regression-tested
  (`test_linked_health_prelocks_active_animals_outside_quarantine`,
  `test_linked_health_winner_prevents_waiting_animal_quarantine_reentry`).
- Partial failure: single transaction; `execute_idempotent` rolls back claim + mutation on any
  exception (claim and result commit atomically; contenders arbitrate at the unique index).
- Unlinked bulk keeps deliberate subset semantics (late entrants not silently added).

### F5 Task-linked events — held
- Matching matrix enforced under the task row lock: farm, PENDING status, category in
  {VACCINE, DEWORMING} (`api/health.py:824-833`); event type == task category; scope == task
  target (animal or exact batch); template/target must match the duty's protocol phrase
  (`template_name_for_task` operates on the operator-free phrase, so supplier/tag names cannot
  retarget the programme item); `event_date < task.due_date` -> 409 (server-side
  not-due-yet mirror).
- Assignment enforced: `visible_to(..., lock_assignee=True)` (403) **before** `complete_task`
  and before any event row is saved — a worker cannot close someone else's duty by submitting a
  `task_id`; owner passes; role peers pass (intended).
- Recurring-duty mutex: the farm advisory lock is taken before animal locks whenever the linked
  duty recurs (`api/health.py:718-726`), matching the generic routes' FARM -> ANIMAL(s) -> TASK
  order; manual-task creation shares the same namespace/key mutex -> no deadlock between the
  mutex users. Double completion impossible (status re-checked under `with_for_update`;
  `complete_task` itself idempotent for non-PENDING).
- Cross-farm task ids: uniform rejections via `Task.farm_id == farm.id` filters (no oracle, no
  500 at int4 bounds — tested at the ceiling).

### F6 Vaccination schedule — held
- Farm scoping: `db.get(Animal)` then `animal.farm_id != farm.id -> 404` (`api/health.py:961-968`);
  huge ids 404 not 500 (tested).
- Computation bounded: <= 2 index probes per template + earliest-fact anchor + one 500-row
  legacy window (`LEGACY_SCHEDULE_SCAN_LIMIT`); booster math rounds half-up; authoritative
  `next_due` (template + stated authority) overrides cadence; farm timezone via
  `today(farm.timezone)`. No per-event lifetime hydration -> no memory-growth DoS.

### G1 Task board — held
- Five tabs paginate independently with bounded limits/offsets; counts computed order-free on
  subqueries (5 counts + 5 pages, index-backed: `ix_tasks_farm_pending_due_id` etc.).
- Object-level scoping via `task_scope` (`services/tasks.py:823-903`): owner sees all; worker
  sees personal duties + own-role duties + the PENDING-only fallback window while the named
  assignee is inactive/tombstoned. SQL predicate and `visible_to` are parity-tested twins
  (`test_task_visibility_parity.py`).
- Verifiers: **only** the awaiting queue goes farm-wide (`api/tasks.py:257-264, 302-314`);
  today/overdue/upcoming/completed stay assignment-scoped even for `tasks.verify` holders
  (`finished` builds on `scoped`, line 319). Completed-tab predicate runs in SQL before the
  cap; SKIPPED rows order by `skipped_at`. Cross-farm ids never leak (tested).
- `actionable_pending_task_predicate` hides inactive-animal residue without hydrating animals.

### G2 Task detail — held
`api/tasks.py:369-387`: resolves through `task_scope`; missing, cross-farm and
assignment-inaccessible share one 404 (deep links only resolve already-visible rows). Int4
bounds guarded.

### G3 Manual task creation — held
- Capacity: per-farm advisory mutex taken **before** the idempotency claim and count-then-insert
  (`api/tasks.py:542-545`, `services/tasks.py:50-88`) — race-safe at the 5000 boundary; replays
  bypass the count under the same held lock.
- Assignment validation under Farm -> Animal -> Membership -> User -> Role share locks held to
  commit: membership active on THIS farm, user not tombstoned, role live on THIS farm, and an
  explicit role+user pair must match the membership's role (`api/tasks.py:441-519`) — no
  assigning a role the worker doesn't hold; cross-farm ids 400 (no oracle; tested).
- Animal must be ACTIVE (locked `FOR SHARE`, re-checked). `recur_days` in [1, 3650] (0/negative
  422); due-date year band 2000-2100; successor representability checked; title <= 200.
- Manual categories restricted to `FEED/CLEANING/OTHER` — no forged workflow categories, no
  form-link confusion, no movement side effects (complete_task's movement branches require
  `auto_generated`). Self-assign + immediate complete: only CLEANING awaits verification and
  self-verify is blocked (owner exempt, RT-FG-8); FEED/OTHER self-completion has no side
  effects — no verification gaming of consequence.

### G4 Task completion + cascades — held (finding RT-FG-1)
- Assignee-only via `visible_to` under the task lock (role duties completable by any holder of
  the role — intended).
- Form-link rejection list is `task_action_url(task) is not None` — by construction exactly the
  mapping in `_shared.py`; ULTRASOUND/KIDDING_DUE/VACCINE/DEWORMING-with-targets are all
  covered. The only bypass class is linkage-less rows (RT-FG-1).
- Early-completion block server-side: `auto_generated or recurring` duties with
  `due_date > today(farm.timezone)` -> 409.
- Lock order canonical on every path: (advisory FARM when recurring) -> affected ANIMALs in one
  ordered SELECT -> TASK `FOR UPDATE`; the day-45 path additionally locks the batch row to
  keep the shared animal -> batch -> task order at zero occupancy (`api/tasks.py:181-207`);
  verify/reject/skip repeat the order. No inversion found against the animal-first writers
  (status sweeps, health events) — the prior deadlock is documented as fixed.
- Day-45 release gate (`_guard_quarantine_release`, `services/tasks.py:113-182`): duty must
  match the recorded protocol date (so >= day 45 once the early block is included), every sibling
  duty DONE/VERIFIED, no target animal under a hold, and only the locked QUARANTINE cohort
  moves to FOUNDATION.
- WEANING: milestone provenance enforced — the linked kidding must sit exactly
  `weaning_days` before the due date (`_guard_generated_weaning_task`), only that litter's
  RECOVERY kids move, dam rests only when no other dependent kid remains (override-excluding
  check via BucketMove history).
- Recurring successors: anchored at `max(due, today)`; `uq_task_recurring_series_due` +
  `ON CONFLICT DO NOTHING` arbitrate concurrent spawns (no 500); completion/skip are net-zero
  manual-queue transitions; verification-required categories spawn only on their terminal
  transition (verify/skip) so a reject cannot double the cadence (tested). Infinite-chain
  growth bounded (net-zero + 5000 cap + verify-spawn guard).
- Inactive animals: direct completion/skip 409 via `_require_locked_linked_animal_active`
  (sweeps close such rows service-side; residue is hidden from lists).

### G5 Task skip — held (finding RT-FG-4)
- Un-skippable set: batch-linked generated duties while the batch holds any ACTIVE animal
  (lock-free read safe in the only mutable direction — statuses never return to ACTIVE and
  batch headcount is immutable post-creation: purchase creation fixes membership, finance
  correction refuses amount/date changes once animals are allocated and never adds animals);
  generated WEANING/BUCKET_MOVE while any locked animal is in RECOVERY (closed-bucket strand
  prevention — includes kids locked for weaning); generated ULTRASOUND while the breeding
  outcome is PENDING and the doe is active. Early skip blocked for recurring duties; successor
  spawned on skip with the same dedupe.
- Delivery-move (PREGNANCY_LATE -> DELIVERY) duties are skippable, but recoverable:
  `LEGAL_BUCKET_TRANSITIONS` allows the manual context for that edge and kidding is recordable
  from any pregnancy bucket — no stranded state (unconfirmed impact only; not filed).

### G6 Task verify — held (RT-FG-8, RT-FG-10)
- Two-person rule: completer cannot verify own work; owner exempt. PENDING/VERIFIED/non-
  verification-required categories -> 400. Missing `tasks.verify` -> 403 (`require_perm`).
- Concurrent verify+reject: both take the same FARM -> ANIMAL -> TASK lock sequence; the loser
  re-reads status under `with_for_update` and 400s — no double transition, no duplicate
  successor. Successor spawn for manual recurring duties capacity-guarded under the held farm
  lock, with live-successor reuse that bypasses the cap correctly (tested at capacity).

### G7 Task reject — held (finding RT-FG-5)
- Requires `tasks.verify`; only DONE+verification-required rows (400 otherwise — verified
  duties are final); legacy personal-row role fallback repaired before returning to PENDING
  (`resolve_personal_task_role_fallback`, raising 409 when no retained membership role
  remains); completer attribution (`completed_by/at`) retained by design; capacity guarded
  (net +1 PENDING manual row) under the farm mutex; inactive-animal reject 409 (documented,
  tested).

### Cross-cutting — held
- Task farm-scoping is uniform 404 on every path (`_get_task`, `task_scope`, health linked-task
  filters, all FK lookups in creation). No raw SQL beyond DDL `text()`; all LIKE patterns
  escaped; all id lookups int4-bounded (no asyncpg 500s — exhaustively tested at bounds).
- `task_action_url` emits same-origin display paths containing only record ids; it gates the
  UI, never the server (the server fence is the 409 at `api/tasks.py:591` — see RT-FG-1 for
  its one weakness). URL "forgery" only changes what a client renders, not what the API does.
- Schema/CHECK parity audited across health/tasks inputs: every Field bound (lengths, ranges,
  vocabularies, money caps) matches its DB constraint; no 500-shaped input found.
- Idempotency: health-event and task-creation writes are claim-committed atomically with their
  results; failed mutations roll back the claim (no poisoned keys); farm+actor scoped.

---

## Verdict

The F/G surface is heavily hardened and internally consistent; ten prior-audit generations of
concurrency and state-machine reasoning hold up under adversarial re-derivation. No Critical or
High findings. One Medium defense-in-depth gap (RT-FG-1: the form-link fence trusts task
linkage columns that the database does not guarantee) is worth closing with a CHECK constraint
plus a category-based route guard; the remaining findings are policy/audit-quality hardening.
