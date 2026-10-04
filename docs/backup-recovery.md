# Backup and recovery

[Documentation index](README.md) · [Project overview](../README.md)

Commands in this runbook start at the repository root unless they explicitly change
directory. Host prerequisites and scheduled service installation are in the [Operations
baseline](../ops/README.md).

## Backup artifacts and recovery inventories

Run `backend/scripts/backup.sh` nightly against production and store the dump off-host.
The script writes `pg_dump` into a private same-filesystem temporary directory, proves
that `pg_restore --list` can parse it, then publishes the archive, its exact-name
SHA-256 sidecar, and a bound `.recovery.json` whole-system inventory. Each archive name
includes an unpredictable run suffix, so independently locked hosts cannot race on one
S3 object key. Local and S3 publication write the sidecars first and the archive last as
the commit marker, so a lister never sees a candidate archive before its recovery
metadata is durable. A destination-wide lock prevents overlapping jobs, and failure
traps remove plaintext, unpublished files, and the owned lock. `GOATFARM_BACKUP_KEEP`
retains the newest 30 **local** dumps by default. Configure an S3 lifecycle rule
(including noncurrent-version expiry if bucket versioning is enabled) for the same
approved retention period; the backup job's retention pruning never deletes remote
copies because a writer cannot safely infer ownership of objects from another host (a
failed run does remove its *own* partially published objects — see the credential
scoping below).

A database dump is not an application recovery point by itself. Production backup
refuses to run without `GOATFARM_RECOVERY_INVENTORY_FILE` naming a fresh, unbound
inventory created by `backend/scripts/recovery_inventory.py capture`. That inventory
records an independently recoverable, versioned screening-object manifest/recovery-point
receipt and escrow receipts plus non-secret SHA-256 identities for JWT, TOTP encryption,
idempotency HMAC, database CA, and backup GPG material. `backup.sh` binds those facts to
the exact encrypted archive name/digest before local or off-site publication. The
screening bucket must keep version history/replication at least as long as a database
restore point can reference its objects. Privacy-retention deletion is intentional and
is not a promise that an already-expired image will be recoverable.

Each identity covers the complete material needed at that recovery point, not only the
currently active scalar: `jwt_public` includes every retained previous verification key,
`totp_encryption` and `idempotency_hmac` include their active and previous-key rings,
and `backup_gpg` covers the decrypting private key plus the trusted signing identity.
Prefer a versioned secret-manager bundle identity and escrow receipt; if `--key-file` is
used, point it at a canonical recovery-bundle manifest containing the whole ring.

Production and S3 backups must be both signed and encrypted with GPG. Pin the signing
key by its complete 40- or 64-hex fingerprint; do not use a mutable email/key-name
selector. S3 server-side encryption remains a second layer. Keep the encryption private
key and signing public key available to the restore operators through a separately
tested recovery path, and use least-privilege database and object-storage credentials.

## Off-site credentials

The nightly job needs exactly three verbs, and only on the one backup prefix:
`s3:PutObject` (archive + checksum + recovery-inventory upload), `s3:ListBucket` on the
bucket — constrain it with an `s3:prefix` condition to the backup prefix (the
never-overwrite collision check runs *before any upload*, so a PutObject-only credential
fails every run closed), and `s3:DeleteObject` on the same prefix (best-effort removal
of the run's *own* partially published objects when publication fails midway; without it
a failed run only logs the orphaned keys). The job never touches objects it did not
itself just write. Enable bucket versioning (or S3 Object Lock) so a compromised backup
host cannot destroy the off-site tier, and give restore-side operators a separate,
broader-read credential — never the backup host's.

## Supported restore revisions

`restore.sh` refuses — before anything touches the target database — backups whose
schema predates Alembic revision `f4e5f6a7b8c9`: older dumps still contain unkeyed
idempotency fingerprints of password-bearing worker-create bodies. The allowed set is
the migration chain from that revision onward, decided by chain membership (Alembic ids
are random hex — they must never be compared as strings), maintained in
`backend/scripts/restore_floor.sh` and kept in sync with the migration chain by test. To
migrate such an archive, restore it into a scratch database, set
`GOATFARM_MIGRATION_DATABASE_URL` to that exact scratch target, run `alembic upgrade
head` (which re-purges), and dump/restore that database.

## Recovery drills

Rehearse the whole path (backup → tamper → checksum refusal → clean restore → exact
object-version recovery → escrowed key identity verification → non-empty refusal)
against production-shaped infrastructure. Open representative raw/normalized/crop
objects referenced by the restored database, exercise a deliberately retention-expired
pointer, and record the wall-clock time — it is your real whole-system RTO.

```bash
# Omit GOATFARM_DB_SSLROOTCERT_PATH only when the database certificate chains
# to the host's normal public trust store.
GOATFARM_DATABASE_URL='postgresql+asyncpg://user:pass@host/goatfarm' \
GOATFARM_DB_SSLMODE=verify-full \
GOATFARM_DB_SSLROOTCERT_PATH=/run/secrets/goatfarm-postgres-ca.pem \
GOATFARM_ENVIRONMENT=production \
GOATFARM_BACKUP_GPG_RECIPIENT='RECIPIENT_KEY_FINGERPRINT' \
GOATFARM_BACKUP_GPG_SIGNER_FINGERPRINT='SIGNING_KEY_FINGERPRINT' \
GOATFARM_BACKUP_S3_URI='s3://company-backups/goatfarm' \
GOATFARM_RECOVERY_INVENTORY_FILE='/secure/goatfarm-recovery-unbound.json' \
./backend/scripts/backup.sh /var/backups/goatfarm
```

Generate that unbound file immediately before the job from the object replica/inventory
receipt and secret-manager escrow receipts. Use `--check-s3-versioning` in production;
`capture --help` lists the six required `--key-file`/`--identity` and matching
`--escrow-receipt` names. The executable systemd timers, freshness check, Prometheus
scrape/rules, alert acceptance exercise, and off-host log-sink boundary are in the
[Operations baseline](../ops/README.md).

## Environment and credential handling

Both scripts read `GOATFARM_ENVIRONMENT`, `GOATFARM_DB_SSLMODE`, and an optional
`GOATFARM_DB_SSLROOTCERT_PATH` — the application settings that gate TLS and the
signed/encrypted-artifact requirement — from `backend/.env` when the job did not export
them, so a host whose `.env` says `production` cannot be degraded to an unsigned
plaintext dump by an incomplete cron environment. An explicit export still wins, which
is why the invocations in this section pass both explicitly. They use the same pinned
`python-dotenv` grammar as the application, including `export` declarations, quotes,
inline comments, case-insensitive keys, and interpolation; install the backend
environment before running either script. The helper parses one pinned regular-file
snapshot (capped at 1 MiB); an in-place change, non-regular file, or oversized file
fails the job instead of silently falling back to development defaults.

The database password is supplied to libpq through a mode-`0600` temporary passfile;
`pg_dump`, `psql`, and `pg_restore` receive only a password-free URL built by
`backend/scripts/libpq_url.py`, which reads the URL from stdin so it never appears in
any process's arguments.

## Backup concurrency and freshness

The destination keeps one mode-`0600` `.goatfarm-backup.flock` regular file. Never
delete, rename, or rotate that inode: the script opens it on inherited FD 9 and takes a
non-blocking kernel `flock` for the producer's complete process tree. Concurrent
producers fail closed, while process/host crashes release the lock in the kernel without
stale-lock takeover. During transition, a live legacy `.goatfarm-backup.lock/pid` still
blocks a run. After taking the kernel lock, each new producer also atomically claims and
holds that legacy directory for its complete run, so a still-deployed old script cannot
run beside it. The claim is prepared completely under a private name, then published
with an exclusive atomic rename; the canonical name is therefore either absent or a
complete claim even if initialization is killed or encounters an I/O error. The
destination filesystem must implement the platform's atomic no-replace directory rename
(`RENAME_NOREPLACE` on Linux or `RENAME_EXCL` on macOS). The script fails closed when
that primitive is unavailable; it never degrades the cross-version lock to a raceable
check-then-rename on NFS/CIFS or another unsupported mount. Validate this capability in
the backup-host runbook.

During this transition, a dead old-format PID is deliberately not reclaimed: the old
shell may have died while its `pg_dump` child remains active and holds no kernel lock.
Verify the PID and every child are stopped, then remove that old directory manually. A
stale new-format ownership-marked claim can be reclaimed after acquiring the flock
because its descendants inherited FD 9. Directory, PID-file, ownership-file, and
exact-content snapshots are checked across every quarantine move; a raced replacement is
restored only with an exclusive no-overwrite rename, and the new run fails closed.
Cleanup similarly removes only the exact directory identity it published. Alert on any
non-zero backup exit and on a missing complete archive/checksum/inventory set; an upload
failure keeps the complete local backup and makes a best-effort removal of any remote
partial. The supplied freshness checker does not trust filename presence: it hashes the
newest candidate, validates the exact checksum record and bound recovery inventory, and
reports any newer rejected sets through the node-exporter textfile metric.

## Recovery objectives

The baseline database and required screening-object target is a 24-hour recovery-point
objective and whole-system recovery within four hours. The object-store receipt may
assert a tighter target, never a looser one. Enable PostgreSQL WAL archiving and
point-in-time recovery when a tighter database recovery point is required.

## Restore an archive

Restore only into a newly created, empty database. The helper accepts exactly one
checksum record bound to the archive basename, authenticates an encrypted backup against
the configured signer fingerprint, and validates the decrypted archive before
connecting. The archive and sidecar are first copied into the private restore directory,
so replacement of removable/shared source media cannot swap bytes between verification
and use. It refuses relations, routines, types, extensions, extra schemas, or other
namespaced user objects anywhere in the target—not only public tables. `pg_restore` runs
in one transaction, and a separate post-restore query requires exactly one well-formed
Alembic revision marker. Production restores reject plaintext archives and archives
without a valid bound `.recovery.json` sidecar. The sidecar archive digest is rechecked
before any target database connection.

Inject `GOATFARM_RESTORE_DATABASE_URL` through the process supervisor or secret manager.
Do not put the credential-bearing URL on the restore command line (or type its value
directly into shell history); the script accepts only the archive path as a positional
argument.

```bash
# Omit GOATFARM_DB_SSLROOTCERT_PATH only when the database certificate chains
# to the host's normal public trust store.
GOATFARM_RESTORE_CONFIRM=goatfarm_restore_test \
GOATFARM_DB_SSLMODE=verify-full \
GOATFARM_DB_SSLROOTCERT_PATH=/run/secrets/goatfarm-postgres-ca.pem \
GOATFARM_ENVIRONMENT=production \
GOATFARM_RESTORE_GPG_SIGNER_FINGERPRINT='SIGNING_KEY_FINGERPRINT' \
./backend/scripts/restore.sh /secure/goatfarm-ARCHIVE.dump.gpg
```

## Migrate and verify the restored database

The revision stored in an authentic backup may legitimately be older than the currently
deployed code. After the restore succeeds, point the migration URL at the restored
database and bring it to the current application head before starting the API or
performing smoke tests. Alembic builds an **async** engine, so the migration URL must
carry the `postgresql+asyncpg://` driver scheme; a plain libpq URL resolves to psycopg2
and aborts with a bare `ModuleNotFoundError: No module named 'psycopg2'` that names
neither the URL nor the cause. `restore.sh` itself accepts `postgres://`,
`postgresql://` or `postgresql+asyncpg://` and always hands `psql`/`pg_restore` a
normalized password-free `postgresql://` URL, so `backend/.env.example` sets
`GOATFARM_RESTORE_DATABASE_URL` with the async scheme and the anchored substitution
below is a no-op for it — while still upgrading a libpq URL if the operator supplied
one:

```bash
cd backend
GOATFARM_MIGRATION_DATABASE_URL="${GOATFARM_RESTORE_DATABASE_URL/#postgresql:/postgresql+asyncpg:}" \
GOATFARM_DB_SSLMODE=verify-full \
.venv/bin/alembic upgrade head
```

Run and document a restore drill at least quarterly. After that migration, verify
`alembic current` with the same explicit `GOATFARM_MIGRATION_DATABASE_URL`,
representative row counts, `/readyz`, authentication, and the core
animal/health/feeding/finance screens. Authenticate an account whose restored TOTP
ciphertext predates the incident, verify a token with the restored JWT identity, replay
one retained idempotency record using the restored HMAC ring, and fetch representative
exact object versions from the inventory before recording the elapsed time against the
four-hour recovery target. Treat a failed Alembic sanity check as an unusable restore
requiring operator investigation. The helper removes its passfile, source snapshot, GPG
status, and private decrypted archive on every exit path.

## Deletion reconciliation

Encrypted backups can contain identity data from before a later account deletion until
those backups expire. Set and enforce a backup-retention period that matches the privacy
policy. A restore of an older recovery point can also resurrect pre-deletion
login/profile data, so production recovery requires an external, access-controlled
deletion-reconciliation ledger or equivalent operator record that is replayed before the
restored service accepts traffic. Test that reconciliation in the quarterly drill; the
database backup cannot safely be its only source.
