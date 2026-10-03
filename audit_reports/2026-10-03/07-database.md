# Database, migrations and operational scripts quality review

Repository: `/Users/pradeepreddy/Desktop/goat-farm-management-main`.
Review date: 2026-10-03. Read-only review; no application source edits, database mutations, attack simulations, migrations against a server, or backup/restore against a live target.

## Coverage and limits

All 140 authored files in the assigned scope (22,330 physical source lines) were semantically read in full: 20 model modules, 97 migration modules, Alembic environment/template, 19 operational scripts, `app/db.py`, and `app/seed.py`. Long sources were read in numbered chunks; output omissions were reread. All migration bodies, trigger definitions, data repairs, downgrade guards and deletion paths were included. The coverage ledger is `/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/evidence/herdly-audit-database-files.txt`. Outside-scope callers/tests/runbook sections were sampled to validate candidate findings and are separately identified in that ledger.

Independent mechanical graph inspection found 97 unique migration revisions, one root (`ed5efe13a516`), one head (`f4a8c2e6d1b9`), no missing parents and no migration importing mutable `app` implementation modules. This supports graph consistency; it does not prove actual installed schema parity.

Four offline SQL-generation paths were executed with explicitly dummy localhost URLs and development/disabled TLS settings. Offline mode opens no database connection. Two configuration-helper checks used temporary files containing only invented fixture values. These checks reproduced findings below. Parent-reported test/lint/type/build results were context, not rerun or treated as proof of database correctness.

Database connectivity was unavailable. No actual `alembic check`, fresh/upgraded catalog comparison, online migration/downgrade rehearsal, SQL constraint insertion checks, query plans, throughput/lock tests, backup decryption from real keys, live restore or restore drill was performed. Severity reflects demonstrable source behavior and operational impact, without assuming current production data already exhibits a defect. Defensive schema gaps are explicitly lower severity.

## Findings

### DB-01 — Medium: supported offline migration rendering breaks in historical revisions

Evidence:

- `/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/alembic/env.py:61` provides `run_migrations_offline` and routes `--sql` to it at line119.
- `/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/alembic/versions/c3d4e5f6a7b1_kid_entry_tag_index_predicate.py:47` unconditionally calls `op.get_bind().exec_driver_sql(...).fetchall()` to diagnose duplicate tags.
- `/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/alembic/versions/cad1e2f3a4b5_drop_unproducible_vocabularies.py:51` consumes `.scalars()` from a preflight execute result in upgrade without an offline branch.
- `/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/alembic/versions/d4e5f6a7b8c9_user_tombstones.py:56` consumes `.scalar_one()` in downgrade without an offline branch.
- `/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/alembic/versions/f3d4e5f6a7b8_actor_scoped_farm_idempotency.py:196` consumes `.scalars()` in downgrade without an offline branch (its upgrade correctly handles offline mode).

Reproduction: `alembic upgrade base:head --sql` exits1 at the first revision with `AttributeError: 'MockConnection' object has no attribute 'exec_driver_sql'`. Narrow renders `upgrade b9c0d1e2f3a4:cad1e2f3a4b5 --sql`, `downgrade d4e5f6a7b8c9:d3f4a5b6c7d8 --sql`, and `downgrade f3d4e5f6a7b8:f2c3d4e5f6a7 --sql` each exit1 with `NoneType` query-result errors. Logs: `/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/evidence/herdly-offline-upgrade.log`, `/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/evidence/herdly-offline-cad.log`, `/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/evidence/herdly-offline-user.log`, `/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/evidence/herdly-offline-narrow-2.log`. Partial SQL files were generated but not applied.

Impact: a DBA cannot produce a complete reviewable upgrade script or the affected downgrade scripts through the advertised Alembic offline entry point. Narrow offline tests can pass while a complete upgrade still fails; `/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/tests/test_deployment_artifacts.py:1131` checks only one unaffected range.

Improvement: add complete offline-render CI checks and explicit support boundaries. Applied revision immutability means appending a revision alone cannot repair a failing historical walk. If offline export is required, implement an audited compatibility/export layer that emits equivalent fail-closed SQL preflight guards for these steps; reconcile that design with the immutable-history policy. Otherwise detect unsupported ranges before emitting partial SQL and document their online-only requirement. Do not silently omit the preflights.

### DB-02 — Medium: explicit backup classification drops a private CA stored in dotenv

Evidence: `/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/scripts/backup_env.sh:32` returns immediately when both environment and SSL mode are nonempty exports. Line36 resolves the optional CA from the process environment alone. The shared helper's later fallback at line106 would load the dotenv CA, but this branch bypasses it. Backup exports that result to libpq at `/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/scripts/backup.sh:356`; restore does the same at `/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/scripts/restore.sh:266`.

Reproduction: a temporary dotenv contained production, verify-full, and `/fixture/private-ca.pem`. With both classification settings exported and the CA unset, the helper resolved an empty CA. With only environment exported, the same helper resolved `/fixture/private-ca.pem`.

Impact: normal cron/operator overrides can cause backup and restore to lose the application's configured private trust root and fail TLS verification. Verification is still enabled; this is backup availability/configuration parity, not evidence of a TLS bypass.

Improvement: when the dotenv file exists, resolve an unresolved optional CA from the same pinned snapshot even if both mandatory classification settings are exported. Preserve explicit-export precedence and the intentional missing-dotenv classification behavior. Add the reproduced mixed-source case to helper tests.

### DB-03 — Medium: retention batches do not bound transactions or cumulative locks

Evidence: `/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/services/retention.py:99` drains every eligible row through an unlimited `while True`; the batch size at104 bounds each statement. All six table cohorts are drained before the sole per-farm commit at188. The source's lock-skipping guarantee at212-214 then relies on parent `ON DELETE CASCADE` to remove child rows that were deliberately skipped.

Impact: one farm's large backlog produces an unbounded transaction, cumulative row locks/WAL/rollback work and delayed progress for subsequent farms. A parent cascade can wait on a locked child despite the earlier `SKIP LOCKED` selection; skipping a row in a SELECT does not make a later FK cascade nonblocking. This is a scalability/concurrency limitation inferred from transaction and FK behavior, not a measured outage. PostgreSQL retains row/table locks until transaction end. [PostgreSQL locking documentation](https://www.postgresql.org/docs/current/explicit-locking.html).

Improvement: select a bounded root-image cohort, delete its full dependent chain and commit per cohort; bound task cohorts similarly. Add a finite per-pass batch/time budget so one tenant cannot monopolize the sweep. Establish explicit handling for images with actively locked children. Test against a production-sized history with a second session holding child locks once a database is available. Existing sampled retention tests check statement chunking, not bounded transaction lifetime.

### DB-04 — Medium: retention discovery transfers every expired row merely to find farms

Evidence: `/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/services/retention.py:160` selects one farm id per expired screening image; line165 does the same per terminal task. Line169 applies `set(...)` and union in Python. No `DISTINCT`, farm pagination, or bounded streaming is applied.

Impact: a retention backlog causes network/result-memory usage proportional to all expired rows before any deletion starts. A single farm with millions of tasks yields millions of repeated copies of one farm id. This undermines the nominal bounded sweep even before DB-03 applies.

Improvement: perform SQL `DISTINCT`/`UNION` for farm discovery and keyset-page farm ids, retaining deterministic order and fault isolation. Validate plans for the age/status predicates and size a backlog benchmark; no query-plan measurement was possible here.

### DB-05 — Low: retention summary includes rolled-back deletes

Evidence: `/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/services/retention.py:231` through293 increments a shared summary after each table cohort. A subsequent failure causes rollback at184 and `failed_farms += 1`, but previously accumulated row counts are not removed. The commit at188 is also outside the farm exception handler.

Impact: a failure after one child cohort was deleted reports those rolled-back rows as successfully deleted in `total_deleted` and maintenance metrics. A commit failure bypasses the advertised per-farm failure path. The sampled fault-isolation test at `/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/tests/test_retention.py:422` injects failure before deletes and does not cover partial progress followed by rollback.

Improvement: accumulate farm-local counts, include the commit in the transaction exception handling, merge counts only after successful commit, and test a failure after the first successful deletion plus a commit failure.

### DB-06 — Low: reviewed-finding CHECK allows incomplete reviewer attribution

Evidence: `/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/models/screening.py:451` and the applied creation revision `/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/alembic/versions/b8d9e0f2a3c4_screening_phase2_rotation_specialists_review.py:47` use:

`(status = 'PENDING_REVIEW') = (reviewed_at IS NULL AND reviewed_by_id IS NULL)`

For a terminal status, the right side needs only be false. A timestamp alone or reviewer alone therefore passes. The model comment at448 describes both review fields as required. The normal API sets both at `/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/api/screening.py:333`.

Impact: direct SQL/import/future writer mistakes can create confirmed training labels with incomplete audit metadata. No existing malformed rows or normal-API corruption were observed; this is database defense quality.

Improvement: append a revision that preflights partial attribution and replaces the CHECK with explicit pending/both-null versus terminal/both-nonnull branches. Add meaningful insertion checks for both partial states and the valid states. Preserve the applied revision.

### DB-07 — Low: screening ancestry is tenant-safe but internally inconsistent parents remain legal

Evidence: `/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/app/models/screening.py:333` and339 validate `(farm_id,image_id)` and `(farm_id,crop_id)` independently. They do not assert that a run's crop belongs to its image. Finding FKs at420 and426 similarly do not ensure its denormalized crop is the run's crop, despite the provenance comment at475-476. The Phase3 migration establishes the same relationships.

Impact: a same-farm import or future writer can associate a run/finding with another image's crop, distorting review/export provenance and causing deletion through an unexpected parent. Correct pipeline paths sampled in this review set coherent values. The retention service explicitly tolerates this mismatch at207-211; it does not prevent creating it. This is not a demonstrated cross-tenant access defect.

Improvement: preflight current mismatches and append a candidate-key/composite-provenance constraint or a narrowly scoped trigger that validates image↔crop↔run relationships, including null whole-photo cases. Alternatively derive the finding's denormalized crop from its authoritative run. Add a same-farm/wrong-parent negative test.

### DB-08 — Low: Compose delivery guard rejects a valid explicit-empty override

Evidence: `/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/scripts/compose_env_guard.py:101` considers a secret delivered if either dotenv or environment contains a nonempty value, ignoring shell precedence. The manifest forwards the already resolved values at `/Users/pradeepreddy/Desktop/goat-farm-management-main/docker-compose.production.yml:67` through74. The helper's own docstring at14 describes shell-over-file precedence.

Reproduction: fixture dotenv carried a stale plain database URL; process environment explicitly set that plain variable to `''` and its FILE route to `/fixture/database-secret`. `delivery_problems()` still returned `GOATFARM_DATABASE_URL and GOATFARM_DATABASE_URL_FILE are both set; deliver exactly one`.

Impact: an operator cannot clear a stale plain secret using a legitimate shell override while switching to file delivery; the guard falsely prevents the migration/rollout. No actual secret or production rollout was touched.

Improvement: treat environment key presence as authoritative, including empty values; use dotenv only when a key is absent from environment. Test explicit-empty/plain plus valid FILE delivery and the reverse clearing case.

### DB-09 — Low: migration documentation incorrectly describes same-transaction validation as nonblocking

Evidence: `/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/alembic/versions/f1e2d3c4b5a6_husbandry_vocabulary_check_widenings.py:21` through26 claims the CHECK swap holds ACCESS EXCLUSIVE only for a catalog swap and reads/writes never stall behind validation. Its helper drops/adds/validates at77-84 in one transaction. The same claim appears in `/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/alembic/versions/f8a2c4e6b1d9_widen_preset_role_codes.py:13` through18 and `/Users/pradeepreddy/Desktop/goat-farm-management-main/README.md:759`. Alembic's transaction wrapping is explicit at `/Users/pradeepreddy/Desktop/goat-farm-management-main/backend/alembic/env.py:78`.

Impact: the stronger earlier lock remains until commit, so a weaker validation lock does not release it or make that transaction online. Operators or future migration authors could rely on the incorrect pattern. The current runbook at README151-158 correctly requires rehearsal and quiesced maintenance, mitigating deployment risk. PostgreSQL documents transaction-lifetime locks and ALTER TABLE lock strengths. [Lock lifetime](https://www.postgresql.org/docs/current/explicit-locking.html), [ALTER TABLE](https://www.postgresql.org/docs/current/sql-altertable.html).

Improvement: correct the runbook's explanation without rewriting applied revisions, annotate historical lock impact in the deployment notes, and use deliberately separated DDL/validation transactions for future genuinely online changes. Keep the maintenance-window rule until rehearsals establish otherwise.

## Positive design evidence and follow-up limits

- Tenant composite references/candidate keys cover the central livestock, reproductive, task, history, notification and finance relationships; later revisions correct the screening bigint FK widths. Fail-closed SQL/data preflights generally avoid guessing repairs for lossy numeric conversions, incompatible downgrade data and invalid enums.
- Money uses decimal numeric columns; feed/weight conversions move stored values to explicit precision. Some public/application quantity types intentionally expose floats (`asdecimal=False`), so exact database storage alone does not establish end-to-end decimal arithmetic; no blanket precision guarantee is inferred.
- Release writers share an advisory lock across Alembic and atomic restore. Concurrent-index revisions inspect invalid remnants and either remove them online or emit a diagnostic offline.
- Seed logic separates reference creation from edits, preserves user customization, applies an advisory mutex and uses bounded repair batches. The reporting scripts are standalone computations; source review is not verification of the external husbandry/economic claims in their generated prose.
- Backup/restore scripts use private file modes, credential passfiles rather than URL credentials in argv, descriptor-based producer locking, pinned copies, checksums, signatures/encryption gates and rollback/empty-target restore safeguards. Backup publication's `pg_restore --list` checks archive parsing/TOC, not an actual executed restore. Real signed-artifact recovery, restored-schema parity and the documented quarterly recovery drill remain unverified operational requirements.
- No high/critical defect was established in this assigned slice. The absence of live database verification limits confidence in current catalog parity, lock timing, migration data compatibility and recoverability; the reproducible helper/offline failures and semantic limitations above remain valid independently of the unavailable database.
