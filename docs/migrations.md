# Database migrations

[Documentation index](README.md) · [Project overview](../README.md)

Every online Alembic invocation must set `GOATFARM_MIGRATION_DATABASE_URL` explicitly,
including development and tests; the writable localhost application default is never an
implicit migration target. Alembic prints only the sanitized host/port/database and
current→head revision set before it acquires its writer lock. Production releases must
set the URL to a separately privileged database identity used only by Alembic. The
long-running API should use a credential without schema/DDL privileges. Compose supplies
only that URL—not the API credential—to the migration job. Alembic uses a migration-only
settings projection, so `GOATFARM_ENVIRONMENT=production` refuses a missing migration
URL and every `GOATFARM_DB_SSLMODE` except `verify-full` before creating its engine,
without requiring unrelated cookie/JWT/HMAC settings. Libpq backup and restore jobs
enforce the same production mode. Modes such as `require` encrypt the wire but can leave
the server unauthenticated when no trusted root is configured. Migrations do **not**
inherit the request-path `GOATFARM_DB_STATEMENT_TIMEOUT_MS` budget: the Alembic
connection applies `GOATFARM_MIGRATION_STATEMENT_TIMEOUT_MS` instead, 900000 ms (15
minutes) by default. Raise that finite bound only after measuring a rehearsal. A fixed
10-second `lock_timeout` still applies, so DDL that cannot acquire its lock fails fast
instead of queueing behind live traffic.

Offline migration support boundary: `alembic --sql` rejects ranges crossing upgrades
`c3d4e5f6a7b1` / `cad1e2f3a4b5` or downgrades `d4e5f6a7b8c9` / `f3d4e5f6a7b8` before
emitting any SQL. Those immutable historical steps require live-data query preflights; a
full `base:head` export is therefore online-only. Rehearse these ranges on a disposable
restored PostgreSQL database and run them online in the maintenance window. Supported
narrow ranges still emit their complete fail-closed SQL guards. No historical preflight
is omitted or rewritten to make an offline export appear complete.

The `f3d4e5f6a7b8` release migration validates the legacy personal-task role invariant
and builds a transactional partial index on `tasks`. Its ordinary `CREATE INDEX` takes a
PostgreSQL `SHARE` lock that blocks task writes while the build runs. Schedule that
one-shot upgrade in a maintenance window, after the migration job has exclusive rollout
ownership, rather than during live task traffic. The later `a1b2c3d4e5f7` repairs the
personal-task rows that revision's PENDING-only backfill skipped:
`ck_tasks_user_assignment_has_role` also fires on an UPDATE that moves a row back *into*
`PENDING`, which is exactly what rejecting a completed cleaning duty does, so an
unrepaired row made that duty permanently un-rejectable.

Treat the first upgrade of any existing deployment to the current head as a maintenance
operation, not as an online rolling migration. The intervening history includes
feed-quantity table rewrites, potentially full-table data preflights and backfills,
constraint validation, session cleanup, and non-concurrent index creation. First
rehearse the complete upgrade against a current restored copy, record its runtime and
lock impact, take a verified backup, quiesce application writes, and give one migration
job exclusive ownership until `alembic check` passes. An upgrade crossing `f9a3b7c1d5e2`
or the screening privacy-state backfill `fd4e5f6a7b8c` on an existing database refuses
to run unless `GOATFARM_MIGRATION_WRITES_QUIESCED=true`; the preflight reports the
eligible legacy-review row count so the rehearsal and production facts can be recorded.
Resume the API and screening worker only after the migration succeeds.

## Rollout ownership and legacy preflights

Supported Alembic and restore jobs share a database advisory lock. Quiesce manual schema
changes and give one job exclusive rollout ownership until its final Alembic marker
check succeeds.

Migration `b7c8d9e0f1a2` fails if a legacy health event has an authority-notification or
isolation date without scheduled-disease suspicion. Preserve those dates, reconcile each
reported event by confirming `suspected_scheduled_disease=true` and a nonblank
`disease_target`, and retry. The error reports a count and sample event IDs.

The first application of security migration `f4e5f6a7b8c9` requires draining
password-bearing worker-create traffic and stopping every pre-HMAC API instance before
the purge. Start the new image only after migration succeeds. Otherwise an old process
can insert another unkeyed fingerprint after the purge. An older backup must be restored
and migrated in isolation before a post-HMAC service can accept traffic.

## Downgrades

Alembic downgrade walks below `f7d8c9b0a1e2` and `b6d8f0a2c4e6` drop the tenant-guard
composite foreign keys, so a rollback window on a live database loses DB-level
cross-farm guards until the walk completes — quiesce app writers for the whole walk (the
runbook protocol) and prefer stopping production rollbacks at the last tenant-guard
revision rather than downgrading to base.

## Historical finance preflight

The first shipped form of revision `c8f1d3a5e709` (same id, since corrected in-repo)
rewrote non-finite money to `amount = 0` and nulled five optional price columns instead
of refusing; a database that migrated under that variant carries no marker of the
rewrite. Any database whose provenance spans August 2026 images should be probed during
the pre-migration health check — a nonzero count means ledger rows may have been
silently neutralized and must be reconciled against backups before the upgrade proceeds:

```sql
-- Rows a damaged c8f1d3a5e709 pass may have rewritten. NULLed optional
-- prices are legitimate on their own; the signal is their co-occurrence
-- with a zero amount on rows whose provenance implies a real booking.
SELECT count(*) AS suspect_neutralized_rows
  FROM transactions
 WHERE amount = 0
   AND type = 'EXPENSE'
   AND created_at < '2026-08-20';
```

A related invariant of the same program: applied Alembic revisions are immutable — CI
fails any change that edits, renames, or deletes an existing
`backend/alembic/versions/*.py` file instead of appending a new revision.

## Historical migration limitations

Applied revisions remain immutable. Account for these limitations when rehearsing an
upgrade or downgrade:

- Three CHECK swaps in older revisions (`b1c2d3e4f5a6`, `c1d2e3f4a5b6`, `bd201c1cdc1b`)
  take ACCESS EXCLUSIVE over a full-table scan. Adding `NOT VALID` then validating in
  the **same transaction** also retains that earlier ACCESS EXCLUSIVE lock until commit;
  the historical descriptions in `f1e2d3c4b5a6` and `f8a2c4e6b1d9` do not make those
  swaps nonblocking. Rehearse and quiesce traffic for these applied revisions. Future
  changes on hot tables must deliberately separate DDL and validation transactions,
  measure both phases and document their actual lock windows.
- `c4f6a8b0d2e5`'s jsonb preflight parses every row in Python before the DDL. Fine at
  current sizes; a future variant should bound the scan (keyset batches) or push the
  parse into SQL.
- `e1f2a3b4c5d6`'s premium backfill skips policies whose recorded horizon predates their
  start (zero-length periods); those policies show no historical premium rows.
- Legacy task rejection notes and malformed kidding-link references were rewritten by
  older cleanups without an archive table; the audit trail for those specific edits
  lives in the dated audit bundles only.
- No `naming_convention` is attached to the Alembic target metadata: early revisions
  create unnamed FK constraints that later revisions drop by their PostgreSQL default
  names, so a convention retrofit renames them at replay time and aborts the chain (see
  `backend/alembic/env.py`). It can only arrive with a chain-wide rename revision.

See [Deployment](deployment.md) for the rollout commands and
[Backup and recovery](backup-recovery.md) for restore acceptance checks.
