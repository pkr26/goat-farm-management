# Track 1 — UI/UX, accessibility, and localization

Audit date: 3 October 2026 (America/Phoenix)

Audited source commit: `1ca78768ed227182b1b84663bcc97c5ea9bee41b`

## Verdict

The frontend has a thoughtful accessibility and localization foundation: both language catalogs have key and placeholder parity, public entry points passed sampled automated WCAG checks, the worker board has explicit loading/error/empty states, important dialogs restore focus, mobile list alternatives exist for dense tables, and dark/reduced-motion design tokens are systematic. The remaining weaknesses are concentrated in shared-shell behavior and places where shared components bypass those foundations.

Five medium-severity issues materially affect keyboard, small-screen, or Telugu users. The most visible are authenticated-header overflow at supported phone/tablet widths, focus remaining in navigation after client-side route changes, English status output in Telugu views, no language control on the shared worker surface, and raw English conflict responses in Telugu workflows. Six lower-severity issues cover touch sizing, nonvisual overdue state, table naming, form-error association, residual untranslated/non-farm-time content, and gaps in the automated accessibility gate.

## Scope and evidence

- Inspected authored React/Next.js UI, shared primitives, responsive shell, worker login/board/offline flows, EN/TE catalogs and helpers, accessibility/localization tests, and relevant backend conflict text only where needed to establish the rendered error.
- Exercised public and authenticated representative UI in Chromium through Playwright. Authenticated/API responses were intercepted with synthetic in-memory fixtures. No backend was started, no existing farm database was opened or migrated, and no application source was changed.
- Sampled `/login`, `/register`, and `/worker/login` in English and Telugu at 360×800, 768×1024, and 1280×800 with axe tags `wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa`, and `wcag22a/aa`; no violations were returned on those rendered public states. A mocked authenticated dark/Telugu shell also returned no axe violations.
- Reproduced authenticated-shell behavior at 320, 360, 768, and 1280 CSS pixels; exercised keyboard navigation, a Telugu animal list, and a worker overdue-duty card with accessibility-tree snapshots.
- Ran 10 focused Vitest files covering account-dialog ARIA, language toggle, section navigation, sidebar, animal-detail ARIA, insurance ARIA, purchases ARIA, worker touch targets, the i18n gate, and the English-literal scanner: **10 files / 45 tests passed**. The catalog tests report **3,306 keys in each of EN and TE**, with placeholder parity.
- Static searches were used for shared-component call sites, untranslated fallback paths, touch sizing, chart labels, table captions, and worker language entry points.

## Findings

### 01-M1 — The authenticated header overflows at phone and tablet widths

- **Severity:** Medium
- **Confidence:** High
- **Evidence status:** Reproduced at runtime
- **Impact:** Header controls become horizontally clipped or force document-level horizontal scrolling. At 320 px the logout control ends outside the viewport; at 360 px the farm switcher is compressed to an effectively unreadable sliver. At 768 px the account label becomes visible at the same breakpoint at which the desktop shell consumes sidebar space, pushing the logout control past the viewport. This affects farm switching, account access, and sign-out on small phones and portrait tablets.
- **Preconditions:** Authenticated owner/manager shell; narrow phone around 320–360 CSS px, or 768 px tablet with a nontrivial account/farm name.
- **Runtime evidence:** At 320 px, `document.documentElement.scrollWidth` was 340 for a 320 px viewport; measured header items ended at x=340. At 768 px, scroll width was 807 for a 768 px viewport and logout ended at approximately x=808. At 360 px there was no overflow, but the farm switcher measured only 26 px wide.
- **Current evidence:** The fixed collection of controls is one non-wrapping flex row at `frontend/src/app/(app)/app-layout-client.tsx:405-430`; the farm name alone is allowed to shrink at `frontend/src/app/(app)/app-layout-client.tsx:283-305`; the account name turns on at `md` in `frontend/src/components/account-dialog.tsx:473-492`; `md` begins exactly where the mobile sidebar stops, per `frontend/src/hooks/use-mobile.ts:3-4`.
- **Recommendation:** Define explicit phone/tablet priorities rather than letting the farm control collapse implicitly. Hide the account text until a wider container breakpoint, permit low-priority controls to move into a menu, and add 320/360/768 assertions for zero document overflow and a useful minimum farm-switcher width.

### 01-M2 — Client-side navigation leaves keyboard and screen-reader focus in the sidebar

- **Severity:** Medium
- **Confidence:** High
- **Evidence status:** Reproduced at runtime
- **Impact:** After choosing a module, a keyboard or screen-reader user is not placed at or notified of the new page. Focus remains on the old navigation link, so the user must traverse remaining sidebar and header controls or manually find the main landmark before interacting with the new content.
- **Preconditions:** Authenticated desktop shell; navigate with a sidebar link using keyboard or assistive technology; client-side transition rather than a full reload.
- **Runtime evidence:** Focusing the Animals sidebar link on `/dashboard` and pressing Enter navigated to `/animals`, but `document.activeElement` remained the `<a href="/animals">Animals</a>` element. The new main heading was not focused or announced.
- **Current evidence:** Route changes update the title but have no focus effect at `frontend/src/app/(app)/app-layout-client.tsx:325-346`. The skip link is useful only when explicitly invoked at `frontend/src/app/(app)/app-layout-client.tsx:385-396`. The main landmark has `tabIndex={-1}` but is keyed only by farm and is never focused on pathname change at `frontend/src/app/(app)/app-layout-client.tsx:440-448`.
- **Recommendation:** On successful pathname changes initiated inside the shell, focus the main landmark or page H1 and ensure its accessible name identifies the destination. Preserve focus for in-page/query-only changes and avoid overriding focus deliberately placed in a dialog or form.

### 01-M3 — Shared status fallback emits English inside Telugu views

- **Severity:** Medium
- **Confidence:** High
- **Evidence status:** Reproduced at runtime and source-proven
- **Impact:** Core herd status appears in English even when the surrounding list, filters, and actions are Telugu. This weakens comprehension for Telugu-first workers/managers and creates mixed-language accessible names.
- **Preconditions:** Telugu selected; a caller renders `StatusBadge` without localized children, such as the animal list.
- **Runtime evidence:** A mocked Telugu animal list rendered the active goat's status as `Active`; the mobile animal link's accessible name was `G-101 Active Lakshmi` while surrounding UI was Telugu.
- **Current evidence:** `StatusBadge` title-cases the raw enum in English whenever children are absent at `frontend/src/components/status-badge.tsx:85-94` and `frontend/src/components/status-badge.tsx:97-118`. The animal mobile card and desktop table use that fallback at `frontend/src/app/(app)/animals/page.tsx:1602-1624` and `frontend/src/app/(app)/animals/page.tsx:1658-1675`. Other call sites pass localized `enumLabel`, showing that a localized path already exists but is not enforced.
- **Recommendation:** Require callers to provide localized content or make the badge accept a typed enum domain plus active language. Remove the generic English humanizer from user-facing fallback behavior, and add a Telugu-render test for each shared status domain.

### 01-M4 — Workers cannot change language on the shared-tablet surface

- **Severity:** Medium
- **Confidence:** High
- **Evidence status:** Source-proven; user impact inferred from persisted-language behavior
- **Impact:** A manager's previously stored language silently determines the next worker's login and shift language. The worker cannot correct it from provisioning, roster, PIN, active-shift, or offline-shift screens, which is particularly harmful on a shared device with workers who have different language preferences.
- **Preconditions:** Shared device; `herdly.language` already contains a language chosen by another user, or a worker wants to switch during login/shift.
- **Current evidence:** Worker layout defaults to Telugu only when the shared local-storage key is absent at `frontend/src/app/worker/layout.tsx:97-110`. The active worker header exposes identity, offline/queue state, password, and end shift but no language control at `frontend/src/app/worker/layout.tsx:260-301`. Provisioning, roster, and PIN surfaces likewise have none at `frontend/src/app/worker/login/page.tsx:393-427`, `frontend/src/app/worker/login/page.tsx:553-637`, and `frontend/src/app/worker/login/page.tsx:641-707`. Repository search finds `LanguageToggle` only on login, registration, farm selection, and the authenticated manager shell.
- **Recommendation:** Put a persistent, 44 px minimum EN/తెలుగు control in the worker login shell and active/offline worker header. Decide explicitly whether language is device-wide or worker-specific; if device-wide, keep the switch available before identity selection.

### 01-M5 — Some conflict paths deliberately surface backend English in Telugu workflows

- **Severity:** Medium
- **Confidence:** High
- **Evidence status:** Source-proven; rendered-language impact inferred
- **Impact:** At the moment an insurance or ownership operation is rejected, a Telugu user can receive an untranslated English toast and inline error. Exact conflict details are useful, but bypassing localization makes a blocked financial/administrative workflow harder to understand and recover from.
- **Preconditions:** Telugu selected and a 409 conflict occurs, such as registering a duplicate policy number, renewing/claiming an ineligible policy, or transferring ownership amid conflicting state.
- **Current evidence:** The generic mapper returns unmatched server detail verbatim at `frontend/src/lib/server-error-phrases.ts:73-95`. Insurance explicitly selects `err.detail` for every 409 at `frontend/src/app/(app)/finance/insurance/page.tsx:210-215`, `frontend/src/app/(app)/finance/insurance/page.tsx:491-496`, and `frontend/src/app/(app)/finance/insurance/page.tsx:605-610`; ownership transfer does the same at `frontend/src/app/(app)/team/components/ownership-transfer-dialog.tsx:74-78`. A duplicate policy conflict is constructed as English text at `backend/app/api/finance.py:899-904`.
- **Recommendation:** Prefer stable machine-readable conflict codes with localized templates and parameters. Until all codes exist, route 409s through a localized generic message while retaining safe specifics such as policy number separately.

### 01-L1 — Several worker controls miss the product's 44 px field-touch floor

- **Severity:** Low
- **Confidence:** High
- **Evidence status:** Reproduced at runtime
- **Impact:** Secondary but operational controls are harder to hit with gloves, wet hands, or a moving handheld device. The issue does not breach WCAG 2.2's 24 px minimum in the sampled layout, but it contradicts the worker surface's explicit 44 px design target.
- **Preconditions:** Touch use on worker provisioning/roster/PIN screens.
- **Runtime evidence:** The unpinned “set up this tablet” control, “Change farm”, and “Who is working?” measured 36 px high at 360 px. By contrast, farm/worker choices and PIN keys measured 64 px, and roster pagination measured 44 px.
- **Current evidence:** The setup CTA and several cancel/back/dialog buttons use the default button height at `frontend/src/app/worker/login/page.tsx:420-426`, `frontend/src/app/worker/login/page.tsx:531-548`, `frontend/src/app/worker/login/page.tsx:600-634`, and `frontend/src/app/worker/login/page.tsx:698-707`. The shared default is 36 px at `frontend/src/components/ui/button.tsx:22-35`. The active worker header explicitly documents and uses a 44 px floor at `frontend/src/app/worker/layout.tsx:288-300`.
- **Recommendation:** Apply `min-h-11` consistently to every actionable control on worker routes, including secondary navigation and confirmation actions, and expand the existing worker touch-target test beyond duty-card actions.

### 01-L2 — The visual overdue badge has no accessible text

- **Severity:** Low
- **Confidence:** High
- **Evidence status:** Reproduced at runtime
- **Impact:** The urgency cue on an individual duty is visual-only. Linear reading still encounters the labelled “Overdue” section, which limits severity, but list-item navigation, extracted accessible names, or reused cards do not identify the task itself as overdue.
- **Preconditions:** Worker has an overdue duty and uses a screen reader or another accessibility-tree consumer.
- **Runtime evidence:** The overdue list item's accessibility snapshot contained the task title, due date, Done, and Skip, with no overdue/error text. The triangle icon was absent from the tree.
- **Current evidence:** The badge's only child is an `aria-hidden` triangle at `frontend/src/app/worker/page.tsx:68-84`; supplying that child replaces `StatusBadge`'s fallback text at `frontend/src/components/status-badge.tsx:97-118`.
- **Recommendation:** Add localized visually-hidden “Overdue” text inside the badge (or give the badge a localized accessible name) while retaining the icon. Assert it in the worker card ARIA test.

### 01-L3 — Visual data-card titles are not headings or table captions

- **Severity:** Low
- **Confidence:** High
- **Evidence status:** Source-proven; assistive-technology impact inferred
- **Impact:** Screen-reader users cannot reliably discover these sections through heading navigation or identify a table by its visible title. Column headers still describe cells, so the data remains usable, but navigation and orientation degrade across many owner/manager views.
- **Preconditions:** A `DataTableCard` supplies a title around a table; user navigates by headings/tables with assistive technology.
- **Current evidence:** `DataTableCard` renders its title through `CardTitle` at `frontend/src/components/data-table-card.tsx:45-53`; `CardTitle` is a plain `<div>` at `frontend/src/components/ui/card.tsx:36-45`. Although the table primitive supports a semantic `<caption>` at `frontend/src/components/ui/table.tsx:95-105`, static search found 56 app-level `DataTableCard` uses and only one app-level `TableCaption` use (`frontend/src/app/(app)/planner/page.tsx:1470-1472`).
- **Recommendation:** Give `DataTableCard` a semantic heading level and programmatically associate its title with contained tables, preferably with an `sr-only` caption or `aria-labelledby`. Avoid globally changing every decorative `CardTitle` without checking hierarchy.

### 01-L4 — Several animal-create errors are announced but not associated with their fields

- **Severity:** Low
- **Confidence:** High
- **Evidence status:** Source-proven
- **Impact:** Newly inserted `role="alert"` text may be announced, but a screen-reader user revisiting the invalid input does not hear its error and some fields are not exposed as invalid. This makes correction of a long modal form unnecessarily difficult.
- **Preconditions:** Animal-create validation fails for date of birth, estimated date, historical reason, birth weight, weight date, purchase date/price, or notes.
- **Current evidence:** Tag number correctly uses `aria-invalid`, `aria-describedby`, and an error id at `frontend/src/app/(app)/animals/page.tsx:613-626`. In contrast, date fields and their alerts lack this association at `frontend/src/app/(app)/animals/page.tsx:832-854`; the same pattern occurs for historical reason and birth weight at `frontend/src/app/(app)/animals/page.tsx:865-877` and `frontend/src/app/(app)/animals/page.tsx:905-916`, weight/purchase fields at `frontend/src/app/(app)/animals/page.tsx:935-976`, and notes at `frontend/src/app/(app)/animals/page.tsx:996-1005`.
- **Recommendation:** Apply one form-field helper that consistently sets `aria-invalid`, a stable error id, and `aria-describedby` (preserving any hint ids) for every validation branch.

### 01-L5 — Residual nonvisual and temporal strings bypass the active locale

- **Severity:** Low
- **Confidence:** High
- **Evidence status:** Partly reproduced; remainder source-proven
- **Impact:** Telugu mode is not complete in browser tabs or screen-reader chart output, and worker receipt/offline timestamps follow the device language/timezone rather than the selected UI language and farm timezone. A timestamp can therefore display a different calendar day from farm records near timezone boundaries.
- **Preconditions:** Telugu UI for title/chart issues; a browser locale or timezone different from the farm for worker timestamps.
- **Runtime evidence:** In Telugu, `/login`, `/register`, and `/worker/login` retained the English title `Herdly — Goat farm management`.
- **Current evidence:** Root metadata is English at `frontend/src/app/layout.tsx:37-44`; the authenticated title fallback is English at `frontend/src/app/(app)/app-layout-client.tsx:170-177`, and `/owner` has no route-title entry at `frontend/src/app/(app)/app-layout-client.tsx:141-168`. Donut accessible labels hardcode `Distribution:` at `frontend/src/components/charts.tsx:60-72` and the dashboard does not override it at `frontend/src/app/(app)/dashboard/page.tsx:895-902`. Worker receipt and offline-verification times use bare `toLocaleString()` at `frontend/src/app/worker/layout.tsx:315-318` and `frontend/src/app/worker/offline/page.tsx:76-79`, despite the language- and farm-timezone-aware formatter at `frontend/src/lib/format.ts:139-171`.
- **Recommendation:** Centralize localized document titles and chart-label templates, add all route families, and use `formatFarmDateTime` for operational worker timestamps.

### 01-L6 — The automated axe gate leaves meaningful routes, severities, and WCAG 2.2 out of scope

- **Severity:** Low
- **Confidence:** High
- **Evidence status:** Source-proven
- **Impact:** CI can pass while owner-critical pages, detail/form states, and every moderate/minor violation remain unchecked. The comment says lower-impact findings are logged, but the implementation neither fails nor logs them, making regression visibility weaker than stated.
- **Preconditions:** An accessibility regression is outside the 13 listed route landing states, is moderate/minor, appears only after interaction, or is covered by a WCAG 2.2-only rule.
- **Current evidence:** The route list at `frontend/e2e/a11y-gate.spec.ts:14-28` omits core animals, buckets, breeding, kidding, feeding, purchases, reports, owner, farm selection, registration, and detail/form/modal states. The scan includes only WCAG 2.0/2.1 tags at `frontend/e2e/a11y-gate.spec.ts:73-75`, then filters to critical/serious at `frontend/e2e/a11y-gate.spec.ts:78-87`; moderate/minor results are not logged. Mobile automation is restricted to two Pixel 7 worker journeys at `frontend/playwright.config.ts:56-75`, so it does not cover manager phone or 768 px tablet layouts.
- **Recommendation:** Add risk-based states rather than merely every route: animal create/validation, core lists, dialogs, permission/error/empty states, Telugu status content, manager phone, and portrait tablet. Include WCAG 2.2 tags and at least publish all impacts as a CI artifact or a ratcheted baseline.

## Verified strengths

- **Catalog integrity:** EN and TE have equal 3,306-key coverage and placeholder parity. The English-literal scanner and gate tests passed; only a small allowlisted set of acronyms/metadata/internal constants remains. The runtime defects above come from fallback paths that bypass catalogs, not broad missing-key drift.
- **Public entry points:** Sampled login, registration, and worker login states had no axe A/AA violations or horizontal overflow at 360, 768, or 1280 px. Labels, visible focus styling, and semantic headings were present in the sampled states.
- **Worker state design:** The duty board distinguishes loading, fetch failure with Retry, genuine empty state, overdue/today sections, and truncated-result counts at `frontend/src/app/worker/page.tsx:261-341`. Primary duty actions use 44 px or larger targets.
- **Responsive content alternatives:** Dense animal/task data uses cards below `md` rather than forcing a desktop table into a phone viewport; the existing mobile worker journey explicitly checks card selection and primary 44 px actions at `frontend/e2e/mobile-worker-journey.spec.ts:13-38`.
- **Dark mode and motion:** Semantic light/dark OKLCH tokens cover foreground, surfaces, statuses, charts, and sidebar at `frontend/src/app/globals.css:80-173`; reduced-motion handling is present at `frontend/src/app/globals.css:205-215`. The sampled dark/Telugu authenticated shell returned no axe violations.
- **Keyboard-capable primitives:** Sidebar links, farm switcher, dialogs, tabs, selects, and sortable headers are authored as native controls or accessible primitives. The account dialog explicitly registers its trigger for focus restoration at `frontend/src/components/account-dialog.tsx:467-493`.
- **Failure feedback:** Representative owner and worker views retain explicit loading, retry, empty, alert, and conflict surfaces rather than silently clearing content. Most server errors already route through a centralized localized mapper.

## Limitations

- This was a source audit plus synthetic frontend runtime exercise. The backend and existing local goat-farm database were deliberately not started or modified; authenticated behavior used intercepted fixtures. Full real-stack race/conflict behavior was therefore not re-executed in this track.
- Axe detects only a subset of accessibility failures. No VoiceOver, NVDA, TalkBack, switch-control, magnification, or physical touch/glove session was performed.
- Telugu review established structural parity and obvious English leakage, not linguistic quality; fluent native-speaker review remains necessary for terminology, tone, pluralization, and field comprehension.
- Viewports are CSS-pixel simulations, not a physical-device matrix. Browser zoom, dynamic text sizing, safe-area insets, virtual keyboards, slow networks, PWA install mode, and sunlight/low-connectivity field conditions were not independently validated.
- Dark mode was sampled, not exhaustively contrast-measured for every state, chart slice, disabled control, or third-party primitive.

## Counts

**Critical: 0 · High: 0 · Medium: 5 · Low: 6 · Info: 0 — Total findings: 11**
