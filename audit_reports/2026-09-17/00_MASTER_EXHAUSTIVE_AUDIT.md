# Master Exhaustive Audit — 2026-09-17

Full-codebase deep audit: every backend source file (`app/**`, 71 Alembic migrations,
`scripts/`), every frontend source file (`src/**`, configs), all infra (Dockerfiles,
docker-compose, all GitHub workflows, dependabot, gitleaks/trivy policies), both test
suites, and the shared OpenAPI contract. Methodology: 12 parallel deep-review slices
(backend core/security, auth/team API, domain APIs ×2, services, simulation engine,
models/schemas/migrations, frontend lib/shell, frontend pages ×2, infra/CI, test
suites) plus independent static analysis (ruff, route-vs-OpenAPI cross-check,
secret/artifact scan). **Every finding below was re-verified against source by the
orchestrator before inclusion**; the two headline simulation math findings were
additionally reproduced numerically against the project venv, and the TOTP
`compare_digest` crash was reproduced empirically.

Prior audit rounds (2026-09-01 → 2026-09-17) were cross-referenced; already-fixed and
in-file-documented behaviors are not re-reported.

## Objective checks (all clean)

- `ruff check app/` — zero findings.
- OpenAPI contract: all 94 backend routes present in `shared/openapi.json` with
  correct prefixes/methods (the 15 apparent gaps are empty-path `@router.get("")`
  declarations and imperative `healthz`/`readyz` registration).
- Git hygiene: no tracked secrets/caches/build artifacts; no `.env` committed;
  gitleaks/trivy policies scoped and documented.
- No `dangerouslySetInnerHTML`, `eval`, `exec`, or `pickle` anywhere; no
  `target="_blank"` without `rel`; no console logging of tokens.

---

## HIGH

### H-1. TOTP 2FA can be silently stripped with password + a stolen access token
`backend/app/api/auth.py:1773-1807` (enroll), `:953` (login), `:1925-1942` (disable)

`POST /api/auth/totp/enroll` requires only the current password — no
currently-valid TOTP code, no `totp_state == "ACTIVE"` guard, and no
`token_version` revalidation after the password confirmation releases the session.
It overwrites `totp_secret_enc` and forces `totp_state = "PENDING"`; login demands
a code only when state is `ACTIVE`. An attacker holding a phished password plus any
live access token permanently removes the victim's second factor with no session
revocation and no distinct security event (`auth.totp.enroll_started` is
indistinguishable from a first-time enrollment). The sister endpoint `totp_disable`
deliberately requires password + valid code for ACTIVE enrollments — enroll
retains exactly the hole the prior re-audit closed on disable. Aggravators: (a)
`totp_disable`'s wrong-code path records no throttle attempts (unlike
`confirm`/`challenge`), an unthrottled 6-digit oracle; (b) the missing
`token_version` recheck means a concurrent password rotation does not abort an
in-flight enroll.

**Fix:** refuse enroll when `totp_state == "ACTIVE"` unless the request carries a
currently-valid code (mirror `totp_disable`); revalidate `token_version` under the
row lock; apply the TOTP-challenge throttle ledger to disable's code check; emit a
distinct `auth.totp.enroll_replaced` event.

### H-2. Screening write endpoints are gated on a read permission; the seeded "Auditor" role can mint S3 uploads and burn paid provider billing
`backend/app/api/screening.py:64, 595, 637, 678`; `backend/app/permissions.py:248-262`

`POST /api/screening/batches`, `POST /batches/{id}/submit`, and `POST /uploads` all
require only `health.view` — a view permission held by the seeded read-only VIEWER
("Auditor — read-only access for investors, consultants and lenders") and by any
custom role. Every other module in the codebase splits `.view` (GET) from
`.manage` (POST); screening is the sole exception, and `test_screening.py` has no
permission tests. A read-only auditor can create unbounded `ScreeningBatch` rows,
mint presigned S3 PUT URLs with no per-farm rate limit, and upload distinct junk
images (the sha256 dedup only catches identical bytes) that the worker ships to the
configured paid vision provider (Anthropic/OpenAI) — attacker-controlled billing
plus unbounded S3 growth plus review-queue flooding. Compounds with H-3 and H-4.

Also: `create_batch`/`submit_batch` skip the `screening_enabled` 503 gate that
`request_upload` enforces.

**Fix:** gate the three POSTs on `health.manage` (or a new `screening.upload`
granted to worker presets only); add RBAC tests (viewer → 403, manager → 201);
apply the `screening_enabled` check uniformly.

### H-3. Screening worker: ERROR rows with no GATE-stage run retry every cycle with zero backoff — ≥50 poison uploads permanently halt screening for every tenant
`backend/app/services/screening/pipeline.py:220-245` (claim), `:336-338` (budget), `:710-761` (no-run ERROR paths)

The ERROR-retry horizon is keyed exclusively on the latest GATE-stage
`ScreeningRun`. Download failure, normalization failure (corrupt/hostile bytes),
and derivative-upload failure all set `ERROR` before any run row exists, so
`latest_run.last_at IS NULL` makes the claim predicate always true — the 1-hour
backoff never applies. Poison rows sort oldest-first and fill the per-cycle claim
`LIMIT` (default 50), so `new_keys = […][: max(0, budget - len(claimed))]` is
always 0: no PENDING upload and no new S3 key, for any farm, is ever processed
again, while the worker re-downloads and re-decodes the poison set every 300 s
poll, forever.

**Fix:** key the ERROR-retry horizon on `ScreeningImage.updated_at` (or
`max(created_at)` across all run stages).

### H-4. Production CSP blocks the screening feature end-to-end (upload AND display)
`frontend/next.config.ts:25-36` vs `frontend/src/components/screening-check-dialog.tsx:134` and `frontend/src/app/(app)/screening/page.tsx:313-350`

The production `Content-Security-Policy` ships `connect-src 'self'` and
`img-src 'self' data:`. The disease-check walkthrough PUTs photo bytes directly to
the cross-origin presigned S3 URL (`fetch(result.data.upload_url, {method: "PUT"})`)
and renders presigned S3 GET URLs as `<img src>`. With AWS S3 or any cross-origin
MinIO endpoint, the browser blocks both — the feature cannot upload or display
photos in any real production deployment. Dev builds ship no CSP, and no e2e spec
drives the browser→S3 PUT, which is why CI has never caught it. (Workaround
deployments fronting MinIO same-origin are possible but not the default wiring.)

**Fix:** proxy photo bytes through the backend origin (best), or add the S3
endpoint origin to `connect-src`/`img-src` via a configured knob; add an e2e that
exercises the PUT against a stub S3.

---

## MEDIUM

### M-1. Mortality memo discloses clinical death figures without `health.view`
`backend/app/api/finance.py:594` + `backend/app/services/finance.py:449-463`

`GET /api/finance` returns `mortality_loss` (12-month death head count and
weight-derived ₹ valuation) under `finance.view` alone, while dashboard
(`_CLINICAL_OUTCOME_STATUSES` strip) and reports deliberately withhold exactly
these figures behind `health.view` ("deaths and stillbirths are clinical
outcomes"). The seeded ACCOUNTANT preset (finance.view, no health.view) reads
death counts through the finance side door that dashboard/reports close — a
disease-outbreak signal leak.

**Fix:** gate `mortality_loss` on `"health.view" in perms` (None when withheld),
matching the dashboard convention; pin with a test.

### M-2. No security/audit events for self-service password change or account deletion
`backend/app/api/auth.py:1296-1396, 1487-1591`

`security_event(` is emitted for every TOTP/refresh/challenge action but neither
`change_password` (the canonical "takeover completed" moment — revokes all
sessions) nor `delete_account` (irreversible tombstoning) emits anything, in
violation of the platform's own DET-1/DET-2 detection standard. An attacker
finalizing a takeover via `/change-password` is invisible to the SIEM stream.

**Fix:** emit `auth.password.changed` / `auth.account.deleted` after commit.

### M-3. Screening cycle: per-image `db.commit()` outside the try — a DB-level failure cascades
`backend/app/services/screening/pipeline.py:374-383`

If `_process_image` aborts the transaction (constraint/DataError after a flush),
the except-handler mutates the ORM object and the un-caught `await db.commit()`
raises `PendingRollbackError`, crashing the cycle: remaining claimed images are
stranded in PROCESSING for ≥10 min and the error text is never persisted.

**Fix:** `rollback()` in the handler before mutating, and wrap the commit in its
own try that continues the loop.

### M-4. Screening S3 discovery starves at the 5,000-key listing cap; raw objects are never deleted
`backend/app/services/screening/s3.py:74-89` + `pipeline.py:74-76, 273-277`

`ListObjectsV2` returns the 5,000 lexicographically smallest keys under `raw/`;
unpadded farm ids make "10" sort before "2", raw keys are never removed, and ISO
dates sort last — once the farms sorting first hold 5,000 objects, every later
farm's direct-to-S3 uploads become permanently invisible while the cycle reports
`listed=5000` as healthy. The pre-registered PENDING rows mask this for the
presign flow, but the module's documented primary ingress silently degrades to
nothing (while still paying a 5,000-key LIST every 300 s).

**Fix:** checkpointed `StartAfter` cursor persisted between cycles (round-robin),
or lifecycle-delete raw objects once terminal; alarm when
`listed == MAX_LISTED_KEYS_PER_CYCLE`.

### M-5. No size cap on screening uploads; worker reads the whole S3 object into memory before any limit applies
`backend/app/services/screening/s3.py:91-102, 111-130` + `pipeline.py:701`

The presigned `put_object` cannot express a length condition, and neither the API
nor the worker enforces any byte ceiling (`download()` does `Body.read()` in
full). Bytes go direct to S3, bypassing the 1 MiB edge/API body cap entirely; the
decompression-bomb guard runs only after the full read. One multi-GB object OOM-
kills the single-worker process that serves every screening cycle, and the aged-
ERROR retry path repeats the transfer (compounded by H-3).

**Fix:** `head_object` before download and terminally skip objects above a hard
cap (~25 MB); switch to presigned POST policies with `content-length-range`.

### M-6. Simulation: terminal balloon re-admits interest-only years into avg/min DSCR (contradicts the adjacent comment)
`backend/app/simulation/engine.py:1748-1751, 1872-1883`

The balloon is added to the final year's *principal* for reporting, but the
repaying-year filter tests raw `row.principal > 0`. Reproduced: horizon 12 /
term 13 / moratorium 12 → `avg_dscr = min_dscr = -2.0047` where the documented
result is `None`; downstream, `run_optimization` returns 0/97 feasible (every
candidate failing only the DSCR floor) and Monte Carlo `prob_dscr_below_one`
degenerates to 1.00.

**Fix:** filter on operating principal (subtract the balloon from the horizon's
final year), mirroring `_operating_debt_service`.

### M-7. Simulation: held-for-festival males drawn by a scheduled grower sale are priced at the grower chain's average weight (≈21% revenue underbooking)
`backend/app/simulation/engine.py:842-865`

When an ordered `male_grower` sale exhausts the age chain and dips into
`held_males`, the older/heavier held animals are priced at the chain average (or
the mid-age fallback). Reproduced: booked ₹7,827/head (21.23 kg) vs true 25.85 kg
at age 10 → ₹117,410 vs ~₹148,700 on a 15-head event (−21%). Fires exactly when
combining the product's own recommended hold-into-Bakrid pattern with a planned
sale event; propagates into `EventFill.price_per_head`/`revenue` and planner
action text.

**Fix:** compute the held draw's average weight from `held_males` per-age
`male_weight_at_age` and blend by head drawn.

### M-8. Insurance: registration accepts an unbounded future `renewal_date` on an append-only register
`backend/app/schemas/finance.py:152-154` + `backend/app/services/finance.py:195-202`

The BIZ-3 five-year span cap exists only on the renewal path. A mistyped year at
registration (`3026`) books one premium covering a millennium, never surfaces in
the expiry window, never spawns the renewal duty, and no edit/delete/backwards-
renewal exists to correct it.

**Fix:** apply `MAX_RENEWAL_SPAN_DAYS` at creation (and/or a schema ceiling on
`renewal_date`); optionally a DB CHECK.

### M-9. The "exhaustive RBAC 403 matrix" passes for the wrong reason
`backend/tests/test_rbac_exhaustive.py:146-206` + `backend/app/deps.py:206-213`

The zero-permission worker is provisioned with `must_change_password=True` and
never rotated, so the rotation fence 403s every request before any `require_perm`
logic runs. Deleting every permission guard keeps this test green. (The two static
introspection tests and `test_rbac.py`'s rotated samples are genuine.)

**Fix:** use `provisioned_worker_login` in `_make_zero_perm_worker` and assert
"Missing permission" in the denial body.

### M-10. Post-logout browser Back lands on an infinite "Loading…" screen
`frontend/src/lib/auth-context.tsx:130, 228, 390, 411-414, 443-448`

`forcedLogout` is set on signOut and cleared only in `signIn`. After logout,
client-side Back navigation to a protected route finds `shouldRedirect === false`
(latch still set) and `(app)/app-layout-client.tsx`'s gate renders the loading
skeleton forever — no redirect, no recovery short of a full reload. Same on the
401-forced path.

**Fix:** clear the latch when the transition has landed (public path reached), or
use a per-intent latch like `signedOutRedirectIntent`.

### M-11. Screening walkthrough: S3 PUT has no timeout and the dialog-reset effect omits `uploading` — a wedged upload bricks the dialog
`frontend/src/components/screening-check-dialog.tsx:133-138, 72-80`

The raw cross-origin PUT bypasses the api-client's 60 s timeout discipline; a
stalled PUT on flaky mobile data leaves `uploading === true` forever, and
reopening the dialog resets file/preview/batch but not the flag — Upload and
Finish stay disabled with no error until a page reload.

**Fix:** `signal: AbortSignal.timeout(60_000)`, abort on dialog close, and
`setUploading(false)` in the reset effect.

### M-12. i18n bypass: simulation, vaccination-schedule, and most animals copy are hardcoded English in a bilingual app
`simulation/page.tsx` (0 `t()` calls in 3,701 lines), `health/schedule/[animalId]/page.tsx` (0), `animals/page.tsx` + `animals/[id]/page.tsx` (phenotype selects only), `tasks/page.tsx:1699` Suspense fallback

Telugu users get fully English screens on four surfaces while health/tasks/screening
are fully localized (154/132/58 `t()` calls respectively).

**Fix:** route copy through the catalog; start with the schedule page (smallest).

### M-13. Insurance create dialog: a blank "Premium (₹)" is silently saved as ₹0
`frontend/src/app/(app)/finance/insurance/page.tsx:92-96`

`z.coerce.number()` maps the cleared number input (`""`) to `0`, which passes
`nonnegative` — a required-marked field silently becomes a ₹0 premium in the
append-only register. The ledger-correction schema in the same domain documents
and defends against exactly this hazard.

**Fix:** `z.preprocess` blank → `undefined` + required, as `correctionSchema.amount` does.

### M-14. Screening deep-link failure renders an empty panel with no error or retry
`frontend/src/app/(app)/screening/page.tsx:306-310, 473`

`detailQuery` has no error branch: a bookmarked/rotated/foreign `?image_id=`
yields `detail === undefined` after `isPending` clears — title and Back button
over an empty body. The list-level failure state similarly shows a dead-end
"Retry" label with no retry control (lines 504-509).

**Fix:** add `isError` branches with `ApiError.detail` + retry (sibling-page pattern).

---

## LOW

| # | Finding | Location |
|---|---------|----------|
| L-1 | Insurance `renew` resurrects a CLAIMED (documented-terminal) policy; claim→renew→claim cycle repeats, inflating premium rows | `services/finance.py:265-269` |
| L-2 | `verify_totp_code` accepts non-ASCII digits (`'12345٥'.isdigit()` is True) → `hmac.compare_digest` TypeError → unhandled 500 on all three TOTP endpoints (empirically reproduced) | `security.py:919-933` |
| L-3 | Unbounded `animal_id` insurance-list filter → asyncpg int4 `DataError` → 500 (violates the repo's own `MAX_INT32_ID` convention) | `api/finance.py:780` |
| L-4 | `scalar_one()` (not `..._or_none`) in the three authenticated TOTP routes → 500 when the account is deleted concurrently (wide Argon2 window in enroll) | `api/auth.py:1789-1796, 1820-1827, 1905-1912` |
| L-5 | `s3_endpoint_url` exempt from the https validation every provider URL gets — SigV4 credentials on plaintext HTTP allowed in production | `core/config.py:386` |
| L-6 | TOTP AES key cache never invalidated on JWT keyring live-reload (latent — no in-process reload path today) | `security.py:938-952` |
| L-7 | Orval client omits the required `Idempotency-Key` header for `POST /api/auth/farms` (spec declares it required); the hand-written transport shim papers over it — generated SDK in isolation always 422s | `endpoints.ts:~1803` vs `shared/openapi.json` |
| L-8 | `_expire_abandoned_uploads`: unbounded single-statement UPDATE, no SKIP LOCKED batching — post-downtime backlog lock / two-worker deadlock vector | `pipeline.py:177-191` |
| L-9 | Cadence backfill materializes overdue seasonal rounds for months before the farm had any animals (floor is farm creation, not first animal) | `cadence.py:498-506` |
| L-10 | Calibration averages recurring costs over the truncated 20k-row ledger span → overstated per-month costs (the "unviable looks financeable" direction) when >20k rows exist | `simulation_calibration.py:909-997` |
| L-11 | `WOOD_CURVATURE_B` in `__all__` references a deleted name — `from app.simulation import *` crashes | `simulation/__init__.py:109` |
| L-12 | daily_ops: home-born males are force-sold at 8 months, 4 months before the sire gate — the `MALE_KIDS → BREEDING` edge is unreachable; sire-less herds stay sterile all run | `daily_ops.py:1086-1110, 1407-1430` |
| L-13 | explain.py hard-coded twinning/triplet bands can contradict the computed litter size in the same sentence | `explain.py:596-603` |
| L-14 | Settling (unserviceable) does counted in the auto-sire headcount — replacement bucks bought a month early (one-month conservative bias) | `engine.py:998-999, 1145-1146` |
| L-15 | `HealthEventIn.next_due_date` unbounded on an immutable fact (contrast the 730-day withdrawal ceiling) — mistyped year suppresses boosters for ~180 years | `schemas/health.py:246` |
| L-16 | `bucket_moves.effective_date` server default is `CURRENT_DATE` (UTC), not farm-local — out-of-band writers get the wrong day 18:30-24:00 UTC | `models/animals.py:559-563` |
| L-17 | Residual FLOAT columns after the exact-numerics program: `purchases.avg_weight_kg`/`avg_age_months`, `FeedRecipeLine.kg_per_100kg`, `VaccineTemplate` age/booster/repeat months | `models/purchases.py:69-70`, `models/feeding.py:54`, `models/health.py:273-275` |
| L-18 | `tasks.title_args` JSONB lacks the `jsonb_typeof = 'object'` CHECK every other JSONB column carries | `models/tasks.py:126-128` |
| L-19 | TOTP enrollment URI rendered as `href` without the `safeAppPath`/scheme validation all other API-derived links get | `account-dialog.tsx:566-571` |
| L-20 | Screening dialog races: stale upload continuation credits the new walkthrough; `ensureBatchId` has no synchronous double-click lock (unbatched POST mints duplicate batches) | `screening-check-dialog.tsx:104-162` |
| L-21 | Blank error text for non-JSON error responses with empty `statusText` (HTTP/2) — blank alerts precisely on proxy failures | `api-client.ts:799-815` |
| L-22 | `screening.export.done` lacks the one/many split — "1 records" | `i18n/en.ts:653`, `screening/page.tsx:151` |
| L-23 | Simulation "Repeat plan" commits the last valid values while inputs display invalid drafts (no `onValidityChange` wiring in the dialog) | `simulation/page.tsx:3209-3303` |
| L-24 | Animal status dialog: "Price per kg" enablement misses the empty-string weight (`""` ≠ undefined/null) — enabled with no weight, error deferred to submit | `animals/[id]/page.tsx:970` |
| L-25 | Clear-restriction dialog leaks error/409-conflict state across close→reopen (sibling dialogs reset on open) | `animals/[id]/page.tsx:1324-1394` |
| L-26 | Farm-switch fence missing on error continuations: kidding/breeding dialogs' catch paths and all of screening's actions toast the old farm's errors over the new farm's UI | `kidding/page.tsx:400-404`, `breeding/page.tsx:258-278, 559-563, 732-736`, `screening/page.tsx` (no `captureFarmScope` import) |
| L-27 | Ops-simulation run continuation has no farm-scope fence at all — farm A's results paint farm B's page (only analysis page without one) | `ops-simulation/page.tsx:411-443` |
| L-28 | Planner: "Use my herd"/"Use farm records" don't disarm the breed-defaults adoption ref — a late preset response silently wipes the applied basis after the success toast | `planner/page.tsx:282-375` |
| L-29 | Dashboard "Held since" and planner "Updated" render UTC datetimes via browser-locale `toLocaleDateString()` — off-by-one day for non-IST browsers, ignores te locale | `dashboard/page.tsx:805`, `planner/page.tsx:1390, 1471` |
| L-30 | Tautological assertion in the TOTP at-rest test (`compare_digest(sha256(stored), sha256(stored))`) | `tests/test_totp.py:75-77` |
| L-31 | Cull-escape-hatch test accepts both 200 and 409 — the documented behavior is never actually pinned | `tests/test_audit_remediation.py:113-119` |
| L-32 | Multi-code status assertions that don't pin which rule fired (`in {400,409,422}`, fuzzy `"20" in detail`) | `test_e2e_scenario_gaps.py:154,177`, `test_e2e_lifecycle_audit.py:531,1287,1324` |
| L-33 | 14 tautological payload assertions in the e2e auth/team contract spec (variable vs the literals it was built from) | `e2e/api-contract-auth-team.spec.ts` (13 sites) |
| L-34 | Manual `get_settings` monkeypatching in screening tests (safe as written, but bypasses guaranteed undo) | `tests/test_screening.py:1144-1146, 1205-1207` |
| L-35 | Reports e2e smoke passes on an empty farm (`\d+` matches 0); two-person verify rule has no worker-side e2e; global-setup leaves durable state with no teardown | `e2e/feeding-finance.spec.ts:118-123`, `e2e/tasks-*.spec.ts`, `e2e/global-setup.ts` |
| L-36 | `GOATFARM_METRICS_ENABLED` / `GOATFARM_RATE_LIMIT_BACKEND` absent from both `.env.example` files | `core/config.py:340,346` |
| L-37 | `transactions.created_at` carries pre-2026-08-09 server-local wall times in the same naive column (documented unfixable — treat as unreliable) | migration `b2c3d4e5f6a8` context |

---

## Verified clean (notable)

- **Auth/session core**: refresh rotation with reuse detection and family
  revocation, cookie/bearer pairing, origin guards, login enumeration timing
  equalization, per-email soft ceilings — all intact and internally consistent.
- **Tenancy**: no IDOR found anywhere — every route resolves farm via
  `CurrentFarm`; membership/role lookups are farm-scoped with `MAX_INT32_ID`
  bounds; 18 composite `(farm_id, child_id)` FKs; all tenant uniques farm-scoped;
  migration chain verified programmatically single-head, no forks/orphans (70
  revisions).
- **Money**: no Float money columns at head; `Decimal`/`NUMERIC(12-14,2)` with
  CHECKs; exact-paise allocation everywhere; corrections are atomic void+replace
  under `FOR UPDATE`.
- **Concurrency**: canonical lock ordering (Farm→Animal→Task, Membership→User→Role,
  doe-then-record, kid→entry→kidding) consistently applied; run limits,
  idempotency claims, and quota creation are lock-serialized.
- **Simulation core**: 120-month mass balance residual 4.6e-14; kidding cycle
  exactly 5+2+1; Bakrid Gregorian months 2026-2032 correct; Monte Carlo
  deterministic per seed with correct percentile method.
- **Frontend security posture**: access token memory-only; single-flight refresh
  with Web Locks cross-tab serialization; farm-scope fencing on success paths;
  `safeAppPath` on all API-derived navigation; zero XSS sinks.
- **Infra/CI**: digest-pinned bases with build-time CVE sweeps; non-root, cap-
  dropped, no-new-privileges everywhere; two-segment network isolation; release
  pipeline verifies published config digests against scanned gate bits; all
  actions SHA-pinned with minimal permissions.
- **conftest**: per-test TRUNCATE + reseed, DB-name guard, no cross-loop leaks;
  TOTP/concurrency/idempotency deeply tested (15/24/45 tests).
- All 107 backend routes across 16 modules are exercised by the test suite.

## Recommended fix order

1. **H-2 + H-3 + M-5** (screening write surface, retry starvation, size cap) —
   one remediation pass over the screening module closes the abuse + DoS cluster.
2. **H-1** (TOTP enroll guard + disable throttle) — small, high-value auth hardening.
3. **H-4** (CSP/proxy for S3 photo traffic) — the feature ships broken in prod
   without it.
4. **M-1, M-2** (mortality gate; missing security events) — detection/consistency.
5. **M-6, M-7** (DSCR balloon; held-male pricing) — lender-facing money metrics.
6. **M-9** (RBAC test rotation) — restores the safety net's dynamic half.
7. Remaining MEDIUMs and the LOW table in the order listed.
