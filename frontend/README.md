# Herdly frontend

Next.js App Router application for farm managers and the worker tablet.
The UI uses strict TypeScript, Tailwind v4, Base UI components, TanStack
Query, react-hook-form and Zod. Orval generates the API client from
[`shared/openapi.json`](../shared/openapi.json).

See [development](../docs/development.md) for the complete local stack,
[architecture](../docs/architecture.md) for system boundaries, and
[AGENTS.md](./AGENTS.md) for frontend conventions.

## Local development

Use Node 22 or newer and the pnpm version pinned in `package.json`.

```sh
corepack enable
pnpm install --frozen-lockfile
pnpm dev
```

The frontend runs at `http://localhost:3000`. Start the backend first;
`next.config.ts` proxies `/api/*` and `/readyz` to `BACKEND_URL` (default
`http://localhost:8000`) so refresh cookies remain first-party. The local
`/healthz` route reports frontend liveness and takes precedence over the
backend fallback rewrite. The backend URL must use loopback or a private
Compose service name.

## Commands

| Command | Purpose |
| --- | --- |
| `pnpm dev` | Start the development server |
| `pnpm build` | Build the production standalone server |
| `pnpm start` | Serve the production build |
| `pnpm lint` | Run ESLint with no warnings allowed |
| `pnpm typecheck` | Check TypeScript without emitting files |
| `pnpm test` | Run the Vitest component and unit suites |
| `pnpm test:coverage` | Check coverage floors and produce V8 reports |
| `pnpm test:watch` | Run Vitest interactively |
| `pnpm e2e` | Run Playwright against the real stack |
| `pnpm orval` | Regenerate API endpoints and models |
| `pnpm verify:dependencies` | Check installed dependency mitigations |
| `pnpm audit:verified` | Scan dependency advisories with verified local mitigations |
| `pnpm check:route-js-budget` | Check route JavaScript sizes after a build |

Playwright provisions a fresh user and farm. Its specs run serially because
they share that farm. Chromium is the default; set `E2E_BROWSER=firefox` or
`E2E_BROWSER=webkit` to use another browser. `E2E_UVICORN` overrides the
backend launch command. See [mutation testing](./mutation/README.md) for
current harness commands and artifact rules.

## Source layout

| Path | Responsibility |
| --- | --- |
| `src/app/(app)/` | Authenticated manager routes and sidebar shell |
| `src/app/worker/` | PIN sign-in, shared tablet shell and offline duty board |
| `src/components/` | Shared interface components and primitives |
| `src/lib/` | Authentication, request coordination, permissions, formatting and localization |
| `src/lib/worker-outbox.ts` | Durable IndexedDB writes, legacy imports and review receipts |
| `src/api/generated/` | Generated Orval endpoints and models |
| `src/test/` | Fixtures, MSW handlers and test render helpers |
| `e2e/` | Browser journeys and API integration checks |
| `scripts/` | Localization, dependency and build checks |

Edit API models in the backend, export the contract, then run `pnpm orval`.
Generated files are checked in for review and reproducibility; never edit
them manually. Unit and component tests live alongside the code and name
the behavior they protect.

Colors come from `src/app/globals.css`. Inter, Fraunces, JetBrains Mono and
Noto Sans Telugu are loaded through `next/font`; both language catalogs must
receive new interface strings. Worker writes remain scoped to their original
actor and farm across logout, handover and retries.
