# Track 8 — Performance, scalability, and concurrency

Audit date: 3 October 2026 (America/Phoenix)

Audited source commit: `1ca78768ed227182b1b84663bcc97c5ea9bee41b`

## Verdict

No Critical or High finding was established. I found four Medium issues, one Low issue, and one Info-level deployment boundary. The most consequential bottlenecks are queue fairness in screening, synchronous/serial notification delivery, and the task board's fixed query and row amplification. The frontend also has a measured cold-load cost that conflicts with its low-bandwidth target.

This was an independent source audit. No existing audit, improvement, status, or mutation report was used. No application source was changed.

## Findings

### 08-1 — The task-board contract fetches every tab and can issue 25 task-domain SQL statements per refresh

- **Severity:** Medium
- **Confidence:** High
- **Evidence status:** Source-proven; query count was derived from the current ORM control flow, not reproduced against PostgreSQL.
- **Current evidence:** `backend/app/api/tasks.py:266-392`, `backend/app/api/_shared.py:48-53`, `frontend/src/app/(app)/tasks/page.tsx:645-665`, `frontend/src/app/worker/page.tsx:133-146`, `backend/app/models/tasks.py:69,232-264`, `backend/app/core/config.py:727-738`.
- **Impact:** Every board load performs five sequential count queries and five sequential page queries. Each nonempty page has three `selectinload` relationships, so the endpoint can add 15 relationship queries: 25 task-domain statements, before farm/auth dependencies; a non-owner's `task_scope` adds another membership query. The normal web page asks for up to 250 task rows even though only one tab is active, while the worker board asks for up to 810 rows (four 200-row active/review pages plus ten completed rows) although its shift view consumes overdue and today. The web query explicitly refetches on window focus, repeating this work.
- **Preconditions:** An authenticated caller with `tasks.view`; the upper statement count requires nonempty results in all five tabs. Payload amplification is greatest at configured page limits.
- **Scaling amplifier:** The completed tab counts all matching history and orders by `CASE(status = 'SKIPPED', skipped_at, completed_at), id`. The declared task indexes cover farm/status/due-date and pending subsets, not that terminal-time expression. Retention of terminal tasks is opt-in and disabled by default, so a large farm's count and sort can grow indefinitely. This index-cost conclusion is inferred from metadata; no `EXPLAIN` was run.
- **Recommendation:** Make the active tab an explicit request parameter. Return all five counts with conditional aggregates, but fetch/enrich rows only for the selected tab (and let the worker request only today/overdue). Add a query-count regression test and validate a matching terminal-history index with production-shaped `EXPLAIN (ANALYZE, BUFFERS)` before adding it.

### 08-2 — Screening fairness is applied only after a global 2,000-row age window

- **Severity:** Medium
- **Confidence:** High
- **Evidence status:** Source-proven selection order; delay estimates are inferred from configured limits, without provider or database load.
- **Current evidence:** `backend/app/services/screening/pipeline.py:168-172,505-583`, `backend/app/core/config.py:917-929`, `backend/app/api/screening.py:88-104`, `backend/app/worker/__init__.py:191-249`.
- **Impact:** `_claim_retry_rows` first orders all eligible images globally by creation time and limits that set to 2,000. Only that truncated set is partitioned and ranked per farm. Farms with no row among the globally oldest 2,000 therefore receive no fairness slot, even though the outer query appears round-robin. Under normal API caps, 40 farms can lawfully hold 10,000 older eligible images (250 each). With the default 50-image cycle and five-minute post-cycle wait, a newer farm at the tail cannot enter the window until more than 160 cycles have drained, roughly 13 hours 25 minutes even with zero processing time. ERROR/FLAGGED retries can extend the delay.
- **Preconditions:** More than 2,000 eligible rows across enough older farm queues; screening must be enabled. One normal farm cannot fill the window by itself because intake is capped at 250 in-flight images per farm.
- **Recommendation:** Select a bounded set of eligible farms using a durable rotating cursor, then take a small bounded number of oldest rows per farm. Keep `FOR UPDATE SKIP LOCKED`, the attempt cap, and per-farm call budget. Add a multi-tenant backlog test that proves a newly eligible farm is admitted within a stated number of cycles independent of other farms' queue depth.

### 08-3 — A single serial notification loop lets slow recipients delay every tenant and holds a DB connection during provider I/O

- **Severity:** Medium
- **Confidence:** High
- **Evidence status:** Source-proven serialization and connection lifetime; elapsed delay is a bounded source-derived scenario, not a live-provider reproduction.
- **Current evidence:** `backend/app/main.py:483-549`, `backend/app/services/notifications/outbox.py:71-190`, `backend/app/services/notifications/service.py:125-174,328-373,461-505,603-664`, `backend/app/services/notifications/providers.py:22-23,92,102-128`, `backend/app/core/config.py:930-956`.
- **Impact:** One coroutine dispatches all due outbox events serially, then all due digests serially, then the farm alert page. Events and recipients are also serial loops. With defaults, a safely retryable connect timeout can consume about 22 seconds per recipient (two 10-second attempts plus two-second backoff). Fifty opted-in recipients—the default farm daily cap—can therefore occupy the only dispatcher for roughly 18 minutes 20 seconds; later farms, digests, and time-sensitive alerts wait behind it. After the durable claim commit, `db.get` and the cap count start a new transaction, then the external send occurs before settlement commits, so one shared backend-pool connection remains checked out during each provider wait.
- **Preconditions:** Notifications are explicitly enabled (they are off by default), the provider is slow or timing out, and there are multiple recipients/events or tenants waiting.
- **Recommendation:** Separate outbox, digest, and periodic-alert scheduling so one class cannot stall the others. Use small, configurable global and per-farm delivery concurrency with the existing durable claim/dedupe as the correctness boundary. End the read transaction and release its connection before provider I/O, then settle in a short fresh transaction. Add a fake-provider timing test that asserts cross-farm latency and pool checkout duration.

### 08-4 — Every web route ships both full language catalogs; measured cold-route JS is 511–591 KiB gzip-equivalent

- **Severity:** Medium
- **Confidence:** High
- **Evidence status:** Reproduced locally from a production build at the audited commit.
- **Current evidence:** `frontend/src/lib/i18n/index.tsx:28-29,48-54,89-103`, `frontend/src/components/providers.tsx:33-45`, `frontend/src/app/layout.tsx:46-60`.
- **Impact:** The root client provider statically imports both English and Telugu catalogs, so even unauthenticated and single-language visits fetch them. The catalog sources total 697,742 bytes (248,770 English plus 448,972 Telugu); separate gzip measurements total 150,418 bytes. The production build's 686,434-byte shared chunk visibly contains both catalogs and compresses to 153,756 bytes. Enumerating deduplicated script tags from a local production server measured:

  | Route | Scripts | Raw JS | gzip level-6 estimate |
  | --- | ---: | ---: | ---: |
  | `/login` | 23 | 1,962,312 B | 522,956 B (511 KiB) |
  | `/dashboard` | 26 | 2,058,346 B | 548,724 B (536 KiB) |
  | `/simulation` | 28 | 2,250,993 B | 605,558 B (591 KiB) |

  This is a cold-cache transfer/parse cost; immutable hashed chunks substantially reduce repeat-navigation cost.
- **Preconditions:** First visit, cleared cache, or a new deployment hash; impact is greatest on rural/mobile links and lower-end devices.
- **Recommendation:** Split catalogs by locale and preferably by feature namespace. Persist a server-readable locale choice so the initial render can request only the selected catalog; ensure Telugu completeness or load a small scoped English fallback rather than the entire application dictionary. Put cold-route compressed JS budgets in CI, including `/login` and the simulation route.

### 08-5 — TOTP recovery-code minting holds the user row lock and a DB checkout through ten sequential Argon2 hashes

- **Severity:** Low
- **Confidence:** High
- **Evidence status:** Lock scope is source-proven; hashing time was reproduced locally, while production contention is inferred.
- **Current evidence:** `backend/app/api/auth.py:2668-2679,2697-2761,2940-2998`, `backend/app/security.py:78-127,296-331`, `backend/app/core/config.py:819-827`.
- **Impact:** TOTP confirmation and recovery regeneration acquire `User ... FOR UPDATE`, then `_mint_totp_recovery_codes` performs ten hashes sequentially in one password-worker slot before committing. On this audit host, one default recovery hash took 0.0262 seconds and ten took 0.2232 seconds. During that time the user row and request transaction remain live; two concurrent mints for different users can also consume both default Argon slots. The impact is localized and excess password work fails fast rather than accumulating, hence Low severity.
- **Preconditions:** Successful TOTP enrollment confirmation or recovery-code regeneration; contention needs a concurrent credential operation or general pool pressure.
- **Recommendation:** Use the snapshot/rollback/hash/re-lock/revalidate pattern already used by password workflows: prepare the random codes and hashes outside a database transaction, reacquire the user lock, revalidate token/TOTP generation state, and atomically replace the rows. Discard prepared codes on a failed revalidation.

### 08-6 — Horizontal backend scaling is intentionally unsupported until process-local admission controls are externalized

- **Severity:** Info
- **Confidence:** High
- **Evidence status:** Source- and deployment-configuration-proven; no multi-replica runtime was attempted.
- **Current evidence:** `backend/app/api/_run_limits.py:1-13,105-128`, `backend/app/main.py:655-675`, `backend/app/core/config.py:1621-1638`, `backend/app/db.py:117-135`, `Dockerfile:82-86`, `docker-compose.production.yml:128-140`.
- **Impact:** Auth attempt ledgers/workflow reservations, per-user/per-farm simulation locks, and simulation cost budgets are process-local. The production validator rejects declared multi-worker environment values and the image pins one uvicorn worker, which is a sound supported configuration. It cannot detect two container replicas: each replica would multiply admission budgets and add up to another 15 default application DB connections. The deployment therefore has vertical capacity and a single-backend availability boundary; adding replicas is not a safe transparent scaling action.
- **Preconditions:** An operator runs multiple backend containers/pods despite the documented single-process contract.
- **Recommendation:** Keep one backend replica in the supported topology. Before horizontal scaling, move security throttles and CPU budgets to shared atomic storage, use a distributed simulation work queue, define leader/lease behavior for background loops, and budget aggregate database connections across replicas.

## Strengths observed

- The async database engine has explicit pool size/overflow/timeouts, pre-ping, recycling, and a server-side statement timeout (`backend/app/db.py:117-135`). Pagination offsets and most list sizes are server-bounded.
- CPU-heavy simulations are offloaded and protected by per-user, per-farm, and process-wide admission plus a sliding cost budget; cancellation retains leases until native work actually finishes (`backend/app/api/_run_limits.py:42-128,339-405`). The API rolls back its dependency transaction before simulation CPU work.
- Password hashing uses a dedicated, non-queuing executor. Cancellation does not release memory admission prematurely, and overload maps to HTTP 429 (`backend/app/security.py:78-127`, `backend/app/main.py:917-929`). Ordinary login/password workflows deliberately end DB transactions before Argon work.
- Screening claims are durable, use `FOR UPDATE SKIP LOCKED`, cap attempts and per-cycle work, enforce per-farm intake and provider-call budgets, and commit outcomes per image (`backend/app/services/screening/pipeline.py:413-583,586-763`). The fairness defect above does not undermine duplicate-claim safety.
- Mutations commonly acquire deterministic ordered row sets or transaction advisory locks. The task completion path, for example, locks animals in ascending ID order and documents its batch/task ordering (`backend/app/api/tasks.py:157-235`).
- React Query has bounded retry and a shared 15-second staleness default, and Next emits route-specific chunks with gzip support (`frontend/src/components/providers.tsx:12-20`).

## Validation performed

- Confirmed the exact audited commit with `git rev-parse HEAD`.
- `cd frontend && pnpm build` completed successfully. I served the result only on local loopback, enumerated each route's initial script URLs, fetched each unique asset, and measured raw and deterministic gzip-level-6 sizes. All temporary servers were stopped.
- Ran an in-process, database-free Argon2 microbenchmark through `hash_totp_recovery_codes_async`: 1 code = 0.0262 s; 10 codes = 0.2232 s on this host. These are structural evidence, not production latency targets.
- `docker compose ps` showed no running project services. No database was created, started, queried, or mutated. No S3 bucket, screening provider, SMS provider, or other live service was contacted.
- `pnpm build` refreshed only ignored `frontend/.next` output. No tracked application file changed; this report is the only intended repository write.

## Limits and measurements still needed

- There was no available disposable PostgreSQL service, and the existing local/shared database was intentionally not touched. Consequently there are no `EXPLAIN (ANALYZE, BUFFERS)` results, measured task-board query counts, lock-wait traces, or pool-saturation curves. The task and index findings explicitly separate source proof from workload-dependent impact.
- No destructive/concurrent load test was run. Provider latency, S3 throughput, notification delivery time, and screening drain rate were not measured against live systems.
- The frontend numbers cover initial JS assets named by locally served production HTML. They do not replace WebPageTest/Lighthouse measurements on representative low-end Android devices and rural network profiles, nor do they measure post-hydration API payloads.
- Resource limits are present in the supplied Compose topology, but production database capacity, connection headroom, latency SLOs, queue-age alerts, and real data cardinalities were unavailable. Missing those measurements is an operational validation gap, not evidence by itself of a defect.

## Finding count

- Critical: 0
- High: 0
- Medium: 4
- Low: 1
- Info: 1
- Total: 6
