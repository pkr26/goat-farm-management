# Configuration

[Documentation index](README.md) · [Project overview](../README.md)

Settings use the `GOATFARM_` prefix. The backend environment file is read relative to
`backend/`; the root environment file configures Docker Compose. Keep credentials
outside version control. Use the environment templates and settings implementation as
the authoritative list of accepted variables.

| File | Purpose |
| --- | --- |
| [backend/.env.example](../backend/.env.example) | API, worker and migration settings |
| [.env.example](../.env.example) | Local Compose and production interpolation settings |
| [app/core/config.py](../backend/app/core/config.py) | Defaults, validation and secret-file loading |
| [docker-compose.production.yml](../docker-compose.production.yml) | Supported production mounts and service settings |

## Runtime and production safeguards

Common settings include `GOATFARM_DATABASE_URL`, `GOATFARM_DB_SSLMODE` (TLS to the DB;
production requires `verify-full`), JWT TTLs, Argon2 parameters,
`GOATFARM_CORS_ORIGINS`, `GOATFARM_COOKIE_SECURE` (set `true` behind HTTPS),
`GOATFARM_ENVIRONMENT` (`development`/`production`), `GOATFARM_AUTH_RATE_LIMIT_*`
(login/register and per-owner worker-password throttling), `GOATFARM_ALLOWED_HOSTS` (the
API virtual-host allowlist), `GOATFARM_MAX_FARMS_PER_USER`,
`GOATFARM_MAX_TEAM_MEMBERS_PER_FARM`, `GOATFARM_MAX_ROLES_PER_FARM`,
`GOATFARM_MAX_SIMULATION_SCENARIOS_PER_FARM`,
`GOATFARM_MAX_PENDING_MANUAL_TASKS_PER_FARM`, and the `GOATFARM_DB_POOL_*` /
`GOATFARM_DB_STATEMENT_TIMEOUT_MS` pool guards (a request-path backstop only; migrations
use the separate timeout described in [Database migrations](migrations.md)).

## Database URLs and TLS

Database URLs must use the exact `postgresql+asyncpg://` scheme. Keep TLS configuration
in `GOATFARM_DB_SSLMODE` and (for a private CA) `GOATFARM_DB_SSLROOTCERT_PATH`; a
matching legacy `?sslmode=` URL parameter is accepted and removed before asyncpg
connects, while conflicting or other URL-level TLS settings are rejected at boot.

## Request bounds

Requests are capped at 1 MiB by default (`GOATFARM_MAX_REQUEST_BODY_BYTES`); configure
the edge proxy to the same or a smaller limit. Request paths plus query strings are
capped at 8 KiB (`GOATFARM_MAX_REQUEST_TARGET_BYTES`, returning 414); the edge must
apply an equal or smaller request-line limit.

## Signing keys

RS256 key pairs are auto-generated on first run in development only: new generations
land in `~/.cache/goatfarm/keys` (`$XDG_CACHE_HOME/goatfarm/keys` when set), outside the
repository tree; an existing `backend/keys/` pair is still honored when already present.
Production must mount a stable matching RSA keypair (at least 2048 bits); startup fails
immediately if it is missing or invalid.

## Production validation

With `GOATFARM_ENVIRONMENT=production` the app **refuses to boot** if
`GOATFARM_COOKIE_SECURE` is false, the password minimum is below 12, database TLS does
not verify both the certificate chain and hostname, or CORS contains anything other than
exact non-loopback HTTPS origins, or the HTTP host allowlist is empty/wildcard/loopback,
or a custom refresh-cookie name lacks the case-sensitive `__Host-` prefix. The
development cookie default is upgraded to `__Host-goatfarm_refresh` automatically in
production; `/docs`, `/redoc` and `/openapi.json` are not served.

## API process and proxy limits

The auth rate limiter is in-memory and per process: run exactly **one** uvicorn worker /
replica (with N workers the effective limit multiplies by N). Its storage sits behind
the `LimiterBackend` seam in `backend/app/ratelimit.py` (`MemoryLimiterBackend` is the
only implementation); `GOATFARM_RATE_LIMIT_BACKEND` accepts only `memory` and startup
fails with this same explanation for anything else, so a multi-replica deployment cannot
boot into silently multiplied limits — a shared backend (e.g. Redis) is the future fix
if multi-process is ever needed. The supported production Compose file declares one
backend replica and the API holds PostgreSQL advisory lock `718204615` on a dedicated
connection for its entire serving lifetime. A second API process/container therefore
fails startup even if an orchestrator overrides the manifest. Do not bypass that lease;
move every process-local admission control to a shared backend before scaling. The API
database endpoint must preserve session affinity for that dedicated connection;
transaction-pooled proxies are not part of the supported topology (use a direct
PostgreSQL endpoint or session pooling).

## Password and token work limits

Argon2 hashing and verification run off the event loop in a dedicated, queue-free pool
bounded by `GOATFARM_ARGON2_WORKER_THREADS` (two by default); excess password work gets
a retryable `429` instead of blocking readiness or allocating an unbounded queue.
Rejected refresh tokens use the base `GOATFARM_AUTH_RATE_LIMIT_MAX_ATTEMPTS` per-IP
ceiling, checked before the next JWT decode. A separate all-refresh predecode ceiling is
ten times that value, so successful page loads do not consume the smaller invalid-only
budget. Once an IP fills the invalid-only budget, every refresh from that IP—including a
valid cookie behind the same NAT—is rejected until the window expires; this is the
unavoidable fail-closed tradeoff when validity itself requires signature verification.
Production must also apply a shared edge/WAF rate limit before requests reach the
process: cover login/register/refresh **and every bearer-protected API path**. Fresh
malformed bearer JWTs must be signature-checked before the app can classify them, so an
in-process per-token limiter cannot safely stop a distributed unique-token flood without
also denying valid users behind a shared NAT. Keep this control at the shared edge; the
supported API topology has one process.

## Idempotency fingerprints

Production also requires a stable, independently generated (at least 32 characters)
`GOATFARM_IDEMPOTENCY_REQUEST_HMAC_SECRET` in the API process. It keys fingerprints for
idempotent operations whose request contains password material; the raw password and raw
idempotency key are never stored. The known development fallback is rejected in
production. Keep this secret distinct from JWT, database, and user-password secrets. The
same known fallback is also rejected from the verification-only previous-key list.

For the separate migration credential, timeout and write-quiescence requirements, see
[Database migrations](migrations.md). See [Deployment](deployment.md) for
service-specific secret directories and proxy configuration.
