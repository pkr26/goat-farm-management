<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->

# Styling conventions

- **Theme tokens only**: colors come from the CSS variables in `src/app/globals.css` (emerald brand on neutral surfaces, Tailwind v4 CSS-first — there is no `tailwind.config`). Never hardcode hex colors. For colored tints use Tailwind palette utilities with dark pairs, e.g. `bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300`.
- **Dark mode**: `next-themes` provider is mounted in `src/components/providers.tsx` (class-based, system default). Any tinted styling must include a `dark:` variant.
- **Shared components** (`src/components/`): use `PageHeader` (page title/description/actions), `StatCard` (metrics), `DataTableCard` (tables inside cards), `EmptyState` (empty lists), `StatusBadge` (status strings), `Logo` (brand), `ThemeToggle`. Don't reintroduce ad-hoc local versions of these.
- **App shell**: pages render inside the sidebar layout's centered `max-w-7xl` container on a `bg-muted/40` canvas — no page-level max-widths or page backgrounds.
- **Navigation**: sidebar groups live in `NAV_GROUPS` in `src/app/(app)/app-layout-client.tsx`; new routes need an entry there with their permission key.
- **Icons**: `lucide-react` only — no emoji in UI.
- **Numbers**: right-align numeric table columns and use `tabular-nums` for figures.
- **Semantic test hooks over utility classes**: when a component's styling
  encodes STATE (done/destructive/tone), also emit a semantic attribute
  (`data-done`, `data-tone`, …) and write tests against THAT — assertions on
  Tailwind utilities (`line-through`, `bg-warning-tint`, …) break on every
  restyle. Existing class-coupled assertions are being
  migrated opportunistically; do not add new ones.
- **Fonts**: Inter (sans), Fraunces (headings) + JetBrains Mono via `next/font` in `src/app/layout.tsx`; mapped to `--font-sans`/`--font-mono` tokens. Noto Sans Telugu is loaded alongside and swapped into the sans/heading stacks under `html:lang(te)` (Inter/Fraunces carry no Telugu glyphs).

## Worker tablet surface (`src/app/worker/`)

- Outside the `(app)` group on purpose: no sidebar, no `NAV_GROUPS` entries.
  Big targets (≥44px), Telugu-first default (`herdly.language` unset → `te`
  on first mount of the worker shell).
- Tablet pinning is a first-class flow on `/worker/login` ("Set up this
  tablet"): a manager signs in (password + TOTP/recovery code) and picks the
  farm, which writes `herdly.tabletFarm` (via `writeTabletFarmId` in the
  worker layout) and immediately signs the manager out. Do not reintroduce
  any other writer for that key.
- Session gates: the worker shell (`src/app/worker/layout.tsx`) redirects
  signed-out sessions to the `/worker/login` PIN pad — never the manager's
  `/login` form, since PIN-only workers hold no password. The signed-in-without-a-farm gate lives in `src/app/worker/page.tsx`,
  not the layout.
- Duty actions for the shared board render through
  `src/components/task-row-actions.tsx`: extend that
  component instead of forking a per-page copy. The worker board's DutyCard
  keeps its own offline-aware completion wrapper by design.
- Duty mutations go through the offline-aware wrapper in
  `src/app/worker/page.tsx`: persist in `src/lib/worker-outbox.ts` before the
  first request; one `Idempotency-Key` per logical action and all its retries.
  IndexedDB transactions preserve actor/farm scoped writes across tabs and
  handover. 409/other definitive 4xx become retained review receipts; expired
  pending writes require review. `End shift` preserves pending work and clears
  identity/query state. Never silently delete, evict or acknowledge an
  uncommitted device write. `offline-queue.ts` holds the compatibility storage key and action allowlist;
  `worker-outbox.ts` validates and imports legacy data.
- Test hooks are `data-testid` (`pin-key-*`, `complete-{id}`, `skip-{id}`,
  `worker-queue-depth`, `end-shift`) — the surface is Telugu-first, so
  aria-labels localize and testids stay stable.
- New worker strings land in BOTH `en.ts` and `te.ts` (the MessageKey union
  enforces parity).
