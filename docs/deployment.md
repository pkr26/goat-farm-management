# Deployment

[Documentation index](README.md) · [Project overview](../README.md)

The supported production topology is a single host running the API, frontend, nginx edge
and optional screening worker, with an external PostgreSQL server. The public TLS
terminator forwards to the edge's loopback listener. The API holds a PostgreSQL session
advisory lease that prevents a second API process from serving concurrently.

Commands in this runbook start at the repository root unless they explicitly change
directory.

## Before rollout

1. Provision a host with a stable public address and monitoring. A 2–4 vCPU, 8 GB RAM
   host is the documented starting point; measure resource use for the deployment's
   actual workload.
2. Configure public TLS, an external `verify-full` PostgreSQL endpoint, and separate
   API, migration and screening-worker database credentials.
3. Mount the stable JWT keypair, independent TOTP encryption key, idempotency HMAC
   secret, and any enabled provider credentials. Production settings validation rejects
   an incomplete configuration.
4. Configure nightly encrypted, signed backups with recovery inventories, off-host log
   delivery and independently delivered alerts. Complete a recovery drill before
   treating the deployment as ready.
5. Verify the TLS terminator appends its connecting peer to `X-Forwarded-For`. Add its
   exact source address to `GOATFARM_TRUSTED_PROXY_HOSTS` alongside the edge IP. Two
   client source IPs must produce distinct edge forwarding chains; rate limiting one
   client must leave another able to authenticate.

## Health probes

`GET /healthz` reports process liveness without authentication. The frontend origin
serves its own liveness handler. `GET /readyz` checks `SELECT 1` against the backend
pool and returns 503 when the database is unreachable. Point load balancers and
orchestrators at the appropriate probe.

## Metrics

`GET /metrics` serves Prometheus text exposition (`goatfarm_http_requests_total` and
`goatfarm_http_request_duration_seconds` labeled by route template/method/status;
`goatfarm_auth_rate_limit_rejections_total` by limiter scope;
`goatfarm_idempotency_replays_total`; `goatfarm_simulation_admission_rejections_total`;
refresh-session purge and maintenance progress/failure counters). Collection defaults to
enabled in production, independently of endpoint exposure. Production `/metrics`
requires an independent random bearer token of at least 32 characters, delivered as
`GOATFARM_METRICS_BEARER_TOKEN` or its `_FILE` route. Without that credential the
endpoint returns 404; missing/wrong bearer authorization returns 401. The Compose edge
keeps it off the public route; scrape on the app network with `Authorization: Bearer
<token>`. In development public scraping defaults to enabled and can be disabled with
`GOATFARM_METRICS_PUBLIC_ENABLED=false`. `GOATFARM_METRICS_ENABLED=false` stops
collection and exposition together; counters are per process, consistent with the
single-process topology.

## Backend image and production topology

Build and run the backend image from the repository root on a private container network
behind the edge. Publish the edge listener only:

```bash
docker build -t goatfarm-backend .
docker run --expose 8000 \
  -v /secure/goatfarm-jwt:/app/keys:ro \
  -v /secure/goatfarm-postgres-ca.pem:/run/secrets/goatfarm-postgres-ca.pem:ro \
  -e GOATFARM_DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/goatfarm \
  -e GOATFARM_ENVIRONMENT=production \
  -e GOATFARM_COOKIE_SECURE=true \
  -e GOATFARM_CORS_ORIGINS='["https://app.example.com"]' \
  -e GOATFARM_ALLOWED_HOSTS='["api.example.com","backend"]' \
  -e GOATFARM_DB_SSLMODE=verify-full \
  -e GOATFARM_DB_SSLROOTCERT_PATH=/run/secrets/goatfarm-postgres-ca.pem \
  -e GOATFARM_MIN_PASSWORD_LENGTH=12 \
  -e GOATFARM_IDEMPOTENCY_REQUEST_HMAC_SECRET="$GOATFARM_IDEMPOTENCY_REQUEST_HMAC_SECRET" \
  -e GOATFARM_TOTP_ENCRYPTION_KEY="$GOATFARM_TOTP_ENCRYPTION_KEY" \
  -e GOATFARM_JWT_PRIVATE_KEY_PATH=/app/keys/jwt_private.pem \
  -e GOATFARM_JWT_PUBLIC_KEY_PATH=/app/keys/jwt_public.pem \
  goatfarm-backend
```

Run `alembic upgrade head` as a separate, one-shot release job before starting or
rolling API containers. The default API command never performs DDL and serves with
uvicorn as a non-root user. Production must use an external PostgreSQL endpoint whose
certificate matches the FQDN in the URL, with a publicly trusted chain or a read-only
private-CA bundle mounted at `GOATFARM_DB_SSLROOTCERT_PATH` in **each** migration, API,
and screening worker container. The application builds an explicit standard-library TLS
context for `verify-full`; it does not depend on asyncpg finding a hidden
`~/.postgresql/root.crt` in the non-root image.

`docker-compose.yml` is deliberately a local-development stack: its bundled PostgreSQL
has no server certificate or CA topology and its migration job refuses
`GOATFARM_ENVIRONMENT=production` before any API starts. Copy the repository-root
`.env.example` to `.env` for that local stack. For a real single-host deployment, use
the separate `docker-compose.production.yml` **by itself** (never merge it with the
local file). It has no `db` service, requires immutable backend/frontend/edge image
digests, mounts the database CA into migration/API/worker only, and mounts JWT PEMs into
the API only. Point its complete separately privileged `GOATFARM_DATABASE_URL` and
`GOATFARM_MIGRATION_DATABASE_URL` values at the external DB: the API role has no DDL
privileges and the migration role is distinct and DDL-capable. Percent-encode reserved
characters in URL usernames/passwords. The edge is the only host-published service, and
it binds loopback; a production TLS terminator proxies to it rather than exposing its
HTTP listener.

From the repository root, create a mode-`0600` environment file outside the checkout
(shown values are placeholders) and ensure the non-root container UID 10001 owns or can
read the two host-mounted secret paths. A mode-`0600` file is appropriate when it is
owned by UID 10001; do not make secrets world-readable:

```dotenv
GOATFARM_BACKEND_IMAGE_REPOSITORY=ghcr.io/<owner>/goatfarm-backend
GOATFARM_BACKEND_IMAGE_DIGEST=sha256:<published-backend-manifest-digest>
GOATFARM_FRONTEND_IMAGE_REPOSITORY=ghcr.io/<owner>/goatfarm-frontend
GOATFARM_FRONTEND_IMAGE_DIGEST=sha256:<published-frontend-manifest-digest>
GOATFARM_EDGE_IMAGE_REPOSITORY=ghcr.io/<owner>/goatfarm-edge
GOATFARM_EDGE_IMAGE_DIGEST=sha256:<published-edge-manifest-digest>
GOATFARM_DB_CA_FILE=/secure/goatfarm-postgres-ca.pem
GOATFARM_JWT_SECRET_DIR=/secure/goatfarm-jwt
GOATFARM_COMPOSE_ENV_FILE=/secure/goatfarm.production.env
GOATFARM_DATABASE_URL=postgresql+asyncpg://api:...@db.example.com:5432/goatfarm
GOATFARM_WORKER_DATABASE_URL=postgresql+asyncpg://goatfarm_worker:CHANGE_ME@your-postgres-host:5432/goatfarm
GOATFARM_MIGRATION_DATABASE_URL=postgresql+asyncpg://migrator:...@db.example.com:5432/goatfarm
GOATFARM_CORS_ORIGINS=["https://app.example.com"]
GOATFARM_ALLOWED_HOSTS=["app.example.com","backend"]
GOATFARM_IDEMPOTENCY_REQUEST_HMAC_SECRET=<independent-32-plus-character-secret>
GOATFARM_TOTP_ENCRYPTION_KEY=<independent-32-byte-base64url-secret>
# Optional file-delivered secrets: replace any of
# the plain secret values above with a *_FILE container path inside the
# read-only /run/secrets/app mount and remove the plain line. The
# config-guard preflight refuses an env file that delivers a required
# secret by both routes or by neither.
# GOATFARM_API_SECRET_DIR=/secure/goatfarm/api
# GOATFARM_MIGRATION_SECRET_DIR=/secure/goatfarm/migration
# GOATFARM_WORKER_SECRET_DIR=/secure/goatfarm/worker
# GOATFARM_WORKER_DATABASE_URL_FILE=/run/secrets/app/worker_database_url
# GOATFARM_DATABASE_URL_FILE=/run/secrets/app/database_url
# GOATFARM_MIGRATION_DATABASE_URL_FILE=/run/secrets/app/migration_database_url
# GOATFARM_IDEMPOTENCY_REQUEST_HMAC_SECRET_FILE=/run/secrets/app/idempotency_request_hmac_secret
# GOATFARM_TOTP_ENCRYPTION_KEY_FILE=/run/secrets/app/totp_encryption_key
# GOATFARM_S3_ACCESS_KEY_ID_FILE=/run/secrets/app/s3_access_key_id
# GOATFARM_S3_SECRET_ACCESS_KEY_FILE=/run/secrets/app/s3_secret_access_key
# GOATFARM_SCREENING_ANTHROPIC_API_KEY_FILE=/run/secrets/app/screening_anthropic_api_key
# GOATFARM_SCREENING_OPENAI_API_KEY_FILE=/run/secrets/app/screening_openai_api_key
# GOATFARM_MSG91_AUTH_KEY_FILE=/run/secrets/app/msg91_auth_key
GOATFARM_DOCKER_SUBNET=198.18.243.0/24
GOATFARM_EDGE_PROXY_IP=198.18.243.10
# The default trusts only the edge. With the required outer TLS terminator,
# append its exact source address as nginx sees it (often the app-network
# gateway, e.g. 198.18.243.1 for a host-local terminator); verify it from
# the edge access log and never substitute the whole Docker subnet.
GOATFARM_TRUSTED_PROXY_HOSTS=198.18.243.10,198.18.243.1
# Maximum farms owned by one account.
GOATFARM_MAX_FARMS_PER_USER=25
# Set both CSP lists and bucket CORS when screening is enabled.
GOATFARM_SCREENING_ENABLED=false
```

File delivery uses three separate host directories mounted read-only at
`/run/secrets/app`: `GOATFARM_API_SECRET_DIR` (default `./secrets/api`),
`GOATFARM_MIGRATION_SECRET_DIR` (`./secrets/migration`), and
`GOATFARM_WORKER_SECRET_DIR` (`./secrets/worker`). Use mode-`0600` files readable by UID
10001 outside the checkout. The migration directory contains only the DDL-role URL; the
API directory contains its runtime DB URL and HMAC/TOTP/metrics/provider credentials;
the worker directory contains its separate `GOATFARM_WORKER_DATABASE_URL` credential and
storage/provider keys. The worker's database role needs only its image-processing DML
grants and no DDL, account/authentication or API signing access. Required provider files
may be duplicated in the two appropriate directories. `*_FILE` paths stay relative to
the same in-container mount; remove each plain secret line after adopting its file
route. Missing, unreadable or blank files fail closed.

Upgrading an older shared-secret deployment requires this preflight: create the three
directories, copy only the role-appropriate files into each, create a separate worker DB
role/URL with the required DML grants, set the three directory variables and worker DB
delivery route, and remove `GOATFARM_APP_SECRET_DIR`. Run the documented `config-guard`
command on every rollout before migration. It rejects the retired shared-directory knob,
overlapping directory paths, ambiguous delivery, and missing worker DB delivery.
Plain-env deployments remain supported but must explicitly supply the separate worker
URL. Never point service directories at a shared parent. Host-side jobs that read the
URL from their own environment are unaffected: keep exporting `GOATFARM_DATABASE_URL`
(from your secret store/file) in the shell that runs `backend/scripts/backup.sh`.

Then render and execute the name preflight before every rollout. Rendering proves
required interpolation is present, while the explicit one-shot run catches misspelt
`GOATFARM_*` names in the exact env file — and, since the secrets became deliverable by
file or by plain value, that every required secret is delivered by exactly one of the
two routes. Do not rely on a previously completed `config-guard` container from an older
`up`: Compose can reuse that successful one-shot service even after the bind-mounted env
file changes.

```bash
docker compose --env-file /secure/goatfarm.production.env \
  -f docker-compose.production.yml config --quiet
docker compose --env-file /secure/goatfarm.production.env \
  -f docker-compose.production.yml run --rm --no-deps config-guard
docker compose --env-file /secure/goatfarm.production.env \
  -f docker-compose.production.yml pull
docker compose --env-file /secure/goatfarm.production.env \
  -f docker-compose.production.yml up -d
```

Do not replace the digest variables with mutable tags. Read the three multi-architecture
manifest digests from the GitHub release published by the gated release workflow; the
release notes pin the exact digests the workflow scanned. The images carry
provenance/SBOM attestations and keyless Sigstore signatures; the release contains a
signed `SHA256SUMS` bundle. Verify both with the exact issuer/repository/workflow
identity commands in [SECURITY.md](../SECURITY.md) before updating the manifest. The
production manifest constructs `repository@sha256:...`, while local/staging examples
below may use a release tag for convenience. Review [Database migrations](migrations.md)
before any upgrade or downgrade.

## Proxy trust

Run exactly one API process (see [Configuration](configuration.md)) behind a
TLS-terminating proxy; set `GOATFARM_TRUSTED_PROXY_HOSTS` to the fixed edge IP plus
every trusted forwarding hop's exact address as observed by the next hop, so Uvicorn can
walk past those hops and rate limiting keys on the real client IP. In the standalone
production Compose topology, the default is the edge IP only; its required host-local
TLS terminator commonly reaches Docker through the app-network gateway (for the example
subnet, `198.18.243.1`), which must be appended explicitly after verifying it in the
edge access log. Startup rejects a hostname (it can never match a peer address, so it
would silently trust nothing) and rejects `*` or any prefix-length-0 network
(always-trust, which makes `X-Forwarded-For` and every per-IP ceiling spoofable). Name
the proxy's addresses, not the range it sits in. Keep the raw edge listener
loopback-only and ensure the outer terminator appends the transport peer to
`X-Forwarded-For`; never expose a path that lets an untrusted network client inject a
trusted hop.

## Frontend and edge routing

The frontend runs as a **Next.js Node server**: dynamic routes, security headers and the
same-origin `/api` rewrite require a runtime. Build `frontend/Dockerfile` with the
internal API destination, for example:

```bash
docker build --build-arg BACKEND_URL=http://backend:8000 \
  -t goatfarm-frontend frontend
```

Place it behind the same public origin as the API — route browser `/api/*` traffic
directly through the edge to the backend. Next's rewrite proxy does not emit
`X-Forwarded-For` (it sets only `x-forwarded-host`), so the API would see the Next container's address for
every client: per-IP authentication limits would then apply to every client as one
shared source, causing deployment-wide throttling. `docker-compose.yml` therefore
exposes a single `edge` (nginx) container on loopback port 3000 by default; the API's
`8000` and the frontend's `3000` remain internal-only. It proxies `/api/` straight to
`backend:8000` and everything else to `frontend:3000`, preserves the browser's `Host`,
appends `X-Forwarded-For`, sets `X-Forwarded-Proto` from the static
`GOATFARM_EDGE_PUBLIC_SCHEME` deployment value (never from a client header), and mirrors
`GOATFARM_MAX_REQUEST_BODY_BYTES` with `client_max_body_size 1m`. The edge holds one
fixed address inside the operator-selectable `GOATFARM_DOCKER_SUBNET` network so
`GOATFARM_TRUSTED_PROXY_HOSTS` can name exactly that one host (`GOATFARM_EDGE_PROXY_IP`)
rather than the bridge range, which would also cover the docker gateway. Compose uses
the same edge-IP interpolation for both the nginx address and backend trust
configuration, and an explicit `GOATFARM_TRUSTED_PROXY_HOSTS` in `.env` still overrides
the trust list — required when an additional outer proxy or load balancer fronts the
edge, whose address must also be trusted or every client keys the per-IP auth ceilings
as one IP. Set `GOATFARM_EDGE_PUBLIC_SCHEME=https` when that outer hop terminates public
TLS: Compose refuses to serve when `production` is configured with its HTTP default.
Keep `GOATFARM_EDGE_BIND_HOST=127.0.0.1` unless a deliberate TLS topology requires
another binding; never expose this raw HTTP listener directly. Outside `production`, the
edge refuses to start (exit 2) if `GOATFARM_EDGE_BIND_HOST` names a non-loopback address
— an isolated, firewalled staging box can opt in explicitly with
`GOATFARM_ALLOW_DEV_PUBLIC_BIND=true`. If the documented default conflicts with a host,
VPN, or cloud route, override the subnet and an address inside it in `.env`; no
Compose-file edit is required — but note that changing the subnet or edge IP of an
**already-created** stack (including upgrading across a release that changed the
defaults, e.g. `172.31.243.0/24` → `198.18.243.0/24`) requires recreating the network:
run `docker compose down` once before `docker compose up -d`, or Compose refuses to
start with an "incorrect ipam config" error. The `frontend` service is only `expose`d,
never published. The browser Content-Security-Policy is a per-request nonce policy
emitted by the frontend's `src/proxy.ts` — a nonce must be minted at the render boundary
so Next.js can stamp it on its own scripts, which neither the Next build nor the edge
can do. The deployment's S3/MinIO origins reach the frontend container as runtime env:
when `GOATFARM_SCREENING_ENABLED=true`, set both `GOATFARM_CSP_CONNECT_ORIGINS` and
`GOATFARM_CSP_IMG_ORIGINS` to the public S3/MinIO origin (space-separated exact HTTPS
origins; loopback HTTP is permitted only for local development). The proxy re-validates
them before they enter `connect-src`/`img-src`, and the edge entrypoint independently
validates the same values at boot, refusing startup if either list is blank, malformed,
or attempts header syntax injection — so a bad deployment value fails loudly before
nginx starts. Do not publish the standalone frontend directly: the edge is the sole
published listener and owns that boot-time validation. Next's server-side rewrite still
uses changeOrigin and sends `Host: backend:8000` for anything it does proxy, so every
Compose `GOATFARM_ALLOWED_HOSTS` override must retain the exact `backend` service name
alongside any public API hostname (for example, `["api.example.com","backend"]`).
Omitting it makes the API health check pass while every request Next forwards is
rejected with `400 Invalid host header`.

## Container hardening

Every service in `docker-compose.yml` runs with `security_opt:
["no-new-privileges:true"]` and `cap_drop: ["ALL"]`, and carries explicit
`mem_limit`/`cpus` values (db 1g/2.0, migrate 1g/1.0, backend 2g/2.0, frontend 1g/1.0,
edge 256m/0.5 — plain `docker compose up` enforces these compose-spec limits, no swarm
needed). Two services then re-add only the capabilities their images require: `db` gets
`CHOWN/DAC_OVERRIDE/FSETID/SETGID/SETUID` (initdb on the fresh volume plus the
root→postgres drop), and `edge` gets `CHOWN/SETGID/SETUID` — the official nginx image's
root master chowns its temp dirs at startup and every worker setgid/setuid(101)s, both
fatally without those capabilities. Dropping all of them crash-loops the edge, the sole
public listener. The edge additionally runs with a read-only root filesystem (its
writable surface is two size-capped tmpfs mounts: `/var/cache/nginx` for body/proxy
buffers, `/var/run` for the pid file), healthchecks a loopback-only `/edge-healthz`
location served by nginx itself (so its status never depends on an upstream being up),
and every service rotates its json-file logs at 10 MB × 3 files. Preserve these settings
when overriding `docker-compose.yml` for a deployment — especially the edge capability
set and `read_only`, which are load-bearing, required for startup and isolation.

## Registry images for local and staging environments

Pushing a `v*` tag runs `.github/workflows/release.yml`: it builds
`ghcr.io/<owner>/goatfarm-backend:vX.Y.Z` and `ghcr.io/<owner>/goatfarm-frontend:vX.Y.Z`
plus the hardened `ghcr.io/<owner>/goatfarm-edge:vX.Y.Z` for `linux/amd64` and
`linux/arm64` (so ARM hosts — including Apple-Silicon machines — pull a native
manifest), gates each architecture on the same fixable-HIGH/CRITICAL Trivy policy as the
security workflow **before publishing anything**, pushes the multi-arch manifests with
SLSA provenance and SPDX SBOM attestations attached via buildx, and creates a GitHub
release carrying the six per-architecture SPDX SBOM files, signed checksums, and
published image digests (tags containing a pre-release suffix such as `-rc1` publish as
GitHub pre-releases). On the deployment host, point Compose at the published tags
instead of local builds (`docker login ghcr.io` first — packages are private by
default):

```yaml
# docker-compose.override.yml (`!reset` needs Compose v2.24+)
services:
  # The name preflight runs from the same immutable backend artifact; do
  # not accidentally leave this one service building local checkout code.
  config-guard:
    build: !reset null
    image: ghcr.io/<owner>/goatfarm-backend:vX.Y.Z
  migrate:
    build: !reset null
    image: ghcr.io/<owner>/goatfarm-backend:vX.Y.Z
  backend:
    build: !reset null
    image: ghcr.io/<owner>/goatfarm-backend:vX.Y.Z
  frontend:
    build: !reset null
    image: ghcr.io/<owner>/goatfarm-frontend:vX.Y.Z
  edge:
    build: !reset null
    image: ghcr.io/<owner>/goatfarm-edge:vX.Y.Z
```

```bash
docker compose pull
docker compose run --rm --no-deps config-guard
docker compose up -d   # migrate still runs first as its own job
curl -fsS http://127.0.0.1:3000/healthz       # edge + frontend alive ("ok")
curl -fsS http://127.0.0.1:3000/readyz        # backend ready (SELECT 1 through the edge)
```

The guard validates the file Compose interpolated: plain `docker compose up` uses
`./.env`, and an operator overriding the stack with `docker compose --env-file other.env
up` should set `GOATFARM_COMPOSE_ENV_FILE=other.env` inside that same file (exactly as
the production template requires) so the preflight checks the env file that actually
drove interpolation.

This published-image override is for the local/development Compose topology; it
intentionally refuses `GOATFARM_ENVIRONMENT=production` because the bundled PostgreSQL
is TLS-off. Use the external-DB production topology above (and pin the same image
digests) for a real deployment.

The smoke `curl`s traverse the full edge path: `/healthz` is the frontend's own no-auth
route handler, `/readyz` still proxies through to the backend's DB-aware readiness
probe. Pin the exact tag in the override file — floating tags make rollbacks and the
one-migration-job protocol above impossible to reason about. Roll back by repinning the
previous tag and re-running the pull, `config-guard`, and `up` sequence above (after
checking the migration notes in [Database migrations](migrations.md) for downgrades,
which are not always reversible).

## Image changes and rollback

The frontend's `BACKEND_URL` is a build-time argument baked into its routes manifest.
Rebuild when the internal backend address or port changes; a runtime environment
override does not change the rewrite destination.

The weekly security workflow scans pinned base-image digests. Refresh the Dockerfile,
Compose and workflow pins together when a fixable high or critical vulnerability
requires an update. Production rollback also requires reviewing [Migration
limits](migrations.md#downgrades); repinning an image alone does not reverse a schema
change.

See [Backup and recovery](backup-recovery.md), [Data retention](data-retention.md),
[Security operations](security-operations.md), and the [Operations
baseline](../ops/README.md) for ongoing maintenance.
