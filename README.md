# Goat Farm Management

Multi-farm web app for commercial **Osmanabadi** goat farming in Telangana,
India. Monorepo: async **FastAPI + PostgreSQL** JSON API (`backend/`), a
**Next.js + React + strict TypeScript** SPA (`frontend/`), and a shared
OpenAPI contract (`shared/openapi.json`).

```
backend/    FastAPI app (async SQLAlchemy 2.0 + asyncpg, Alembic, Argon2id, JWT)
frontend/   Next.js App Router SPA (Tailwind + shadcn/ui, TanStack Query, Orval)
shared/     openapi.json — the API contract (exported from the backend)
```

## Quick start

Prereqs: Python 3.13, PostgreSQL 14+ running locally, Node 24+ with pnpm 9
(`corepack enable`).

```bash
# 1. Backend
cd backend
python3.13 -m venv .venv
./.venv/bin/pip install -e '.[dev]'   # or: pip install -r requirements from pyproject
createdb goatfarm                      # once
./.venv/bin/alembic upgrade head
./.venv/bin/uvicorn app.main:app --reload --port 8000

# 2. Frontend (new terminal)
cd frontend
pnpm install --frozen-lockfile
pnpm dev                               # http://localhost:3000
```

The Next dev server proxies `/api/*` to `localhost:8000` (see
`frontend/next.config.ts`), so the refresh cookie stays first-party.
Register → create a farm → start adding animals. Reference data (bucket
definitions, TMR recipes, vaccine templates, role presets) is seeded
automatically at startup and farm creation.

Configuration is via `GOATFARM_*` env vars (`backend/app/core/config.py`):
`GOATFARM_DATABASE_URL`, JWT TTLs, Argon2 parameters, `GOATFARM_CORS_ORIGINS`,
`GOATFARM_COOKIE_SECURE` (set `true` behind HTTPS), `GOATFARM_AUTH_RATE_LIMIT_*`
(login/register throttling), `GOATFARM_MAX_FARMS_PER_USER`, and the
`GOATFARM_DB_POOL_*` / `GOATFARM_DB_STATEMENT_TIMEOUT_MS` pool guards. RS256 key
pairs are auto-generated into `backend/keys/` on first run (gitignored).

## Auth & tenancy model

- Login/register issue an RS256 **access JWT** (30 min, `Authorization:
  Bearer`, held in memory only by the SPA) plus a rotating **refresh JWT**
  (14 d, httpOnly `SameSite=Lax` cookie scoped to `/api/auth`).
- Farm context travels in the **`X-Farm-Id` header**, validated per request
  (owner or active membership). All domain endpoints require it.
- RBAC: owners hold every permission; workers get a role's permission bundle
  (presets: Animal Mover, Veterinarian, Cleaner, Cleaner Manager, Feeder —
  editable, plus custom roles). `GET /api/auth/permissions` returns the
  caller's effective set for the active farm; the nav and buttons mirror it.
- Legacy pbkdf2 password hashes (pre-migration users) verify transparently
  and are upgraded to Argon2id on first login.
- Login and register are rate-limited (failed attempts for login, keyed per
  client IP + email; all register attempts per client IP) and farm ownership
  is capped per user. Behind a reverse proxy, set
  `GOATFARM_TRUSTED_PROXY_HOSTS` so real client IPs key the limiter.
- Team consent guards: an account that already belongs to another farm's
  team (active or not) cannot be absorbed into yours, and worker password
  resets apply only to accounts whose sole farm affiliation is yours —
  passwords are global, so cross-farm resets are refused.

## Development

```bash
# Backend
cd backend
./.venv/bin/python -m pytest            # 2315 tests, real PostgreSQL (goatfarm_test)
./.venv/bin/ruff format --check . && ./.venv/bin/ruff check .
./.venv/bin/python -m mypy --strict app
./.venv/bin/python scripts/export_openapi.py   # regenerate shared/openapi.json

# Frontend
cd frontend
pnpm orval           # regenerate the typed client from shared/openapi.json
pnpm test            # 649 Vitest + MSW tests
pnpm exec playwright test   # 22 browser e2e tests across 11 specs (starts dev servers if needed)
pnpm build           # strict typecheck + production build
```

The API contract flows one way: backend routes/schemas →
`shared/openapi.json` → Orval-generated TanStack Query hooks
(`frontend/src/api/generated/`). After changing the backend, re-run the
export **and** `pnpm orval`.

## The bucket system

Every animal lives in exactly one **bucket** (pen/stage), and every move is
recorded with a timestamp and reason. Buckets mirror the production cycle:

`QUARANTINE → FOUNDATION → BREEDING → PREGNANCY_EARLY → PREGNANCY_LATE →
DELIVERY → RECOVERY → RESTING → BREEDING …`

Kids branch off at weaning (day 60) into `MALE_KIDS` (sold at 8–9 months,
24–28 kg) and `FEMALE_KIDS` (grown to breeding-ready). New purchases sit in
`QUARANTINE` for a 45-day protocol (deworm → PPR → ET+TT → Goat Pox → FMD →
footbath) before joining `FOUNDATION`. The app auto-generates dated tasks for
every transition: ultrasounds, pre-kidding vaccines, bucket moves, weaning,
and the whole quarantine schedule.

## Duties & verification

- Auto-generated tasks (ultrasound, vaccine, bucket move, weaning, …) are
  assigned to the matching preset role automatically; owners can also create
  manual duties assigned to a role or a specific worker, with optional
  recurrence (`repeats every N days` — completing one schedules the next).
- Workers see only duties assigned to their role or to them; completing a
  duty stamps `completed_by`/`completed_at` — who did what is recorded.
- **Cleaning verification loop**: CLEANING duties marked done wait in the
  *Awaiting verification* tab until a `tasks.verify` holder **verifies**
  them (→ VERIFIED, attributed) or **rejects** them with a note, which sends
  them back to the worker's list. Someone other than the completer must
  verify (the farm owner is exempt).
- Form-linked duties (ultrasound, kidding, vaccines) can only be completed
  through their record forms, not the task list's complete button.
- Key records (bucket moves, health events, feeding, weights, breedings,
  kiddings, transactions) carry `created_by_id` for attribution.

## Domain reference

- Gestation **150 days** (kidding window 145–155); ultrasound scan at
  breeding + 32 days; heat cycle 21 days. Kidding records are accepted only
  within 100–200 days of the breeding date.
- Breeding-ready doe: female, ≥10 months, ≥22 kg, not pregnant, in
  FOUNDATION / FEMALE_KIDS / RESTING. Two consecutive failed cycles →
  cull candidate.
- Weaning at day 60 (doe → RESTING; kids → MALE_KIDS / FEMALE_KIDS by sex).
- Feeding: TMR per bucket, 3 shifts split **40% (6:30 AM, sweep bunks) /
  20% (1:30 PM) / 40% (7:30 PM)**. RESTING switches MAINTENANCE_75_25 →
  FLUSH_70_30 at day 10; MALE_KIDS switch LACTATING_60_40 → FATTENING_50_50
  at day 91.
- Core vaccines: FMD (6-monthly, Sep/Mar), PPR (3-yearly), ET (annual,
  pre-monsoon), HS, Goat Pox, plus pre-kidding ET+TT 4–6 weeks before due
  date. Deworming every 6 months (June/January).
- Money in ₹ (Indian grouping), metric units.

## Backend layout

```
backend/
  app/
    main.py          App factory (lifespan seeds, CORS, routers)
    core/config.py   Pydantic settings (GOATFARM_* env vars)
    db.py            Async engine/session (autoflush=False), Base
    models.py        21 tables, domain enums, computed properties
    services.py      All domain flows + state guards (breeding, kidding,
                     quarantine, tasks, feeding, finance, dashboard)
    security.py      Argon2id hashing, legacy pbkdf2 verify/upgrade, RS256 JWT
    permissions.py   Permission catalog + role presets
    deps.py          JWT auth, X-Farm-Id resolution, require_perm
    seed.py          Reference data + per-farm presets (idempotent)
    schemas/         Pydantic v2 In/Out models per module
    api/             auth, animals, buckets, breeding, kidding, health, tasks,
                     feeding, finance, purchases, dashboard (incl. reports), team
  alembic/           Migrations (single head: initial schema)
  scripts/           export_openapi.py
  tests/             2315 tests (logic, RBAC, adversarial, concurrency) on real PostgreSQL
```

## Frontend layout

```
frontend/
  src/
    app/
      page.tsx            Redirect hub (login / farm-select / dashboard)
      login|register/     Auth pages (RHF + Zod)
      farm-select/        Farm picker + create
      (app)/              Authed shell (permission nav) + module pages:
                          dashboard, animals (+[id]), buckets, breeding,
                          kidding, health (+schedule/[animalId]), tasks,
                          feeding (+recipes, inventory), purchases, finance,
                          reports, team
    api/generated/        Orval output (do not edit) + custom-instance.ts
    lib/                  api-client (token store, refresh retry, ApiError),
                          auth-context, use-permissions, format
    components/ui/        Committed shadcn/ui primitives (base-ui)
  e2e/                    Playwright specs (auth, animals, breeding flow, task guards)
  src/**/*.test.*         Vitest + MSW unit/component tests
```

## Notes

- All datetimes are stored naive UTC and "today" is the UTC date everywhere
  (`backend/app/utils.py`). Date-only inputs accept one day of "future"
  headroom so clients east of UTC (India is UTC+5:30) can enter their local
  today during 00:00–05:30 local; genuinely future dates are still rejected.
- Passkeys are a **future amendment**: neither `webauthn` nor
  `@simplewebauthn/browser` is installed in this release.
- The refresh cookie is `Secure`-flaggable via `GOATFARM_COOKIE_SECURE=true`
  in production; CORS is credentialed and pinned to the frontend origin.
- The v1 Jinja app was removed after the rewrite; its behavioral contract
  lives on in `backend/tests/`.
