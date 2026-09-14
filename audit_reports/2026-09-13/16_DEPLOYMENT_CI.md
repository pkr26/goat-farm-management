# Red Team Audit — Part R: Deployment, CI/CD, Contract (R1–R9)

**Date:** 2026-09-13 · **Auditor:** RT-R (deployment/CI/contract) · **Repo:** `goat_saas` @ `main` (3ffdc80)

**Scope:** R1 root `Dockerfile`, R2 `frontend/Dockerfile`, R3 `docker-compose.yml` (+ inline edge nginx config), R4 `ci.yml`, R5 `release.yml`, R6 `security.yml` (+ `.github/scripts/gate_sarif.py`), R7 `mutation.yml`, R8 `shared/openapi.json`, R9 `frontend/e2e/*` as adversarial harness. Cross-cutting: `.dockerignore`, `.gitignore`, git-tracked-secret scan, digest verification against the live registry, README/compose parity.

**Method:** attack hypotheses per unit (baked secrets, pinning integrity, production-bypass defaults, isolation/amplification, CI RCE/injection, contract drift, harness blind spots). Every finding below is code-evidenced; unconfirmed items are marked. Sibling findings referenced, not re-derived: RT-M-1 (03_…MIDDLEWARE), RT-M-2, RT-L8-1 (09_…SIMULATION_ENGINE).

## Findings table

| ID | Severity | Unit | Title |
|----|----------|------|-------|
| RT-R-1 | Medium | R3 | No edge-layer rate limiting on the sole public listener (amplifies RT-M-2) |
| RT-R-2 | Medium | R3 | Flat Compose network: frontend/edge can reach `db:5432` — no segmentation |
| RT-R-3 | Medium | R3/R1 | 2g `mem_limit` + single worker + single replica converts RT-L8-1 into a whole-stack, cross-tenant outage |
| RT-R-4 | Medium | R3 | `development` default on a deliberate public bind is refused nowhere — log-only warning; every M10 validator stays off |
| RT-R-5 | Low | R9 | No cross-tenant / horizontal-authz negative tests anywhere in the e2e suite; rate-limit-disable makes M2/M3 regressions invisible |
| RT-R-6 | Low | R5 | Release publishes a *rebuild* of the scanned artifact (cache-mediated), not the scanned bits themselves |
| RT-R-7 | Low | R2 | `BACKEND_URL` is baked into `routes-manifest.json` at build time; runtime overrides silently no-op |
| RT-R-8 | Low | R1/R2/R3/R6 | Base-image digest refresh is incident-driven only; 3 of 4 pinned digests already lag their live tags |
| RT-R-9 | Info | R1 | Root build context ships audit reports / IDE dirs (context hygiene only — nothing copied into the image) |
| RT-R-10 | Info | R9/R4 | e2e backend readiness probe depends on `/openapi.json`, which is `None` under `ENVIRONMENT=production` |
| RT-R-11 | Info | R4 | CI-only `trust` auth Postgres, `SUPERUSER` runner role, host-network smoke — ephemeral-runner scoped, held |

No Critical findings. No High findings. All Critical-class hypotheses (exposed services, baked secrets, CI RCE) were attacked and held — see per-unit notes.

---

## RT-R-1 (Medium, R3) — No edge-layer rate limiting on the sole public listener

**Evidence:** `docker-compose.yml:241-295` — the entire `server{}` block of the edge config contains no `limit_req_zone` / `limit_req` / `limit_conn` directive. The edge is the only host-published listener (`docker-compose.yml:231-235`).

**Exploit sketch:** An unauthenticated client floods `POST /api/auth/login` / `/register` / `/refresh` through the edge. nginx applies no throttling and proxies every request; each lands on the single uvicorn worker (`Dockerfile:52`, `--workers 1`) where per-request Argon2 work plus the in-memory limiter's accounting all execute on that one process. Sibling RT-M-2 (Medium) already quantifies Argon2-pool saturation producing 429s for legitimate logins; the deployment adds no outer chokepoint, so the flood consumes connection/worker capacity before the app limiter even matters, and with one replica there is no shed lane for healthy traffic. The loopback default bind limits exposure to local/misconfigured-public deployments, which is why this is not High.

**Impact:** Availability of the auth path (and, via worker saturation, of the whole API) degrades under an unauthenticated L7 flood that a one-line edge policy would absorb.

**Fix:** Add `limit_req_zone $binary_remote_addr zone=edge_auth:10m rate=…` and `limit_req` (with `burst` + `nodelay`) on `location /api/auth/` (login/register/refresh), returning 429/503 at the edge. Behind the documented outer-proxy topology, key on the operator-trusted rightmost XFF hop — or keep keying on the edge's view and document it. Coordinate rates with the app limiter so the edge is the stricter of the two.

## RT-R-2 (Medium, R3) — Flat Compose network: frontend/edge can reach `db:5432`

**Evidence:** `docker-compose.yml:297-301` defines one `default` network; no service declares `networks:` beyond the edge's fixed address (`docker-compose.yml:225-230`), so db, migrate, backend, frontend and edge all share one bridge. `db` publishes no ports (correct), but within the bridge every peer can open `db:5432`.

**Exploit sketch:** Compromise the frontend container (Node runtime, exposed to edge traffic) or the edge (nginx, internet-facing when bound publicly). From either, `postgresql://…@db:5432` is directly reachable; only credential separation (frontend receives no DB credentials — verified, `docker-compose.yml:136-156`) stands between the compromised container and the tenant database. The same holds for backend↔frontend lateral movement (the frontend never needs the API's internal listener via Docker DNS except through the edge path — actually it does proxy to `backend:8000`, so backend must remain reachable from frontend; db does not).

**Impact:** Defense-in-depth gap: a container compromise in the least-trusted tiers gets network reach to the most sensitive one; any future credential leak (e.g., a copied env block) becomes immediately exploitable.

**Fix:** Two Compose networks: `db_net` (db + migrate + backend) and `app_net` (backend + frontend + edge). Backend joins both; frontend/edge never join `db_net`.

## RT-R-3 (Medium, R3/R1) — 2g mem_limit + single worker + single replica turns RT-L8-1 into a cross-tenant outage

**Evidence:** `docker-compose.yml:85` (`mem_limit: 2g`, commented as Monte-Carlo headroom), `Dockerfile:52` (`--workers 1`, documented as required by the in-memory limiter), `docker-compose.yml:5-6` (single replica, deliberate), `restart: unless-stopped` (`docker-compose.yml:79`). Sibling RT-L8-1 (High) shows `close_gaps` materializing purchase events pre-validation with ~500 GB projected RSS for a 608M-event run.

**Exploit sketch:** A tenant submits a planner run whose parameters materialize a very large event list. The kernel OOM-kills the only worker at 2g; `restart: unless-stopped` re-boots the container, startup re-runs, the client retries, and the loop repeats. Every other tenant's requests fail during each cycle. `stop_grace_period: 40s` and the graceful-shutdown design (`Dockerfile:52`, compose comment line 88-90) are for clean drains — they do nothing against SIGKILL from the OOM killer.

**Impact:** Deployment-level amplification of an application finding: a per-tenant compute abuse becomes a whole-farm outage, and the compose ceiling is documented as headroom rather than as a blast-radius bound with monitoring.

**Fix:** Primary fix is app-side (RT-L8-1: bound materialization before validation). Deployment-side: treat `mem_limit` as a blast-radius bound — add OOM/restart alerting (the edge healthcheck only asserts nginx itself, `docker-compose.yml:180-188`, and `/readyz` only recovers after restart), and document that the single-worker design couples every tenant to the largest admissible request.

## RT-R-4 (Medium, R3) — `development` default on a public bind is refused nowhere; log-only warning

**Evidence:** `docker-compose.yml:106` (`GOATFARM_ENVIRONMENT: ${GOATFARM_ENVIRONMENT:-development}` — same default for backend, edge (line 202) and migrate (line 74)); `.env.example:6` ships `GOATFARM_ENVIRONMENT=development`. The edge command refuses only the inverse mistake — production with HTTP (`docker-compose.yml:212-215`) — while a non-loopback bind under `development` emits a stderr **WARNING** and continues (`docker-compose.yml:216-223`).

**Exploit sketch:** An operator sets `GOATFARM_EDGE_BIND_HOST=0.0.0.0` (or a LAN IP) but never sets `GOATFARM_ENVIRONMENT=production`. The stack boots fully: dev JWT keypair auto-generated inside the `jwtkeys` volume, the publicly-known development HMAC idempotency secret accepted (the `:?` guard at `docker-compose.yml:118` only checks presence; the value check lives in the production-only validator, `backend/app/core/config.py:517-524`), `MIN_PASSWORD_LENGTH` 8, localhost CORS, relaxed cookie attributes — every M10 gate keyed on `environment == "production"` stays off, on a publicly reachable listener. The only defense is one stderr line that a `compose up -d` operator never sees.

**Impact:** A one-variable misconfiguration yields a silently weak public deployment; severity held at Medium because the default bind is loopback, going public requires a deliberate override, and that override does trigger the (missable) warning.

**Fix:** Refuse, don't warn: exit non-zero when `GOATFARM_EDGE_BIND_HOST` is non-loopback and `GOATFARM_ENVIRONMENT != production`, mirroring the existing production/HTTPS refusal (operators who genuinely want a dev-mode LAN bind can set an explicit `GOATFARM_EDGE_ALLOW_INSECURE_BIND=1` escape hatch).

## RT-R-5 (Low, R9) — No cross-tenant negative e2e coverage; harness structurally blind to tenancy + rate limiting

**Evidence:** All 20 specs reviewed. The only multi-farm test is same-user (`frontend/e2e/farm-switch.spec.ts:22-80` — the provisioned owner creates Farm B and asserts cache isolation; no second identity ever touches another identity's resources). `api-contract-auth-team.spec.ts` registers multiple identities (lines 161-178, 250, 364-396) but only for lifecycle flows (change-password, delete, worker CRUD); the sole 401 assertions are stale-credential checks (lines 349, 392, 507) and the domain spec's 404 is a post-abort read (`api-contract-domain.spec.ts:321`). No spec forges `X-Farm-Id`, attempts cross-farm object access, or asserts a horizontal-authz 403/404. Grep for `403|forbidden|cross-tenant|another farm` across specs: zero hits. Meanwhile CI runs the suite with `GOATFARM_AUTH_RATE_LIMIT_ENABLED: "false"` (`ci.yml:232`; flag name verified against `backend/app/core/config.py:244`, default `true`, set nowhere in compose/.env.example), so limiter behavior (RT-M-2 class) is invisible by construction.

**Impact:** The "adversarial harness" role claimed for R9 does not extend to the product's core security property (tenant isolation) or to its outer shell (rate limiting). A regression in B1 farm scoping or C2 purchase-cascade scoping that unit/integration tests missed would sail through e2e green.

**Fix:** Add one spec that provisions two identities (global-setup already demonstrates the pattern) and asserts: cross-farm animal/batch/finance reads 404 uniformly, forged `X-Farm-Id` on the other farm 404s, and cross-tenant deep links render the app's no-access state. Add a small opt-in rate-limit spec (`E2E_RATE_LIMIT=1` env, default off) asserting a 429 after N bursts.

## RT-R-6 (Low, R5) — Release publishes a rebuild of the scanned artifact

**Evidence:** `release.yml:78-167` gate-builds per-arch into the local daemon and Trivy-scans those loaded tags; `release.yml:223-247` then **builds again** (`docker/build-push-action`, `push: true`, `cache-from: type=gha`) rather than pushing the scanned digests. The GHA cache is size-capped and evictable; on a miss, the publish build regenerates layers instead of replaying them. Provenance `mode=max` (line 232, 245) attests the *rebuild*.

**Impact:** The Trivy gate and the published bits are coupled only by build determinism (frozen lockfiles, digest-pinned bases — strong, but unenforced). A cache-eviction or subtle nondeterminism decouples "what was scanned" from "what was shipped" without any check failing.

**Fix:** After pushing, re-run the Trivy gate against the pushed digest (`image-ref: ${BACKEND_IMAGE}@${digest}`), or assert layer-digest equality between the gate tag and the pushed manifest before the release job runs. Cheap and closes the gap completely.

## RT-R-7 (Low, R2) — `BACKEND_URL` baked at build time; runtime override silently no-ops

**Evidence:** `frontend/next.config.ts:59-61` computes rewrites from `process.env.BACKEND_URL` during `next build`; the result is flattened into `routes-manifest.json` — verified in the local build: `"destination": "http://localhost:8000/healthz"` etc. The standalone server routes from that manifest (`frontend/Dockerfile:30` copies `.next/standalone`), so a runtime `-e BACKEND_URL=…` changes nothing. `assertSafeBackendUrl` (`src/lib/backend-rewrites.ts:17-44`) correctly admits the single-label `http://backend:8000` compose default.

**Impact:** Availability/config-drift trap: renaming the backend service or changing its port without rebuilding the frontend image leaves the edge "healthy" (`/edge-healthz` is nginx-own) and the frontend liveness green (`/healthz` is its own route handler) while every `/api` rewrite proxies to a dead destination. Nothing fails until 502s.

**Fix:** Document "BACKEND_URL is build-time" next to the compose `build.args` (the compose file itself is fine — it always rebuilds with the right arg), and/or have the standalone server compare the baked destination's host against an env var at boot and fail fast on mismatch.

## RT-R-8 (Low, R1/R2/R3/R6) — Digest refresh is incident-driven only; three pins already lag

**Evidence:** All four pins are well-formed digests and pin real images. Verified against the live Docker Hub API (2026-09-13): `python:3.13-slim` now resolves to `sha256:9d2e5553…` vs pinned `sha256:9662417a…` (`Dockerfile:13`); `node:24-alpine` → `sha256:50c8e8ca…` vs pinned `sha256:d32cdf61…` (`frontend/Dockerfile:2`); `postgres:16` → `sha256:f1c3376c…` vs pinned `sha256:95206741…` (`docker-compose.yml:23`, mirrored in all four workflows); `nginx:1.29-alpine` matches (`docker-compose.yml:159`). No Dependabot/Renovate config exists under `.github/`.

**Impact:** The loop works but is reactive: a fixable HIGH/CRITICAL appearing in a pinned base only surfaces when the weekly `security.yml` scan of those same pinned bits goes red, after which a human re-pins. Until then, known-fixed CVEs ship in every release (they *would* be caught pre-push by the release gate only if the fix existed in the pinned bits — it doesn't, because the pin predates the fix).

**Fix:** Add a scheduled workflow (or Renovate container digest updates) that opens a PR whenever a pinned digest lags its tag and the delta contains security-relevant package changes.

## RT-R-9 (Info, R1) — Root build-context hygiene

Root `.dockerignore` excludes `.env*`, `backend/keys`, `backend/tests`, `.git`, `.github`, `docker-compose.yml`, `shared`, `frontend` — everything sensitive or irrelevant to the backend image. It does **not** exclude `audit_reports/`, `FRONTEND_AUDIT_REPORT.md`, `UI_UX_AUDIT*.md`, `.claude/`, `.vscode/`, `.zcode/`, `.mypy_cache/`, `.ruff_cache/` — all shipped to the builder as context. Nothing beyond `backend/app`, `alembic`, `alembic.ini`, `healthcheck.py` is `COPY`d (`Dockerfile:36-39`), so image content is unaffected; this is context bloat/hygiene only (audited those files clean of credentials — see cross-cutting). Frontend `.dockerignore` is tight (excludes `e2e`, `.env*`, `node_modules`, `.next`, test outputs).

## RT-R-10 (Info, R9/R4) — e2e readiness probe requires dev-mode openapi exposure

`playwright.config.ts:59` gates the backend webServer on `http://localhost:8000/openapi.json`, but `backend/app/main.py:587` sets `openapi_url=None` under `ENVIRONMENT=production`. The suite therefore cannot run against a production-mode backend (it would wait 120s and fail with a confusing timeout). Dev/CI default is development, so this is a latent trap for anyone pointing e2e at a prod-like stack — worth a comment or a `/healthz`-based probe.

## RT-R-11 (Info, R4) — CI service/runner posture (held)

`ci.yml:29-31,216-219,308-311` and `mutation.yml:35-37` run Postgres with `POSTGRES_HOST_AUTH_METHOD: trust` and password `postgres` on the runner's localhost; `ci.yml:59,257` creates a `SUPERUSER` `runner` role; `ci.yml:340-346` runs the built backend on `--network host`. All on ephemeral, isolated hosted runners with no secrets in scope (`permissions: contents: read`), no inbound reachability — standard and acceptable. The register smoke correctly asserts a string `access_token` (`ci.yml:362-366`).

---

## Per-unit attacked-&-held notes

### R1 — root `Dockerfile`
- **Digest pinning:** real 64-hex digest present (`Dockerfile:13`); pin lags live tag (RT-R-8) but that is pin semantics, not a weakness.
- **uv supply chain:** `uv==0.12.1` installed via `pip` from PyPI over TLS (`Dockerfile:30`); dependencies installed only via `uv sync --frozen --no-dev --no-install-project` from the committed lock (with artifact hashes). No curl|sh patterns.
- **Layer order:** manifest copied before app (`Dockerfile:26` vs `36-39`) — cache-poisoning would require a poisoned lockfile, which CI's `uv lock --check` (`ci.yml:69`) plus review gate. Held.
- **Non-root:** `USER goatfarm` after all root ops (`Dockerfile:41`); uid/gid 10001 system account; keys dir created root-side then owned `goatfarm:0700` (`Dockerfile:34`). Named-volume first-mount copy-up preserves the image dir's ownership/perms, so the `jwtkeys` volume is writable by the backend user — no root-owned-volume trap.
- **Healthcheck:** `python healthcheck.py` runs under the venv PATH; Host synthesis (`backend/scripts/healthcheck.py:16-25`) sends `allowed_hosts[0]` (compose default `localhost` — passes TrustedHost) and synthesizes a subdomain for wildcard entries. Works with the compose `GOATFARM_ALLOWED_HOSTS` default. Held.
- **CMD:** exec form, uvicorn PID 1, `--workers 1` hard-coded (in-memory limiter honesty), `--no-proxy-headers --no-server-header` (proxy trust moved to the app's explicit `GOATFARM_TRUSTED_PROXY_HOSTS` allowlist — consistent with the compose single-edge-IP default, `docker-compose.yml:127`), graceful shutdown 30s matched by compose `stop_grace_period: 40s` (`docker-compose.yml:90`). `EXPOSE 8000` only. No secrets in context (`.dockerignore`). Held.

### R2 — `frontend/Dockerfile`
- Digest-pinned base in all three stages (same digest — no stage confusion). `npm`/`npx` and the global npm tree removed from the runner (`frontend/Dockerfile:26-27`); corepack/pnpm shims not enabled in the runner. Non-root `nextjs` (10001).
- Standalone output: only `.next/standalone` + `.next/static` copied into the runner (lines 30-31) — no source maps (productionBrowserSourceMaps defaults off; none observed in `.next` manifests), no dev deps, no test/e2e files (dockerignore). `next.config.ts` sets `poweredByHeader: false` and enforces CSP/HSTS in production builds (`NODE_ENV=production`, `frontend/Dockerfile:18`).
- Healthcheck probes its own `/healthz` route handler with `wget --spider` — correctly decoupled from backend availability. `BACKEND_URL` build-arg baking → RT-R-7 (Low). Held otherwise.

### R3 — `docker-compose.yml` + edge nginx
- **Ports:** only the edge publishes (`${GOATFARM_EDGE_BIND_HOST:-127.0.0.1}:3000:3000`, line 235); db/backend/frontend are `expose`-only. Verified no other `ports:` anywhere. Held.
- **Secrets:** `POSTGRES_PASSWORD` and `GOATFARM_IDEMPOTENCY_REQUEST_HMAC_SECRET` use `:?` required interpolation (lines 39, 118); URLs supplied whole (no compose-side credential splicing); migration/API credential split (lines 70, 98) with a migration-only settings projection (lines 74-75). `.env.example` ships only empty or known-development values, and the production gate rejects the known dev HMAC value. Held (residual: RT-R-4).
- **Body limits:** edge `client_max_body_size 1m` (`docker-compose.yml:260`) vs backend default `max_request_body_bytes = 1_048_576` (`backend/app/core/config.py:169`, ceiling 20 MiB) — exact match; a raised backend limit stays edge-blocked (fail-closed). Held (Info, folded into RT-R-1 context).
- **XFF/XFP:** `X-Forwarded-For $proxy_add_x_forwarded_for` (append) with the backend trusting exactly the edge's fixed IP (line 127; subnet-scoped, operator-overridable); `X-Forwarded-Proto` pinned to the operator value, never client-derived (line 256). Consistent with sibling RT-M's proxy-trust model. Held.
- **Upstream re-resolution:** `resolver 127.0.0.11 valid=10s ipv6=off` + variable `proxy_pass` with explicit `$request_uri` restatement (lines 270, 288-294) — the documented redeploy-stale-IP remediation. Held.
- **`/edge-healthz`:** exact-match location above `location /`, `allow 127.0.0.1; deny all;` (lines 276-281). Held.
- **Hardening:** `no-new-privileges` on all five services; `cap_drop: ALL` everywhere; db re-adds exactly its five initdb caps, edge exactly CHOWN/SETGID/SETUID (justified in comments); edge `read_only` rootfs with two size-capped tmpfs. Log rotation anchor on every service. `stop_grace_period: 40s` on the draining backend. Held.
- **Ordering:** backend `depends_on` db-healthy **and** migrate `service_completed_successfully` (lines 91-95); migrate itself waits on db-healthy and has no restart policy (one-shot). Held.
- **ENVIRONMENT plumbing:** same interpolation default into migrate/backend/edge; edge https-refusal reads it (line 212). Held (residual: RT-R-4).
- **Findings:** RT-R-1 (no limit_req), RT-R-2 (flat network), RT-R-3 (2g/single-worker amplification), RT-R-4 (warning-only public bind).

### R4 — `ci.yml`
- Triggers push-main + `pull_request` (not `pull_request_target` — grep across all four workflows: zero hits). PR concurrency cancel; `permissions: contents: read` top-level. No `${{ github.event.* }}` interpolation inside any `run:` block in any workflow (grep verified) — the only untrusted-ish interpolations are `github.repository_owner`/`github.actor` into single-quoted literals in release.yml, both from tag-push contexts with safe character sets.
- Backend job: digest-pinned Postgres service; `uv lock --check` freshness; ruff/mypy --strict; **OpenAPI snapshot gate** (`python scripts/export_openapi.py && git diff --exit-code -- ../shared/openapi.json`, lines 85-89); alembic upgrade→check→downgrade base→upgrade→check round-trip (lines 91-103); pytest coverage floor single-sourced in `pyproject.toml` `fail_under = 75` (line 78); pip-audit pinned (`pip-audit==2.10.1`) over the exported frozen requirements. Held.
- Frontend job: orval regeneration freshness (`pnpm orval && git diff --exit-code -- src/api/generated`, lines 171-175); vitest coverage with thresholds 85/80/85/85 enforced via `test:coverage` (lines 177-185 + `vitest.config.ts:28-33`); `pnpm audit --audit-level=low` (line 203) — breaks on any low vuln, strict supply-chain posture. Held.
- e2e job: 3-browser matrix, `fail-fast: false`; rate-limit disable flag is the real setting (`auth_rate_limit_enabled`, default true) and is set **only** here and in the docker smoke — absent from compose, `.env.example`, and playwright.config (RT-R-5 covers the harness implication). Held.
- docker job: builds both images, runs migrations + boots the built backend, polls `/readyz`, asserts string `access_token` from a real register. No artifact upload here (trap prints logs) — acceptable.
- Residual Info: RT-R-11.

### R5 — `release.yml`
- Tag-triggered `v*` (maintainer-only ref); queue-only concurrency; job-scoped permissions (`packages: write` confined to the images job, `contents: write` confined to the release job, lines 31-35, 254-257).
- Gate-before-push: four per-arch builds loaded locally, each Trivy-gated `HIGH,CRITICAL ignore-unfixed exit-code 1` **before any push** (lines 78-167) — a vuln with no fix available does not block (documented tradeoff), a fixable one does.
- Four SPDX SBOMs generated from the exact gated per-arch images; publish builds attach `provenance: mode=max` + SBOM attestations; release notes pin the published digests idempotently (lines 293-303). Tag/actor handling is shell-quoted throughout. Held, except RT-R-6 (rebuild-vs-scanned-bits).

### R6 — `security.yml`
- CodeQL matrix python + javascript-typescript on push/PR/weekly; `upload: never` with the local SARIF gate — `.github/scripts/gate_sarif.py` exists and correctly fails (exit 1) on `level == error` or `security-severity >= 7.0` (lines 20, 74-87), with rule-index/rule-id fallback resolution and hard failure (exit 2) on unparseable SARIF. Held.
- gitleaks-action with `fetch-depth: 0` (full history); **no `.gitleaksignore` anywhere** (verified) and no config disabling rules. Held.
- Weekly cron; container job scans both built app images **and** the exact compose-pinned postgres/nginx digests (kept in lockstep via env vars, lines 84-88) — infrastructure supply chain covered. Held.

### R7 — `mutation.yml`
- Weekly cron + `workflow_dispatch` (write-access trigger only); never on PRs (documented and verified); `permissions: contents: read`; inner `timeout 20000`s (≈333 min) inside the 350-min job cap with `--kill-after=60`; non-0/non-124 exit surfaced as a warning annotation; results + `.meta` verdicts uploaded with 90-day retention. Held.

### R8 — `shared/openapi.json` (contract truth)
Machine-verified with Python (file never read whole):
- **76 paths / 89 operations**; `servers` absent (no absolute URL leak); single `HTTPBearer` security scheme; security declared on 83 ops — the 6 without are `healthz`, `readyz`, register/login/refresh/logout (intentionally unauthenticated). Correct.
- **Every operation has an `operationId`** (Orval requirement) — zero missing.
- **Source reconciliation is exact:** 88 router decorators across 15 routers; `POST /api/team/workers/{membership_id}/toggle` is `include_in_schema=False` (405 tombstone, B9) → 87 /api ops + `/healthz` + `/readyz` = 89. No drift, no orphan endpoints; freshness enforced by the CI gate.
- **Idempotency-Key contract:** `required: true` **only** on `POST /api/feeding/dispense` and `POST /api/finance/new` (`RequiredIdempotencyKey`, `api/feeding.py:144`, `api/finance.py:586`). The contract exposes it as **optional** (`= None`) on `feeding/mix`, `feeding/inventory/{id}/add`, `purchases/new`, `auth/farms`, `animals`, `kidding`, and others — **confirming sibling RT-M-1's scope**: those money/stock mutations are replay-protected only when the client volunteers a key. The contract is truthful to the code; the gap is a design decision flagged under Part M, not contract drift.
- 188 component schemas; **no $ref cycles** (DFS-verified); no unresolved refs; info block is title+version only. Held.

### R9 — e2e suite as adversarial harness
- **Provisioning safety:** global-setup registers a fresh random-suffix identity + farm via the real API (`global-setup.ts:25-53`); credentials land in `.e2e-state.json`, which is gitignored (`frontend/.gitignore: /e2e/.e2e-state.json`) and **not tracked** (git ls-files verified — only the 20 specs + helpers + global-setup are). No hardcoded pre-existing accounts. Held.
- **Determinism:** zero `waitForTimeout`/hard sleeps across all specs/helpers; waits are `expect`/`expect.poll`/`toPass` with explicit timeouts (`helpers.ts:67-78` even documents why `networkidle` was removed). `workers: 1`, `forbidOnly` in CI, retries 2 in CI. Held.
- **Coverage:** strong happy-path + lifecycle + RBAC-nav + contract-status breadth (including idempotency-key usage in api-contract specs, `api-contract-domain.spec.ts:675-677`). **Blind spots:** cross-tenant negatives absent and rate limiting disabled → RT-R-5 (Low). Readiness-probe nuance → RT-R-10.

## Cross-cutting checks
- **Tracked secrets:** `git ls-files` filter for `.env`, `keys/`, `*.pem`, `*.key`, e2e-state → only `.env.example` and `backend/.env.example` (safe). History scan over recent revs for key/AWS patterns: clean; gitleaks runs full-history in CI with no bypass. `backend/alembic.ini` contains no DSN (env-driven). `mutants/` and `audit_reports/` tracked but scanned clean of credentials.
- **README ↔ compose parity:** deployment section matches reality (loopback edge default, cap sets, 256m/0.5 edge, `!reset` override pattern for GHCR images, one-migration-job protocol, subnet-recreation caveat, `GOATFARM_ALLOWED_HOSTS` must retain the `backend` label). No drift found.
- **Digest liveness:** see RT-R-8 (three of four pins lag the live tags; nginx matches).

## Severity summary
Critical 0 · High 0 · Medium 4 (RT-R-1..RT-R-4) · Low 4 (RT-R-5..RT-R-8) · Info 3 (RT-R-9..RT-R-11)
