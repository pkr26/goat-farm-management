# Infrastructure & Supply Chain Audit (2026-10-01)

Auditor 09 of 10 — INFRASTRUCTURE, DEPLOYMENT & SUPPLY CHAIN angle. All in-scope files
were read in full; every finding below was re-verified against the exact lines and the
referenced counterpart (config.py field, compose service, workflow step) before grading.

## Executive summary

This is an unusually well-hardened deployment surface. Both application images are
digest-pinned, multi-stage, run as dedicated non-root UID 10001, strip their package
managers, and carry real healthchecks. Both compose manifests apply no-new-privileges,
cap_drop ALL (with documented, minimal cap_add), read-only root filesystems, size-capped
tmpfs, memory/CPU limits, log rotation, and a two-segment network topology that keeps the
database off the app segment. The edge nginx templates handle the classic traps correctly
(variable proxy_pass with Docker's resolver, $request_uri restated, prefix-match rate
zone without trailing-slash bypass, XFF rightmost-entry trust walk, body-size
correspondence with the app, security-header ownership rules). CI has top-level
`permissions: contents: read`, SHA-pinned actions, hash-locked bootstraps, fail-closed
gates (including a migration-immutability gate that now fails closed on force-pushes),
and a release pipeline that gates per-arch Trivy scans before publishing, verifies the
pushed config digest equals the scanned bits, and confines `contents: write` to a single
post-gate job. No secrets are tracked in git; both `.env.example` files ship empty or
clearly-labeled development-only values that production validators reject at boot.

No Critical or High findings. The residual issues are hardening/policy drift: most
production secrets are still injected via environment variables (visible via
`docker inspect`) even though the JWT keypair and DB CA already use the file-mount
pattern; ~12.6 MB of generated mutation-testing artifacts are committed and flow into
the frontend Docker build context; the third-party compose image digests are aging with
22 suppressed CVEs under a manual refresh policy; and a few documentation/toolchain
drift points. The overall verdict: deployable as designed, with the Medium finding worth
fixing when the deployment gains a real secrets mechanism.

Severity counts: **Critical 0, High 0, Medium 1, Low 4, Info 7** (positives listed separately).

## Findings

### [Medium] Most production secrets are injected via environment variables, not files

Location: `docker-compose.production.yml:94-165` (backend), `:199-235` (screening-worker), `:65-69` (migrate)
Evidence:
```yaml
      GOATFARM_DATABASE_URL: '${GOATFARM_DATABASE_URL:?set the external DDL-free API PostgreSQL URL}'
      GOATFARM_IDEMPOTENCY_REQUEST_HMAC_SECRET: '${GOATFARM_IDEMPOTENCY_REQUEST_HMAC_SECRET:?...}'
      GOATFARM_TOTP_ENCRYPTION_KEY: '${GOATFARM_TOTP_ENCRYPTION_KEY:?...}'
      GOATFARM_S3_ACCESS_KEY_ID: ${GOATFARM_S3_ACCESS_KEY_ID:-}
      GOATFARM_S3_SECRET_ACCESS_KEY: ${GOATFARM_S3_SECRET_ACCESS_KEY:-}
      GOATFARM_SCREENING_ANTHROPIC_API_KEY: ${GOATFARM_SCREENING_ANTHROPIC_API_KEY:-}
      GOATFARM_MSG91_AUTH_KEY: ${GOATFARM_MSG91_AUTH_KEY:-}
```
The same manifest already mounts the JWT PEM directory and the PostgreSQL CA as
read-only bind files (`/run/secrets/jwt`, `/run/secrets/goatfarm-postgres-ca.pem`).
Impact: every one of these values is readable from the host via
`docker inspect docker-compose-production_backend_1` (and from `/proc/<pid>/environ`
by anyone with host access), lands in `docker events`/audit plumbing, and outlives the
container in operator shell history of `--env-file` edits. The DB URL additionally
carries its password inline. This is the standard compose idiom and the app already
forbids the values from being logged, so it is a hardening gap and internal policy
inconsistency (two secret-delivery mechanisms in one file), not an exploitable flaw.
Fix: extend the existing `/run/secrets` bind-mount pattern — e.g. `GOATFARM_DB_CA_FILE`-style
path variables for the S3/provider/MSG91 keys and the HMAC/TOTP secrets (config.py
already supports file paths for JWT keys; add analogous `_FILE` indirection or a small
entrypoint reader), or run the stack under a secrets-capable orchestrator.
Confirmed (values are `environment:` entries in the manifest as committed).

### [Low] ~12.6 MB of generated mutation artifacts are committed and enter the frontend Docker build context

Location: `frontend/mutation/` (coverage-map.json 7.9M, manifest.json 2.1M,
results.jsonl 2.0M, survivors.json 568K, report.md 284K — all `git ls-files`-tracked);
`frontend/.dockerignore` (no `mutation` entry); `frontend/Dockerfile:17` (`COPY . .`)
Evidence: `git ls-files frontend/mutation` lists all of the above; the frontend
`.dockerignore` excludes node_modules/.next/coverage/test-results but not `mutation/`.
Impact: every clone carries 12.6 MB of regenerable campaign output; every frontend
image build ships the same bytes into the builder stage's context and layer (the
runtime image is unaffected because only `.next/standalone`, `.next/static` and
`public/` are copied out). The root `.dockerignore` deliberately excludes
`backend/mutation` (line 30) — the frontend counterpart missed the equivalent entry.
Fix: add `mutation` to `frontend/.dockerignore`; decide whether the campaign artifacts
belong in git at all (the backend side already ignores `backend/mutation/*.log` and
`verify_extremes.jsonl`; consider a release-asset or CI-artifact home for the big JSONs).
Confirmed.

### [Low] Third-party compose image digests age manually under 22 suppressed CVEs; Dependabot cannot refresh compose digests

Location: `.trivyignore.compose-images:23` (`# refreshed: 2026-09-14`) and lines 27-82;
`docker-compose.yml:54,381`; `docker-compose.production.yml:272`;
`.github/dependabot.yml:13-19` (docker ecosystem covers the two Dockerfiles only)
Evidence: 22 CVE IDs are suppressed across the pinned `postgres:16@sha256:f1c337…` and
`nginx:1.30-alpine@sha256:dc5069…` digests, each annotated "fix published upstream,
image not yet rebuilt". The marker is 17 days old against the 30-day CI-enforced bound
(`backend/scripts/check_trivy_ignore_freshness.py`, run by `security.yml` on every push,
PR and weekly). The security↔compose digest lockstep is test-enforced
(`backend/tests/test_security_workflow.py:99-113`) but only against
`docker-compose.yml` — the production file's nginx digest is currently identical but
not covered by that assertion.
Impact: fixable HIGH/CRITICAL CVEs in the deployed database/edge linger until an
operator manually re-pulls, re-inspects, re-scans and prunes; the policy bounds the
debt at 30 days but nothing automates the refresh, and Dependabot's docker ecosystem
does not update `image:` references in compose files.
Fix: extend the lockstep test to `docker-compose.production.yml`; consider a weekly
workflow that proposes digest bumps (pull new digest, scan, open a PR) so the refresh
isn't purely manual.
Confirmed (marker date and counts verified; test coverage verified).

### [Low] README calls the GitHub release "signed" but no release artifact or image is cryptographically signed

Location: `README.md:488` ("Read the two multi-architecture manifest digests from the
signed GitHub release"); `.github/workflows/release.yml` (no cosign/notation step —
grep for `cosign|notation sign|keyless` over `.github/` and `docker/` finds nothing)
Evidence: release.yml attaches `provenance: mode=max` + `sbom: true` build
attestations and pins the published digests into the release notes; that is provenance
and digest pinning, not signatures. GitHub Releases are not cryptographically signed
by default.
Impact: an operator following the runbook may believe there is a signature to verify;
in reality the integrity mechanism is the digest pinned into the release notes plus
compose's mandatory `@sha256:` interpolation. A GHCR/GitHub compromise has no
out-of-band check.
Fix: either soften the README wording ("release published by the gated workflow,
digests pinned in the notes") or add keyless cosign signing at publish time and
document `cosign verify`.
Confirmed.

### [Low] Node toolchain version is sourced from two places that can drift

Location: `.github/workflows/ci.yml:253,359` (`node-version: 24` hardcoded in the
`frontend` and `e2e` jobs); `.github/workflows/security.yml:50`
(`node-version-file: frontend/.nvmrc`); `frontend/.nvmrc` = `24`
Evidence: security.yml reads the version from the repo file; ci.yml duplicates the
literal. They agree today.
Impact: a future `.nvmrc` bump makes the weekly audit run on a different Node than the
gating CI jobs — the classic "works in audit, fails in CI" drift, with no test
asserting equality.
Fix: use `node-version-file: frontend/.nvmrc` in ci.yml's two jobs.
Confirmed.

### [Info] Edge entrypoint ships test-only path/validation overrides in the production container

Location: `docker/edge-entrypoint.sh:249-250,284-286`
(`EDGE_TEMPLATE_PATH`, `EDGE_RENDERED_CONFIG_PATH`, `GOATFARM_EDGE_VALIDATE_ONLY`)
These are documented as test hooks and default to the immutable Compose `configs`
targets. Setting them requires control of the edge container's environment, which
already implies host/compose control, so this is an operator foot-gun (an env residue
of `GOATFARM_EDGE_VALIDATE_ONLY=true` would make the edge exit 0 without starting
nginx) rather than an attack path. Consider gating them behind `environment=development`.
Confirmed.

### [Info] gitleaks configuration is narrowly allowlisted and fails closed on version skew

Location: `.gitleaks.toml:20-25`; `.github/workflows/security.yml:113`
The single allowlist entry (Idempotency-Key fixture literals) is scoped to a precise
regex, justified in a comment, and extends the default ruleset. The file requires
gitleaks >= 8.21 syntax; if `gitleaks-action@ff98106…` (v2.3.9, SHA-pinned) ever
resolves an older binary the config fails to parse and the job fails — closed, not
open. Note (conditional): gitleaks-action requires `GITLEAKS_LICENSE` on organization
repositories; this run apparently works, so the repo is on a personal plan.
Confirmed.

### [Info] gate_sarif.py implements a deliberate error/high threshold with documented pass-through classes

Location: `.github/scripts/gate_sarif.py:66-87`
Findings whose level is neither `error` (directly or via `problem.severity`) and that
carry no `security-severity >= 7.0` are passed by design; parse failures return exit 2
(fail closed); a missing SARIF file returns 2. `_location` reads only the first
location of each result (adequate for CodeQL output). The threshold matches the
workflow's stated "error or high-severity" policy. No bypass found.
Confirmed.

### [Info] CI Postgres services run with trust auth — CI-only, runner-local

Location: `.github/workflows/ci.yml:106-116,324-333,417-426`
`POSTGRES_HOST_AUTH_METHOD: trust` with the port mapped to the runner's localhost is
standard for ephemeral CI databases; no repository secrets transit these instances.
The images are the same digest-pinned `postgres:16@sha256:f1c337…` the compose files
deploy. No action needed.

### [Info] CI failure-masking patterns audited: all `|| true` uses are cleanup paths

Location: `.github/workflows/ci.yml:456-457,489-490` (smoke-test `docker logs`/`stop`
cleanup), `release.yml:396` (documented best-effort package-version deletion in the
failure-path cleanup step)
The one historical `|| true` that could mask a gate (migration immutability after
force-push) was removed and now fails closed (`ci.yml:59-70`). No `continue-on-error`
exists in any workflow.
Confirmed.

### [Info] Release trigger trust chain is documented and fails closed

Location: `.github/workflows/release.yml:11-13,49-66`
A `v*` tag push (requires write access; tag protection mentioned as an additional
control) still must pass main-reachability and a successful push-event CI run for the
exact SHA before anything is built; per-arch Trivy gates run before any push; the
published per-arch config digest is verified against the scanned local image
(`release.yml:307-341`) before the release tag is assembled. `contents: write` exists
only in the post-gate `release` job. No `pull_request_target` anywhere; PR-triggered
workflows run with `contents: read` only and hold no secrets beyond the per-run token.
Confirmed.

### [Info] Workflow-injection surface reviewed: no attacker-controllable interpolation into `run:`

Location: `.github/workflows/ci.yml:46-70` (`github.event_name`, `pull_request.base.sha`,
`event.before` — enum/hex values only); `release.yml:80,90,384`
(`github.repository_owner`, `github.actor` — GitHub-restricted charsets, quoted);
`GITHUB_REF_NAME` (tag) reaches shell only inside double quotes (`release.yml:323-363,
434-448`) and into action inputs, never bare. Git refnames cannot contain control
characters, and every expansion site is quoted.
Confirmed.

### [Info] Mutation harness and utility scripts are CI-safe executables

Location: `frontend/mutation/*.mjs`, `frontend/scripts/*.mjs`
The harness is not invoked by any workflow or package.json script; `mutate_run.mjs`
maps vitest exit codes monotonically (0 → SURVIVED, non-zero → KILLED, timeout →
TIMEOUT) with no masking, spawns only `process.execPath` with paths from the committed
manifest, and verifies manifest SHA-256s before running. `scan-english-literals.mjs`
and `generate-worker-icons.mjs` read repo-local files only. The only caveat is the
committed artifact bulk (see the Low above).
Confirmed.

## Coverage manifest

| File | Status |
|---|---|
| `Dockerfile` | Read fully. Digest-pinned base, non-root UID 10001, hash-locked uv sync, pip/uv stripped, healthcheck, exec-form CMD, `--no-proxy-headers`. Clean. |
| `frontend/Dockerfile` | Read fully. Multi-stage, digest-pinned node:24-alpine, non-root, npm stripped, standalone output, /healthz probe with documented IPv4 rationale. Clean. |
| `.dockerignore` (root) | Read fully. Excludes .env*, audits, frontend, backend/mutation, script allowlist. Clean. |
| `frontend/.dockerignore` | Read fully. **Gap: `mutation/` not excluded** (Low finding). |
| `docker-compose.yml` | Read fully (493 lines). Hardening anchors, two-segment networks, config-guard preflight, `:?` required vars, loopback-only edge bind, no DB port. Clean apart from global env-secrets pattern (Medium, production-focused). |
| `docker-compose.production.yml` | Read fully (333 lines). Digest-pinned images via required env, file-mounted JWT/CA, verify-full DB, loopback edge, production-frozen knobs. Medium finding on env-borne secrets. |
| `docker/edge-proxy.dev.conf.template` | Read fully. Variable proxy_pass + Docker resolver, prefix-match auth zone, edge-generated-response header ownership, XFF append. Clean. |
| `docker/edge-proxy.production.conf.template` | Read fully. Rightmost-XFF zone keying (map), HSTS on edge-generated responses, same routing. Clean. |
| `docker/edge-entrypoint.sh` | Read fully (302 lines). `set -eu -f`, shell-only grammar validators for every sed-substituted value, CSP origin allowlist (HTTPS-or-loopback), production-HTTPS assertion, dev public-bind refusal. Info finding on test-only overrides. |
| `.github/workflows/ci.yml` | Read fully (505 lines). Permissions, SHA-pinned actions, fail-closed immutability gate, frozen-lockfile checks, hash-pinned uv/pip-audit bootstraps, docker smoke under production flags. Low finding on hardcoded node version. |
| `.github/workflows/security.yml` | Read fully (239 lines). Weekly pip/pnpm audit, CodeQL + local SARIF gate, gitleaks (history, fetch-depth 0), trivy-ignore freshness gate, SBOMs, digest-locked compose-image scans with scoped trivyignores. Clean. |
| `.github/workflows/release.yml` | Read fully (461 lines). Pre-publish per-arch Trivy gates, published-bits digest verification, attestations, confined write permissions, failure-path tag cleanup. Low finding (README "signed" wording; no cosign). |
| `.github/scripts/gate_sarif.py` | Read fully. Fail-closed parsing; deliberate error/high threshold. Clean (Info note). |
| `.github/dependabot.yml` | Read fully. pip, npm, docker ×2, github-actions — all relevant ecosystems covered; compose digests out of its reach (Low finding context). |
| `.env.example` (root) | Read fully. Empty POSTGRES_PASSWORD with `:?` compose enforcement, CHANGE_ME URL placeholders, dev-only HMAC value with documented production rejection, edge/network knobs documented. No secret material. |
| `backend/.env.example` | Read fully (335 lines). Every variable cross-checked against `config.py` fields and `NON_APP_ENV_VARS`; no orphans, no real values. |
| `backend/app/core/config.py` | Read fully (1583 lines). Production validator enforces: cookie_secure, `__Host-` cookie, non-loopback HTTPS CORS, non-loopback ALLOWED_HOSTS, min password 12, Argon2 floors, DB verify-full, >=32-char non-default HMAC secret, required TOTP key, single-worker env guard, /metrics force-off. Unknown-`GOATFARM_*`-var boot refusal in all three settings projections. |
| `.gitleaks.toml` | Read fully. Narrow, documented allowlist; fails closed on old gitleaks. |
| `.trivyignore.compose-images` | Read fully. Scoped to the two third-party images only, per-CVE rationale, 30-day freshness marker enforced in CI. Low finding on current debt. |
| `.gitignore` (root) | Read fully. `.env*` with `!.env.example`, keys/backups/dumps excluded. |
| `frontend/.gitignore` | Read fully. `.env*`, `*.pem`, e2e state excluded. |
| `backend/pyproject.toml` | Read fully. All runtime+dev deps exact `==`-pinned; no git/http/path sources; strict mypy; coverage ratchet fail_under=92. |
| `backend/uv.lock` | Policy-checked: 60 packages, 59 PyPI-registry, 1 editable self; 897 hash entries; no git/file/url sources. `pins/uv.txt` and `pins/pip-audit.txt` fully hash-locked (357 hashes). |
| `backend/alembic.ini` | Read fully. UTC, stderr logging; no credentials. |
| `backend/.python-version` | `3.13` — matches CI and Dockerfile base. |
| `frontend/package.json` | Read fully. Hash-pinned `packageManager`, no install lifecycle scripts (no postinstall/preinstall), pnpm security overrides for transitive chains, engines node>=22. |
| `frontend/pnpm-lock.yaml` | Policy-checked: lockfileVersion 9.0; all resolved sources npm-registry; overrides present; no git/tarball deps. |
| `frontend/.nvmrc` | `24`. Consistent today (see Low finding). |
| `frontend/next.config.ts` | Read fully. Standalone output, poweredByHeader off, security headers + prod HSTS, sw.js no-cache, deploy-time sharp gate. Clean. |
| `frontend/eslint.config.mjs` | Read fully. No security-relevant rulesets disabled; ignores limited to generated/diff-gated paths. |
| `frontend/orval.config.ts` | Read fully. Input is the committed `shared/openapi.json` (CI diff-gates drift); no remote schema fetch. Clean. |
| `frontend/vitest.config.ts` | Read fully. Coverage thresholds with per-glob floors. Clean. |
| `frontend/vitest.components.config.ts` / `vitest.libcore.config.ts` / `vitest.mutation.config.ts` | Read fully. Harness-only scopes, deliberately not mergeConfig; mutation config disables coverage. Clean. |
| `frontend/playwright.config.ts` | Read fully. `forbidOnly` in CI, workers 1, retries 2 in CI (standard), self-managed webServers, no untrusted input into commands. |
| `frontend/mutation/*.mjs` (7 files) | Read `mutate_run.mjs` fully; `mutate_gen/cover/report/triage/reverify/transform` skimmed for CI-executable hazards: local-only, no network, no failure masking, not wired into CI. |
| `frontend/scripts/scan-english-literals.mjs`, `generate-worker-icons.mjs` | Read fully. Repo-local inputs only; icon script pixel-verifies the maskable safe zone. |
| `backend/scripts/` (22 files) | `compose_env_guard.py`, `check_trivy_ignore_freshness.py`, `healthcheck.py` read fully; `backup.sh`, `restore.sh`, `totp_breakglass.py`, `rekey_totp_secrets.py`, `screening_worker_healthcheck.py` scanned for secret echo (`set -x`, print of secret values): none found — error paths print identifiers/fingerprints only; `export_openapi.py` read. Others are domain tools out of this audit's angle. |
| `README.md` (deployment claims) | Sections 330-500, 555-660, 800-870 cross-checked against compose/workflows: resource limits, edge topology, secrets, preflight commands, XFF guidance all match the files. One wording issue (Low: "signed" release). |

Coverage: 100% of the assigned scope read (two backend/scripts tools only skimmed for
the secret-handling angle noted above; their functional semantics belong to other auditors).

## Positive observations

1. **Both images are exemplary**: digest-pinned bases (python:3.13-slim, node:24-alpine),
   non-root UID/GID 10001, read-only-rootfs-compatible layouts, package managers stripped
   from the runtime layers to delete their vendored-CVE surface, HEALTHCHECK with sane
   start periods, and a documented single-worker CMD that keeps the in-memory rate
   limiter honest.
2. **Compose hardening is uniform and explained**: every service carries
   no-new-privileges, cap_drop ALL (+ minimal documented cap_add for the two official
   images that need them), mem/cpu limits, log rotation, and the DB sits on its own
   network segment that the edge and frontend cannot reach; the only published port is
   the loopback-bound edge.
3. **Fail-closed production gates at boot** (config.py): cookie/CORS/hosts/password/
   Argon2/DB-TLS/secret-quality checks refuse startup, unknown `GOATFARM_*` names are
   rejected in all three process projections, and the compose `config-guard` +
   `:?` interpolations extend that to the deployment file itself — the env-contract
   drift class (typo'd knob silently reverting to default) is systematically closed.
4. **CI supply chain**: top-level least-privilege permissions, all actions pinned to
   commit SHAs, uv and pip-audit bootstrapped from hash-locked pin files,
   `uv lock --check` + `pnpm install --frozen-lockfile` with diff-gates for generated
   artifacts, `--require-hashes` everywhere pip fetches, and every job has a
   `timeout-minutes`.
5. **The release pipeline is the strongest part**: scan-before-publish per
   architecture, published-bits-vs-scanned-bits digest verification, provenance+SBOM
   attestations, digest pinning into release notes, write permissions confined to one
   post-gate job, and cleanup of unverified scaffolding tags on failure.
6. **Suppressed-CVE debt is bounded and visible**: `.trivyignore.compose-images` is
   scoped to exactly two scan steps, itemizes per-CVE rationale, and a CI freshness
   gate caps its age at 30 days.
7. **Secrets hygiene in-tree**: no `.env`, key, or dump files tracked; dev-only HMAC
   fallback value is a named constant the production validator rejects; JWT dev keys
   generate outside the repo tree; gitleaks scans full history weekly with a narrow,
   documented allowlist.
8. **The nginx edge was clearly engineered against known proxy footguns**: Docker-DNS
   re-resolution for recreated upstreams, `$request_uri` restated after variable
   proxy_pass, no-trailing-slash rate-zone match, XFF rightmost-entry trust walk in
   production, and a documented header-ownership rule that avoids duplicated/conflicting
   security headers.
