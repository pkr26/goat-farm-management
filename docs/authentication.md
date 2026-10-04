# Authentication and tenancy

[Documentation index](README.md) · [Project overview](../README.md)

## Access and refresh sessions

Login/register issue an RS256 **access JWT** (30 min, `Authorization: Bearer`, held in
memory only by the SPA) plus a rotating **refresh JWT** (14 d, httpOnly `SameSite=Lax`
cookie scoped to `/`; production uses the host-bound `__Host-goatfarm_refresh` name).
Every refresh token is backed by a server-side **session row**: refresh consumes the
presented token and rotates it, presenting an already-consumed or revoked token is
treated as theft and revokes the whole token family (with a three-second idempotent
grace for simultaneous tabs), and logout / password change / owner-initiated worker
password reset revoke the user's sessions server-side. Farm-membership deactivation is
tenant-local: it immediately removes that farm's access without logging the same account
out of unrelated farms. Access JWTs carry a server-checked revocation version, so those
security events invalidate already-issued bearer tokens as well as refresh sessions. API
responses are marked `Cache-Control: no-store`. Refresh families and retained rotations
are hard-capped per user/family; old consumed rows can be compacted without weakening
replay detection because every newly issued token carries its signed family identifier.
Expired server-side rows are purged only in ordered, lock-skipping finite batches at
startup and periodically (`GOATFARM_REFRESH_SESSION_CLEANUP_*`). Legacy tenant repairs,
inactive-animal duty retirement, and tombstoned-user membership deactivation likewise
run only in finite post-readiness batches (`GOATFARM_LEGACY_REPAIR_*`,
`GOATFARM_INACTIVE_ANIMAL_TASK_CLEANUP_*`, and `GOATFARM_DELETED_MEMBERSHIP_CLEANUP_*`).
Inactive-animal duties and deleted users are excluded from actionable/authorized views
immediately, before their retained rows converge in the background. Tokens require
issuer, audience, subject, kind, id, issued-at and expiry claims; configure stable
`GOATFARM_JWT_ISSUER` and `GOATFARM_JWT_AUDIENCE` values for every environment. `POST
/api/auth/change-password` gives users self-service password change (requires the
current password; revokes all other sessions).

## Farm context

Farm context travels in the **`X-Farm-Id` header**, validated per request (owner or
active membership). Every farm-scoped domain endpoint requires it; the only exceptions
are `GET /api/simulation/defaults` and `GET /api/simulation/defaults/breeds`, which
serve global breed/production assumptions and need authentication alone (no farm
context, no permission), and the owner console (`GET /api/owner/overview`, `GET
/api/owner/benchmarks`), which spans every farm the CALLER owns — ownership is its
permission and its tenant scoping (a worker with full grants gets 403, and one owner's
response never includes another owner's farm).

## Permissions

owners hold every permission; workers get a role's permission bundle (presets: Farm
Manager, Animal Mover, Veterinarian, Procurement Officer, Feeder, Cleaner, Cleaner
Manager, Accountant, Auditor — all editable, plus custom roles). `GET
/api/auth/permissions` returns the caller's effective set for the active farm; the nav
and buttons mirror it.

## Legacy password imports

Legacy `pbkdf2_sha256$iterations$salt_hex$digest_hex` password hashes (pre-migration
users) verify transparently and are upgraded to Argon2id on first login. The supported
import range is an iteration count from 1 through 1,000,000; spellings Python's `int()`
accepts (`+50000`, `50_000`, surrounding whitespace) verify for compatibility with
historical imports, and a hash outside the ceiling now logs a server-side warning so the
lockout is diagnosable instead of reading as a wrong password. Every rejected login
additionally pays `GOATFARM_REJECTED_LOGIN_PBKDF2_WORK_BUDGET` PBKDF2 iterations of
timing padding (default 50,000); a deployment importing costlier legacy hashes must
raise it to at least its maximum imported iteration count. Before importing an external
user table or deploying this boundary over existing rows, audit accounts that need an
offline rehash/password reset:

```sql
WITH legacy AS (
    SELECT id, email, split_part(password_hash, '$', 2) AS iteration_text
    FROM users
    WHERE password_hash LIKE 'pbkdf2_sha256$%'
)
SELECT id, email, iteration_text
FROM legacy
WHERE CASE
    WHEN iteration_text ~ '^[0-9]+$'
    THEN iteration_text::numeric NOT BETWEEN 1 AND 1000000
    ELSE TRUE
END;
```

Any returned row cannot log in through the legacy verifier; review it before rollout
rather than raising the per-request work ceiling ad hoc.

## Auth throttling

Login and register are rate-limited (failed attempts for login, keyed per client IP and
email; all register attempts per client IP) and farm ownership is capped per user.
Behind a reverse proxy, set `GOATFARM_TRUSTED_PROXY_HOSTS` so real client IPs key the
limiter, and enforce a shared edge/WAF ceiling on every auth path—including successful
login and refresh traffic—and every bearer-protected endpoint before CPU-hard password
work, JWT verification, or session-row mutation. Rejected refresh attempts are
IP-throttled; successful page-load refreshes do not consume the abuse budget. The SPA
coordinates refresh across tabs with the browser Web Locks API when available.

## Worker provisioning

until verified invitations are implemented, **every pre-existing account is refused** by
worker provisioning. Password resets are allowed only for new accounts explicitly
provisioned by that farm; the provenance is stored on the membership and legacy rows
fail closed.

## Account deletion

Non-owner account deletion atomically tombstones the login identity, scrubs its
profile/password, and revokes sessions. That User tombstone is the immediate
authorization barrier. Memberships and task assignments remain as non-authorizing
audit/FK anchors; active memberships are deactivated later in finite `FOR UPDATE SKIP
LOCKED` batches, never enumerated by the deletion request. Attributed farm, health,
task, and finance history is therefore not rewritten. Tombstones cannot authenticate and
display as `Deleted account`; the old email can register as a new user ID. This is
de-identification/pseudonymization, not a promise that every operational record is
anonymous. Set documented legal-purpose and retention rules before production. Farm
owners must currently transfer/dispose of ownership through an operator process before
their sign-in account can be deleted. Farm-selector and account-export hydration also
fail explicitly above the configurable `GOATFARM_MAX_ACCOUNT_AFFILIATIONS_PER_RESPONSE`
ceiling, so a pathological legacy/imported identity cannot force an unbounded response.

## Two-factor authentication

Any account can enroll from Account → Two-factor authentication (RFC 6238, any
authenticator app; on phones the otpauth:// link opens the app directly). Enrollment is
opt-in but **strongly recommended for farm owners** — owners have all farm permissions.
Design notes: the shared secret is AES-GCM encrypted at rest under an independent stable
TOTP key (a DB dump alone recovers nothing; follow the ordered stable-key → rekey →
JWT-cutover [security runbook](security-operations.md) rather than re-enrolling active
users); codes are single-use per time step; the login challenge token is single-use,
5-minute, version-bound and throttled to 5 attempts/5 minutes per account. Because there
is no external recovery channel (see [Account recovery](#account-recovery)), losing the
authenticator means an owner-provisioned reset is impossible for the owner account
itself — keep the one-use recovery codes generated during enrollment securely; an unused
code can complete the login challenge.

## Account recovery

There is no self-service email/SMS or support recovery channel. Farm owners can reset
only accounts provisioned by that farm. An operator can recover a lost second factor
through the [TOTP reset
runbook](security-operations.md#totp-recovery-and-operator-reset). Any future recovery
channel must preserve the account identity and second-factor guarantees.

## Retrying side-effecting requests

Farm creation, finance transaction creation/correction, purchase-batch,
individual-animal and animal-weight creation, manual-duty and worker-account creation,
health-event creation, saved-simulation-scenario creation, and feed
dispense/mix/stock-add accept an `Idempotency-Key` header (1–128 printable,
non-whitespace ASCII characters). Farm creation, finance transaction creation, purchase
batches, and feed dispense/mix/stock-add require this header. Purchased-animal creation
requires it when the request books money; other supported operations accept it
optionally. Missing required keys return 422. Farm creation has no tenant yet, so those
keys are scoped by authenticated actor and operation. Every other key remains scoped by
authenticated actor, farm, route and concrete path. Reusing a key with the same
validated request replays the original successful JSON/status without repeating farm
seeding, ledger, animal, task, scenario or inventory effects; changing the body or
concrete path identity returns HTTP 409. Failed 4xx/5xx attempts are not cached.

Successful records are retained for seven days by default, configurable with
`GOATFARM_IDEMPOTENCY_RETENTION_HOURS` (1 hour–90 days). The raw key is never stored,
only its SHA-256 digest. A background job uses the expiry index plus `FOR UPDATE SKIP
LOCKED` to delete small fixed batches (interval, batch size and maximum batches are
configurable). User requests never scan or delete a global expiry cohort; reusing one
expired key removes only that exact scoped row.

See [Security operations](security-operations.md) for recovery and rotation, [Field
workflows](field-workflows.md) for shared-tablet sessions, and
[Configuration](configuration.md) for production safeguards.
