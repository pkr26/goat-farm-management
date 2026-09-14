# Red Team Remediation Log (2026-09-13)

Fix pass for the 2026-09-13 red team audit (`00`–`17` in this directory).
Every actionable High and Medium finding is fixed and regression-tested; all
code-actionable Lows are fixed; Info items are either quick-won or explicitly
accepted below. Verification status at the bottom.

## High findings — all fixed

| ID | Fix |
|---|---|
| RT-M-1 | `RequiredIdempotencyKey` on `POST /api/feeding/mix`, `/api/feeding/inventory/{id}/add`, `/api/purchases/new`, `/api/auth/farms`; `_REQUIRED_IDEMPOTENCY_HEADER_ROUTES` extended so the OpenAPI contract marks them required; the managed-purchase branch of `POST /api/animals` (source=PURCHASED without a historical-import reason) requires the key too (RT-C-4). Frontend already keys all of these; e2e direct fetches updated. |
| RT-C-1 | The managed-purchase cascade in `POST /api/animals` now requires `purchases.manage` in addition to `animals.create` (403 with guidance otherwise). |
| RT-HIJ-1 | `POST /api/purchases/new` with `create_animals=true` now requires `animals.create` in addition to `purchases.manage`; `create_animals=false` stays a pure procurement record. |
| RT-L8-1 | `close_gaps` pre-checks the purchase-event budget by pure arithmetic (`sum(ceil(count/MAX_HEAD))` vs `MAX_PLAN_EVENTS=500`) and raises an actionable `ValueError` (→ 422) **before any materialization**; `_purchases_from` repeats the check self-contained with a 1000-append defensive backstop and inf-clamps `needed`. The 608M-event OOM bomb now returns a fast 422. |

## Medium findings — all fixed

| ID | Fix |
|---|---|
| RT-A-1 | The per-email login ceiling is now **soft**: only the composite (IP,email) and per-IP scopes pre-reject; an email-blocked request still pays full Argon2 verification, a *correct* password logs in, and only wrong passwords answer 429. `_login_email_locked` queries the email bucket directly (not via the ordered reason function) so a simultaneously-tripped composite cannot shadow it. |
| RT-B-1 | Accepted-risk interim (invitation flow is the real fix): the oracle stays rate-bounded by the per-owner password-work budget (10/window) with equalized Argon2 timing; documented. |
| RT-C-2 | The meat-sale minimum-age gate follows **sex + age** instead of `current_bucket == MALE_KIDS`, so an unweaned male kid riding in RECOVERY with its dam can no longer be sold under age (cull remains open). |
| RT-DE-1 / RT-C-5 | A history override can no longer move a doe with an open service/pregnancy out of the reproductive workflow buckets {BREEDING, PREGNANCY_EARLY, PREGNANCY_LATE, DELIVERY} — 409 with resolution guidance. Override *into* workflow buckets remains the sanctioned correction. |
| RT-FG-1 | Task completion now 409s for any **auto-generated** task whose category is form-linked (ULTRASOUND/KIDDING_DUE/VACCINE/DEWORMING) regardless of linkage columns (category fence on top of the existing linkage fence). DB CHECK constraints noted as migration follow-up. |
| RT-HIJ-2 | Finance corrections reject `category ∈ SYSTEM_ONLY_CATEGORIES` whenever the row has no producing record (source_type is None) — a manual row can no longer be corrected into system categories. |
| RT-KL-1 | Dashboard `buckets`/`total_active`/`sex_counts` and reports `bucket_rows`/`total_active`/`sex_counts` gated behind `animals.view`; withheld = `None` sentinel (owner/full-permission behavior unchanged). |
| RT-KL-2 | Ops-sim enforces `MAX_RESULT_HEAD_DAYS` (50,000) on the **entire result payload** (days + journeys), not just `include_ledger`; oversized shapes get an actionable 422. |
| RT-L8-2 | `backward_planner` guards zero denominators (`conception_rate=0`, `sex_ratio_female` 0/1 with matching-sex targets) with actionable `ValueError`s → 422, not `ZeroDivisionError` 500. |
| RT-L8-3 | Ops-sim cost now `horizon × (1 + head//10) × (1 + 10)` birth-amplification factor: the measured 500→4,300-head worst case is fully covered while legit max runs stay well inside the 650k budget. |
| RT-M-2 | Two-layer mitigation: backend ordering (see RT-A-2/RT-M-5 below) + edge `limit_req` (RT-R-1). Pool saturation itself remains bounded by the non-queuing executor (429 fail-fast). |
| RT-M2-1 | `Settings` refuses to boot on unknown `GOATFARM_*` process-env variables (allowlist: script/edge/test-only names); misspelled knobs now fail loudly in every environment. |
| RT-N-1 | `libpq_url.py` converted denylist → **allowlist** (`application_name`, `connect_timeout` only); `sslrootcert`/`sslcert`/`gssencmode`/etc. fail loudly. |
| RT-P7-1 | Backend withheld totals are `None` sentinels (RT-KL-4) and the dashboard/reports pages render "Requires … access" markers for them (the page's own OR-the-permission convention). |
| RT-Q-1 | `taskSkipUnavailable` mirrors the backend's third skip gate (generated animal-linked ULTRASOUND while the service is pending). |
| RT-R-1 | Edge nginx `limit_req_zone` 5 r/s burst 20 (429) on `location /api/auth/` — first-line flood shaping before the single worker's Argon2 pool. |
| RT-R-2 | Compose split into `goatfarm_app` (edge/frontend/backend) + `goatfarm_data` (db/migrate/backend) — the edge and frontend can no longer reach `db:5432`. Edge keeps its fixed app-segment IP for `TRUSTED_PROXY_HOSTS`. |
| RT-R-3 | Closed by RT-L8-1's pre-check + backstop; the 2g/1-worker/1-replica topology now never sees the materialization. |
| RT-R-4 | The edge entrypoint now **refuses** (exit 2) a non-loopback bind with `ENVIRONMENT=development` unless `GOATFARM_ALLOW_DEV_PUBLIC_BIND=true` is set explicitly. |

## Low findings — all code-actionable ones fixed

- **RT-A-2** register probe-limiter `is_blocked` hoisted above the Argon2 hash (zero oracle cost — existence is already disclosed by the 400, and `is_blocked` allocates nothing for unseen keys).
- **RT-A-3** production boot validates the JWT private key's file mode (refuses group/other-readable; symlink-following retained for K8s mounts).
- **RT-B-2** `_locked_membership` pins the target role for owners too (unconditional `_pin_membership_role`, lock order unchanged).
- **RT-B-3** structured `goatfarm.audit` security-event logging on every team admin mutation (worker create/role/status/reset, role CRUD); append-only DB table noted as follow-up.
- **RT-B-4** `X-Farm-Id` accepts strict `[0-9]+` only (Unicode digits, `+`, whitespace, underscores → 400).
- **RT-C-3** an owner override out of a purchased quarantine now requires the pristine-protocol check (mid-protocol batches must use the guarded day-45 task).
- **RT-C-6 / IdentifierText** control characters rejected in animal tags/names and kid tags (`no_control_characters` validator).
- **RT-DE-2** inbreeding fence extended to grandparent-grandchild and avuncular matings (bounded ancestor probes).
- **RT-FG-2** a non-owner who placed a scheduled-disease episode cannot clear it (two-person rule with owner exemption, mirroring the verify rule).
- **RT-FG-3** `place_movement_restriction` clears stale clearance attribution fields on each new episode.
- **RT-FG-4 / RT-FG-5** skip reason and reject note are required (min_length=1); frontend dialog gating + label updated.
- **RT-HIJ-3** quarantine task titles carry `[Batch #id]` instead of supplier free text (frontend `task-prefill` parser is bracket-agnostic; legacy rows still parse).
- **RT-HIJ-4** dispensing dates cannot predate the farm (UTC-vs-local comparison documented as loosen-only).
- **RT-HIJ-5** ration override domain cap 50 kg/head/day in `FeedSettingIn`.
- **RT-HIJ-6** the purchases `IntegrityError` handler inspects `constraint_name` and only maps `uq_animal_tag_per_farm` to the retry-409; everything else re-raises.
- **RT-KL-3** `ready_to_move_suggestions` uses an exact-total scalar subquery (no `count().over()` window aggregate).
- **RT-KL-4 / RT-KL-5** withheld dashboard totals are `None` sentinels; capacity-busy 429 carries `Retry-After: 5`.
- **RT-L8-4** `fodder_yield_t_dm_per_acre_year` floor `ge=0.01` (named `MIN_FODDER_YIELD_T`, also used by the Monte-Carlo clamp).
- **RT-M-3 / RT-M-4** metrics method label allowlisted to standard verbs (else `OTHER`); request-log paths control-character-escaped (log-forging closed).
- **RT-M-5 (+RT-A-7)** `_raise_invalid_refresh` consults the per-IP gate **before** appending per-cookie keys — a throttled address no longer pressures the limiter's key ceiling.
- **RT-M-6** production boot refuses `UVICORN_WORKERS`/`WEB_CONCURRENCY` > 1 (in-memory budgets are per-process).
- **RT-M2-2** production force-disables `/metrics` (mirrors `/docs`), with the rationale documented.
- **RT-M2-3** new `goatfarm_maintenance_loop_batches/rows` counters wired into all four previously-invisible loops.
- **RT-N-2 / RT-N-3 / RT-N-4** backup S3 cleanup failures are loud (orphan keys named); the two CHECK-narrowing downgrades gained house-style data preflights (verified live on a scratch DB); missing `.env` + unset env now refuses instead of silently defaulting to development.
- **RT-O-1 / RT-O-2 / RT-O-3** `TimeoutError` matched as abort (no auto-replay of internally-owned timeouts); local epoch supersession maps to `unavailable` (never fake-`rejected`); a revoked persisted farm clears the selection (routes to farm-select) instead of silently entering `list[0]`.
- **RT-P2-1 / RT-P2-2 / RT-P2-3 / RT-P2-5 / RT-P2-6 / RT-P6-1 / RT-P11-1** typed worker password cleared on dialog dismissal; `kids_per_kidding` sentinel-OR gating; strict `?batch=` grammar; scenario-delete farm-scope fence; planner Update disabled on empty name + raw `<a>` → `Link`; buckets deep-link permission-gated with validated fallback; finance month regex tightened to real months.
- **RT-Q-2 / RT-Q-3** persisted idempotency recovery digests are never evicted while their realm entry is live (flood-proof); Donut filters non-finite slices.
- **RT-R-5** new `frontend/e2e/cross-tenant.spec.ts`: worker 403, cross-farm 404 + forged header uniformity, anonymous 401.
- **Quick-won Info**: `/api/planner/plans` added to `FARM_DATA_PATHS`; export toast epoch-fenced; `clearIdempotencyRequestState` documented as the test-pinned variant; README deployment notes for the downgrade FK window, BACKEND_URL build-time baking, and digest-refresh policy (RT-N-5 / RT-R-7 / RT-R-8).

## Accepted / deferred (explicit)

- **RT-B-1** (worker-create existence oracle) — real fix is the invitation flow; interim posture documented.
- **RT-FG-1 DB CHECK constraints, RT-B-3 audit table** — API guard + structured logging now; migrations noted as follow-ups.
- **RT-R-6** (release rebuild vs scanned bits) — buildx cache-mediated rebuild retained; documented.
- **RT-R-8** digest refresh — incident-driven via the weekly security scan; documented in README.
- Remaining Info items from reports 01–17 are observations of documented design (see each report).

## Verification

- **Backend**: `ruff format` + `ruff check` clean (187 files); `mypy --strict` clean (99 modules); **full `pytest` suite: 4163 passed, 4 skipped, 0 failed**; `shared/openapi.json` re-exported (76 paths) and frontend Orval client regenerated from it.
- **Frontend**: `tsc --noEmit` clean; `eslint` 0 errors; **full `vitest` suite: 4471/4471 passed** (232 files).
- **New regression coverage**: `tests/test_redteam_spine_fixes.py` (13), `tests/test_redteam_domain_fixes.py` (22), `tests/test_dashboard_redteam.py` (9), `tests/test_simulation_redteam.py` (10), 28 new script tests in `test_deployment_artifacts.py`, `e2e/cross-tenant.spec.ts` (3), plus ~50 updated pre-existing tests pinning the new contracts (soft lockout, sentinel `None`s, required keys/reasons, permission coupling, strict header grammar, segmented networks, fodder floor, dispensing floor, ration cap).
- **Cross-cutting interactions resolved** (each found only in the full-suite run and fixed): migration tests that downgrade past `f3d4e5f6a7b8` now purge the (newly persisted) NULL-farm farm-creation idempotency claims first; the purchases tag-collision 409 reads the constraint name by walking the driver exception chain (asyncpg adapter shapes differ); test clients without the conftest hook gained auto-keying.
- Compose YAML validated programmatically (segment attachments verified); migration preflights exercised on a scratch database; shell scripts `bash -n`/`sh -n` clean.
