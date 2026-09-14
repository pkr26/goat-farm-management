# Red Team Audit — Part M units M5–M11: Seeding, Repair, Loops, Metrics, Handlers, Boot Validators, Settings

Date: 2026-09-13
Target: `/Users/pavankumarreddyreddem/Desktop/goat_saas` (backend `backend/app/`)
Units in scope: **M5** startup seeding, **M6** legacy repair batching, **M7** five background
loops, **M8** metrics + probes, **M9** exception handlers & baseline headers, **M10**
production boot validators, **M11** settings surface.

## Methodology

Every implementation was read end-to-end and attacked per the hypotheses in the scope brief:
concurrency/race analysis of seeding (advisory locks, ON CONFLICT winners, FOR UPDATE
mutexes, savepoints, lock ordering between repair/creation/request paths), poison-row and
shutdown analysis of all five loops, exposure analysis of `/metrics`//`healthz`//`readyz`,
reflection/leak analysis of every error-response path (including middleware short-circuits
and the 500 path above the outermost user middleware), complete enumeration of
`Settings._production_safety` with a gap hunt for what is *not* validated, and black-box
verification of settings/env parsing edge cases against the pinned dependency set
(pydantic 2.13.4, pydantic-settings 2.14.2, FastAPI 0.141.1, Starlette 1.4.1,
uvicorn 0.52.1). Middleware response-header coverage was verified empirically with an
ASGI transport (bad Host → 400, oversized target → 414, declared oversized body → 413,
404 — all confirmed to carry `X-Request-ID` + baseline headers). Migration interactions
were checked against the full Alembic chain. Tests in `test_ops.py`, `test_metrics.py`,
`test_inactive_animal_task_cleanup.py`, `test_online_index_recovery.py`,
`test_ops_migration_integrity.py`, `test_deployment_artifacts.py` and
`test_concurrency.py` were reviewed for what they pin.

Sibling findings cross-referenced, **not** re-reported: RT-M-3 (unbounded Prometheus
`method` label), RT-M-4 (log forging via percent-decoded path — independently re-verified
here), RT-M-6 (multi-worker detection warn-only), RT-A-3 (JWT key file permissions).

---

## Findings

| ID | Severity | Unit | Title |
|----|----------|------|-------|
| RT-M2-1 | **Medium** | M11/M10 | Misspelled `GOATFARM_*` **environment variables** are silently ignored — `extra="forbid"` does not cover the env-var source, so production config typos revert knobs to defaults |
| RT-M2-2 | Low | M10 | Production boot validators omit `metrics_enabled`: unauthenticated `/metrics` is on by default in production while docs are force-disabled |
| RT-M2-3 | Low | M7/M8 | Maintenance-loop observability asymmetry: only the refresh-session loop exports Prometheus counters; four loops are log-only |
| RT-M2-4 | Info | M6 | Legacy farm repair counts claimed-not-healed farms; persistently conflicting farms are re-claimed every pass and can consume batch slots |
| RT-M2-5 | Info | M5 | Seed/migration mutual exclusion is advisory and lock-ID-disjoint; safety rests on ON CONFLICT + guarded DML + compose ordering, not on a shared lock |
| RT-M2-6 | Info | M10 | No production cross-checks for TTL sanity, app-vs-edge body-limit drift, or trusted-proxy/deployment consistency |

Counts: **0 Critical, 0 High, 1 Medium, 2 Low, 3 Info.** No cross-tenant impact, no
authz bypass, no loop-death path, no seeding-corruption path was found in these units.

---

### RT-M2-1 (Medium, M11/M10) — `extra="forbid"` does not reject unknown `GOATFARM_*` environment variables

**Evidence.** `backend/app/core/config.py:87-89` sets
`SettingsConfigDict(env_prefix="GOATFARM_", env_file=BACKEND_DIR / ".env", extra="forbid")`,
with the stated intent (comment at 84-86): *"Fail fast on misspelled keys in backend/.env
and direct Settings(...) construction."* That guarantee is real for the two sources it
names — empirically verified on the pinned dependency set:

- unknown key in `backend/.env` → `ValidationError` at boot (refused);
- unknown kwarg to `Settings(...)` → `ValidationError` (refused);
- unknown `GOATFARM_`-prefixed **OS environment variable** → **silently ignored, boot
  succeeds** (verified: `GOATFARM_UNKNOWN_KNOB=1` and `GOATFARM_BACKUP_GPG_RECIPIENT=x`
  exported → `Settings()` constructs cleanly). pydantic-settings' `EnvSettingsSource`
  only collects variables that match declared field names, so `extra="forbid"` never
  sees the stray ones.

This matters because **production injects configuration through real environment
variables**, not through `backend/.env` (`docker-compose.yml` backend service, lines
96-131: `GOATFARM_CORS_ORIGINS`, `GOATFARM_ALLOWED_HOSTS`, `GOATFARM_TRUSTED_PROXY_HOSTS`,
`GOATFARM_MIN_PASSWORD_LENGTH`, …).

**Exploit sketch (misconfiguration, not attacker-driven).** An operator hardens a
deployment by setting `GOATFARM_AUTH_RATE_LIMIT_MAX_ATTEMPTS=5`,
`GOATFARM_ARGON2_TIME_COST=6`, `GOATFARM_ACCESS_TOKEN_TTL_SECONDS=300`, or disables
metrics via `GOATFARM_METRICS_ENABLED=flase` (typo). The variable does not match any
field; the app boots green and silently runs the defaults (10 attempts / 300 s, time
cost 3, 30-min tokens, metrics on). Nothing in the logs indicates the knob was dropped.

**Impact.** The fail-closed `_production_safety` floor catches the *most* dangerous
reverts (dev-fallback HMAC, `cookie_secure=false`, loopback CORS/hosts, password floor
<12, non-verify-full DB TLS all refuse boot). But every knob whose default *passes* the
production floor degrades silently on typo: Argon2 time cost (default 3 ≥ floor 2),
rate-limit attempts/window, access/refresh TTLs, `max_request_body_bytes`,
`trusted_proxy_hosts` (falls back to the compose-injected edge IP — or to `""` outside
compose, collapsing all per-IP auth ceilings onto one address), `metrics_enabled`,
`refresh_reuse_grace_seconds`, pool sizes. This is a config-integrity hole in exactly
the surface (`Settings`) whose design goal is "silently ignoring a security setting is
more dangerous than refusing to boot."

**Fix.** Add a boot-time audit that enumerates `os.environ` keys with the `GOATFARM_`
prefix, diffs them against the model's known field names (plus an explicit documented
allow-list for the out-of-band backup/restore tooling namespace `GOATFARM_BACKUP_*` /
`GOATFARM_RESTORE_*` used by `backend/scripts/backup.sh` / `restore.sh` per
`README.md:609-611`), and refuses to boot on unknown names — or at minimum logs a
prominent warning naming the ignored variable. A regression test should set
`GOATFARM_MIN_PASSWORD_LENGHT=20` (typo) in the environment and assert the boot
refusal/warning.

**Note.** The flip side was also verified: because stray env vars are ignored (not
rejected), exporting `GOATFARM_BACKUP_*` variables into the API process's environment
does **not** crash the app — the "backup exports vs `extra=forbid` collision" hypothesis
from the scope brief is disproven for the env source (it *would* collide only if such
keys were written into `backend/.env`).

---

### RT-M2-2 (Low, M10) — Production validators omit `metrics_enabled`; unauthenticated `/metrics` defaults ON in production

**Evidence.** `backend/app/core/config.py:258` — `metrics_enabled: bool = True` with no
`_production_safety` check (`config.py:479-624` never mentions it). `backend/app/main.py:585-587`
force-disables `/docs`, `/redoc`, `/openapi.json` in production specifically because they
"disclose the full API surface", while `main.py:681-690` registers unauthenticated
`GET /metrics` whenever `metrics_enabled` — i.e. by default in every production boot.

**Exploit sketch.** In the shipped topology this is unreachable (edge routes only
`/api/` to the backend, `docker-compose.yml:283-296`; backend is `expose:`-only,
line 133). But the entire premise of `_production_safety` is to catch the class of
misdeployment where the app is reachable on a port/edge-path the operator did not
intend. Under any such exposure (published port, edge misroute, `network_mode: host`,
a second proxy), an unauthenticated scraper obtains per-route-template request rates,
status-code distribution (including 401/403/429 volumes — a free brute-force-progress
oracle), auth rate-limit rejection counts by scope, idempotency replay counts, and
simulation admission rejection reasons (`backend/app/metrics.py:33-72`) — with no
credentials and no log trail.

Content itself was verified clean: the endpoint renders only the six collectors on the
dedicated `CollectorRegistry` (`metrics.py:27,112-114`) — no default process/CPU/env
collectors, no secrets; route labels are templates, never raw paths (pinned by
`test_metrics.py:87-108`). The gap is the default-on posture in production, not the
payload.

**Impact.** Information disclosure / reconnaissance aid under a deployment mistake that
the boot validator family is designed to preempt. (Label-cardinality caveat for `method`
is sibling finding RT-M-3.)

**Fix.** In `_production_safety`, require `metrics_enabled` to be an explicit
environment decision in production (unset → refuse boot or default off with the README
observability section referenced), mirroring the docs treatment.

---

### RT-M2-3 (Low, M7/M8) — Four of five maintenance loops have no metrics; failures are log-only

**Evidence.** Only the refresh-session loop records telemetry:
`backend/app/main.py:180` calls `metrics.record_refresh_session_purge_batch(removed)`
(collectors at `metrics.py:63-72`). The idempotency purge loop (`main.py:194-216`),
legacy repair loop (`main.py:219-252`), inactive-animal task cleanup (`main.py:255-283`),
and deleted-membership deactivation (`main.py:286-314`) emit only INFO logs on progress
and `logger.exception` on failure.

**Exploit sketch.** Any condition that makes one of these loops fail every pass (a
schema/behavior regression after an upgrade, a persistent deadlock victim, a poison
write pattern) is invisible to Prometheus-based alerting; the only signal is recurring
`logger.exception` lines in container logs, which in the shipped compose rotate away
after 3×10 MB (`docker-compose.yml:13-16`). Functional impact stays bounded — each loop
survives and retries (see attacked-&-held) — but the *silent feature loss* the loops
exist to prevent (unbounded table growth, permanently PENDING duties, retained
memberships) would also be silent operationally.

**Impact.** Defense-in-depth/observability gap; no direct security impact.

**Fix.** Add per-loop counters (batches executed, rows processed, iterations failed)
alongside the existing refresh-purge pair and record them in all five loops (inside the
same `if enabled()` guard pattern).

---

### RT-M2-4 (Info, M6) — Repair counts claimed-not-healed farms; persistent conflicts consume batch slots

**Evidence.** `backend/app/seed.py:551-566`: a per-farm savepoint catches `IntegrityError`
and logs a warning, but `repair_legacy_farms_batch` returns `len(farm_ids)`
(`seed.py:568`) — farms whose preset insert *failed* are reported as repaired. The
loop's continuation heuristic (`main.py:240`, `if farms < farm_batch_size and
tasks_count < task_batch_size: break`) therefore treats them as progress, and the
eligibility predicate (`seed.py:512-537`) still sees their missing preset, so they are
re-claimed (lowest-ID order, `ORDER BY Farm.id ... LIMIT batch_size`) every pass,
hourly, forever, each time emitting a warning.

**Attack sketch.** To starve later farms you would need ≥ `farm_batch_size` (default 25,
max 500) permanently-conflicting lowest-ID farms. The name-suffix logic
(`_free_preset_role_name`, `seed.py:406-424`) reads *all* live role names for the claimed
batch first (`seed.py:543-550`), so a deterministic conflict requires an external writer
racing between the read and the savepoint flush — transient by nature, and exactly the
case the savepoint exists for. No code-evidenced path to a persistent conflict was found;
unconfirmed beyond the theoretical external-writer/DB-damage case.

**Impact.** Bounded slot consumption + warning noise; one tenant cannot roll back
another's repair (pinned by `test_ops.py:1710`). Reporting claimed-not-written counts
also slightly inflates the loop's "processed" log line.

**Fix.** Return only successfully-healed farms (count savepoint successes), or exclude
savepoint-failed farms from the reported count so the continuation heuristic reflects
real progress.

---

### RT-M2-5 (Info, M5) — Seed/migration mutual exclusion is advisory-only and uses disjoint lock IDs

**Evidence.** App seeding serializes booting *app processes* with
`pg_advisory_xact_lock(718204613)` (`backend/app/seed.py:275`; only user of that key).
Alembic serializes *release writers* with a different session lock, `718204614`
(`backend/alembic/env.py:45,88`). Nothing excludes an app boot from racing a migration
run. Safety today rests on three facts, all verified: (1) no migration INSERTs reference
rows — the only reference-data DML migration, `b5d7f9a1c3e5_backfill_species_reference_data.py`,
performs guarded `UPDATE`/`DELETE` only (lines 41-75), so concurrent app
`ON CONFLICT DO NOTHING` inserts and guarded migration corrections commute; (2) the
recipe-line duplication hazard (no unique constraint on `feed_recipe_lines`) is closed
by the single-statement `RETURNING`-winner pattern (`seed.py:294-317`) *plus* the
advisory lock; (3) compose gates `backend` on
`migrate: condition: service_completed_successfully` (`docker-compose.yml:94-95`).

**Attack sketch.** A deployment that starts the API and `alembic upgrade head`
concurrently (outside compose) relies purely on (1)/(2). With the current migration set
that is still safe; a future data migration that INSERTs reference rows (or un-guarded
UPDATEs) would race app seeding with no lock arbitration. Unconfirmed as a live issue —
flagged as a design tripwire.

**Fix.** Either have `seed_reference_data` also take (or check) the release-writer lock
before seeding, or document the invariant "reference-data migrations must be guarded and
insert-free" next to `RELEASE_WRITER_ADVISORY_LOCK_ID`.

---

### RT-M2-6 (Info, M10) — Missing production cross-checks (TTL ratio, edge-vs-app body limit, proxy consistency)

**Evidence and notes.**

1. **TTL ratio** — `access_token_ttl_seconds` (`config.py:191`) and
   `refresh_token_ttl_seconds` (`config.py:192`) are independently bounded but never
   compared; `refresh < access` (e.g. 60 s refresh / 30 min access) boots cleanly and
   produces a nonsensical session model (rotating refresh that dies before the access
   token). Availability/semantics only.
2. **Body-limit drift** — the app default is 1 MiB, tunable to 20 MiB
   (`config.py:169`), while the edge hardcodes `client_max_body_size 1m`
   (`docker-compose.yml:260`) with a comment claiming it "mirrors"
   `GOATFARM_MAX_REQUEST_BODY_BYTES`. Raising the app knob does not raise the edge:
   oversized uploads die at nginx with an HTML 413 that bypasses the app's JSON error
   contract and baseline headers. Drift is fail-closed in both directions (stricter
   layer wins), so this is contract drift, not a bypass.
3. **Trusted proxy consistency** — `trusted_proxy_hosts` is format-validated at boot
   (`config.py:442-477`, refusing hostnames/wildcards), but there is no production check
   that a non-empty value matches the actual deployment topology; a wrong CIDR quietly
   re-collapses per-IP auth ceilings. Default (`""`) trusts nothing; the compose default
   pins the edge's fixed IP (`docker-compose.yml:126-131`). Acceptable residual risk,
   noted for completeness.

---

## Per-unit attacked-&-held notes

### M5 — Startup seeding (`seed.py`, `main.py` lifespan, farm-create path)

- **Concurrent boots / migrate race:** `seed_reference_data` takes
  `pg_advisory_xact_lock(718204613)` for the whole seed transaction (`seed.py:275`);
  two app boots serialize; bucket/vaccine inserts are single-statement
  `ON CONFLICT DO NOTHING` (`seed.py:276-292,319-334`); recipe insert is
  `ON CONFLICT DO NOTHING ... RETURNING id` and only the winner inserts lines
  (`seed.py:294-317`) — no duplicate-line path even without the lock. Held
  (residual: RT-M2-5 tripwire).
- **Existing rows never rewritten:** create-only semantics verified and pinned by
  `test_ops.py:968` (operator-edited bucket label preserved; deleted release rows
  re-added only when absent). A release *changing* lines of an existing recipe will
  not propagate — by design, via data migration (`b5d7f9a1c3e5`).
- **seed_new_farm atomicity:** farm creation wraps `seed_new_farm` inside
  `execute_idempotent` (`api/auth.py:1522-1562`); the service rolls back the whole
  transaction on any `BaseException` (`services/idempotency.py:307-315` keyed path and
  claim/mutate commit atomically) — a mid-create seed failure leaves no partial farm
  (no roles-without-inventory state). Held.
- **Farm-row mutex:** `seed_default_roles` locks `Farm FOR UPDATE` before reading codes
  (`seed.py:489`), the consistent order with its own FK `KEY SHARE` and both callers
  (farm-create already owns the row; repair claims it `FOR UPDATE SKIP LOCKED` first,
  `seed.py:530-538`). Pinned by `test_concurrency.py:181`.
- **Display-name collisions:** `_free_preset_role_name` suffixes with the stable code
  then ordinals against the full live-name snapshot (`seed.py:406-424,543-550`); loop
  terminates (finite `taken`); a custom "Feeder" role cannot break batch repair
  (`test_ops.py:1400`).
- **Tombstoned presets:** `_role_identity_rows` counts tombstoned codes as present
  (`seed.py:452-467`), and the role-delete API refuses presets outright
  (`api/team.py:1250`); `test_ops.py:1523` pins name reuse without code recreation. Held.
- **Farm inputs:** `FarmCreateIn` caps name/location at 120 chars (matching
  `String(120)` columns, `models/core.py:83-84`), whitespace-only rejected
  (`api/auth.py:1500-1503`); timezone validated as real IANA zone + non-placeholder
  (`schemas/auth.py:134-152`) — no `farmToday` crash path from invalid tz at creation.
- **Inventory repair preserves edits:** `_seed_farm_inventories` is
  `INSERT..SELECT ... ON CONFLICT DO NOTHING` on `uq_feed_inventory_farm_ingredient`
  (`seed.py:353-398`); quantities/prices/reorder never overwritten; no API renames or
  deletes inventory rows (only `POST /api/feeding/inventory/{id}/add` exists,
  `api/feeding.py:320-368`), so the repair cannot fight operator intent. Held.
- **Boot-bounded:** `seed_startup` seeds fixed-size global data only
  (`seed.py:757-764`); tenant work is post-readiness; `test_ops.py:1119` pins that boot
  seeding never scans/locks tenant farms. Startup failure refuses to serve
  (`main.py:357-359`).

### M6 — Legacy repair batching

- **Two-phase commit crash consistency:** farm phase commits before the task phase
  (`seed.py:751-753`); a crash between leaves roles committed and tasks still eligible —
  both phases independently idempotent, retried next interval. The split is the
  documented deadlock avoidance (Farm→Task vs Task→Farm KEY SHARE);
  `test_ops.py:1166` pins farm locks are released before the task phase.
- **SKIP LOCKED / livelock vs farm creation:** repair claims existing farms
  `FOR UPDATE SKIP LOCKED` (`seed.py:536`); farm creation locks only its own new row —
  disjoint, no livelock.
- **Per-farm savepoint isolation:** `begin_nested` per farm (`seed.py:558-566`) — one
  tenant's conflict rolls back only that farm (test 1710). Non-IntegrityError failures
  abort the batch and are caught by the loop (logged, retried hourly).
- **Task backfill eligibility/starvation:** the claim set is restricted by correlated
  EXISTS `resolvable` (`seed.py:612-643`) — permanently unresolvable rows are excluded
  from the claim, not re-scanned (the old starvation bug); soft-deleted membership
  roles and defunct presets are filtered in both probe and locked read
  (`seed.py:612-622,683-691`); `test_ops.py:1760,2172,2279` pin these.
- **Lock discipline:** `FOR UPDATE SKIP LOCKED OF Task` avoids locking joined Farm rows
  (`seed.py:644-659`); membership source rows pinned `FOR SHARE` (`seed.py:691`) so the
  fallback role cannot change under the write. Claim bounded ≤10,000, id-ordered
  (`test_ops.py:1953,1997`).
- Residual: RT-M2-4 (claimed-vs-healed accounting).

### M7 — Five background loops

- **Poison-row death: impossible.** All five loops wrap each pass in
  `try: ... except asyncio.CancelledError: raise` / `except Exception:
  logger.exception(...)` and continue (`main.py:186-191,213-216,248-251,279-282,310-313`).
  A failing batch kills one pass, never the loop; `test_inactive_animal_task_cleanup.py:295`
  pins survival.
- **Shutdown:** all tasks cancelled and awaited (`suppress(CancelledError)`) before
  engine disposal (`main.py:401-419`); `CancelledError` is re-raised everywhere, never
  swallowed as generic `Exception`; engine disposal therefore cannot race a mid-query
  loop.
- **Bounds:** every interval has a floor (`ge=60`/`ge=10`, `config.py:128,148,156,162,195`)
  — no zero/negative tight loop; batch sizes and max_batches all `ge`/`le` bounded;
  per-batch fresh sessions — no long transactions.
- **Predicates verified:** inactive-animal cleanup joins `Animal` and requires
  `status != ACTIVE` with `Task.status = PENDING` (`services/animals.py:331-343`) —
  cannot skip tasks of ACTIVE animals; NULL-animal duties unreachable by this sweeper
  (handled by dedicated batch sweeper). Deleted-membership deactivation filters
  `User.deleted_at IS NOT NULL` and re-checks `is_active` in the UPDATE
  (`deps.py:313-343`) — live users untouched. Refresh purge deletes only
  `expires_at < now-30d` (`deps.py:265-287`); idempotency purge only
  `expires_at <= now` (`services/idempotency.py:99-118`); both materialize the locked
  candidate set before DELETE to respect the limit.
- **Boot batch:** one finite refresh purge before readiness (`main.py:345-356`); the
  periodic worker continues the rest. Residual: RT-M2-3 (metrics asymmetry).

### M8 — Metrics + probes

- `/metrics` registered only when `metrics_enabled` (`main.py:681-690`); flag off →
  route absent (404) and collectors take no observations (`metrics.py:75-109`, pinned by
  `test_metrics.py:111-127`). Rendered content is exactly the six custom collectors on
  the dedicated registry — no process/env/secret leakage; route labels are templates
  with `unmatched` fallback (never raw paths).
- `/healthz` liveness (no deps), `/readyz` does one pooled `SELECT 1`; failure returns
  the documented 503 body `{"status": "unavailable"}` with no internals
  (`main.py:526-537`; pinned by `test_ops.py:147,166`). Probes unauthenticated by
  design; in the shipped topology the edge routes only `/api/` to the backend, so
  neither probe nor metrics is publicly reachable (backend `expose`-only).
- Residual exposure posture: RT-M2-2; label cardinality: sibling RT-M-3. Non-API
  responses (docs/dev, metrics, probes) carry baseline headers but not
  `Cache-Control: no-store` — deliberate, public/operational data.

### M9 — Exception handlers & baseline headers

- **422 non-reflection:** the handler reconstructs each error from only
  `("type", "loc", "msg")` (`main.py:440-456`) — `input`, `ctx`, `url` from pydantic's
  `errors()` are dropped for bodies, nested models, lists, query/header params alike;
  `msg` strings are template messages (they name *expected* values, never the received
  input). Pinned by `test_ops.py:259`.
- **`_json_safe`:** recursive over dict/list/tuple, converts non-finite floats to
  strings, decodes bytes, isoformats dates (`main.py:422-437`); `Decimal` paths are
  normalized by `jsonable_encoder` first. No path where a non-finite value crashes the
  422 response.
- **PasswordWorkCapacityError:** 429 + `Retry-After: 1`, opaque body
  (`main.py:511-518`).
- **500 opacity:** body is exactly `{"detail": "Internal server error"}`; traceback
  logged with the request id recovered from `request.state` (the contextvar is already
  reset above the outermost user middleware); CORS headers reproduced manually for
  allowed origins only, Vary: Origin set (`main.py:477-508`; pinned by
  `test_ops.py:2425,2470,2514,2535`).
- **Baseline headers on every path — empirically verified:** Starlette's
  `add_middleware` insert(0) + reversed wrapping makes `request_id_middleware` the
  *outermost* user middleware, above TrustedHost/RequestBodyLimit/CORS/ProxyHeaders;
  measured via ASGI transport: TrustedHost 400, body-limit 414, declared-CL 413, and
  404 all carry `X-Request-ID` + `X-Content-Type-Options` etc.; the 500 path re-stamps
  them in the handler. `Cache-Control: no-store` applies to `/api/*` only, deliberately.
- **PII/secret logging:** no `print(` anywhere in `backend/app`; no
  subprocess/os.system/pickle/eval/exec; no logger call renders passwords, tokens,
  cookies or secrets (grep over all 35 logger call sites); request logs carry
  method/status/duration and the *path* only — never bodies or query strings. The
  percent-decoded-path log-forging issue in that one field is sibling finding RT-M-4
  (independently re-confirmed: uvicorn `unquote(raw_path)` at
  `uvicorn/protocols/http/httptools_impl.py:257-263` feeds
  `main.py:656-662`).

### M10 — Production boot validators

Enumerated checks in `_production_safety` (`config.py:479-624`) — all verified present
and tested (`test_ops.py:558-782`): Argon2 `memory ≥ 8×parallelism` (all envs);
`__Host-` name requires `cookie_secure` (all envs); production: HMAC secret ≥32 chars
and not the dev fallback, previous secrets ≥32/no dev fallback/no dups/not equal to
current; `cookie_secure=true`; refresh cookie name `__Host-`-prefixed (dev default is
auto-upgraded); CORS non-empty and every entry an exact non-loopback HTTPS origin (no
wildcard/userinfo/path/query/fragment); `allowed_hosts` non-empty, no `*`, no loopback,
no `/:@?#`/whitespace, canonicalized to the byte form TrustedHostMiddleware compares
(field validator `config.py:296-365`); `min_password_length ≥ 12`; Argon2 time ≥2 /
memory ≥19456 KiB / hash_len ≥32; `db_sslmode == verify-full` (and the migration
projection enforces the same for the DDL job, `config.py:71-78`).
Out-of-model validators: `rate_limit_backend` must be `memory` (`config.py:367-380`),
`trusted_proxy_hosts` format-validated (`config.py:442-477`), `refresh_cookie_name`
grammar-validated, multi-worker/replica warning at lifespan (`main.py:329-337`, sibling
RT-M-6), JWT keypair validation at boot (`validate_jwt_keypair`, `main.py:341`; key
*permissions* unvalidated — sibling RT-A-3), `environment` restricted to
development/production by `Literal` (`config.py:93`).
Gaps found: RT-M2-1 (env-var typo silence), RT-M2-2 (metrics_enabled), RT-M2-6 (TTL
ratio / body-limit drift / proxy-topology). `db_statement_timeout_ms` cannot be
silently disabled (`ge=1`, `config.py:115`).

### M11 — Settings surface

- `extra="forbid"` verified against all three sources (see RT-M2-1 for the env-var
  gap): `.env` extras and constructor kwargs refuse boot; malformed JSON for list
  fields (e.g. `GOATFARM_CORS_ORIGINS` without brackets) fails boot with a clean
  `SettingsError` naming the field (empirically verified); booleans parse `"false"` /
  `"0"` correctly; all numerics carry `ge`/`le` bounds (negative pool sizes etc.
  rejected).
- **Secrets:** `idempotency_request_hmac_secret` (and previous list) are `SecretStr`;
  `get_secret_value()` is used only inside `_production_safety` comparisons and no
  error message or log line ever includes a secret value (grep-verified; the sole
  `logger.info` at boot prints only `environment`, `main.py:322`).
- **`.env` location** is the fixed `BACKEND_DIR / ".env"` path — no user-controlled
  resolution, no traversal.
- **Backup-tooling env collision:** moot — stray `GOATFARM_BACKUP_*` env vars are
  ignored (RT-M2-1's mechanism); they would only collide if written into
  `backend/.env` itself. `MigrationSettings` deliberately projects a least-privilege
  `extra="ignore"` subset for the DDL container (`config.py:47-78`).
- Wrong-scheme `database_url` (e.g. `postgres://` without `+asyncpg`) fails at first
  engine construction inside lifespan — clean boot failure, acceptable.

---

## Test-coverage assessment

Held under attack: `test_ops.py` (probes/422/413/414, production-gate matrix
558-782, boot-seed no-tenant-scan 1119, two-phase lock release 1166, seeding
idempotency 1215/1299/1400/1450/1487/1523/1559/1600/1631/1667/1710, task-backfill
1760-2279, unhandled-error headers/CORS 2425-2535), `test_metrics.py` (counter
exposition, unmatched-template cardinality, disabled-flag 404/no-collection,
limiter-backend gate), `test_inactive_animal_task_cleanup.py` (batching, live-session
convergence, survival after failure), `test_concurrency.py:181` (farm-row seed
serialization), `test_online_index_recovery.py` / `test_ops_migration_integrity.py`
(reference-data corrections, migration round-trip behavior).

Gaps matching findings: no test asserts unknown `GOATFARM_*` env vars are
rejected/warned (RT-M2-1); none that production refuses/allows `metrics_enabled`
explicitly (RT-M2-2); none that a loop failure is visible in metrics (RT-M2-3);
none that repair reports healed-farm counts (RT-M2-4).

## Regression tests to add

1. RT-M2-1: with `GOATFARM_MIN_PASSWORD_LENGHT=20` (typo) exported → boot refusal or
   loud warning naming the ignored variable; `backend/.env` variant already covered.
2. RT-M2-2: production Settings with `GOATFARM_METRICS_ENABLED` unset → boot refusal
   (after fix); set `false` → `/metrics` 404 in production app.
3. RT-M2-3: fail one loop pass (stub its service to raise) → failure counter
   increments (after fix).
4. RT-M2-4: farm with persistent role conflict → `repair_legacy_farms_batch` returns
   0 healed farms (after fix) and other farms still progress.
