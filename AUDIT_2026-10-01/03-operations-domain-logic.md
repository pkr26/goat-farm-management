# Operations & Finance Domain Logic Audit (2026-10-01)

Auditor 03 of 10 — angle: business-logic correctness of the operations/finance domains
(feeding, finance, purchases, tasks, planner, buckets, dashboard, notifications, retention,
idempotency, cadence/chronology). All in-scope files were read line-by-line; every finding
below was traced through callers/callees and the model layer before being recorded.

## Executive summary

**Verdict: SOLID WITH TARGETED GAPS.** The operations/finance core is unusually
disciplined: exact `Decimal`/`money()` arithmetic everywhere money is touched (no float
currency math in any write path), largest-remainder allocation for both paise and grams,
DB CHECK constraints backing every domain invariant, `FOR UPDATE` serialization on every
read-modify-write of balances and task state, an audited void-and-replace ledger
correction flow that reconciles denormalized copies, and a genuinely well-engineered
idempotency and notifications dedupe layer. One High finding exists in the notifications
fan-out (same-day alerts keep reaching deactivated workers — the exact class of bug the
digest path was already fixed for), plus two Medium edge-case defects (an insurance duty
born already overdue; digest fan-out not resumable per recipient after a mid-fan-out
crash) and three Low robustness items.

Severity counts: **Critical 0 · High 1 · Medium 2 · Low 3 · Info/positive 9.**

## Findings

### [HIGH] Same-day alert classes keep SMSing deactivated (removed) workers

- Location: `backend/app/services/notifications/service.py:504-550` (`notify_alert_class`),
  contrast `service.py:348-356` (`_digest_text_for_recipient`); lifecycle confirmations at
  `backend/app/api/team.py:1205-1211` (`set_worker_status`) and
  `backend/app/models/notifications.py:62-67`.
- Evidence (`notify_alert_class` recipient selection — farm + opt-in only):
  ```python
  recipients = list(
      (
          await db.execute(
              select(NotificationRecipient).where(
                  NotificationRecipient.farm_id == farm.id, column.is_(True)
              )
          )
      )
      .scalars()
  )
  ```
  versus the digest's own guard (added by the 2026-09-29 audit pass):
  ```python
  select(FarmMembership, User)
  .join(User, FarmMembership.user_id == User.id)
  .where(
      FarmMembership.id == recipient.membership_id,
      FarmMembership.farm_id == farm.id,
      FarmMembership.is_active.is_(True),
      User.deleted_at.is_(None),
  )
  ```
- Verification: worker removal is `membership.is_active = payload.is_active` + commit
  (`team.py:1205-1211`) — the `NotificationRecipient` row survives (its FK CASCADE fires
  only on a hard membership delete, which no route performs; account tombstoning also only
  deactivates memberships, see `deps.deactivate_deleted_user_memberships`). Fan-out callers
  confirmed: `emit_alert` (api/health.py, api/screening.py, api/animals.py) and the
  background sweeps `overdue_critical_sweep`, `kidding_watch_daily`, `feed_reorder_daily`
  (main.py:473-477) all route through `notify_alert_class`.
- Impact: a removed worker's phone keeps receiving paid SMS about farm operations —
  including `MOVEMENT_RESTRICTION` (scheduled-disease suspicion) and `SCREENING_FLAG`
  alerts — indefinitely after deactivation, consuming the farm's daily SMS cap budget and
  disclosing operational/clinical information to someone no longer on the team. Confirmed
  (deterministic; no crash or race needed).
- Fix: mirror the digest's membership-active + user-tombstone check inside
  `notify_alert_class`'s recipient query (join `FarmMembership`/`User` on
  `recipient.membership_id`, skip inactive/tombstoned recipients the same way the digest
  returns None), or purge/deactivate recipient rows on membership deactivation.

### [MEDIUM] Insurance renewal duty is spawned already overdue for horizons under 30 days

- Location: `backend/app/services/finance.py:185-208` (`_spawn_renewal_task`), called at
  `finance.py:324-325` (creation) and `finance.py:400-401` (renewal).
- Evidence:
  ```python
  due = policy.renewal_date - timedelta(days=INSURANCE_RENEWAL_LEAD_DAYS)
  await _add_task(
      db, farm_id, f"Insurance renewal due: policy {policy.policy_number}", due, ...
  )
  ```
  with the caller guard only excluding horizons that have already arrived:
  ```python
  if renewal_date > reference:
      await _spawn_renewal_task(db, farm.id, policy)
  ```
  while the helper's own docstring states the intended contract:
  "A renewal date that has already arrived gets no task: the 30-day lead has passed, and a
  backdated duty would only bury the register's real work."
- Verification: `InsurancePolicyIn` allows `start_date = today`, `renewal_date = today+14`;
  the register is append-only and BIZ-3 caps the span at five years but sets no minimum.
  For any horizon ≤ 30 days, `due = renewal_date − 30d` lies in the past, so the guard
  (`renewal_date > reference`) still fires and an auto-generated INSURANCE duty is created
  overdue by `(30 − horizon)` days — precisely the "backdated duty" the docstring rules
  out. Both the create and renew paths share the defect. Confirmed (deterministic).
- Impact: short-horizon policies (and every renewal made inside the final 30 days of a
  lapsed-but-still-renewable window) mint immediately-overdue duties, polluting the
  overdue tab and the OVERDUE_CRITICAL sweep from day one; the overdue count overstates
  real work.
- Fix: gate the spawn on the lead itself — `if policy.renewal_date - timedelta(days=INSURANCE_RENEWAL_LEAD_DAYS) > reference:` — matching the documented intent (or clamp `due` to `reference` if a same-day nag is wanted).

### [MEDIUM] Digest fan-out is not resumable per recipient after a mid-fan-out crash (conditional)

- Location: `backend/app/services/notifications/service.py:474-483`
  (`farms_ready_for_digest` settled-check) and `service.py:413-435`
  (`run_digest_for_farm` loop).
- Evidence:
  ```python
  settled_today = (
      select(NotificationLog.id)
      .where(
          NotificationLog.farm_id == Farm.id,
          NotificationLog.alert_class == "DAILY_DIGEST",
          NotificationLog.local_date == local_date,
          NotificationLog.status != "SKIPPED_QUIET",
      )
      .exists()
  )
  ```
  while each recipient's outcome commits individually inside the fan-out loop
  (`send_notification` → `settle` → `await db.commit()` per recipient).
- Verification: the day-dedupe unique key is `(farm, recipient, class, payload, local
  day)` — per recipient — so re-running the whole farm after a partial failure would be
  safe and idempotent (already-served recipients answer `fresh=False`). But the readiness
  gate settles the **farm** on any single recipient's settled row: if the process dies or
  the loop iteration raises after recipient 1 of 5 settled SENT (the loop has no per-farm
  try/except; the outer tick `except Exception` in `main.py:480` just logs), the farm is
  "done" for the day and recipients 2-5 receive no digest. Conditional (requires a crash
  between recipients); consequence is a missed paid digest, never a duplicate.
- Impact: opt-in workers silently miss the morning digest for that day; the failure mode
  is invisible (only the tick-level log line).
- Fix: make the readiness gate per-recipient — e.g. `NOT EXISTS (settled row for this
  farm JOIN recipients with daily_digest)` — or wrap `run_digest_for_farm`'s per-farm call
  so a partial failure does not mark the farm settled (the per-recipient dedupe already
  makes re-entry safe).

### [LOW] Digest SMS undercounts a worker's duties beyond ten

- Location: `backend/app/services/notifications/service.py:362-381`.
- Evidence:
  ```python
  titles = list(
      (
          await db.execute(
              scoped.with_only_columns(Task.title).order_by(Task.due_date, Task.id).limit(10)
          )
      ).scalars()
  )
  ...
  parts = [f"Herdly {reference.isoformat()}: {len(titles)} duties today"]
  ```
- Impact: a worker with 25 due duties is told "10 duties today" — the headline count is
  the capped sample, not the true total. Display-only (no ledger/data effect) but it
  misstates the workload the digest exists to summarize. Confirmed.
- Fix: `select(func.count())` over the same scoped predicate for the headline, keep the
  LIMIT for the body lines.

### [LOW] Bakrid advisory mixes clamped and unclamped month arithmetic

- Location: `backend/app/services/dashboard.py:61-77`.
- Evidence:
  ```python
  window_start = add_months(festival, -BAKRID_HOLD_WINDOW_MONTHS)
  effective_dob = func.coalesce(Animal.date_of_birth, Animal.estimated_dob)
  finish_date = cast(
      effective_dob + func.make_interval(0, MEAT_SALE_AGE_MONTHS[1]),
      Date,
  )
  ```
- Verification: `utils.add_months` clamps to month end (May 31 − 2 months → Mar 31),
  while PostgreSQL `make_interval(0, 9)` adds months without clamping (May 31 + 9 →
  Mar 2/3 after day-overflow). The two edges of the same window therefore use different
  calendar semantics. Conditional on boundary-day births/festival dates; advisory-only
  (a count in a dashboard hint, no write path).
- Impact: a male born on a month-end day can be counted in/out of the hold window
  inconsistently with the Python twin used elsewhere.
- Fix: compute `finish_date` with a clamped SQL month-add equivalent to `add_months`
  (or compute both edges in one engine).

### [LOW] Cadence rounds can be suppressed by a manually titled duty

- Location: `backend/app/services/cadence.py:334-341` (exact-title dedupe for the feed
  routine / water check) and `cadence.py:420-427` (prefix dedupe for reorder alerts).
- Evidence:
  ```python
  already = await _task_exists(
      db, farm_id,
      Task.category == TaskCategory.FEED.value,
      Task.status == TaskStatus.PENDING.value,
      Task.title.ilike(_prefix_pattern(f"Reorder {item.ingredient}:"), escape="\\"),
  )
  ```
- Verification: manual duties may use category FEED (`ManualTaskCategoryStr = Literal["FEED",
  "CLEANING", "OTHER"]`), so an operator-titled "Reorder Maize: …" (or an exact copy of the
  morning-routine title) suppresses the auto round for as long as it stays PENDING.
  Requires deliberate/exact title collision. Confirmed code path, low likelihood.
- Impact: a legitimate auto alert silently never materializes while the colliding manual
  duty is open.
- Fix: key the dedupe on `title_key` (server-owned, never client-supplied) instead of the
  free-text title, as the recurring-series dedupe already does.

### [INFO] Positive observations / non-findings

1. **Money math is exact end-to-end.** Every currency write passes through
   `Decimal`+`money()` (ROUND_HALF_UP, via `str` so float noise never enters the ledger);
   `allocate_money` distributes whole paise with `divmod` so per-head purchase shares sum
   exactly to the booked expense (`services/purchases.py:157-162`); monthly P&L totals are
   rounded with the ledger's own `money()` (`services/finance.py:143-150`); the feed
   Hamilton allocator (`_allocate_recipe_grams`) and `_shift_quantities` preserve exact
   gram totals with deterministic tie-breaks. No float arithmetic on money was found in
   any persisted path; wire-level `float(Decimal)` conversions are documented legacy
   contract and exact-to-the-cent well beyond the DB's ₹1e9 per-row cap.
2. **Stock can never go negative.** DB CHECKs (`ck_feed_inventory_qty`,
   `ck_finished_feed_qty_nonneg`) plus service-side `FOR UPDATE` re-reads that refuse
   shortages atomically; mixes lock ingredient rows in canonical name order (deadlock
   avoidance); the finished-stock upsert adds to the committed balance in SQL.
3. **The ledger correction flow is a model of care.** Void-and-replace with a partial
   unique index on the active source pair (no double-booking of system-generated rows),
   source-specific reconcilers that keep the animal/batch/feed-inventory denormalized
   copies in step, a deterministic (date, source_id) latest-purchase ordering, refusal to
   drive stock negative, and flush-void-then-insert to satisfy the index.
4. **Task state machine has no illegal transitions.** `FOR UPDATE` on the task row plus
   pre-locked animals in canonical order; PENDING-only entry points; two-person
   verification rule with owner exemption; verification-required series spawn successors
   only on terminal transitions (prevents cadence doubling); quarantine/RECOVERY/ultrasound
   skip gates prevent permanent dead-ends; recurring spawn dedupes via
   `(farm, series, due_date)` with `ON CONFLICT DO NOTHING`.
5. **Chronology/cadence primitives are calendar-correct.** `add_months` clamps to month
   end; no week/DST arithmetic anywhere (farm-local business dates via `today(tz)`); the
   cadence sweep is advisory-lock serialized, backfill-bounded, and per-farm
   fault-isolated; `_estimated_dob_from_age` interpolates fractional months over actual
   calendar anchors to stay monotonic across Februaries.
6. **Dashboard aggregations are window-correct and permission-honest.** Exact counts via
   uncorrelated scalar subqueries (not window drains), zero-safe averages/rates, the
   withheld-as-`None` (never zero) convention for permission gates, and matching
   twin predicates between SQL and Python (age months, task visibility) with dedicated
   parity tests referenced.
7. **Idempotency is production-grade.** Claim-before-mutation committed atomically with
   the response, `ON CONFLICT` arbitration for concurrent same-key requests, retention
   re-evaluated after lock waits, per-actor standing quota that never blocks replays,
   HMAC fingerprints for password-bearing operations, stale-cache 409 on schema drift.
8. **Retention deletes are FK-safe and isolated.** Child-first bounded batches with
   `SKIP LOCKED`, root-anchored eligibility (an aged image takes its young children), no
   FK anywhere targets `tasks.id` (verified) so terminal-duty deletion cannot orphan
   rows, and per-farm rollback/continue on failure.
9. **Simulation-family routers (router level only)** release the auth transaction before
   CPU work, price admission by actual cost (including birth amplification and gap-closing
   passes), and hard-422 non-finite results; scenario/plan CRUD uses optimistic revision
   checks plus advisory-lock-serialized quotas.

## Coverage manifest

| File | Status |
|---|---|
| backend/app/api/feeding.py (374) | full |
| backend/app/api/finance.py (1109) | full |
| backend/app/api/purchases.py (318) | full |
| backend/app/api/tasks.py (930) | full |
| backend/app/api/planner.py (545) | full |
| backend/app/api/buckets.py (148) | full |
| backend/app/api/dashboard.py (775) | full |
| backend/app/api/simulation.py (724) | full (router level; engine internals out of scope per brief) |
| backend/app/api/ops_simulation.py (142) | full (router level) |
| backend/app/services/feeding.py (737) | full |
| backend/app/services/finance.py (671) | full |
| backend/app/services/purchases.py (258) | full |
| backend/app/services/tasks.py (992) | full |
| backend/app/services/dashboard.py (347) | full |
| backend/app/services/cadence.py (612) | full |
| backend/app/services/chronology.py (203) | full |
| backend/app/services/retention.py (302) | full |
| backend/app/services/idempotency.py (481) | full |
| backend/app/services/notifications/__init__.py (43) | full |
| backend/app/services/notifications/hooks.py (55) | full |
| backend/app/services/notifications/providers.py (132) | full |
| backend/app/services/notifications/service.py (691) | full |
| backend/app/services/_common.py (176) | full |
| backend/app/services/__init__.py (278) | full |

**100% of in-scope files read completely.** Supporting context read for verification (not
audit targets): `models/{finance,purchases,tasks,feeding,notifications,idempotency,planner,enums,helpers}.py`,
`models/constants.py` (SHIFT_SPLIT/VERIFICATION categories), `schemas/{finance,tasks,feeding,common}.py`,
`utils.py`, `deps.py`, `db.py`, `api/_shared.py`, `api/_run_limits.py`, `api/team.py`
(membership lifecycle + notification prefs), `main.py` (background loops).
Excluded per instructions: AUDIT_REPORT_2026-09-28.md, AUDIT_AND_IMPLEMENTATION_PLAYBOOK.md,
MUTATION_TESTING_REPORT.md, AUDIT_2026-10-01/ (concurrent auditors), README (orientation only).
