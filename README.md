# Herdly — Goat Farm Management

Multi-farm web app for commercial **Osmanabadi** goat (meat) herds in
Telangana, India: kidding, 60-day weaning, buck rotation, festival-season
live-weight sales. Monorepo: async **FastAPI + PostgreSQL** JSON API
(`backend/`), a **Next.js + React + strict TypeScript** SPA (`frontend/`),
and a shared OpenAPI contract (`shared/openapi.json`).

```
backend/    FastAPI app (async SQLAlchemy 2.0 + asyncpg, Alembic, Argon2id, JWT)
frontend/   Next.js App Router SPA (Tailwind + shadcn/ui, TanStack Query, Orval)
shared/     openapi.json — the API contract (exported from the backend)
```

## Quick start

Prereqs: Python 3.13, uv 0.12.1, PostgreSQL 14+ running locally, Node 24+
with pnpm 9 (`corepack enable`).

```bash
# 1. Backend
cd backend
uv sync --frozen --extra dev           # creates backend/.venv from uv.lock
createdb goatfarm                      # once
./.venv/bin/alembic upgrade head
./.venv/bin/uvicorn app.main:app --reload --port 8000

# 2. Frontend (new terminal)
cd frontend
pnpm install --frozen-lockfile
pnpm dev                               # http://localhost:3000
```

The Next dev server proxies `/api/*`, `/healthz`, and `/readyz` to
`localhost:8000` (see `frontend/next.config.ts`), so the refresh cookie stays
first-party and every operation in the generated client remains same-origin.
Register → create a farm → start adding animals. Fixed-size global
reference data (bucket definitions, TMR recipes, vaccine templates) is seeded
automatically at startup; every new farm receives its role presets and feed
inventory synchronously. Legacy-farm repair runs only after readiness in
finite, independently committed, lock-skipping batches configured by
`GOATFARM_LEGACY_REPAIR_*`. This keeps rolling-deploy startup cost independent
of the number of farms and historical tasks.

`backend/uv.lock` pins the full transitive dependency graph (runtime + dev):
`uv sync --frozen --extra dev` reproduces it exactly, and `uv lock --upgrade`
re-resolves it after changing `pyproject.toml`.

Configuration is via `GOATFARM_*` env vars (`backend/app/core/config.py`; see
`backend/.env.example` for the full list — `.env` is read relative to
`backend/` regardless of the launch directory): `GOATFARM_DATABASE_URL`,
`GOATFARM_DB_SSLMODE` (TLS to the DB; production requires `verify-full`), JWT
TTLs, Argon2 parameters, `GOATFARM_CORS_ORIGINS`, `GOATFARM_COOKIE_SECURE`
(set `true` behind HTTPS), `GOATFARM_ENVIRONMENT`
(`development`/`production`), `GOATFARM_AUTH_RATE_LIMIT_*` (login/register and
per-owner worker-password throttling), `GOATFARM_ALLOWED_HOSTS` (the API virtual-host allowlist),
`GOATFARM_MAX_FARMS_PER_USER`, `GOATFARM_MAX_TEAM_MEMBERS_PER_FARM`,
`GOATFARM_MAX_ROLES_PER_FARM`, `GOATFARM_MAX_SIMULATION_SCENARIOS_PER_FARM`,
`GOATFARM_MAX_PENDING_MANUAL_TASKS_PER_FARM`,
and the `GOATFARM_DB_POOL_*` /
`GOATFARM_DB_STATEMENT_TIMEOUT_MS` pool guards (a request-path backstop only;
migrations have their own `GOATFARM_MIGRATION_STATEMENT_TIMEOUT_MS`, below).
Requests are capped at 1 MiB by
default (`GOATFARM_MAX_REQUEST_BODY_BYTES`); configure the edge proxy to
the same or a smaller limit. Request paths plus query strings are capped at
8 KiB (`GOATFARM_MAX_REQUEST_TARGET_BYTES`, returning 414); the edge must
apply an equal or smaller request-line limit. RS256 key pairs are
auto-generated into `backend/keys/` on first run in development only
(gitignored). Production must mount a stable matching RSA keypair (at least
2048 bits); startup fails immediately if it is missing or invalid. With
`GOATFARM_ENVIRONMENT=production` the app **refuses to boot** if
`GOATFARM_COOKIE_SECURE` is false, the password minimum is below 12, database
TLS does not verify both the certificate chain and hostname, or CORS contains
anything other than exact non-loopback HTTPS origins, or the HTTP host
allowlist is empty/wildcard/loopback, or a custom refresh-cookie name lacks the
case-sensitive `__Host-` prefix. The development cookie default is upgraded to
`__Host-goatfarm_refresh` automatically in production;
`/docs`, `/redoc` and `/openapi.json` are not served. The auth rate limiter
is in-memory and per process: run exactly **one** uvicorn worker / replica
(with N workers the effective limit multiplies by N). Argon2 hashing and
verification run off the event loop in a dedicated, queue-free pool bounded by
`GOATFARM_ARGON2_WORKER_THREADS` (two by default); excess password work gets a
retryable `429` instead of blocking readiness or allocating an unbounded queue.
Rejected refresh tokens use the base
`GOATFARM_AUTH_RATE_LIMIT_MAX_ATTEMPTS` per-IP ceiling, checked before the next
JWT decode. A separate all-refresh predecode ceiling is ten times that value,
so successful page loads do not consume the smaller invalid-only budget. Once
an IP fills the invalid-only budget, every refresh from that IP—including a
valid cookie behind the same NAT—is rejected until the window expires; this is
the unavoidable fail-closed tradeoff when validity itself requires signature
verification.
Production must also apply a shared edge/WAF rate limit before requests reach
the process: cover login/register/refresh **and every bearer-protected API
path**. Fresh malformed bearer JWTs must be signature-checked before the app
can classify them, so an in-process per-token limiter cannot safely stop a
distributed unique-token flood without also denying valid users behind a shared
NAT. Especially enforce this when the service is horizontally scaled.
Production also requires a stable, independently generated (at least 32
characters) `GOATFARM_IDEMPOTENCY_REQUEST_HMAC_SECRET` on every replica. It
keys fingerprints for idempotent operations whose request contains password
material; the raw password and raw idempotency key are never stored. The known
development fallback is rejected in production. Keep this secret distinct
from JWT, database, and user-password secrets. The same known fallback is also
rejected from the verification-only previous-key list.

Production releases should set `GOATFARM_MIGRATION_DATABASE_URL` to a
separately privileged database identity used only by Alembic. The long-running
API should use a credential without schema/DDL privileges. Compose supplies
only that URL—not the API credential—to the migration job. Alembic uses a
migration-only settings projection, so `GOATFARM_ENVIRONMENT=production`
refuses every `GOATFARM_DB_SSLMODE` except `verify-full` before creating its
engine, without requiring unrelated cookie/JWT/HMAC settings. Libpq backup and
restore jobs enforce the same production mode. Modes such as `require` encrypt
the wire but can leave the server unauthenticated when no trusted root is
configured. Migrations do **not** inherit the
request-path `GOATFARM_DB_STATEMENT_TIMEOUT_MS` budget: the Alembic connection
applies `GOATFARM_MIGRATION_STATEMENT_TIMEOUT_MS` instead, `0` (unbounded) by
default, because a table rewrite, a constraint validation, or a `CREATE INDEX
CONCURRENTLY` (which waits for every concurrent transaction to drain)
legitimately runs far longer than any request may, and cancelling one aborts
the release job. A fixed 10-second `lock_timeout` still applies, so DDL that
cannot acquire its lock fails fast instead of queueing behind live traffic.

The `f3d4e5f6a7b8` release migration validates the legacy personal-task role
invariant and builds a transactional partial index on `tasks`. Its ordinary
`CREATE INDEX` takes a PostgreSQL `SHARE` lock that blocks task writes while
the build runs. Schedule that one-shot upgrade in a maintenance window, after
the migration job has exclusive rollout ownership, rather than during live
task traffic. The later `a1b2c3d4e5f7` repairs the personal-task rows that
revision's PENDING-only backfill skipped: `ck_tasks_user_assignment_has_role`
also fires on an UPDATE that moves a row back *into* `PENDING`, which is
exactly what rejecting a completed cleaning duty does, so an unrepaired row
made that duty permanently un-rejectable.

Treat the first upgrade of any existing deployment to the current head as a
maintenance operation, not as an online rolling migration. The intervening
history includes feed-quantity table rewrites, potentially full-table data
preflights and backfills, constraint validation, session cleanup, and non-concurrent index
creation. First rehearse the complete upgrade against a current restored copy,
record its runtime and lock impact, take a verified backup, quiesce application
writes, and give one migration job exclusive ownership until `alembic check`
passes. Resume API replicas only after that succeeds.

## Auth & tenancy model

- Login/register issue an RS256 **access JWT** (30 min, `Authorization:
  Bearer`, held in memory only by the SPA) plus a rotating **refresh JWT**
  (14 d, httpOnly `SameSite=Lax` cookie scoped to `/`; production uses the
  host-bound `__Host-goatfarm_refresh` name). Every
  refresh token is backed by a server-side **session row**: refresh consumes
  the presented token and rotates it, presenting an already-consumed or
  revoked token is treated as theft and revokes the whole token family (with
  a three-second idempotent grace for simultaneous tabs), and
  logout / password change / owner-initiated worker password reset revoke the
  user's sessions server-side. Farm-membership deactivation is tenant-local:
  it immediately removes that farm's access without logging the same account
  out of unrelated farms. Access JWTs carry a
  server-checked revocation version, so those security events invalidate
  already-issued bearer tokens as well as refresh sessions. API responses are
  marked `Cache-Control: no-store`.
  Refresh families and retained rotations are hard-capped per user/family;
  old consumed rows can be compacted without weakening replay detection
  because every newly issued token carries its signed family identifier.
  Expired server-side rows are purged only in ordered, lock-skipping finite
  batches at startup and periodically (`GOATFARM_REFRESH_SESSION_CLEANUP_*`).
  Legacy tenant repairs, inactive-animal duty retirement, and tombstoned-user
  membership deactivation likewise run only in finite post-readiness batches
  (`GOATFARM_LEGACY_REPAIR_*`, `GOATFARM_INACTIVE_ANIMAL_TASK_CLEANUP_*`, and
  `GOATFARM_DELETED_MEMBERSHIP_CLEANUP_*`). Inactive-animal duties and deleted
  users are excluded from actionable/authorized views immediately, before
  their retained rows converge in the background.
  Tokens require issuer, audience, subject, kind, id, issued-at and expiry
  claims; configure stable `GOATFARM_JWT_ISSUER` and
  `GOATFARM_JWT_AUDIENCE` values for every environment.
  `POST /api/auth/change-password` gives users self-service password change
  (requires the current password; revokes all other sessions).
- Farm context travels in the **`X-Farm-Id` header**, validated per request
  (owner or active membership). Every farm-scoped domain endpoint requires it;
  the only exceptions are `GET /api/simulation/defaults` and
  `GET /api/simulation/defaults/breeds`, which serve global breed/production
  assumptions and need authentication alone (no farm context, no permission).
- RBAC: owners hold every permission; workers get a role's permission bundle
  (presets: Farm Manager, Animal Mover, Veterinarian, Procurement Officer,
  Feeder, Cleaner, Cleaner Manager, Accountant, Auditor — all editable, plus
  custom roles). `GET
  /api/auth/permissions` returns the caller's effective set for the active
  farm; the nav and buttons mirror it.
- Legacy `pbkdf2_sha256$iterations$salt_hex$digest_hex` password hashes
  (pre-migration users) verify transparently and are upgraded to Argon2id on
  first login. The supported import range is an iteration count from 1
  through 1,000,000; spellings Python's `int()` accepts (`+50000`, `50_000`,
  surrounding whitespace) verify for compatibility with historical imports,
  and a hash outside the ceiling now logs a server-side warning so the
  lockout is diagnosable instead of reading as a wrong password. Every
  rejected login additionally pays
  `GOATFARM_REJECTED_LOGIN_PBKDF2_WORK_BUDGET` PBKDF2 iterations of timing
  padding (default 50,000); a deployment importing costlier legacy hashes
  must raise it to at least its maximum imported iteration count. Before
  importing an external user table or deploying this boundary over existing
  rows, audit accounts that need an offline rehash/password reset:

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

  Any returned row cannot log in through the legacy verifier; review it before
  rollout rather than raising the per-request work ceiling ad hoc.
- Login and register are rate-limited (failed attempts for login, keyed per
  client IP + email; all register attempts per client IP) and farm ownership
  is capped per user. Behind a reverse proxy, set
  `GOATFARM_TRUSTED_PROXY_HOSTS` so real client IPs key the limiter, and enforce
  a shared edge/WAF ceiling on every auth path—including successful login and
  refresh traffic—and every bearer-protected endpoint before CPU-hard password
  work, JWT verification, or session-row mutation.
- Rejected refresh attempts are IP-throttled; successful page-load refreshes
  do not consume the abuse budget. The SPA coordinates refresh across tabs
  with the browser Web Locks API when available.
- Team consent guard: until verified invitations are implemented, **every
  pre-existing account is refused** by worker provisioning. Password resets
  are allowed only for new accounts explicitly provisioned by that farm; the
  provenance is stored on the membership and legacy rows fail closed.
- Non-owner account deletion atomically tombstones the login identity, scrubs
  its profile/password, and revokes sessions. That User tombstone is the
  immediate authorization barrier. Memberships and task assignments remain as
  non-authorizing audit/FK anchors; active memberships are deactivated later in
  finite `FOR UPDATE SKIP LOCKED` batches, never enumerated by the deletion
  request. Attributed farm, health, task, and finance history is therefore not
  rewritten. Tombstones cannot authenticate and display as `Deleted account`;
  the old email can register as a new user ID. This is
  de-identification/pseudonymization, not a promise that every operational
  record is anonymous. Set documented legal-purpose and retention rules before
  production. Farm owners must currently transfer/dispose of ownership through
  an operator process before their sign-in account can be deleted.
  Farm-selector and account-export hydration also fail explicitly above the
  configurable `GOATFARM_MAX_ACCOUNT_AFFILIATIONS_PER_RESPONSE` ceiling, so a
  pathological legacy/imported identity cannot force an unbounded response.

## Development

```bash
# Backend
cd backend
./.venv/bin/python -m pytest            # 3,400+ tests, real PostgreSQL (goatfarm_test)
./.venv/bin/ruff format --check . && ./.venv/bin/ruff check .
./.venv/bin/python -m mypy --strict app
./.venv/bin/mutmut run --max-children 4 # deterministic, DB-free simulation profile
./.venv/bin/mutmut results
./.venv/bin/python scripts/export_openapi.py   # regenerate shared/openapi.json

# Frontend
cd frontend
pnpm orval           # regenerate the typed client from shared/openapi.json
pnpm test            # 1,300+ Vitest + MSW tests
pnpm exec playwright test   # 34 browser/proxy e2e tests across 16 specs (fresh user+farm
                     # provisioned per run by e2e/global-setup.ts; serial workers)
pnpm build           # strict typecheck + production build
```

The mutation profile deliberately selects deterministic simulation tests and
uses `--mutation-pure` to deselect PostgreSQL integration cases. Do not run
parallel mutation workers against the shared integration-test database; use a
unique database and one child if a future campaign targets DB-backed services.
Because the profile mutates only covered lines, archive or remove the generated
`backend/mutants/` cache after editing mutation-target source code and before an
authoritative run. Mutmut 3.7 can otherwise reuse stale line-coverage mappings;
test-only changes are invalidated by the configured test-file dependency hash.

**Mutation-score scope (read before quoting a number).** The mutmut campaign
mutates **only `app/simulation/*.py` and `app/schemas/simulation.py`** —
20 of the backend tree's ~240 Python files — and runs only the ten deterministic simulation
suites against each mutant (`only_mutate` and
`pytest_add_cli_args_test_selection` in `backend/pyproject.toml`). A headline
like "90% kill rate" is a statement about the simulation package, not the
routers, services, security, or data layer; those are covered by the
integration suite over real PostgreSQL instead. The same applies to frontend
Stryker deltas quoted from targeted campaigns — the last full-repo snapshot
is the authoritative aggregate, and targeted post-remediation reports carry
live survivors by construction.

The API contract flows one way: backend routes/schemas →
`shared/openapi.json` → Orval-generated TanStack Query hooks
(`frontend/src/api/generated/`). After changing the backend, re-run the
export **and** `pnpm orval`.

CI (`.github/workflows/ci.yml`) runs the full gate on every push/PR: backend
pytest against a Postgres service, `ruff format --check`, `ruff check`,
`mypy --strict`, `pip-audit`; frontend `pnpm install --frozen-lockfile`,
`pnpm test`, `pnpm build`, `pnpm audit`; Playwright against the real frontend,
API, and PostgreSQL; and builds both application containers. A separate
pinned-action security workflow runs CodeQL, full-history secret scanning,
produces SPDX SBOMs for the backend, frontend, and deployed Compose
infrastructure images, and fails on error/high CodeQL or fixable high/critical
image vulnerabilities.
Dependabot monitors the Python, pnpm, Docker, and GitHub Actions ecosystems.

## Production

- Unauthenticated ops endpoints: `GET /healthz` (liveness: process up) and
  `GET /readyz` (readiness: `SELECT 1` against the pool, 503 when the DB is
  unreachable). Point load balancers / orchestrators at these.
- Build and run the backend image from the repo root on a private container
  network behind the edge/load balancer. Do not publish port 8000 directly:

  ```bash
  docker build -t goatfarm-backend .
  docker run --expose 8000 \
    -v /secure/goatfarm-jwt:/app/keys:ro \
    -e GOATFARM_DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/goatfarm \
    -e GOATFARM_ENVIRONMENT=production \
    -e GOATFARM_COOKIE_SECURE=true \
    -e GOATFARM_CORS_ORIGINS='["https://app.example.com"]' \
    -e GOATFARM_ALLOWED_HOSTS='["api.example.com","backend"]' \
    -e GOATFARM_DB_SSLMODE=verify-full \
    -e GOATFARM_MIN_PASSWORD_LENGTH=12 \
    -e GOATFARM_IDEMPOTENCY_REQUEST_HMAC_SECRET="$GOATFARM_IDEMPOTENCY_REQUEST_HMAC_SECRET" \
    -e GOATFARM_JWT_PRIVATE_KEY_PATH=/app/keys/jwt_private.pem \
    -e GOATFARM_JWT_PUBLIC_KEY_PATH=/app/keys/jwt_public.pem \
    goatfarm-backend
  ```

  Run `alembic upgrade head` as a separate, one-shot release job before
  starting or rolling API containers. The default API command never performs
  DDL and serves with uvicorn as a non-root user. `docker-compose.yml` models
  this explicitly with `db` → `migrate` → `backend`; its `jwtkeys` volume
  keeps the generated development identity stable across restarts. Copy the
  repository-root `.env.example` to `.env` first. Compose passes complete,
  separately configurable `GOATFARM_DATABASE_URL` and
  `GOATFARM_MIGRATION_DATABASE_URL` values rather than concatenating raw
  credentials. Percent-encode reserved characters in URL usernames/passwords;
  production should use a DDL-free API role and a distinct DDL-capable
  migration role. The edge is the only host-published service, and it binds
  loopback by default; a production TLS terminator proxies to it rather than
  exposing its HTTP listener.
  Supported Alembic and restore jobs share a database advisory lock, so two
  release/restore writers fail closed instead of racing. This protocol cannot
  stop manually issued DDL that ignores the application tooling: quiesce all
  operator schema changes and give the restore job exclusive rollout ownership
  until its final Alembic marker check succeeds.
  Migration `b7c8d9e0f1a2` also fails closed if a legacy health event has an
  authority-notification or isolation date without scheduled-disease suspicion.
  Preserve those compliance dates; reconcile each flagged event by confirming
  `suspected_scheduled_disease=true` and a nonblank `disease_target`, then retry
  the migration. The error reports the count and sample event IDs.
  The release that first applies security migration `f4e5f6a7b8c9` is a
  one-time exception to an ordinary rolling cutover: drain password-bearing
  worker-create traffic and stop every pre-HMAC API instance, apply the
  migration while writes are quiescent, and only then start the new image.
  Otherwise an old instance could insert another unkeyed fingerprint after
  the purge. Do not restore a pre-F4 database backup into a running post-F4
  service without reapplying the purge.
- Run **one** worker/replica (in-memory login and per-owner worker-password
  rate limits, see Configuration),
  behind a TLS-terminating proxy; set `GOATFARM_TRUSTED_PROXY_HOSTS` to the
  proxy's own IPs/CIDRs so rate limiting keys on real client IPs. Startup
  rejects a hostname (it can never match a peer address, so it would silently
  trust nothing) and rejects `*` or any prefix-length-0 network (always-trust,
  which makes `X-Forwarded-For` and every per-IP ceiling spoofable). Name the
  proxy's addresses, not the range it sits in.
- The frontend is a **Next.js Node server**, not a static export: dynamic
  routes, security headers and the same-origin `/api` rewrite require a
  runtime. Build `frontend/Dockerfile` with the internal API destination, for
  example:

  ```bash
  docker build --build-arg BACKEND_URL=http://backend:8000 \
    -t goatfarm-frontend frontend
  ```

  Place it behind the same public origin as the API — but do **not** route
  browser `/api/*` traffic through Next's rewrite. That proxy never emits
  `X-Forwarded-For` (it sets only `x-forwarded-host`), so the API would see the
  Next container's address for every client: the eleventh signup in five
  minutes — from anyone — 429s, and 100 bad logins lock the deployment out.
  `docker-compose.yml` therefore exposes a single `edge` (nginx) container on
  loopback port 3000 by default; the API's `8000` and the frontend's `3000`
  remain internal-only. It proxies `/api/` straight to `backend:8000` and everything else to
  `frontend:3000`, preserves the browser's `Host`, appends
  `X-Forwarded-For`, sets `X-Forwarded-Proto` from the static
  `GOATFARM_EDGE_PUBLIC_SCHEME` deployment value (never from a client header),
  and mirrors
  `GOATFARM_MAX_REQUEST_BODY_BYTES` with `client_max_body_size 1m`. The `edge`
  holds one fixed address inside the operator-selectable
  `GOATFARM_DOCKER_SUBNET` network so
  `GOATFARM_TRUSTED_PROXY_HOSTS` can name exactly that one host
  (`GOATFARM_EDGE_PROXY_IP`) rather than the bridge range, which would also
  cover the docker gateway. Compose uses the same edge-IP interpolation for
  both the nginx address and backend trust configuration, and an explicit
  `GOATFARM_TRUSTED_PROXY_HOSTS` in `.env` still overrides the trust list —
  required when an additional outer proxy or load balancer fronts the edge,
  whose address must also be trusted or every client keys the per-IP auth
  ceilings as one IP. Set `GOATFARM_EDGE_PUBLIC_SCHEME=https` when that outer
  hop terminates public TLS: Compose refuses to serve when `production` is
  configured with its HTTP default. Keep `GOATFARM_EDGE_BIND_HOST=127.0.0.1`
  unless a deliberate TLS topology requires another binding; never expose this
  raw HTTP listener directly. If the documented default conflicts with a host, VPN,
  or cloud route, override the subnet and an address inside it in `.env`; no
  Compose-file edit is required — but note that changing the subnet or edge
  IP of an **already-created** stack (including upgrading across a release
  that changed the defaults, e.g. `172.31.243.0/24` → `198.18.243.0/24`)
  requires recreating the network: run `docker compose down` once before
  `docker compose up -d`, or Compose refuses to start with an
  "incorrect ipam config" error. The
  `frontend` service is only `expose`d, never published.
  Next's server-side rewrite still uses changeOrigin and sends
  `Host: backend:8000` for anything it does proxy, so every Compose
  `GOATFARM_ALLOWED_HOSTS` override must retain the exact `backend` service
  name alongside any public API hostname (for example,
  `["api.example.com","backend"]`). Omitting it makes the API health check pass
  while every request Next forwards is rejected with `400 Invalid host header`.

**Zero-downtime JWT key rotation:** every newly issued token carries a
deterministic `kid` (the base64url SHA-256 fingerprint of its RSA public key).
The API signs only with the active private key and verifies by `kid` against
the active public key plus at most three verification-only public keys from
`GOATFARM_JWT_PREVIOUS_PUBLIC_KEY_PATHS` (a JSON path array). Tokens issued
before `kid` was introduced are tried against that same bounded keyring during
migration; a token that supplies an unknown `kid` is rejected without fallback.
Startup validates every key as RSA >=2048 bits, rejects duplicate key IDs, and
checks that the active pair matches.

Use a two-phase rollout so mixed old/new API instances can verify one another's
tokens throughout a rolling deployment:

1. Generate a new RSA pair at new secret paths; do not overwrite the running
   pair in place. Keep the old private key secured for emergency rollback, but
   never put a private-key path in the previous-key list.
2. Pre-stage the new **public** key: keep the old pair active, add the new
   public path to `GOATFARM_JWT_PREVIOUS_PUBLIC_KEY_PATHS`, and roll/restart
   every instance. No instance signs with the new key yet, but every instance
   can verify it before cutover begins.
3. Roll the signing cutover: point `GOATFARM_JWT_PRIVATE_KEY_PATH` /
   `GOATFARM_JWT_PUBLIC_KEY_PATH` at the new pair and replace the staged entry
   with the old **public** path, for example
   `["/run/secrets/goatfarm_jwt_2026_07_public.pem"]`. During this rollout, old
   instances have old-active/new-verification and new instances have
   new-active/old-verification, so both token generations work everywhere.
4. Keep the old public key configured for at least the refresh-token lifetime
   after the final old-key signer stopped (currently 14 days), plus the
   60-second clock-skew allowance. Monitor authentication failures throughout
   the overlap.
5. After that window, remove the old public path and restart all instances.
   Tokens carrying its `kid`—and legacy no-`kid` tokens signed by it—are then
   rejected. Securely retire the old private key under the deployment's key
   retention policy.

**Sensitive idempotency HMAC rotation:** generate a new independent secret;
never derive it from or reuse a JWT key. First deploy with the old secret still
current and the new secret included in
`GOATFARM_IDEMPOTENCY_REQUEST_HMAC_PREVIOUS_SECRETS`. Then deploy the new
secret as current and retain the old secret in that verification-only JSON
array. This two-phase rollout lets mixed replicas replay records made by either
generation. At most three previous secrets are accepted, all comparisons are
constant-time, and new records always use the current secret. Keep the old
secret for at least `GOATFARM_IDEMPOTENCY_RETENTION_HOURS` after the last old
signer stops, then remove it everywhere. If a key is compromised, do not keep
it for overlap: remove it and delete affected `team.workers.create`
idempotency rows, accepting that clients must retry with a new key. Migration
`f4e5f6a7b8c9` performs this purge once for every pre-HMAC worker-create row;
its deletion is intentionally not reversed by downgrade.

### Backups and disaster recovery

Run `backend/scripts/backup.sh` nightly against production and store the dump
off-host. The script writes `pg_dump` into a private same-filesystem temporary
directory, proves that `pg_restore --list` can parse it, then publishes the
archive and its exact-name SHA-256 sidecar. Each archive name includes an
unpredictable run suffix, so independently locked hosts cannot race on one S3
object key. A destination-wide lock prevents overlapping jobs, and failure
traps remove plaintext, unpublished files, and the owned lock. `GOATFARM_BACKUP_KEEP`
retains the newest 30 **local** dumps by default. Configure an S3 lifecycle
rule (including noncurrent-version expiry if bucket versioning is enabled) for
the same approved retention period; the backup job never deletes remote copies
because a writer cannot safely infer ownership of objects from another host.

Production and S3 backups must be both signed and encrypted with GPG. Pin the
signing key by its complete 40- or 64-hex fingerprint; do not use a mutable
email/key-name selector. S3 server-side encryption remains a second layer.
Keep the encryption private key and signing public key available to the restore
operators through a separately tested recovery path, and use least-privilege
database and object-storage credentials.

```bash
GOATFARM_DATABASE_URL='postgresql+asyncpg://user:pass@host/goatfarm' \
GOATFARM_DB_SSLMODE=verify-full \
GOATFARM_ENVIRONMENT=production \
GOATFARM_BACKUP_GPG_RECIPIENT='RECIPIENT_KEY_FINGERPRINT' \
GOATFARM_BACKUP_GPG_SIGNER_FINGERPRINT='SIGNING_KEY_FINGERPRINT' \
GOATFARM_BACKUP_S3_URI='s3://company-backups/goatfarm' \
./backend/scripts/backup.sh /var/backups/goatfarm
```

Both scripts read `GOATFARM_ENVIRONMENT` and `GOATFARM_DB_SSLMODE` — the two
application settings that gate TLS and the signed/encrypted-artifact
requirement — from `backend/.env` when the job did not export them, so a host
whose `.env` says `production` cannot be degraded to an unsigned plaintext dump
by an incomplete cron environment. An explicit export still wins, which is why
the invocations in this section pass both explicitly. They use the same pinned
`python-dotenv` grammar as the application, including `export` declarations,
quotes, inline comments, case-insensitive keys, and interpolation; install the
backend environment before running either script. The helper parses one pinned
regular-file snapshot (capped at 1 MiB); an in-place change, non-regular file,
or oversized file fails the job instead of silently falling back to development
defaults.

The database password is supplied to libpq through a mode-`0600` temporary
passfile; `pg_dump`, `psql`, and `pg_restore` receive only a password-free URL
built by `backend/scripts/libpq_url.py`, which reads the URL from stdin so it
never appears in any process's arguments.
The destination keeps one mode-`0600` `.goatfarm-backup.flock` regular file.
Never delete, rename, or rotate that inode: the script opens it on inherited FD
9 and takes a non-blocking kernel `flock` for the producer's complete process
tree. Concurrent producers fail closed, while process/host crashes release the
lock in the kernel without stale-lock takeover. During transition, a live
legacy `.goatfarm-backup.lock/pid` still blocks a run. After taking the kernel
lock, each new producer also atomically claims and holds that legacy directory
for its complete run, so a still-deployed old script cannot run beside it. The
claim is prepared completely under a private name, then published with an
exclusive atomic rename; the canonical name is therefore either absent or a
complete claim even if initialization is killed or encounters an I/O error.
The destination filesystem must implement the platform's atomic no-replace
directory rename (`RENAME_NOREPLACE` on Linux or `RENAME_EXCL` on macOS).
The script fails closed when that primitive is unavailable; it never degrades
the cross-version lock to a raceable check-then-rename on NFS/CIFS or another
unsupported mount. Validate this capability in the backup-host runbook.

During this transition, a dead old-format PID is deliberately not reclaimed:
the old shell may have died while its `pg_dump` child remains active and holds
no kernel lock. Verify the PID and every child are stopped, then remove that old
directory manually. A stale new-format ownership-marked claim can be reclaimed
after acquiring the flock because its descendants inherited FD 9. Directory,
PID-file, ownership-file, and exact-content snapshots are checked across every
quarantine move; a raced replacement is restored only with an exclusive
no-overwrite rename, and the new run fails closed. Cleanup similarly removes
only the exact directory identity it published. Alert on any non-zero backup
exit and on a missing daily artifact; an upload failure keeps the complete local
backup and makes a best-effort removal of any remote partial.

The baseline target is a 24-hour recovery-point objective (nightly full
backup) and recovery within four hours. Enable PostgreSQL WAL archiving and
point-in-time recovery when a tighter recovery point is required.

Restore only into a newly created, empty database. The helper accepts exactly
one checksum record bound to the archive basename, authenticates an encrypted
backup against the configured signer fingerprint, and validates the decrypted
archive before connecting. The archive and sidecar are first copied into the
private restore directory, so replacement of removable/shared source media
cannot swap bytes between verification and use. It refuses relations,
routines, types, extensions, extra schemas, or other namespaced user objects
anywhere in the target—not only public tables. `pg_restore` runs in one
transaction, and a separate post-restore query requires exactly one
well-formed Alembic revision marker. Production restores reject plaintext
archives.

Inject `GOATFARM_RESTORE_DATABASE_URL` through the process supervisor or
secret manager. Do not put the credential-bearing URL on the restore command
line (or type its value directly into shell history); the script accepts only
the archive path as a positional argument.

```bash
GOATFARM_RESTORE_CONFIRM=goatfarm_restore_test \
GOATFARM_DB_SSLMODE=verify-full \
GOATFARM_ENVIRONMENT=production \
GOATFARM_RESTORE_GPG_SIGNER_FINGERPRINT='SIGNING_KEY_FINGERPRINT' \
./backend/scripts/restore.sh /secure/goatfarm-2026-08-08.dump.gpg
```

The revision stored in an authentic backup may legitimately be older than the
currently deployed code. After the restore succeeds, point the migration URL
at the restored database and bring it to the current application head before
starting the API or performing smoke tests. Alembic builds an **async** engine,
so the migration URL must carry the `postgresql+asyncpg://` driver scheme;
a plain libpq URL resolves to psycopg2 and aborts with a bare
`ModuleNotFoundError: No module named 'psycopg2'` that names neither the URL
nor the cause. `restore.sh` itself accepts `postgres://`, `postgresql://` or
`postgresql+asyncpg://` and always hands `psql`/`pg_restore` a normalized
password-free `postgresql://` URL, so `backend/.env.example` sets
`GOATFARM_RESTORE_DATABASE_URL` with the async scheme and the anchored
substitution below is a no-op for it — while still upgrading a libpq URL if the
operator supplied one:

```bash
cd backend
GOATFARM_MIGRATION_DATABASE_URL="${GOATFARM_RESTORE_DATABASE_URL/#postgresql:/postgresql+asyncpg:}" \
GOATFARM_DB_SSLMODE=verify-full \
.venv/bin/alembic upgrade head
```

Run and document a restore drill at least quarterly. After that migration,
verify `alembic current`, representative row counts, `/readyz`, authentication,
and the core animal/health/feeding/finance screens; record the elapsed time
against the four-hour recovery target. Treat a failed Alembic sanity check as
an unusable restore requiring operator investigation. The helper removes its
passfile, source snapshot, GPG status, and private decrypted archive on every
exit path.

Encrypted backups can contain identity data from before a later account
deletion until those backups expire. Set and enforce a backup-retention period
that matches the privacy policy. A restore of an older recovery point can also
resurrect pre-deletion login/profile data, so production recovery requires an
external, access-controlled deletion-reconciliation ledger or equivalent
operator record that is replayed before the restored service accepts traffic.
Test that reconciliation in the quarterly drill; the database backup cannot
safely be its only source.

### Retrying side-effecting requests

Farm creation, finance transaction creation/correction, purchase-batch,
individual-animal and animal-weight creation, manual-duty and worker-account
creation, health-event creation, saved-simulation-scenario creation, and feed
dispense/mix/stock-add accept an
optional `Idempotency-Key` header (1–128
printable, non-whitespace ASCII characters). Farm creation has no tenant yet,
so those keys are scoped by authenticated actor and operation. Every other
key remains scoped by authenticated actor, farm, route and concrete path.
Reusing a key with the same validated request replays the original successful
JSON/status without repeating farm seeding, ledger, animal, task, scenario or
inventory effects; changing the body or concrete path identity returns HTTP
409. Failed 4xx/5xx attempts are not cached.

Successful records are retained for seven days by default, configurable with
`GOATFARM_IDEMPOTENCY_RETENTION_HOURS` (1 hour–90 days). The raw key is never
stored, only its SHA-256 digest. A background job uses the expiry index plus
`FOR UPDATE SKIP LOCKED` to delete small fixed batches (interval, batch size and
maximum batches are configurable). User requests never scan or delete a global
expiry cohort; reusing one expired key removes only that exact scoped row.

## The bucket system

Every animal lives in exactly one **bucket** (pen/stage), and every move is
recorded with a timestamp and reason. Buckets mirror the production cycle:

`QUARANTINE → FOUNDATION → BREEDING → PREGNANCY_EARLY → PREGNANCY_LATE →
DELIVERY → RECOVERY → RESTING → BREEDING …`

Kids branch off at weaning (day 60) into `MALE_KIDS` (sold at 8–9 months,
24–28 kg) and `FEMALE_KIDS` (grown to breeding-ready). New purchases sit in
`QUARANTINE` for a 45-day protocol (deworm → PPR → ET+TT → Goat Pox → FMD →
footbath) before joining `FOUNDATION`. The app auto-generates dated tasks for
every transition: ultrasounds, pre-kidding vaccines, bucket moves, weaning,
and the whole quarantine schedule.

## Duties & verification

- Auto-generated tasks (ultrasound, vaccine, bucket move, weaning, …) are
  assigned to the matching preset role automatically; owners can also create
  safe manual FEED/CLEANING/OTHER duties assigned to a role or a specific
  worker, with optional recurrence (`repeats every N days` — completing one
  schedules the next). Outstanding manual duties are capped per farm (5,000
  by default) so a compromised task creator cannot grow the queue without
  bound; completed/skipped history and generated workflow duties do not count.
- Workers see only duties assigned to their role or to them; completing a
  duty stamps `completed_by`/`completed_at` — who did what is recorded.
- **Cleaning verification loop**: CLEANING duties marked done wait in the
  *Awaiting verification* tab until a `tasks.verify` holder **verifies**
  them (→ VERIFIED, attributed) or **rejects** them with a note, which sends
  them back to the worker's list. Someone other than the completer must
  verify (the farm owner is exempt).
- Form-linked duties (ultrasound, kidding, vaccines) can only be completed
  through their record forms, not the task list's complete button.
- Key records (bucket moves, health events, feeding, weights, breedings,
  kiddings, transactions) carry `created_by_id` for attribution.

## Domain reference

- Gestation **150 days** (kidding window 145–155); ultrasound scan at
  breeding + 32 days; heat cycle 21 days. Kidding records are accepted only
  within 100–200 days of the breeding date.
- Breeding-ready doe: female, ≥10 months, ≥22 kg, not pregnant, in
  FOUNDATION / FEMALE_KIDS / RESTING. Two consecutive failed cycles →
  cull candidate.
- Weaning at day 60 (doe → RESTING; kids → MALE_KIDS / FEMALE_KIDS by sex).
- Feeding: TMR per bucket, 3 shifts split **40% (6:30 AM, sweep bunks) /
  20% (1:30 PM) / 40% (7:30 PM)**. RESTING switches MAINTENANCE_75_25 →
  FLUSH_70_30 at day 10; MALE_KIDS switch LACTATING_60_40 → FATTENING_50_50
  at day 91.
- Core vaccines: FMD (6-monthly, Sep/Mar), PPR (3-yearly), ET (annual,
  pre-monsoon), HS, Goat Pox, plus pre-kidding ET+TT 4–6 weeks before due
  date. Deworming every 6 months (June/January).
- Money in ₹ (Indian grouping), metric units.

## Backend layout

```
backend/
  app/
    main.py          App factory (lifespan seeds, CORS, routers, logging,
                     request IDs, /healthz + /readyz, prod-safety validation)
    core/config.py   Pydantic settings (GOATFARM_* env vars)
    db.py            Async engine/session (autoflush=False, pre-ping), Base
    models/          27 tables, domain enums, computed properties — split per
                     domain (enums, constants, core, animals, breeding, …)
    services/        All domain flows + state guards — split per domain
                     (animals, breeding, kidding, health, tasks, feeding,
                     purchases, finance, dashboard)
    security.py      Argon2id hashing, legacy pbkdf2 verify/upgrade, RS256 JWT
    permissions.py   Permission catalog + role presets
    deps.py          JWT auth, X-Farm-Id resolution, require_perm, session
                     revocation helpers
    seed.py          Reference data + per-farm presets (idempotent)
    schemas/         Pydantic v2 In/Out models per module
    api/             auth, animals, buckets, breeding, kidding, health, tasks,
                     feeding, finance, purchases, dashboard (incl. reports),
                     team, simulation, planner (target-based backward
                     planning); shared out-builders in api/_shared.py, shared
                     run-limits/offload machinery in api/_run_limits.py
  alembic/           Migrations (single linear head: initial schema through
                     auth/session, domain-traceability, finance/feed/task and
                     movement-clearance hardening)
  scripts/           export_openapi.py, healthcheck.py, backup.sh, restore.sh,
                     libpq_url.py (URL → credential-safe libpq inputs)
  tests/             3,400+ tests (logic, RBAC, adversarial, concurrency) on real PostgreSQL
```

## Frontend layout

```
frontend/
  src/
    app/
      page.tsx            Redirect hub (login / farm-select / dashboard)
      login|register/     Auth pages (RHF + Zod)
      farm-select/        Farm picker + create
      (app)/              Authed shell (permission nav) + module pages:
                          dashboard, animals (+[id]), buckets, breeding,
                          kidding, health (+schedule/[animalId]), tasks,
                          feeding (+recipes, inventory), purchases, finance,
                          reports, team
    api/generated/        Orval output (do not edit) + custom-instance.ts
    lib/                  api-client (token store, refresh retry, ApiError),
                          auth-context, use-permissions, format
    components/ui/        Committed shadcn/ui primitives (base-ui)
  e2e/                    Playwright specs (auth, animals, breeding flow, task guards)
  src/**/*.test.*         Vitest + MSW unit/component tests
```

## Notes

- Instants are stored as naive UTC datetimes. Date-only business rules use the
  active farm's IANA timezone (default `Asia/Kolkata`), so dashboards, due
  dates, feeding plans, and daily records change day at the farm's midnight.
  Existing date validation retains one day of clock-skew headroom for clients;
  genuinely future dates are still rejected.
- Passkeys are a **future amendment**: neither `webauthn` nor
  `@simplewebauthn/browser` is installed in this release.
- The production refresh cookie is host-bound (`__Host-` prefix), `Secure`,
  `HttpOnly`, `SameSite=Lax`, and `Path=/`, with no `Domain` attribute. CORS is
  credentialed and pinned to the frontend origin.
- The v1 Jinja app was removed after the rewrite; its behavioral contract
  lives on in `backend/tests/`.
