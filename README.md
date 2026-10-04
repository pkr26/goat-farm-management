# Herdly — Goat Farm Management

Herdly manages commercial Osmanabadi goat herds across multiple farms: animal records,
breeding and kidding, husbandry duties, feeding and inventory, health, purchases,
finance and production planning. Owners manage farm permissions; workers use the web app
or a Telugu-first shared-tablet workflow. Optional photo screening supports veterinary
review.

The monorepo contains an async FastAPI/PostgreSQL API, a Next.js/React frontend with
strict TypeScript, and a generated OpenAPI contract shared between them.

## Quick start

Use Python 3.13, uv 0.12.1, PostgreSQL 16, Node.js 24 (see
[frontend/.nvmrc](frontend/.nvmrc)), and pnpm 9.15.9. Run these commands from the
repository root in separate terminals. The local PostgreSQL role must be able to create
the development database.

Backend:

```sh
cd backend
uv sync --locked --extra dev
createdb goatfarm
GOATFARM_MIGRATION_DATABASE_URL=postgresql+asyncpg://localhost:5432/goatfarm \
  ./.venv/bin/alembic upgrade head
./.venv/bin/uvicorn app.main:app --reload --port 8000
```

Frontend:

```sh
cd frontend
corepack enable
pnpm install --frozen-lockfile
pnpm dev
```

Open [localhost:3000](http://localhost:3000), register, create a farm, and add animals.
The frontend development server proxies `/api/*` and `/readyz` to the API on port 8000.
`/healthz` on the frontend origin reports frontend liveness. Reference data is seeded at
startup; each farm receives role presets and feed inventory when it is created.

For custom local settings, copy [backend/.env.example](backend/.env.example) to
`backend/.env`. Online migrations always require an explicit
`GOATFARM_MIGRATION_DATABASE_URL`. The [Development guide](docs/development.md) also
covers the local Docker Compose stack and isolated test databases.

## Repository layout

| Path | Purpose |
| --- | --- |
| [backend/](backend/) | FastAPI routes, domain services, PostgreSQL models, migrations and tests |
| [frontend/](frontend/) | Next.js routes, shared UI, generated API client and browser tests |
| [shared/](shared/) | Exported OpenAPI contract and compatibility waivers |
| [docker/](docker/) | Edge proxy configuration and container entrypoint |
| [ops/](ops/README.md) | Backup timers, monitoring inputs and live screening contract |
| [docs/](docs/README.md) | Architecture, development guides and operational runbooks |

## Development checks

From the repository root, `make install`, `make check`, `make test`, and `make build`
run the common workflows. `make help` lists the available targets.

Run backend checks from `backend/` after installing the development dependencies:

```sh
./.venv/bin/ruff format --check .
./.venv/bin/ruff check .
./.venv/bin/python -m mypy --strict app scripts mutation ../.github/scripts
./.venv/bin/python -m mypy --strict tests
./.venv/bin/python -m pytest
```

Backend tests recreate a disposable PostgreSQL database; see the [Testing
instructions](docs/development.md#testing) before running them. Run frontend checks from
`frontend/`:

```sh
pnpm lint
pnpm typecheck
pnpm test:coverage
pnpm build
```

API changes flow from backend schemas to `shared/openapi.json` to the generated frontend
client. Run `backend/scripts/export_openapi.py` with the backend Python environment,
then `pnpm orval` from `frontend/`. See [Development](docs/development.md) for browser
tests and the complete CI gates.

## Documentation

| Guide | Topics |
| --- | --- |
| [Architecture](docs/architecture.md) | Module boundaries, API contract and background processing |
| [Domain model](docs/domain.md) | Buckets, breeding cycle, duties and husbandry defaults |
| [Authentication](docs/authentication.md) | Sessions, farm tenancy, permissions and safe retries |
| [Configuration](docs/configuration.md) | Environment settings and production validation |
| [Deployment](docs/deployment.md) | Production topology, secret mounts and release rollout |
| [Database migrations](docs/migrations.md) | Upgrade rehearsal, locking and historical constraints |
| [Backup and recovery](docs/backup-recovery.md) | Verified recovery points, object inventories and restore drills |
| [Security operations](docs/security-operations.md) | TOTP recovery and key rotation |
| [Data retention](docs/data-retention.md) | Object deletion, retry monitoring and account attribution |
| [Field workflows](docs/field-workflows.md) | Shared tablets, offline drafts and notifications |
| [Photo screening](docs/screening.md) | Uploads, provider cascade and veterinary review |

Production uses [docker-compose.production.yml](docker-compose.production.yml) with an
external TLS-verified PostgreSQL endpoint and exactly one API process. Read the
deployment and migration runbooks before a rollout.

Contributions follow [CONTRIBUTING.md](CONTRIBUTING.md). Report vulnerabilities through
[SECURITY.md](SECURITY.md). The project is proprietary; see [LICENSE](LICENSE).
Historical plans and review records live in [docs/archive/](docs/archive/README.md).
