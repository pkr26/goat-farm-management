# Remediation Log — 2026-09-17 exhaustive audit

All 55 findings from `00_MASTER_EXHAUSTIVE_AUDIT.md` remediated in-tree
(uncommitted). Verification: ruff clean, `mypy --strict app` clean (115
files), full backend suite green on a real PostgreSQL 16.9, Alembic
71-revision chain verified single-head with a clean upgrade→check→downgrade→
upgrade round-trip, OpenAPI snapshot regenerated and idempotent.

## HIGH

- **H-1 TOTP strip** — `totp_enroll` now refuses to overwrite an ACTIVE
  enrollment (409), revalidates `token_version` under the row lock
  (mirroring change_password), and `totp_disable`'s wrong-code path joined
  the throttle ledger (new `totp-disable` scope, 429s + metrics). Tests:
  enroll-on-ACTIVE 409 (original secret still gates login), disable
  brute-force throttling.
- **H-2 screening writes on a read permission** — `create_batch`,
  `submit_batch`, `request_upload` moved from `health.view` to
  `health.manage` (held by MANAGER/VET presets and owners; the read-only
  Auditor correctly loses write access), plus the `screening_enabled` 503
  gate on the two batch endpoints. RBAC tests: viewer 403 on all three
  POSTs while still reading; vet passes the permission layer.
- **H-3 screening retry starvation** — ERROR-row retry horizon keyed on the
  image's own `updated_at` (no-run poison rows now respect the 1-hour
  backoff and can no longer monopolize the claim budget). Tests: backoff
  before/after aging.
- **H-4 production CSP blocks screening** — build-time
  `GOATFARM_CSP_CONNECT_ORIGINS`/`GOATFARM_CSP_IMG_ORIGINS` (fail-closed
  parser with tests, `src/lib/csp-origins.ts`) wired through next.config.ts,
  the frontend Dockerfile (new ARGs), docker-compose build args, and both
  `.env.example` files. Default CSP unchanged; operators set the S3 public
  origin when screening is enabled.

## MEDIUM

- **M-1** mortality memo gated on `health.view` (`mortality_loss: null`
  when withheld; dashboard convention; nullability flowed through
  OpenAPI + generated client + finance page).
- **M-2** `auth.password.changed` / `auth.account.deleted` security events
  emitted after commit.
- **M-3** screening per-image handler rolls back before recording the
  ERROR and the commit is failure-tolerant (cycle survives a DB-level
  failure mid-image).
- **M-4** listing-cap tripwire: warning + summary note when the S3 listing
  hits `MAX_LISTED_KEYS_PER_CYCLE` (farms sorting later are invisible this
  cycle). Full cursoring documented as the scale-out path.
- **M-5** 25 MB download cap: `head_object` before transfer; oversize →
  terminal SKIPPED (never re-claimed), zero downloads.
- **M-6** DSCR repaying-year filter now uses operating principal (balloon
  excluded) — the 12/13/12 repro returns `None` as documented; regression
  test added.
- **M-7** held-for-festival males priced from their own per-age weights
  (head-weighted blend) — repro now books the true age weight.
- **M-8** insurance registration span capped at 5 years (schema + service
  twins of the BIZ-3 renewal cap).
- **M-9** RBAC exhaustive matrix: worker rotated via `login_and_rotate`;
  403s must name a declared permission code.
- **M-10** `forcedLogout` latch released once the logout transition lands
  on a public path (Back-after-logout redirects again instead of hanging
  on the loading gate).
- **M-11** screening PUT bounded by `AbortSignal.timeout(60_000)`; dialog
  reset clears `uploading`.
- **M-12** i18n: vaccination-schedule page fully localized (22 keys en+te),
  tasks Suspense fallback, animals page chrome (13 keys en+te). Simulation
  page full localization documented as follow-up (3.7k lines of copy).
- **M-13** insurance premium blank → required (ledger-correction
  `z.preprocess` pattern), no silent ₹0.
- **M-14** screening deep-link/list failures render the API error detail
  with real Retry controls.

## LOW

L-1 renew-on-CLAIMED refused; L-2 ASCII-decimal TOTP guard (no more
TypeError/500 — empirically pinned); L-3 `animal_id` filter int32-bounded
(422, not 500); L-4 `scalar_one_or_none` + 401 in the three TOTP routes;
L-5 `s3_endpoint_url` under the https-or-loopback rule; L-6 TOTP AES cache
keyed by keyring identity (reload self-invalidates); L-7 orval header gap
documented at the mutator; L-8 abandoned-upload expiry in bounded
SKIP LOCKED batches; L-9 cadence backfill floored at the earliest
animal-introduction fact; L-10 calibration cost-span from a true full-window
min-date query; L-11 stale `WOOD_CURVATURE_B` export removed; L-12
daily-ops notes the never-a-replacement-sire limitation; L-13 explain
litter wording derived from the computed figure; L-14 conservative
settling-doe sire purchase documented in place; L-15 `next_due_date`
3650-day ceiling (schema + DB CHECK `ck_health_events_next_due_bounded`);
L-16 `bucket_moves.effective_date` UTC server default dropped; L-17 six
residual float columns → NUMERIC (recipe `kg_per_100kg` at its documented
six-decimal tolerance, scale 6); L-18 `title_args` jsonb shape CHECK; L-19
`otpauth://` scheme guard on the enrollment link; L-20 screening dialog
busy-ref + session-epoch fence; L-21 non-empty ApiError detail fallback for
HTTP/2 empty statusText; L-22 export toast one/many split; L-23 repeat-plan
invalid-draft guard; L-24 price-per-kg enablement covers empty weight;
L-25 clearance dialog state reset on open; L-26 farm-scope fences on error
continuations (kidding/breeding/screening); L-27 ops-simulation result
fence; L-28 planner defaults-ref disarmed by the basis buttons; L-29
`formatFarmDateTime` for held-since/updated columns; L-30 TOTP at-rest
tautology replaced with the intended hash-distinctness pin; L-31/L-32
status assertions pinned to exact codes/details; L-33 twelve tautological
e2e payload assertions deleted; L-34 monkeypatch fixture for settings
patching; L-35 reports e2e now proves populated values (L-9 teardown
documented wontfix: account deletion 409s while owning a farm and no
farm-delete endpoint exists by design; worker-side two-person-verify e2e
noted as a coverage follow-up); L-36 `GOATFARM_METRICS_ENABLED`/
`GOATFARM_RATE_LIMIT_BACKEND` documented; L-37 informational (documented
mixed clock), no action.

## Test-suite integrations

- New regression tests for every behavioral fix (78-test screening file
  now includes RBAC/backoff/size-cap; TOTP +4; finance insurance +4;
  simulation DSCR/pricing/cadence/daily-ops +4; health ceiling updated).
- Migration chain: one new revision `c3e5a9f1d7b4`; `restore_floor.sh`
  allowlist, ops-migration HEAD pin, and feed-quantity numeric contract
  updated to match.
- Full suite: **4,530 passed, 0 failed** (final run; the first full run's
  five failures were migration-pinning tests updated above).

## Verification asymmetry (frontend)

No Node runtime exists on this machine, so `tsc --noEmit`, eslint, vitest,
and Playwright could not run locally. All frontend edits were made by
pattern-matching the codebase's existing idioms with full-file reads, the
generated client was hand-updated in exact orval style (byte-parity with
regeneration is CI-gated), and every catalog change preserves en/te key
parity. `pnpm typecheck && pnpm test && pnpm orval` on a Node 24 machine is
the remaining verification step.
