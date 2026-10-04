# Track 4 — Database and migrations

Audit date: 2026-10-03 (America/Phoenix)  
Commit: `1ca78768ed227182b1b84663bcc97c5ea9bee41b`  
Scope: SQLAlchemy models/session setup, Alembic environment and all revisions, retention, and database backup/restore scripts. Prior audit reports and implementation-plan/status files were not consulted.

## Audit-execution incident: local development database was unintentionally upgraded

During the audit, one Alembic command intended for the disposable PostgreSQL container used conventional, **unprefixed** environment-variable names:

```text
# working directory: backend/
DATABASE_URL='postgresql+asyncpg://track4:***@127.0.0.1:55444/track4db' \
ALEMBIC_DATABASE_URL='postgresql+asyncpg://track4:***@127.0.0.1:55444/track4db' \
ENVIRONMENT=development \
.venv/bin/alembic -c alembic.ini upgrade head
```

`backend/.env` was absent. `MigrationSettings` accepts only the `GOATFARM_` prefix and otherwise supplies a writable localhost development default ([`backend/app/core/config.py:361`](../../backend/app/core/config.py), [`backend/app/core/config.py:370`](../../backend/app/core/config.py)); Alembic consumes that value directly ([`backend/alembic/env.py:116`](../../backend/alembic/env.py)). The command therefore connected to `postgresql+asyncpg://localhost:5432/goatfarm`, not the disposable container.

The Alembic transcript showed the local database starting at `f4a8c2e6d1b9` and applying exactly these five revisions to `f9a3b7c1d5e2`:

1. `f5b9d3e7a2c0`: adds nullable refresh-session origin/farm/membership columns and constraints. It deliberately leaves existing sessions with a `NULL` origin ([`backend/alembic/versions/f5b9d3e7a2c0_scope_pin_refresh_sessions.py:6`](../../backend/alembic/versions/f5b9d3e7a2c0_scope_pin_refresh_sessions.py), [`backend/alembic/versions/f5b9d3e7a2c0_scope_pin_refresh_sessions.py:25`](../../backend/alembic/versions/f5b9d3e7a2c0_scope_pin_refresh_sessions.py)).
2. `f6c0e4f8b3d1`: runs clinical consistency preflights, adds constraints/triggers and evidence/budget tables, gives existing findings `review_revision = 0`, and inserts a cutover-hold budget only for farms with `PROCESSING` images ([`backend/alembic/versions/f6c0e4f8b3d1_clinical_evidence_and_budget.py:32`](../../backend/alembic/versions/f6c0e4f8b3d1_clinical_evidence_and_budget.py), [`backend/alembic/versions/f6c0e4f8b3d1_clinical_evidence_and_budget.py:72`](../../backend/alembic/versions/f6c0e4f8b3d1_clinical_evidence_and_budget.py), [`backend/alembic/versions/f6c0e4f8b3d1_clinical_evidence_and_budget.py:242`](../../backend/alembic/versions/f6c0e4f8b3d1_clinical_evidence_and_budget.py)). The preflights passed.
3. `f7d1e5f9b4c2`: creates the notification outbox and adds `notification_log.outbox_id`; it intentionally performs no historical alert backfill ([`backend/alembic/versions/f7d1e5f9b4c2_transactional_notification_outbox.py:1`](../../backend/alembic/versions/f7d1e5f9b4c2_transactional_notification_outbox.py), [`backend/alembic/versions/f7d1e5f9b4c2_transactional_notification_outbox.py:24`](../../backend/alembic/versions/f7d1e5f9b4c2_transactional_notification_outbox.py)).
4. `f8e2f6a0c5d3`: creates `maintenance_progress` and inserts the two initial checkpoint rows ([`backend/alembic/versions/f8e2f6a0c5d3_durable_maintenance_progress.py:25`](../../backend/alembic/versions/f8e2f6a0c5d3_durable_maintenance_progress.py)).
5. `f9a3b7c1d5e2`: takes an exclusive lock and backfills revision-zero history for eligible legacy reviewed findings ([`backend/alembic/versions/f9a3b7c1d5e2_preserve_legacy_screening_reviews.py:22`](../../backend/alembic/versions/f9a3b7c1d5e2_preserve_legacy_screening_reviews.py)).

No downgrade or compensating database write was attempted after discovery. A read-only snapshot at the end of the audit showed: `users=1`, `farms=1`, `refresh_sessions=1` (the one session has `session_origin IS NULL`), `screening_images=0`, `screening_runs=0`, `screening_findings=0`, `screening_finding_reviews=0`, `screening_daily_budgets=0`, `screening_call_reservations=0`, `health_rounds=0`, `notification_log=0`, `notification_outbox=0`, and `maintenance_progress=2`. These current counts show that the conditional f6/f9 data backfills affected no screening rows and that f8 inserted its two declared checkpoints. There is no pre-command row-count snapshot, so this audit cannot independently prove that every pre-existing value was unchanged.

The least-destructive state is to leave this local database at head, which matches the audited commit. If exact pre-upgrade state is required, restore a verified pre-incident backup/snapshot into a **new empty database**, validate it, and switch the local connection explicitly. Do not blindly downgrade this populated database: f5 documents mandatory session revocation for rollback, and later revisions deliberately refuse some evidence-losing populated downgrades ([`backend/alembic/versions/f5b9d3e7a2c0_scope_pin_refresh_sessions.py:9`](../../backend/alembic/versions/f5b9d3e7a2c0_scope_pin_refresh_sessions.py), [`backend/alembic/versions/f7d1e5f9b4c2_transactional_notification_outbox.py:79`](../../backend/alembic/versions/f7d1e5f9b4c2_transactional_notification_outbox.py), [`backend/alembic/versions/f9a3b7c1d5e2_preserve_legacy_screening_reviews.py:51`](../../backend/alembic/versions/f9a3b7c1d5e2_preserve_legacy_screening_reviews.py)).

## Findings

### DB-01 — Medium — Alembic has a writable implicit development target

**Confidence:** High. **Status:** Reproduced by the incident above.

**Evidence.** Migration settings ignore unprefixed variables, default `environment` to development, and default the database to localhost `goatfarm` ([`backend/app/core/config.py:361`](../../backend/app/core/config.py), [`backend/app/core/config.py:370`](../../backend/app/core/config.py)). Online Alembic immediately builds its engine from that resolved URL, without requiring an explicit migration URL or printing a sanitized target confirmation ([`backend/alembic/env.py:116`](../../backend/alembic/env.py), [`backend/alembic/env.py:124`](../../backend/alembic/env.py)). The README names the correct `GOATFARM_*` variables, which mitigates but does not fail closed on a typo ([`README.md:52`](../../README.md)).

**Impact and preconditions.** When a local `goatfarm` database is reachable, a missing/misspelled prefix can mutate the wrong database. The production-only validation helps only if `GOATFARM_ENVIRONMENT=production` itself is supplied correctly. The audit incident applied five revisions before the wrong target was noticed.

**Recommendation.** Require an explicitly supplied `GOATFARM_MIGRATION_DATABASE_URL` for every online Alembic invocation, including development, or require a separate affirmative opt-in for the localhost default. Before acquiring the advisory lock, print the sanitized host/port/database and current→target revisions. A non-interactive release job can still fail closed without adding a prompt.

### DB-02 — Medium — Retention omits durable high-churn ledgers and audit anchors

**Confidence:** High. **Status:** Statically established; growth impact is inferred.

**Evidence.** Each provider attempt commits a durable budget row/receipt before external I/O ([`backend/app/services/screening/budget.py:59`](../../backend/app/services/screening/budget.py), [`backend/app/services/screening/budget.py:96`](../../backend/app/services/screening/budget.py)); the receipt tables have no time-based lifecycle of their own ([`backend/app/models/screening.py:552`](../../backend/app/models/screening.py), [`backend/app/models/screening.py:567`](../../backend/app/models/screening.py)). The default call cap permits 400 receipts per farm per day ([`backend/app/core/config.py:922`](../../backend/app/core/config.py)), or about 146,000 rows per farm-year at sustained use. `notification_log` is explicitly append-only audit/dedupe state, and completed outbox rows also persist ([`backend/app/models/notifications.py:1`](../../backend/app/models/notifications.py), [`backend/app/models/notifications.py:118`](../../backend/app/models/notifications.py), [`backend/app/models/notifications.py:173`](../../backend/app/models/notifications.py)).

The retention summary and candidate union cover only screening image chains and terminal tasks ([`backend/app/services/retention.py:55`](../../backend/app/services/retention.py), [`backend/app/services/retention.py:149`](../../backend/app/services/retention.py)). It also explicitly retains every screening batch indefinitely ([`backend/app/services/retention.py:22`](../../backend/app/services/retention.py)). Enabling the opt-in sweep therefore does not bound any of these newer ledgers/anchors.

**Impact and preconditions.** With screening or notifications enabled over months/years, table/index size and full-backup/restore time grow monotonically. The risk requires sustained feature use; low-volume or disabled deployments are unaffected. Deleting receipts too early could reopen idempotency/budget windows, and notification logs may have audit value, so indiscriminate deletion would also be unsafe.

**Recommendation.** Define explicit retention/archive periods per table. After the maximum retry/idempotency horizon, delete old `screening_daily_budgets` in bounded farm/date cohorts so receipts cascade. Partition or archive notification logs and completed outbox events according to the audit policy. Either age screening batches after their child chains are gone or document their permanent-retention/storage forecast. Include all new scopes in retention metrics and tests.

### DB-03 — Medium — Database recovery does not recover the screening objects referenced by it

**Confidence:** High. **Status:** Statically established; disaster impact is inferred.

**Evidence.** A screening image row stores only an S3 bucket/key pointer, not the image bytes ([`backend/app/models/screening.py:89`](../../backend/app/models/screening.py), [`backend/app/models/screening.py:173`](../../backend/app/models/screening.py)). Retention delegates deletion of those objects to an independent bucket lifecycle policy ([`backend/app/services/retention.py:14`](../../backend/app/services/retention.py)). The backup path is a PostgreSQL-only `pg_dump` validated with `pg_restore --list` ([`backend/scripts/backup.sh:381`](../../backend/scripts/backup.sh), [`backend/scripts/backup.sh:393`](../../backend/scripts/backup.sh)); restore replays only that database archive ([`backend/scripts/restore.sh:308`](../../backend/scripts/restore.sh), [`backend/scripts/restore.sh:373`](../../backend/scripts/restore.sh)). The documented 24-hour RPO/WAL guidance is likewise database-focused ([`README.md:1141`](../../README.md)).

**Impact and preconditions.** Loss, corruption, accidental deletion, or a lifecycle-policy error in the raw-image bucket can leave otherwise valid restored findings/training history pointing to unavailable evidence. Restoring an older database generation without a coordinated object generation can also yield a cross-system point-in-time mismatch. This does not threaten purely relational herd/finance facts.

**Recommendation.** State the object-store RPO/RTO separately. Enable and test appropriate versioning/replication or an independent backup for the raw screening prefix, retain versions at least as long as database recovery points that can reference them, and record an object manifest/version identifier with recovery artifacts. Quarterly drills should verify representative referenced objects and the intended behavior for deliberately lifecycle-expired images.

### DB-04 — Low — Screening CHECK constraints accept contradictory evidence states

**Confidence:** High. **Status:** Reproduced on disposable PostgreSQL 16; all inserts were rolled back.

**Evidence.** The image/crop comment says only `ERROR` rows carry an error, but the one-way implication merely requires error text when status is `ERROR` ([`backend/app/models/screening.py:105`](../../backend/app/models/screening.py), [`backend/app/models/screening.py:299`](../../backend/app/models/screening.py)). The dimension constraint accepts one null side regardless of the other side's sign ([`backend/app/models/screening.py:115`](../../backend/app/models/screening.py)). The run comment says verdicts exist only for `OK` and failures carry error text, but its constraint limits verdict vocabulary only when status is `OK`; it does not require error text on `ERROR` ([`backend/app/models/screening.py:360`](../../backend/app/models/screening.py)).

A transaction against a fresh head schema successfully inserted and selected back all of these rows before rollback:

```text
screening_images: status=HEALTHY, error='error retained on a healthy image', width=-1, height=NULL
screening_crops:  status=HEALTHY, error='error retained on a healthy crop'
screening_runs:   run_status=ERROR, verdict='invented', error=NULL
```

**Impact and preconditions.** A future buggy service, import, or trusted direct-SQL writer can persist evidence that contradicts the review UI and provider-accuracy/training semantics. Current application paths appear to construct coherent rows, so the precondition is a bypass/regression rather than an ordinary request.

**Recommendation.** Make the error predicate bidirectional, require dimensions to be either both null or both positive, and encode mutually exclusive `OK` versus `ERROR` run payloads (including verdict/error and any intended confidence/latency rules). Add raw-SQL constraint tests for both accepted and rejected state matrices.

### DB-05 — Low — User attribution is not tenant-bound at the database layer

**Confidence:** High. **Status:** Statically established; exploit/bug path is inferred.

**Evidence.** Entity relationships generally use composite tenant FKs; for example, screening findings bind `(farm_id, run_id)` and `(farm_id, crop_id)` ([`backend/app/models/screening.py:424`](../../backend/app/models/screening.py)). In contrast, actor fields bind only to global `users.id`: screening batch creator ([`backend/app/models/screening.py:74`](../../backend/app/models/screening.py)), finding reviewer/history reviewer ([`backend/app/models/screening.py:500`](../../backend/app/models/screening.py), [`backend/app/models/screening.py:540`](../../backend/app/models/screening.py)), health-event creator ([`backend/app/models/health.py:151`](../../backend/app/models/health.py), [`backend/app/models/health.py:187`](../../backend/app/models/health.py)), and ledger creator/voider ([`backend/app/models/finance.py:106`](../../backend/app/models/finance.py), [`backend/app/models/finance.py:116`](../../backend/app/models/finance.py)). PostgreSQL therefore accepts attribution to a valid user who neither owns nor belongs to the row's farm. A simple membership FK is insufficient because owners deliberately are not membership rows ([`backend/app/models/core.py:274`](../../backend/app/models/core.py)).

**Impact and preconditions.** A trusted-writer bug/import can create plausible but cross-tenant audit attribution. Normal API authorization supplies the current farm-authorized user, so this is defense-in-depth rather than a demonstrated request-path tenant escape.

**Recommendation.** Add a reusable deferred trigger/constraint-trigger that accepts an actor only if they are the farm owner or have a retained `(farm_id, user_id)` membership, and apply it to audit-critical actor columns. Preserve inactive/tombstoned membership history so later account lifecycle changes do not invalidate old facts.

### DB-06 — Low — The head migration can hold an unbounded exclusive write lock

**Confidence:** High. **Status:** Static; no production-sized lock-duration test was run.

**Evidence.** `f9a3b7c1d5e2` takes `EXCLUSIVE` on `screening_findings`, changes history constraints, and scans/inserts all eligible legacy findings in the same transaction ([`backend/alembic/versions/f9a3b7c1d5e2_preserve_legacy_screening_reviews.py:22`](../../backend/alembic/versions/f9a3b7c1d5e2_preserve_legacy_screening_reviews.py), [`backend/alembic/versions/f9a3b7c1d5e2_preserve_legacy_screening_reviews.py:39`](../../backend/alembic/versions/f9a3b7c1d5e2_preserve_legacy_screening_reviews.py)). Alembic's 10-second lock timeout limits acquisition wait, but the migration statement timeout defaults to unlimited ([`backend/alembic/env.py:53`](../../backend/alembic/env.py), [`backend/alembic/env.py:124`](../../backend/alembic/env.py)). The README correctly instructs operators to rehearse and quiesce writes for first upgrade to head ([`README.md:160`](../../README.md)), which materially reduces the risk.

**Impact and preconditions.** An upgrade from f8 with a large review corpus can block screening-finding writers for the duration of DDL/backfill, or fail acquisition amid active reviews. It is primarily an operational availability risk when the documented maintenance procedure is not enforced.

**Recommendation.** Encode write quiescence as a release gate rather than relying only on prose, and record rehearsal duration/row count before production. If the table can become large before all deployments reach head, split constraint installation, bounded backfill, and validation into staged revisions.

## Validation performed

- Built a fresh disposable `postgres:16-alpine` database through all 102 revision files to the single head `f9a3b7c1d5e2`; the graph's one branchpoint (`c3e5a9f1d7b4`) converged at its merge revision.
- `alembic check` on that fresh head returned `No new upgrade operations detected`, establishing current ORM/schema parity for Alembic-visible objects.
- Downgraded the empty disposable database from head to base successfully and verified zero application tables remained.
- Checked offline rendering per migration direction. The only rejected steps were the four declared online-only live-query preflights in [`backend/alembic/env.py:61`](../../backend/alembic/env.py); a full offline range failed before SQL emission, while a supported recent range rendered.
- Reproduced DB-04 inside a transaction and rolled it back. Removed the disposable container after validation.

## Strengths and limits

The database baseline is otherwise strong: the app pool uses pre-ping, bounded acquisition, recycling, TLS propagation, and a server-side statement timeout ([`backend/app/db.py:117`](../../backend/app/db.py)); migrations use transactional DDL, a fail-fast DDL lock timeout, a shared migration/restore advisory lock, and guaranteed pool disposal ([`backend/alembic/env.py:108`](../../backend/alembic/env.py), [`backend/alembic/env.py:132`](../../backend/alembic/env.py)). Tenant entity relationships broadly use composite FKs, money/weights use bounded exact numerics, and timestamps consistently use explicit UTC server expressions. Downgrades that would discard durable evidence often fail closed.

Backup/restore handling is notably defensive: private staging, no credential-bearing child arguments, archive validation, authenticated encryption in production/off-site use, checksum binding, no-overwrite publication, revision-floor checks, empty-target enforcement, the shared writer lock, single-transaction restore, and post-restore revision validation are all present ([`backend/scripts/backup.sh:336`](../../backend/scripts/backup.sh), [`backend/scripts/restore.sh:154`](../../backend/scripts/restore.sh), [`backend/scripts/restore.sh:229`](../../backend/scripts/restore.sh), [`backend/scripts/restore.sh:308`](../../backend/scripts/restore.sh)).

Limits: this audit did not use production-sized data, exercise concurrent request traffic during DDL, or perform an end-to-end signed/encrypted backup, S3 publication, and restore drill. `alembic check` cannot detect trigger/function semantic drift beyond metadata it models. The local-database incident means no claim is made that all audit activity was isolated; its known effects and uncertainty are documented above.

## Counts

Findings: **6 total — 0 Critical, 0 High, 3 Medium, 3 Low.**  
Reproduced: **2** (`DB-01`, `DB-04`). Static/inferred: **4**.  
Separate audit-execution incident: **1**, fully described above and also the reproduction basis for `DB-01`.
