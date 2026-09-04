# Red Team Audit #2 — "The Injector" — Injection, Query Construction, Schema & Migrations

**Date:** 2026-09-04 · **Method:** read-only adversarial code audit — full sweep of `backend/app` (api/, core/, models/, schemas/, services/, simulation/), `backend/alembic`, `backend/scripts`, error handlers. Every vector in mandate attempted and verified in code.

## Top findings

### 1. Unbounded `idempotency_records` accumulation vs. throughput-capped purge — LOW
`backend/app/services/idempotency.py:299-313` (claim insert), `backend/app/core/config.py:125-128` (retention/purge settings)

Attack: an authenticated worker with any mutate permission (e.g. `finance.manage`) loops `POST /api/finance/new` (or any idempotent mutation) with a fresh `Idempotency-Key` per request and a slightly different note. Each successful call inserts one `idempotency_records` row carrying a full JSONB `response_body`, retained 7 days. There is **no per-actor/per-farm record quota** anywhere (only key charset/length at `MAX_IDEMPOTENCY_KEY_LENGTH = 128`). The purge loop removes at most `idempotency_cleanup_batch_size × idempotency_cleanup_max_batches` per hour (default 500 × 10 = 5,000 rows/hr).

Impact: sustained writes faster than 5k/hr grow the shared multi-tenant table (plus WAL/index bloat) for at least the retention window; the mutation itself also creates domain rows, but the idempotency row roughly doubles per-request storage and is pure overhead a client opts into per header. Authenticated-only, storage pressure — hence Low.

Evidence: `pg_insert(IdempotencyRecord).values(...).on_conflict_do_nothing()` with no count guard; grep for quota (`max`, `capacity`, `limit` near idempotency) returns nothing but key length.

**Fix:** reject new claims when an actor/farm already holds N unexpired idempotency rows (cheap indexed count), e.g. 1,000.

### 2. `TaskCreateIn.due_date` has no sane-date floor/ceiling — LOW
`backend/app/schemas/tasks.py:34` (`due_date: date`)

Attack: `POST /api/tasks` with `{"title":"x","due_date":"0001-01-01"}` (or `9999-12-31`). Contrast with `PurchaseBatchIn._not_ancient` (`schemas/purchases.py:36-41`) which rejects `year < 2000`.

Impact: a task permanently stuck in the "overdue" tab and poisoned dashboard aggregates; no overflow/500 (the recurrence validator at `tasks.py:42-47` correctly blocks `date.max` arithmetic). Data-hygiene only — no injection, no crash.

**Fix:** add the same year-≥-2000 (and reasonable future ceiling) validator used for purchases.

**No Critical/High/Medium findings.** No injection, query-construction, deserialization, or constraint-bypass exploit could be constructed against this data layer.

## Vectors attempted and properly defended

1. **Raw SQL / string-built SQL:** only three `text()` uses in the live app — `app/main.py:529` (`SELECT 1`), `app/seed.py:520` (advisory lock constant), plus static index predicates/server defaults in models. All migrations use static literals (trigger functions in `d3b5f7c9e024` are fixed strings). **Zero** f-string/`%`/`.format`/concat into SQL with client data; no raw asyncpg anywhere (engine is `create_async_engine`, `app/db.py:29-43`).
2. **Dynamic sort/filter/operator injection:** no `sort`/`filter`/`include` DSL exists. Every list filter is a `Literal` enum (`TransactionTypeStr` finance.py:23, `BucketStr` animals.py:25, `shift` milk.py:80 with `pattern="^(MORNING|AFTERNOON|NIGHT)$"`); every `order_by` is a server-authored column tuple (finance.py:559, animals.py:300, dashboard `_side`). LIKE searches escape wildcards and bind the pattern (`api/animals.py:288-294`, `api/health.py:88-91` with `escape="\\"`).
3. **Pydantic coercion/bounds abuse:** `StrictInputModel(extra="forbid")` base (`schemas/common.py:29-37`); `StrictInt`/`StrictBool`/`FiniteFloat` with `Field(strict=True)` so `"12.5"`/`true` can't become numbers; money capped at ₹1e9 with paise rounding (`MoneyFloat`), kg at 1e6, weight at 1000; NUL/control chars and unpaired UTF-16 surrogates rejected before asyncpg (`_postgres_text`, common.py:91-113); `9999-12` month filter handled (`api/finance.py:533-546`); non-finite simulation results 422'd (`_finite_payload`).
4. **JSONB/column abuse:** the only JSONB column is `IdempotencyRecord.response_body` (`models/idempotency.py:77`) — server-authored from validated `BaseModel.model_dump()`, never client JSON; stored simulation/planner assumptions are validated models revalidated on read with a graceful 422 (`api/simulation.py:92-109`); `Role.permissions` fail-closed on corrupt shape (`models/core.py:166-183`); request bodies capped at 1 MiB with a streaming receive guard (`main.py:72-138`).
5. **Error/exception leakage:** unhandled exceptions return an opaque `{"detail":"Internal server error"}` 500 with the traceback only logged (`main.py:476-507`); 422s strip Pydantic's `input` echo (`main.py:439-455`); every `detail=str(exc)` traced catches narrow domain types (`ValueError`/`LitterSizeError`/`ManualTaskCapacityError`) with code-authored messages — never driver/DB exceptions; no secrets in logs (IPs/ids only); no `pickle`/`eval`/`yaml.load` anywhere.

## Verdict on the data layer

**Clean.** A fully ORM-bound, parameterized data layer with typed enums at every filter boundary, strict Pydantic input models, Decimal-backed money columns, and a migration chain that adds (never drops) constraints — unique tag/email/membership/idempotency scopes, tenant-composite FKs, and domain check constraints are all present. The codebase shows clear evidence of prior adversarial audit rounds (`audit_reports/2026-09-01..03` with remediation logs), and this independent injection sweep confirms those fixes hold. The two residual findings are capacity/hygiene Lows, not injection vulnerabilities: **no exploitable SQL/operator injection, unsafe deserialization, or schema weakness exists in the current data layer.**
