# Architecture

[Documentation index](README.md) · [Project overview](../README.md)

Herdly has two application runtimes and an optional photo-processing worker. The API
owns domain state and authorization in PostgreSQL. The frontend renders owner and worker
workflows through a generated, same-origin API client. The screening worker claims
durable image-processing jobs and uses the configured S3-compatible store and vision
providers.

## Repository boundaries

| Path | Responsibility |
| --- | --- |
| [backend/app/main.py](../backend/app/main.py) | Application construction, lifecycle, routes and background maintenance |
| [backend/app/api/](../backend/app/api/) | HTTP validation, authorization and response construction |
| [backend/app/services/](../backend/app/services/) | Domain transitions, invariants, transactions and external adapters |
| [backend/app/models/](../backend/app/models/) | SQLAlchemy tables, relationships and domain enums |
| [backend/app/schemas/](../backend/app/schemas/) | Pydantic request and response contracts |
| [backend/app/core/config.py](../backend/app/core/config.py) | Validated settings and service-specific secret loading |
| [backend/app/deps.py](../backend/app/deps.py) | Authentication, farm resolution and permission dependencies |
| [backend/app/security.py](../backend/app/security.py) | Password hashing and JWT signing/verification |
| [backend/app/permissions.py](../backend/app/permissions.py) | Permission catalog and role presets |
| [backend/app/seed.py](../backend/app/seed.py) | Idempotent reference and per-farm seeding |
| [backend/app/worker/](../backend/app/worker/) | Screening queue polling and worker heartbeat |
| [backend/alembic/](../backend/alembic/) | Immutable schema migration history |
| [backend/scripts/](../backend/scripts/) | Contract export, migration preflight, recovery and operator tooling |
| [frontend/src/app/](../frontend/src/app/) | Next.js App Router routes and authenticated shells |
| [frontend/src/components/](../frontend/src/components/) | Domain UI and shared components |
| [frontend/src/lib/](../frontend/src/lib/) | Authentication, API transport, permissions and formatting |
| [frontend/src/api/generated/](../frontend/src/api/generated/) | Generated TanStack Query client |
| [shared/](../shared/) | Exported OpenAPI contract and compatibility exceptions |

## HTTP and authorization

During development, Next.js proxies `/api/*` and `/readyz` to the API. In the supported
container topology, nginx sends `/api/` directly to the backend and other browser
traffic to the frontend. This preserves the client forwarding chain used by per-IP
admission limits. The frontend `/healthz` route reports its own liveness; backend
`/readyz` checks PostgreSQL readiness.

Access tokens are held in browser memory, while refresh tokens use an HttpOnly cookie
and a server-side session family. Farm-scoped requests resolve `X-Farm-Id` against
ownership or active membership before checking permissions. The API enforces these
boundaries; the frontend mirrors the resulting grants for navigation and controls. See
[Authentication and tenancy](authentication.md).

## API contract

Backend routes and Pydantic schemas define the contract exported by
[export_openapi.py](../backend/scripts/export_openapi.py) to
[shared/openapi.json](../shared/openapi.json). Orval then generates the frontend client.
Generated files are committed so CI can check freshness; change the backend definition
and regenerate both artifacts instead of editing generated output. CI also checks
compatibility against the immutable base revision.

## State changes and background work

Domain services own animal transitions, breeding/kidding, health events, duty
verification, feeding, purchasing and finance. Attributed records retain the actor
responsible for the change. Supported creation operations use scoped idempotency records
so a client can retry a lost response without repeating ledger or inventory effects.

Reference data is seeded at API startup and per-farm presets are seeded when a farm is
created. Legacy repair, session cleanup, cadence materialization and retention operate
in finite post-readiness batches. Durable maintenance checkpoints advance only after a
completed page; repeat work is idempotent. Domain reads do not create recurring duties.

Screening claims use committed PostgreSQL row locks. Notifications use a transactional
outbox and short claim/settlement transactions around bounded provider I/O. Screening
deletion first records a durable exact-key intent, then verifies object removal before
finalizing relational deletion. See [Field workflows](field-workflows.md), [Photo
screening](screening.md) and [Data retention](data-retention.md) for the detailed
contracts.

## Security events and operational limits

Successful security state changes are committed to an append-only `security_events`
ledger in the same transaction as their domain change. A post-commit `goatfarm.audit`
projection emits the `security_event` log signal. Refresh-family revocation,
password/TOTP lifecycle changes, recovery-code use, account export/deletion and planner
downloads are examples.

Rejected requests use fixed-cardinality process counters and bounded summary logs,
rather than allocating an append-only database row per attempt. The signal allowlist
excludes routine token expiry and attacker-controlled labels. Ship durable security
events and request-failure summaries off-host; local Docker log rotation is bounded and
does not provide audit retention.

Auth rate limits and simulation admission budgets are process-local. Production runs one
API process and holds a dedicated PostgreSQL session advisory lease; a second API
process fails startup. Shared admission controls are a prerequisite for future scaling.
Use a direct PostgreSQL endpoint or session pooling, as transaction pooling cannot
preserve the lease connection's affinity.

## Time and tests

Domain instants use naive UTC datetimes; scheduler checkpoints use timezone-aware
PostgreSQL timestamps. Date-only rules use the farm's IANA timezone, defaulting to
`Asia/Kolkata`. Dashboards, duties and feeding plans change day at farm-local midnight.
See [Domain model](domain.md).

Backend integration tests run against real PostgreSQL. Frontend unit/component tests use
Vitest and MSW, and Playwright exercises the real API/frontend stack. Mutation harnesses
have separate campaign documentation under
[backend/mutation](../backend/mutation/README.md) and
[frontend/mutation](../frontend/mutation/README.md). See [Development](development.md)
for setup and checks.
