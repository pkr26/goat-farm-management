# GoatFarm frontend

Next.js App Router SPA for the goat-farm management API. Strict TypeScript,
Tailwind v4 + shadcn/ui, TanStack Query, react-hook-form + zod, and an Orval
client generated from `../shared/openapi.json`.

See the [root README](../README.md) for the monorepo overview and how to run
the backend, and [`AGENTS.md`](./AGENTS.md) for the styling and component
conventions this package follows.

## Getting started

The repo pins **pnpm 9** (`packageManager` in `package.json`); use it rather
than npm/yarn so `pnpm-lock.yaml` stays authoritative and CI's
`pnpm install --frozen-lockfile` keeps passing.

```bash
corepack enable
pnpm install --frozen-lockfile
pnpm dev            # http://localhost:3000
```

`next.config.ts` rewrites `/api/*` to `BACKEND_URL` (default
`http://localhost:8000`), so start the backend first — the refresh cookie
stays first-party that way.

## Scripts

| Command           | What it does                                        |
| ----------------- | --------------------------------------------------- |
| `pnpm dev`        | Dev server                                           |
| `pnpm build`      | Production build                                     |
| `pnpm start`      | Serve the production build                           |
| `pnpm typecheck`  | `tsc --noEmit`                                       |
| `pnpm lint`       | ESLint                                               |
| `pnpm test`       | Vitest (jsdom + MSW), co-located `*.test.ts(x)`      |
| `pnpm test:coverage` | Vitest with V8 text, JSON, and HTML coverage       |
| `pnpm test:mutation` | Incremental Stryker mutation testing with Vitest   |
| `pnpm e2e`        | Playwright suite in `e2e/`                           |
| `pnpm orval`      | Regenerate `src/api/generated/` from the contract    |

## Layout

```
src/app/          App Router routes; (app)/ is the authenticated sidebar shell
src/components/   Shared UI (PageHeader, StatCard, DataTableCard, pickers, ui/)
src/lib/          api-client, auth-context, formatters, permission helpers
src/api/generated Orval output — generated, never edited by hand
src/test/         MSW server and the render helpers used by component tests
e2e/              Playwright specs
```

Fonts are Inter (sans) and JetBrains Mono, loaded with `next/font` in
`src/app/layout.tsx` and exposed as the `--font-sans` / `--font-mono` tokens
in `src/app/globals.css`.

`src/api/generated/` is produced by `pnpm orval` from `shared/openapi.json`,
which the backend exports. Change the API in the backend, re-export the
contract, then regenerate — never edit the generated client.
