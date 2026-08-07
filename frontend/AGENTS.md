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
- **Navigation**: sidebar groups live in `NAV_GROUPS` in `src/app/(app)/layout.tsx`; new routes need an entry there with their permission key.
- **Icons**: `lucide-react` only — no emoji in UI.
- **Numbers**: right-align numeric table columns and use `tabular-nums` for figures.
- **Fonts**: Inter (sans) + JetBrains Mono via `next/font` in `src/app/layout.tsx`; mapped to `--font-sans`/`--font-mono` tokens.
