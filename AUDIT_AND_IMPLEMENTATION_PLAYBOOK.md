# Herdly — Master Audit Report & Implementation Playbook

> **Purpose of this document:** This is the single source of truth for (a) what the deep audit found and (b) exactly how to implement every improvement. It is written so that **any agent in a future session can pick up any section and execute it** without re-doing the audit. Each implementation spec lists goal, evidence, exact files, steps, tests, and verification commands.
>
> **Product context (read first):** Herdly is a multi-tenant goat farm management app (FastAPI + PostgreSQL backend, Next.js + React + TS frontend). The owner runs **their own** operation: up to ~20 farms, each run independently by farm workers/managers. **This is NOT a commercial SaaS** — no billing, no external customers. Commercial-only audit findings are recorded in §4 and deliberately deprioritized.
>
> Audit date: 2026-09-21. Method: 8 parallel deep audit agents covering API layer, domain logic, database/migrations, security, frontend architecture, UI/UX/a11y, testing/CI, and DevOps/product. All findings below cite real code (file:line) verified during the audit.

---

# PART 1 — AUDIT RESULTS

## 1.1 Scores

| Dimension | Score | Verdict |
|---|---|---|
| Security (auth, RBAC, sessions, TOTP, uploads) | **92** | Textbook. Zero critical/high code defects; README claims verified against code |
| Backend domain logic & services | **90** | Exact money math (Decimal paise), exceptional race discipline, single-sourced state machines |
| Testing & CI | **87** | 3,484 backend tests on real Postgres, 263 frontend test files, elite release pipeline |
| Backend API layer | **87** | All 78 endpoints verified authz-covered; field-level authorization in serializers |
| Database & migrations | **86** | Tenant-guard composite FKs, verified linear 76-revision chain; no retention strategy |
| UI/UX & accessibility | **82** | Real design-token discipline and a11y fundamentals; Telugu i18n is a sidebar, not a product |
| Frontend architecture & state | **80** | Auth/token/refresh plumbing best-in-class; 26 monolithic client pages (up to 4,242 lines) |
| DevOps/reliability (for this use case) | **75** | Infra craft ~90 (backups, releases, hardening); no alerting stack; 24h RPO — acceptable-ish for owner-operator |
| **Overall (owner-operator lens)** | **~85** | Engineering top-tier; gaps are product features, not quality |

## 1.2 What is genuinely elite (do not break these)

- **Tenant isolation at DB level** — composite `(farm_id, id)` FKs so cross-farm references fail in PostgreSQL even if app code is wrong (`backend/app/models/animals.py:60-75`, `tasks.py:43-67`, `screening.py:132-136`).
- **Refresh-token rotation with theft detection**, family revocation, `__Host-` cookies, TOTP with independently encrypted secrets + anti-replay (`backend/app/security.py`, `api/auth.py`).
- **Idempotency done right** — transaction-bound claims, HMAC fingerprints for password-bearing ops (`backend/app/services/idempotency.py`).
- **JWT verification** — RS256 pinned twice, `kid` dict-lookup only, mandatory `ver` claim, bounded keyring (`security.py:739-838`).
- **Zero TODO/FIXME/HACK markers, 0 `@ts-ignore`, 0 real `as any`, mypy --strict green.**
- **Backup/restore scripts are paranoia-grade AND tested** (GPG sign+encrypt, kernel flock, restore-floor revision gating; 86 tests in `backend/tests/test_deployment_artifacts.py`).
- **Migration discipline** — applied revisions immutable (CI-enforced), full upgrade→downgrade→upgrade round-trip in CI (`.github/workflows/ci.yml:144-156`).
- **Race discipline** — documented lock ordering, `FOR UPDATE SKIP LOCKED` claiming, optimistic concurrency tokens on roles/scenarios/plans/clearances.
- **Worker scoping already exists** — workers see only duties assigned to them/their role (`backend/app/services/tasks.py:959 task_scope`); completion attribution (`completed_by_id`).

## 1.3 Verified bugs (all real, all confirmed against code)

| # | Sev | Bug | Location |
|---|-----|-----|----------|
| B1 | HIGH | Cross-tab forced logout: revoking a farm membership makes every other open tab destroy a *valid* session and redirect to /login — sign-out and farm-revoke share one storage signal | `frontend/src/lib/auth-context.tsx:291,437-445` |
| B2 | MED | Insurance claims accepted after coverage expiry — validates `claim_date >= start_date` but never `<= renewal_date` | `backend/app/services/finance.py:401-428` |
| B3 | MED | Aborted pregnancies never get a re-breeding duty (both other RESTING exits schedule one) — does most needing re-service silently drop off the task board | `backend/app/services/breeding.py:1084-1115` vs `services/tasks.py:349-393,626-631,698-703` |
| B4 | MED | Reports gating inconsistency: `breeding_stats.kiddings`, `total_records`, `mortality.total_kids_born` returned ungated while sibling aggregates are withheld without `breeding.view`/`health.view` | `backend/app/api/dashboard.py:702-716,748-754` |
| B5 | MED | Screening intake not idempotent: retried `POST /api/screening/batches` / `/uploads` mint duplicate rows | `backend/app/api/screening.py:654-699,782-912` |
| B6 | MED | Screening errors leak raw provider/S3 exception text (infra topology) to farm members | `backend/app/services/screening/pipeline.py:569,623` → `api/screening.py:195` |
| B7 | MED | Backend coverage ratchet ~8 pts weaker than documented: `fail_under = 85` evaluates combined line+branch (actual 92.7%), not the branch figure the comment claims | `backend/pyproject.toml:101-111` |
| B8 | MED | Screening UI (marquee AI feature) has **0% frontend test coverage**, no e2e spec; global thresholds hid it | `frontend/src/app/(app)/screening/page.tsx` (98 statements, 0%) |
| B9 | LOW | Planner under-charges CPU budget ~55% worst case (up to 17 forward passes vs 11 priced) | `backend/app/api/planner.py:255`, `services/simulation/planner.py:486-550` |
| B10 | LOW | `plan_probabilities` uses flat prices while the Monte Carlo it claims to mirror applies annual price variation — biased probabilities | `backend/app/services/simulation/planner.py:654-657` vs `montecarlo.py:425-429` |

**Smaller verified issues:** RESTING-day constant duplicated (`services/dashboard.py:236` hard-codes 30 vs `services/tasks.py:51 REBREED_AFTER_RESTING_DAYS`); `move_animal` fallback date uses default TZ not farm TZ (`services/animals.py:95,221` — latent, all current callers pass explicit dates); screening provider httpx clients never closed (`services/screening/providers.py:88,175`); `ix_transactions_date`/`ix_health_events_date`/`Task.recurring_series_id` redundant indexes + ~10 redundant `farm_id` singles (`models/finance.py:102`, `health.py:34`, `tasks.py:186`); list-envelope inconsistencies (`AnimalListOut` lacks limit/offset echo; screening batches lack total/offset; feeding/buckets unpaginated); screening table rows use `role="button"` `<tr>` breaking SR table semantics (`screening/page.tsx:574-584`); sub-AA contrast microtext (`app-layout-client.tsx:262`, `farm-select/page.tsx:239`); register password-hint not associated for screen readers (`register/page.tsx:154-170`); no `created_at` on HealthEvent/KiddingRecord/BreedingRecord/WeightRecord/FeedingRecord/KidEntry (backdating indistinguishable from same-day entry).

## 1.4 Structural observations (verified, not bugs)

- **26 pages are monolithic client components**; `simulation/page.tsx` is 4,242 lines; zero `next/dynamic`/`React.lazy` in `src/` — route-level splitting only.
- **No production observability**: frontend error boundaries log to console only; backend logs are plain text rotated at ~30MB; production force-disables `/metrics` (`config.py:1334-1341`); no Sentry/OTel/Prometheus-stack in repo.
- **Query keys lack farm scope**; correctness rests on the manual `cancelQueries()+clear()` convention on farm switch (`auth-context.tsx:184-189`) — correct today, unenforced structurally.
- **No automated a11y/perf/visual gates** (no axe-core, Lighthouse, bundle budget).
- **No data retention/archival** on high-velocity tables (`screening_*`, terminal `tasks`, `weight_records`); screening fact-table PKs are int4 (`models/screening.py:158,290,362,446`).
- **Staleness strategy is "navigate again"**: no `refetchInterval`, no cross-tab cache invalidation, no SSE.

---

# PART 2 — SCOPE FILTER (owner-operator model)

## 2.1 IGNORE these audit findings (commercial-SaaS-only)

- Billing/subscriptions/plans/entitlements — no customers.
- Horizontal-scaling panic — 20 farms × 5–10 staff ≈ 100–200 users; one uvicorn process is comfortable. Single-host compose is CORRECT here.
- API versioning, webhooks, third-party API keys/OAuth — no external consumers.
- GDPR churn flows, customer data export, customer-facing audit viewer — you own all data; nightly encrypted backups are your export.
- Zero-downtime/blue-green deploys — a 5-minute midnight maintenance window is fine.
- Support helpdesk, product analytics, multi-region CDN.

## 2.2 KEEP at full priority

Everything in Part 3 below. Notably: notifications, Telugu, offline, and screening cost caps matter MORE for own operations, not less.

---

# PART 3 — IMPLEMENTATION PLAYBOOK

> Each spec is self-contained. Standard gates after ANY change (run from repo subdirs):
> - Backend: `cd backend && ./.venv/bin/python -m pytest -q` · `./.venv/bin/ruff format --check . && ./.venv/bin/ruff check .` · `./.venv/bin/python -m mypy --strict app` · `./.venv/bin/alembic upgrade head && ./.venv/bin/alembic check`
> - Contract sync after ANY backend route/schema change: `cd backend && ./.venv/bin/python scripts/export_openapi.py` then `cd frontend && pnpm orval` (CI has freshness gates for both).
> - Frontend: `cd frontend && pnpm test:coverage` (floors 85/80/85/85) · `pnpm build` · `pnpm exec playwright test`
> - Conventions: backend = no narration comments, match module style, mypy strict green, NEVER edit an applied migration file (append new revisions only). Frontend = theme tokens only (no hex), `dark:` pairs on tints, shared components (PageHeader/StatCard/DataTableCard/EmptyState/StatusBadge), lucide icons only, i18n strings in BOTH `en.ts` and `te.ts` (typed MessageKey union enforces parity), semantic `data-*` test hooks not class assertions.

## ITEM 0 — Farm capacity config (5 minutes, do first)

**Goal:** allow 20 farms under one owner account.
**Evidence:** `max_farms_per_user` default is 10 (`backend/app/core/config.py:755`).
**Action:** set `GOATFARM_MAX_FARMS_PER_USER=25` in the deployment env file. No code change.

## ITEM 1 — Bug fixes (each independent; batch into one PR or do singly)

### B1 — Cross-tab forced logout on farm revocation
- **Where:** `frontend/src/lib/auth-context.tsx:291` (`applyFarmList` calls `clearStoredFarmId()` when persisted farm was revoked) and `:437-445` (storage listener treats `newValue === null` exclusively as session teardown → `clearSession()` + `/login` in every tab).
- **Fix:** make the storage signal carry intent — e.g. store a JSON payload `{reason: "signout" | "farm-revoked"}` (or use two separate keys), so the farm-revoked path routes other tabs to `/farm-select` with their session intact; only `signout` runs `clearSession()`.
- **Tests:** extend existing cross-tab auth-context tests — farm-revoke in tab A must NOT destroy tab B's session; sign-out still must.

### B2 — Insurance claim after coverage expiry
- **Where:** `backend/app/services/finance.py:401-428` (`claim_insurance_policy`).
- **Fix:** reject `claim_date > policy.renewal_date` with the module's standard domain-error shape (409/422 per local convention); add test: claim dated past renewal → rejected; claim within window → still works.

### B3 — Aborted pregnancies get no re-breeding duty
- **Where:** `backend/app/services/breeding.py:1084-1115` (`mark_aborted`); compare RESTING exits in `services/tasks.py:349-393,626-631,698-703` that schedule the REBREED duty (`REBREED_AFTER_RESTING_DAYS = 30`, `tasks.py:51`).
- **Fix:** schedule the same REBREED duty on abortion, honoring the same resting/flush rules; add service test asserting the duty appears on the board after `mark_aborted`.

### B4 — Reports gating inconsistency
- **Where:** `backend/app/api/dashboard.py:702-716,748-754`.
- **Fix:** gate `breeding_stats.kiddings`, `breeding_stats.total_records`, `mortality.total_kids_born` behind the same `breeding.view`/`health.view` checks as their siblings (null-not-zero withheld convention, `dashboard.py:288-293`). Update RBAC report tests.

### B5 — Screening intake idempotency
- **Where:** `backend/app/api/screening.py:654-699` (`POST /batches`), `:782-912` (`POST /uploads`).
- **Fix:** accept optional `Idempotency-Key` via existing `execute_idempotent` machinery (actor+farm+route+path scope), exactly as finance/feeding do; publish the header in OpenAPI like other idempotent routes (see `main.py:716-723`); tests: replay returns original, changed body → 409.

### B6 — Screening error text leaks infra detail
- **Where:** `pipeline.py:569` (raw `exc` stored into `ScreeningImage.error`), `pipeline.py:623` (provider errors into `ScreeningRun.error`), surfaced verbatim at `api/screening.py:195`.
- **Fix:** map to a fixed enum of user-safe reason codes (`PROVIDER_ERROR`, `DOWNLOAD_FAILED`, `INVALID_IMAGE`, …) for tenant-facing fields; keep raw detail in logs/operator-only fields; update screening tests.

### B7 — Coverage ratchet honesty
- **Where:** `backend/pyproject.toml:101-111`.
- **Fix:** set `fail_under = 92` (matches measured combined 92.7%) and correct the comment; optionally add a branch-specific gate in CI. Sanity-check frontend global floors vs measured 94.2% the same way; add per-directory floor so a whole page can never sit at 0% again (see B8).

### B8 — Screening UI has zero tests
- **Fix:** Vitest/MSW coverage for `frontend/src/app/(app)/screening/page.tsx` and `screening-check-dialog.tsx` (upload, quota-exceeded, provider-failure, result rendering); one Playwright journey with stubbed screening outcome; add per-glob coverage thresholds in `vitest.config.ts`.

### B9+B10 — Planner pricing/consistency
- **Fix:** price `close_gaps` worst case honestly (`api/planner.py:255`: up to 17 forward passes, not 11); make `plan_probabilities` apply `_apply_annual_price_variation` when `risk.within_run_price_variation` is on (`planner.py:654-657`, mirror `montecarlo.py:425-429`); update planner tests.

### B-small batch
- Unify RESTING-day constant: `dashboard.py:236` use `REBREED_AFTER_RESTING_DAYS` from `tasks.py:51`.
- `move_animal`: require `reference_date` or take farm TZ (`services/animals.py:95,221`).
- Close screening provider httpx clients on worker shutdown (`providers.py:88,175`).
- Redundant-index hygiene revision (CONCURRENTLY drops): `ix_transactions_date`, `ix_health_events_date`, `Task.recurring_series_id` single index; review ~10 `farm_id index=True` singles superseded by farm-leading composites.
- A11y quickies: screening row → real button/link inside cell (`screening/page.tsx:574-584`); raise sidebar tagline contrast (`app-layout-client.tsx:262`); associate register password hint via `aria-describedby` (`register/page.tsx:154-170`).

## ITEM 2 — Worker Tablet PWA (the daily-driver worker app)

> Full approved design; build in 3 phases. Locked decisions: PWA on existing Next.js stack · per-worker PIN login · offline queue from day one · tasks + linked record forms.

### Phase 1 — Backend: PIN auth + idempotent completion

1. **Model+migration:** add `pin_hash: Mapped[str|None]` (String(255)) and `pin_updated_at` to `FarmMembership` (`backend/app/models/core.py:207-242`); new Alembic revision after head `cad1e2f3a4b5` — plain nullable ADD COLUMNs, no rewrite.
2. **Config (`core/config.py`):** `worker_pin_min_length` (default 4, ge=4, le=12; production validator ≥6 near `config.py:1234-1307`), `worker_pin_rate_limit_max_attempts=10`, `worker_pin_rate_limit_window_seconds=300`.
3. **PIN management (`api/team.py`, `schemas/team.py`):** `WorkerCreateIn` gains optional `pin` — hash via `_hash_team_password` (`team.py:200`) to inherit Argon pool admission + per-owner throttle; store on membership; **do NOT set `must_change_password` for PIN-provisioned workers** (fence at `deps.py:206-213` would lock them out). New `POST /api/team/workers/{membership_id}/reset-pin` mirroring `reset-password` (`team.py:1127`), revoking user sessions on change. Both accept `Idempotency-Key` following the `team.workers.create` HMAC pattern (`services/idempotency.py`).
4. **Roster endpoint:** `GET /api/auth/worker-roster?farm_id=…` — unauthenticated, returns `{items:[{membership_id, display_name}]}` for active, non-tombstoned, role-bound (`role_id IS NOT NULL`) memberships with `pin_hash` set; hard per-IP throttle (~30/5min, new scope). Document the first-name-leak tradeoff in a comment.
5. **`POST /api/auth/worker-login`** modeled on `login` (`api/auth.py:833`): body `{farm_id, membership_id, pin}`; resolve membership+user (farm match, role-bound, active, not tombstoned, pin set); hard throttle per (IP,farm,membership) + per-(IP,farm) spray scope at 10×; dummy-hash timing parity for unknown pairs (`auth.py:163-170`); verify via `verify_password_async` with `_reserve_password_work("worker-pin-work")`; refuse 403 if `totp_state=="ACTIVE"`; success → `_issue_tokens` (`auth.py:645`) unchanged. Match login's duplicate-header/int4-ceiling/JSON-content-type hygiene.
6. **Idempotent completion:** `POST /api/tasks/{id}/complete` (`api/tasks.py:581`) and `/skip` accept optional `Idempotency-Key` via `execute_idempotent`; replay returns original result (makes offline retry safe).
7. **Tests (`backend/tests/test_worker_pin_auth.py`):** PIN provisioning, reset revokes sessions, owner-only enforcement, login success/wrong-PIN lockout/spray scope/timing parity, tombstone/inactive/owner/TOTP refusals, roster shape+throttle, complete/skip idempotent replay + 409-on-different-body, production PIN-length validator.
8. **Contract:** export OpenAPI → `pnpm orval`.

### Phase 2 — Frontend: `/worker` surface

1. **Shell:** `frontend/src/app/worker/layout.tsx` (top-level, outside `(app)`) — minimal client shell: farm name, worker name, big "End shift" logout, offline badge, queue status. Reuse `useAuth()`/`usePermissions()`; replicate `!user → /login`, `!farmId → /worker/login` gates. No sidebar/NAV_GROUPS changes. Telugu-first: on mount, if no `herdly.language` stored, `setLanguage("te")` (`i18n/index.tsx:34`).
2. **Login (`/worker/login`):** farm pinned in localStorage (`herdly.tabletFarm`, one-time manager setup); fetch roster → tap name → large PIN pad → `worker-login` → existing `signIn()` (`auth-context.tsx:398-411`) → `/worker`. "End shift" = existing `signOut()` (:223-257) extended to wipe the offline queue. Add `/worker/login` to `PUBLIC_PATHS` (`auth-context.tsx:67`).
3. **Task board (`/worker`):** `useListTasksApiTasksGet`; render today+overdue as large cards (extend mobile-card pattern `tasks/page.tsx:734-831`, ≥44px targets); extract `RowActions` (`tasks/page.tsx:212-667`) into `components/task-row-actions.tsx`, rendered with `canComplete` only; keep optimistic completion (`lib/task-optimistic.ts:20`). Form-linked duties follow `task.action_url` via `permittedTaskActionPath` + `withReturnTo(…,"/worker")` (`lib/task-action-access.ts:61`) — existing forms already honor `?returnTo`.
4. **Redirects:** add `/worker` to `APP_ROUTE_PERMISSIONS` (keyed `tasks.view`) + landing rule (accounts with `tasks.complete` but not `dashboard.view` land on `/worker`) in `lib/permission-navigation.ts:24-33,35+`; `login/page.tsx:121` and `farm-select/page.tsx:114` pick it up via `firstPermittedPathFromList`.
5. **Offline queue (`src/lib/offline-queue.ts`, new):** localStorage records `{id, path, init, queuedAt, actorScope, farmScope, v:1}`; hardening mirrors `idempotent-request.ts` (bounded count/bytes, version, fail-closed reads); actor+farm scoped (`persistentSignature` pattern, `:337-360`) so another worker on the same tablet never replays someone else's writes. Mutation wrapper enqueues on network failure/offline with Telugu "Saved — will send when online"; replay FIFO through `apiFetch` (`api-client.ts:716`) on `online`/interval/focus; extend `isIdempotencyProtectedMutation` to `/api/tasks/\d+/complete|skip`; 409 → done, drop; 5xx → backoff. `clearSession()` (`auth-context.tsx:205-221`) wipes the queue.
6. **PWA infra:** create `frontend/public/` with `sw.js` (network-first `/api`, cache-first `_next/static` + shell) and 192/512 icons; `src/app/manifest.ts` (`start_url:"/worker"`, `display:"standalone"`); register SW from worker layout; CSP add `worker-src 'self'` + `manifest-src 'self'` (`src/lib/csp.ts:112-123`, update test mirrors); proxy matcher excludes `sw.js`+`manifest.webmanifest` (`src/proxy.ts:61`); `no-cache` header for `/sw.js` in `next.config.ts`; **Dockerfile: add `COPY --from=builder --chown=nextjs:nextjs /app/public ./public`** (standalone omits `public/`).
7. **i18n:** all worker strings in both catalogs.
8. **Tests:** Vitest queue suite (enqueue/replay/409-drop/actor-scope/wipe/bounds), login page, board rendering; Playwright Pixel-7 journey: provision worker with PIN → pin tablet → PIN login → own duties only → offline complete → reconnect → synced+attributed → form-linked duty deep-link with `returnTo=/worker`.

### Phase 3 — Gates + docs
All standard gates green; README "Worker tablet app" section (PIN model, roster tradeoff, shared-device logout discipline, offline-queue semantics); `frontend/AGENTS.md` worker-surface conventions.
**v1 out-of-scope:** worker-native simplified forms, Background Sync/push, QR badges, tablet-only token scoping.

## ITEM 3 — Cross-farm owner dashboard (#1 missing feature for 20 farms)

**Goal:** one screen across all owner farms + benchmarking; no more switching farms 20 times.

**Design:**
1. **Backend — new router `backend/app/api/owner.py`:**
   - `GET /api/owner/overview` — owner-only (resolve farms where caller's membership is owner-role; reuse membership predicates from `deps.py`). For each owned farm return: farm id/name, active animals, overdue duty count, today's duties done/pending, animals on kidding watch, movement-restricted count, open screening flags, monthly income/expense/net.
   - Implementation: do NOT loop per farm with the per-farm services N times per request blindly — write aggregate SQL per farm group (the per-farm queries exist in `services/dashboard.py`/`finance.py`; lift their cores into farm-grouped aggregates keyed on `farm_id IN (...)`). Bound by `max_farms_per_user`. Add `Cache-Control: no-store` as usual; permission = "is owner of ≥1 farm".
   - `GET /api/owner/benchmarks?days=90` — per-farm: conception rate, kid mortality %, avg daily gain / feed cost per kg, ₹ profit per animal sold; computed from existing aggregates (breeding stats, mortality, finance per-animal P&L already exist per farm — generalize).
2. **Frontend — `/owner` page (or `/reports/owner`):** farms as ranked cards/table (DataTableCard), worst-first sorting for attention items, per-farm drill-through links that switch farm context via existing farm-switch machinery. Register in `NAV_GROUPS` (`app/(app)/layout.tsx`) visible to owners only.
3. **Tests:** cross-farm aggregation correctness (2+ farms, data never mixes), non-owner 403, empty-owner case; frontend rendering + permission gating.

## ITEM 4 — Notifications (WhatsApp/SMS) (highest ROI after tablet app)

**Goal:** daily task digest to each farm's workers/manager + same-day alerts to owner for: screening flags, kidding-watch windows, overdue critical duties, feed below reorder level.

**Design:**
1. **Provider:** MSG91 (SMS, India-friendly) or WhatsApp Business API. New `backend/app/services/notifications/` with a provider seam like screening's `VisionProvider` (one interface, MSG91 first implementation, console/log provider for dev).
2. **Data:** new tables via one Alembic revision: `notification_recipients` (membership-scoped phone, opt-in flags per alert class, verified flag) and `notification_log` (append-only: farm, recipient, class, payload hash, provider message id, status, timestamps — the audit trail + dedupe).
3. **Digest job:** background loop following the existing bounded-batch patterns (`GOATFARM_*_CLEANUP_*` loops in `main.py` lifespan are the template): once per farm's morning (farm timezone — convention exists: farm IANA TZ, default Asia/Kolkata), aggregate duties due today per worker (reuse `task_scope`) → one SMS/WhatsApp per recipient.
4. **Alerts:** hook points already exist — screening finding confirmed (`api/screening.py` review endpoint), movement restriction placed, feed reorder alerts (cadence materialization), overdue-duty sweep. Emit into `notification_log` and send; per-farm daily caps + quiet hours.
5. **Config:** `GOATFARM_MSG91_*` (key, sender, template ids), `GOATFARM_NOTIFICATIONS_ENABLED=false` default, fail-closed startup validation when enabled (pattern: screening's enablement gates).
6. **Frontend:** team page gains phone/notification prefs per member; owner opt-in toggles per alert class.
7. **Security note:** notifications ≠ account recovery — keep the no-email-reset stance unchanged.
8. **Tests:** provider-mocked send paths, dedupe, quiet hours, per-farm caps, digest content per role scope, failure retry/backoff.

## ITEM 5 — Telugu completion + i18n gate

**Goal:** the product speaks Telugu, not just the chrome.
1. Catalog all user-facing literals on: dashboard (~95% hardcoded English — `app/(app)/dashboard/page.tsx:202,272-345,375-397`), farm-select (also add `LanguageToggle` there — currently absent, `farm-select/page.tsx:184-209`), reports, no-access, and the form pages missing `useT` (animals/new, buckets, feeding/*, finance/insurance, health/new, kidding/new, ops-simulation).
2. Add backend error-code mapping: backend emits machine-readable codes; map through en/te catalogs client-side (`lib/mutations.ts:9-10`, `api-client.ts:566-594` currently render server text verbatim) — highest-stakes text (validation, denials, rate limits) reaches Telugu workers.
3. CI gate: catalog-parity check + an ESLint `no-literal-string`-style rule (or JSX-text scanner) so English literals can't creep back.

## ITEM 6 — Screening cost control

1. Per-farm daily LLM-spend cap: config `GOATFARM_SCREENING_DAILY_CALL_BUDGET_PER_FARM` enforced in the pipeline claim path (`services/screening/pipeline.py`); over-budget rows stay PENDING for next day.
2. Cost metrics: screening Prometheus counters (calls, errors, est. cost per provider) in `backend/app/metrics.py` — note production currently force-disables `/metrics`; expose it on the internal network only per README §Observability when you self-host a scraper.
3. One retry-on-5xx/timeout inside provider adapters (`providers.py:88,114`) to avoid re-paying full calls for blips.
4. Operational policy (no code): screen QUARANTINE/PREGNANCY_LATE/DELIVERY pens daily rather than whole herd; use `GET /api/screening/stats` scoreboard to pick the cheapest accurate gate provider.

## ITEM 7 — Owner lockout protection (TOTP recovery codes)

**Goal:** eliminate the permanent-owner-lockout failure mode without weakening no-email-recovery.
1. **Backend:** new Alembic revision — `totp_recovery_codes` table (user_id FK, code_hash Argon2, used_at nullable). At TOTP enrollment/confirmation (`api/auth.py:1828-1892` area), generate 8–10 single-use codes, return them ONCE in the confirm response. `POST /api/auth/totp/challenge` additionally accepts a recovery code: constant-time compare via hash verify, mark used (single-use), same 5-attempt/5-min throttle, emits a `security_event` (`audit.py`) — alert on it. Regenerate endpoint (requires password + TOTP) revokes prior codes.
2. **Frontend:** account dialog (`account-dialog.tsx`) shows codes once with print/copy; login challenge screen accepts "use a recovery code".
3. **Runbook (docs):** break-glass DB-side reset procedure documented in README for the case both devices + all codes are lost.
4. **Tests:** single-use enforcement, throttle parity with TOTP challenge, revocation on regenerate, audit event emitted.

## ITEM 8 — Deployment checklist (your infra)

1. Cloud VPS 2–4 vCPU / 8 GB RAM (NOT a farm-premises box — tablets reach it over the internet).
2. Caddy/nginx TLS terminator → `docker-compose.production.yml` (edge binds loopback; terminator proxies to it — README §Production).
3. Env: `GOATFARM_MAX_FARMS_PER_USER=25`, `GOATFARM_ENVIRONMENT=production`, `GOATFARM_COOKIE_SECURE=true`, `GOATFARM_DB_SSLMODE=verify-full`, JWT keypair mounted, `GOATFARM_TOTP_ENCRYPTION_KEY`, `GOATFARM_IDEMPOTENCY_REQUEST_HMAC_SECRET`, screening provider keys if enabled.
4. Nightly `backend/scripts/backup.sh` → S3/B2 with GPG recipient+signer fingerprints; alert on non-zero exit; quarterly restore drill (wall-clock time = real RTO).
5. Expected cost: ~₹2–4k/month infra + LLM screening spend (capped per ITEM 6).

## ITEM 9 — Data housekeeping (do when data accumulates; not urgent)

1. Retention/archival job for `screening_images/runs/crops/findings` + terminal `tasks`/`weight_records`/`feeding_records` older than N months (archive schema or partitions); add `(farm_id, crop_id)` indexes before any cascade delete path (`models/screening.py` gaps).
2. Screening fact-table PKs int4 → bigint while tables are small (`models/screening.py:158,290,362,446`).
3. Add `created_at` (server-default UTC) to HealthEvent, KiddingRecord, BreedingRecord, WeightRecord, FeedingRecord, KidEntry — closes the backdating blind spot for insurance/withdrawal evidence.

## ITEM 10 — Frontend structural health (do opportunistically, not blocking)

1. Decompose the 5 giant pages into feature modules (`simulation/page.tsx` 4,242 lines, `animals/[id]` 2,342, `health` 2,148, `tasks` 1,747, `team` 1,636); add `next/dynamic` for the simulation engine and account dialog.
2. Farm id in TanStack Query keys (structural tenant isolation in cache) instead of relying solely on `cancelQueries()+clear()`.
3. `refetchOnWindowFocus` or short `refetchInterval` for dashboard/tasks/screening; cross-tab invalidation via BroadcastChannel.
4. axe-core Playwright gate; `pytest-xdist` with per-worker DBs when backend CI nears its 60-min timeout; extend mypy to `backend/scripts/`; broaden ruff rules (`S`, `C4`, `SIM`, `RET`, `PTH`).

---

# PART 4 — RECOMMENDED BUILD ORDER

1. **ITEM 0** (config) — 5 minutes.
2. **ITEM 1** bug fixes — 1–2 days total.
3. **ITEM 2** Worker Tablet PWA — the workers' daily driver.
4. **ITEM 7** TOTP recovery codes + **ITEM 8** deployment hardening — protect yourself.
5. **ITEM 4** notifications — the farm starts talking to you.
6. **ITEM 3** owner dashboard — manage 20 farms from one screen.
7. **ITEM 5** Telugu completion + **ITEM 6** screening cost caps.
8. **ITEMS 9–10** as scale demands.
