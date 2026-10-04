# Security operations

[Documentation index](README.md) · [Project overview](../README.md)

Commands in this runbook start at the repository root unless an explicit instruction
changes directory. Use production access through the existing secret-management and
deployment process. Follow the key transition order: TOTP legacy rekey first, JWT
signing-key cutover second.

## TOTP recovery and operator reset

Activating two-factor mints ten single-use recovery codes (`XXXXX-XXXXX`), revealed
exactly once at activation and re-revealable only through *Regenerate recovery codes*
(password + a live authenticator code; the whole prior set is revoked). A recovery code
redeems the login TOTP challenge and emits the `auth.totp.recovery_code_used` security
event — alert on it: that login did not prove possession of the authenticator device.

If both the authenticator device and every unused recovery code are lost, run the
shipped break-glass script against the external database through the production stack's
one-shot `migrate` service — the only container carrying the DDL-role
`GOATFARM_MIGRATION_DATABASE_URL` and the database CA. (The production Compose file has
no `db` service by design and the backend image ships no `psql`, so hand-typed `psql`
against "the compose database" fails exactly when it is needed.)

```sh
# Inspect first (dry run — exits 2 while there is work left on purpose):
docker compose --env-file /secure/goatfarm.production.env \
  -f docker-compose.production.yml run --rm --no-deps migrate \
  python scripts/totp_breakglass.py --email owner@example.in
# Reset:
docker compose --env-file /secure/goatfarm.production.env \
  -f docker-compose.production.yml run --rm --no-deps migrate \
  python scripts/totp_breakglass.py --email owner@example.in --apply
```

From the backend directory in an installed checkout, run `.venv/bin/python
scripts/totp_breakglass.py --email … [--apply]`. It matches the one live account for the
lowercase email under a row lock (refusing ambiguous or tombstoned matches) and performs
exactly this reset in one transaction:

```sql
-- Break-glass: drop the second factor for one account.
UPDATE users SET totp_secret_enc = NULL, totp_state = NULL, totp_last_step = NULL,
                 token_version = token_version + 1
 WHERE id = <the one live account matching the email>;
DELETE FROM totp_recovery_codes WHERE user_id = <the same account>;
```

A dry run that finds a second factor deliberately exits nonzero — a counted reset must
never look like a completed recovery (the same convention as `rekey_totp_secrets.py`).

The `token_version` bump signs out every existing session for that account, so a
previously stolen bearer token loses access. Re-enroll immediately after signing back in
— a factor-less account has no second factor until then.

## TOTP encryption migration

TOTP secrets are encrypted with a separate, stable AES-256 key named
`GOATFARM_TOTP_ENCRYPTION_KEY`, encoded as an unpadded 32-byte base64url value. It is
required in production and must remain stable across API restarts. Generate a new value
with:

```sh
python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Deploy that value first while the old JWT signer is still active, then inventory and
rewrap legacy ciphertext from the backend directory:

```sh
.venv/bin/python scripts/rekey_totp_secrets.py
.venv/bin/python scripts/rekey_totp_secrets.py --apply
.venv/bin/python scripts/rekey_totp_secrets.py  # verification after --apply
```

For the production Compose image, the script is included at `/app/scripts`; after
running the normal `config-guard` preflight, use the same mounted configuration:

```sh
docker compose --env-file /secure/goatfarm.production.env \
  -f docker-compose.production.yml run --rm --no-deps backend \
  python scripts/rekey_totp_secrets.py --apply
```

Proceed with the JWT cutover only after a successful `--apply` and a fresh verification
run reports `rekeyed=0` **and** `unavailable=0`. A dry run that finds rows requiring
rekeying exits nonzero, so it cannot be mistaken for that cutover gate. The job locks
finite batches, is safe to rerun, and rewraps legacy JWT-derived ciphertext (plus a
previous stable TOTP key, if configured) into the current v2 envelope. Do not retain an
old JWT private key in the long-running API merely for MFA recovery: the rekey job is
the bounded migration window. If it reports unavailable rows, stop the JWT rotation,
repair the ciphertext/key configuration, and rerun it. If an accidental JWT cutover has
already stranded raw legacy rows, run an isolated one-off rekey job with a temporary
copy of the production environment: mount the matching **pre-cutover** private *and*
public PEM as that job's active `GOATFARM_JWT_PRIVATE_KEY_PATH` /
`GOATFARM_JWT_PUBLIC_KEY_PATH`, and set `GOATFARM_JWT_PREVIOUS_PUBLIC_KEY_PATHS=[]`. Do
not carry the normal post-cutover verification ring into that job: the old active public
key would otherwise be listed twice, and the deliberately duplicate-key-safe validator
will refuse it. Keep the same database URL and stable TOTP key, run `--apply`, then the
clean verification check, and remove the old private key again. Never add it to the
API's JWT keyring or keep it in API memory.

Changing the TOTP key itself uses a two-phase key transition. First deploy the existing
current key `K1` with the new key `K2` listed in
`GOATFARM_TOTP_ENCRYPTION_PREVIOUS_KEYS` in the API configuration; that prepares
verification of `K2` rows before the key becomes active. Then switch the API to current
`K2` with `K1` in the previous-key array, apply the rekey, then verify `rekeyed=0` and
`unavailable=0` before removing `K1`. Never switch the current key without that overlap.

## JWT key rotation

Every newly issued token carries a deterministic `kid` (the base64url SHA-256
fingerprint of its RSA public key). The API signs only with the active private key and
verifies by `kid` against the active public key plus at most three verification-only
public keys from `GOATFARM_JWT_PREVIOUS_PUBLIC_KEY_PATHS` (a JSON path array). Tokens
issued before `kid` was introduced are tried against that same bounded keyring during
migration; a token that supplies an unknown `kid` is rejected without fallback. Startup
validates every key as RSA >=2048 bits, rejects duplicate key IDs, and checks that the
active pair matches.

Use a two-phase key transition so tokens issued before and after a restart remain valid.
Restart the single API process at each stage; its advisory lease requires the previous
serving process to stop before a replacement can serve:

1. Complete the TOTP encryption migration above, then generate a new RSA pair at new
   secret paths; do not overwrite the running pair in place. Keep an old private key, if
   needed for deployment rollback, outside the running API and never put a private-key
   path in the previous-key list.
2. Pre-stage the new **public** key: keep the old pair active, add the new public path
   to `GOATFARM_JWT_PREVIOUS_PUBLIC_KEY_PATHS`, and restart the API. The API still signs
   with the old key and can verify the staged key.
3. Roll the signing cutover: point `GOATFARM_JWT_PRIVATE_KEY_PATH` /
   `GOATFARM_JWT_PUBLIC_KEY_PATH` at the new pair and replace the staged entry with the
   old **public** path, for example `["/run/secrets/goatfarm_jwt_previous_public.pem"]`.
   Restart the API with new-active/old-verification configuration; both token
   generations remain verifiable.
4. Keep the old public key configured for at least the refresh-token lifetime after the
   final old-key signer stopped (currently 14 days), plus the 60-second clock-skew
   allowance. Monitor authentication failures throughout the overlap.
5. After that window, remove the old public path and restart the API. Tokens carrying
   its `kid`—and legacy no-`kid` tokens signed by it—are then rejected. Securely retire
   the old private key under the deployment's key retention policy.

## Idempotency HMAC rotation

Generate a new independent secret; never derive it from or reuse a JWT key. First deploy
with the old secret still current and the new secret included in
`GOATFARM_IDEMPOTENCY_REQUEST_HMAC_PREVIOUS_SECRETS`. Then deploy the new secret as
current and retain the old secret in that verification-only JSON array. This transition
preserves replay of records made by either secret generation. At most three previous
secrets are accepted, all comparisons are constant-time, and new records always use the
current secret. Keep the old secret for at least `GOATFARM_IDEMPOTENCY_RETENTION_HOURS`
after the last old signer stops, then remove it from the API configuration. If a key is
compromised, do not keep it for overlap: remove it and delete affected
`team.workers.create` idempotency rows, accepting that clients must retry with a new
key. Migration `f4e5f6a7b8c9` performs this purge once for every pre-HMAC worker-create
row; its deletion is intentionally not reversed by downgrade.

See [Authentication](authentication.md) for the session model and [Backup and
recovery](backup-recovery.md) for key escrow requirements.
