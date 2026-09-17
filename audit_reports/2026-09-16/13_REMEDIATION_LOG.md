# 13 — Remediation Log (recommendations; NOT applied in this campaign)

Findings only were produced, per campaign scope. Suggested owners are indicative.

## P1 — schedule immediately

| Finding | Recommended fix | Owner | Effort |
|---|---|---|---|
| HUM-1 (10) | Second factor for owner accounts (TOTP at minimum; owner = god-mode). Interim: login anomaly events + new-device notification once DET-2 lands. | backend | M |
| INJ-1 (03) | Sanitize DPR-bound strings: reject/reject-or-escape CR/LF and leading `=`/`+`/`-`/`@` in any value interpolated into the DPR; strip U+2028/2029. Alternative: render the title from a fixed prefix + sanitized plan name with newlines collapsed. Add a regression test (plan name with `\n## Means of finance`). | backend | S |
| INJ-2 (03) | Use `_log_safe_path`-equivalent sanitization on the `request.url.path` argument at `app/main.py:608`; fix the enshrined assertion in `tests/test_ops.py:2508`. | backend | S |
| DET-1/DET-2 (09) | Emit `goatfarm.audit security_event` for: refresh family revocation (family id + actor, no PII), JWT verification failure (reason code), permission-denied on mutating routes, DPR download. One logger, one schema. | backend | S–M |
| INFRA-1 (08) | Boot-time warning (or readyz sub-check) when zero trusted proxies are configured while `GOATFARM_ENVIRONMENT=production` and the edge is expected in front; document the terminator checklist inline in compose. | backend/infra | S |

## P2 — plan within a sprint

| Finding | Recommended fix |
|---|---|
| AUTH-1 | Clamp the pbkdf2 rejection work at the budget (constant time regardless of stored iterations) or raise the default budget; pin with a test above 50k iterations. |
| INJ-3 / INJ-4 / FE-4 | Extend `PostgresText` to reject DEL, C1 (specifically U+0085/U+009B), U+2028/2029, and bidi marks Cf in prose; extend `no_control_characters` for identifiers; add `dir="auto"` (or first-strong isolation) on user-controlled strings in the UI. Migration note: existing rows are grandfathered — sanitize on read for display. |
| INFRA-5 | Document/enforce S3 credential scoping (PutObject-only) in the backup runbook; require bucket versioning/Object-Lock in the DR docs. |
| INFRA-2 (RT-R-7) | Make `assertSafeBackendUrl` validate `https:` targets too (allowlist the compose service name + operator-configured origin); add an e2e poisoned-BACKEND_URL build test. |
| INFRA-3 | Add an Alembic revision floor (`>= f4e5f6a7b8c9`) to `restore.sh` with a clear operator message. |
| INFRA-4 | Either template `client_max_body_size` from `GOATFARM_MAX_REQUEST_BODY_BYTES` in the compose config or delete the "mirrors" comment; add an artifact test pinning the correspondence. |
| INFRA-6 | Change `.env.example` to two distinct identities with a comment on grants (API: no DDL; migration: DDL only). |
| BIZ-1 | Wrap cached-response re-validation failures in a 409 "idempotent response no longer representable — retry with a new key" instead of 500. |
| BIZ-2 | Per-farm try/except in the cadence page loop; log and continue to the next farm. |
| DET-3/4 | Periodic limiter summary log; expose counters via the internal metrics endpoint (already disabled in prod — pair with an allowlisted scrape policy decision). |
| FE-1..3 | Optional hardening: extend backend replay grace slightly past the fallback-mutex worst case; treat 403-with-html body as `unavailable`; don't revoke on login-path farm-fetch failure (retry once instead). |

## P3 — backlog / watch

- AUTH-2: application-level assert on login content-type (stop relying on FastAPI incidental behavior).
- AUTH-5: delete dead `decode_token`.
- FE-5/FE-6: orval mutator adding `encodeURIComponent`; drop unused chart color/style props.
- RT-1: nothing to do (informational; note in threat model for future outer caches).
- INFRA-7: `server_tokens off` in edge config.
- INFRA-8: decide whether `/readyz` should be edge-gated (tradeoff with LB probes).
- INFRA-9 (RT-R-6): push the exact scanned digests (build once, scan, promote).
- GOV-1: publish the DPDP retention position (worker footprint in farm data).
- GOV-2: document the replica-scaling multiplier in the threat model; consider a boot warning.
- TEN-3: add endpoint-level task list-vs-single parity test; pin X-Farm-Id leading-zero canonicalization in a test.
- CVE watch: track upstream rebuilds for `.trivyignore.compose-images` (esp. **CVE-2026-14456 openssl in postgres:16** — the only network-plausible accepted CVE); add pip-audit/pnpm-audit to the weekly security schedule (currently push/PR only).

## Recurring drills

- Quarterly: backup/restore drill on production-shaped infra (report 07 template).
- Post-rotation: JWT key-rotation rehearsal (generate new pair, add old public
  key to `GOATFARM_JWT_PREVIOUS_PUBLIC_KEY_PATHS`, verify old tokens die
  gracefully) — the mechanism exists but has never been drilled.
- Annual (authorized): staged-account phishing exercise per report 10 §9.1.
