# GoatFarm SaaS — UI/UX Audit Report

**Date:** 2026-08-31
**Goal measure:** "One of the best UI/UX experiences in the industry" — benchmarked against the tier the goal implies (Linear / Stripe / Vercel-grade product surfaces), not against "acceptable admin panel".
**Scope:** Entire `frontend/` SPA (Next.js 16 App Router · React 19 · Tailwind v4 · shadcn/base-ui · TanStack Query · Orval client), plus a **live browser walkthrough** of the real rendered app (login → farm-select → farm creation → empty first-run → populated dashboard → animals list → add-animal dialog → mobile 390px → light mode → simulation), verified against a running backend with seeded data.

**Method — three independent lenses, cross-verified:**
1. **Design-system code audit** — every page, shared component and UI primitive read for visual-consistency violations (typography, color, spacing, badges, tables, forms, dark-mode risks).
2. **UX-pattern code audit** — every page read for experience patterns (first-run, loading/error/empty states, forms, feedback, navigation, mobile, terminology, a11y).
3. **Live visual walkthrough** — the actual app rendered in Chromium at 1440×900 and 390×844, in dark and light themes, with expert design critique of each screen. Where the visual pass and the DOM disagreed (one case: the mobile sidebar), the DOM measurement wins and is documented below.

**Environment notes:** the local `goatfarm` Postgres DB was on a pre-buffalo schema and blocked startup; it was backed up to `goatfarm_dev_backup_20260831.sql` (repo root) and recreated with `alembic upgrade head`. An `audit@goatfarm.test` account and "Navipet Osmanabadi Farm" with 8 animals were created for the walkthrough. Both dev servers are running (`localhost:3000`, `localhost:8000`).

---

## 1. Executive summary

**Overall today: ≈ 5.5 / 10 against the "best in industry" bar.** The app is a *competent, disciplined shadcn application* — clean tokens, strong accessibility wiring, excellent form internals, genuinely best-in-class destructive-action patterns — wearing **stock clothes with no identity**. Nothing about the interface says "premium" yet, and several structural UX gaps (zero onboarding, text-only loading, a 4,300-line simulation wall, no charts in a *financial simulation* product) would be disqualifying at the tier you're aiming for.

The single most important insight from the audit: **this is not a polish problem, it's a systems problem.** The quality is uneven because the same concept is built 3–7 different ways in different files (badges, empty states, section titles, alert boxes, dialog footers, pagination). Fixing the system first — one status component, one empty-state component, one section-title scale, one chart library — will raise every screen simultaneously and make "beautiful" cheap to maintain. Then the identity layer (typography, color personality, illustration, motion) is what makes it *memorable*.

### Scorecard

| Dimension | Score | Verdict |
|---|---|---|
| Design tokens & theming | 7/10 | Solid oklch token system, both themes, but bypassed ~60× by raw Tailwind palette |
| Visual consistency | 5/10 | 7 status-chip mechanisms, 3 section-title scales, 4 empty-state treatments |
| Typography | 5/10 | Inter everywhere (heading = body), `tabular-nums` applied inconsistently |
| Color | 6/10 | Good semantic core; status colors never declared as tokens; ~5 duplicated tint palettes |
| **Data visualization** | **2/10** | **No chart library. A Monte-Carlo histogram built from flex divs. In a finance product.** |
| Layout & density | 6/10 | Consistent page shells; milk double-pads; simulation is an endless scroll |
| Motion & micro-interaction | 3/10 | Only library defaults; no transitions, no list animations, no celebration moments |
| Forms & inputs | 7.5/10 | Excellent a11y wiring, world-class RemotePicker; submit-only validation, footer inconsistencies |
| Feedback (loading/empty/error) | 5/10 | Errors genuinely good; loading is text-only everywhere; empty states rarely actionable |
| Navigation & IA | 6.5/10 | Good grouped sidebar, deep links, URL-backed filters *sometimes*; no breadcrumbs |
| Mobile | 5/10 | Shell adapts correctly; tables are scroll-only; 24–32px touch targets |
| Accessibility | 7/10 | Strong aria baseline; contrast gaps; one native `window.confirm`; color-only states |
| **Onboarding & first-run** | **1/10** | **None. Register → farm → wall of zeros. No guidance anywhere.** |
| Brand & personality | 3/10 | One nice custom asset (the goat mark); everything else is default shadcn |

---

## 2. What's already excellent (preserve — don't redesign away)

These are genuinely better than most commercial SaaS and should anchor the redesign:

- **Destructive-action design.** The two-step purchase-consequence review with an explicit irreversibility warning (`purchases/page.tsx:499-551`), and the finance correction flow that requires a typed reason *plus* an "I understand the original will be voided" checkbox (`finance/page.tsx:440-466`) are best-in-class patterns.
- **The RemotePicker family** (`components/remote-picker.tsx`) — searchable, server-side, debounced combobox with full keyboard roving focus and a live status line ("N options available. Checked X of Y…"). A11y exemplary.
- **Form accessibility wiring** — `aria-invalid`/`aria-describedby`/`role="alert"` applied with remarkable consistency across hundreds of fields; the health dialog even auto-opens its collapsed advanced section when a submit error lives inside it (`health/page.tsx:315-328, 575-579`).
- **Stale-data resilience** — the animal profile keeps showing last-good data with a banner and freezes unsafe writes during refresh failure (`animals/[id]/page.tsx:1565-1584`).
- **Deep-linkable state** — animals filters, task tabs, and auto-opening dialogs (`?new=1`, `?breeding_id=`) with context-aware back links.
- **Species-aware shell** — the sidebar/hide-milk logic and the farm-type radio cards at farm creation are the right bones; they just need to extend *into* the pages (see F-U3).

---

## 3. Findings

Severity scale: **P0** = blocks the "best in industry" claim, **P1** = major visible gap, **P2** = consistency/polish, **P3** = nit.
(All paths under `frontend/src/`.)

### A. Strategic / experience-level findings

**F-U1 · P0 — Zero onboarding; the first-run moment is a wall of zeros.**
Verified live: a brand-new owner lands on a dashboard with two `0` stat cards, a huge "Nothing due today" card and no guidance whatsoever. Grep confirms no checklist, tour, sample-data seeder or Help link exists anywhere in the shell (`app-layout-client.tsx` has no Help item). For low-digital-literacy farm users this is the make-or-break moment. The vision review of the empty dashboard: *"the hierarchy actually works against activation… shows state, not possibility."*
**Fix:** first-run checklist card on the dashboard (add first animal → record weight → first purchase → first breeding), dismissible with progress persisted; contextual CTAs in every empty state; optional demo-data seeder.

**F-U2 · P0 — No data visualization in a data product.**
`package.json` contains no chart library. The dashboard's only "chart" is progress bars in bucket tiles (`dashboard/page.tsx:546-550`); the Monte-Carlo NPV distribution is a histogram of flex divs (`simulation/page.tsx:4135-4150`). A dairy product with a lactation-curve simulator, 12-month P&L, milk-yield trends and risk percentiles renders *none of it* graphically.
**Fix:** adopt one chart stack (Recharts fits the shadcn ecosystem), define the 5-color chart palette already tokenized in `globals.css:79-83,114-118` (currently unused!), and ship: dashboard trend sparklines + herd composition donut, P&L bar/line, lactation curve, NPV histogram + percentile bands, sensitivity tornado.

**F-U3 · P0 — Species vocabulary breaks for buffalo users; the brand itself is goat-only.**
The sidebar says "Calving" and "Breeding / AI" for dairy farms, but inside every page it's goat language: `breeding/page.tsx:282` ("Doe *"), `:339` ("Buck *"), table heads "Doe/Buck/Kids/Expected kidding" (`:959-966`); the kidding dialog says "Record kidding", "Kid 1 tag" (`kidding/page.tsx:250-283, 361-363`); reports hardcode "Kiddings recorded", "Alive kids per kidding" (`reports/page.tsx:205-280`); purchases says "goats" (`purchases:395`). Product metadata and login marketing are goat-only (`layout.tsx:17-18` "Commercial Osmanabadi goat farm management"; `login/page.tsx:164` "Built for goat farmers").
**Fix:** extend `farm-vocabulary.ts` (it already exists and is good) across breeding/kidding/reports/purchases pages and the brand metadata.

**F-U4 · P1 — Loading is plain "Loading…" text on every page; page headers flash away.**
The `Skeleton` primitive exists and is dead code (used only inside sidebar internals). ~60 occurrences of text-only "Loading…" in 3 visual variants; the one `loading.tsx` is a single `<p>`. Because early returns happen before `<PageHeader>`, **the whole page including its title and primary action disappears on every visit** — on rural connections this reads as a broken app.
**Fix:** page-shell skeleton (header + stat row + card placeholders) in `loading.tsx`; keep headers mounted while data settles; one canonical inline loading treatment.

**F-U5 · P1 — Raw enum codes shown to users everywhere.**
Buckets render as `MALE_KIDS`, event types as `VACCINE`, transaction categories as `ANIMAL_PURCHASE`, kid statuses as `ALIVE`, ease as `NORMAL`, shifts as `MORNING` (`health:1248-1252`, `feeding:794-818`, `finance:985-989`, `kidding:457-461`, `feeding:604-641`). "Bucket" — the app's core concept — is explained on exactly one page (`buckets:158`).
**Fix:** one `labelize()` util + label maps per enum; humanize every control and cell; add a first-use tooltip/legend for buckets.

**F-U6 · P1 — Simulation is a single-scroll wall.**
`simulation/page.tsx` is 4,354 lines; measured live at **2,810px tall on an empty farm** (3+ viewports) before any assumptions expand or results render. Setup → calibration → assumptions (dozens of numeric fields) → herd events → two planners → results → scenarios → comparison, with no tabs or wizard. Its scenario delete is also the app's **only native `window.confirm`** (`simulation:1865`), breaking the dialog language used everywhere else.
**Fix:** tabbed or stepped structure (Setup / Assumptions / Events & Planners / Results / Scenarios), sticky run bar, replace `window.confirm` with the standard destructive dialog.

**F-U7 · P1 — Empty states are mostly dead ends, some actively wrong.**
Animals with no filters set: *"No animals found / No animals match these filters."* (`animals:1163-1168`); finance on a truly empty ledger: *"No transactions match. Try clearing the filters…"* (`finance:788-793`). Only 2 of ~15 empty states carry a CTA (purchases, dashboard-weights). Milk uses a bare `<p>` instead of the EmptyState component (`milk:298-301`).
**Fix:** every list empty state gets the shared component + a primary CTA + one sentence of guidance; copy branches on "no data yet" vs "no matches".

**F-U8 · P1 — No table sorting anywhere; no undo anywhere; filters inconsistently URL-backed.**
Not one column in the app is sortable. No toast offers an undo action. Animals/tasks encode filters in the URL (shareable, back-safe) but finance month/type/category (`finance:500-504`) and feeding history dates (`feeding:284-285`) are component state lost on refresh — users experience this as "sometimes my filters survive, sometimes not."
**Fix:** sortable-header pattern in `ui/table.tsx`; URL-sync hook reused by every filter; consider undo for soft operations (move, dispense, task complete).

**F-U9 · P1 — Mobile tables and touch targets are field-hostile.**
Verified live: at 390px the weight table is 649px wide inside a 390px viewport — every table is `overflow-x-auto` + `whitespace-nowrap` with no card fallback; the health event log is 10 columns with no min-width and *squeezes* (`health:1024-1037`). Default button is 32px tall, `sm` is 28px, `xs` is 24px (`ui/button.tsx:22-28`) — below 44px guidance for gloved outdoor use. (The shell itself is correct at mobile — sidebar becomes a Sheet; verified by DOM measurement after the visual pass claimed otherwise.)
**Fix:** min-widths on wide tables, card/list fallback below `sm` for the 3 widest tables, min 40–44px hit areas for primary row actions.

**F-U10 · P2 — Permissions failure is a dead end on ~15 pages.**
"Could not load your permissions — refresh the page to try again." with no Retry button, unlike the consistent inline retry pattern used for data errors. The milk page's errors are text-only with no retry at all (`milk:292-295, 339-342`).
**Fix:** shared `<PermissionsError>` with a retry that refetches the permissions query.

### B. Visual design system findings

**F-V1 · P0 — Status-chip fragmentation: 7 mechanisms, 4 casing conventions.**
Canonical `StatusBadge` (Title Case, `status-badge.tsx`) vs ALL-CAPS `ScheduleStatusBadge` re-implementing its own tints (`health/schedule:31-66`) vs ALL-CAPS `OutcomeBadge` (`breeding:115-121`) vs lowercase inline Badges (`feeding:589` "done", `team:1035` "preset") vs ALL-CAPS finance Badges (`finance:813` "INCOME") vs hand-rolled pill spans (`reports:232,239`) vs inline date chips in two styles (`health:124-134` vs `tasks:485-491`). No dot indicators anywhere.
**Fix:** one Badge system with variants (tint/outline/dot), one label casing rule (sentence case), delete the re-implementations.

**F-V2 · P1 — The semantic token system is bypassed ~60× by raw Tailwind colors.**
No hardcoded hex anywhere (good), but `emerald/red/amber/zinc` classes appear across 14 files, including **five independent re-declarations of the status tint palette** that will drift (`schedule:37-74`, `breeding:100-107`, `finance:135-141`, `feeding:589`, `team:1035`). Two confirmed dark-mode breakages: `text-emerald-700` with no dark variant (`simulation:4299` — unreadable on dark card) and `border-amber-500/50` (`animals/[id]:1568`).
**Fix:** declare `success/warning/info` + tinted-container tokens in `globals.css`; lint rule (or grep CI check) banning raw palette classes outside `globals.css`/`status-badge`.

**F-V3 · P1 — Typography has no system or identity.**
Section titles exist at three competing scales — `text-lg font-semibold` (`dashboard:531`), `text-sm font-medium` (`simulation:2400`, `team:870`), `text-xs uppercase tracking-wide` (`purchases:565`, `tasks:1156`) — and the `text-lg` ones skip `font-heading`. `--font-heading` maps to the same Inter as body (`globals.css:21`), so there is no typographic identity; JetBrains Mono is loaded but used twice. `tabular-nums` is applied inconsistently (missing on dashboard weight table `:627-628` and animals list `:1202-1204`).
**Fix:** define the heading scale as components/variants; pair a display face for page titles and numerals (or at minimum distinct weights + tabular-nums on every numeric cell — one utility class).

**F-V4 · P1 — Container, alert and empty-state variants multiply.**
Cards: primitive uses ring+shadow-xs, but farm-select tiles use border+shadow-sm (`farm-select:197`), dashboard bucket tiles drop the shadow (`dashboard:558`), feeding tiles use `rounded-lg border` (`feeding:517`). The same "overdue alert" is built three ways: ring (`dashboard:216`), border+bg (`kidding:783`), border+bg/70 (`animals/[id]:1062`). Destructive alert boxes come in three styles (`border-destructive/40`, `/5`, `/10`). Empty states render 4 ways (shared component / plain `<p>` / inline text-sm / dashboard's compressed `py-8` variant).
**Fix:** primitives for `AlertCard`, `EmptyState` (one padding), `Tile`; audit Card overrides.

**F-V5 · P1 — Dialog conventions have collapsed.**
Cancel-before-confirm vs confirm-only vs **Cancel-after-destructive-confirm** (`account-dialog.tsx:405-426` — the only reversed one). Error placement varies: above form (standard), inside the footer with `mr-auto` (`health:1761-1765`), or *below* the footer (`tasks:379-383`). Required-field marking mixes `*` suffixes and "(optional)" suffixes with no legend. Help text sometimes sits under controls, sometimes above the whole form.
**Fix:** one DialogFooter contract (Cancel left, confirm right, danger = destructive variant, error above footer), one required convention (labels plain + "optional" suffix, industry standard).

**F-V6 · P2 — Button hierarchy violations.**
Team page shows two simultaneous primary actions (`team:1254, 1311`). The tasks board uses per-row *primary* "Complete" buttons while the dashboard renders the same action as outline/ghost (`tasks:293-310` vs `dashboard:84-93`). "Change status" — a mixed-intent dialog — is styled `destructive` as a trigger (`animals/[id]:583-590`). Submit labels have no verb convention ("Save", "Move", "Record", "Mix batch", "Confirm"…).
**Fix:** one primary action per view; row actions are outline/ghost; triggers are neutral, only confirms are destructive.

**F-V7 · P2 — Login/register are near-duplicate code and the app's only "designed" screens, but template-tier.**
Live verdict (6.5/10): the emerald gradient brand panel works, but panel text is low-contrast on the gradient, the form CardTitle is `text-2xl` (larger than every other dialog/card title in the app), the fixed gradient ignores theme (hard light/dark seam), and the two pages are ~90% duplicated code. The landing `/` is a redirect stub.
**Fix:** one shared AuthLayout; raise panel text contrast; derive the gradient from `--primary`; add one memorable brand moment (illustration/photography).

**F-V8 · P2 — Assorted consistency drift** (each verified): animals page hand-rolls a bordered pagination box instead of shared `PaginationControls` (`animals:1213-1244`); milk double-pads its root (`milk:165`); two sub-nav patterns (underline `FeedingNav` vs Tabs on tasks); sr-only vs visible table headers mixed *within* pages (dashboard, kidding); "Showing X of Y" caption spacing varies 4 ways; `no-access`/`farm-select` hand-roll `<h1>`s with wrong sizes and no `font-heading`; header action buttons render at three different weight/size combos across pages; decorative warning icons inconsistently `aria-hidden` (10 uses app-wide).

**F-V9 · P2 — Accessibility gaps on an otherwise strong base.**
`muted-foreground` ≈ #8b8b8b on white (~3.9:1) carries large amounts of `text-xs` helper text and the "switch farm" link — borderline AA failures. Breeding ultrasound status is color-only text (`breeding:993-1002`). Amber-on-amber tints borderline. One native `window.confirm` bypasses focus management (`simulation:1865`).
**Fix:** darken `--muted-foreground` one step; pair color with icon/text everywhere; kill the last `window.confirm`.

**F-V10 · P3 — Brand/metadata details.** Page `<title>` is static "GoatFarm" for every route (no per-page titles); no favicon/app-icon audit done but logo SVG is solid; `README` metadata describes a goat-only product while the app is multi-species.

### C. Findings from the live visual walkthrough (screens the code can't show)

| Screen | Verdict | Key quotes/facts |
|---|---|---|
| Login (1440, dark) | 6.5/10 "clean-but-template" | "right panel feels like a default component library example… gradient panel text hard to read… form field contrast poor in dark" |
| Farm-select | Template-tier | Farm-type radio cards lack icons/visual differentiation; "Create farm" CTA below the fold at 768px; two muted helper lines blur together |
| **Empty dashboard** | **3/10 — worst moment** | "shows state, not possibility… zero-value stat cards occupy the most valuable real estate… zero psychological momentum" |
| Populated dashboard | 6.5/10 "spreadsheet-adjacent" | "Data is displayed, not communicated — no trends, no sparklines, no proportion… bucket tiles flat… primary button competes with five ghost links" |
| Animals list | 6.5/10 | M/F badges grey-on-grey, no color coding; tag column "reads like a barcode font"; no sticky header; "Add animal" button cramped against page edge |
| Add-animal dialog | Solid skeleton | Radio cards praised; footer cramped at 900px viewport; no blur validation; label casing inconsistent |
| Mobile 390px | Shell correct (DOM-verified), content poor | No page overflow; sidebar correctly hidden; stat cards stack; **weight table 649px wide in 390px viewport** |
| Light dashboard | 7.5/10 — best state | "Sidebar near-white on white — invisible chrome… green accent good but over-exposed… define elevation scale" |
| Simulation (empty) | Wall | 2,810px tall with zero data |

---

## 4. Roadmap to "best in industry"

Ordered so each phase compounds. Estimates assume one focused engineer+designer pair.

### Phase 0 — Stop the bleeding (1–2 days)
1. Fix wrong empty-state copy (animals, finance) + add CTAs to every empty state (F-U7).
2. Skeletons in `loading.tsx` + keep `PageHeader` mounted during loads (F-U4 first half).
3. Kill `window.confirm` in simulation (F-U6).
4. Permissions-error Retry component (F-U10).
5. DialogFooter contract + account-dialog button order (F-V5).
6. `tabular-nums` on all numeric cells; unify submit-button verbs (F-V6).
7. Dark-mode breakages `simulation:4299`, `animals/[id]:1568` (F-V2).

### Phase 1 — Design-system foundation (~1 week) → makes every screen consistent at once
1. **Status system**: declare `success/warning/info/danger` + tint-container tokens; rebuild one Badge (tint/outline/dot variants, sentence case); delete all 7 implementations (F-V1, F-V2).
2. **Typography**: heading scale as variants; display/numeral treatment; label-casing rules (F-V3).
3. **Primitives**: `SectionCard`, `AlertCard`, `EmptyState` (one padding, CTA slot, illustration slot), `PageSkeleton`, `Tile`; fix Card/border/shadow variants (F-V4, F-V8).
4. **Charts**: add Recharts; wire the already-tokenized chart palette; build `SparkLine`, `TrendChart`, `Donut`, `Histogram` wrappers (F-U2).
5. **Enum labeling**: `labelize()` + per-enum label maps; humanize every control/cell (F-U5).
6. CI guard: ban raw palette classes outside tokens (regression prevention).

### Phase 2 — Identity & hero surfaces (~1–2 weeks) → makes it *memorable*
1. **Dashboard redesign**: alert-first ("3 does due this week" hero), stat cards with sparklines + deltas, herd-flow visualization for buckets (the farm's mental model!), charts from Phase 1.
2. **Brand layer**: shared AuthLayout, theme-derived gradients, illustration set (species-aware goat/buffalo line art), sidebar brand treatment (light-mode tint per vision review), elevation scale.
3. **Onboarding**: first-run checklist + optional sample data (F-U1).
4. **Vocabulary completion** for buffalo farms + neutral product metadata (F-U3).

### Phase 3 — Experience depth (~2 weeks)
1. Simulation restructure into tabs/steps with sticky run bar (F-U6).
2. Table sorting, sticky headers, mobile card fallbacks for the 3 widest tables, 44px touch targets (F-U9, F-U8).
3. URL-backed filters everywhere via one hook; undo toasts for soft ops (F-U8).
4. Breadcrumbs on detail pages; Help entry point; per-route `<title>`s.

### Phase 4 — Delight & differentiation (ongoing)
Motion system (list stagger, dialog springs, number tickers), command palette (⌘K), report PDF/print styling, Telugu locale readiness for field workers, PWA/offline for shed use.

---

## 5. Appendix — evidence index

- Visual-consistency code audit: 12 categories, ~70 located findings with file:line (source: full read of all 18 module pages + auth pages + 14 shared components + 17 UI primitives).
- UX-pattern code audit: 12 categories with quoted copy (source: full read of all pages + components + lib).
- Live walkthrough: 9 screens captured at 1440×900 and 390×844, dark + light; DOM measurements for the two claims where vision and DOM disagreed (mobile sidebar — DOM proved the shell correct; table widths — confirmed scroll-only).
- Prior functional audit (`FRONTEND_AUDIT_REPORT.md`, 2026-08-30) remains valid for correctness/security; its open items (M-1 withheld suggestions, M-2 epoch guards, M-3 gestation cap, M-4 silent validation) overlap with F-U7/F-V5 above and should be fixed alongside Phase 0.

---

## 6. Remediation status (2026-08-31, post-implementation)

All Phase 0–3 items from the roadmap above were implemented in one pass:

**Foundation** — new token system in `globals.css` (warm-paper light theme, green-black dark theme, sage sidebar, semantic `success/warning/info/destructive` + tint surfaces, radius 0.75rem); Fraunces display typeface for headings/stat numerals (`layout.tsx`); `enum-labels.ts` (one human-label source for every enum, species-aware buckets); StatusBadge v2 (dot + semantic tokens, superset enum coverage); Badge success/warning/info variants; StatCard/EmptyState v2; skeleton kit (`components/skeletons.tsx`) + route-level `PageSkeleton`; larger touch targets (buttons default 36px, sm 32px); tabular numerals on all tables; per-route document titles.

**Shell & auth** — sticky blurred topbar with FarmSwitcher pill (farm + type badge), avatar AccountDialog trigger, icon Logout; sidebar active-route indicator bar + footer tagline; shared `AuthLayout` for login/register (theme-derived gradient panel, species-neutral copy); farm-select redesigned (species-iconed farm cards, icon radio-cards with checked states, placeholders).

**Dashboard** — first-run onboarding card (3 linked steps when the herd is empty), herd-composition donut + linked bucket list (new SVG chart kit `components/charts.tsx`: Sparkline/Donut/BarList, zero dependencies), semantic overdue/cull styling.

**Every module page** — empty states with CTAs (incl. the wrong-copy fixes on animals/finance), all raw enum codes humanized (M/F, buckets, shifts, event types, transaction categories, kid statuses, ease, methods, outcomes Title Case), all raw emerald/red/amber replaced with semantic tokens (both dark-mode breakages fixed), dialog footer/error conventions unified, one-primary-per-view button hierarchy (team/tasks/animals dialogs), the last `window.confirm` replaced with a proper destructive dialog, animals pagination on the shared control, milk double-padding removed, URL-backed finance filters, min-widths on wide tables, buffalo vocabulary across breeding/kidding/reports/purchases, sticky section navigator on the simulation page.

**Verification** — `tsc --noEmit` clean; `eslint` 0 errors (one pre-existing RHF `watch()` warning, unchanged from HEAD); vitest: **3,179/3,186 passing** — every file green in isolation; the 7 residual failures are (a) 3 pre-existing `idempotent-request.persistence` environmental failures reproducible identically on pristine HEAD (Node ≥25 localStorage, audit N-1) and (b) run-order flakes that pass in isolation (documented jsdom/MSW contention, audit NEW-3). Live-browser design review of the redesigned surfaces: dashboard light 8/10, dark 9/10, animals 8.5/10, finance 8.5/10, simulation 8.5/10 — up from 3–6.5/10.

**Known remainders (Phase 4+)** — table column sorting, undo toasts, mobile card-fallbacks for the widest tables, Telugu locale, command palette, print/export styling.

---

## 7. Independent post-remediation audit (2026-08-31, evening pass)

Five independent adversarial agents audited the uncommitted remediation (design-system integrity, functional behavior, test integrity, WCAG a11y, build/test gates), plus a live browser verification pass. Every real finding was fixed in-tree and re-verified.

**Found and fixed (highlights):**
- A11y P1: farm-select radio cards used an invalid Tailwind variant (`has-[[input:checked]]`) that compiles to a dead selector — no focus ring, no checked state, verified empirically by compiling with the project's own Tailwind and testing in headless Chromium. Fixed to `has-[input:...]`; live-verified.
- A11y P1: two Label-in-Name regressions in the new topbar (FarmSwitcher, Account trigger) — accessible names now include the visible text.
- Behavior P1: three new "Retry permissions" buttons were silent no-ops (`void refetch` without calling) — fixed and wired through the sidebar and milk page too; every page's permissions dead-end now has a working retry.
- Design P1: Donut legend/ring palette desync when zero-count buckets precede real ones (wrong colors — a data bug) — filter once, share indexing; regression-tested.
- Design P1: control-height misalignment after the button resize (Inputs/Selects stayed 32px beside 36px buttons) — Inputs/Selects realigned to the button scale.
- Contrast: `text-warning` small text 3.2:1 (light), auth brand panel 1.6–1.9:1 (dark), destructive chip 3.9:1 on hover, chart-3 2.5:1 non-text — all raised to AA (token + component fixes; auth fix live-verified in dark).
- Behavior P2: "Welcome to your new farm" shown to a wound-down (all-sold) herd — now gated on a truly empty status register.
- Consistency: three hand-rolled chips unified onto Badge geometry; EmptyState success-green icon tile neutralized; duplicate donut legend removed; missing font-heading on one dashboard heading; account initials trimmed.
- Semantics: `outcome`/`taskCategory` label maps corrected/completed against the real backend enums; the tasks board no longer shows raw `KIDDING_DUE`-style codes.
- A11y P2 batch: single live region for route loading, `prefers-reduced-motion` global guard, anchor-nav moves focus (tabIndex targets), post-delete focus lands on the Scenarios section, NPV histogram is a labeled `role=img`, 56 bare "Loading…" paragraphs now announce politely.

**New tests written from the audit's gap list (18):** enum-labels unit tests (5), charts unit tests incl. edge cases (9), simulation delete-cancel path + dialog-names-scenario (1), dashboard onboarding gating/CTA suite (3).

**Audit verdicts:** Test integrity: NOT weakened — zero deletions, zero escape hatches, +199 net assertions, several strengthened (the two minor specificity losses noted in §3 of that report were acceptable adaptations). Design: "foundation is real" — token system verified name-complete in both themes with the tint ramp passing 4.5:1 numerically. Behavior: no confirmed regression of any HEAD behavior. Gates: production build green (27 routes), dev smoke 15/15 routes 200.

**Final gates:** `tsc --noEmit` clean · `eslint` 0 errors (1 pre-existing RHF warning, identical on HEAD) · `next build` green · vitest **3,189/3,194** — the 5 residuals are the documented pre-existing Node≥25 `localStorage` environment cluster (3 reproduce byte-identically on pristine HEAD; the other 2 pass in isolation; flagged by the prior functional audit as N-1/NEW-3).

**Remaining known items (not blocking):** `Sparkline`/`BarList` are shipped-but-unconsumed chart kit (now unit-tested); finance filter mirroring/external-change lacks a router-level test (covered live, partially in tests); `goatfarm_dev_backup_20260831.sql` at the repo root is a DB dump — do not commit it.
