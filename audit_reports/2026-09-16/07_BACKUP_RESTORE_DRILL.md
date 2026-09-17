# 07 — Backup / Restore Live Drill (catalog 10.4) + migration-window review

First execution test of the DR path against a live schema. Throwaway Postgres
16.2, dev mode (plaintext backup permitted; production GPG path verified
statically in report 08 §3).

## Drill log (all against a **live** backend serving traffic on the source DB)

| Step | Command (essence) | Result |
|---|---|---|
| 1. Backup | `backup.sh /tmp/gfaudit/backups` | exit 0 — dump (226 KB) + `.sha256` sidecar published, 0600 perms, advisory-flock serialized, pg_restore pre-parse validation passed |
| 2. Tamper A (missing sidecar) | restore of copy without `.sha256` | **Refused** (exit 2): "checksum is missing or not a regular file" |
| 3. Tamper B (renamed dump + sidecar) | sidecar names a different file | **Refused** (exit 2): "checksum record does not name <file>" — checksums are **name-bound** |
| 4. Tamper C (byte flipped at offset 100000, name matches) | real hash mismatch | **Refused** (exit 2): "backup checksum mismatch" |
| 5. Clean restore | into empty `goatfarm_rt_restore` | exit 0 — single-transaction restore, Alembic revision `e0f4a8b2c6d5` stamped, data verified (5 animals / 13 users / 52 tasks) |
| 6. Non-empty target | restore again onto restored DB | **Refused** (exit 2): "target database contains user schema objects" |
| 7. Live-source interplay | restore ran while uvicorn served the source DB | no interference; same-DB restore would be refused by the empty-check + advisory lock 718204614 (shared with Alembic) |

## Verdict

**DEFENDED end-to-end in development mode.** The restore path enforces:
checksum-of-ciphertext before decrypt, name-bound sidecars, empty-target
re-assertion inside the transaction under the migration advisory lock,
single-transaction `--exit-on-error`, URL scrubbing from child argv, 077 umask,
pinned (O_NOFOLLOW) copies. Production adds mandatory GPG sign-then-encrypt with
full-fingerprint pinning and `verify-full` TLS (static, report 08 §3 — the
40+-test suite `test_deployment_artifacts.py:301-1590` covers the GPG failure
modes that cannot be drilled without a keyring).

## Migration window (catalog 6.6)

Compose wiring gates `backend` on `migrate: service_completed_successfully` —
a failed migration keeps the old container serving; no new-code/old-schema
exposure. `lock_timeout=10s` makes DDL queuing behind traffic a loud deploy
abort (availability event, not corruption). Residuals are in report 08
(INFRA-3 revision floor outside compose; INFRA-5 prose-only DB least privilege).

## Recommendation

Repeat this drill quarterly against production-shaped infrastructure
(GPG keyring, verify-full TLS, S3 tier) and time it — this dev drill took
<2 min wall-clock for 226 KB; production data size will change the RTO math.
