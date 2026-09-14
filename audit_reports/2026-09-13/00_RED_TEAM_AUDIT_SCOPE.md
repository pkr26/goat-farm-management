# Red Team Audit — Exhaustive Functionality Scope (2026-09-13)

Phase 0 deliverable: the complete inventory of every functionality in the
monorepo that the red team audit campaign will cover. Each item is an auditable
unit with an ID (`A1`, `C3`, …) used by all subsequent per-domain audit
reports. Counts: **88 backend API endpoints** across 15 routers, **54 Alembic
migrations**, **18 simulation-engine modules**, **~30 frontend routes**,
**~20 logic-bearing shared libs/components**, **5 compose services**, **4 CI
workflows**.

Method: for every unit the audit attacks it as an adversary would —
authorization bypass (horizontal/vertical), tenant isolation, input abuse,
business-logic/cascade abuse, race/concurrency, replay/idempotency abuse,
enumeration oracles, resource exhaustion, and (frontend) trust-boundary and
state-machine abuse. Findings land in per-domain reports in this directory;
each finding gets severity, exploit sketch, and a regression test.

---

## Part A — Backend: Authentication, tokens, accounts (`api/auth.py`, `security.py`, `deps.py`)

- **A1 Registration** — anti-enumeration hash-before-check, separate probe limiter, policy, token-pair mint.
- **A2 Login** — 3-tier throttle (composite/per-email/per-IP), Argon2 timing equalization, dummy-hash, PBKDF2 legacy verify + transparent rehash, password-work reservation, race-safe re-lock.
- **A3 JWT issuance** — RS256, `kid` derivation, reserved-claim protection, `ver` (token_version) binding.
- **A4 JWT verification & keyring** — alg pinning, legacy-key bounded fallback (≤4 keys), strict claim shapes, leeway/expiry ordering.
- **A5 Refresh rotation** — family model, reuse detection + theft revocation, concurrent-tab grace window, family/session ceilings, three-point expiry checks, User→RefreshSession lock order.
- **A6 Refresh cookie handling** — `__Host-` upgrade, duplicate-cookie rejection, origin/Sec-Fetch-Site/Referer CSRF guard.
- **A7 Logout** — cookie/bearer combinations, mismatch rejection, bearer-only = logout-everywhere, family compaction.
- **A8 Change password** — confirmation budgets shared with account-delete, token_version bump, session revocation, fresh pair.
- **A9 Account export** — identity-only JSON attachment, affiliation cap → 409.
- **A10 Account deletion** — password confirm, farm-ownership refusal, tombstone scrub, session purge.
- **A11 `must_change_password` fence** — allowlist enforcement in `current_user`.
- **A12 Access-token revocation check** — `token_version` on every request.
- **A13 JWT key management** — dev auto-keygen (flock, atomic, torn-pair repair), production boot validation, previous-key rotation.

## Part B — Backend: Tenancy, RBAC core, team administration (`permissions.py`, `deps.py`, `api/team.py`)

- **B1 Farm scoping** — `X-Farm-Id` validation (dup/int range/404 uniformity), unknown-vs-forbidden conflation.
- **B2 Farm creation** — quota, User FOR UPDATE serialization, synchronous `seed_new_farm`, idempotent actor-scoped claim.
- **B3 Farm listing / multi-farm switching** — per-request re-derivation, affiliation overflow.
- **B4 Permission resolution** — owner implies all; worker role set; corrupt JSONB fails closed; `require_perm` denials.
- **B5 Dependency chain locks** — Membership→User→Role ordering, read-path lock-free caching, unsafe-method re-pin.
- **B6 Permission catalog/presets/dependency rules** — 27 permissions, `.manage`→`.view` dependencies, role presets, `TASK_CATEGORY_ROLE_MAP`.
- **B7 Worker provisioning** — owner-only, anti-takeover generic error, capacity advisory lock, HMAC-fingerprint idempotency, in-process gate.
- **B8 Worker role reassignment** — self-service, peer-manager, delegated-ceiling (`_guard_role_scope`), owner-only `team.manage` grants.
- **B9 Worker status changes** — retired `toggle` 405 tombstone, PUT status idempotency, self-deactivation block, no session revocation (farm-local).
- **B10 Owner worker-password reset** — policy contract (active/provisioned/no-other-affiliation), reauthorization after hash, token_version bump.
- **B11 Custom role CRUD** — capacity, revision optimistic lock, preset undeletable, 0-member/no-duty deletion guard, `_clean_permissions` ceilings.
- **B12 Team page aggregate** — roster + catalog response-overflow guards, `can_reset_password` disclosure.

## Part C — Backend: Animal lifecycle domain (`api/animals.py`, `services/animals.py`)

- **C1 Herd register query** — filters, `q` escaping, pagination caps, `include_all_statuses`.
- **C2 Animal creation** — three sources (BORN/PURCHASED/historical-import owner-only), purchase cascade (batch + quarantine schedule + expense), species weight caps, auto-tag retry.
- **C3 Animal profile** — per-section pagination, gated sections (health/breeding).
- **C4 Bucket moves** — `LEGAL_BUCKET_TRANSITIONS`, BREEDING gates, owner-only `history_override`, quarantine re-entry protocol.
- **C5 Weight recording** — ACTIVE-only, caps, chronology.
- **C6 Status change cascade (SOLD/DEAD/CULLED)** — withdrawal/movement blocks, pregnancy resolution, orphan weaning, task sweep, batch protocol skip, ANIMAL_SALE booking (₹0 floor), scheduled-disease death restriction.
- **C7 Buckets board** — occupancy, preview limit, feed overrides exposure.

## Part D — Backend: Breeding (`api/breeding.py`, `services/breeding.py`)

- **D1 Breeding list + detail** — pagination, int4 guard, availability aggregates permission split.
- **D2 Candidate search** — `kind` required, bounded query, least-privilege selector.
- **D3 Service recording** — doe/buck locking, eligibility (age/weight/VWP/open-breeding), inbreeding fence, partial-unique race → 409, AI/AI_SEXED methods.
- **D4 Ultrasound recording** — lock order, litter caps, cascade (expected date, KIDDING_DUE task, bucket moves), negative-result close + task skips.
- **D5 Pregnancy abort** — state preconditions, RESTING move, task skips, loss-cause attribution.

## Part E — Backend: Kidding (`api/kidding.py`, `services/kidding.py`)

- **E1 Kidding register** — upcoming/overdue/history tri-page pagination.
- **E2 Pregnancy resolution helper** — task/deep-link correctness.
- **E3 Record kidding** — parent/breeding lock order, litter entries, birth-weight bands, kid creation + tags (trigger), RECOVERY moves, WEANING duty generation.

## Part F — Backend: Health (`api/health.py`, `services/health.py`)

- **F1 Reference lookups** — schedule templates, animal search (movement-restricted exposure), purchase-batch exact-id anti-probe search.
- **F2 Movement restrictions** — episode history + `restriction_version` optimistic concurrency, clearance semantics (authority-notified preservation).
- **F3 Health events (single)** — withdrawal ceiling, scheduled-disease restriction placement, chronology.
- **F4 Bulk events (preview + record)** — snapshot staleness 409, bucket/batch scope caps, reviewed-target enforcement.
- **F5 Task-linked events** — scope/type/template/target matching, `visible_to` assignment, linked-duty completion, recurring-duty mutex.
- **F6 Vaccination schedule computation** — template → per-animal schedule states.

## Part G — Backend: Tasks (`api/tasks.py`, `services/tasks.py`)

- **G1 Task board** — 5-tab independent pagination, object-level scoping (workers vs owner), awaiting-queue for verifiers.
- **G2 Task detail** — uniform 404 (cross-farm/hidden).
- **G3 Manual task creation** — capacity guard, assignment validation (membership/role/ACTIVE animal), recurrence.
- **G4 Task completion** — assignee-only, form-linked rejection, early-completion block, canonical lock order, cascades (batch quarantine release, weaning moves, recurring successor).
- **G5 Task skip** — quarantine-protocol and RECOVERY-exit and pending-ULTRASOUND un-skippability, reason capture, successor spawn.
- **G6 Task verify** — two-person rule (owner exempt), successor under capacity.
- **G7 Task reject** — note required, return-to-pending, legacy role repair.

## Part H — Backend: Feeding (`api/feeding.py`, `services/feeding.py`)

- **H1 Today's plan** — 3-shift ration computation, dispensing records window, truthful totals.
- **H2 Feed settings** — per-bucket kg/head/day override.
- **H3 Dispense** — **required** Idempotency-Key, recipe enforcement, dry-roughage case, finished-stock debit, shortage 400.
- **H4 Dispensing history** — date filters, backdating, pagination.
- **H5 Recipes + finished stock** — read models from mixes.
- **H6 Mix batch** — ingredient debit per lines, shortage 400, idempotency.
- **H7 Inventory + add stock** — FOR UPDATE lock, FEED_PURCHASE booking, `last_purchase_price_per_kg` update.

## Part I — Backend: Finance (`api/finance.py`, `services/finance.py`)

- **I1 Ledger** — filters (typed Literals), all-time totals ex-voided, 12-month P&L.
- **I2 Manual transaction** — **required** Idempotency-Key, animal-link cross-farm rejection without oracle.
- **I3 Correction (void-and-replace)** — atomic audited rewrites of source records: ANIMAL_SALE (anchors, withdrawal), ANIMAL_PURCHASE (dates, bucket move), PURCHASE_BATCH (allocation), FEED_PURCHASE (repricing, ambiguity 409); frozen fields 422.

## Part J — Backend: Purchases (`api/purchases.py`, `services/purchases.py`)

- **J1 Batch list/search** — supplier LIKE escape, `#id` exact.
- **J2 Batch creation** — stub animals in QUARANTINE, 45-day/8-step schedule, expense booking, species weight cap, tag collision retry.
- **J3 Batch detail** — purpose-scoped animal/task payloads, degraded schedule view.

## Part K — Backend: Dashboard & reports (`api/dashboard.py`, `services/dashboard.py`)

- **K1 Dashboard aggregate** — field-level authz inside one payload, withheld = None sentinels.
- **K2 Reports aggregate** — herd summary, breeding rates, mortality; per-section permission gates; cull preview cap.

## Part L — Backend: Simulation, planner, ops-sim (`api/simulation.py`, `api/planner.py`, `api/ops_simulation.py`, `_run_limits.py`, `simulation/` 18 modules, `services/simulation_calibration.py`)

- **L1 Defaults/breeds/calibration** — 6-permission calibration gate, lookback bounds, advisory-only.
- **L2 Herd snapshot** — dual gate (`simulation.view` + `animals.view`).
- **L3 Run admission control** — 1/farm + 1/user + 2 global slots, priced sliding CPU budget, 429 semantics, cancellation-safe leases.
- **L4 Ad-hoc run** — rollback-before-CPU, NaN/±inf 422 guard.
- **L5 Scenario CRUD + compare + run** — advisory-lock quota, revision optimistic lock, stored-JSON revalidation, ≤5 compare budget, invalid rows flagged not 500.
- **L6 Planner run + plan CRUD** — horizon pre-check before budget charge, anchor re-bake, quota.
- **L7 Ops-sim run** — herd-coherence 422, ledger head-day cap, deterministic seed.
- **L8 Engine internals** — `engine`, `assumptions` validation, `feed`, `finance`, `lactation`, `market`, `shocks`, `montecarlo`, `optimization`, `backward_planner`, `planner`, `daily_ops`, `results`, `explain`, `snapshot`, `vocabulary`, `defaults` — numerical correctness, non-finite propagation, unbounded-loop/DoS abuse of user-controlled parameters.

## Part M — Backend: Cross-cutting infrastructure (`main.py`, `ratelimit.py`, `core/config.py`, `metrics.py`, `seed.py`, `services/idempotency.py`)

- **M1 Middleware stack** — request-target 414, Content-Length + streamed-body 413, trusted-host canonicalization, CORS exact-origin policy, ProxyHeaders trust boundary, request-id forging rules.
- **M2 Rate limiter core** — sliding window, tiered LRU eviction/spray resistance, reservations, per-scope ceilings, memory-backend single-process honesty.
- **M3 Argon2 worker pool** — bounded semaphore, 429 semantics, cancellation safety.
- **M4 Idempotency service** — claim/replay/contender-wait, fingerprint mismatches 409, HMAC sensitive ops, capacity guard, retention/purge.
- **M5 Startup seeding** — global reference data (buckets/recipes/vaccines) idempotency, per-farm presets + inventory, race behavior on concurrent boots.
- **M6 Legacy repair batching** — SKIP LOCKED, two-phase commit, per-farm savepoints, bounded batches.
- **M7 Background loops** — refresh purge, idempotency purge, legacy repair, inactive-animal task cleanup, deleted-user membership deactivation (shutdown safety).
- **M8 Metrics + probes** — `/metrics` unauthenticated exposure boundary, label cardinality, `/healthz` `/readyz`.
- **M9 Exception handlers & headers** — 422 input non-reflection, non-finite json-safety, opaque 500s, baseline headers, CORS-on-error reproduction.
- **M10 Production boot validators** — every `_production_safety` refusal path (cookie, CORS, hosts, TLS, Argon2 floors, HMAC secret).
- **M11 Settings surface** — env parsing, `extra=forbid`, secret handling, dev/proj divergence.

## Part N — Backend: Data layer, migrations, ops scripts (`db.py`, `alembic/`, `models/`, `scripts/`)

- **N1 Engine/pool/session** — timeouts, SSL modes, recycle, autoflush conventions.
- **N2 Alembic env & chain** — advisory lock, migration credentials split, lock_timeout, 54-revision chain integrity, downgrade round-trip.
- **N3 Schema invariants** — CHECK constraints, composite tenant FKs, triggers (tag namespace, kidding lock order), partial uniques, tombstones.
- **N4 `backup.sh`** — dump/verify/encrypt/sign/publish/S3, flock serialization, rollback.
- **N5 `restore.sh`** — empty-DB-only, confirm env, credential hygiene, signer check, advisory-lock coordination.
- **N6 `libpq_url.py` / `dotenv_value.py` / lock & copy helpers** — TOCTOU-safe reads, passfile hygiene.
- **N7 `healthcheck.py` / `export_openapi.py` / `dump_daily_ops.py`**.

## Part O — Frontend: Session, auth, API layer

- **O1 `lib/api-client.ts`** — token store, epochs, refresh flow (Web Locks + fallback fail-closed), outcome trichotomy, actor-scope checks, 401 retry, cookie-critical sections, `assertSafeApiPath`, timeouts, error normalization, success buffering.
- **O2 `lib/auth-context.tsx`** — bootstrap single-refresh, `establishSession` generations, `selectFarm` cache purge, `signOut` coalescing, forced-logout latch, redirect rules, localStorage farm persistence.
- **O3 `api/custom-instance.ts`** — Orval mutator, envelope preservation, null-query stripping.
- **O4 `/login`** — submit single-flight, session-epoch fence, 401 vs network copy, returnTo validation.
- **O5 `/register`** — policy error surfacing, signIn continuation.
- **O6 `/farm-select`** — farm-switch race lock, durable farm creation (no retry → no dup), returnTo validation.
- **O7 `account-dialog`** — export blob, change-password 3-way refresh outcome, delete account, action serialization.
- **O8 Root dispatcher + `(app)` shell** — permission landing resolution, nav gating, must-change banner, farm remount, document titles.

## Part P — Frontend: Domain pages

- **P1 `/animals`** — filter/search/pagination URL state, create dialog (source-aware schema, owner-only BORN), deep-link `?new=1`.
- **P2 `/animals/[id]`** — weight/move/status/clear-restriction dialogs, shared action flight, profile-settling write freeze, restriction 409 handling, section pagination.
- **P3 `/animals/new` shim**.
- **P4 `/breeding`** — candidate pickers, ultrasound dialog (unobservable window check), abort dialog, `?ultrasound_id=` deep link + stale-link latch.
- **P5 `/breeding/[id]/ultrasound` shim**.
- **P6 `/buckets`** — preview truncation, backend-supplied `animals_page_path` validation.
- **P7 `/dashboard`** — withheld-sentinel fail-closed rendering, task action links.
- **P8 `/feeding`** — plan, dispense dialog (idempotency-key stability), settings, history URL state.
- **P9 `/feeding/inventory`** — add stock, mix (shortage pre-warn), single-flight reuse.
- **P10 `/feeding/recipes`** — read-only.
- **P11 `/finance`** — filters, add dialog, correction dialog (consequence confirm, in-flight completion semantics).
- **P12 `/health`** — event dialog (3 scopes), bulk preview fail-closed flow, deep links (`task_id`/`animal_id`/`purchase_batch_id`), compliance fields.
- **P13 `/health/new` + `/health/schedule/[animalId]` + `task-prefill.ts`**.
- **P14 `/kidding` + `/kidding/new`** — litter field arrays, date window schema, `?breeding_id=` deep link.
- **P15 `/ops-simulation`** — toy-herd editor, client-side row validation, ledger download.
- **P16 `/planner`** — target editor, saved-plan CRUD, 409 stale-plan flow.
- **P17 `/purchases`** — two-step consequence confirm, batch detail dialog.
- **P18 `/reports`** — withheld vs no-data vs zero semantics.
- **P19 `/simulation`** — assumptions editor, scenario CRUD/compare, run-option gating, dirty-editor fingerprints.
- **P20 `/tasks`** — 5-tab URL state, action gating (not-due, unskippable), recurring confirm, team pickers.
- **P21 `/team`** — worker/role CRUD, permission matrix editor (dependencies + ceiling), row action locks.
- **P22 Boundaries** — `error.tsx`, `global-error.tsx`, `loading.tsx`, `not-found.tsx`, `no-access`, `/healthz` route.

## Part Q — Frontend: Cross-cutting libs & components

- **Q1 Client permission system** — `use-permissions` (fail-closed on error), `permission-envelope`, `permission-navigation` (landing routes, manage routes, farm-switch degradation, traversal rejection), `permission-gate`.
- **Q2 `task-action-access`** — backend-supplied `action_url` gating, skip/unavailable mirrors, encoding rejection.
- **Q3 `farm-scope-guard` + `use-single-flight`** — cross-farm continuation fences, double-click locks.
- **Q4 `idempotent-request.ts`** — sessionStorage persistence model, signature scoping (actor/farm), password-route exclusion, abort-signal conflicts, retry semantics.
- **Q5 `query-invalidation.ts`** — farm-data staleness predicate, deliberate exclusions.
- **Q6 `use-url-state`, `persisted-numbers`, `backend-caps`** — URL grammars, kg/₹ precision floors, OpenAPI parity.
- **Q7 i18n** — fallback chain, interpolation, storage safety, language-aware labels.
- **Q8 `utils.ts` `safeAppPath`** — canonicalization-attack surface.
- **Q9 Picker components** — `remote-picker` (debounce/infinite pages/keyboard), `animal-picker`, `breeding-candidate-picker`, `health-target-pickers` (`#id` resolution).
- **Q10 Display components** — `charts` (SVG math, finite filtering), `pagination-controls` (offset sanitization), `status-badge`, `stale-data-notice`, `data-table-card`.
- **Q11 `next.config.ts`** — proxy rewrites, `assertSafeBackendUrl`, security headers/CSP/HSTS policy.

## Part R — Deployment, CI/CD, contract

- **R1 Root `Dockerfile`** — digest pinning, non-root user, workers=1 enforcement, key dir perms.
- **R2 `frontend/Dockerfile`** — 3-stage, npm removal, standalone.
- **R3 `docker-compose.yml`** — 5 services, edge nginx (read-only rootfs, scheme gate, body limit, XFF), secrets `.env` handling, healthchecks, resource caps, subnet/IPAM.
- **R4 `ci.yml`** — OpenAPI freshness gate, migration round-trip, coverage floors, audits, e2e matrix, docker smoke.
- **R5 `release.yml`** — Trivy gate-before-push, SBOMs, provenance, digest pinning.
- **R6 `security.yml`** — CodeQL + SARIF gate, gitleaks, container scans.
- **R7 `mutation.yml`** — mutmut campaign scope.
- **R8 `shared/openapi.json`** — contract truth (76 paths), required-idempotency patch, Orval generation fidelity.
- **R9 e2e suite (Playwright, 20 specs)** — as adversarial verification harness + global-setup provisioning safety.

---

## Suggested audit order (dependency-first)

1. **A + B** (auth + tenancy: the trust root everything else leans on)
2. **M1–M4** (middleware/rate-limit/idempotency: the outer shell)
3. **C → D → E → F → G** (lifecycle chain with the heaviest cascades)
4. **H + I + J** (stock & money integrity)
5. **K + L** (aggregates + compute surfaces)
6. **M5–M11 + N** (ops: seeding, loops, migrations, scripts)
7. **O → Q** (frontend session/permission/data-layer)
8. **P** (every page)
9. **R** (deployment/CI/contract)
