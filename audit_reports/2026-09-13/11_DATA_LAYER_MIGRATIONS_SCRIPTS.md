# Red Team Audit — Part N: Data Layer, Migrations, Ops Scripts

**Report ID:** 11_DATA_LAYER_MIGRATIONS_SCRIPTS
**Date:** 2026-09-13
**Scope:** Units N1–N7 of `audit_reports/2026-09-13/00_RED_TEAM_AUDIT_SCOPE.md`
**Auditor posture:** authorized red team; implementation review + attack-path verification against the shipped code only. Every finding below cites file:line evidence. Hypotheses that did not survive code inspection are recorded as *attacked & held*, not as findings.

---

## 1. Methodology

1. **Full reads** of every N-scope file: `backend/app/db.py`, `backend/alembic/env.py`, `backend/alembic.ini`, all of `backend/scripts/` (`backup.sh`, `restore.sh`, `backup_env.sh`, `libpq_url.py`, `dotenv_value.py`, `backup_flock.py`, `backup_legacy_lock.py`, `pinned_copy.py`, `healthcheck.py`, `export_openapi.py`, `dump_daily_ops.py`).
2. **Programmatic chain analysis** of all 54 revisions under `backend/alembic/versions/` (parent/child graph, single-head/single-lineage, missing-parent detection), plus full reads of the load-bearing revisions (`ed5efe13a516`, `f5b1a09c8d7e`, `f7d8c9b0a1e2`, `b6d8f0a2c4e6`, `d3b5f7c9e024`, `e7f9a1b3c5d8`, `c3d4e5f6a7b1`, `d4e5f6a7b8c9`, `c4f6a8b0d2e5`, `e6f8a0b2c4d7`, `c1d2e3f4a5b6`, `f2c3d4e5f6a7`, `c2a4e6b8d013`, `bd201c1cdc1b`, `d1e2f3a4b5c6`, `f8a2c4e6b1d9`, `f9b3c7d1e5a2`) and targeted reads of the remaining corrective migrations' data-rewrite blocks.
3. **Cross-checks into live call sites** to verify hypotheses: JSON-column write patterns (mutation vs replace), simulation/planner/ops-sim connection release before CPU work, background-loop session usage, engine disposal, advisory-lock namespace inventory (app + triggers + alembic + restore), model↔migration constraint parity, GPG status-file parsing semantics, libpq parameter precedence rules.
4. **Test review:** `test_deployment_artifacts.py` (backup/restore/helper surface, ~40 tests), `test_jsonb_columns.py`, `test_online_index_recovery.py`, `test_domain_check_constraints.py`, `test_ops_migration_integrity.py`, `test_feed_quantity_numeric.py`, `test_monetized_cull_migration.py`, `test_domain_audit_migration.py`; CI roundtrip steps in `.github/workflows/ci.yml:96-103`.

Severity scale: Critical = cross-tenant/authz/backup-capture; High = data corruption, integrity bypass, silent lost updates; Medium = defense-in-depth, availability, info disclosure; Low = hardening; Info = observations.

---

## 2. Findings table

| ID | Severity | Unit | Title |
|----|----------|------|-------|
| RT-N-1 | Medium | N6 (affects N4/N5) | `libpq_url.py` sensitive-query denylist misses libpq TLS-trust parameters; URL query can redirect the enforced `verify-full` gate |
| RT-N-2 | Low | N4 | Off-site backup failure cleanup is best-effort and remote retention is unbounded (orphaned archive/sidecar pairs accumulate) |
| RT-N-3 | Low | N2 | CHECK-narrowing downgrades (`f9b3c7d1e5a2`, `d1e2f3a4b5c6`) lack the house data preflight — mid-rollback abort with a raw `CheckViolation` |
| RT-N-4 | Low | N4/N6 | Silent "development" downgrade when `backend/.env` is absent and env vars unset declassifies backup/restore safety gates |
| RT-N-5 | Low | N3 | Tenant-guard composite FKs are absent during downgrade walks (rollback window with live writers unguarded at the DB level) |
| RT-N-6 | Info | N1 | Pool exhaustion above 15 concurrent connection-holding requests queues 30 s then surfaces as 500 (by-design backstop; verified bounded) |
| RT-N-7 | Info | N3 | `bd201c1cdc1b` permanently deletes BUFFALO_DAIRY tenants and the milk ledger; downgrade restores schema only |
| RT-N-8 | Info | N2 | Offline (`--sql`) migration generation skips data preflights by design (`c4f6a8b0d2e5`, `f2c3d4e5f6a7`, health-compliance revision) |
| RT-N-9 | Info | N4 | Backup dumps contain Argon2 password hashes (and refresh-session identifiers); plaintext local backups are permitted outside production/off-site |
| RT-N-10 | Info | N7 | `healthcheck.py` hardcodes port 8000 with a single 4 s attempt, no retry; `dump_daily_ops.py`/`export_openapi.py` are contract-pinned developer tools |

No Critical and no High findings were evidenced in units N1–N7.

---

## 3. Detailed findings

### RT-N-1 (Medium) — libpq TLS-trust parameters bypass the URL identity denylist

**Evidence:**
- `backend/scripts/libpq_url.py:19-31` — `SENSITIVE_QUERY_KEYS = {dbname, host, hostaddr, passfile, password, port, service, servicefile, sslmode, sslpassword, user}`; rejected keys are refused at `:98-100`.
- `backend/scripts/libpq_url.py:106-108` — every *other* query parameter is preserved verbatim into the `postgresql://…` URL handed to `pg_dump`/`psql` (`urlunsplit(("postgresql", safe_netloc, path, parts.query, ""))`).
- `backend/scripts/backup.sh:341` / `backend/scripts/restore.sh:232` — `export PGSSLMODE="${DB_SSLMODE}"`, where `DB_SSLMODE` is gated to `verify-full` in production (`backup_env.sh` shared ladder; `backup.sh:114-117`, `restore.sh:116-119`).
- libpq documented precedence: connection-string parameters override `PGSSLMODE`/environment.

**Exploit sketch:** an operator-supplied `GOATFARM_DATABASE_URL` (or `GOATFARM_RESTORE_DATABASE_URL`) of the form `postgresql://u@db:5432/prod?sslrootcert=/shared/attacker-ca.pem` passes `libpq_url.py` untouched. The scripts' production gate still exports `PGSSLMODE=verify-full`, but certificate verification now runs against the attacker-controlled CA — the gate verifies *something*, just not against the trust anchor the operator believes was used. `sslcert`/`sslkey` (client-credential redirection), `gssencmode`, `channel_binding`, `sslnegotiation` and `requirepeer` pass through the same hole. The denylist was built precisely to stop the URL overriding "connection identity" (`:100`: "may not override connection identity") — TLS trust material *is* connection identity and is only partially covered.

**Impact:** defense-in-depth failure of the production TLS invariant for the most privileged database operations (full dump, full restore). Not remotely exploitable: the URL is operator-controlled, and an adversary who can set the URL could equally repoint the host (which the denylist does not prevent either — the URL legitimately carries host/user/dbname). The realistic harm is a confused-deployed pipeline or a URL pasted from a vendor wiki silently downgrading server authentication.

**Fix:** extend `SENSITIVE_QUERY_KEYS` with `sslrootcert`, `sslcert`, `sslkey`, `sslcrldir`, `sslcrl`, `gssencmode`, `channel_binding`, `sslnegotiation`, `krbsrvname`, `requirepeer`, `passfile` (already present) — or invert the design: whitelist only `application_name`-class parameters and reject everything else.

---

### RT-N-2 (Low) — best-effort off-site cleanup; no remote retention

**Evidence:** `backend/scripts/backup.sh:52-59` — on failure before `REMOTE_COMPLETE`, `aws s3 rm "${REMOTE_CHECKSUM}" >/dev/null 2>&1 || true` then the archive, both swallowing errors. `backup.sh:452-471` — the `GOATFARM_BACKUP_KEEP` retention prune operates only on `"${DEST_DIR}"/goatfarm-*.dump[.gpg]` local artifacts; nothing prunes `S3_URI`.

**Exploit sketch / impact:** a torn run (checksum upload fails, or the cleanup `rm` itself fails — e.g. transient credentials revoked mid-run) leaves a remote archive without its sidecar forever. Restore refuses sidecar-less pairs (`restore.sh:68-71`), so integrity is not weakened, and per-run `mktemp` suffixes prevent key collisions; the residue is availability/cost: unbounded off-site growth and accumulating orphaned objects with no operator signal. Local retention math itself is correct (delete indices `KEEP..end` ⇒ exactly `KEEP` survivors; no off-by-one), and the sort/deletion is array-based, immune to newline-filename splitting.

**Fix:** log cleanup failures loudly with object keys; consider an S3 lifecycle rule or a listing-based remote prune bounded by `GOATFARM_BACKUP_KEEP`.

---

### RT-N-3 (Low) — CHECK-narrowing downgrades without data preflight

**Evidence:**
- `f9b3c7d1e5a2_widen_kid_count_check_for_goat_quadruplets.py:36-42` — downgrade re-creates `kid_count_detected BETWEEN 1 AND 3` as a fully validated CHECK with no preflight for existing quadruplet rows.
- `d1e2f3a4b5c6_widen_gestation_check_constraints.py:55-70` — same pattern narrowing 350→200-day bounds.
- Contrast with the house pattern: `e6f8a0b2c4d7:54-76` preflights for monetized culls before narrowing; `d4e5f6a7b8c9:53-60` refuses downgrade while tombstones exist; `c4f6a8b0d2e5:54-100`, `f2c3d4e5f6a7:37-91`, `c1d2e3f4a5b6:482-495` preflight before widening.

**Impact:** the CI roundtrip (`ci.yml:96-103` upgrade→downgrade base→upgrade) runs against an empty database, so a rollback on a *populated* production DB aborts mid-walk with a raw `CheckViolation` naming only a constraint, not the offending rows — the exact operator-hostile failure mode the preflight pattern was built to prevent. Fail-closed, no data loss (transactional DDL rolls back), hence Low.

**Fix:** add the standard `SELECT … LIMIT n` preflight raising the house-style `RuntimeError` naming row ids before each narrowing `create_check_constraint`.

---

### RT-N-4 (Low) — absent `.env` silently declassifies backup/restore runs

**Evidence:** `backend/scripts/backup_env.sh:21-55` — when neither `GOATFARM_DB_SSLMODE` nor `GOATFARM_ENVIRONMENT` is exported, both are resolved from one descriptor-pinned `backend/.env` snapshot; `dotenv_value.py:60-63` maps `FileNotFoundError` to an empty mapping, and the snapshot mode then substitutes the *defaults* `disable` / `development`. The GPG requirement gate is `backup.sh:131-140` (`ENVIRONMENT == production || S3_URI non-empty`).

**Impact:** on a cron host where the safety classification lived only in `backend/.env` and that file is removed (or the job's cwd/`SCRIPT_DIR` layout changes so the path misses), nightly backups silently continue as "development": plaintext local archives of Argon2 password hashes with no GPG, no off-site push, SSLMODE `disable`. The descriptor-pinning work prevents *split* reads (old+new mixing), not *absence*. The application would also boot as development in that world, so this requires a scripts-only deployment divergence — real for cron-run backup jobs that never import the app.

**Fix:** make the `.env`-absent case require an explicit `GOATFARM_SAFETY_DEFAULTS=development` opt-in (or fail) instead of defaulting the classification down.

---

### RT-N-5 (Low) — downgrade walks temporarily remove tenant-guard composite FKs

**Evidence:** `f7d8c9b0a1e2_tenant_composite_foreign_keys.py:358-372` and `b6d8f0a2c4e6_history_tables_tenant_foreign_keys.py:105-113` — downgrades drop all `(farm_id, <parent>)` composite constraints (and `kid_entries.farm_id` entirely) before the older revisions run.

**Impact:** during a downgrade-then-upgrade rollback on a live database, application writes in the window are no longer cross-farm-guarded at the DB level; the API's own farm scoping still applies (no API path inserts cross-tenant parents — verified by the composite-FK preflights originally finding zero legacy violations), so this is a defense-in-depth gap in an operational window that the runbook (quiesce app during migration) is expected to keep empty. Note only.

**Fix (optional):** none needed beyond runbook; or stop downgrade at the last tenant-guard revision rather than to base in production rollback procedures.

---

### RT-N-6 (Info) — pool exhaustion behavior verified and bounded

`backend/app/db.py:76-90` — pool 5 + overflow 10 = 15 connections, `pool_timeout` 30 s; `config.py:112-115`. Beyond 15 concurrently connection-holding requests, SQLAlchemy raises `TimeoutError` (not `OperationalError`) → unhandled → 500 after a 30 s queue. Verified mitigations that keep 15 sufficient for the design point: single-process deployment (`main.py:330-337` warns on multi-worker), simulation/planner/ops-sim roll back and release the connection *before* CPU work (`api/simulation.py:426`, `:674`; `api/planner.py:197`; `api/ops_simulation.py:60`), the 5 background loops each hold a session only briefly per cycle, `pool_pre_ping=True` survives DB restarts, `statement_timeout=30000` is a per-connection `server_settings` (applies to every pooled session; migrations are exempt via the separate engine, below), and the engine is disposed on shutdown (`main.py:418-419`). `pool_recycle=1800` is inert risk. Note `server_settings` is incompatible with pgbouncer transaction pooling — no such tier exists in the deployment (direct Postgres 16).

---

### RT-N-7 (Info) — goat-only pivot is an irreversible data migration

`bd201c1cdc1b_goat_only_simplification.py:57-68` deletes every BUFFALO_DAIRY farm's rows child-first and drops `milk_records`; its own downgrade docstring (`:134-142`) states deleted tenants are not restored. Transactional and preflight-free by design (a `WHERE farm_type='BUFFALO_DAIRY'` DELETE cannot mismatch). Operators must take a backup before upgrading past `a1b2c3d4e5f6` — `backup.sh` exists for exactly this; nothing in the migration prompts for one.

### RT-N-8 (Info) — offline migration generation skips data preflights

`c4f6a8b0d2e5:91-97`, `f2c3d4e5f6a7:83-88` emit a `-- WARNING` SQL comment instead of preflighting when `context.is_offline_mode()`; applying such a generated script to an unaudited database fails mid-DDL (transactional rollback). Deliberate trade-off, documented in-file. `alembic/env.py:48-58` offline mode also lacks the advisory lock (generation only — correct).

### RT-N-9 (Info) — dump contents vs secret material

The dump contains `users.password_hash` (Argon2), refresh-session jtis/token hashes, and idempotency HMAC *fingerprints* — but no JWT private/public keys (filesystem key dir per `config.py:20-31`, never a DB column) and no idempotency HMAC secret (env-only `SecretStr`, `config.py:138`). Gate verified: GPG (full-fingerprint recipient + signer, sign-then-encrypt) is mandatory when `ENVIRONMENT=production` **or** any S3 URI is set (`backup.sh:131-145`); production restores require a `.gpg` artifact (`restore.sh:128-131`). Plaintext local backups are permitted only in non-production, local-only runs — dev convenience, files are `0600` inside a `0700` destination (`umask 077`, `install -d -m 0700`, `chmod 0600` on every artifact).

### RT-N-10 (Info) — N7 tooling observations

`healthcheck.py` connects to `127.0.0.1:8000` (hardcoded port; container contract) with `timeout=4`, one attempt, and synthesizes `healthcheck.<domain>` for wildcard entries (`:22-25`) — correct against Starlette's requires-a-label wildcard matching; covered by `test_deployment_artifacts.py:1437`. `export_openapi.py` deterministically writes `shared/openapi.json` (CI freshness gate). `dump_daily_ops.py` pins scenario dates to the 2026-09-03 commit for byte-stable regeneration; purely offline.

---

## 4. Per-unit attacked-&-held notes

### N1 — Engine/pool/session (`backend/app/db.py`)
- **Mutation tracking (JSONB lost-update hypothesis): HELD — no vulnerability.** No `MutableDict`/`MutableList` is needed: every JSON-column writer *replaces* the ORM string attribute wholesale (`api/team.py:1216` `role.permissions = json.dumps(...)`; `api/planner.py:394,397,417`; `api/simulation.py:627` `model_dump_json()`); no call site mutates a parsed structure in place and expects a flush. `JSONText.process_bind_param` (`db.py:47-53`) parses str binds so nothing double-encodes into a JSON string scalar; the `result_processor` (`db.py:55-65`) accepts both codec shapes. Invalid-JSON assignment fails loudly at bind time.
- **statement_timeout separation: HELD.** App engine: `db.py:87` `server_settings.statement_timeout = db_statement_timeout_ms` (30 s, `config.py:115`). Alembic runs a *separate* engine with `migration_statement_timeout_ms` (`env.py:77-84`) — the app sessionmaker (`db.py:94-100`) sets no per-session override, and migrations never touch `get_sessionmaker()`.
- **Engine lifecycle: HELD.** `reset_engine` is test-hook-only (docstring `db.py:104`), catches cross-loop `RuntimeError`; production disposes in lifespan shutdown (`main.py:418-419`).

### N2 — Alembic env & 54-revision chain
- **Chain integrity: HELD — verified programmatically.** 54 revisions, one root (`ed5efe13a516`), single head (`c4f6a8b0d2e5`), zero branch points, zero missing parents, chain walk covers all 54. CI exercises upgrade→check→downgrade base→upgrade→check (`ci.yml:96-103`).
- **Advisory-lock coordination: HELD.** `env.py:45,87-93` `pg_try_advisory_lock(718204614)` matches `restore.sh:294` `pg_try_advisory_xact_lock(718204614)` (session- and xact-level advisory locks on the same key conflict — serialization is real). `connectable.dispose()` in a `finally` (`env.py:100-103`) guarantees release on every failure path; the `SET lock_timeout` autobegin-transaction is committed before migrations join it (`env.py:94-98`).
- **`lock_timeout` vs unbounded DDL: correct as documented.** `lock_timeout='10s'` bounds lock *acquisition* queueing only; `migration_statement_timeout_ms` defaults 0 (`config.py:123`) so long rewrites/`VALIDATE CONSTRAINT` are not cancelled mid-flight.
- **Least privilege: HELD.** `env.py` reads only `get_migration_settings()`; `MigrationSettings` (`config.py:47-78`) is a 5-field projection (`extra="ignore"`) that refuses production without `verify-full`.
- **Injection sweep: HELD.** Every f-string `op.execute` interpolates module-level constants only (e.g. `e7f9a1b3c5d8:191-222`, `c3d4e5f6a7b1:37-38`, `c2a4e6b8d013:80-93`, `f8a2c4e6b1d9:60`); no migration interpolates row data or request data into SQL. Data-rewrites use parameterized `sa.text(...)` binds or static identifiers from in-file tuples.
- **`alembic.ini`: no secrets** (logging config only). `compare_server_default=True` (`env.py:55,63`) is drift noise, not drift silence.
- **CONCURRENTLY handling: HELD.** The online-index revisions use `op.get_context().autocommit_block()` (`c2a4e6b8d013:84`, `e5f6a7b8c9d0`, `f1b2c3d4e5f6`) and detect/reject invalid same-named remnants *before* an idempotent rebuild (`c2a4e6b8d013:34-80`); `test_online_index_recovery.py:42` pins the behavior.

### N3 — Schema invariants
- **Composite tenant-FK program: COMPLETE — no remaining gap found.** `f7d8c9b0a1e2` covers farm_memberships→roles, animals→purchase_batches/dam/sire, health_events→animal/batch, breeding→doe/buck, kidding→doe/breeding, tasks→animal/batch/breeding/assigned_role/assigned_user(membership), transactions→related_animal/correction_of, kid_entries→kidding/animal (with backfilled `farm_id`); `b6d8f0a2c4e6` closed the last two history tables (weight_records, bucket_moves) with the NOT VALID + VALIDATE online recipe; `c5a8e1f3b7d2`/`b3d7f1a5c9e2` had added the milk_records composite while that table lived; the model layer mirrors every one (models/core.py:199-204, animals.py:55-68/432-435/468-471, breeding.py:148-156/219-227/271-279, health.py:36-44/189-197, tasks.py:41-64, finance.py:70-84). Remaining single-column FKs point at *global* parents only (users, farms, feed_recipes.code) — correctly out of scope. The ORM even derives `weight_records`/`bucket_moves.farm_id` for legacy writers via a `before_flush` listener that refuses to guess (`models/animals.py:598-628`).
- **State-machine uniques exist in the DB:** `uq_breeding_open_pregnancy` partial unique (`d8f2b6a41e90:82`, mirrored models/breeding.py:159), `uq_kidding_breeding_record` (`ed5efe13a516:429`), unique `refresh_sessions.jti` (`b3e91c47a2f5:43`), `ck_breeding_records_outcome_state` machine CHECK (`c1d2e3f4a5b6:450-458`). API/`kid_count` and gestation bounds were widened in lockstep with the service caps (`f9b3c7d1e5a2`, `d1e2f3a4b5c6`, re-tightened post-pivot by `bd201c1cdc1b:116-131` after the buffalo data deletion, which is the only sound order).
- **Trigger advisory-lock namespaces: no collision.** Tag-namespace triggers use single-key `pg_advisory_xact_lock(_shared)(farm_id)` (`d3b5f7c9e024:39-83`); the kidding service deliberately pre-acquires the same exclusive key to avoid the SHARE→EXCLUSIVE upgrade deadlock (`services/kidding.py:118-131`); all other API locks use the two-argument namespaced form (`4711`-`4715`: tasks/planner/simulation/team) which occupies a disjoint 64-bit keyspace; seed uses `718204613`, release-writer `718204614`. Single-key farm ids would need to reach ~7.2×10⁸ to collide — impossible.
- **Trigger lock order: consistent.** `e7f9a1b3c5d8` locks the doe (Animal) before the BreedingRecord in an alphabetically-first BEFORE INSERT trigger, restricts the reproductive trigger to INSERT, and makes farm/doe/buck and farm/doe/breeding relationships immutable via guard triggers — matching the API's documented Animal-first order; the date-mortality guard avoids parent locks entirely.
- **Tombstones: HELD.** `d4e5f6a7b8c9` CHECK enforces scrubbed profile on `deleted_at`, RESTRICTs pregnancy-loss attribution, and its downgrade refuses while tombstones exist (`:53-60`).
- **Monetized cull: HELD.** `e6f8a0b2c4d7` swaps `ck_animals_sale_fields` under NOT VALID → VALIDATE → rename while the stricter constraint still protects rows; downgrade preflights (offline DO-block *and* online bind path).

### N4 — `backup.sh`
- **Held:** `set -euo pipefail` + `umask 077`; URL never in child argv/env (stdin → `libpq_url.py`, `backup.sh:151-156,332-341`); passfile 0600 O_EXCL/O_NOFOLLOW in a 0700 mktemp work dir, deleted immediately after `pg_dump` (`:331-370`); parse verification `pg_restore --list` pre-encryption (`:379`); checksum sidecar of the *ciphertext*, published sidecar-first with rollback cleanup of a half-published pair (`:407-416`, cleanup `:60-63`); no-overwrite guards local and remote (`:360-363`, `:436-443`); unique per-run suffix defeats the same-second multi-host S3 key race (`:348-354`); retention off-by-one checked (keeps exactly `KEEP`); GPG recipient≠signer roles with both-or-neither and full 40/64-hex fingerprints (`:126-149`); flock on a persistent inode with type/inode revalidation and FIFO-safe `<>` open rationale (`:158-185`, `backup_flock.py:27-57`); the legacy mkdir-lock migration is snapshot-token verified (quarantine/reclaim/release paths in `backup_legacy_lock.py`, fail-closed `renameat2`/`renamex_np` with no racy fallback `:261-279`, extensively covered by `test_deployment_artifacts.py:414-903`).

### N5 — `restore.sh`
- **Held:** empty-DB-only enforced twice — preflight (`:233-271`) and again *inside* the single restore transaction under `pg_try_advisory_xact_lock(718204614)` (`:288-342`), so a concurrent supported writer cannot interleave; `GOATFARM_RESTORE_CONFIRM` must equal the derived target DB name (`:179-182`); target URL over stdin, all credential envs unset before children (`:17-29,170-178`); checksum sidecar is descriptor-pinned, size-bounded (≤512 B), exactly one record, name-matched, hex-strict (`:55-113` + `pinned_copy.py`); GPG signature verified from `--status-fd` with full-fingerprint equality (signing-subkey or primary) and requires exactly one valid, matching signature with zero BADSIG/ERRSIG-class markers (`:193-221`); decrypted archive re-parsed with `pg_restore --list` before any DB connection (`:229-230`); restore executes as one `--single-transaction` with `SET LOCAL lock_timeout` and `--exit-on-error` rendering (`:283-342`); post-restore Alembic single-revision sanity (`:346-363`); superuser not required (no extensions in schema; advisory locks need no catalog privileges — in-file comment `:274-282`). Partial-visibility race with an app connecting mid-restore is impossible: readers see empty-or-complete because the restore is one transaction.

### N6 — helpers
- **Held:** `libpq_url.py` — password via stdin, control-char rejection, single-dbname enforcement, `postgresql+asyncpg` scheme normalization, passfile escaping of `\` and `:`, wildcard port when URL omits it (`:44-125`); the only gap is RT-N-1. `dotenv_value.py` — FIFO rejection via O_NONBLOCK+fstat, fd pinning with before/after dev/ino/size/mtime/ctime identity, 1 MiB bound enforced twice, dotenv-grammar parity with pydantic-settings, case-insensitive last-wins normalization (`:21-149`). `backup_flock.py` — regular-file + same-inode validation of the inherited FD before flock, revalidation after acquisition, chmod 0600. `backup_legacy_lock.py` — snapshot tokens bind directory+file identity and content; every move/delete is identity-verified with race restoration that never overwrites a new claimant. `pinned_copy.py` — O_NOFOLLOW/O_NONBLOCK open, size-bounded copy, growth probe byte, before/after metadata equality, O_EXCL destination cleaned on failure (`:27-90`).

### N7 — `healthcheck.py` / `export_openapi.py` / `dump_daily_ops.py`
- **Held** (details in RT-N-10): wildcard-host synthesis is correct for Starlette semantics; OpenAPI export is deterministic and CI-gated; daily-ops dumps are pinned-date deterministic and DB-free.

---

## 5. Regression-test suggestions

1. **RT-N-1:** extend `test_deployment_artifacts.py::test_url_helper_uses_stdin_and_writes_escaped_private_passfile` with a case asserting `?sslrootcert=...` (and each newly denied key) is rejected by `libpq_url.py`.
2. **RT-N-3:** data-bearing downgrade test — insert a `kid_count_detected = 4` row, run `alembic downgrade -1` from `f9b3c7d1e5a2`, assert the operator-actionable preflight error (after the fix) rather than `CheckViolation`.
3. **RT-N-4:** assert `dotenv_value.py --snapshot` on an absent file fails (or requires opt-in) when invoked from a "production-classified" backup run — after the fix decision.
4. **RT-N-2:** simulate a failed checksum upload with revoked credentials and assert the run's stderr names the orphaned object keys.

---

## 6. Conclusion

Units N1–N7 are the most hardened surface audited so far in this campaign: the backup/restore pair implements a verifier-visible chain (pin → checksum → signature → single-transaction guarded replay) with credential hygiene that survives argument-list and environment inspection, and the migration chain is linear, preflighted on every data-rewriting revision, and serialized against restore through a shared advisory lock. No cross-tenant, authorization, or backup-capture vulnerability was found. The residue is one Medium defense-in-depth gap in the libpq URL trust-parameter denylist and a set of Low/Info operational hardening notes documented above.
