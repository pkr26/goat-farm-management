# Independent audit verification record

Audit date: 3 October 2026 (America/Phoenix)

Audited source commit: `1ca78768ed227182b1b84663bcc97c5ea9bee41b`

This file records the fresh cross-project checks run by the coordinating reviewer. Track-specific reproductions and limitations remain in the individual reports.

## Static, contract, and build checks

| Check | Result |
| --- | --- |
| Backend Ruff over `app`, `scripts`, `mutation`, and `tests` | Passed: no findings |
| Backend mypy over `app`, `scripts`, and `mutation` | Passed: no issues in 158 source files |
| Frontend TypeScript typecheck | Passed |
| Frontend ESLint | Passed |
| Frontend production build | Passed |
| Runtime-generated OpenAPI versus `shared/openapi.json` | Exact equality; 108 paths and 242 schemas |
| Frontend production dependency audit (`pnpm audit --prod`) | Zero reported vulnerabilities |

The backend virtual environment did not contain a `pip-audit` executable during this coordinating pass. Dependency and supply-chain controls are assessed separately in Track 10; absence of that executable is not treated as proof that Python dependencies are vulnerability-free.

## Automated test suites

| Suite | Fresh result |
| --- | --- |
| Backend full suite on a uniquely named disposable PostgreSQL database | **5,166 passed, 4 skipped, 0 failed** in 1,753.65 seconds |
| Frontend full Vitest suite | **5,281 passed, 0 failed** across 326 files in about 265 seconds |
| Chromium real-stack Playwright suite, one worker and no retry | **73 passed, 1 failed** in 3.4 minutes |

The one Playwright failure was `worker tablet › PIN login, offline completion, sync with attribution`. Before disconnecting, the test proved that the service worker controlled the page and that the current-shift snapshot existed. After disconnecting and navigating to `/worker/offline`, the page remained at `Loading…`; the cached duty did not become actionable within 15 seconds. Source and trace review tie this to the independently identified offline/auth-bootstrap delay in Track 5, finding 05-2. It is counted once as that product finding, while this run supplies real-stack reproduction evidence.

The run also repeatedly emitted React's duplicate-child-key warning for `BREEDING:MAINTENANCE_75_25`. Track 9 evaluates the warning and the fact that the browser gate does not fail on it.

## Test isolation and cleanup

- Backend full-suite and browser E2E verification used uniquely named disposable databases rather than the application's ordinary test database.
- The E2E database `goatfarm_e2e_independent10` was dropped after the run, and the local E2E servers on ports 3000 and 8000 were confirmed closed.
- The separate accidental migration of the existing local development database is not part of this isolated verification. It is disclosed in `98-audit-side-effect.md` and Track 4.
- No real AI, SMS, email, object-storage, payment, or veterinary provider was invoked.

## Coverage limits

This is strong automated evidence, not a production certification. The coordinating browser run covered Chromium only; Firefox, WebKit, physical low-end devices, manual screen readers, production-scale data, provider failure modes, disaster restoration, and a live multi-replica deployment were not exercised here. The individual tracks distinguish source-proven, locally reproduced, and inferred impacts.
