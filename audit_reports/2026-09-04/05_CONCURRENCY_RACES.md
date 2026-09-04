# Red Team Audit #5 — "The Race Runner" — Concurrency, Races & Async Correctness

**Date:** 2026-09-04 · **Method:** read-only adversarial code audit (no requests actually fired — reasoning from code + DB constraints). Persona: attacking by sending two requests at the same time.

## TOP FINDINGS

### 1. POST /api/breeding and POST /api/kidding accept no Idempotency-Key — client sends one the backend silently drops — MEDIUM
`backend/app/api/breeding.py:214-221`, `backend/app/api/kidding.py:194-201`, `frontend/src/lib/idempotent-request.ts:184-191`

Interleaving: (1) Client POSTs `/api/kidding` with `Idempotency-Key: K`; (2) server commits kidding + kid animals + tasks, response lost at socket; (3) `executeWithOneNetworkRetry` (`idempotent-request.ts:334-344`) re-POSTs the identical body/key; (4) FastAPI ignores the undeclared header, `br.kidding_record is not None` → **409 "already kidded" for an operation that succeeded**. Same for breeding via `uq_breeding_open_pregnancy` → 409. No duplicate data is possible (constraints below), but a committed mutation surfaces as a conflict error the operator cannot distinguish from a real one. The frontend itself admits it: *"Server-side dedup needs the backend param (tracked)"* (`idempotent-request.ts:187-188`) and `frontend/src/test/adversarial/adv-D-transport.test.ts:53-56`.

Evidence: `async def create_breeding(payload, db, user, farm, perms) -> BreedingRecordOut:` — no `idempotency_key: IdempotencyKey` parameter, no `execute_idempotent` call.

**Fix:** add the `IdempotencyKey` param + wrap both creates in `execute_idempotent` (operation `POST /api/breeding` / `POST /api/kidding`).

### 2. POST /api/feeding/dispense: keyless replay double-debits feed stock — no DB natural key — MEDIUM
`backend/app/services/feeding.py:589-621`, `backend/app/api/feeding.py:132-189`

`feeding_records` has no unique (farm, bucket, date, shift) (`models/feeding.py` — only `uq_feed_inventory_farm_ingredient` / `uq_finished_feed_farm_recipe` exist), and `execute_idempotent`'s keyless path just runs `mutate` (`services/idempotency.py:283-291`).

Interleaving: A POST dispense 20 kg ‖ B identical POST. A: `SELECT ... FOR UPDATE` on `FeedFinishedStock` → 20→10, insert record, commit. B: acquires the lock after A, sees 10 ≥ 20? (if stock allows) → double-debits and inserts a **second** FeedingRecord. FOR UPDATE prevents the lost update but not the duplicate booking. Protected only by the frontend registry (allowlist `idempotent-request.ts:191`); curl/integration replays commit twice.

**Fix:** the client contract should require the key for stock mutations (422 when absent), or add a natural-key uniqueness policy for dispense.

### 3. POST /api/finance/new: keyless replay double-books money — no DB natural key on transactions — MEDIUM
`backend/app/api/finance.py:649-704`

Same class as #2: `mutate()` appends a `Transaction` row; the only constraint is the provenance partial unique `uq_transactions_active_source` (farm_id, source_type, source_id), which is NULL for manual entries. Two identical keyless POSTs → two identical ledger rows (the milk-litres fence at :664-666 only guards `milk_litres` payloads, and only against parlour production). The frontend registry covers the official client (`/api/finance/new` is allowlisted, `idempotent-request.ts:175`); nothing backstops a non-registry client.

**Fix:** require Idempotency-Key on money-creating POSTs server-side (reject keyless), since no natural key exists.

### 4. /api/milk/new missing from the frontend idempotency allowlist although the backend fully supports it — LOW
`frontend/src/lib/idempotent-request.ts:170-195` vs `backend/app/api/milk.py:175, 295-307`

Backend declares `idempotency_key: IdempotencyKey` and wraps in `execute_idempotent`; the client allowlist omits the path, so the official client never sends the key and gets no single-network-retry coalescing. Accidental double-submit (both fire before `disabled={submit.pending || addRecord.isPending}`, `milk/page.tsx:386`): A commits 201; B hits `record_milk`'s "already recorded; correcting requires a correction_reason" → 422 error toast. Data safe via `uq_milk_records_animal_day_shift`; UX-only false error.

**Fix:** add `path === "/api/milk/new"` to `isIdempotencyProtectedMutation`.

### 5. Multi-process guard for in-memory admission controls is warning-only and blind to launchers that don't set the env — LOW
`backend/app/main.py:322-336`

`workers_env = os.environ.get("UVICORN_WORKERS", "1")` — a bare `uvicorn app.main:app --workers 4` sets neither `UVICORN_WORKERS` nor `WEB_CONCURRENCY`, so the default "1" passes and the warning never fires while 4 processes each run the auth limiter, simulation CPU budget (`_run_limits.py:5-8` "singletons are per-process by design"), and worker-idempotency gates (`team.py:104-108`). No data-integrity impact (PostgreSQL arbitrates every mutation); throttles/budgets multiply N×. The code itself concedes "Detecting the actual worker count is not reliably possible from inside the process." *(Cross-referenced by Audit #1 finding 5.)*

**Fix:** none available in-process; enforce `--workers 1` at the orchestrator/entrypoint (e.g., fail in the Docker CMD wrapper).

## RACES CHECKED AND PROPERLY BACKSTOPPED

1. **Create-animal tag race**: `uq_animal_tag_per_farm` (`models/animals.py:39`; migration `ed5efe13a516:229`) + savepoint retry loop and mapped `IntegrityError`→400 (`api/animals.py:442-582`, constraint names checked at :572-576). Auto-tag loser retries once with a fresh random tag.
2. **Milk (animal, date, shift) double-submit**: animal row locked `FOR UPDATE` *before* the upsert (`api/milk.py:197-203`) serializing same-milking writers; `uq_milk_records_animal_day_shift` (`models/milk.py:40`; migration `b3d7f1a5c9e2:131`) makes the loser a correction, never a second row.
3. **"Invitation accepted twice → two owners"**: no invitation flow exists; workers are owner-provisioned under `pg_advisory_xact_lock` farm mutex (`team.py:605-621`), `uq_membership_user_farm` (`models/core.py:192`), `users.email` unique with concurrent-register `IntegrityError`→400 (`auth.py:728-733`).
4. **Task complete/verify recurring spawn**: Task `FOR UPDATE` + status re-check (`api/tasks.py:566-618`) so the loser 400s "not pending"; successor spawn uses `ON CONFLICT DO NOTHING (uq_task_recurring_series_due)` then re-selects the winner (`services/tasks.py:749-784`; migration `c8f1d3a5c709:233`); manual-queue capacity count serialized by `pg_advisory_xact_lock` (`services/tasks.py:50-67`).
5. **Finance correction double-apply + refresh rotation**: original row `FOR UPDATE` + `voided_at` re-check → 409 "already corrected" (`api/finance.py:735-745`) plus `uq_transactions_active_source` partial unique (migration `c8f1d3a5c709:180-189`); refresh takes User→RefreshSession `FOR UPDATE` and re-checks `consumed_at` with a bounded reuse-grace replay (`auth.py:887-997`). Feeding stock lost-updates additionally covered by canonical ingredient-order `FOR UPDATE` (`services/feeding.py:438-451`) and the finished-stock `on_conflict_do_update` additive upsert (:466-484).

## VERDICT

Concurrency safety is **high — well above typical for this stack**. Every check-then-act attacked is guarded by `SELECT ... FOR UPDATE` under documented canonical lock orders (Farm advisory → Animals ascending id → Task/Breeding), lost updates on stock prevented by row locks, balances are SQL aggregates rather than stored counters, `get_db` yields one session per request (`db.py:72-74`, `autoflush=False, expire_on_commit=False` with pitfalls documented at each use site), and background loops open a fresh session per batch (`main.py:174, 204, 230`). A durable, transaction-bound idempotency framework with a DB unique index as arbiter (`services/idempotency.py:298-395`) covers the money/stock POSTs — but it is **opt-in per endpoint and per client**: breeding/kidding never wired it (#1), and dispense/finance-manual have no DB natural key behind it (#2, #3). Those three are the only exploitable double-commit surfaces, and #1 cannot corrupt data — only lie to the user with a 409.
