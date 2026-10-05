# Independent security, privacy, and API audit

Source: `7245368fa91333fb39387df789fa4ef9a58dbea3`. Date: 4 October 2026, America/Phoenix.

This review derived findings from current implementation and fresh probes. Earlier audit conclusions were not used as findings. No application source was changed.

## Coverage

| Track | Review and verification | Outcome / boundary |
| --- | --- | --- |
| 1 Architecture (backend portion) | API/service/schema/model separation; central authentication and permissions dependencies; background and worker configuration boundaries | Configuration coupling is covered by O26-01 in the operations report. This is not a full refactoring proposal. |
| 7 Authentication and sessions | Password/PIN/TOTP flows, refresh-family lifecycle, exact-session cancellation, credential-change revalidation, worker provisioning provenance, rate limits, key handling | S26-02 is a traceability gap. No new authentication bypass was reproduced. Full regression results are in the consolidated report. |
| 8 Permissions and farm isolation | Recursively inventoried all included routers and dependency trees; reviewed farm/membership/user/role locking; ownership transfer; owner-only credential reset | 126 runtime route records, including hidden metrics; only `/api/auth/permissions` has farm context without a `require_perm` closure, intentionally returning the caller's permissions. No cross-farm data disclosure was reproduced. |
| 9 Application security | Strict bearer/cookie handling, CSRF origin checks, body and target limits, input boundaries, CSP and security headers; source search for dangerous execution/HTML sinks | S26-01 reproduced. This is not an external penetration test of a deployed environment. |
| 10 Privacy and lifecycle | Account exports/tombstones, delayed membership/PIN/TOTP/notification-phone cleanup, immutable security-event records and projection | S26-02 reproduced. No legal-compliance certification or deployment-specific retention decision is claimed. |
| 14 API contracts | Compared freshly generated in-memory OpenAPI with committed contract; recursively inventoried routes and dependencies | Exact contract equality: 108 documented paths, 125 documented operations; hidden `/metrics` makes 126 runtime route records. No generated artifact was rewritten. |

## S26-01 — Oversized numeric farm header produces an internal server error

- **Severity:** Low (P3).
- **Tracks:** 9, 14.
- **Verification:** Reproduced with the real ASGI application, a valid registered bearer session, and a freshly migrated disposable PostgreSQL database.
- **Confidence:** High.
- **Source:** `backend/app/deps.py:716–720`, particularly the unguarded `int(x_farm_id)` at line 718.
- **Prerequisite:** An authenticated caller can deliver a 5,000-digit ASCII `X-Farm-Id` header to the backend. Any deployed edge's independent header limit still applies.
- **Trigger:** `GET /api/animals` with a valid bearer and `X-Farm-Id` set to 5,000 copies of `9`.
- **Expected:** A bounded 4xx validation response, with no attempted lookup of an impossible farm ID.
- **Actual:** The regex accepts the header; Python's integer-string conversion limit raises `ValueError` before the explicit range guard. The application returns HTTP 500.
- **Impact:** An invalid tenant selector becomes a server error and exception-log event. No unauthorized access, persistent data corruption, or service-wide outage was established; this is why the finding is Low.
- **Correction:** Bound header length before integer conversion, preserving the intended handling of ordinary out-of-range IDs; handle conversion failure as a client error. Add an HTTP-level regression asserting a 4xx for oversized numeric input.
- **Evidence:** `evidence/root/test_security_probes.py::test_oversized_numeric_farm_header_returns_500`; `evidence/root/security-probes.log`; `evidence/root/security-probes.xml`.

## S26-02 — Successful logout revocations have no durable attributed security event

- **Severity:** Low (P3).
- **Tracks:** 7, 10, 24.
- **Verification:** Reproduced independently for both logout routes with real sessions and PostgreSQL.
- **Confidence:** High for the omission; its operational priority depends on the required audit trail.
- **Source:** `backend/app/api/auth.py:1610–1637` and `backend/app/api/auth.py:1735–1854`; the helper `backend/app/deps.py:357` mutates session rows without emitting an event. `docs/architecture.md` describes durable successful security-state transitions.
- **Prerequisite:** A live user completes `POST /api/auth/logout-session` or `POST /api/auth/logout`.
- **Trigger:** Register a disposable user, count `SecurityEvent` rows, log out, attempt `/api/auth/me` with the previous bearer, and count the ledger again.
- **Expected:** A successful revocation has an attributed, transactional event, while duplicate/no-op or invalid logout attempts do not create attacker-amplifiable event rows.
- **Actual:** Both endpoints return 204 and the previous bearer receives 401, but the security-event row count increases by zero.
- **Impact:** Revocation itself works. Request logs show method/path/status, but do not supply the durable actor/family attribution used for other security lifecycle events; operators cannot reconstruct a logout from the immutable event ledger.
- **Correction:** Emit one event in the same transaction only when a real revocation/version change occurs, with bounded identifiers and no token material. Verify repeated no-op logout does not add events.
- **Evidence:** Both parameterizations of `evidence/root/test_security_probes.py::test_logout_does_not_record_durable_event`; `evidence/root/security-probes.log` and XML receipt.

## Commands and evidence

The final reproduction run used an explicitly owned `goatfarm_test_a26_rootprobe_7c82490a` database, with both application and migration URLs set to that database. From `backend/`:

```sh
GOATFARM_TEST_DB=goatfarm_test_a26_rootprobe_7c82490a \
GOATFARM_DATABASE_URL=postgresql+asyncpg://localhost:5432/goatfarm_test_a26_rootprobe_7c82490a \
GOATFARM_MIGRATION_DATABASE_URL=postgresql+asyncpg://localhost:5432/goatfarm_test_a26_rootprobe_7c82490a \
.venv/bin/python -m pytest -c pyproject.toml -p tests.conftest \
../audit_reports/independent-26-track-2026-10-04/evidence/root/test_security_probes.py -q -s
```

**Result: 3 passed.** These tests intentionally assert the observed defective behavior so they serve as reproduction receipts; they are not acceptance tests for a corrected product. The fixture removed its owned database. The initial invocation omitted the explicit pytest configuration, causing two asynchronous fixture setup errors; that harness mistake is preserved in `security-probes-harness-error.log` and is not an application finding. An earlier two-probe successful run is also retained; it is not added to the final unique count.

`evidence/root/api-contract-and-dependencies.json` contains the current contract equality result and the recursively flattened dependency inventory. An initial inventory looked only at top-level FastAPI routes; the final inventory corrects this for nested included routers and contains all 126 records. No two-route sample is presented as full route coverage.

The consolidated report contains the fresh complete-suite, browser, static, build, and dependency results and their limitations.
