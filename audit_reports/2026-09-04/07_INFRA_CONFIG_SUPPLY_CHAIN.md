# Red Team Audit #7 — "The Infra Wrecker" — Deployment, Config, Secrets, CI/CD & Supply Chain

**Date:** 2026-09-04 · **Method:** read-only adversarial audit. Scope: root + frontend Dockerfiles, docker-compose.yml, both .dockerignore/.gitignore, root + backend .env.example, .github/workflows/*, dependabot, gate_sarif.py, backend core config/main/ratelimit/_run_limits/db/deps/security (keypair path), health probes, alembic wiring, backup/restore scripts, pyproject/uv.lock, package.json/pnpm-lock, next.config.ts, secret-pattern grep across all 949 git-tracked files. **No Critical or High findings survived verification.** All verified residual issues are Low.

## FINDINGS

### 1. Production-safety gates are opt-in via `GOATFARM_ENVIRONMENT` — "development" ships none of them — LOW
`docker-compose.yml:71` (`GOATFARM_ENVIRONMENT: ${GOATFARM_ENVIRONMENT:-development}`), `docker-compose.yml:135-138` (edge refuses only `production`+`http`), `backend/app/core/config.py:473` (`if self.environment != "production": return self`)

Attack scenario: a self-hoster serving real users leaves the compose default `development`, sets `GOATFARM_EDGE_BIND_HOST=0.0.0.0` (the documented knob) — they get a publicly reachable plain-HTTP stack with `cookie_secure=false` (compose line 72), non-`__Host-` refresh cookie, `min_password_length=8`, docs/OpenAPI enabled, DB TLS disabled, and no fail-closed boot checks. Every gate keys on the operator-declared label, not actual exposure.

Evidence: the edge's only HTTP refusal condition is `$$GOATFARM_ENVIRONMENT = "production"`; all config validators short-circuit on `environment != "production"`.

**Fix:** warn loudly (or refuse) when the edge bind host is non-loopback while environment is `development`.

### 2. Repo-committed known HMAC secret is a compose fallback default — LOW
`docker-compose.yml:79`: `GOATFARM_IDEMPOTENCY_REQUEST_HMAC_SECRET: ${GOATFARM_IDEMPOTENCY_REQUEST_HMAC_SECRET:-development-only-idempotency-hmac-secret-change-me}`; default also in `backend/app/core/config.py:132`

Attack scenario: a deployment with production-like data but `GOATFARM_ENVIRONMENT=development` (see finding 1) silently signs idempotency request fingerprints with a publicly known constant, defeating the stated purpose (`config.py:129-131`: fingerprints "must not be usable as an offline password verifier by a database/backup reader").

Mitigation already present: production gate `config.py:480-487` rejects this exact value and <32-char secrets, so this only bites development-labeled real deployments.

**Fix:** drop the compose fallback default; let config's identical dev default apply (no behavior change) and require the var via `:?` when environment is production.

### 3. Frontend build context leaks gitignored local artifacts into the builder layer — LOW
`frontend/Dockerfile:13` (`COPY . .`) vs `frontend/.dockerignore:1-11` (excludes only `node_modules, .next, coverage, playwright-report, test-results, .env, .env.*, e2e, **/*.test.ts(x)`)

Verified: `frontend/reports/`, `frontend/.stryker-tmp*`, `frontend/tsconfig.tsbuildinfo` exist on disk and are gitignored but NOT dockerignored, so they ship into the builder layer and its cache. Final runtime image only copies `.next/standalone` + `.next/static` (`frontend/Dockerfile:30-31`), so this is builder-layer hygiene/bloat plus any future secret a developer drops in an untracked report — not a runtime exposure.

**Fix:** add `reports`, `.stryker-tmp*`, `*.tsbuildinfo`, `Dockerfile`, `*.config.*` to `frontend/.dockerignore`.

### 4. Dual credential channels for the local DB can drift (example values only — no real secrets) — LOW
`.env.example:8` (`POSTGRES_PASSWORD=` empty) vs `.env.example:44-45` (`GOATFARM_DATABASE_URL=postgresql+asyncpg://goatfarm:goatfarm@db:5432/goatfarm` — literal password `goatfarm` inline)

The inline example password is a second source of truth for the same secret. Failure mode is fail-closed (compose `:?` guard at `docker-compose.yml:20` refuses boot on empty POSTGRES_PASSWORD; a mismatched URL password simply fails auth), so not exploitable — confirmed: **no .env.example value is a real credential**; the only literals are placeholders `goatfarm:goatfarm@db` and the flagged dev HMAC constant.

**Fix:** use a `@see note`-style placeholder inside the URLs too, or a documented `goatfarm:${POSTGRES_PASSWORD}` convention reminder.

## VECTORS TRIED AND PROPERLY HARDENED

1. **Committed secrets:** full-history gitleaks runs in CI (`security.yml:67-78`) plus an independent grep across all tracked files for `ghp_/AKIA/xox/sk-/AIza/BEGIN PRIVATE KEY` — zero hits; only a fake PEM in a test fixture (`backend/tests/test_security_hardening.py:1654`). Postgres superuser password is NOT hardcoded and port 5432 is NOT published (compose `db` service has no `ports:`, only the internal network; `POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?...}` refuses boot).
2. **Container hygiene:** all four base images digest-pinned (`Dockerfile:13`, `docker-compose.yml:13,114`, `frontend/Dockerfile:2` — python:3.13-slim@sha256, node:24-alpine@sha256, postgres:16@sha256, nginx@sha256); non-root UID/GID 10001 in both images; `.env*`, `backend/keys`, `backups/`, tests excluded from backend context (`.dockerignore`); `uv sync --frozen --no-dev` ships no dev deps; JWT keys are a named volume, never a build arg; healthchecks are non-abusive probes.
3. **CI/CD supply chain:** every `uses:` pinned by full SHA (zero mutable `@v4` refs), workflow-level `permissions: contents: read`, no `pull_request_target`/`workflow_run`, no secrets echoed (CI smoke test checks only `.access_token | type` via jq, `ci.yml:312`), `uv lock --check` blocks manifest/lockfile drift, pnpm `--frozen-lockfile`, caches content-addressed and branch-scoped; dependabot covers pip, npm, docker (root + frontend), github-actions.
4. **Proxy/CORS/header spoofing:** `CORSMiddleware` with enumerated origins + credentials (production validator rejects `*`, loopback, non-HTTPS, and wildcard entries — `config.py:521-553`); `TrustedHostMiddleware` with canonicalized non-loopback hosts in prod; `ProxyHeadersMiddleware` installed only for an explicit validated proxy allowlist (`main.py:572-577`, validator `config.py:409-444` rejects `*`, `/0`, hostnames); uvicorn launched `--no-proxy-headers`; nginx edge pins `X-Forwarded-Proto` from operator config, never client input.
5. **DoS surface:** body limit enforced three times (nginx `client_max_body_size 1m`, `RequestBodyLimitMiddleware` on both Content-Length and streamed chunks — `main.py:72-138`, target-length cap 414); pagination bounded on every list endpoint sampled (`le=100/200` limits, `MAX_PAGE_OFFSET=1_000_000`); simulation family requires auth+permission and runs under full admission control (`_run_limits.py`: per-farm/per-user locks, 2 process-wide slots, priced 650k-unit/5-min CPU budget, `monte_carlo_runs le=2000`); both in-memory limiters have 50k-key cardinality ceilings with tiered LRU eviction and O(1) paths; rate-limiter keys on `request.client.host` after proxy-trust resolution, never a raw spoofable header; multi-worker budget multiplication guarded by hard-coded `--workers 1` plus a startup env warning (`main.py:328-336`); `/healthz`/`/readyz` leak nothing (status strings only, docs disabled in prod).

## VERDICT

Infra/config posture: **exceptionally strong — top-tier for a project of this kind.** Digest-pinned images, least-privilege CI, fail-closed production boot validation, defense-in-depth body/DoS limits, and clean secret hygiene throughout. The residual risk is not in the code but in the operator-optional nature of the production gates (findings 1-2): everything hinges on the operator honestly setting `GOATFARM_ENVIRONMENT=production`, and a development-labeled deployment exposed beyond loopback inherits none of the protections. Closing that gap (exposure-aware warnings) and the two minor hygiene items would leave nothing to report.
