# Data retention

[Documentation index](README.md) · [Project overview](../README.md)

Production requires `GOATFARM_RETENTION_SWEEP_ENABLED=true`. Each pass is
farm-keyset-paged and transaction-bounded. Defaults retain screening image chains for
180 days, empty screening-batch anchors for 365 days, daily provider budgets/call
receipts for 90 days, completed notification outbox/log evidence for 400 days, and
terminal tasks for 365 days. Adjust these only against the deployment's retry, audit,
clinical, and privacy obligations. The append-only security-event ledger and long-term
weight/feeding facts are deliberately outside this purge policy.

## Screening deletion protocol

Screening removal is a durable three-phase saga. First, PostgreSQL commits a per-image
tombstone containing the exact raw, normalized, and crop key manifest and updates the
image's same-row `retention_tombstoned_at` fence in the same transaction. Worker claims,
reviews, exports, owner totals, and presigning all exclude that fence, including a
request that began waiting on the image lock before the tombstone committed. Second,
retention idempotently removes every version/delete marker for each unshared key and
verifies both the current key and version listing are absent. Legacy derivative keys
still referenced anywhere in the bucket (including another farm's legacy row) are
recorded as preserved. New normalized and crop derivatives use
image/crop-identity-scoped `v2` keys, so a new chain cannot start referencing a legacy
key while it is being purged. Only after that outcome is durably acknowledged does a
separate transaction remove the relational chain and its tombstone. An object error
leaves a `PENDING` intent with attempt metadata; a crash after object success retries
the safe idempotent purge; a SQL/commit failure during finalization leaves an
`OBJECTS_DELETED` intent that finalizes later without calling S3 again. Do not manually
delete rows from `screening_retention_deletions`: its restrictive FK is the recovery
boundary. Operators should alert on repeated `OBJECT_STORE_DELETE_FAILED`,
`CONFIGURED_BUCKET_MISMATCH`, or `INVALID_MANIFEST` error codes and fix the cause; the
next enabled retention pass resumes them automatically. Failed intents use a durable
five-minute base, capped 24-hour exponential `next_attempt_at` backoff, never pin newer
ready intents solely by id, and per-farm unfinished manifests are capped at
`retention_delete_batch_size * retention_max_batches_per_farm`. Each dispatch purges at
most one object key and one 1,000-entry version page, commits that per-key cursor, and
keeps successful partial-page work immediately eligible inside the finite sweep budget
rather than misreporting it as a provider failure or delaying it until the next daily
pass.

## Object-store permissions

The API process uses the verified permanent-delete primitive for retention and
post-presign-expiry raw cleanup; the screening worker uses it for immediate raw cleanup
after a derivative is durable. If those processes have separate roles, both roles need
these permissions:

- On the bucket ARN: `s3:GetBucketVersioning`; plus `s3:ListBucketVersions` and
  `s3:ListBucket` constrained with `s3:prefix` to both the configured raw namespace
  (`${GOATFARM_SCREENING_S3_PREFIX}/*`, default `raw/*`) and the fixed derivative
  namespace (`screening/*`). Include every historical raw prefix still referenced by
  database rows after a prefix change. `ListBucket` is required so HEAD of an absent key
  returns a verifiable not-found response instead of 403.
- On object ARNs for both namespaces (and any retained historical raw namespace), for
  example `arn:aws:s3:::BUCKET/raw/*` and `arn:aws:s3:::BUCKET/screening/*`:
  `s3:DeleteObject`, `s3:DeleteObjectVersion`, and `s3:GetObject` (the authorization
  used by HEAD).

Do not grant only current-object delete: that leaves recoverable historical versions and
prevents the database saga from truthfully acknowledging erasure.

## Monitoring and recovery

The retry queue is observable without exposing object keys:

```sql
SELECT status, COALESCE(last_error, 'NONE') AS last_error,
     count(*) AS intents, min(next_attempt_at) AS next_due,
     max(failure_count) AS max_consecutive_failures,
     min(updated_at) AS oldest_update
FROM screening_retention_deletions
GROUP BY status, COALESCE(last_error, 'NONE')
ORDER BY status, last_error;
```

Investigate any `PENDING` row whose `updated_at` remains older than two sweep intervals.
Restore bucket access/configuration or repair a demonstrably invalid manifest under an
audited maintenance procedure, then let the normal sweep retry it. Never manually
promote a row to `OBJECTS_DELETED` or remove it: that would bypass verified absence and
destroy the recovery boundary.

## Account identity and retained attribution

A departing worker's account identity is scrubbed (tombstone), but their *contributions*
to farm records (task attribution, transaction authorship, free-text they wrote) are
retained for farm integrity by design. `account/export` deliberately covers only account
identity + memberships, not farm-domain data. Document the legal purpose and retention
period for the deployment before operating with production identity data. Older backups
can retain identity data that has since been deleted. Follow the [Recovery
reconciliation requirements](backup-recovery.md#deletion-reconciliation) before
reopening a restored service.
