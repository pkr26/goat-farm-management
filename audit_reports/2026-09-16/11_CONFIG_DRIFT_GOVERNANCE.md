# 11 — Config Drift & Governance (catalog 11.1–11.4)

## 11.1 Operator-gates / config-drift audit

Cross-read of `backend/app/core/config.py` `_production_safety` (lines 530–711)
↔ `docker-compose.yml` env wiring ↔ README production section ↔ `.env.example`.

**DEFENDED, fail-closed everywhere we tried to break it:**
- Every unsafe-in-prod default (loopback CORS/ALLOWED_HOSTS, `cookie_secure=false`,
  `sslmode=disable`, password floor 8, dev HMAC constant, wildcard proxies,
  `__Host-` cookie prefix, multi-worker) is **rejected at boot** under
  `GOATFARM_ENVIRONMENT=production`. Unknown `GOATFARM_*` env typos refuse boot.
  `/metrics` force-disabled in prod.
- Compose `:?` guards make the two required secrets non-defaultable.
- README production snippet agrees with the config gates on every value
  cross-checked.
- Edge guard refuses production+HTTP and non-loopback public binds.

**The one real drift exposure is INFRA-1 (Medium)**: adding an outer TLS
terminator without updating `GOATFARM_TRUSTED_PROXY_HOSTS` collapses all
clients into one rate-limit identity (deployment-wide register lockout). It is
documented in three places but enforced nowhere — a boot-time warning or a
"trusted-proxy count ≠ expected" health check would convert it to fail-closed.

Secondary drift items: INFRA-4 (body-size comment/code drift), INFRA-6
(migration least-privilege prose-only), INFRA-8 (`/readyz` public oracle).

## 11.2 Threat-model / attack-tree refresh

The single-replica invariant is load-bearing in three places: in-memory rate
ledgers, simulation semaphore/budgets (per-process), background-loop cadence.
The repo *enforces* the worker count at boot in production — but nothing
prevents an operator from horizontally scaling **containers** behind a
load balancer (each replica = fresh ledgers + full sim budget). The attack
tree should record: "scaling to N replicas multiplies auth rate limits and
simulation concurrency N×." Document a scaling path (Postgres-backed limiter)
before anyone needs it.

## 11.3 OpenAPI contract abuse review

`shared/openapi.json` fully documents every route — an attacker's map. Verified
this campaign: contract ↔ runtime parity held (every OpenAPI route was drivable;
no undocumented route discovered by the harness; all 422 responses match the
schemas). Schemathesis-style differential fuzzing remains a recommended
recurring CI addition (contract-driven fuzzing of *valid* payloads found no
issues in the manual subset we ran).

## 11.4 Data-privacy compliance (DPDP Act 2023 orientation)

- PII held: email, name, farm economics. Deletion: account tombstoning with
  email/name scrub (CHECK-constrained) — but farm-domain data (animals, tasks,
  finance rows created by the worker) is **retained by design** for farm
  integrity (task attribution preserved; verified in prior campaigns).
  `account/export` deliberately excludes farm-domain data (documented) — under
  DPDP's access/correction principles, a departing worker's *personal* data
  footprint in notes/free-text (their name in `administered_by`, `vet_name`,
  author ids on transactions) is **not exportable or erasable** today. Likely
  acceptable as legitimate-business retention, but should be a documented
  decision, not an accident.
- No data leaves the deployment (no analytics/telemetry — verified, report 05).
- Backups contain full PII; GPG-at-rest + fingerprint-pinned restore verified;
  retention 30 dumps. Document a DPDP-aligned retention schedule for backups.

| ID | Sev | Finding |
|---|---|---|
| GOV-1 | Info | Departing-worker personal footprint in retained farm data is neither exportable nor erasable — document as a deliberate DPDP position. |
| GOV-2 | Info | Horizontal container scaling silently multiplies per-process security budgets (rate limits, sim semaphore) — record in the threat model; consider boot warning when >1 backend replica is detected behind the edge. |
