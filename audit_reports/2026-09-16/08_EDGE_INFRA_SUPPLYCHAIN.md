# 08 — Edge / Infrastructure / Supply Chain (static)

Catalog items 6.1–6.7 (static halves) and 8.1–8.4. No Docker on the audit host;
conclusions derive from deep config/script falsification cross-checked against
`backend/tests/test_deployment_artifacts.py` (1,900+ lines of artifact-pinning
tests). Live halves are in reports 06/07.

## 1. HTTP request smuggling surface (6.1)

**DEFENDED — structurally.**
- Edge nginx: `proxy_request_buffering` on (nginx fully re-serializes client
  framing), **no upstream keepalive** (per-request connections → reuse desync
  impossible), no H2 listener, `client_max_body_size 1m` applies to the buffered
  body (chunked cannot bypass), limit_req zone `auth_flood` 5 r/s burst=20 on
  `/api/auth/` only, resolver-pinned variable `proxy_pass` (Docker DNS,
  no attacker-controlled upstream), `merge_slashes` path-differential fails
  closed (raw path 404s at Starlette).
- Next proxy: `/api/*` never transits Next in the shipped topology (edge routes
  directly); `/healthz` shadowed by a local route. Node 24 llhttp rejects
  ambiguous CL/TE.
- uvicorn/h11 pins are post-advisory: uvicorn 0.52.1, h11 0.16.0 (post
  CVE-2025-43859 fix), httptools 0.8.0, starlette 1.4.1. Live raw-socket
  results in report 06 §2.

## 2. Edge guards (6.2) — DEFENDED

Guard script TOCTOU-free (env rendered into the config blob hash); empty-vs-unset
fail closed; case-variants ("Production") refuse; non-loopback spellings
(`::`, `localhost.`) refuse. XFF: `$proxy_add_x_forwarded_for` + uvicorn
rightmost-untrusted walk — spoofed left-side values lose.

## 3. Backup/restore attack path (6.5) — DEFENDED (+2 residuals)

Sign-then-encrypt single invocation; full 40/64-hex fingerprint normalization
(substring pinning impossible); VALIDSIG-only with EXPSIG/REVKEYSIG/BADSIG
fatal; checksum-of-ciphertext before decrypt; pinned copies (O_NOFOLLOW);
empty-target re-assert under advisory lock shared with Alembic; umask 077;
libpq URL allowlist blocks `sslrootcert` trust-smuggling. Live drill: report 07.

## 4. Migration window (6.6) — DEFENDED, 2 residuals (INFRA-3/5 below)

## 5. Container runtime (6.7) — DEFENDED

Non-root uid 10001 both images; keys volume 0700 + boot-mode check; healthchecks
not network-spoofable (loopback + allowed_hosts[0]; frontend probe hits the
local shadow route, verified); PID1 exec-form with graceful shutdown; edge
read-only rootfs. Backend/frontend rootfs writable (headroom, Info).

## 6. Supply chain (8.1–8.4)

- **CI/CD (8.1): DEFENDED** — SHA-pinned actions, no `pull_request_target`/
  `workflow_run`, least-privilege GITHUB_TOKEN (write only on the release job),
  SARIF gate fail-closed (unparseable = exit 2).
- **Lockfiles (8.2): DEFENDED** — all pins current; pip-audit gates CI; pnpm
  overrides verified as **dev-only transitives** (fast-uri/js-yaml/nanoid/qs
  are build-tool deps, not in the runtime bundle); no un-overridden vulnerable
  transitive found (braces 3.0.3, micromatch 4.0.8, cross-spawn 7.0.6, semver
  6.3.1/7.8.5, undici 7.29/8.10 all patched).
- **Accepted CVEs (8.4): `.trivyignore.compose-images` has 42 entries.** Only
  one PLAUSIBLE class: **CVE-2026-14456 (openssl, postgres:16 image)** — linked
  into the postmaster and reachable on every production deployment (DB TLS
  verify-full), though the `goatfarm_data` network is internal-only. The
  remaining 35 (nginx libuuid/zlib-ng/busybox, PG pcre2/sqlite/perl, 22 Go
  stdlib in gosu) are unreachable in this configuration.
- **Registry integrity (8.3):** Trivy-gated push, SLSA provenance, SBOMs; RT-R-6
  (release rebuilds rather than pushing scanned bits) remains open by documented
  decision.

## Findings

| ID | Sev | Finding | Evidence |
|---|---|---|---|
| INFRA-1 | **Medium (availability)** | **XFF aggregate identity behind an outer TLS terminator.** Compose trusts only the edge IP. Add an outer terminator without adding it to `GOATFARM_TRUSTED_PROXY_HOSTS` and every client shares one identity → the per-IP register budget (10/300 s) becomes **deployment-wide**: one attacker 429s all signups, nginx zone likewise aggregate. Extensively documented in prose but unenforced — the strongest config-drift exposure in the stack. | `docker-compose.yml:126-132,267-271`; `app/main.py:741-746`; README:445-453 |
| INFRA-2 | Low | RT-R-7 still open, scope refined: `BACKEND_URL` baked at build; `assertSafeBackendUrl` **returns early for any `https:` URL**, so a rebuilt image with an attacker `https://` target passes validation. Exploitation additionally requires the documented misdeployment (routing `/api` via Next). | `frontend/Dockerfile:10-11`; `frontend/src/lib/backend-rewrites.ts:12-41` |
| INFRA-3 | Low | Pre-F4 backup restore: `restore.sh` asserts revision well-formedness but no `≥ f4e5f6a7b8c9` floor. Compose's migrate-before-backend gate re-applies the password-oracle purge; bare `docker run` deployments rely on runbook prose only. | `restore.sh:346-363` |
| INFRA-4 | Low | `client_max_body_size 1m` is a literal despite the "mirrors GOATFARM_MAX_REQUEST_BODY_BYTES" comment; the env knob appears nowhere in compose — raising the backend limit yields silent edge 413s (fail-closed direction, but comment/code drift). | `docker-compose.yml:290-292` |
| INFRA-5 | Low-Med (policy) | Offsite S3 credential scope unenforced: the backup job never deletes, but nothing scopes the ambient AWS credential to `PutObject` — a compromised backup host with `s3:DeleteObject` can destroy the offsite tier. | `backend/scripts/backup.sh:126-133,449-456` |
| INFRA-6 | Low | Migration least-privilege is prose-only: `.env.example` uses the same bootstrap identity for API and migration URLs, so the example deployment's API credential can run DDL. | `.env.example:51-54` |
| INFRA-7 | Info | nginx `server_tokens` not disabled (version disclosure on every response/error page). | compose edge config |
| INFRA-8 | Info | Public `/readyz` availability oracle (unauthenticated DB-up/down signal through the edge). Deliberate probe design. | `main.py:642-653`; edge `location /` |
| INFRA-9 | Low | RT-R-6: release job rebuilds (cache-mediated) rather than pushing the exact scanned bits — a base-image change between Trivy gate and publish can diverge scanned vs shipped. | `.github/workflows/release.yml` |
