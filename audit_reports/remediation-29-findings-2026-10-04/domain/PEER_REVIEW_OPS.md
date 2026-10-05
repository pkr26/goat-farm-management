# Independent peer review: O26-03 and O26-04 remediation

Reviewed `.github/workflows/release.yml`, `.github/scripts/cleanup_release_versions.py`, `backend/scripts/{backup.sh,restore.sh,recovery_inventory.py,check_backup_freshness.py}`, new ownership/manifest tests, and the retained actual-GPG control output. No additional concrete defect was confirmed in these fixes.

## Release cleanup ownership (O26-03)

The registry publisher now uses a run/attempt-specific temporary tag and successful action digest outputs. Final multi-architecture release receipts are recorded after assembly and stored under a run/attempt-specific path, so an earlier attempt's receipt is not a cleanup grant. Cleanup matches the exact package-version digest, then requires every current alias to belong to the recorded owned tag set. This matters because GHCR deletes package versions, not individual aliases. Missing successful receipts result in no registry API calls.

I independently executed six adversarial controls against the real cleanup script and a local fake `gh` executable; no real registry operations occurred. [peer-release-cleanup-result.json](peer-release-cleanup-result.json) records:

1. Failed preflight with a pre-existing old release/publish tag: no API call.
2. Matching tag but different digest over paginated output: retained.
3. Owned digest with an additional old stable alias: retained.
4. Untagged version with matching digest: retained.
5. Matching publish and final aliases, with both exact receipts: only that version deleted.
6. Matching publish digest with an unowned final alias: retained.

The initial peer harness was adjusted to the agent's newly introduced run/attempt receipt filename before obtaining the final six-pass result. A publication interrupted before a verifiable receipt, or a registry version with other aliases, can require manual cleanup; retaining it does not recreate the original preflight deletion defect. This local verification does not execute a hosted release or establish behavior under a concurrent independent registry writer.

## Authenticated recovery inventory (O26-04)

The detached signature covers canonical manifest content including generated time, exact archive name/hash, database recovery time, object recovery time/digest/receipt and key escrow identities. Trust comes from an externally configured signer fingerprint; the manifest does not choose its own trust anchor. Verification checks GPG's valid-signature status, return code, disallowed failure statuses, signer/primary-key fingerprints and timestamp consistency. Binding signs and verifies before the existing atomic publication protocol. Restore verifies the manifest before database restoration; freshness authenticates before ranking and verifies the archive binding again before reporting.

Freshness uses the minimum of the signed database dump-start timestamp and actual object recovery timestamp. Re-signing a newly captured inventory therefore cannot make an unchanged old object recovery point current. Archive filesystem timestamps do not drive RPO.

I reviewed the ops agent's [five genuine GPG controls](../ops/signed-recovery-controls.json): valid fresh manifest accepted; timestamp edit, escrow substitution and unexpected trust anchor rejected; fresh signature with a 72-hour-old object point reported stale. I did not count these as separately re-executed independent tests. Existing unit controls additionally cover database-binding substitutions and removed signatures. The reviewed source supports those outcomes.

Cryptographic authenticity assumes the configured private signing key and executable/runtime are trustworthy. The manifest's cloud recovery receipt remains an operator-supplied statement about externally recovered objects; these local controls do not independently restore a live bucket or escrow store.
