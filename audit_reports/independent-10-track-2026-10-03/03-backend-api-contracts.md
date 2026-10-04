# Track 3 — Backend and API contracts

Audit date: 2026-10-03 (America/Phoenix)  
Audited commit: `1ca78768ed227182b1b84663bcc97c5ea9bee41b`

## Verdict

The backend has unusually strong transactional and tenant-authorization foundations, and the checked-in OpenAPI/generated client are current. The main contract defect is that 23 enforced numeric ceilings are published with a non-standard schema keyword, so standards-based consumers do not learn the real limits. The compatibility guard also does not preserve any previous contract and therefore cannot do what its documentation claims after a deliberate regeneration.

Finding count: **0 Critical, 0 High, 2 Medium, 3 Low**.

## Findings

### BAPI-01 — Enforced numeric ceilings are emitted as non-standard `le` schema keywords

- **Severity:** Medium
- **Confidence:** High
- **Evidence status:** Reproduced locally
- **Impact:** OpenAPI validators, documentation tools, and generated clients see an unconstrained `number` and can submit values that the server rejects with 422. This affects money, animal weights, and feed quantities, including create/correction flows.
- **Preconditions:** A client relies on the published OpenAPI contract rather than duplicating backend-only constants.

The shared numeric aliases apply ceilings with nested `Field(le=...)` metadata (`backend/app/schemas/common.py:137-170`). In the resulting Pydantic schema, however, the field contains `"le"` rather than JSON Schema/OpenAPI's `"maximum"`. The committed contract contains **23** such occurrences; representative examples are animal birth weight and purchase price (`shared/openapi.json:22935-22969`), transaction amount and feed quantity (`shared/openapi.json:37840-37879`), and recorded weight (`shared/openapi.json:38499-38503`). By contrast, an ordinary bounded identifier in the same spec correctly emits `maximum` (`shared/openapi.json:37857-37867`).

Local reproduction showed that `WeightIn(weight_kg=1001)` and `TransactionIn(amount=1000000001, ...)` both fail at runtime with `less_than_equal`, while `model_json_schema()` returns `{'type': 'number', 'le': ...}`. The generated TypeScript consequently contains only `number` with no maximum annotation for these values (`frontend/src/api/generated/models/weightIn.ts:8-12`, `frontend/src/api/generated/models/transactionIn.ts:10-16`, `frontend/src/api/generated/models/animalCreateIn.ts:26-30`).

**Recommendation:** express these ceilings with constraint metadata that Pydantic serializes as standard `maximum`, regenerate both artifacts, and add a contract test that rejects non-standard `le`/`ge`/`lt`/`gt` keys while asserting representative `maximum` values.

### BAPI-02 — The advertised breaking-contract guard has no immutable comparison baseline

- **Severity:** Medium
- **Confidence:** High
- **Evidence status:** Source-proven; failure impact is inferred
- **Impact:** A removed operation/schema, narrowed enum, changed required field, type change, or response-shape break can pass CI after the developer regenerates the OpenAPI file and client. External or older clients then receive no compatibility warning.
- **Preconditions:** A breaking backend change is accompanied by the repository's instructed snapshot/client regeneration.

The test documentation says its shape check catches a re-export after a breaking change, including a dropped enum value (`backend/tests/test_contract_drift.py:1-14`). It actually constructs both operands from the current checkout: live `create_app().openapi()` and the current, regeneratable `shared/openapi.json` (`backend/tests/test_contract_drift.py:28-49`). It compares only operation IDs, path/method pairs, and schema **names** (`backend/tests/test_contract_drift.py:51-83`); it never compares enum members, properties, requiredness, request/response schemas, parameters, or status/header contracts. Once the snapshot is regenerated, removals disappear from both operands.

CI correctly catches stale artifacts by exporting the current schema and diffing it (`.github/workflows/ci.yml:170-174`), then regenerating and diffing the frontend client (`.github/workflows/ci.yml:269-273`), but neither check is a previous-release compatibility comparison. The focused suite still passes: `7 passed` for `backend/tests/test_contract_drift.py`, which demonstrates freshness but not backwards compatibility.

**Recommendation:** compare the candidate schema against a release-tagged/baselined contract using an OpenAPI breaking-change tool or a checked-in immutable compatibility fixture. At minimum gate removals/narrowing across parameters, required properties, enum values, response statuses, response bodies, and documented headers. Rename the current test if it remains only a freshness/shape smoke check.

### BAPI-03 — Bearer 401 responses omit the required authentication challenge

- **Severity:** Low
- **Confidence:** High
- **Evidence status:** Reproduced locally
- **Impact:** Standards-aware HTTP clients, gateways, and generic re-authentication middleware cannot determine the expected authentication scheme from a 401 response, even though OpenAPI declares HTTP Bearer.
- **Preconditions:** A protected endpoint receives a missing, invalid, expired, revoked, or otherwise rejected access token.

`_unauthenticated()` builds a bare 401 with no headers (`backend/app/deps.py:42-43`), and all principal token failure branches use it (`backend/app/deps.py:140-190`). The global handler would preserve challenge headers if supplied (`backend/app/main.py:835-854`), but none are supplied. Local ASGI requests to both `GET /api/auth/me` and `GET /api/animals` returned the stable `UNAUTHENTICATED` JSON envelope and `Cache-Control: no-store`, but `WWW-Authenticate` was absent. The 401 OpenAPI response also documents no header (`backend/app/schemas/common.py:267-303`). No backend/frontend test asserts the challenge.

**Recommendation:** attach `WWW-Authenticate: Bearer` to access-token 401s, document the response header, and test missing plus invalid/expired/revoked-token paths.

### BAPI-04 — Malformed finance month filters silently succeed with misleading results

- **Severity:** Low
- **Confidence:** High
- **Evidence status:** Source-proven; DB-backed runtime path not exercised in this audit
- **Impact:** A typo can look like a valid month with no transactions; an empty value is treated as no filter and can return the full ledger. Clients cannot distinguish bad input from valid empty data.
- **Preconditions:** An authenticated finance reader supplies a malformed or empty `month` query value (the shipped UI has its own guard, so this primarily affects direct/integration clients).

The route declares `month: str | None` without a format or pattern (`backend/app/api/finance.py:543-555`), and OpenAPI publishes an arbitrary nullable string (`shared/openapi.json:12118-12133`). A non-empty parse failure is deliberately converted to `WHERE false`, returning 200 with an empty page; `9999-12` is handled the same way (`backend/app/api/finance.py:565-581`). Because the branch is `if month`, `?month=` skips filtering altogether. The existing bound test pins only the `9999-12` 200-empty behavior (`backend/tests/test_input_bounds.py:180-184`) and has no malformed-input contract assertion.

**Recommendation:** introduce a reusable validated calendar-month type and return 422 for malformed/unrepresentable values. If the v1 behavior must remain, deprecate it explicitly before tightening the next API version.

### BAPI-05 — Router-wide error declarations advertise status codes routes cannot emit

- **Severity:** Low
- **Confidence:** High
- **Evidence status:** Source/artifact-proven; representative authenticated runtime path not exercised
- **Impact:** Generated response unions and API documentation overstate endpoint behavior, increasing client branching and making genuine status-contract regressions harder to notice.
- **Preconditions:** A consumer uses the OpenAPI response list to implement exhaustive handling or generate an SDK.

Every API router applies the same 12 error declarations, including 415 (`backend/app/schemas/common.py:258-303`; router wiring at `backend/app/main.py:1132-1149`). Yet the only application code that raises 415 is `_require_json_content_type()` (`backend/app/api/auth.py:431-443`), attached to just four unauthenticated auth endpoints (`backend/app/api/auth.py:831`, `916`, `1173`, `3102`). Nevertheless `GET /api/animals` advertises 415 and Orval emits it in that read operation's error union (`frontend/src/api/generated/endpoints.ts:3611-3639`). The common-response test explicitly preserves this blanket declaration (`backend/tests/test_contract_drift.py:106-129`).

**Recommendation:** keep truly global middleware errors global, but compose smaller response maps by route class (authenticated reads, JSON writes, throttled auth, provider-backed operations, and so on). Add status-contract tests for representative classes.

## Positive controls observed

- **Route/auth coverage:** the shared contract contains 125 operations. All 116 protected operations explicitly declare `HTTPBearer`; the nine without security are the two probes plus intentional register/login/session-entry routes. Routers are centrally included (`backend/app/main.py:1132-1149`), and unsafe tenant requests re-lock the relevant authorization state before mutation (`backend/app/deps.py:681-761`).
- **Transactional idempotency:** 26 POST operations publish `Idempotency-Key` (six required). The service validates and hashes keys, binds identity to actor/farm/operation/body, arbitrates concurrent claims in PostgreSQL, commits claim and domain mutation together, and revalidates replayed bodies (`backend/app/services/idempotency.py:1-7`, `43-103`, `209-297`, `300-427`). CI/tests also connect the six required routes to the frontend registry (`backend/tests/test_contract_drift.py:159-244`).
- **Bounds, pagination, and cache behavior:** paginated resource routes use bounded limits and the shared `MAX_PAGE_OFFSET`; regression coverage exercises the 10,000 offset ceiling and driver-safe ID handling (`backend/tests/test_input_bounds.py:32-103`). API responses receive `Cache-Control: no-store` and `Pragma: no-cache` centrally (`backend/app/main.py:857-872`). Validation errors omit rejected input and retain a stable machine code (`backend/app/main.py:813-832`).

## Verification and limits

Executed without changing application code:

- `backend/.venv/bin/python -m pytest -q tests/test_contract_drift.py` — **7 passed**.
- In-process Pydantic checks for `WeightIn` and `TransactionIn` — runtime ceilings rejected, emitted schemas contained non-standard `le`.
- In-process ASGI requests to `/api/auth/me` and `/api/animals` — stable 401 body/no-store confirmed; missing Bearer challenge reproduced.
- Static enumeration of `shared/openapi.json` — 125 operations, 116 secured, 23 non-standard numeric-bound keywords, and 26 idempotency-declared POSTs.

This track did not run the PostgreSQL-backed integration suite, exercise production proxies/providers, or perform a full security audit. The finance-month and over-declared-status findings are therefore source/artifact-proven rather than end-to-end reproduced. No application files were edited.
