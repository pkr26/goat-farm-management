# Coordinating validation command record

Source revision: `7245368fa91333fb39387df789fa4ef9a58dbea3`. These are the commands/settings used for this audit, not an instruction to reuse an existing database. The named audit databases and temporary signing keys are removed after validation. Environment versions are in [validation-environment.json](validation-environment.json).

## Backend complete suite

Run from `backend/`, with the existing venv:

```sh
GOATFARM_TEST_DB=goatfarm_test_a26_root_9b83417e \
GOATFARM_DATABASE_URL=postgresql+asyncpg://localhost:5432/goatfarm_test_a26_root_9b83417e \
GOATFARM_MIGRATION_DATABASE_URL=postgresql+asyncpg://localhost:5432/goatfarm_test_a26_root_9b83417e \
.venv/bin/python -m pytest --cov=app --cov-branch --cov-report=term \
  --cov-report=xml:../audit_reports/independent-26-track-2026-10-04/evidence/backend-coverage.xml \
  --junitxml=../audit_reports/independent-26-track-2026-10-04/evidence/backend-full.xml \
  > ../audit_reports/independent-26-track-2026-10-04/evidence/backend-full.log 2>&1
```

The existing fixture owns creation, migration, data isolation and deletion of this explicitly named database. The existing combined line/branch floor is 92%; it was not changed. No changed-code coverage claim is made because no application source was changed by this audit.

## Production build and browser baselines

The local production build used `pnpm build` in `frontend/` with `BACKEND_URL=http://localhost:8000`. Its public/static output was copied into the generated standalone directory for serving. The retained [audit Playwright configuration](../playwright.audit.config.mts) extends the existing project configuration, uses the original E2E spec directory/setup, disables retries, and disables reuse of unrelated servers.

The browser database was separately created and migrated to head with both application and migration URLs explicitly set to `goatfarm_test_a26_browser_9b83417e`. Both ports were checked free before starting. Playwright owned the local Next standalone server on port 3000 and Uvicorn on port 8000. All browser runs used these explicit audit settings:

```text
CI=true
NEXT_TELEMETRY_DISABLED=1
GOATFARM_ENVIRONMENT=development
GOATFARM_DATABASE_URL=postgresql+asyncpg://localhost:5432/goatfarm_test_a26_browser_9b83417e
GOATFARM_MIGRATION_DATABASE_URL=postgresql+asyncpg://localhost:5432/goatfarm_test_a26_browser_9b83417e
GOATFARM_AUTH_RATE_LIMIT_ENABLED=false
GOATFARM_WORKER_ROSTER_ENABLED=true
GOATFARM_NOTIFICATIONS_ENABLED=false
GOATFARM_SCREENING_ENABLED=false
GOATFARM_JWT_PRIVATE_KEY_PATH=/tmp/goatfarm-a26-browser-9b83417e/private.pem
GOATFARM_JWT_PUBLIC_KEY_PATH=/tmp/goatfarm-a26-browser-9b83417e/public.pem
```

Separate invocations from the repository root set `E2E_BROWSER` to `chromium`, `webkit`, and `firefox`, respectively:

```sh
pnpm --dir frontend exec playwright test \
  --config=../audit_reports/independent-26-track-2026-10-04/evidence/playwright.audit.config.mts
```

The full WebKit baseline retained its login navigation timeout. Its separate focused diagnostic additionally set `AUDIT_RUN_LABEL=webkit-focused` and appended:

```sh
e2e/a11y-gate.spec.ts --grep '/finance/insurance \(te\)' --max-failures=1
```

This preserved the failed full report instead of overwriting it. Firefox's app-independent launch control reproduced the failure, so the full Firefox run was interrupted after two completed launch timeouts; the third test was interrupted and 97 did not run. No Firefox product-compatibility pass is claimed.

The auth rate limiter is deliberately disabled for browser fixture throughput; these browser runs do not validate rate limiting. The backend security tests and source review cover rate-limit behavior separately. Provider integrations are disabled in browser baselines and are tested by isolated synthetic worker probes instead. Browser cleanup and restoration of the pre-existing ignored E2E state are recorded in [browser-cleanup.json](browser-cleanup.json).

## Other controls

Frontend coverage, lint, typechecking, bundle budgets and mutation-harness commands are recorded in [the frontend review](../../frontend.md). Exact migration-roundtrip commands, database guards, state observations and exit statuses are retained in [migration-roundtrip.json](../ops/migration-roundtrip.json). Scanner/image/policy identities are retained in [container-scan-manifest.json](../ops/container-scan-manifest.json). The complete audit's validation table is the authoritative summary of passed, failed, blocked and incomplete checks.
