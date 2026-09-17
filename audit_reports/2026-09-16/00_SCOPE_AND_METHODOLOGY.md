# Red-Team Audit Campaign 2026-09-16 — Scope & Methodology

Full-execution campaign covering the entire audit catalog proposed on 2026-09-16
(auth/identity, tenancy/RBAC, injection/data layer, business logic, frontend,
runtime infrastructure, supply chain, human layer, detection/purple-team,
config drift/governance). Every audit from the proposed catalog was executed
either as a **live attack against a running stack** or as a **deep static
falsification audit**; human-layer items were executed as tabletop/simulation
assessments (no real targets were contacted).

## Campaign structure

| Report | Catalog coverage | Method |
|---|---|---|
| 01_AUTH_IDENTITY | 1.1–1.7 | static falsification + live (phases 4, 8) |
| 02_TENANCY_RBAC | 2.1–2.5 | static falsification + live (phase 2) |
| 03_INJECTION_DATALAYER | 3.1–3.5 | static falsification + live (phases 3, 5) |
| 04_BUSINESS_LOGIC_IDEMPOTENCY | 4.1–4.5 | static falsification + live (phase 5); 4.1/4.2 verified as previously remediated |
| 05_FRONTEND_CLIENT | 5.1–5.6 | static falsification (live browser chain N/A: no Node on audit host) |
| 06_RUNTIME_ATTACKS_LIVE | 2.1 live, 6.1 partial, 7.1–7.4 | live: 50-attack cross-tenant matrix, JWT/refresh/worker suites, 10 raw-socket smuggling primitives, DoS-lite probes, rate-limit spray |
| 07_BACKUP_RESTORE_DRILL | 10.4 | live drill: backup → tamper (×2 forms) → clean restore → non-empty refusal |
| 08_EDGE_INFRA_SUPPLYCHAIN | 6.1–6.7, 8.1–8.4 | static falsification (no Docker on audit host; nginx edge analyzed from compose config, backed by `test_deployment_artifacts.py` assertions) |
| 09_DETECTION_PURPLE_TEAM | 10.1–10.3 | evidence-driven: replayed-attack visibility mapping + IR tabletop |
| 10_HUMAN_LAYER_TABLETOP | 9.1–9.4 | tabletop assessment (no real phishing performed) |
| 11_CONFIG_DRIFT_GOVERNANCE | 11.1–11.4 | static cross-read: config.py gates ↔ compose ↔ README ↔ .env.example |
| 12_MASTER_SYNTHESIS | — | severity roll-up of all findings |
| 13_REMEDIATION_LOG | — | prioritized fix recommendations (not applied) |

## Live environment (throwaway, local-only)

- PostgreSQL 16.2 (bundled binaries, 127.0.0.1:5432, trust auth, `/tmp/gfaudit/pgdata`) — migrated to head `e0f4a8b2c6d5` via the repo's Alembic chain.
- Backend: `uvicorn app.main:app` port 8000, `GOATFARM_ENVIRONMENT=development`, single worker, backend `.venv` (Python 3.13).
- Dev-mode deltas vs production (relevant when reading results): cookie not
  `__Host-`-prefixed, CSP/HSTS not present on API (none by design), password
  floor 8, `/docs`+`/metrics` enabled, dev idempotency HMAC secret. All
  production-only boot gates were verified statically (report 11).
- Frontend/edge were NOT run (no Node/Docker on the audit host). Frontend
  conclusions are static + backed by the repo's Playwright e2e suites; edge
  conclusions are static config analysis cross-checked against
  `backend/tests/test_deployment_artifacts.py`.

## Evidence

- `evidence/phase_bootstrap.json` — seeded two-farm scenario (owners o1/o2, farms A=1/B=2, animals, breeding, task, purchase, insurance, scenario, worker)
- `evidence/phase2_crosstenant.json` — 50 cross-tenant attacks + own-farm foreign-ID set + 12 X-Farm-Id/Authorization header-abuse cases
- `evidence/phase3_dpr.json` — DPR download raw-bytes injection test
- `evidence/phase4_auth.json` — 7 JWT tamper variants, refresh rotation/theft, worker fencing, token_version revocation
- `evidence/phase5_bizlogic.json` — state machine, idempotency, money bounds, feeding, pagination, validation
- `evidence/phase6_smuggling.json` — 10 raw-socket desync primitives vs uvicorn/h11
- `evidence/phase7_dos.json` — body/target caps, simulation cost, concurrency
- `evidence/phase8_ratelimit.json` — spray, distributed stuffing, ledger-key normalization, timing
- `evidence/security_status_log_excerpt.txt`, `evidence/uvicorn_runtime.log` — detection evidence
- Harness scripts preserved at `/tmp/gfaudit/live/` (scratch, not committed)

## Severity scale

Critical (immediate compromise) / High (significant attacker capability) /
Medium (real but bounded) / Low (hardening) / Info (posture/robustness).
"DEFENDED" = attack attempted, control held; prior-campaign fixes were
specifically re-falsified.
