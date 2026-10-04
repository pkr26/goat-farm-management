# Development

[Documentation index](README.md) · [Project overview](../README.md)

Commands start at the repository root unless a block explicitly changes directory. Use
the committed lockfiles to reproduce dependencies. Python 3.13 is pinned in
[backend/.python-version](../backend/.python-version), Node.js 24 in
[frontend/.nvmrc](../frontend/.nvmrc), and pnpm in the frontend `packageManager`. The
backend uv bootstrap is pinned in [backend/pins/uv.txt](../backend/pins/uv.txt).

## Repository commands

The root [Makefile](../Makefile) provides shortcuts without changing the package
commands or their lockfile behavior:

| Command | Purpose |
| --- | --- |
| `make install` | Install backend development tools and frontend dependencies |
| `make check` | Run formatting, lint and strict typing checks |
| `make lint` | Run backend formatting/lint and frontend ESLint |
| `make typecheck` | Run backend strict typing/test typing and frontend TypeScript |
| `make test` | Run both test suites; requires local PostgreSQL |
| `make test-backend` | Run pytest against its disposable database |
| `make test-frontend` | Run Vitest |
| `make build` | Build the frontend for production |

Run `make help` to list targets. Coverage and browser checks use the package commands
described below.

## Local API and frontend

Run PostgreSQL 16 locally with a role that can create a database, then install and start
the API:

```sh
cd backend
uv sync --locked --extra dev
createdb goatfarm
GOATFARM_MIGRATION_DATABASE_URL=postgresql+asyncpg://localhost:5432/goatfarm \
  ./.venv/bin/alembic upgrade head
./.venv/bin/uvicorn app.main:app --reload --port 8000
```

For a custom database or settings, copy `backend/.env.example` to `backend/.env` and
adjust it before migration. Keep the application's database URL and the explicit
migration URL pointed at the same development database. The backend loads `.env`
relative to its directory, regardless of the launch directory.

Start the frontend in another terminal:

```sh
cd frontend
corepack enable
pnpm install --frozen-lockfile
pnpm dev
```

Open [localhost:3000](http://localhost:3000). Register an account and create a farm;
presets and inventory are seeded by the application. The Next.js configuration defaults
`BACKEND_URL` to `http://localhost:8000` and proxies API traffic on the same browser
origin. The frontend `/healthz` handler reports frontend liveness, while `/readyz` is
the backend's database-aware probe.

## Local Docker Compose

The default Compose file includes PostgreSQL, the migration job, API, frontend and edge.
The edge is the only published service and binds to loopback.

```sh
cp .env.example .env
```

Choose a database password and set `POSTGRES_PASSWORD` plus both complete
`GOATFARM_DATABASE_URL` and `GOATFARM_MIGRATION_DATABASE_URL` values in `.env`.
Percent-encode reserved characters in URL credentials. The template deliberately
contains placeholders and does not run until these are supplied.

```sh
docker compose config --quiet
docker compose run --rm --no-deps config-guard
docker compose up --build -d
curl -fsS http://127.0.0.1:3000/healthz
curl -fsS http://127.0.0.1:3000/readyz
```

Keep `GOATFARM_ENVIRONMENT=development` for this stack: the bundled database has no
production TLS configuration. Production uses the separate external-database
[Deployment](deployment.md) topology.

## Formatting and static analysis

From `backend/`:

```sh
./.venv/bin/ruff format --check .
./.venv/bin/ruff check .
./.venv/bin/python -m mypy --strict app scripts mutation ../.github/scripts
./.venv/bin/python -m mypy --strict tests
```

Use `./.venv/bin/ruff format .` to apply formatting. The strict tests typing
check must remain at zero errors.

From `frontend/`:

```sh
pnpm lint
pnpm typecheck
pnpm build
```

Frontend UI and styling conventions are in [frontend/AGENTS.md](../frontend/AGENTS.md).

## Testing

### Backend

The integration suite connects to the local PostgreSQL maintenance database as the
operating-system user. That role must be able to create and drop databases. The session
fixture creates a disposable test database, applies migrations, truncates application
tables between tests, and drops the database on exit.

From `backend/`:

```sh
GOATFARM_TEST_DB=goatfarm_test ./.venv/bin/python -m pytest
GOATFARM_TEST_DB=goatfarm_test ./.venv/bin/python -m pytest \
  --cov=app --cov-branch --cov-report=term --cov-report=xml
```

Use a database name ending in `_test` or containing `_test_`; never use a real
application database. Concurrent runs need distinct disposable names. The fixture
explicitly sets the API and migration URLs to its own test target, and disables auth
throttling except in tests that exercise it. Configuration is in
[backend/tests/conftest.py](../backend/tests/conftest.py).

### Frontend

From `frontend/`:

```sh
pnpm test
pnpm test:coverage
```

Coverage thresholds are configured in [vitest.config.ts](../frontend/vitest.config.ts)
and evaluated by the coverage command. Plain `pnpm test` does not enforce those
thresholds.

### Browser tests

Playwright starts the frontend and API and provisions a fresh user and farm. Locally it
can reuse running servers. Use a disposable database, and stop servers configured for a
real development database before the run so they cannot be reused accidentally.

From `backend/`, prepare the test database:

```sh
createdb goatfarm_e2e_test
GOATFARM_MIGRATION_DATABASE_URL=postgresql+asyncpg://localhost:5432/goatfarm_e2e_test \
  ./.venv/bin/alembic upgrade head
```

Then, from `frontend/`:

```sh
export GOATFARM_DATABASE_URL=postgresql+asyncpg://localhost:5432/goatfarm_e2e_test
export GOATFARM_MIGRATION_DATABASE_URL=postgresql+asyncpg://localhost:5432/goatfarm_e2e_test
export GOATFARM_AUTH_RATE_LIMIT_ENABLED=false
export GOATFARM_WORKER_ROSTER_ENABLED=true
pnpm exec playwright install
pnpm e2e
```

Chromium is the default. Set `E2E_BROWSER=firefox` or `E2E_BROWSER=webkit` to reproduce
another CI browser job. The shared-tablet journeys also run at mobile and tablet sizes.
`pnpm exec playwright test --list` discovers tests without creating application state.
Remove the disposable database afterward with `dropdb goatfarm_e2e_test`.

### Migration checks

Use a fresh, explicitly named disposable database for a migration round trip. These
commands create and then remove application schema:

```sh
cd backend
createdb goatfarm_migration_test
export GOATFARM_DATABASE_URL=postgresql+asyncpg://localhost:5432/goatfarm_migration_test
export GOATFARM_MIGRATION_DATABASE_URL=postgresql+asyncpg://localhost:5432/goatfarm_migration_test
./.venv/bin/alembic upgrade head
./.venv/bin/alembic check
./.venv/bin/alembic downgrade base
./.venv/bin/alembic upgrade head
./.venv/bin/alembic check
dropdb goatfarm_migration_test
```

Applied migration files are immutable. Corrections require a new revision. See [Database
migrations](migrations.md) for production upgrade rehearsal and historical
online/offline boundaries.

## Updating the API contract

Regenerate both committed outputs after changing backend routes or schemas:

```sh
cd backend
./.venv/bin/python scripts/export_openapi.py
```

Then, from `frontend/`:

```sh
pnpm orval
```

Review the resulting contract and client diff. Do not hand-edit generated client files.
CI checks freshness and compatibility with the immutable base contract; explicit
compatibility exceptions are recorded in
[shared/openapi-compatibility-waivers.json](../shared/openapi-compatibility-waivers.json).

## Dependency changes

After intentionally changing backend dependencies, update `uv.lock` with `uv lock` or
`uv lock --upgrade` and verify `uv lock --check`. Frontend dependency changes must
update `pnpm-lock.yaml` through pnpm. Keep the bootstrap hash pins, container base-image
digests and documented dependency mitigations consistent.

The frontend dependency verification and production build enforce the sharp/libheif
decoder-safety guard. A Next.js or sharp update must satisfy
[image-deps-guard.ts](../frontend/src/lib/image-deps-guard.ts); changing a version pin
alone does not bypass the image-decoder safety requirement.

## CI and release validation

[CI](../.github/workflows/ci.yml) checks formatting, lint, strict typing, backend and
frontend coverage, changed-code coverage, generated contract/client freshness, OpenAPI
compatibility, migration immutability and round trips, Playwright browser results,
dependency audits, image builds and runtime smoke checks. Browser tests requiring a
retry fail the CI verdict.

The [security workflow](../.github/workflows/security.yml) performs CodeQL, secret
scanning, SBOM generation and image vulnerability checks. A `v*` tag runs the [release
workflow](../.github/workflows/release.yml), which requires successful CI and security
results for the tagged commit, scans each target architecture before publishing, signs
manifest digests and release checksums, and attaches provenance/SBOMs. Existing release
versions are not overwritten. See [Release
authenticity](../SECURITY.md#release-authenticity) and [Deployment](deployment.md)
before using a release artifact.
